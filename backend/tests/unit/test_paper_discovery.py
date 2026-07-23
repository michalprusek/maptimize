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
    classify_query, parse_epmc_result, pdf_urls_from_result,
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
        {"availability": "Open access", "documentStyle": "pdf", "site": "Europe_PMC",
         "url": "https://europepmc.org/articles/PMC13248438?pdf=render"},
    ]},
}
# Live-verified 2026-07-22: a "site": "PubMedCentral" pdf entry looks importable
# (documentStyle pdf, availability Open access) but its url redirects to an NCBI
# bot-check page when fetched server-side -- must NOT be treated as importable.
_PMC_NCBI_RAW = {
    "id": "9999999", "source": "MED", "doi": "10.1000/pmc-ncbi",
    "title": "A paper only mirrored on PMC/NCBI", "authorString": "Doe J.",
    "pubYear": "2025", "abstractText": "...",
    "isOpenAccess": "Y", "hasPDF": "Y",
    "journalInfo": {"journal": {"title": "Some journal"}},
    "fullTextUrlList": {"fullTextUrl": [
        {"availability": "Open access", "documentStyle": "pdf", "site": "PubMedCentral",
         "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9999999/pdf/paper.pdf"},
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
    assert pdf_urls_from_result(_OA_RAW) == [
        "https://europepmc.org/articles/PMC13248438?pdf=render"]
    # Free-but-no-pdf preprint is NOT importable, despite availability "Free"
    assert pdf_urls_from_result(_PREPRINT_RAW) == []
    assert pdf_urls_from_result(_PAYWALLED_RAW) == []


def test_pdf_url_ignores_isopenaccess_flag():
    # isOpenAccess must not drive the decision in EITHER direction -- flip it
    # on both a downloadable and a non-downloadable record and nothing changes.
    #
    # The "Y" case is the load-bearing one: live-verified 2026-07-22, bioRxiv
    # preprints report isOpenAccess "N" while still exposing a Free Europe_PMC
    # pdf entry, so an implementation that gated on isOpenAccess would drop
    # them. Flipping the flag on a record with an EMPTY fullTextUrlList (the
    # only case this test used to cover) can never fail: the loop has nothing
    # to iterate, so it returns None no matter what the flag says.
    still_downloadable = {**_OA_RAW, "isOpenAccess": "N", "hasPDF": "N"}
    assert (pdf_urls_from_result(still_downloadable)
            == ["https://europepmc.org/articles/PMC13248438?pdf=render"])

    still_not_downloadable = {**_PAYWALLED_RAW, "isOpenAccess": "Y", "hasPDF": "Y"}
    assert pdf_urls_from_result(still_not_downloadable) == []


def test_pdf_url_excludes_pubmedcentral_ncbi_entries():
    # documentStyle=pdf + availability=Open access alone are NOT enough --
    # a PubMedCentral-sited entry's url 404s/bot-checks when fetched
    # server-side, so it must be excluded despite otherwise qualifying.
    assert pdf_urls_from_result(_PMC_NCBI_RAW) == []


def test_pdf_url_requires_europe_pmc_site_even_with_no_site_key():
    # A malformed/older record shape with no "site" key at all must not be
    # treated as importable by accident (missing != "Europe_PMC").
    no_site = {**_OA_RAW, "fullTextUrlList": {"fullTextUrl": [
        {"availability": "Open access", "documentStyle": "pdf",
         "url": "https://example.org/no-site-field.pdf"},
    ]}}
    assert pdf_urls_from_result(no_site) == []


def test_parse_epmc_result_maps_fields():
    r = parse_epmc_result(_OA_RAW)
    assert r.doi == "10.1186/s43897-026-00231-0"
    assert r.journal == "Molecular horticulture"      # journalInfo.journal.title
    assert r.year == "2026"
    assert r.authors == "Hlavackova K, Ovecka M."
    assert r.pdf_urls  # importability is bool(pdf_urls), there is no singular accessor
    assert r.source_url == "https://europepmc.org/abstract/MED/42260696"


def test_parse_epmc_result_tolerates_missing_journal():
    r = parse_epmc_result(_PREPRINT_RAW)
    assert r.journal is None
    assert r.pdf_urls == []


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


import httpx
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


async def test_fetch_pdf_ssrf_refusal_message_is_generic_but_log_has_detail(monkeypatch, caplog):
    # _is_safe_url's internal reason can contain a resolved private IP and
    # must never reach the client; the raised message is a fixed, generic
    # string, while the real reason still goes to the server-side log.
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (False, "Access to private IP address (10.0.0.5) is not allowed"))
    with caplog.at_level("WARNING", logger=pds.logger.name):
        with pytest.raises(pds.PdfFetchError) as ei:
            await pds.fetch_pdf("http://169.254.169.254/latest/meta-data")
    assert str(ei.value) == pds.SSRF_REFUSAL_MESSAGE
    assert "10.0.0.5" not in str(ei.value)
    assert "10.0.0.5" in caplog.text


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


async def test_fetch_pdf_rejects_body_not_starting_with_pdf_magic_bytes(monkeypatch):
    # A 200 response with a "pdf" content-type can still be an HTML error page
    # some publishers mislabel -- fail fast instead of storing a file the
    # indexer will choke on later.
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (True, ""))
    not_really_pdf = _FakeStream(
        headers={"content-type": "application/pdf"},
        chunks=(b"<html>not a pdf</html>",),
    )
    monkeypatch.setattr(pds.httpx, "AsyncClient", _client_returning(not_really_pdf))
    with pytest.raises(pds.PdfFetchError) as ei:
        await pds.fetch_pdf("https://europepmc.org/fake.pdf")
    assert "not a valid pdf" in str(ei.value).lower()


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
    assert len(out.papers) == 1
    assert out.failed_queries == 0
    assert out.dropped_queries == 0


async def test_discover_topic_uses_single_query(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    # Rewrite unavailable/off -> falls back to the raw text (pre-rewrite
    # behaviour). The test harness already forces GEMINI_API_KEY="" so this
    # would be the real outcome anyway; mocked explicitly so the test doesn't
    # depend on that incidental environment detail.
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value=None))
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
    assert len(out.papers) == 1
    assert out.papers[0].doi == "10.1016/b"
    assert out.failed_queries == 1  # the caller can tell one sub-query broke


async def test_discover_caps_titles_at_max_subqueries(monkeypatch):
    # A pasted 300-entry bibliography must not turn into 300 outbound EPMC
    # requests -- only the first MAX_SUBQUERIES lines are ever queried, and the
    # caller is told how many were dropped.
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    many_titles = "\n".join(f"Title number {i}" for i in range(pds.MAX_SUBQUERIES + 5))
    out = await pds.discover(many_titles)
    assert len(calls) == pds.MAX_SUBQUERIES
    assert out.dropped_queries == 5


async def test_discover_caps_dois_at_max_subqueries(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    many_dois = "\n".join(f"10.1038/nature{10000 + i}" for i in range(pds.MAX_SUBQUERIES + 3))
    out = await pds.discover(many_dois)
    assert len(calls) == pds.MAX_SUBQUERIES
    assert out.dropped_queries == 3


async def test_discover_topic_query_is_not_capped_or_dropped(monkeypatch):
    # The truncation is specific to the doi/titles fan-out; a single free-text
    # topic query is exactly one sub-query and nothing is ever "dropped".
    monkeypatch.setattr(pds, "search_epmc", AsyncMock(return_value=[]))
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value=None))
    out = await pds.discover("MAP bundling in vitro")
    assert out.dropped_queries == 0


async def test_discover_uses_small_result_limit_for_doi_and_titles(monkeypatch):
    # doi/title sub-queries are a specific match target -- don't ask Europe PMC
    # for a caller-sized page of fuzzy matches per pasted title.
    seen_limits = []

    async def fake_search(q, limit=25):
        seen_limits.append(limit)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    await pds.discover("Tau regulates microtubules\nEg5 drives bundling", limit=25)
    assert seen_limits == [pds.SUBQUERY_RESULT_LIMIT, pds.SUBQUERY_RESULT_LIMIT]


