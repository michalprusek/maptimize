# Multi-group membership, folder visibility, and MCP library scoping

**Date:** 2026-07-31
**Status:** approved, ready for planning

## Problem

Michal wants a second group (`UTIA ZOI`) alongside `Dr. Janke Lab`, with colleagues
joining by request and an admin approving. Each group should carry a shared `common`
folder plus one private folder per member. The MCP agent should, by default, search
everything the user can read across *all* their groups, but be able to narrow to a
folder or a group, and should be able to see the folder tree.

What actually exists today:

| Requirement | State |
|---|---|
| A second group | `group_members` carries `UNIQUE (user_id)` — **one group per user**. Michal is a `member` of Dr. Janke Lab (id 2, 12 members). |
| Admin approves joins | Does not exist. `POST /api/groups/{id}/join` admits anyone instantly. |
| Michal admin everywhere | Global `users.role = ADMIN` already. But `group_members.role` is decorative: `update_group`, `delete_group`, `kick_member` all check `created_by_user_id` (Filip Šroubek, user 2). |
| `common` + private folders | `document_folders` exists (tree, dissolve-on-delete) but is wholly group-shared: `_visible()` is `user_id == me OR group_id == my group`. Production holds one folder ("Danilo", 18 documents). |
| MCP folder scoping | `find_documents` has `folder_id` + `in_folder`. `search_documents` — the semantic search the agent actually uses — has no folder filter. |
| MCP group scoping | No such concept. |
| Tree for the agent | `list_folders` returns a flat `(id, name, parent_id)` with no counts, owner, or group. |

## Decisions

Taken with the user during brainstorming:

1. **Multi-group membership** — a user may belong to several groups.
2. **UMAP switches to a global fit**, filtered at read time.
3. **Folder tree per group**: a real group-root folder containing `common` plus one
   private folder per member.
4. **Join by request, approved by a group admin.** No email (the backend has no SMTP).
5. **Automatic adoption of group-less experiments/documents is removed.** Sharing
   becomes an explicit act.
6. **MCP scoping via parameters on the existing tools** plus an enriched `list_folders`.
   No new tree tool, no path-string scoping.
7. **Context caps stay** (`read_document_pages` 10 pages/call, `search_documents` 10
   images / 50 refs). Reach is solved by navigation, not by bigger single responses.

### Why the UMAP has to change

Crop coordinates live in `cell_crops.umap_x/umap_y` — *one shared projection per
scope*, which is why `refresh_scope_key()` returns `g{group_id}` and why joining a
group adopts the joiner's group-less experiments (`adopt_orphan_experiments`): group
members must share a corpus exactly.

Multi-group breaks that equality. A member of groups A and B reads `own ∪ A ∪ B`; a
member of only A reads `own ∪ A`. Both fits write the same columns, so they would
overwrite each other with coordinates from incompatible spaces — silently, with the
plot degrading rather than anything raising.

The fix is the rule the discriminant projection already follows: *the filter selects
which points are returned, never which are fitted.* Fit globally, filter on read.

### Why document privacy is stamped, not derived

`RAGDocument.group_id` is already the ACL truth (`NULL` = owner only) and that rule is
mirrored in **four** places: `document_scope`, `document_read_scope`, the raw-SQL
`owner_clause` in `rag_service.search_documents`, and `_inject_user_id_filter` in
`services/sql_query_service.py`. Deriving a document's visibility from its folder would
mean adding a folder join to all four and keeping them in step forever.

Instead, one helper decides placement and the document's own `group_id` is re-stamped
whenever placement changes. The ACL surface does not change at all.

## Design

### Data model

```
group_members         DROP  UNIQUE (user_id)   →   UNIQUE (group_id, user_id)
                      role ∈ {admin, member} becomes authoritative

group_join_requests   NEW
  id, group_id FK→groups ON DELETE CASCADE, user_id FK→users ON DELETE CASCADE,
  status ∈ {pending, approved, rejected}, message TEXT NULL,
  created_at, decided_at NULL, decided_by_user_id FK→users NULL
  UNIQUE (group_id, user_id)      -- re-requesting flips rejected → pending

document_folders      + visibility ∈ {group, private}   NOT NULL DEFAULT 'group'
                      + kind ∈ {root, common, user, custom} NOT NULL DEFAULT 'custom'

experiments           unchanged (an experiment belongs to at most one group)
rag_documents         unchanged (group_id stays the ACL truth)
```

`kind` exists so seeded folders can be found without matching on names (every group has
a `common`) and so they can be made immutable.

Schema is applied by `create_all` (new tables) plus `ensure_schema_updates()` (new
columns and the constraint swap) at startup. `backend/migrations/*.sql` is documentation
and is never executed — the constraint swap must be in `ensure_schema_updates()` or it
will silently never happen in production.

### ACL

`utils/groups.py` remains the SSOT; it widens from one id to a list:

