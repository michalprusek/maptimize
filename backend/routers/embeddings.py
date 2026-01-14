"""Embeddings and UMAP visualization endpoints."""

import logging
from typing import Optional, Union

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.user import User
from models.cell_crop import CellCrop
from models.image import Image
from models.experiment import Experiment
from utils.security import get_current_user
from schemas.embeddings import (
    UmapType,
    UmapPointResponse,
    UmapDataResponse,
    UmapFovPointResponse,
    UmapFovDataResponse,
    FeatureExtractionTriggerResponse,
    FeatureExtractionStatus,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Minimum points needed for meaningful UMAP
MIN_POINTS_FOR_UMAP = 10


@router.get("/umap")
async def get_umap_visualization(
    umap_type: UmapType = Query(UmapType.CROPPED, description="Type: fov or cropped"),
    experiment_id: Optional[int] = Query(None, description="Filter by experiment"),
    n_neighbors: int = Query(15, ge=5, le=50, description="UMAP n_neighbors"),
    min_dist: float = Query(0.1, ge=0.0, le=1.0, description="UMAP min_dist"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Union[UmapDataResponse, UmapFovDataResponse]:
    """
    Get UMAP 2D projection of embeddings.

    - type=cropped: Returns cell crop embeddings (default)
    - type=fov: Returns FOV/image embeddings

    Uses pre-computed coordinates when available, otherwise computes on-the-fly.
    """
    if umap_type == UmapType.FOV:
        return await _get_fov_umap(experiment_id, current_user, db)
    else:
        return await _get_cropped_umap(experiment_id, n_neighbors, min_dist, current_user, db)


async def _get_cropped_umap(
    experiment_id: Optional[int],
    n_neighbors: int,
    min_dist: float,
    current_user: User,
    db: AsyncSession
) -> UmapDataResponse:
    """Get UMAP visualization for cell crops."""
    # Build query for crops with embeddings
    query = (
        select(CellCrop)
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(
            selectinload(CellCrop.map_protein),
            selectinload(CellCrop.image)
        )
        .where(
            Experiment.user_id == current_user.id,
            CellCrop.embedding.isnot(None)
        )
    )

    if experiment_id:
        # Verify ownership
        exp_result = await db.execute(
            select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.user_id == current_user.id
            )
        )
        if not exp_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Experiment not found"
            )
        query = query.where(Image.experiment_id == experiment_id)

    # IMPORTANT: Order by ID for deterministic UMAP results
    query = query.order_by(CellCrop.id)
    result = await db.execute(query)
    crops = result.scalars().all()

    if len(crops) < MIN_POINTS_FOR_UMAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {MIN_POINTS_FOR_UMAP} crops with embeddings for UMAP. Found: {len(crops)}"
        )

    # Check if we have pre-computed coordinates
    all_have_umap = all(c.umap_x is not None and c.umap_y is not None for c in crops)

    if all_have_umap:
        # Use pre-computed coordinates
        logger.info(f"Using pre-computed UMAP for {len(crops)} crops")
        projection = np.array([[c.umap_x, c.umap_y] for c in crops])
        silhouette = _compute_silhouette_from_crops(crops)
    else:
        # Compute UMAP on-the-fly
        logger.info(f"Computing UMAP on-the-fly for {len(crops)} crops")
        embeddings = np.array([c.embedding for c in crops])
        projection, silhouette = _compute_umap(embeddings, crops, n_neighbors, min_dist)

    # Build response
    points = []
    for i, crop in enumerate(crops):
        protein = crop.map_protein
        points.append(UmapPointResponse(
            crop_id=crop.id,
            image_id=crop.image_id,
            x=float(projection[i, 0]),
            y=float(projection[i, 1]),
            protein_name=protein.name if protein else None,
            protein_color=protein.color if protein else "#888888",
            thumbnail_url=f"/api/images/crops/{crop.id}/image?type=mip",
            bundleness_score=crop.bundleness_score,
        ))

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
    db: AsyncSession
) -> UmapFovDataResponse:
    """Get UMAP visualization for FOV images."""
    # Build query for images with embeddings
    query = (
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(Image.map_protein))
        .where(
            Experiment.user_id == current_user.id,
            Image.embedding.isnot(None)
        )
    )

    if experiment_id:
        # Verify ownership
        exp_result = await db.execute(
            select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.user_id == current_user.id
            )
        )
        if not exp_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Experiment not found"
            )
        query = query.where(Image.experiment_id == experiment_id)

    # IMPORTANT: Order by ID for deterministic UMAP results
    query = query.order_by(Image.id)
    result = await db.execute(query)
    images = result.scalars().all()

    if len(images) < MIN_POINTS_FOR_UMAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {MIN_POINTS_FOR_UMAP} FOV images with embeddings for UMAP. Found: {len(images)}"
        )

    # Check if we have pre-computed coordinates
    all_have_umap = all(img.umap_x is not None and img.umap_y is not None for img in images)
    computed_at = None

    if all_have_umap:
        # Use pre-computed coordinates
        logger.info(f"Using pre-computed UMAP for {len(images)} FOV images")
        projection = np.array([[img.umap_x, img.umap_y] for img in images])
        silhouette = _compute_silhouette_from_images(images)
        # Get the oldest computed_at as the effective computation time
        computed_times = [img.umap_computed_at for img in images if img.umap_computed_at]
        computed_at = min(computed_times) if computed_times else None
    else:
        # Compute UMAP on-the-fly
        logger.info(f"Computing FOV UMAP on-the-fly for {len(images)} images")
        embeddings = np.array([img.embedding for img in images])
        projection, silhouette = _compute_umap(embeddings, images, 15, 0.1)

    # Build response
    points = []
    for i, image in enumerate(images):
        protein = image.map_protein
        points.append(UmapFovPointResponse(
            image_id=image.id,
            experiment_id=image.experiment_id,
            x=float(projection[i, 0]),
            y=float(projection[i, 1]),
            protein_name=protein.name if protein else None,
            protein_color=protein.color if protein else "#888888",
            thumbnail_url=f"/api/images/{image.id}/file?type=thumbnail",
            original_filename=image.original_filename,
        ))

    return UmapFovDataResponse(
        points=points,
        total_images=len(images),
        silhouette_score=silhouette,
        is_precomputed=all_have_umap,
        computed_at=computed_at,
    )


