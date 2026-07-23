"""UMAP computation service for pre-computing 2D projections.

This service handles deterministic UMAP computation and storage of
coordinates for cell crops, FOV images, and proteins.

SSOT for UMAP-related constants and computation functions.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.cell_crop import CellCrop
from models.experiment import Experiment
from models.image import Image, MapProtein
from schemas.embeddings import UmapType
from utils.groups import experiment_owner_filter, get_user_group_id

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


def _normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    """L2 normalize embeddings for cosine similarity."""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    return embeddings / norms


def _ring_layout(n_points: int) -> np.ndarray:
    """Evenly spaced points on the unit circle (origin for a single point).

    Used when there are too few distinct embeddings to fit UMAP at all.
    """
    if n_points <= 1:
        return np.zeros((max(n_points, 1), 2))
    angles = np.linspace(0, 2 * np.pi, n_points, endpoint=False)
    return np.column_stack([np.cos(angles), np.sin(angles)])


def _compute_umap_projection(
    embeddings_norm: np.ndarray,
    n_neighbors: int,
    min_dist: float,
    use_random_init: bool = False,
) -> np.ndarray:
    """
    Core UMAP projection computation.

    Rows that are exactly equal are fitted once and share the resulting
    coordinates. UMAP's layout optimiser applies random negative sampling per
    row, so passing duplicates straight through scatters them — on real data two
    proteins with the same sequence landed ~7% of the plot diagonal apart, which
    reads as "the same protein is in two places". Collapsing first is what makes
    equal input give equal output; ``random_state`` alone does not, since it only
    makes a whole run repeatable.

    Args:
        embeddings_norm: L2-normalized embedding vectors (N x D)
        n_neighbors: UMAP n_neighbors parameter
        min_dist: UMAP min_dist parameter
        use_random_init: Use random init (for small datasets < 10)

    Returns:
        2D projection array (N x 2), in the order the rows were given
    """
    import umap

    np.random.seed(RANDOM_STATE)

    unique_rows, inverse = np.unique(embeddings_norm, axis=0, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)
    n_samples = len(unique_rows)

    # UMAP needs at least 3 distinct points to build a neighbour graph. Fewer
    # means every embedding is (nearly) the same one, so there is no structure
    # to project — lay them out deterministically instead of raising.
    if n_samples < 3:
        logger.warning(
            "UMAP skipped: %d embeddings collapse to %d distinct value(s)",
            len(embeddings_norm), n_samples,
        )
        return _ring_layout(n_samples)[inverse]

    effective_n_neighbors = min(n_neighbors, n_samples - 1)

    # Use random init for small datasets (spectral fails with k >= N)
    init_method = "random" if use_random_init or n_samples < 10 else "spectral"

    reducer = umap.UMAP(
        n_neighbors=effective_n_neighbors,
        min_dist=min_dist,
        n_components=2,
        metric="cosine",
        random_state=RANDOM_STATE,
        init=init_method,
    )
    return reducer.fit_transform(unique_rows)[inverse]


def compute_silhouette(
    embeddings: np.ndarray,
    items: list,
) -> Optional[float]:
    """
    Compute silhouette score on raw embeddings based on protein labels.

    Uses cosine metric on full-dimensional embeddings (not UMAP projections)
    to measure cluster quality in the original feature space.

    Runs inside asyncio.to_thread (see _compute_and_store_umap), so it must touch
    only eagerly-loaded attributes. Reading a lazy relationship here fires a DB
    load off the event loop and raises MissingGreenlet.

    Args:
        embeddings: Raw embedding vectors (N x D)
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
    Fit a UMAP projection over the given embeddings.

    CPU-bound and takes seconds — callers must not run this on the read path.
    _compute_and_store_umap offloads it to a thread and persists the result.

    Args:
        embeddings: Array of embedding vectors (N x D)
        items: List of CellCrop or Image objects (for protein labels)
        n_neighbors: UMAP n_neighbors parameter
        min_dist: UMAP min_dist parameter

    Returns:
        Tuple of (projection array N x 2, silhouette score or None)

    Raises:
        ValueError: If fewer than 3 samples are given
    """
    n_samples = len(embeddings)
    if n_samples < 3:
        raise ValueError(f"Need at least 3 samples for UMAP, got {n_samples}")

    embeddings_norm = _normalize_embeddings(embeddings)
    projection = _compute_umap_projection(embeddings_norm, n_neighbors, min_dist)
    silhouette = compute_silhouette(embeddings_norm, items)

    return projection, silhouette


# =============================================================================
# Batch UMAP Computation (stores to DB)
# =============================================================================


async def _compute_and_store_umap(
    items: list,
    umap_type: UmapType,
    db: AsyncSession,
    user_id: int,
) -> dict:
    """
    Common helper for computing UMAP and storing coordinates.

    DRY: Consolidates shared logic between compute_crop_umap and compute_fov_umap.

    Args:
        items: List of CellCrop or Image objects with embeddings
        umap_type: Which corpus these items are
        db: AsyncSession database connection
        user_id: User ID for logging

    Returns:
        dict with success count, silhouette score, and computed_at
    """
    word = umap_type.item_word

    if len(items) < MIN_POINTS_FOR_UMAP:
        return {
            "error": f"Need at least {MIN_POINTS_FOR_UMAP} {word} with embeddings",
            "count": len(items),
        }

    embeddings = np.array([item.embedding for item in items])

    # Fitting is CPU-bound and takes seconds, and blocking the event loop stalls
    # every other request this worker is serving (the API runs a single uvicorn
    # process, so that is all of them). The thread only reads attributes the
    # callers eagerly loaded, so no lazy IO escapes the loop — see
    # compute_silhouette.
    projection, silhouette = await asyncio.to_thread(
        compute_umap_online, embeddings, items
    )

    now = datetime.now(timezone.utc)
    for i, item in enumerate(items):
        item.umap_x = float(projection[i, 0])
        item.umap_y = float(projection[i, 1])
        item.umap_computed_at = now

    await db.commit()

    silhouette_str = f"{silhouette:.3f}" if silhouette else "N/A"
    logger.info(
        f"Computed {word} UMAP for user {user_id}: "
        f"{len(items)} {word}, silhouette={silhouette_str}"
    )

    return {
        "success": len(items),
        "silhouette_score": silhouette,
        "computed_at": now.isoformat(),
    }


async def compute_crop_umap(user_id: int, db: AsyncSession) -> dict:
    """
    Compute UMAP for cell crops and store coordinates in database.

    Always covers everything the user can read (own + group) — see
    refresh_umap_scope for why a narrower fit would corrupt the shared
    coordinate space.

    Args:
        user_id: User ID for ownership filtering
        db: AsyncSession database connection

    Returns:
        dict with success count, silhouette score, and computed_at
    """
    group_id = await get_user_group_id(user_id, db)

    query = (
        select(CellCrop)
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(CellCrop.map_protein))
        .where(
            experiment_owner_filter(user_id, group_id),
            CellCrop.embedding.isnot(None),
        )
        .order_by(CellCrop.id)
    )

    result = await db.execute(query)
    crops = result.scalars().all()

    return await _compute_and_store_umap(crops, UmapType.CROPPED, db, user_id)


async def compute_fov_umap(user_id: int, db: AsyncSession) -> dict:
    """
    Compute UMAP for FOV images and store coordinates in database.

    Always covers everything the user can read (own + group) — see
    refresh_umap_scope for why a narrower fit would corrupt the shared
    coordinate space.

    Args:
        user_id: User ID for ownership filtering
        db: AsyncSession database connection

    Returns:
        dict with success count, silhouette score, and computed_at
    """
    group_id = await get_user_group_id(user_id, db)

    query = (
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(Image.map_protein))
        .where(
            experiment_owner_filter(user_id, group_id),
            Image.embedding.isnot(None),
        )
        .order_by(Image.id)
    )

    result = await db.execute(query)
    images = result.scalars().all()

    return await _compute_and_store_umap(images, UmapType.FOV, db, user_id)


# =============================================================================
# Automatic UMAP Refresh (self-healing)
# =============================================================================

# Scopes with a refresh already running. A scope shares one global projection, so
# concurrent refreshes would duplicate seconds of CPU work and race writing the
# same rows.
#
# Process-local state is sufficient ONLY because the API runs a single uvicorn
# process — see the CMD in backend/Dockerfile{,.gpu,.dev}. Adding `--workers N`
# would silently reduce this to per-worker dedupe, letting N workers fit the same
# rows concurrently and race their writes.
_inflight_refreshes: set[tuple[str, str]] = set()

# Scopes whose last refresh raised, with the reason. A read that sees a scope in
# here stops rescheduling it: without this the client's poll loop would trigger a
# fresh multi-second fit every few seconds forever, and the failure would stay
# invisible — the exact silence that hid this bug for months. Cleared on the next
# success or by an explicit /umap/recompute.
_failed_refreshes: dict[tuple[str, str], str] = {}


def refresh_scope_key(
    umap_type: UmapType,
    user_id: int,
    group_id: Optional[int],
) -> tuple[str, str]:
    """Dedupe key for a refresh.

    Group members share a corpus, so they share a key — otherwise each member's
    dashboard would kick off its own redundant fit of the same rows. That holds
    because joining a group adopts the member's group-less experiments
    (utils.groups.adopt_orphan_experiments), so no member can read an experiment
    their peers cannot.

    The scope token is prefixed because user ids and group ids share this key
    space: group 2 and user 2 must not collide.
    """
    scope = f"g{group_id}" if group_id is not None else f"u{user_id}"
    return (umap_type.value, scope)


def get_refresh_error(
    umap_type: UmapType,
    user_id: int,
    group_id: Optional[int],
) -> Optional[str]:
    """Return why this scope's last refresh failed, or None if it didn't."""
    return _failed_refreshes.get(refresh_scope_key(umap_type, user_id, group_id))


def clear_refresh_error(
    umap_type: UmapType,
    user_id: int,
    group_id: Optional[int],
) -> None:
    """Forget a scope's recorded failure so it will be retried."""
    _failed_refreshes.pop(refresh_scope_key(umap_type, user_id, group_id), None)


