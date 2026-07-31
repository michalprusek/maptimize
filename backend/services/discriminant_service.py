"""Supervised projection that maximally separates MAP proteins.

UMAP answers "what structure is in these embeddings"; this answers "how far
apart can the proteins be pushed, and how much of that is real". The second
half is the reason this file is not four lines of scikit-learn.

⚠️ The measurements that fixed every choice here (balanced accuracy over 14
proteins, chance 0.071, on the production corpus of 1277 crops):

    split                      raw     microscope-centred
    random over crops         0.679          0.665
    grouped by image          0.653          0.655
    grouped by experiment     0.183          0.300

Permutation null under the experiment split: mean 0.055, max 0.078, over 20 shuffles.

So: a random split overstates the signal by 2.5x, and almost all of that is
EXPERIMENT-level, not image-level. Grouping by image recovers only 0.026 of the
0.493 gap (~5%): 40% of images hold a single crop, so same-image duplication is a
minor contributor. What leaks is that each experiment carries one protein and one
set of acquisition conditions, so any split that puts an experiment on both sides
lets the model recover the label from the batch.

Centering per microscope *raises* the honest score (0.183 -> 0.300), because the
instrument was a confounder hurting generalisation across experiments rather than
a source of signal.
"""
import logging
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Regularisation, not rank. 1024 features over 1277 samples is guaranteed
# overfitting — LDA reaches 0.998 training accuracy on the raw features — so the
# projection would separate noise. Note the default svd solver never inverts the
# within-class scatter, so this is NOT about singularity; a maintainer who knows
# that would otherwise read a false justification and drop the step. It also
# happens to be ~5x faster (7.4s vs 38.3s), which is a bonus, not the reason.
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
# A class needs two experiments to be scoreable at all — one to train on and one
# to be tested in. See scoreable_mask.
MIN_GROUPS_PER_CLASS = 2


class NotEnoughDataError(ValueError):
    """The corpus cannot support a supervised projection at all."""


@dataclass(frozen=True)
class DiscriminantResult:
    """A fitted projection plus what it is worth.

    `crop_ids` travels with `coords` so the two cannot drift: a row dropped
    anywhere upstream would shift every later coordinate onto the wrong crop,
    mislabelling each dot with someone else's protein — silently, on a plot
    people read as data.

    `null_mean`/`null_max` are None when no shuffle survived, never 0.0. A
    balanced accuracy of exactly zero under shuffling is impossible, so that
    sentinel would read as the strongest possible evidence for the score.
    """

    crop_ids: tuple
    coords: np.ndarray  # (n, 2) display geometry, from the full-data fit
    balanced_accuracy: float
    chance: float
    null_mean: Optional[float]
    null_max: Optional[float]
    # The bar to read the score against. The MAX of a handful of draws is not a
    # ceiling: measured on this corpus, 17.5% of individual shuffles exceed the
    # max of the shipped 20, so that "ceiling" is a lucky low draw frozen by the
    # seed. The 95th percentile is stable; the p-value is the honest statistic.
    null_p95: Optional[float]
    p_value: Optional[float]
    n_permutations: int
    n_proteins: int
    n_experiments: int
    # (protein, out-of-fold recall, crops scored) — see score_projection.
    per_class: tuple[tuple[str, float, int], ...] = ()
    # Proteins on the plot but absent from the score: only one experiment each,
    # so grouped CV cannot test them. See scoreable_mask.
    unscoreable_proteins: tuple[str, ...] = ()
    # Identity of the corpus this was fitted on, so a later read can tell it has
    # gone stale instead of serving old coordinates beside fresh labels.
    fingerprint: str = ""

    def __post_init__(self):
        if self.coords.shape != (len(self.crop_ids), 2):
            raise ValueError(
                f"coords {self.coords.shape} do not line up with "
                f"{len(self.crop_ids)} crop ids"
            )

    def coords_by_id(self) -> dict:
        return {
            int(cid): (float(x), float(y))
            for cid, (x, y) in zip(self.crop_ids, self.coords)
        }


