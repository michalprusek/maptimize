"""UMAP computation service for pre-computing 2D projections.

This service handles deterministic UMAP computation and storage of
coordinates for cell crops and proteins.

SSOT for UMAP-related constants and computation functions.
"""

import asyncio
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.cell_crop import CellCrop
from models.experiment import Experiment
from models.image import Image, MapProtein
from schemas.embeddings import UnscoredReason

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


class DegenerateEmbeddingsError(ValueError):
    """Too few *distinct* embeddings to project.

    A ValueError subclass so callers that already handle the "not enough
    samples" case keep working.
    """


def _compute_umap_projection(
    embeddings_norm: np.ndarray,
    n_neighbors: int,
    min_dist: float,
    use_random_init: bool = False,
) -> np.ndarray:
    """
    Core UMAP projection computation.

    Rows that compare *exactly* equal are fitted once and share the resulting
    coordinates. UMAP does not guarantee equal coordinates for equal rows — its
    layout is stochastic — so passing duplicates straight through puts the same
    protein in two places. ``random_state`` does not help: it makes a whole run
    repeatable, which is a different property from equal input giving equal
    output.

    Note the equality is exact. Near-identical rows are NOT collapsed, which is
    why protein_embedding_service reuses stored vectors verbatim instead of
    re-encoding: if that reuse is ever removed, twins drift apart by ~1e-5, this
    collapse silently stops matching them, and the bug returns.

    Args:
        embeddings_norm: L2-normalized embedding vectors (N x D)
        n_neighbors: UMAP n_neighbors parameter
        min_dist: UMAP min_dist parameter
        use_random_init: Use random init (for small datasets < 10)

    Returns:
        2D projection array (N x 2), in the order the rows were given

    Raises:
        DegenerateEmbeddingsError: If fewer than 3 distinct rows are given.
    """
    import umap

    np.random.seed(RANDOM_STATE)

    unique_rows, inverse = np.unique(embeddings_norm, axis=0, return_inverse=True)
    # Flattened because NumPy 2.0 returned this index as a column vector; used
    # as-is it would index the projection into an (N, 1, 2) array.
    inverse = np.asarray(inverse).reshape(-1)
    n_samples = len(unique_rows)

    # Fewer than 3 *distinct* rows leaves no neighbour graph to build. Returning
    # a made-up layout here would be worse than failing: the caller stores it in
    # umap_x/umap_y and serves it as a real projection, so nobody ever learns the
    # plot is fiction.
    if n_samples < 3:
        raise DegenerateEmbeddingsError(
            f"{len(embeddings_norm)} embeddings collapse to {n_samples} distinct "
            f"value(s); need at least 3 to compute a projection"
        )

    # Safe without the old floor-to-2 only because of the early return above,
    # which guarantees n_samples - 1 >= 2.
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


@dataclass(frozen=True)
class Separability:
    """How cleanly a labelled set of points separates in embedding space.

    ``score`` is a silhouette, but it never travels alone: the value depends on
    how many classes were scored and how many points carried a label, so two
    scores are only comparable between subsets of comparable size. Returning the
    three together is what lets the UI put them side by side and lets a reader
    judge whether the comparison is fair.
    """

    score: float
    n_classes: int
    n_points: int


# A silhouette over a handful of points is noise, and one class has nothing to
# separate from.
MIN_LABELED_FOR_SEPARABILITY = 10


def protein_labels(items: list) -> list[Optional[int]]:
    """Label each item by its MAP protein, ``None`` where none is assigned.

    Must touch only eagerly-loaded attributes: every caller is async code, where
    reading a lazy relationship fires a DB load outside a greenlet and raises
    MissingGreenlet. It also runs BEFORE compute_crop_umap hands the fit to a
    worker thread, so that only plain numbers cross over.
    """
    return [
        protein.id if (protein := getattr(item, "map_protein", None)) else None
        for item in items
    ]


@dataclass(frozen=True)
class SeparabilityGap:
    """A score that could not be computed, with the counts that explain why.

    The absence is a result in its own right. Measured on production data
    (2026-10-06, 2501 crops / 1242 FOVs), every missing score was a filter that
    left one class on the scored axis — narrowing to a single experiment does it
    for all five axes — and never the size floor or a sklearn failure. Reporting
    nothing made that indistinguishable from a response that had not arrived.
    """

    reason: UnscoredReason
    n_classes: int
    n_points: int


def _labeled_counts(labels: list[Optional[int]]) -> tuple[int, int]:
    """``(distinct values, points)`` among the entries that carry a label."""
    labeled = [label for label in labels if label is not None]
    return len(set(labeled)), len(labeled)


def separability_gap(labels: list[Optional[int]]) -> Optional[SeparabilityGap]:
    """Why ``labels`` cannot be scored, or None when they can.

    The single home of the thresholds: ``compute_separability`` guards with this
    rather than repeating the comparison, so the reason shown to a reader cannot
    disagree with the decision that withheld the score.
    """
    n_classes, n_points = _labeled_counts(labels)

    if n_points == 0:
        reason = UnscoredReason.NO_LABELS
    elif n_classes < 2:
        reason = UnscoredReason.SINGLE_CLASS
    elif n_points < MIN_LABELED_FOR_SEPARABILITY:
        reason = UnscoredReason.TOO_FEW_POINTS
    else:
        return None

    return SeparabilityGap(reason=reason, n_classes=n_classes, n_points=n_points)