async def refresh_umap_scope(
    umap_type: UmapType,
    user_id: int,
    group_id: Optional[int] = None,
) -> None:
    """
    Recompute and store UMAP coordinates for a whole scope, at most once at a time.

    Always covers the full scope rather than a single experiment: coordinates are
    one shared projection, so fitting a subset would write coordinates from a
    different space into the same columns and corrupt the combined plot.

    Records failures in _failed_refreshes so a permanently broken scope is
    reported to the client instead of being retried forever.

    Never raises: Starlette awaits this after the response is sent, where an
    escaping exception has nobody to catch it.
    """
    key = refresh_scope_key(umap_type, user_id, group_id)
    if key in _inflight_refreshes:
        logger.info(f"UMAP refresh {key} already running - skipping duplicate")
        return

    _inflight_refreshes.add(key)
    try:
        from database import get_db_context

        async with get_db_context() as db:
            compute = (
                compute_fov_umap if umap_type is UmapType.FOV else compute_crop_umap
            )
            result = await compute(user_id, db)

        if "error" in result:
            logger.warning(f"UMAP refresh {key} skipped: {result['error']}")
        else:
            logger.info(f"UMAP refresh {key} complete: {result}")
        _failed_refreshes.pop(key, None)
    except Exception as exc:
        logger.exception(f"UMAP refresh {key} failed")
        _failed_refreshes[key] = f"{type(exc).__name__}: {exc}"
    finally:
        _inflight_refreshes.discard(key)


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
    Clears umap_x, umap_y, and umap_computed_at; the next read of the UMAP
    endpoint sees the missing coordinates and schedules refresh_umap_scope.

    Args:
        db: AsyncSession database connection
        experiment_id: Invalidate crops in this experiment
        image_id: Invalidate crops from this image

    Returns:
        Number of crops invalidated
    """
    stmt = update(CellCrop).values(
        umap_x=None,
        umap_y=None,
        umap_computed_at=None,
    )

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

    Clears umap_x, umap_y, and umap_computed_at; the next read of the UMAP
    endpoint sees the missing coordinates and schedules refresh_umap_scope.

    Args:
        db: AsyncSession database connection
        experiment_id: Invalidate images in this experiment
        image_id: Invalidate specific image

    Returns:
        Number of images invalidated
    """
    stmt = update(Image).values(
        umap_x=None,
        umap_y=None,
        umap_computed_at=None,
    )

    if image_id:
        stmt = stmt.where(Image.id == image_id)
    elif experiment_id:
        stmt = stmt.where(Image.experiment_id == experiment_id)

    result = await db.execute(stmt)
    return result.rowcount


