"""
MobileSAM decoder service for interactive segmentation inference.

Takes pre-computed image embeddings and click prompts to generate masks.
This is the fast part - runs in ~10-50ms per inference.
"""

import logging
from typing import List, Tuple, Optional
import numpy as np
import torch
import cv2

from .sam_encoder import get_sam_encoder

logger = logging.getLogger(__name__)


class SAMDecoder:
    """
    MobileSAM decoder for interactive mask prediction.

    Uses pre-computed image embeddings for fast inference.
    The decoder takes point prompts (clicks) and produces segmentation masks.

    Attributes:
        encoder: Shared SAMEncoder instance (contains the full model)
    """

    def __init__(self):
        """Initialize the decoder (uses shared encoder)."""
        self._encoder = None

    @property
    def encoder(self):
        """Get the shared encoder (which also contains the decoder)."""
        if self._encoder is None:
            self._encoder = get_sam_encoder()
        return self._encoder

    def predict_mask(
        self,
        embedding: np.ndarray,
        image_shape: Tuple[int, int],  # (height, width)
        point_coords: List[Tuple[int, int]],
        point_labels: List[int],  # 1 = positive, 0 = negative
        multimask_output: bool = False,
        box: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[np.ndarray, float, np.ndarray]:
        """
        Predict segmentation mask from click prompts.

        Args:
            embedding: Pre-computed image embedding from SAMEncoder
            image_shape: Original image (height, width)
            point_coords: List of (x, y) click coordinates
            point_labels: List of labels (1=foreground, 0=background)
            multimask_output: If True, return 3 masks with different granularity
            box: Optional bounding box (x1, y1, x2, y2) to constrain segmentation

        Returns:
            Tuple of (mask, iou_score, low_res_logits)
            - mask: Binary mask array (H, W) where 1=object, 0=background
            - iou_score: SAM's predicted IoU (confidence)
            - low_res_logits: Low-resolution mask logits for refinement
        """
        self.encoder.ensure_loaded()

        # Validate model and predictor are available
        if self.encoder.model is None:
            raise RuntimeError("SAM model not loaded. Check model weights.")

        predictor = self.encoder.model.predictor
        if predictor is None:
            raise RuntimeError("SAM predictor not initialized. Model may have failed to load.")

        # Set the cached embedding
        # Convert embedding to tensor and set in predictor
        embedding_tensor = torch.from_numpy(embedding)
        if predictor.device.type == "cuda":
            embedding_tensor = embedding_tensor.cuda()

        # Set features directly in predictor
        predictor.features = embedding_tensor

        # Set original image size for coordinate mapping
        predictor.orig_size = image_shape
        predictor.input_size = image_shape  # Assuming embedding was computed at original size

        # Convert points to numpy arrays
        coords = np.array(point_coords, dtype=np.float32)
        labels = np.array(point_labels, dtype=np.int32)

        # Add batch dimension if needed
        if len(coords.shape) == 2:
            coords = coords[None, :]  # (1, N, 2)
            labels = labels[None, :]  # (1, N)

        try:
            # Run prediction
            if box is not None:
                box_array = np.array(box, dtype=np.float32)[None, :]  # (1, 4)
                masks, iou_scores, low_res_logits = predictor.predict(
                    point_coords=coords,
                    point_labels=labels,
                    box=box_array,
                    multimask_output=multimask_output,
                )
            else:
                masks, iou_scores, low_res_logits = predictor.predict(
                    point_coords=coords,
                    point_labels=labels,
                    multimask_output=multimask_output,
                )

            # Select best mask
            if multimask_output and len(masks.shape) == 4:
                # masks shape: (batch, num_masks, H, W)
                best_idx = np.argmax(iou_scores[0])
                mask = masks[0, best_idx]
                iou_score = float(iou_scores[0, best_idx])
                low_res = low_res_logits[0, best_idx]
            elif len(masks.shape) == 4:
                mask = masks[0, 0]
                iou_score = float(iou_scores[0, 0])
                low_res = low_res_logits[0, 0]
            else:
                mask = masks[0] if len(masks.shape) == 3 else masks
                iou_score = float(iou_scores.flatten()[0])
                low_res = low_res_logits[0] if len(low_res_logits.shape) > 2 else low_res_logits

            return mask.astype(bool), iou_score, low_res

        except Exception as e:
            logger.exception("Mask prediction failed")
            raise RuntimeError(f"Mask prediction failed: {e}") from e

    def predict_from_image(
        self,
        image: np.ndarray,
        point_coords: List[Tuple[int, int]],
        point_labels: List[int],
        multimask_output: bool = False,
    ) -> Tuple[np.ndarray, float, np.ndarray]:
        """
        Predict mask directly from image (without pre-computed embedding).

        Convenience method for one-off predictions. For repeated queries
        on the same image, use predict_mask() with cached embedding instead.

        Args:
            image: Image array (H, W, 3) RGB
            point_coords: List of (x, y) click coordinates
            point_labels: List of labels (1=foreground, 0=background)
            multimask_output: If True, return 3 masks with different granularity

        Returns:
            Tuple of (mask, iou_score, low_res_logits)
        """
        # Encode image first
        embedding = self.encoder.encode_image_from_array(image)
        height, width = image.shape[:2]

        return self.predict_mask(
            embedding=embedding,
            image_shape=(height, width),
            point_coords=point_coords,
            point_labels=point_labels,
            multimask_output=multimask_output,
        )

    def mask_to_polygon(
        self,
        mask: np.ndarray,
        simplify_tolerance: float = 1.5,
        min_points: int = 4,
    ) -> List[Tuple[int, int]]:
        """
        Convert binary mask to polygon points.

        Uses OpenCV contour detection and Douglas-Peucker simplification.

        Args:
            mask: Binary mask array (H, W)
            simplify_tolerance: Douglas-Peucker simplification tolerance in pixels.
                               Higher = fewer points, smoother polygon.
            min_points: Minimum number of points to return.

        Returns:
            List of (x, y) polygon points, or empty list if no contours found.
        """
        # Ensure mask is binary uint8
        if mask.dtype == bool:
            mask_uint8 = mask.astype(np.uint8) * 255
        elif mask.dtype == np.float32 or mask.dtype == np.float64:
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

        # Ensure minimum points
        while len(simplified) < min_points and epsilon > 0.5:
            epsilon /= 2
            simplified = cv2.approxPolyDP(largest, epsilon, closed=True)

        # Convert to list of (x, y) tuples
        points = [(int(p[0][0]), int(p[0][1])) for p in simplified]

        logger.debug(f"Polygon: {len(largest)} -> {len(points)} points (epsilon={epsilon})")

        return points

    def polygon_to_mask(
        self,
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

    def calculate_mask_area(self, mask: np.ndarray) -> int:
        """Calculate mask area in pixels."""
        return int(np.sum(mask))

    def calculate_polygon_area(self, polygon: List[Tuple[int, int]]) -> int:
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


# Global decoder instance
_decoder: Optional[SAMDecoder] = None


def get_sam_decoder() -> SAMDecoder:
    """
    Get or create the global SAM decoder instance.

    Returns:
        Shared SAMDecoder instance
    """
    global _decoder
    if _decoder is None:
        logger.info("Initializing SAM decoder...")
        _decoder = SAMDecoder()
    return _decoder
