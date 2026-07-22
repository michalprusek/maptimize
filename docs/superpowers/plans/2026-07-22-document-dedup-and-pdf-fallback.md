# Document Dedup & PDF Fetch Fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the same file being stored and indexed twice, and stop one dead publisher link failing an import that another source could satisfy.

**Architecture:** A `content_hash` column on `rag_documents` plus a duplicate lookup inside `save_uploaded_document()` — the single function both the manual upload endpoint and the discovery import already call, so both paths deduplicate by construction. Separately, `pdf_url_from_result` becomes a candidate *list*, and the import walks it (Europe PMC entries → Unpaywall → preprint-host patterns) through the unchanged SSRF-safe `fetch_pdf`.

**Tech Stack:** FastAPI, SQLAlchemy async (asyncpg), Pydantic v2, httpx, pytest + pytest-asyncio, Next.js/Zustand/next-intl.

**Spec:** `docs/superpowers/specs/2026-07-22-document-dedup-and-pdf-fallback-design.md`

## Global Constraints

- **Migrations are additive only.** `ADD COLUMN IF NOT EXISTS` inside a savepoint in `ensure_schema_updates()` (`backend/database.py`). No Alembic in this project. Never a destructive migration — this is a production database with real user data.
- **Migration/backfill failures log at `logger.error` and append to `failed_updates`.** A prior backfill logged at `debug` under an INFO root logger, so a real failure printed "Schema updates applied successfully". Do not repeat this.
- **Writes stay owner-only.** Group membership grants read, never modify. Never mutate a document the caller does not own.
- **Every UI string goes in `frontend/messages/en.json` AND `fr.json`, exactly once each.** Edit those files as plain text — never round-trip them through a JSON serialiser (it reorders and reformats the whole file).
- **Cost:** Unpaywall is only contacted after every Europe PMC candidate has failed. Assert this in tests, the same way the Gemini rewrite's call count is asserted.
- **Test runner** (working tree, no rebuild):
  ```bash
  docker run --rm --entrypoint bash -v /home/cvat/maptimize/backend:/app -w /app -u 0 \
    -e HF_HUB_OFFLINE=1 -e CUDA_VISIBLE_DEVICES= maptimize-maptimize-backend:latest \
    -lc "pip install -q pytest pytest-asyncio 2>/dev/null; python -m pytest tests/unit/ -q"
  ```
  Baseline: **1583 passed**.

---

### Task 1: `content_hash` column, index and backfill

**Files:**
- Modify: `backend/models/rag_document.py` (after the `source_url` column, ~line 124)
- Modify: `backend/database.py:161` (updates list) and `~:276` (index block)
- Test: `backend/tests/unit/test_document_dedup.py` (create)

**Interfaces:**
- Produces: `RAGDocument.content_hash` (`Optional[str]`, 64-char hex, indexed); `backfill_document_hashes(conn) -> int` returning the number of rows it failed to hash.

- [ ] **Step 1: Add the column to the model**

```python
    # sha256 of the uploaded bytes, the deduplication key. NULL for rows that
    # predate the column and for any row whose file could not be read during
    # backfill -- NULL != NULL in SQL, so those never match each other.
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
```

- [ ] **Step 2: Add the migration entry** in `backend/database.py`, at the end of the `rag_documents` group in `updates`:

```python
            # sha256 of the file content: the deduplication key
            ("rag_documents", "content_hash", "VARCHAR(64)"),
```

- [ ] **Step 3: Add the index**, mirroring the `idx_doc_doi` block exactly:

```python
        try:
            await conn.execute(text("SAVEPOINT idx_doc_hash"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_rag_documents_content_hash "
                "ON rag_documents (content_hash)"
            ))
            await conn.execute(text("RELEASE SAVEPOINT idx_doc_hash"))
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT idx_doc_hash"))
            logger.error(f"Failed to create ix_rag_documents_content_hash: {e}")
            failed_updates.append("ix_rag_documents_content_hash")
```

- [ ] **Step 4: Write the failing backfill test** in `backend/tests/unit/test_document_dedup.py`

