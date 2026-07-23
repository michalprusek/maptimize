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
# NOTE: ml.features.esmc_encoder (torch/ESM-C) is imported lazily inside the
# functions below, so importing this module does not require the heavy `ml` extra.

logger = logging.getLogger(__name__)


class EncoderUnavailableError(RuntimeError):
    """The ESM-C encoder could not be loaded at all.

    Distinct from a per-sequence failure: no sequence will encode until the
    GPU or the weights are back, so the batch loop re-raises this instead of
    filing it against whichever protein happened to be next.
    """


def _update_protein_embedding(
    protein: MapProtein,
    embedding: "np.ndarray | list[float]",
    model_name: str,
    sequence_length: int,
) -> None:
    """Update protein record with embedding and invalidate UMAP coordinates."""
    # asarray() because the reuse path hands us a stored list rather than an
    # ndarray. The list -> float64 -> list round-trip is exact, which is what
    # keeps twins bit-identical for the UMAP duplicate collapse.
    protein.embedding = np.asarray(embedding).tolist()
    protein.embedding_model = model_name
    protein.embedding_computed_at = datetime.now(timezone.utc)
    protein.sequence_length = sequence_length
    # Invalidate UMAP (will be recomputed on next visualization)
    protein.umap_x = None
    protein.umap_y = None
    protein.umap_computed_at = None


async def _load_sequence_index(
    db: AsyncSession,
    exclude_ids: frozenset[int] = frozenset(),
) -> dict[str, MapProtein]:
    """Map parsed amino-acid sequence -> a protein already holding its embedding.

    ESM-C on GPU is not bit-reproducible: encoding one sequence twice returns
    vectors that differ by ~1e-5 (measured across every protein/"3D sim" twin in
    production). The difference is numerically meaningless, but it is enough that
    the two no longer compare equal, and anything downstream relying on equality
    — the UMAP duplicate collapse above all — then draws them as two different
    proteins. Reusing the stored vector makes identical sequences identical by
    construction, and skips a GPU load while doing so.

    ``exclude_ids`` holds the proteins about to be (re)computed. Without it a
    force-recompute would find each protein's own stale row and reuse it,
    quietly turning "recompute everything" into a no-op.

    The SQL predicates below are duplicated as Python checks on purpose. The
    guarantee is per-candidate, so it must not depend on a query that someone
    later narrows or widens; the duplication is also what lets the mocked-DB
    tests exercise the rules at all, since an AsyncMock ignores WHERE clauses.
    """
    from ml.features.esmc_encoder import ESMCEncoder, parse_fasta_sequence

    result = await db.execute(
        select(MapProtein).where(
            MapProtein.embedding.isnot(None),
            MapProtein.embedding_model == ESMCEncoder.MODEL_NAME,
            MapProtein.fasta_sequence.isnot(None),
        )
    )

    index: dict[str, MapProtein] = {}
    for candidate in result.scalars().all():
        if candidate.id in exclude_ids or not candidate.fasta_sequence:
            continue
        # A vector from another encoder is not interchangeable with this one.
        # (Width needs no check here: the column is Vector(1152), so pgvector
        # rejects a wrong-width vector at write time.)
        if candidate.embedding is None or candidate.embedding_model != ESMCEncoder.MODEL_NAME:
            continue
        try:
            sequence = parse_fasta_sequence(candidate.fasta_sequence)
        except ValueError as exc:
            # A malformed sequence on some other protein is that protein's
            # problem; it must not abort the lookup for everyone else. It is
            # still worth saying out loud: a stored embedding whose FASTA no
            # longer parses means that protein's UMAP position is stale.
            logger.warning(
                "Protein '%s' (id=%s) has a stored embedding but its FASTA no "
                "longer parses (%s); excluded from sequence reuse.",
                candidate.name, candidate.id, exc,
            )
            continue
        index.setdefault(sequence, candidate)
    return index


