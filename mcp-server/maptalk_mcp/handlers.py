"""Tool handlers.

A handler turns a resolved ``{arg: value}`` dict into a list of MCP content
blocks. Which handler a tool uses is declared by its ``handler:`` key in
``tools.yaml``, so simple REST tools need no code at all — only the two
composite handlers (page reading, web search) live here.

Handler signature: ``async def handler(reg, spec, args) -> list[ContentBlock]``
where ``reg`` is the ToolRegistry (exposes ``.client`` and ``.web_client``).
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse
from typing import TYPE_CHECKING, Any

import httpx
import mcp.types as types

if TYPE_CHECKING:  # avoid an import cycle; only needed for type hints
    from .registry import ToolRegistry, ToolSpec

ContentBlock = types.TextContent | types.ImageContent

_MAX_PAGES_PER_CALL = 10
_WEB_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _text(payload: Any) -> types.TextContent:
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    return types.TextContent(type="text", text=text)


# -- http_json: proxy a single GET to a backend REST endpoint ---------------


async def http_json(
    reg: "ToolRegistry", spec: "ToolSpec", args: dict, token: str | None = None
) -> list[ContentBlock]:
    path = spec.path or ""
    query: dict[str, Any] = {}
    for param in spec.params:
        if param.name not in args:
            continue
        value = args[param.name]
        if param.location == "path":
            path = path.replace("{" + param.name + "}", str(value))
        elif param.location == "query":
            query[param.maps_to or param.name] = value
    resp = await reg.client.request(spec.method or "GET", path, params=query, token=token)
    return _response_blocks(resp)


def _response_blocks(resp) -> list[ContentBlock]:
    if resp.status_code == 204 or not resp.content:
        return [_text({"status": "ok"})]
    return [_text(resp.json())]


async def http_post_json(
    reg: "ToolRegistry", spec: "ToolSpec", args: dict, token: str | None = None
) -> list[ContentBlock]:
    """POST with a JSON body built from ``in: body`` params (path/query still honored)."""
    path = spec.path or ""
    query: dict[str, Any] = {}
    body: dict[str, Any] = {}
    for param in spec.params:
        if param.name not in args:
            continue
        value = args[param.name]
        if param.location == "path":
            path = path.replace("{" + param.name + "}", str(value))
        elif param.location == "query":
            query[param.maps_to or param.name] = value
        elif param.location == "body":
            body[param.maps_to or param.name] = value
    resp = await reg.client.request(
        spec.method or "POST", path, params=query, json=body or None, token=token
    )
    return _response_blocks(resp)


_MAX_SEARCH_IMAGES = 10


async def _pages_as_images(reg, hits, token) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for h in hits:
        content, mime = await reg.client.get_bytes(
            f"/api/rag/documents/{h['document_id']}/pages/{h['page_number']}/image",
            auth="query",
            token=token,
        )
        blocks.append(
            types.ImageContent(
                type="image", data=base64.b64encode(content).decode("ascii"), mimeType=mime
            )
        )
    return blocks


def _hit_summary(hits) -> types.TextContent:
    return _text(
        "\n".join(
            f"{i}. {h.get('document_name')} (doc {h.get('document_id')}, "
            f"p.{h.get('page_number')}, score {h.get('similarity_score')})"
            for i, h in enumerate(hits, start=1)
        )
    )


async def semantic_image_search(
    reg: "ToolRegistry", spec: "ToolSpec", args: dict, token: str | None = None
) -> list[ContentBlock]:
    query = args["query"]
    count = min(int(args.get("count", 10)), _MAX_SEARCH_IMAGES)
    data = await reg.client.get_json(
        "/api/rag/search/documents", params={"q": query, "limit": count}, token=token
    )
    hits = (data.get("results") or [])[:count]
    if not hits:
        return [_text(f"No document pages matched: {query}")]
    return [_hit_summary(hits), *await _pages_as_images(reg, hits, token)]


async def search_by_image(
    reg: "ToolRegistry", spec: "ToolSpec", args: dict, token: str | None = None
) -> list[ContentBlock]:
    import binascii

    filename = args.get("filename", "query.png")
    count = min(int(args.get("count", 10)), _MAX_SEARCH_IMAGES)
    try:
        raw = base64.b64decode(args["image_base64"])
    except (binascii.Error, ValueError):
        return [_text("image_base64 is not valid base64.")]
    data = await reg.client.post_multipart(
        "/api/rag/search/by-image",
        files={"file": (filename, raw)},
        data={"limit": str(count)},
        token=token,
    )
    hits = (data.get("results") or [])[:count]
    if not hits:
        return [_text("No document pages matched the image.")]
    return [_hit_summary(hits), *await _pages_as_images(reg, hits, token)]


async def index_document(
    reg: "ToolRegistry", spec: "ToolSpec", args: dict, token: str | None = None
) -> list[ContentBlock]:
    import binascii

    try:
        raw = base64.b64decode(args["content_base64"])
    except (binascii.Error, ValueError):
        return [_text("content_base64 is not valid base64.")]
    result = await reg.client.post_multipart(
        "/api/rag/documents/upload",
        files={"file": (args["filename"], raw)},
        token=token,
    )
    return [_text(result)]


# -- document_pages: read a document as page images (Vision RAG) -------------


async def document_pages(
    reg: "ToolRegistry", spec: "ToolSpec", args: dict, token: str | None = None
) -> list[ContentBlock]:
    doc_id = args["document_id"]
    start = args["start_page"]
    count = min(args["count"], _MAX_PAGES_PER_CALL)

    pages = await reg.client.get_json(f"/api/rag/documents/{doc_id}/pages", token=token)
    numbers = sorted(p["page_number"] for p in pages)
    window = [n for n in numbers if n >= start][:count]
    if not window:
        return [_text(f"Document {doc_id} has no pages at or after page {start}.")]

    blocks: list[ContentBlock] = [
        _text(
            f"Document {doc_id}: returning page images {window} "
            f"(document has {len(numbers)} indexed page(s))."
        )
    ]
    for number in window:
        content, mime = await reg.client.get_bytes(
            f"/api/rag/documents/{doc_id}/pages/{number}/image", auth="query", token=token
        )
        blocks.append(
            types.ImageContent(
                type="image",
                data=base64.b64encode(content).decode("ascii"),
                mimeType=mime,
            )
        )
    return blocks


# -- web_search: independent of the (Gemini-quota-limited) backend -----------

_RESULT_RE = re.compile(
    r'<a\b[^>]*class="result__a"[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', re.S
)
_SNIPPET_RE = re.compile(r'<a\b[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html)).strip()


def _unwrap_ddg_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    match = re.search(r"[?&]uddg=([^&]+)", href)
    return urllib.parse.unquote(match.group(1)) if match else href


def parse_ddg_html(html: str, max_results: int) -> list[dict[str, str]]:
    titles = _RESULT_RE.findall(html)
    snippets = _SNIPPET_RE.findall(html)
    results: list[dict[str, str]] = []
    for i, (href, inner) in enumerate(titles[:max_results]):
        results.append(
            {
                "title": _strip_tags(inner),
                "url": _unwrap_ddg_url(href),
                "snippet": _strip_tags(snippets[i]) if i < len(snippets) else "",
            }
        )
    return results


async def _fetch_ddg(query: str, max_results: int, client: httpx.AsyncClient | None):
    own = client is None
    if own:
        client = httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers={"User-Agent": _WEB_UA}
        )
    try:
        resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        resp.raise_for_status()
        return parse_ddg_html(resp.text, max_results)
    finally:
        if own:
            await client.aclose()


async def web_search(
    reg: "ToolRegistry", spec: "ToolSpec", args: dict, token: str | None = None
) -> list[ContentBlock]:
    # token is unused: web_search does not touch the maptalk backend.
    query = args["query"]
    max_results = args["max_results"]
    results = await _fetch_ddg(query, max_results, reg.web_client)
    if not results:
        return [_text(f"No web results found for: {query}")]
    formatted = "\n\n".join(
        f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
        for i, r in enumerate(results, start=1)
    )
    return [_text(formatted)]


HANDLERS = {
    "http_json": http_json,
    "http_post_json": http_post_json,
    "document_pages": document_pages,
    "web_search": web_search,
    "semantic_image_search": semantic_image_search,
    "search_by_image": search_by_image,
    "index_document": index_document,
}