def _compute_umap(
    embeddings: np.ndarray,
    items: list,
    n_neighbors: int,
    min_dist: float,
) -> tuple:
    """Compute UMAP projection with deterministic settings."""
    import umap

    # L2-normalize for cosine distance
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    embeddings_norm = embeddings / norms

    try:
        # Initialize seed for fully deterministic UMAP results
        np.random.seed(42)
        reducer = umap.UMAP(
            n_neighbors=n_neighbors,
            min_dist=min_dist,
            n_components=2,
            metric="cosine",
            random_state=42,
        )
        projection = reducer.fit_transform(embeddings_norm)
    except ValueError as e:
        logger.error(f"UMAP parameter error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UMAP parameters: {str(e)}"
        )
    except MemoryError:
        logger.error(f"Out of memory computing UMAP for {len(items)} items")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Too many data points for UMAP. Try filtering to a single experiment."
        )
    except Exception as e:
        logger.exception(f"Unexpected UMAP computation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute UMAP projection. Please try again."
        )

    # Compute silhouette score
    silhouette = _compute_silhouette(embeddings_norm, items)
    return projection, silhouette


def _compute_silhouette(embeddings: np.ndarray, items: list) -> Optional[float]:
    """Compute silhouette score based on protein labels."""
    labeled_indices = []
    labels = []

    for i, item in enumerate(items):
        protein = getattr(item, 'map_protein', None)
        if protein is not None:
            labeled_indices.append(i)
            labels.append(protein.id)

    if len(labeled_indices) < 10 or len(set(labels)) < 2:
        return None

    try:
        from sklearn.metrics import silhouette_score
        labeled_embeddings = embeddings[labeled_indices]
        return float(silhouette_score(labeled_embeddings, labels, metric="cosine"))
    except (ValueError, ImportError) as e:
        logger.warning(f"Could not compute silhouette score: {e}")
        return None


def _compute_silhouette_from_crops(crops: list) -> Optional[float]:
    """Compute silhouette from pre-computed UMAP coordinates for crops."""
    labeled = [(c.umap_x, c.umap_y, c.map_protein.id)
               for c in crops if c.map_protein is not None]

    if len(labeled) < 10 or len(set(l[2] for l in labeled)) < 2:
        return None

    try:
        from sklearn.metrics import silhouette_score
        coords = np.array([[l[0], l[1]] for l in labeled])
        labels = [l[2] for l in labeled]
        return float(silhouette_score(coords, labels, metric="euclidean"))
    except (ValueError, ImportError):
        return None


