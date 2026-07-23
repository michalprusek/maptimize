"""Runtime configuration, read entirely from environment variables.

Kept deliberately dependency-free (no pydantic) so the server starts fast and is
trivial to reason about from an ``.mcp.json`` / ``claude_desktop_config.json``
``env`` block.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:7001"
DEFAULT_TOOLS_FILE = str(Path(__file__).parent / "tools.yaml")

_FALSEY = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSEY


@dataclass(frozen=True)
class Config:
    """Everything the server needs to talk to a maptalk backend."""

    base_url: str
    email: str | None
    password: str | None
    token: str | None
    verify_tls: bool
    timeout: float
    tools_file: str
    # HTTP transport (remote connector) settings — unused by the stdio path.
    auth_token: str | None = None
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    allowed_hosts: list[str] | None = None
    stateless: bool = True
    json_response: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        hosts = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
        return cls(
            base_url=os.environ.get("MAPTALK_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            email=os.environ.get("MAPTALK_EMAIL") or None,
            password=os.environ.get("MAPTALK_PASSWORD") or None,
            token=os.environ.get("MAPTALK_TOKEN") or None,
            verify_tls=_env_bool("MAPTALK_VERIFY_TLS", True),
            timeout=float(os.environ.get("MAPTALK_TIMEOUT", "60")),
            tools_file=os.environ.get("MAPTALK_TOOLS_FILE") or DEFAULT_TOOLS_FILE,
            auth_token=os.environ.get("MCP_AUTH_TOKEN") or None,
            http_host=os.environ.get("MCP_HTTP_HOST", "0.0.0.0"),
            http_port=int(os.environ.get("MCP_HTTP_PORT", "8080")),
            allowed_hosts=[h.strip() for h in hosts.split(",") if h.strip()] or None,
            stateless=_env_bool("MCP_STATELESS", True),
            json_response=_env_bool("MCP_JSON_RESPONSE", False),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.email and self.password)
