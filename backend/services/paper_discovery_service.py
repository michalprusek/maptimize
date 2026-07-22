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
