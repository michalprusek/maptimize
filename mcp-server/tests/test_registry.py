"""Config-driven schema generation and hot-reload."""
from __future__ import annotations

import os
import time

import httpx

from maptalk_mcp.client import MaptalkClient
from maptalk_mcp.config import Config, DEFAULT_TOOLS_FILE
from maptalk_mcp.registry import ToolRegistry


def _noop(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404)


def test_list_tools_builds_schema_from_yaml(make_registry):
    tools = {t.name: t for t in make_registry(_noop).list_tools()}
    # Pin the EXACT public tool contract: a dropped or accidentally-added tool
    # fails here (the three overlapping search/list tools are gone).
    assert set(tools) == {
        "search_documents", "read_document_pages", "read_page_region", "web_search",
        "find_documents", "get_document_metadata", "find_similar_pages",
        "search_by_image", "search_by_text_example", "index_text", "index_document",
        "reindex_document", "delete_document", "get_indexing_status",
        "list_folders", "create_folder", "move_document",
    }

    schema = tools["search_documents"].inputSchema
    assert schema["properties"]["query"]["type"] == "string"
    assert schema["required"] == ["query"]
    # retrieval is built in: `return` defaults to images and is enum-constrained
    assert schema["properties"]["return"]["default"] == "images"
    assert schema["properties"]["return"]["enum"] == ["images", "refs"]
    # backend routing details (maps_to 'q') must never reach the model schema
    assert "q" not in schema["properties"]


def test_real_tools_are_annotated_and_enum_constrained(make_registry):
    tools = {t.name: t for t in make_registry(_noop).list_tools()}
    # every tool carries annotations (consent UX hints)
    assert all(t.annotations is not None for t in tools.values())
    # reads are read-only; web_search reaches the open web; delete is destructive
    assert tools["search_documents"].annotations.readOnlyHint is True
    assert tools["web_search"].annotations.openWorldHint is True
    assert tools["delete_document"].annotations.destructiveHint is True
    assert tools["index_document"].annotations.readOnlyHint is False
    # free-string params are enum-constrained; file_type must include the real
    # stored value "text" (index_text writes file_type=text) or it would reject it
    props = tools["find_documents"].inputSchema["properties"]
    assert props["status"]["enum"] == ["pending", "processing", "completed", "failed"]
    assert "text" in props["file_type"]["enum"]


def test_path_param_is_in_schema_but_stays_declarative(make_registry):
    tools = {t.name: t for t in make_registry(_noop).list_tools()}
    schema = tools["get_document_metadata"].inputSchema
    assert "document_id" in schema["properties"]
    assert "document_id" in schema["required"]


def _client_on(tmp_file: str) -> MaptalkClient:
    http = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(_noop))
    cfg = Config(base_url="http://test", email="a@b", password="p", token=None,
                 verify_tls=True, timeout=10, tools_file=tmp_file)
    return MaptalkClient(cfg, http=http)


def test_descriptions_hot_reload_on_mtime_change(tmp_path):
    yaml_file = tmp_path / "tools.yaml"
    template = (
        "tools:\n"
        "  - name: list_documents\n"
        "    description: {desc}\n"
        "    handler: http_json\n"
        "    method: GET\n"
        "    path: /api/rag/documents\n"
        "    params: []\n"
    )
    yaml_file.write_text(template.format(desc="original description"))
    reg = ToolRegistry(str(yaml_file), _client_on(str(yaml_file)))
    assert reg.list_tools()[0].description == "original description"

    yaml_file.write_text(template.format(desc="updated description"))
    future = time.time() + 10
    os.utime(yaml_file, (future, future))  # guarantee a fresh mtime
    assert reg.list_tools()[0].description == "updated description"


def test_bad_reload_keeps_previous_registry(tmp_path):
    yaml_file = tmp_path / "tools.yaml"
    good = (
        "tools:\n"
        "  - name: list_documents\n"
        "    description: good\n"
        "    handler: http_json\n"
        "    method: GET\n"
        "    path: /api/rag/documents\n"
        "    params: []\n"
    )
    yaml_file.write_text(good)
    reg = ToolRegistry(str(yaml_file), _client_on(str(yaml_file)))
    assert [t.name for t in reg.list_tools()] == ["list_documents"]

    yaml_file.write_text("tools:\n  - name: broken\n    handler: does_not_exist\n")
    future = time.time() + 10
    os.utime(yaml_file, (future, future))
    # invalid edit is ignored, last good registry survives
    assert [t.name for t in reg.list_tools()] == ["list_documents"]


def test_enum_and_annotations_surface_in_tool(tmp_path):
    yaml_file = tmp_path / "tools.yaml"
    yaml_file.write_text(
        "tools:\n"
        "  - name: find_documents\n"
        "    description: filter\n"
        "    handler: http_json\n"
        "    method: GET\n"
        "    path: /api/rag/documents\n"
        "    annotations:\n"
        "      readOnlyHint: true\n"
        "      openWorldHint: false\n"
        "    params:\n"
        "      - name: status\n"
        "        in: query\n"
        "        enum: [pending, completed]\n"
    )
    tool = ToolRegistry(str(yaml_file), _client_on(str(yaml_file))).list_tools()[0]
    # enum reaches the model-facing input schema
    assert tool.inputSchema["properties"]["status"]["enum"] == ["pending", "completed"]
    # annotations become a typed ToolAnnotations the client can read
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.openWorldHint is False


def test_tool_without_annotations_has_none(tmp_path):
    yaml_file = tmp_path / "tools.yaml"
    yaml_file.write_text(
        "tools:\n"
        "  - name: list_documents\n"
        "    description: plain\n"
        "    handler: http_json\n"
        "    method: GET\n"
        "    path: /api/rag/documents\n"
        "    params: []\n"
    )
    tool = ToolRegistry(str(yaml_file), _client_on(str(yaml_file))).list_tools()[0]
    assert tool.annotations is None  # unannotated tools stay unset (SDK worst-case defaults)
