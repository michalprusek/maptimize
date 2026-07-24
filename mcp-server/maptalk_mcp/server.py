"""Wire the config-driven registry to a low-level MCP ``Server``.

The list/call handlers are intentionally thin — every decision lives in the
registry so the transport code never needs to change when tools do.
"""
from __future__ import annotations

import mcp.types as types
from mcp.server.lowlevel import Server

from .registry import ContentBlock, ToolRegistry

# Server-level guidance surfaced to the client at initialize (a "system prompt"
# for the whole connector). Keep it concise — it is injected into context. Per-
# tool descriptions say "should I call THIS tool?"; this says "how do I use this
# whole system?", so the Vision-RAG framing lives here once instead of in every
# tool description.
SERVER_INSTRUCTIONS = (
    "maptalk is a per-user document database for the Dr. Janke lab (scientific "
    "PDFs, lab protocols, papers). It is a Vision-RAG system: pages are indexed "
    "and returned as rendered IMAGES you read directly — there is no OCR text, so "
    "search is semantic over page images, not keyword/full-text.\n\n"
    "Typical workflow:\n"
    "1. search_documents (semantic) — finds and, by default, RETURNS the matching "
    "page images so you can answer straight away; use find_documents to filter the "
    "library by metadata (name/DOI/type/status/pages).\n"
    "2. read_document_pages — read specific pages of a known document.\n"
    "3. read_page_region — ZOOM into a small figure/table/label that is illegible "
    "at full-page scale (the full page is downsampled).\n\n"
    "Everything is scoped to the documents you can access (your own plus your "
    "group's shared library). Write tools (index/reindex/delete/move) act only on "
    "your own documents; delete is irreversible."
)

# Bumped when the tool contract or capabilities change (see MCP versioning).
SERVER_VERSION = "2.0.0"


def build_server(registry: ToolRegistry) -> Server:
    server: Server = Server(
        "maptalk-mcp", version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS
    )

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return registry.list_tools()

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None) -> list[ContentBlock]:
        # HTTP transport: the caller's per-request bearer (their token) is on the
        # original request. stdio has no request -> token None, and the client
        # falls back to its env-based login.
        is_http, token = _bearer_from_context(server)
        if is_http and not token:
            # Remote transport with no usable bearer: FAIL CLOSED. Never fall
            # through to the client's env service login (would execute this
            # caller's tools as the wrong identity).
            return [types.TextContent(type="text", text="Error: unauthorized (missing bearer token)")]
        return await registry.dispatch(name, arguments or {}, token=token)

    return server


def _bearer_from_context(server: Server) -> tuple[bool, str | None]:
    """Return (is_http_request, bearer_token). ``is_http`` distinguishes the
    remote transport (where a bearer is mandatory) from stdio (env-based login)."""
    try:
        request = server.request_context.request
    except LookupError:
        return False, None
    if request is None:
        return False, None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return True, (auth[7:].strip() or None)
    return True, None
