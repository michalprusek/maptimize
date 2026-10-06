"""Embeddings and UMAP visualization endpoints."""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple, Type, TypeVar, Union

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.cell_crop import CellCrop
from models.cell_line import CellLine
from models.experiment import Experiment
from models.image import Image, MapProtein
from models.microscope import Microscope
from models.ptm import PTM
from models.user import User
from schemas.embeddings import (
    FeatureExtractionStatus,
    FeatureExtractionTriggerResponse,
    LabelAxis,
    SeparabilityResponse,
    SeparabilityUnscoredResponse,
    UmapDataResponse,
    UmapFacetRow,
    UmapFovDataResponse,
    UmapFovPointResponse,
    UmapPointResponse,
    UmapType,
    UnscoredReason,  # noqa: F401 — re-exported; the router tests read it from here
)
from utils.facets import UNASSIGNED_FACET_ID, facet_clause, real_ids
from utils.label_groups import (
    NO_GROUPS,  # noqa: F401 — re-exported; the router tests read it from here
    LabelGroups,
    assign_groups,
    parse_label_groups,
    score_labels,
)
from services.umap_service import (
    MIN_POINTS_FOR_UMAP,
    Separability,
    clear_refresh_error,
    compute_separability,
    get_refresh_error,
    protein_labels,
    refresh_umap_scope,
    unscored_gap,
)
from utils.security import get_current_user
from utils.groups import experiment_owner_filter, get_user_group_ids

router = APIRouter()
logger = logging.getLogger(__name__)

# A CellCrop or an Image — both carry umap_x/umap_y and an embedding.
T = TypeVar("T")


@dataclass(frozen=True)
class FacetSelection:
    """What the dashboard filter panel currently has ticked.

    Empty list = facet untouched = no constraint. Facets combine as OR within and
    AND across, and any of them may include ``UNASSIGNED_FACET_ID`` to also match
    rows with nothing assigned.
    """

    experiment_ids: List[int] = field(default_factory=list)
    microscope_ids: List[int] = field(default_factory=list)
    protein_ids: List[int] = field(default_factory=list)
    ptm_ids: List[int] = field(default_factory=list)
    cell_line_ids: List[int] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        """True when the user has narrowed the plot at all."""
        return any(
            (
                self.experiment_ids,
                self.microscope_ids,
                self.protein_ids,
                self.ptm_ids,
                self.cell_line_ids,
            )
        )


def facet_selection(
    experiment_id: Optional[List[int]] = Query(
        None, description="Filter by experiment; repeat for several"
    ),
    microscope_id: Optional[List[int]] = Query(
        None, description="Filter by microscope; repeat for several, 0 = unassigned"
    ),
    protein_id: Optional[List[int]] = Query(
        None, description="Filter by MAP protein; repeat for several, 0 = unassigned"
    ),
    ptm_id: Optional[List[int]] = Query(
        None, description="Filter by PTM; repeat for several, 0 = unassigned"
    ),
    cell_line_id: Optional[List[int]] = Query(
        None, description="Filter by cell line; repeat for several, 0 = unassigned"
    ),
) -> FacetSelection:
    """Collect the dashboard filters into one value.

    A dependency rather than five parameters on the handler: it keeps the facets
    together as the single thing they are, lets a future endpoint take the same
    filter without re-declaring them, and means a caller that constructs the
    handler's arguments itself supplies one object instead of five lists.
    """
    return FacetSelection(
        experiment_ids=experiment_id or [],
        microscope_ids=microscope_id or [],
        protein_ids=protein_id or [],
        ptm_ids=ptm_id or [],
        cell_line_ids=cell_line_id or [],
    )


def label_groups(
    group: Optional[List[str]] = Query(
        None,
        description=(
            "With label_by=group: one class per repetition, each a comma-separated "
            "list of '<facet>:<id>' (facet = experiment, microscope, protein, ptm "
            "or cell_line; id 0 = unassigned). A point belongs to a group when it "
            "carries any of its values."
        ),
    ),
) -> LabelGroups:
    """Parse the caller's groups, or 422 on anything malformed.

    A dependency for the same reason ``facet_selection`` is one: a bare
    ``Query(...)`` parameter reaches a directly-called handler as the Query
    object itself.
    """
    try:
        return parse_label_groups(group)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from None


