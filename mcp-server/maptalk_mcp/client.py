"""Authenticated HTTP client for the maptalk / maptimize backend.

The backend has no API-key mechanism — only user login (``POST /api/auth/login``)
that returns a 24h JWT. This client logs in with credentials from the
environment, caches the bearer, and transparently re-authenticates once on a
401 (expired / revoked token). Because it calls the backend *as a user*, all of
maptimize's document ACL (``document_scope``) applies automatically — the MCP
client sees exactly what that user sees in the UI, nothing more.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import Config


class MaptalkAuthError(RuntimeError):
    """Login failed or no way to authenticate."""


class MaptalkAPIError(RuntimeError):
    """The backend returned a non-2xx response."""


class MaptalkClient:
    def __init__(self, config: Config, http: httpx.AsyncClient | None = None):
        self._config = config
        self._http = http or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout,
            verify=config.verify_tls,
            follow_redirects=True,
        )
        self._token: str | None = config.token
        self._login_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- auth ---------------------------------------------------------------

    async def _login(self) -> str:
        if not self._config.has_credentials:
            raise MaptalkAuthError(
                "No MAPTALK_TOKEN and no MAPTALK_EMAIL/MAPTALK_PASSWORD were "
                "provided, so the server cannot authenticate to the backend."
            )
        # OAuth2 password flow: form-encoded, username=email.
        resp = await self._http.post(
            "/api/auth/login",
            data={"username": self._config.email, "password": self._config.password},
        )
        if resp.status_code == 401:
            raise MaptalkAuthError("Login rejected: incorrect email or password.")
        if resp.status_code >= 400:
            raise MaptalkAuthError(
                f"Login failed ({resp.status_code}): {resp.text[:200]}"
            )
        token = resp.json().get("access_token")
        if not token:
            raise MaptalkAuthError("Login response contained no access_token.")
        return token

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        async with self._login_lock:
            if not self._token:
                self._token = await self._login()
        return self._token

    async def _reauthenticate(self) -> str:
        async with self._login_lock:
            self._token = None  # force a fresh login even under concurrency
            self._token = await self._login()
        return self._token

    # -- requests -----------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth: str = "header",
        token: str | None = None,
        json: Any | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Send an authenticated request.

        ``token`` (HTTP transport): act AS the caller's per-request token (their
        personal access token). No login and no re-auth loop — a 401 means the
        token is invalid/revoked and is surfaced as-is.

        Without ``token`` (stdio): use the cached service login and re-auth once
        on 401.

        ``auth="query"`` sends the token as ``?token=`` instead of an
        Authorization header — required by the page/pdf/passage image routes,
        which authenticate via ``get_current_user_from_query``. ``json`` /
        ``data`` / ``files`` carry a request body for POST tools.
        """
        clean = {k: v for k, v in (params or {}).items() if v is not None}

        async def send(tok: str) -> httpx.Response:
            return await self._send(method, path, clean, tok, auth, json, data, files)

        if token is not None:
            resp = await send(token)
        else:
            resp = await send(await self._ensure_token())
            if resp.status_code == 401 and self._config.has_credentials:
                resp = await send(await self._reauthenticate())
        if resp.status_code >= 400:
            raise MaptalkAPIError(
                f"{method} {path} -> {resp.status_code}: {resp.text[:300]}"
            )
        return resp

    async def validate_token(self, token: str) -> bool:
        """Return True if the token authenticates against the backend (GET /api/auth/me)."""
        resp = await self._send("GET", "/api/auth/me", {}, token, "header")
        return resp.status_code == 200

    async def post_multipart(
        self, path: str, files: dict, data: dict | None = None, token: str | None = None
    ) -> Any:
        resp = await self.request("POST", path, files=files, data=data, token=token)
        return resp.json()

    async def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
        token: str,
        auth: str,
        json: Any | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> httpx.Response:
        query = dict(params)
        headers: dict[str, str] = {}
        if auth == "query":
            query["token"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
        return await self._http.request(
            method, path, params=query, headers=headers, json=json, data=data, files=files
        )

    async def get_json(
        self, path: str, params: dict[str, Any] | None = None, token: str | None = None
    ) -> Any:
        resp = await self.request("GET", path, params=params, token=token)
        return resp.json()

    async def get_bytes(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        auth: str = "header",
        token: str | None = None,
    ) -> tuple[bytes, str]:
        resp = await self.request("GET", path, params=params, auth=auth, token=token)
        mime = resp.headers.get("content-type", "application/octet-stream").split(";")[0]
        return resp.content, mime
