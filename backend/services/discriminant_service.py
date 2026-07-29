"""Supervised projection that maximally separates MAP proteins.

UMAP answers "what structure is in these embeddings"; this answers "how far
apart can the proteins be pushed, and how much of that is real". The second
half is the reason this file is not four lines of scikit-learn.

⚠️ The measurements that fixed every choice here (balanced accuracy over 14
proteins, chance 0.071, on the production corpus of 1277 crops):

    split                      raw     microscope-centred
    random over crops         0.679          0.665
    grouped by image          0.653          0.655
    grouped by experiment     0.186          0.259

Permutation null under the experiment split: mean 0.054, max 0.078.

So: a random split overstates the signal by 2.5x because crops from one image
are near-duplicates; grouping by image barely helps because sibling images share
an experiment's conditions; and centering per microscope *raises* the honest
score, because the instrument was a confounder that hurt generalisation across
experiments rather than a source of signal.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# LDA needs a non-singular within-class scatter matrix, which 1024 features over
# ~1300 samples never gives. Reducing first is not a speed optimisation.
PCA_COMPONENTS = 50

# Folds for the grouped cross-validation. Five keeps ~9 experiments per held-out
# fold, enough for most proteins to appear on both sides.
N_SPLITS = 5

# Each permutation costs a full cross-validation, so this is the honesty/latency
# trade-off. Twenty is enough to place a score far outside the null; it is not
# enough for a precise p-value, and the UI reports the null's range rather than
# pretending otherwise.
N_PERMUTATIONS = 20

# Below these the fit is degenerate rather than merely weak.
MIN_POINTS = 50
MIN_CLASSES = 2
MIN_GROUPS = N_SPLITS


class NotEnoughDataError(ValueError):
    """The corpus cannot support a supervised projection at all."""


@dataclass
class DiscriminantResult:
    """A fitted projection plus what it is worth."""

    coords: np.ndarray  # (n, 2) display geometry, from the full-data fit
    balanced_accuracy: float
    chance: float
    null_mean: float
    null_max: float
    n_permutations: int
    n_proteins: int
    n_experiments: int
    explained: List[float] = field(default_factory=list)


def centre_per_microscope(
    embeddings: np.ndarray, microscope_ids: Sequence[Optional[int]]
) -> np.ndarray:
    """Subtract each instrument's mean embedding.

    The batch offset is the single largest direction in this data — microscope is
    decodable at 0.551 balanced accuracy across experiments and drops to 0.117
    (below the 0.25 chance for four instruments) once this runs. Removing it is
    not cosmetic: two proteins were acquired on one microscope only, so an
    uncorrected projection separates them by instrument and presents it as
    biology.
    """
    centred = embeddings.astype(np.float64, copy=True)
    ids = np.asarray([-1 if m is None else m for m in microscope_ids])
    for scope in np.unique(ids):
        rows = ids == scope
        centred[rows] -= centred[rows].mean(axis=0)
    return centred


def _pipeline(n_components: int):
    """Scale → PCA → LDA. Built per fit so no state leaks between folds."""
    from sklearn.decomposition import PCA
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(
        StandardScaler(),
        PCA(n_components=n_components, random_state=0),
        LinearDiscriminantAnalysis(),
    )


def _components_for(n_train: int, n_features: int) -> int:
    return max(2, min(PCA_COMPONENTS, n_train - 1, n_features))


def fit_projection(embeddings: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """2-D display geometry, fitted on everything.

    ⚠️ In-sample by design. Out-of-fold coordinates come from different LDA fits
    whose axes differ by an arbitrary rotation and sign, so plotting them
    together produces a scatter that means nothing. The honest number comes from
    `score_projection`; this only decides where the dots sit.
    """
    pipe = _pipeline(_components_for(len(labels), embeddings.shape[1]))
    projected = pipe.fit_transform(embeddings, labels)
    # One discriminant exists only for two classes; pad so callers always get 2-D.
    if projected.shape[1] == 1:
        projected = np.column_stack([projected[:, 0], np.zeros(len(projected))])
    return projected[:, :2]


def score_projection(
    embeddings: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
) -> float:
    """Balanced accuracy from cross-validation that never splits an experiment.

    Grouping is the whole point. Crops from one image are near-duplicates and
    sibling images share their experiment's conditions, so a random split leaves
    a relative of nearly every test crop in the training set and reports 0.68
    where the truth is 0.26.
    """
    from sklearn.metrics import balanced_accuracy_score
    from sklearn.model_selection import StratifiedGroupKFold

    splitter = StratifiedGroupKFold(n_splits=N_SPLITS, shuffle=True, random_state=0)
    predicted = np.empty(len(labels), dtype=labels.dtype)
    scored = np.zeros(len(labels), dtype=bool)

    for train, test in splitter.split(embeddings, labels, groups):
        # A fold that caught only one protein cannot train a discriminant; skip
        # it rather than crash, and leave its rows out of the score.
        if len(np.unique(labels[train])) < MIN_CLASSES:
            continue
        pipe = _pipeline(_components_for(len(train), embeddings.shape[1]))
        pipe.fit(embeddings[train], labels[train])
        predicted[test] = pipe.predict(embeddings[test])
        scored[test] = True

    if not scored.any():
        raise NotEnoughDataError(
            "every cross-validation fold was degenerate; too few experiments per protein"
        )
    return float(balanced_accuracy_score(labels[scored], predicted[scored]))


def permutation_null(
    embeddings: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    n_permutations: int = N_PERMUTATIONS,
) -> List[float]:
    """The same score with the protein labels shuffled between experiments.

    ⚠️ Shuffled at EXPERIMENT level, not per crop. Every crop of an experiment
    shares its protein, so a per-crop shuffle would scatter each label across all
    experiments and be destroyed by the grouped split — producing a null far
    below what a real confounder could achieve, and making any score look
    significant.
    """
    rng = np.random.default_rng(0)
    experiments = np.unique(groups)
    # One label per experiment, in experiment order.
    label_of = {g: labels[groups == g][0] for g in experiments}

    scores: List[float] = []
    for _ in range(n_permutations):
        shuffled = rng.permutation([label_of[g] for g in experiments])
        remap = dict(zip(experiments, shuffled))
        fake = np.array([remap[g] for g in groups])
        try:
            scores.append(score_projection(embeddings, fake, groups))
        except (NotEnoughDataError, ValueError) as e:
            logger.debug(f"permutation skipped: {e}")
    return scores


def compute_discriminant(
    embeddings: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    microscope_ids: Sequence[Optional[int]],
    n_permutations: int = N_PERMUTATIONS,
) -> DiscriminantResult:
    """The whole analysis: correct, project, score, and score the null."""
    n_classes = len(np.unique(labels))
    n_groups = len(np.unique(groups))

    if len(labels) < MIN_POINTS:
        raise NotEnoughDataError(
            f"need at least {MIN_POINTS} labelled crops, found {len(labels)}"
        )
    if n_classes < MIN_CLASSES:
        raise NotEnoughDataError(
            f"need at least {MIN_CLASSES} proteins to separate, found {n_classes}"
        )
    if n_groups < MIN_GROUPS:
        raise NotEnoughDataError(
            f"need at least {MIN_GROUPS} experiments to cross-validate across, "
            f"found {n_groups}"
        )

    corrected = centre_per_microscope(embeddings, microscope_ids)
    coords = fit_projection(corrected, labels)
    accuracy = score_projection(corrected, labels, groups)
    null = permutation_null(corrected, labels, groups, n_permutations)

    logger.info(
        f"discriminant: {len(labels)} crops, {n_classes} proteins, "
        f"{n_groups} experiments -> balanced accuracy {accuracy:.3f} "
        f"(chance {1 / n_classes:.3f}, null max {max(null) if null else float('nan'):.3f})"
    )

    return DiscriminantResult(
        coords=coords,
        balanced_accuracy=accuracy,
        chance=1.0 / n_classes,
        null_mean=float(np.mean(null)) if null else 0.0,
        null_max=float(np.max(null)) if null else 0.0,
        n_permutations=len(null),
        n_proteins=n_classes,
        n_experiments=n_groups,
    )
