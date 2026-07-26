"""Experiment schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict, Field

from models.experiment import ExperimentStatus
from schemas.image import MapProteinResponse
from schemas.microscope import MicroscopeResponse


class ExperimentCreate(BaseModel):
    """Schema for creating an experiment."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    map_protein_id: Optional[int] = None
    microscope_id: Optional[int] = None
    fasta_sequence: Optional[str] = None


class ExperimentUpdate(BaseModel):
    """Schema for updating an experiment.

    No `microscope_id` on purpose: the microscope is assigned through
    `PATCH /experiments/{id}/microscope`, which any group member may call.
    Accepting it here too would give one field two endpoints with two different
    ACLs -- and the wider one would be reachable by mistake.
    """
    model_config = ConfigDict(extra="forbid")

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
    group_id: Optional[int] = None
    map_protein: Optional[MapProteinResponse] = None
    microscope: Optional[MicroscopeResponse] = None
    fasta_sequence: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    image_count: int = Field(default=0, ge=0, description="Number of images (non-negative)")
    cell_count: int = Field(default=0, ge=0, description="Number of cells (non-negative)")
    has_sum_projections: bool = False
    creator_name: Optional[str] = None

    class Config:
        from_attributes = True


class ExperimentDetailResponse(ExperimentResponse):
    """Schema for detailed experiment response with images."""
    images: List[ImageSummary] = []