```python
get_user_group_ids(user_id, db) -> list[int]          # replaces get_user_group_id
experiment_owner_filter(user_id, group_ids)           # or_(user_id == me, group_id.in_(group_ids))
```

`.in_([])` is false in SQL, so a user with no groups degrades to owner-only — fail-closed,
matching today's `group_id=None` default. Every call site passes a list; the singular
helper is deleted rather than kept as a shim, so nothing can keep the old semantics by
accident.

Folder visibility:

```python
or_(
    DocumentFolder.user_id == user_id,
    and_(DocumentFolder.visibility == "group",
         DocumentFolder.group_id.in_(group_ids)),
)
```

A peer's private folder carries `visibility='private'` and a different `user_id`, so it
is excluded — including for a group admin and for the global admin. This is the only
place in the application the global admin cannot read, and it gets its own test.

Document placement:

```python
def placement_group_id(folder: DocumentFolder | None) -> int | None:
    if folder is None or folder.visibility == "private":
        return None            # owner-only
    return folder.group_id     # readable by that group
```

Applied on upload (`save_uploaded_document`), on `move_document`, and — recursively over
the subtree — when a folder is moved or a folder is dissolved by deletion. A document at
the library root (`folder_id IS NULL`) is private; sharing means moving it into a group
folder.

Subfolder visibility is inherited from the parent at creation and recomputed for the
whole subtree on move.

### Group roles and the join flow

```python
require_group_admin(user_id, group_id, db)
    # passes when group_members.role == 'admin' OR users.role == ADMIN
```

Replaces the `created_by_user_id` checks in `update_group`, `delete_group` and
`kick_member`; `created_by_user_id` is kept as provenance only. Global admin implying
group admin is what makes "Michal is admin everywhere" durable — no rows to maintain in
groups that do not exist yet.

| Endpoint | Authorization |
|---|---|
| `POST /api/groups/{id}/join-requests` | any authenticated user |
| `GET /api/groups/{id}/join-requests` | group admin |
| `GET /api/groups/join-requests/mine` | self |
| `POST /api/groups/{id}/join-requests/{req_id}/approve` | group admin + `require_interactive_user` |
| `POST /api/groups/{id}/join-requests/{req_id}/reject` | group admin + `require_interactive_user` |
| `DELETE /api/groups/{id}/join-requests/{req_id}` | the requester (cancel) |

`POST /api/groups/{id}/join` is **deleted** — open self-join is the hole this feature
closes.

Approve/reject sit behind `require_interactive_user` deliberately: membership is the
security boundary, and CLAUDE.md's existing rule then keeps them out of MCP. The agent
may request to join; it cannot approve itself into a group.

Approval is one transaction: insert `GroupMember(role='member')`, set the request to
`approved`, and seed the member's private folder in that group.

