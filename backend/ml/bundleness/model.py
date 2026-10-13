"""The shipped bundleness model, and the one function that applies it.

The model is a learning-to-rank head on frozen foundation-model features: a
Bradley-Terry / RankNet reward model fitted to the lab's pairwise "which cell is
more bundled?" judgements (``training.py``). Every stage of it — standardise,
PCA-whiten, dot with the logistic weights — is affine, so the whole chain folds
into one vector and one offset:

    score = weights · embedding + bias

That is what the artifact stores. Inference therefore needs no scikit-learn, no
pickle and no GPU, and the artifact is a JSON file a reviewer can diff.

The score is a z-score against the crops the model was trained on: 0 is the
average annotated crop and 1 is one standard deviation of them. The reference
mean and spread are frozen into ``weights``/``bias`` at training time, so a
number means the same thing next month — a percentile over the live corpus would
move with every upload.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

ARTIFACT_DIR = Path(__file__).parent

# The version the application scores with. Bumping it is the whole deployment of
# a retrained model: on the next start every crop whose stored
# ``bundleness_model`` differs is rescored (``bundleness_service.backfill``).
ACTIVE_VERSION = "v1"


@dataclass(frozen=True)
class BundlenessModel:
    """One trained scorer. Immutable: a score is only reproducible if this is."""

    version: str
    # The encoder whose embeddings the weights were fitted on. A vector from any
    # other encoder has the right length and the wrong meaning, and would score
    # without complaint — so it is checked, never assumed.
    embedding_model: str
    weights: np.ndarray
    bias: float

    def score(self, embeddings: np.ndarray) -> np.ndarray:
        """Score an ``(n, dim)`` array of raw (un-normalised) embeddings."""
        embeddings = np.asarray(embeddings, dtype=np.float64)
        if embeddings.ndim != 2 or embeddings.shape[1] != self.weights.shape[0]:
            raise ValueError(
                f"expected embeddings of shape (n, {self.weights.shape[0]}), "
                f"got {embeddings.shape}"
            )
        return embeddings @ self.weights + self.bias

    def accepts(self, embedding_model: Optional[str]) -> bool:
        """Whether an embedding produced by ``embedding_model`` can be scored."""
        return embedding_model == self.embedding_model


def artifact_path(version: str) -> Path:
    return ARTIFACT_DIR / f"model_{version}.json"


def read_artifact(path: Path) -> BundlenessModel:
    """Parse an artifact, refusing one that could score silently wrong."""
    payload = json.loads(path.read_text())
    weights = np.asarray(payload["weights"], dtype=np.float64)
    bias = float(payload["bias"])
    if weights.ndim != 1 or not np.all(np.isfinite(weights)) or not np.isfinite(bias):
        raise ValueError(f"{path.name}: weights/bias are not a finite vector and scalar")
    return BundlenessModel(
        version=str(payload["version"]),
        embedding_model=str(payload["embedding_model"]),
        weights=weights,
        bias=bias,
    )


@lru_cache(maxsize=1)
def load_model() -> BundlenessModel:
    """The active model, read once per process."""
    path = artifact_path(ACTIVE_VERSION)
    model = read_artifact(path)
    if model.version != ACTIVE_VERSION:
        # The stored version is what lands on every crop row; a file renamed
        # without retraining would stamp rows with a version nobody can find.
        raise ValueError(
            f"{path.name} declares version {model.version!r}, expected {ACTIVE_VERSION!r}"
        )
    return model
