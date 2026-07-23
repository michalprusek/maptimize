"""Live MCP handshake over stdio.

Spawns ``python -m maptalk_mcp``, performs the real initialize + tools/list
round-trip through the protocol, and checks the tools show up. Needs no backend:
listing tools never calls the backend.
"""
from __future__ import annotations

import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_stdio_server_lists_tools():
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAPTALK_")}
    env["MAPTALK_BASE_URL"] = "http://127.0.0.1:9"  # never contacted for tools/list

    params = StdioServerParameters(command=sys.executable, args=["-m", "maptalk_mcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {t.name for t in result.tools}

    assert {"search_documents", "read_document_pages", "web_search"} <= names
