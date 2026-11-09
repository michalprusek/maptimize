"""YOLO cell detector for confocal microscopy MIPs.

The weights (``weights/best.pt``) are trained on the lab's own curated crops by
``scripts/train_detector.py``; ultralytics reads the architecture from the
checkpoint, so nothing here is specific to one YOLO generation.
"""

import asyncio
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass
import logging

import numpy as np

from config import get_settings

logger = logging.getLogger(__name__)

# Confidence a detection needs to become a crop. Chosen per model, on the
# validation experiments, as the F1-maximising threshold
# (``scripts/train_detector.py evaluate``) — change it together with the weights.
# Current weights: YOLO26s, 2026-11-08, chosen 0.45 (test F1 0.894 at it).
CONF_THRESHOLD = 0.45


def get_weights_path() -> Path:
    """Get YOLO weights path from configuration."""
    return get_settings().yolo_model_path


def model_input(image: np.ndarray) -> np.ndarray:
    """The exact uint8 3-channel array the network sees for ``image``.

    One function for inference AND for writing training images
    (``scripts/train_detector.py``): a detector trained on pixels prepared one
    way and run on pixels prepared another degrades without any error. The
    array is handed to ultralytics as-is, which reads numpy input as BGR — the
    same order ``cv2.imread`` returns, so a PNG written with ``cv2.imwrite``
    round-trips to these exact pixels during training.
    """
    if image.ndim == 2:
        image = np.stack([image, image, image], axis=-1)
    if image.dtype == np.uint16:
        image = (image / 256).astype(np.uint8)
    elif image.dtype in (np.float32, np.float64):
        image = (image * 255).astype(np.uint8)
    return image


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
    """YOLO cell detector: NMS IoU 0.7 (cells overlap), at most 100 per image."""

    def __init__(
        self,
        weights_path: Optional[Path] = None,
        device: str = "cpu",
        conf_threshold: float = CONF_THRESHOLD,
        iou_threshold: float = 0.7,
        max_det: int = 100,
    ):
        self.weights_path = weights_path or get_weights_path()
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
                    logger.error(
                        f"CRITICAL: Custom YOLO weights not found at {self.weights_path}. "
                        "Cell detection requires trained weights - generic model is NOT suitable "
                        "for microscopy images."
                    )
                    raise FileNotFoundError(
                        f"Required YOLO weights file not found: {self.weights_path}. "
                        "Ensure weights/best.pt is mounted in the container via docker-compose."
                    )

                logger.info(f"Loading YOLO weights from {self.weights_path}")
                self._model = YOLO(str(self.weights_path))

                self._model.to(self.device)

            except ImportError:
                raise ImportError(
                    "ultralytics package required for detection. "
                    "Install with: pip install ultralytics"
                )
            except Exception as e:
                if isinstance(e, (ImportError, FileNotFoundError)):
                    raise
                logger.error(f"Failed to load YOLO model from {self.weights_path}: {e}")
                raise RuntimeError(
                    f"Failed to initialize cell detector: {e}. "
                    "Check weights file integrity and GPU availability."
                ) from e

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

        # Run inference
        results = self.model(
            model_input(image),
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

    def reset(self) -> None:
        """Release model from GPU memory."""
        if self._model is not None:
            del self._model
            self._model = None
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                logger.warning("torch.cuda.empty_cache() failed during CellDetector reset", exc_info=True)
            logger.info("CellDetector model released from memory")

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


def _get_detector_raw() -> CellDetector:
    """Internal: get or create the detector singleton (called by GPU manager on every acquire)."""
    global _detector
    if _detector is None:
        _detector = CellDetector()
    return _detector


def get_detector() -> CellDetector:
    """Get the detector via GPU model manager (tracks usage, enables auto-unload)."""
    from ml.gpu_manager import get_gpu_manager
    return get_gpu_manager().acquire("detector")


def reset_detector() -> None:
    """Reset the global detector instance (forces reload on next use)."""
    global _detector
    if _detector is not None:
        _detector.reset()
    _detector = None
    logger.info("Detector reset - will reload on next use")


async def detect_cells_in_image(
    image: np.ndarray,
    conf: Optional[float] = None,
    iou: Optional[float] = None,
) -> List[Detection]:
    """
    Convenience function to detect cells in an image.

    Args:
        image: Input image as numpy array
        conf: Confidence threshold (default: uses detector's conf_threshold)
        iou: IOU threshold for NMS (default: uses detector's iou_threshold)

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
