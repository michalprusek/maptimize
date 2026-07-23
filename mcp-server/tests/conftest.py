"""Shared test fixtures.

Everything is mocked with ``httpx.MockTransport`` — no live backend, no
credentials and no GPU are needed. Fixtures return factory callables so each
test can supply its own request handler.
"""
from __future__ import annotations

import httpx
import pytest

from maptalk_mcp.client import MaptalkClient
from maptalk_mcp.config import DEFAULT_TOOLS_FILE, Config
from maptalk_mcp.registry import ToolRegistry


def _config(**over) -> Config:
    base = dict(
        base_url="http://test",
        email="svc@lab",
        password="pw",
        token=None,
        verify_tls=True,
        timeout=10,
        tools_file=DEFAULT_TOOLS_FILE,
    )
    base.update(over)
    return Config(**base)


@pytest.fixture
def make_config():
    return _config


@pytest.fixture
def make_client():
    def _make(handler, **over) -> MaptalkClient:
        http = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
        return MaptalkClient(_config(**over), http=http)

    return _make


@pytest.fixture
def make_registry(make_client):
    def _make(handler, web_handler=None, tools_file=DEFAULT_TOOLS_FILE, **over) -> ToolRegistry:
        client = make_client(handler, **over)
        web_client = None
        if web_handler is not None:
            web_client = httpx.AsyncClient(transport=httpx.MockTransport(web_handler))
        return ToolRegistry(tools_file, client, web_client=web_client)

    return _make
