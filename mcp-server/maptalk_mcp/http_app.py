"""Streamable-HTTP transport — for a hosted, remote MCP connector.

Wraps the low-level MCP server in a Starlette app behind a **mandatory** gate.
Claude's remote custom connectors call this URL from Anthropic's cloud, so the
endpoint is public: each caller presents their own personal access token (PAT),
which the gate validates against the backend (``GET /api/auth/me``) before any
request reaches the transport. This is what stops anonymous use of the
backend-independent ``web_search`` tool; every real document tool call is
independently re-authenticated at the backend as that user.
"""
from __future__ import annotations

import contextlib
import json
import os

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from .registry import ToolRegistry
from .server import build_server

# On a 401, point clients at the OAuth protected-resource metadata so Claude
# Desktop's remote connector can discover the authorization server and start the
# OAuth flow. Claude Code just sends its bearer (PAT) and never sees this.
_RESOURCE_METADATA_URL = os.environ.get(
    "MCP_RESOURCE_METADATA_URL",
    "https://maptimize.utia.cas.cz/.well-known/oauth-protected-resource",
)
_WWW_AUTHENTICATE = f'Bearer resource_metadata="{_RESOURCE_METADATA_URL}"'


async def _send_json(send: Send, status: int, payload: dict, extra_headers=None) -> None:
    body = json.dumps(payload).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def build_http_app(
    registry: ToolRegistry,
    *,
    allowed_hosts: list[str] | None = None,
    stateless: bool = True,
    json_response: bool = False,
) -> Starlette:
    server = build_server(registry)
    security = TransportSecuritySettings(
        # Behind nginx the Host is the public domain; lock it if the operator
        # provides an allowlist, otherwise trust the reverse proxy + PAT gate.
        enable_dns_rebinding_protection=bool(allowed_hosts),
        allowed_hosts=allowed_hosts or [],
        allowed_origins=allowed_hosts or [],
    )
    manager = StreamableHTTPSessionManager(
        app=server,
        stateless=stateless,
        json_response=json_response,
        security_settings=security,
    )

    async def guarded_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        valid = False
        if token:
            try:
                valid = await registry.client.validate_token(token)
            except Exception:
                valid = False
        if not valid:
            # 401 + WWW-Authenticate triggers Claude Desktop's OAuth discovery.
            await _send_json(
                send, 401, {"error": "unauthorized"},
                extra_headers=[(b"www-authenticate", _WWW_AUTHENTICATE.encode())],
            )
            return
        await manager.handle_request(scope, receive, send)

    async def health(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("maptalk-mcp ok\n")

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        async with manager.run():
            yield

    # The MCP endpoint is served at /mcp/ ; a request to /mcp is 307-redirected
    # there (307 preserves POST + body). The bearer gate lives inside the mount,
    # so it runs on every request that actually reaches the transport.
    return Starlette(
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Mount("/mcp", app=guarded_mcp),
        ],
        lifespan=lifespan,
    )
