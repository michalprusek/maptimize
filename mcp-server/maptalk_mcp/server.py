"""Wire the config-driven registry to a low-level MCP ``Server``.

The list/call handlers are intentionally thin — every decision lives in the
registry so the transport code never needs to change when tools do.
"""
from __future__ import annotations

import mcp.types as types
from mcp.server.lowlevel import Server

from .registry import ContentBlock, ToolRegistry


def build_server(registry: ToolRegistry) -> Server:
    server: Server = Server("maptalk-mcp")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return registry.list_tools()

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None) -> list[ContentBlock]:
        # HTTP transport: the caller's per-request bearer (their personal access
        # token) is on the original request. stdio has no request -> token None,
        # and the client falls back to its env-based service login.
        token = _bearer_from_context(server)
        return await registry.dispatch(name, arguments or {}, token=token)

    return server


def _bearer_from_context(server: Server) -> str | None:
    try:
        request = server.request_context.request
    except LookupError:
        return None
    if request is None:
        return None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None
