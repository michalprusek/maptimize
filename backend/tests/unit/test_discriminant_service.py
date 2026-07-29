"""The supervised projection, and specifically whether it can be trusted.

A supervised projection always looks separated, so the tests that matter are not
about plumbing: they check that a planted signal is found, that random labels
score inside the permutation null, and that the cross-validation cannot see an
experiment it is being tested on.
"""
import numpy as np
import pytest

from services import discriminant_service as mod
from services.discriminant_service import (
    NotEnoughDataError,
    centre_per_microscope,
    compute_discriminant,
    fit_projection,
    permutation_null,
    score_projection,
    scoreable_mask,
)


def synthetic(
    n_experiments=15, per_experiment=12, dims=40, separation=0.0,
    experiment_offset=4.0, seed=0,
):
    """Crops grouped in experiments, each experiment carrying one protein.

    `separation` is how far apart the protein means sit. At 0 the labels carry no
    information at all, which is the case the null has to reproduce.

    `experiment_offset` is what makes this data realistic and is deliberately
    LARGE: crops from one image are near-duplicates, so experiment identity is
    trivially learnable. Because each experiment carries a single protein, a
    split that lets one experiment appear on both sides can memorise the offset
    and recover the label from it — the exact leak that reported 0.68 on the
    production corpus where the honest answer was 0.26. With a small offset the
    tests pass whether or not the split is grouped, which makes them useless for
    the guard that matters most.
    """
    rng = np.random.default_rng(seed)
    proteins = np.array([1, 2, 3])
    X, y, groups, scopes = [], [], [], []
    for exp in range(n_experiments):
        protein = proteins[exp % len(proteins)]
        centre = np.zeros(dims)
        centre[protein] = separation
        centre = centre + rng.normal(0, experiment_offset, dims)
        X.append(rng.normal(0, 1, (per_experiment, dims)) + centre)
        y += [protein] * per_experiment
        groups += [exp] * per_experiment
        scopes += [exp % 2] * per_experiment
    return np.vstack(X), np.array(y), np.array(groups), np.array(scopes)


# =============================================================================
# centre_per_microscope
# =============================================================================

def test_centering_removes_each_instruments_mean():
    rng = np.random.default_rng(0)
    X = np.vstack([rng.normal(5, 1, (20, 8)), rng.normal(-5, 1, (20, 8))])
    scopes = [1] * 20 + [2] * 20

    out = centre_per_microscope(X, scopes)

    assert np.allclose(out[:20].mean(axis=0), 0, atol=1e-9)
    assert np.allclose(out[20:].mean(axis=0), 0, atol=1e-9)


def test_centering_keeps_within_instrument_structure():
    # It must remove the offset between instruments, not the spread inside one.
    rng = np.random.default_rng(1)
    X = np.vstack([rng.normal(5, 1, (30, 6)), rng.normal(-5, 1, (30, 6))])
    scopes = [1] * 30 + [2] * 30

    out = centre_per_microscope(X, scopes)

    assert np.allclose(np.std(out[:30], axis=0), np.std(X[:30], axis=0), atol=1e-9)


def test_centering_treats_unassigned_as_its_own_group():
    # A missing microscope is a real state in this data; it must not crash and
    # must not be silently merged with some other instrument.
    X = np.vstack([np.ones((10, 4)) * 3, np.ones((10, 4)) * -3])
    out = centre_per_microscope(X, [None] * 10 + [1] * 10)
    assert np.allclose(out, 0)


def test_centering_does_not_mutate_the_caller():
    X = np.ones((10, 4))
    centre_per_microscope(X, [1] * 10)
    assert np.allclose(X, 1)


# =============================================================================
# The honesty of the score — the tests this file exists for
# =============================================================================

def test_a_planted_signal_is_found():
    # Separation must exceed experiment_offset: a protein difference smaller than
    # the spread between experiments is not recoverable ACROSS experiments, which
    # is precisely why the real corpus scores 0.26 and not 0.9.
    X, y, groups, _ = synthetic(separation=8.0, experiment_offset=1.0)
    assert score_projection(X, y, groups)[0] > 0.8


