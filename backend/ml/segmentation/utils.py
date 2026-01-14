"""
Shared utilities for SAM segmentation.

Contains functions used by both MobileSAM and SAM 3 encoders.
"""

import logging
from typing import List, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def mask_to_polygon(
    mask: np.ndarray,
    simplify_tolerance: float = 1.5,
    min_points: int | None = None,
) -> List[Tuple[int, int]]:
    """
    Convert binary mask to polygon points.

    Uses OpenCV contour detection and Douglas-Peucker simplification.

    Args:
        mask: Binary mask array (H, W)
        simplify_tolerance: Douglas-Peucker simplification tolerance in pixels.
                           Higher = fewer points, smoother polygon.
        min_points: Minimum number of points to return. If provided and the
                   simplified polygon has fewer points, epsilon is reduced
                   iteratively until min_points is reached.

    Returns:
        List of (x, y) polygon points, or empty list if no contours found.
    """
    # Ensure mask is binary uint8
    if mask.dtype == bool:
        mask_uint8 = mask.astype(np.uint8) * 255
    elif mask.dtype in (np.float32, np.float64):
        mask_uint8 = (mask > 0.5).astype(np.uint8) * 255
    else:
        mask_uint8 = mask.astype(np.uint8)
        if mask_uint8.max() == 1:
            mask_uint8 = mask_uint8 * 255

    # Find contours
    contours, _ = cv2.findContours(
        mask_uint8,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    if not contours:
        logger.warning("No contours found in mask")
        return []

    # Get largest contour
    largest = max(contours, key=cv2.contourArea)

    # Simplify polygon using Douglas-Peucker algorithm
    epsilon = simplify_tolerance
    simplified = cv2.approxPolyDP(largest, epsilon, closed=True)

    # Ensure minimum points if specified
    if min_points is not None:
        while len(simplified) < min_points and epsilon > 0.5:
            epsilon /= 2
            simplified = cv2.approxPolyDP(largest, epsilon, closed=True)

    # Convert to list of (x, y) tuples
    points = [(int(p[0][0]), int(p[0][1])) for p in simplified]

    logger.debug(f"Polygon: {len(largest)} -> {len(points)} points (epsilon={epsilon})")

    return points


def polygon_to_mask(
    polygon: List[Tuple[int, int]],
    image_shape: Tuple[int, int],  # (height, width)
) -> np.ndarray:
    """
    Convert polygon back to binary mask.

    Args:
        polygon: List of (x, y) points
        image_shape: Output mask shape (height, width)

    Returns:
        Binary mask array (H, W)
    """
    mask = np.zeros(image_shape, dtype=np.uint8)

    if len(polygon) < 3:
        return mask

    # Convert to numpy array for OpenCV
    pts = np.array(polygon, dtype=np.int32)

    # Fill polygon
    cv2.fillPoly(mask, [pts], 1)

    return mask.astype(bool)


def calculate_polygon_area(polygon: List[Tuple[int, int]]) -> int:
    """
    Calculate polygon area using shoelace formula.

    Args:
        polygon: List of (x, y) points

    Returns:
        Area in pixels (integer)
    """
    n = len(polygon)
    if n < 3:
        return 0

    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += polygon[i][0] * polygon[j][1]
        area -= polygon[j][0] * polygon[i][1]

    return abs(int(area)) // 2
