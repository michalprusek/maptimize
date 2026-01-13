"""Ranking models - TrueSkill ratings and comparisons."""
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Integer, Float, Boolean, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from config import get_settings

if TYPE_CHECKING:
    from .user import User
    from .cell_crop import CellCrop

settings = get_settings()


class UserRating(Base):
    """Per-user rating for a cell crop (TrueSkill)."""

    __tablename__ = "user_ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "cell_crop_id", name="uq_user_cell_rating"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    cell_crop_id: Mapped[int] = mapped_column(
        ForeignKey("cell_crops.id", ondelete="CASCADE"),
        index=True
    )

    # TrueSkill parameters
    mu: Mapped[float] = mapped_column(Float, default=settings.initial_mu)
    sigma: Mapped[float] = mapped_column(Float, default=settings.initial_sigma)
    comparison_count: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
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
    user: Mapped["User"] = relationship(back_populates="ratings")
    cell_crop: Mapped["CellCrop"] = relationship(back_populates="ratings")

    @property
    def ordinal_score(self) -> float:
        """Conservative score (lower bound of confidence interval)."""
        return self.mu - 3 * self.sigma

    def __repr__(self) -> str:
        return f"<UserRating(user={self.user_id}, crop={self.cell_crop_id}, mu={self.mu:.2f})>"


class Comparison(Base):
    """Pairwise comparison record."""

    __tablename__ = "comparisons"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    crop_a_id: Mapped[int] = mapped_column(ForeignKey("cell_crops.id"))
    crop_b_id: Mapped[int] = mapped_column(ForeignKey("cell_crops.id"))
    winner_id: Mapped[int] = mapped_column(ForeignKey("cell_crops.id"))

    # Previous rating values (for undo support)
    prev_winner_mu: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prev_winner_sigma: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prev_loser_mu: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prev_loser_sigma: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Metadata
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="comparisons")

    def __repr__(self) -> str:
        return f"<Comparison(id={self.id}, winner={self.winner_id})>"


class RankingSource(Base):
    """Links experiments to user's ranking session (import tracking)."""

    __tablename__ = "ranking_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "experiment_id", name="uq_user_experiment_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        index=True
    )
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<RankingSource(user={self.user_id}, exp={self.experiment_id})>"
