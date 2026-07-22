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
    """Search failed (as opposed to finding nothing).

    Carries the Europe PMC query that was actually attempted, when there was
    exactly one (a free-text topic search, whether or not an LLM rewrote it)
    -- so the caller can tell "Europe PMC is down" (retry) apart from "the
    rewrite mistranslated your query" (rephrase). None when several
    sub-queries were in flight at once (a doi/titles batch), where blaming a
    single one would be arbitrary.
    """
    def __init__(self, message: str, attempted_query: Optional[str] = None):
        super().__init__(message)
        self.attempted_query = attempted_query


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

- Do NOT invent an availability/open-access filter (e.g. `OPEN_ACCESS:Y`) \
unless the user explicitly asks to restrict to open-access papers -- \
importability is decided separately, after the search, and a filter you add \
here would silently drop valid results the user might still want to see.

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

# Field prefixes that mark a string as genuine Europe PMC query syntax, as
# opposed to natural-language prose. Shared by two heuristics below:
# recognising the real query line inside a rewrite that came with a
# preamble/postamble (_looks_like_epmc_query), and skipping the LLM rewrite
# entirely when the user already typed field syntax by hand
# (_is_already_epmc_syntax -- cost saving, see CLAUDE.md: cost is a hard
# requirement for this project).
_EPMC_FIELD_PREFIXES = ("AUTH:", "TITLE:", "ABSTRACT:", "JOURNAL:", "FIRST_PDATE:", "DOI:", "SRC:")
# A field prefix actually being *used*: at a word boundary and followed
# immediately by a non-space character, the way real syntax is written
# (`AUTH:"Janke C"`) -- as opposed to prose merely mentioning the field
# ("papers with DOI: 10.1234/x"), which must not count as query syntax.
_EPMC_FIELD_USE_RE = re.compile(
    r"\b(?:{}):(?=\S)".format("|".join(p.rstrip(":") for p in _EPMC_FIELD_PREFIXES))
)
_EPMC_BOOLEAN_RE = re.compile(r"\b(?:AND|OR|NOT)\b")


def _is_already_epmc_syntax(text: str) -> bool:
    """True if the text already contains Europe PMC field syntax.

    A power user who types e.g. `AUTH:"Janke C"` directly gets no benefit from
    an LLM translation of it -- skip the Gemini call entirely.

    The prefix must be followed immediately by a non-space character, as real
    syntax is (`AUTH:"Janke C"`). A bare `DOI: ` mid-sentence ("papers with
    DOI: 10.1234/x, plus anything on tubulin") is someone writing prose about
    a field, not using it, and still deserves a rewrite.
    """
    return bool(_EPMC_FIELD_USE_RE.search(text))


def _is_prose_line(line: str) -> bool:
    """True if the line ends the way a sentence does, not the way a query does.

    A colon is the signature of a preamble ("Here is the translated query:")
    introducing the real query on the NEXT line; `.`/`!`/`?` mark prose on
    either side of it ("Sure!", "That should find them."). A Europe PMC query
    does not end that way.

    This is checked BEFORE the field-prefix/boolean signals below, never
    after. Checking it after was a bug in its own right: the model happily
    narrates the syntax it used ("Here is the query, combining the author OR
    the topic:"), so the prose line matched `\\bOR\\b`, was accepted as query
    syntax, and the punctuation guard written to catch exactly that shape
    never ran.
    """
    return line.rstrip().endswith((":", ".", "!", "?"))


def _looks_like_epmc_syntax(line: str) -> bool:
    """Strong signal: a non-prose line carrying a field prefix or a boolean."""
    if not line or _is_prose_line(line):
        return False
    return bool(_EPMC_FIELD_USE_RE.search(line)) or bool(_EPMC_BOOLEAN_RE.search(line))


def _looks_like_epmc_query(line: str) -> bool:
    """Weak signal: any line that isn't obviously a prose sentence.

    Used only as the second-choice rule, after every line has been offered to
    _looks_like_epmc_syntax -- a bare keyword query ("microtubule bundling")
    is perfectly valid and carries no field prefix at all, so it has to be
    accepted, but never in preference to a line that does carry one.
    """
    return bool(line) and not _is_prose_line(line)


