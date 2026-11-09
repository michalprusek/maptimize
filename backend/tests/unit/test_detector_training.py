"""Detector retraining: what counts as ground truth, and how models are judged.

Every function here decides something a wrong answer would hide: a provenance
bug trains the new model on the old model's output, a split bug leaks a slide
into the test set, a matching bug double-counts a cell.
"""

import math

import numpy as np
import pytest

from ml.detection.detector import model_input
from ml.detection.training import (
    HUMAN,
    NEGATIVE,
    REVIEWED,
    UNREVIEWED,
    Evaluation,
    FovResult,
    average_precision,
    crop_box,
    match_flags,
    paired_bootstrap,
    provenance,
    split_experiments,
    yolo_label,
)

A = (0.0, 0.0, 100.0, 100.0)
B = (200.0, 200.0, 300.0, 300.0)


# ── provenance ──────────────────────────────────────────────────────────────


def test_a_hand_drawn_box_makes_the_whole_fov_human():
    assert provenance(True, [(A, 0.8), (B, None)], [A, B]) == HUMAN


def test_detection_off_means_every_box_was_drawn_by_hand():
    assert provenance(False, [(A, 0.8)], []) == HUMAN


def test_boxes_identical_to_the_detector_are_unreviewed_not_ground_truth():
    """The core guard: stored detections that match today's prediction are the
    old model's own output. Calling them ground truth would train the new model
    to imitate the old one and test the old one against itself."""
    assert provenance(True, [(A, 0.8), (B, 0.7)], [B, A]) == UNREVIEWED


def test_a_deleted_detection_marks_the_fov_reviewed():
    assert provenance(True, [(A, 0.8)], [A, B]) == REVIEWED


def test_a_moved_detection_marks_the_fov_reviewed():
    moved = (0.0, 0.0, 100.0, 160.0)  # IoU 0.625 with A
    assert provenance(True, [(moved, 0.8)], [A]) == REVIEWED


def test_int_truncation_of_stored_boxes_is_not_an_edit():
    stored = (0.0, 0.0, 99.0, 99.0)  # what int() of a 99.7 px box stores
    assert provenance(True, [(stored, 0.8)], [A]) == UNREVIEWED


def test_every_detection_deleted_is_a_confirmed_background():
    assert provenance(True, [], [A]) == NEGATIVE


def test_an_empty_fov_the_detector_also_leaves_empty_teaches_nothing():
    assert provenance(True, [], []) is None


def test_an_empty_fov_without_detection_is_not_a_background():
    """Nobody ran the detector, so an empty FOV may simply be unannotated."""
    assert provenance(False, [], [A]) is None


# ── boxes ───────────────────────────────────────────────────────────────────


def test_axis_aligned_crop_is_its_own_box():
    assert crop_box(10, 20, 30, 40, None, 1000, 1000) == (10, 20, 40, 60)
    assert crop_box(10, 20, 30, 40, 0.0, 1000, 1000) == (10, 20, 40, 60)


def test_rotated_crop_becomes_the_envelope_of_its_rotated_rectangle():
    # 100x20 box centred at (100, 100), rotated 90°: becomes 20 wide, 100 tall.
    x1, y1, x2, y2 = crop_box(50, 90, 100, 20, 90.0, 1000, 1000)
    assert (x1, y1, x2, y2) == pytest.approx((90, 50, 110, 150))


def test_rotated_45_envelope_is_wider_than_the_box():
    x1, y1, x2, y2 = crop_box(200, 200, 100, 100, 45.0, 1000, 1000)
    assert x2 - x1 == pytest.approx(100 * math.sqrt(2))


def test_envelope_is_clipped_to_the_image():
    assert crop_box(-10, -10, 50, 50, None, 30, 30) == (0, 0, 30, 30)


def test_a_box_outside_the_image_is_dropped():
    assert crop_box(500, 500, 50, 50, None, 100, 100) is None


def test_yolo_label_is_normalised_centre_and_size():
    assert yolo_label((100, 50, 300, 150), 400, 200) == "0 0.500000 0.500000 0.500000 0.500000"


# ── preprocessing shared with inference ─────────────────────────────────────


def test_model_input_replicates_grey_into_three_identical_channels():
    grey = np.arange(12, dtype=np.uint8).reshape(3, 4)
    out = model_input(grey)
    assert out.shape == (3, 4, 3) and out.dtype == np.uint8
    assert all((out[..., c] == grey).all() for c in range(3))


def test_production_detect_feeds_the_network_model_input():
    """The wiring, not the helper: training images are written from
    ``model_input``, so inference has to hand the network that same array."""
    from ml.detection.detector import CellDetector

    seen = []
    detector = CellDetector()
    detector._model = lambda image, **kwargs: seen.append(image) or []
    grey = np.arange(12, dtype=np.uint8).reshape(3, 4)
    assert detector.detect(grey) == []
    assert np.array_equal(seen[0], model_input(grey))


