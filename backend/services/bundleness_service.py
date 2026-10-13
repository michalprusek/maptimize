"""Bundleness scores on crop rows: writing them, and summarising a selection.

The score is derived data — a pure function of the crop's embedding and the
model version — so it is precomputed and stored (``cell_crops.bundleness_score``
beside ``bundleness_model``) rather than recomputed per request. Two places
write it and nothing else may:

* ``apply_score`` — whenever an embedding is assigned, in the same transaction,
  so the pair can never be observed out of step;
* ``backfill`` — at startup, for every row whose stored version is not the
  active one. That is the entire deployment of a retrained model.

Everything here is numpy on vectors already in the database: no GPU, no model
load, no file IO beyond the one-off artifact read.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np
from sqlalchemy import and_, bindparam, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ml.bundleness import BundlenessModel, load_model
from models.cell_crop import CellCrop

logger = logging.getLogger(__name__)

BACKFILL_BATCH = 500


def apply_score(crop: CellCrop, model: Optional[BundlenessModel] = None) -> None:
    """Set the crop's score from its current embedding, or clear it.

    Clearing matters as much as setting: a crop whose embedding was removed, or
    came from another encoder, must not keep a number computed from something
    it no longer has.
    """
    model = model or load_model()
    if crop.embedding is None or not model.accepts(crop.embedding_model):
        crop.bundleness_score = None
        crop.bundleness_model = None
        return
    crop.bundleness_score = float(model.score(np.asarray([crop.embedding]))[0])
    crop.bundleness_model = model.version


async def backfill(db: AsyncSession, model: Optional[BundlenessModel] = None) -> int:
    """Bring every crop row in line with the active model. Returns rows scored.

    Idempotent: a second run finds nothing to do. Selecting on the stored
    version (rather than on a NULL score) is what makes a version bump rescore
    the whole corpus without a migration.
    """
    model = model or load_model()
    scoreable = and_(
        CellCrop.embedding.isnot(None),
        CellCrop.embedding_model == model.embedding_model,
    )

    # Rows that cannot be scored must not keep a stale number.
    await db.execute(
        update(CellCrop)
        .where(
            ~scoreable,
            or_(CellCrop.bundleness_score.isnot(None), CellCrop.bundleness_model.isnot(None)),
        )
        .values(bundleness_score=None, bundleness_model=None)
    )

    pending = (
        await db.execute(
            select(CellCrop.id)
            .where(scoreable, CellCrop.bundleness_model.is_distinct_from(model.version))
            .order_by(CellCrop.id)
        )
    ).scalars().all()

    table = CellCrop.__table__
    write = (
        update(table)
        .where(table.c.id == bindparam("crop_id"))
        .values(bundleness_score=bindparam("score"), bundleness_model=model.version)
    )
    for start in range(0, len(pending), BACKFILL_BATCH):
        rows = (
            await db.execute(
                select(CellCrop.id, CellCrop.embedding).where(
                    CellCrop.id.in_(pending[start : start + BACKFILL_BATCH])
                )
            )
        ).all()
        scores = model.score(np.array([np.asarray(e, dtype=np.float64) for _, e in rows]))
        # A Core UPDATE keyed by primary key: the ORM never loads the rows, so
        # nothing here can expire or lazy-load, and `updated`-style triggers on
        # the mapped class are not involved.
        await db.execute(
            write,
            [
                {"crop_id": crop_id, "score": float(score)}
                for (crop_id, _), score in zip(rows, scores)
            ],
        )

    await db.commit()
    if pending:
        logger.info(
            "Bundleness backfill: scored %d crops with model %s", len(pending), model.version
        )
    return len(pending)


# =============================================================================
# Summarising a selection
# =============================================================================


@dataclass(frozen=True)
class ClassSummary:
    """Mean bundleness of the points carrying one value on the label axis."""

    label: int
    mean: float
    sd: Optional[float]
    n_scored: int


@dataclass(frozen=True)
class BundlenessSummary:
    """Mean bundleness of a selection, with the counts that qualify it.

    ``n_points`` is every point offered; ``n_scored`` is how many of them had a
    score from the active model. The mean never travels without both: a mean
    over 4 of 300 points is a different statement from one over 300 of 300, and
    the number alone cannot tell a reader which they are looking at.
    """

    mean: float
    sd: Optional[float]
    n_scored: int
    n_points: int
    model_version: str
    classes: List[ClassSummary]


def _mean_sd(values: np.ndarray) -> tuple[float, Optional[float]]:
    # Sample SD; undefined for a single point, and reported as such rather than
    # as a zero that reads like "perfectly uniform".
    sd = float(values.std(ddof=1)) if len(values) > 1 else None
    return float(values.mean()), sd


def summarise(
    scores: Sequence[Optional[float]],
    versions: Sequence[Optional[str]],
    labels: Sequence[Optional[int]],
    model: Optional[BundlenessModel] = None,
) -> Optional[BundlenessSummary]:
    """Mean score of the points, overall and per class of ``labels``.

    All three sequences are positional and the same length. A point counts only
    if its score came from the ACTIVE model: scores from two versions are on two
    different scales, and averaging them produces a number that belongs to
    neither.

    ``labels`` are the classes separability was scored on, so the per-class
    means describe exactly the groups the legend shows. A point with no label
    (``None`` — nothing assigned on the axis, or it sits in several of the
    caller's groups) still counts toward the overall mean, and toward no class.

    Returns ``None`` when no point is scored: there is no mean of nothing, and a
    zero would read as "exactly average".
    """
    model = model or load_model()
    if not (len(scores) == len(versions) == len(labels)):
        raise ValueError("scores, versions and labels must be the same length")

    usable = [
        (float(score), label)
        for score, version, label in zip(scores, versions, labels)
        if score is not None and version == model.version and math.isfinite(score)
    ]
    if not usable:
        return None

    values = np.array([score for score, _ in usable])
    mean, sd = _mean_sd(values)

    by_class: dict[int, list[float]] = {}
    for score, label in usable:
        if label is not None:
            by_class.setdefault(label, []).append(score)

    classes = []
    for label in sorted(by_class):
        class_mean, class_sd = _mean_sd(np.array(by_class[label]))
        classes.append(
            ClassSummary(label=label, mean=class_mean, sd=class_sd, n_scored=len(by_class[label]))
        )

    return BundlenessSummary(
        mean=mean,
        sd=sd,
        n_scored=len(usable),
        n_points=len(scores),
        model_version=model.version,
        classes=classes,
    )
