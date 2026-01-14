"""Image and MapProtein models."""
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Text, Enum, DateTime, Integer, ForeignKey, func, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .experiment import Experiment
    from .cell_crop import CellCrop


class UploadStatus(str, PyEnum):
    """Image upload/processing status."""
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"  # Phase 1 complete: projections created, awaiting processing
    PROCESSING = "PROCESSING"
    DETECTING = "DETECTING"
    EXTRACTING_FEATURES = "EXTRACTING_FEATURES"
    READY = "READY"
    ERROR = "ERROR"


class MapProtein(Base):
    """MAP (Microtubule-Associated Protein) reference."""

    __tablename__ = "map_proteins"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # Hex color for UI

    # Relationships
    images: Mapped[List["Image"]] = relationship(back_populates="map_protein")

    def __repr__(self) -> str:
        return f"<MapProtein(id={self.id}, name={self.name})>"


class Image(Base):
    """Microscopy image model."""

    __tablename__ = "images"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        index=True
    )
    map_protein_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("map_proteins.id"),
        nullable=True
    )

    # File info
    original_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))  # Local storage path
    mip_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sum_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # SUM projection path
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Processing options
    detect_cells: Mapped[bool] = mapped_column(Boolean, default=True)  # Whether to run YOLO detection
    source_discarded: Mapped[bool] = mapped_column(Boolean, default=False)  # Original file was deleted

    # Image metadata
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    z_slices: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # bytes

    # Processing status
    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus),
        default=UploadStatus.UPLOADING
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # Relationships
    experiment: Mapped["Experiment"] = relationship(back_populates="images")
    map_protein: Mapped[Optional["MapProtein"]] = relationship(back_populates="images")
    cell_crops: Mapped[List["CellCrop"]] = relationship(
        back_populates="image",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Image(id={self.id}, filename={self.original_filename})>"