def test_labels_that_mean_nothing_score_at_chance():
    # The load-bearing assertion. If this ever passes with a high score, the
    # pipeline is reading something other than the labels.
    X, y, groups, _ = synthetic(separation=0.0)
    assert score_projection(X, y, groups)[0] < 0.55  # chance is 1/3


def test_a_meaningless_score_sits_inside_its_own_null():
    X, y, groups, _ = synthetic(separation=0.0)
    score = score_projection(X, y, groups)[0]
    null = permutation_null(X, y, groups, n_permutations=5)
    assert null
    assert score <= max(null) + 0.15


def test_a_real_signal_sits_outside_its_null():
    X, y, groups, _ = synthetic(separation=8.0, experiment_offset=1.0)
    score = score_projection(X, y, groups)[0]
    null = permutation_null(X, y, groups, n_permutations=5)
    assert score > max(null) + 0.2


def test_an_experiment_level_confound_does_not_read_as_protein_signal():
    """The failure mode grouping exists to prevent, and the guard that matters most.

    Every experiment sits at its own large offset and each carries one protein,
    so experiment identity alone predicts the label perfectly. Only a split that
    keeps an experiment wholly on one side can tell that the labels themselves
    carry nothing.
    """
    X, y, groups, _ = synthetic(separation=0.0, dims=60, seed=7)
    assert score_projection(X, y, groups)[0] < 0.6


def test_ignoring_the_grouping_would_report_a_signal_that_is_not_there():
    """States the leak explicitly, so the guard above cannot quietly stop biting.

    Same data, same pipeline, split without groups: the score jumps from chance
    to near-perfect purely by memorising which experiment a crop came from.
    """
    from sklearn.metrics import balanced_accuracy_score
    from sklearn.model_selection import StratifiedKFold

    X, y, groups, _ = synthetic(separation=0.0, dims=60, seed=7)

    splitter = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    predicted = np.empty(len(y), dtype=y.dtype)
    for train, test in splitter.split(X, y):
        pipe = mod._pipeline(
            mod._components_for(len(train), X.shape[1]), len(np.unique(y[train]))
        )
        pipe.fit(X[train], y[train])
        predicted[test] = pipe.predict(X[test])
    leaky = balanced_accuracy_score(y, predicted)

    honest = score_projection(X, y, groups)[0]
    assert leaky > 0.9, "the fixture no longer reproduces the leak it is here to model"
    assert honest < 0.6
    assert leaky - honest > 0.3


# =============================================================================
# permutation_null
# =============================================================================

def test_the_null_shuffles_labels_between_experiments_not_within_them():
    """Per-crop shuffling would make any score look significant.

    Every crop of an experiment shares its protein. Scattering labels across
    experiments destroys the grouping the split relies on and drives the null
    below chance, so the comparison flatters whatever the real score is.
    """
    X, y, groups, _ = synthetic(separation=8.0, experiment_offset=1.0)
    seen = []

    real_score = mod.score_projection

    def capture(embeddings, labels, grouping):
        # Each experiment must still carry exactly one label after shuffling.
        per_experiment = {g: set(labels[grouping == g]) for g in np.unique(grouping)}
        seen.append(all(len(v) == 1 for v in per_experiment.values()))
        return real_score(embeddings, labels, grouping)

    mod.score_projection = capture
    try:
        permutation_null(X, y, groups, n_permutations=3)
    finally:
        mod.score_projection = real_score

    assert seen and all(seen)


def _null_labels(X, y, groups, n=3):
    """Labels the null actually hands the scorer, without running the scorer."""
    seen = []
    real_score = mod.score_projection
    mod.score_projection = lambda e, labels, g: (seen.append(labels.copy()), (0.5, 3, ()))[1]
    try:
        permutation_null(X, y, groups, n_permutations=n)
    finally:
        mod.score_projection = real_score
    return seen


