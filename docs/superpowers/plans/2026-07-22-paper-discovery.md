# Paper discovery & import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user describe papers they want, see Europe PMC results with metadata, tick the ones with a legally downloadable PDF, and import those into the existing RAG document library.

**Architecture:** A new `paper_discovery_service.py` owns both the Europe PMC client and a size-limited, SSRF-safe PDF fetcher. Two endpoints in `routers/rag.py` (`/discover`, `/discover/import`) sit on top. Import fetches the PDF **first**, then reuses the untouched `save_uploaded_document(...bytes...)` + `process_document_async` pipeline, so indexing, group sharing and the existing progress UI all come for free. The frontend adds one modal modelled on `ExportModal.tsx`.

**Tech Stack:** FastAPI, SQLAlchemy async, httpx (already a base dep), Europe PMC REST, Next.js + Zustand + next-intl.

## Global Constraints

- **Production environment.** Additive schema only (`ADD COLUMN IF NOT EXISTS` + savepoint-guarded), never destructive.
- **Only legally downloadable PDFs.** A result is importable **iff** its `fullTextUrlList.fullTextUrl[]` has an entry with `documentStyle == "pdf"` AND `availability in {"Open access", "Free"}`. Never infer importability from `isOpenAccess`. Never attempt to bypass a paywall.
- **Re-verify server-side.** The import endpoint must re-resolve the PDF URL from Europe PMC; never trust a client-supplied URL.
- **SSRF:** every outbound fetch goes through the existing `_is_safe_url()`; redirects are followed manually with re-validation per hop.
- **Size cap:** 100 MB streamed, matching the upload endpoint's cap.
- **Outbound concurrency to Europe PMC: max 4**, regardless of batch size (politeness — avoid being blocked).
- **Rate limit: 1000 imports/hour/user**, its own counter, separate from the 10/hour upload limit.
- **Library imports only:** always call `save_uploaded_document(..., thread_id=None)` so imports inherit lab-group sharing and are not page-capped.
- **i18n:** every new UI string in BOTH `frontend/messages/en.json` and `fr.json`, exactly once. No hardcoded UI text.
- **Model IDs** come from `settings.gemini_model` — never hardcoded.
- Journal name is `journalInfo.journal.title` (the flat `journalTitle` is empty).

**Test command (backend, offline, mocked DB, mounts the working tree):**
```bash
bash /tmp/claude-1000/-home-cvat-maptimize/a7643a62-291b-4a95-bd78-a524c9b99af8/scratchpad/run-unit-tests.sh tests/unit/<file> -v
```
Full suite: same script with `tests/unit -q` (baseline **1466 passing**).
Frontend: `cd /home/cvat/maptimize/frontend && npx tsc --noEmit`.

---

### Task 1: Schema — `doi` + `source_url` on `rag_documents`

**Files:**
- Modify: `backend/models/rag_document.py` (add two mapped columns to `RAGDocument`)
- Modify: `backend/database.py` (`ensure_schema_updates()` — `updates` list + a `CREATE INDEX` block)
- Test: `backend/tests/unit/test_paper_discovery.py` (create)

**Interfaces:**
- Produces: `RAGDocument.doi: Optional[str]`, `RAGDocument.source_url: Optional[str]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_paper_discovery.py`:
```python
"""Unit tests for paper discovery & import."""
from models.rag_document import RAGDocument


def test_rag_document_has_provenance_columns():
    cols = RAGDocument.__table__.columns
    assert "doi" in cols, "rag_documents needs a doi column (dedupe key)"
    assert "source_url" in cols, "rag_documents needs a source_url column"
    assert cols["doi"].nullable is True
    assert cols["source_url"].nullable is True
    assert cols["doi"].index is True, "doi is the dedupe lookup key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash /tmp/claude-1000/-home-cvat-maptimize/a7643a62-291b-4a95-bd78-a524c9b99af8/scratchpad/run-unit-tests.sh tests/unit/test_paper_discovery.py -v`
Expected: FAIL — `AssertionError: rag_documents needs a doi column`

- [ ] **Step 3: Add the columns to the model**

In `backend/models/rag_document.py`, inside `class RAGDocument`, right after the `group_id` column:
```python
    # Provenance for documents imported from Europe PMC (NULL for manual uploads).
    # doi is indexed because it is the dedupe key: a paper already in the library
    # must be shown as such instead of being imported twice.
    doi: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
```

- [ ] **Step 4: Add the additive migration**

In `backend/database.py`, add to the `updates` list (next to the `rag_documents` entries):
```python
            # Provenance for papers imported from Europe PMC
            ("rag_documents", "doi", "VARCHAR(255)"),
            ("rag_documents", "source_url", "VARCHAR(1000)"),
```
Then, next to the existing `ix_rag_documents_group_id` index block, add the same savepoint-guarded pattern for the doi index:
```python
        try:
            await conn.execute(text("SAVEPOINT idx_doc_doi"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_rag_documents_doi ON rag_documents (doi)"
            ))
            await conn.execute(text("RELEASE SAVEPOINT idx_doc_doi"))
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT idx_doc_doi"))
            logger.error(f"Failed to create ix_rag_documents_doi: {e}")
            failed_updates.append("ix_rag_documents_doi")
```

- [ ] **Step 5: Run test to verify it passes**

Run the same command as Step 2. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/models/rag_document.py backend/database.py backend/tests/unit/test_paper_discovery.py
git commit -m "discovery: add doi + source_url provenance columns to rag_documents"
```

---

### Task 2: Europe PMC client — search + input classification

**Files:**
- Create: `backend/services/paper_discovery_service.py`
- Test: `backend/tests/unit/test_paper_discovery.py` (extend)

**Interfaces:**
- Produces:
  - `classify_query(text: str) -> tuple[str, list[str]]` → `("doi", [...])` | `("titles", [...])` | `("topic", [text])`
  - `PaperResult` dataclass: `doi, title, authors, journal, year, abstract, pmid, pmcid, pdf_url (Optional[str]), source_url (str)`
  - `parse_epmc_result(raw: dict) -> PaperResult`
  - `pdf_url_from_result(raw: dict) -> Optional[str]`
  - `async search_epmc(query: str, limit: int = 25) -> list[PaperResult]`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_paper_discovery.py`:
