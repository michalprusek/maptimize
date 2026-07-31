"""Embeddings and UMAP visualization endpoints."""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Type, TypeVar, Union

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.cell_crop import CellCrop
from models.experiment import Experiment
from models.image import Image, MapProtein
from models.microscope import Microscope
from models.ptm import PTM
from models.user import User
from schemas.embeddings import (
    DiscriminantDataResponse,
    DiscriminantClassScore,
    DiscriminantMetrics,
    DiscriminantPointResponse,
    FeatureExtractionStatus,
    FeatureExtractionTriggerResponse,
    UmapDataResponse,
    UmapFacetRow,
    UmapFovDataResponse,
    UmapFovPointResponse,
    UmapPointResponse,
    UmapType,
)
from services import discriminant_service
from utils.facets import facet_clause, real_ids
from services.umap_service import (
    MIN_POINTS_FOR_UMAP,
    clear_refresh_error,
    compute_silhouette,
    get_refresh_error,
    refresh_umap_scope,
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

    @property
    def is_active(self) -> bool:
        """True when the user has narrowed the plot at all."""
        return any(
            (self.experiment_ids, self.microscope_ids, self.protein_ids, self.ptm_ids)
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
) -> FacetSelection:
    """Collect the four dashboard filters into one value.

    A dependency rather than four parameters on the handler: it keeps the facets
    together as the single thing they are, lets a future endpoint take the same
    filter without re-declaring them, and means a caller that constructs the
    handler's arguments itself supplies one object instead of four lists.
    """
    return FacetSelection(
        experiment_ids=experiment_id or [],
        microscope_ids=microscope_id or [],
        protein_ids=protein_id or [],
        ptm_ids=ptm_id or [],
    )


@router.get("/umap")
async def get_umap_visualization(
    umap_type: UmapType = Query(UmapType.CROPPED, description="Type: fov or cropped"),
    selection: FacetSelection = Depends(facet_selection),
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

    The four filters are OR within a facet and AND across facets. Passing id 0
    for microscope, protein or PTM also matches rows with nothing assigned.
    """
    # Validate references up front so a stale or deleted id fails with a clear
    # 404 instead of silently matching nothing and looking like an empty result.
    # Reference data is shared, so anyone can delete a value another user's open
    # tab still has ticked.
    await _verify_reference_ids(db, Microscope, selection.microscope_ids, "Microscope")
    await _verify_reference_ids(db, MapProtein, selection.protein_ids, "MAP protein")
    await _verify_reference_ids(db, PTM, selection.ptm_ids, "PTM")

    group_ids = await get_user_group_ids(current_user.id, db)
    if selection.experiment_ids:
        await _verify_experiments_visible(
            selection.experiment_ids, current_user.id, group_ids, db
        )

    if umap_type is UmapType.FOV:
        return await _get_fov_umap(
            selection, current_user, group_ids, background_tasks, db
        )
    return await _get_cropped_umap(
        selection, current_user, group_ids, background_tasks, db
    )


@router.get("/discriminant", response_model=DiscriminantDataResponse)
async def get_discriminant_visualization(
    selection: FacetSelection = Depends(facet_selection),
    include_points: bool = Query(
        True,
        description=(
            "Set false for the metrics only. The point list runs to thousands of "
            "rows, which is useful to a plot and useless to a reader."
        ),
    ),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DiscriminantDataResponse:
    """
    Supervised projection that maximally separates the MAP proteins.

    Unlike the UMAP this is fitted against the protein labels, so it always looks
    separated — the `metrics` block is what says whether that separation means
    anything, and the client renders it beside the plot rather than on demand.

    Fitting takes minutes (a grouped cross-validation plus a permutation null),
    so it runs in the background and the first call returns `is_computing`. The
    projection is cached per scope; group members share one.

    ⚠️ The filter chooses which points come back, never which are fitted. Refitting
    per filter would put two filtered views in incomparable coordinate systems
    and silently change what the axes mean as the user clicks.
    """
    await _verify_reference_ids(db, Microscope, selection.microscope_ids, "Microscope")
    await _verify_reference_ids(db, MapProtein, selection.protein_ids, "MAP protein")
    await _verify_reference_ids(db, PTM, selection.ptm_ids, "PTM")

    group_ids = await get_user_group_ids(current_user.id, db)
    if selection.experiment_ids:
        await _verify_experiments_visible(
            selection.experiment_ids, current_user.id, group_ids, db
        )

    facets = await _load_facets(UmapType.CROPPED, current_user.id, group_ids, db)
    key = discriminant_service.scope_key(current_user.id, group_ids)
    cached = discriminant_service.cached(key)

    if cached is None:
        # A recorded failure describes the corpus at one instant. If the lab has
        # since fixed exactly what it was told to fix, retire the message rather
        # than serving it forever.
        discriminant_service.clear_failure_if_corpus_moved(
            key, await discriminant_service.current_fingerprint(current_user.id, group_ids, db)
        )
        error = discriminant_service.compute_error(key)
        if error is None and not discriminant_service.is_computing(key):
            background_tasks.add_task(
                discriminant_service.refresh_discriminant_scope,
                current_user.id,
                group_ids,
            )
        return DiscriminantDataResponse(
            points=[],
            total_crops=0,
            facets=facets,
            metrics=None,
            is_computing=error is None,
            compute_error=error,
        )

    result = cached
    coords = result.coords_by_id()

    # The fit is a snapshot; the labels and colours below are read live. If the
    # corpus has moved on, a re-annotated crop would be drawn at its OLD
    # coordinate in its NEW colour — a point sitting in the wrong cluster wearing
    # the right label, under a score cross-validated against labels that no
    # longer exist. Say so, and schedule the refit that the code used to claim
    # would happen by itself.
    is_stale = result.fingerprint != await discriminant_service.current_fingerprint(
        current_user.id, group_ids, db
    )
    if is_stale and not discriminant_service.is_computing(key):
        background_tasks.add_task(
            discriminant_service.refresh_discriminant_scope, current_user.id, group_ids
        )

    metrics = DiscriminantMetrics(
        balanced_accuracy=result.balanced_accuracy,
        chance=result.chance,
        null_mean=result.null_mean,
        null_max=result.null_max,
        null_p95=result.null_p95,
        unscoreable_proteins=list(result.unscoreable_proteins),
        p_value=result.p_value,
        per_class=[
            DiscriminantClassScore(protein=name, recall=recall, n_crops=n)
            for name, recall, n in result.per_class
        ],
        n_permutations=result.n_permutations,
        n_proteins=result.n_proteins,
        n_experiments=result.n_experiments,
    )

    if not include_points:
        return DiscriminantDataResponse(
            points=[], total_crops=len(coords), facets=facets, metrics=metrics,
            is_computing=False, is_stale=is_stale, compute_error=None,
        )

    # Same corpus the projection was fitted on, narrowed by the filter. Ordered
    # by id so the payload does not reshuffle between polls.
    query = (
        select(CellCrop)
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(CellCrop.map_protein), selectinload(CellCrop.image))
        .where(
            experiment_owner_filter(current_user.id, group_ids),
            CellCrop.embedding.isnot(None),
            CellCrop.map_protein_id.isnot(None),
        )
    )
    query = _apply_facets(query, selection, CellCrop.map_protein_id)
    crops = (await db.execute(query.order_by(CellCrop.id))).scalars().all()

    points = [
        DiscriminantPointResponse(
            crop_id=crop.id,
            image_id=crop.image_id,
            experiment_id=crop.image.experiment_id,
            x=coords[crop.id][0],
            y=coords[crop.id][1],
            protein_name=crop.map_protein.name if crop.map_protein else None,
            protein_color=crop.map_protein.color if crop.map_protein else "#888888",
            thumbnail_url=f"/api/images/crops/{crop.id}/image?type=mip",
            bundleness_score=crop.bundleness_score,
        )
        # A crop added since the fit has no coordinates. Skip it rather than
        # invent one — placing an unseen crop somewhere would fabricate a data
        # point — but the count below reports every matching crop, so the gap is
        # visible rather than silently shrinking the total to what got drawn.
        for crop in crops
        if crop.id in coords
    ]

    return DiscriminantDataResponse(
        points=points,
        # Every crop the filter matched, including any the fit has not seen yet.
        # Reporting len(points) instead would make an out-of-date projection look
        # like a smaller corpus.
        total_crops=len(crops),
        facets=facets,
        metrics=metrics,
        is_computing=False,
        is_stale=is_stale or len(points) < len(crops),
        compute_error=None,
    )


@router.post("/discriminant/recompute")
async def trigger_discriminant_recomputation(
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Refit this scope's projection, clearing any recorded failure."""
    group_ids = await get_user_group_ids(current_user.id, db)
    key = discriminant_service.scope_key(current_user.id, group_ids)
    discriminant_service.invalidate(key)
    background_tasks.add_task(
        discriminant_service.refresh_discriminant_scope, current_user.id, group_ids
    )
    return {"message": "Discriminant projection scheduled"}


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
    coarsest grouping that still separates every facet, because microscope and
    PTM live on the experiment while protein is per point — roughly one row per
    experiment, so this stays far cheaper than the point query and loads no
    embeddings.
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
            protein_id=protein_id,
            count=count,
        )
        for exp_id, exp_name, microscope_id, ptm_id, protein_id, count in result.all()
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
        facet_clause(protein_column, selection.protein_ids),
    ]
    for clause in clauses:
        if clause is not None:
            query = query.where(clause)
    return query


