"""Feature extraction module for cell crop embeddings (DINOv2/DINOv3) and protein sequences (ESM-C)."""

from .dinov2_encoder import DINOv2Encoder
from .dinov3_encoder import DINOv3Encoder
from .esmc_encoder import (
    ESMCEncoder,
    get_esmc_encoder,
    reset_esmc_encoder,
    parse_fasta_sequence,
    ESMC_EMBEDDING_DIM,
)
from .feature_extractor import (
    FeatureExtractor,
    extract_features_for_crops,
    extract_features_for_images,
    get_encoder,
    reset_encoder,
)

__all__ = [
    # Image encoders
    "DINOv2Encoder",
    "DINOv3Encoder",
    "FeatureExtractor",
    "extract_features_for_crops",
    "extract_features_for_images",
    "get_encoder",
    "reset_encoder",
    # Protein encoder
    "ESMCEncoder",
    "get_esmc_encoder",
    "reset_esmc_encoder",
    "parse_fasta_sequence",
    "ESMC_EMBEDDING_DIM",
]
