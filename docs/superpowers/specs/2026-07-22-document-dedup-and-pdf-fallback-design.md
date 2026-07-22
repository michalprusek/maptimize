# Document deduplication & PDF fetch fallback — design

**Date:** 2026-07-22
**Status:** approved (design)

Two independent robustness fixes to the document import path, specified together
because they share one caller (`POST /api/rag/discover/import`) and one test surface.

- **Part 1** stops the same file being stored and indexed twice.
- **Part 2** stops a single dead publisher link failing an import that could have
  succeeded from another source.

---

# Part 1 — Deduplication by content hash

## Problem

Deduplication today is by **DOI**, and only in discovery: `POST /api/rag/discover`
marks a paper "already in library" if a readable document carries the same DOI.
That leaves two holes:

- **Manual upload is not deduplicated at all.** Uploading the same PDF twice
  produces two files on disk, two DB rows, and two full Vision-RAG indexing runs
  (page rendering + Qwen VL embeddings on a GPU shared with Spheroseg).
- **DOI is NULL for every manually uploaded document**, so a paper uploaded by
  hand and later found through discovery is not recognised as already present.

## Decisions (confirmed with user)

1. **Scope: the whole lab group.** A duplicate is anything the uploader can
   already see. Same paper for ten lab members = one indexing run, not ten.
2. **Behaviour: skip and return the existing document.** No 409, so a 20-paper
   batch import is not interrupted by one duplicate.
3. **Applies to manual upload too**, and to every supported file type — not just
   PDFs. A hash is type-agnostic and a duplicate DOCX wastes the same GPU time.

## Mechanism

`sha256(content)` hex digest, stored in a new column and looked up before anything
is written.

**The hash is computed in `save_uploaded_document()`** — the single function that
both the manual upload endpoint (`routers/rag.py:268`) and the discovery import
(`routers/rag.py:734`) already call. Both paths therefore deduplicate by
construction; neither gets its own copy of the rule.

**The lookup reuses `document_scope(user_id, thread_id, group_id)`**, the existing
ACL SSOT. Visibility and deduplication then cannot disagree: library documents
dedupe across the lab group, chat attachments dedupe only within the caller's own
attachments in that thread. `CLAUDE.md` records that this visibility rule already
exists in four hand-synchronised copies — this adds no fifth.

`save_uploaded_document()` returns `(document, created: bool)` instead of a bare
document. An explicit flag beats having callers guess from a timestamp, and it
forces every existing call site to be visited and considered.

## What must NOT be deduplicated

Three cases where returning the existing document is worse than importing again:

| Case | Why it is excluded |
|---|---|
| The existing document's status is `FAILED` | Deduplicating to it hands the user a broken document *and* removes their only way to retry — the re-upload that would have fixed it now silently resolves to the failure. |
| The upload is a library document but the hash match is a chat attachment (or vice versa) | Different lifetime and visibility: an attachment is deleted with its thread and never widens to the group. Silently aliasing one to the other would make a library document vanish when someone deletes a conversation. |
| The hash matches a document the caller cannot see | Excluded automatically, because the lookup is `document_scope`. Worth stating: a dedupe hit must never reveal that a document exists. |

## API and UI contract

A skipped upload must be visibly skipped. Silently returning the existing document
would look identical to a successful upload, and the user would not learn why their
library did not grow.

| Path | Response | UI |
|---|---|---|
| `POST /api/rag/documents` (manual upload) | 200 with the existing document plus `is_duplicate: true` on `RAGDocumentUploadResponse` | The upload modal says the file is already in the library and highlights the existing entry instead of adding a row. |
| `POST /api/rag/discover/import` | The paper's per-paper result reports `already in library`, not `imported` | The existing per-paper reporting already renders this; the counts must not claim an import that did not happen. |

Both new strings go in `frontend/messages/en.json` **and** `fr.json`, once each.

## Migration

Additive, in `ensure_schema_updates()` (`backend/database.py`), the same way
`group_id`, `doi` and `source_url` were added:

- `ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)`
- `CREATE INDEX IF NOT EXISTS ... ON rag_documents (content_hash)` — the column is
  a lookup key on every upload.

Existing rows keep `content_hash = NULL` and are then backfilled **best-effort**
by hashing `original_path` on disk, inside a savepoint. A missing or unreadable
file is expected (documents can outlive their files) and must not abort startup,
but **every failure is logged at `error` and counted**. `CLAUDE.md` records a
backfill that logged at `debug` under an INFO root logger and printed "Schema
updates applied successfully" after a real failure; this must not repeat.

Rows still NULL after the backfill simply do not participate in deduplication.
`NULL != NULL` in SQL, so they can never match each other by accident.

---

# Part 2 — PDF fetch fallback chain

## Problem

Import of `10.21203/rs.3.rs-9043146/v1` fails with `Publisher returned HTTP 403`.

Verified live, 2026-07-22:

- Europe PMC lists exactly one `documentStyle: "pdf"` entry for this preprint, on
  `site: "Europe_PMC"` — it passes every importability check.