```python
async def test_backfill_hashes_readable_files_and_counts_the_rest(tmp_path, mock_db):
    good = tmp_path / "a.pdf"
    good.write_bytes(b"%PDF-1.4 hello")
    rows = [(1, str(good)), (2, str(tmp_path / "gone.pdf"))]
    conn = AsyncMock()
    conn.execute.return_value = make_result(fetchall=rows)

    failed = await backfill_document_hashes(conn)

    assert failed == 1  # the missing file, counted not swallowed
    updated = [c for c in conn.execute.call_args_list if "UPDATE" in str(c)]
    assert len(updated) == 1
```

- [ ] **Step 5: Run it, expect failure**

Run: `... -m pytest tests/unit/test_document_dedup.py -q`
Expected: FAIL — `backfill_document_hashes` is not defined.

- [ ] **Step 6: Implement the backfill** in `backend/database.py`

```python
async def backfill_document_hashes(conn) -> int:
    """Hash existing documents so they participate in deduplication.

    Best-effort: a document can outlive its file, and a missing file must not
    abort startup. But every failure is logged at error and counted -- a silent
    backfill is how a real failure once hid behind "Schema updates applied
    successfully".
    """
    result = await conn.execute(text(
        "SELECT id, original_path FROM rag_documents WHERE content_hash IS NULL"
    ))
    failed = 0
    for doc_id, path in result.fetchall():
        try:
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except Exception as e:
            logger.error(f"Backfill: cannot hash document {doc_id} at {path}: {e}")
            failed += 1
            continue
        await conn.execute(
            text("UPDATE rag_documents SET content_hash = :h WHERE id = :i"),
            {"h": digest, "i": doc_id},
        )
    return failed
```

Call it from `ensure_schema_updates()` after the index block, inside its own savepoint, appending `"content_hash_backfill"` to `failed_updates` when it returns non-zero.

- [ ] **Step 7: Run the test, expect pass**, then the full suite (1583 + new).

- [ ] **Step 8: Commit**

```bash
git add backend/models/rag_document.py backend/database.py backend/tests/unit/test_document_dedup.py
git commit -m "dedup: add content_hash column, index and best-effort backfill"
```

---

### Task 2: Duplicate lookup in `save_uploaded_document`

**Files:**
- Modify: `backend/models/rag_document.py` (new scope helper next to `document_scope`)
- Modify: `backend/services/document_indexing_service.py:59-141`
- Test: `backend/tests/unit/test_document_dedup.py`

**Interfaces:**
- Consumes: `RAGDocument.content_hash` (Task 1).
- Produces:
  - `document_dedupe_scope(user_id, thread_id, group_id) -> ColumnElement`
  - `save_uploaded_document(...) -> tuple[RAGDocument, bool]` — **signature change**, the bool is `created`.

**Why a separate scope helper rather than `document_scope`:** for a library upload the two are identical, but `document_scope(user_id, thread_id=N, ...)` deliberately returns *library ∪ own attachments in N*. Deduplicating an attachment against a library document is exactly what the spec forbids (different lifetime — the attachment dies with its thread). The helper lives in the same module and is built from the same `_library_visible` primitive, so the ACL rule still has one home.

- [ ] **Step 1: Write the failing tests**

```python
async def test_duplicate_upload_returns_existing_without_creating(mock_db, tmp_path):
    existing = SimpleNamespace(id=7, status="completed")
    mock_db.execute.return_value = make_result(scalar=existing)

    doc, created = await save_uploaded_document(
        user_id=1, filename="p.pdf", content=b"%PDF-x", db=mock_db, thread_id=None)

    assert (doc, created) == (existing, False)
    mock_db.add.assert_not_called()          # no row
    assert not list(tmp_path.iterdir())      # no file


async def test_failed_document_is_not_deduplicated_to(mock_db):
    # Deduplicating to a FAILED document would hand the user a broken document
    # AND remove the re-upload that was their only way to fix it.
    mock_db.execute.return_value = make_result(scalar=None)  # FAILED excluded by the query
    doc, created = await save_uploaded_document(
        user_id=1, filename="p.pdf", content=b"%PDF-x", db=mock_db, thread_id=None)
    assert created is True


def test_dedupe_scope_never_crosses_the_library_attachment_boundary():
    library = str(document_dedupe_scope(1, None, 5))
    attachment = str(document_dedupe_scope(1, 42, 5))
    assert "thread_id IS NULL" in library
    assert "group_id" in library            # library dedupes group-wide
    assert "group_id" not in attachment     # attachments never widen to a group
```