```python
from services.paper_discovery_service import (
    classify_query, parse_epmc_result, pdf_url_from_result,
)

# Shape verified against the live Europe PMC API on 2026-07-22.
_OA_RAW = {
    "id": "42260696", "source": "MED", "pmid": "42260696", "pmcid": "PMC13248438",
    "doi": "10.1186/s43897-026-00231-0",
    "title": "Stress-induced MAPK organization of microtubules",
    "authorString": "Hlavackova K, Ovecka M.",
    "pubYear": "2026",
    "abstractText": "We show that ...",
    "isOpenAccess": "Y", "hasPDF": "Y",
    "journalInfo": {"journal": {"title": "Molecular horticulture"}},
    "fullTextUrlList": {"fullTextUrl": [
        {"availability": "Subscription required", "documentStyle": "doi",
         "url": "https://doi.org/10.1186/s43897-026-00231-0"},
        {"availability": "Open access", "documentStyle": "pdf",
         "url": "https://europepmc.org/articles/PMC13248438?pdf=render"},
    ]},
}
# A bioRxiv preprint: isOpenAccess "N" but availability "Free" — and NO pdf entry.
_PREPRINT_RAW = {
    "id": "PPR1225870", "source": "PPR", "doi": "10.64898/2026.01.16.699769",
    "title": "Eg5 activity and density-driven bundling", "authorString": "Conway W.",
    "pubYear": "2026", "abstractText": "...", "isOpenAccess": "N", "hasPDF": "N",
    "journalInfo": {},
    "fullTextUrlList": {"fullTextUrl": [
        {"availability": "Free", "documentStyle": "doi",
         "url": "https://doi.org/10.64898/2026.01.16.699769"},
    ]},
}
_PAYWALLED_RAW = {
    "id": "1", "source": "MED", "doi": "10.1038/x", "title": "Paywalled",
    "authorString": "A B.", "pubYear": "2025", "abstractText": "...",
    "isOpenAccess": "N", "hasPDF": "N",
    "journalInfo": {"journal": {"title": "Communications biology"}},
    "fullTextUrlList": {"fullTextUrl": []},
}


def test_pdf_url_only_for_open_access_pdf_entries():
    assert pdf_url_from_result(_OA_RAW) == "https://europepmc.org/articles/PMC13248438?pdf=render"
    # Free-but-no-pdf preprint is NOT importable, despite availability "Free"
    assert pdf_url_from_result(_PREPRINT_RAW) is None
    assert pdf_url_from_result(_PAYWALLED_RAW) is None


def test_pdf_url_ignores_isopenaccess_flag():
    # isOpenAccess must not drive the decision: flip it and nothing changes.
    flipped = {**_PAYWALLED_RAW, "isOpenAccess": "Y", "hasPDF": "Y"}
    assert pdf_url_from_result(flipped) is None


def test_parse_epmc_result_maps_fields():
    r = parse_epmc_result(_OA_RAW)
    assert r.doi == "10.1186/s43897-026-00231-0"
    assert r.journal == "Molecular horticulture"      # journalInfo.journal.title
    assert r.year == "2026"
    assert r.authors == "Hlavackova K, Ovecka M."
    assert r.pdf_url is not None
    assert r.source_url == "https://europepmc.org/abstract/MED/42260696"


def test_parse_epmc_result_tolerates_missing_journal():
    r = parse_epmc_result(_PREPRINT_RAW)
    assert r.journal is None
    assert r.pdf_url is None


def test_classify_query_detects_dois():
    kind, items = classify_query("10.1038/nature12373\n10.1016/j.cell.2020.01.001")
    assert kind == "doi"
    assert items == ["10.1038/nature12373", "10.1016/j.cell.2020.01.001"]


def test_classify_query_detects_doi_urls():
    kind, items = classify_query("https://doi.org/10.1038/nature12373")
    assert kind == "doi"
    assert items == ["10.1038/nature12373"]


def test_classify_query_multiline_is_titles():
    kind, items = classify_query("Tau regulates microtubules\nEg5 drives bundling")
    assert kind == "titles"
    assert len(items) == 2


def test_classify_query_freetext_is_topic():
    kind, items = classify_query("MAP bundling in vitro since 2020")
    assert kind == "topic"
    assert items == ["MAP bundling in vitro since 2020"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash /tmp/.../run-unit-tests.sh tests/unit/test_paper_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.paper_discovery_service'`

- [ ] **Step 3: Create the client**

