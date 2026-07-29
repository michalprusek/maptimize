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
    # Assert the message is ACTIONABLE rather than pinning its wording: it must name
    # the offending corner, where it landed, and the image it left. The old text said
    # only "Rotated bbox exceeds image bounds", which told the user neither which
    # edge nor by how much.
    assert not ok
    assert any(c in err for c in ("top-left", "top-right", "bottom-left", "bottom-right"))
    assert "100x100" in err
    assert "-8.3" in err or "-8." in err


def test_validate_rotated_within_bounds():
    # centred with room to spin
    assert validate_bbox_within_image(30, 30, 40, 40, 100, 100, 45.0) == (True, None)


def test_validate_rotated_min_size():
    ok, err = validate_bbox_within_image(30, 30, 5, 40, 100, 100, 30.0)
    assert not ok and "10 pixels" in err


def test_rotated_corners_at_zero_angle_are_the_axis_corners():
    corners = _rotated_corners(10, 20, 30, 40, 0.0)
    assert (10.0, 20.0) in corners and (40.0, 60.0) in corners


# ----- the de-rotation DIRECTION -------------------------------------------
# ⚠️ The existing edge test uses a mirror-symmetric feature, so it holds for +90
# and -90 alike: negating theta in extract_crop_from_projection leaves it green.
# The sign is the contract with the canvas preview (ctx.rotate(+angleRad)) and with
# _rotated_corners, which are three INDEPENDENT copies of the same rotation.


def test_derotation_maps_the_rotated_top_left_corner_to_output_origin():
    """Pins the sign of the rotation, which a symmetric test cannot.

    ⚠️ Assert on the VALUE at (0, 0), not argmax: with the sign flipped the crop
    comes out all zeros, and argmax of an all-zero array is also (0, 0) -- an
    argmax assertion would pass under the very perturbation it exists to catch.
    """
    for angle in (30.0, 90.0, -45.0):
        p = np.zeros((200, 200), dtype=np.float32)
        # The analytic top-left corner of the rotated box, from the shared helper.
        corners = _rotated_corners(60, 60, 40, 30, angle)
        cx, cy = corners[0]  # (u, v) = (-w/2, -h/2) -> the box's own top-left
        p[int(round(cy)), int(round(cx))] = 255.0

        out = extract_crop_from_projection(p, 60, 60, 40, 30, angle)
        assert out.shape == (30, 40)
        # Bilinear sampling spreads the delta, so the corner pixel is < 255 but
        # must be clearly non-zero. A sign flip samples the far side: all zeros.
        assert out[0, 0] > 0.0, f"angle {angle}: corner did not land at the origin"
        assert out.max() > 0.0


# ----- bounds: the far edge and the shrinking case -------------------------


def test_rotated_corner_past_the_far_edge_is_rejected():
    """Mirror of the existing < 0 test. Without the upper-bound check the crop
    would be silently padded with a black wedge (cval=0.0), diluting
    mean_intensity and the embedding with no error anywhere."""
    # Fits exactly when axis-aligned...
    assert validate_bbox_within_image(60, 60, 40, 40, 100, 100) == (True, None)
    # ...but at 45 degrees its corners reach ~108, past the far edge.
    ok, err = validate_bbox_within_image(60, 60, 40, 40, 100, 100, 45.0)
    assert not ok
    assert "108" in err or "bottom-right" in err or "top-right" in err


def test_rotation_can_also_make_an_overflowing_box_fit():
    """Rotation shrinks the footprint on one axis, so the implication runs both
    ways -- a box that fails axis-aligned can pass rotated. Guards against
    'rotated is always stricter' creeping into the validator."""
    assert validate_bbox_within_image(70, 45, 39, 10, 100, 100)[0] is False
    assert validate_bbox_within_image(70, 45, 39, 10, 100, 100, 90.0) == (True, None)
