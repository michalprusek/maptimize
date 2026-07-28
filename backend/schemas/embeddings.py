"""Schemas for embedding and UMAP visualization endpoints."""

from datetime import datetime
from enum import Enum
from typing import List, Optional, Union
from pydantic import BaseModel, Field


class UmapType(str, Enum):
    """Type of UMAP visualization.

    SSOT for which corpus a projection covers. Pass this around rather than bare
    strings — a dispatch on unvalidated strings falls through to one branch
    silently, and a wrong corpus refresh reports success while fixing nothing.
    """
    FOV = "fov"
    CROPPED = "cropped"

    @property
    def item_word(self) -> str:
        """Plural noun for this corpus, for log and error messages."""
        return "images" if self is UmapType.FOV else "crops"


class UmapFacetRow(BaseModel):
    """One (experiment, protein) bucket of the plot, with its point count.

    The filter panel needs, for every value it offers, how many points carry it.
    Rather than repeat an experiment's microscope and PTM on each of its hundreds
    of points, the scope is summarised here once per bucket and the client joins
    on ``experiment_id``. Rows are computed over the scope *before* facet filters
    are applied, so unticking a facet value never makes it disappear from the
    panel.

    A null id means the experiment has nothing assigned for that facet; the
    client offers those buckets as the "Unassigned" option.
    """

    experiment_id: int = Field(..., description="Experiment these points belong to")
    experiment_name: str = Field(..., description="Experiment name, for the filter list")
    microscope_id: Optional[int] = Field(None, description="Microscope, or null if unassigned")
    ptm_id: Optional[int] = Field(None, description="PTM, or null if unassigned")
    protein_id: Optional[int] = Field(None, description="MAP protein, or null if unassigned")
    count: int = Field(..., description="Points with embeddings in this bucket")


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
    facets: List[UmapFacetRow] = Field(
        default_factory=list,
        description="Filter options with counts, over the scope before facet filters",
    )
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
    refresh_error: Optional[str] = Field(
        None,
        description=(
            "The background refresh for this scope failed. Coordinates are "
            "missing and will not arrive on their own — stop polling, show this, "
            "and retry via POST /umap/recompute."
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
    facets: List[UmapFacetRow] = Field(
        default_factory=list,
        description="Filter options with counts, over the scope before facet filters",
    )
    silhouette_score: Optional[float] = Field(
        None,
        description="Silhouette score measuring cluster separation (-1 to 1)"
    )
    computed_at: Optional[datetime] = Field(
        None,
        description="When the projection these points come from was fitted",
    )
    is_stale: bool = Field(
        False,
        description=(
            "Images have embeddings but no coordinates yet (new upload or edit). "
            "A refresh is running in the background; poll until this clears."
        ),
    )
    refresh_error: Optional[str] = Field(
        None,
        description=(
            "The background refresh for this scope failed. Coordinates are "
            "missing and will not arrive on their own — stop polling, show this, "
            "and retry via POST /umap/recompute."
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