Create `backend/services/paper_discovery_service.py`:
```python
"""Europe PMC paper discovery + legally-downloadable PDF fetching.

Europe PMC is the single source: one REST API gives search, structured metadata,
and — for open-access records — a direct PDF URL. It indexes PubMed, PMC and
preprints, so a free preprint of an otherwise paywalled paper surfaces naturally.

Importability is decided by the fullTextUrl list, NOT by ``isOpenAccess``:
verified live 2026-07-22, bioRxiv preprints report ``isOpenAccess: "N"`` yet
``availability: "Free"``, while exposing only a DOI link and no PDF. We only ever
download an entry that explicitly advertises a PDF as Open access / Free.
"""
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
# Politeness: never open more than this many connections to Europe PMC at once,
# no matter how many papers the user selected.
EPMC_MAX_CONCURRENCY = 4
EPMC_TIMEOUT = 20.0

# Availability values Europe PMC uses for content we may legally download.
_DOWNLOADABLE = {"Open access", "Free"}

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")


@dataclass
class PaperResult:
    """One candidate paper, already normalised for the picker UI."""
    doi: Optional[str]
    title: str
    authors: Optional[str]
    journal: Optional[str]
    year: Optional[str]
    abstract: Optional[str]
    pmid: Optional[str]
    pmcid: Optional[str]
    pdf_url: Optional[str]   # None => not importable (paywalled / no free PDF)
    source_url: str          # always set: where a human can read about it


def classify_query(text: str) -> tuple[str, list[str]]:
    """Decide how to interpret what the user typed.

    ``("doi", [dois])``    - one or more DOIs (bare or as doi.org URLs)
    ``("titles", [lines])`` - a pasted list of titles (multiple non-empty lines)
    ``("topic", [text])``   - free text to be turned into a search query
    """
    stripped = text.strip()
    dois = _DOI_RE.findall(stripped)
    if dois:
        # Deduplicate while preserving order.
        seen: list[str] = []
        for d in dois:
            if d not in seen:
                seen.append(d)
        return "doi", seen

    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if len(lines) > 1:
        return "titles", lines
    return "topic", [stripped]


def pdf_url_from_result(raw: dict[str, Any]) -> Optional[str]:
    """Return a legally downloadable PDF URL, or None.

    Only an entry that is explicitly a PDF *and* marked Open access / Free
    qualifies. ``isOpenAccess`` is deliberately ignored (see module docstring).
    """
    urls = ((raw.get("fullTextUrlList") or {}).get("fullTextUrl")) or []
    for entry in urls:
        if (entry.get("documentStyle") == "pdf"
                and entry.get("availability") in _DOWNLOADABLE
                and entry.get("url")):
            return entry["url"]
    return None


def _source_url(raw: dict[str, Any]) -> str:
    """A human-readable landing page for the record."""
    source, ext_id = raw.get("source"), raw.get("id")
    if source and ext_id:
        return f"https://europepmc.org/abstract/{source}/{ext_id}"
    doi = raw.get("doi")
    return f"https://doi.org/{doi}" if doi else "https://europepmc.org"


def parse_epmc_result(raw: dict[str, Any]) -> PaperResult:
    """Normalise one Europe PMC record. Tolerates every field being absent."""
    journal = ((raw.get("journalInfo") or {}).get("journal") or {}).get("title")
    return PaperResult(
        doi=raw.get("doi"),
        title=raw.get("title") or "(untitled)",
        authors=raw.get("authorString"),
        journal=journal,
        year=raw.get("pubYear"),
        abstract=raw.get("abstractText"),
        pmid=raw.get("pmid"),
        pmcid=raw.get("pmcid"),
        pdf_url=pdf_url_from_result(raw),
        source_url=_source_url(raw),
    )


async def _epmc_search_raw(query: str, limit: int) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",   # needed for abstractText + journalInfo
        "pageSize": str(max(1, min(limit, 100))),
    }
    async with httpx.AsyncClient(timeout=EPMC_TIMEOUT) as client:
        resp = await client.get(f"{EPMC_BASE}/search", params=params)
        resp.raise_for_status()
        data = resp.json()
    return ((data.get("resultList") or {}).get("result")) or []


async def search_epmc(query: str, limit: int = 25) -> list[PaperResult]:
    """Run one Europe PMC query and return normalised results."""
    raw_results = await _epmc_search_raw(query, limit)
    return [parse_epmc_result(r) for r in raw_results]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash /tmp/.../run-unit-tests.sh tests/unit/test_paper_discovery.py -v`
Expected: PASS (all tests from Step 1).

- [ ] **Step 5: Commit**

```bash
git add backend/services/paper_discovery_service.py backend/tests/unit/test_paper_discovery.py
git commit -m "discovery: Europe PMC client with importability driven by fullTextUrl, not isOpenAccess"
```

---

### Task 3: Size-limited, SSRF-safe PDF fetcher

**Files:**
- Modify: `backend/services/paper_discovery_service.py`
- Test: `backend/tests/unit/test_paper_discovery.py` (extend)

**Interfaces:**
- Consumes: `_is_safe_url` from `services.gemini_agent_service`.
- Produces: `async fetch_pdf(url: str) -> bytes` — raises `PdfFetchError(str)` on any refusal.
- Produces: `class PdfFetchError(Exception)`; `MAX_PDF_BYTES = 100 * 1024 * 1024`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_paper_discovery.py`:
```python
import pytest
import services.paper_discovery_service as pds


class _FakeStream:
    """Minimal stand-in for httpx's streaming response context manager."""
    def __init__(self, status=200, headers=None, chunks=(b"%PDF-1.4 body",)):
        self.status_code = status
        self.headers = headers if headers is not None else {"content-type": "application/pdf"}
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


def _client_returning(stream):
    class _C:
        async def __aenter__(self_inner):
            return self_inner

        async def __aexit__(self_inner, *a):
            return False

        def stream(self_inner, method, url, **kw):
            return stream
    return lambda *a, **kw: _C()


async def test_fetch_pdf_rejects_unsafe_url(monkeypatch):
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (False, "blocked"))
    with pytest.raises(pds.PdfFetchError):
        await pds.fetch_pdf("http://169.254.169.254/latest/meta-data")


async def test_fetch_pdf_enforces_size_cap(monkeypatch):
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (True, ""))
    monkeypatch.setattr(pds, "MAX_PDF_BYTES", 10)
    big = _FakeStream(chunks=(b"x" * 6, b"x" * 6))
    monkeypatch.setattr(pds.httpx, "AsyncClient", _client_returning(big))
    with pytest.raises(pds.PdfFetchError) as ei:
        await pds.fetch_pdf("https://europepmc.org/a.pdf")
    assert "too large" in str(ei.value).lower()


async def test_fetch_pdf_rejects_non_pdf_content_type(monkeypatch):
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (True, ""))
    html = _FakeStream(headers={"content-type": "text/html; charset=utf-8"})
    monkeypatch.setattr(pds.httpx, "AsyncClient", _client_returning(html))
    with pytest.raises(pds.PdfFetchError) as ei:
        await pds.fetch_pdf("https://europepmc.org/a.pdf")
    assert "content-type" in str(ei.value).lower()


async def test_fetch_pdf_rejects_error_status(monkeypatch):
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (True, ""))
    monkeypatch.setattr(pds.httpx, "AsyncClient", _client_returning(_FakeStream(status=404)))
    with pytest.raises(pds.PdfFetchError):
        await pds.fetch_pdf("https://europepmc.org/missing.pdf")


async def test_fetch_pdf_returns_bytes(monkeypatch):
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (True, ""))
    monkeypatch.setattr(pds.httpx, "AsyncClient",
                        _client_returning(_FakeStream(chunks=(b"%PDF-", b"ok"))))
    assert await pds.fetch_pdf("https://europepmc.org/a.pdf") == b"%PDF-ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash /tmp/.../run-unit-tests.sh tests/unit/test_paper_discovery.py -v`
