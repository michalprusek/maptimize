"""Handler dispatch: request building, images, web search, error surfacing."""
from __future__ import annotations

import base64

import httpx


def _with_login(routes):
    """Wrap a route table so /api/auth/login always mints a token."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "T"})
        return routes(request)

    return handler


async def test_search_documents_maps_query_and_limit(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/search/documents":
            assert request.url.params["q"] == "fixation"
            assert request.url.params["limit"] == "5"
            return httpx.Response(200, json={"query": "fixation", "results": [{"document_id": 3}]})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = await reg.dispatch("search_documents", {"query": "fixation", "limit": 5})
    assert len(blocks) == 1 and blocks[0].type == "text"
    assert "document_id" in blocks[0].text


async def test_path_param_substitution(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/documents/42":
            return httpx.Response(200, json={"id": 42, "name": "x"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = await reg.dispatch("get_document_metadata", {"document_id": 42})
    assert blocks[0].type == "text"
    assert "42" in blocks[0].text


async def test_read_document_pages_returns_page_images(make_registry):
    png = b"\x89PNG\r\n\x1a\n-fake-bytes"

    def routes(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/rag/documents/7/pages":
            return httpx.Response(200, json=[{"page_number": 1}, {"page_number": 2}, {"page_number": 3}])
        if path.startswith("/api/rag/documents/7/pages/") and path.endswith("/image"):
            assert request.url.params["token"] == "T"  # query-token auth
            return httpx.Response(200, content=png, headers={"content-type": "image/webp"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = await reg.dispatch("read_document_pages", {"document_id": 7, "start_page": 1, "count": 2})
    images = [b for b in blocks if b.type == "image"]
    assert len(images) == 2  # count honoured (2 of 3 pages)
    assert base64.b64decode(images[0].data) == png
    assert images[0].mimeType == "image/webp"


async def test_web_search_parses_ddg_results(make_registry):
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x">First <b>hit</b></a>
      <a class="result__snippet" href="#">Snippet <b>one</b> here</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.org/b">Second hit</a>
      <a class="result__snippet" href="#">Snippet two</a>
    </div>
    """

    def backend(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "T"})

    def web(request: httpx.Request) -> httpx.Response:
        assert "duckduckgo" in str(request.url)
        return httpx.Response(200, text=html)

    reg = make_registry(backend, web_handler=web)
    blocks = await reg.dispatch("web_search", {"query": "microtubule", "max_results": 5})
    text = blocks[0].text
    assert "https://example.com/a" in text  # uddg redirect unwrapped
    assert "First hit" in text  # tags stripped
    assert "https://example.org/b" in text


async def test_backend_error_is_reported_not_raised(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    reg = make_registry(_with_login(routes))
    blocks = await reg.dispatch("list_documents", {})
    assert blocks[0].type == "text"
    assert "Error" in blocks[0].text


async def test_unknown_tool_is_reported(make_registry):
    reg = make_registry(_with_login(lambda r: httpx.Response(404)))
    blocks = await reg.dispatch("nonexistent_tool", {})
    assert "Unknown tool" in blocks[0].text


async def test_per_request_token_passthrough(make_registry):
    """With a per-request token, the backend is called AS that token — no login."""
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/api/auth/login"  # pass-through must not log in
        if request.url.path == "/api/rag/documents":
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json=[{"id": 1}])
        return httpx.Response(404)

    reg = make_registry(routes)
    blocks = await reg.dispatch("list_documents", {}, token="mtk_pat_abc123")
    assert seen["auth"] == "Bearer mtk_pat_abc123"
    assert blocks[0].type == "text"


async def test_page_image_token_passthrough_uses_query(make_registry):
    """document_pages must forward the caller's token as ?token= for image bytes."""
    def routes(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/api/auth/login"
        path = request.url.path
        if path == "/api/rag/documents/5/pages":
            assert request.headers.get("Authorization") == "Bearer mtk_pat_xyz"
            return httpx.Response(200, json=[{"page_number": 1}])
        if path.endswith("/image"):
            assert request.url.params["token"] == "mtk_pat_xyz"
            return httpx.Response(200, content=b"img", headers={"content-type": "image/webp"})
        return httpx.Response(404)

    reg = make_registry(routes)
    blocks = await reg.dispatch(
        "read_document_pages", {"document_id": 5, "start_page": 1, "count": 1}, token="mtk_pat_xyz"
    )
    assert any(b.type == "image" for b in blocks)
