"""Image schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator

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


class CellCropGalleryResponse(BaseModel):
    """Cell crop for gallery display with parent image info."""
    id: int
    image_id: int
    parent_filename: str
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    bundleness_score: Optional[float] = None
    detection_confidence: Optional[float] = None
    excluded: bool = False
    created_at: datetime
    map_protein_name: Optional[str] = None
    map_protein_color: Optional[str] = None

    class Config:
        from_attributes = True


class ImageResponse(BaseModel):
    """Schema for image response."""
    id: int
    experiment_id: int
    original_filename: str
    status: UploadStatus
    width: Optional[int] = Field(None, gt=0)
    height: Optional[int] = Field(None, gt=0)
    z_slices: Optional[int] = Field(None, gt=0)
    file_size: Optional[int] = Field(None, gt=0)
    error_message: Optional[str] = None
    detect_cells: bool = True  # Whether detection was requested
    source_discarded: bool = False  # Original file was deleted
    created_at: datetime
    processed_at: Optional[datetime] = None
    map_protein: Optional[MapProteinResponse] = None
    cell_count: int = Field(default=0, ge=0)

    class Config:
        from_attributes = True


class ImageDetailResponse(ImageResponse):
    """Schema for detailed image response with cells."""
    cell_crops: List[CellCropSummary] = []
    mip_url: Optional[str] = None
    sum_url: Optional[str] = None  # SUM projection URL
    thumbnail_url: Optional[str] = None


class BatchProcessRequest(BaseModel):
    """Request schema for batch processing images."""
    image_ids: List[int] = Field(..., min_length=1, max_length=1000, description="List of image IDs to process")
    detect_cells: bool = Field(True, description="Whether to run YOLO detection")
    map_protein_id: Optional[int] = Field(None, description="Optional MAP protein to assign to all images")

    @field_validator("image_ids")
    @classmethod
    def unique_image_ids(cls, v: List[int]) -> List[int]:
        """Ensure image IDs are unique to prevent duplicate processing."""
        unique_ids = list(dict.fromkeys(v))  # Preserves order while removing duplicates
        if len(unique_ids) != len(v):
            # Return unique IDs instead of raising error for better UX
            return unique_ids
        return v


class BatchProcessResponse(BaseModel):
    """Response schema for batch processing."""
    processing_count: int = Field(..., ge=0, description="Number of images queued for processing")
    message: str = Field(..., min_length=1, description="Status message")


class FOVResponse(BaseModel):
    """FOV (Field of View) response - represents an uploaded image for FOV gallery."""
    id: int
    experiment_id: int
    original_filename: str
    status: UploadStatus
    width: Optional[int] = Field(None, gt=0)
    height: Optional[int] = Field(None, gt=0)
    z_slices: Optional[int] = Field(None, gt=0)
    file_size: Optional[int] = Field(None, gt=0)
    detect_cells: bool = False
    thumbnail_url: Optional[str] = None
    cell_count: int = Field(default=0, ge=0)
    map_protein: Optional[MapProteinResponse] = None
    created_at: datetime
    processed_at: Optional[datetime] = None

    class Config:
        from_attributes = True
