"""Embeddings and UMAP visualization endpoints."""

import logging
from typing import Optional, Union

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
    compute_silhouette_from_umap_coords,
    compute_umap_online,
)
from utils.security import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/umap")
async def get_umap_visualization(
    umap_type: UmapType = Query(UmapType.CROPPED, description="Type: fov or cropped"),
    experiment_id: Optional[int] = Query(None, description="Filter by experiment"),
    n_neighbors: int = Query(15, ge=5, le=50, description="UMAP n_neighbors"),
    min_dist: float = Query(0.1, ge=0.0, le=1.0, description="UMAP min_dist"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Union[UmapDataResponse, UmapFovDataResponse]:
    """
    Get UMAP 2D projection of embeddings.

    - type=cropped: Returns cell crop embeddings (default)
    - type=fov: Returns FOV/image embeddings

    Uses pre-computed coordinates when available, otherwise computes on-the-fly.
    """
    if umap_type == UmapType.FOV:
        return await _get_fov_umap(experiment_id, current_user, db)
    return await _get_cropped_umap(experiment_id, n_neighbors, min_dist, current_user, db)


async def _verify_experiment_ownership(
    experiment_id: int,
    user_id: int,
    db: AsyncSession,
) -> None:
    """Verify that user owns the experiment. Raises HTTPException if not found."""
    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found",
        )


async def _get_cropped_umap(
    experiment_id: Optional[int],
    n_neighbors: int,
    min_dist: float,
    current_user: User,
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
            Experiment.user_id == current_user.id,
            CellCrop.embedding.isnot(None),
        )
    )

    if experiment_id:
        await _verify_experiment_ownership(experiment_id, current_user.id, db)
        query = query.where(Image.experiment_id == experiment_id)

    # Order by ID for deterministic UMAP results
    query = query.order_by(CellCrop.id)
    result = await db.execute(query)
    crops = result.scalars().all()

    if len(crops) < MIN_POINTS_FOR_UMAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {MIN_POINTS_FOR_UMAP} crops with embeddings. Found: {len(crops)}",
        )

    # Check if we have pre-computed coordinates
    all_have_umap = all(c.umap_x is not None and c.umap_y is not None for c in crops)

    if all_have_umap:
        logger.info(f"Using pre-computed UMAP for {len(crops)} crops")
        projection = np.array([[c.umap_x, c.umap_y] for c in crops])
        silhouette = compute_silhouette_from_umap_coords(crops)
    else:
        logger.info(f"Computing UMAP on-the-fly for {len(crops)} crops")
        embeddings = np.array([c.embedding for c in crops])
        projection, silhouette = _compute_umap_with_error_handling(
            embeddings, crops, n_neighbors, min_dist
        )

    # Build response
    points = [
        UmapPointResponse(
            crop_id=crop.id,
            image_id=crop.image_id,
            x=float(projection[i, 0]),
            y=float(projection[i, 1]),
            protein_name=crop.map_protein.name if crop.map_protein else None,
            protein_color=crop.map_protein.color if crop.map_protein else "#888888",
            thumbnail_url=f"/api/images/crops/{crop.id}/image?type=mip",
            bundleness_score=crop.bundleness_score,
        )
        for i, crop in enumerate(crops)
    ]

    return UmapDataResponse(
        points=points,
        total_crops=len(crops),
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        silhouette_score=silhouette,
    )