def _sanitize_rewritten_query(raw: Optional[str]) -> Optional[str]:
    """Clean up whatever Gemini returned, or None if nothing usable is left.

    Strips a markdown code fence if present (including a degenerate
    single-line fence, e.g. `` ```AUTH:"X"``` ``, which has no separate
    opening/closing line to drop), then picks the line most likely to be the
    query, in three tiers: a non-prose line carrying field syntax
    (_looks_like_epmc_syntax), else any non-prose line
    (_looks_like_epmc_query), else the last non-empty line.

    Blindly taking line 1 was a real production bug: the model is told to
    reply with ONLY the query, but routinely prepends a preamble anyway (e.g.
    'Here is the translated query:\\nAUTH:"Janke C" AND microtubule'), and the
    preamble itself silently became the Europe PMC query while the real one
    was dropped. The tiers exist because neither signal is sufficient alone: a
    valid query can be bare keywords with no field syntax at all, and a
    preamble can name the syntax it used ("...combining the author OR the
    topic:"). The last-line fallback prefers a postamble over a preamble,
    since the model prepends far more often than it appends.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        if "\n" in text:
            lines = text.split("\n")[1:]           # drop the opening ``` / ```lang line
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]                  # drop the closing ``` line
            text = "\n".join(lines).strip()
        else:
            # A single-line fence, e.g. ```AUTH:"X"``` -- there is no
            # separate opening/closing line to drop.
            text = text.strip("`").strip()

    non_empty_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not non_empty_lines:
        return None
    chosen = (
        # Best: a line that both carries query syntax and isn't a sentence.
        next((ln for ln in non_empty_lines if _looks_like_epmc_syntax(ln)), None)
        # Next best: any non-sentence line (a bare keyword query).
        or next((ln for ln in non_empty_lines if _looks_like_epmc_query(ln)), None)
        # Nothing qualifies: take the LAST line, not the first -- a preamble
        # is far more common than a postamble.
        or non_empty_lines[-1]
    )
    cleaned = " ".join(chosen.split())[:_MAX_REWRITTEN_QUERY_LEN]
    return cleaned or None


async def rewrite_topic_query(text: str) -> Optional[str]:
    """Translate a free-text topic description into Europe PMC query syntax.

    Returns None on ANY failure -- no API key configured, the SDK not
    installed, a timeout, any other exception, or an empty result after
    sanitizing -- so the caller can always fall back to searching the raw
    text as before. This function must never raise.
    """
    if not settings.gemini_api_key:
        # Static misconfiguration, not a transient per-call failure -- logged
        # at error to match the rest of the codebase (rag_service.py,
        # gemini_agent_service.py).
        logger.error("Query rewrite skipped: GEMINI_API_KEY not configured")
        return None

    try:
        import google.genai as genai
        from google.genai import types
    except Exception:
        # Broader than ImportError: a broken transitive dependency of
        # google-genai can raise something else at import time, and this
        # function's contract is "never raises" either way.
        logger.error("Query rewrite skipped: google-genai not installed")
        return None

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        # Native async SDK call (matches rag_service.py's
        # extract_relevant_passages), NOT asyncio.to_thread: wait_for only
        # cancels the awaitable, not the underlying OS thread, so on timeout a
        # to_thread call keeps blocking a slot in asyncio's shared default
        # executor. The native async call is genuinely cancellable.
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
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
        # Inside the try: unlike getattr()'s AttributeError-only default,
        # `.text` is a property that does real work on the SDK response
        # object and can raise something else that must be caught here too.
        raw_text = getattr(response, "text", None)
    except asyncio.TimeoutError:
        logger.warning(
            "Query rewrite timed out after %ss for %r", _QUERY_REWRITE_TIMEOUT, text[:80]
        )
        return None
    except Exception as exc:
        logger.warning(
            "Query rewrite failed for %r: %s: %s", text[:80], type(exc).__name__, exc
        )
        return None

    sanitized = _sanitize_rewritten_query(raw_text)
    if not sanitized:
        # The only failure branch that used to log nothing at all -- "why do
        # author searches come back empty" was ungreppable without this.
        logger.warning(
            "Query rewrite for %r produced no usable query after sanitizing (raw reply: %r)",
            text[:80], (raw_text or "")[:200],
        )
    return sanitized


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
    # The query Europe PMC actually ran, set ONLY when a topic-search rewrite
    # both succeeded AND changed the query (Fix #9: echoing the input back is
    # noise, not information). None for doi/titles searches (never rewritten),
    # for an already-field-syntax topic search (rewrite skipped -- see
    # rewrite_failed), and whenever the rewrite ultimately wasn't what
    # produced the results shown (see rewrite_failed's case (b) below).
    effective_query: Optional[str] = None
    # True when an LLM rewrite was attempted for a free-text topic search and
    # either (a) produced nothing usable, so the raw text was searched
    # instead unrewritten, or (b) DID produce a query, but that query came
    # back with zero results and no errors -- Europe PMC answers a
    # syntactically valid but semantically wrong query with HTTP 200 + zero
    # results, so a bad rewrite and "genuinely nothing to find" are otherwise
    # indistinguishable. In case (b), the raw text was re-searched once and
    # those results (if any) are what `papers` holds instead.
    # False for doi/titles searches and for an already-field-syntax topic
    # search: no rewrite was ever attempted there, so it cannot have "failed".
    # One flag deliberately covers both cases: from the UI's perspective they
    # mean the same thing -- "the smart translation is not what's behind what
    # you're seeing" -- so a second, more granular flag would only add a
    # distinction the UI has no separate message for.
    rewrite_failed: bool = False


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
    rewrite_failed = False
    raw_topic: Optional[str] = None
    used_llm_rewrite = False
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
        if _is_already_epmc_syntax(raw_topic):
            # Fix #10: a power user who already typed field syntax (e.g.
            # `AUTH:"Janke C"`) gets zero benefit from an LLM translation of
            # it -- skip the Gemini call entirely and search verbatim.
            queries = [raw_topic]
        else:
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
                used_llm_rewrite = True
                # Fix #9: only surface "Searched as: ..." when the rewrite
                # actually changed something -- echoing the input back is noise.
                if rewritten != raw_topic:
                    effective_query = rewritten
                queries = [rewritten]
            else:
                # Fix #11: a rewrite was attempted and produced nothing
                # usable -- distinct from "no rewrite needed" (doi/titles, or
                # already-field-syntax), which the UI has no reason to warn
                # the user about.
                rewrite_failed = True
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
        raise DiscoveryError(
            "Europe PMC search failed",
            attempted_query=queries[0] if len(queries) == 1 else None,
        ) from failures[-1]

    seen: set[str] = set()
    merged: list[PaperResult] = []
    for batch in batches:
        for paper in batch:
            key = (paper.doi or paper.source_url).lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(paper)

    # Fix #2: Europe PMC answers a syntactically valid but semantically wrong
    # query with HTTP 200 + zero results (verified live) -- it does not raise,
    # so nothing above notices a bad rewrite. If the LLM's rewrite was
    # actually used, ran clean (no failures), and still came back empty, retry
    # ONCE with the user's exact wording before reporting "no papers found".
    # Costs one extra HTTP call, and only in the already-failed case -- zero
    # extra Gemini calls.
    if used_llm_rewrite and not merged and not failures:
        try:
            fallback_papers = await search_epmc(raw_topic, limit=per_query_limit)
        except Exception:
            logger.exception("Europe PMC fallback search failed for %s", raw_topic[:80])
            fallback_papers = []
        if fallback_papers:
            merged = fallback_papers
        # Either way, the rewrite didn't deliver -- tell the UI (Fix #11) and
        # stop claiming the (discarded) rewrite is what actually ran.
        rewrite_failed = True
        effective_query = None

    return DiscoveryResult(
        papers=merged, failed_queries=len(failures), dropped_queries=dropped_queries,
        effective_query=effective_query, rewrite_failed=rewrite_failed,
    )
