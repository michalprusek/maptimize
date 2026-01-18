"""Protein embedding computation service.

Handles ESM-C 600M embedding computation for protein sequences
and stores results in the database.
"""

import logging
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.image import MapProtein
from ml.features.esmc_encoder import get_esmc_encoder, parse_fasta_sequence

logger = logging.getLogger(__name__)


def _update_protein_embedding(
    protein: MapProtein,
    embedding: np.ndarray,
    model_name: str,
    sequence_length: int,
) -> None:
    """Update protein record with embedding and invalidate UMAP coordinates."""
    protein.embedding = embedding.tolist()
    protein.embedding_model = model_name
    protein.embedding_computed_at = datetime.now(timezone.utc)
    protein.sequence_length = sequence_length
    # Invalidate UMAP (will be recomputed on next visualization)
    protein.umap_x = None
    protein.umap_y = None
    protein.umap_computed_at = None


async def compute_protein_embedding(
    protein_id: int,
    db: AsyncSession,
) -> dict:
    """
    Compute ESM-C 600M embedding for a protein's FASTA sequence.

    Args:
        protein_id: ID of the protein to compute embedding for.
        db: Database session.

    Returns:
        Dict with success status, embedding info, or error message.

    Raises:
        ValueError: If protein not found or has no FASTA sequence.
    """
    # Get protein
    result = await db.execute(
        select(MapProtein).where(MapProtein.id == protein_id)
    )
    protein = result.scalar_one_or_none()

    if not protein:
        raise ValueError(f"Protein with ID {protein_id} not found")

    if not protein.fasta_sequence:
        raise ValueError(
            f"Protein '{protein.name}' has no FASTA sequence. "
            "Add a sequence before computing embedding."
        )

    # Parse and validate sequence
    clean_sequence = parse_fasta_sequence(protein.fasta_sequence)

    logger.info(
        f"Computing ESM-C embedding for protein '{protein.name}' "
        f"(length: {len(clean_sequence)} aa)"
    )

    # Compute embedding
    encoder = get_esmc_encoder()
    embedding = encoder.encode_sequence(protein.fasta_sequence)

    # Update protein record
    _update_protein_embedding(protein, embedding, encoder.model_name, len(clean_sequence))

    await db.commit()

    logger.info(
        f"Computed embedding for protein '{protein.name}': "
        f"{len(embedding)}-dim vector"
    )

    return {
        "success": True,
        "protein_id": protein_id,
        "protein_name": protein.name,
        "sequence_length": len(clean_sequence),
        "embedding_dim": len(embedding),
        "embedding_model": encoder.model_name,
        "computed_at": protein.embedding_computed_at.isoformat(),
    }


async def batch_compute_protein_embeddings(
    db: AsyncSession,
    force_recompute: bool = False,
) -> dict:
    """
    Compute embeddings for all proteins with FASTA sequences.

    Args:
        db: Database session.
        force_recompute: If True, recompute even if embedding exists.

    Returns:
        Dict with counts of computed, skipped, and failed proteins.
    """
    # Get proteins with FASTA sequences
    query = select(MapProtein).where(MapProtein.fasta_sequence.isnot(None))

    if not force_recompute:
        query = query.where(MapProtein.embedding.is_(None))

    result = await db.execute(query)
    proteins = result.scalars().all()

    if not proteins:
        return {
            "computed": 0,
            "failed": 0,
            "message": "No proteins need embedding computation",
        }

    computed = 0
    failed = 0
    errors: list[dict] = []

    encoder = get_esmc_encoder()

    for protein in proteins:
        try:
            clean_sequence = parse_fasta_sequence(protein.fasta_sequence)
            embedding = encoder.encode_sequence(protein.fasta_sequence)
            _update_protein_embedding(protein, embedding, encoder.model_name, len(clean_sequence))
            computed += 1
            logger.info(f"Computed embedding for '{protein.name}'")

        except (KeyboardInterrupt, SystemExit):
            # Re-raise system-level errors
            raise
        except RuntimeError as e:
            error_str = str(e).lower()
            # Check for GPU OOM errors - should abort batch
            if "out of memory" in error_str or "cuda" in error_str:
                logger.error(f"GPU out of memory processing '{protein.name}': {e}")
                errors.append({
                    "protein": protein.name,
                    "error": str(e),
                    "error_type": "gpu_oom",
                    "retryable": False
                })
                # Abort batch on OOM - don't continue processing
                break
            else:
                failed += 1
                errors.append({
                    "protein": protein.name,
                    "error": str(e),
                    "error_type": "runtime_error",
                    "retryable": True
                })
                logger.error(f"Failed to compute embedding for '{protein.name}': {e}")
        except ValueError as e:
            # Validation errors (bad sequence) - not retryable
            failed += 1
            errors.append({
                "protein": protein.name,
                "error": str(e),
                "error_type": "validation_error",
                "retryable": False
            })
            logger.error(f"Invalid sequence for '{protein.name}': {e}")
        except Exception as e:
            failed += 1
            errors.append({
                "protein": protein.name,
                "error": str(e),
                "error_type": "unknown",
                "retryable": True
            })
            logger.error(f"Failed to compute embedding for '{protein.name}': {e}")

    await db.commit()

    return {
        "computed": computed,
        "failed": failed,
        "errors": errors or None,
        "message": f"Computed {computed} embeddings, {failed} failed",
    }
