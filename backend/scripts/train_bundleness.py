"""Train a bundleness model from the lab's pairwise comparisons.

    python scripts/train_bundleness.py --metric-id 7 --version v2

Reads every non-undone comparison of one ranking metric plus the DINOv3
embeddings of the crops involved, reports how well a model trained on the other
experiments ranks each experiment's crops, and writes
``ml/bundleness/model_<version>.json``.

Read-only against the database. Deploying the result is a separate, deliberate
step: set ``ACTIVE_VERSION`` in ``ml/bundleness/model.py``, commit the artifact,
rebuild — the backend rescores every crop on its next start.

Run it inside the backend image (it needs scikit-learn and the app's models):

    docker run --rm --network container:maptimize-backend \
        --env-file <(docker exec maptimize-backend env | grep -E '^(DATABASE_URL|SECRET_KEY)=') \
        -e CUDA_VISIBLE_DEVICES= -v "$PWD/backend:/app" -w /app \
        --entrypoint python <backend image> scripts/train_bundleness.py --version v2
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from database import get_db_context  # noqa: E402
from ml.bundleness.model import artifact_path  # noqa: E402
from ml.bundleness.training import (  # noqa: E402
    N_COMPONENTS,
    REGULARISATION_C,
    compared_rows,
    fit_scorer,
    validate,
)
from models.cell_crop import CellCrop  # noqa: E402
from models.image import Image  # noqa: E402
from models.metric import MetricComparison, MetricImage  # noqa: E402


async def load_training_data(metric_id: int):
    """Comparisons as row-index triples, with the embeddings they point into."""
    async with get_db_context() as db:
        side_a, side_b = MetricImage.__table__.alias("a"), MetricImage.__table__.alias("b")
        winner = MetricImage.__table__.alias("w")
        comparisons = (
            await db.execute(
                select(side_a.c.cell_crop_id, side_b.c.cell_crop_id, winner.c.cell_crop_id)
                .select_from(MetricComparison.__table__)
                .join(side_a, side_a.c.id == MetricComparison.image_a_id)
                .join(side_b, side_b.c.id == MetricComparison.image_b_id)
                .join(winner, winner.c.id == MetricComparison.winner_id)
                .where(
                    MetricComparison.metric_id == metric_id,
                    MetricComparison.undone.is_(False),
                )
            )
        ).all()

        crop_ids = {crop for a, b, _ in comparisons for crop in (a, b) if crop is not None}
        crops = (
            await db.execute(
                select(
                    CellCrop.id,
                    CellCrop.embedding,
                    CellCrop.embedding_model,
                    Image.experiment_id,
                )
                .join(Image, CellCrop.image_id == Image.id)
                .where(CellCrop.id.in_(crop_ids), CellCrop.embedding.isnot(None))
                .order_by(CellCrop.id)
            )
        ).all()

    encoders = Counter(model for _, _, model, _ in crops)
    if len(encoders) != 1:
        raise SystemExit(f"compared crops span several encoders, cannot mix them: {dict(encoders)}")

    row_of = {crop_id: row for row, (crop_id, *_rest) in enumerate(crops)}
    embeddings = np.array([np.asarray(e, dtype=np.float64) for _, e, _, _ in crops])
    experiments = np.array([experiment for *_rest, experiment in crops])
    pairs = np.array(
        [
            (row_of[a], row_of[b], int(w == a))
            for a, b, w in comparisons
            # A comparison whose crop was deleted, or lost its embedding, has
            # nothing to learn from; a crop compared with itself says nothing.
            if a in row_of and b in row_of and a != b
        ]
    )
    return embeddings, pairs, experiments, next(iter(encoders))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--metric-id", type=int, default=7)
    parser.add_argument("--version", required=True, help="e.g. v2 — never reuse one")
    parser.add_argument("--force", action="store_true", help="overwrite an existing artifact")
    args = parser.parse_args()

    target = artifact_path(args.version)
    if target.exists() and not args.force:
        # Scores stored under this version would silently stop matching the file.
        raise SystemExit(f"{target.name} already exists; pick a new --version")

    embeddings, pairs, experiments, encoder = asyncio.run(load_training_data(args.metric_id))
    print(f"{len(pairs)} comparisons over {len(compared_rows(pairs))} crops, encoder {encoder}")

    # Leave one experiment out: the question a new upload actually asks.
    by_experiment = validate(embeddings, pairs, experiments)
    # 5 random folds of crops: the easier question, for comparison with the
    # July prototype (0.82 pooled on 371 crops).
    folds = np.random.default_rng(0).integers(0, 5, len(embeddings))
    by_crop = validate(embeddings, pairs, folds)
    for name, report in (("leave-one-experiment-out", by_experiment), ("5-fold by crop", by_crop)):
        print(name, json.dumps(report, indent=2))

    scorer = fit_scorer(embeddings, pairs)
    target.write_text(
        json.dumps(
            {
                "version": args.version,
                "embedding_model": encoder,
                "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "method": (
                    f"StandardScaler -> PCA({N_COMPONENTS}, whiten) -> Bradley-Terry "
                    f"logistic head (C={REGULARISATION_C}, no intercept), folded to one "
                    "affine map and z-scored against the training crops"
                ),
                "metric_id": args.metric_id,
                "validation": {
                    "leave_one_experiment_out": by_experiment,
                    "five_fold_by_crop": by_crop,
                },
                "bias": scorer.bias,
                "weights": [float(w) for w in scorer.weights],
            },
            indent=1,
        )
        + "\n"
    )
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
