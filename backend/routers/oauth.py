"""OAuth 2.0 authorization server for MCP remote connectors (Claude Desktop / Cowork).

Authorization-code flow with PKCE (S256). Users authenticate with their maptimize
account on the /authorize page; Claude receives a short-lived access token
(kind='oauth', so it is data-plane-only like a PAT) plus a refresh token.

These endpoints live at the domain ROOT (not under /api) because that is where MCP
clients look for the /.well-known/oauth-* metadata and the /authorize + /token
endpoints (mounted in main.py without the /api prefix).
"""
import base64
import hashlib
import html
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models.oauth_client import OAuthClient
from models.user import User
from utils.security import create_oauth_access_token, verify_password

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

ISSUER = os.environ.get("OAUTH_ISSUER", "https://maptimize.utia.cas.cz")
RESOURCE = f"{ISSUER}/mcp/"
CODE_TTL_SECONDS = 300
REFRESH_TTL_DAYS = 30


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


# ---- discovery metadata ----------------------------------------------------


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/oauth/authorize",
        "token_endpoint": f"{ISSUER}/oauth/token",
        "registration_endpoint": f"{ISSUER}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": ["mcp"],
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata():
    return {"resource": RESOURCE, "authorization_servers": [ISSUER]}


# ---- dynamic client registration -------------------------------------------


@router.post("/oauth/register")
async def register_client(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise HTTPException(status_code=400, detail="redirect_uris is required")
    client_id = "mcp-" + secrets.token_urlsafe(16)
    db.add(OAuthClient(
        client_id=client_id,
        redirect_uris=json.dumps(redirect_uris),
        client_name=str(body.get("client_name") or "")[:200],
    ))
    await db.flush()
    return JSONResponse(status_code=201, content={
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": body.get("client_name", ""),
    })


async def _client_allows(db: AsyncSession, client_id: str, redirect_uri: str) -> bool:
    row = (await db.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )).scalar_one_or_none()
    if row is None:
        return False
    try:
        return redirect_uri in json.loads(row.redirect_uris)
    except Exception:
        return False


# ---- authorization endpoint (login + consent) ------------------------------


