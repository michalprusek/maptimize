"""Auth + request behaviour of MaptalkClient."""
from __future__ import annotations

import urllib.parse

import httpx
import pytest

from maptalk_mcp.client import MaptalkAuthError


async def test_login_then_authorized_request(make_client):
    calls = {"login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            calls["login"] += 1
            form = urllib.parse.parse_qs(request.content.decode())
            assert form["username"] == ["svc@lab"]
            assert form["password"] == ["pw"]
            return httpx.Response(200, json={"access_token": "TOK", "user": {}})
        if request.url.path == "/api/rag/documents":
            assert request.headers["Authorization"] == "Bearer TOK"
            return httpx.Response(200, json=[{"id": 1}])
        return httpx.Response(404)

    client = make_client(handler)
    data = await client.get_json("/api/rag/documents")
    assert data[0]["id"] == 1
    assert calls["login"] == 1  # token cached: only one login
    await client.get_json("/api/rag/documents")
    assert calls["login"] == 1
    await client.aclose()


async def test_reauthenticates_once_on_401(make_client):
    state = {"login": 0, "hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            state["login"] += 1
            return httpx.Response(200, json={"access_token": f"TOK{state['login']}"})
        if request.url.path == "/api/rag/documents":
            state["hits"] += 1
            if state["hits"] == 1:  # first authorized call: token expired
                return httpx.Response(401, json={"detail": "expired"})
            assert request.headers["Authorization"] == "Bearer TOK2"
            return httpx.Response(200, json=[{"id": 2}])
        return httpx.Response(404)

    client = make_client(handler)
    data = await client.get_json("/api/rag/documents")
    assert data[0]["id"] == 2
    assert state["login"] == 2  # logged in, then re-logged in after the 401
    await client.aclose()


async def test_query_auth_puts_token_in_querystring(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "QT"})
        if request.url.path.endswith("/image"):
            assert request.url.params["token"] == "QT"
            assert "Authorization" not in request.headers
            return httpx.Response(200, content=b"img", headers={"content-type": "image/webp"})
        return httpx.Response(404)

    client = make_client(handler)
    content, mime = await client.get_bytes("/api/rag/documents/1/pages/1/image", auth="query")
    assert content == b"img"
    assert mime == "image/webp"
    await client.aclose()


async def test_missing_credentials_raises(make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = make_client(handler, email=None, password=None)
    with pytest.raises(MaptalkAuthError):
        await client.get_json("/api/rag/documents")
    await client.aclose()
