"""Schemas for MCP personal access tokens."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MCPTokenCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)


class MCPTokenResponse(BaseModel):
    """A token as listed back to its owner — never includes the secret."""
    id: int
    label: str
    token_prefix: str
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MCPTokenCreatedResponse(BaseModel):
    """Returned once, at creation time, carrying the one-time plaintext token."""
    id: int
    label: str
    token_prefix: str
    created_at: datetime
    token: str