def unscored_gap(labels: list[Optional[int]]) -> SeparabilityGap:
    """Explain a score ``compute_separability`` did not return for ``labels``.

    Either a threshold withheld it, or the thresholds passed and the scorer
    still returned nothing — which is ``FAILED``, with the same counts.
    """
    gap = separability_gap(labels)
    if gap is not None:
        return gap
    n_classes, n_points = _labeled_counts(labels)
    return SeparabilityGap(
        reason=UnscoredReason.FAILED, n_classes=n_classes, n_points=n_points
    )


def compute_separability(
    embeddings: np.ndarray,
    labels: list[Optional[int]],
) -> Optional[Separability]:
    """
    Score how well ``labels`` separate in the raw embedding space.

    Uses a cosine silhouette on the **full-dimensional** embeddings, not on the
    UMAP coordinates. UMAP does not preserve distance, so a silhouette taken from
    the 2-D projection measures how good the projection is rather than how
    separable the data are — which is the opposite of the question being asked.

    ``labels`` is positional against ``embeddings``: entry *i* is the class of
    row *i*, or ``None`` when that row carries no value on the chosen axis.
    Unlabelled rows are dropped rather than pooled into a class of their own —
    "nobody recorded this" is an absence, not a group that could separate from
    anything.

    Args:
        embeddings: Raw embedding vectors (N x D)
        labels: Class of each row, ``None`` where unassigned

    Returns:
        Separability, or None when there is too little to say
    """
    if separability_gap(labels) is not None:
        return None

    labeled_indices = [i for i, label in enumerate(labels) if label is not None]
    labeled = [labels[i] for i in labeled_indices]
    distinct = set(labeled)

    try:
        from sklearn.metrics import silhouette_score
        score = silhouette_score(embeddings[labeled_indices], labeled, metric="cosine")
    except (ValueError, ImportError) as e:
        logger.warning(f"Could not compute separability: {e}")
        return None

    return Separability(
        score=float(score),
        n_classes=len(distinct),
        n_points=len(labeled_indices),
    )


def compute_umap_online(
    embeddings: np.ndarray,
    labels: list[Optional[int]],
    n_neighbors: int = DEFAULT_N_NEIGHBORS,
    min_dist: float = DEFAULT_MIN_DIST,
) -> Tuple[np.ndarray, Optional[Separability]]:
    """
    Fit a UMAP projection over the given embeddings.

    CPU-bound and takes seconds — callers must not run this on the read path.
    compute_crop_umap offloads it to a thread and persists the result.

    Args:
        embeddings: Array of embedding vectors (N x D)
        labels: Class of each row for the separability score, positionally
            aligned with ``embeddings``; ``None`` where unassigned
        n_neighbors: UMAP n_neighbors parameter
        min_dist: UMAP min_dist parameter

    Returns:
        Tuple of (projection array N x 2, Separability or None)

    Raises:
        ValueError: If fewer than 3 samples are given
    """
    n_samples = len(embeddings)
    if n_samples < 3:
        raise ValueError(f"Need at least 3 samples for UMAP, got {n_samples}")

    embeddings_norm = _normalize_embeddings(embeddings)
    projection = _compute_umap_projection(embeddings_norm, n_neighbors, min_dist)
    separability = compute_separability(embeddings_norm, labels)

    return projection, separability


# =============================================================================
# Batch UMAP Computation (stores to DB)
# =============================================================================


async def compute_crop_umap(db: AsyncSession) -> dict:
    """
    Compute UMAP for every cell crop and store the coordinates in the database.

    Fits over ALL crops with an embedding, not the caller's readable subset. The
    coordinates are one shared projection stored on the crop row, so a per-caller
    fit is only safe while every caller shares a corpus exactly -- which stopped
    being true when a user could belong to several groups. Access control applies
    to what a request RETURNS, never to what is fitted.

    Returns:
        dict with success count, separability, and computed_at
    """
    query = (
        select(CellCrop)
        .join(Image, CellCrop.image_id == Image.id)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .options(selectinload(CellCrop.map_protein))
        .where(CellCrop.embedding.isnot(None))
        .order_by(CellCrop.id)
    )

    result = await db.execute(query)
    crops = result.scalars().all()

    if len(crops) < MIN_POINTS_FOR_UMAP:
        return {
            "error": f"Need at least {MIN_POINTS_FOR_UMAP} crops with embeddings",
            "count": len(crops),
        }

    embeddings = np.array([crop.embedding for crop in crops])
    # Labelled by protein because that is the axis worth logging after a fit; the
    # read path scores whichever axis the caller asked for, over its own subset.
    labels = protein_labels(crops)

    # Fitting is CPU-bound and takes seconds, and blocking the event loop stalls
    # every other request this worker is serving (the API runs a single uvicorn
    # process, so that is all of them). protein_labels ran on this thread, so the
    # only things crossing into the worker are plain numbers — no lazy IO can
    # escape the loop.
    projection, separability = await asyncio.to_thread(
        compute_umap_online, embeddings, labels
    )

    now = datetime.now(timezone.utc)
    for i, crop in enumerate(crops):
        crop.umap_x = float(projection[i, 0])
        crop.umap_y = float(projection[i, 1])
        crop.umap_computed_at = now

    await db.commit()

    score_str = f"{separability.score:.3f}" if separability else "N/A"
    logger.info(f"Computed crop UMAP: {len(crops)} crops, separability={score_str}")

    return {
        "success": len(crops),
        "separability": separability,
        "computed_at": now.isoformat(),
    }


