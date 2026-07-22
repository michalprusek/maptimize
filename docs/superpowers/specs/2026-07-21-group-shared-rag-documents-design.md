# Group-shared RAG documents — design

**Date:** 2026-07-21
**Status:** approved (design), pending implementation plan

## Goal

A library-uploaded RAG document becomes readable by every member of the
uploader's lab group — list, semantic search, page reads, and agent access —
exactly the way experiments are already shared via `group_id`. This mirrors the
existing `experiment_owner_filter` access model.

## Decisions (confirmed with user)

1. **Scope: library uploads only.** Documents uploaded to the general library
   (`thread_id IS NULL`) are shared group-wide. Files attached to a specific chat
   thread (`thread_id` set) stay private to their owner and that conversation.
2. **Access level: read-only for the group.** Members can list, search, read, and
   view pages of shared library documents. Only the owner can delete or reindex.
   This matches the existing experiment rule ("skupina dává právo číst, ne měnit").
3. **Existing docs: backfill + going forward.** A one-time backfill stamps
   existing library documents with their owner's group; new library uploads are
   stamped at creation.
4. **Frontend: in scope.** Non-owners see a "Shared" badge and do **not** see the
   delete/reindex controls (otherwise they'd see a button that 403s).

## Background — the pattern this follows

Experiments are already group-shared:

- `Experiment.group_id` FK column (`models/experiment.py:38-42`), `ON DELETE SET NULL`.
- SSOT read filter `experiment_owner_filter(user_id, group_id)` in `utils/groups.py:20-30`
  returns `or_(user_id == u, group_id == g)`; degrades to owner-only when `group_id is None`.
- `get_user_group_id(user_id, db)` (`utils/groups.py:12-17`) resolves the caller's
  single group (one group per user, enforced by `uq_user_one_group`).
- `adopt_orphan_experiments(db, user_id, group_id)` (`utils/groups.py:33-58`) stamps
  the joiner's pre-existing group-less experiments on group join.
- Writes stay owner-only; group grants read, not mutate.
- The agent resolves `group_id` **once per turn** in `generate_response`
  (`gemini_agent_service.py:~1191`) and threads it into `execute_tool` /
  `experiment_owner_filter`.

Documents already have a partial equivalent: `document_scope(user_id, thread_id)`
in `models/rag_document.py:21-38`, whose own docstring says it "Mirrors the
`experiment_owner_filter` pattern." It scopes by owner + thread but does not yet
consider group membership. `RAGDocument` has **no `group_id` column** — the one
schema gap.

`document_scope` today:
- `thread_id=None` → `and_(user_id == u, thread_id IS NULL)` — library only.
- `thread_id=N`   → `and_(user_id == u, or_(thread_id IS NULL, thread_id == N))` — library + that thread's attachments.

The library-vs-attachment axis is what keeps a private chat attachment from
leaking into another conversation. We widen **only the library branch** to the
group; the attachment branch stays owner-scoped. That structural choice *is* the
"library uploads only" decision, enforced in one SSOT function.

## Change surface

### 1. Schema — `backend/database.py :: ensure_schema_updates()`

The repo uses a home-grown additive migration (no Alembic): `ADD COLUMN IF NOT
EXISTS` in the `updates` list plus savepoint-guarded backfill blocks.

- Add to the `updates` list (next to the existing `experiments`/`metrics` group_id lines ~142):
  ```python
  ("rag_documents", "group_id", "INTEGER REFERENCES groups(id) ON DELETE SET NULL"),
  ```
- Add the matching mapped column to `RAGDocument` (`models/rag_document.py`),
  copied from `Experiment.group_id`:
  ```python
  group_id: Mapped[Optional[int]] = mapped_column(
      ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True,
  )
  ```
- Backfill block (savepoint-guarded, like the metric_ratings backfill already
  present at `database.py:204-220`):
  ```sql
  UPDATE rag_documents SET group_id = gm.group_id
  FROM group_members gm
  WHERE rag_documents.user_id = gm.user_id
    AND rag_documents.thread_id IS NULL
    AND rag_documents.group_id IS NULL;
  ```
  Only library docs (`thread_id IS NULL`) are stamped; attachments keep `group_id NULL`.

Additive-only, idempotent, no destructive change — safe for prod per CLAUDE.md.

### 2. SSOT read filters — `backend/models/rag_document.py`

- **Extend** `document_scope(user_id, thread_id=None, group_id=None)`: the library
  clause becomes `or_(user_id == u, group_id == g)` (when `group_id` given); the
  attachment clause stays strictly `user_id == u`. Semantics:
  - `thread_id=None` → shared library (owner OR group).
  - `thread_id=N`   → shared library OR the caller's own attachments in thread N.
  - `group_id=None` → degrades to today's owner-only behaviour (fail-closed).
- **Add** `document_read_scope(user_id, group_id=None)` for single-document
  fetch-by-id endpoints:
  `or_(user_id == u, and_(thread_id IS NULL, group_id == g))` — the owner sees any
  of their own docs (including their own attachments, needed to serve attachment
  pages in the doc viewer), and a group member sees shared library docs.
  Degrades to `user_id == u` when `group_id is None`.

Two helpers because listing (attachments must never pollute the library listing)
and fetch-by-id (owner may fetch their own attachment by id) are genuinely
different rules.

### 3. Reads become group-aware

Thread the caller's `group_id` (resolved via `get_user_group_id`, or once-per-turn
for the agent) into every read site:

**`backend/routers/rag.py`**
- `get_document_for_user` (helper at `:126-131`, used by get/pdf/pages/image/search-within):
  switch from `user_id == u` to `document_read_scope(user_id, group_id)`; resolve
  `group_id` in the helper (or its callers) via `get_user_group_id`.
- `list_documents` (`:155`): pass `group_id` into `document_scope`.
- `search` / `search_documents_only` / `search_within_document` (`:488,529,563`):
  resolve and pass `group_id`.

**`backend/services/rag_service.py`**
- `search_documents` raw SQL (`:90,143`): add `OR rd.group_id = :group_id` to the
  library case (respecting the existing `thread_id` scope filter); add `group_id`
  param.
- `get_document_content` (`:485-488`), `get_all_documents_summary` (`:562-565`),
  `_get_document_page_image_path` (`:688-690`), `get_cached_passage` (`:854-857`):
  add `group_id` param and use `document_read_scope`.

**`backend/services/gemini_agent_service.py`**
- Pass the per-turn `group_id` (already resolved at `:~1191`, currently only fed to
  `experiment_owner_filter`) into the document tool sites: StatsCache doc count
  (`:1604`), `list_documents` (`:1623`), `get_documents_summary` (`:1637`),
  `semantic_search`/`combined_search` (`:1641`), `search_documents` (`:1645`),
  `get_document_content` (`:1659`), `extract_document_region` (`:2427`),
  `show_document_pages` (`:2493`).
- `_inject_user_id_filter` (`:248-262`): add `rag_documents` to the group-widening
  branch (currently only `experiments` gets `OR group_id`). Safe because only
  library docs carry a non-NULL `group_id`, so attachments still match on `user_id`
  only.

### 4. Writes stay owner-only (unchanged)

`delete_document` (`document_indexing_service.py:490-493`) and `reindex_document`
(`:535-538`) keep their `RAGDocument.user_id == user_id` filter. A non-owner's
delete/reindex therefore 404s/403s. No change needed.

### 5. Write stamping — `backend/services/document_indexing_service.py :: save_uploaded_document` (`:112-120`)

Resolve `get_user_group_id(user_id, db)` and set `group_id` on the new
`RAGDocument` **only when `thread_id is None`** (library upload). Attachments are
created with `group_id=None`. Mirrors `create_experiment` stamping
(`routers/experiments.py:115,121`).

### 6. Orphan adoption on group join

- `backend/utils/groups.py`: add `adopt_orphan_documents(db, user_id, group_id)` —
  identical to `adopt_orphan_experiments` but on `RAGDocument` with an extra
  `RAGDocument.thread_id.is_(None)` predicate (never adopt attachments).
- `backend/routers/groups.py`: call it alongside `adopt_orphan_experiments` at the
  existing join sites (`:120,334`), committing in the same transaction.

The startup backfill (item 1) covers today's members who are already in the group;
`adopt_orphan_documents` covers anyone who uploads before joining and joins later.

### 7. Frontend — owner affordance

The listing widens transparently once the backend does, but a non-owner must not
see delete/reindex controls.

- **API response:** add `is_owner: bool` to the document list payload, computed
  server-side as `row.user_id == current_user.id` (`routers/rag.py list_documents`).
  Add `is_owner` to the `RAGDocument` TS type (`frontend/lib/api.ts:1954-1965`).
- **`frontend/components/chat/DocumentsModal.tsx`:** render a "Shared" badge on
  documents where `!is_owner`; hide the delete and reindex controls for those rows.
- **i18n:** new keys in **both** `frontend/messages/en.json` and
  `frontend/messages/fr.json` (e.g. `documents.sharedBadge`). No hardcoded strings.

### 8. Tests

Per the backend harness (CLAUDE.md, `run-coverage.sh`, `backend/tests/unit/`):

- Unit: `document_scope`/`document_read_scope` produce the expected clauses with and
  without `group_id`; attachment branch never widens.
- Router: a group peer can `GET /api/rag/documents` and see a peer's shared library
  doc, can read its pages, but gets 403/404 on `DELETE`. An attachment
  (`thread_id` set) is never visible to another user.
- Backfill: a library doc with `group_id NULL` whose owner is in a group gets
  stamped; an attachment does not.
- Agent: document tools return group-shared docs when `group_id` is threaded;
  `_inject_user_id_filter` widens `rag_documents`.

Keep the full suite green; target ~99% line coverage.

## Out of scope / deliberately unchanged

- `process_document_async` loads by id with no ACL (background task) — unchanged.
- Passage/page image files on disk are scoped transitively via document id —
  no path-level ACL change.
- All experiment/FOV/metric ACL — untouched.
- Group members still cannot mutate (delete/reindex) another member's document.

## Risks

- **Raw-SQL widening in the agent** (`_inject_user_id_filter`) is the highest-risk
  edit — it operates on model-generated SQL. Mitigation: only library docs carry a
  non-NULL `group_id`, so the `OR group_id = :g` term cannot expose attachments,
  and the existing `user_id = :u` term is retained.
- **Backfill on a large table** — single additive `UPDATE`, savepoint-guarded, runs
  once at startup; consistent with the existing metric_ratings backfill.