async def test_discover_escapes_quotes_in_title_query(monkeypatch):
    # A pasted title containing a literal `"` must not be able to break out of
    # the quoted TITLE:"..." term and inject new Europe PMC query syntax.
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    await pds.discover('x" OR (OPEN_ACCESS:Y)\nsecond title here')
    assert all('"' not in c[len('TITLE:"'):-1] for c in calls), calls
    assert calls[0] == 'TITLE:"x OR (OPEN_ACCESS:Y)"'


# ============================================================================ #
# LLM query rewrite for free-text topic searches (rewrite_topic_query) and its
# wiring into discover()'s topic branch. See paper_discovery_service.py's
# module docstring / CLAUDE.md for the motivating live failure: a natural-
# language request like "find all microtubule related papers from lab of dr.
# carsten janke" sent to Europe PMC verbatim returns zero relevant papers.
# ============================================================================ #
import asyncio
import sys
import types as pytypes
from unittest.mock import AsyncMock, MagicMock, patch


def _rewrite_genai_response(text):
    """A fake google.genai response carrying `.text` -- rewrite_topic_query
    reads only that attribute (unlike the vision-extraction path elsewhere in
    the codebase, this is a plain text-in/text-out call)."""
    return SimpleNamespace(text=text)


def _patch_query_rewrite_genai(response_or_callable):
    """Inject a fake google.genai module so rewrite_topic_query's lazy import
    resolves to our stub.

    rewrite_topic_query calls the native async SDK method
    ``client.aio.models.generate_content`` (matches rag_service's
    extract_relevant_passages -- genuinely cancellable on timeout, unlike the
    old ``asyncio.wait_for(asyncio.to_thread(...))`` pattern), so the stub is
    an ``AsyncMock``. When ``response_or_callable`` is a plain callable,
    ``AsyncMock.side_effect`` awaits it automatically if it's itself an async
    function (see the timeout test below), or calls it synchronously
    otherwise (e.g. a plain function that raises, for the exception test).
    """
    if callable(response_or_callable):
        generate_content = AsyncMock(side_effect=response_or_callable)
    else:
        generate_content = AsyncMock(return_value=response_or_callable)

    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    genai = pytypes.ModuleType("google.genai")
    genai.Client = MagicMock(return_value=client)
    type_mod = pytypes.ModuleType("google.genai.types")
    type_mod.ThinkingConfig = MagicMock()
    type_mod.GenerateContentConfig = MagicMock()
    genai.types = type_mod
    google_pkg = pytypes.ModuleType("google")
    google_pkg.genai = genai
    return patch.dict("sys.modules", {
        "google": google_pkg,
        "google.genai": genai,
        "google.genai.types": type_mod,
    }), generate_content


# --------------------------------------------------------------------------- #
# rewrite_topic_query: no API key / SDK missing -> None, no call attempted.
# --------------------------------------------------------------------------- #

async def test_rewrite_topic_query_no_api_key_returns_none_without_calling(monkeypatch):
    fake_settings = SimpleNamespace(gemini_api_key="", gemini_model="gemini-3.6-flash")
    ctx, generate_content = _patch_query_rewrite_genai(_rewrite_genai_response("AUTH:\"X\""))
    with patch.object(pds, "settings", fake_settings), ctx:
        out = await pds.rewrite_topic_query("papers from the Janke lab")
    assert out is None
    generate_content.assert_not_called()  # no API key -> never even attempted


async def test_rewrite_topic_query_returns_none_when_sdk_missing(monkeypatch):
    fake_settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.6-flash")
    # sys.modules[name] = None is the standard trick to force `import google.genai`
    # to raise ImportError, without needing the real package to be absent.
    monkeypatch.setitem(sys.modules, "google.genai", None)
    with patch.object(pds, "settings", fake_settings):
        out = await pds.rewrite_topic_query("papers from the Janke lab")
    assert out is None


# --------------------------------------------------------------------------- #
# rewrite_topic_query: success path + sanitization of the model's reply.
# --------------------------------------------------------------------------- #

async def test_rewrite_topic_query_success_uses_configured_model_and_low_thinking(monkeypatch):
    fake_settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.6-flash")
    ctx, generate_content = _patch_query_rewrite_genai(
        _rewrite_genai_response('AUTH:"Janke C" AND microtubule')
    )
    with patch.object(pds, "settings", fake_settings), ctx:
        out = await pds.rewrite_topic_query(
            "find all microtubule related papers from lab of dr. carsten janke"
        )
    assert out == 'AUTH:"Janke C" AND microtubule'
    # settings.gemini_model is NEVER hardcoded (CLAUDE.md rule).
    assert generate_content.call_args.kwargs["model"] == "gemini-3.6-flash"


async def test_rewrite_topic_query_strips_markdown_fence(monkeypatch):
    fake_settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.6-flash")
    fenced = '```\nAUTH:"Janke C" AND microtubule\n```'
    ctx, _ = _patch_query_rewrite_genai(_rewrite_genai_response(fenced))
    with patch.object(pds, "settings", fake_settings), ctx:
        out = await pds.rewrite_topic_query("microtubule papers by Janke")
    assert out == 'AUTH:"Janke C" AND microtubule'


async def test_rewrite_topic_query_strips_trailing_prose(monkeypatch):
    fake_settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.6-flash")
    chatty = (
        'AUTH:"Janke C" AND microtubule\n'
        "This query searches for papers authored by Janke about microtubules."
    )
    ctx, _ = _patch_query_rewrite_genai(_rewrite_genai_response(chatty))
    with patch.object(pds, "settings", fake_settings), ctx:
        out = await pds.rewrite_topic_query("microtubule papers by Janke")
    assert out == 'AUTH:"Janke C" AND microtubule'


# --------------------------------------------------------------------------- #
# rewrite_topic_query: must never raise -- exception and timeout both -> None.
# --------------------------------------------------------------------------- #

async def test_rewrite_topic_query_returns_none_on_exception(monkeypatch):
    fake_settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.6-flash")

    def boom(*a, **k):
        raise RuntimeError("Gemini API exploded")

    ctx, _ = _patch_query_rewrite_genai(boom)
    with patch.object(pds, "settings", fake_settings), ctx:
        out = await pds.rewrite_topic_query("microtubule papers by Janke")
    assert out is None


async def test_rewrite_topic_query_returns_none_on_timeout(monkeypatch):
    fake_settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.6-flash")
    monkeypatch.setattr(pds, "_QUERY_REWRITE_TIMEOUT", 0.05)

    # Must be a real `await asyncio.sleep`, not a blocking `time.sleep`: the
    # native async SDK call (Fix #5) is genuinely cancellable by
    # asyncio.wait_for only because it actually yields control back to the
    # event loop -- a synchronous sleep here would block the loop itself and
    # defeat the point of the test (the timeout could never fire mid-call).
    async def slow(*a, **k):
        await asyncio.sleep(0.3)
        return _rewrite_genai_response('AUTH:"Janke C"')

    ctx, _ = _patch_query_rewrite_genai(slow)
    with patch.object(pds, "settings", fake_settings), ctx:
        out = await pds.rewrite_topic_query("microtubule papers by Janke")
    assert out is None


# --------------------------------------------------------------------------- #
# _sanitize_rewritten_query: direct unit coverage of edge cases.
# --------------------------------------------------------------------------- #

def test_sanitize_rewritten_query_empty_and_whitespace_only():
    assert pds._sanitize_rewritten_query(None) is None
    assert pds._sanitize_rewritten_query("") is None
    assert pds._sanitize_rewritten_query("   \n  \n") is None


