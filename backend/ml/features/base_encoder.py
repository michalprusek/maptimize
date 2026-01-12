"""Abstract base class for feature encoders."""

from abc import ABC, abstractmethod
from typing import Optional, Literal
import torch


PoolingMode = Literal["cls", "mean", "max", "cls_mean"]


def get_device() -> torch.device:
    """Auto-detect the best available device."""
    if torch.backends.mps.is_available():
        return torch.device("mps")
    elif torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


class BaseEncoder(ABC):
    """
    Abstract base class for feature extraction encoders.

    Subclasses must implement:
    - load_model(): Load the model and processor
    - extract_features(): Extract features from a batch of images
    """

    def __init__(
        self,
        device: Optional[str] = None,
        pooling: PoolingMode = "cls"
    ):
        """
        Initialize the encoder.

        Args:
            device: Device to use ("cuda", "mps", "cpu", or None for auto).
            pooling: Pooling strategy for feature extraction.
        """
        if device is None:
            self.device = get_device()
        else:
            self.device = torch.device(device)

        self.model = None
        self.processor = None
        self.embedding_dim: int = 0
        self.base_embedding_dim: int = 0
        self.model_name: str = "base"
        self.is_loaded: bool = False
        self.pooling: PoolingMode = pooling
        self.supports_patch_features: bool = False

    @abstractmethod
    def load_model(self) -> None:
        """Load the model and processor."""
        pass

    @abstractmethod
    def extract_features(self, images: torch.Tensor) -> torch.Tensor:
        """
        Extract features from a batch of preprocessed images.

        Args:
            images: Batch of images (B, C, H, W), ImageNet normalized.

        Returns:
            Feature vectors (B, embedding_dim).
        """
        pass

    def get_embedding_dim(self) -> int:
        """Return the dimension of output feature vectors."""
        return self.embedding_dim

    def ensure_loaded(self) -> None:
        """Ensure the model is loaded before use."""
        if not self.is_loaded:
            self.load_model()

    def __call__(self, images: torch.Tensor) -> torch.Tensor:
        """Extract features (shorthand for extract_features)."""
        self.ensure_loaded()
        return self.extract_features(images)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(model_name={self.model_name!r}, "
            f"embedding_dim={self.embedding_dim}, pooling={self.pooling!r}, "
            f"device={self.device})"
        )

    def pool_patch_tokens(
        self,
        last_hidden_state: torch.Tensor,
        has_cls_token: bool = True
    ) -> torch.Tensor:
        """
        Apply pooling strategy to transformer hidden states.

        Args:
            last_hidden_state: Hidden states from transformer (B, N, D).
            has_cls_token: Whether the first token is a CLS token.

        Returns:
            Pooled features (B, embedding_dim).
        """
        if self.pooling == "cls":
            if has_cls_token:
                return last_hidden_state[:, 0, :]
            return last_hidden_state.mean(dim=1)

        elif self.pooling == "mean":
            if has_cls_token:
                patch_tokens = last_hidden_state[:, 1:, :]
            else:
                patch_tokens = last_hidden_state
            return patch_tokens.mean(dim=1)

        elif self.pooling == "max":
            if has_cls_token:
                patch_tokens = last_hidden_state[:, 1:, :]
            else:
                patch_tokens = last_hidden_state
            return patch_tokens.max(dim=1).values

        elif self.pooling == "cls_mean":
            if has_cls_token:
                cls_token = last_hidden_state[:, 0, :]
                patch_tokens = last_hidden_state[:, 1:, :]
                mean_pooled = patch_tokens.mean(dim=1)
                return torch.cat([cls_token, mean_pooled], dim=1)
            mean_pooled = last_hidden_state.mean(dim=1)
            return torch.cat([mean_pooled, mean_pooled], dim=1)

        raise ValueError(f"Unknown pooling mode: {self.pooling}")

    def get_effective_embedding_dim(self) -> int:
        """Get the effective embedding dimension after pooling."""
        if self.pooling == "cls_mean":
            return self.base_embedding_dim * 2
        return self.base_embedding_dim
