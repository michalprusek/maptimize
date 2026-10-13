"""The bundleness model: the shipped artifact, and the training that makes one.

The artifact stores a single folded vector, so nearly everything that can go
wrong goes wrong SILENTLY — a vector of the right length always produces a
number. These tests pin the properties a wrong number would violate.
"""
import json

import numpy as np
import pytest
from scipy.stats import spearmanr

from ml.bundleness import model as model_module
from ml.bundleness.model import ACTIVE_VERSION, BundlenessModel, load_model, read_artifact
from ml.bundleness.training import (
    compared_rows,
    fit_scorer,
    held_out_scores,
    latent_strengths,
    validate,
)


# =============================================================================
# The shipped artifact
# =============================================================================

def test_the_shipped_artifact_is_the_active_version_and_matches_the_crop_encoder():
    model = load_model()
    assert model.version == ACTIVE_VERSION
    # cell_crops.embedding is vector(1024) written by the dinov3-large encoder.
    assert model.weights.shape == (1024,)
    assert model.embedding_model == "dinov3-large"
    assert np.all(np.isfinite(model.weights)) and np.isfinite(model.bias)


def test_the_shipped_artifact_records_how_well_it_was_validated():
    # The number on the dashboard is only as good as these; an artifact shipped
    # without them cannot be audited later.
    payload = json.loads(model_module.artifact_path(ACTIVE_VERSION).read_text())
    held_out = payload["validation"]["leave_one_experiment_out"]
    assert held_out["n_crops"] > 100
    assert 0 < held_out["within_group_spearman_mean"] <= 1
    assert payload["metric_id"] and payload["trained_at"]


def _model(weights=(1.0, 2.0), bias=0.5, encoder="dinov3-large"):
    return BundlenessModel("vT", encoder, np.array(weights, dtype=float), bias)


def test_score_is_the_affine_map_the_artifact_stores():
    scores = _model().score(np.array([[1.0, 1.0], [0.0, -1.0]]))
    assert scores.tolist() == [3.5, -1.5]


@pytest.mark.parametrize("bad", [np.zeros(2), np.zeros((3, 5)), np.zeros((1, 2, 2))])
def test_score_refuses_embeddings_of_the_wrong_shape(bad):
    # A 768-dim DINOv2 vector must not be broadcast into an answer.
    with pytest.raises(ValueError):
        _model().score(bad)


def test_only_the_encoder_it_was_trained_on_is_accepted():
    model = _model()
    assert model.accepts("dinov3-large")
    assert not model.accepts("dinov2-large")
    # NULL encoder = an embedding of unknown origin (old import); never guess.
    assert not model.accepts(None)


@pytest.mark.parametrize("weights,bias", [([1.0, float("nan")], 0.0), ([1.0, 2.0], float("inf"))])
def test_a_non_finite_artifact_is_refused(tmp_path, weights, bias):
    # NaN weights would score every crop NaN, which Postgres stores happily.
    path = tmp_path / "model_vX.json"
    path.write_text(json.dumps(
        {"version": "vX", "embedding_model": "e", "weights": weights, "bias": bias}
    ))
    with pytest.raises(ValueError):
        read_artifact(path)


def test_an_artifact_declaring_another_version_is_refused(tmp_path, monkeypatch):
    # The declared version is what gets stamped on every crop row. A file copied
    # to a new name without retraining would stamp rows with the old one, and
    # the backfill would then rescore the whole corpus on every start.
    path = tmp_path / "model.json"
    path.write_text(json.dumps(
        {"version": "v0", "embedding_model": "e", "weights": [1.0], "bias": 0.0}
    ))
    monkeypatch.setattr(model_module, "artifact_path", lambda version: path)
    load_model.cache_clear()
    try:
        with pytest.raises(ValueError, match="declares version"):
            load_model()
    finally:
        load_model.cache_clear()


# =============================================================================
# Training
# =============================================================================

def _planted(n_crops=120, dim=40, n_pairs=1500, seed=0, noise=0.3):
    """Embeddings in which one latent factor decides who wins a comparison.

    Low-rank on purpose, like a real embedding: five factors mixed into ``dim``
    correlated features. With independent features, standardising makes every
    direction equally large and PCA keeps an arbitrary subset of them.
    """
    rng = np.random.default_rng(seed)
    factors = rng.normal(size=(n_crops, 5))
    embeddings = factors @ rng.normal(size=(5, dim)) + 0.05 * rng.normal(size=(n_crops, dim)) + 5.0
    truth = factors[:, 0]
    a = rng.integers(0, n_crops, n_pairs)
    b = rng.integers(0, n_crops, n_pairs)
    keep = a != b
    a, b = a[keep], b[keep]
    won = (truth[a] - truth[b] + rng.normal(scale=noise, size=len(a)) > 0).astype(int)
    return embeddings, np.column_stack([a, b, won]), truth


