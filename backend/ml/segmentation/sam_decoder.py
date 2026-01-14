"""
MobileSAM decoder service for interactive segmentation inference.

Takes pre-computed image embeddings and click prompts to generate masks.
This is the fast part - runs in ~10-50ms per inference.
"""

import logging
from typing import List, Tuple, Optional
import numpy as np
import torch

from .sam_encoder import get_mobilesam_encoder

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
            self._encoder = get_mobilesam_encoder()
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
        Predict segmentation mask from click prompts using cached embedding.

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

        model = self.encoder.model
        if model is None:
            raise RuntimeError("SAM model not loaded. Check model weights.")

        predictor = model.predictor
        if predictor is None:
            raise RuntimeError("SAM predictor not initialized.")

        try:
            # Restore embedding to predictor
            embedding_tensor = torch.from_numpy(embedding)
            device = predictor.device if hasattr(predictor, 'device') else self.encoder.device
            if device == "cuda" or (hasattr(device, 'type') and device.type == "cuda"):
                embedding_tensor = embedding_tensor.cuda()

            # Set the cached features in predictor
            predictor.features = embedding_tensor

            # Set image size info for coordinate transformation
            height, width = image_shape
            predictor.orig_size = (height, width)
            predictor.input_size = (height, width)

            # Prepare point prompts - SAM expects [[x, y], [x, y], ...]
            points_array = np.array(point_coords, dtype=np.float32)
            labels_array = np.array(point_labels, dtype=np.int32)

            # Try direct SAM architecture access first (works with cached embeddings)
            sam_model = getattr(predictor, 'model', None)
            if sam_model and hasattr(sam_model, 'prompt_encoder') and hasattr(sam_model, 'mask_decoder'):
                # Direct SAM architecture access
                masks, iou_scores, low_res_logits = self._sam_predict_with_embedding(
                    predictor, embedding_tensor, points_array, labels_array,
                    image_shape, box, multimask_output
                )
            else:
                # Fallback: Create dummy image and use model predict
                logger.warning("Using fallback prediction method")
                dummy_img = np.zeros((height, width, 3), dtype=np.uint8)
                results = model.predict(
                    dummy_img,
                    points=[point_coords],
                    labels=[point_labels],
                    verbose=False,
                )
                if results and len(results) > 0 and results[0].masks is not None:
                    mask_data = results[0].masks.data[0].cpu().numpy()
                    return mask_data.astype(bool), 0.9, mask_data
                raise RuntimeError("Fallback prediction returned no masks")

            # Process results
            if isinstance(masks, torch.Tensor):
                masks = masks.cpu().numpy()
            if isinstance(iou_scores, torch.Tensor):
                iou_scores = iou_scores.cpu().numpy()
            if isinstance(low_res_logits, torch.Tensor):
                low_res_logits = low_res_logits.cpu().numpy()

            # Select best mask
            masks = np.atleast_3d(masks)
            if masks.ndim == 4:
                masks = masks[0]  # Remove batch dim

            if multimask_output and masks.shape[0] > 1:
                best_idx = np.argmax(iou_scores.flatten()[:masks.shape[0]])
                mask = masks[best_idx]
                iou_score = float(iou_scores.flatten()[best_idx])
                low_res = low_res_logits[best_idx] if low_res_logits.ndim > 2 else low_res_logits
            else:
                mask = masks[0] if masks.shape[0] > 0 else masks
                iou_score = float(iou_scores.flatten()[0]) if iou_scores.size > 0 else 0.9
                low_res = low_res_logits[0] if low_res_logits.ndim > 2 else low_res_logits

            return mask.astype(bool), iou_score, low_res

        except (RuntimeError, ValueError, TypeError) as e:
            logger.exception("Mask prediction failed")
            raise RuntimeError(f"Mask prediction failed: {e}") from e
        except torch.cuda.OutOfMemoryError as e:
            logger.error(f"GPU out of memory during mask prediction: {e}")
            raise RuntimeError(f"GPU out of memory: {e}") from e

    def _sam_predict_with_embedding(
        self,
        predictor,
        embedding: torch.Tensor,
        points: np.ndarray,
        labels: np.ndarray,
        image_shape: Tuple[int, int],
        box: Optional[Tuple[int, int, int, int]],
        multimask_output: bool,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Direct SAM inference using pre-computed embedding."""
        device = embedding.device
        height, width = image_shape

        # Get SAM model components
        sam_model = predictor.model

        # SAM encodes images with longest side = 1024
        # Coordinates must be transformed from original space to 1024-space
        scale = 1024.0 / max(height, width)

        # Scale point coordinates from original image space to SAM's internal space
        scaled_points = points.copy().astype(np.float32)
        scaled_points[:, 0] = points[:, 0] * scale  # x
        scaled_points[:, 1] = points[:, 1] * scale  # y

        logger.debug(
            f"Coordinate transform: scale={scale:.4f}, "
            f"orig=({points[0, 0]:.0f},{points[0, 1]:.0f}) -> "
            f"scaled=({scaled_points[0, 0]:.1f},{scaled_points[0, 1]:.1f})"
        )

        point_coords = torch.from_numpy(scaled_points).float().to(device)
        point_labels = torch.from_numpy(labels).int().to(device)

        # Add batch dimension
        if point_coords.dim() == 2:
            point_coords = point_coords.unsqueeze(0)
            point_labels = point_labels.unsqueeze(0)

        # Run inference without gradients
        with torch.no_grad():
            # Encode prompts
            box_tensor = None
            if box is not None:
                # Scale box coordinates from original to 1024-space
                scaled_box = [
                    box[0] * scale,  # x1
                    box[1] * scale,  # y1
                    box[2] * scale,  # x2
                    box[3] * scale,  # y2
                ]
                box_tensor = torch.tensor([scaled_box], device=device).float()

            sparse_embeddings, dense_embeddings = sam_model.prompt_encoder(
                points=(point_coords, point_labels),
                boxes=box_tensor,
                masks=None,
            )

            # Run mask decoder
            low_res_masks, iou_predictions = sam_model.mask_decoder(
                image_embeddings=embedding,
                image_pe=sam_model.prompt_encoder.get_dense_pe(),
                sparse_prompt_embeddings=sparse_embeddings,
                dense_prompt_embeddings=dense_embeddings,
                multimask_output=multimask_output,
            )

            # Upscale masks to original size
            masks = torch.nn.functional.interpolate(
                low_res_masks,
                size=(height, width),
                mode="bilinear",
                align_corners=False,
            )
            masks = masks > 0.0  # Threshold to binary

        # Return as numpy arrays (detach from computation graph)
        return (
            masks.squeeze(0).cpu().numpy(),
            iou_predictions.squeeze(0).cpu().numpy(),
            low_res_masks.squeeze(0).detach().cpu().numpy(),
        )

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