def test_upload_detection_runs_at_the_threshold_chosen_on_validation():
    """The threshold is chosen per model on validation data; the detector the
    upload pipeline builds (``_get_detector_raw``) must hand exactly that to
    the network, not a constructor default somebody else picked."""
    from ml.detection import detector as det

    calls = []
    det.reset_detector()
    try:
        production = det._get_detector_raw()
        production._model = lambda image, **kwargs: calls.append(kwargs) or []
        production.detect(np.zeros((4, 4), dtype=np.uint8))
    finally:
        det.reset_detector()
    assert calls[0]["conf"] == det.CONF_THRESHOLD


def test_model_input_scales_16_bit_down_to_8():
    out = model_input(np.full((2, 2), 65535, dtype=np.uint16))
    assert out.dtype == np.uint8 and int(out.max()) == 255


# ── split ───────────────────────────────────────────────────────────────────


def _owners(n_theo=40, n_ana=20):
    owners = {i: "theo" for i in range(n_theo)}
    owners.update({100 + i: "ana" for i in range(n_ana)})
    return owners


def test_split_assigns_every_experiment_exactly_once():
    owners = _owners()
    split = split_experiments(owners, seed=0)
    assert set(split) == set(owners)
    assert set(split.values()) == {"train", "val", "test"}


def test_split_is_stratified_by_owner():
    split = split_experiments(_owners(), seed=0)
    for owner_range in (range(40), range(100, 120)):
        assigned = {split[e] for e in owner_range}
        assert assigned == {"train", "val", "test"}


def test_split_fractions_hold_for_every_owner_separately():
    """Pooling owners would still give ~15 % test overall, but not per owner:
    a small annotator could land entirely in train and never be measured."""
    owners = _owners()
    owners.update({200 + i: "xin" for i in range(7)})
    split = split_experiments(owners, seed=0)
    for exps, (n_test, n_val, n_train) in (
        (range(40), (6, 6, 28)), (range(100, 120), (3, 3, 14)), (range(200, 207), (1, 1, 5)),
    ):
        got = [split[e] for e in exps]
        assert (got.count("test"), got.count("val"), got.count("train")) == (n_test, n_val, n_train)


def test_split_is_deterministic_and_independent_of_dict_order():
    owners = _owners()
    reversed_owners = dict(reversed(list(owners.items())))
    assert split_experiments(owners, 3) == split_experiments(reversed_owners, 3)
    assert split_experiments(owners, 3) != split_experiments(owners, 4)


# ── matching and AP ─────────────────────────────────────────────────────────


def test_two_predictions_on_one_cell_count_once():
    flags = match_flags([A, A], [0.9, 0.8], [A], 0.5)
    assert flags.tolist() == [True, False]


def test_matching_is_by_score_not_by_list_order():
    """Flags are paired with scores sorted high-to-low, so they must come back in
    that order. A confident hit listed after a weak miss is still a hit at 0.9:
    AP 1.0. Matching in list order would pin the hit on the 0.3 box: AP 0.5."""
    far = (500.0, 500.0, 600.0, 600.0)
    assert match_flags([far, A], [0.3, 0.9], [A], 0.5).tolist() == [True, False]
    assert Evaluation([_fov(1, 1, [far, A], [0.3, 0.9], [A])]).ap([0]) == pytest.approx(1.0)


def test_a_prediction_takes_the_ground_truth_it_overlaps_most():
    pred = (0.0, 0.0, 100.0, 100.0)
    worse = (0.0, 0.0, 100.0, 180.0)  # IoU 0.56 with pred
    flags = match_flags([pred, worse], [0.9, 0.8], [worse, pred], 0.5)
    assert flags.tolist() == [True, True]


def test_below_the_iou_threshold_is_a_miss():
    assert match_flags([(0.0, 0.0, 100.0, 100.0)], [0.9], [(50.0, 0.0, 150.0, 100.0)], 0.5).tolist() == [False]


def test_perfect_ranking_scores_ap_one():
    assert average_precision(np.array([0.9, 0.8]), np.array([True, True]), 2) == pytest.approx(1.0)


def test_ap_counts_missed_ground_truth():
    assert average_precision(np.array([0.9]), np.array([True]), 2) == pytest.approx(0.5)


def test_ap_punishes_a_confident_false_positive_ranked_first():
    # FP at 0.9, TP at 0.8 -> precision at recall 1 is 0.5.
    assert average_precision(np.array([0.9, 0.8]), np.array([False, True]), 1) == pytest.approx(0.5)


def test_ap_uses_the_interpolated_precision_envelope():
    """FP, TP, TP over 2 cells: the first recall step lands at precision 0.5, but
    the interpolated (max-to-the-right) precision there is 2/3. Without the
    envelope AP is 0.583 instead of 0.667."""
    ap = average_precision(np.array([0.9, 0.8, 0.7]), np.array([False, True, True]), 2)
    assert ap == pytest.approx(2 / 3)


def test_ap_with_no_ground_truth_is_undefined_not_zero():
    assert math.isnan(average_precision(np.array([0.9]), np.array([False]), 0))


def _fov(image_id, exp, preds, scores, gts):
    return FovResult(image_id, exp, list(preds), list(scores), list(gts))


