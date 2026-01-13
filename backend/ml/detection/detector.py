"""
YOLOv8-based cell detector for confocal microscopy images.
Adapted from /Users/michalprusek/Desktop/microtubules/detection/
"""

import asyncio
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import logging

import numpy as np

logger = logging.getLogger(__name__)

# Weights path
WEIGHTS_PATH = Path(__file__).parent / "weights" / "best.pt"


@dataclass
class Detection:
    """Single cell detection result."""
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    confidence: float
    class_id: int = 0


class CellDetector:
    """
    YOLOv8 cell detector for microscopy images.

    Configured for overlapping cells with:
    - IOU threshold: 0.7 (high overlap tolerance)
    - Max detections: 100 per image
    - Confidence threshold: 0.7 (default)
    """

    def __init__(
        self,
        weights_path: Optional[Path] = None,
        device: str = "cpu",
        conf_threshold: float = 0.7,
        iou_threshold: float = 0.7,
        max_det: int = 100,
    ):
        self.weights_path = weights_path or WEIGHTS_PATH
        self.device = device
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.max_det = max_det
        self._model = None

    @property
    def model(self):
        """Lazy load the YOLO model."""
        if self._model is None:
            try:
                from ultralytics import YOLO

                if not self.weights_path.exists():
                    logger.warning(f"Weights not found at {self.weights_path}, using pretrained")
                    self._model = YOLO("yolov8n.pt")
                else:
                    logger.info(f"Loading YOLO weights from {self.weights_path}")
                    self._model = YOLO(str(self.weights_path))

                # Move to device
                self._model.to(self.device)

                # Mark model as already fused to skip fusion during inference
                # Our trained weights may have Conv layers without bn (version mismatch)
                # This prevents ultralytics from trying to fuse and failing
                if hasattr(self._model, 'model'):
                    self._model.model.fused = True
                    logger.info("Model marked as fused (skipping fusion)")

            except ImportError:
                raise ImportError(
                    "ultralytics package required for detection. "
                    "Install with: pip install ultralytics"
                )

        return self._model

    def detect(
        self,
        image: np.ndarray,
        conf: Optional[float] = None,
        iou: Optional[float] = None,
    ) -> List[Detection]:
        """
        Detect cells in an image.

        Args:
            image: Input image as numpy array (grayscale or RGB)
            conf: Confidence threshold (default: self.conf_threshold)
            iou: IOU threshold for NMS (default: self.iou_threshold)

        Returns:
            List of Detection objects
        """
        conf = conf or self.conf_threshold
        iou = iou or self.iou_threshold

        # Ensure image is in correct format
        if len(image.shape) == 2:
            # Grayscale -> RGB
            image = np.stack([image, image, image], axis=-1)

        # Normalize if 16-bit
        if image.dtype == np.uint16:
            image = (image / 256).astype(np.uint8)
        elif image.dtype == np.float32 or image.dtype == np.float64:
            image = (image * 255).astype(np.uint8)

        # Run inference
        results = self.model(
            image,
            conf=conf,
            iou=iou,
            max_det=self.max_det,
            verbose=False,
        )

        # Parse results
        detections = []
        if results and len(results) > 0:
            boxes = results[0].boxes

            for box in boxes:
                # Get bounding box coordinates (xyxy format)
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                confidence = float(box.conf[0].cpu().numpy())
                class_id = int(box.cls[0].cpu().numpy())

                # Convert to x, y, w, h format
                detections.append(Detection(
                    bbox_x=int(x1),
                    bbox_y=int(y1),
                    bbox_w=int(x2 - x1),
                    bbox_h=int(y2 - y1),
                    confidence=confidence,
                    class_id=class_id,
                ))

        logger.info(f"Detected {len(detections)} cells")
        return detections

    async def detect_async(
        self,
        image: np.ndarray,
        **kwargs
    ) -> List[Detection]:
        """Async wrapper for detect."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.detect(image, **kwargs))


# Global detector instance (lazy loaded)
_detector: Optional[CellDetector] = None


def get_detector() -> CellDetector:
    """Get or create the global detector instance."""
    global _detector
    if _detector is None:
        _detector = CellDetector()
    return _detector


def reset_detector() -> None:
    """Reset the global detector instance (forces reload on next use)."""
    global _detector
    _detector = None
    logger.info("Detector reset - will reload on next use")


async def detect_cells_in_image(
    image: np.ndarray,
    conf: float = 0.7,
    iou: float = 0.7,
) -> List[Detection]:
    """
    Convenience function to detect cells in an image.

    Args:
        image: Input image as numpy array
        conf: Confidence threshold
        iou: IOU threshold for NMS

    Returns:
        List of Detection objects
    """
    detector = get_detector()
    return await detector.detect_async(image, conf=conf, iou=iou)


def create_mip(zstack: np.ndarray) -> np.ndarray:
    """
    Create Maximum Intensity Projection from Z-stack.

    Args:
        zstack: 3D array with shape (Z, H, W) or (Z, H, W, C)

    Returns:
        2D MIP image
    """
    return np.max(zstack, axis=0)


def create_mip_std(zstack: np.ndarray) -> np.ndarray:
    """
    Create MIP+STD RGB image for better feature extraction.

    R = MIP (Maximum Intensity Projection)
    G = STD (Standard Deviation across Z)
    B = MIP

    Args:
        zstack: 3D array with shape (Z, H, W)

    Returns:
        RGB image with shape (H, W, 3)
    """
    mip = np.max(zstack, axis=0)
    std = np.std(zstack, axis=0)

    # Normalize
    mip_norm = normalize_image(mip)
    std_norm = normalize_image(std)

    return np.stack([mip_norm, std_norm, mip_norm], axis=-1)


def normalize_image(
    image: np.ndarray,
    low_percentile: float = 1.0,
    high_percentile: float = 99.5,
) -> np.ndarray:
    """
    Normalize image to 0-255 range using percentile clipping.

    Args:
        image: Input image
        low_percentile: Lower percentile for clipping
        high_percentile: Upper percentile for clipping

    Returns:
        Normalized uint8 image
    """
    low = np.percentile(image, low_percentile)
    high = np.percentile(image, high_percentile)

    if high - low < 1e-6:
        return np.zeros_like(image, dtype=np.uint8)

    normalized = (image - low) / (high - low)
    normalized = np.clip(normalized, 0, 1)

    return (normalized * 255).astype(np.uint8)