def test_training_recovers_the_direction_the_comparisons_were_made_on():
    embeddings, pairs, truth = _planted()
    scores = fit_scorer(embeddings, pairs, n_components=10)(embeddings)
    assert spearmanr(scores, truth)[0] > 0.9


def test_scores_are_standardised_against_the_compared_crops():
    # This is what gives the unit its meaning: 0 = average annotated crop,
    # 1 = one SD of them. Standardising over anything else changes the scale.
    embeddings, pairs, _ = _planted()
    pairs = pairs[(pairs[:, 0] < 60) & (pairs[:, 1] < 60)]  # crops 60+ never compared
    scores = fit_scorer(embeddings, pairs, n_components=10)(embeddings)
    reference = scores[compared_rows(pairs)]
    assert abs(reference.mean()) < 1e-9
    assert abs(reference.std() - 1.0) < 1e-9
    assert abs(scores.std() - 1.0) > 1e-6  # ...and NOT against the whole corpus


def test_swapping_every_winner_reverses_the_ranking():
    # Guards the sign convention end to end: "a won" must mean a scores HIGHER.
    embeddings, pairs, _ = _planted()
    flipped = pairs.copy()
    flipped[:, 2] = 1 - flipped[:, 2]
    forward = fit_scorer(embeddings, pairs, n_components=10)(embeddings)
    backward = fit_scorer(embeddings, flipped, n_components=10)(embeddings)
    assert spearmanr(forward, backward)[0] < -0.99


def test_the_order_a_pair_was_shown_in_does_not_matter():
    # No intercept + both orders fed in: which crop was on the left is noise.
    embeddings, pairs, _ = _planted()
    mirrored = np.column_stack([pairs[:, 1], pairs[:, 0], 1 - pairs[:, 2]])
    one = fit_scorer(embeddings, pairs, n_components=10)
    other = fit_scorer(embeddings, mirrored, n_components=10)
    assert np.allclose(one.weights, other.weights, atol=1e-6)
    assert abs(one.bias - other.bias) < 1e-6


def test_latent_strengths_rank_a_crop_that_always_wins_on_top():
    pairs = np.array([[0, 1, 1], [0, 2, 1], [1, 2, 1], [0, 1, 1]])
    strengths = latent_strengths(pairs, 3)
    assert strengths[0] > strengths[1] > strengths[2]
    assert np.all(np.isfinite(strengths))  # ridge keeps the unbeaten crop finite


def test_a_fold_never_trains_on_a_pair_touching_its_held_out_group():
    # The leak this guards: a pair with ONE crop in the test fold still teaches
    # the model about that crop. Splitting comparisons instead of crops is how a
    # ranking model gets a held-out score it did not earn.
    embeddings, pairs, _ = _planted()
    groups = np.arange(len(embeddings)) % 4
    seen = []

    def spy(all_embeddings, training_pairs):
        seen.append(training_pairs)
        return fit_scorer(all_embeddings, training_pairs, n_components=10)

    held_out_scores(embeddings, pairs, groups, fit=spy)

    assert len(seen) == 4
    for group, training_pairs in zip(np.unique(groups), seen):
        assert len(training_pairs) > 0
        assert not np.any(groups[training_pairs[:, 0]] == group)
        assert not np.any(groups[training_pairs[:, 1]] == group)


def test_crops_in_no_comparison_get_no_held_out_score():
    embeddings, pairs, _ = _planted()
    pairs = pairs[(pairs[:, 0] < 60) & (pairs[:, 1] < 60)]
    predicted = held_out_scores(
        embeddings, pairs, np.arange(len(embeddings)) % 3,
        fit=lambda e, p: fit_scorer(e, p, n_components=10),
    )
    assert np.all(np.isfinite(predicted[:60]))
    assert np.all(np.isnan(predicted[60:]))


def test_validation_headline_cannot_be_earned_by_recognising_the_group():
    # Three groups with very different mean bundleness, and NO usable signal
    # inside a group: the embedding encodes only which group a crop is in.
    # Pooled rank correlation looks excellent; within-group must not.
    rng = np.random.default_rng(1)
    n, dim = 150, 30
    groups = np.arange(n) % 3
    embeddings = rng.normal(size=(n, dim)) * 0.01
    embeddings[:, 0] += groups * 5.0  # the only structure: group identity
    truth = groups * 3.0 + rng.normal(size=n)  # within-group part is invisible
    a, b = rng.integers(0, n, 4000), rng.integers(0, n, 4000)
    keep = a != b
    a, b = a[keep], b[keep]
    pairs = np.column_stack([a, b, (truth[a] > truth[b]).astype(int)])

    # Random folds, so each fold still sees all three groups in training.
    report = validate(embeddings, pairs, rng.integers(0, 5, n))
    assert report["pooled_spearman"] > 0.6

    by_group = validate(embeddings, pairs, groups)
    assert by_group["n_groups"] == 3
    assert abs(by_group["within_group_spearman_mean"]) < 0.25