def test_operating_point_only_keeps_scores_at_or_above_conf():
    ev = Evaluation([_fov(1, 1, [A, B], [0.9, 0.3], [A, B])])
    assert ev.operating_point([0], 0.5) == {
        "precision": 1.0, "recall": 0.5, "f1": pytest.approx(2 / 3), "tp": 1, "fp": 0, "fn": 1,
    }
    assert ev.operating_point([0], 0.3)["recall"] == 1.0


def test_best_conf_picks_the_threshold_with_highest_f1():
    far = (500.0, 500.0, 600.0, 600.0)
    ev = Evaluation([_fov(1, 1, [A, far], [0.9, 0.4], [A])])  # the 0.4 box is a FP
    assert ev.best_conf([0], [0.3, 0.5, 0.95]) == 0.5


def test_bootstrap_detects_a_real_improvement():
    far = (500.0, 500.0, 600.0, 600.0)
    old = Evaluation([_fov(i, i, [far], [0.9], [A]) for i in range(20)])
    new = Evaluation([_fov(i, i, [A], [0.9], [A]) for i in range(20)])
    result = paired_bootstrap(old, new, lambda ev, idx: ev.ap(idx), n_resamples=200)
    assert result["diff"] == pytest.approx(1.0)
    assert result["ci95"][0] > 0 and result["p_not_better"] == 0.0


def test_bootstrap_resamples_experiments_not_fovs():
    """New model wins every FOV of slide A and loses every FOV of slide B. With
    two experiments there are only three outcomes (AA +1, AB 0, BB -1), so the
    CI is the full [-1, 1]. Resampling the 20 FOVs as if independent would
    report a tight interval around 0 — false confidence from correlated FOVs."""
    far = (500.0, 500.0, 600.0, 600.0)
    old = [_fov(i, 0, [far], [0.9], [A]) for i in range(10)] + [_fov(10 + i, 1, [A], [0.9], [A]) for i in range(10)]
    new = [_fov(i, 0, [A], [0.9], [A]) for i in range(10)] + [_fov(10 + i, 1, [far], [0.9], [A]) for i in range(10)]
    result = paired_bootstrap(Evaluation(old), Evaluation(new),
                              lambda ev, idx: ev.operating_point(idx, 0.5)["recall"], n_resamples=500)
    assert result["diff"] == pytest.approx(0.0)
    assert result["ci95"] == pytest.approx([-1.0, 1.0])


def test_bootstrap_refuses_unpaired_evaluations():
    with pytest.raises(ValueError):
        paired_bootstrap(Evaluation([_fov(1, 1, [], [], [A])]), Evaluation([_fov(2, 1, [], [], [A])]),
                         lambda ev, idx: ev.ap(idx))


# ── training recipe ─────────────────────────────────────────────────────────


def _training_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "train_detector.py"
    spec = importlib.util.spec_from_file_location("train_detector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_training_never_rotates_or_shears_box_labels():
    """ultralytics turns a warped box into the envelope of its warped corners,
    so rotation/shear train the detector on boxes larger than the cell — the
    January model's loose boxes. Flips are lossless and stay."""
    augment = _training_script().AUGMENT
    assert augment["degrees"] == 0 and augment["shear"] == 0
    assert augment.get("perspective", 0) == 0
    assert augment["flipud"] > 0 and augment["fliplr"] > 0


async def test_cached_predictions_are_never_reused_for_different_weights(tmp_path, monkeypatch):
    """After a deploy ``weights/best.pt`` is a different model under the same
    name. Predictions cached for the old file must not answer for the new one:
    they would decide the production threshold without any sign of it."""
    script = _training_script()
    old_w, new_w = tmp_path / "old.pt", tmp_path / "new.pt"
    old_w.write_bytes(b"january model")
    new_w.write_bytes(b"november model")
    (tmp_path / "manifest.json").write_text(
        '{"fovs": [{"image_id": 7, "experiment_id": 1, "split": "test", '
        '"provenance": "human", "gt": [[0, 0, 10, 10]]}]}'
    )
    cache = script.prediction_cache_path(tmp_path, "best", "test", str(old_w))
    cache.parent.mkdir()
    cache.write_text('{"7": [[0, 0, 10, 10, 0.9]]}')

    hit = await script.predict(tmp_path, "test", "best", str(old_w))
    assert hit[0].pred_scores == [0.9]

    class Recomputed(Exception):
        pass

    def must_not_reuse(*args, **kwargs):
        raise Recomputed

    monkeypatch.setattr(script, "CellDetector", must_not_reuse)
    with pytest.raises(Recomputed):
        await script.predict(tmp_path, "test", "best", str(new_w))


def test_weights_fingerprint_follows_content_not_path(tmp_path):
    from ml.detection.training import weights_fingerprint

    a, b, c = tmp_path / "a.pt", tmp_path / "b.pt", tmp_path / "c.pt"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"x" * 10)
    c.write_bytes(b"y" * 10)
    assert weights_fingerprint(a) == weights_fingerprint(b) != weights_fingerprint(c)
