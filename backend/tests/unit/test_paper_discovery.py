"""Unit tests for paper discovery & import."""
from models.rag_document import RAGDocument


def test_rag_document_has_provenance_columns():
    cols = RAGDocument.__table__.columns
    assert "doi" in cols, "rag_documents needs a doi column (dedupe key)"
    assert "source_url" in cols, "rag_documents needs a source_url column"
    assert cols["doi"].nullable is True
    assert cols["source_url"].nullable is True
    assert cols["doi"].index is True, "doi is the dedupe lookup key"


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


# ============================================================================ #
# Task 4: discover() + /discover endpoint
# ============================================================================ #
from unittest.mock import AsyncMock


async def test_discover_dois_queries_each_doi(monkeypatch):
    # NOTE: plan's literal fixture used "10.1/a" / "10.2/b", but classify_query's
    # _DOI_RE (Task 2, already committed) requires a 4-9 digit registrant after
    # "10." -- a 1-digit registrant is not a valid DOI shape and would classify
    # as "titles" instead, defeating the point of this test. Swapped in
    # well-formed DOIs so the query is actually routed through the "doi" branch.
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return [pds.parse_epmc_result({**_OA_RAW, "doi": "10.1038/a", "id": "a"})]

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    out = await pds.discover("10.1038/a\n10.1016/b")
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
