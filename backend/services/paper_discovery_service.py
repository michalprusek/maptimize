"""Europe PMC paper discovery + legally-downloadable PDF fetching.

Europe PMC is the single source: one REST API gives search, structured metadata,
and — for open-access records — a direct PDF URL. It indexes PubMed, PMC and
preprints, so a free preprint of an otherwise paywalled paper surfaces naturally.

Importability is decided by the fullTextUrl list, NOT by ``isOpenAccess``:
verified live 2026-07-22, bioRxiv preprints report ``isOpenAccess: "N"`` yet
``availability: "Free"``, while exposing only a DOI link and no PDF. We only ever
download an entry that explicitly advertises a PDF as Open access / Free.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
# Politeness: never open more than this many connections to Europe PMC at once,
# no matter how many papers the user selected.
EPMC_MAX_CONCURRENCY = 4
# This is a user-initiated, explicitly-awaited action (the user clicked Search
# and is watching a spinner), so it's fine to wait longer than a background
# call would. Measured live against the real API: the same query returned in
# 0.18s, 12.52s, 0.59s and 0.23s on consecutive attempts -- Europe PMC has
# multi-second latency spikes (one of four consecutive calls took >12s), and
# leaving only ~7s of headroom above that under a 20s ceiling was too tight.
EPMC_TIMEOUT = 45.0

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
    *and* served directly by Europe PMC qualifies. ``isOpenAccess`` is
    deliberately ignored (see module docstring).

    ``site`` must be ``"Europe_PMC"``: live-verified 2026-07-22, Europe PMC
    search results also carry ``site: "PubMedCentral"`` entries whose ``url``
    points at ``ncbi.nlm.nih.gov/pmc/articles/.../pdf/``. Fetched server-side
    (no browser, no cookies), that URL redirects to a 200 ``text/html`` NCBI
    bot-check page, not the PDF. ``fetch_pdf`` correctly rejects it (wrong
    content-type), but by then the picker has already told the user it was
    importable. Filtering here means /discover only ever advertises entries
    that actually download.
    """
    urls = ((raw.get("fullTextUrlList") or {}).get("fullTextUrl")) or []
    for entry in urls:
        if (entry.get("documentStyle") == "pdf"
                and entry.get("availability") in _DOWNLOADABLE
                and entry.get("site") == "Europe_PMC"
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


# Mirrors the upload endpoint's cap so a discovered paper can never be bigger
# than something a user could upload by hand.
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_REDIRECTS = 5
PDF_READ_TIMEOUT = 60.0


class PdfFetchError(Exception):
    """A PDF could not be fetched; the message is safe to show the user."""


# The one message fetch_pdf raises for an SSRF refusal. A shared constant (not
# just the same string typed twice) so routers/rag.py can recognise this
# specific case for logging without re-deriving _is_safe_url's internal
# reason, which can contain a resolved private IP and must never reach the
# client (see fetch_pdf's docstring).
SSRF_REFUSAL_MESSAGE = "Refused to fetch this URL for security reasons"


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
            # `reason` is _is_safe_url's internal diagnostic (e.g. "Access to
            # private IP address (10.0.0.5) is not allowed") -- log it, but
            # raise only the generic, safe-to-display message.
            logger.warning("Refused to fetch PDF URL %s: %s", current, reason)
            raise PdfFetchError(SSRF_REFUSAL_MESSAGE)

        async with httpx.AsyncClient(timeout=PDF_READ_TIMEOUT, follow_redirects=False) as client:
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise PdfFetchError("Redirect without a target")
                    # str(), not .human_repr(): that is yarl's API, not httpx's.
                    # Resolve relative Locations against the current URL so a
                    # hop like "/pdf/x.pdf" is re-validated as an absolute URL.
                    current = str(httpx.URL(current).join(location))
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
                body = b"".join(chunks)
                if not body.startswith(b"%PDF"):
                    # Fail fast rather than storing a file the indexer will
                    # choke on later -- a 200 + "pdf" content-type can still be
                    # an HTML error page some publishers mislabel.
                    raise PdfFetchError("Downloaded file is not a valid PDF")
                return body

    raise PdfFetchError("Too many redirects")


class DiscoveryError(Exception):
    """Search failed (as opposed to finding nothing)."""


# A pasted bibliography turns into one Europe PMC sub-query per DOI/title; cap
# how many we ever issue for one /discover call so a 300-entry paste can't fan
# out into hundreds of outbound requests. Anything beyond the cap is silently
# dropped -- the caller (routers/rag.py) surfaces the drop count to the user.
MAX_SUBQUERIES = 20

# A DOI or a single title is already a specific, near-unique match target
# (unlike a free-text topic search), so a handful of results per sub-query is
# plenty -- this keeps 10 pasted titles from coming back as 250 fuzzy matches.
SUBQUERY_RESULT_LIMIT = 3


def _escape_query_term(text: str) -> str:
    """Strip characters that could break out of a quoted Europe PMC query term.

    A client-supplied title/DOI is interpolated into e.g. ``TITLE:"{t}"``
    verbatim; a literal ``"`` in the input would close the quote early and let
    the rest of the string be parsed as new query syntax (e.g.
    ``x" OR (OPEN_ACCESS:Y)``).
    """
    return text.replace('"', "")


# Free text handed to Europe PMC verbatim is treated as plain keyword search:
# "papers from the lab of dr. carsten janke" matches nothing useful because
# Europe PMC has no notion of "lab of" meaning "author". rewrite_topic_query
# asks Gemini to translate the request into EPMC's field syntax first (see
# its docstring). Kept as a plain constant (not an f-string) so `.format()`
# is the only place `{query}` is substituted -- the request text itself may
# contain literal `{`/`}` characters.
_QUERY_REWRITE_PROMPT = """You are an expert user of the Europe PMC search API. \
Translate the request below into a single Europe PMC search query.

Europe PMC query syntax:
- `AUTH:"Lastname I"` -- author search. Surname, a space, then bare initials \
with NO periods and NO comma (e.g. `AUTH:"Janke C"`, never `AUTH:"Janke, C."` \
or `AUTH:"C. Janke"`).
- Plain keywords for a topic/subject; put exact phrases in double quotes \
(e.g. "microtubule bundling").
- `FIRST_PDATE:[2020 TO *]` -- restrict to a publication date range; use `*` \
for an open end.
- `JOURNAL:"..."`, `TITLE:"..."`, `ABSTRACT:"..."` -- restrict to a specific field.
- Boolean operators `AND`, `OR`, `NOT` to combine any of the above.

Worked example:
Request: find all microtubule related papers from lab of dr. carsten janke
Query: AUTH:"Janke C" AND microtubule

Now translate this request into ONE Europe PMC query. Reply with ONLY the \
query string itself -- no explanation, no markdown formatting, and no quotes \
around the whole answer.

Request: {query}"""

# A user-initiated search where the user is watching a spinner can tolerate a
# few extra seconds, but this happens BEFORE the Europe PMC call and must stay
# well under EPMC_TIMEOUT so a slow rewrite can't double the worst-case wait.
_QUERY_REWRITE_TIMEOUT = 20.0

# The model is told to reply with ONLY the query string, but may still wrap it
# in a markdown fence or add an explanation anyway -- cap the length so a
# rambling response can never become an absurdly long Europe PMC query.
_MAX_REWRITTEN_QUERY_LEN = 500


def _sanitize_rewritten_query(raw: Optional[str]) -> Optional[str]:
    """Clean up whatever Gemini returned, or None if nothing usable is left.

    Strips a markdown code fence if present, keeps only the first non-empty
    line (in case prose follows the query anyway), collapses internal
    whitespace/newlines, and caps the length.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]           # drop the opening ``` / ```lang line
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]                  # drop the closing ``` line
        text = "\n".join(lines).strip()
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    cleaned = " ".join(first_line.split())[:_MAX_REWRITTEN_QUERY_LEN]
    return cleaned or None


async def rewrite_topic_query(text: str) -> Optional[str]:
    """Translate a free-text topic description into Europe PMC query syntax.

    Returns None on ANY failure -- no API key configured, the SDK not
    installed, a timeout, any other exception, or an empty result after
    sanitizing -- so the caller can always fall back to searching the raw
    text as before. This function must never raise.
    """
    if not settings.gemini_api_key:
        logger.warning("Query rewrite skipped: GEMINI_API_KEY not configured")
        return None

    try:
        import google.genai as genai
        from google.genai import types
    except ImportError:
        logger.warning("Query rewrite skipped: google-genai not installed")
        return None

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=settings.gemini_model,
                contents=_QUERY_REWRITE_PROMPT.format(query=text),
                config=types.GenerateContentConfig(
                    # Gemini 3.x replaces temperature/top_p/top_k with
                    # thinking_level; "low" keeps this cheap, single-shot
                    # translation fast -- it's not worth deep reasoning.
                    thinking_config=types.ThinkingConfig(thinking_level="low"),
                ),
            ),
            timeout=_QUERY_REWRITE_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("Query rewrite failed for %r: %s", text[:80], exc)
        return None

    return _sanitize_rewritten_query(getattr(response, "text", None))


@dataclass
class DiscoveryResult:
    """Everything discover() learned, not just the papers.

    A user pasting a 300-entry bibliography or hitting a flaky Europe PMC
    outage must be able to tell that something was skipped/failed rather than
    silently seeing an incomplete list with no explanation.
    """
    papers: list[PaperResult]
    failed_queries: int    # sub-queries that raised, but didn't sink the whole call
    dropped_queries: int   # sub-queries never run at all (MAX_SUBQUERIES cap)
    # The query Europe PMC actually ran, ONLY set when rewrite_topic_query
    # changed it -- None when the raw text was used (rewrite unavailable/
    # failed, or this was a doi/titles search, which is never rewritten) so
    # the UI can show "Searched as: ..." only when it's actually informative.
    effective_query: Optional[str] = None


async def discover(query: str, limit: int = 25) -> DiscoveryResult:
    """Turn whatever the user typed into a de-duplicated candidate list.

    Raises:
        DiscoveryError: every sub-query failed (e.g. Europe PMC timed out or
            errored) -- this is a search failure, not "zero matches", and must
            never be silently reported to the caller as an empty result list.
            If only *some* sub-queries failed, the ones that succeeded are
            still useful, so partial results are returned instead of raising.
    """
    kind, items = classify_query(query)
    dropped_queries = 0
    effective_query: Optional[str] = None
    per_query_limit = limit
    if kind == "doi":
        capped = items[:MAX_SUBQUERIES]
        dropped_queries = len(items) - len(capped)
        queries = [f'DOI:"{d}"' for d in capped]
        per_query_limit = min(limit, SUBQUERY_RESULT_LIMIT)
    elif kind == "titles":
        capped = items[:MAX_SUBQUERIES]
        dropped_queries = len(items) - len(capped)
        queries = [f'TITLE:"{_escape_query_term(t)}"' for t in capped]
        per_query_limit = min(limit, SUBQUERY_RESULT_LIMIT)
    else:
        # Structured doi/titles queries are already unambiguous Europe PMC
        # syntax and cost nothing to build -- only a free-text topic search
        # needs (and pays for) an LLM rewrite, and only ONE call per /discover.
        raw_topic = items[0]
        try:
            rewritten = await rewrite_topic_query(raw_topic)
        except Exception:
            # rewrite_topic_query's contract is "never raises"; this guards
            # against a violation of that contract (e.g. a misbehaving mock)
            # so a rewrite failure can never sink the whole search.
            logger.exception("rewrite_topic_query raised despite its no-raise contract")
            rewritten = None
        rewritten = (rewritten or "").strip() or None
        if rewritten:
            effective_query = rewritten
            queries = [rewritten]
        else:
            queries = [raw_topic]

    semaphore = asyncio.Semaphore(EPMC_MAX_CONCURRENCY)
    failures: list[BaseException] = []

    async def run(q: str) -> list[PaperResult]:
        async with semaphore:
            try:
                return await search_epmc(q, limit=per_query_limit)
            except Exception as exc:
                logger.exception("Europe PMC query failed: %s", q[:80])
                failures.append(exc)
                return []

    batches = await asyncio.gather(*(run(q) for q in queries))

    if failures and len(failures) == len(queries):
        raise DiscoveryError("Europe PMC search failed") from failures[-1]

    seen: set[str] = set()
    merged: list[PaperResult] = []
    for batch in batches:
        for paper in batch:
            key = (paper.doi or paper.source_url).lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(paper)
    return DiscoveryResult(
        papers=merged, failed_queries=len(failures), dropped_queries=dropped_queries,
        effective_query=effective_query,
    )
