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


async def test_discover_raises_discovery_error_when_all_subqueries_fail(monkeypatch):
    # A live timeout/outage must not be reported to the caller as "no results"
    # — every sub-query failing is a search failure, not an empty match set.
    async def always_fail(q, limit=25):
        raise RuntimeError("Europe PMC down")

    monkeypatch.setattr(pds, "search_epmc", always_fail)
    with pytest.raises(pds.DiscoveryError):
        await pds.discover("10.1038/a\n10.1016/b")


async def test_discover_returns_partial_results_when_some_subqueries_fail(monkeypatch):
    # One failing DOI in a multi-DOI batch must not sink the whole batch, and
    # must not raise -- the caller still gets the papers that DID resolve.
    async def flaky(q, limit=25):
        if "10.1038/a" in q:
            raise RuntimeError("Europe PMC down")
        return [pds.parse_epmc_result({**_OA_RAW, "doi": "10.1016/b", "id": "b"})]

    monkeypatch.setattr(pds, "search_epmc", flaky)
    out = await pds.discover("10.1038/a\n10.1016/b")
    assert len(out) == 1
    assert out[0].doi == "10.1016/b"


# ============================================================================ #
# Task 5: /discover/import endpoint + discovery rate limiter
# ============================================================================ #
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


from fastapi import HTTPException


async def test_discover_endpoint_maps_discovery_error_to_502(monkeypatch, mock_db):
    monkeypatch.setattr(
        rag_router, "discover_papers",
        AsyncMock(side_effect=pds.DiscoveryError("Europe PMC search failed")),
    )

    with pytest.raises(HTTPException) as ei:
        await rag_router.discover_sources(
            payload=rag_router.DiscoverRequest(query="MAP bundling"),
            current_user=SimpleNamespace(id=1),
            db=mock_db,
        )
    assert ei.value.status_code == 502


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


def _client_sequence(streams, seen):
    """Fake httpx.AsyncClient that returns `streams` in order and records URLs.

    Needed to exercise the redirect loop: the single-stream helper above always
    hands back the same response, so it can never walk a redirect chain.
    """
    queue = list(streams)

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, **kw):
            seen.append(url)
            return queue.pop(0)

    return lambda *a, **kw: _C()


async def test_fetch_pdf_follows_redirect_and_revalidates_each_hop(monkeypatch):
    """A real Europe PMC PDF URL redirects, so this path must actually work."""
    checked: list[str] = []

    def guard(u):
        checked.append(u)
        return (True, "")

    seen: list[str] = []
    monkeypatch.setattr(pds, "_is_safe_url", guard)
    monkeypatch.setattr(pds.httpx, "AsyncClient", _client_sequence([
        _FakeStream(status=302, headers={"location": "https://cdn.example.org/final.pdf"}),
        _FakeStream(chunks=(b"%PDF-", b"final")),
    ], seen))

    assert await pds.fetch_pdf("https://europepmc.org/a?pdf=render") == b"%PDF-final"
    # every hop was fetched AND every hop was SSRF-checked
    assert seen == ["https://europepmc.org/a?pdf=render", "https://cdn.example.org/final.pdf"]
    assert checked == seen


async def test_fetch_pdf_resolves_relative_redirect(monkeypatch):
    """A relative Location must be joined against the current URL, not fetched raw."""
    seen: list[str] = []
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (True, ""))
    monkeypatch.setattr(pds.httpx, "AsyncClient", _client_sequence([
        _FakeStream(status=302, headers={"location": "/pdf/final.pdf"}),
        _FakeStream(chunks=(b"%PDF-rel",)),
    ], seen))

    assert await pds.fetch_pdf("https://europepmc.org/articles/PMC1?pdf=render") == b"%PDF-rel"
    assert seen[1] == "https://europepmc.org/pdf/final.pdf"


