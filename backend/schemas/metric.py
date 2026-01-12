"""Metric schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


# CRUD Schemas

class MetricCreate(BaseModel):
    """Schema for creating a metric."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None


class MetricUpdate(BaseModel):
    """Schema for updating a metric."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None


class MetricResponse(BaseModel):
    """Schema for metric response."""
    id: int
    name: str
    description: Optional[str] = None
    image_count: int = 0
    comparison_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MetricListResponse(BaseModel):
    """List of metrics."""
    items: List[MetricResponse]
    total: int


# Metric Image Schemas

class MetricImageResponse(BaseModel):
    """Schema for metric image."""
    id: int
    metric_id: int
    cell_crop_id: Optional[int] = None
    file_path: Optional[str] = None
    original_filename: Optional[str] = None
    image_url: Optional[str] = None
    created_at: datetime

    # Rating info (if exists)
    mu: Optional[float] = None
    sigma: Optional[float] = None
    ordinal_score: Optional[float] = None
    comparison_count: int = 0

    class Config:
        from_attributes = True


class MetricImageForRanking(BaseModel):
    """Metric image info for ranking UI."""
    id: int
    image_url: Optional[str] = None
    cell_crop_id: Optional[int] = None
    original_filename: Optional[str] = None

    class Config:
        from_attributes = True


class ImportCropsRequest(BaseModel):
    """Request to import cell crops from experiments."""
    experiment_ids: List[int]


class ImportCropsResponse(BaseModel):
    """Result of import operation."""
    imported_count: int
    skipped_count: int  # Already in metric


# Ranking Schemas

class MetricPairResponse(BaseModel):
    """Response with next pair to compare in a metric."""
    image_a: MetricImageForRanking
    image_b: MetricImageForRanking
    comparison_number: int
    total_comparisons: int


class MetricComparisonCreate(BaseModel):
    """Schema for submitting a metric comparison."""
    image_a_id: int
    image_b_id: int
    winner_id: int
    response_time_ms: Optional[int] = None


class MetricComparisonResponse(BaseModel):
    """Schema for metric comparison response."""
    id: int
    metric_id: int
    image_a_id: int
    image_b_id: int
    winner_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class MetricRankingItem(BaseModel):
    """Single item in the metric ranking."""
    rank: int
    metric_image_id: int
    image_url: Optional[str] = None
    cell_crop_id: Optional[int] = None
    original_filename: Optional[str] = None
    mu: float
    sigma: float
    ordinal_score: float
    comparison_count: int


class MetricRankingResponse(BaseModel):
    """Full metric ranking response."""
    items: List[MetricRankingItem]
    total: int
    page: int
    per_page: int


class MetricProgressResponse(BaseModel):
    """Metric ranking progress/convergence info."""
    total_comparisons: int
    convergence_percent: float = Field(..., ge=0, le=100)
    estimated_remaining: int
    average_sigma: float
    target_sigma: float
    phase: str  # "exploration" or "exploitation"
    image_count: int


# Available experiments for import

class ExperimentForImport(BaseModel):
    """Experiment available for importing crops into a metric."""
    id: int
    name: str
    image_count: int
    crop_count: int
    already_imported: int  # How many crops already in this metric
