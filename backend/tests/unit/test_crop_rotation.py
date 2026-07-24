"""Unit tests for rotated bounding-box crop extraction + validation.

Covers services/crop_editor_service.py: the de-rotated extraction
(extract_crop_from_projection with an angle) and the angle-aware bounds check
(validate_bbox_within_image). Uses real numpy/scipy (not mocked in the unit
harness) so the rotation math is genuinely exercised.
"""
import numpy as np

from services.crop_editor_service import (
    _rotated_corners,
    extract_crop_from_projection,
    validate_bbox_within_image,
)


def _vedge() -> np.ndarray:
    """100×100, left half dark / right half bright (a vertical edge at col 50)."""
    p = np.zeros((100, 100), dtype=np.float32)
    p[:, 50:] = 255.0
    return p


# ----- extraction ----------------------------------------------------------

def test_angle_zero_is_plain_axis_slice():
    p = _vedge()
    out = extract_crop_from_projection(p, 30, 30, 40, 40, 0.0)
    assert out.shape == (40, 40)
    assert np.array_equal(out, p[30:70, 30:70])


def test_angle_defaults_to_zero():
    p = _vedge()
    assert np.array_equal(
        extract_crop_from_projection(p, 30, 30, 40, 40), p[30:70, 30:70]
    )


def test_rotation_keeps_shape_and_reorients_the_edge():
    p = _vedge()
    c0 = extract_crop_from_projection(p, 30, 30, 40, 40, 0.0)
    c90 = extract_crop_from_projection(p, 30, 30, 40, 40, 90.0)
    assert c90.shape == (40, 40)
    # angle 0: vertical edge -> left darker than right
    assert c0[:, :20].mean() < c0[:, 20:].mean()
    # angle 90: the same edge is now horizontal (top/bottom contrast dominates)
    horiz = abs(c90[:20, :].mean() - c90[20:, :].mean())
    vert = abs(c90[:, :20].mean() - c90[:, 20:].mean())
    assert horiz > vert


def test_rotation_changes_pixels_vs_axis_crop():
    p = _vedge()
    c0 = extract_crop_from_projection(p, 30, 30, 40, 40, 0.0)
    c45 = extract_crop_from_projection(p, 30, 30, 40, 40, 45.0)
    assert not np.allclose(c0, c45)


def test_rotation_3d_keeps_channels_separate():
    p = np.zeros((100, 100, 3), dtype=np.float32)
    p[:, 50:, 0] = 255.0  # only the red channel has the edge
    out = extract_crop_from_projection(p, 30, 30, 40, 40, 30.0)
    assert out.shape == (40, 40, 3)
    # green/blue channels stay empty -> rotation didn't mix channels
    assert out[..., 1].max() == 0 and out[..., 2].max() == 0
    assert out[..., 0].max() > 0


# ----- validation ----------------------------------------------------------

def test_validate_axis_aligned_paths_unchanged():
    assert validate_bbox_within_image(0, 0, 40, 40, 100, 100) == (True, None)
    ok, err = validate_bbox_within_image(-1, 0, 40, 40, 100, 100)
    assert not ok and "negative" in err
    ok, err = validate_bbox_within_image(70, 0, 40, 40, 100, 100)
    assert not ok and "width" in err
    ok, err = validate_bbox_within_image(0, 70, 40, 40, 100, 100)
    assert not ok and "height" in err
    ok, err = validate_bbox_within_image(0, 0, 5, 40, 100, 100)
    assert not ok and "10 pixels" in err


def test_validate_rotated_corner_out_of_bounds():
    # flush in the corner: fine axis-aligned, but a 45° spin pushes a corner < 0
    assert validate_bbox_within_image(0, 0, 40, 40, 100, 100) == (True, None)
    ok, err = validate_bbox_within_image(0, 0, 40, 40, 100, 100, 45.0)
    assert not ok and "Rotated bbox exceeds" in err


def test_validate_rotated_within_bounds():
    # centred with room to spin
    assert validate_bbox_within_image(30, 30, 40, 40, 100, 100, 45.0) == (True, None)


def test_validate_rotated_min_size():
    ok, err = validate_bbox_within_image(30, 30, 5, 40, 100, 100, 30.0)
    assert not ok and "10 pixels" in err


def test_rotated_corners_at_zero_angle_are_the_axis_corners():
    corners = _rotated_corners(10, 20, 30, 40, 0.0)
    assert (10.0, 20.0) in corners and (40.0, 60.0) in corners