def centre_per_microscope(
    embeddings: np.ndarray, microscope_ids: Sequence[Optional[int]]
) -> np.ndarray:
    """Subtract each instrument's mean embedding.

    The batch offset is the single largest direction in this data — microscope is
    decodable at 0.548 balanced accuracy across experiments and drops to 0.116
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


def _pipeline(n_components: int, n_classes: int):
    """Scale → PCA → LDA. Built per fit so no state leaks between folds."""
    from sklearn.decomposition import PCA
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(
        StandardScaler(),
        PCA(n_components=n_components, random_state=0),
        # Uniform priors, because the score is balanced accuracy: leaving the
        # empirical priors lets the classifier favour large classes while the
        # metric weights all 14 equally, costing ~0.04 (0.260 -> 0.300 measured).
        LinearDiscriminantAnalysis(priors=np.full(n_classes, 1.0 / n_classes)),
    )


def _components_for(n_train: int, n_features: int) -> int:
    return max(2, min(PCA_COMPONENTS, n_train - 1, n_features))


def fit_projection(embeddings: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """2-D display geometry, fitted on everything.

    ⚠️ In-sample by design. Out-of-fold coordinates come from different fits whose
    axes are not related by ANY alignment: measured between two folds, the
    principal angles are 1.3° and 67°, and the second axis differs in scale by
    10.7x. Do not reach for Procrustes — there is no congruence to undo. Plotting
    them together produces a scatter that means nothing. The honest number comes
    from `score_projection`; this only decides where the dots sit.
    """
    pipe = _pipeline(
        _components_for(len(labels), embeddings.shape[1]), len(np.unique(labels))
    )
    projected = pipe.fit_transform(embeddings, labels)
    # One discriminant exists only for two classes; pad so callers always get 2-D.
    if projected.shape[1] == 1:
        projected = np.column_stack([projected[:, 0], np.zeros(len(projected))])
    return projected[:, :2]


def scoreable_mask(labels: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Rows whose protein appears in at least two experiments.

    ⚠️ A protein confined to ONE experiment cannot be scored by a split that
    holds experiments out: the only fold that tests it is the fold that removed
    every one of its crops from training, so the classifier has never seen the
    class and its recall is 0 by construction — not by measurement.

    Left in, such a class drags balanced accuracy down by 1/n_classes and the
    result reads as "these proteins are indistinguishable". Found in production:
    a user scoped to their own 6 experiments, each carrying a different protein,
    scored exactly 0.000 against a chance of 0.167. Every one of those zeros was
    arithmetic, not biology.

    Such classes still contribute to the FIT (their points are on the plot); they
    are only excluded from the score and the null, which must agree on a corpus.
    """
    counts = {c: len(np.unique(groups[labels == c])) for c in np.unique(labels)}
    return np.array([counts[c] >= MIN_GROUPS_PER_CLASS for c in labels])


def _name(label, names: Optional[Mapping]) -> str:
    """Display name for a class label.

    ⚠️ Labels are `map_protein_id` INTEGERS. Without this the metrics reach the
    UI as "7 0.79" — a table of database ids the reader cannot act on, and the
    per-class breakdown exists precisely so they can see WHICH proteins separate.
    """
    return str((names or {}).get(label, label))


