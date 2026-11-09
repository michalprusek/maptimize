"""Retraining the cell detector on the crops the lab has curated.

Imported by ``scripts/train_detector.py`` alone; the API process never loads it.
Everything here is pure (numpy only) so the decisions that decide what the
detector learns, and how it is judged, are unit-tested without a GPU or a DB.

Which crops are ground truth
----------------------------
A crop row is not evidence that a person looked at it: an upload with
detection on stores whatever the old detector predicted. Training on those
rows teaches the new model to imitate the old one, and testing on them scores
the old model against its own output. So every FOV gets a provenance:

``human``       a person drew at least one box, or detection was off and every
                box was drawn by hand. The other boxes in such a FOV survived a
                person editing it, so they count as confirmed.
``reviewed``    every box came from the detector, but the set differs from what
                the detector predicts today: somebody deleted or moved boxes.
``unreviewed``  identical to what the detector predicts: nobody can tell
                whether it was ever checked.
``negative``    no crops, although the detector predicts some: somebody deleted
                every detection. A confirmed background image.

Only ``human`` FOVs from held-out experiments are used for validation and
testing; the others can only ever enter the training pool.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

from ml.detection.geometry import rotated_corners

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels

HUMAN = "human"
REVIEWED = "reviewed"
UNREVIEWED = "unreviewed"
NEGATIVE = "negative"

# Two predictions of the same detector on the same pixels agree to within a
# pixel; 0.9 IoU tolerates int truncation of the stored boxes and nothing a
# person would do by hand.
SAME_BOX_IOU = 0.9

SPLITS = ("train", "val", "test")


def crop_box(
    x: float, y: float, w: float, h: float, angle: Optional[float], width: int, height: int
) -> Optional[Box]:
    """The axis-aligned box a crop teaches the detector, clipped to the image.

    A rotated crop becomes the envelope of its rotated rectangle — the box an
    axis-aligned detector has to output for the cell to be fully inside it
    (the HBB conversion DOTA uses for its horizontal-box task). ``None`` when
    nothing of the box lies inside the image.
    """
    if angle:
        xs, ys = zip(*rotated_corners(x, y, w, h, angle))
    else:
        xs, ys = (x, x + w), (y, y + h)
    x1, y1 = max(0.0, min(xs)), max(0.0, min(ys))
    x2, y2 = min(float(width), max(xs)), min(float(height), max(ys))
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return (x1, y1, x2, y2)


def yolo_label(box: Box, width: int, height: int) -> str:
    """One line of a YOLO label file: class 0, normalised centre and size."""
    x1, y1, x2, y2 = box
    return (
        f"0 {(x1 + x2) / 2 / width:.6f} {(y1 + y2) / 2 / height:.6f} "
        f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}"
    )


def iou_matrix(a: Sequence[Box], b: Sequence[Box]) -> np.ndarray:
    """Pairwise IoU, shape (len(a), len(b))."""
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)))
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    ix1 = np.maximum(a[:, None, 0], b[None, :, 0])
    iy1 = np.maximum(a[:, None, 1], b[None, :, 1])
    ix2 = np.minimum(a[:, None, 2], b[None, :, 2])
    iy2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (area_a[:, None] + area_b[None, :] - inter)


def same_boxes(a: Sequence[Box], b: Sequence[Box], min_iou: float = SAME_BOX_IOU) -> bool:
    """True when ``a`` and ``b`` are the same set of boxes, one-to-one."""
    if len(a) != len(b):
        return False
    if not len(a):
        return True
    return int(match_flags(list(a), [1.0] * len(a), list(b), min_iou).sum()) == len(a)


def provenance(
    detect_cells: bool,
    crops: Sequence[tuple[Box, Optional[float]]],
    predicted: Sequence[Box],
) -> Optional[str]:
    """Classify one FOV (see module docstring); ``None`` = nothing to learn.

    ``crops`` are (box, detection_confidence) — a NULL confidence marks a box a
    person drew. ``predicted`` is what the current detector outputs today.
    """
    if not crops:
        return NEGATIVE if detect_cells and predicted else None
    if not detect_cells or any(conf is None for _, conf in crops):
        return HUMAN
    if same_boxes([box for box, _ in crops], predicted):
        return UNREVIEWED
    return REVIEWED


def weights_fingerprint(path: str | Path) -> str:
    """Short content hash of a weights file.

    Cached predictions are keyed on it: the same name can point at different
    weights (a retrained run, or ``weights/best.pt`` after a deploy), and a
    cache keyed on the name alone would silently score the new model with the
    old model's predictions — and pick the production threshold from them.
    Content, not mtime or size: a copy changes the mtime, two checkpoints can
    share a size.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def split_experiments(
    owner_by_experiment: dict[int, str],
    seed: int,
    fractions: tuple[float, float, float] = (0.7, 0.15, 0.15),
) -> dict[int, str]:
    """Assign whole experiments to train / val / test, stratified by owner.

    FOVs from one experiment share a slide, a transfection and an imaging
    session, so splitting by FOV would score how well the model remembers a
    slide. Stratifying by owner keeps every annotator's style in every split.
    Deterministic in ``seed`` and independent of dict order.
    """
    rng = np.random.default_rng(seed)
    by_owner: dict[str, list[int]] = {}
    for exp, owner in owner_by_experiment.items():
        by_owner.setdefault(owner, []).append(exp)
    assignment: dict[int, str] = {}
    for owner in sorted(by_owner):
        exps = sorted(by_owner[owner])
        rng.shuffle(exps)
        n = len(exps)
        n_test = int(round(n * fractions[2]))
        n_val = int(round(n * fractions[1]))
        for i, exp in enumerate(exps):
            if i < n_test:
                assignment[exp] = "test"
            elif i < n_test + n_val:
                assignment[exp] = "val"
            else:
                assignment[exp] = "train"
    return assignment


