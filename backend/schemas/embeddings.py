"""Schemas for embedding and UMAP visualization endpoints."""

from datetime import datetime
from enum import Enum
from typing import List, Optional, Union
from pydantic import BaseModel, Field


class UmapType(str, Enum):
    """Type of UMAP visualization."""
    FOV = "fov"
    CROPPED = "cropped"


class UmapPointResponse(BaseModel):
    """Single point in UMAP visualization."""

    crop_id: int = Field(..., description="Cell crop ID")
    image_id: int = Field(..., description="Parent image ID")
    experiment_id: int = Field(..., description="Experiment ID for navigation")
    x: float = Field(..., description="UMAP x coordinate")
    y: float = Field(..., description="UMAP y coordinate")
    protein_name: Optional[str] = Field(None, description="MAP protein name")
    protein_color: str = Field("#888888", description="Hex color for visualization")
    thumbnail_url: str = Field(..., description="URL to crop thumbnail")
    bundleness_score: Optional[float] = Field(None, description="Bundleness metric")


class UmapDataResponse(BaseModel):
    """UMAP visualization data response for cell crops."""

    points: List[UmapPointResponse] = Field(..., description="UMAP points")
    total_crops: int = Field(..., description="Total number of crops")
    silhouette_score: Optional[float] = Field(
        None,
        description="Silhouette score measuring cluster separation (-1 to 1)"
    )
    is_stale: bool = Field(
        False,
        description=(
            "Crops have embeddings but no coordinates yet (new upload or edit). "
            "A refresh is running in the background; poll until this clears."
        ),
    )


class UmapFovPointResponse(BaseModel):
    """Single FOV point in UMAP visualization."""

    image_id: int = Field(..., description="Image ID")
    experiment_id: int = Field(..., description="Experiment ID")
    x: float = Field(..., description="UMAP x coordinate")
    y: float = Field(..., description="UMAP y coordinate")
    protein_name: Optional[str] = Field(None, description="MAP protein name")
    protein_color: str = Field("#888888", description="Hex color for visualization")
    thumbnail_url: str = Field(..., description="URL to FOV thumbnail")
    original_filename: str = Field(..., description="Original filename")


class UmapFovDataResponse(BaseModel):
    """UMAP visualization data response for FOV images."""

    points: List[UmapFovPointResponse] = Field(..., description="UMAP FOV points")
    total_images: int = Field(..., description="Total number of FOV images")
    silhouette_score: Optional[float] = Field(
        None,
        description="Silhouette score measuring cluster separation (-1 to 1)"
    )
    is_precomputed: bool = Field(True, description="Whether coordinates are pre-computed")
    computed_at: Optional[datetime] = Field(None, description="When UMAP was computed")
    is_stale: bool = Field(
        False,
        description=(
            "Images have embeddings but no coordinates yet (new upload or edit). "
            "A refresh is running in the background; poll until this clears."
        ),
    )


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