@router.get("/umap")
async def get_umap_visualization(
    umap_type: UmapType = Query(UmapType.CROPPED, description="Type: fov or cropped"),
    selection: FacetSelection = Depends(facet_selection),
    label_by: LabelAxis = Query(
        LabelAxis.PROTEIN,
        description="Dimension the separability score groups points by",
    ),
    groups: LabelGroups = Depends(label_groups),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Union[UmapDataResponse, UmapFovDataResponse]:
    """
    Get UMAP 2D projection of embeddings.

    - type=cropped: Returns cell crop embeddings (default)
    - type=fov: Returns FOV/image embeddings

    Serves pre-computed coordinates only. Points whose embeddings arrived after
    the last projection are reported via ``is_stale`` and a refresh is scheduled
    in the background; fitting never blocks the response. If that refresh keeps
    failing, ``refresh_error`` says why instead of leaving the client to poll.

    Fit parameters are not tunable per request: every point in a scope must come
    from one shared fit, so refreshes always fit with the umap_service defaults.
    Filtering therefore never changes where a point sits — it only chooses which
    points of the one shared projection are returned.

    The filters are OR within a facet and AND across facets. Passing id 0 for
    microscope, protein, PTM or cell line also matches rows with nothing
    assigned.

    ``separability`` scores the points actually returned, so narrowing the
    filters is how a caller compares one context against another — controls
    only, one PTM, a single microscope. ``label_by`` chooses which dimension the
    score groups by; the counts beside it say how many classes and how many
    labelled points went into it, because a silhouette is not comparable across
    subsets of different size.
    """
    # Validate references up front so a stale or deleted id fails with a clear
    # 404 instead of silently matching nothing and looking like an empty result.
    # Reference data is shared, so anyone can delete a value another user's open
    # tab still has ticked.
    await _verify_reference_ids(db, Microscope, selection.microscope_ids, "Microscope")
    await _verify_reference_ids(db, MapProtein, selection.protein_ids, "MAP protein")
    await _verify_reference_ids(db, PTM, selection.ptm_ids, "PTM")
    await _verify_reference_ids(db, CellLine, selection.cell_line_ids, "Cell line")

    group_ids = await get_user_group_ids(current_user.id, db)
    if selection.experiment_ids:
        await _verify_experiments_visible(
            selection.experiment_ids, current_user.id, group_ids, db
        )

    # Group members are deliberately NOT existence-checked the way filter ids
    # are. A filter id that matches nothing silently empties the plot; a group
    # member that matches nothing leaves a group the legend shows with zero
    # points, which already says so. And the labels are applied only to points
    # the ACL has returned, so an id the caller cannot see labels nothing.
    #
    # ``groups`` is forwarded unread: ``_labels_and_groups`` is the one place
    # that decides whether the axis uses it.
    if umap_type is UmapType.FOV:
        return await _get_fov_umap(
            selection, label_by, groups, current_user, group_ids,
            background_tasks, db,
        )
    return await _get_cropped_umap(
        selection, label_by, groups, current_user, group_ids,
        background_tasks, db,
    )


@router.get("/separability", response_model=SeparabilityResponse)
async def get_separability(
    umap_type: UmapType = Query(UmapType.CROPPED, description="Type: fov or cropped"),
    selection: FacetSelection = Depends(facet_selection),
    label_by: LabelAxis = Query(
        LabelAxis.PROTEIN,
        description="Dimension to group points by",
    ),
    groups: LabelGroups = Depends(label_groups),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SeparabilityResponse:
    """
    How cleanly one subset of the corpus separates along one labelled axis.

    The same number the dashboard shows, without the points — a projection is
    thousands of coordinates, and a caller asking "do 3D SIM and Airyscan
    separate?" wants one figure, not the plot. It delegates to the UMAP handler
    rather than repeating the query, so the answer given here and the badge on
    the chart can never drift apart.

    Filters are the way to choose a context: narrow to controls, to one PTM, to
    one microscope, and compare the results. ``n_classes`` and ``n_points`` come
    back with the score because a silhouette is not comparable across subsets of
    different size — a comparison that ignores them is not a comparison.

    404 when nothing can be scored (fewer than 10 points carry a value on this
    axis, or they all carry the same one). That is a different statement from a
    score of zero, and returning null invites a caller to report it as one.
    """
    projection = await get_umap_visualization(
        umap_type=umap_type,
        selection=selection,
        label_by=label_by,
        groups=groups,
        background_tasks=background_tasks,
        current_user=current_user,
        db=db,
    )
    if projection.separability is None:
        unscored = projection.separability_unscored
        found = (
            f" Found {unscored.n_points} labelled points over "
            f"{unscored.n_classes} distinct values ({unscored.reason.value})."
            if unscored
            else " The filter matched no plotted points."
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Not enough labelled points to score separability by "
                f"{label_by.value}: it needs at least 10 points carrying a "
                f"value on that axis, spread over at least 2 distinct values."
                + found
            ),
        )
    return projection.separability


# -- Tombstone: the discriminant (LDA) projection, removed 2026-08-04 ---------
#
# A biologist can keep this dashboard open for days. Their cached bundle still
# has the LDA button, and clicking it after the deploy would 404 — which the
# client renders as "Unable to Generate Visualization / Not Found" beside a
# Retry that can never succeed. That reads as "the analysis is broken", not
# "this feature is gone; reload".
#
# 410 Gone carries a sentence the UNMODIFIED cached bundle will display, which
# is the only channel that reaches a tab loaded before the deploy. Every hit is
# logged, so this can be deleted once the log falls silent — it has no other
# reason to exist and no client written after 2026-08-04 will ever call it.
@router.get("/discriminant", status_code=status.HTTP_410_GONE)
@router.post("/discriminant/recompute", status_code=status.HTTP_410_GONE)
async def discriminant_removed(current_user: User = Depends(get_current_user)):
    logger.info(
        "stale client called the removed discriminant endpoint (user %s)",
        current_user.id,
    )
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "The discriminant (LDA) projection was removed. Reload the page to "
            "get the current dashboard."
        ),
    )


