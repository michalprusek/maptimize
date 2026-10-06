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


class LabelAxis(str, Enum):
    """Which dimension the separability score groups points by.

    The same dimensions the filter panel offers, so the axis a reader can
    colour by is exactly the axis they can score by. Keeping them one vocabulary
    is what stops the index from reporting on classes the legend does not show.

    ``GROUP`` is the one that is not a column: the classes are sets of facet
    values the caller supplies with the request (see ``utils/label_groups.py``).
    """

    PROTEIN = "protein"
    MICROSCOPE = "microscope"
    PTM = "ptm"
    CELL_LINE = "cell_line"
    EXPERIMENT = "experiment"
    GROUP = "group"


class SeparabilityResponse(BaseModel):
    """How cleanly the plotted points separate along one labelled axis.

    ``score`` never travels alone. A silhouette depends on how many classes were
    compared and how many points carried a label, so two scores are comparable
    only between subsets of comparable size — the counts are what let a reader
    see whether the comparison they are making is fair.
    """

    score: float = Field(..., description="Silhouette over the raw embeddings (-1 to 1)")
    label_by: LabelAxis = Field(..., description="Dimension the points were grouped by")
    n_classes: int = Field(..., description="Distinct values scored")
    n_points: int = Field(..., description="Points carrying a value on this axis")


class UnscoredReason(str, Enum):
    """Why a set of points has no separability score.

    Listed from "nothing to work with" to "something went wrong"; the first that
    applies is the one reported, because it is the one a reader can act on.
    """

    NO_LABELS = "no_labels"
    SINGLE_CLASS = "single_class"
    TOO_FEW_POINTS = "too_few_points"
    FAILED = "failed"


class SeparabilityUnscoredResponse(BaseModel):
    """The score is missing, and this is why.

    A separate object rather than a nullable ``score`` on
    ``SeparabilityResponse``: a null score sitting beside real counts is one
    careless read away from being reported as zero.
    """

    label_by: LabelAxis = Field(..., description="Dimension the points were grouped by")
    reason: UnscoredReason = Field(..., description="Why no score could be computed")
    n_classes: int = Field(..., description="Distinct values found on this axis")
    n_points: int = Field(..., description="Points carrying a value on this axis")


class UmapFacetRow(BaseModel):
    """One (experiment, protein) bucket of the plot, with its point count.

    The filter panel needs, for every value it offers, how many points carry it.
    Rather than repeat an experiment's microscope, PTM and cell line on each of
    its hundreds of points, the scope is summarised here once per bucket and the client joins
    on ``experiment_id``. Rows are computed over the scope *before* facet filters
    are applied, so unticking a facet value never makes it disappear from the
    panel.

    A null id means nothing is assigned, and the client offers those buckets as
    the "Unassigned" option — but note the two grains: a null microscope, PTM or
    cell line is a property of the experiment, while a null protein is a property
    of these points only, so one experiment can have both a null-protein bucket
    and assigned ones.
    """

    experiment_id: int = Field(..., description="Experiment these points belong to")
    experiment_name: str = Field(..., description="Experiment name, for the filter list")
    microscope_id: Optional[int] = Field(None, description="Microscope, or null if unassigned")
    ptm_id: Optional[int] = Field(None, description="PTM, or null if unassigned")
    cell_line_id: Optional[int] = Field(None, description="Cell line, or null if unassigned")
    protein_id: Optional[int] = Field(None, description="MAP protein, or null if unassigned")
    count: int = Field(..., description="Points with embeddings in this bucket")


# One wording for both point types: the two must not drift on what -1 means.
_GROUP_FIELD = (
    "Only with label_by=group: index of the caller's group this point falls in, "
    "-1 when it falls in more than one (and is left out of the score), null "
    "when it is in none."
)


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
    group: Optional[int] = Field(None, description=_GROUP_FIELD)


class UmapDataResponse(BaseModel):
    """UMAP visualization data response for cell crops."""

    points: List[UmapPointResponse] = Field(..., description="UMAP points")
    total_crops: int = Field(..., description="Total number of crops")
    facets: List[UmapFacetRow] = Field(
        default_factory=list,
        description="Filter options with counts, over the scope before facet filters",
    )
    separability: Optional[SeparabilityResponse] = Field(
        None,
        description=(
            "How cleanly these points separate along the requested label axis, "
            "or null when too few of them carry a value to say anything."
        ),
    )
    separability_unscored: Optional[SeparabilityUnscoredResponse] = Field(
        None,
        description=(
            "Set exactly when points came back but `separability` is null: "
            "which threshold withheld the score, with the counts behind it."
        ),
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
    group: Optional[int] = Field(None, description=_GROUP_FIELD)


class UmapFovDataResponse(BaseModel):
    """UMAP visualization data response for FOV images."""

    points: List[UmapFovPointResponse] = Field(..., description="UMAP FOV points")
    total_images: int = Field(..., description="Total number of FOV images")
    facets: List[UmapFacetRow] = Field(
        default_factory=list,
        description="Filter options with counts, over the scope before facet filters",
    )
    separability: Optional[SeparabilityResponse] = Field(
        None,
        description=(
            "How cleanly these points separate along the requested label axis, "
            "or null when too few of them carry a value to say anything."
        ),
    )
    separability_unscored: Optional[SeparabilityUnscoredResponse] = Field(
        None,
        description=(
            "Set exactly when points came back but `separability` is null: "
            "which threshold withheld the score, with the counts behind it."
        ),
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
