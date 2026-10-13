"""Fit and validate a bundleness model from pairwise comparisons.

Pure functions over arrays — no database, no files. ``scripts/train_bundleness.py``
does the IO. Kept apart from ``model.py`` so the API never imports scikit-learn.

Method (standard learning-to-rank on frozen features; Burges et al. 2005
"RankNet", Bradley & Terry 1952):

    P(a beats b) = sigmoid(w · (phi(a) - phi(b)))

with ``phi`` = StandardScaler -> PCA(whiten). There is no intercept because the
model must be antisymmetric: swapping a and b has to flip the probability
exactly. Each comparison is fed in both orders, which enforces that and makes
the two classes balanced by construction.

PCA is not decoration. The embedding has 1024 dimensions and the lab has ranked
a few hundred distinct crops, so an unreduced logistic regression has more
parameters than examples. 25 components was the plateau when measured
(2026-10-13, 361 crops: Spearman 0.82 / 0.86 / 0.86 / 0.85 at 10 / 25 / 50 / 100).
"""

from dataclasses import dataclass
from typing import Callable, Dict, Sequence

import numpy as np
from scipy.stats import spearmanr
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

N_COMPONENTS = 25
REGULARISATION_C = 0.1
RANDOM_STATE = 0


@dataclass(frozen=True)
class LinearScorer:
    """``score = weights · embedding + bias``, the folded pipeline."""

    weights: np.ndarray
    bias: float

    def __call__(self, embeddings: np.ndarray) -> np.ndarray:
        return np.asarray(embeddings, dtype=np.float64) @ self.weights + self.bias


def compared_rows(pairs: np.ndarray) -> np.ndarray:
    """Row indices of every crop that appears in at least one comparison."""
    return np.unique(pairs[:, :2])


def fit_scorer(
    embeddings: np.ndarray,
    pairs: np.ndarray,
    n_components: int = N_COMPONENTS,
    c: float = REGULARISATION_C,
) -> LinearScorer:
    """Fit the Bradley-Terry head and fold it into one vector.

    ``pairs`` is ``(n, 3)`` int: row of crop a, row of crop b, 1 if a won else 0.
    The scaler and PCA are fitted on the compared crops only — the rest of the
    corpus has no labels, and letting it shape the features would make the model
    depend on what happened to be uploaded that week.

    The returned scores are standardised against those same crops (mean 0, SD 1),
    which is what gives the number a fixed meaning.
    """
    rows = compared_rows(pairs)
    reference = np.asarray(embeddings[rows], dtype=np.float64)

    scaler = StandardScaler().fit(reference)
    pca = PCA(n_components, whiten=True, random_state=RANDOM_STATE).fit(
        scaler.transform(reference)
    )

    def features(x: np.ndarray) -> np.ndarray:
        return pca.transform(scaler.transform(x))

    difference = features(embeddings[pairs[:, 0]]) - features(embeddings[pairs[:, 1]])
    outcome = pairs[:, 2]
    head = LogisticRegression(C=c, fit_intercept=False, max_iter=2000).fit(
        np.vstack([difference, -difference]),
        np.concatenate([outcome, 1 - outcome]),
    )
    w = head.coef_[0]

    # Fold scaler -> PCA(whiten) -> dot(w) into one affine map of the raw
    # embedding:  ((x - m) / s - pm) @ C.T / sqrt(ev) @ w
    direction = pca.components_.T @ (w / np.sqrt(pca.explained_variance_))
    weights = direction / scaler.scale_
    bias = -float((scaler.mean_ / scaler.scale_ + pca.mean_) @ direction)

    folded = reference @ weights + bias
    if not np.allclose(folded, features(reference) @ w, rtol=1e-6, atol=1e-6):
        # The artifact stores only the folded form; if the algebra above ever
        # stops matching the pipeline, every stored score is wrong and nothing
        # else would notice.
        raise AssertionError("folded weights disagree with the fitted pipeline")

    spread = float(folded.std())
    if spread == 0:
        raise ValueError("model scores every training crop identically")
    return LinearScorer(weights=weights / spread, bias=(bias - float(folded.mean())) / spread)


