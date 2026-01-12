"""DINOv3 encoder for cell crop feature extraction."""

import logging
import os
from typing import Optional
import torch

from .base_encoder import BaseEncoder, PoolingMode

logger = logging.getLogger(__name__)


class DINOv3Encoder(BaseEncoder):
    """
    DINOv3 encoder with configurable pooling.

    DINOv3 is Meta's latest self-supervised Vision Transformer, significantly
    improved over DINOv2 with better scaling and representation quality.

    Note: This model requires HuggingFace authentication.
    Run `huggingface-cli login` and accept the model terms at:
    https://huggingface.co/facebook/dinov3-vits16-pretrain-lvd1689m

    Variants:
        - small: 384-dim, ViT-S/16
        - base: 768-dim, ViT-B/16
        - large: 1024-dim, ViT-L/16
    """

    VARIANTS = {
        "small": ("facebook/dinov3-vits16-pretrain-lvd1689m", 384),
        "base": ("facebook/dinov3-vitb16-pretrain-lvd1689m", 768),
        "large": ("facebook/dinov3-vitl16-pretrain-lvd1689m", 1024),
    }

    def __init__(
        self,
        variant: str = "large",
        device: Optional[str] = None,
        pooling: PoolingMode = "cls"
    ):
        """
        Initialize the DINOv3 encoder.

        Args:
            variant: Model variant ("small", "base", or "large").
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
        self.model_name = f"dinov3-{variant}"
        if pooling != "cls":
            self.model_name += f"-{pooling}"
        self.supports_patch_features = True

    def load_model(self) -> None:
        """Load the DINOv3 model from HuggingFace."""
        try:
            from transformers import AutoModel

            logger.info(f"Loading {self.model_name} from {self.model_id}...")
            logger.info("Note: DINOv3 requires transformers >= 4.56")

            # Use HF_TOKEN from environment for gated models
            hf_token = os.environ.get("HF_TOKEN")
            if hf_token:
                logger.info("Using HF_TOKEN for authentication")
                self.model = AutoModel.from_pretrained(self.model_id, token=hf_token)
            else:
                logger.warning("HF_TOKEN not set - model may fail to load if gated")
                self.model = AutoModel.from_pretrained(self.model_id)

            self.model = self.model.to(self.device)
            self.model.train(False)

            self.is_loaded = True
            logger.info(
                f"Loaded {self.model_name} ({self.embedding_dim}-dim) on {self.device}"
            )

        except Exception as e:
            raise RuntimeError(
                f"Failed to load DINOv3. Ensure you have:\n"
                f"1. transformers >= 4.56 installed\n"
                f"2. HF_TOKEN environment variable set\n"
                f"3. Accepted model terms on HuggingFace\n"
                f"Error: {e}"
            )

    @torch.no_grad()
    def extract_features(self, images: torch.Tensor) -> torch.Tensor:
        """
        Extract features from images using configured pooling strategy.

        Args:
            images: Batch of images (B, 3, H, W), ImageNet-normalized.

        Returns:
            Feature vectors (B, embedding_dim).
        """
        self.ensure_loaded()

        images = images.to(self.device)
        outputs = self.model(pixel_values=images)

        # DINOv3 may have register tokens; CLS is still at index 0
        features = self.pool_patch_tokens(outputs.last_hidden_state, has_cls_token=True)

        return features.cpu()
