"""UMAP computation service for pre-computing 2D projections.

This service handles deterministic UMAP computation and storage of
coordinates for both cell crops and FOV images.

SSOT for UMAP-related constants and computation functions.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.cell_crop import CellCrop
from models.experiment import Experiment
from models.image import Image

logger = logging.getLogger(__name__)

# =============================================================================
# UMAP Constants (Single Source of Truth)
# =============================================================================
MIN_POINTS_FOR_UMAP = 10
DEFAULT_N_NEIGHBORS = 15
DEFAULT_MIN_DIST = 0.1
RANDOM_STATE = 42


# =============================================================================
# Core UMAP Computation Functions
# =============================================================================


def _compute_silhouette(
    embeddings: np.ndarray,
    items: list,
) -> Optional[float]:
    """
    Compute silhouette score based on protein labels.

    Args:
        embeddings: Normalized embedding vectors
        items: List of CellCrop or Image objects with map_protein attribute

    Returns:
        Silhouette score (-1 to 1) or None if not computable
    """
    labeled_indices = []
    labels = []

    for i, item in enumerate(items):
        protein = getattr(item, 'map_protein', None)
        if protein is not None:
            labeled_indices.append(i)
            labels.append(protein.id)

    # Need at least 10 labeled items and 2 different labels
    if len(labeled_indices) < 10 or len(set(labels)) < 2:
        return None

    try:
        from sklearn.metrics import silhouette_score
        labeled_embeddings = embeddings[labeled_indices]
        return float(silhouette_score(labeled_embeddings, labels, metric="cosine"))
    except (ValueError, ImportError) as e:
        logger.warning(f"Could not compute silhouette score: {e}")
        return None


def compute_umap_online(
    embeddings: np.ndarray,
    items: list,
    n_neighbors: int = DEFAULT_N_NEIGHBORS,
    min_dist: float = DEFAULT_MIN_DIST,
) -> Tuple[np.ndarray, Optional[float]]:
    """
    Compute UMAP projection on-the-fly.

    Used by API endpoints when pre-computed coordinates are not available
    or when custom parameters are requested.

    Args:
        embeddings: Array of embedding vectors (N x D)
        items: List of CellCrop or Image objects (for protein labels)
        n_neighbors: UMAP n_neighbors parameter
        min_dist: UMAP min_dist parameter

    Returns:
        Tuple of (projection array N x 2, silhouette score or None)

    Raises:
        ValueError: If UMAP parameters are invalid
        MemoryError: If too many data points
    """
    import umap

    np.random.seed(RANDOM_STATE)

    # L2 normalize for cosine similarity
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    embeddings_norm = embeddings / norms

    reducer = umap.UMAP(
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        n_components=2,
        metric="cosine",
        random_state=RANDOM_STATE,
    )
    projection = reducer.fit_transform(embeddings_norm)
    silhouette = _compute_silhouette(embeddings_norm, items)

    return projection, silhouette


def compute_silhouette_from_umap_coords(items: list) -> Optional[float]:
    """
    Compute silhouette score from pre-computed UMAP coordinates.

    Works with both CellCrop and Image objects that have umap_x, umap_y,
    and map_protein attributes.

    Args:
        items: List of objects with umap_x, umap_y, and map_protein attributes

    Returns:
        Silhouette score (-1 to 1) or None if not computable
    """
    labeled = [
        (item.umap_x, item.umap_y, item.map_protein.id)
        for item in items
        if item.map_protein is not None
    ]

    if len(labeled) < 10 or len(set(l[2] for l in labeled)) < 2:
        return None

    try:
        from sklearn.metrics import silhouette_score
        coords = np.array([[l[0], l[1]] for l in labeled])
        labels = [l[2] for l in labeled]
        return float(silhouette_score(coords, labels, metric="euclidean"))
    except (ValueError, ImportError):
        return None


# =============================================================================
# Batch UMAP Computation (stores to DB)
# =============================================================================


async def compute_crop_umap(
    user_id: int,
    db: AsyncSession,
    experiment_id: Optional[int] = None,
) -> dict:
    """
    Compute UMAP for cell crops and store coordinates in database.

    Args:
        user_id: User ID for ownership filtering
        db: AsyncSession database connection
        experiment_id: Optional experiment ID to filter by

    Returns:
        dict with success count, silhouette score, and computed_at
    """
    query = (
        select(CellCrop)
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(CellCrop.map_protein))
        .where(
            Experiment.user_id == user_id,
            CellCrop.embedding.isnot(None),
        )
        .order_by(CellCrop.id)
    )

    if experiment_id:
        query = query.where(Image.experiment_id == experiment_id)

    result = await db.execute(query)
    crops = result.scalars().all()

    if len(crops) < MIN_POINTS_FOR_UMAP:
        return {
            "error": f"Need at least {MIN_POINTS_FOR_UMAP} crops with embeddings",
            "count": len(crops),
        }

    embeddings = np.array([c.embedding for c in crops])
    projection, silhouette = compute_umap_online(embeddings, crops)

    now = datetime.now(timezone.utc)
    for i, crop in enumerate(crops):
        crop.umap_x = float(projection[i, 0])
        crop.umap_y = float(projection[i, 1])
        crop.umap_computed_at = now

    await db.commit()

    logger.info(
        f"Computed crop UMAP for user {user_id}"
        f"{f' experiment {experiment_id}' if experiment_id else ''}: "
        f"{len(crops)} crops, silhouette={silhouette:.3f if silhouette else 'N/A'}"
    )

    return {
        "success": len(crops),
        "silhouette_score": silhouette,
        "computed_at": now.isoformat(),
    }


async def compute_fov_umap(
    user_id: int,
    db: AsyncSession,
    experiment_id: Optional[int] = None,
) -> dict:
    """
    Compute UMAP for FOV images and store coordinates in database.

    Args:
        user_id: User ID for ownership filtering
        db: AsyncSession database connection
        experiment_id: Optional experiment ID to filter by

    Returns:
        dict with success count, silhouette score, and computed_at
    """
    query = (
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(Image.map_protein))
        .where(
            Experiment.user_id == user_id,
            Image.embedding.isnot(None),
        )
        .order_by(Image.id)
    )

    if experiment_id:
        query = query.where(Image.experiment_id == experiment_id)

    result = await db.execute(query)
    images = result.scalars().all()

    if len(images) < MIN_POINTS_FOR_UMAP:
        return {
            "error": f"Need at least {MIN_POINTS_FOR_UMAP} FOV images with embeddings",
            "count": len(images),
        }

    embeddings = np.array([img.embedding for img in images])
    projection, silhouette = compute_umap_online(embeddings, images)

    now = datetime.now(timezone.utc)
    for i, image in enumerate(images):
        image.umap_x = float(projection[i, 0])
        image.umap_y = float(projection[i, 1])
        image.umap_computed_at = now

    await db.commit()

    logger.info(
        f"Computed FOV UMAP for user {user_id}"
        f"{f' experiment {experiment_id}' if experiment_id else ''}: "
        f"{len(images)} images, silhouette={silhouette:.3f if silhouette else 'N/A'}"
    )

    return {
        "success": len(images),
        "silhouette_score": silhouette,
        "computed_at": now.isoformat(),
    }


# =============================================================================
# UMAP Invalidation Functions
# =============================================================================


async def invalidate_crop_umap(
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    image_id: Optional[int] = None,
) -> int:
    """
    Invalidate pre-computed UMAP coordinates for crops.

    Call this after new embeddings are extracted or existing ones change.

    Args:
        db: AsyncSession database connection
        experiment_id: Invalidate crops in this experiment
        image_id: Invalidate crops from this image

    Returns:
        Number of crops invalidated
    """
    stmt = update(CellCrop).values(umap_computed_at=None)

    if image_id:
        stmt = stmt.where(CellCrop.image_id == image_id)
    elif experiment_id:
        stmt = stmt.where(
            CellCrop.image_id.in_(
                select(Image.id).where(Image.experiment_id == experiment_id)
            )
        )

    result = await db.execute(stmt)
    return result.rowcount


async def invalidate_fov_umap(
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    image_id: Optional[int] = None,
) -> int:
    """
    Invalidate pre-computed UMAP coordinates for FOV images.

    Args:
        db: AsyncSession database connection
        experiment_id: Invalidate images in this experiment
        image_id: Invalidate specific image

    Returns:
        Number of images invalidated
    """
    stmt = update(Image).values(umap_computed_at=None)

    if image_id:
        stmt = stmt.where(Image.id == image_id)
    elif experiment_id:
        stmt = stmt.where(Image.experiment_id == experiment_id)

    result = await db.execute(stmt)
    return result.rowcount
