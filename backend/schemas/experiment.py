"""Experiment schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field

from models.experiment import ExperimentStatus


class ExperimentCreate(BaseModel):
    """Schema for creating an experiment."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class ExperimentUpdate(BaseModel):
    """Schema for updating an experiment."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[ExperimentStatus] = None


class ImageSummary(BaseModel):
    """Brief image info for experiment list."""
    id: int
    original_filename: str
    status: str
    thumbnail_path: Optional[str] = None

    class Config:
        from_attributes = True


class ExperimentResponse(BaseModel):
    """Schema for experiment response."""
    id: int
    name: str
    description: Optional[str] = None
    status: ExperimentStatus
    created_at: datetime
    updated_at: datetime
    image_count: int = 0
    cell_count: int = 0

    class Config:
        from_attributes = True


class ExperimentDetailResponse(ExperimentResponse):
    """Schema for detailed experiment response with images."""
    images: List[ImageSummary] = []
