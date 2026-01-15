"""
Shared utilities for SAM segmentation.

Contains functions used by both MobileSAM and SAM 3 encoders.
"""

import logging
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _prepare_mask_for_contours(mask: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[List]]:
    """
    Prepare mask for contour detection: validate, squeeze, convert to uint8.

    Args:
        mask: Binary mask array (potentially with extra dimensions)

    Returns:
        Tuple of (processed_mask_uint8, contours) or (None, None) on failure.
    """
    # Ensure mask is 2D - squeeze extra dimensions
    if mask.ndim > 2:
        mask = np.squeeze(mask)
    if mask.ndim != 2:
        logger.warning(f"Invalid mask dimensions: {mask.shape}, expected 2D")
        return None, None

    # Ensure mask is contiguous and valid
    if mask.size == 0:
        logger.warning("Empty mask received")
        return None, None

    # Make contiguous copy if needed
    if not mask.flags['C_CONTIGUOUS']:
        mask = np.ascontiguousarray(mask)

    # Convert to binary uint8
    if mask.dtype == bool:
        mask_uint8 = mask.astype(np.uint8) * 255
    elif mask.dtype in (np.float32, np.float64):
        mask_uint8 = (mask > 0.5).astype(np.uint8) * 255
    else:
        mask_uint8 = mask.astype(np.uint8)
        if mask_uint8.max() == 1:
            mask_uint8 = mask_uint8 * 255

    # Ensure contiguous after conversion
    mask_uint8 = np.ascontiguousarray(mask_uint8)

    # Find contours
    try:
        contours, _ = cv2.findContours(
            mask_uint8,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )
    except cv2.error as e:
        logger.error(f"OpenCV findContours failed: {e}, mask shape: {mask_uint8.shape}, dtype: {mask_uint8.dtype}")
        return None, None

    if not contours:
        logger.warning("No contours found in mask")
        return None, None

    return mask_uint8, contours


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
    _, contours = _prepare_mask_for_contours(mask)
    if contours is None:
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


def mask_to_polygons(
    mask: np.ndarray,
    simplify_tolerance: float = 1.5,
    min_area: int = 100,
) -> List[List[Tuple[int, int]]]:
    """
    Convert binary mask to multiple polygon points (all contours).

    Unlike mask_to_polygon which returns only the largest contour,
    this function returns ALL contours above the minimum area threshold.

    Args:
        mask: Binary mask array (H, W)
        simplify_tolerance: Douglas-Peucker simplification tolerance in pixels.
        min_area: Minimum contour area in pixels to include.

    Returns:
        List of polygons, each polygon is a list of (x, y) points.
    """
    _, contours = _prepare_mask_for_contours(mask)
    if contours is None:
        return []

    # Process ALL contours above minimum area
    polygons = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        # Simplify polygon
        simplified = cv2.approxPolyDP(contour, simplify_tolerance, closed=True)

        # Need at least 3 points for a polygon
        if len(simplified) < 3:
            continue

        # Convert to list of (x, y) tuples
        points = [(int(p[0][0]), int(p[0][1])) for p in simplified]
        polygons.append(points)

    logger.debug(f"Found {len(polygons)} polygons from {len(contours)} contours")

    return polygons


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


def normalize_polygon_data(data: List) -> List[List[Tuple[int, int]]]:
    """
    Normalize polygon data to consistent multi-polygon format.

    Handles both:
    - Single polygon: [[x,y], [x,y], ...]
    - Multi-polygon: [[[x,y], ...], [[x,y], ...], ...]

    Returns list of polygons (always multi-polygon format).
    """
    if not data or len(data) == 0:
        return []

    # Check if first element is a polygon (list of points) or a point (list of 2 numbers)
    first = data[0]
    if isinstance(first, list) and len(first) > 0 and isinstance(first[0], list):
        # Multi-polygon format - already correct
        return data

    # Single polygon format - wrap in list
    return [data]