async def test_fetch_pdf_refuses_redirect_to_unsafe_host(monkeypatch):
    """The whole point of manual redirects: a hop into the internal network is blocked."""
    seen: list[str] = []

    def guard(u):
        return (False, "private address") if "169.254" in u else (True, "")

    monkeypatch.setattr(pds, "_is_safe_url", guard)
    monkeypatch.setattr(pds.httpx, "AsyncClient", _client_sequence([
        _FakeStream(status=302, headers={"location": "http://169.254.169.254/latest/meta-data"}),
    ], seen))

    with pytest.raises(pds.PdfFetchError) as ei:
        await pds.fetch_pdf("https://europepmc.org/a?pdf=render")
    assert "refused" in str(ei.value).lower()


async def test_fetch_pdf_gives_up_after_too_many_redirects(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (True, ""))
    hops = [
        _FakeStream(status=302, headers={"location": f"https://example.org/{i}"})
        for i in range(pds.MAX_REDIRECTS + 1)
    ]
    monkeypatch.setattr(pds.httpx, "AsyncClient", _client_sequence(hops, seen))

    with pytest.raises(pds.PdfFetchError) as ei:
        await pds.fetch_pdf("https://europepmc.org/start")
    assert "redirect" in str(ei.value).lower()


async def test_fetch_pdf_rejects_redirect_with_no_location_header(monkeypatch):
    # A 302 that forgets to say where to go is a broken/hostile response, not a
    # hop to follow.
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (True, ""))
    monkeypatch.setattr(pds.httpx, "AsyncClient",
                        _client_returning(_FakeStream(status=302, headers={})))
    with pytest.raises(pds.PdfFetchError) as ei:
        await pds.fetch_pdf("https://europepmc.org/a.pdf")
    assert "target" in str(ei.value).lower()


# ============================================================================ #
# Task 6: close remaining coverage gaps (_source_url fallbacks, the real
# _epmc_search_raw/search_epmc network path, the real _is_safe_url delegate,
# and the /discover + /discover/import branches no test exercised yet).
# ============================================================================ #

def test_source_url_falls_back_to_doi_link_when_no_source_or_id():
    # No "source"/"id" pair (e.g. a record shape Europe PMC didn't fully
    # populate) but a DOI is present -> a doi.org link, not a dead abstract URL.
    assert pds._source_url({"doi": "10.1234/x"}) == "https://doi.org/10.1234/x"


def test_source_url_falls_back_to_generic_epmc_when_nothing_identifies_it():
    assert pds._source_url({}) == "https://europepmc.org"


def test_is_safe_url_real_delegate_blocks_localhost():
    # Every other test in this file replaces pds._is_safe_url outright; this one
    # leaves it alone to exercise the real lazy import + delegation to the
    # agent service's SSRF guard.
    ok, reason = pds._is_safe_url("http://localhost/admin")
    assert ok is False
    assert "localhost" in reason.lower()


class _FakeGetResponse:
    """Minimal stand-in for httpx's non-streaming GET response."""
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _get_client_returning(response, calls=None):
    class _C:
        async def __aenter__(self_inner):
            return self_inner

        async def __aexit__(self_inner, *a):
            return False

        async def get(self_inner, url, params=None):
            if calls is not None:
                calls.append((url, params))
            return response
    return lambda *a, **kw: _C()


async def test_epmc_search_raw_hits_search_endpoint_and_parses_result_list(monkeypatch):
    calls: list = []
    payload = {"resultList": {"result": [_OA_RAW]}}
    monkeypatch.setattr(pds.httpx, "AsyncClient",
                        _get_client_returning(_FakeGetResponse(payload), calls))

    out = await pds._epmc_search_raw("MAP bundling", 5)

    assert out == [_OA_RAW]
    url, params = calls[0]
    assert url == f"{pds.EPMC_BASE}/search"
    assert params["query"] == "MAP bundling"
    assert params["resultType"] == "core"
    assert params["pageSize"] == "5"