Expected: FAIL — `AttributeError: module 'services.paper_discovery_service' has no attribute 'fetch_pdf'`

- [ ] **Step 3: Implement the fetcher**

Append to `backend/services/paper_discovery_service.py`:
```python
# Mirrors the upload endpoint's cap so a discovered paper can never be bigger
# than something a user could upload by hand.
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_REDIRECTS = 5
PDF_READ_TIMEOUT = 60.0


class PdfFetchError(Exception):
    """A PDF could not be fetched; the message is safe to show the user."""


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Delegate to the agent service's SSRF guard (imported lazily to avoid a
    heavy import at module load; monkeypatchable in tests)."""
    from services.gemini_agent_service import _is_safe_url as guard
    return guard(url)


async def fetch_pdf(url: str) -> bytes:
    """Download a PDF with SSRF, content-type and size guards.

    Redirects are followed manually so every hop is re-validated — a redirect is
    exactly how an open-access URL could be turned into an internal one.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        ok, reason = _is_safe_url(current)
        if not ok:
            raise PdfFetchError(f"Refused to fetch URL: {reason}")

        async with httpx.AsyncClient(timeout=PDF_READ_TIMEOUT, follow_redirects=False) as client:
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise PdfFetchError("Redirect without a target")
                    current = httpx.URL(current).join(location).human_repr()
                    continue
                if resp.status_code != 200:
                    raise PdfFetchError(f"Publisher returned HTTP {resp.status_code}")

                ctype = (resp.headers.get("content-type") or "").lower()
                if "pdf" not in ctype:
                    # A paywall usually answers with an HTML landing page.
                    raise PdfFetchError(f"Not a PDF (content-type: {ctype or 'unknown'})")

                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_PDF_BYTES:
                        raise PdfFetchError("PDF is too large (over 100 MB)")
                    chunks.append(chunk)
                return b"".join(chunks)

    raise PdfFetchError("Too many redirects")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash /tmp/.../run-unit-tests.sh tests/unit/test_paper_discovery.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/paper_discovery_service.py backend/tests/unit/test_paper_discovery.py
git commit -m "discovery: SSRF-safe, size-capped PDF fetcher with per-hop redirect validation"
```

---

### Task 4: Query building + the `/discover` endpoint

**Files:**
- Modify: `backend/services/paper_discovery_service.py` (add `discover`)
- Modify: `backend/schemas/chat.py` (response schemas)
- Modify: `backend/routers/rag.py` (endpoint)
- Test: `backend/tests/unit/test_paper_discovery.py` (extend)

**Interfaces:**
- Consumes: `classify_query`, `search_epmc`, `PaperResult` (Task 2).
- Produces:
  - `async discover(query: str, limit: int = 25) -> list[PaperResult]` (dedupes by DOI across sub-queries)
  - Pydantic `DiscoveredPaper` and `DiscoverResponse` in `schemas/chat.py`
  - `POST /api/rag/discover` body `{"query": str}` → `DiscoverResponse`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_paper_discovery.py`:
```python
from unittest.mock import AsyncMock


