"""Box geometry shared by the crop editor and detector training."""

import math


def rotated_corners(x: float, y: float, w: float, h: float, angle: float) -> list[tuple[float, float]]:
    """Corners of the (x, y, w, h) box rotated ``angle`` degrees about its centre.

    This is what ``cell_crops.bbox_angle`` means. The crop editor validates and
    extracts against these corners, and both the annotation export and detector
    training derive their axis-aligned box from them, so all three agree on what
    a rotated crop covers. The canvas mirror lives in ``frontend/lib/editor/geometry.ts``.
    """
    cx, cy = x + w / 2.0, y + h / 2.0
    theta = math.radians(angle)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return [
        (cx + u * cos_t - v * sin_t, cy + u * sin_t + v * cos_t)
        for u in (-w / 2.0, w / 2.0)
        for v in (-h / 2.0, h / 2.0)
    ]
