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
    assert {"search_documents", "semantic_search", "list_documents",
            "get_document_metadata", "read_document_pages", "web_search"} <= set(tools)

    schema = tools["search_documents"].inputSchema
    assert schema["properties"]["query"]["type"] == "string"
    assert schema["properties"]["limit"]["type"] == "integer"
    assert schema["properties"]["limit"]["default"] == 20
    assert schema["required"] == ["query"]
    # backend routing details (maps_to 'q') must never reach the model schema
    assert "q" not in schema["properties"]


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
