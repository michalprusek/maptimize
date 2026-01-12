"""Image schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field

from models.image import UploadStatus


class MapProteinCreate(BaseModel):
    """Schema for creating a MAP protein."""
    name: str = Field(..., min_length=1, max_length=100)
    full_name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class MapProteinResponse(BaseModel):
    """Schema for MAP protein response."""
    id: int
    name: str
    full_name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None

    class Config:
        from_attributes = True


class CellCropSummary(BaseModel):
    """Brief cell crop info."""
    id: int
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    bundleness_score: Optional[float] = None
    sum_crop_path: Optional[str] = None  # SUM projection crop path
    excluded: bool = False

    class Config:
        from_attributes = True


class ImageResponse(BaseModel):
    """Schema for image response."""
    id: int
    experiment_id: int
    original_filename: str
    status: UploadStatus
    width: Optional[int] = None
    height: Optional[int] = None
    z_slices: Optional[int] = None
    file_size: Optional[int] = None
    error_message: Optional[str] = None
    detect_cells: bool = True  # Whether detection was requested
    source_discarded: bool = False  # Original file was deleted
    created_at: datetime
    processed_at: Optional[datetime] = None
    map_protein: Optional[MapProteinResponse] = None
    cell_count: int = 0

    class Config:
        from_attributes = True


class ImageDetailResponse(ImageResponse):
    """Schema for detailed image response with cells."""
    cell_crops: List[CellCropSummary] = []
    mip_url: Optional[str] = None
    sum_url: Optional[str] = None  # SUM projection URL
    thumbnail_url: Optional[str] = None
