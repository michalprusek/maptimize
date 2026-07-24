"""Application-control tools: experiments, images + detection, proteins, query_database.

Same pattern as test_handlers.py — a MockTransport route table wrapped so
/api/auth/login mints a token, then dispatch a tool and assert on the request the
backend would have received (method, path, body/query) and on the returned blocks.
"""
from __future__ import annotations

import base64
import json

import httpx


def _with_login(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "T"})
        return routes(request)

    return handler


def _blocks(result):
    return result[0] if isinstance(result, tuple) else result


# -- experiments -----------------------------------------------------------

async def test_create_experiment_posts_json_body(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/experiments" and request.method == "POST":
            body = json.loads(request.content)
            assert body == {"name": "Exp A", "map_protein_id": 5}
            return httpx.Response(201, json={"id": 10, "name": "Exp A"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch("create_experiment", {"name": "Exp A", "map_protein_id": 5}))
    assert "Exp A" in blocks[0].text


async def test_update_experiment_sends_only_provided_fields(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/experiments/3" and request.method == "PATCH":
            assert json.loads(request.content) == {"status": "completed"}
            return httpx.Response(200, json={"id": 3, "status": "completed"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    _blocks(await reg.dispatch("update_experiment", {"experiment_id": 3, "status": "completed"}))


async def test_delete_experiment_issues_delete(make_registry):
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/experiments/10":
            seen["method"] = request.method
            return httpx.Response(204)
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    _blocks(await reg.dispatch("delete_experiment", {"experiment_id": 10}))
    assert seen["method"] == "DELETE"


async def test_assign_protein_uses_query_param(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/experiments/3/protein" and request.method == "PATCH":
            assert request.url.params["map_protein_id"] == "9"
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    _blocks(await reg.dispatch("assign_experiment_protein", {"experiment_id": 3, "map_protein_id": 9}))


# -- images & detection ----------------------------------------------------

async def test_upload_image_posts_multipart_with_experiment_id(make_registry):
    raw = b"\x89PNG\r\n\x1a\n microscopy bytes"

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/images/upload" and request.method == "POST":
            assert "multipart/form-data" in request.headers["content-type"]
            body = request.content
            assert b'name="experiment_id"' in body and b"12" in body
            assert raw in body
            return httpx.Response(200, json={"id": 55, "status": "UPLOADING"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    b64 = base64.b64encode(raw).decode()
    blocks = _blocks(await reg.dispatch(
        "upload_image", {"experiment_id": 12, "filename": "fov.png", "content_base64": b64}))
    assert "55" in blocks[0].text


async def test_upload_image_bad_base64_is_reported(make_registry):
    reg = make_registry(_with_login(lambda r: httpx.Response(404)))
    blocks = _blocks(await reg.dispatch(
        "upload_image", {"experiment_id": 1, "filename": "x.png", "content_base64": "abc"}))
    assert "not valid base64" in blocks[0].text


async def test_process_images_posts_int_array_and_default_detect(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/images/batch-process" and request.method == "POST":
            body = json.loads(request.content)
            assert body["image_ids"] == [1, 2, 3]
            assert body["detect_cells"] is True  # YAML default
            return httpx.Response(200, json={"processed_count": 3})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    _blocks(await reg.dispatch("process_images", {"image_ids": [1, 2, 3]}))


async def test_process_images_forwards_explicit_detect_false(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/images/batch-process" and request.method == "POST":
            # explicit False must be preserved, not dropped back to the default True
            assert json.loads(request.content)["detect_cells"] is False
            return httpx.Response(200, json={"processed_count": 1})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    _blocks(await reg.dispatch("process_images", {"image_ids": [1], "detect_cells": False}))


async def test_process_images_non_list_rejected(make_registry):
    reg = make_registry(_with_login(lambda r: httpx.Response(404)))
    blocks = _blocks(await reg.dispatch("process_images", {"image_ids": "1,2,3"}))
    assert "must be an array" in blocks[0].text


async def test_process_images_bad_element_rejected(make_registry):
    reg = make_registry(_with_login(lambda r: httpx.Response(404)))
    blocks = _blocks(await reg.dispatch("process_images", {"image_ids": [1, "x", 3]}))
    assert "must be an array of integer" in blocks[0].text


async def test_reprocess_image_path_and_query(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/images/9/reprocess" and request.method == "POST":
            assert request.url.params["detect_cells"] == "false"
            return httpx.Response(200, json={"id": 9})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    _blocks(await reg.dispatch("reprocess_image", {"image_id": 9, "detect_cells": False}))


async def test_assign_protein_unassign_omits_param(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/experiments/3/protein" and request.method == "PATCH":
            # omitting map_protein_id clears the assignment -> no query param sent
            assert "map_protein_id" not in request.url.params
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    _blocks(await reg.dispatch("assign_experiment_protein", {"experiment_id": 3}))


async def test_redetect_cells_posts_array(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/images/batch-redetect" and request.method == "POST":
            assert json.loads(request.content) == {"image_ids": [7, 8]}
            return httpx.Response(200, json={"processed_count": 2, "message": "ok"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    _blocks(await reg.dispatch("redetect_cells", {"image_ids": [7, 8]}))


async def test_list_cell_crops_passes_experiment_query(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/images/crops":
            assert request.url.params["experiment_id"] == "4"
            return httpx.Response(200, json=[{"id": 1, "bundleness_score": 0.5}])
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch("list_cell_crops", {"experiment_id": 4}))
    assert "bundleness_score" in blocks[0].text


# -- proteins & database ---------------------------------------------------

async def test_create_protein_posts_body(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/proteins" and request.method == "POST":
            assert json.loads(request.content) == {"name": "PRC1", "gene_name": "PRC1"}
            return httpx.Response(201, json={"id": 2, "name": "PRC1"})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch("create_protein", {"name": "PRC1", "gene_name": "PRC1"}))
    assert "PRC1" in blocks[0].text


async def test_delete_protein_issues_delete(make_registry):
    seen = {}

    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/proteins/2":
            seen["method"] = request.method
            return httpx.Response(204)
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    _blocks(await reg.dispatch("delete_protein", {"protein_id": 2}))
    assert seen["method"] == "DELETE"


async def test_query_database_posts_sql_body(make_registry):
    def routes(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/query" and request.method == "POST":
            body = json.loads(request.content)
            assert body["sql"].startswith("SELECT")
            return httpx.Response(200, json={"columns": ["status"], "rows": [
                {"status": "READY"}], "row_count": 1})
        return httpx.Response(404)

    reg = make_registry(_with_login(routes))
    blocks = _blocks(await reg.dispatch(
        "query_database", {"sql": "SELECT status FROM images JOIN experiments ON true"}))
    assert "row_count" in blocks[0].text


# -- registry: schemas & annotations for the new tools ---------------------

async def test_new_tools_are_registered_with_correct_schema(make_registry):
    reg = make_registry(_with_login(lambda r: httpx.Response(404)))
    tools = {t.name: t for t in reg.list_tools()}

    for name in ["list_experiments", "create_experiment", "delete_experiment",
                 "upload_image", "process_images", "redetect_cells", "list_cell_crops",
                 "list_proteins", "create_protein", "delete_protein", "query_database"]:
        assert name in tools, f"{name} missing from registry"

    # image_ids surfaces as a typed array (registry array support)
    proc = tools["process_images"].inputSchema
    assert proc["properties"]["image_ids"]["type"] == "array"
    assert proc["properties"]["image_ids"]["items"]["type"] == "integer"

    # destructive tools carry the hint so the client asks for confirmation
    for name in ["delete_experiment", "delete_image", "delete_protein"]:
        assert tools[name].annotations.destructiveHint is True
    # reads are marked read-only
    for name in ["list_experiments", "get_image", "list_cell_crops", "list_proteins"]:
        assert tools[name].annotations.readOnlyHint is True
    # mutating (non-destructive) writes must NOT be readOnly, or the client would
    # skip consent for a mutation
    for name in ["create_experiment", "update_experiment", "assign_experiment_protein",
                 "upload_image", "process_images", "reprocess_image", "redetect_cells",
                 "create_protein", "update_protein", "compute_protein_embedding"]:
        assert tools[name].annotations.readOnlyHint is False