# --------------------------------------------------------------------------- #
# discover()'s topic branch: uses the rewrite when it succeeds, falls back to
# the raw text on any failure mode, and never calls the rewrite for doi/titles
# (the hard cost-control requirement: one Gemini call per free-text search,
# ZERO for doi/title searches, since those are already-structured queries).
# --------------------------------------------------------------------------- #

async def test_discover_topic_branch_uses_rewritten_query(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        # Non-empty: Fix #2 retries with the raw text once a rewritten query
        # comes back with zero results, which would add a second call and
        # defeat the point of this test (that the rewritten query alone is
        # what got searched). The empty-result retry has its own tests below.
        return [pds.parse_epmc_result({**_OA_RAW, "doi": "10.1/x", "id": "x"})]

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    monkeypatch.setattr(
        pds, "rewrite_topic_query",
        AsyncMock(return_value='AUTH:"Janke C" AND microtubule'),
    )
    out = await pds.discover("find all microtubule related papers from lab of dr. carsten janke")
    assert calls == ['AUTH:"Janke C" AND microtubule']
    assert out.effective_query == 'AUTH:"Janke C" AND microtubule'
    assert out.rewrite_failed is False


async def test_discover_topic_branch_falls_back_when_rewrite_raises(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    monkeypatch.setattr(
        pds, "rewrite_topic_query", AsyncMock(side_effect=RuntimeError("Gemini down")),
    )
    out = await pds.discover("MAP bundling in vitro")  # must not raise
    assert calls == ["MAP bundling in vitro"]
    assert out.effective_query is None


async def test_discover_topic_branch_falls_back_when_rewrite_returns_none(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value=None))
    out = await pds.discover("MAP bundling in vitro")
    assert calls == ["MAP bundling in vitro"]
    assert out.effective_query is None


async def test_discover_topic_branch_falls_back_when_rewrite_returns_whitespace(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value="   "))
    out = await pds.discover("MAP bundling in vitro")
    assert calls == ["MAP bundling in vitro"]
    assert out.effective_query is None


async def test_discover_doi_branch_never_calls_rewrite(monkeypatch):
    monkeypatch.setattr(pds, "search_epmc", AsyncMock(return_value=[]))
    rewrite = AsyncMock(return_value="should never be used")
    monkeypatch.setattr(pds, "rewrite_topic_query", rewrite)
    await pds.discover("10.1038/nature12373\n10.1016/j.cell.2020.01.001")
    rewrite.assert_not_awaited()  # DOIs are already structured -- no LLM call, no cost


async def test_discover_titles_branch_never_calls_rewrite(monkeypatch):
    monkeypatch.setattr(pds, "search_epmc", AsyncMock(return_value=[]))
    rewrite = AsyncMock(return_value="should never be used")
    monkeypatch.setattr(pds, "rewrite_topic_query", rewrite)
    await pds.discover("Tau regulates microtubules\nEg5 drives bundling")
    rewrite.assert_not_awaited()  # pasted titles are already structured -- no LLM call


# --------------------------------------------------------------------------- #
# /discover endpoint: effective_query passes through to the response only
# when discover() actually set it.
# --------------------------------------------------------------------------- #

async def test_discover_endpoint_surfaces_effective_query_when_rewrite_applied(monkeypatch, mock_db):
    monkeypatch.setattr(rag_router, "_check_discovery_search_rate_limit", AsyncMock())
    monkeypatch.setattr(rag_router, "discover_papers", AsyncMock(return_value=pds.DiscoveryResult(
        papers=[], failed_queries=0, dropped_queries=0,
        effective_query='AUTH:"Janke C" AND microtubule',
    )))

    out = await rag_router.discover_sources(
        payload=rag_router.DiscoverRequest(query="papers from the Janke lab"),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )
    assert out.effective_query == 'AUTH:"Janke C" AND microtubule'


async def test_discover_endpoint_effective_query_is_none_when_rewrite_did_not_apply(monkeypatch, mock_db):
    monkeypatch.setattr(rag_router, "_check_discovery_search_rate_limit", AsyncMock())
    monkeypatch.setattr(rag_router, "discover_papers", AsyncMock(return_value=pds.DiscoveryResult(
        papers=[], failed_queries=0, dropped_queries=0,
    )))

    out = await rag_router.discover_sources(
        payload=rag_router.DiscoverRequest(query="10.1038/nature12373"),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )
    assert out.effective_query is None


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
    monkeypatch.setattr(rag_router, "fetch_paper_pdf", boom)
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
    monkeypatch.setattr(rag_router, "fetch_paper_pdf", AsyncMock(return_value=b"%PDF-1.4"))
    saved = AsyncMock(return_value=(doc, True))
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
    monkeypatch.setattr(rag_router, "_check_discovery_search_rate_limit", AsyncMock())
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
    monkeypatch.setattr(rag_router, "fetch_paper_pdf", fetch)
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
    # The generic, fixed message -- not guard()'s internal "private address" reason.
    assert str(ei.value) == pds.SSRF_REFUSAL_MESSAGE
    assert "private address" not in str(ei.value)


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


def test_discover_request_rejects_oversized_query():
    # A pasted 300-entry bibliography must be rejected at the schema layer,
    # before classify_query/discover ever see it (defense in depth alongside
    # discover()'s own MAX_SUBQUERIES cap).
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        rag_router.DiscoverRequest(query="x" * 4001)

    # Exactly at the limit is fine.
    rag_router.DiscoverRequest(query="x" * 4000)


async def test_discover_endpoint_marks_already_imported_papers(monkeypatch, mock_db):
    monkeypatch.setattr(rag_router, "_check_discovery_search_rate_limit", AsyncMock())
    new_paper = pds.parse_epmc_result({**_OA_RAW, "doi": "10.1/new", "id": "new"})
    old_paper = pds.parse_epmc_result({**_OA_RAW, "doi": "10.1/OLD", "id": "old"})
    monkeypatch.setattr(rag_router, "discover_papers", AsyncMock(return_value=pds.DiscoveryResult(
        papers=[new_paper, old_paper], failed_queries=0, dropped_queries=0,
    )))
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
    assert out.failed_queries == 0
    assert out.dropped_queries == 0
    by_doi = {r.doi: r for r in out.results}
    assert by_doi["10.1/new"].already_imported is False
    assert by_doi["10.1/OLD"].already_imported is True
    assert by_doi["10.1/OLD"].importable is True  # _OA_RAW carries a PDF link


