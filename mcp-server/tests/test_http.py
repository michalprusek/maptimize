"""Streamable-HTTP transport: PAT gate + live round-trip.

Each test spawns ``python -m maptalk_mcp --transport http`` and points it at an
in-process stub backend that stands in for ``GET /api/auth/me`` (200 for the one
valid PAT, 401 otherwise) — the gate validates every caller's PAT against it.
"""
from __future__ import annotations

import http.server
import os
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

from maptalk_mcp.config import DEFAULT_TOOLS_FILE
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

VALID_PAT = "mtk_pat_valid_example_token"


class _StubBackendHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/api/auth/me":
            ok = self.headers.get("Authorization") == f"Bearer {VALID_PAT}"
            self.send_response(200 if ok else 401)
            self.end_headers()
            self.wfile.write(b"{}")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # silence
        pass


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def stub_backend():
    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), _StubBackendHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()


@pytest.fixture
def http_server(stub_backend):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MAPTALK_", "MCP_"))}
    env.update(
        {
            "MCP_HTTP_HOST": "127.0.0.1",
            "MCP_HTTP_PORT": str(port),
            "MAPTALK_BASE_URL": stub_backend,
            "MAPTALK_TOOLS_FILE": DEFAULT_TOOLS_FILE,
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "maptalk_mcp", "--transport", "http"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/healthz", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("http server did not become healthy")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _post_mcp(base: str, headers: dict) -> int:
    with httpx.Client() as client:
        return client.post(
            base + "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={**headers, "Accept": "application/json, text/event-stream"},
        ).status_code


async def test_rejects_missing_token(http_server):
    assert _post_mcp(http_server, {}) == 401


async def test_rejects_non_pat_bearer(http_server):
    assert _post_mcp(http_server, {"Authorization": "Bearer not-a-pat"}) == 401


async def test_rejects_invalid_pat(http_server):
    assert _post_mcp(http_server, {"Authorization": "Bearer mtk_pat_wrong"}) == 401


async def test_lists_tools_with_valid_pat(http_server):
    headers = {"Authorization": f"Bearer {VALID_PAT}"}
    async with streamablehttp_client(http_server + "/mcp/", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {t.name for t in result.tools}
    assert {"search_documents", "read_document_pages", "web_search"} <= names