def latent_strengths(pairs: np.ndarray, n_rows: int, ridge: float = 1.0) -> np.ndarray:
    """Feature-free Bradley-Terry strength per crop: the ranking to reproduce.

    One free parameter per crop, ridge-regularised so a crop that won its only
    comparison does not run to infinity. This uses no image information at all,
    which is what makes it a fair target for the image model.
    """
    strengths = np.zeros(n_rows)
    a, b, won = pairs[:, 0], pairs[:, 1], pairs[:, 2].astype(float)
    for _ in range(300):
        p = 1.0 / (1.0 + np.exp(-(strengths[a] - strengths[b])))
        gradient = np.zeros(n_rows)
        hessian = np.full(n_rows, ridge)
        np.add.at(gradient, a, won - p)
        np.add.at(gradient, b, p - won)
        gradient -= ridge * strengths
        np.add.at(hessian, a, p * (1 - p))
        np.add.at(hessian, b, p * (1 - p))
        strengths += gradient / hessian
    return strengths


def held_out_scores(
    embeddings: np.ndarray,
    pairs: np.ndarray,
    groups: Sequence,
    fit: Callable[[np.ndarray, np.ndarray], LinearScorer] = fit_scorer,
) -> np.ndarray:
    """Score every compared crop by a model that never saw its group.

    One fold per distinct group (leave-one-group-out). A fold trains only on
    comparisons whose BOTH crops are outside the held-out group: a pair with one
    foot in the test fold still teaches the model about the held-out crop.
    Splitting comparisons instead of crops is the classic leak here — the same
    crop then sits on both sides and the score measures memory.

    Rows that are not in any comparison come back as NaN.
    """
    groups = np.asarray(groups)
    rows = compared_rows(pairs)
    predicted = np.full(len(embeddings), np.nan)
    for group in np.unique(groups[rows]):
        held_out = groups == group
        training = pairs[~held_out[pairs[:, 0]] & ~held_out[pairs[:, 1]]]
        scorer = fit(embeddings, training)
        test_rows = rows[held_out[rows]]
        predicted[test_rows] = scorer(embeddings[test_rows])
    return predicted


def validate(
    embeddings: np.ndarray,
    pairs: np.ndarray,
    groups: Sequence,
) -> Dict[str, float]:
    """How well the model ranks crops from a group it never trained on.

    Reports the WITHIN-group rank correlation, averaged over groups, as the
    headline. Pooling held-out scores across groups flatters the model: every
    group in the training data is a different protein, proteins differ in mean
    bundleness, and a model that had only learned to recognise the protein would
    already reach 0.63 pooled (measured). Within one group that shortcut is worth
    exactly zero.
    """
    groups = np.asarray(groups)
    rows = compared_rows(pairs)
    target = latent_strengths(pairs, len(embeddings))
    predicted = held_out_scores(embeddings, pairs, groups)

    within = []
    for group in np.unique(groups[rows]):
        members = rows[groups[rows] == group]
        if len(members) >= 3:
            within.append(float(spearmanr(predicted[members], target[members])[0]))

    both_held_out = groups[pairs[:, 0]] == groups[pairs[:, 1]]
    judged = pairs[both_held_out]
    agrees = (predicted[judged[:, 0]] > predicted[judged[:, 1]]).astype(int) == judged[:, 2]
    return {
        "n_crops": int(len(rows)),
        "n_comparisons": int(len(pairs)),
        "n_groups": int(len(np.unique(groups[rows]))),
        "within_group_spearman_mean": float(np.mean(within)),
        "within_group_spearman_min": float(np.min(within)),
        "pooled_spearman": float(spearmanr(predicted[rows], target[rows])[0]),
        "pairwise_accuracy": float(agrees.mean()),
    }
