"""ESM-C 600M encoder for protein sequence embeddings.

ESM-C (Evolutionary Scale Model - Cambrian) is EvolutionaryScale's best protein
representation learning model. The 600M parameter version rivals ESM2-3B and
approaches the capabilities of the 15B model.

Architecture:
- 36 layers, 1152 hidden units, 18 attention heads
- Pre-LN, rotary embeddings, SwiGLU activations
- No biases in linear layers or layer norms

Output: 1152-dimensional embeddings using mean pooling over sequence tokens.

References:
- GitHub: https://github.com/evolutionaryscale/esm
- Blog: https://www.evolutionaryscale.ai/blog/esm-cambrian
"""

import logging
import re
from typing import Optional

import numpy as np
import torch

from .base_encoder import get_device

logger = logging.getLogger(__name__)

ESMC_EMBEDDING_DIM = 1152  # ESM-C 600M output dimension


def parse_fasta_sequence(fasta_text: str) -> str:
    """
    Parse FASTA format and extract the amino acid sequence.

    Handles both raw sequences and full FASTA format with header.

    Args:
        fasta_text: Raw sequence or FASTA-formatted text.

    Returns:
        Clean amino acid sequence (uppercase, whitespace removed).

    Raises:
        ValueError: If sequence is empty or contains invalid characters.
    """
    if not fasta_text or not fasta_text.strip():
        raise ValueError("Empty sequence provided")

    lines = fasta_text.strip().split("\n")

    # Filter out header lines (starting with >)
    sequence_lines = [
        line.strip() for line in lines
        if not line.strip().startswith(">")
    ]

    sequence = "".join(sequence_lines).upper()

    # Remove any whitespace
    sequence = re.sub(r"\s+", "", sequence)

    if not sequence:
        raise ValueError("No sequence found in FASTA input")

    # Validate amino acid characters (standard 20 + X for unknown)
    valid_aa = set("ACDEFGHIKLMNPQRSTVWYX")
    invalid_chars = set(sequence) - valid_aa
    if invalid_chars:
        raise ValueError(
            f"Invalid characters in sequence: {invalid_chars}. "
            f"Expected standard amino acids (A-Z except B, J, O, U, Z) or X."
        )

    return sequence


class ESMCEncoder:
    """
    ESM-C 600M encoder for protein sequence embeddings.

    Uses mean pooling over sequence tokens to produce a single 1152-dim vector.

    Attributes:
        device: Computation device (cuda preferred for performance)
        model_name: Human-readable model name for DB storage
        embedding_dim: Output embedding dimension (1152)
    """

    EMBEDDING_DIM = ESMC_EMBEDDING_DIM
    MODEL_ID = "esmc_600m"

    def __init__(self, device: Optional[str] = None):
        """
        Initialize the ESM-C encoder.

        Args:
            device: Device to use ("cuda", "mps", "cpu", or None for auto).
        """
        if device is None:
            self.device = get_device()
        else:
            self.device = torch.device(device)

        self.model = None
        self.model_name = "esmc-600m"
        self.embedding_dim = self.EMBEDDING_DIM
        self.is_loaded = False

    def load_model(self) -> None:
        """Load the ESM-C 600M model."""
        if self.is_loaded:
            return

        try:
            from esm.models.esmc import ESMC

            logger.info(f"Loading ESM-C 600M model on {self.device}...")

            self.model = ESMC.from_pretrained(self.MODEL_ID)
            self.model = self.model.to(self.device)
            self.model.train(False)

            self.is_loaded = True
            logger.info(f"ESM-C 600M loaded successfully on {self.device}")

        except ImportError as e:
            raise RuntimeError(
                f"ESM package not installed. Install with: pip install esm\n"
                f"For better performance: pip install flash-attn --no-build-isolation\n"
                f"Error: {e}"
            ) from e

        except torch.cuda.OutOfMemoryError as e:
            raise RuntimeError(
                f"Not enough GPU memory for ESM-C 600M (~2.5GB required). "
                f"Error: {e}"
            ) from e

        except Exception as e:
            logger.exception("Failed to load ESM-C model")
            raise RuntimeError(
                f"Failed to load ESM-C model: {type(e).__name__}: {e}"
            ) from e

    def ensure_loaded(self) -> None:
        """Ensure the model is loaded before use."""
        if not self.is_loaded:
            self.load_model()

    @torch.no_grad()
    def encode_sequence(self, sequence: str) -> np.ndarray:
        """
        Encode a protein sequence to a 1152-dim embedding vector.

        Uses mean pooling over all sequence tokens (excluding special tokens).

        Args:
            sequence: Amino acid sequence (raw or FASTA format).

        Returns:
            1152-dimensional numpy array.

        Raises:
            ValueError: If sequence is invalid.
            RuntimeError: If encoding fails.
        """
        self.ensure_loaded()

        # Parse FASTA if needed
        clean_sequence = parse_fasta_sequence(sequence)

        try:
            from esm.sdk.api import ESMProtein, LogitsConfig

            # Create protein object
            protein = ESMProtein(sequence=clean_sequence)

            # Encode to tensor
            protein_tensor = self.model.encode(protein)

            # Get embeddings via logits call with return_embeddings=True
            logits_output = self.model.logits(
                protein_tensor,
                LogitsConfig(sequence=True, return_embeddings=True)
            )

            # embeddings shape: (1, seq_len, 1152)
            embeddings = logits_output.embeddings

            # Mean pooling over sequence length dimension
            # Skip first and last token (BOS/EOS) if present
            if embeddings.shape[1] > 2:
                # Mean over actual sequence tokens
                mean_embedding = embeddings[:, 1:-1, :].mean(dim=1)
            else:
                mean_embedding = embeddings.mean(dim=1)

            return mean_embedding.squeeze(0).cpu().numpy()

        except torch.cuda.OutOfMemoryError as e:
            torch.cuda.empty_cache()
            raise RuntimeError(
                f"GPU out of memory encoding sequence of length {len(clean_sequence)}. "
                f"Try a shorter sequence or use CPU. Error: {e}"
            ) from e

        except Exception as e:
            logger.exception(f"Failed to encode sequence of length {len(clean_sequence)}")
            raise RuntimeError(
                f"Failed to encode protein sequence: {type(e).__name__}: {e}"
            ) from e

    def reset(self) -> None:
        """Reset the encoder and release model from memory."""
        if self.model is None:
            return

        del self.model
        self.model = None
        self.is_loaded = False

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info("ESM-C encoder reset")


# Global ESM-C encoder instance (lazy loaded singleton)
_esmc_encoder: Optional[ESMCEncoder] = None


def get_esmc_encoder() -> ESMCEncoder:
    """Get or create the global ESM-C encoder instance."""
    global _esmc_encoder
    if _esmc_encoder is None:
        logger.info("Initializing ESM-C encoder (first use)...")
        _esmc_encoder = ESMCEncoder()
    return _esmc_encoder


def reset_esmc_encoder() -> None:
    """Reset the global ESM-C encoder instance."""
    global _esmc_encoder
    if _esmc_encoder is not None:
        _esmc_encoder.reset()
        _esmc_encoder = None
        logger.info("ESM-C encoder reset - will reload on next use")
