# Group-shared RAG documents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make library-uploaded RAG documents readable by every member of the uploader's lab group, exactly mirroring how experiments are shared via `group_id`; chat-thread attachments stay private; owner alone can delete/reindex.

**Architecture:** Add a `group_id` FK to `rag_documents` (like `experiments.group_id`). Widen the existing SSOT read filter `document_scope()` and add a fetch-by-id filter `document_read_scope()`, both in `models/rag_document.py`. Thread the caller's `group_id` (resolved via `get_user_group_id`, already resolved once-per-turn in the agent) into every document read site — routers, `rag_service`, and the agent's document tools + raw-SQL injector. Writes stay owner-only. Backfill existing library docs and adopt orphans on group join. Surface `is_owner` to the frontend so non-owners get a "Shared" badge and no delete/reindex controls.

**Tech Stack:** FastAPI + SQLAlchemy async (asyncpg) + pgvector; home-grown additive migration in `backend/database.py`; Next.js/React + next-intl frontend; pytest unit tests (`backend/tests/unit/`, mocked DB, offline CPU per the coverage harness).

## Global Constraints

- **Prod environment.** Additive schema changes only (`ADD COLUMN IF NOT EXISTS` + savepoint-guarded backfill). Never destructive. (CLAUDE.md)
- **SSOT/DRY.** Every document read goes through `document_scope()` or `document_read_scope()` in `models/rag_document.py`. Never re-introduce a bare `RAGDocument.user_id == user_id` at a read site.
- **Library-only sharing.** Only documents with `thread_id IS NULL` are group-shared. Chat attachments (`thread_id` set) stay strictly owner-scoped and are stamped with `group_id = None`.
- **Read-only for the group.** `delete_document` / `reindex_document` keep their bare `user_id == user_id` filter — do not widen writes.
- **Fail-closed.** When `group_id is None`, every filter degrades to owner-only.
- **i18n.** No hardcoded UI strings. Every new string added to BOTH `frontend/messages/en.json` and `frontend/messages/fr.json`. (CLAUDE.md)
- **Model id / config** unchanged (no touch to `settings.gemini_model`).
- **Tests:** unit tests live in `backend/tests/unit/`, run offline/CPU-only, use `mock_db` + `make_result` from `tests/unit/conftest.py`; `asyncio_mode=auto` (plain `async def`). Keep the full suite green (~99% line coverage target).