async def test_discover_endpoint_surfaces_failed_and_dropped_query_counts(monkeypatch, mock_db):
    # Partial search failures / the MAX_SUBQUERIES cap must be visible to the
    # caller, not silently swallowed into an apparently-complete result list.
    monkeypatch.setattr(rag_router, "_check_discovery_search_rate_limit", AsyncMock())
    monkeypatch.setattr(rag_router, "discover_papers", AsyncMock(return_value=pds.DiscoveryResult(
        papers=[], failed_queries=3, dropped_queries=5,
    )))

    out = await rag_router.discover_sources(
        payload=rag_router.DiscoverRequest(query="MAP bundling"),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.failed_queries == 3
    assert out.dropped_queries == 5


async def test_discover_endpoint_skips_dedupe_lookup_when_no_dois(monkeypatch, mock_db):
    # A paper with no DOI can never match the library by DOI, and the dedupe
    # query must not run at all (no "IN ()" round-trip) when there is nothing
    # to look up.
    monkeypatch.setattr(rag_router, "_check_discovery_search_rate_limit", AsyncMock())
    paper = pds.parse_epmc_result({**_OA_RAW, "doi": None})
    monkeypatch.setattr(rag_router, "discover_papers", AsyncMock(return_value=pds.DiscoveryResult(
        papers=[paper], failed_queries=0, dropped_queries=0,
    )))
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


async def test_check_discovery_search_rate_limit_delegates_with_search_budget(monkeypatch):
    # /discover previously had NO rate limiter at all -- this is its own,
    # distinct budget/key from the import limiter above.
    generic = AsyncMock()
    monkeypatch.setattr(rag_router, "_check_rate_limit_generic", generic)

    await rag_router._check_discovery_search_rate_limit(42)

    generic.assert_awaited_once_with(
        key="rate_limit:discover:42",
        limit=rag_router.DISCOVERY_SEARCH_RATE_LIMIT_REQUESTS,
        window=rag_router.DISCOVERY_SEARCH_RATE_LIMIT_WINDOW,
    )


# ---------------------------------------------------------------------------
# _resolve_paper_by_doi: real body (every import test monkeypatches it away).
# ---------------------------------------------------------------------------

async def test_resolve_paper_by_doi_returns_first_result(monkeypatch):
    paper = pds.parse_epmc_result(_OA_RAW)
    search = AsyncMock(return_value=[paper])
    monkeypatch.setattr(rag_router, "search_epmc", search)

    # Call with the SAME doi the fake result carries -- the point of this test
    # is "a genuine match is returned", not the mismatch-rejection behaviour,
    # which has its own test below.
    out = await rag_router._resolve_paper_by_doi(paper.doi)

    assert out is paper
    search.assert_awaited_once_with(f'DOI:"{paper.doi}"', limit=1)


async def test_resolve_paper_by_doi_returns_none_when_epmc_has_nothing(monkeypatch):
    monkeypatch.setattr(rag_router, "search_epmc", AsyncMock(return_value=[]))
    assert await rag_router._resolve_paper_by_doi("10.1186/missing") is None


async def test_resolve_paper_by_doi_rejects_malformed_doi_without_querying(monkeypatch):
    # A client value that doesn't even look like a DOI must never reach the
    # Europe PMC query string unescaped (e.g. `x" OR (OPEN_ACCESS:Y)`).
    search = AsyncMock()
    monkeypatch.setattr(rag_router, "search_epmc", search)

    assert await rag_router._resolve_paper_by_doi('x" OR (OPEN_ACCESS:Y)') is None
    search.assert_not_awaited()


async def test_resolve_paper_by_doi_rejects_mismatched_result(monkeypatch):
    # Europe PMC's search can fuzzy-match; if the record it returns carries a
    # DIFFERENT doi than requested, that is not a resolution -- the caller
    # must not import the wrong paper under the requested paper's provenance.
    mismatched = pds.parse_epmc_result({**_OA_RAW, "doi": "10.1234/some-other-paper"})
    monkeypatch.setattr(rag_router, "search_epmc", AsyncMock(return_value=[mismatched]))
    assert await rag_router._resolve_paper_by_doi("10.1186/requested-paper") is None


async def test_resolve_paper_by_doi_rejects_mismatched_result_case_insensitively(monkeypatch):
    # Case must not matter for the equality check either way.
    paper = pds.parse_epmc_result({**_OA_RAW, "doi": "10.1186/X"})
    monkeypatch.setattr(rag_router, "search_epmc", AsyncMock(return_value=[paper]))
    assert await rag_router._resolve_paper_by_doi("10.1186/x") is paper


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


def test_paper_filename_clamps_long_author_with_no_comma():
    # RAGDocument.name is String(255); a consortium authorString with no comma
    # means .split(",")[0] returns the WHOLE string -- must still be clamped
    # or a long-enough author line + title can overflow the column and fail
    # the commit.
    long_author = "A" * 200
    paper = SimpleNamespace(authors=long_author, year="2024", title="Short title")
    name = rag_router._paper_filename(paper)
    assert name.startswith(("A" * 80) + " 2024 - ")
    assert ("A" * 81) not in name


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
    # The reason shown to the client must be the fixed generic message --
    # NOT the raw exception text, which could carry internal detail.
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
    assert out.failed[0].reason == rag_router._GENERIC_IMPORT_ERROR
    assert "epmc exploded" not in out.failed[0].reason  # internal detail not leaked
    saved.assert_not_awaited()


async def test_import_discovered_rolls_back_when_storing_fails(monkeypatch, mock_db):
    # save_uploaded_document itself raises (as if its own db.flush() failed) --
    # `document` is never bound in the router, so there is no path to unlink;
    # the router must guard on that instead of raising AttributeError.
    paper = pds.parse_epmc_result(_OA_RAW)
    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", AsyncMock(return_value=paper))
    monkeypatch.setattr(rag_router, "fetch_paper_pdf", AsyncMock(return_value=b"%PDF-1.4"))
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
    assert out.failed[0].reason == rag_router._GENERIC_IMPORT_ERROR
    assert "disk full" not in out.failed[0].reason  # internal detail not leaked
    mock_db.rollback.assert_awaited_once()


async def test_import_discovered_deletes_orphaned_file_when_commit_fails(monkeypatch, mock_db, tmp_path):
    # save_uploaded_document RETURNS a document (the file made it to disk) but
    # the subsequent db.commit() fails -- the file must be deleted so it isn't
    # orphaned forever (data/rag_documents/ has no reaper).
    paper = pds.parse_epmc_result(_OA_RAW)
    pdf_path = tmp_path / "orphan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    doc = SimpleNamespace(id=7, doi=None, source_url=None, original_path=str(pdf_path))

    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", AsyncMock(return_value=paper))
    monkeypatch.setattr(rag_router, "fetch_paper_pdf", AsyncMock(return_value=b"%PDF-1.4"))
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())
    monkeypatch.setattr(rag_router, "save_uploaded_document", AsyncMock(return_value=(doc, True)))
    mock_db.commit = AsyncMock(side_effect=RuntimeError("constraint violation"))

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=[paper.doi]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.imported == 0
    assert out.failed[0].reason == rag_router._GENERIC_IMPORT_ERROR
    assert not pdf_path.exists(), "orphaned PDF must be cleaned up when the commit fails"
    mock_db.rollback.assert_awaited_once()


async def test_import_discovered_caps_batch_size(mock_db):
    too_many = [f"10.1038/nature{10000 + i}" for i in range(rag_router.MAX_IMPORT_BATCH + 1)]
    with pytest.raises(HTTPException) as ei:
        await rag_router.import_discovered(
            payload=rag_router.ImportRequest(dois=too_many),
            background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
            current_user=SimpleNamespace(id=1),
            db=mock_db,
        )
    assert ei.value.status_code == 400


async def test_import_discovered_dedupes_request_list(monkeypatch, mock_db):
    # The same DOI ticked/submitted twice in one request must only be fetched
    # (and counted against the rate limit) once.
    paper = pds.parse_epmc_result(_OA_RAW)
    doc = SimpleNamespace(id=7, doi=None, source_url=None)
    resolve = AsyncMock(return_value=paper)
    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", resolve)
    monkeypatch.setattr(rag_router, "fetch_paper_pdf", AsyncMock(return_value=b"%PDF-1.4"))
    monkeypatch.setattr(rag_router, "save_uploaded_document", AsyncMock(return_value=(doc, True)))
    rate_limit = AsyncMock()
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", rate_limit)

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=[paper.doi, paper.doi, f"  {paper.doi}  "]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.imported == 1
    assert resolve.await_count == 1
    rate_limit.assert_awaited_once_with(1, count=1)  # budget charged once, not 3x


async def test_import_discovered_skips_papers_already_in_library(monkeypatch, mock_db):
    # A DOI already present in the caller's readable library must be reported
    # as skipped WITHOUT ever being fetched -- avoids duplicate documents from
    # a re-click, a retried 504, or two lab members importing the same paper.
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())
    monkeypatch.setattr(rag_router, "get_user_group_id", AsyncMock(return_value=None))
    # DB reports the DOI already in the library, in a different case.
    mock_db.execute.return_value = make_result(scalars_all=["10.1186/S43897-026-00231-0"])
    resolve = AsyncMock()
    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", resolve)

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=["10.1186/s43897-026-00231-0"]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.imported == 0
    # Duplicates are no longer reported as failures: "0 imported - 3 failed"
    # was the summary for the commonest case there is, re-selecting papers
    # you already imported.
    assert out.failed == []
    # Reported in the caller's original casing, not the lowercased match key.
    assert out.already_in_library == ["10.1186/s43897-026-00231-0"]
    resolve.assert_not_awaited()


