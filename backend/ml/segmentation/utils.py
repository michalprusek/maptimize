"""
Shared utilities for SAM segmentation.

Contains functions used by both MobileSAM and SAM 3 encoders.

Polygon formats:
- Simple polygon: [[x,y], [x,y], ...] - list of coordinate tuples
- Polygon with holes: {"outer": [[x,y], ...], "holes": [[[x,y], ...], ...]}
  - outer: the external boundary (clockwise)
  - holes: list of internal boundaries (counter-clockwise) that are cut out
"""

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# Type alias for polygon with holes
PolygonWithHoles = Dict[str, Any]  # {"outer": List[Tuple[int,int]], "holes": List[List[Tuple[int,int]]]}
SimplePolygon = List[Tuple[int, int]]
AnyPolygon = Union[SimplePolygon, PolygonWithHoles]


def _validate_and_convert_mask(mask: np.ndarray) -> Optional[np.ndarray]:
    """
    Validate and convert mask to uint8 format suitable for contour detection.

    Shared helper that performs dimension validation, dtype conversion, and
    ensures the array is contiguous.

    Args:
        mask: Binary mask array (potentially with extra dimensions)

    Returns:
        Processed mask as uint8 array, or None if validation fails.
    """
    # Ensure mask is 2D - squeeze extra dimensions
    if mask.ndim > 2:
        mask = np.squeeze(mask)
    if mask.ndim != 2:
        logger.warning(f"Invalid mask dimensions: {mask.shape}, expected 2D")
        return None

    # Ensure mask is contiguous and valid
    if mask.size == 0:
        logger.warning("Empty mask received")
        return None

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
    return np.ascontiguousarray(mask_uint8)


def _prepare_mask_for_contours(mask: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[List]]:
    """
    Prepare mask for contour detection: validate, squeeze, convert to uint8.

    Uses RETR_EXTERNAL to find only outermost contours (no holes).

    Args:
        mask: Binary mask array (potentially with extra dimensions)

    Returns:
        Tuple of (processed_mask_uint8, contours) or (None, None) on failure.
    """
    mask_uint8 = _validate_and_convert_mask(mask)
    if mask_uint8 is None:
        return None, None

    # Find contours (external only)
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


def _prepare_mask_for_contours_with_hierarchy(
    mask: np.ndarray
) -> Tuple[Optional[np.ndarray], Optional[List], Optional[np.ndarray]]:
    """
    Prepare mask for contour detection with hierarchy (for detecting holes).

    Uses RETR_CCOMP to get 2-level hierarchy:
    - Outer contours have hierarchy[i][3] == -1 (no parent)
    - Holes have hierarchy[i][3] != -1 (parent is outer contour index)

    Args:
        mask: Binary mask array (potentially with extra dimensions)

    Returns:
        Tuple of (processed_mask_uint8, contours, hierarchy) or (None, None, None) on failure.
    """
    mask_uint8 = _validate_and_convert_mask(mask)
    if mask_uint8 is None:
        return None, None, None

    # Find contours with hierarchy (RETR_CCOMP for 2-level hierarchy)
    try:
        contours, hierarchy = cv2.findContours(
            mask_uint8,
            cv2.RETR_CCOMP,  # 2-level hierarchy: outer + holes
            cv2.CHAIN_APPROX_SIMPLE
        )
    except cv2.error as e:
        logger.error(f"OpenCV findContours failed: {e}, mask shape: {mask_uint8.shape}, dtype: {mask_uint8.dtype}")
        return None, None, None

    if not contours:
        logger.warning("No contours found in mask")
        return None, None, None

    return mask_uint8, contours, hierarchy


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


# ============================================================================
# Polygon with Holes Support
# ============================================================================

def _simplify_contour(
    contour: np.ndarray,
    simplify_tolerance: float = 1.5,
) -> List[Tuple[int, int]]:
    """
    Simplify a single contour using Douglas-Peucker algorithm.

    Args:
        contour: OpenCV contour array
        simplify_tolerance: Epsilon for Douglas-Peucker simplification

    Returns:
        List of (x, y) tuples
    """
    simplified = cv2.approxPolyDP(contour, simplify_tolerance, closed=True)
    return [(int(p[0][0]), int(p[0][1])) for p in simplified]


