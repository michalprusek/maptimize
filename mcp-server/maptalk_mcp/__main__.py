"""Entry point. Serves the maptalk MCP server over stdio or streamable HTTP.

- ``--transport stdio`` (default): a local server for Claude Code / Claude
  Desktop. Whether the backend is local or remote is decided by
  ``MAPTALK_BASE_URL``.
- ``--transport http``: a hosted remote connector (Streamable HTTP). A public,
  Anthropic-cloud-reachable endpoint; each caller authenticates with their own
  personal access token, validated per request against the backend.
"""
from __future__ import annotations

import argparse
import sys

import anyio

from .client import MaptalkClient
from .config import Config
from .registry import ToolRegistry
from .server import build_server


async def _serve_stdio(server) -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(prog="maptalk-mcp")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio (local, default) or http (hosted remote connector).",
    )
    args = parser.parse_args()

    config = Config.from_env()
    client = MaptalkClient(config)
    registry = ToolRegistry(config.tools_file, client)

    if args.transport == "stdio":
        anyio.run(_serve_stdio, build_server(registry))
        return

    # http: each caller authenticates with their own PAT (validated per request
    # against the backend), so no shared MCP_AUTH_TOKEN / service login is needed.
    import uvicorn

    from .http_app import build_http_app

    app = build_http_app(
        registry,
        allowed_hosts=config.allowed_hosts,
        stateless=config.stateless,
        json_response=config.json_response,
    )
    print(
        f"[maptalk-mcp] serving HTTP on {config.http_host}:{config.http_port} "
        f"(backend={config.base_url})",
        file=sys.stderr,
    )
    uvicorn.run(app, host=config.http_host, port=config.http_port, log_level="info")


if __name__ == "__main__":
    main()