async def test_import_discovered_fetches_multiple_papers_concurrently(monkeypatch, mock_db):
    # One paper failing must not prevent the others in the same batch from
    # being fetched and imported -- the fetch phase runs all of them via
    # asyncio.gather, not a loop that could short-circuit.
    ok_paper = pds.parse_epmc_result({**_OA_RAW, "doi": "10.1038/ok-paper", "id": "ok"})
    docs = []

    async def fake_resolve(doi):
        if doi == "10.1038/bad-paper":
            return None
        return ok_paper

    async def fake_save(**kwargs):
        doc = SimpleNamespace(id=len(docs) + 1, doi=None, source_url=None)
        docs.append(doc)
        return doc, True

    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", fake_resolve)
    monkeypatch.setattr(rag_router, "fetch_paper_pdf", AsyncMock(return_value=b"%PDF-1.4"))
    monkeypatch.setattr(rag_router, "save_uploaded_document", fake_save)
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())

    out = await rag_router.import_discovered(
        payload=rag_router.ImportRequest(dois=["10.1038/ok-paper", "10.1038/bad-paper"]),
        background_tasks=SimpleNamespace(add_task=lambda *a, **k: None),
        current_user=SimpleNamespace(id=1),
        db=mock_db,
    )

    assert out.imported == 1
    assert len(out.failed) == 1
    assert out.failed[0].doi == "10.1038/bad-paper"
    assert out.failed[0].reason == "Not found in Europe PMC"


# ============================================================================ #
# PR #37 review gaps: the LLM query-rewrite step.
#
# Unless a test is explicitly unit-testing the sanitizer, it drives the REAL
# rewrite_topic_query through discover() with a stubbed google.genai module,
# so the cost invariant ("exactly one Gemini call per free-text search, ZERO
# for anything already structured" -- CLAUDE.md, hard requirement) is checked
# against the actual wiring rather than against a stand-in for it.
# ============================================================================ #

def _rewrite_settings():
    """Settings that make rewrite_topic_query take its happy path."""
    return SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.6-flash")


def _one_paper(doi="10.1/x", ext_id="x"):
    """A single non-empty search result.

    Non-empty matters everywhere the rewrite is expected to *succeed*: an empty
    result set triggers discover()'s raw-text retry, which adds a second
    search_epmc call and flips rewrite_failed -- the opposite of what those
    tests are asserting. The retry has its own dedicated tests below.
    """
    return [pds.parse_epmc_result({**_OA_RAW, "doi": doi, "id": ext_id})]


# --------------------------------------------------------------------------- #
# (1) _sanitize_rewritten_query must pick the QUERY line, not the prose around
#     it. Taking line 1 blindly was a real production bug: the model is told to
#     reply with only the query and routinely prepends a preamble anyway, so
#     the preamble silently became the Europe PMC query.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw, expected", [
    # THE regression. Before the fix this returned "Here is the translated
    # query:" and the real query was thrown away.
    ('Here is the translated query:\nAUTH:"Janke C" AND microtubule',
     'AUTH:"Janke C" AND microtubule'),
    # Same shape, but the real query is bare keywords -- no field prefix and no
    # boolean operator, so ONLY the preamble's sentence-final "!" distinguishes
    # them. A colon-only preamble rule lets "Sure!" win here.
    ('Sure!\nmicrotubule bundling', 'microtubule bundling'),
    # Degenerate single-line fence: there is no separate opening/closing line
    # to drop, so the generic fence handling would leave the backticks behind.
    ('```AUTH:"X"```', 'AUTH:"X"'),
    # Multi-line fence WITH a language tag on the opening line.
    ('```text\nAUTH:"Janke C" AND microtubule\n```',
     'AUTH:"Janke C" AND microtubule'),
    # Postamble instead of preamble.
    ('AUTH:"Janke C" AND microtubule\nThis finds every Janke paper on microtubules.',
     'AUTH:"Janke C" AND microtubule'),
    # Both at once.
    ('Sure, here you go:\nAUTH:"Janke C"\nHope that helps.', 'AUTH:"Janke C"'),
    # Nothing looks like a query at all -> the documented last resort is the
    # LAST non-empty line (a preamble is far more common than a postamble).
    ('I am sorry.\nI could not translate that request.',
     'I could not translate that request.'),
    # The model narrates the syntax it used, so the PROSE line contains the
    # very signals ("AND", "OR", "AUTH:") that mark a line as query syntax.
    # These passed only once the sentence-final punctuation check was moved
    # BEFORE the prefix/boolean checks instead of after them -- previously the
    # strong signal short-circuited and the guard never ran.
    ('Note: I used AND to combine the terms.\nAUTH:"Janke C" AND microtubule',
     'AUTH:"Janke C" AND microtubule'),
    ('I used AUTH: to restrict this to the author.\nAUTH:"Janke C"',
     'AUTH:"Janke C"'),
    # Sharpest of the three: the preamble ends in the colon the rule was
    # written for, but `\bOR\b` matched first.
    ('Here is the query, combining the author OR the topic:\nAUTH:"Janke C"',
     'AUTH:"Janke C"'),
    # A bare keyword line must NOT beat a field-syntax line just by coming
    # first -- both are non-prose, so only the two-tier preference separates
    # them.
    ('microtubule bundling\nAUTH:"Janke C"', 'AUTH:"Janke C"'),
])
def test_sanitize_rewritten_query_picks_the_query_line_not_the_prose(raw, expected):
    assert pds._sanitize_rewritten_query(raw) == expected


# --------------------------------------------------------------------------- #
# (2) Cost: exactly ONE Gemini call for a free-text topic, ZERO otherwise.
# --------------------------------------------------------------------------- #

