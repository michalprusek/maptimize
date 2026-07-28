"""MAP Protein routes.

Shared reference data on the same `utils.reference_data` helpers as microscopes
and PTMs, with one difference worth knowing before you read the counts: a
protein's usage is counted in IMAGES (`Image.map_protein_id`), not experiments,
because the images of one experiment may each carry their own protein.
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from models.image import DEFAULT_PROTEINS, MapProtein, Image
from schemas.image import (
    MapProteinCreate,
    MapProteinUpdate,
    MapProteinDetailedResponse,
    UmapProteinPointResponse,
    UmapProteinDataResponse,
)
from utils.reference_data import (
    count_referencing,
    count_referencing_grouped,
    ensure_name_unique,
    get_or_404,
    pick_color,
)
from utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

LABEL = "Protein"


def empty_protein_umap(total_proteins: int) -> UmapProteinDataResponse:
    """A "nothing to plot" UMAP response.

    Both reasons for it — too few proteins, and too few *distinct* embeddings —
    must look the same to the client, so the shape is written once.
    """
    return UmapProteinDataResponse(
        points=[],
        total_proteins=total_proteins,
        silhouette_score=None,
        is_precomputed=False,
        computed_at=None,
    )


@router.get("", response_model=List[MapProteinDetailedResponse])
async def list_proteins(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all MAP proteins with detailed info including image counts."""
    result = await db.execute(
        select(MapProtein).order_by(MapProtein.name)
    )
    proteins = result.scalars().all()

    if not proteins:
        for p_data in DEFAULT_PROTEINS:
            db.add(MapProtein(**p_data))
        await db.commit()

        result = await db.execute(
            select(MapProtein).order_by(MapProtein.name)
        )
        proteins = result.scalars().all()

    image_counts = await count_referencing_grouped(db, Image.map_protein_id)

    return [
        MapProteinDetailedResponse.from_protein(p, image_counts.get(p.id, 0))
        for p in proteins
    ]


