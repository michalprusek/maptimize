"""User model."""
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Enum, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .experiment import Experiment
    from .ranking import UserRating, Comparison
    from .metric import Metric
    from .user_settings import UserSettings
    from .bug_report import BugReport
    from .chat import ChatThread
    from .rag_document import RAGDocument
    from .agent_memory import AgentMemory


class UserRole(str, PyEnum):
    """User roles."""
    VIEWER = "viewer"
    RESEARCHER = "researcher"
    ADMIN = "admin"


class User(Base):
    """User model for authentication and authorization."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole),
        default=UserRole.RESEARCHER
    )
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # Relationships
    experiments: Mapped[List["Experiment"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
    ratings: Mapped[List["UserRating"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
    comparisons: Mapped[List["Comparison"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
    metrics: Mapped[List["Metric"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
    settings: Mapped[Optional["UserSettings"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False
    )
    bug_reports: Mapped[List["BugReport"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
    chat_threads: Mapped[List["ChatThread"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
    rag_documents: Mapped[List["RAGDocument"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )
    agent_memories: Mapped[List["AgentMemory"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email})>"