def mask_to_polygon_with_holes(
    mask: np.ndarray,
    simplify_tolerance: float = 1.5,
    min_hole_area: int = 50,
) -> PolygonWithHoles:
    """
    Convert binary mask to polygon with internal holes.

    Uses RETR_CCOMP hierarchy to detect outer boundary and inner holes.
    Ring-shaped structures (e.g., cell membranes with hollow centers) are
    properly represented with holes cut out.

    Args:
        mask: Binary mask array (H, W)
        simplify_tolerance: Douglas-Peucker simplification tolerance in pixels
        min_hole_area: Minimum area for holes to include (filters noise)

    Returns:
        Dict with "outer" and "holes" keys:
        - outer: List of (x, y) points for outer boundary
        - holes: List of polygons (each a list of (x, y) points) for holes
    """
    _, contours, hierarchy = _prepare_mask_for_contours_with_hierarchy(mask)

    if contours is None or hierarchy is None:
        return {"outer": [], "holes": []}

    hierarchy = hierarchy[0]  # hierarchy has shape (1, N, 4)

    # Find the largest outer contour (parent == -1)
    outer_contour = None
    outer_index = -1
    max_area = 0

    for i, contour in enumerate(contours):
        parent = hierarchy[i][3]
        if parent == -1:  # No parent = outer contour
            area = cv2.contourArea(contour)
            if area > max_area:
                max_area = area
                outer_contour = contour
                outer_index = i

    if outer_contour is None:
        logger.warning("No outer contour found in mask")
        return {"outer": [], "holes": []}

    # Find all holes that belong to this outer contour
    holes = []
    for i, contour in enumerate(contours):
        parent = hierarchy[i][3]
        if parent == outer_index:  # This contour's parent is our outer contour
            area = cv2.contourArea(contour)
            if area >= min_hole_area:
                holes.append(_simplify_contour(contour, simplify_tolerance))

    outer_simplified = _simplify_contour(outer_contour, simplify_tolerance)

    logger.debug(
        f"Polygon with holes: outer={len(outer_simplified)} points, "
        f"holes={len(holes)} (filtered from {sum(1 for i in range(len(contours)) if hierarchy[i][3] == outer_index)})"
    )

    return {
        "outer": outer_simplified,
        "holes": holes,
    }


def polygon_to_mask_with_holes(
    polygon_data: PolygonWithHoles,
    image_shape: Tuple[int, int],  # (height, width)
) -> np.ndarray:
    """
    Convert polygon with holes back to binary mask.

    Args:
        polygon_data: Dict with "outer" and "holes" keys
        image_shape: Output mask shape (height, width)

    Returns:
        Binary mask array (H, W) as bool
    """
    mask = np.zeros(image_shape, dtype=np.uint8)

    outer = polygon_data.get("outer", [])
    if len(outer) < 3:
        return mask.astype(bool)

    # Fill outer contour
    outer_pts = np.array(outer, dtype=np.int32)
    cv2.fillPoly(mask, [outer_pts], 1)

    # Cut out holes
    for hole in polygon_data.get("holes", []):
        if len(hole) >= 3:
            hole_pts = np.array(hole, dtype=np.int32)
            cv2.fillPoly(mask, [hole_pts], 0)

    return mask.astype(bool)


def is_polygon_with_holes(data: Any) -> bool:
    """
    Check if polygon data is in the new holes format.

    Args:
        data: Polygon data to check

    Returns:
        True if data is {"outer": [...], "holes": [...]} format
    """
    return isinstance(data, dict) and "outer" in data


def normalize_polygon_format(data: Any) -> PolygonWithHoles:
    """
    Normalize any polygon format to the new holes format.

    Handles:
    - New format: {"outer": [...], "holes": [...]} - returned as-is
    - Old simple format: [[x,y], ...] - converted to {"outer": [...], "holes": []}

    Args:
        data: Polygon data in any supported format

    Returns:
        Polygon in holes format
    """
    if is_polygon_with_holes(data):
        return data

    # Old format - simple list of points
    if isinstance(data, list) and len(data) > 0:
        # Check if it's actually a list of points (not multi-polygon)
        first = data[0]
        if isinstance(first, (list, tuple)) and len(first) == 2:
            # It's a simple polygon [[x,y], ...]
            return {"outer": data, "holes": []}

    # Empty or invalid - return empty
    return {"outer": [], "holes": []}


def calculate_polygon_area_with_holes(polygon_data: PolygonWithHoles) -> int:
    """
    Calculate polygon area accounting for holes.

    Uses shoelace formula for outer boundary, then subtracts hole areas.

    Args:
        polygon_data: Dict with "outer" and "holes" keys

    Returns:
        Net area in pixels (outer - holes)
    """
    outer = polygon_data.get("outer", [])
    outer_area = calculate_polygon_area(outer)

    holes_area = sum(
        calculate_polygon_area(hole)
        for hole in polygon_data.get("holes", [])
    )

    return max(0, outer_area - holes_area)