def _take_precomputed(
    items: list[T],
    umap_type: UmapType,
    background_tasks: BackgroundTasks,
) -> tuple[list[T], bool, Optional[str]]:
    """
    Select the items that already have coordinates, refreshing the rest in the background.

    Items whose embeddings arrived after the last projection (new upload or crop
    edit) have no coordinates yet. Serve what exists now and schedule the re-fit:
    the fit runs after the response is sent, and the client polls until is_stale
    clears. Never fit on the read path — that stalls page load for seconds.

    Takes no caller identity: the projection is global (one fit per type over
    every row), so who is reading decides what comes back, never what is fitted.

    A projection whose last refresh failed is NOT rescheduled; its error is
    returned so the client can stop polling and show it. Otherwise each poll would
    kick off another doomed multi-second fit, forever, in silence.

    Returns (items with coordinates, is_stale, refresh_error).
    """
    with_umap = [i for i in items if i.umap_x is not None and i.umap_y is not None]

    stale_count = len(items) - len(with_umap)
    if stale_count == 0:
        return with_umap, False, None

    refresh_error = get_refresh_error(umap_type)
    if refresh_error is not None:
        logger.warning(
            f"{stale_count}/{len(items)} {umap_type.item_word} missing UMAP "
            f"coordinates, but the last refresh failed ({refresh_error}) - "
            f"not rescheduling"
        )
        return with_umap, False, refresh_error

    logger.info(
        f"{stale_count}/{len(items)} {umap_type.item_word} missing UMAP "
        f"coordinates - scheduling background refresh"
    )
    background_tasks.add_task(refresh_umap_scope, umap_type)
    return with_umap, True, None


async def _verify_reference_ids(
    db: AsyncSession,
    model: Type,
    ids: Sequence[int],
    label: str,
) -> None:
    """404 if any selected reference id no longer exists.

    The unassigned sentinel is stripped first: it names the absence of a row, so
    looking it up would 404 every filter that includes "Unassigned".
    """
    wanted = real_ids(ids)
    if not wanted:
        return

    result = await db.execute(select(model.id).where(model.id.in_(wanted)))
    missing = sorted(set(wanted) - set(result.scalars().all()))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label} not found: {', '.join(str(i) for i in missing)}",
        )


