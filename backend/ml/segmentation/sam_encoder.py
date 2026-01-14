"""
MobileSAM image encoder service for pre-computing embeddings.

Uses MobileSAM (lightweight Segment Anything Model) to generate image embeddings
that can be cached and reused for fast interactive segmentation.

The encoder is the expensive part of SAM (timing varies by image size and GPU).
Once embeddings are cached, interactive inference is fast (~10-50ms).
"""

import logging
import os
import zlib
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import torch
from PIL import Image as PILImage

logger = logging.getLogger(__name__)

# Model weights directory: WEIGHTS_DIR env var, or default to backend/weights
WEIGHTS_DIR = os.environ.get(
    "WEIGHTS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "weights"),
)
SAM_MODEL_PATH = os.path.join(WEIGHTS_DIR, "mobile_sam.pt")


class SAMEncoder:
    """
    MobileSAM image encoder for generating reusable image embeddings.

    The encoder runs the image through SAM's vision transformer to produce
    dense feature maps that can be reused for multiple interactive queries.

    Attributes:
        device: Computation device (cuda/cpu)
        model_name: Human-readable model name for DB storage
    """

    def __init__(self, device: Optional[str] = None):
        """
        Initialize the SAM encoder.

        Args:
            device: Device to use (None for auto-detection)
        """
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model_name = "mobilesam-encoder"
        self._model = None

    def _load_model(self) -> None:
        """Load the MobileSAM model from Ultralytics."""
        if self._model is not None:
            return

        try:
            from ultralytics import SAM

            logger.info("Loading MobileSAM model...")
            logger.info(f"Device: {self.device}")

            if not os.path.exists(SAM_MODEL_PATH):
                raise RuntimeError(
                    f"SAM model weights not found at {SAM_MODEL_PATH}. "
                    f"Run: python scripts/download_weights.py"
                )

            self._model = SAM(SAM_MODEL_PATH)
            self._model.to(self.device)

            # Initialize predictor with dummy prediction (required by Ultralytics SAM)
            logger.info("Initializing SAM predictor...")
            dummy_img = np.zeros((64, 64, 3), dtype=np.uint8)
            self._model.predict(dummy_img, bboxes=[[0, 0, 32, 32]], verbose=False)

            if self._model.predictor is None:
                raise RuntimeError("Failed to initialize SAM predictor")

            logger.info(f"MobileSAM model loaded successfully on {self.device}")

        except ImportError as e:
            raise RuntimeError(
                f"Ultralytics library not installed. Run: uv add ultralytics\nError: {e}"
            ) from e

    def _extract_features(self, image: np.ndarray) -> np.ndarray:
        """
        Extract features from an image array.

        Args:
            image: RGB image array (H, W, 3), uint8

        Returns:
            Feature embedding array
        """
        self._model.predictor.set_image(image)
        features = self._model.predictor.features

        if features is None:
            raise RuntimeError("Failed to extract features from SAM encoder")

        if isinstance(features, torch.Tensor):
            return features.detach().cpu().numpy()
        return np.array(features)

    def _prepare_image(self, image: np.ndarray) -> np.ndarray:
        """
        Convert image to RGB uint8 format expected by SAM.

        Args:
            image: Image array (H, W, 3) RGB, (H, W) grayscale, or (H, W, 1)

        Returns:
            RGB uint8 image array (H, W, 3)
        """
        # Convert grayscale to RGB
        if len(image.shape) == 2:
            image = np.stack([image] * 3, axis=-1)
        elif image.shape[2] == 1:
            image = np.concatenate([image] * 3, axis=-1)

        # Ensure uint8
        if image.dtype != np.uint8:
            if image.max() <= 1.0:
                image = (image * 255).astype(np.uint8)
            else:
                image = image.astype(np.uint8)

        return image

    def encode_image(self, image_path: str) -> Tuple[np.ndarray, int, int]:
        """
        Encode an image file and return the embedding.

        Args:
            image_path: Path to image file (PNG, TIFF, etc.)

        Returns:
            Tuple of (embedding array, width, height)

        Raises:
            FileNotFoundError: If image file doesn't exist
            RuntimeError: If encoding fails
        """
        self._load_model()

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image = PILImage.open(path)
        if image.mode != "RGB":
            image = image.convert("RGB")

        image_np = np.array(image)
        height, width = image_np.shape[:2]

        logger.info(f"Encoding image: {image_path} ({width}x{height})")

        try:
            embedding = self._extract_features(image_np)
            logger.info(f"Embedding shape: {embedding.shape}, dtype: {embedding.dtype}")
            return embedding, width, height

        except torch.cuda.OutOfMemoryError as e:
            raise RuntimeError(
                f"GPU out of memory encoding image {image_path} ({width}x{height}). "
                f"Try a smaller image or use CPU."
            ) from e

    def encode_image_from_array(self, image: np.ndarray) -> np.ndarray:
        """
        Encode an image array and return the embedding.

        Args:
            image: Image array (H, W, 3) RGB or (H, W) grayscale

        Returns:
            Embedding array
        """
        self._load_model()
        image = self._prepare_image(image)
        return self._extract_features(image)

    def compress_embedding(self, embedding: np.ndarray) -> bytes:
        """
        Compress embedding for database storage.

        Uses float16 conversion and zlib compression for significant
        size reduction (typically 6-8x compression ratio).

        Args:
            embedding: Embedding array from encode_image()

        Returns:
            Compressed bytes
        """
        # Convert to float16 for 50% size reduction
        embedding_f16 = embedding.astype(np.float16)
        # Compress with zlib (level 6 is good balance of speed/compression)
        compressed = zlib.compress(embedding_f16.tobytes(), level=6)

        original_size = embedding.nbytes
        compressed_size = len(compressed)
        ratio = compressed_size / original_size * 100

        logger.debug(
            f"Embedding compressed: {original_size / 1024 / 1024:.1f}MB -> "
            f"{compressed_size / 1024 / 1024:.1f}MB ({ratio:.1f}%)"
        )

        return compressed

    def decompress_embedding(
        self,
        data: bytes,
        shape: Tuple[int, ...],
    ) -> np.ndarray:
        """
        Decompress embedding from database storage.

        Args:
            data: Compressed bytes from compress_embedding()
            shape: Original embedding shape tuple

        Returns:
            Decompressed embedding array (float32)
        """
        # Decompress
        decompressed = zlib.decompress(data)
        # Reconstruct array as float16
        embedding = np.frombuffer(decompressed, dtype=np.float16)
        # Reshape and convert back to float32 for inference
        return embedding.reshape(shape).astype(np.float32)

    def ensure_loaded(self) -> None:
        """Ensure the model is loaded. Public alias for _load_model()."""
        self._load_model()

    @property
    def model(self):
        """Get the underlying SAM model. Ensures model is loaded first."""
        self.ensure_loaded()
        return self._model

    def reset(self) -> None:
        """Reset the encoder and release model from memory."""
        if self._model is None:
            return

        del self._model
        self._model = None

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info("SAM encoder reset")


# Global encoder instance (lazy loaded singleton)
_encoder: Optional[SAMEncoder] = None


def get_mobilesam_encoder() -> SAMEncoder:
    """Get or create the global MobileSAM encoder instance."""
    global _encoder
    if _encoder is None:
        logger.info("Initializing MobileSAM encoder (first use)...")
        _encoder = SAMEncoder()
    return _encoder


# Alias for backward compatibility
get_sam_encoder = get_mobilesam_encoder


def reset_mobilesam_encoder() -> None:
    """Reset the global encoder instance. Forces model reload on next use."""
    global _encoder
    if _encoder is not None:
        _encoder.reset()
        _encoder = None
        logger.info("MobileSAM encoder reset - will reload on next use")


# Alias for backward compatibility
reset_sam_encoder = reset_mobilesam_encoder
