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


def _blocks(result):
    """dispatch() returns either a list of content blocks, or a
    (blocks, structuredContent) tuple — this unwraps to the blocks."""
    return result[0] if isinstance(result, tuple) else result


def _structured(result):
    return result[1] if isinstance(result, tuple) else None


async def test_search_documents_refs_mode_is_text_only(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/search/documents":
            assert request.url.params["q"] == "fixation"
            assert request.url.params["limit"] == "5"
            return httpx.Response(200, json={"query": "fixation", "results": [
                {"document_id": 3, "document_name": "Prot.pdf", "page_number": 2, "similarity_score": 0.9}]})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    result = await reg.dispatch("search_documents", {"query": "fixation", "return": "refs", "limit": 5})
    blocks = _blocks(result)
    assert len(blocks) == 1 and blocks[0].type == "text"
    assert "Prot.pdf" in blocks[0].text and "doc 3" in blocks[0].text
    # structuredContent carries the machine-readable hits for chaining
    assert _structured(result)["results"][0]["document_id"] == 3


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
    blocks = _blocks(await reg.dispatch("search_documents", {"query": "fixation"}))
    images = [b for b in blocks if b.type == "image"]
    assert len(images) == 1 and base64.b64decode(images[0].data) == png


async def test_search_documents_forwards_the_library_scope(make_registry):
    """Scoping args must reach the backend as repeated query params.

    Without this the agent can be told to search one folder, report that it did,
    and have searched the whole library -- the failure mode where the answer looks
    right and the scope silently was not applied.
    """
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/search/documents":
            seen["params"] = request.url.params
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    await reg.dispatch("search_documents", {
        "query": "tubulin", "return": "refs",
        "folder_ids": [3, 4], "group_ids": [2], "include_subfolders": False,
    })
    assert seen["params"].get_list("folder_ids") == ["3", "4"]
    assert seen["params"].get_list("group_ids") == ["2"]
    assert seen["params"].get("include_subfolders") == "false"


async def test_search_documents_without_scope_args_sends_none(make_registry):
    """Omitted means "everything you can read" -- the backend default. Sending an
    empty list instead would scope the search to no folders at all."""
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/search/documents":
            seen["params"] = request.url.params
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    await reg.dispatch("search_documents", {"query": "tubulin", "return": "refs"})
    assert "folder_ids" not in seen["params"]
    assert "group_ids" not in seen["params"]


async def test_search_documents_include_fov_appends_fov_matches(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/rag/search/documents":
            return httpx.Response(200, json={"query": "cell", "results": []})
        if path == "/api/rag/search/fov":
            return httpx.Response(200, json={"query": "cell", "results": [{"image_id": 42}]})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch("search_documents", {"query": "cell", "include_fov": True}))
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
    blocks = _blocks(await reg.dispatch("find_documents", {"skip": 0}))
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


async def test_search_documents_clamps_limit_per_mode(make_registry):
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/search/documents":
            seen["limit"] = request.url.params["limit"]
            return httpx.Response(200, json={"query": "x", "results": []})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    await reg.dispatch("search_documents", {"query": "x", "limit": 999})
    assert seen["limit"] == "10"  # images mode capped at 10
    await reg.dispatch("search_documents", {"query": "x", "return": "refs", "limit": 999})
    assert seen["limit"] == "50"  # refs mode capped at 50


async def test_search_documents_one_bad_page_keeps_the_others(make_registry):
    png = b"\x89PNG\r\n\x1a\n-p"

    def routes(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/api/rag/search/documents":
            return httpx.Response(200, json={"query": "x", "results": [
                {"document_id": 3, "document_name": "D", "page_number": 1, "similarity_score": 0.9},
                {"document_id": 3, "document_name": "D", "page_number": 2, "similarity_score": 0.8}]})
        if p == "/api/rag/documents/3/pages/1/image":
            return httpx.Response(200, content=png, headers={"content-type": "image/webp"})
        if p == "/api/rag/documents/3/pages/2/image":
            return httpx.Response(404, text="gone")  # one bad page
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch("search_documents", {"query": "x"}))
    assert len([b for b in blocks if b.type == "image"]) == 1  # the good page survives
    assert any(b.type == "text" and "p.2 image unavailable" in b.text for b in blocks)


async def test_search_documents_fov_failure_keeps_doc_results(make_registry):
    png = b"\x89PNG\r\n\x1a\n-p"

    def routes(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/api/rag/search/documents":
            return httpx.Response(200, json={"query": "x", "results": [
                {"document_id": 3, "document_name": "D", "page_number": 1, "similarity_score": 0.9}]})
        if p == "/api/rag/documents/3/pages/1/image":
            return httpx.Response(200, content=png, headers={"content-type": "image/webp"})
        if p == "/api/rag/search/fov":
            return httpx.Response(500, text="fov down")  # optional augmentation fails
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch("search_documents", {"query": "x", "include_fov": True}))
    assert any(b.type == "image" for b in blocks)  # primary doc result NOT discarded
    assert any(b.type == "text" and "FOV image search failed" in b.text for b in blocks)


async def test_search_documents_include_fov_empty(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/api/rag/search/documents":
            return httpx.Response(200, json={"query": "x", "results": []})
        if p == "/api/rag/search/fov":
            return httpx.Response(200, json={"query": "x", "results": []})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch("search_documents", {"query": "x", "include_fov": True}))
    assert any(b.type == "text" and "No FOV" in b.text for b in blocks)


async def test_find_documents_last_page_has_no_call_again(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/documents":
            return httpx.Response(200, json=[{"id": 5}], headers={"X-Total-Count": "5"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch("find_documents", {"skip": 4}))
    text = " ".join(b.text for b in blocks if b.type == "text")
    assert "1 of 5" in text and "Call again" not in text  # terminal page


async def test_find_documents_no_total_header_no_note(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/documents":
            return httpx.Response(200, json=[{"id": 1}])  # no X-Total-Count header
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    result = await reg.dispatch("find_documents", {})
    assert not any("Showing" in b.text for b in _blocks(result) if b.type == "text")
    assert "total" not in _structured(result)


async def test_find_documents_malformed_total_degrades(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/documents":
            return httpx.Response(200, json=[{"id": 1}], headers={"X-Total-Count": "not-a-number"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    result = await reg.dispatch("find_documents", {})  # must NOT raise / discard the listing
    assert _blocks(result)[0].type == "text"
    assert "total" not in _structured(result)


async def test_find_documents_forwards_folder_scope(make_registry):
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/documents":
            seen["params"] = request.url.params
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    await reg.dispatch(
        "find_documents", {"folder_ids": [5, 8], "include_subfolders": False}
    )
    # Repeated params, not a JSON-encoded string: that is what FastAPI's
    # Query(List[int]) parses. A stringified list arrives as one unparseable value.
    assert seen["params"].get_list("folder_ids") == ["5", "8"]
    assert seen["params"].get("include_subfolders") == "false"


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
    blocks = _blocks(await reg.dispatch("find_documents", {}, token="mtk_pat_abc123"))
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


async def test_index_from_url_sends_only_the_link(make_registry):
    """The whole point of this tool: the file never passes through the model.

    index_document takes bytes inline, which is ~350k tokens per megabyte -- so a
    real paper can only be indexed if the SERVER fetches it.
    """
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/documents/from-url":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "id": 5, "name": "p.pdf", "file_type": "pdf", "status": "pending",
                "page_count": 0, "created_at": "2026-08-01T00:00:00Z",
                "is_duplicate": False,
            })
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    await reg.dispatch("index_document_from_url", {"url": "https://example.org/p.pdf"})
    assert seen["body"] == {"url": "https://example.org/p.pdf"}
    assert "content_base64" not in seen["body"]


async def test_import_papers_sends_dois_as_a_list(make_registry):
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rag/discover/import":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "imported": 2, "failed": [], "already_in_library": [],
            })
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    await reg.dispatch("import_papers", {"dois": ["10.1/a", "10.1/b"]})
    assert seen["body"]["dois"] == ["10.1/a", "10.1/b"]


async def test_list_experiments_sends_no_cap_but_reports_the_total(make_registry):
    """Omitting limit/skip must put NOTHING on the wire, and still report a total.

    The backend answers an unparametrised call in full, so any query param this
    tool invented would cap a listing the server was willing to give whole. The
    total still matters: it is what lets the model tell "these are all of them"
    from "this is a page", which a bare array cannot express.
    """
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/experiments":
            seen["params"] = dict(request.url.params)
            return httpx.Response(
                200, json=[{"id": 1}, {"id": 2}], headers={"X-Total-Count": "2"}
            )
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    result = await reg.dispatch("list_experiments", {})
    blocks = _blocks(result)
    text = " ".join(b.text for b in blocks if b.type == "text")

    assert seen["params"] == {}, f"tool imposed a cap the server did not ask for: {seen}"
    assert "2 of 2" in text
    assert "Call again" not in text  # nothing left to page for


async def test_list_experiments_pages_when_asked(make_registry):
    """The escape hatch works: an explicit limit caps and steers the next page."""
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/experiments":
            assert request.url.params["limit"] == "2"
            return httpx.Response(
                200, json=[{"id": 3}, {"id": 4}], headers={"X-Total-Count": "9"}
            )
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch("list_experiments", {"limit": 2, "skip": 2}))
    text = " ".join(b.text for b in blocks if b.type == "text")
    assert "2 of 9" in text
    assert "skip=4" in text
