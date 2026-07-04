"""RAG (Retrieval-Augmented Generation) ML models.

The Qwen VL encoder pulls in torch/transformers, so it is imported lazily via
module ``__getattr__``. Importing this package for the lightweight
``QWEN_VL_EMBEDDING_DIM`` constant (as the SQLAlchemy models do) therefore does
NOT load the ML stack.
"""

from .constants import QWEN_VL_EMBEDDING_DIM

__all__ = [
    "QwenVLEncoder",
    "get_qwen_vl_encoder",
    "reset_qwen_vl_encoder",
    "QWEN_VL_EMBEDDING_DIM",
]

_LAZY_ENCODER_EXPORTS = {
    "QwenVLEncoder",
    "get_qwen_vl_encoder",
    "reset_qwen_vl_encoder",
}


def __getattr__(name: str):
    """Load the torch-backed encoder only when actually requested (PEP 562)."""
    if name in _LAZY_ENCODER_EXPORTS:
        from . import qwen_vl_encoder

        return getattr(qwen_vl_encoder, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