def _compute_silhouette_from_images(images: list) -> Optional[float]:
    """Compute silhouette from pre-computed UMAP coordinates for images."""
    labeled = [(img.umap_x, img.umap_y, img.map_protein.id)
               for img in images if img.map_protein is not None]

    if len(labeled) < 10 or len(set(l[2] for l in labeled)) < 2:
        return None

    try:
        from sklearn.metrics import silhouette_score
        coords = np.array([[l[0], l[1]] for l in labeled])
        labels = [l[2] for l in labeled]
        return float(silhouette_score(coords, labels, metric="euclidean"))
    except (ValueError, ImportError):
        return None


@router.post("/umap/recompute")
async def trigger_umap_recomputation(
    umap_type: UmapType = Query(..., description="Type to recompute: fov or cropped"),
    experiment_id: Optional[int] = Query(None, description="Experiment scope (optional)"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Trigger UMAP recomputation for the specified type and scope.

    This will re-compute and store UMAP coordinates for all items in the scope.
    """
    if experiment_id:
        # Verify ownership
        exp_result = await db.execute(
            select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.user_id == current_user.id
            )
        )
        if not exp_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Experiment not found"
            )

    # Trigger background recomputation
    background_tasks.add_task(
        _recompute_umap_background,
        umap_type,
        current_user.id,
        experiment_id
    )

    return {"message": f"UMAP recomputation started for {umap_type.value}"}


async def _recompute_umap_background(
    umap_type: UmapType,
    user_id: int,
    experiment_id: Optional[int]
):
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
    db: AsyncSession = Depends(get_db)
):
    """Get feature extraction status for user's crops."""
    # Build base conditions (without the embedding filter)
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
    total_result = await db.execute(total_query)
    total = total_result.scalar() or 0

    # Crops with embeddings query (separate query, not mutating base)
    with_emb_query = (
        select(func.count(CellCrop.id))
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(*base_conditions, CellCrop.embedding.isnot(None))
    )
    with_emb_result = await db.execute(with_emb_query)
    with_embeddings = with_emb_result.scalar() or 0

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
    db: AsyncSession = Depends(get_db)
):
    """
    Trigger feature extraction for crops without embeddings.
    Runs in background.
    """
    # Verify ownership
    exp_result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.user_id == current_user.id
        )
    )
    if not exp_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found"
        )

    # Count crops without embeddings
    count_result = await db.execute(
        select(func.count(CellCrop.id))
        .join(Image, CellCrop.image_id == Image.id)
        .where(
            Image.experiment_id == experiment_id,
            CellCrop.embedding.is_(None)
        )
    )
    pending_count = count_result.scalar() or 0

    if pending_count == 0:
        return FeatureExtractionTriggerResponse(
            message="All crops already have embeddings",
            pending=0
        )

    # Get crop IDs
    crops_result = await db.execute(
        select(CellCrop.id)
        .join(Image, CellCrop.image_id == Image.id)
        .where(
            Image.experiment_id == experiment_id,
            CellCrop.embedding.is_(None)
        )
    )
    crop_ids = [row[0] for row in crops_result.all()]

    # Trigger background extraction
    background_tasks.add_task(
        _extract_features_background,
        crop_ids,
        experiment_id
    )

    return FeatureExtractionTriggerResponse(
        message=f"Feature extraction started for {pending_count} crops",
        pending=pending_count
    )


async def _extract_features_background(crop_ids: list, experiment_id: int):
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
    experiment_id: Optional[int] = Query(None, description="Experiment ID (optional, all if not specified)"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Trigger FOV embedding extraction for images without embeddings.
    Runs in background.
    """
    # Build query for images without embeddings
    base_conditions = [
        Experiment.user_id == current_user.id,
        Image.embedding.is_(None)
    ]

    if experiment_id:
        # Verify ownership
        exp_result = await db.execute(
            select(Experiment).where(
                Experiment.id == experiment_id,
                Experiment.user_id == current_user.id
            )
        )
        if not exp_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Experiment not found"
            )
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
            pending=0
        )

    # Get image IDs
    images_result = await db.execute(
        select(Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(*base_conditions)
    )
    image_ids = [row[0] for row in images_result.all()]

    # Trigger background extraction
    background_tasks.add_task(
        _extract_fov_features_background,
        image_ids
    )

    return FeatureExtractionTriggerResponse(
        message=f"FOV feature extraction started for {pending_count} images",
        pending=pending_count
    )


async def _extract_fov_features_background(image_ids: list):
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
