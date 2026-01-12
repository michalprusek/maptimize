"""Metric models - User-defined metrics for pairwise ranking."""
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List

from sqlalchemy import Integer, Float, Boolean, String, Text, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from config import get_settings

if TYPE_CHECKING:
    from .user import User
    from .cell_crop import CellCrop

settings = get_settings()


class Metric(Base):
    """User-defined metric for pairwise ranking (e.g., bundleness, polarity)."""

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    user: Mapped["User"] = relationship(back_populates="metrics")
    images: Mapped[List["MetricImage"]] = relationship(
        back_populates="metric",
        cascade="all, delete-orphan"
    )
    ratings: Mapped[List["MetricRating"]] = relationship(
        back_populates="metric",
        cascade="all, delete-orphan"
    )
    comparisons: Mapped[List["MetricComparison"]] = relationship(
        back_populates="metric",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Metric(id={self.id}, name='{self.name}')>"


class MetricImage(Base):
    """Image associated with a metric for ranking."""

    __tablename__ = "metric_images"
    __table_args__ = (
        UniqueConstraint("metric_id", "cell_crop_id", name="uq_metric_cell_crop"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    metric_id: Mapped[int] = mapped_column(
        ForeignKey("metrics.id", ondelete="CASCADE"),
        index=True
    )

    # Can be either a CellCrop reference or a directly uploaded image
    cell_crop_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cell_crops.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # For directly uploaded images (not from experiments)
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Relationships
    metric: Mapped["Metric"] = relationship(back_populates="images")
    cell_crop: Mapped[Optional["CellCrop"]] = relationship()
    rating: Mapped[Optional["MetricRating"]] = relationship(
        back_populates="metric_image",
        uselist=False,
        cascade="all, delete-orphan"
    )

    @property
    def image_url(self) -> Optional[str]:
        """Get the image URL (either from cell_crop or direct file)."""
        if self.cell_crop:
            return self.cell_crop.mip_path
        return self.file_path

    def __repr__(self) -> str:
        return f"<MetricImage(id={self.id}, metric={self.metric_id})>"


class MetricRating(Base):
    """TrueSkill rating for an image within a metric."""

    __tablename__ = "metric_ratings"
    __table_args__ = (
        UniqueConstraint("metric_id", "metric_image_id", name="uq_metric_image_rating"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    metric_id: Mapped[int] = mapped_column(
        ForeignKey("metrics.id", ondelete="CASCADE"),
        index=True
    )
    metric_image_id: Mapped[int] = mapped_column(
        ForeignKey("metric_images.id", ondelete="CASCADE"),
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
    metric: Mapped["Metric"] = relationship(back_populates="ratings")
    metric_image: Mapped["MetricImage"] = relationship(back_populates="rating")

    @property
    def ordinal_score(self) -> float:
        """Conservative score (lower bound of confidence interval)."""
        return self.mu - 3 * self.sigma

    def __repr__(self) -> str:
        return f"<MetricRating(metric={self.metric_id}, image={self.metric_image_id}, mu={self.mu:.2f})>"


class MetricComparison(Base):
    """Pairwise comparison record for a metric."""

    __tablename__ = "metric_comparisons"

    id: Mapped[int] = mapped_column(primary_key=True)
    metric_id: Mapped[int] = mapped_column(
        ForeignKey("metrics.id", ondelete="CASCADE"),
        index=True
    )
    image_a_id: Mapped[int] = mapped_column(ForeignKey("metric_images.id"))
    image_b_id: Mapped[int] = mapped_column(ForeignKey("metric_images.id"))
    winner_id: Mapped[int] = mapped_column(ForeignKey("metric_images.id"))

    # Metadata
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Relationships
    metric: Mapped["Metric"] = relationship(back_populates="comparisons")

    def __repr__(self) -> str:
        return f"<MetricComparison(id={self.id}, metric={self.metric_id}, winner={self.winner_id})>"
