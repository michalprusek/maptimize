"""Pending membership requests.

Joining a group is a request that a group admin approves; self-service join was
deleted when this model landed, because it admitted anyone who could name a group
id. One row per (group, user): re-requesting after a rejection flips the same row
back to ``pending`` rather than piling up history, which keeps "does this user
have a request outstanding" a single-row lookup.
"""
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class JoinRequestStatus(str, PyEnum):
    """Lifecycle of a join request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class GroupJoinRequest(Base):
    """A user's request to join a group, and the admin's decision on it."""

    __tablename__ = "group_join_requests"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_join_request_group_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=JoinRequestStatus.PENDING.value, index=True
    )
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # SET NULL rather than CASCADE: deleting the admin's account must not erase the
    # record that a membership was approved.
    decided_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<GroupJoinRequest(id={self.id}, group={self.group_id}, "
            f"user={self.user_id}, status={self.status})>"
        )
