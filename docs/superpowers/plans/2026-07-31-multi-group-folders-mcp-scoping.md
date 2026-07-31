# Multi-group membership, folder visibility, and MCP scoping — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user belong to several groups, gate joining behind admin approval, give every group a shared `common` folder plus a private folder per member, and let the MCP agent search across all their groups by default or narrow to a folder.

**Architecture:** `utils/groups.py` stays the single source of truth for read access and widens from one group id to a list; folder visibility becomes an explicit column and a document's `group_id` is re-stamped from its folder rather than derived at query time; the UMAP moves to a global fit because per-scope stored coordinates cannot survive overlapping group sets.

**Tech Stack:** FastAPI + SQLAlchemy 2 async (asyncpg), Postgres + pgvector, Next.js + next-intl, MCP server (`mcp>=1.2,<2`), pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-07-31-multi-group-folders-mcp-scoping-design.md`

## Global Constraints

- Production environment. **Never** drop the database, never `docker compose down -v`, additive migrations only.
- Schema changes go in `backend/database.py::ensure_schema_updates()`. `backend/migrations/*.sql` is documentation and is **never executed**.
- Rebuild with `docker-compose.prod.yml`, never the dev compose file.
- Every user-visible string goes in `frontend/messages/en.json` **and** `frontend/messages/fr.json`. No hardcoded JSX text.
- Every new/changed application endpoint must also be reachable from MCP (`mcp-server/maptalk_mcp/tools.yaml`) — except endpoints behind `require_interactive_user`, which are excluded by rule.
- `mcp` SDK stays pinned `>=1.2,<2`.
- After a write endpoint is added, call it once against the real database via `docker exec -i -w /app maptimize-backend python - < script.py`, each call in its own `async_session_maker()` session.
- Every new guard is verified by **perturbation**: delete the fix, watch the test fail, restore it.
- Reads are group-shared; writes to experiments and images stay owner-only. The four deliberate group-write exceptions (crops, microscope, PTM, protein) are unchanged by this work.
- E2E is never pointed at production.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `backend/models/group_join_request.py` | `GroupJoinRequest` model |
| `backend/utils/folder_seed.py` | Idempotent creation of a group's root/`common`/member folders |
| `backend/utils/folder_placement.py` | `placement_group_id`, subtree visibility + document re-stamping |
| `backend/schemas/group_join_request.py` | Request/response schemas for the join flow |
| `backend/tests/unit/test_multi_group_acl.py` | Multi-group read scope across every ACL mirror |
| `backend/tests/unit/test_group_join_requests.py` | Request → approve/reject flow and its authorization |
| `backend/tests/unit/test_folder_visibility.py` | Private folders, inheritance, seeded-folder immutability |
| `backend/tests/unit/test_folder_placement.py` | Document `group_id` re-stamping on every move path |
| `backend/tests/unit/test_experiment_group_assignment.py` | `PATCH /experiments/{id}/group` ownership rules |
| `backend/scripts/migrate_multi_group_folders.py` | One-off idempotent production backfill |
| `frontend/e2e/unit/folderTree.spec.ts` | Tree construction from the flat folder list |

**Modified**

| File | Change |
|---|---|
| `backend/models/group.py` | Drop `uq_user_one_group`, add `uq_group_member` |
| `backend/models/document_folder.py` | `visibility`, `kind` columns |
| `backend/models/rag_document.py` | `document_scope` / `document_read_scope` / `document_dedupe_scope` take a group **list** |
| `backend/models/__init__.py` | Export `GroupJoinRequest` |
| `backend/database.py` | `ensure_schema_updates()`: new columns + constraint swap |
| `backend/utils/groups.py` | `get_user_group_ids`, list-based filter, `require_group_admin`, delete both `adopt_orphan_*` |
| `backend/routers/groups.py` | Role-based authorization, join-request endpoints, delete open join |
| `backend/routers/folders.py` | Visibility-aware `_visible`, inheritance, immutable seeded folders |
| `backend/routers/rag.py` | Folder/group scoping params on search + list endpoints |
| `backend/routers/experiments.py` | `PATCH /{id}/group` |
| `backend/routers/{embeddings,metrics,images,query}.py` | `get_user_group_ids` call sites |
| `backend/services/rag_service.py` | `_owner_clause` list form, folder filter in `_search_pages_by_embedding` |
| `backend/services/sql_query_service.py` | `_inject_user_id_filter` widens with a group list |
| `backend/services/umap_service.py` | Global fit, collapsed scope key |
| `backend/services/discriminant_service.py` | Cache key from the group list |
| `backend/services/document_indexing_service.py` | Placement stamping on upload, dedupe scope list |
| `mcp-server/maptalk_mcp/tools.yaml` | Scoping params, enriched `list_folders`, 4 new tools |
| `mcp-server/maptalk_mcp/handlers.py` | Repeated-param encoding for the scoping args |
| `mcp-server/maptalk_mcp/server.py` | `SERVER_VERSION` → 3.0.0 |
| `frontend/app/documents/page.tsx` + components | Group-rooted tree with visibility badges |
| `frontend/app/dashboard/settings/page.tsx` | Multiple groups, request-to-join, approval queue |
| `frontend/messages/{en,fr}.json` | New strings |
| `CLAUDE.md` | Document the new ACL surface and its traps |

---

# Phase A — Multi-group core

### Task A1: Schema — join requests, folder columns, constraint swap

**Files:**
- Create: `backend/models/group_join_request.py`
- Modify: `backend/models/group.py:44-47`, `backend/models/document_folder.py:26-30`, `backend/models/__init__.py`, `backend/database.py:176-215`
- Test: `backend/tests/unit/test_group_join_requests.py`

**Interfaces:**
- Produces: `GroupJoinRequest(id, group_id, user_id, status, message, created_at, decided_at, decided_by_user_id)`; `JoinRequestStatus` with `PENDING="pending"`, `APPROVED="approved"`, `REJECTED="rejected"`; `DocumentFolder.visibility: str`, `DocumentFolder.kind: str`; module constants `FOLDER_VISIBILITY_GROUP="group"`, `FOLDER_VISIBILITY_PRIVATE="private"`, `FOLDER_KIND_ROOT="root"`, `FOLDER_KIND_COMMON="common"`, `FOLDER_KIND_USER="user"`, `FOLDER_KIND_CUSTOM="custom"` in `models/document_folder.py`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_group_join_requests.py
from models.group import GroupMember
from models.group_join_request import GroupJoinRequest, JoinRequestStatus
from models.document_folder import (
    DocumentFolder, FOLDER_VISIBILITY_GROUP, FOLDER_VISIBILITY_PRIVATE,
    FOLDER_KIND_ROOT, FOLDER_KIND_COMMON, FOLDER_KIND_USER, FOLDER_KIND_CUSTOM,
)


def test_group_membership_is_unique_per_group_not_per_user():
    """A user may hold one membership in each of several groups."""
    names = {c.name for c in GroupMember.__table__.constraints if c.name}
    assert "uq_user_one_group" not in names, "the one-group-per-user constraint must be gone"
    uq = next(c for c in GroupMember.__table__.constraints if c.name == "uq_group_member")
    assert {col.name for col in uq.columns} == {"group_id", "user_id"}


def test_join_request_table_shape():
    cols = GroupJoinRequest.__table__.columns
    assert {"id", "group_id", "user_id", "status", "message",
            "created_at", "decided_at", "decided_by_user_id"} <= set(cols.keys())
    uq = next(c for c in GroupJoinRequest.__table__.constraints
              if getattr(c, "name", None) == "uq_join_request_group_user")
    assert {col.name for col in uq.columns} == {"group_id", "user_id"}
    assert JoinRequestStatus.PENDING.value == "pending"


def test_folder_carries_visibility_and_kind():
    cols = DocumentFolder.__table__.columns
    assert cols["visibility"].default.arg == FOLDER_VISIBILITY_GROUP
    assert cols["kind"].default.arg == FOLDER_KIND_CUSTOM
    assert {FOLDER_VISIBILITY_GROUP, FOLDER_VISIBILITY_PRIVATE} == {"group", "private"}
    assert {FOLDER_KIND_ROOT, FOLDER_KIND_COMMON, FOLDER_KIND_USER} == {"root", "common", "user"}
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd backend && python -m pytest tests/unit/test_group_join_requests.py -v
```
Expected: `ModuleNotFoundError: No module named 'models.group_join_request'`.

- [ ] **Step 3: Add the model**

```python
# backend/models/group_join_request.py
"""Pending membership requests.

Joining a group is a request the group's admin approves -- self-service join was
removed with this model's introduction. One row per (group, user): re-requesting
after a rejection flips the same row back to ``pending`` rather than piling up
history, which keeps "does this user have a pending request" a single-row lookup.
"""
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class JoinRequestStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class GroupJoinRequest(Base):
    __tablename__ = "group_join_requests"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_join_request_group_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=JoinRequestStatus.PENDING.value, index=True
    )
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
```

- [ ] **Step 4: Swap the membership constraint**

In `backend/models/group.py`, replace `UniqueConstraint("user_id", name="uq_user_one_group")` with `UniqueConstraint("group_id", "user_id", name="uq_group_member")`, and update the class docstring to say a user may belong to several groups.

- [ ] **Step 5: Add the folder columns**

In `backend/models/document_folder.py` add the six module constants and:

```python
    # 'group' = every member of group_id sees it; 'private' = only user_id does.
    # Inherited from the parent at creation and recomputed for the whole subtree on
    # move, so a subfolder can never be more visible than the folder holding it.
    visibility: Mapped[str] = mapped_column(
        String(20), default=FOLDER_VISIBILITY_GROUP, server_default=FOLDER_VISIBILITY_GROUP
    )
    # Seeded folders ('root' | 'common' | 'user') are immutable: they cannot be
    # renamed, moved or deleted. 'custom' is anything a person made.
    kind: Mapped[str] = mapped_column(
        String(20), default=FOLDER_KIND_CUSTOM, server_default=FOLDER_KIND_CUSTOM
    )
```

Export `GroupJoinRequest` and `JoinRequestStatus` from `backend/models/__init__.py` (both the import and `__all__`).

- [ ] **Step 6: Teach `ensure_schema_updates` about all of it**

Append to the `updates` list in `backend/database.py`:

```python
            # Folder visibility ('group' | 'private') and seeded-folder kind
            ("document_folders", "visibility", "VARCHAR(20) DEFAULT 'group' NOT NULL"),
            ("document_folders", "kind", "VARCHAR(20) DEFAULT 'custom' NOT NULL"),
```

and after the existing `metric_ratings` constraint block, add the membership constraint swap using the same savepoint pattern:

```python
        # Multi-group membership: a user may belong to several groups, but only
        # once to each. create_all never alters an existing table, so without this
        # the old one-group-per-user UNIQUE would survive in production forever.
        for sql, label in (
            ("ALTER TABLE group_members DROP CONSTRAINT IF EXISTS uq_user_one_group",
             "drop uq_user_one_group"),
            ("ALTER TABLE group_members ADD CONSTRAINT uq_group_member "
             "UNIQUE (group_id, user_id)", "add uq_group_member"),
        ):
            try:
                await conn.execute(text("SAVEPOINT group_member_constraint"))
                await conn.execute(text(sql))
                await conn.execute(text("RELEASE SAVEPOINT group_member_constraint"))
            except Exception as e:
                await conn.execute(text("ROLLBACK TO SAVEPOINT group_member_constraint"))
                if "already exists" in str(e).lower():
                    logger.debug(f"{label}: already applied")
                else:
                    logger.error(f"Failed to {label}: {e}")
                    failed_updates.append(f"group_members.{label}")
```

- [ ] **Step 7: Run the tests**

```bash
cd backend && python -m pytest tests/unit/test_group_join_requests.py -v
```
Expected: 3 passed.

- [ ] **Step 8: Perturb to prove the constraint test bites**

Temporarily restore `UniqueConstraint("user_id", name="uq_user_one_group")` in `models/group.py`, re-run — `test_group_membership_is_unique_per_group_not_per_user` must FAIL — then remove it again.

- [ ] **Step 9: Commit**

```bash
git add backend/models backend/database.py backend/tests/unit/test_group_join_requests.py
git commit -m "Let a user belong to several groups, and add the join-request table"
```

---

### Task A2: Widen the ACL from one group to a list

**Files:**
- Modify: `backend/utils/groups.py`, `backend/models/rag_document.py:29-98`, `backend/services/rag_service.py:59-120`, `backend/services/sql_query_service.py:160-200`, `backend/routers/{experiments,images,embeddings,metrics,query,rag,folders}.py`, `backend/services/{umap_service,discriminant_service,document_indexing_service}.py`
- Test: `backend/tests/unit/test_multi_group_acl.py`

**Interfaces:**
- Consumes: nothing from A1 beyond the model import.
- Produces: `async get_user_group_ids(user_id, db) -> list[int]`; `experiment_owner_filter(user_id, group_ids: Sequence[int]) -> ColumnElement`; `document_scope(user_id, thread_id, group_ids)`, `document_read_scope(user_id, group_ids)`, `document_dedupe_scope(user_id, thread_id, group_ids)`; `rag_service._owner_clause(group_ids)`. `get_user_group_id` and both `adopt_orphan_*` functions no longer exist.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_multi_group_acl.py
import pytest
from sqlalchemy import select

from models.experiment import Experiment
from models.rag_document import (
    RAGDocument, document_scope, document_read_scope, document_dedupe_scope,
)
from utils.groups import experiment_owner_filter


def _sql(clause) -> str:
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_experiment_filter_covers_every_group_the_user_is_in():
    sql = _sql(experiment_owner_filter(7, [2, 5]))
    assert "user_id = 7" in sql
    assert "IN (2, 5)" in sql.replace("IN (2,5)", "IN (2, 5)")


def test_no_groups_is_owner_only_not_everything():
    """An empty list must fail closed: IN () is false, never absent."""
    sql = _sql(experiment_owner_filter(7, []))
    assert "user_id = 7" in sql
    # No group term may widen the scope when the user has no groups.
    assert "group_id" not in sql


@pytest.mark.parametrize("build", [
    lambda gids: document_read_scope(7, gids),
    lambda gids: document_scope(7, None, gids),
])
def test_document_scopes_take_a_group_list(build):
    sql = _sql(build([2, 5]))
    assert "IN (2, 5)" in sql.replace("IN (2,5)", "IN (2, 5)")


def test_library_group_term_stays_and_gated_on_thread_id_is_null():
    """Attachments must never widen to a group, however many groups exist."""
    sql = _sql(document_scope(7, None, [2, 5])).lower()
    assert "thread_id is null" in sql
    # The group term is ANDed under the library branch, not ORed at top level.
    assert sql.index("thread_id is null") < sql.index("in (2, 5)".replace("in (2,5)", "in (2, 5)"))


def test_dedupe_scope_still_narrower_than_read_scope():
    thread_sql = _sql(document_dedupe_scope(7, 42, [2, 5]))
    assert "in (2, 5)" not in thread_sql.lower().replace("in (2,5)", "in (2, 5)"), \
        "a chat attachment must dedupe only against its own thread"


def test_adoption_helpers_are_gone():
    import utils.groups as g
    assert not hasattr(g, "adopt_orphan_experiments")
    assert not hasattr(g, "adopt_orphan_documents")
    assert not hasattr(g, "get_user_group_id"), "the singular helper must not survive as a shim"
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && python -m pytest tests/unit/test_multi_group_acl.py -v
```
Expected: `TypeError`/`ImportError` — `experiment_owner_filter` still takes one id.

- [ ] **Step 3: Rewrite `utils/groups.py`**

```python
async def get_user_group_ids(user_id: int, db: AsyncSession) -> list[int]:
    """Every group the user belongs to. Empty list = no group (fail-closed)."""
    result = await db.execute(
        select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    )
    return list(result.scalars().all())


def experiment_owner_filter(user_id: int, group_ids: Sequence[int] = ()) -> ColumnElement:
    """Experiments the user may read: their own, plus any group they belong to.

    SSOT for the read rule. ``group_ids`` is a list because membership is
    many-to-many; an empty list contributes no term at all, so a user without a
    group degrades to owner-only rather than to "everything".
    """
    conditions = [Experiment.user_id == user_id]
    if group_ids:
        conditions.append(Experiment.group_id.in_(list(group_ids)))
    return or_(*conditions)
```

Delete `get_user_group_id`, `adopt_orphan_experiments` and `adopt_orphan_documents` outright.

- [ ] **Step 4: Widen the three document scopes**

In `backend/models/rag_document.py`, change `group_id: Optional[int] = None` to `group_ids: Sequence[int] = ()` in `_library_visible`, `document_scope`, `document_read_scope` and `document_dedupe_scope`, replacing `RAGDocument.group_id == group_id` with `RAGDocument.group_id.in_(list(group_ids))` guarded by `if group_ids:`. **Do not touch the `and_(RAGDocument.thread_id.is_(None), ...)` gate** — it is what keeps attachments from leaking to a group.

- [ ] **Step 5: Widen the two raw-SQL mirrors**

`backend/services/rag_service.py::_owner_clause(group_ids)` becomes
`"(d.user_id = :user_id OR d.group_id = ANY(:group_ids))"` when `group_ids` is non-empty, else `"d.user_id = :user_id"`, with `params["group_ids"] = [int(g) for g in group_ids]`.

`backend/services/sql_query_service.py::_inject_user_id_filter` builds its widened predicate from a list the same way. Keep the alias-qualified reference, one predicate per alias, and the injected FK correlation for indirect tables — those are unrelated to this change and must survive it.

- [ ] **Step 6: Update every call site**

```bash
cd backend && grep -rn "get_user_group_id\b\|adopt_orphan" --include=*.py . | grep -v tests
```
Rename each to `get_user_group_ids`, rename the local variable `group_id` → `group_ids`, and pass it through. Delete the `adopt_orphan_*` calls in `routers/groups.py` (both `create_group` and `join_group`) along with the `adopted` logging around them.

- [ ] **Step 7: Run the new test and the whole unit suite**

```bash
cd backend && python -m pytest tests/unit/test_multi_group_acl.py -v
cd backend && python -m pytest tests/unit -q
```
Expected: the new file passes; existing failures are only in tests that pass a bare `group_id=` — update those call sites in the tests, never the assertions.

- [ ] **Step 8: Perturb the fail-closed test**

In `experiment_owner_filter`, temporarily change `if group_ids:` to `if group_ids is not None:` and confirm `test_no_groups_is_owner_only_not_everything` fails (empty `IN ()` renders). Restore.

- [ ] **Step 9: Commit**

```bash
git add backend
git commit -m "Widen every group ACL mirror from one id to a list"
```

---

### Task A3: Enforce group roles and gate joining behind approval

**Files:**
- Modify: `backend/utils/groups.py`, `backend/routers/groups.py`, `backend/schemas/group.py`
- Create: `backend/schemas/group_join_request.py`
- Test: `backend/tests/unit/test_group_join_requests.py` (extend)

**Interfaces:**
- Consumes: `GroupJoinRequest`, `JoinRequestStatus` (A1); `get_user_group_ids` (A2); `ensure_member_folder` is *not* available yet — Task C2 wires it in.
- Produces: `async require_group_admin(user_id, group_id, db) -> None` (raises 403); `async is_group_admin(user_id, group_id, db) -> bool`; endpoints listed below; `JoinRequestResponse(id, group_id, group_name, user_id, user_name, user_email, status, message, created_at, decided_at)`.

- [ ] **Step 1: Write the failing tests**

```python
# appended to backend/tests/unit/test_group_join_requests.py
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from models.user import UserRole
from routers import groups as groups_router
from tests.unit.conftest import make_result


def _user(uid, role=UserRole.RESEARCHER):
    return SimpleNamespace(id=uid, email=f"u{uid}@x.cz", name=f"U{uid}", role=role)


async def test_open_self_join_endpoint_is_gone():
    assert not hasattr(groups_router, "join_group"), \
        "self-service join is the hole this feature closes"


async def test_only_a_group_admin_approves(mock_db):
    """A plain member approving someone else's request is a 403."""
    mock_db.execute.return_value = make_result(scalar=SimpleNamespace(role="member"))
    with pytest.raises(HTTPException) as exc:
        await groups_router.approve_join_request(
            group_id=2, request_id=9, current_user=_user(7), db=mock_db
        )
    assert exc.value.status_code == 403


async def test_global_admin_is_admin_of_every_group(mock_db):
    """No per-group row needed: users.role == ADMIN passes require_group_admin."""
    mock_db.execute.return_value = make_result(scalar=None)  # no membership at all
    await groups_router.require_group_admin(
        _user(1, UserRole.ADMIN).id, 2, mock_db, actor=_user(1, UserRole.ADMIN)
    )  # must not raise


async def test_approval_creates_membership_and_marks_the_request(mock_db):
    request_row = SimpleNamespace(
        id=9, group_id=2, user_id=7, status="pending", decided_at=None, decided_by_user_id=None
    )
    mock_db.execute.side_effect = [
        make_result(scalar=SimpleNamespace(role="admin")),   # require_group_admin
        make_result(scalar=request_row),                     # the request
        make_result(scalar=None),                            # not already a member
    ]
    with patch.object(groups_router, "ensure_member_folder", AsyncMock()) as seed:
        await groups_router.approve_join_request(
            group_id=2, request_id=9, current_user=_user(1), db=mock_db
        )
    assert request_row.status == "approved"
    assert request_row.decided_by_user_id == 1
    added = [c.args[0] for c in mock_db.add.call_args_list]
    assert any(getattr(o, "role", None) == "member" for o in added), "membership must be created"
    seed.assert_awaited_once()


async def test_requesting_twice_is_a_conflict(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=None),                                  # not a member
        make_result(scalar=SimpleNamespace(id=9, status="pending")),  # already pending
    ]
    with pytest.raises(HTTPException) as exc:
        await groups_router.create_join_request(
            group_id=2, body=SimpleNamespace(message=None), current_user=_user(7), db=mock_db
        )
    assert exc.value.status_code == 409
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && python -m pytest tests/unit/test_group_join_requests.py -v
```
Expected: `AttributeError: module 'routers.groups' has no attribute 'approve_join_request'` (and `join_group` still exists, so that test fails too).

- [ ] **Step 3: Add the authorization helpers to `utils/groups.py`**

```python
async def is_group_admin(user_id: int, group_id: int, db: AsyncSession) -> bool:
    """True when the user holds the 'admin' role in this specific group."""
    result = await db.execute(
        select(GroupMember.role).where(
            GroupMember.group_id == group_id, GroupMember.user_id == user_id
        )
    )
    return result.scalar_one_or_none() == "admin"


async def require_group_admin(user_id, group_id, db, *, actor=None) -> None:
    """Authorize a group-administration action.

    A global ``users.role == ADMIN`` counts as admin of every group, including
    groups created later -- which is what makes "this person administers
    everything" durable without rows to maintain. ``created_by_user_id`` is
    provenance only and grants nothing.
    """
    if actor is not None and getattr(actor.role, "value", actor.role) == "admin":
        return
    if not await is_group_admin(user_id, group_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a group admin can do this",
        )
```

- [ ] **Step 4: Rewrite the router's authorization and join flow**

In `backend/routers/groups.py`:
- Replace the three `group.created_by_user_id != current_user.id` checks in `update_group`, `delete_group` and `kick_member` with `await require_group_admin(current_user.id, group_id, db, actor=current_user)`.
- **Delete `join_group` entirely** (endpoint and function).
- Re-key `leave_group`'s ownership transfer: when the leaver is the last `role == "admin"`, promote the earliest-joined remaining member to `admin` and set `created_by_user_id`; when nobody remains, delete the group.
- Add the six endpoints from the spec's table. `approve_join_request` and `reject_join_request` depend on `require_interactive_user`; the rest use `get_current_user`.
- `approve_join_request` body: authorize → load the pending request (404 otherwise) → 409 if already a member → `db.add(GroupMember(group_id, user_id, role="member"))` → set `status`, `decided_at=datetime.now(timezone.utc)`, `decided_by_user_id` → `await ensure_member_folder(db, group, requester)` → commit → return the detail response rebuilt by a fresh SELECT.
- `create_join_request`: 409 if already a member or a pending request exists; if a `rejected` row exists, flip it back to `pending` and clear the decision fields.

Import `ensure_member_folder` from `utils.folder_seed` at module level, so the test can patch `groups_router.ensure_member_folder`. That module is built in Task C2; in this task create it containing only:

```python
async def ensure_member_folder(db, group, user):
    """Placeholder — Task C2 replaces this with the real seeding."""
    return None
```

C2 **replaces** this definition rather than adding a second one.

- [ ] **Step 5: Run the tests**

```bash
cd backend && python -m pytest tests/unit/test_group_join_requests.py -v
```
Expected: all pass.

- [ ] **Step 6: Perturb the authorization test**

Delete the `require_group_admin` call in `approve_join_request`, confirm `test_only_a_group_admin_approves` fails, restore it.

- [ ] **Step 7: Verify against the real database**

```bash
docker exec -i -w /app maptimize-backend python - <<'PY'
# one session per call, exactly what get_db() hands a real request
import asyncio
from database import async_session_maker
from routers.groups import create_join_request, approve_join_request
...
PY
```
Confirm approve returns 200 and does not raise `MissingGreenlet`.

- [ ] **Step 8: Commit**

```bash
git add backend
git commit -m "Gate group joining behind admin approval, and make the role column authoritative"
```

---

### Task A4: Assign an experiment to a group (replaces adoption)

**Files:**
- Modify: `backend/routers/experiments.py`
- Test: `backend/tests/unit/test_experiment_group_assignment.py`

**Interfaces:**
- Consumes: `get_user_group_ids` (A2), `load_experiment_response` (existing).
- Produces: `PATCH /api/experiments/{experiment_id}/group?group_id=N`, handler `update_experiment_group`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_experiment_group_assignment.py
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers import experiments as exp_router
from tests.unit.conftest import make_result


async def test_group_assignment_is_owner_only(mock_db):
    """A container belongs to whoever uploaded it -- same rule as deleting a FOV."""
    mock_db.execute.return_value = make_result(scalar=SimpleNamespace(id=3, user_id=99))
    with pytest.raises(HTTPException) as exc:
        await exp_router.update_experiment_group(
            experiment_id=3, group_id=2,
            current_user=SimpleNamespace(id=7), db=mock_db,
        )
    assert exc.value.status_code == 403


async def test_cannot_donate_an_experiment_to_a_group_you_are_not_in(mock_db):
    exp = SimpleNamespace(id=3, user_id=7, group_id=None)
    mock_db.execute.return_value = make_result(scalar=exp)
    with patch.object(exp_router, "get_user_group_ids", AsyncMock(return_value=[5])):
        with pytest.raises(HTTPException) as exc:
            await exp_router.update_experiment_group(
                experiment_id=3, group_id=2,
                current_user=SimpleNamespace(id=7), db=mock_db,
            )
    assert exc.value.status_code == 400
    assert exp.group_id is None


async def test_response_is_rebuilt_by_reselecting_the_row(mock_db):
    """updated_at is server-generated and expires on commit; serializing the
    in-session object raises MissingGreenlet in production but not under AsyncMock."""
    exp = SimpleNamespace(id=3, user_id=7, group_id=None)
    mock_db.execute.return_value = make_result(scalar=exp)
    with patch.object(exp_router, "get_user_group_ids", AsyncMock(return_value=[2])), \
         patch.object(exp_router, "load_experiment_response", AsyncMock()) as loader:
        await exp_router.update_experiment_group(
            experiment_id=3, group_id=2,
            current_user=SimpleNamespace(id=7), db=mock_db,
        )
    assert exp.group_id == 2
    loader.assert_awaited_once()
    assert not mock_db.refresh.called, "never refresh(attribute_names=...) -- it leaves updated_at expired"
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && python -m pytest tests/unit/test_experiment_group_assignment.py -v
```
Expected: `AttributeError: ... has no attribute 'update_experiment_group'`.

- [ ] **Step 3: Implement**

```python
@router.patch("/{experiment_id}/group", response_model=ExperimentResponse)
async def update_experiment_group(
    experiment_id: int,
    group_id: Optional[int] = Query(None, description="Group to share with; omit to unshare"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Share an experiment with one of the owner's groups, or unshare it.

    Owner-only: the experiment is a container, and containers follow the same rule
    as deleting a FOV or renaming the experiment. This replaces the automatic
    adoption that used to happen on join, which had no answer once a user could
    belong to several groups.
    """
    result = await db.execute(select(Experiment).where(Experiment.id == experiment_id))
    experiment = result.scalar_one_or_none()
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if experiment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can share this experiment")
    if group_id is not None:
        if group_id not in await get_user_group_ids(current_user.id, db):
            raise HTTPException(status_code=400, detail="You are not a member of that group")
    experiment.group_id = group_id
    await db.commit()
    return await load_experiment_response(db, experiment_id)
```

- [ ] **Step 4: Run the tests**

```bash
cd backend && python -m pytest tests/unit/test_experiment_group_assignment.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Verify against the real database, then commit**

```bash
docker exec -i -w /app maptimize-backend python - < /tmp/probe_group_assign.py
git add backend && git commit -m "Add explicit experiment-to-group assignment"
```

---

# Phase B — UMAP global fit

### Task B1: Fit globally, filter on read

**Files:**
- Modify: `backend/services/umap_service.py:270-430`, `backend/services/discriminant_service.py:540-650`
- Test: `backend/tests/unit/test_umap_service.py` (extend), `backend/tests/unit/test_discriminant_service.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `async compute_crop_umap(db) -> dict`, `async compute_fov_umap(db) -> dict` (no `user_id`); `refresh_scope_key(umap_type) -> tuple[str]`; `async refresh_umap_scope(umap_type) -> None`.

- [ ] **Step 1: Write the failing test**

```python
async def test_the_fit_covers_every_crop_not_just_the_callers(mock_db):
    """Two members with different group sets must not overwrite each other's
    coordinates: umap_x/umap_y are ONE projection, stored on the crop row."""
    import inspect
    from services import umap_service

    src = inspect.getsource(umap_service.compute_crop_umap)
    assert "experiment_owner_filter" not in src, \
        "the fit corpus must not depend on who asked -- filter on read instead"
    assert "user_id" not in inspect.signature(umap_service.compute_crop_umap).parameters


def test_scope_key_no_longer_carries_a_group():
    from services.umap_service import refresh_scope_key, UmapType
    assert refresh_scope_key(UmapType.CROPPED) == (UmapType.CROPPED.value,)
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && python -m pytest tests/unit/test_umap_service.py -k "fit_covers or scope_key" -v
```

- [ ] **Step 3: Implement**

Drop `user_id`/`group_id` parameters and the `experiment_owner_filter(...)` term from `compute_crop_umap` and `compute_fov_umap`; keep `embedding.isnot(None)` and the `order_by(id)` (deterministic input order keeps successive fits comparable). Collapse `refresh_scope_key`, `get_refresh_error`, `clear_refresh_error` and `refresh_umap_scope` to take only `umap_type`, and update their callers in `routers/embeddings.py`. Rewrite the module docstrings that explain per-scope fitting to explain the global fit and the "filter selects what is returned, never what is fitted" rule.

In `discriminant_service`, replace the `f"g{group_id}"` / `f"u{user_id}"` cache key with `f"u{user_id}|g{','.join(str(g) for g in sorted(group_ids))}"`.

- [ ] **Step 4: Run the UMAP and discriminant suites**

```bash
cd backend && python -m pytest tests/unit/test_umap_service.py tests/unit/test_discriminant_service.py -q
```

- [ ] **Step 5: Perturb**

Re-add `experiment_owner_filter` to `compute_crop_umap` and confirm the first test fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add backend && git commit -m "Fit the UMAP globally so overlapping group sets cannot corrupt coordinates"
```

---

# Phase C — Folder visibility

### Task C1: Private folders, inheritance, immutable seeded folders

**Files:**
- Modify: `backend/routers/folders.py`
- Test: `backend/tests/unit/test_folder_visibility.py`

**Interfaces:**
- Consumes: folder constants (A1), `get_user_group_ids` (A2).
- Produces: `_visible(user_id, group_ids) -> ColumnElement`; `FolderResponse` gains `group_id`, `group_name`, `visibility`, `kind`, `path`, `document_count`; `FolderCreate` gains `visibility: Optional[str]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_folder_visibility.py
import pytest
from fastapi import HTTPException

from models.document_folder import (
    DocumentFolder, FOLDER_KIND_COMMON, FOLDER_KIND_ROOT,
    FOLDER_KIND_USER, FOLDER_VISIBILITY_PRIVATE,
)
from routers.folders import _visible, _reject_if_seeded


def _sql(clause):
    return str(clause.compile(compile_kwargs={"literal_binds": True})).lower()


def test_a_peers_private_folder_is_invisible_even_to_the_group():
    sql = _sql(_visible(7, [2]))
    assert "user_id = 7" in sql
    assert "visibility = 'group'" in sql, \
        "without the visibility term every private folder in my group is visible to me"


@pytest.mark.parametrize("kind", [FOLDER_KIND_ROOT, FOLDER_KIND_COMMON, FOLDER_KIND_USER])
def test_seeded_folders_cannot_be_renamed_moved_or_deleted(kind):
    folder = DocumentFolder(id=1, user_id=7, name="common", kind=kind)
    with pytest.raises(HTTPException) as exc:
        _reject_if_seeded(folder)
    assert exc.value.status_code == 400


def test_custom_folders_stay_editable():
    _reject_if_seeded(DocumentFolder(id=1, user_id=7, name="drafts", kind="custom"))
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && python -m pytest tests/unit/test_folder_visibility.py -v
```
Expected: `ImportError: cannot import name '_reject_if_seeded'`.

- [ ] **Step 3: Implement**

```python
def _visible(user_id: int, group_ids: Sequence[int]):
    """Folders the user may see: their own (private ones included) plus the
    group-visible folders of every group they belong to. A colleague's private
    folder carries visibility='private' and a different user_id, so it is
    excluded here -- for peers, for the group admin, and for the global admin.
    This is the only place in the application a global admin cannot read.
    """
    mine = DocumentFolder.user_id == user_id
    if not group_ids:
        return mine
    return or_(
        mine,
        and_(
            DocumentFolder.visibility == FOLDER_VISIBILITY_GROUP,
            DocumentFolder.group_id.in_(list(group_ids)),
        ),
    )


def _reject_if_seeded(folder: DocumentFolder) -> None:
    """Seeded folders are structure, not content: renaming or deleting `common`
    would leave every group member's mental model wrong and orphan the tree."""
    if folder.kind in (FOLDER_KIND_ROOT, FOLDER_KIND_COMMON, FOLDER_KIND_USER):
        raise HTTPException(status_code=400, detail=f"The '{folder.name}' folder cannot be modified")
```

Call `_reject_if_seeded` at the top of `update_folder` and `delete_folder`. In `create_folder`, inherit `visibility` and `group_id` from the parent when there is one (a subfolder of a private folder is private); at the root, honour the explicit `visibility` argument, defaulting to `private` with `group_id=None`. Extend `FolderResponse` with the new fields and compute `path` and `document_count` in `list_folders` with one grouped count query — never per row.

- [ ] **Step 4: Run the tests, then perturb**

```bash
cd backend && python -m pytest tests/unit/test_folder_visibility.py -v
```
Remove the `visibility` term from `_visible`, confirm the first test fails, restore.

- [ ] **Step 5: Commit**

```bash
git add backend && git commit -m "Give folders a visibility, and make seeded folders immutable"
```

---

### Task C2: Seed the group folder tree

**Files:**
- Create: `backend/utils/folder_seed.py`
- Modify: `backend/routers/groups.py` (create + approve paths)
- Test: `backend/tests/unit/test_folder_visibility.py` (extend)

**Interfaces:**
- Consumes: folder constants (A1).
- Produces: `async ensure_group_folders(db, group) -> tuple[DocumentFolder, DocumentFolder]` returning `(root, common)`; `async ensure_member_folder(db, group, user) -> DocumentFolder`. Both idempotent, keyed on `kind` (+ `user_id`), neither commits.

- [ ] **Step 1: Write the failing test**

```python
async def test_seeding_twice_creates_nothing_the_second_time(mock_db):
    """The production backfill is re-runnable, so seeding must be idempotent."""
    from utils.folder_seed import ensure_group_folders
    from tests.unit.conftest import make_result
    existing_root = DocumentFolder(id=1, name="G", kind=FOLDER_KIND_ROOT, group_id=2)
    existing_common = DocumentFolder(id=2, name="common", kind=FOLDER_KIND_COMMON, group_id=2)
    mock_db.execute.side_effect = [
        make_result(scalar=existing_root), make_result(scalar=existing_common),
    ]
    root, common = await ensure_group_folders(mock_db, SimpleNamespace(id=2, name="G", created_by_user_id=1))
    assert (root, common) == (existing_root, existing_common)
    mock_db.add.assert_not_called()


async def test_member_folder_is_private_and_hangs_under_the_group_root(mock_db):
    from utils.folder_seed import ensure_member_folder
    from tests.unit.conftest import make_result
    root = DocumentFolder(id=1, name="G", kind=FOLDER_KIND_ROOT, group_id=2)
    mock_db.execute.side_effect = [make_result(scalar=None), make_result(scalar=root)]
    folder = await ensure_member_folder(mock_db, SimpleNamespace(id=2, name="G", created_by_user_id=1),
                                        SimpleNamespace(id=7, name="Theo"))
    assert folder.visibility == FOLDER_VISIBILITY_PRIVATE
    assert folder.kind == FOLDER_KIND_USER
    assert folder.user_id == 7
    assert folder.parent_id == 1
```

- [ ] **Step 2: Run and watch it fail**, then implement `backend/utils/folder_seed.py` with both functions, each doing `SELECT ... WHERE group_id == group.id AND kind == ...` first and only adding when absent, followed by `await db.flush()` so the caller gets ids.

- [ ] **Step 3: Wire it in** — `create_group` calls `ensure_group_folders` then `ensure_member_folder` for the creator; `approve_join_request` calls `ensure_member_folder` for the approved user (replacing the no-op stub from A3).

- [ ] **Step 4: Run, perturb (make the SELECT return `None` unconditionally → the idempotence test fails), commit.**

```bash
git add backend && git commit -m "Seed each group with a root, a common folder, and a private folder per member"
```

---

### Task C3: Stamp document visibility from its folder

**Files:**
- Create: `backend/utils/folder_placement.py`
- Modify: `backend/routers/folders.py` (move + dissolve), `backend/routers/rag.py` (`move_document`), `backend/services/document_indexing_service.py` (`save_uploaded_document`)
- Test: `backend/tests/unit/test_folder_placement.py`

**Interfaces:**
- Consumes: folder constants (A1), `_visible` (C1).
- Produces: `placement_group_id(folder: DocumentFolder | None) -> int | None`; `async apply_subtree_placement(db, folder) -> int` returning the number of documents re-stamped.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_folder_placement.py
from models.document_folder import DocumentFolder, FOLDER_VISIBILITY_PRIVATE, FOLDER_VISIBILITY_GROUP
from utils.folder_placement import placement_group_id


def test_a_document_at_the_library_root_is_private():
    assert placement_group_id(None) is None


def test_a_document_in_a_private_folder_is_owner_only():
    f = DocumentFolder(id=1, user_id=7, group_id=2, visibility=FOLDER_VISIBILITY_PRIVATE)
    assert placement_group_id(f) is None, \
        "group_id must be cleared, or the whole group reads a private document"


def test_a_document_in_a_group_folder_is_readable_by_that_group():
    f = DocumentFolder(id=1, user_id=7, group_id=2, visibility=FOLDER_VISIBILITY_GROUP)
    assert placement_group_id(f) == 2


async def test_moving_a_folder_restamps_every_document_beneath_it(mock_db):
    """Dragging a subtree from `common` into a private folder must not leave the
    documents inside it group-readable."""
    from utils.folder_placement import apply_subtree_placement
    ...  # asserts an UPDATE ... WHERE folder_id IN (subtree ids) SET group_id = NULL is issued
```

- [ ] **Step 2: Run, watch it fail, implement.** `apply_subtree_placement` walks children breadth-first from the folder table, propagates `visibility`/`group_id` down, and issues one `UPDATE rag_documents SET group_id = ... WHERE folder_id = ANY(...)` per distinct placement value.

- [ ] **Step 3: Call it** from `update_folder` (after a move), `delete_folder` (documents dissolving upward take the parent's placement), `move_document`, and `save_uploaded_document`.

- [ ] **Step 4: Run, perturb (skip the call in `update_folder` → the subtree test fails), verify against the real database, commit.**

```bash
git add backend && git commit -m "Re-stamp document visibility whenever its folder placement changes"
```

---

### Task C4: Leaving a group keeps your private folder

**Files:**
- Modify: `backend/routers/groups.py` (`leave_group`, `kick_member`)
- Test: `backend/tests/unit/test_folder_visibility.py` (extend)

**Interfaces:**
- Consumes: `ensure_member_folder` (C2), folder constants (A1).
- Produces: `async detach_member_folder(db, group_id, user_id) -> None` in `utils/folder_seed.py`.

- [ ] **Step 1: Write the failing test** asserting that after `detach_member_folder`, the folder has `parent_id is None`, `group_id is None`, `kind == "custom"`, and `visibility == "private"` — otherwise it hangs under a root the ex-member can no longer see and disappears from their tree while still holding their files.
- [ ] **Step 2: Run, watch it fail, implement, call it from both `leave_group` and `kick_member`.**
- [ ] **Step 3: Run, perturb, commit.**

```bash
git add backend && git commit -m "Detach a member's private folder when they leave a group"
```

---

# Phase D — MCP, frontend, migration

### Task D1: Folder and group scoping on the search endpoints

**Files:**
- Modify: `backend/routers/rag.py` (`/search`, `/search/documents`, `/documents`), `backend/services/rag_service.py` (`search_documents`, `_search_pages_by_embedding`, `search_documents_metadata`)
- Test: `backend/tests/unit/test_rag_search_scoping.py`

**Interfaces:**
- Consumes: `_visible` (C1), `get_user_group_ids` (A2).
- Produces: `async resolve_folder_scope(db, folder_ids, include_subfolders, group_ids, user_id) -> list[int] | None` in `backend/utils/folder_placement.py`; search functions gain `folder_ids: Optional[Sequence[int]] = None`.

- [ ] **Step 1: Write the failing test** — asserting (a) omitting the params searches the caller's whole readable scope, (b) `folder_ids=[3]` with `include_subfolders=True` expands to the subtree, (c) an unreadable folder id is dropped rather than trusted, (d) the folder filter is bound as a parameter (`= ANY(:folder_ids)`), never string-interpolated.
- [ ] **Step 2: Run, watch it fail, implement.** Subtree expansion happens in Python against `_visible`-scoped rows, so the resulting id list is already authorized; the raw SQL then only adds `AND d.folder_id = ANY(:folder_ids)`.
- [ ] **Step 3: Run, perturb (drop the `_visible` filter in `resolve_folder_scope` → the unreadable-folder test fails), commit.**

```bash
git add backend && git commit -m "Let document search be scoped to folders or groups"
```

---

### Task D2: MCP surface

**Files:**
- Modify: `mcp-server/maptalk_mcp/tools.yaml`, `handlers.py`, `server.py`
- Test: `mcp-server/tests/test_registry.py`, `test_protocol.py`, `test_app_control_tools.py`

**Interfaces:**
- Consumes: the endpoints from D1, A3 and A4.
- Produces: tools `list_groups`, `list_join_requests`, `request_group_join`, `assign_experiment_group`; `search_documents` and `find_documents` accept `folder_ids` (array of integer), `include_subfolders` (boolean, default true), `group_ids` (array of integer); `SERVER_VERSION = "3.0.0"`.

- [ ] **Step 1: Write the failing test** pinning the new tool-name set, `SERVER_VERSION`, that `find_documents` no longer declares `in_folder`, and that array params are encoded as repeated query params (`?folder_ids=3&folder_ids=4`), not as a JSON string.
- [ ] **Step 2: Run, watch it fail, implement.** Approve/reject are deliberately absent — they sit behind `require_interactive_user` and would return 403 to a connector token.
- [ ] **Step 3: Run the MCP suite, commit.**

```bash
cd mcp-server && .venv/bin/python -m pytest -q
git add mcp-server && git commit -m "Expose group membership and folder scoping to the agent"
```

---

### Task D3: Frontend

**Files:**
- Modify: `frontend/app/documents/page.tsx` and its folder components, `frontend/app/dashboard/settings/page.tsx`, `frontend/lib/api.ts`, `frontend/messages/en.json`, `frontend/messages/fr.json`
- Test: `frontend/e2e/unit/folderTree.spec.ts`

**Interfaces:**
- Consumes: the enriched `GET /api/rag/folders` payload (C1) and the join-request endpoints (A3).
- Produces: `buildFolderTree(folders: FolderNode[]): TreeRoot[]` in `frontend/components/documents/folderTree.ts`.

- [ ] **Step 1: Write the failing unit test** for `buildFolderTree`: group roots sort first, `common` before private folders, orphaned nodes (parent not visible) surface at the root instead of vanishing, and counts aggregate up the tree.
- [ ] **Step 2: Run `npm run test:unit`, watch it fail, implement.**
- [ ] **Step 3: Build the UI** — visibility badge on private folders, an approval queue for admins, "request to join" on groups the user is not in. Every string via `useTranslations`, added to both message files.
- [ ] **Step 4: `npx tsc --noEmit` and `npm run test:unit` clean, commit.**

```bash
git add frontend && git commit -m "Show the group-rooted document tree and the join-request queue"
```

---

### Task D4: Production migration

**Files:**
- Create: `backend/scripts/migrate_multi_group_folders.py`

**Interfaces:**
- Consumes: `ensure_group_folders`, `ensure_member_folder` (C2).
- Produces: an idempotent script safe to run repeatedly.

- [ ] **Step 1: Write the script** — for every existing group, seed root + `common` + a private folder per member; re-parent existing top-level custom folders under their group's root keeping `visibility='group'`; create `UTIA ZOI` owned by user 1 with a membership, seeded folders included; set `group_members.role='admin'` for user 1 in every group.
- [ ] **Step 2: Dry-run it** against a copy of the schema in the test compose stack (`docker-compose.test.yml`), asserting the second run is a no-op.
- [ ] **Step 3: Back up the production database**, then run it inside `maptimize-backend`.
- [ ] **Step 4: Verify by query** — every group has exactly one root and one `common`; every member has exactly one private folder; the 18 existing documents still carry `group_id = 2`; user 1 has two memberships, both `role='admin'`.
- [ ] **Step 5: Commit.**

---

### Task D5: Full verification

- [ ] **Step 1:** `bash run-coverage.sh` — whole backend suite green, coverage not regressed.
- [ ] **Step 2:** `cd mcp-server && .venv/bin/python -m pytest -q`.
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit && npm run test:unit`.
- [ ] **Step 4:** Rebuild and deploy with `docker-compose.prod.yml`, then exercise the live path through the connector: `list_groups → request_group_join → list_folders → search_documents(folder_ids=...) → assign_experiment_group`, confirming a colleague's private folder never appears.
- [ ] **Step 5:** Trigger one UMAP recompute and confirm the dashboard renders.
- [ ] **Step 6:** Update `CLAUDE.md` — multi-group ACL, the folder visibility rule and its four mirrors, seeded-folder immutability, why approval is outside MCP, and the global UMAP fit.

---

## Self-Review

**Spec coverage:** multi-group (A1, A2) · roles + approval (A3) · adoption replacement (A4) · UMAP (B1) · folder visibility and inheritance (C1) · seeding (C2) · document stamping (C3) · leave/kick (C4) · MCP scoping and tree (D1, D2) · frontend (D3) · production data (D4) · verification and docs (D5). No spec section is unclaimed.

**Type consistency:** `get_user_group_ids` returns `list[int]` and every consumer names the local `group_ids`; `placement_group_id` returns `int | None`; `ensure_group_folders` returns `(root, common)` and `ensure_member_folder` a single folder; folder constants are imported from `models.document_folder` everywhere, never re-declared.

**Ordering caveat:** A3 references `ensure_member_folder`, which C2 creates. Executed in plan order, A3 defines it as a no-op and C2 replaces that definition — it must not end up defined twice.
