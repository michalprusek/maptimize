"""Segmentation models for SAM-based cell segmentation."""
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .cell_crop import CellCrop
    from .user import User
    from .image import Image


class FOVSegmentationMask(Base):
    """
    Segmentation mask (polygon) for an entire FOV image.

    Stores the segmentation polygon for the whole field of view.
    Individual cell masks are then extracted as clips from this FOV mask
    based on the cell crop bounding boxes.
    """

    __tablename__ = "fov_segmentation_masks"

    id: Mapped[int] = mapped_column(primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"),
        unique=True,  # One mask per FOV image
        index=True
    )

    # Polygon stored as JSON array of [x, y] points
    # e.g., [[100, 150], [105, 152], [110, 155], ...]
    # Coordinates are in FOV image space
    polygon_points: Mapped[list] = mapped_column(JSON)

    # Mask area in pixels (for quality metrics)
    area_pixels: Mapped[int] = mapped_column(Integer)

    # IoU score from SAM prediction (confidence measure)
    iou_score: Mapped[float] = mapped_column(Float)

    # How the mask was created
    # "interactive" - via click prompts in editor
    # "auto" - automatically from detection
    # "imported" - from external source
    creation_method: Mapped[str] = mapped_column(
        String(20),
        default="interactive"
    )

    # Number of click prompts used to generate this mask
    prompt_count: Mapped[int] = mapped_column(Integer, default=0)

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

    # Relationship
    image: Mapped["Image"] = relationship(back_populates="fov_segmentation_mask")

    def __repr__(self) -> str:
        return f"<FOVSegmentationMask(id={self.id}, image_id={self.image_id}, iou={self.iou_score:.2f})>"


class SegmentationMask(Base):
    """
    Segmentation mask (polygon) for a cell crop.

    Stores the finalized polygon boundary created through interactive
    SAM segmentation. The polygon is stored as a JSON array of [x, y] points.
    """

    __tablename__ = "segmentation_masks"

    id: Mapped[int] = mapped_column(primary_key=True)
    cell_crop_id: Mapped[int] = mapped_column(
        ForeignKey("cell_crops.id", ondelete="CASCADE"),
        unique=True,  # One mask per crop
        index=True
    )

    # Polygon stored as JSON array of [x, y] points
    # e.g., [[100, 150], [105, 152], [110, 155], ...]
    # Coordinates are relative to the FOV image (not the crop)
    polygon_points: Mapped[list] = mapped_column(JSON)

    # Mask area in pixels (for quality metrics)
    area_pixels: Mapped[int] = mapped_column(Integer)

    # IoU score from SAM prediction (confidence measure)
    iou_score: Mapped[float] = mapped_column(Float)

    # How the mask was created
    # "interactive" - via click prompts in editor
    # "auto" - automatically from detection
    # "imported" - from external source
    creation_method: Mapped[str] = mapped_column(
        String(20),
        default="interactive"
    )

    # Number of click prompts used to generate this mask
    prompt_count: Mapped[int] = mapped_column(Integer, default=0)

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

    # Relationship
    cell_crop: Mapped["CellCrop"] = relationship(back_populates="segmentation_mask")

    def __repr__(self) -> str:
        return f"<SegmentationMask(id={self.id}, crop_id={self.cell_crop_id}, iou={self.iou_score:.2f})>"


class UserSegmentationPrompt(Base):
    """
    User's stored prompts for segmentation (exemplars for "training").

    SAM doesn't actually train per-user, but we can store user's click patterns
    that produced good segmentations. These can be:
    1. Used as reference/suggestions for new segmentations
    2. Analyzed to understand user preferences
    3. Potentially used for future automated segmentation

    Prompts can be scoped to an experiment or be global (experiment_id=null).
    """

    __tablename__ = "user_segmentation_prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    experiment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )  # If null, applies globally

    # Reference image and crop this prompt was created from
    source_image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE")
    )
    source_crop_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cell_crops.id", ondelete="SET NULL"),
        nullable=True
    )

    # Click points as JSON array
    # [{"x": 100, "y": 150, "label": 1}, {"x": 200, "y": 250, "label": 0}]
    # label: 1 = foreground (positive), 0 = background (negative)
    click_points: Mapped[list] = mapped_column(JSON)

    # The resulting polygon from these prompts (for preview/reference)
    result_polygon: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # User rating of this prompt's quality (optional, 1-5)
    quality_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Descriptive name/tag for this prompt set (optional)
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship()
    source_image: Mapped["Image"] = relationship()

    def __repr__(self) -> str:
        return f"<UserSegmentationPrompt(id={self.id}, user_id={self.user_id}, points={len(self.click_points)})>"
