"""The redirect-URI allowlist, which is the whole anti-phishing story of DCR.

Registration is open: anyone may POST /oauth/register. What keeps that from
turning /authorize into a phishing oracle is that only a known vendor's callback
may be registered as a redirect target. An attacker who could register
``redirect_uri=attacker.example`` would phish a victim to the REAL authorize page
-- correct domain, correct TLS, correct login form -- and receive the code.

So the allowlist is a security boundary, and prefix matching is a sharp tool: a
prefix that does not end at a path boundary matches a *different host* whose name
merely starts with the allowed one.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from routers import oauth


@pytest.mark.parametrize("uri", [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "http://localhost:8765/callback",
    "http://127.0.0.1:33418/oauth/callback",
])
def test_known_vendor_and_loopback_callbacks_are_allowed(uri):
    assert oauth._redirect_allowed(uri)


@pytest.mark.parametrize("uri", [
    # A host that merely BEGINS with an allowed one. Without a path boundary in
    # the prefix these all match, and each is a working phishing target.
    "https://claude.ai.evil.example/steal",
    "https://chatgpt.com.evil.example/steal",
    "http://localhost.evil.example/steal",
    "http://127.0.0.1.evil.example/steal",
    # Plain foreign hosts and non-strings.
    "https://evil.example/callback",
    "http://claude.ai/downgraded",
    "",
    None,
    12345,
])
def test_everything_else_is_refused(uri):
    assert not oauth._redirect_allowed(uri)


def test_every_prefix_ends_at_a_path_boundary():
    """The property the cases above depend on, asserted directly.

    A prefix ending in the host name alone (``http://localhost``) matches
    ``http://localhost.evil.example``; one ending in ``/`` or ``:`` cannot.
    """
    for prefix in oauth.ALLOWED_REDIRECT_PREFIXES:
        assert prefix.endswith(("/", ":")), f"{prefix!r} does not end at a path boundary"


async def test_registering_a_foreign_callback_is_rejected(mock_db):
    request = SimpleNamespace(json=AsyncMock(return_value={
        "redirect_uris": ["https://evil.example/callback"],
        "client_name": "phish",
    }))
    with pytest.raises(HTTPException) as exc:
        await oauth.register_client(request, db=mock_db)
    assert exc.value.status_code == 400
    mock_db.add.assert_not_called()


async def test_one_bad_uri_poisons_the_whole_registration(mock_db):
    """Registering [good, bad] must fail rather than silently keeping the good
    one: the client would then hold a client_id it believes covers both."""
    request = SimpleNamespace(json=AsyncMock(return_value={
        "redirect_uris": [
            "https://chatgpt.com/connector_platform_oauth_redirect",
            "https://evil.example/callback",
        ],
    }))
    with pytest.raises(HTTPException) as exc:
        await oauth.register_client(request, db=mock_db)
    assert exc.value.status_code == 400
    mock_db.add.assert_not_called()


async def test_registering_the_chatgpt_callback_succeeds(mock_db):
    request = SimpleNamespace(json=AsyncMock(return_value={
        "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        "client_name": "ChatGPT",
    }))
    resp = await oauth.register_client(request, db=mock_db)
    assert resp.status_code == 201
    mock_db.add.assert_called_once()
    registered = mock_db.add.call_args.args[0]
    assert "chatgpt.com" in registered.redirect_uris
