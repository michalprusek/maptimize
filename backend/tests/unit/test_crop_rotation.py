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
    # Green/blue stay empty -> rotation didn't mix channels. Threshold rather than
    # exact zero: the cubic spline prefilter leaves ~1e-14 numerical dust in an
    # all-zero channel. Real channel mixing would leak the red channel's 255s, so
    # 1e-6 still catches it with eight orders of magnitude to spare -- and it is far
    # below one uint8 intensity level, which is the smallest difference that can
    # survive into a stored crop.
    assert out[..., 1].max() < 1e-6 and out[..., 2].max() < 1e-6
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


# ----- the interpolation order is a measured choice -------------------------
# An axis-aligned crop is an exact slice, a rotated one is resampled, so whatever the
# resampler does to texture becomes a systematic rotated-vs-unrotated difference that
# the embedding, the UMAP and the discriminant projection can all read. Measured on
# real production crops: bilinear loses ~40% of Laplacian variance and 12% of mean
# gradient at 30-45 deg; cubic loses 17.7% and 3.6%. These pin that choice.


def _affine_with_order(projection, x, y, w, h, angle, order):
    """Reference implementation of the SAME map, with the order as a parameter, so
    the production function can be compared against a bilinear baseline."""
    from scipy import ndimage

    cx, cy = x + w / 2.0, y + h / 2.0
    t = np.deg2rad(angle)
    c, s = np.cos(t), np.sin(t)
    return ndimage.affine_transform(
        projection,
        np.array([[c, s], [-s, c]]),
        offset=(cy - (h / 2.0) * c - (w / 2.0) * s,
                cx + (h / 2.0) * s - (w / 2.0) * c),
        output_shape=(h, w), order=order, mode="constant", cval=0.0,
    )


def _texture(n=160, seed=7) -> np.ndarray:
    """Fine, high-frequency structure -- what a low-pass filter destroys first and
    what a bundleness measure is actually looking at."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:n, 0:n]
    base = 120.0 + 100.0 * np.sin(xx * 1.1) * np.cos(yy * 0.9)
    return np.clip(base + rng.normal(0, 18, (n, n)), 0, 255).astype(np.float32)


def _lapvar(a):
    from scipy import ndimage
    return float(ndimage.laplace(a.astype(np.float64)).var())


def _gradmean(a):
    gx, gy = np.gradient(a.astype(np.float64))
    return float(np.hypot(gx, gy).mean())


def test_derotation_preserves_texture_better_than_bilinear_would():
    """The reason for order=3. Reverting to order=1 reds this."""
    p = _texture()
    x, y, w, h, angle = 40, 40, 70, 70, 30.0
    exact = p[y:y + h, x:x + w]

    produced = extract_crop_from_projection(p, x, y, w, h, angle)
    bilinear = _affine_with_order(p, x, y, w, h, angle, 1)

    # Closeness to the exact slice's texture, as a fraction (1.0 = no loss).
    for measure in (_lapvar, _gradmean):
        ref = measure(exact)
        got_err = abs(measure(produced) - ref) / ref
        bil_err = abs(measure(bilinear) - ref) / ref
        assert got_err < bil_err, (
            f"{measure.__name__}: produced err {got_err:.3f} is not better than "
            f"bilinear's {bil_err:.3f}"
        )


def test_derotation_does_not_invent_intensities_outside_the_source():
    """Cubic splines overshoot near sharp edges; unclipped that becomes a halo once
    the crop is percentile-stretched into uint8."""
    p = np.zeros((120, 120), dtype=np.float32)
    p[40:80, 40:80] = 255.0          # a hard-edged square: worst case for ringing
    out = extract_crop_from_projection(p, 30, 30, 60, 60, 20.0)
    assert out.min() >= p.min() - 1e-6
    assert out.max() <= p.max() + 1e-6

    # ...and the unclipped reference really does overshoot, so the clip is load-bearing
    raw = _affine_with_order(p, 30, 30, 60, 60, 20.0, 3)
    assert raw.max() > p.max() + 1e-6 or raw.min() < p.min() - 1e-6


def test_ninety_degrees_is_an_exact_pixel_permutation():
    """No resampling is mathematically needed at 90 deg, and none happens: the output
    is the input permuted, offset one row by the w/2 centre convention that
    _rotated_corners and the canvas mirror also use."""
    p = _texture(n=100)
    side = 40
    x = y = 30
    exact = p[y:y + side, x:x + side]
    out = extract_crop_from_projection(p, x, y, side, side, 90.0)
    truth = np.rot90(exact, k=1)
    # interior only: the offset row wraps at the border
    shifted = np.roll(truth, 1, axis=0)
    assert np.abs(out[1:-1, 1:-1] - shifted[1:-1, 1:-1]).max() < 1e-3
