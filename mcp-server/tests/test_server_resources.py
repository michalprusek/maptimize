"""Resources catalog + prompt templates (server.py).

The resource handlers hit the backend AS the caller, so the security-critical
bit is that they FAIL CLOSED on a remote request with no bearer instead of
falling through to the env service login (which would serve another user's
catalog). Both paths are covered here.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import AnyUrl

import maptalk_mcp.server as S


def _registry(**client_over) -> MagicMock:
    reg = MagicMock()
    reg.client.get_json = AsyncMock(**client_over)
    return reg


async def test_list_resources_returns_catalog():
    reg = _registry(return_value=[
        {"id": 3, "name": "Prot.pdf", "file_type": "pdf", "page_count": 5, "status": "completed"}
    ])
    with patch.object(S, "_bearer_from_context", return_value=(True, "tok")):
        resources = await S._list_resources_impl(MagicMock(), reg)
    assert len(resources) == 1
    assert str(resources[0].uri) == "maptalk://document/3"
    assert resources[0].name == "Prot.pdf"
    reg.client.get_json.assert_awaited_once()


async def test_list_resources_fail_closed_without_bearer():
    reg = _registry()
    with patch.object(S, "_bearer_from_context", return_value=(True, None)):
        resources = await S._list_resources_impl(MagicMock(), reg)
    assert resources == []
    reg.client.get_json.assert_not_awaited()  # ACL: never touch the backend


async def test_read_resource_returns_metadata():
    reg = _registry(return_value={"id": 3, "name": "Prot.pdf"})
    with patch.object(S, "_bearer_from_context", return_value=(True, "tok")):
        out = await S._read_resource_impl(MagicMock(), reg, AnyUrl("maptalk://document/3"))
    assert json.loads(out[0].content)["id"] == 3
    assert out[0].mime_type == "application/json"


async def test_read_resource_fail_closed_without_bearer():
    reg = _registry()
    with patch.object(S, "_bearer_from_context", return_value=(True, None)):
        with pytest.raises(ValueError, match="unauthorized"):
            await S._read_resource_impl(MagicMock(), reg, AnyUrl("maptalk://document/3"))
    reg.client.get_json.assert_not_awaited()


async def test_read_resource_rejects_unknown_uri():
    reg = _registry()
    with patch.object(S, "_bearer_from_context", return_value=(False, None)):
        with pytest.raises(ValueError, match="Unsupported"):
            await S._read_resource_impl(MagicMock(), reg, AnyUrl("maptalk://other/3"))


def test_document_id_from_uri():
    assert S._document_id_from_uri(AnyUrl("maptalk://document/24")) == 24
    assert S._document_id_from_uri(AnyUrl("maptalk://other/24")) is None
    assert S._document_id_from_uri(AnyUrl("maptalk://document/xx")) is None


def test_render_prompt_templates():
    assert "read_document_pages" in S._render_prompt("summarize_document", {"document_id": 3})
    assert "5,6" in S._render_prompt("compare_documents", {"document_ids": "5,6"})
    assert "microtubule" in S._render_prompt("literature_search", {"topic": "microtubule"})
    with pytest.raises(ValueError, match="Unknown prompt"):
        S._render_prompt("nope", {})
