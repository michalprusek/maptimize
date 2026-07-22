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
    assert pdf_url_from_result(_OA_RAW) == "https://europepmc.org/articles/PMC13248438?pdf=render"
    # Free-but-no-pdf preprint is NOT importable, despite availability "Free"
    assert pdf_url_from_result(_PREPRINT_RAW) is None
    assert pdf_url_from_result(_PAYWALLED_RAW) is None


def test_pdf_url_ignores_isopenaccess_flag():
    # isOpenAccess must not drive the decision: flip it and nothing changes.
    flipped = {**_PAYWALLED_RAW, "isOpenAccess": "Y", "hasPDF": "Y"}
    assert pdf_url_from_result(flipped) is None


def test_pdf_url_excludes_pubmedcentral_ncbi_entries():
    # documentStyle=pdf + availability=Open access alone are NOT enough --
    # a PubMedCentral-sited entry's url 404s/bot-checks when fetched
    # server-side, so it must be excluded despite otherwise qualifying.
    assert pdf_url_from_result(_PMC_NCBI_RAW) is None


def test_pdf_url_requires_europe_pmc_site_even_with_no_site_key():
    # A malformed/older record shape with no "site" key at all must not be
    # treated as importable by accident (missing != "Europe_PMC").
    no_site = {**_OA_RAW, "fullTextUrlList": {"fullTextUrl": [
        {"availability": "Open access", "documentStyle": "pdf",
         "url": "https://example.org/no-site-field.pdf"},
    ]}}
    assert pdf_url_from_result(no_site) is None


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
import sys
import types as pytypes
from unittest.mock import MagicMock, patch


def _rewrite_genai_response(text):
    """A fake google.genai response carrying `.text` -- rewrite_topic_query
    reads only that attribute (unlike the vision-extraction path elsewhere in
    the codebase, this is a plain text-in/text-out call)."""
    return SimpleNamespace(text=text)


def _patch_query_rewrite_genai(response_or_callable):
    """Inject a fake google.genai module so rewrite_topic_query's lazy import
    resolves to our stub.

    Unlike rag_service's extract_relevant_passages (which awaits
    ``client.aio.models.generate_content``), rewrite_topic_query matches
    gemini_agent_service's pattern: a plain (blocking) ``client.models.
    generate_content`` invoked through ``asyncio.to_thread`` -- so the stub's
    ``generate_content`` must be an ordinary callable, not an AsyncMock.
    """
    if callable(response_or_callable):
        generate_content = MagicMock(side_effect=response_or_callable)
    else:
        generate_content = MagicMock(return_value=response_or_callable)

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
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
    import time as real_time
    fake_settings = SimpleNamespace(gemini_api_key="key", gemini_model="gemini-3.6-flash")
    monkeypatch.setattr(pds, "_QUERY_REWRITE_TIMEOUT", 0.05)

    def slow(*a, **k):
        real_time.sleep(0.3)
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


def test_sanitize_rewritten_query_caps_length():
    huge = "A" * 900
    out = pds._sanitize_rewritten_query(huge)
    assert out == "A" * pds._MAX_REWRITTEN_QUERY_LEN


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
        return []

    monkeypatch.setattr(pds, "search_epmc", fake_search)
    monkeypatch.setattr(
        pds, "rewrite_topic_query",
        AsyncMock(return_value='AUTH:"Janke C" AND microtubule'),
    )
    out = await pds.discover("find all microtubule related papers from lab of dr. carsten janke")
    assert calls == ['AUTH:"Janke C" AND microtubule']
    assert out.effective_query == 'AUTH:"Janke C" AND microtubule'


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
    monkeypatch.setattr(rag_router, "fetch_pdf", AsyncMock(return_value=b"%PDF-1.4"))
    monkeypatch.setattr(rag_router, "_check_discovery_rate_limit", AsyncMock())
    monkeypatch.setattr(rag_router, "save_uploaded_document", AsyncMock(return_value=doc))
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
    monkeypatch.setattr(rag_router, "fetch_pdf", AsyncMock(return_value=b"%PDF-1.4"))
    monkeypatch.setattr(rag_router, "save_uploaded_document", AsyncMock(return_value=doc))
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
    assert out.failed[0].reason == "Already in your library"
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
        return doc

    monkeypatch.setattr(rag_router, "_resolve_paper_by_doi", fake_resolve)
    monkeypatch.setattr(rag_router, "fetch_pdf", AsyncMock(return_value=b"%PDF-1.4"))
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
