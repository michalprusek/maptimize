"""Bug report model for user feedback and issue tracking."""
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, Text, Enum, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .user import User


class BugReportStatus(str, PyEnum):
    """Status of a bug report."""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class BugReportCategory(str, PyEnum):
    """Category of a bug report."""
    BUG = "bug"
    FEATURE = "feature"
    OTHER = "other"


class BugReport(Base):
    """Bug report submitted by users."""
    __tablename__ = "bug_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Report content
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[BugReportCategory] = mapped_column(
        Enum(BugReportCategory),
        default=BugReportCategory.BUG
    )
    status: Mapped[BugReportStatus] = mapped_column(
        Enum(BugReportStatus),
        default=BugReportStatus.OPEN
    )

    # Auto-collected debug info
    browser_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    screen_resolution: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    user_settings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="bug_reports")

    def __repr__(self) -> str:
        return f"<BugReport(id={self.id}, category={self.category}, status={self.status})>"