async def _verify_experiment_ownership(
    experiment_id: int,
    user_id: int,
    db: AsyncSession,
) -> None:
    """Verify that user owns the experiment or is in the same group.

    The single-id entry point, for endpoints that scope to one experiment rather
    than filter across many.
    """
    group_ids = await get_user_group_ids(user_id, db)
    await _verify_experiments_visible([experiment_id], user_id, group_ids, db)


async def _verify_experiments_visible(
    experiment_ids: Sequence[int],
    user_id: int,
    group_ids: Sequence[int],
    db: AsyncSession,
) -> None:
    """404 unless every selected experiment is one the user may read.

    One query for the whole selection rather than one per id — and it reports
    only ids the ACL filter rejected, so a user cannot probe for the existence of
    another group's experiments by watching which ids come back.
    """
    result = await db.execute(
        select(Experiment.id).where(
            Experiment.id.in_(list(experiment_ids)),
            experiment_owner_filter(user_id, group_ids),
        )
    )
    missing = sorted(set(experiment_ids) - set(result.scalars().all()))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Experiment not found: {', '.join(str(i) for i in missing)}",
        )


async def _load_facets(
    umap_type: UmapType,
    user_id: int,
    group_ids: Sequence[int],
    db: AsyncSession,
) -> List[UmapFacetRow]:
    """Summarise the readable scope into filter options with counts.

    Deliberately ignores the active facet selection: the panel has to keep
    offering a value after you untick it, and has to show how many points each
    *other* value would bring back. Grouping by (experiment, protein) is the
    coarsest grouping that still separates every facet, because microscope, PTM
    and cell line all live on the experiment while protein is per point —
    roughly one row per experiment, so this stays far cheaper than the point
    query and loads no embeddings.
    """
    if umap_type is UmapType.FOV:
        protein_col = Image.map_protein_id
        count_col = Image.id
        embedded = Image.embedding.isnot(None)
        joins = [(Experiment, Image.experiment_id == Experiment.id)]
    else:
        protein_col = CellCrop.map_protein_id
        count_col = CellCrop.id
        embedded = CellCrop.embedding.isnot(None)
        joins = [
            (Image, CellCrop.image_id == Image.id),
            (Experiment, Image.experiment_id == Experiment.id),
        ]

    buckets = (
        Experiment.id,
        Experiment.name,
        Experiment.microscope_id,
        Experiment.ptm_id,
        Experiment.cell_line_id,
        protein_col,
    )
    source = select(*buckets, func.count(count_col))
    for target, onclause in joins:
        source = source.join(target, onclause)

    result = await db.execute(
        source.where(
            experiment_owner_filter(user_id, group_ids),
            embedded,
        ).group_by(*buckets)
    )

    return [
        UmapFacetRow(
            experiment_id=exp_id,
            experiment_name=exp_name,
            microscope_id=microscope_id,
            ptm_id=ptm_id,
            cell_line_id=cell_line_id,
            protein_id=protein_id,
            count=count,
        )
        for exp_id, exp_name, microscope_id, ptm_id, cell_line_id, protein_id, count
        in result.all()
    ]


def _guard_enough_points(
    found: int, selection: FacetSelection, umap_type: UmapType
) -> None:
    """400 only when the *unfiltered* scope is too small to have been projected.

    The threshold guards fitting, not reading. Coordinates come from one shared
    fit that has already happened, so a filtered view returning three points is
    correct and worth plotting. Applying the threshold to filtered views instead
    answered any narrow combination with "Need at least N crops with embeddings"
    — an error where an honest, empty plot belonged — and would have made the PTM
    facet unusable from day one, since every experiment starts unassigned.
    """
    if selection.is_active or found >= MIN_POINTS_FOR_UMAP:
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"Need at least {MIN_POINTS_FOR_UMAP} {umap_type.item_word} "
            f"with embeddings. Found: {found}"
        ),
    )