`leave_group` removes the membership for that one group. If the leaver was the last
admin, the longest-standing remaining member is promoted; if there are no members left,
the group is deleted (today's behaviour, re-keyed from `created_by_user_id` to `role`).

### Folder seeding

`utils/folder_seed.py` is the SSOT:

```python
ensure_group_folders(db, group)  -> root folder (kind='root', visibility='group')
                                    + 'common' (kind='common', parent=root)
ensure_member_folder(db, group, user) -> private folder named after the user
                                         (kind='user', visibility='private', parent=root)
```

Called on group creation, on join approval, and once from the production backfill. Both
are idempotent (keyed on `kind` + `group_id` [+ `user_id`]), so re-running the backfill
is safe.

Folders with `kind` in `{root, common, user}` cannot be renamed, moved or deleted. The
router rejects those with 400. This removes the "what if someone deletes `common`"
class of edge cases entirely. The one exception is not a user action: renaming a group
renames its root folder, so the tree cannot drift from the group list.

Leaving or being kicked from a group re-parents the departing member's private folder to
the library root (`parent_id = NULL`, `group_id = NULL`, `kind = 'custom'`) and leaves
its documents untouched — they were already owner-only. Without this the folder would
hang under a group root the ex-member can no longer see, and would vanish from their
tree while still holding their files.

Deleting a group relies on the existing `ON DELETE SET NULL` on both
`document_folders.group_id` and `rag_documents.group_id`: the group's folders and
documents fall back to owner-only rather than being deleted.

### Replacing adoption

- Experiments: `PATCH /api/experiments/{id}/group?group_id=N` (or omitted to unshare).
  **Owner-only** — a container belongs to whoever uploaded it, the same rule that keeps
  FOV deletion and experiment rename owner-only. `group_id` must be a group the owner
  belongs to. The response is rebuilt with `load_experiment_response()`; never serialize
  the in-session object after commit (`updated_at` is `onupdate=func.now()` and would
  raise `MissingGreenlet`).
- Documents: sharing is moving into a group folder. `move_document` already exists; it
  gains the re-stamp.
- `adopt_orphan_experiments` and `adopt_orphan_documents` are deleted along with their
  call sites and tests.

### UMAP

- `compute_crop_umap(db)` / `compute_fov_umap(db)` drop the user/group filter and fit
  every crop/image that has an embedding.
- `refresh_scope_key(umap_type)` collapses to one key per projection type; the
  `_failed_refreshes` and `_inflight_refreshes` maps follow.
- Read endpoints keep their ACL filter unchanged — only the fit corpus changes.
- `MIN_POINTS_FOR_UMAP` now guards the global fit; the existing rule that it must not
  reject *filtered* views stands.
- `discriminant_service`'s in-process cache key becomes `u{user}|g{sorted group ids}`.
  Its results are not persisted, so a wrong key costs a recompute, not corruption.

One-off consequence: the first global fit rewrites all ~1277 crop coordinates, so the
dashboard plot changes orientation. Clusters are preserved; UMAP axes carry no meaning.

### MCP surface

```yaml
list_folders      + group_id, group_name, visibility, kind, path, document_count
search_documents  + folder_ids[], include_subfolders (default true), group_ids[]
find_documents    converge on the same three params (replacing folder_id + in_folder)
new tools         list_groups, list_join_requests, request_group_join,
                  assign_experiment_group
```

Omitting the scoping params searches everything the caller can read, across all their
groups plus their private folders — the requested default. Subtree expansion is computed
from the folder table in Python and passed to the query as an id list, so the raw-SQL
page search gains a single `AND d.folder_id = ANY(:folder_ids)`.

`SERVER_VERSION` → 3.0.0 (the `find_documents` contract changes). `tests/test_registry.py`,
`tests/test_protocol.py` and `tests/test_app_control_tools.py` pin the tool-name set and
the version and must be updated with it.

`mcp` stays pinned `>=1.2,<2`.

### Frontend

- Documents page: tree rendered from the enriched `list_folders` — group roots, `common`,
  private folders with a visibility badge, document counts.
- Settings: several group cards instead of one; "request to join" on groups the user is
  not in; an approval queue with approve/reject for group admins.
- Every new string goes into `messages/en.json` **and** `messages/fr.json`; no hardcoded
  JSX text.

### Production migration

Schema through `ensure_schema_updates()`. Data through one idempotent script:

1. `Dr. Janke Lab`: create root + `common` + a private folder for each of the 12 members.
   Re-parent the existing folder "Danilo" (id 4, 18 documents) under the root, keeping
   `visibility='group'` so nothing disappears from anyone's view.
2. Create `UTIA ZOI` with Michal (user 1) as admin, plus root + `common` + his private
   folder; Michal keeps his Dr. Janke Lab membership.
3. Set `group_members.role = 'admin'` for user 1 in every existing group.
4. Trigger one UMAP recompute after the global-fit switch.

⚠️ Dropping `UNIQUE (user_id)` is effectively one-way: once anyone holds two
memberships the constraint cannot be restored without removing memberships.

## Testing

Backend unit tests (`backend/tests/unit/`):

- `experiment_owner_filter` / `document_scope` / `document_read_scope` /
  `document_dedupe_scope` with two group ids, and with an empty list (fail-closed).
- `sql_query_service` injects the widened predicate through the table *alias*, one per
  alias on a self-join, with the FK correlation for indirect tables intact.
- A peer's private folder is invisible to another member, to the group admin, and to the
  global admin; the peer's documents inside it are likewise invisible.
- Placement re-stamping: move a document, move a folder, dissolve a folder — each
  updates `rag_documents.group_id` for the whole affected subtree.
- Join flow: only a group admin (or global admin) may approve; a non-admin gets 403;
  approval creates the membership and the private folder; a duplicate request 409s;
  approve/reject reject a connector token.
- Seeded folders reject rename/move/delete.
- No adoption: joining a group leaves the joiner's group-less experiments untouched.
- `assign_experiment_group` is owner-only and rejects a group the owner is not in.

MCP tests: tool-name set and `SERVER_VERSION`; scoping args reach the backend as
repeated query params; `find_documents` no longer accepts `in_folder`.

Frontend: a unit test for tree construction from the flat folder list (the existing
`npm run test:unit` runner, no browser).

Verification discipline:

- Every new guard is confirmed by **perturbation** — remove the fix, watch the test go
  red, restore it. A green test that was never seen red proves nothing.
- Every new write endpoint is called once **against the real database** via
  `docker exec -i -w /app maptimize-backend python - < script.py`, each call in its own
  `async_session_maker()` session. `mock_db` is an `AsyncMock` and models no attribute
  expiry, which is how a production 500 on experiment rename once passed 1457 green tests.
- E2E is never pointed at production: it creates and deletes data, and the test user does
  not exist there.

## Out of scope

- Email invitations (no SMTP anywhere in the backend).
- Per-folder permissions beyond `group` / `private` (no per-user grants, no read-only
  sharing).
- Moving experiments between groups in bulk, or a group-transfer UI beyond the single
  `PATCH .../group` endpoint.
- Raising MCP context caps.