# ── Evaluation ──────────────────────────────────────────────────────────────


@dataclass
class FovResult:
    """One evaluated FOV: what the detector said and what the lab annotated."""

    image_id: int
    experiment_id: int
    pred_boxes: list[Box]
    pred_scores: list[float]
    gt_boxes: list[Box]


def match_flags(
    pred_boxes: Sequence[Box], pred_scores: Sequence[float], gt_boxes: Sequence[Box], iou_thr: float
) -> np.ndarray:
    """True-positive flag per prediction, in descending-score order.

    Greedy matching as in the COCO / VOC protocols: each prediction, highest
    score first, takes the still-unmatched ground truth it overlaps most, if
    that overlap reaches ``iou_thr``. A second box on an already-claimed cell
    is a false positive.
    """
    order = np.argsort(-np.asarray(pred_scores, dtype=float), kind="stable")
    ious = iou_matrix([pred_boxes[i] for i in order], gt_boxes)
    taken = np.zeros(len(gt_boxes), dtype=bool)
    flags = np.zeros(len(order), dtype=bool)
    for k in range(len(order)):
        if not len(gt_boxes):
            break
        candidates = np.where(taken, -1.0, ious[k])
        j = int(np.argmax(candidates))
        if candidates[j] >= iou_thr:
            taken[j] = True
            flags[k] = True
    return flags


def average_precision(scores: np.ndarray, flags: np.ndarray, n_gt: int) -> float:
    """Area under the interpolated precision-recall curve (all-point, VOC 2010+)."""
    if n_gt == 0:
        return float("nan")
    order = np.argsort(-scores, kind="stable")
    tp = np.cumsum(flags[order])
    fp = np.cumsum(~flags[order])
    recall = np.concatenate([[0.0], tp / n_gt, [1.0]])
    precision = np.concatenate([[1.0], tp / np.maximum(tp + fp, 1), [0.0]])
    precision = np.maximum.accumulate(precision[::-1])[::-1]
    steps = np.where(recall[1:] != recall[:-1])[0]
    return float(np.sum((recall[steps + 1] - recall[steps]) * precision[steps + 1]))