def score_projection(
    embeddings: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    label_names: Optional[Mapping] = None,
) -> tuple[float, int, tuple[tuple[str, float, int], ...]]:
    """Balanced accuracy, how many classes it covered, and the per-class recall.

    Grouping is the whole point, and the leak it closes is at the EXPERIMENT
    level: each experiment carries one protein and one set of acquisition
    conditions, so any split that puts an experiment on both sides lets the model
    read the label off the batch and reports 0.68 where the truth is 0.30.
    Grouping by image instead closes almost none of that gap — 40% of images hold
    a single crop.
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
        pipe = _pipeline(
            _components_for(len(train), embeddings.shape[1]),
            len(np.unique(labels[train])),
        )
        pipe.fit(embeddings[train], labels[train])
        predicted[test] = pipe.predict(embeddings[test])
        scored[test] = True

    if not scored.any():
        raise NotEnoughDataError(
            "every cross-validation fold was degenerate; too few experiments per protein"
        )
    # The class count is returned, not assumed: `balanced_accuracy_score`
    # averages recall only over classes present in y_true, so a skipped fold
    # silently drops a protein from the average. Quoting that beside a `chance`
    # computed from ALL proteins reads as "1.00 against chance 0.50" when one of
    # the two was never tested.
    truth, guess = labels[scored], predicted[scored]
    # Per-class recall, because balanced accuracy is their mean and a mean is the
    # wrong summary here: measured live, CLIP170 reaches 0.79 and TRIM46 0.47
    # while MAP2d manages 0.05 across 167 crops. "The model separates the MAPs at
    # 0.30" invites reading that as 0.30 for each of them.
    per_class = tuple(
        (
            _name(c, label_names),
            float(np.mean(guess[truth == c] == c)),
            int(np.sum(truth == c)),
        )
        for c in np.unique(truth)
    )
    return (
        float(balanced_accuracy_score(truth, guess)),
        int(len(per_class)),
        per_class,
    )


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

    # ⚠️ Verified, not assumed. The crop-curation endpoint lets a group member set
    # a crop's protein independently of its experiment's, so a mixed experiment is
    # reachable. If one existed, the real score could exploit within-experiment
    # structure this null cannot reproduce — pushing the null down and making every
    # score look more significant than it is.
    mixed = [int(g) for g in experiments if len(np.unique(labels[groups == g])) > 1]
    if mixed:
        raise NotEnoughDataError(
            "cannot build a null: experiments "
            f"{', '.join(str(g) for g in mixed[:5])} carry more than one protein, "
            "so shuffling at experiment level would not reproduce the real structure"
        )

    label_of = {g: labels[groups == g][0] for g in experiments}

    scores: List[float] = []
    for _ in range(n_permutations):
        shuffled = rng.permutation([label_of[g] for g in experiments])
        remap = dict(zip(experiments, shuffled))
        fake = np.array([remap[g] for g in groups])
        try:
            scores.append(score_projection(embeddings, fake, groups)[0])
        except NotEnoughDataError as e:
            # Only the expected degeneracy. A genuine sklearn ValueError (NaN in
            # the embeddings, a singular scatter) must NOT be swallowed: it would
            # leave an empty null, reported as though shuffling achieved nothing.
            logger.debug(f"permutation skipped: {e}")
    return scores


def compute_discriminant(
    embeddings: np.ndarray,
    labels: np.ndarray,
    groups: np.ndarray,
    microscope_ids: Sequence[Optional[int]],
    n_permutations: int = N_PERMUTATIONS,
    crop_ids: Optional[Sequence[int]] = None,
    fingerprint: str = "",
    label_names: Optional[Mapping] = None,
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
    # The plot shows everything; the score and its null share a smaller corpus.
    coords = fit_projection(corrected, labels)

    keep = scoreable_mask(labels, groups)
    dropped = tuple(sorted({_name(c, label_names) for c in np.unique(labels[~keep])}))
    if len(np.unique(labels[keep])) < MIN_CLASSES:
        raise NotEnoughDataError(
            "cannot score this selection: "
            + (
                "every protein here appears in a single experiment"
                if len(dropped) == n_classes
                else f"only {len(np.unique(labels[keep]))} protein(s) appear in "
                "more than one experiment"
            )
            + ", and a split that holds whole experiments out can never test a "
            "protein it was not trained on. Add an experiment for a protein you "
            "already have, or widen the selection."
        )
    accuracy, n_scored, per_class = score_projection(
        corrected[keep], labels[keep], groups[keep], label_names
    )
    null = permutation_null(
        corrected[keep], labels[keep], groups[keep], n_permutations
    )

    logger.info(
        f"discriminant: {len(labels)} crops, {n_classes} proteins, "
        f"{n_groups} experiments -> balanced accuracy {accuracy:.3f} "
        f"(chance {1 / n_scored:.3f} over {n_scored} scored classes, null max "f"{max(null) if null else float('nan'):.3f} from {len(null)} shuffles)"
    )

    return DiscriminantResult(
        crop_ids=tuple(crop_ids) if crop_ids is not None else tuple(range(len(labels))),
        coords=coords,
        balanced_accuracy=accuracy,
        # Denominator of the score that was actually computed, not of the corpus
        # we hoped to score.
        chance=1.0 / n_scored,
        null_mean=float(np.mean(null)) if null else None,
        null_max=float(np.max(null)) if null else None,
        null_p95=float(np.percentile(null, 95)) if null else None,
        # (1 + #{null >= observed}) / (n + 1) — the standard permutation p, which
        # cannot go below 1/(n+1). With 20 shuffles the floor is 0.048, so this
        # can establish "outside the null" and never "p < 0.01".
        p_value=(
            (1 + sum(1 for s in null if s >= accuracy)) / (len(null) + 1)
            if null else None
        ),
        n_permutations=len(null),
        n_proteins=n_scored,
        n_experiments=n_groups,
        per_class=per_class,
        unscoreable_proteins=dropped,
        fingerprint=fingerprint,
    )


# =============================================================================
# Scope cache
#
# One cross-validation is ~28 s and the full computation runs 21 of them (the
# score plus 20 shuffles) — roughly ten minutes — so this never runs inside
# a request. Same shape as umap_service's refresh bookkeeping: module-level state
# keyed by scope, an in-flight set so a group's dashboards do not each kick off
# the same computation, and a recorded failure so a doomed run is not rescheduled
# on every poll.
#
# Cached in process rather than in the database on purpose: this is a scope-level
# analysis, not a per-crop attribute, the metrics have nowhere natural to live in
# the schema, and a restart simply recomputes.
# =============================================================================

_projections: dict[str, DiscriminantResult] = {}
_inflight: set[str] = set()
_failed: dict[str, str] = {}

# Bumped when a scope is invalidated. `invalidate()` cannot cancel work already
# running, so without this a Retry pressed mid-fit wipes the cache, schedules
# nothing (the in-flight guard skips it), and then the OLD fit writes its stale
# result back and clears the failure flag as though it were fresh.
_generation: dict[str, int] = {}

# Corpus fingerprint at the moment a failure was recorded, so the failure can be
# retired when the corpus moves rather than outliving the condition it describes.
_failed_at: dict[str, str] = {}


def corpus_fingerprint(rows: Sequence[tuple]) -> str:
    """Identity of the corpus a projection was fitted on.

    Covers crop id, protein label and microscope, because all three change what
    the fit means: the labels are what it is fitted to, and the microscope drives
    the centering. Cheap — no embeddings, one small tuple per crop.
    """
    import hashlib

    digest = hashlib.sha256()
    for row in rows:
        digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def scope_key(user_id: int, group_ids: Sequence[int]) -> str:
    """One cache entry per readable corpus: the caller AND their groups.

    It used to key on the group alone, which was sound only while every member of
    a group read exactly the same rows -- guaranteed by the automatic adoption of
    a joiner's group-less experiments. That adoption is gone and membership is
    many-to-many, so neither half of the assumption holds: a member of A+B reads
    more than a member of A, and sharing one fit between them would report a score
    measured on a corpus the caller cannot see.

    Both parts are prefixed and the groups sorted, so the key is stable and a user
    id can never be read as a group id.
    """
    return f"u{user_id}|g{','.join(str(g) for g in sorted(group_ids))}"


def cached(key: str):
    """The scope's projection, or None if it has not been computed yet."""
    return _projections.get(key)


