"""Cell crop model - detected cells from images."""
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .image import Image
    from .ranking import UserRating


class CellCrop(Base):
    """Detected cell crop from a microscopy image."""

    __tablename__ = "cell_crops"

    id: Mapped[int] = mapped_column(primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"),
        index=True
    )

    # Bounding box
    bbox_x: Mapped[int] = mapped_column(Integer)
    bbox_y: Mapped[int] = mapped_column(Integer)
    bbox_w: Mapped[int] = mapped_column(Integer)
    bbox_h: Mapped[int] = mapped_column(Integer)
    detection_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Stored crop files
    mip_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sum_crop_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # SUM projection crop
    std_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Computed metrics
    bundleness_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mean_intensity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    skewness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    kurtosis: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Status
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Relationships
    image: Mapped["Image"] = relationship(back_populates="cell_crops")
    ratings: Mapped[List["UserRating"]] = relationship(
        back_populates="cell_crop",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<CellCrop(id={self.id}, image_id={self.image_id})>"
