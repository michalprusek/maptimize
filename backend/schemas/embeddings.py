"""Schemas for embedding and UMAP visualization endpoints."""

from typing import List, Optional
from pydantic import BaseModel, Field


class UmapPointResponse(BaseModel):
    """Single point in UMAP visualization."""

    crop_id: int = Field(..., description="Cell crop ID")
    image_id: int = Field(..., description="Parent image ID")
    x: float = Field(..., description="UMAP x coordinate")
    y: float = Field(..., description="UMAP y coordinate")
    protein_name: Optional[str] = Field(None, description="MAP protein name")
    protein_color: str = Field("#888888", description="Hex color for visualization")
    thumbnail_url: str = Field(..., description="URL to crop thumbnail")
    bundleness_score: Optional[float] = Field(None, description="Bundleness metric")


class UmapDataResponse(BaseModel):
    """UMAP visualization data response."""

    points: List[UmapPointResponse] = Field(..., description="UMAP points")
    total_crops: int = Field(..., description="Total number of crops")
    n_neighbors: int = Field(..., description="UMAP n_neighbors parameter")
    min_dist: float = Field(..., description="UMAP min_dist parameter")


class FeatureExtractionTriggerResponse(BaseModel):
    """Response for feature extraction trigger."""

    message: str
    pending: int = Field(..., description="Number of crops queued for extraction")


class FeatureExtractionStatus(BaseModel):
    """Status of feature extraction for an experiment."""

    total: int = Field(..., description="Total crops")
    with_embeddings: int = Field(..., description="Crops with embeddings")
    without_embeddings: int = Field(..., description="Crops without embeddings")
    percentage: float = Field(..., description="Percentage complete")