async def test_epmc_search_raw_tolerates_missing_result_list(monkeypatch):
    # A malformed/empty response body must yield "no results", not a KeyError.
    monkeypatch.setattr(pds.httpx, "AsyncClient",
                        _get_client_returning(_FakeGetResponse({})))
    assert await pds._epmc_search_raw("nothing found", 5) == []


async def test_search_epmc_normalises_every_raw_result(monkeypatch):
    payload = {"resultList": {"result": [_OA_RAW, _PREPRINT_RAW]}}
    monkeypatch.setattr(pds.httpx, "AsyncClient",
                        _get_client_returning(_FakeGetResponse(payload)))

    out = await pds.search_epmc("query", limit=10)

    assert [p.doi for p in out] == [_OA_RAW["doi"], _PREPRINT_RAW["doi"]]
    assert all(isinstance(p, pds.PaperResult) for p in out)


# ---------------------------------------------------------------------------
# /discover endpoint: empty-query 400 + the DOI-dedupe ("already_imported")
# branch that no existing test drives (the only prior /discover test forces
# the search itself to raise, never reaching the dedupe/response-building code).
# ---------------------------------------------------------------------------
from tests.unit.conftest import make_result


async def test_discover_endpoint_rejects_empty_query(mock_db):
    with pytest.raises(HTTPException) as ei:
        await rag_router.discover_sources(
            payload=rag_router.DiscoverRequest(query="   "),
            current_user=SimpleNamespace(id=1),
            db=mock_db,
        )
    assert ei.value.status_code == 400


