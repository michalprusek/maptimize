"""Chat models for RAG-powered conversation threads."""
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import String, Text, Float, Integer, ForeignKey, DateTime, func, JSON, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .user import User


class ChatRole(str, PyEnum):
    """Chat message roles."""
    USER = "user"
    ASSISTANT = "assistant"


class GenerationStatus(str, PyEnum):
    """Status of AI response generation."""
    IDLE = "idle"
    GENERATING = "generating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ChatThread(Base):
    """Chat conversation thread with a user."""

    __tablename__ = "chat_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    name: Mapped[str] = mapped_column(String(255), default="New Chat")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

    # Generation status for async message processing
    generation_status: Mapped[str] = mapped_column(
        String(20), default="idle"
    )
    generation_task_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    generation_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    generation_error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="chat_threads")
    messages: Mapped[List["ChatMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at"
    )

    def __repr__(self) -> str:
        return f"<ChatThread(id={self.id}, name={self.name!r})>"


class ChatMessage(Base):
    """Individual message in a chat thread."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        index=True
    )
    # Role is validated against ChatRole enum values
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)

    # Citations and references (flexible JSONB storage)
    # Format: [{"type": "document", "doc_id": 1, "page": 5}, {"type": "fov", "image_id": 42}]
    # Type annotation: list (not dict) to match actual usage
    citations: Mapped[Optional[list]] = mapped_column(JSONB, default=list)

    # Image references for multimodal responses
    # Format: [{"path": "/uploads/...", "caption": "..."}]
    image_refs: Mapped[Optional[list]] = mapped_column(JSONB, default=list)

    # Tool calls made by the assistant
    # Format: [{"tool": "search_documents", "args": {...}, "result": {...}}]
    tool_calls: Mapped[Optional[list]] = mapped_column(JSONB, default=list)

    # Gemini Interactions API interaction ID for server-side state management
    # Only stored for assistant messages; used as previous_interaction_id for next message
    interaction_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    __table_args__ = (
        # Role must be 'user' or 'assistant'
        CheckConstraint("role IN ('user', 'assistant')", name='check_role_valid'),
        # Content cannot be empty
        CheckConstraint("content <> ''", name='check_content_not_empty'),
    )

    # Relationships
    thread: Mapped["ChatThread"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        content_preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"<ChatMessage(id={self.id}, role={self.role}, content={content_preview!r})>"
