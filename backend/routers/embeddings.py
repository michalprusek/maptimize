"""Embeddings and UMAP visualization endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status
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
    UmapPointResponse,
    UmapDataResponse,
    FeatureExtractionTriggerResponse,
    FeatureExtractionStatus,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Minimum crops needed for meaningful UMAP
MIN_CROPS_FOR_UMAP = 10


@router.get("/umap", response_model=UmapDataResponse)
async def get_umap_visualization(
    experiment_id: Optional[int] = Query(None, description="Filter by experiment"),
    n_neighbors: int = Query(15, ge=5, le=50, description="UMAP n_neighbors"),
    min_dist: float = Query(0.1, ge=0.0, le=1.0, description="UMAP min_dist"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get UMAP 2D projection of cell crop embeddings.

    Returns coordinates colored by MAP protein type.
    UMAP is computed on-the-fly for the requested scope.
    """
    import numpy as np

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

    result = await db.execute(query)
    crops = result.scalars().all()

    if len(crops) < MIN_CROPS_FOR_UMAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {MIN_CROPS_FOR_UMAP} crops with embeddings for UMAP. Found: {len(crops)}"
        )

    # Extract embeddings and compute UMAP
    embeddings = np.array([c.embedding for c in crops])

    try:
        import umap
        reducer = umap.UMAP(
            n_neighbors=n_neighbors,
            min_dist=min_dist,
            n_components=2,
            metric="euclidean",
            random_state=42,
        )
        projection = reducer.fit_transform(embeddings)
    except Exception as e:
        logger.error(f"UMAP computation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"UMAP computation failed: {str(e)}"
        )

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
    )


@router.get("/status", response_model=FeatureExtractionStatus)
async def get_embedding_status(
    experiment_id: Optional[int] = Query(None, description="Filter by experiment"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get feature extraction status for user's crops."""
    # Base query
    base_query = (
        select(func.count(CellCrop.id))
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(Experiment.user_id == current_user.id)
    )

    if experiment_id:
        base_query = base_query.where(Image.experiment_id == experiment_id)

    # Total crops
    total_result = await db.execute(base_query)
    total = total_result.scalar() or 0

    # Crops with embeddings
    with_emb_result = await db.execute(
        base_query.where(CellCrop.embedding.isnot(None))
    )
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
        crop_ids
    )

    return FeatureExtractionTriggerResponse(
        message=f"Feature extraction started for {pending_count} crops",
        pending=pending_count
    )


async def _extract_features_background(crop_ids: list):
    """Background task for feature extraction."""
    from database import get_db_context
    from ml.features import extract_features_for_crops

    try:
        async with get_db_context() as db:
            result = await extract_features_for_crops(crop_ids, db)
            logger.info(f"Background feature extraction complete: {result}")
    except Exception as e:
        logger.exception(f"Background feature extraction failed: {e}")