- [ ] **Step 2: Run, expect failure** (`document_dedupe_scope` undefined; `save_uploaded_document` returns a bare document).

- [ ] **Step 3: Add the scope helper** in `backend/models/rag_document.py`, directly below `document_read_scope`:

```python
def document_dedupe_scope(
    user_id: int,
    thread_id: Optional[int],
    group_id: Optional[int],
) -> ColumnElement:
    """Which documents a new upload may be recognised as a duplicate OF.

    Narrower than document_scope on purpose: a library upload dedupes against
    library documents (group-wide, so one lab indexes a paper once), and a chat
    attachment dedupes only against the caller's own attachments in the SAME
    thread. Deduplicating across that boundary would alias a library document to
    something deleted with a conversation.
    """
    if thread_id is None:
        return and_(RAGDocument.thread_id.is_(None), _library_visible(user_id, group_id))
    return and_(RAGDocument.user_id == user_id, RAGDocument.thread_id == thread_id)
```

- [ ] **Step 4: Implement the lookup** in `save_uploaded_document`, immediately after `file_type` validation and **before** any directory or file is created:

```python
    content_hash = hashlib.sha256(content).hexdigest()

    # Resolve the group first: the dedupe scope needs it, and so does the row we
    # may be about to create. Fail-closed to owner-only, as before.
    group_id = None
    if thread_id is None:
        try:
            group_id = await get_user_group_id(user_id, db)
        except Exception:
            logger.exception(f"Failed to resolve group for user {user_id}; uploading as owner-only")

    existing = (await db.execute(
        select(RAGDocument).where(
            RAGDocument.content_hash == content_hash,
            RAGDocument.status != DocumentStatus.FAILED.value,
            document_dedupe_scope(user_id, thread_id, group_id),
        ).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "Duplicate upload of %s (sha256 %s...) -> existing document %s",
            filename, content_hash[:12], existing.id,
        )
        return existing, False
```

Set `content_hash=content_hash` on the new `RAGDocument`, delete the now-duplicated `group_id` resolution further down, and change the final line to `return document, True`.

- [ ] **Step 5: Run the tests, expect pass.**

- [ ] **Step 6: Commit**

```bash
git add backend/models/rag_document.py backend/services/document_indexing_service.py backend/tests/unit/test_document_dedup.py
git commit -m "dedup: skip re-storing a file already visible to the uploader"
```

---

### Task 3: Manual upload reports the duplicate

**Files:**
- Modify: `backend/schemas/chat.py:113-123`
- Modify: `backend/routers/rag.py:266-281`
- Modify: `frontend/lib/api.ts`, `frontend/stores/chatStore.ts`, `frontend/components/chat/DocumentsModal.tsx`
- Modify: `frontend/messages/en.json`, `frontend/messages/fr.json`
- Test: `backend/tests/unit/test_document_dedup.py`

**Interfaces:**
- Consumes: `save_uploaded_document(...) -> tuple[RAGDocument, bool]` (Task 2).
- Produces: `RAGDocumentUploadResponse.is_duplicate: bool`.

- [ ] **Step 1: Write the failing test**

```python
async def test_upload_endpoint_reports_duplicate_and_skips_indexing(mock_db):
    tasks = MagicMock()
    with patch("routers.rag.save_uploaded_document",
               AsyncMock(return_value=(SimpleNamespace(
                   id=7, name="p.pdf", file_type="pdf", status="completed",
                   page_count=3, created_at=datetime.now()), False))):
        resp = await rag_r.upload_document(
            tasks, file=_pdf_upload(), thread_id=None,
            current_user=SimpleNamespace(id=1), db=mock_db)

    assert resp.is_duplicate is True
    tasks.add_task.assert_not_called()   # no second indexing run
```

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Add the schema field**

```python
    # True when this upload was recognised as a copy of a document already
    # visible to the uploader: nothing was stored and nothing was indexed, and
    # `id` refers to the pre-existing document.
    is_duplicate: bool = False
```

- [ ] **Step 4: Update the endpoint** (`backend/routers/rag.py:268`)

```python
        document, created = await save_uploaded_document(...)
        await db.commit()

        if created:
            background_tasks.add_task(process_document_async, document.id)
            logger.info(f"Queued document {document.id} for processing")

        response = RAGDocumentUploadResponse.model_validate(document)
        response.is_duplicate = not created
        return response
```