async def _get_fov_umap(
    experiment_id: Optional[int],
    current_user: User,
    db: AsyncSession,
) -> UmapFovDataResponse:
    """Get UMAP visualization for FOV images."""
    query = (
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(Image.map_protein))
        .where(
            Experiment.user_id == current_user.id,
            Image.embedding.isnot(None),
        )
    )

    if experiment_id:
        await _verify_experiment_ownership(experiment_id, current_user.id, db)
        query = query.where(Image.experiment_id == experiment_id)

    # Order by ID for deterministic UMAP results
    query = query.order_by(Image.id)
    result = await db.execute(query)
    images = result.scalars().all()

    if len(images) < MIN_POINTS_FOR_UMAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {MIN_POINTS_FOR_UMAP} FOV images with embeddings. Found: {len(images)}",
        )

    # Check if we have pre-computed coordinates
    all_have_umap = all(img.umap_x is not None and img.umap_y is not None for img in images)
    computed_at = None

    if all_have_umap:
        logger.info(f"Using pre-computed UMAP for {len(images)} FOV images")
        projection = np.array([[img.umap_x, img.umap_y] for img in images])
        silhouette = compute_silhouette_from_umap_coords(images)
        computed_times = [img.umap_computed_at for img in images if img.umap_computed_at]
        computed_at = min(computed_times) if computed_times else None
    else:
        logger.info(f"Computing FOV UMAP on-the-fly for {len(images)} images")
        embeddings = np.array([img.embedding for img in images])
        projection, silhouette = _compute_umap_with_error_handling(
            embeddings, images, 15, 0.1
        )

    # Build response
    points = [
        UmapFovPointResponse(
            image_id=image.id,
            experiment_id=image.experiment_id,
            x=float(projection[i, 0]),
            y=float(projection[i, 1]),
            protein_name=image.map_protein.name if image.map_protein else None,
            protein_color=image.map_protein.color if image.map_protein else "#888888",
            thumbnail_url=f"/api/images/{image.id}/file?type=thumbnail",
            original_filename=image.original_filename,
        )
        for i, image in enumerate(images)
    ]

    return UmapFovDataResponse(
        points=points,
        total_images=len(images),
        silhouette_score=silhouette,
        is_precomputed=all_have_umap,
        computed_at=computed_at,
    )


def _compute_umap_with_error_handling(
    embeddings: np.ndarray,
    items: list,
    n_neighbors: int,
    min_dist: float,
) -> tuple:
    """Compute UMAP with HTTP error handling for API endpoints."""
    try:
        return compute_umap_online(embeddings, items, n_neighbors, min_dist)
    except ValueError as e:
        logger.error(f"UMAP parameter error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UMAP parameters: {e}",
        )
    except MemoryError:
        logger.error(f"Out of memory computing UMAP for {len(items)} items")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Too many data points for UMAP. Try filtering to a single experiment.",
        )
    except Exception as e:
        logger.exception(f"Unexpected UMAP computation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute UMAP projection. Please try again.",
        )


@router.post("/umap/recompute")
async def trigger_umap_recomputation(
    umap_type: UmapType = Query(..., description="Type to recompute: fov or cropped"),
    experiment_id: Optional[int] = Query(None, description="Experiment scope (optional)"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger UMAP recomputation for the specified type and scope."""
    if experiment_id:
        await _verify_experiment_ownership(experiment_id, current_user.id, db)

    background_tasks.add_task(
        _recompute_umap_background,
        umap_type,
        current_user.id,
        experiment_id,
    )

    return {"message": f"UMAP recomputation started for {umap_type.value}"}


async def _recompute_umap_background(
    umap_type: UmapType,
    user_id: int,
    experiment_id: Optional[int],
) -> None:
    """Background task for UMAP recomputation."""
    from database import get_db_context
    from services.umap_service import compute_crop_umap, compute_fov_umap

    logger.info(
        f"Starting background UMAP recomputation for {umap_type.value}, "
        f"user {user_id}, experiment {experiment_id or 'all'}"
    )

    try:
        async with get_db_context() as db:
            if umap_type == UmapType.FOV:
                result = await compute_fov_umap(user_id, db, experiment_id)
            else:
                result = await compute_crop_umap(user_id, db, experiment_id)

            if "error" in result:
                logger.warning(f"UMAP recomputation: {result['error']}")
            else:
                logger.info(f"UMAP recomputation complete: {result}")
    except Exception as e:
        logger.exception(f"UMAP recomputation failed: {e}")


@router.get("/status", response_model=FeatureExtractionStatus)
async def get_embedding_status(
    experiment_id: Optional[int] = Query(None, description="Filter by experiment"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeatureExtractionStatus:
    """Get feature extraction status for user's crops."""
    base_conditions = [Experiment.user_id == current_user.id]
    if experiment_id:
        base_conditions.append(Image.experiment_id == experiment_id)

    # Total crops query
    total_query = (
        select(func.count(CellCrop.id))
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(*base_conditions)
    )
    total = (await db.execute(total_query)).scalar() or 0

    # Crops with embeddings
    with_emb_query = (
        select(func.count(CellCrop.id))
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(*base_conditions, CellCrop.embedding.isnot(None))
    )
    with_embeddings = (await db.execute(with_emb_query)).scalar() or 0

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
    base_conditions = [
        Experiment.user_id == current_user.id,
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