def compute_error(key: str) -> Optional[str]:
    """Why this scope's last computation failed, or None if it did not."""
    return _failed.get(key)


def clear_failure_if_corpus_moved(key: str, fingerprint: str) -> bool:
    """Forget a recorded failure once the corpus it described has changed.

    Every failure this service records is transient by nature — "need at least 5
    experiments", "no crops with a protein assignment". Without this the message
    is served forever, so a lab that fixes exactly what it was told to fix keeps
    reading the complaint, and the MCP surface has no retry at all.
    """
    if key not in _failed or _failed_at.get(key) == fingerprint:
        return False
    _failed.pop(key, None)
    _failed_at.pop(key, None)
    return True


def is_computing(key: str) -> bool:
    return key in _inflight


def store(key: str, result: DiscriminantResult, generation: int) -> None:
    """Cache a finished fit, unless it was superseded while it ran."""
    if generation != _generation.get(key, 0):
        logger.info(f"discriminant scope {key}: discarding a superseded fit")
        return
    _projections[key] = result
    _failed.pop(key, None)


def record_failure(
    key: str,
    reason: str,
    generation: Optional[int] = None,
    fingerprint: str = "",
) -> None:
    if generation is not None and generation != _generation.get(key, 0):
        return
    _failed[key] = reason
    _failed_at[key] = fingerprint


def generation(key: str) -> int:
    return _generation.get(key, 0)


