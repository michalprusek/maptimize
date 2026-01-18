"""Experiment schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field

from models.experiment import ExperimentStatus
from schemas.image import MapProteinResponse


class ExperimentCreate(BaseModel):
    """Schema for creating an experiment."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    map_protein_id: Optional[int] = None
    fasta_sequence: Optional[str] = None


class ExperimentUpdate(BaseModel):
    """Schema for updating an experiment."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[ExperimentStatus] = None
    fasta_sequence: Optional[str] = None


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
    map_protein: Optional[MapProteinResponse] = None
    fasta_sequence: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    image_count: int = 0
    cell_count: int = 0
    has_sum_projections: bool = False

    class Config:
        from_attributes = True


class ExperimentDetailResponse(ExperimentResponse):
    """Schema for detailed experiment response with images."""
    images: List[ImageSummary] = []