@router.post("", response_model=MapProteinDetailedResponse, status_code=status.HTTP_201_CREATED)
async def create_protein(
    data: MapProteinCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new MAP protein."""
    await ensure_name_unique(db, MapProtein, data.name, LABEL)

    values = data.model_dump()
    if not values.get("color"):
        values["color"] = await pick_color(db, MapProtein)

    protein = MapProtein(**values)
    db.add(protein)
    await db.commit()
    await db.refresh(protein)

    return MapProteinDetailedResponse.from_protein(protein, 0)


@router.get("/umap", response_model=UmapProteinDataResponse)
async def get_protein_umap(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get UMAP visualization data for proteins with embeddings."""
    from services.umap_service import (
        DegenerateEmbeddingsError,
        compute_protein_umap_online,
    )
    import numpy as np

    result = await db.execute(
        select(MapProtein)
        .where(MapProtein.embedding.isnot(None))
        .order_by(MapProtein.name)
    )
    proteins = result.scalars().all()

    if len(proteins) < 3:
        return empty_protein_umap(len(proteins))

    image_counts = await count_referencing_grouped(db, Image.map_protein_id)
    all_precomputed = all(p.umap_x is not None and p.umap_y is not None for p in proteins)

    if all_precomputed:
        points = [
            UmapProteinPointResponse(
                protein_id=p.id,
                name=p.name,
                x=p.umap_x,
                y=p.umap_y,
                color=p.color or "#888888",
                sequence_length=p.sequence_length,
                image_count=image_counts.get(p.id, 0),
            )
            for p in proteins
        ]
        computed_at = max(
            (p.umap_computed_at for p in proteins if p.umap_computed_at),
            default=None
        )
        return UmapProteinDataResponse(
            points=points,
            total_proteins=len(proteins),
            silhouette_score=None,
            is_precomputed=True,
            computed_at=computed_at.isoformat() if computed_at else None,
        )

    embeddings = np.array([p.embedding for p in proteins])
    try:
        projection, silhouette = compute_protein_umap_online(embeddings)
    except DegenerateEmbeddingsError:
        # Every protein shares one or two embeddings, so there is nothing to
        # project. Report "no data" rather than serving a made-up layout the
        # user would read as a real result.
        logger.warning(
            f"Protein UMAP not computable: {len(proteins)} proteins have "
            f"fewer than 3 distinct embeddings"
        )
        return empty_protein_umap(len(proteins))

    points = [
        UmapProteinPointResponse(
            protein_id=p.id,
            name=p.name,
            x=float(projection[i, 0]),
            y=float(projection[i, 1]),
            color=p.color or "#888888",
            sequence_length=p.sequence_length,
            image_count=image_counts.get(p.id, 0),
        )
        for i, p in enumerate(proteins)
    ]

    return UmapProteinDataResponse(
        points=points,
        total_proteins=len(proteins),
        silhouette_score=silhouette,
        is_precomputed=False,
        computed_at=None,
    )


@router.get("/suggested-color")
async def get_suggested_protein_color(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Suggest an unused colour to pre-fill the create form.

    Uses the same picker create relies on (SSOT), so the swatch shown up front is
    the colour the protein would actually get. It is only a suggestion — the user
    may change it — and it is NOT reserved, so two concurrent creates can still
    land on the same colour (accepted, exactly like the create path).
    """
    return {"color": await pick_color(db, MapProtein)}


# =============================================================================
# Protein CRUD by ID (must come after /umap + /suggested-color to avoid route conflict)
# =============================================================================


@router.get("/{protein_id}", response_model=MapProteinDetailedResponse)
async def get_protein(
    protein_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get protein details."""
    protein = await get_or_404(db, MapProtein, protein_id, LABEL)
    image_count = await count_referencing(db, Image.map_protein_id, protein_id)
    return MapProteinDetailedResponse.from_protein(protein, image_count)


@router.patch("/{protein_id}", response_model=MapProteinDetailedResponse)
async def update_protein(
    protein_id: int,
    data: MapProteinUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update a MAP protein."""
    protein = await get_or_404(db, MapProtein, protein_id, LABEL)

    if data.name and data.name != protein.name:
        await ensure_name_unique(db, MapProtein, data.name, LABEL, exclude_id=protein_id)

    update_data = data.model_dump(exclude_unset=True)

    # An explicitly null colour means "assign me an unused one" (the UI's Auto
    # button). Omitting the field entirely still means "leave it alone" — the
    # two must stay distinguishable, which is why exclude_unset is load-bearing.
    if "color" in update_data and not update_data["color"]:
        update_data["color"] = await pick_color(db, MapProtein)

    for field, value in update_data.items():
        setattr(protein, field, value)

    # If FASTA changed, invalidate embedding and UMAP
    if "fasta_sequence" in update_data:
        protein.embedding = None
        protein.embedding_model = None
        protein.embedding_computed_at = None
        protein.sequence_length = None
        protein.umap_x = None
        protein.umap_y = None
        protein.umap_computed_at = None

    await db.commit()
    await db.refresh(protein)

    image_count = await count_referencing(db, Image.map_protein_id, protein_id)
    return MapProteinDetailedResponse.from_protein(protein, image_count)


@router.delete("/{protein_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_protein(
    protein_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a MAP protein (only if no images are associated)."""
    protein = await get_or_404(db, MapProtein, protein_id, LABEL)
    image_count = await count_referencing(db, Image.map_protein_id, protein_id)

    if image_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete protein with {image_count} associated images"
        )

    await db.delete(protein)
    await db.commit()


@router.post("/{protein_id}/compute-embedding")
async def compute_protein_embedding_endpoint(
    protein_id: int,
    force: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Compute ESM-C 600M embedding for a protein's FASTA sequence.

    ``force=true`` encodes from scratch instead of reusing a same-sequence
    protein's vector — the only way out if the vector being copied is itself
    bad.
    """
    from services.protein_embedding_service import compute_protein_embedding

    try:
        return await compute_protein_embedding(protein_id, db, force=force)
    except LookupError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except RuntimeError as e:
        logger.exception(f"Failed to compute embedding for protein {protein_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
