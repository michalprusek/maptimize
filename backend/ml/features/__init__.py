"""Feature extraction module for cell crop embeddings (DINOv2/DINOv3)."""

from .dinov2_encoder import DINOv2Encoder
from .dinov3_encoder import DINOv3Encoder
from .feature_extractor import (
    FeatureExtractor,
    extract_features_for_crops,
    get_encoder,
    reset_encoder,
)

__all__ = [
    "DINOv2Encoder",
    "DINOv3Encoder",
    "FeatureExtractor",
    "extract_features_for_crops",
    "get_encoder",
    "reset_encoder",
]