def _apply_facets(query, selection: FacetSelection, protein_column):
    """AND every active facet onto the point query.

    ``protein_column`` differs per corpus (the crop's protein for cropped, the
    image's for FOV) so that filtering by protein always agrees with the colour
    the point is actually drawn in.
    """
    clauses = [
        facet_clause(Image.experiment_id, selection.experiment_ids),
        facet_clause(Experiment.microscope_id, selection.microscope_ids),
        facet_clause(Experiment.ptm_id, selection.ptm_ids),
        facet_clause(Experiment.cell_line_id, selection.cell_line_ids),
        facet_clause(protein_column, selection.protein_ids),
    ]
    for clause in clauses:
        if clause is not None:
            query = query.where(clause)
    return query


# Which column of the facet summary carries each experiment-level axis. Protein
# and experiment are not here because they live on the point itself.
_EXPERIMENT_AXIS_COLUMN = {
    LabelAxis.MICROSCOPE: "microscope_id",
    LabelAxis.PTM: "ptm_id",
    LabelAxis.CELL_LINE: "cell_line_id",
}


def _axis_labels(
    axis: LabelAxis,
    items: Sequence,
    experiment_ids: Sequence[int],
    facets: Sequence[UmapFacetRow],
) -> List[Optional[int]]:
    """The class of each point on ``axis``, positionally aligned with ``items``.

    Protein lives on the point. Microscope, PTM and cell line are properties of
    the experiment and are joined from the facet summary rather than fetched
    again:
    ``_load_facets`` has already run in this handler, over the whole readable
    scope, which is a superset of these points.

    ``None`` means "nothing assigned on this axis" and ``compute_separability``
    drops it. That is deliberate — pooling every unassigned experiment into one
    class would let absence look like a group that separates from the rest.

    Raises on an axis it cannot score. Every handler parameter with a
    ``Query(...)`` default arrives as that Query object when the handler is
    called directly, so a dispatch that fell through to protein would silently
    label the score with the wrong axis.
    """
    if axis == LabelAxis.PROTEIN:
        return protein_labels(items)
    if axis == LabelAxis.EXPERIMENT:
        return list(experiment_ids)

    try:
        column = _EXPERIMENT_AXIS_COLUMN[axis]
    except (KeyError, TypeError):
        raise ValueError(f"unsupported label axis: {axis!r}") from None

    by_experiment = {row.experiment_id: getattr(row, column) for row in facets}
    return [by_experiment.get(experiment_id) for experiment_id in experiment_ids]


def _group_assignment(
    groups: LabelGroups,
    items: Sequence,
    experiment_ids: Sequence[int],
    facets: Sequence[UmapFacetRow],
) -> List[Optional[int]]:
    """Which of the caller's groups each point falls in (see ``assign_groups``).

    Builds the per-point facet values from the same two sources ``_axis_labels``
    uses — protein from the point, the rest from the facet summary — so a group
    made of one facet's values scores exactly what that axis would.
    """
    by_experiment = {row.experiment_id: row for row in facets}
    proteins = protein_labels(items)

    def value(row: Optional[UmapFacetRow], column: str) -> int:
        return (getattr(row, column, None) if row else None) or UNASSIGNED_FACET_ID

    points = []
    for experiment_id, protein_id in zip(experiment_ids, proteins):
        row = by_experiment.get(experiment_id)
        points.append(
            {
                "experiment": experiment_id,
                "protein": protein_id or UNASSIGNED_FACET_ID,
                "microscope": value(row, "microscope_id"),
                "ptm": value(row, "ptm_id"),
                "cell_line": value(row, "cell_line_id"),
            }
        )
    return assign_groups(groups, points)


def _labels_and_groups(
    axis: LabelAxis,
    groups: LabelGroups,
    items: Sequence,
    experiment_ids: Sequence[int],
    facets: Sequence[UmapFacetRow],
) -> Tuple[List[Optional[int]], Optional[List[Optional[int]]]]:
    """``(labels to score, per-point group or None)`` for either kind of axis.

    The second half is what the plot colours by. It is returned rather than
    recomputed by the client: the score and the colours then come from one
    assignment, and cannot disagree about which points were ambiguous.

    ``groups`` is read only on the group axis. Under any other it may be stale
    client state — or, when a handler is called directly without it, the
    ``Depends`` placeholder — and must not be touched.
    """
    if axis is LabelAxis.GROUP:
        assigned = _group_assignment(groups, items, experiment_ids, facets)
        return score_labels(assigned), assigned
    return _axis_labels(axis, items, experiment_ids, facets), None