**Test command (used throughout):**
```bash
docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v
```
(If the dev stack isn't up: `docker compose -f docker-compose.dev.yml up -d backend`. The full gated suite is `bash run-coverage.sh` from the repo root.)

---

### Task 1: Add `group_id` column, model field, and backfill

**Files:**
- Modify: `backend/models/rag_document.py` (add mapped column to `RAGDocument`)
- Modify: `backend/database.py` (add column to `updates` list ~line 157; add backfill block ~after line 220)
- Test: `backend/tests/unit/test_document_acl.py` (create)

**Interfaces:**
- Produces: `RAGDocument.group_id: Optional[int]` column (FK `groups.id`, `ON DELETE SET NULL`, indexed, nullable).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_document_acl.py`:
```python
"""Unit tests for group-shared RAG document access control."""
from models.rag_document import RAGDocument


def test_rag_document_has_group_id_column():
    col = RAGDocument.__table__.columns.get("group_id")
    assert col is not None, "rag_documents needs a group_id column"
    assert col.nullable is True
    # FK targets groups.id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "groups"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: FAIL — `AssertionError: rag_documents needs a group_id column`

- [ ] **Step 3: Add the mapped column to the model**

In `backend/models/rag_document.py`, inside `class RAGDocument`, immediately after the `thread_id` column (ends line 77), add:
```python
    # NULL = not shared; set = readable by every member of this group (library
    # uploads only). Stamped at creation for thread_id IS NULL docs; attachments
    # keep it NULL. Mirrors Experiment.group_id. ON DELETE SET NULL so deleting a
    # group orphans the doc back to owner-only rather than deleting it.
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
```
(`ForeignKey` and `Optional` are already imported in this file.)

- [ ] **Step 4: Add the column + backfill to `ensure_schema_updates()`**

In `backend/database.py`, add to the `updates` list (right after the `rag_documents`/`truncated_from_pages` entry at line 156):
```python
            # Group support for shared library documents (thread_id IS NULL only)
            ("rag_documents", "group_id", "INTEGER REFERENCES groups(id) ON DELETE SET NULL"),
```

Then add a backfill block right after the existing `backfill_user_id` block (after line 220, before the enum section comment at line 222):
```python
        # Backfill group_id for existing LIBRARY documents (thread_id IS NULL).
        # Stamp each with its owner's group so lab members see docs uploaded
        # before this feature existed. Attachments (thread_id set) stay private.
        try:
            await conn.execute(text("SAVEPOINT backfill_doc_group"))
            await conn.execute(text("""
                UPDATE rag_documents SET group_id = gm.group_id
                FROM group_members gm
                WHERE rag_documents.user_id = gm.user_id
                  AND rag_documents.thread_id IS NULL
                  AND rag_documents.group_id IS NULL
            """))
            await conn.execute(text("RELEASE SAVEPOINT backfill_doc_group"))
            logger.info("Backfilled group_id on library rag_documents")
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT backfill_doc_group"))
            logger.debug(f"Backfill rag_documents.group_id skipped: {e}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/models/rag_document.py backend/database.py backend/tests/unit/test_document_acl.py
git commit -m "maptalk: add group_id column + backfill for shared library documents"
```

---

### Task 2: Widen `document_scope`, add `document_read_scope` (SSOT filters)

**Files:**
- Modify: `backend/models/rag_document.py:21-38` (extend `document_scope`; add `document_read_scope`)
- Test: `backend/tests/unit/test_document_acl.py` (extend)

**Interfaces:**
- Consumes: `RAGDocument.group_id` (Task 1).
- Produces:
  - `document_scope(user_id: int, thread_id: Optional[int] = None, group_id: Optional[int] = None) -> ColumnElement` — listing/search scope. Library branch widens to `owner OR group`; attachment branch stays owner-only.
  - `document_read_scope(user_id: int, group_id: Optional[int] = None) -> ColumnElement` — fetch-by-id scope: `owner's own (any) OR group-shared library`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_document_acl.py`:
```python
from models.rag_document import document_scope, document_read_scope


def _sql(clause):
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_document_scope_library_widens_to_group():
    sql = _sql(document_scope(user_id=1, thread_id=None, group_id=7))
    assert "rag_documents.group_id" in sql          # group OR present
    assert "rag_documents.user_id" in sql
    assert "rag_documents.thread_id IS NULL" in sql  # still library-only


def test_document_scope_owner_only_without_group():
    sql = _sql(document_scope(user_id=1, thread_id=None, group_id=None))
    assert "group_id" not in sql                     # fail-closed to owner


def test_document_scope_thread_group_shares_library_not_attachments():
    # thread context: library shared to group, but the group term must be gated
    # by thread_id IS NULL so another member's attachment can never appear.
    sql = _sql(document_scope(user_id=1, thread_id=5, group_id=7))
    assert "rag_documents.group_id" in sql
    assert "rag_documents.thread_id = 5" in sql      # own attachments still visible


def test_document_read_scope_group_shares_library_only():
    sql = _sql(document_read_scope(user_id=1, group_id=7))
    assert "rag_documents.user_id" in sql            # owner sees own (incl. attachments)
    assert "rag_documents.group_id" in sql           # + group-shared library
    assert "rag_documents.thread_id IS NULL" in sql  # group term gated to library


def test_document_read_scope_owner_only_without_group():
    sql = _sql(document_read_scope(user_id=1, group_id=None))
    assert "group_id" not in sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: FAIL — `document_read_scope` import error / `document_scope() got an unexpected keyword argument 'group_id'`

- [ ] **Step 3: Rewrite the two SSOT filters**

Replace `document_scope` (lines 21-38 of `backend/models/rag_document.py`) with:
```python
def _library_visible(user_id: int, group_id: Optional[int]) -> ColumnElement:
    """Who may see a LIBRARY document (thread_id IS NULL): owner, or -- when the
    caller is in a group -- any member of that group. group_id=None -> owner only."""
    if group_id is None:
        return RAGDocument.user_id == user_id
    return or_(RAGDocument.user_id == user_id, RAGDocument.group_id == group_id)


def document_scope(
    user_id: int,
    thread_id: Optional[int] = None,
    group_id: Optional[int] = None,
) -> ColumnElement:
    """SSOT for which documents a caller may see in a LISTING or SEARCH.

    Library documents (thread_id IS NULL) are shared group-wide; chat attachments
    belong to their thread and stay owner-private, so they never widen to a group.

    ``thread_id=None`` -> the shared library only.
    ``thread_id=N``    -> the shared library PLUS the caller's OWN attachments in N.
    ``group_id=None``  -> owner-only (fail-closed).

    Every listing/search query that scopes RAGDocument goes through this. Mirrors
    the ``experiment_owner_filter`` pattern in utils/groups.py.
    """
    library = and_(RAGDocument.thread_id.is_(None), _library_visible(user_id, group_id))
    if thread_id is None:
        return library
    own_attachment = and_(
        RAGDocument.user_id == user_id,
        RAGDocument.thread_id == thread_id,
    )
    return or_(library, own_attachment)


def document_read_scope(user_id: int, group_id: Optional[int] = None) -> ColumnElement:
    """SSOT for a single-document FETCH BY ID (serve pdf/pages, read content,
    extract region, cached passage).

    The owner may fetch any of their own documents -- including their own chat
    attachments, needed to serve attachment pages in the viewer. A group member
    may additionally fetch a group-shared LIBRARY document. ``group_id=None`` ->
    owner-only (fail-closed). Writes must NOT use this -- they stay owner-only.
    """
    owner = RAGDocument.user_id == user_id
    if group_id is None:
        return owner
    shared_library = and_(
        RAGDocument.thread_id.is_(None),
        RAGDocument.group_id == group_id,
    )
    return or_(owner, shared_library)
```
(`and_`, `or_` are already imported at the top of the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/models/rag_document.py backend/tests/unit/test_document_acl.py
git commit -m "maptalk: widen document_scope to group + add document_read_scope (SSOT)"
```

---

### Task 3: Stamp `group_id` on new library uploads

**Files:**
- Modify: `backend/services/document_indexing_service.py:58-125` (`save_uploaded_document`)
- Test: `backend/tests/unit/test_document_acl.py` (extend)

**Interfaces:**
- Consumes: `get_user_group_id(user_id, db)` from `utils/groups.py`.
- Produces: new `RAGDocument.group_id` set to the owner's group iff `thread_id is None`; else `None`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_document_acl.py`:
```python
from unittest.mock import AsyncMock, patch
import services.document_indexing_service as dis


async def test_library_upload_is_stamped_with_group(mock_db, tmp_path):
    with patch.object(dis, "get_user_group_id", AsyncMock(return_value=7)), \
         patch.object(dis.settings, "rag_document_dir", tmp_path):
        doc = await dis.save_uploaded_document(
            user_id=1, filename="paper.pdf", content=b"%PDF-1.4",
            db=mock_db, thread_id=None,
        )
    assert doc.group_id == 7


async def test_attachment_upload_is_not_stamped(mock_db, tmp_path):
    with patch.object(dis, "get_user_group_id", AsyncMock(return_value=7)), \
         patch.object(dis.settings, "rag_document_dir", tmp_path):
        doc = await dis.save_uploaded_document(
            user_id=1, filename="paper.pdf", content=b"%PDF-1.4",
            db=mock_db, thread_id=99,
        )
    assert doc.group_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: FAIL — `assert None == 7` (group_id not yet stamped)

- [ ] **Step 3: Add the import and stamp logic**

In `backend/services/document_indexing_service.py`, add near the top-level imports (with the other `from utils...`/`from models...` imports):
```python
from utils.groups import get_user_group_id
```
Then in `save_uploaded_document`, replace the DB-record creation (lines 111-120) with:
```python
    # Library uploads (thread_id IS NULL) are shared with the owner's lab group;
    # chat attachments stay private (group_id stays None). Mirrors experiment
    # group stamping in routers/experiments.py::create_experiment.
    group_id = await get_user_group_id(user_id, db) if thread_id is None else None

    # Create DB record
    document = RAGDocument(
        user_id=user_id,
        thread_id=thread_id,
        group_id=group_id,
        name=filename,
        file_type=file_type,
        original_path=str(original_path),
        status=DocumentStatus.PENDING.value,
        file_size=len(content),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/document_indexing_service.py backend/tests/unit/test_document_acl.py
git commit -m "maptalk: stamp owner's group_id on new library document uploads"
```

---

### Task 4: `adopt_orphan_documents` on group join

**Files:**
- Modify: `backend/utils/groups.py` (add `adopt_orphan_documents`)
- Modify: `backend/routers/groups.py` (call it beside `adopt_orphan_experiments` at the join sites ~lines 120, 334)
- Test: `backend/tests/unit/test_document_acl.py` (extend)

**Interfaces:**
- Produces: `adopt_orphan_documents(db: AsyncSession, user_id: int, group_id: int) -> int` — stamps the joiner's group-less LIBRARY docs; returns rowcount. Caller commits.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_document_acl.py`:
```python
import utils.groups as groups_util
from tests.unit.conftest import make_result


async def test_adopt_orphan_documents_only_touches_library(mock_db):
    mock_db.execute = AsyncMock(return_value=make_result(rowcount=3))
    n = await groups_util.adopt_orphan_documents(mock_db, user_id=1, group_id=7)
    assert n == 3
    # the UPDATE must be gated to library docs (thread_id IS NULL) and orphans
    stmt = mock_db.execute.call_args.args[0]
    sql = str(stmt).lower()
    assert "thread_id is null" in sql
    assert "group_id is null" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: FAIL — `AttributeError: module 'utils.groups' has no attribute 'adopt_orphan_documents'`

- [ ] **Step 3: Implement `adopt_orphan_documents`**

In `backend/utils/groups.py`, add the import at the top (next to `from models.experiment import Experiment`):
```python
from models.rag_document import RAGDocument
```
Then append after `adopt_orphan_experiments`:
```python
async def adopt_orphan_documents(
    db: AsyncSession,
    user_id: int,
    group_id: int,
) -> int:
    """Share the joiner's group-less LIBRARY documents with the group they joined.

    Library docs are stamped with group_id at upload, so anything uploaded before
    the owner had a group keeps group_id NULL and is invisible to peers. Adopting
    them makes the joiner's existing library visible group-wide, matching
    adopt_orphan_experiments. Attachments (thread_id set) are never adopted -- they
    stay private to their conversation. Callers must commit.

    Returns the number of documents adopted.
    """
    result = await db.execute(
        update(RAGDocument)
        .where(
            RAGDocument.user_id == user_id,
            RAGDocument.thread_id.is_(None),
            RAGDocument.group_id.is_(None),
        )
        .values(group_id=group_id)
    )
    return result.rowcount
```
(`update` is already imported in this file.)

- [ ] **Step 4: Wire it into the group-join router**

In `backend/routers/groups.py`, find each place `adopt_orphan_experiments(...)` is awaited (near lines 120 and 334) and add the import + call. Update the import line:
```python
from utils.groups import (
    get_user_group_id,
    adopt_orphan_experiments,
    adopt_orphan_documents,
)
```
(merge with the existing import from `utils.groups`; keep whatever else it already imports). Then immediately after each `await adopt_orphan_experiments(db, <user>, <group>)` call, add on the next line:
```python
        await adopt_orphan_documents(db, <user>, <group>)
```
using the SAME `<user>` and `<group>` argument expressions the adjacent `adopt_orphan_experiments` call uses. Do not add a new commit — it rides the existing transaction/commit that already follows the experiment adoption.

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/utils/groups.py backend/routers/groups.py backend/tests/unit/test_document_acl.py
git commit -m "maptalk: adopt orphan library documents into group on join"
```

---

### Task 5: Group-aware document SEARCH (`rag_service`)

**Files:**
- Modify: `backend/services/rag_service.py` — `search_documents` (signature + `has_indexed` pre-check ~86-94 + raw SQL WHERE ~130-150) and `combined_search` (~290-297, forward `group_id`)
- Test: `backend/tests/unit/test_document_acl.py` (extend)

**Interfaces:**
- Consumes: caller-supplied `group_id`.
- Produces:
  - `search_documents(query, user_id, db, limit=None, include_text=True, document_ids=None, thread_id=None, group_id=None)`
  - `combined_search(query, user_id, db, experiment_id=None, doc_limit=None, fov_limit=None, group_id=None)`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_document_acl.py`:
```python
import services.rag_service as rag_service


async def test_search_documents_widens_precheck_to_group(mock_db):
    # No own indexed pages, but group has some -> pre-check must still find them.
    # We assert the pre-check SQL and its params include the group term.
    calls = []

    async def fake_execute(stmt, params=None):
        calls.append((str(stmt), params or {}))
        return make_result(first=None)  # pre-check returns "nothing" -> early []

    mock_db.execute = fake_execute
    out = await rag_service.search_documents(
        query="x", user_id=1, db=mock_db, thread_id=None, group_id=7,
    )
    assert out == []
    precheck_sql, precheck_params = calls[0]
    assert "group_id" in precheck_sql.lower()
    assert precheck_params.get("group_id") == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: FAIL — `search_documents() got an unexpected keyword argument 'group_id'`

- [ ] **Step 3: Widen `search_documents`**

In `backend/services/rag_service.py`, add `group_id: Optional[int] = None` to the `search_documents` signature (after `thread_id`).

Introduce a shared owner-clause helper just above the `has_indexed` pre-check (replace lines 85-96). The clause widens LIBRARY docs to the group; attachments (group_id NULL) match only via `user_id`:
```python
    # Library docs are group-shared; attachments (group_id NULL) match only the
    # owner. SSOT mirror of models.rag_document.document_read_scope, expressed in
    # raw SQL for the pgvector query.
    if group_id is not None:
        owner_clause = "(rd.user_id = :user_id OR (rd.thread_id IS NULL AND rd.group_id = :group_id))"
    else:
        owner_clause = "rd.user_id = :user_id"

    # Skip the (expensive) embedding-model load when the caller has nothing to search.
    precheck_params = {"user_id": user_id}
    if group_id is not None:
        precheck_params["group_id"] = group_id
    has_indexed = await db.execute(
        text(
            "SELECT 1 FROM rag_document_pages rdp "
            "JOIN rag_documents rd ON rd.id = rdp.document_id "
            f"WHERE {owner_clause} AND rd.status = 'completed' "
            "AND rdp.embedding IS NOT NULL LIMIT 1"
        ),
        precheck_params,
    )
    if has_indexed.first() is None:
        return []
```

Then in the `params` dict (currently lines 108-112) add the group binding, and use `owner_clause` in the main query. Replace the `params = {...}` block with:
```python
        params = {
            "embedding": str(embedding_list),
            "user_id": user_id,
            "limit": limit,
        }
        if group_id is not None:
            params["group_id"] = group_id
```
And in `query_sql` (line 130-150) replace the line `WHERE rd.user_id = :user_id` with:
```python
            WHERE {owner_clause}
```
(The existing `{scope_filter}` for `thread_id` stays as-is — combined with `owner_clause` it still keeps another member's attachment out, because attachments never carry a `group_id`.)

- [ ] **Step 4: Forward `group_id` through `combined_search`**

In `combined_search` (line 290), add `group_id: Optional[int] = None` to the signature (after `fov_limit`). Find the internal call to `search_documents(...)` inside `combined_search` and add `group_id=group_id` to it. (Leave `search_fov_images` untouched — FOV image sharing is out of scope.)

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/services/rag_service.py backend/tests/unit/test_document_acl.py
git commit -m "maptalk: group-share document semantic search (rag_service)"
```

---

### Task 6: Group-aware document FETCH-BY-ID (`rag_service`)

**Files:**
- Modify: `backend/services/rag_service.py` — `get_document_content` (~456-492), `get_all_documents_summary` (~543-565), `_get_document_page_image_path` (~673-695), `get_cached_passage` (~834-861), `extract_passage_image` (~710-717), `extract_relevant_passages` (~879-886)
- Test: `backend/tests/unit/test_document_acl.py` (extend)

**Interfaces:**
- Consumes: `document_read_scope(user_id, group_id)` (Task 2).
- Produces: each function above gains a trailing `group_id: Optional[int] = None` param and swaps its `RAGDocument.user_id == user_id` filter for `document_read_scope(user_id, group_id)`. `extract_passage_image` / `extract_relevant_passages` forward `group_id` to `_get_document_page_image_path`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_document_acl.py`:
```python
from sqlalchemy import select as _select


async def test_get_document_content_uses_read_scope(mock_db):
    captured = {}

    async def fake_execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return make_result(scalar=None)  # not found -> returns None, fine

    mock_db.execute = fake_execute
    out = await rag_service.get_document_content(
        document_id=5, user_id=1, db=mock_db, group_id=7,
    )
    assert out is None
    assert "rag_documents.group_id" in captured["sql"]  # group widening applied
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: FAIL — `get_document_content() got an unexpected keyword argument 'group_id'`

- [ ] **Step 3: Add the import and widen each function**

In `backend/services/rag_service.py`, ensure the import includes `document_read_scope`:
```python
from models.rag_document import RAGDocument, RAGDocumentPage, document_read_scope
```
(merge with the existing `from models.rag_document import ...` line; keep the other names it already imports.)

For each function, add `group_id: Optional[int] = None` as the last parameter and replace its filter:

`get_document_content` — replace the `.where(RAGDocument.id == document_id, RAGDocument.user_id == user_id)` (lines 485-488) with:
```python
        .where(RAGDocument.id == document_id)
        .where(document_read_scope(user_id, group_id))
```

`get_all_documents_summary` — replace `.where(RAGDocument.user_id == user_id, RAGDocument.status == "completed")` (lines 562-565) with:
```python
        .where(document_read_scope(user_id, group_id))
        .where(RAGDocument.status == "completed")
```

`_get_document_page_image_path` — replace `.where(RAGDocument.id == document_id, RAGDocument.user_id == user_id)` (lines 687-690) with:
```python
        .where(RAGDocument.id == document_id)
        .where(document_read_scope(user_id, group_id))
```

`get_cached_passage` — replace `.where(RAGDocument.id == document_id, RAGDocument.user_id == user_id)` (lines 854-857) with:
```python
        select(RAGDocument.id).where(
            RAGDocument.id == document_id
        ).where(document_read_scope(user_id, group_id))
```
(Leave `_get_passages_cache_path(user_id)` as the CALLER's cache dir — a member who extracts a shared doc's passage caches it under their own id and serves it back from there.)

`extract_passage_image` — after adding `group_id` to its signature, forward it in its internal `_get_document_page_image_path(...)` call: add `group_id=group_id`.

`extract_relevant_passages` — same: add `group_id` to the signature and pass `group_id=group_id` into its internal `_get_document_page_image_path(...)` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/rag_service.py backend/tests/unit/test_document_acl.py
git commit -m "maptalk: group-share document fetch-by-id reads (rag_service)"
```

---

### Task 7: Routers group-aware + `is_owner` in response

**Files:**
- Modify: `backend/schemas/chat.py:126-140` (`RAGDocumentResponse` — add `is_owner`)
- Modify: `backend/routers/rag.py` — `get_document_for_user` (120-138), `list_documents` (143-165), `search`/`search_documents_only`/`search_within_document` (~488, 529, 563)
- Test: `backend/tests/unit/test_document_acl.py` (extend)

**Interfaces:**
- Consumes: `get_user_group_id` (utils/groups), `document_read_scope`/`document_scope`, `RAGDocumentResponse`.
- Produces: `get_document_for_user(db, document_id, user_id, group_id=None)`; list response items carry `is_owner: bool`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_document_acl.py`:
```python
import routers.rag as rag_router


async def test_get_document_for_user_widens_to_group(mock_db):
    captured = {}

    async def fake_execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return make_result(scalar=object())  # found -> returns the doc

    mock_db.execute = fake_execute
    await rag_router.get_document_for_user(mock_db, document_id=5, user_id=1, group_id=7)
    assert "rag_documents.group_id" in captured["sql"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: FAIL — `get_document_for_user() got an unexpected keyword argument 'group_id'`

- [ ] **Step 3: Add `is_owner` to the schema**

In `backend/schemas/chat.py`, add to `RAGDocumentResponse` (after `indexed_at`, line 137):
```python
    is_owner: bool = True
```

- [ ] **Step 4: Widen the router**

In `backend/routers/rag.py`, ensure `get_user_group_id` and `document_read_scope` are imported (add to the existing imports):
```python
from utils.groups import get_user_group_id
from models.rag_document import RAGDocument, document_scope, document_read_scope
```
(merge with whatever the file already imports from `models.rag_document`).

Rewrite `get_document_for_user` (lines 120-138):
```python
async def get_document_for_user(
    db: AsyncSession,
    document_id: int,
    user_id: int,
    group_id: Optional[int] = None,
) -> RAGDocument:
    """Get a RAG document the caller may READ. Raises 404 if not visible.

    Read scope = owner's own doc OR a group-shared library doc. Callers that must
    mutate (delete/reindex) must NOT rely on this -- they keep an owner-only check.
    """
    result = await db.execute(
        select(RAGDocument).where(
            RAGDocument.id == document_id,
        ).where(document_read_scope(user_id, group_id))
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    return document
```

For every caller of `get_document_for_user` in this file (get/pdf/pages/image/search-within endpoints), resolve the group once and pass it. In each such endpoint, immediately before the `await get_document_for_user(...)` call add:
```python
    group_id = await get_user_group_id(current_user.id, db)
```
and change the call to `await get_document_for_user(db, document_id, current_user.id, group_id)`.

Rewrite `list_documents` body (lines 155-165) to widen + set `is_owner`:
```python
    group_id = await get_user_group_id(current_user.id, db)
    query = select(RAGDocument).where(document_scope(current_user.id, None, group_id))

    if status_filter:
        query = query.where(RAGDocument.status == status_filter)

    query = query.order_by(RAGDocument.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    documents = result.scalars().all()

    responses = []
    for doc in documents:
        resp = RAGDocumentResponse.model_validate(doc)
        resp.is_owner = doc.user_id == current_user.id
        responses.append(resp)
    return responses
```

For the search endpoints (`search`, `search_documents_only`, `search_within_document`), resolve `group_id = await get_user_group_id(current_user.id, db)` and pass `group_id=group_id` into the `combined_search(...)` / `search_documents(...)` calls they make. (`search_within_document` already calls `get_document_for_user` — pass its group there too.)

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/schemas/chat.py backend/routers/rag.py backend/tests/unit/test_document_acl.py
git commit -m "maptalk: group-aware document router + is_owner in list response"
```

---

### Task 8: Thread `group_id` into agent document tools + SQL injector

**Files:**
- Modify: `backend/services/gemini_agent_service.py` — `_inject_user_id_filter` (248-262); document tool sites in `execute_tool` (1604, 1623, 1637, 1641, 1645-1648, 1659-1665, 2427-2434, 2444-2450, 2493-2496)
- Test: `backend/tests/unit/test_document_acl.py` (extend)

**Interfaces:**
- Consumes: the per-turn `group_id` already resolved in `generate_response` (line 1191) and already passed into `execute_tool(..., group_id=...)`.
- Produces: every document tool query widened by `group_id`; `_inject_user_id_filter` widens `rag_documents` like `experiments`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_document_acl.py`:
```python
from services.gemini_agent_service import _inject_user_id_filter


def test_inject_filter_widens_rag_documents_to_group():
    out = _inject_user_id_filter(
        "SELECT * FROM rag_documents", "rag_documents", group_id=7
    )
    assert "rag_documents.user_id = :user_id" in out
    assert "rag_documents.group_id = :group_id" in out


def test_inject_filter_rag_documents_owner_only_without_group():
    out = _inject_user_id_filter("SELECT * FROM rag_documents", "rag_documents")
    assert "group_id" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: FAIL — the group predicate is not injected for `rag_documents`

- [ ] **Step 3: Widen the SQL injector**

In `backend/services/gemini_agent_service.py`, change the condition at lines 259-260:
```python
    if table in ("experiments", "rag_documents") and group_id is not None:
        predicate = f"({table}.user_id = :user_id OR {table}.group_id = :group_id)"
    else:
        predicate = f"{table}.user_id = :user_id"
```
Update the docstring sentence at lines 255-257 to say the widening now covers `experiments` and `rag_documents` (both have a `group_id`); other tables stay owner-scoped. (Safe: attachments carry `group_id = NULL`, so the `OR group_id` term can only match shared library docs.)

- [ ] **Step 4: Thread `group_id` into the document tool calls**

In `execute_tool`, pass the already-in-scope `group_id` into each document read:
- line 1604: `document_scope(user_id, thread_id)` → `document_scope(user_id, thread_id, group_id)`
- line 1623 (`list_documents`): `document_scope(user_id, thread_id)` → `document_scope(user_id, thread_id, group_id)`
- line 1637 (`get_documents_summary`): add `group_id=group_id` to `get_all_documents_summary(...)`
- line 1641 (`semantic_search`): add `group_id=group_id` to `combined_search(...)`
- lines 1645-1648 (`search_documents`): add `group_id=group_id` to `search_documents(...)`
- lines 1659-1665 (`get_document_content`): add `group_id=group_id`
- lines 2427-2434 (`extract_relevant_passages`): add `group_id=group_id`
- lines 2444-2450 (`extract_passage_image`): add `group_id=group_id`
- lines 2493-2496 (`show_document_pages` → `get_document_content`): add `group_id=group_id`

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit/test_document_acl.py -v`
Expected: PASS

- [ ] **Step 6: Run the whole unit suite to catch regressions**

Run: `docker compose -f docker-compose.dev.yml exec -T backend python -m pytest tests/unit -q`
Expected: PASS (no regressions in existing agent/rag tests)

- [ ] **Step 7: Commit**

```bash
git add backend/services/gemini_agent_service.py backend/tests/unit/test_document_acl.py
git commit -m "maptalk: give the agent group-shared document access (tools + SQL)"
```

---

### Task 9: Frontend — "Shared" badge + hide delete/reindex for non-owners

**Files:**
- Modify: `frontend/lib/api.ts:1954-1965` (`RAGDocument` type — add `is_owner`)
- Modify: `frontend/components/chat/DocumentsModal.tsx` (badge + conditional controls)
- Modify: `frontend/messages/en.json` and `frontend/messages/fr.json` (`chat.sharedBadge`)

**Interfaces:**
- Consumes: `is_owner` from the list response (Task 7).
- Produces: non-owner rows show a "Shared" badge and no delete control.

- [ ] **Step 1: Add `is_owner` to the TS type**

In `frontend/lib/api.ts`, in the `RAGDocument` interface (after `indexed_at?: string;`):
```typescript
  is_owner?: boolean;
```
(optional so older cached payloads don't break; treat `undefined` as owned — see Step 3.)

- [ ] **Step 2: Add the i18n string**

In `frontend/messages/en.json`, under the `"chat"` object, add:
```json
    "sharedBadge": "Shared",
```
In `frontend/messages/fr.json`, under the `"chat"` object, add:
```json
    "sharedBadge": "Partagé",
```
(Edit both as plain text; do not round-trip the JSON — see the i18n duplicate-key note in project memory.)

- [ ] **Step 3: Badge + hide delete in `DocumentsModal.tsx`**

In `frontend/components/chat/DocumentsModal.tsx`, in the **completed documents** row (the block around lines 168-208), add a "Shared" badge next to the name and gate the delete button. Replace the name line (line 183) with:
```tsx
                      <div className="flex items-center gap-2 min-w-0">
                        <div className="truncate text-sm font-medium">{doc.name}</div>
                        {doc.is_owner === false && (
                          <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary-500/15 text-primary-400 border border-primary-500/20">
                            {t("sharedBadge")}
                          </span>
                        )}
                      </div>
```
And wrap the delete button (lines 199-205) so it only renders for owners:
```tsx
                      {doc.is_owner !== false && (
                        <button
                          onClick={(e) => handleDeleteDocument(doc, e)}
                          className="p-2 hover:bg-red-500/20 rounded-lg text-text-muted hover:text-red-400 transition-colors"
                          title={tCommon("delete")}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
```
(Do the same guard on the **failed** documents' delete button at lines 150-156 — a non-owner should not delete a peer's failed doc. Wrap it in `{doc.is_owner !== false && ( ... )}`.)

- [ ] **Step 4: Type-check the frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Verify i18n keys parse**

Run: `cd frontend && node -e "JSON.parse(require('fs').readFileSync('messages/en.json')); JSON.parse(require('fs').readFileSync('messages/fr.json')); console.log('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api.ts frontend/components/chat/DocumentsModal.tsx frontend/messages/en.json frontend/messages/fr.json
git commit -m "maptalk: show Shared badge and hide delete for non-owner documents"
```

---

### Task 10: Full-suite verification + simplifier pass

**Files:** none (verification + cleanup only)

- [ ] **Step 1: Run the full backend suite (gated harness)**

Run: `bash run-coverage.sh`
Expected: whole suite green; `backend/coverage.json` shows no regression from baseline (~99%). The new ACL branches in `document_scope`/`document_read_scope`/`_inject_user_id_filter`/`save_uploaded_document`/`adopt_orphan_documents` are covered by `tests/unit/test_document_acl.py`.

- [ ] **Step 2: Run the live agent smoke test (optional; costs money — real Gemini+DB+GPU)**

Run: `docker exec maptimize-backend python /app/tests/run_agent_conversations.py -q "List my documents" -q "Search my documents for fixation protocols"`
Expected: `OK` for both turns; no `FAIL`/`NEAR-CAP`.

- [ ] **Step 3: Run code-simplifier on the changed code (per CLAUDE.md)**

Dispatch the `code-simplifier` agent over the diff (backend `rag_service.py`, `gemini_agent_service.py`, `routers/rag.py`, `models/rag_document.py`, `database.py`, `utils/groups.py`, `document_indexing_service.py`; frontend `DocumentsModal.tsx`) to check DRY/SSOT and simplify. Apply and re-run Step 1.

- [ ] **Step 4: Deploy (prod rebuild — only when the user asks)**

```bash
docker compose -f docker-compose.prod.yml build maptimize-backend maptimize-frontend --no-cache
docker compose -f docker-compose.prod.yml up -d maptimize-backend maptimize-frontend
```
(The `group_id` column + backfill apply automatically at backend startup via `ensure_schema_updates()`.)

---

## Self-Review

**Spec coverage:**
- Schema + model + backfill → Task 1 ✓
- SSOT `document_scope` widen + `document_read_scope` → Task 2 ✓
- Write stamping (library only) → Task 3 ✓
- Orphan adoption on join → Task 4 ✓
- Search reads (raw SQL + pre-check + combined_search) → Task 5 ✓
- Fetch-by-id reads (content/summary/page-path/passage/extract) → Task 6 ✓
- Router widening + `is_owner` → Task 7 ✓
- Agent tools + SQL injector → Task 8 ✓
- Frontend badge + hidden delete + i18n → Task 9 ✓
- Writes stay owner-only (delete/reindex untouched) → Global Constraints + explicitly NOT modified ✓
- Verification + simplifier → Task 10 ✓

**Placeholder scan:** No TBD/TODO; every code step shows concrete code; test bodies are complete.

**Type consistency:** `document_scope(user_id, thread_id, group_id)` and `document_read_scope(user_id, group_id)` are defined in Task 2 and used with those exact signatures in Tasks 5-8. `get_user_group_id`, `adopt_orphan_documents`, `is_owner` names are consistent across tasks. The `group_id` param is threaded with the same name everywhere.

**Known coupling to verify during execution:** `combined_search`, `search_within_document`, and the exact caller list of `get_document_for_user` in `routers/rag.py` — the plan says "find every caller" for these because the router has several endpoints using the helper; the executing agent must grep for `get_document_for_user(` and update each call.
