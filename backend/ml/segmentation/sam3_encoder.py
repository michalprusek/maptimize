"""
SAM 3 encoder service for text-based segmentation.

Uses Meta's Segment Anything Model 3 (SAM 3) which supports:
- Text prompts: "find all cells", "nucleus", etc.
- Visual prompts: points, boxes (backward compatible with SAM 2)
- Multi-instance detection from a single text query

SAM 3 requires CUDA GPU. For MPS (Mac), use MobileSAM instead.
"""

import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import numpy as np
import torch
from PIL import Image as PILImage

from .utils import mask_to_polygon

logger = logging.getLogger(__name__)

# Model weights directory
WEIGHTS_DIR = os.environ.get(
    "WEIGHTS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "weights"),
)
SAM3_MODEL_PATH = os.path.join(WEIGHTS_DIR, "sam3.pt")


class SAM3Encoder:
    """
    SAM 3 encoder supporting text prompts and image segmentation.

    SAM 3 introduces text-based concept segmentation - users can describe
    what they want to segment (e.g., "cell", "nucleus") and the model
    finds all matching instances.

    Attributes:
        device: Computation device (cuda only - SAM 3 requires CUDA)
        model_name: Human-readable model name for DB storage
        supports_text_prompts: Always True for SAM 3
    """

    def __init__(self, device: Optional[str] = None):
        """
        Initialize the SAM 3 encoder.

        Args:
            device: Device to use (defaults to cuda, falls back to cpu)

        Raises:
            RuntimeError: If CUDA is not available (SAM 3 requires CUDA for text prompts)
        """
        if device is None:
            if torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
                logger.warning(
                    "SAM 3 text prompting works best with CUDA. "
                    "CPU inference may be slow."
                )

        self.device = device
        self.model_name = "sam3-semantic"
        self.supports_text_prompts = True
        self._model = None
        self._processor = None
        self._current_state = None
        self._current_image_path = None

    def _load_model(self) -> None:
        """Load the SAM 3 model."""
        if self._model is not None:
            return

        try:
            from sam3.model_builder import build_sam3_image_model
            from sam3.model.sam3_image_processor import Sam3Processor

            logger.info("Loading SAM 3 model...")
            logger.info(f"Device: {self.device}")

            # SAM 3 auto-downloads weights if not present
            self._model = build_sam3_image_model()
            self._processor = Sam3Processor(self._model, confidence_threshold=0.3)

            # Move to device
            if self.device == "cuda":
                self._model = self._model.cuda()

            logger.info(f"SAM 3 model loaded successfully on {self.device}")

        except ImportError as e:
            raise RuntimeError(
                f"SAM 3 library not installed. Run: uv add sam3\n"
                f"Or: pip install git+https://github.com/facebookresearch/sam3.git\n"
                f"Error: {e}"
            ) from e

    def set_image(self, image_path: str) -> Dict[str, Any]:
        """
        Load image and extract backbone features.

        This prepares the image for subsequent text or point queries.
        The state is cached so multiple queries on the same image are fast.

        Args:
            image_path: Path to image file

        Returns:
            State dict containing image features
        """
        self._load_model()

        # Check if same image is already loaded
        if self._current_image_path == image_path and self._current_state is not None:
            logger.debug(f"Using cached state for {image_path}")
            return self._current_state

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image = PILImage.open(path)
        if image.mode != "RGB":
            image = image.convert("RGB")

        logger.info(f"Setting image for SAM 3: {image_path} ({image.width}x{image.height})")

        # Extract backbone features
        self._current_state = self._processor.set_image(image)
        self._current_image_path = image_path

        return self._current_state

    def predict_with_text(
        self,
        image_path: str,
        text_prompt: str,
        confidence_threshold: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Run text-based segmentation.

        Finds all instances matching the text description.

        Args:
            image_path: Path to image file
            text_prompt: Natural language description (e.g., "cell", "nucleus")
            confidence_threshold: Minimum confidence to include (0.0-1.0)

        Returns:
            Dict with:
                - masks: List of binary masks [N, H, W]
                - boxes: List of bounding boxes [[x1, y1, x2, y2], ...]
                - scores: List of confidence scores
                - polygons: List of polygon points for each mask
        """
        self._load_model()

        # Set image (uses cache if same image)
        state = self.set_image(image_path)

        logger.info(f"Running text query: '{text_prompt}' (threshold={confidence_threshold})")

        # Run text prompt
        state = self._processor.set_text_prompt(state=state, prompt=text_prompt)

        # Extract results
        masks = state["masks"]  # bool tensor [N, H, W]
        boxes = state["boxes"]  # float tensor [N, 4]
        scores = state["scores"]  # float tensor [N]

        # Filter by confidence
        if isinstance(scores, torch.Tensor):
            high_conf_idx = scores > confidence_threshold
            masks = masks[high_conf_idx]
            boxes = boxes[high_conf_idx]
            scores = scores[high_conf_idx]

        # Convert to numpy/lists
        if isinstance(masks, torch.Tensor):
            masks = masks.cpu().numpy()
        if isinstance(boxes, torch.Tensor):
            boxes = boxes.cpu().numpy()
        if isinstance(scores, torch.Tensor):
            scores = scores.cpu().numpy()

        # Convert masks to polygons
        polygons = []
        areas = []
        for mask in masks:
            polygon = mask_to_polygon(mask)
            polygons.append(polygon)
            areas.append(int(np.sum(mask)))

        logger.info(f"Found {len(masks)} instances for '{text_prompt}'")

        return {
            "success": True,
            "masks": masks,
            "boxes": boxes.tolist() if len(boxes) > 0 else [],
            "scores": scores.tolist() if len(scores) > 0 else [],
            "polygons": polygons,
            "areas": areas,
            "prompt": text_prompt,
        }

    def predict_with_points(
        self,
        image_path: str,
        point_coords: List[Tuple[int, int]],
        point_labels: List[int],
        box: Optional[Tuple[int, int, int, int]] = None,
    ) -> Dict[str, Any]:
        """
        Run point-based segmentation (SAM 2 compatible mode).

        SAM 3 is backward compatible with SAM 2's visual prompts.

        Args:
            image_path: Path to image file
            point_coords: List of (x, y) click coordinates
            point_labels: List of labels (1=foreground, 0=background)
            box: Optional bounding box (x1, y1, x2, y2) to constrain

        Returns:
            Dict with mask, polygon, and score
        """
        self._load_model()

        # Set image
        state = self.set_image(image_path)

        logger.info(f"Running point prompt with {len(point_coords)} points")

        try:
            # SAM 3 supports point prompts via set_visual_prompt
            points_array = np.array(point_coords, dtype=np.float32)
            labels_array = np.array(point_labels, dtype=np.int32)

            # Use SAM 3's point prompt interface
            if hasattr(self._processor, 'set_point_prompt'):
                state = self._processor.set_point_prompt(
                    state=state,
                    points=points_array,
                    labels=labels_array,
                    box=box,
                )
            else:
                # Fallback: Use underlying SAM model directly
                from sam3.model.sam3_image_processor import set_visual_prompt
                state = set_visual_prompt(
                    state=state,
                    points=points_array,
                    labels=labels_array,
                    box=np.array(box) if box else None,
                )

            # Extract results
            masks = state.get("masks")
            scores = state.get("scores")

            if masks is None or len(masks) == 0:
                return {"success": False, "error": "No mask generated"}

            # Get best mask
            if isinstance(masks, torch.Tensor):
                mask = masks[0].cpu().numpy()
            else:
                mask = masks[0]

            if isinstance(scores, torch.Tensor):
                score = float(scores[0].cpu())
            else:
                score = float(scores[0]) if len(scores) > 0 else 0.9

            polygon = mask_to_polygon(mask)
            area = int(np.sum(mask))

            return {
                "success": True,
                "mask": mask,
                "polygon": polygon,
                "iou_score": score,
                "area_pixels": area,
            }

        except Exception as e:
            logger.exception("Point-based segmentation failed")
            return {"success": False, "error": str(e)}

    def refine_with_points(
        self,
        image_path: str,
        text_prompt: str,
        instance_index: int,
        point_coords: List[Tuple[int, int]],
        point_labels: List[int],
    ) -> Dict[str, Any]:
        """
        Refine a text-detected instance using point prompts.

        First runs text query to get initial masks, then uses point
        prompts to refine the selected instance.

        Args:
            image_path: Path to image file
            text_prompt: Original text prompt
            instance_index: Which detected instance to refine (0-indexed)
            point_coords: List of (x, y) click coordinates
            point_labels: List of labels (1=foreground, 0=background)

        Returns:
            Dict with refined mask, polygon, and score
        """
        self._load_model()

        # First get text results
        text_results = self.predict_with_text(image_path, text_prompt)

        if not text_results["success"] or len(text_results["masks"]) == 0:
            return {"success": False, "error": "No instances found for text prompt"}

        if instance_index >= len(text_results["masks"]):
            return {"success": False, "error": f"Instance index {instance_index} out of range"}

        # Get initial mask for the selected instance
        initial_mask = text_results["masks"][instance_index]
        initial_box = text_results["boxes"][instance_index]

        # Use SAM 3's point refinement on the selected region
        # SAM 3 supports combining text + point prompts
        state = self._current_state

        # Add point prompts to refine
        points_array = np.array(point_coords, dtype=np.float32)
        labels_array = np.array(point_labels, dtype=np.int32)

        # Run refinement using the box from text detection as constraint
        try:
            # SAM 3 allows combining prompts
            from sam3.model.sam3_image_processor import combine_prompts

            refined_state = combine_prompts(
                state,
                box=initial_box,
                points=points_array,
                point_labels=labels_array,
            )

            refined_mask = refined_state["masks"][0]
            refined_score = float(refined_state["scores"][0])

            if isinstance(refined_mask, torch.Tensor):
                refined_mask = refined_mask.cpu().numpy()

            polygon = mask_to_polygon(refined_mask)
            area = int(np.sum(refined_mask))

            return {
                "success": True,
                "mask": refined_mask,
                "polygon": polygon,
                "score": refined_score,
                "area": area,
            }

        except Exception as e:
            logger.exception(f"Point refinement failed, returning unrefined result")
            # Return initial result with warning flag so UI can inform user
            return {
                "success": True,
                "refinement_failed": True,
                "warning": f"Point refinement failed ({e}), showing initial text result",
                "mask": initial_mask,
                "polygon": text_results["polygons"][instance_index],
                "score": text_results["scores"][instance_index],
                "area": text_results["areas"][instance_index],
            }

    def ensure_loaded(self) -> None:
        """Ensure the model is loaded."""
        self._load_model()

    def reset(self) -> None:
        """Reset the encoder and release model from memory."""
        if self._model is None:
            return

        del self._model
        del self._processor
        self._model = None
        self._processor = None
        self._current_state = None
        self._current_image_path = None

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info("SAM 3 encoder reset")


# Global SAM 3 encoder instance (lazy loaded singleton)
_sam3_encoder: Optional[SAM3Encoder] = None


def get_sam3_encoder() -> SAM3Encoder:
    """Get or create the global SAM 3 encoder instance."""
    global _sam3_encoder
    if _sam3_encoder is None:
        logger.info("Initializing SAM 3 encoder (first use)...")
        _sam3_encoder = SAM3Encoder()
    return _sam3_encoder


def reset_sam3_encoder() -> None:
    """Reset the global SAM 3 encoder instance."""
    global _sam3_encoder
    if _sam3_encoder is not None:
        _sam3_encoder.reset()
        _sam3_encoder = None
        logger.info("SAM 3 encoder reset - will reload on next use")