# =============================================================================
# Automatic UMAP Refresh (self-healing)
# =============================================================================

# There is exactly one projection — every crop, fitted together — so the refresh
# state is two module-level values rather than a map keyed by projection. (It
# was a map while a second, FOV-level projection existed; that was removed on
# 2026-10-13 and the key went with it.)
#
# Whether a refresh is running right now. Concurrent refreshes would duplicate
# seconds of CPU work and race writing the same rows.
#
# Process-local state is sufficient ONLY because the API runs a single uvicorn
# process — see the CMD in backend/Dockerfile{,.gpu,.dev}. Adding `--workers N`
# would silently reduce this to per-worker dedupe, letting N workers fit the same
# rows concurrently and race their writes.
_refresh_inflight: bool = False

# Why the last refresh failed, or None. A read that sees a reason stops
# rescheduling: without this the client's poll loop would trigger a fresh
# multi-second fit every few seconds forever, and the failure would stay
# invisible — the exact silence that hid this bug for months. Cleared on the next
# success or by an explicit /umap/recompute.
_refresh_error: Optional[str] = None


def get_refresh_error() -> Optional[str]:
    """Return why the last refresh failed, or None if it didn't."""
    return _refresh_error


def clear_refresh_error() -> None:
    """Forget a recorded failure so the projection will be retried."""
    global _refresh_error
    _refresh_error = None


async def refresh_umap() -> None:
    """
    Recompute and store UMAP coordinates for every crop, at most once at a time.

    Always covers the whole corpus rather than a single experiment or a single
    caller's scope: coordinates are one shared projection, so fitting a subset
    would write coordinates from a different space into the same columns and
    corrupt the combined plot.

    Records failures so a permanently broken projection is reported to the
    client instead of being retried forever.

    Never raises: Starlette awaits this after the response is sent, where an
    escaping exception has nobody to catch it.
    """
    global _refresh_inflight, _refresh_error
    if _refresh_inflight:
        logger.info("UMAP refresh already running - skipping duplicate")
        return

    _refresh_inflight = True
    try:
        from database import get_db_context

        async with get_db_context() as db:
            result = await compute_crop_umap(db)

        if "error" in result:
            # "Too few points" is a standing condition, not a transient one, so it
            # has to be RECORDED -- clearing it here let the read path reschedule
            # a doomed fit on every poll, which is the exact silent loop the
            # failure record was added to close.
            logger.warning(f"UMAP refresh skipped: {result['error']}")
            _refresh_error = result["error"]
        else:
            logger.info(f"UMAP refresh complete: {result}")
            _refresh_error = None
    except Exception as exc:
        logger.exception("UMAP refresh failed")
        _refresh_error = f"{type(exc).__name__}: {exc}"
    finally:
        _refresh_inflight = False


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
    endpoint sees the missing coordinates and schedules refresh_umap.

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


# =============================================================================
# Protein UMAP Functions
# =============================================================================


def compute_protein_umap_online(
    embeddings: np.ndarray,
    n_neighbors: int = DEFAULT_N_NEIGHBORS,
    min_dist: float = DEFAULT_MIN_DIST,
) -> np.ndarray:
    """
    Compute UMAP projection for protein embeddings on-the-fly.

    Returns the projection alone. A protein row is its own class, so there is
    nothing for a separability score to separate — this used to return a second
    value that was unconditionally ``None``, and the endpoint published it as a
    real field that no reader could ever see populated.

    Args:
        embeddings: Array of protein embedding vectors (N x 1152)
        n_neighbors: UMAP n_neighbors parameter
        min_dist: UMAP min_dist parameter

    Returns:
        Projection array N x 2
    """
    n_samples = len(embeddings)
    if n_samples < 3:
        raise ValueError(f"Need at least 3 proteins for UMAP, got {n_samples}")

    embeddings_norm = _normalize_embeddings(embeddings)
    return _compute_umap_projection(embeddings_norm, n_neighbors, min_dist)


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
    try:
        projection = compute_protein_umap_online(embeddings)
    except DegenerateEmbeddingsError as exc:
        # Storing a placeholder layout would leave umap_x/umap_y looking like a
        # real projection forever. Report it and write nothing.
        logger.warning(f"Protein UMAP skipped: {exc}")
        return {"error": str(exc), "count": len(proteins)}

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
