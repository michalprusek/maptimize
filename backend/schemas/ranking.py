"""Ranking schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class CellCropForRanking(BaseModel):
    """Cell crop info for ranking UI."""
    id: int
    image_id: int
    mip_url: Optional[str] = None
    map_protein_name: Optional[str] = None
    bundleness_score: Optional[float] = None

    class Config:
        from_attributes = True


class PairResponse(BaseModel):
    """Response with next pair to compare."""
    crop_a: CellCropForRanking
    crop_b: CellCropForRanking
    comparison_number: int
    total_comparisons: int


class ComparisonCreate(BaseModel):
    """Schema for submitting a comparison."""
    crop_a_id: int
    crop_b_id: int
    winner_id: int
    response_time_ms: Optional[int] = None


class ComparisonResponse(BaseModel):
    """Schema for comparison response."""
    id: int
    crop_a_id: int
    crop_b_id: int
    winner_id: int
    timestamp: datetime

    class Config:
        from_attributes = True


class RankingItem(BaseModel):
    """Single item in the ranking."""
    rank: int
    cell_crop_id: int
    image_id: int
    mip_url: Optional[str] = None
    map_protein_name: Optional[str] = None
    mu: float
    sigma: float
    ordinal_score: float
    comparison_count: int
    bundleness_score: Optional[float] = None


class RankingResponse(BaseModel):
    """Full ranking response."""
    items: List[RankingItem]
    total: int
    page: int
    per_page: int


class ProgressResponse(BaseModel):
    """Ranking progress/convergence info."""
    total_comparisons: int
    convergence_percent: float = Field(..., ge=0, le=100)
    estimated_remaining: int
    average_sigma: float
    target_sigma: float
    phase: str  # "exploration" or "exploitation"


# Import source schemas

class ImportSourceResponse(BaseModel):
    """Experiment available for ranking import."""
    experiment_id: int
    experiment_name: str
    image_count: int
    crop_count: int
    included: bool  # Whether already added to ranking sources


class ImportSourcesRequest(BaseModel):
    """Request to add experiments as ranking sources."""
    experiment_ids: List[int]


class ImportResult(BaseModel):
    """Result of import operation."""
    added_experiments: int
    total_images: int
    total_crops: int