async def test_discover_endpoint_marks_already_imported_papers(monkeypatch, mock_db):
    new_paper = pds.parse_epmc_result({**_OA_RAW, "doi": "10.1/new", "id": "new"})
    old_paper = pds.parse_epmc_result({**_OA_RAW, "doi": "10.1/OLD", "id": "old"})
    monkeypatch.setattr(rag_router, "discover_papers",
                        AsyncMock(return_value=[new_paper, old_paper]))
    monkeypatch.setattr(rag_router, "get_user_group_id", AsyncMock(return_value=7))
    # The library already holds the lowercase DOI of `old_paper` -> the match
    # must be case-insensitive.
    mock_db.execute.return_value = make_result(scalars_all=["10.1/old"])

    out = await rag_router.discover_sources(
        payload=rag_router.DiscoverRequest(query="MAP bundling"),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.query == "MAP bundling"
    by_doi = {r.doi: r for r in out.results}
    assert by_doi["10.1/new"].already_imported is False
    assert by_doi["10.1/OLD"].already_imported is True
    assert by_doi["10.1/OLD"].importable is True  # _OA_RAW carries a PDF link


async def test_discover_endpoint_skips_dedupe_lookup_when_no_dois(monkeypatch, mock_db):
    # A paper with no DOI can never match the library by DOI, and the dedupe
    # query must not run at all (no "IN ()" round-trip) when there is nothing
    # to look up.
    paper = pds.parse_epmc_result({**_OA_RAW, "doi": None})
    monkeypatch.setattr(rag_router, "discover_papers", AsyncMock(return_value=[paper]))
    get_group = AsyncMock(return_value=7)
    monkeypatch.setattr(rag_router, "get_user_group_id", get_group)

    out = await rag_router.discover_sources(
        payload=rag_router.DiscoverRequest(query="x"),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.results[0].already_imported is False
    get_group.assert_not_awaited()
    mock_db.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# _check_discovery_rate_limit: real body (every import test monkeypatches it
# away outright).
# ---------------------------------------------------------------------------

async def test_check_discovery_rate_limit_delegates_with_import_budget(monkeypatch):
    generic = AsyncMock()
    monkeypatch.setattr(rag_router, "_check_rate_limit_generic", generic)

    await rag_router._check_discovery_rate_limit(42, count=5)

    generic.assert_awaited_once_with(
        key="rate_limit:discovery_import:42",
        limit=rag_router.DISCOVERY_RATE_LIMIT_REQUESTS,
        window=rag_router.DISCOVERY_RATE_LIMIT_WINDOW,
        count=5,
    )


# ---------------------------------------------------------------------------
# _resolve_paper_by_doi: real body (every import test monkeypatches it away).
# ---------------------------------------------------------------------------

async def test_resolve_paper_by_doi_returns_first_result(monkeypatch):
    paper = pds.parse_epmc_result(_OA_RAW)
    search = AsyncMock(return_value=[paper])
    monkeypatch.setattr(rag_router, "search_epmc", search)

    out = await rag_router._resolve_paper_by_doi("10.1186/x")

    assert out is paper
    search.assert_awaited_once_with('DOI:"10.1186/x"', limit=1)


async def test_resolve_paper_by_doi_returns_none_when_epmc_has_nothing(monkeypatch):
    monkeypatch.setattr(rag_router, "search_epmc", AsyncMock(return_value=[]))
    assert await rag_router._resolve_paper_by_doi("10.1186/missing") is None


# ---------------------------------------------------------------------------
# _paper_filename: field-missing edge cases.
# ---------------------------------------------------------------------------

def test_paper_filename_handles_all_fields_missing():
    paper = SimpleNamespace(authors=None, year=None, title=None)
    assert rag_router._paper_filename(paper) == "paper n.d. - untitled.pdf"


def test_paper_filename_truncates_long_title_and_uses_first_author():
    paper = SimpleNamespace(authors="Smith J, Doe A., Lee K.", year="2024", title="x" * 90)
    name = rag_router._paper_filename(paper)
    assert name.startswith("Smith J 2024 - ")
    assert name.endswith(".pdf")
    assert "x" * 60 in name
    assert "x" * 61 not in name  # title is capped at 60 chars


# ---------------------------------------------------------------------------
# /discover/import: empty-selection 400, DOI-not-found-in-EPMC, generic
# exception during fetch, and rollback when storing the document fails.
# ---------------------------------------------------------------------------

async def test_import_discovered_rejects_empty_selection(mock_db):
    with pytest.raises(HTTPException) as ei:
        await rag_router.import_discovered(
            payload=rag_router.ImportRequest(dois=["   ", ""]),
            background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
            current_user=SimpleNamespace(id=1),
            db=mock_db,
        )
    assert ei.value.status_code == 400


async def test_import_discovered_reports_doi_not_found_in_epmc(monkeypatch, mock_db):
    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", AsyncMock(return_value=None))
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())
    saved = AsyncMock()
    monkeypatch.setattr(rag_router, "save_uploaded_document", saved)

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=["10.1234/missing"]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.imported == 0
    assert out.failed[0].doi == "10.1234/missing"
    assert out.failed[0].reason == "Not found in Europe PMC"
    saved.assert_not_awaited()


async def test_import_discovered_reports_unexpected_exception_as_failure(monkeypatch, mock_db):
    # Anything other than PdfFetchError during resolve/fetch must still be
    # caught and reported per-paper, not bubble up and abort the whole batch.
    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi",
                        AsyncMock(side_effect=RuntimeError("epmc exploded")))
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())
    saved = AsyncMock()
    monkeypatch.setattr(rag_router, "save_uploaded_document", saved)

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=["10.1234/x"]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.imported == 0
    assert out.failed[0].reason == "Import failed: epmc exploded"
    saved.assert_not_awaited()


async def test_import_discovered_rolls_back_when_storing_fails(monkeypatch, mock_db):
    paper = pds.parse_epmc_result(_OA_RAW)
    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", AsyncMock(return_value=paper))
    monkeypatch.setattr(rag_router, "fetch_pdf", AsyncMock(return_value=b"%PDF-1.4"))
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())
    monkeypatch.setattr(rag_router, "save_uploaded_document",
                        AsyncMock(side_effect=RuntimeError("disk full")))

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=[paper.doi]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.imported == 0
    assert out.failed[0].reason == "Could not store: disk full"
    mock_db.rollback.assert_awaited_once()
