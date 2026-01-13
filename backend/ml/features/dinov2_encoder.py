"""DINOv2 encoder for cell crop feature extraction."""

import logging
import os
from typing import Optional
import torch

from .base_encoder import BaseEncoder, PoolingMode

logger = logging.getLogger(__name__)


class DINOv2Encoder(BaseEncoder):
    """
    DINOv2 encoder with configurable pooling.

    DINOv2 is Meta's self-supervised Vision Transformer with excellent
    representation quality for downstream tasks.

    Variants:
        - small: 384-dim, ViT-S/14
        - base: 768-dim, ViT-B/14
        - large: 1024-dim, ViT-L/14
        - giant: 1536-dim, ViT-G/14
    """

    VARIANTS = {
        "small": ("facebook/dinov2-small", 384),
        "base": ("facebook/dinov2-base", 768),
        "large": ("facebook/dinov2-large", 1024),
        "giant": ("facebook/dinov2-giant", 1536),
    }

    def __init__(
        self,
        variant: str = "large",
        device: Optional[str] = None,
        pooling: PoolingMode = "cls"
    ):
        """
        Initialize the DINOv2 encoder.

        Args:
            variant: Model variant ("small", "base", "large", or "giant").
            device: Device to use (None for auto-detection).
            pooling: Pooling strategy ("cls", "mean", "max", "cls_mean").
        """
        super().__init__(device, pooling)

        if variant not in self.VARIANTS:
            raise ValueError(
                f"Unknown variant: {variant}. "
                f"Available: {list(self.VARIANTS.keys())}"
            )

        self.variant = variant
        self.model_id, self.base_embedding_dim = self.VARIANTS[variant]
        self.embedding_dim = self.get_effective_embedding_dim()
        self.model_name = f"dinov2-{variant}"
        if pooling != "cls":
            self.model_name += f"-{pooling}"
        self.supports_patch_features = True

    def load_model(self) -> None:
        """Load the DINOv2 model from HuggingFace."""
        try:
            from transformers import AutoModel

            logger.info(f"Loading {self.model_name} from {self.model_id}...")

            # DINOv2 models are public, but HF_TOKEN can help with rate limits
            hf_token = os.environ.get("HF_TOKEN")
            if hf_token:
                logger.debug("Using HF_TOKEN for authentication")
                self.model = AutoModel.from_pretrained(self.model_id, token=hf_token)
            else:
                self.model = AutoModel.from_pretrained(self.model_id)

            self.model = self.model.to(self.device)
            self.model.train(False)

            self.is_loaded = True
            logger.info(
                f"Loaded {self.model_name} ({self.embedding_dim}-dim) on {self.device}"
            )

        except ImportError as e:
            raise RuntimeError(
                f"transformers library not installed. Run: pip install transformers\n"
                f"Error: {e}"
            )
        except OSError as e:
            raise RuntimeError(
                f"Failed to download DINOv2 model '{self.model_id}'. "
                f"Check your internet connection.\nError: {e}"
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to load DINOv2 model '{self.model_id}'.\nError: {e}"
            )

    @torch.no_grad()
    def extract_features(self, images: torch.Tensor) -> torch.Tensor:
        """
        Extract features from images using configured pooling strategy.

        Args:
            images: Batch of images (B, 3, H, W), ImageNet-normalized.

        Returns:
            Feature vectors (B, embedding_dim).

        Raises:
            RuntimeError: If inference fails (e.g., CUDA OOM).
        """
        self.ensure_loaded()

        try:
            images = images.to(self.device)
            outputs = self.model(pixel_values=images)

            # DINOv2 uses CLS token at index 0
            features = self.pool_patch_tokens(outputs.last_hidden_state, has_cls_token=True)

            return features.cpu()

        except torch.cuda.OutOfMemoryError as e:
            raise RuntimeError(
                f"GPU out of memory processing batch of {images.shape[0]} images. "
                f"Try reducing batch size.\nError: {e}"
            )
        except RuntimeError as e:
            if "CUDA" in str(e):
                raise RuntimeError(f"CUDA error during DINOv2 inference: {e}")
            raise