def invalidate(key: Optional[str] = None) -> None:
    """Drop a scope's projection (or every scope) so the next read recomputes."""
    if key is None:
        _projections.clear()
        _failed.clear()
        for k in list(_generation):
            _generation[k] += 1
        return
    # One scope only: a user retrying their own failed fit must not discard every
    # other user's cached projection on this worker.
    _projections.pop(key, None)
    _failed.pop(key, None)
    _failed_at.pop(key, None)
    _generation[key] = _generation.get(key, 0) + 1


async def refresh_discriminant_scope(user_id: int, group_ids: Sequence[int]) -> None:
    """Compute and cache one scope's projection. Safe to call from a BackgroundTask.

    Deduped through `_inflight`, so a dashboard polling while the fit runs does
    not stack up a second one. Never raises: a background task that throws is
    swallowed by the runtime, so the reason is recorded for the next poll to
    report instead of the client spinning forever.
    """
    import asyncio

    from sqlalchemy import select

    from database import async_session_maker
    from models.cell_crop import CellCrop
    from models.experiment import Experiment
    from models.image import Image, MapProtein
    from utils.groups import experiment_owner_filter

    key = scope_key(user_id, group_ids)
    if key in _inflight:
        return
    _inflight.add(key)
    gen = generation(key)
    try:
        async with async_session_maker() as db:
            rows = (
                await db.execute(
                    select(
                        CellCrop.id,
                        CellCrop.embedding,
                        CellCrop.map_protein_id,
                        Experiment.id,
                        Experiment.microscope_id,
                        MapProtein.name,
                    )
                    .join(Image, CellCrop.image_id == Image.id)
                    .join(Experiment, Image.experiment_id == Experiment.id)
                    .join(MapProtein, CellCrop.map_protein_id == MapProtein.id)
                    .where(
                        experiment_owner_filter(user_id, group_ids),
                        CellCrop.embedding.isnot(None),
                        CellCrop.map_protein_id.isnot(None),
                    )
                    .order_by(CellCrop.id)
                )
            ).all()

        if not rows:
            record_failure(
                key,
                "No crops with both an embedding and a protein assignment",
                gen,
                corpus_fingerprint([]),
            )
            return

        crop_ids = [r[0] for r in rows]
        embeddings = np.asarray([r[1] for r in rows], dtype=np.float64)
        labels = np.asarray([r[2] for r in rows])
        groups = np.asarray([r[3] for r in rows])
        microscopes = [r[4] for r in rows]

        # sklearn is synchronous and this takes minutes; keep it off the loop or
        # every other request on this worker stalls behind it.
        result = await asyncio.to_thread(
            compute_discriminant,
            embeddings,
            labels,
            groups,
            microscopes,
            N_PERMUTATIONS,
            crop_ids,
            corpus_fingerprint([(r[0], r[2], r[4]) for r in rows]),
            # protein id -> name, so the per-class recall names proteins rather
            # than printing database ids at a biologist.
            {r[2]: r[5] for r in rows},
        )
        store(key, result, gen)
    except NotEnoughDataError as e:
        logger.info(f"discriminant scope {key}: {e}")
        record_failure(key, str(e), gen, corpus_fingerprint([(r[0], r[2], r[4]) for r in rows]))
    except Exception as e:
        logger.error(f"discriminant scope {key} failed: {e}", exc_info=True)
        record_failure(key, f"Projection failed: {e}", gen)
    finally:
        _inflight.discard(key)


async def current_fingerprint(user_id: int, group_ids: Sequence[int], db) -> str:
    """The corpus identity as it stands now, without loading embeddings.

    Compared against the cached fit's fingerprint so a re-annotated batch is
    reported stale instead of being drawn at its old coordinates in its new
    colour — a point sitting in the wrong cluster wearing the right label.
    """
    from sqlalchemy import select

    from models.cell_crop import CellCrop
    from models.experiment import Experiment
    from models.image import Image
    from utils.groups import experiment_owner_filter

    rows = (
        await db.execute(
            select(CellCrop.id, CellCrop.map_protein_id, Experiment.microscope_id)
            .join(Image, CellCrop.image_id == Image.id)
            .join(Experiment, Image.experiment_id == Experiment.id)
            .where(
                experiment_owner_filter(user_id, group_ids),
                CellCrop.embedding.isnot(None),
                CellCrop.map_protein_id.isnot(None),
            )
            .order_by(CellCrop.id)
        )
    ).all()
    return corpus_fingerprint(rows)
