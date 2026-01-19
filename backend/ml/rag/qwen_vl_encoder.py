"""Qwen3 VL Embedding encoder for document and query embeddings.

Qwen3 VL Embedding is a vision-language model designed for multimodal retrieval.
It produces 2048-dimensional embeddings for both images and text queries.

IMPORTANT: This model has asymmetric encoding modes:
- encode_document(image): For indexing PDF pages, FOV images, documents
- encode_query(text): For user search queries

References:
- HuggingFace: https://huggingface.co/Qwen/Qwen3-Embedding-VL
"""

import logging
from pathlib import Path
from typing import Optional, Union, List

import numpy as np
import torch
from PIL import Image

from ml.features.base_encoder import get_device

logger = logging.getLogger(__name__)

QWEN_VL_EMBEDDING_DIM = 2048  # Qwen3-VL-Embedding-2B output dimension
QWEN_VL_MODEL_ID = "Qwen/Qwen3-VL-Embedding-2B"  # Correct model ID from HuggingFace


class QwenVLEncoder:
    """
    Qwen3 VL Embedding encoder for multimodal document retrieval.

    Uses the Qwen3-Embedding-VL model to create embeddings for:
    - Document images (PDF pages, microscopy FOV, etc.)
    - Text queries

    The model uses asymmetric encoding - documents and queries are encoded
    differently for optimal retrieval performance.

    Attributes:
        device: Computation device (CUDA preferred for performance)
        model_name: Human-readable model name for DB storage
        embedding_dim: Output embedding dimension (2048)
    """

    EMBEDDING_DIM = QWEN_VL_EMBEDDING_DIM
    MODEL_ID = QWEN_VL_MODEL_ID

    def __init__(self, device: Optional[str] = None):
        """
        Initialize the Qwen VL encoder.

        Args:
            device: Device to use ("cuda", "mps", "cpu", or None for auto).
        """
        if device is None:
            self.device = get_device()
        else:
            self.device = torch.device(device)

        self.model = None
        self.processor = None
        self.model_name = "qwen3-vl-embedding"
        self.embedding_dim = self.EMBEDDING_DIM
        self.is_loaded = False

    def load_model(self) -> None:
        """Load the Qwen3 VL Embedding model."""
        if self.is_loaded:
            return

        try:
            from transformers import AutoModel, AutoProcessor

            logger.info(f"Loading Qwen3 VL Embedding model on {self.device}...")

            # Load processor
            self.processor = AutoProcessor.from_pretrained(
                self.MODEL_ID,
                trust_remote_code=True,
            )

            # Load model in fp16 for memory efficiency on GPU
            dtype = torch.float16 if self.device.type == "cuda" else torch.float32
            self.model = AutoModel.from_pretrained(
                self.MODEL_ID,
                torch_dtype=dtype,
                trust_remote_code=True,
            )
            self.model = self.model.to(self.device)
            self.model.eval()  # Set to evaluation mode

            self.is_loaded = True
            logger.info(f"Qwen3 VL Embedding loaded successfully on {self.device}")

        except ImportError as e:
            raise RuntimeError(
                f"Required packages not installed. Install with: "
                f"pip install transformers qwen-vl-utils\n"
                f"Error: {e}"
            ) from e

        except torch.cuda.OutOfMemoryError as e:
            # Try CPU fallback
            logger.warning(f"GPU OOM, attempting CPU fallback: {e}")
            torch.cuda.empty_cache()
            self._load_on_cpu()

        except Exception as e:
            logger.exception("Failed to load Qwen VL model")
            raise RuntimeError(
                f"Failed to load Qwen VL model: {type(e).__name__}: {e}"
            ) from e

    def _load_on_cpu(self) -> None:
        """Fallback to CPU loading when GPU memory is insufficient."""
        try:
            from transformers import AutoModel, AutoProcessor

            logger.info("Loading Qwen3 VL Embedding on CPU (fallback)...")

            self.device = torch.device("cpu")

            self.processor = AutoProcessor.from_pretrained(
                self.MODEL_ID,
                trust_remote_code=True,
            )

            self.model = AutoModel.from_pretrained(
                self.MODEL_ID,
                torch_dtype=torch.float32,
                trust_remote_code=True,
            )
            self.model.eval()  # Set to evaluation mode

            self.is_loaded = True
            logger.info("Qwen3 VL Embedding loaded on CPU (slower but functional)")

        except Exception as e:
            raise RuntimeError(
                f"Failed to load Qwen VL model on CPU: {type(e).__name__}: {e}"
            ) from e

    def ensure_loaded(self) -> None:
        """Ensure the model is loaded before use."""
        if not self.is_loaded:
            self.load_model()

    @torch.no_grad()
    def encode_document(
        self,
        image: Union[Image.Image, str, Path],
    ) -> np.ndarray:
        """
        Encode a document image to a 2048-dim embedding vector.

        Use this for indexing PDF pages, FOV images, and other documents.

        Args:
            image: PIL Image, file path, or Path object.

        Returns:
            2048-dimensional numpy array.

        Raises:
            ValueError: If image cannot be loaded.
            RuntimeError: If encoding fails.
        """
        self.ensure_loaded()

        # Load image if path provided
        if isinstance(image, (str, Path)):
            image = Image.open(image).convert("RGB")
        elif isinstance(image, Image.Image):
            image = image.convert("RGB")
        else:
            raise ValueError(f"Unsupported image type: {type(image)}")

        try:
            # Resize large images to avoid OOM
            max_size = 1024
            if max(image.size) > max_size:
                ratio = max_size / max(image.size)
                new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            # Process image through the processor
            # Use simple prompt for document embedding
            prompt = "<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>Describe this document image.<|im_end|>\n<|im_start|>assistant\n"

            inputs = self.processor(
                text=[prompt],
                images=[image],
                padding=True,
                return_tensors="pt",
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Get embeddings from the model's hidden states
            outputs = self.model(**inputs, output_hidden_states=True)

            # Use the last hidden state's mean as embedding
            hidden_states = outputs.hidden_states[-1]  # (batch, seq_len, hidden_dim)

            # Mean pool over sequence dimension (excluding padding if present)
            if "attention_mask" in inputs:
                mask = inputs["attention_mask"].unsqueeze(-1)  # (batch, seq_len, 1)
                masked_hidden = hidden_states * mask
                embedding = masked_hidden.sum(dim=1) / mask.sum(dim=1)  # (batch, hidden_dim)
            else:
                embedding = hidden_states.mean(dim=1)  # (batch, hidden_dim)

            embedding = embedding.squeeze(0)

            # Normalize the embedding
            embedding = embedding / (embedding.norm() + 1e-8)

            # Handle dimension mismatch - truncate or pad to target dimension
            hidden_dim = embedding.shape[0]
            if hidden_dim == self.EMBEDDING_DIM:
                result = embedding
            elif hidden_dim > self.EMBEDDING_DIM:
                result = embedding[:self.EMBEDDING_DIM]
            else:
                result = torch.zeros(self.EMBEDDING_DIM, device=self.device, dtype=embedding.dtype)
                result[:hidden_dim] = embedding

            return result.cpu().float().numpy()

        except torch.cuda.OutOfMemoryError as e:
            torch.cuda.empty_cache()
            raise RuntimeError(
                f"GPU out of memory encoding document. "
                f"Try smaller images or use CPU. Error: {e}"
            ) from e

        except Exception as e:
            logger.exception("Failed to encode document")
            raise RuntimeError(
                f"Failed to encode document: {type(e).__name__}: {e}"
            ) from e

    @torch.no_grad()
    def encode_query(self, text: str) -> np.ndarray:
        """
        Encode a text query to a 2048-dim embedding vector.

        Use this for user search queries.

        Args:
            text: Query text string.

        Returns:
            2048-dimensional numpy array.

        Raises:
            ValueError: If text is empty.
            RuntimeError: If encoding fails.
        """
        self.ensure_loaded()

        if not text or not text.strip():
            raise ValueError("Empty query text provided")

        try:
            # Format for text query (text-only mode)
            prompt = f"<|im_start|>user\n{text.strip()}<|im_end|>\n<|im_start|>assistant\n"

            inputs = self.processor(
                text=[prompt],
                padding=True,
                return_tensors="pt",
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Get embeddings from the model's hidden states
            outputs = self.model(**inputs, output_hidden_states=True)

            # Use the last hidden state's mean as embedding
            hidden_states = outputs.hidden_states[-1]

            # Mean pool over sequence dimension (excluding padding if present)
            if "attention_mask" in inputs:
                mask = inputs["attention_mask"].unsqueeze(-1)
                masked_hidden = hidden_states * mask
                embedding = masked_hidden.sum(dim=1) / mask.sum(dim=1)
            else:
                embedding = hidden_states.mean(dim=1)

            embedding = embedding.squeeze(0)

            # Normalize
            embedding = embedding / (embedding.norm() + 1e-8)

            # Handle dimension mismatch
            hidden_dim = embedding.shape[0]
            if hidden_dim == self.EMBEDDING_DIM:
                result = embedding
            elif hidden_dim > self.EMBEDDING_DIM:
                result = embedding[:self.EMBEDDING_DIM]
            else:
                result = torch.zeros(self.EMBEDDING_DIM, device=self.device, dtype=embedding.dtype)
                result[:hidden_dim] = embedding

            return result.cpu().float().numpy()

        except torch.cuda.OutOfMemoryError as e:
            torch.cuda.empty_cache()
            raise RuntimeError(
                f"GPU out of memory encoding query. Error: {e}"
            ) from e

        except Exception as e:
            logger.exception(f"Failed to encode query: {text[:50]}...")
            raise RuntimeError(
                f"Failed to encode query: {type(e).__name__}: {e}"
            ) from e

    @torch.no_grad()
    def encode_documents_batch(
        self,
        images: List[Union[Image.Image, str, Path]],
        batch_size: int = 4,
    ) -> List[np.ndarray]:
        """
        Encode multiple document images in batches.

        Args:
            images: List of PIL Images or file paths.
            batch_size: Number of images to process at once.

        Returns:
            List of 2048-dimensional numpy arrays.
        """
        self.ensure_loaded()

        results = []
        for i in range(0, len(images), batch_size):
            batch = images[i:i + batch_size]
            for img in batch:
                try:
                    embedding = self.encode_document(img)
                    results.append(embedding)
                except Exception as e:
                    logger.warning(f"Failed to encode image {i}: {e}")
                    # Return zero embedding for failed images
                    results.append(np.zeros(self.EMBEDDING_DIM, dtype=np.float32))

        return results

    def reset(self) -> None:
        """Reset the encoder and release model from memory."""
        if self.model is None:
            return

        del self.model
        del self.processor
        self.model = None
        self.processor = None
        self.is_loaded = False

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info("Qwen VL encoder reset")


# Global Qwen VL encoder instance (lazy loaded singleton)
_qwen_vl_encoder: Optional[QwenVLEncoder] = None


def get_qwen_vl_encoder() -> QwenVLEncoder:
    """Get or create the global Qwen VL encoder instance."""
    global _qwen_vl_encoder
    if _qwen_vl_encoder is None:
        logger.info("Initializing Qwen VL encoder (first use)...")
        _qwen_vl_encoder = QwenVLEncoder()
    return _qwen_vl_encoder


def reset_qwen_vl_encoder() -> None:
    """Reset the global Qwen VL encoder instance."""
    global _qwen_vl_encoder
    if _qwen_vl_encoder is not None:
        _qwen_vl_encoder.reset()
        _qwen_vl_encoder = None
        logger.info("Qwen VL encoder reset - will reload on next use")
