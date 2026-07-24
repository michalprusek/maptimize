"""Wire the config-driven registry to a low-level MCP ``Server``.

The list/call handlers are intentionally thin — every decision lives in the
registry so the transport code never needs to change when tools do.
"""
from __future__ import annotations

import json
from typing import Iterable

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import AnyUrl

from .registry import HandlerResult, ToolRegistry

# Resource URI scheme for the browsable document catalog: maptalk://document/{id}
_DOCUMENT_URI_PREFIX = "maptalk://document/"

# User-triggered prompt templates (slash-commands in the client). They return
# message templates only — no server-side LLM — steering the agent to use the
# tools. Kept small and static.
_PROMPTS: list[types.Prompt] = [
    types.Prompt(
        name="summarize_document",
        description="Summarize one library document (main question, methods, results, conclusions).",
        arguments=[types.PromptArgument(
            name="document_id", description="Id of the document to summarize.", required=True)],
    ),
    types.Prompt(
        name="compare_documents",
        description="Compare two or more library documents side by side.",
        arguments=[types.PromptArgument(
            name="document_ids", description="Comma-separated document ids.", required=True)],
    ),
    types.Prompt(
        name="literature_search",
        description="Search the library for a topic and synthesize what the documents say.",
        arguments=[types.PromptArgument(
            name="topic", description="The topic or question to research.", required=True)],
    ),
]


def _document_id_from_uri(uri: AnyUrl) -> int | None:
    """Parse the document id out of a maptalk://document/{id} resource URI."""
    text = str(uri)
    if not text.startswith(_DOCUMENT_URI_PREFIX):
        return None
    try:
        return int(text[len(_DOCUMENT_URI_PREFIX):].strip("/"))
    except ValueError:
        return None


def _prompt_message(text: str) -> types.PromptMessage:
    return types.PromptMessage(
        role="user", content=types.TextContent(type="text", text=text)
    )


async def _list_resources_impl(server: Server, registry: ToolRegistry) -> list[types.Resource]:
    """The document catalog, scoped to the caller. FAIL CLOSED on a remote request
    with no bearer — otherwise the env service login would serve another user's
    catalog (same class of bug as the OAuth escalation closed in PR #40)."""
    is_http, token = _bearer_from_context(server)
    if is_http and not token:
        return []
    docs = await registry.client.get_json(
        "/api/rag/documents", params={"limit": 100}, token=token
    )
    return [
        types.Resource(
            uri=AnyUrl(f"{_DOCUMENT_URI_PREFIX}{d['id']}"),
            name=d["name"],
            description=(
                f"{d.get('file_type')} · {d.get('page_count')} page(s) · {d.get('status')}"
            ),
            mimeType="application/json",
        )
        for d in docs
    ]


async def _read_resource_impl(
    server: Server, registry: ToolRegistry, uri: AnyUrl
) -> list[ReadResourceContents]:
    """Read one document's metadata, scoped to the caller (same fail-closed rule)."""
    is_http, token = _bearer_from_context(server)
    if is_http and not token:
        raise ValueError("unauthorized (missing bearer token)")
    doc_id = _document_id_from_uri(uri)
    if doc_id is None:
        raise ValueError(f"Unsupported resource URI: {uri}")
    doc = await registry.client.get_json(f"/api/rag/documents/{doc_id}", token=token)
    return [ReadResourceContents(
        content=json.dumps(doc, indent=2), mime_type="application/json"
    )]


def _render_prompt(name: str, arguments: dict | None) -> str:
    """The user-facing text for a prompt template (no server-side LLM)."""
    args = arguments or {}
    if name == "summarize_document":
        return (
            f"Read document {args.get('document_id', '')} with read_document_pages and "
            "summarize it: the main question, methods, key results, and conclusions. "
            "Cite page numbers, and use read_page_region to read any illegible figure."
        )
    if name == "compare_documents":
        return (
            f"Compare documents {args.get('document_ids', '')}. Read each with "
            "read_document_pages, then contrast their aims, methods, findings, and any "
            "disagreements in a table with per-claim page citations."
        )
    if name == "literature_search":
        return (
            f"Research \"{args.get('topic', '')}\" in the library: call search_documents "
            "to find relevant pages, read the top hits, and synthesize an answer with "
            "per-claim page citations."
        )
    raise ValueError(f"Unknown prompt: {name}")

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
    async def _call_tool(name: str, arguments: dict | None) -> HandlerResult:
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

    # -- Resources: a browsable, read-only catalog of the caller's documents ----

    @server.list_resources()
    async def _list_resources() -> list[types.Resource]:
        return await _list_resources_impl(server, registry)

    @server.read_resource()
    async def _read_resource(uri: AnyUrl) -> Iterable[ReadResourceContents]:
        return await _read_resource_impl(server, registry, uri)

    # -- Prompts: user-triggered templated workflows (no server-side LLM) --------

    @server.list_prompts()
    async def _list_prompts() -> list[types.Prompt]:
        return _PROMPTS

    @server.get_prompt()
    async def _get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
        return types.GetPromptResult(
            description=f"maptalk prompt: {name}",
            messages=[_prompt_message(_render_prompt(name, arguments))],
        )

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
