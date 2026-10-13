"""Bundleness scorer: a linear read-out of the DINOv3 crop embedding.

``model`` is everything inference needs (numpy only). ``training`` fits a new
version and is imported by ``scripts/train_bundleness.py`` alone, so the API
process never pays for scikit-learn.
"""

from .model import BundlenessModel, load_model

__all__ = ["BundlenessModel", "load_model"]