async def test_discover_topic_branch_makes_exactly_one_gemini_call(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return _one_paper()

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    ctx, generate_content = _patch_query_rewrite_genai(
        _rewrite_genai_response('AUTH:"Janke C" AND microtubule')
    )
    with patch.object(pds, "settings", _rewrite_settings()), ctx:
        out = await pds.discover("papers from the lab of dr. carsten janke")

    # ONE call -- not zero (the rewrite must actually happen) and not one per
    # retry/sub-query.
    generate_content.assert_awaited_once()
    assert calls == ['AUTH:"Janke C" AND microtubule']
    assert out.effective_query == 'AUTH:"Janke C" AND microtubule'


async def test_discover_doi_branch_makes_zero_gemini_calls(monkeypatch):
    # Unlike the existing doi/titles tests, this one leaves the REAL
    # rewrite_topic_query in place and asserts against the SDK stub, so it
    # would catch discover() calling Gemini through some other route.
    monkeypatch.setattr(pds, "search_epmc", AsyncMock(return_value=[]))
    ctx, generate_content = _patch_query_rewrite_genai(_rewrite_genai_response('AUTH:"X"'))
    with patch.object(pds, "settings", _rewrite_settings()), ctx:
        await pds.discover("10.1038/nature12373\n10.1016/j.cell.2020.01.001")
    generate_content.assert_not_awaited()


async def test_discover_titles_branch_makes_zero_gemini_calls(monkeypatch):
    monkeypatch.setattr(pds, "search_epmc", AsyncMock(return_value=[]))
    ctx, generate_content = _patch_query_rewrite_genai(_rewrite_genai_response('AUTH:"X"'))
    with patch.object(pds, "settings", _rewrite_settings()), ctx:
        await pds.discover("Tau regulates microtubules\nEg5 drives bundling")
    generate_content.assert_not_awaited()


async def test_discover_skips_gemini_when_the_user_already_typed_field_syntax(monkeypatch):
    # A power user who types `AUTH:"Janke C"` by hand gets nothing from an LLM
    # translation of it -- the call must be skipped entirely (cost), and the
    # text searched verbatim.
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    ctx, generate_content = _patch_query_rewrite_genai(
        _rewrite_genai_response('AUTH:"Someone Else"')  # must never be used
    )
    with patch.object(pds, "settings", _rewrite_settings()), ctx:
        out = await pds.discover('AUTH:"Janke C" AND microtubule')

    generate_content.assert_not_awaited()
    assert calls == ['AUTH:"Janke C" AND microtubule']
    assert out.effective_query is None
    # No rewrite was attempted, so it cannot have "failed" -- the UI must not
    # warn about a translation that never ran.
    assert out.rewrite_failed is False


@pytest.mark.parametrize("text, is_syntax", [
    ('AUTH:"Janke C"', True),
    ('TITLE:"microtubule bundling" AND tubulin', True),
    ('SRC:MED', True),                       # no quotes, still real syntax
    # Prose that merely MENTIONS a field is not the user using it. The space
    # after the colon is the tell -- real syntax has none. Skipping the
    # rewrite here would send "papers with DOI: ... plus anything on tubulin"
    # to Europe PMC verbatim, i.e. exactly the keyword-soup search the rewrite
    # exists to prevent.
    ('papers with DOI: 10.1234/x, plus anything on tubulin', False),
    ('what did the janke lab publish on AUTH: authorship?', False),
    ('microtubule papers from the lab of dr. carsten janke', False),
])
def test_is_already_epmc_syntax_needs_a_field_actually_in_use(text, is_syntax):
    assert pds._is_already_epmc_syntax(text) is is_syntax


# --------------------------------------------------------------------------- #
# (3) The length cap must survive all the way to Europe PMC, not just exist in
#     the sanitizer.
# --------------------------------------------------------------------------- #

async def test_discover_truncates_an_overlong_rewrite_before_searching(monkeypatch):
    captured = []

    async def fake_search(q, limit=25):
        captured.append(q)
        return _one_paper()

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    ctx, _ = _patch_query_rewrite_genai(_rewrite_genai_response("A" * 900))
    with patch.object(pds, "settings", _rewrite_settings()), ctx:
        out = await pds.discover("microtubule bundling papers")

    assert len(captured) == 1
    # Hard literal, deliberately NOT _MAX_REWRITTEN_QUERY_LEN: comparing the
    # result against the same constant the code truncates with would still pass
    # if the cap were quietly raised to 900.
    assert len(captured[0]) == 500
    assert captured[0] == "A" * 500
    assert out.effective_query == "A" * 500


# --------------------------------------------------------------------------- #
# (4) thinking_level="low" is really what gets requested.
# --------------------------------------------------------------------------- #

async def test_rewrite_topic_query_requests_low_thinking_level():
    ctx, generate_content = _patch_query_rewrite_genai(
        _rewrite_genai_response('AUTH:"Janke C"')
    )
    with patch.object(pds, "settings", _rewrite_settings()), ctx:
        await pds.rewrite_topic_query("microtubule papers by Janke")
        type_mod = sys.modules["google.genai.types"]
        config_kwargs = type_mod.GenerateContentConfig.call_args.kwargs
        thinking_kwargs = type_mod.ThinkingConfig.call_args.kwargs

    # Gemini 3.x: thinking_level replaces temperature/top_p/top_k. "low" keeps
    # this one-shot translation cheap and fast.
    assert thinking_kwargs["thinking_level"] == "low"
    # ...and that ThinkingConfig is the one actually wired into the request,
    # rather than built and dropped on the floor.
    assert config_kwargs["thinking_config"] is type_mod.ThinkingConfig.return_value
    assert (generate_content.call_args.kwargs["config"]
            is type_mod.GenerateContentConfig.return_value)


# --------------------------------------------------------------------------- #
# (5) Timeout ordering: the rewrite runs BEFORE the Europe PMC call, so it must
#     not be able to dominate the worst-case wait the user sits through.
# --------------------------------------------------------------------------- #

def test_query_rewrite_timeout_stays_well_under_the_epmc_timeout():
    assert pds._QUERY_REWRITE_TIMEOUT < pds.EPMC_TIMEOUT


# --------------------------------------------------------------------------- #
# (6) A no-op rewrite is not worth showing the user.
# --------------------------------------------------------------------------- #

async def test_discover_no_op_rewrite_reports_no_effective_query(monkeypatch):
    topic = "microtubule bundling in vitro"
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return _one_paper()

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    ctx, generate_content = _patch_query_rewrite_genai(_rewrite_genai_response(topic))
    with patch.object(pds, "settings", _rewrite_settings()), ctx:
        out = await pds.discover(topic)

    generate_content.assert_awaited_once()
    assert calls == [topic]        # the search still runs
    assert out.papers              # ...and returns what it found
    # Echoing the user's own words back as "Searched as: ..." is noise.
    assert out.effective_query is None
    # The rewrite worked; it just didn't change anything. Not a failure.
    assert out.rewrite_failed is False


# --------------------------------------------------------------------------- #
# (7) Empty-result fallback. Europe PMC answers a syntactically valid but
#     semantically wrong query with HTTP 200 + zero results, so a bad rewrite
#     is otherwise indistinguishable from "nothing to find".
# --------------------------------------------------------------------------- #

async def test_discover_retries_with_raw_text_when_the_rewrite_finds_nothing(monkeypatch):
    raw = "papers from the lab of dr. carsten janke"
    rewritten = 'AUTH:"Janke X" AND microtubule'   # plausible, but wrong
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return [] if q == rewritten else _one_paper(doi="10.1/fallback", ext_id="fb")

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value=rewritten))

    out = await pds.discover(raw)

    # Exactly one extra HTTP call, with the user's EXACT wording, and no extra
    # Gemini call.
    assert calls == [rewritten, raw]
    assert [p.doi for p in out.papers] == ["10.1/fallback"]
    assert out.failed_queries == 0
    # The rewrite is not what produced these results -- say so, and stop
    # claiming the discarded rewrite is what ran.
    assert out.rewrite_failed is True
    assert out.effective_query is None


async def test_discover_survives_a_fallback_search_that_itself_fails(monkeypatch):
    raw = "papers from the lab of dr. carsten janke"
    rewritten = 'AUTH:"Janke X"'
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        if q == raw:
            raise RuntimeError("Europe PMC down")
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value=rewritten))

    out = await pds.discover(raw)   # must not raise: the retry is best-effort

    assert calls == [rewritten, raw]
    assert out.papers == []
    assert out.rewrite_failed is True
    assert out.effective_query is None


async def test_discover_does_not_retry_when_the_rewritten_query_found_papers(monkeypatch):
    search = AsyncMock(return_value=_one_paper())
    monkeypatch.setattr(pds, "search_epmc", search)
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value='AUTH:"Janke C"'))

    out = await pds.discover("papers from the lab of dr. carsten janke")

    # The retry costs a real HTTP round-trip -- it must fire ONLY on the
    # zero-results path.
    assert search.await_count == 1
    assert out.rewrite_failed is False
    assert out.effective_query == 'AUTH:"Janke C"'


# --------------------------------------------------------------------------- #
# (8) rewrite_failed: true only when a rewrite was attempted and didn't deliver.
# --------------------------------------------------------------------------- #

async def test_discover_flags_rewrite_failed_when_the_rewrite_produces_nothing(monkeypatch):
    calls = []

    async def fake_search(q, limit=25):
        calls.append(q)
        return _one_paper()

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value=None))

    out = await pds.discover("MAP bundling in vitro")

    assert calls == ["MAP bundling in vitro"]   # searched unrewritten
    assert out.rewrite_failed is True
    assert out.effective_query is None


