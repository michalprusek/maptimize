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
    # A host that merely BEGINS with an allowed one. Prefix matching admits all
    # of these, and each is a working phishing target.
    "https://claude.ai.evil.example/steal",
    "https://chatgpt.com.evil.example/steal",
    "http://localhost.evil.example/steal",
    "http://127.0.0.1.evil.example/steal",
    # ⚠️ URL userinfo. "http://localhost:" LOOKS like it ends at a boundary, but
    # ":" opens the password field, not a port -- the real host here is
    # evil.example. Prefix matching cannot see that; only parsing can.
    "http://localhost:x@evil.example/cb",
    "http://127.0.0.1:x@evil.example/cb",
    "https://claude.ai@evil.example/cb",
    "https://user:pass@chatgpt.com.evil.example/cb",
    # Plain foreign hosts, wrong scheme, and non-strings.
    "https://evil.example/callback",
    "http://claude.ai/downgraded",
    "javascript:alert(1)",
    "",
    None,
    12345,
])
def test_everything_else_is_refused(uri):
    assert not oauth._redirect_allowed(uri)


def test_userinfo_is_refused_outright():
    """Credentials in a redirect URI are never legitimate here, and they are the
    one way an attacker makes an allowed name appear in the authority."""
    assert not oauth._redirect_allowed("https://claude.ai:pw@evil.example/cb")
    assert not oauth._redirect_allowed("https://claude.ai@claude.ai/cb")


def test_the_allowed_host_is_matched_exactly_not_as_a_prefix():
    """The property the cases above depend on, asserted on the mechanism.

    A subdomain of an allowed host is a different origin and is not admitted;
    neither is a host that merely starts with one.
    """
    for host in oauth.ALLOWED_REDIRECT_HOSTS:
        assert not oauth._redirect_allowed(f"https://{host}.evil.example/cb")
        assert not oauth._redirect_allowed(f"https://evil-{host}/cb")


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


# --- diagnosing a refusal ----------------------------------------------------

@pytest.mark.parametrize("uri,expect", [
    ("", "missing"),
    ("https://evil.example/cb", "not an allowed connector host"),
    ("http://claude.ai/cb", "must use https"),
    ("https://claude.ai:pw@evil.example/cb", "must not contain credentials"),
    ("https://localhost/cb", "must use http"),
    ("javascript:alert(1)", "no host"),
])
def test_a_refusal_says_which_rule_it_broke(uri, expect):
    """Five causes used to collapse into the same silent False, so an operator
    debugging "the connector will not connect" had one generic string and an
    empty log. The URI came from the client, so the reason is not a secret."""
    reason = oauth._redirect_refusal(uri)
    assert reason is not None and expect in reason, f"{uri!r} -> {reason!r}"


def test_an_admissible_uri_has_no_reason():
    assert oauth._redirect_refusal("https://chatgpt.com/cb") is None
    assert oauth._redirect_refusal("http://localhost:8765/cb") is None


async def test_registration_names_the_offending_uri(mock_db):
    """Registering several at once, one bad: say which, or the client retries blind."""
    request = SimpleNamespace(json=AsyncMock(return_value={
        "redirect_uris": ["https://chatgpt.com/ok", "https://evil.example/cb"],
    }))
    with pytest.raises(HTTPException) as exc:
        await oauth.register_client(request, db=mock_db)
    assert "https://evil.example/cb" in exc.value.detail, "the bad URI is not named"
    assert "https://chatgpt.com/ok" not in exc.value.detail, "the good one is blamed too"
