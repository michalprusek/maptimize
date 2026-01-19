"""Agent memory model for persistent user context.

This model stores long-term memories for the AI agent:
- User preferences and notes
- Key findings and insights
- Project context that survives between sessions
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import String, Text, DateTime, ForeignKey, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from ml.rag import QWEN_VL_EMBEDDING_DIM

if TYPE_CHECKING:
    from .user import User


class MemoryType(str, PyEnum):
    """Types of agent memories."""
    PREFERENCE = "preference"  # User preferences (e.g., "always show cell counts")
    NOTE = "note"  # User notes or observations
    FINDING = "finding"  # Key research findings
    CONTEXT = "context"  # Project context
    REMINDER = "reminder"  # Things to remember
    CUSTOM = "custom"  # User-defined


class AgentMemory(Base):
    """Long-term memory storage for the AI agent."""

    __tablename__ = "agent_memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )

    # Memory key for direct retrieval
    key: Mapped[str] = mapped_column(String(255), index=True)

    # Memory content
    value: Mapped[str] = mapped_column(Text)

    # Type of memory
    memory_type: Mapped[str] = mapped_column(
        String(50),
        default=MemoryType.NOTE.value
    )

    # Vector embedding for semantic search
    embedding: Mapped[Optional[list]] = mapped_column(
        Vector(QWEN_VL_EMBEDDING_DIM),
        nullable=True
    )

    # Metadata
    tags: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )  # Comma-separated tags

    # Access tracking
    access_count: Mapped[int] = mapped_column(default=0)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="agent_memories")

    __table_args__ = (
        # Unique key per user
        Index("ix_agent_memories_user_key", "user_id", "key", unique=True),
    )

    def __repr__(self) -> str:
        return f"<AgentMemory(id={self.id}, key={self.key!r}, type={self.memory_type})>"