- [ ] **Step 5: Frontend** — `is_duplicate?: boolean` on the upload response type in `api.ts`; in `chatStore.ts`'s `uploadDocument`, do not prepend a row when `is_duplicate` is true (it is already in the list) and surface the notice; render `t("documentDuplicate")` in `DocumentsModal.tsx`.

- [ ] **Step 6: i18n** — add once to each file, as plain text:
  - `en.json`: `"documentDuplicate": "This file is already in your library."`
  - `fr.json`: `"documentDuplicate": "Ce fichier est déjà dans votre bibliothèque."`

- [ ] **Step 7: Run** backend tests, then `npx tsc --noEmit` in `frontend/` (expect exit 0), then confirm each key appears exactly once:
  `grep -c '"documentDuplicate"' frontend/messages/en.json frontend/messages/fr.json` → `1` and `1`.

- [ ] **Step 8: Commit**

```bash
git add backend/schemas/chat.py backend/routers/rag.py frontend/ backend/tests/unit/test_document_dedup.py
git commit -m "dedup: report an already-present file on manual upload"
```

---

### Task 4: Discovery import reports "already in library"

**Files:**
- Modify: `backend/schemas/chat.py` (`ImportResponse`)
- Modify: `backend/routers/rag.py:729-757`
- Modify: `frontend/components/chat/DiscoverSourcesModal.tsx`, `frontend/lib/api.ts`, `frontend/messages/{en,fr}.json`
- Test: `backend/tests/unit/test_paper_discovery.py`

**Interfaces:**
- Consumes: `save_uploaded_document(...) -> tuple[RAGDocument, bool]` (Task 2).
- Produces: `ImportResponse.already_in_library: list[str]` (DOIs).

- [ ] **Step 1: Write the failing test** — a paper whose save returns `created=False` must appear in `already_in_library`, must NOT count toward `imported`, and must NOT schedule indexing.

```python
async def test_import_reports_duplicates_separately_from_imports(mock_db):
    tasks = MagicMock()
    with patch("routers.rag.save_uploaded_document",
               AsyncMock(return_value=(SimpleNamespace(id=3, original_path="/x"), False))), \
         patch("routers.rag.fetch_pdf", AsyncMock(return_value=b"%PDF-x")), \
         patch("routers.rag._resolve_paper_by_doi", AsyncMock(return_value=_paper())):
        resp = await rag_r.import_papers(
            tasks, ImportRequest(dois=["10.1/x"]),
            current_user=SimpleNamespace(id=1), db=mock_db)

    assert resp.imported == 0
    assert resp.already_in_library == ["10.1/x"]
    tasks.add_task.assert_not_called()
```

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Add `already_in_library: list[str] = []` to `ImportResponse`** with a comment that these are neither successes nor failures — the paper is present, nothing was done.

- [ ] **Step 4: Update the import loop**

```python
        document, created = await save_uploaded_document(...)
        if created:
            document.doi = paper.doi
            document.source_url = paper.source_url
        await db.commit()
```
then
```python
        if not created:
            already_in_library.append(doi)
            continue
        background_tasks.add_task(process_document_async, document.id)
        imported += 1
```

**Do not stamp `doi`/`source_url` on a duplicate.** The existing document may belong to a lab mate, and writes stay owner-only.

- [ ] **Step 5: Frontend** — show the count in the import summary; `en.json` `"importAlreadyInLibrary": "{count} already in your library"`, `fr.json` `"importAlreadyInLibrary": "{count} déjà dans votre bibliothèque"`.

- [ ] **Step 6: Run** backend tests + `tsc`, confirm i18n keys appear once each.

- [ ] **Step 7: Commit**

```bash
git commit -am "dedup: report already-present papers separately in discovery import"
```

---

### Task 5: Europe PMC returns every PDF candidate

**Files:**
- Modify: `backend/services/paper_discovery_service.py:44-104`
- Test: `backend/tests/unit/test_paper_discovery.py`

