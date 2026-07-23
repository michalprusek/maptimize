"""Security utilities - password hashing and JWT."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status, Query, Request
from fastapi.security import OAuth2PasswordBearer
from typing import Optional
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models.user import User
from schemas.user import TokenPayload

logger = logging.getLogger(__name__)
settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(
        plain_password.encode('utf-8'),
        hashed_password.encode('utf-8')
    )


def create_access_token(
    user_id: int,
    role: str,
    expires_delta: Optional[timedelta] = None
) -> str:
    """Create a JWT access token."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_expire_minutes
        )

    to_encode = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
    }
    return jwt.encode(
        to_encode,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm
    )


def create_oauth_access_token(
    user_id: int, role: str, expires_delta: Optional[timedelta] = None
) -> str:
    """Access token issued via the OAuth flow. Carries kind='oauth' so it is
    treated as data-plane-only (like a PAT) — never usable for account-sensitive
    actions even though it is a normal signed JWT otherwise."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    to_encode = {"sub": str(user_id), "role": role, "exp": expire, "kind": "oauth"}
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[TokenPayload]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        return TokenPayload(
            sub=int(payload["sub"]),
            exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            role=payload["role"],
            kind=payload.get("kind"),
        )
    except ExpiredSignatureError:
        logger.debug("Token expired")
        return None
    except JWTClaimsError as e:
        logger.warning(f"Invalid token claims: {e}")
        return None
    except JWTError as e:
        logger.debug(f"JWT decode error: {type(e).__name__}: {e}")
        return None


async def _authenticate(
    token: str, db: AsyncSession, request: Optional[Request]
) -> User:
    """Resolve a bearer JWT to a User — either an interactive login or an OAuth
    connector token (kind='oauth'). Records ``request.state.principal_kind`` so
    guards can tell a connector token from an interactive login.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_token(token)
    if payload is None:
        raise credentials_exception
    if payload.exp < datetime.now(timezone.utc):
        raise credentials_exception
    result = await db.execute(select(User).where(User.id == payload.sub))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    if request is not None:
        request.state.principal_kind = "oauth" if payload.kind == "oauth" else "jwt"
    return user


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current authenticated user from a JWT or a PAT bearer."""
    return await _authenticate(token, db, request)


async def get_current_user_from_query(
    request: Request,
    token: str = Query(..., description="Access token (JWT or PAT)"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate via a ``?token=`` query param (JWT or PAT).

    Use this for endpoints that serve files/images where an Authorization header
    cannot be sent (e.g., <img src="...">, file downloads).
    """
    return await _authenticate(token, db, request)


async def require_interactive_user(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Reject requests authenticated with an OAuth connector token — for
    account-sensitive actions (password/email change, admin) that a connector
    must never perform.
    """
    if getattr(request.state, "principal_kind", None) == "oauth":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires an interactive login, not a connector token.",
        )
    return user


async def get_current_admin(
    current_user: User = Depends(require_interactive_user)
) -> User:
    """Require admin role (and an interactive login, never a PAT)."""
    if current_user.role.value != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user