def _login_page(client_id, redirect_uri, code_challenge, state, scope, error="") -> str:
    f = html.escape
    err = f'<p class="err">{f(error)}</p>' if error else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect to maptimize</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;
   min-height:100vh;align-items:center;justify-content:center;margin:0}}
 .card{{background:#1e293b;padding:2rem;border-radius:12px;width:340px;box-shadow:0 10px 30px rgba(0,0,0,.4)}}
 h1{{font-size:1.15rem;margin:0 0 .25rem}} p.sub{{color:#94a3b8;font-size:.85rem;margin:0 0 1.25rem}}
 label{{display:block;font-size:.8rem;margin:.75rem 0 .25rem;color:#cbd5e1}}
 input{{width:100%;box-sizing:border-box;padding:.6rem;border-radius:8px;border:1px solid #334155;
   background:#0f172a;color:#e2e8f0}}
 button{{width:100%;margin-top:1.25rem;padding:.7rem;border:0;border-radius:8px;background:#6366f1;
   color:#fff;font-weight:600;cursor:pointer}}
 .err{{color:#f87171;font-size:.8rem;margin:.5rem 0 0}}
</style></head><body><div class="card">
 <h1>Connect Claude to maptimize</h1>
 <p class="sub">Sign in to authorize the AI connector to access your documents.</p>
 {err}
 <form method="post" action="/oauth/authorize">
  <input type="hidden" name="client_id" value="{f(client_id)}">
  <input type="hidden" name="redirect_uri" value="{f(redirect_uri)}">
  <input type="hidden" name="code_challenge" value="{f(code_challenge)}">
  <input type="hidden" name="state" value="{f(state)}">
  <input type="hidden" name="scope" value="{f(scope)}">
  <label>Email</label><input name="email" type="email" autocomplete="username" required autofocus>
  <label>Password</label><input name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Authorize</button>
 </form></div></body></html>"""


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize(
    db: AsyncSession = Depends(get_db),
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    state: str = "",
    scope: str = "",
):
    if response_type != "code":
        raise HTTPException(status_code=400, detail="unsupported_response_type")
    if code_challenge_method != "S256" or not code_challenge:
        raise HTTPException(status_code=400, detail="PKCE (S256) required")
    if not await _client_allows(db, client_id, redirect_uri):
        raise HTTPException(status_code=400, detail="invalid client_id or redirect_uri")
    return HTMLResponse(_login_page(client_id, redirect_uri, code_challenge, state, scope))


@router.post("/oauth/authorize", response_class=HTMLResponse)
async def authorize_submit(
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    code_challenge: str = Form(...),
    state: str = Form(""),
    scope: str = Form(""),
):
    if not await _client_allows(db, client_id, redirect_uri):
        raise HTTPException(status_code=400, detail="invalid client")
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return HTMLResponse(
            _login_page(client_id, redirect_uri, code_challenge, state, scope,
                        error="Incorrect email or password"),
            status_code=401,
        )
    code = jwt.encode(
        {
            "sub": str(user.id),
            "cid": client_id,
            "ruri": redirect_uri,
            "cc": code_challenge,
            "typ": "code",
            "jti": secrets.token_hex(8),
            "exp": datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}", status_code=302
    )


# ---- token endpoint --------------------------------------------------------


async def _consume_code_jti(jti: str) -> bool:
    """Single-use enforcement for authorization codes. Returns True the first
    time a jti is seen, False on replay. Fail-open if Redis is unavailable."""
    url = os.environ.get("REDIS_URL")
    if not url:
        return True
    try:
        import redis.asyncio as redis

        r = redis.from_url(url, decode_responses=True)
        ok = await r.set(f"oauth_code:{jti}", "1", nx=True, ex=CODE_TTL_SECONDS)
        await r.aclose()
        return bool(ok)
    except Exception:
        logger.warning("OAuth code single-use check failed (Redis) — allowing", exc_info=True)
        return True


def _token_response(user: User, client_id) -> JSONResponse:
    access = create_oauth_access_token(user.id, user.role.value)
    refresh = jwt.encode(
        {
            "sub": str(user.id),
            "cid": client_id,
            "typ": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return JSONResponse({
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
        "refresh_token": refresh,
        "scope": "mcp",
    })


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=400, detail="invalid_grant")


@router.post("/oauth/token")
async def token(
    db: AsyncSession = Depends(get_db),
    grant_type: str = Form(...),
    code: str = Form(None),
    redirect_uri: str = Form(None),
    client_id: str = Form(None),
    code_verifier: str = Form(None),
    refresh_token: str = Form(None),
):
    if grant_type == "authorization_code":
        payload = _decode(code or "")
        if payload.get("typ") != "code":
            raise HTTPException(status_code=400, detail="invalid_grant")
        if payload.get("ruri") != redirect_uri:
            raise HTTPException(status_code=400, detail="invalid_grant (redirect_uri)")
        if client_id and payload.get("cid") != client_id:
            raise HTTPException(status_code=400, detail="invalid_grant (client_id)")
        expected = _b64url(hashlib.sha256((code_verifier or "").encode()).digest())
        if not code_verifier or expected != payload.get("cc"):
            raise HTTPException(status_code=400, detail="invalid_grant (PKCE)")
        if not await _consume_code_jti(payload.get("jti", "")):
            raise HTTPException(status_code=400, detail="invalid_grant (code already used)")
        user = await db.get(User, int(payload["sub"]))
        if user is None:
            raise HTTPException(status_code=400, detail="invalid_grant")
        return _token_response(user, payload.get("cid"))

    if grant_type == "refresh_token":
        payload = _decode(refresh_token or "")
        if payload.get("typ") != "refresh":
            raise HTTPException(status_code=400, detail="invalid_grant")
        user = await db.get(User, int(payload["sub"]))
        if user is None:
            raise HTTPException(status_code=400, detail="invalid_grant")
        return _token_response(user, payload.get("cid"))

    raise HTTPException(status_code=400, detail="unsupported_grant_type")