def _separability(
    embeddings: np.ndarray,
    labels: List[Optional[int]],
    axis: LabelAxis,
) -> Tuple[Optional[SeparabilityResponse], Optional[SeparabilityUnscoredResponse]]:
    """Score ``labels``, or say why they cannot be scored.

    Exactly one of the pair is set. The second half exists because a missing
    score used to be reported as nothing at all, and on the dashboard that reads
    as "still loading" or "broken" — while in practice it always meant the
    filter had left a single class on the axis being scored.
    """
    scored = compute_separability(embeddings, labels)
    if scored is not None:
        return (
            SeparabilityResponse(
                score=scored.score,
                label_by=axis,
                n_classes=scored.n_classes,
                n_points=scored.n_points,
            ),
            None,
        )

    gap = unscored_gap(labels)
    return None, SeparabilityUnscoredResponse(
        label_by=axis,
        reason=gap.reason,
        n_classes=gap.n_classes,
        n_points=gap.n_points,
    )


async def _get_cropped_umap(
    selection: FacetSelection,
    axis: LabelAxis,
    groups: LabelGroups,
    current_user: User,
    group_ids: Sequence[int],
    background_tasks: BackgroundTasks,
    db: AsyncSession,
) -> UmapDataResponse:
    """Get UMAP visualization for cell crops."""
    query = (
        select(CellCrop)
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(
            selectinload(CellCrop.map_protein),
            selectinload(CellCrop.image),
        )
        .where(
            experiment_owner_filter(current_user.id, group_ids),
            CellCrop.embedding.isnot(None),
        )
    )

    query = _apply_facets(query, selection, CellCrop.map_protein_id)

    # Stable order so the payload does not reshuffle between polls
    query = query.order_by(CellCrop.id)
    result = await db.execute(query)
    crops = result.scalars().all()

    _guard_enough_points(len(crops), selection, UmapType.CROPPED)

    facets = await _load_facets(UmapType.CROPPED, current_user.id, group_ids, db)

    crops_with_umap, is_stale, refresh_error = _take_precomputed(
        crops, UmapType.CROPPED, background_tasks
    )

    # Counts every crop with an embedding, including the ones still awaiting
    # coordinates — it must not shrink to the plotted subset.
    total_crops = len(crops)

    if not crops_with_umap:
        return UmapDataResponse(
            points=[],
            total_crops=total_crops,
            facets=facets,
            separability=None,
            is_stale=is_stale,
            refresh_error=refresh_error,
        )

    logger.info(f"Using pre-computed UMAP for {len(crops_with_umap)}/{total_crops} crops")
    embeddings = np.array([c.embedding for c in crops_with_umap])
    labels, point_groups = _labels_and_groups(
        axis,
        groups,
        crops_with_umap,
        [crop.image.experiment_id for crop in crops_with_umap],
        facets,
    )
    separability, unscored = _separability(embeddings, labels, axis)

    # Build response. Points carry only what varies per point; the experiment's
    # microscope and PTM are repeated far too often to send per point, so the
    # client joins them from `facets` on experiment_id.
    points = [
        UmapPointResponse(
            crop_id=crop.id,
            image_id=crop.image_id,
            experiment_id=crop.image.experiment_id,
            x=float(crop.umap_x),
            y=float(crop.umap_y),
            protein_name=crop.map_protein.name if crop.map_protein else None,
            protein_color=crop.map_protein.color if crop.map_protein else "#888888",
            thumbnail_url=f"/api/images/crops/{crop.id}/image?type=mip",
            bundleness_score=crop.bundleness_score,
            group=point_groups[index] if point_groups is not None else None,
        )
        for index, crop in enumerate(crops_with_umap)
    ]

    return UmapDataResponse(
        points=points,
        total_crops=total_crops,
        facets=facets,
        separability=separability,
        separability_unscored=unscored,
        is_stale=is_stale,
        refresh_error=refresh_error,
    )


