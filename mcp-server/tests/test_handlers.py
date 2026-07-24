"""Handler dispatch: request building, images, web search, error surfacing."""
from __future__ import annotations

import base64
import json

import httpx


def _with_login(routes):
    """Wrap a route table so /api/auth/login always mints a token."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "T"})
        return routes(request)

    return handler


async def test_search_documents_refs_mode_is_text_only(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/search/documents":
            assert request.url.params["q"] == "fixation"
            assert request.url.params["limit"] == "5"
            return httpx.Response(200, json={"query": "fixation", "results": [
                {"document_id": 3, "document_name": "Prot.pdf", "page_number": 2, "similarity_score": 0.9}]})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = await reg.dispatch("search_documents", {"query": "fixation", "return": "refs", "limit": 5})
    assert len(blocks) == 1 and blocks[0].type == "text"
    assert "Prot.pdf" in blocks[0].text and "doc 3" in blocks[0].text


async def test_search_documents_default_returns_page_images(make_registry):
    png = b"\x89PNG\r\n\x1a\n-page"

    def routes(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/rag/search/documents":
            return httpx.Response(200, json={"query": "fixation", "results": [
                {"document_id": 3, "document_name": "Prot.pdf", "page_number": 2, "similarity_score": 0.9}]})
        if path == "/api/rag/documents/3/pages/2/image":
            assert request.url.params["token"] == "T"  # query-token auth
            return httpx.Response(200, content=png, headers={"content-type": "image/webp"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    # default return=images: retrieval is built in — one call yields the page image
    blocks = await reg.dispatch("search_documents", {"query": "fixation"})
    images = [b for b in blocks if b.type == "image"]
    assert len(images) == 1 and base64.b64decode(images[0].data) == png


async def test_search_documents_include_fov_appends_fov_matches(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/rag/search/documents":
            return httpx.Response(200, json={"query": "cell", "results": []})
        if path == "/api/rag/search/fov":
            return httpx.Response(200, json={"query": "cell", "results": [{"image_id": 42}]})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = await reg.dispatch("search_documents", {"query": "cell", "include_fov": True})
    text = " ".join(b.text for b in blocks if b.type == "text")
    assert "42" in text  # FOV image match surfaced


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


async def test_read_page_region_returns_high_res_crop(make_registry):
    png = b"\x89PNG\r\n\x1a\n-crop-bytes"

    def routes(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/rag/documents/4/pages/4/region":
            assert request.url.params["bbox"] == "100,200,400,600"  # ymin,xmin,ymax,xmax
            assert request.url.params["token"] == "T"  # query-token auth
            return httpx.Response(200, content=png, headers={"content-type": "image/png"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = await reg.dispatch(
        "read_page_region",
        {"document_id": 4, "page_number": 4, "ymin": 100, "xmin": 200, "ymax": 400, "xmax": 600},
    )
    images = [b for b in blocks if b.type == "image"]
    assert len(images) == 1
    assert base64.b64decode(images[0].data) == png
    assert images[0].mimeType == "image/png"


async def test_read_page_region_rejects_bad_bbox_without_calling_backend(make_registry):
    called = {"n": 0}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/region"):
            called["n"] += 1
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    # xmin >= xmax is invalid -> a text error, and the region endpoint is never hit.
    blocks = await reg.dispatch(
        "read_page_region",
        {"document_id": 4, "page_number": 4, "ymin": 100, "xmin": 600, "ymax": 400, "xmax": 200},
    )
    assert len(blocks) == 1 and blocks[0].type == "text"
    assert "bbox" in blocks[0].text.lower()
    assert called["n"] == 0


async def test_find_documents_surfaces_total_and_pagination(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/documents":
            assert request.url.params["skip"] == "0"
            return httpx.Response(
                200, json=[{"id": 1}, {"id": 2}], headers={"X-Total-Count": "5"}
            )
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = await reg.dispatch("find_documents", {"skip": 0})
    text = " ".join(b.text for b in blocks if b.type == "text")
    assert "2 of 5" in text  # showed 2, 5 total
    assert "skip=2" in text  # steers the next page


async def test_move_document_into_folder_sends_folder_id(make_registry):
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/documents/7" and request.method == "PATCH":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"id": 7, "folder_id": 3})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    await reg.dispatch("move_document", {"document_id": 7, "folder_id": 3})
    assert seen["body"] == {"folder_id": 3}


async def test_move_document_to_root_sends_null_folder_id(make_registry):
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/documents/7" and request.method == "PATCH":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"id": 7, "folder_id": None})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    # folder_id omitted -> the pipeline strips it, but the custom handler must
    # still send an explicit null so the backend moves the doc to root.
    await reg.dispatch("move_document", {"document_id": 7})
    assert seen["body"] == {"folder_id": None}


async def test_create_folder_posts_name_and_parent(make_registry):
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/folders" and request.method == "POST":
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 9, "name": "Papers", "parent_id": 2})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    await reg.dispatch("create_folder", {"name": "Papers", "parent_id": 2})
    assert seen["body"] == {"name": "Papers", "parent_id": 2}


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
    blocks = await reg.dispatch("find_documents", {})
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
    blocks = await reg.dispatch("find_documents", {}, token="mtk_pat_abc123")
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