async def _get_cropped_umap(
    selection: FacetSelection,
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
            silhouette_score=None,
            is_stale=is_stale,
            refresh_error=refresh_error,
        )

    logger.info(f"Using pre-computed UMAP for {len(crops_with_umap)}/{total_crops} crops")
    embeddings = np.array([c.embedding for c in crops_with_umap])
    silhouette = compute_silhouette(embeddings, crops_with_umap)

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
        )
        for crop in crops_with_umap
    ]

    return UmapDataResponse(
        points=points,
        total_crops=total_crops,
        facets=facets,
        silhouette_score=silhouette,
        is_stale=is_stale,
        refresh_error=refresh_error,
    )


async def _get_fov_umap(
    selection: FacetSelection,
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
            silhouette_score=None,
            computed_at=None,
            is_stale=is_stale,
            refresh_error=refresh_error,
        )

    logger.info(f"Using pre-computed UMAP for {len(images_with_umap)}/{total_images} FOV images")
    embeddings = np.array([img.embedding for img in images_with_umap])
    silhouette = compute_silhouette(embeddings, images_with_umap)
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
        )
        for image in images_with_umap
    ]

    return UmapFovDataResponse(
        points=points,
        total_images=total_images,
        facets=facets,
        silhouette_score=silhouette,
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
    Force a UMAP recomputation for the caller's scope.

    Reads schedule refreshes automatically, so this is the retry path for a scope
    whose refresh failed (reads stop rescheduling those) and an escape hatch for
    re-fitting coordinates that are already complete.
    """
    group_ids = await get_user_group_ids(current_user.id, db)
    # Clear the recorded failure so reads resume auto-scheduling this scope.
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
