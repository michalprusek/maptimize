"""Register all ML models with the GPU Model Manager.

Called once at application startup from main.py lifespan.
Uses lazy imports to avoid loading heavy ML dependencies at import time.
"""

import logging
from typing import List, Tuple

from ml.gpu_manager import get_gpu_manager

logger = logging.getLogger(__name__)

# Each entry: (name, module_path, get_fn_name, reset_fn_name, estimated_vram_mb)
_MODEL_DEFINITIONS: List[Tuple[str, str, str, str, int]] = [
    ("yolov8",   "ml.detection.detector",          "_get_detector_raw",           "reset_detector",           100),
    ("mobilesam", "ml.segmentation.sam_encoder",    "_get_mobilesam_encoder_raw",  "reset_mobilesam_encoder",  200),
    ("sam3",     "ml.segmentation.sam3_encoder",    "_get_sam3_encoder_raw",       "reset_sam3_encoder",       3000),
    ("qwen_vl",  "ml.rag.qwen_vl_encoder",         "_get_qwen_vl_encoder_raw",    "reset_qwen_vl_encoder",    4000),
    ("dinov3",   "ml.features.feature_extractor",   "_get_encoder_raw",            "reset_encoder",            1500),
    ("esmc",     "ml.features.esmc_encoder",        "_get_esmc_encoder_raw",       "reset_esmc_encoder",       2500),
]


def _make_lazy_caller(module_path: str, fn_name: str):
    """Create a function that lazily imports module_path and calls fn_name."""
    def caller():
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, fn_name)()
    return caller


def register_all_models() -> None:
    """Register all ML models for GPU lifecycle management."""
    manager = get_gpu_manager()

    for name, module_path, get_fn_name, reset_fn_name, vram_mb in _MODEL_DEFINITIONS:
        manager.register(
            name=name,
            get_fn=_make_lazy_caller(module_path, get_fn_name),
            reset_fn=_make_lazy_caller(module_path, reset_fn_name),
            estimated_vram_mb=vram_mb,
        )

    logger.info("Registered %d models with GPU manager", manager.model_count)
