"""
SAM model factory - automatically selects the best model based on available hardware.

Device selection logic:
- CUDA GPU → SAM 3 (supports text prompts)
- MPS (Mac) → MobileSAM (lightweight, no text prompts)
- CPU → MobileSAM (fallback)

This allows the application to provide text prompting on servers with CUDA GPUs
while still working on Mac development machines with MobileSAM.
"""

import logging
from typing import Optional, Dict, Any, Protocol, List, Tuple
from enum import Enum

import torch

logger = logging.getLogger(__name__)


class SAMVariant(Enum):
    """Available SAM model variants."""
    MOBILE_SAM = "mobilesam"
    SAM3 = "sam3"


class SAMEncoderProtocol(Protocol):
    """Protocol defining the interface for SAM encoders."""

    model_name: str
    device: str
    supports_text_prompts: bool

    def ensure_loaded(self) -> None:
        """Ensure the model is loaded."""
        ...

    def reset(self) -> None:
        """Reset and release model from memory."""
        ...


def detect_device() -> str:
    """
    Detect the best available compute device.

    Returns:
        Device string: "cuda", "mps", or "cpu"
    """
    if torch.cuda.is_available():
        return "cuda"
    elif torch.backends.mps.is_available():
        return "mps"
    else:
        return "cpu"


def get_recommended_variant(device: Optional[str] = None) -> SAMVariant:
    """
    Get the recommended SAM variant for the current/specified device.

    Args:
        device: Optional device override. If None, auto-detects.

    Returns:
        Recommended SAMVariant
    """
    if device is None:
        device = detect_device()

    if device == "cuda":
        # CUDA → SAM 3 (full text prompting support)
        return SAMVariant.SAM3
    else:
        # MPS or CPU → MobileSAM (lightweight, reliable)
        return SAMVariant.MOBILE_SAM


def get_sam_encoder(variant: Optional[SAMVariant] = None):
    """
    Get the appropriate SAM encoder based on variant or auto-detection.

    Args:
        variant: Optional specific variant to use. If None, auto-selects
                 based on available hardware.

    Returns:
        SAM encoder instance (either SAMEncoder or SAM3Encoder)
    """
    if variant is None:
        variant = get_recommended_variant()

    logger.info(f"Getting SAM encoder: variant={variant.value}")

    if variant == SAMVariant.SAM3:
        from .sam3_encoder import get_sam3_encoder
        return get_sam3_encoder()
    else:
        from .sam_encoder import get_sam_encoder as get_mobilesam_encoder
        return get_mobilesam_encoder()


def get_sam_decoder(variant: Optional[SAMVariant] = None):
    """
    Get the appropriate SAM decoder based on variant or auto-detection.

    For SAM 3, the decoder is integrated into the encoder (Sam3Processor).
    For MobileSAM, we use the separate decoder.

    Args:
        variant: Optional specific variant to use.

    Returns:
        SAM decoder instance
    """
    if variant is None:
        variant = get_recommended_variant()

    if variant == SAMVariant.SAM3:
        # SAM 3 uses integrated processor, but we return encoder for API compatibility
        from .sam3_encoder import get_sam3_encoder
        return get_sam3_encoder()
    else:
        from .sam_decoder import get_sam_decoder as get_mobilesam_decoder
        return get_mobilesam_decoder()


def get_capabilities() -> Dict[str, Any]:
    """
    Get capabilities of the current SAM setup.

    Returns:
        Dict with:
            - device: Current compute device
            - variant: Selected SAM variant
            - supports_text_prompts: Whether text prompting is available
            - model_name: Human-readable model name
    """
    device = detect_device()
    variant = get_recommended_variant(device)

    capabilities = {
        "device": device,
        "variant": variant.value,
        "supports_text_prompts": variant == SAMVariant.SAM3,
        "model_name": "SAM 3" if variant == SAMVariant.SAM3 else "MobileSAM",
    }

    logger.info(f"SAM capabilities: {capabilities}")
    return capabilities


def text_segmentation_available() -> bool:
    """
    Check if text-based segmentation is available.

    Text segmentation requires SAM 3, which requires CUDA.

    Returns:
        True if text prompting is available
    """
    return get_recommended_variant() == SAMVariant.SAM3


# Convenience function for text segmentation
def segment_with_text(
    image_path: str,
    text_prompt: str,
    confidence_threshold: float = 0.5,
) -> Dict[str, Any]:
    """
    High-level function for text-based segmentation.

    Args:
        image_path: Path to image file
        text_prompt: Natural language description
        confidence_threshold: Minimum confidence (0.0-1.0)

    Returns:
        Dict with instances (masks, polygons, scores) or error

    Raises:
        RuntimeError: If text prompting is not available (no CUDA)
    """
    if not text_segmentation_available():
        return {
            "success": False,
            "error": "Text segmentation requires CUDA GPU. Current device does not support SAM 3.",
            "device": detect_device(),
        }

    from .sam3_encoder import get_sam3_encoder
    encoder = get_sam3_encoder()

    return encoder.predict_with_text(
        image_path=image_path,
        text_prompt=text_prompt,
        confidence_threshold=confidence_threshold,
    )
