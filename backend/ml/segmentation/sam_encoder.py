"""
SAM 3 image encoder service for pre-computing embeddings.

Uses SAM 3 (Segment Anything Model 3) to generate image embeddings
that can be cached and reused for fast interactive segmentation.

The encoder is the expensive part of SAM (~5-15s on GPU).
Once embeddings are cached, interactive inference is instant (~10-50ms).
"""

import logging
import zlib
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
import torch
from PIL import Image as PILImage

logger = logging.getLogger(__name__)

# SAM model configuration
# Using Ultralytics SAM3 for simpler API
SAM3_MODEL_PATH = "sam3.pt"  # Will be downloaded on first use


class SAMEncoder:
    """
    SAM 3 image encoder for generating reusable image embeddings.

    The encoder runs the image through SAM's vision transformer to produce
    dense feature maps that can be reused for multiple interactive queries.

    Attributes:
        variant: Model variant identifier
        device: Computation device (cuda/cpu)
        model: Loaded SAM model
        model_name: Human-readable model name for DB storage
    """

    def __init__(
        self,
        variant: str = "sam3",
        device: Optional[str] = None,
    ):
        """
        Initialize the SAM encoder.

        Args:
            variant: Model variant (currently only "sam3" supported)
            device: Device to use (None for auto-detection)
        """
        self.variant = variant
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model = None
        self._predictor = None
        self.model_name = f"sam3-encoder"
        self._is_loaded = False

    @property
    def model(self):
        """Lazy load the SAM3 model."""
        if self._model is None:
            self._load_model()
        return self._model

    @property
    def predictor(self):
        """Get the image predictor (handles embedding computation)."""
        if self._predictor is None:
            self._load_model()
        return self._predictor

    def _load_model(self) -> None:
        """Load the SAM3 model from Ultralytics."""
        try:
            from ultralytics import SAM

            logger.info(f"Loading SAM3 model...")
            logger.info(f"Device: {self.device}")

            # Load SAM3 model (downloads automatically if not present)
            self._model = SAM(SAM3_MODEL_PATH)

            # Move to device
            self._model.to(self.device)

            # Get the predictor for embedding computation
            # Ultralytics SAM provides set_image() and predict() methods
            self._predictor = self._model.predictor

            self._is_loaded = True
            logger.info(f"SAM3 model loaded successfully on {self.device}")

        except ImportError as e:
            raise RuntimeError(
                f"Ultralytics library not installed. "
                f"Run: pip install ultralytics\nError: {e}"
            )
        except Exception as e:
            logger.exception(f"Failed to load SAM3 model")
            raise RuntimeError(f"Failed to load SAM3 model: {e}")

    def ensure_loaded(self) -> None:
        """Ensure model is loaded before use."""
        if not self._is_loaded:
            self._load_model()

    def encode_image(
        self,
        image_path: str,
    ) -> Tuple[np.ndarray, int, int]:
        """
        Encode an image and return the embedding.

        This runs the SAM image encoder to produce dense feature maps
        that can be reused for multiple interactive segmentation queries.

        Args:
            image_path: Path to image file (PNG, TIFF, etc.)

        Returns:
            Tuple of (embedding array, width, height)

        Raises:
            FileNotFoundError: If image file doesn't exist
            RuntimeError: If encoding fails
        """
        self.ensure_loaded()

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        # Load image
        image = PILImage.open(path)

        # Convert to RGB if needed
        if image.mode != "RGB":
            image = image.convert("RGB")

        image_np = np.array(image)
        height, width = image_np.shape[:2]

        logger.info(f"Encoding image: {image_path} ({width}x{height})")

        try:
            # Set image in predictor - this computes the embedding
            self._model.predictor.set_image(image_np)

            # Get the image embedding from predictor
            # SAM stores this in the predictor after set_image()
            features = self._model.predictor.features

            if features is None:
                raise RuntimeError("Failed to extract features from SAM encoder")

            # Convert to numpy and move to CPU
            if isinstance(features, torch.Tensor):
                embedding = features.cpu().numpy()
            else:
                embedding = np.array(features)

            logger.info(f"Embedding shape: {embedding.shape}, dtype: {embedding.dtype}")

            return embedding, width, height

        except torch.cuda.OutOfMemoryError as e:
            raise RuntimeError(
                f"GPU out of memory encoding image {image_path} ({width}x{height}). "
                f"Try a smaller image or use CPU.\nError: {e}"
            )
        except Exception as e:
            logger.exception(f"Failed to encode image: {image_path}")
            raise RuntimeError(f"Failed to encode image: {e}")

    def encode_image_from_array(
        self,
        image: np.ndarray,
    ) -> np.ndarray:
        """
        Encode an image array and return the embedding.

        Args:
            image: Image array (H, W, 3) RGB or (H, W) grayscale

        Returns:
            Embedding array
        """
        self.ensure_loaded()

        # Convert grayscale to RGB if needed
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

        # Set image and get features
        self._model.predictor.set_image(image)
        features = self._model.predictor.features

        if features is None:
            raise RuntimeError("Failed to extract features from SAM encoder")

        if isinstance(features, torch.Tensor):
            return features.cpu().numpy()
        return np.array(features)

    def compress_embedding(self, embedding: np.ndarray) -> bytes:
        """
        Compress embedding for database storage.

        Uses float16 conversion and zlib compression to reduce size
        from ~16MB to ~2-4MB.

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

    def reset(self) -> None:
        """Reset the encoder (release model from memory)."""
        if self._model is not None:
            del self._model
            del self._predictor
            self._model = None
            self._predictor = None
            self._is_loaded = False

            # Clear CUDA cache
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            logger.info("SAM encoder reset")


# Global encoder instance (lazy loaded singleton)
_encoder: Optional[SAMEncoder] = None


def get_sam_encoder() -> SAMEncoder:
    """
    Get or create the global SAM encoder instance.

    Returns:
        Shared SAMEncoder instance
    """
    global _encoder
    if _encoder is None:
        logger.info("Initializing SAM3 encoder (first use)...")
        _encoder = SAMEncoder()
    return _encoder


def reset_sam_encoder() -> None:
    """
    Reset the global encoder instance.

    Forces model reload on next use. Useful for freeing GPU memory.
    """
    global _encoder
    if _encoder is not None:
        _encoder.reset()
        _encoder = None
        logger.info("SAM encoder reset - will reload on next use")
