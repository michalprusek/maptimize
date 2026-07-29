"""Experiment schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict, Field

from models.experiment import ExperimentStatus
from schemas.image import MapProteinResponse
from schemas.microscope import MicroscopeResponse
from schemas.ptm import PTMResponse
from schemas.reference import RejectsNullName


class ExperimentCreate(BaseModel):
    """Schema for creating an experiment."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    map_protein_id: Optional[int] = None
    microscope_id: Optional[int] = None
    ptm_id: Optional[int] = None
    fasta_sequence: Optional[str] = None


class ExperimentUpdate(RejectsNullName):
    """Schema for updating an experiment.

    No `microscope_id` and no `ptm_id` on purpose: both are assigned through
    their own endpoints (`PATCH /experiments/{id}/microscope` and
    `.../ptm`), which any group member may call. Accepting them here too would
    give one field two endpoints with two different ACLs -- and the wider one
    would be reachable by mistake.

    `RejectsNullName` because `experiments.name` is NOT NULL: an explicit
    `{"name": null}` otherwise reaches the UPDATE and returns 500, not 422.
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
    ptm: Optional[PTMResponse] = None
    fasta_sequence: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    image_count: int = Field(default=0, ge=0, description="Number of images (non-negative)")
    cell_count: int = Field(default=0, ge=0, description="Number of cells (non-negative)")
    has_sum_projections: bool = False
    creator_name: Optional[str] = None
    # Owner id, so the client can tell which controls will actually work.
    # Reads are group-shared but most WRITES are owner-only, and the UI cannot
    # derive that from `creator_name` (names are not identities). Without it the
    # protein selector on a colleague's card looks live and 403s on click — on
    # this corpus that is 40 of 46 experiments. Microscope and PTM are the
    # deliberate group-writable exceptions and stay enabled for everyone.
    user_id: int

    class Config:
        from_attributes = True


class ExperimentDetailResponse(ExperimentResponse):
    """Schema for detailed experiment response with images."""
    images: List[ImageSummary] = []