async def _get_fov_umap(
    selection: FacetSelection,
    axis: LabelAxis,
    groups: LabelGroups,
    current_user: User,
    group_ids: Sequence[int],
    background_tasks: BackgroundTasks,
    db: AsyncSession,
) -> UmapFovDataResponse:
    """Get UMAP visualization for FOV images."""
    query = (
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(Image.map_protein))
        .where(
            experiment_owner_filter(current_user.id, group_ids),
            Image.embedding.isnot(None),
        )
    )

    query = _apply_facets(query, selection, Image.map_protein_id)

    # Stable order so the payload does not reshuffle between polls
    query = query.order_by(Image.id)
    result = await db.execute(query)
    images = result.scalars().all()

    _guard_enough_points(len(images), selection, UmapType.FOV)

    facets = await _load_facets(UmapType.FOV, current_user.id, group_ids, db)

    images_with_umap, is_stale, refresh_error = _take_precomputed(
        images, UmapType.FOV, background_tasks
    )

    # Counts every image with an embedding, including the ones still awaiting
    # coordinates — it must not shrink to the plotted subset.
    total_images = len(images)

    if not images_with_umap:
        return UmapFovDataResponse(
            points=[],
            total_images=total_images,
            facets=facets,
            separability=None,
            computed_at=None,
            is_stale=is_stale,
            refresh_error=refresh_error,
        )

    logger.info(f"Using pre-computed UMAP for {len(images_with_umap)}/{total_images} FOV images")
    embeddings = np.array([img.embedding for img in images_with_umap])
    labels, point_groups = _labels_and_groups(
        axis,
        groups,
        images_with_umap,
        [image.experiment_id for image in images_with_umap],
        facets,
    )
    separability, unscored = _separability(embeddings, labels, axis)
    computed_times = [img.umap_computed_at for img in images_with_umap if img.umap_computed_at]
    computed_at = min(computed_times) if computed_times else None

    points = [
        UmapFovPointResponse(
            image_id=image.id,
            experiment_id=image.experiment_id,
            x=float(image.umap_x),
            y=float(image.umap_y),
            protein_name=image.map_protein.name if image.map_protein else None,
            protein_color=image.map_protein.color if image.map_protein else "#888888",
            thumbnail_url=f"/api/images/{image.id}/file?type=thumbnail",
            original_filename=image.original_filename,
            group=point_groups[index] if point_groups is not None else None,
        )
        for index, image in enumerate(images_with_umap)
    ]

    return UmapFovDataResponse(
        points=points,
        total_images=total_images,
        facets=facets,
        separability=separability,
        separability_unscored=unscored,
        computed_at=computed_at,
        is_stale=is_stale,
        refresh_error=refresh_error,
    )