def test_the_null_permutes_the_experiment_to_protein_map():
    # The invariant the null actually has: the multiset of labels OVER
    # EXPERIMENTS is preserved, because it permutes that map. Shuffling must not
    # resample — a null drawn from a different class balance is not comparable to
    # the real score.
    X, y, groups, _ = synthetic(separation=1.0)
    by_experiment = sorted(y[groups == g][0] for g in np.unique(groups))

    seen = _null_labels(X, y, groups)

    assert seen
    for labels in seen:
        assert sorted(labels[groups == g][0] for g in np.unique(groups)) == by_experiment


def test_the_null_does_not_preserve_the_per_crop_class_balance():
    # ⚠️ Documented, not lamented. Moving a label from a 5-crop experiment to a
    # 50-crop one changes how many CROPS carry it, so the null's balanced
    # accuracy is computed over a different class balance than the real score.
    #
    # This test exists because its predecessor asserted the opposite and passed:
    # `synthetic()` gives every experiment the same size, which makes crop counts
    # invariant for free. On the production corpus experiments range from 5 to 87
    # crops, so the property was never true where it mattered — a green test
    # pinning a guarantee the code does not make. Unequal sizes here on purpose.
    X, y, groups, _ = synthetic(separation=1.0)
    keep = np.ones(len(y), dtype=bool)
    keep[np.flatnonzero(groups == np.unique(groups)[0])[:-3]] = False  # starve one
    X, y, groups = X[keep], y[keep], groups[keep]

    seen = _null_labels(X, y, groups, n=8)
    crop_counts = {tuple(np.bincount(labels, minlength=4)[1:]) for labels in seen}

    assert len(crop_counts) > 1, "unequal experiments must move the crop-level balance"


# =============================================================================
# fit_projection
# =============================================================================

def test_projection_is_two_dimensional_even_with_two_classes():
    # Two classes yield a single discriminant; callers still plot in 2-D.
    rng = np.random.default_rng(0)
    X = rng.normal(0, 1, (60, 10))
    y = np.array([1] * 30 + [2] * 30)
    assert fit_projection(X, y).shape == (60, 2)


def test_projection_separates_a_planted_signal_visibly():
    X, y, groups, _ = synthetic(separation=8.0, experiment_offset=1.0)
    coords = fit_projection(X, y)
    spread_between = np.linalg.norm(
        coords[y == 1].mean(axis=0) - coords[y == 2].mean(axis=0)
    )
    spread_within = np.std(coords[y == 1], axis=0).mean()
    assert spread_between > spread_within


# =============================================================================
# Guards
# =============================================================================

@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (dict(n_experiments=2, per_experiment=5), "labelled crops"),
        (dict(n_experiments=15, per_experiment=12), None),
    ],
)
def test_too_few_points_is_refused(kwargs, expected):
    X, y, groups, scopes = synthetic(**kwargs)
    if expected is None:
        return
    with pytest.raises(NotEnoughDataError, match=expected):
        compute_discriminant(X, y, groups, scopes, n_permutations=1)


def test_a_single_protein_cannot_be_separated():
    X, _, groups, scopes = synthetic()
    y = np.ones(len(groups), dtype=int)
    with pytest.raises(NotEnoughDataError, match="proteins to separate"):
        compute_discriminant(X, y, groups, scopes, n_permutations=1)


def test_too_few_experiments_to_cross_validate_is_refused():
    # Four experiments cannot support a five-fold grouped split, and reporting a
    # score from a degenerate split would be worse than refusing.
    X, y, groups, scopes = synthetic(n_experiments=4, per_experiment=20)
    with pytest.raises(NotEnoughDataError, match="experiments to cross-validate"):
        compute_discriminant(X, y, groups, scopes, n_permutations=1)


# =============================================================================
# compute_discriminant end to end
# =============================================================================

