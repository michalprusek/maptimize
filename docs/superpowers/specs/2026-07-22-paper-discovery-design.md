# Paper discovery & import ("Najít zdroje") — design

**Date:** 2026-07-22
**Status:** approved (design), pending implementation plan

## Goal

Let a user describe the papers they want ("MAP bundling in vitro since 2020", a list of
titles, or a list of DOIs), see the matching literature with enough metadata to judge it,
tick the ones they want, and have those imported into the RAG document library — the same
library the chat agent already reads. Equivalent to NotebookLM's "Discover sources".

## Decisions (confirmed with user)

1. **Scientific papers only** — not general web pages. Structured metadata for the
   picking step, real PDFs for the Vision RAG pipeline.
2. **Paywalled papers are shown but not importable** — listed with a "paywall" badge and a
   link to the publisher, checkbox disabled. Nothing is bypassed; the user can still upload
   the PDF manually if they have access.
3. **Dedicated modal**, not a chat tool — the tick-list is a real UI flow, reached from the
   existing Documents modal.
4. **One input field, both modes** — a natural-language topic OR a pasted list of
   titles/DOIs.
5. **Rate limit 1000 imports/hour/user** (user's explicit call, overriding the 25/h
   originally proposed).

## Source: Europe PMC

One REST API covers the whole flow:

- **Search** — `GET https://www.ebi.ac.uk/europepmc/webservices/rest/search` with
  `query`, `format=json`, `pageSize`, `resultType=core` (core gives the abstract).
- **Metadata per hit** — `title`, `authorString`, `pubYear`, `doi`, `pmid`, `pmcid`,
  `abstractText`, and the journal name at **`journalInfo.journal.title`** (the flat
  `journalTitle` field comes back empty — verified live 2026-07-22).
- **Importability** is decided by `fullTextUrlList.fullTextUrl[]`, **not** by
  `isOpenAccess`. A result is importable iff that list contains an entry with
  `documentStyle == "pdf"` **and** `availability in {"Open access", "Free"}`; that entry's
  `url` is the download target (in practice
  `https://europepmc.org/articles/{PMCID}?pdf=render`).
  Verified live: `isOpenAccess` alone is misleading — bioRxiv preprints come back with
  `isOpenAccess: "N"` yet `availability: "Free"`, but expose only a DOI-style link and no
  `pdf` entry, so they are correctly treated as not directly importable (shown with a
  link-out instead).
- Indexes PubMed, PMC and preprints (incl. bioRxiv), so preprints — often the only free
  version of a paywalled paper — surface naturally.

PubMed (already in `APPROVED_APIS`) is deliberately NOT the primary source: it has no PDFs.

Europe PMC is **not** added to `APPROVED_APIS` — discovery uses a dedicated client
(`paper_discovery_service.py`) that calls it directly over httpx. `APPROVED_APIS` gates the
chat agent's generic `call_external_api` tool, which is a different access path; the agent
cannot reach Europe PMC through it.

## Input handling

The single query field is classified before searching:

| Input looks like | Handling |
|---|---|
| One or more DOIs (`10.xxxx/...`) | Direct lookup per DOI (`query=DOI:"..."`) |
| Multiple lines / clearly a list of titles | One title search per line, best match each |
| Anything else (free text) | Passed straight to Europe PMC's own relevance ranking |

