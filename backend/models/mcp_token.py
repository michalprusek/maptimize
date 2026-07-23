"""Personal access tokens for the remote MCP connector.

Each row is one revocable token belonging to a user. Only the sha256 hash is
stored (see utils/tokens.py). A new table like this is created automatically by
``Base.metadata.create_all`` — no ensure_schema_updates entry is needed.
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .user import User


class MCPToken(Base):
    __tablename__ = "mcp_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="mcp_tokens")

    def __repr__(self) -> str:
        return f"<MCPToken(id={self.id}, user_id={self.user_id}, prefix={self.token_prefix})>"