def test_the_result_carries_everything_needed_to_judge_it():
    X, y, groups, scopes = synthetic(separation=8.0, experiment_offset=1.0)
    out = compute_discriminant(X, y, groups, scopes, n_permutations=3)

    assert out.coords.shape == (len(y), 2)
    assert out.n_proteins == 3
    assert out.n_experiments == 15
    assert out.chance == pytest.approx(1 / 3)
    assert out.n_permutations == 3
    # The score and the null it must be read against travel together; a caller
    # cannot render one without the other.
    assert out.balanced_accuracy > out.null_max


def test_the_microscope_offset_is_removed_before_fitting():
    """Two proteins acquired on one instrument each is the confound in this data.

    Without centering the projection separates them perfectly by instrument. The
    honest answer is that these labels carry nothing.
    """
    rng = np.random.default_rng(3)
    n_exp, per = 15, 12
    X, y, groups, scopes = [], [], [], []
    for exp in range(n_exp):
        protein = 1 if exp % 2 == 0 else 2
        scope = protein  # protein and instrument perfectly confounded
        offset = np.zeros(30)
        offset[0] = 20.0 if scope == 1 else -20.0
        X.append(rng.normal(0, 1, (per, 30)) + offset)
        y += [protein] * per
        groups += [exp] * per
        scopes += [scope] * per
    X, y, groups, scopes = np.vstack(X), np.array(y), np.array(groups), np.array(scopes)

    out = compute_discriminant(X, y, groups, scopes, n_permutations=3)

    assert out.balanced_accuracy < 0.75, (
        "the instrument offset was read as protein signal — centering did not run"
    )


# =============================================================================
# Classes that grouped CV cannot score at all
# =============================================================================

def _one_experiment_each(n_proteins=6, per_experiment=40, dims=30):
    """Every protein confined to a single experiment — the degenerate corpus.

    This is not hypothetical: a user scoped to their own six experiments, each
    carrying a different protein, hit exactly this in production.
    """
    rng = np.random.default_rng(3)
    X, y, groups = [], [], []
    for exp in range(n_proteins):
        centre = np.zeros(dims)
        centre[exp] = 6.0
        X.append(rng.normal(0, 1, (per_experiment, dims)) + centre)
        y += [exp + 1] * per_experiment
        groups += [exp] * per_experiment
    return np.vstack(X), np.array(y), np.array(groups)


def test_a_protein_in_one_experiment_can_never_be_scored():
    X, y, groups = _one_experiment_each()
    assert not scoreable_mask(y, groups).any()


def test_a_corpus_of_single_experiment_proteins_refuses_instead_of_scoring_zero():
    """The failure this guard exists for, and it is a SILENT one.

    Holding an experiment out removes every crop of its protein from training, so
    the classifier cannot predict that class and its recall is 0 by arithmetic.
    Without the guard the whole corpus scores exactly 0.000 against a chance of
    0.167 and the UI reports "no separation" — a measurement-shaped statement
    about data that was never measurable.
    """
    X, y, groups = _one_experiment_each()

    with pytest.raises(NotEnoughDataError, match="single experiment"):
        compute_discriminant(X, y, groups, [1] * len(y), n_permutations=0)


def test_proteins_that_cannot_be_scored_are_named_not_silently_dropped():
    # Mixed corpus: proteins 1 and 2 span two experiments each, protein 3 sits in
    # one. The score must come from the first two, and the third must be reported
    # rather than quietly folded into a lower number.
    rng = np.random.default_rng(4)
    X, y, groups = [], [], []
    for exp, protein in enumerate([1, 1, 2, 2, 1, 2, 3]):
        centre = np.zeros(30)
        centre[protein] = 6.0
        X.append(rng.normal(0, 1, (40, 30)) + centre)
        y += [protein] * 40
        groups += [exp] * 40
    X, y, groups = np.vstack(X), np.array(y), np.array(groups)

    result = compute_discriminant(X, y, groups, [1] * len(y), n_permutations=0)

    assert result.unscoreable_proteins == ("3",)
    assert result.n_proteins == 2, "the excluded protein must leave the denominator too"
    assert {name for name, _, _ in result.per_class} == {"1", "2"}
    # Its points are still plotted — the projection is fitted on everything.
    assert len(result.coords) == len(y)
