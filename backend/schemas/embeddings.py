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

    A null id means nothing is assigned, and the client offers those buckets as
    the "Unassigned" option — but note the two grains: a null microscope or PTM
    is a property of the experiment, while a null protein is a property of these
    points only, so one experiment can have both a null-protein bucket and
    assigned ones.
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


class DiscriminantPointResponse(BaseModel):
    """One crop in the supervised projection."""

    crop_id: int = Field(..., description="Cell crop ID")
    image_id: int = Field(..., description="Parent image ID")
    experiment_id: int = Field(..., description="Experiment ID for navigation")
    x: float = Field(..., description="First discriminant coordinate")
    y: float = Field(..., description="Second discriminant coordinate")
    protein_name: Optional[str] = Field(None, description="MAP protein name")
    protein_color: str = Field("#888888", description="Hex color for visualization")
    thumbnail_url: str = Field(..., description="URL to crop thumbnail")
    bundleness_score: Optional[float] = Field(None, description="Bundleness metric")


class DiscriminantClassScore(BaseModel):
    """How well one protein is recovered, out of fold."""

    protein: str = Field(..., description="Protein name as labelled")
    recall: float = Field(..., description="Out-of-fold recall for this protein")
    n_crops: int = Field(..., description="Crops of this protein that were scored")


class DiscriminantMetrics(BaseModel):
    """What the separation on screen is actually worth.

    Never optional in the UI: a supervised projection always looks separated, so
    the picture without these numbers is not a result. `balanced_accuracy` is
    cross-validated with experiments held out whole, while the plotted geometry
    comes from a fit on everything — the two answer different questions and the
    client says so.
    """

    balanced_accuracy: float = Field(
        ..., description="Out-of-fold balanced accuracy, grouped by experiment"
    )
    chance: float = Field(..., description="1 / number of proteins")
    null_mean: Optional[float] = Field(
        None,
        description=(
            "Mean score with labels shuffled between experiments. Null when no "
            "shuffle survived — NOT 0.0, which would read as the strongest "
            "possible evidence for the score beside it."
        ),
    )
    null_max: Optional[float] = Field(
        None,
        description=(
            "Highest score the shuffled labels reached. ⚠️ NOT a ceiling: it is "
            "the max of a small sample, and on this corpus 17.5% of individual "
            "shuffles exceed the max of the 20 that run. Compare against "
            "`null_p95` and read `p_value`; quoting a ratio against this number "
            "overstates the evidence by whatever the seed happened to draw."
        ),
    )
    null_p95: Optional[float] = Field(
        None,
        description="95th percentile of the null — the stable bar to read the score against",
    )
    p_value: Optional[float] = Field(
        None,
        description=(
            "(1 + #{null >= score}) / (n + 1). Floored at 1/(n_permutations + 1), "
            "so with 20 shuffles the smallest attainable value is 0.048: this can "
            "show a score sits outside the null, never that p < 0.01."
        ),
    )
    per_class: List["DiscriminantClassScore"] = Field(
        default_factory=list,
        description=(
            "Out-of-fold recall per protein. The headline is their mean, and the "
            "spread is wide — read this before concluding the projection "
            "separates every MAP equally."
        ),
    )
    unscoreable_proteins: List[str] = Field(
        default_factory=list,
        description=(
            "Proteins drawn on the plot but absent from the score: each occurs "
            "in only one experiment, so a split that holds experiments out can "
            "never test them. Left in the score they would contribute a recall "
            "of 0 by arithmetic and read as 'indistinguishable'."
        ),
    )
    n_permutations: int = Field(..., description="Shuffles behind the null")
    n_proteins: int = Field(..., description="Proteins being separated")
    n_experiments: int = Field(..., description="Experiments the split had to work with")


class DiscriminantDataResponse(BaseModel):
    """Supervised (LDA) projection of cell crops by MAP protein."""

    points: List[DiscriminantPointResponse] = Field(..., description="Projected crops")
    total_crops: int = Field(..., description="Crops in the filtered view")
    facets: List[UmapFacetRow] = Field(
        default_factory=list,
        description="Filter options with counts, over the scope before facet filters",
    )
    metrics: Optional[DiscriminantMetrics] = Field(
        None, description="Null until the projection has been computed"
    )
    is_computing: bool = Field(
        False,
        description=(
            "The projection is being fitted in the background (minutes, not "
            "seconds). Poll until this clears."
        ),
    )
    is_stale: bool = Field(
        False,
        description=(
            "The corpus changed since this projection was fitted — crops added, "
            "proteins reassigned, a microscope changed. The points shown still "
            "carry their OLD coordinates while their labels and colours are "
            "current, so treat the picture and the score as provisional. A "
            "refit is already scheduled; poll until this clears."
        ),
    )
    compute_error: Optional[str] = Field(
        None,
        description=(
            "The computation failed and will not retry on its own — stop polling "
            "and show this."
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
