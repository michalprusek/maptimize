"""SAM-based segmentation services.

The SAM encoder/decoder pull in torch, so they are imported lazily via module
``__getattr__``. This keeps ``from ml.segmentation.utils import ...`` (numpy +
opencv only) free of the heavy ML stack, so the backend imports without the
``ml`` extra installed.
"""

__all__ = [
    "SAMEncoder",
    "get_sam_encoder",
    "reset_sam_encoder",
    "SAMDecoder",
    "get_sam_decoder",
]

_LAZY = {
    "SAMEncoder": "sam_encoder",
    "get_sam_encoder": "sam_encoder",
    "reset_sam_encoder": "sam_encoder",
    "SAMDecoder": "sam_decoder",
    "get_sam_decoder": "sam_decoder",
}


def __getattr__(name: str):
    """Import the torch-backed SAM modules only when actually requested (PEP 562)."""
    module = _LAZY.get(name)
    if module is not None:
        import importlib

        mod = importlib.import_module(f".{module}", __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
