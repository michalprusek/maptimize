"""Embeddings and UMAP visualization endpoints."""

import logging
from typing import Optional, TypeVar, Union

import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.cell_crop import CellCrop
from models.experiment import Experiment
from models.image import Image
from models.user import User
from schemas.embeddings import (
    FeatureExtractionStatus,
    FeatureExtractionTriggerResponse,
    UmapDataResponse,
    UmapFovDataResponse,
    UmapFovPointResponse,
    UmapPointResponse,
    UmapType,
)
from services.umap_service import (
    MIN_POINTS_FOR_UMAP,
    clear_refresh_error,
    compute_silhouette,
    get_refresh_error,
    refresh_umap_scope,
)
from utils.security import get_current_user
from utils.groups import experiment_owner_filter, get_user_group_id

router = APIRouter()
logger = logging.getLogger(__name__)

# A CellCrop or an Image — both carry umap_x/umap_y and an embedding.
T = TypeVar("T")


@router.get("/umap")
async def get_umap_visualization(
    umap_type: UmapType = Query(UmapType.CROPPED, description="Type: fov or cropped"),
    experiment_id: Optional[int] = Query(None, description="Filter by experiment"),
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
    """
    group_id = await get_user_group_id(current_user.id, db)
    if umap_type is UmapType.FOV:
        return await _get_fov_umap(
            experiment_id, current_user, group_id, background_tasks, db
        )
    return await _get_cropped_umap(
        experiment_id, current_user, group_id, background_tasks, db
    )


def _take_precomputed(
    items: list[T],
    umap_type: UmapType,
    user_id: int,
    group_id: Optional[int],
    background_tasks: BackgroundTasks,
) -> tuple[list[T], bool, Optional[str]]:
    """
    Select the items that already have coordinates, refreshing the rest in the background.

    Items whose embeddings arrived after the last projection (new upload or crop
    edit) have no coordinates yet. Serve what exists now and schedule the re-fit:
    the fit runs after the response is sent, and the client polls until is_stale
    clears. Never fit on the read path — that stalls page load for seconds.

    A scope whose last refresh failed is NOT rescheduled; its error is returned so
    the client can stop polling and show it. Otherwise each poll would kick off
    another doomed multi-second fit, forever, in silence.

    Returns (items with coordinates, is_stale, refresh_error).
    """
    with_umap = [i for i in items if i.umap_x is not None and i.umap_y is not None]

    stale_count = len(items) - len(with_umap)
    if stale_count == 0:
        return with_umap, False, None

    refresh_error = get_refresh_error(umap_type, user_id, group_id)
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
    background_tasks.add_task(refresh_umap_scope, umap_type, user_id, group_id)
    return with_umap, True, None


async def _verify_experiment_ownership(
    experiment_id: int,
    user_id: int,
    db: AsyncSession,
) -> None:
    """Verify that user owns the experiment or is in the same group."""
    group_id = await get_user_group_id(user_id, db)
    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            experiment_owner_filter(user_id, group_id),
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found",
        )


async def _get_cropped_umap(
    experiment_id: Optional[int],
    current_user: User,
    group_id: Optional[int],
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
            experiment_owner_filter(current_user.id, group_id),
            CellCrop.embedding.isnot(None),
        )
    )

    if experiment_id:
        await _verify_experiment_ownership(experiment_id, current_user.id, db)
        query = query.where(Image.experiment_id == experiment_id)

    # Stable order so the payload does not reshuffle between polls
    query = query.order_by(CellCrop.id)
    result = await db.execute(query)
    crops = result.scalars().all()

    if len(crops) < MIN_POINTS_FOR_UMAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {MIN_POINTS_FOR_UMAP} crops with embeddings. Found: {len(crops)}",
        )

    crops_with_umap, is_stale, refresh_error = _take_precomputed(
        crops, UmapType.CROPPED, current_user.id, group_id, background_tasks
    )

    # Counts every crop with an embedding, including the ones still awaiting
    # coordinates — it must not shrink to the plotted subset.
    total_crops = len(crops)

    if not crops_with_umap:
        return UmapDataResponse(
            points=[],
            total_crops=total_crops,
            silhouette_score=None,
            is_stale=is_stale,
            refresh_error=refresh_error,
        )

    logger.info(f"Using pre-computed UMAP for {len(crops_with_umap)}/{total_crops} crops")
    embeddings = np.array([c.embedding for c in crops_with_umap])
    silhouette = compute_silhouette(embeddings, crops_with_umap)

    # Build response
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
        silhouette_score=silhouette,
        is_stale=is_stale,
        refresh_error=refresh_error,
    )


async def _get_fov_umap(
    experiment_id: Optional[int],
    current_user: User,
    group_id: Optional[int],
    background_tasks: BackgroundTasks,
    db: AsyncSession,
) -> UmapFovDataResponse:
    """Get UMAP visualization for FOV images."""
    query = (
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(Image.map_protein))
        .where(
            experiment_owner_filter(current_user.id, group_id),
            Image.embedding.isnot(None),
        )
    )

    if experiment_id:
        await _verify_experiment_ownership(experiment_id, current_user.id, db)
        query = query.where(Image.experiment_id == experiment_id)

    # Stable order so the payload does not reshuffle between polls
    query = query.order_by(Image.id)
    result = await db.execute(query)
    images = result.scalars().all()

    if len(images) < MIN_POINTS_FOR_UMAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {MIN_POINTS_FOR_UMAP} FOV images with embeddings. Found: {len(images)}",
        )

    images_with_umap, is_stale, refresh_error = _take_precomputed(
        images, UmapType.FOV, current_user.id, group_id, background_tasks
    )

    # Counts every image with an embedding, including the ones still awaiting
    # coordinates — it must not shrink to the plotted subset.
    total_images = len(images)

    if not images_with_umap:
        return UmapFovDataResponse(
            points=[],
            total_images=total_images,
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

    # Build response
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
    group_id = await get_user_group_id(current_user.id, db)
    # Clear the recorded failure so reads resume auto-scheduling this scope.
    clear_refresh_error(umap_type, current_user.id, group_id)
    background_tasks.add_task(refresh_umap_scope, umap_type, current_user.id, group_id)

    return {"message": f"UMAP recomputation started for {umap_type.value}"}


@router.get("/status", response_model=FeatureExtractionStatus)
async def get_embedding_status(
    experiment_id: Optional[int] = Query(None, description="Filter by experiment"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeatureExtractionStatus:
    """Get feature extraction status for user's crops."""
    group_id = await get_user_group_id(current_user.id, db)
    base_conditions = [experiment_owner_filter(current_user.id, group_id)]
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
    group_id = await get_user_group_id(current_user.id, db)
    base_conditions = [
        experiment_owner_filter(current_user.id, group_id),
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