**Implemented (PR #37, 2026-07-22):** free-text topic queries are translated into Europe
PMC field syntax by one Gemini call — `rewrite_topic_query()` in
`paper_discovery_service.py` — gated to `classify_query`'s "topic" branch only. DOI and
title-list searches are never rewritten (already structured, so zero added cost or
latency), and input that already contains field syntax is passed through untouched. Any
failure (no API key, SDK missing, timeout, exception, empty output) falls back to the raw
text, so the rewrite can never fail the search itself. The query actually sent is returned
as `effective_query` and shown as "Searched as: …" in the modal.

Why it was needed: verified live, `find all microtubule related papers from lab of dr.
carsten janke` searched verbatim returned 0 of 6 relevant hits (AlphaFold2 papers, a
conference abstract, a tribute to Carol Robinson). Rewritten to
`AUTH:"Janke C" AND microtubule` it returns 8 of 8 papers with Janke as an author.

## Result list

Each hit renders: title, authors, journal + year, DOI, truncated abstract, and exactly one
status badge that determines whether its checkbox is enabled:

| Badge | Checkbox | Meaning |
|---|---|---|
| Open access | enabled | A PDF can be fetched and imported |
| Paywall | **disabled** | No free full text; link out to the publisher |
| Already in library | **disabled** | Same DOI already imported (dedupe) |

## Import flow

`POST /api/rag/discover/import` receives the selected results. Per paper:

1. Re-verify open access server-side (never trust the client's claim).
2. Fetch the PDF: `httpx.AsyncClient.stream`, `follow_redirects=False` with a manual
   redirect loop re-validating every hop through the existing `_is_safe_url()`, a
   **100 MB streamed byte cap** (matching the upload endpoint), content-type check, and a
   read timeout suited to a large PDF.
3. `save_uploaded_document(user_id, filename, content=pdf_bytes, db, thread_id=None)` —
   unchanged; `thread_id=None` means it is a library upload, so it inherits lab-group
   sharing and is not page-capped.
4. `background_tasks.add_task(process_document_async, document.id)` — the existing
   indexing pipeline, untouched. The Documents modal's existing "processing" bucket shows
   progress for free.

Outbound concurrency is capped at **4** regardless of how many papers were selected, to stay
within Europe PMC's politeness expectations. The response is per-paper so the UI can report
`3 imported, 1 failed (PDF unavailable)` rather than one opaque success/failure.

**Throughput note:** the 1000/h limit is a ceiling, not throughput. Each import costs a page
render + Qwen VL embeddings on a GPU shared with Spheroseg, so large batches queue and drain
gradually through the existing background pipeline.

## Schema change (additive)

`rag_documents` gains two nullable columns, added the same way `group_id` was
(`ensure_schema_updates()` in `backend/database.py`, `ADD COLUMN IF NOT EXISTS` +
savepoint-guarded, plus `CREATE INDEX IF NOT EXISTS`):

- `doi TEXT` — indexed; the dedupe key ("already in library") and the provenance anchor.
- `source_url TEXT` — where it came from, so a user can get back to the publisher page.

Both stay NULL for manually uploaded documents.

## Components

| Unit | Responsibility |
|---|---|
| `backend/services/paper_discovery_service.py` (new) | Europe PMC client (search, DOI/title lookup, OA resolution) **and** the size-limited PDF fetcher. The only place either concern lives. |
| `backend/routers/rag.py` | Two endpoints: `POST /api/rag/discover` (search → candidates) and `POST /api/rag/discover/import` (fetch + save + schedule indexing). Own rate-limit counter, separate from uploads. |
| `backend/database.py` | The two additive columns + index. |
| `frontend/components/chat/DiscoverSourcesModal.tsx` (new) | Query field, result list with checkboxes, select-all, "Import selected". Styled after `ExportModal.tsx`. |
| `frontend/components/chat/DocumentsModal.tsx` | Entry point button. |
| `frontend/stores/chatStore.ts` | `discoverSources(query)` and `importDiscovered(selected)`; on success prepend into `documents` exactly like `uploadDocument` does. |
| `frontend/messages/{en,fr}.json` | All new strings, both locales. |

## Error handling

- **Europe PMC unreachable / 429** → the modal shows a clear failure with a retry, no
  partial silent state.
- **PDF fetch fails** (404, redirect to a paywall, wrong content-type, exceeds the cap) →
  that paper is reported as failed in the response with a reason; no orphan DB row and no
  orphan file. Fetch happens *before* `save_uploaded_document`, so nothing is written on
  failure.
- **Indexing fails afterwards** → the existing `FAILED` status + `error_message` path
  already surfaces this in the Documents modal.
- **Rate limit exceeded** → 429 with `Retry-After`, mirroring `_check_upload_rate_limit`.
- Never log the full Gemini API key or user PDFs; log URLs through the existing
  `sanitizeUrlForLogging` convention on the frontend.

## Testing

- Unit (`backend/tests/unit/`, mocked DB/httpx): input classification (DOI vs titles vs free
  text); OA vs paywall mapping from an Europe PMC payload; dedupe by DOI; the PDF fetcher's
  size cap, content-type rejection, and redirect re-validation; import reports per-paper
  failures without creating rows.
- A test that the import path calls `save_uploaded_document` with `thread_id=None` (so the
  group-sharing and page-cap behaviour is the library one).
- Frontend: `tsc` clean; i18n keys present in both locales exactly once.

## Out of scope

- General web pages (explicitly deferred — would need HTML→PDF rendering).
- Bypassing paywalls in any form.
- Automatic/scheduled discovery; this is user-initiated only.
- Citation-graph expansion ("papers citing this one").

## Risks

- **Europe PMC blocking us** if we are impolite. Mitigated by the concurrency cap of 4 and
  a bounded result page size; revisit if they publish stricter limits.
- **OA metadata can be wrong** — a paper flagged open access whose PDF 404s. Handled as a
  per-paper failure rather than a broken import.
- **`_is_safe_url()` does a blocking `getaddrinfo` in the event loop** (pre-existing). The
  PDF fetcher inherits that; worth moving to a thread if discovery makes it hot.