**Interfaces:**
- Produces: `pdf_urls_from_result(raw) -> list[str]`; `PaperResult.pdf_urls: list[str]` with `pdf_url` kept as a property returning the first (so the picker's importable flag and every existing caller are unchanged).

- [ ] **Step 1: Write the failing test**

```python
def test_pdf_urls_returns_every_qualifying_entry_in_order():
    raw = {"fullTextUrlList": {"fullTextUrl": [
        {"documentStyle": "pdf", "availability": "Open access", "site": "PubMedCentral",
         "url": "https://ncbi.example/bot-check"},                       # excluded
        {"documentStyle": "pdf", "availability": "Open access", "site": "Europe_PMC",
         "url": "https://epmc.example/one.pdf"},
        {"documentStyle": "html", "availability": "Free", "site": "Europe_PMC",
         "url": "https://epmc.example/page"},                            # excluded
        {"documentStyle": "pdf", "availability": "Free", "site": "Europe_PMC",
         "url": "https://epmc.example/two.pdf"},
    ]}}
    assert pdf_urls_from_result(raw) == [
        "https://epmc.example/one.pdf", "https://epmc.example/two.pdf"]


def test_pdf_url_is_the_first_candidate():
    paper = parse_epmc_result(_OA_RAW)
    assert paper.pdf_url == paper.pdf_urls[0]
```

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Implement** — rename the loop to collect instead of returning early, keeping the docstring's `site`/`availability` reasoning verbatim:

```python
def pdf_urls_from_result(raw: dict[str, Any]) -> list[str]:
    urls = ((raw.get("fullTextUrlList") or {}).get("fullTextUrl")) or []
    return [
        e["url"] for e in urls
        if (e.get("documentStyle") == "pdf"
            and e.get("availability") in _DOWNLOADABLE
            and e.get("site") == "Europe_PMC"
            and e.get("url"))
    ]
```

In `PaperResult`, replace `pdf_url: Optional[str]` with `pdf_urls: list[str]` and add:

```python
    @property
    def pdf_url(self) -> Optional[str]:
        """First candidate -- what the picker uses to decide 'importable'."""
        return self.pdf_urls[0] if self.pdf_urls else None
```

- [ ] **Step 4: Run the full discovery suite, expect pass** (the property keeps every existing assertion valid).

- [ ] **Step 5: Commit**

```bash
git commit -am "discovery: collect every Europe PMC PDF candidate, not just the first"
```

---

### Task 6: Unpaywall and preprint-host resolvers

**Files:**
- Modify: `backend/config.py` (add `unpaywall_email`)
- Modify: `backend/services/paper_discovery_service.py`
- Test: `backend/tests/unit/test_paper_discovery.py`

**Interfaces:**
- Produces: `async unpaywall_pdf_urls(doi: Optional[str]) -> list[str]`; `preprint_pdf_urls(doi: Optional[str]) -> list[str]`. Both return `[]` on any failure and never raise.

- [ ] **Step 1: Write the failing tests**

```python
async def test_unpaywall_returns_pdf_locations_and_skips_nulls():
    payload = {"oa_locations": [
        {"url_for_pdf": None}, {"url_for_pdf": "https://repo.example/a.pdf"}]}
    with patch("services.paper_discovery_service.httpx.AsyncClient") as c:
        c.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: payload))
        assert await unpaywall_pdf_urls("10.1/x") == ["https://repo.example/a.pdf"]


async def test_unpaywall_never_raises():
    with patch("services.paper_discovery_service.httpx.AsyncClient",
               side_effect=RuntimeError("boom")):
        assert await unpaywall_pdf_urls("10.1/x") == []


@pytest.mark.parametrize("doi, expected", [
    # The reported failure: Research Square, verified live to serve 34 MB of %PDF-
    ("10.21203/rs.3.rs-9043146/v1",
     ["https://www.researchsquare.com/article/rs-9043146/v1.pdf"]),
    # bioRxiv and medRxiv share the 10.1101 prefix, so both are tried.
    ("10.1101/2020.01.01.123456", [
        "https://www.biorxiv.org/content/10.1101/2020.01.01.123456v1.full.pdf",
        "https://www.medrxiv.org/content/10.1101/2020.01.01.123456v1.full.pdf"]),
    ("10.1038/nature12373", []),   # not a known preprint host
    (None, []),
])
def test_preprint_pdf_urls(doi, expected):
    assert preprint_pdf_urls(doi) == expected
```

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Add the setting** in `backend/config.py`:

```python
    # Unpaywall requires a contact address as a query parameter. Free API,
    # legal OA copies only -- never a paywall bypass.
    unpaywall_email: str = "maptimize@utia.cas.cz"
```

- [ ] **Step 4: Implement both resolvers** (derive preprint URLs from the DOI itself — no extra request, deterministic and testable):

```python
_RESEARCH_SQUARE_RE = re.compile(r"^10\.21203/rs\.\d+\.(rs-\d+)/(v\d+)$", re.I)
_BIORXIV_PREFIX = "10.1101/"


def preprint_pdf_urls(doi: Optional[str]) -> list[str]:
    """PDF URLs derivable from a known preprint host's DOI shape.

    Europe PMC's own PDF link can be broken while the preprint server still
    serves the file: 10.21203/rs.3.rs-9043146/v1 returns HTTP 403
    ("PDF link has expired or is invalid") from Europe PMC and 34 MB of PDF
    from Research Square (verified live 2026-07-22).
    """
    if not doi:
        return []
    m = _RESEARCH_SQUARE_RE.match(doi.strip())
    if m:
        return [f"https://www.researchsquare.com/article/{m.group(1)}/{m.group(2)}.pdf"]
    if doi.lower().startswith(_BIORXIV_PREFIX):
        # Both servers use this prefix and the DOI does not say which.
        return [f"https://www.{h}.org/content/{doi}v1.full.pdf"
                for h in ("biorxiv", "medrxiv")]
    return []
```

`unpaywall_pdf_urls` wraps everything in `try/except Exception -> []`, logs at warning, and uses `EPMC_TIMEOUT`.

- [ ] **Step 5: Run, expect pass.**

- [ ] **Step 6: Commit**

```bash
git commit -am "discovery: add Unpaywall and preprint-host PDF resolvers"
```

---

### Task 7: Walk the candidate chain on import

**Files:**
- Modify: `backend/services/paper_discovery_service.py`
- Modify: `backend/routers/rag.py:690-697`
- Test: `backend/tests/unit/test_paper_discovery.py`

**Interfaces:**
- Consumes: `pdf_urls_from_result` (Task 5), `unpaywall_pdf_urls`, `preprint_pdf_urls` (Task 6).
- Produces: `async fetch_paper_pdf(paper: PaperResult) -> bytes`, raising `PdfFetchError` carrying the **last** real error.

- [ ] **Step 1: Write the failing tests**

```python
async def test_second_candidate_is_tried_when_the_first_403s():
    paper = _paper(pdf_urls=["https://a.example/x.pdf", "https://b.example/y.pdf"])
    calls = []
    async def fake_fetch(url):
        calls.append(url)
        if url.startswith("https://a."):
            raise PdfFetchError("Publisher returned HTTP 403")
        return b"%PDF-ok"
    with patch("services.paper_discovery_service.fetch_pdf", fake_fetch):
        assert await fetch_paper_pdf(paper) == b"%PDF-ok"
    assert len(calls) == 2


async def test_unpaywall_is_not_consulted_when_europe_pmc_works():
    # Cost invariant: the fallback must not add requests to the common path.
    unpaywall = AsyncMock(return_value=[])
    with patch("services.paper_discovery_service.fetch_pdf", AsyncMock(return_value=b"%PDF-x")), \
         patch("services.paper_discovery_service.unpaywall_pdf_urls", unpaywall):
        await fetch_paper_pdf(_paper(pdf_urls=["https://a.example/x.pdf"]))
    unpaywall.assert_not_awaited()


async def test_last_real_error_is_reported_when_every_candidate_fails():
    # "3 candidates failed" is useless; 403 vs wrong content-type vs too large
    # is what tells the user whether to retry or fetch it by hand.
    with patch("services.paper_discovery_service.fetch_pdf",
               AsyncMock(side_effect=PdfFetchError("File exceeds 100 MB limit"))), \
         patch("services.paper_discovery_service.unpaywall_pdf_urls", AsyncMock(return_value=[])):
        with pytest.raises(PdfFetchError, match="exceeds 100 MB"):
            await fetch_paper_pdf(_paper(pdf_urls=["https://a.example/x.pdf"]))
```

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Implement**

```python
async def fetch_paper_pdf(paper: PaperResult) -> bytes:
    """Try every known source for this paper's PDF, cheapest first.

    Europe PMC's own links are tried first because they are the ones the picker
    already vetted; the resolvers are only consulted once those are exhausted,
    so the common path costs no extra requests.
    """
    last: Optional[PdfFetchError] = None

    async def attempt(urls: list[str]) -> Optional[bytes]:
        nonlocal last
        for url in urls:
            try:
                return await fetch_pdf(url)
            except PdfFetchError as exc:
                logger.info("PDF candidate failed (%s): %s", exc, url[:120])
                last = exc
        return None

    data = await attempt(paper.pdf_urls)
    if data is not None:
        return data
    data = await attempt(await unpaywall_pdf_urls(paper.doi))
    if data is not None:
        return data
    data = await attempt(preprint_pdf_urls(paper.doi))
    if data is not None:
        return data
    raise last or PdfFetchError("No freely downloadable PDF for this paper")
```

- [ ] **Step 4: Point the import endpoint at it** — replace `return paper, await fetch_pdf(paper.pdf_url)` with `return paper, await fetch_paper_pdf(paper)`, and change the `if not paper.pdf_url` guard to `if not paper.pdf_urls and not paper.doi` (a paper with a DOI but no Europe PMC PDF can still be resolvable).

- [ ] **Step 5: Run the full suite, expect pass.**

- [ ] **Step 6: Commit**

```bash
git commit -am "discovery: fall back through every PDF source before failing an import"
```

---

### Task 8: Verify against reality

**Files:** none (verification only)

- [ ] **Step 1: Full unit suite** — expect ≥ 1583 plus the new tests, zero failures.
- [ ] **Step 2: `npx tsc --noEmit`** in `frontend/` — expect exit 0.
- [ ] **Step 3: Build and deploy** both images with `docker-compose.prod.yml` (`--no-cache`; the backend build takes ~25–30 min because of torch). Wait for the build to finish, then `up -d`.
- [ ] **Step 4: Confirm the new code is in the RUNNING container**, not merely built:

```bash
docker exec maptimize-backend python -c "
import services.paper_discovery_service as p
print(hasattr(p, 'fetch_paper_pdf'), hasattr(p, 'preprint_pdf_urls'))
print(p.preprint_pdf_urls('10.21203/rs.3.rs-9043146/v1'))"
```

- [ ] **Step 5: Import the paper that started this**, live:

```bash
docker exec maptimize-backend python -c "
import asyncio, services.paper_discovery_service as p
async def m():
    r = await p.discover('10.21203/rs.3.rs-9043146/v1', limit=1)
    data = await p.fetch_paper_pdf(r.papers[0])
    print('bytes:', len(data), 'magic:', data[:5])
asyncio.run(m())"
```
Expected: `magic: b'%PDF-'`, roughly 34 MB.

- [ ] **Step 6: Verify deduplication end to end** — upload the same PDF twice through the UI; the second must report "already in your library", add no row, and start no indexing run. Confirm with `SELECT count(*) FROM rag_documents WHERE content_hash = ...` returning 1.

---

## Self-Review

**Spec coverage:** hash key + column + index (T1); backfill with error-level logging (T1); lookup at the shared choke point (T2); the three must-not-dedupe cases — FAILED (T2 test), library/attachment boundary (T2 scope helper), invisible documents (T2, via the scope) (T2); manual upload contract (T3); discovery import contract (T4); all Europe PMC candidates (T5); Unpaywall (T6); preprint patterns (T6); ordered chain with last-error reporting (T7); cost invariant (T7); i18n in both locales (T3, T4). The spec's deliberate scope boundary — the discovery *listing* keeps its Europe-PMC-only badge — is honoured by T5 keeping `pdf_url` as the first candidate, so no listing behaviour changes.

**Type consistency:** `save_uploaded_document` returns `tuple[RAGDocument, bool]` in T2 and is consumed as `document, created` in T3 and T4. `pdf_urls` is a `list[str]` field in T5 and consumed as such in T7. `unpaywall_pdf_urls` is async everywhere; `preprint_pdf_urls` is sync everywhere (T6 defines, T7 awaits only the former).

**Known risk carried into execution:** Task 2 changes a function signature with two production call sites and existing tests that unpack a bare document. Those tests must be updated in Task 2, not left to fail into later tasks.