@pytest.mark.parametrize("query", [
    "10.1038/nature12373\n10.1016/j.cell.2020.01.001",   # doi branch
    "Tau regulates microtubules\nEg5 drives bundling",   # titles branch
])
async def test_discover_never_flags_rewrite_failed_for_structured_queries(monkeypatch, query):
    monkeypatch.setattr(pds, "search_epmc", AsyncMock(return_value=[]))
    rewrite = AsyncMock(return_value='AUTH:"never used"')
    monkeypatch.setattr(pds, "rewrite_topic_query", rewrite)

    out = await pds.discover(query)

    rewrite.assert_not_awaited()
    # No rewrite was ever attempted here, so it cannot have failed -- warning
    # the user about a translation that never ran would be a lie.
    assert out.rewrite_failed is False
    assert out.effective_query is None


# --------------------------------------------------------------------------- #
# (9) DiscoveryError.attempted_query: "Europe PMC is down" (retry) vs "the
#     rewrite mistranslated your query" (rephrase).
# --------------------------------------------------------------------------- #

async def _always_failing_search(q, limit=25):
    raise RuntimeError("Europe PMC down")


async def test_discovery_error_carries_the_single_attempted_query(monkeypatch):
    rewritten = 'AUTH:"Janke C" AND microtubule'
    monkeypatch.setattr(pds, "search_epmc", _always_failing_search)
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value=rewritten))

    with pytest.raises(pds.DiscoveryError) as ei:
        await pds.discover("papers from the lab of dr. carsten janke")

    # The REWRITTEN query, not the user's text: that's what Europe PMC saw, so
    # that's what the user needs to see to judge whether to rephrase or retry.
    assert ei.value.attempted_query == rewritten


async def test_discovery_error_carries_the_raw_text_when_no_rewrite_happened(monkeypatch):
    monkeypatch.setattr(pds, "search_epmc", _always_failing_search)
    monkeypatch.setattr(pds, "rewrite_topic_query", AsyncMock(return_value=None))

    with pytest.raises(pds.DiscoveryError) as ei:
        await pds.discover("MAP bundling in vitro")

    assert ei.value.attempted_query == "MAP bundling in vitro"


async def test_discovery_error_has_no_attempted_query_for_a_multi_query_batch(monkeypatch):
    monkeypatch.setattr(pds, "search_epmc", _always_failing_search)

    with pytest.raises(pds.DiscoveryError) as ei:
        await pds.discover("10.1038/nature12373\n10.1016/j.cell.2020.01.001")

    # Several sub-queries were in flight at once; singling one out would be
    # arbitrary and would mislead the user about what actually failed.
    assert ei.value.attempted_query is None


# =========================================================================== #
# PDF fetch fallback chain.
#
# Europe PMC's own PDF link can be dead while the paper is freely downloadable
# elsewhere. Live-verified 2026-07-22 on 10.21203/rs.3.rs-9043146/v1: Europe PMC
# lists exactly one qualifying pdf entry, and it answers HTTP 403 with
# {"error":"PDF link has expired or is invalid"} -- while Research Square serves
# 34 MB of real PDF for the same DOI.
# =========================================================================== #

def _candidate_paper(pdf_urls, doi="10.1/x"):
    return pds.PaperResult(
        doi=doi, title="T", authors="A", journal="J", year="2026",
        abstract=None, pmid=None, pmcid=None,
        pdf_urls=list(pdf_urls), source_url="https://example.org/abs",
    )


async def test_second_candidate_is_tried_when_the_first_fails(monkeypatch):
    tried = []

    async def fake_fetch(url):
        tried.append(url)
        if url.endswith("dead.pdf"):
            raise pds.PdfFetchError("Publisher returned HTTP 403")
        return b"%PDF-real"

    monkeypatch.setattr(pds, "fetch_pdf", fake_fetch)
    paper = _candidate_paper(["https://a.example/dead.pdf", "https://b.example/live.pdf"])

    assert await pds.fetch_paper_pdf(paper) == b"%PDF-real"
    assert tried == ["https://a.example/dead.pdf", "https://b.example/live.pdf"]


async def test_resolvers_are_not_consulted_when_europe_pmc_works(monkeypatch):
    # Cost invariant: the fallback must add ZERO requests to the common path.
    unpaywall = AsyncMock(return_value=[])
    preprint = MagicMock(return_value=[])
    monkeypatch.setattr(pds, "fetch_pdf", AsyncMock(return_value=b"%PDF-x"))
    monkeypatch.setattr(pds, "unpaywall_pdf_urls", unpaywall)
    monkeypatch.setattr(pds, "preprint_pdf_urls", preprint)

    await pds.fetch_paper_pdf(_candidate_paper(["https://a.example/live.pdf"]))

    unpaywall.assert_not_awaited()
    preprint.assert_not_called()


async def test_falls_through_to_the_preprint_host(monkeypatch):
    # The reported failure, in miniature: the only Europe PMC candidate 403s,
    # Unpaywall knows no PDF, and the preprint host has the file.
    async def fake_fetch(url):
        if "researchsquare" in url:
            return b"%PDF-from-preprint-host"
        raise pds.PdfFetchError("Publisher returned HTTP 403")

    monkeypatch.setattr(pds, "fetch_pdf", fake_fetch)
    monkeypatch.setattr(pds, "unpaywall_pdf_urls", AsyncMock(return_value=[]))
    paper = _candidate_paper(["https://europepmc.org/api/fulltextRepo?x=1"],
                             doi="10.21203/rs.3.rs-9043146/v1")

    assert await pds.fetch_paper_pdf(paper) == b"%PDF-from-preprint-host"


async def test_reports_the_last_real_error_when_everything_fails(monkeypatch):
    # "3 candidates failed" is useless: 403 vs wrong content-type vs too large
    # is what tells the user whether to retry or fetch the PDF by hand.
    monkeypatch.setattr(pds, "fetch_pdf",
                        AsyncMock(side_effect=pds.PdfFetchError("File exceeds 100 MB limit")))
    monkeypatch.setattr(pds, "unpaywall_pdf_urls", AsyncMock(return_value=[]))
    monkeypatch.setattr(pds, "preprint_pdf_urls", MagicMock(return_value=[]))

    with pytest.raises(pds.PdfFetchError, match="exceeds 100 MB"):
        await pds.fetch_paper_pdf(_candidate_paper(["https://a.example/x.pdf"]))


async def test_no_candidates_at_all_still_raises_a_usable_message(monkeypatch):
    monkeypatch.setattr(pds, "unpaywall_pdf_urls", AsyncMock(return_value=[]))
    monkeypatch.setattr(pds, "preprint_pdf_urls", MagicMock(return_value=[]))

    with pytest.raises(pds.PdfFetchError, match="No freely downloadable PDF"):
        await pds.fetch_paper_pdf(_candidate_paper([]))


# --------------------------------------------------------------------------- #
# Unpaywall resolver
# --------------------------------------------------------------------------- #

def _unpaywall_client(response):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response)
    return MagicMock(return_value=client)


async def test_unpaywall_returns_pdf_locations_and_skips_nulls(monkeypatch):
    payload = {"oa_locations": [
        {"url_for_pdf": None},                          # landing page only
        {"url_for_pdf": "https://repo.example/a.pdf"},
        {},                                             # malformed entry
    ]}
    response = MagicMock(status_code=200, json=MagicMock(return_value=payload))
    monkeypatch.setattr(pds.httpx, "AsyncClient", _unpaywall_client(response))

    assert await pds.unpaywall_pdf_urls("10.1/x") == ["https://repo.example/a.pdf"]


async def test_unpaywall_passes_the_configured_contact_email(monkeypatch):
    # Unpaywall requires it; omitting it gets the caller blocked.
    response = MagicMock(status_code=200, json=MagicMock(return_value={"oa_locations": []}))
    factory = _unpaywall_client(response)
    monkeypatch.setattr(pds.httpx, "AsyncClient", factory)

    await pds.unpaywall_pdf_urls("10.1/x")

    get = factory.return_value.get
    assert get.await_args.kwargs["params"]["email"] == pds.settings.unpaywall_email