async def test_discover_dois_queries_each_doi(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return [pds.parse_epmc_result({**_OA_RAW, "doi": "10.1/a", "id": "a"})]

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    out = await pds.discover("10.1/a\n10.2/b")
    assert len(calls) == 2
    assert all(c.startswith("DOI:") for c in calls), calls
    # same DOI returned twice -> deduped
    assert len(out) == 1


async def test_discover_topic_uses_single_query(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    await pds.discover("MAP bundling in vitro")
    assert calls == ["MAP bundling in vitro"]


async def test_discover_titles_queries_each_line(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    await pds.discover("Tau regulates microtubules\nEg5 drives bundling")
    assert len(calls) == 2
    assert all(c.startswith("TITLE:") for c in calls), calls
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL — `AttributeError: ... has no attribute 'discover'`

- [ ] **Step 3: Implement `discover`**

Append to `backend/services/paper_discovery_service.py`:
```python
import asyncio


async def discover(query: str, limit: int = 25) -> list[PaperResult]:
    """Turn whatever the user typed into a de-duplicated candidate list."""
    kind, items = classify_query(query)
    if kind == "doi":
        queries = [f'DOI:"{d}"' for d in items]
    elif kind == "titles":
        queries = [f'TITLE:"{t}"' for t in items]
    else:
        queries = list(items)

    semaphore = asyncio.Semaphore(EPMC_MAX_CONCURRENCY)

    async def run(q: str) -> list[PaperResult]:
        async with semaphore:
            try:
                return await search_epmc(q, limit=limit)
            except Exception:
                logger.exception("Europe PMC query failed: %s", q[:80])
                return []

    batches = await asyncio.gather(*(run(q) for q in queries))

    seen: set[str] = set()
    merged: list[PaperResult] = []
    for batch in batches:
        for paper in batch:
            key = (paper.doi or paper.source_url).lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(paper)
    return merged
```

- [ ] **Step 4: Add the response schemas**

In `backend/schemas/chat.py`, append:
```python
class DiscoveredPaper(BaseModel):
    """One candidate paper in the discovery picker."""
    doi: Optional[str] = None
    title: str
    authors: Optional[str] = None
    journal: Optional[str] = None
    year: Optional[str] = None
    abstract: Optional[str] = None
    source_url: str
    # True only when Europe PMC advertises a downloadable PDF for this record.
    importable: bool
    # Set when the same DOI is already in the caller's library.
    already_imported: bool = False


class DiscoverResponse(BaseModel):
    query: str
    results: List[DiscoveredPaper]
```

- [ ] **Step 5: Add the endpoint**

In `backend/routers/rag.py`, add the imports and the endpoint:
```python
from services.paper_discovery_service import discover as discover_papers
from schemas.chat import DiscoveredPaper, DiscoverResponse
from pydantic import BaseModel as _BaseModel


class DiscoverRequest(_BaseModel):
    query: str


@router.post("/discover", response_model=DiscoverResponse)
async def discover_sources(
    payload: DiscoverRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search Europe PMC for papers matching the user's description.

    Returns candidates only — nothing is downloaded here. `importable` reflects
    whether Europe PMC advertises a legally downloadable PDF.
    """
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    papers = await discover_papers(query)

    # Mark papers already in the caller's readable library (dedupe by DOI).
    dois = [p.doi for p in papers if p.doi]
    existing: set[str] = set()
    if dois:
        group_id = await get_user_group_id(current_user.id, db)
        rows = await db.execute(
            select(RAGDocument.doi)
            .where(RAGDocument.doi.in_(dois))
            .where(document_scope(current_user.id, None, group_id))
        )
        existing = {d.lower() for d in rows.scalars().all() if d}

    return DiscoverResponse(
        query=query,
        results=[
            DiscoveredPaper(
                doi=p.doi, title=p.title, authors=p.authors, journal=p.journal,
                year=p.year, abstract=(p.abstract or "")[:600] or None,
                source_url=p.source_url,
                importable=p.pdf_url is not None,
                already_imported=bool(p.doi and p.doi.lower() in existing),
            )
            for p in papers
        ],
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `bash /tmp/.../run-unit-tests.sh tests/unit/test_paper_discovery.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/services/paper_discovery_service.py backend/schemas/chat.py backend/routers/rag.py backend/tests/unit/test_paper_discovery.py
git commit -m "discovery: /discover endpoint with DOI dedupe against the readable library"
```

---

### Task 5: The `/discover/import` endpoint

**Files:**
- Modify: `backend/routers/rag.py`
- Test: `backend/tests/unit/test_paper_discovery.py` (extend)

**Interfaces:**
- Consumes: `fetch_pdf`, `PdfFetchError`, `search_epmc`/`discover` (Tasks 2-3); `save_uploaded_document`, `process_document_async`.
- Produces: `POST /api/rag/discover/import` body `{"dois": [str]}` → `ImportResponse` with per-paper outcomes.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_paper_discovery.py`:
```python
from types import SimpleNamespace
import routers.rag as rag_router


async def test_import_reports_per_paper_failure_without_creating_rows(monkeypatch, mock_db):
    paper = pds.parse_epmc_result(_OA_RAW)

    async def fake_resolve(doi):
        return paper

    async def boom(url):
        raise pds.PdfFetchError("Publisher returned HTTP 404")

    saved = AsyncMock()
    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", fake_resolve)
    monkeypatch.setattr(rag_router, "fetch_pdf", boom)
    monkeypatch.setattr(rag_router, "save_uploaded_document", saved)
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=[paper.doi]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )
    assert out.imported == 0
    assert out.failed[0].reason.startswith("Publisher returned HTTP 404")
    saved.assert_not_awaited()  # no orphan row when the download fails


async def test_import_saves_as_library_document(monkeypatch, mock_db):
    paper = pds.parse_epmc_result(_OA_RAW)
    doc = SimpleNamespace(id=7, doi=None, source_url=None)

    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", AsyncMock(return_value=paper))
    monkeypatch.setattr(rag_router, "fetch_pdf", AsyncMock(return_value=b"%PDF-1.4"))
    saved = AsyncMock(return_value=doc)
    monkeypatch.setattr(rag_router, "save_uploaded_document", saved)
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())
    scheduled = []

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=[paper.doi]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: scheduled.append(a)),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )
    assert out.imported == 1
    # library upload -> inherits group sharing, no page cap
    assert saved.await_args.kwargs["thread_id"] is None
    # provenance recorded for dedupe next time
    assert doc.doi == paper.doi
    assert doc.source_url == paper.source_url
    assert scheduled, "indexing must be scheduled"


async def test_import_refuses_paywalled_paper(monkeypatch, mock_db):
    paywalled = pds.parse_epmc_result(_PAYWALLED_RAW)
    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", AsyncMock(return_value=paywalled))
    fetch = AsyncMock()
    monkeypatch.setattr(rag_router, "fetch_pdf", fetch)
    monkeypatch.setattr(rag_router, "save_uploaded_document", AsyncMock())
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=[paywalled.doi]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )
    assert out.imported == 0
    fetch.assert_not_awaited()  # server-side re-verification, never trust the client
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL — `AttributeError: module 'routers.rag' has no attribute 'import_discovered'`

- [ ] **Step 3: Implement the endpoint**

In `backend/routers/rag.py`, add the rate limiter, schemas and endpoint:
```python
from services.paper_discovery_service import (
    fetch_pdf, PdfFetchError, search_epmc, EPMC_MAX_CONCURRENCY,
)

# Discovery imports get their own budget: each one is a user-confirmed,
# open-access PDF from an allow-listed source, not an arbitrary upload, so the
# 10/hour upload cap would be far too tight for a bulk import.
DISCOVERY_RATE_LIMIT_REQUESTS = 1000
DISCOVERY_RATE_LIMIT_WINDOW = 3600


async def _check_discovery_rate_limit(user_id: int, count: int = 1) -> None:
    """Sliding-window limiter mirroring _check_upload_rate_limit."""
    await _check_rate_limit_generic(
        key=f"rate_limit:discovery_import:{user_id}",
        limit=DISCOVERY_RATE_LIMIT_REQUESTS,
        window=DISCOVERY_RATE_LIMIT_WINDOW,
        count=count,
    )


class ImportRequest(_BaseModel):
    dois: List[str]


class ImportFailure(_BaseModel):
    doi: str
    reason: str


class ImportResponse(_BaseModel):
    imported: int
    failed: List[ImportFailure]


async def _resolve_paper_by_doi(doi: str):
    """Re-resolve a paper server-side; never trust a client-supplied PDF URL."""
    results = await search_epmc(f'DOI:"{doi}"', limit=1)
    return results[0] if results else None


@router.post("/discover/import", response_model=ImportResponse)
async def import_discovered(
    payload: ImportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import the selected papers into the document library.

    The PDF is fetched BEFORE any DB row is created, so a failed download leaves
    neither an orphan row nor an orphan file.
    """
    dois = [d.strip() for d in (payload.dois or []) if d and d.strip()]
    if not dois:
        raise HTTPException(status_code=400, detail="No papers selected")
    await _check_discovery_rate_limit(current_user.id, count=len(dois))

    imported = 0
    failures: list[ImportFailure] = []
    semaphore = asyncio.Semaphore(EPMC_MAX_CONCURRENCY)

    async def fetch_one(doi: str):
        async with semaphore:
            paper = await _resolve_paper_by_doi(doi)
            if paper is None:
                raise PdfFetchError("Not found in Europe PMC")
            if not paper.pdf_url:
                raise PdfFetchError("No freely downloadable PDF for this paper")
            return paper, await fetch_pdf(paper.pdf_url)

    for doi in dois:
        try:
            paper, content = await fetch_one(doi)
        except PdfFetchError as e:
            failures.append(ImportFailure(doi=doi, reason=str(e)))
            continue
        except Exception as e:
            logger.exception("Discovery import failed for %s", doi)
            failures.append(ImportFailure(doi=doi, reason=f"Import failed: {e}"))
            continue

        filename = _paper_filename(paper)
        try:
            document = await save_uploaded_document(
                user_id=current_user.id, filename=filename, content=content,
                db=db, thread_id=None,
            )
            document.doi = paper.doi
            document.source_url = paper.source_url
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.exception("Failed to store discovered paper %s", doi)
            failures.append(ImportFailure(doi=doi, reason=f"Could not store: {e}"))
            continue

        background_tasks.add_task(process_document_async, document.id)
        imported += 1

    return ImportResponse(imported=imported, failed=failures)
```

Also add the filename helper next to it:
```python
def _paper_filename(paper) -> str:
    """A readable, collision-tolerant filename. save_uploaded_document sanitises
    it further and prefixes a timestamp, so this only needs to be human-friendly."""
    first_author = (paper.authors or "").split(",")[0].strip() or "paper"
    year = paper.year or "n.d."
    title = (paper.title or "untitled")[:60]
    return f"{first_author} {year} - {title}.pdf"
```

- [ ] **Step 4: Extract the shared rate-limit helper**

`_check_upload_rate_limit` currently inlines its Redis sliding window. Refactor it so both limiters share one implementation — add `_check_rate_limit_generic(key, limit, window, count)` containing the existing logic (including the fail-open on `redis.RedisError` and the `Retry-After` header), and make `_check_upload_rate_limit` call it with the existing upload constants. Do not change upload behaviour.

- [ ] **Step 5: Run tests to verify they pass**

Run: `bash /tmp/.../run-unit-tests.sh tests/unit/test_paper_discovery.py -v`
Expected: PASS. Then run the full suite: `bash /tmp/.../run-unit-tests.sh tests/unit -q` — must stay green (baseline 1466 + the new tests).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/rag.py backend/tests/unit/test_paper_discovery.py
git commit -m "discovery: /discover/import endpoint (fetch-then-store, per-paper outcomes)"
```

---

### Task 6: Frontend API client + store actions

**Files:**
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/stores/chatStore.ts`

**Interfaces:**
- Produces:
  - `api.discoverSources(query: string): Promise<DiscoverResponse>`
  - `api.importDiscovered(dois: string[]): Promise<ImportResult>`
  - types `DiscoveredPaper`, `DiscoverResponse`, `ImportResult`
  - store: `discoverSources(query)`, `importDiscovered(dois)`, state `discoverResults`, `isDiscovering`, `isImportingPapers`

- [ ] **Step 1: Add the API types and methods**

In `frontend/lib/api.ts`, next to the other RAG types:
```typescript
export interface DiscoveredPaper {
  doi?: string;
  title: string;
  authors?: string;
  journal?: string;
  year?: string;
  abstract?: string;
  source_url: string;
  importable: boolean;
  already_imported: boolean;
}

export interface DiscoverResponse {
  query: string;
  results: DiscoveredPaper[];
}

export interface ImportResult {
  imported: number;
  failed: { doi: string; reason: string }[];
}
```
and, in the RAG section of the client class:
```typescript
  async discoverSources(query: string): Promise<DiscoverResponse> {
    return this.request<DiscoverResponse>("/api/rag/discover", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
  }

  async importDiscovered(dois: string[]): Promise<ImportResult> {
    return this.request<ImportResult>("/api/rag/discover/import", {
      method: "POST",
      body: JSON.stringify({ dois }),
    });
  }
```

- [ ] **Step 2: Add the store actions**

In `frontend/stores/chatStore.ts`, add to the interface next to `uploadDocument`:
```typescript
  discoverResults: DiscoveredPaper[];
  isDiscovering: boolean;
  isImportingPapers: boolean;
  discoverSources: (query: string) => Promise<void>;
  importDiscovered: (dois: string[]) => Promise<ImportResult | null>;
```
and the implementations next to `loadDocuments`:
```typescript
  discoverResults: [],
  isDiscovering: false,
  isImportingPapers: false,

  discoverSources: async (query: string) => {
    set({ isDiscovering: true });
    try {
      const res = await api.discoverSources(query);
      set({ discoverResults: res.results });
    } catch (error) {
      console.error("Failed to discover sources:", error);
      set({ discoverResults: [] });
      throw error;
    } finally {
      set({ isDiscovering: false });
    }
  },

  importDiscovered: async (dois: string[]) => {
    set({ isImportingPapers: true });
    try {
      const result = await api.importDiscovered(dois);
      // Imported papers arrive as PENDING documents; reload so they show up in
      // the modal's "processing" bucket with progress.
      await get().loadDocuments();
      return result;
    } catch (error) {
      console.error("Failed to import papers:", error);
      return null;
    } finally {
      set({ isImportingPapers: false });
    }
  },
```

- [ ] **Step 3: Type-check**

Run: `cd /home/cvat/maptimize/frontend && npx tsc --noEmit`
Expected: exit 0, 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/api.ts frontend/stores/chatStore.ts
git commit -m "discovery: frontend API client + store actions"
```

---

### Task 7: `DiscoverSourcesModal` + entry point + i18n

**Files:**
- Create: `frontend/components/chat/DiscoverSourcesModal.tsx`
- Modify: `frontend/components/chat/DocumentsModal.tsx` (button)
- Modify: `frontend/components/chat/index.ts` (export)
- Modify: `frontend/messages/en.json`, `frontend/messages/fr.json`

**Interfaces:**
- Consumes: store `discoverSources`, `importDiscovered`, `discoverResults`, `isDiscovering`, `isImportingPapers`.

- [ ] **Step 1: Add the i18n strings**

Under the `"chat"` object in `frontend/messages/en.json` (edit as plain text — never round-trip the JSON):
```json
    "discoverSources": "Find sources",
    "discoverPlaceholder": "Describe the papers you want, or paste titles / DOIs…",
    "discoverSearch": "Search",
    "discoverSearching": "Searching…",
    "discoverNoResults": "No papers found",
    "discoverPaywalled": "Paywall",
    "discoverAlreadyImported": "In library",
    "discoverOpenAccess": "Open access",
    "discoverSelectAll": "Select all importable",
    "discoverImportSelected": "Import selected",
    "discoverImporting": "Importing…",
    "discoverImportedCount": "{count} imported",
    "discoverFailedCount": "{count} failed",
    "discoverOpenPublisher": "Open at publisher",
```
and the French equivalents in `frontend/messages/fr.json`:
```json
    "discoverSources": "Trouver des sources",
    "discoverPlaceholder": "Décrivez les articles voulus, ou collez des titres / DOI…",
    "discoverSearch": "Rechercher",
    "discoverSearching": "Recherche…",
    "discoverNoResults": "Aucun article trouvé",
    "discoverPaywalled": "Payant",
    "discoverAlreadyImported": "Dans la bibliothèque",
    "discoverOpenAccess": "Libre accès",
    "discoverSelectAll": "Tout sélectionner",
    "discoverImportSelected": "Importer la sélection",
    "discoverImporting": "Importation…",
    "discoverImportedCount": "{count} importés",
    "discoverFailedCount": "{count} échoués",
    "discoverOpenPublisher": "Ouvrir chez l'éditeur",
```

- [ ] **Step 2: Create the modal**

Create `frontend/components/chat/DiscoverSourcesModal.tsx`. Follow `ExportModal.tsx`'s checkbox-list conventions verbatim (same classes) so it matches the rest of the app:

```tsx
"use client";

import { useState, useMemo } from "react";
import { useTranslations } from "next-intl";
import { useChatStore } from "@/stores/chatStore";
import { X, Search, ExternalLink, Loader2, Lock, Check } from "lucide-react";
import { clsx } from "clsx";

interface DiscoverSourcesModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function DiscoverSourcesModal({ isOpen, onClose }: DiscoverSourcesModalProps) {
  const t = useTranslations("chat");
  const tCommon = useTranslations("common");
  const {
    discoverResults, isDiscovering, isImportingPapers,
    discoverSources, importDiscovered,
  } = useChatStore();

  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [summary, setSummary] = useState<string | null>(null);

  // Only open-access, not-yet-imported papers can be selected.
  const selectable = useMemo(
    () => discoverResults.filter((p) => p.importable && !p.already_imported && p.doi),
    [discoverResults]
  );

  const runSearch = async () => {
    if (!query.trim()) return;
    setSelected(new Set());
    setSummary(null);
    try {
      await discoverSources(query.trim());
    } catch {
      setSummary(t("discoverNoResults"));
    }
  };

  const toggle = (doi: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(doi) ? next.delete(doi) : next.add(doi);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) =>
      prev.size === selectable.length
        ? new Set()
        : new Set(selectable.map((p) => p.doi as string))
    );
  };

  const runImport = async () => {
    const result = await importDiscovered(Array.from(selected));
    if (result) {
      setSummary(
        `${t("discoverImportedCount", { count: result.imported })}` +
          (result.failed.length
            ? ` · ${t("discoverFailedCount", { count: result.failed.length })}`
            : "")
      );
      setSelected(new Set());
    }
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm animate-fade-in" onClick={onClose} />
      <div className="fixed inset-0 z-[101] flex items-center justify-center p-4 pointer-events-none">
        <div
          className={clsx(
            "w-full max-w-2xl max-h-[85vh] bg-bg-secondary rounded-xl border border-white/10",
            "shadow-2xl pointer-events-auto flex flex-col animate-scale-in"
          )}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between px-5 py-4 border-b border-white/10">
            <h2 className="text-lg font-semibold text-text-primary">{t("discoverSources")}</h2>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/10 text-text-secondary hover:text-text-primary transition-colors">
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="px-5 py-4 border-b border-white/10 flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") runSearch(); }}
              placeholder={t("discoverPlaceholder")}
              className="flex-1 px-3 py-2 text-sm bg-white/5 border border-white/10 rounded-lg text-text-primary placeholder:text-text-muted focus:outline-none focus:border-primary-500/50"
            />
            <button
              onClick={runSearch}
              disabled={isDiscovering || !query.trim()}
              className="px-4 py-2 rounded-lg bg-primary-500/20 hover:bg-primary-500/30 border border-primary-500/30 text-primary-400 text-sm font-medium disabled:opacity-50 flex items-center gap-2"
            >
              {isDiscovering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              {isDiscovering ? t("discoverSearching") : t("discoverSearch")}
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-5 space-y-2">
            {discoverResults.length === 0 && !isDiscovering && (
              <div className="text-center py-8 text-text-muted">{t("discoverNoResults")}</div>
            )}
            {discoverResults.map((p) => {
              const disabled = !p.importable || p.already_imported || !p.doi;
              return (
                <label
                  key={p.doi || p.source_url}
                  className={clsx(
                    "flex items-start gap-3 p-3 rounded-lg border transition-colors",
                    disabled
                      ? "border-white/5 bg-white/[0.01] opacity-60 cursor-default"
                      : "border-white/10 hover:bg-white/5 cursor-pointer"
                  )}
                >
                  <input
                    type="checkbox"
                    disabled={disabled}
                    checked={!!p.doi && selected.has(p.doi)}
                    onChange={() => p.doi && toggle(p.doi)}
                    className="mt-1 w-4 h-4 rounded border-white/20 bg-bg-secondary text-primary-500 focus:ring-primary-500 disabled:opacity-40"
                  />
                  <span className="flex-1 min-w-0">
                    <span className="block text-sm font-medium text-text-primary">{p.title}</span>
                    <span className="block text-xs text-text-muted mt-0.5">
                      {[p.authors, p.journal, p.year].filter(Boolean).join(" · ")}
                    </span>
                    {p.abstract && (
                      <span className="block text-xs text-text-secondary mt-1 line-clamp-2">{p.abstract}</span>
                    )}
                    <span className="flex items-center gap-2 mt-2">
                      {p.already_imported ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-green-500/15 text-green-400 border border-green-500/20">
                          <Check className="w-3 h-3" />{t("discoverAlreadyImported")}
                        </span>
                      ) : p.importable ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px] bg-primary-500/15 text-primary-400 border border-primary-500/20">
                          {t("discoverOpenAccess")}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/15 text-amber-400 border border-amber-500/20">
                          <Lock className="w-3 h-3" />{t("discoverPaywalled")}
                        </span>
                      )}
                      <a
                        href={p.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center gap-1 text-[10px] text-text-muted hover:text-primary-400"
                      >
                        <ExternalLink className="w-3 h-3" />{t("discoverOpenPublisher")}
                      </a>
                    </span>
                  </span>
                </label>
              );
            })}
          </div>

          <div className="flex items-center justify-between px-5 py-4 border-t border-white/10">
            <div className="flex items-center gap-3">
              <button
                onClick={toggleAll}
                disabled={selectable.length === 0}
                className="text-xs text-text-secondary hover:text-text-primary disabled:opacity-40"
              >
                {t("discoverSelectAll")}
              </button>
              {summary && <span className="text-xs text-text-muted">{summary}</span>}
            </div>
            <div className="flex items-center gap-2">
              <button onClick={onClose} className="px-3 py-2 rounded-lg text-sm text-text-secondary hover:bg-white/5">
                {tCommon("cancel")}
              </button>
              <button
                onClick={runImport}
                disabled={selected.size === 0 || isImportingPapers}
                className="px-4 py-2 rounded-lg bg-primary-500/20 hover:bg-primary-500/30 border border-primary-500/30 text-primary-400 text-sm font-medium disabled:opacity-50 flex items-center gap-2"
              >
                {isImportingPapers && <Loader2 className="w-4 h-4 animate-spin" />}
                {isImportingPapers ? t("discoverImporting") : `${t("discoverImportSelected")} (${selected.size})`}
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 3: Wire the entry point**

In `frontend/components/chat/DocumentsModal.tsx`: import `DiscoverSourcesModal` and `Search` from lucide-react, add `const [isDiscoverOpen, setIsDiscoverOpen] = useState(false);`, render a button directly above `<DocumentUpload />`:
```tsx
            <button
              onClick={() => setIsDiscoverOpen(true)}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-primary-500/10 hover:bg-primary-500/20 border border-primary-500/20 text-primary-400 text-sm font-medium transition-colors"
            >
              <Search className="w-4 h-4" />
              {t("discoverSources")}
            </button>
```
and render `<DiscoverSourcesModal isOpen={isDiscoverOpen} onClose={() => setIsDiscoverOpen(false)} />` next to the existing `ConfirmModal`. Export the new component from `frontend/components/chat/index.ts`.

- [ ] **Step 4: Verify**

```bash
cd /home/cvat/maptimize/frontend && npx tsc --noEmit
node -e "JSON.parse(require('fs').readFileSync('messages/en.json'));JSON.parse(require('fs').readFileSync('messages/fr.json'));console.log('ok')"
```
Expected: tsc exit 0; `ok`. Also confirm each new key appears exactly once per file:
`grep -c '"discoverSources"' messages/en.json messages/fr.json` → `1` each.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/chat/DiscoverSourcesModal.tsx frontend/components/chat/DocumentsModal.tsx frontend/components/chat/index.ts frontend/messages/en.json frontend/messages/fr.json
git commit -m "discovery: Find sources modal with per-paper import selection"
```

---

### Task 8: Verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `bash /tmp/.../run-unit-tests.sh tests/unit -q`
Expected: all green (baseline 1466 + the new discovery tests).

- [ ] **Step 2: Coverage gate**

Run: `bash run-coverage.sh`
Expected: whole suite green; `backend/coverage.json` shows no regression from ~98.8%.

- [ ] **Step 3: Live Europe PMC smoke test (read-only, no writes)**

Confirm the real API still matches what the parser expects:
```bash
docker run --rm --entrypoint python maptimize-unit-test -c "
import asyncio, sys; sys.path.insert(0,'/app')
from services.paper_discovery_service import discover
rs = asyncio.run(discover('microtubule associated protein bundling', limit=5))
print('results:', len(rs))
for r in rs:
    print(' ', 'IMPORTABLE' if r.pdf_url else 'paywall   ', (r.journal or '-')[:28], '|', r.title[:50])
"
```
(mount the working tree as the runner script does). Expected: several results, at least one `IMPORTABLE` with a non-empty journal — proving `journalInfo.journal.title` and the PDF-url rule still hold against the live API.

- [ ] **Step 4: Frontend build**

Run: `cd /home/cvat/maptimize/frontend && npx tsc --noEmit`
Expected: 0 errors.

---

## Self-Review

**Spec coverage:** Europe PMC source → Task 2; input classification (DOI/titles/topic) → Tasks 2+4; result list with the three badge states → Tasks 4+7; import flow (fetch-then-store, concurrency 4, size cap, SSRF) → Tasks 3+5; schema `doi`+`source_url`+index → Task 1; rate limit 1000/h separate counter → Task 5; frontend modal/store/api/i18n → Tasks 6+7; error handling per paper → Task 5; testing → every task + Task 8. ✓

**Deferred from the spec, deliberately:** the Gemini query-rewrite step for free-text topics. Europe PMC's own relevance ranking handles plain natural-language queries acceptably, and adding an LLM hop would add latency plus a failure mode on the critical path. The `classify_query` "topic" branch passes the text straight through, and a rewrite can be slotted into that one branch later without touching anything else. Noted here rather than silently dropped.

**Placeholder scan:** no TBD/TODO; every code step contains complete code; every command has an expected result.

**Type consistency:** `PaperResult` fields (`doi/title/authors/journal/year/abstract/pmid/pmcid/pdf_url/source_url`) are used identically in Tasks 2-5; `DiscoveredPaper`/`DiscoverResponse`/`ImportResponse` names match between `schemas/chat.py`, the router and the TypeScript types; `fetch_pdf`/`PdfFetchError`/`EPMC_MAX_CONCURRENCY`/`discover`/`search_epmc` are referenced with the exact names defined in Tasks 2-3; store action names match the modal's usage.