# =============================================================================
# Protein UMAP Functions
# =============================================================================


def compute_protein_umap_online(
    embeddings: np.ndarray,
    n_neighbors: int = DEFAULT_N_NEIGHBORS,
    min_dist: float = DEFAULT_MIN_DIST,
) -> Tuple[np.ndarray, Optional[float]]:
    """
    Compute UMAP projection for protein embeddings on-the-fly.

    Args:
        embeddings: Array of protein embedding vectors (N x 1152)
        n_neighbors: UMAP n_neighbors parameter
        min_dist: UMAP min_dist parameter

    Returns:
        Tuple of (projection array N x 2, silhouette score or None)
    """
    n_samples = len(embeddings)
    if n_samples < 3:
        raise ValueError(f"Need at least 3 proteins for UMAP, got {n_samples}")

    embeddings_norm = _normalize_embeddings(embeddings)
    projection = _compute_umap_projection(embeddings_norm, n_neighbors, min_dist)

    # Silhouette score not applicable for proteins (no labels)
    return projection, None


async def compute_protein_umap(db: AsyncSession) -> dict:
    """
    Compute UMAP for all proteins with embeddings and store coordinates.

    Args:
        db: AsyncSession database connection

    Returns:
        dict with success count and computed_at
    """
    query = (
        select(MapProtein)
        .where(MapProtein.embedding.isnot(None))
        .order_by(MapProtein.id)
    )

    result = await db.execute(query)
    proteins = result.scalars().all()

    if len(proteins) < MIN_POINTS_FOR_UMAP:
        return {
            "error": f"Need at least {MIN_POINTS_FOR_UMAP} proteins with embeddings",
            "count": len(proteins),
        }

    embeddings = np.array([p.embedding for p in proteins])
    projection, _ = compute_protein_umap_online(embeddings)

    now = datetime.now(timezone.utc)
    for i, protein in enumerate(proteins):
        protein.umap_x = float(projection[i, 0])
        protein.umap_y = float(projection[i, 1])
        protein.umap_computed_at = now

    await db.commit()

    logger.info(f"Computed protein UMAP: {len(proteins)} proteins")

    return {
        "success": len(proteins),
        "computed_at": now.isoformat(),
    }


async def invalidate_protein_umap(db: AsyncSession) -> int:
    """
    Invalidate pre-computed UMAP coordinates for all proteins.

    Args:
        db: AsyncSession database connection

    Returns:
        Number of proteins invalidated
    """
    stmt = update(MapProtein).values(
        umap_x=None,
        umap_y=None,
        umap_computed_at=None,
    )

    result = await db.execute(stmt)
    return result.rowcount