@router.post("/umap/recompute")
async def trigger_umap_recomputation(
    umap_type: UmapType = Query(..., description="Type to recompute: fov or cropped"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Force a UMAP recomputation of the whole corpus for one projection type.

    There is one fit per type, so this affects every reader, not just the caller.
    Reads schedule refreshes automatically, so this is the retry path for a
    projection whose refresh failed (reads stop rescheduling those) and an escape
    hatch for re-fitting coordinates that are already complete.
    """
    # Clear the recorded failure so reads resume auto-scheduling this projection.
    clear_refresh_error(umap_type)
    background_tasks.add_task(refresh_umap_scope, umap_type)

    return {"message": f"UMAP recomputation started for {umap_type.value}"}


@router.get("/status", response_model=FeatureExtractionStatus)
async def get_embedding_status(
    experiment_id: Optional[int] = Query(None, description="Filter by experiment"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeatureExtractionStatus:
    """Get feature extraction status for user's crops."""
    group_ids = await get_user_group_ids(current_user.id, db)
    base_conditions = [experiment_owner_filter(current_user.id, group_ids)]
    if experiment_id:
        base_conditions.append(Image.experiment_id == experiment_id)

    # Single query for both total and with-embeddings counts
    result = await db.execute(
        select(
            func.count(CellCrop.id).label("total"),
            func.count(CellCrop.id).filter(CellCrop.embedding.isnot(None)).label("with_emb"),
        )
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(*base_conditions)
    )
    row = result.one()
    total = row.total or 0
    with_embeddings = row.with_emb or 0

    without_embeddings = total - with_embeddings
    percentage = (with_embeddings / total * 100) if total > 0 else 0

    return FeatureExtractionStatus(
        total=total,
        with_embeddings=with_embeddings,
        without_embeddings=without_embeddings,
        percentage=round(percentage, 1),
    )


@router.post("/extract", response_model=FeatureExtractionTriggerResponse)
async def trigger_feature_extraction(
    experiment_id: int = Query(..., description="Experiment ID"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeatureExtractionTriggerResponse:
    """Trigger feature extraction for crops without embeddings. Runs in background."""
    await _verify_experiment_ownership(experiment_id, current_user.id, db)

    # Count crops without embeddings
    count_result = await db.execute(
        select(func.count(CellCrop.id))
        .join(Image, CellCrop.image_id == Image.id)
        .where(
            Image.experiment_id == experiment_id,
            CellCrop.embedding.is_(None),
        )
    )
    pending_count = count_result.scalar() or 0

    if pending_count == 0:
        return FeatureExtractionTriggerResponse(
            message="All crops already have embeddings",
            pending=0,
        )

    # Get crop IDs
    crops_result = await db.execute(
        select(CellCrop.id)
        .join(Image, CellCrop.image_id == Image.id)
        .where(
            Image.experiment_id == experiment_id,
            CellCrop.embedding.is_(None),
        )
    )
    crop_ids = [row[0] for row in crops_result.all()]

    background_tasks.add_task(_extract_features_background, crop_ids, experiment_id)

    return FeatureExtractionTriggerResponse(
        message=f"Feature extraction started for {pending_count} crops",
        pending=pending_count,
    )


async def _extract_features_background(crop_ids: list, experiment_id: int) -> None:
    """Background task for feature extraction."""
    from database import get_db_context
    from ml.features import extract_features_for_crops

    logger.info(
        f"Starting background feature extraction for {len(crop_ids)} crops "
        f"in experiment {experiment_id}"
    )

    try:
        async with get_db_context() as db:
            result = await extract_features_for_crops(crop_ids, db)
            logger.info(
                f"Background feature extraction complete for experiment {experiment_id}: "
                f"{result['success']} success, {result['failed']} failed"
            )
    except RuntimeError as e:
        logger.error(
            f"Background feature extraction failed for experiment {experiment_id} "
            f"(model error): {e}"
        )
    except Exception as e:
        logger.exception(
            f"Background feature extraction failed for experiment {experiment_id}: {e}"
        )


@router.post("/extract-fov", response_model=FeatureExtractionTriggerResponse)
async def trigger_fov_feature_extraction(
    experiment_id: Optional[int] = Query(None, description="Experiment ID (optional)"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeatureExtractionTriggerResponse:
    """Trigger FOV embedding extraction for images without embeddings. Runs in background."""
    group_ids = await get_user_group_ids(current_user.id, db)
    base_conditions = [
        experiment_owner_filter(current_user.id, group_ids),
        Image.embedding.is_(None),
    ]

    if experiment_id:
        await _verify_experiment_ownership(experiment_id, current_user.id, db)
        base_conditions.append(Image.experiment_id == experiment_id)

    # Count images without embeddings
    count_result = await db.execute(
        select(func.count(Image.id))
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(*base_conditions)
    )
    pending_count = count_result.scalar() or 0

    if pending_count == 0:
        return FeatureExtractionTriggerResponse(
            message="All FOV images already have embeddings",
            pending=0,
        )

    # Get image IDs
    images_result = await db.execute(
        select(Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(*base_conditions)
    )
    image_ids = [row[0] for row in images_result.all()]

    background_tasks.add_task(_extract_fov_features_background, image_ids)

    return FeatureExtractionTriggerResponse(
        message=f"FOV feature extraction started for {pending_count} images",
        pending=pending_count,
    )


async def _extract_fov_features_background(image_ids: list) -> None:
    """Background task for FOV feature extraction."""
    from database import get_db_context
    from ml.features import extract_features_for_images

    logger.info(f"Starting background FOV feature extraction for {len(image_ids)} images")

    try:
        async with get_db_context() as db:
            result = await extract_features_for_images(image_ids, db)
            logger.info(
                f"Background FOV feature extraction complete: "
                f"{result['success']} success, {result['failed']} failed"
            )
    except RuntimeError as e:
        logger.error(f"Background FOV feature extraction failed (model error): {e}")
    except Exception as e:
        logger.exception(f"Background FOV feature extraction failed: {e}")