async def test_unpaywall_returns_empty_on_http_error(monkeypatch):
    response = MagicMock(status_code=404, json=MagicMock(return_value={}))
    monkeypatch.setattr(pds.httpx, "AsyncClient", _unpaywall_client(response))
    assert await pds.unpaywall_pdf_urls("10.1/x") == []


async def test_unpaywall_never_raises(monkeypatch):
    # It runs only after Europe PMC already failed -- degrading to "no more
    # candidates" must always beat turning a fetch problem into an error.
    monkeypatch.setattr(pds.httpx, "AsyncClient", MagicMock(side_effect=RuntimeError("boom")))
    assert await pds.unpaywall_pdf_urls("10.1/x") == []


async def test_unpaywall_skips_the_call_without_a_doi(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(pds.httpx, "AsyncClient", factory)
    assert await pds.unpaywall_pdf_urls(None) == []
    factory.assert_not_called()


# --------------------------------------------------------------------------- #
# Preprint-host URL derivation (from the DOI itself -- no extra request)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("doi, expected", [
    # THE reported failure. Verified live: this URL serves 34 MB of %PDF-.
    ("10.21203/rs.3.rs-9043146/v1",
     ["https://www.researchsquare.com/article/rs-9043146/v1.pdf"]),
    ("10.21203/rs.2.rs-1234/v3",
     ["https://www.researchsquare.com/article/rs-1234/v3.pdf"]),
    # bioRxiv and medRxiv share the 10.1101 prefix and the DOI does not say
    # which, so both are offered; the wrong one costs one 404.
    ("10.1101/2020.01.01.123456", [
        "https://www.biorxiv.org/content/10.1101/2020.01.01.123456v1.full.pdf",
        "https://www.medrxiv.org/content/10.1101/2020.01.01.123456v1.full.pdf"]),
    ("10.1038/nature12373", []),      # a journal, not a preprint host
    ("10.21203/not-the-right-shape", []),
    ("", []),
    (None, []),
])
def test_preprint_pdf_urls(doi, expected):
    assert pds.preprint_pdf_urls(doi) == expected


def test_preprint_pdf_urls_tolerates_surrounding_whitespace():
    assert pds.preprint_pdf_urls("  10.21203/rs.3.rs-9043146/v1  ") == [
        "https://www.researchsquare.com/article/rs-9043146/v1.pdf"]


# --------------------------------------------------------------------------- #
# Review follow-ups: the fallback chain must survive the failures it exists for.
# --------------------------------------------------------------------------- #

async def test_transport_errors_do_not_abort_the_candidate_chain(monkeypatch):
    """A connect error or read timeout is the COMMONEST shape of a dead link.

    fetch_pdf originally let httpx exceptions escape, so the first unreachable
    host aborted fetch_paper_pdf entirely -- skipping Unpaywall and the preprint
    host, i.e. exactly the rescue a dead link is supposed to trigger.
    """
    async def fake_fetch(url):
        if "dead" in url:
            raise httpx.ConnectError("connection refused")
        return b"%PDF-rescued"

    monkeypatch.setattr(pds, "fetch_pdf", fake_fetch)
    monkeypatch.setattr(pds, "unpaywall_pdf_urls", AsyncMock(return_value=[]))
    paper = _candidate_paper(["https://dead.example/x.pdf"],
                             doi="10.21203/rs.3.rs-9043146/v1")

    assert await pds.fetch_paper_pdf(paper) == b"%PDF-rescued"


async def test_fetch_pdf_converts_transport_errors_into_pdf_fetch_error(monkeypatch):
    class _Boom:
        def __call__(self, *a, **k):
            raise httpx.ReadTimeout("too slow")

    monkeypatch.setattr(pds.httpx, "AsyncClient", _Boom())
    monkeypatch.setattr(pds, "_is_safe_url", lambda u: (True, ""))

    with pytest.raises(pds.PdfFetchError, match="Could not reach the publisher"):
        await pds.fetch_pdf("https://example.org/x.pdf")


async def test_first_error_is_reported_not_the_speculative_last_one(monkeypatch):
    """preprint_pdf_urls guesses; its 404 must not bury the real diagnosis.

    For any 10.1101 DOI it emits both biorxiv and medrxiv knowing one 404s. If
    the LAST error won, every failed bioRxiv import would report "HTTP 404" from
    a URL the user never asked for, hiding "over 100 MB" or "not a PDF".
    """
    async def fake_fetch(url):
        if "europepmc" in url:
            raise pds.PdfFetchError("PDF is too large (over 100 MB)")
        raise pds.PdfFetchError("Publisher returned HTTP 404")

    monkeypatch.setattr(pds, "fetch_pdf", fake_fetch)
    monkeypatch.setattr(pds, "unpaywall_pdf_urls", AsyncMock(return_value=[]))
    paper = _candidate_paper(["https://europepmc.org/x.pdf"],
                             doi="10.1101/2020.01.01.123456")

    with pytest.raises(pds.PdfFetchError, match="over 100 MB"):
        await pds.fetch_paper_pdf(paper)


async def test_unpaywall_survives_a_payload_whose_locations_are_not_objects(monkeypatch):
    """Its docstring promises it never raises -- the comprehension was outside
    the try, so a list of bare strings escaped and killed the preprint fallback
    AND discarded the real Europe PMC error."""
    # MIXED on purpose: a garbage entry must not cost us the VALID one sitting
    # next to it. Asserting only "returns []" for an all-garbage payload passes
    # even without the per-entry guard, because the surrounding try already
    # swallows the AttributeError -- and silently loses every real URL with it.
    payload = {"oa_locations": [
        "https://not-an-object.example/a.pdf",          # a bare string
        None,
        7,
        {"url_for_pdf": "https://repo.example/real.pdf"},
    ]}
    response = MagicMock(status_code=200, json=MagicMock(return_value=payload))
    monkeypatch.setattr(pds.httpx, "AsyncClient", _unpaywall_client(response))

    assert await pds.unpaywall_pdf_urls("10.1/x") == ["https://repo.example/real.pdf"]


async def test_paper_result_has_no_singular_pdf_url_accessor():
    # It existed briefly and was a trap: `fetch_pdf(paper.pdf_url)` -- the
    # pre-fallback spelling -- reads naturally and silently skips the whole
    # candidate chain.
    assert not hasattr(_candidate_paper(["https://a.example/x.pdf"]), "pdf_url")


async def test_discover_marks_a_paper_without_pdf_candidates_as_not_importable(monkeypatch):
    """`importable` is the field the picker's checkbox reads.

    Getting it wrong walks the user into a guaranteed failure: import_discovered
    re-verifies `pdf_urls` server-side and refuses, so a paper advertised as
    importable but carrying no candidates can only ever fail.
    """
    paywalled = pds.parse_epmc_result(_PAYWALLED_RAW)
    open_access = pds.parse_epmc_result(_OA_RAW)
    assert paywalled.pdf_urls == [] and open_access.pdf_urls

    monkeypatch.setattr(rag_router, "discover_papers", AsyncMock(
        return_value=pds.DiscoveryResult(
            papers=[paywalled, open_access], failed_queries=0, dropped_queries=0)))
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())
    monkeypatch.setattr(rag_router, "get_user_group_id", AsyncMock(return_value=None))

    db = AsyncMock()
    db.execute.return_value = make_result(scalars_all=[])
    out = await rag_router.discover_sources(
        payload=rag_router.DiscoverRequest(query="tubulin"),
        current_user=SimpleNamespace(id=1), db=db)

    by_doi = {r.doi: r.importable for r in out.results}
    assert by_doi[paywalled.doi] is False
    assert by_doi[open_access.doi] is True