IOU_THRESHOLDS = tuple(np.round(np.arange(0.5, 0.96, 0.05), 2))


class Evaluation:
    """Per-FOV match flags computed once, so resampling FOVs is cheap."""

    def __init__(self, fovs: Sequence[FovResult]):
        self.fovs = list(fovs)
        # The operating threshold chosen for this model on validation data;
        # set by the caller once known, read by threshold-dependent metrics.
        self.conf: Optional[float] = None
        self._scores = [np.sort(np.asarray(f.pred_scores, dtype=float))[::-1] for f in self.fovs]
        self._flags = {
            thr: [match_flags(f.pred_boxes, f.pred_scores, f.gt_boxes, thr) for f in self.fovs]
            for thr in IOU_THRESHOLDS
        }

    def _pooled(self, idx: Iterable[int], thr: float) -> tuple[np.ndarray, np.ndarray, int]:
        idx = list(idx)
        scores = np.concatenate([self._scores[i] for i in idx]) if idx else np.zeros(0)
        flags = np.concatenate([self._flags[thr][i] for i in idx]) if idx else np.zeros(0, bool)
        n_gt = sum(len(self.fovs[i].gt_boxes) for i in idx)
        return scores, flags.astype(bool), n_gt

    def ap(self, idx: Iterable[int], thr: float = 0.5) -> float:
        return average_precision(*self._pooled(idx, thr))

    def ap50_95(self, idx: Iterable[int]) -> float:
        idx = list(idx)
        return float(np.mean([self.ap(idx, thr) for thr in IOU_THRESHOLDS]))

    def operating_point(self, idx: Iterable[int], conf: float, thr: float = 0.5) -> dict:
        """Precision / recall / F1 of what production keeps: scores >= ``conf``."""
        scores, flags, n_gt = self._pooled(idx, thr)
        keep = scores >= conf
        tp = int(flags[keep].sum())
        fp = int(keep.sum()) - tp
        fn = n_gt - tp
        precision = tp / (tp + fp) if tp + fp else float("nan")
        recall = tp / n_gt if n_gt else float("nan")
        f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else float("nan")
        return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}

    def best_conf(self, idx: Iterable[int], grid: Sequence[float]) -> float:
        """The threshold in ``grid`` with the highest F1 (ties → the higher one)."""
        idx = list(idx)
        best = max(grid, key=lambda c: (self.operating_point(idx, c)["f1"], c))
        return float(best)

    def experiment_index(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for i, f in enumerate(self.fovs):
            out.setdefault(f.experiment_id, []).append(i)
        return out


def paired_bootstrap(
    a: Evaluation,
    b: Evaluation,
    metric,
    n_resamples: int = 2000,
    seed: int = 0,
) -> dict:
    """``metric(b) - metric(a)`` with a 95 % CI from resampling whole experiments.

    ``a`` and ``b`` must evaluate the same FOVs in the same order (two models on
    one test set). Experiments, not FOVs, are the resampling unit for the same
    reason the split is by experiment: FOVs of one slide are not independent.
    """
    if [f.image_id for f in a.fovs] != [f.image_id for f in b.fovs]:
        raise ValueError("paired bootstrap needs both evaluations on the same FOVs")
    groups = list(a.experiment_index().values())
    all_idx = [i for g in groups for i in g]
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_resamples):
        pick = rng.integers(0, len(groups), len(groups))
        idx = [i for k in pick for i in groups[k]]
        diffs.append(metric(b, idx) - metric(a, idx))
    diffs = np.asarray(diffs)
    return {
        "diff": float(metric(b, all_idx) - metric(a, all_idx)),
        "ci95": [float(np.nanpercentile(diffs, 2.5)), float(np.nanpercentile(diffs, 97.5))],
        "p_not_better": float(np.mean(diffs <= 0)),
    }