async def compute_protein_embedding(
    protein_id: int,
    db: AsyncSession,
    force: bool = False,
) -> dict:
    """
    Compute ESM-C 600M embedding for a protein's FASTA sequence.

    Args:
        protein_id: ID of the protein to compute embedding for.
        db: Database session.
        force: Encode from scratch even if another protein already holds a
            vector for this sequence. Without this there is no way back from a
            bad stored vector: the twin would be copied again on every retry,
            and the user has no way to tell reuse from computation. Same trap
            CLAUDE.md records for FAILED documents in the dedup path.

    Returns:
        Dict with success status, embedding info, or error message.

    Raises:
        ValueError: If protein not found or has no FASTA sequence.
    """
    from ml.features.esmc_encoder import get_esmc_encoder, parse_fasta_sequence

    # Get protein
    result = await db.execute(
        select(MapProtein).where(MapProtein.id == protein_id)
    )
    protein = result.scalar_one_or_none()

    if not protein:
        # LookupError -> 404 at the endpoint (distinct from ValueError -> 400)
        raise LookupError(f"Protein with ID {protein_id} not found")

    if not protein.fasta_sequence:
        raise ValueError(
            f"Protein '{protein.name}' has no FASTA sequence. "
            "Add a sequence before computing embedding."
        )

    # Parse and validate sequence
    clean_sequence = parse_fasta_sequence(protein.fasta_sequence)

    twin = None
    if not force:
        index = await _load_sequence_index(db, frozenset({protein_id}))
        twin = index.get(clean_sequence)
    if twin is not None:
        logger.info(
            f"Protein '{protein.name}' has the same sequence as '{twin.name}' - "
            f"reusing its embedding instead of re-encoding"
        )
        embedding = twin.embedding
        model_name = twin.embedding_model
    else:
        logger.info(
            f"Computing ESM-C embedding for protein '{protein.name}' "
            f"(length: {len(clean_sequence)} aa)"
        )
        encoder = get_esmc_encoder()
        embedding = encoder.encode_sequence(protein.fasta_sequence)
        model_name = encoder.model_name

    # Update protein record
    _update_protein_embedding(protein, embedding, model_name, len(clean_sequence))

    await db.commit()

    logger.info(
        f"Stored embedding for protein '{protein.name}': "
        f"{len(embedding)}-dim vector"
    )

    return {
        "success": True,
        "protein_id": protein_id,
        "protein_name": protein.name,
        "sequence_length": len(clean_sequence),
        "embedding_dim": len(embedding),
        "embedding_model": model_name,
        "reused_from": twin.name if twin is not None else None,
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
        Dict with ``computed``, ``reused`` and ``failed`` counts plus ``errors``
        and ``message``. Raises EncoderUnavailableError if the encoder cannot be
        loaded at all — that is not a per-protein failure and must not be
        reported as one.
    """
    from ml.features.esmc_encoder import get_esmc_encoder, parse_fasta_sequence

    # Get proteins with FASTA sequences
    query = select(MapProtein).where(MapProtein.fasta_sequence.isnot(None))

    if not force_recompute:
        query = query.where(MapProtein.embedding.is_(None))

    result = await db.execute(query)
    proteins = result.scalars().all()

    if not proteins:
        return {
            "computed": 0,
            "reused": 0,
            "failed": 0,
            "errors": None,
            "message": "No proteins need embedding computation",
        }

    computed = 0
    reused = 0
    failed = 0
    aborted = False
    errors: list[dict] = []

    # Proteins in this batch are excluded: on force_recompute they still carry
    # their old vector, and matching against it would skip the recompute.
    sequence_index = await _load_sequence_index(
        db, frozenset(p.id for p in proteins)
    )
    # Loaded on first real encode — a batch that is entirely duplicates needs no GPU.
    encoder = None

    for protein in proteins:
        try:
            clean_sequence = parse_fasta_sequence(protein.fasta_sequence)

            twin = sequence_index.get(clean_sequence)
            if twin is not None:
                _update_protein_embedding(
                    protein, twin.embedding, twin.embedding_model, len(clean_sequence)
                )
                reused += 1
                logger.info(
                    f"Reused '{twin.name}' embedding for '{protein.name}' "
                    f"(identical sequence)"
                )
                continue

            if encoder is None:
                # Acquiring the encoder is not a property of this protein. Let
                # it fail the whole batch rather than be filed as "invalid
                # sequence" against whichever protein happened to come first —
                # and re-tried, and re-filed, for every remaining one.
                try:
                    encoder = get_esmc_encoder()
                except Exception as exc:
                    raise EncoderUnavailableError(
                        f"ESM-C encoder could not be loaded "
                        f"({type(exc).__name__}: {exc}). No sequences were encoded."
                    ) from exc
            embedding = encoder.encode_sequence(protein.fasta_sequence)
            _update_protein_embedding(protein, embedding, encoder.model_name, len(clean_sequence))
            # Later proteins with this sequence copy the vector instead of
            # re-encoding it, so twins inside one batch end up identical.
            sequence_index[clean_sequence] = protein
            computed += 1
            logger.info(f"Computed embedding for '{protein.name}'")

        except (KeyboardInterrupt, SystemExit):
            # Re-raise system-level errors
            raise
        except EncoderUnavailableError:
            # Infrastructure, not this protein. Must precede the RuntimeError
            # handler below, which it would otherwise match.
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
                failed += 1
                aborted = True
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

    if aborted:
        # An aborted run must not report a clean tally: the proteins the loop
        # never reached are counted nowhere else.
        not_attempted = len(proteins) - computed - reused - failed
        message = (
            f"Aborted after GPU out of memory: computed {computed}, "
            f"reused {reused}, {failed} failed, {not_attempted} not attempted"
        )
    else:
        message = f"Computed {computed} embeddings, reused {reused}, {failed} failed"

    return {
        "computed": computed,
        "reused": reused,
        "failed": failed,
        "errors": errors or None,
        "message": message,
    }
