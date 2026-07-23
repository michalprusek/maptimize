"""Personal access token (PAT) management for the remote MCP connector.

Each user generates their own tokens here and pastes one into their Claude
Desktop custom connector. All endpoints require an interactive login (never a
PAT) — a PAT must not be able to mint or revoke PATs.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.mcp_token import MCPToken
from models.user import User
from schemas.mcp_token import (
    MCPTokenCreate,
    MCPTokenCreatedResponse,
    MCPTokenResponse,
)
from utils.security import require_interactive_user
from utils.tokens import generate_pat

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_ACTIVE_TOKENS = 10
CREATE_LIMIT_PER_HOUR = 5


@router.get("", response_model=list[MCPTokenResponse])
async def list_tokens(
    current_user: User = Depends(require_interactive_user),
    db: AsyncSession = Depends(get_db),
):
    """List the caller's tokens (never returns a secret)."""
    result = await db.execute(
        select(MCPToken)
        .where(MCPToken.user_id == current_user.id)
        .order_by(MCPToken.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=MCPTokenCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: MCPTokenCreate,
    current_user: User = Depends(require_interactive_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a token. The plaintext is returned exactly once."""
    from routers.rag import _check_rate_limit_generic

    await _check_rate_limit_generic(
        f"mcp_token_create:{current_user.id}", limit=CREATE_LIMIT_PER_HOUR, window=3600
    )

    active = await db.execute(
        select(func.count())
        .select_from(MCPToken)
        .where(MCPToken.user_id == current_user.id, MCPToken.revoked_at.is_(None))
    )
    if active.scalar_one() >= MAX_ACTIVE_TOKENS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You already have {MAX_ACTIVE_TOKENS} active tokens. Revoke one first.",
        )

    plaintext, token_hash, token_prefix = generate_pat()
    token = MCPToken(
        user_id=current_user.id,
        token_hash=token_hash,
        token_prefix=token_prefix,
        label=body.label.strip(),
    )
    db.add(token)
    await db.flush()
    await db.refresh(token)
    logger.info("User %s created MCP token %s (%s)", current_user.id, token.id, token_prefix)
    return MCPTokenCreatedResponse(
        id=token.id,
        label=token.label,
        token_prefix=token.token_prefix,
        created_at=token.created_at,
        token=plaintext,
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: int,
    current_user: User = Depends(require_interactive_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-revoke one of the caller's tokens (preserves the audit trail)."""
    result = await db.execute(
        select(MCPToken).where(
            MCPToken.id == token_id, MCPToken.user_id == current_user.id
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
    return None