- Fetching it returns **HTTP 403** with the body
  `{"error":"PDF link has expired or is invalid"}`. The file is broken on Europe
  PMC's side.
- The URL is stable across API calls, and neither a browser `User-Agent`, a
  `Referer`, nor a `JSESSIONID` obtained from the article page changes the result.
  It is not a bot check and not a session-token problem.
- The paper **is** freely available: `https://doi.org/10.21203/rs.3.rs-9043146/v1`
  resolves to Research Square, and `.../article/rs-9043146/v1.pdf` returns
  34 MB starting with `%PDF-` — inside the existing 100 MB cap.

The defect is therefore not "this paper is unavailable" but "we commit to one
candidate URL and give up when it fails".

## Mechanism

`pdf_url_from_result()` (returns one URL) becomes `pdf_urls_from_result()`
(returns an **ordered candidate list**). Import walks the list and the first
candidate that yields a real PDF wins.

Candidate order — cheapest and most trustworthy first:

1. **Every** Europe PMC `fullTextUrl` entry passing the existing rule
   (`documentStyle == "pdf"`, `availability ∈ {Open access, Free}`,
   `site == "Europe_PMC"`). Today only the first is used; the rule itself is
   unchanged.
2. **Unpaywall** — `GET https://api.unpaywall.org/v2/{doi}?email=...`, taking each
   `oa_locations[].url_for_pdf`. Free, and indexes only legal OA copies.
3. **Preprint-host URL patterns** applied to the DOI's landing page: Research
   Square (`.../article/rs-<id>/v<n>` → `+ ".pdf"`) and bioRxiv/medRxiv
   (`+ ".full.pdf"`).

Every candidate goes through the existing `fetch_pdf()` unchanged — same SSRF
guard, same per-hop redirect revalidation, same 100 MB streamed cap, same
content-type check. The fallback adds candidates; it does not add a second, less
careful way to fetch.

**Steps 2 and 3 run only after every step-1 candidate has failed**, so the common
path costs no extra requests. Unpaywall requires a contact email as a query
parameter: it goes in `settings`, never hardcoded.

Note the ordering does not fix the reported paper until step 3 — Unpaywall reports
`is_oa: true` but `url_for_pdf: null` for it. Steps 2 and 3 are complementary, not
redundant, and dropping either would leave a real gap.

## Error reporting

Per-paper failure already exists and stays. With several candidates the response
must name **why the last real attempt failed**, not "3 candidates failed": the
distinction between 403, a wrong content-type, and exceeding the size cap is what
tells the user whether to retry or fetch the PDF by hand.

## Deliberate scope boundary

The discovery **result list** keeps computing its `open access` / `paywall` badge
from Europe PMC metadata alone. Resolving fallbacks for every row would mean an
Unpaywall call per result. The fallback applies **at import time only**: a paper
shown as importable simply imports more reliably. No paper currently shown as
paywalled becomes importable through this change.

---

# Testing

Unit (`backend/tests/unit/`, mocked httpx/DB):

**Part 1**
- Same bytes twice → one row, one file, one indexing task; second call returns
  `created=False` and the same document id.
- Different bytes, same filename → two documents (the name is not the key).
- A group member's library document deduplicates; a non-member's does not.
- A `FAILED` document is **not** deduplicated to.
- A library upload does not deduplicate to a chat attachment, and vice versa.
- Manual upload and discovery import both hit the same path (assert on the shared
  function, so the two endpoints cannot drift).
- Backfill: hashes what it can, survives a missing file, logs and counts failures.

**Part 2**
- First candidate 403s, second succeeds → import succeeds, one document.
- All candidates fail → per-paper failure naming the last real error; no orphan
  row and no orphan file.
- Unpaywall is **not** called when a Europe PMC candidate succeeds (cost
  invariant, asserted the same way as the Gemini rewrite's).
- `pdf_urls_from_result` returns every qualifying entry, still excludes
  `site: "PubMedCentral"` and non-OA entries, and preserves order.
- Research Square and bioRxiv pattern derivation, including a landing URL that
  matches no known pattern.

# Out of scope

- Bypassing paywalls in any form.
- Near-duplicate detection (same paper, different bytes — e.g. v1 vs v2 of a
  preprint). The DOI check already covers the common case; perceptual matching is
  a different project.
- Deduplicating documents already in the library against each other (no
  retroactive merge). The backfill only records hashes; it never deletes a row.

# Risks

- **Backfill reads every existing document from disk at startup.** The library is
  small today (tens of documents), but this grows linearly. If it ever becomes
  slow, it moves to a background task — it is deliberately best-effort so that
  change is safe.
- **Group-wide deduplication means a user can be handed a document they do not
  own.** They can read it (that is what group sharing already grants) but not
  delete or reindex it — writes stay owner-only. The UI must not offer a delete
  button that will 403.
- **Unpaywall is a new external dependency.** It is only reached on the failure
  path, and a failure there degrades to "no more candidates", never to an error.
