"""MAP Protein routes."""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
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
from utils.colors import pick_unused_color
from utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Helper Functions (DRY)
# =============================================================================


async def get_protein_or_404(protein_id: int, db: AsyncSession) -> MapProtein:
    """Fetch protein by ID or raise 404."""
    result = await db.execute(
        select(MapProtein).where(MapProtein.id == protein_id)
    )
    protein = result.scalar_one_or_none()
    if not protein:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Protein not found"
        )
    return protein


async def get_image_count_for_protein(protein_id: int, db: AsyncSession) -> int:
    """Get count of images associated with a protein."""
    result = await db.execute(
        select(func.count(Image.id)).where(Image.map_protein_id == protein_id)
    )
    return result.scalar() or 0


async def get_image_counts_by_protein(db: AsyncSession) -> Dict[int, int]:
    """Get image counts grouped by protein ID."""
    result = await db.execute(
        select(Image.map_protein_id, func.count(Image.id))
        .where(Image.map_protein_id.isnot(None))
        .group_by(Image.map_protein_id)
    )
    return dict(result.all())


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


async def check_protein_name_unique(
    name: str, db: AsyncSession, exclude_id: Optional[int] = None
) -> None:
    """Raise 400 if protein name already exists."""
    query = select(MapProtein).where(MapProtein.name == name)
    if exclude_id:
        query = query.where(MapProtein.id != exclude_id)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Protein with this name already exists"
        )

async def pick_protein_color(db: AsyncSession) -> str:
    """Pick a colour no existing protein is using.

    Check-then-act: two concurrent creates can pick the same colour. Accepted
    for the same reason as the document dedup in CLAUDE.md — the cost is one
    duplicate marker, while a unique constraint on colour would reject
    perfectly legitimate user-chosen values.
    """
    result = await db.execute(
        select(MapProtein.color).where(MapProtein.color.isnot(None))
    )
    used = {row[0].lower() for row in result.all() if row[0]}
    return pick_unused_color(used)


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

    # If no proteins exist, create defaults
    if not proteins:
        for p_data in DEFAULT_PROTEINS:
            db.add(MapProtein(**p_data))
        await db.commit()

        result = await db.execute(
            select(MapProtein).order_by(MapProtein.name)
        )
        proteins = result.scalars().all()

    image_counts = await get_image_counts_by_protein(db)

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
    await check_protein_name_unique(data.name, db)

    values = data.model_dump()
    if not values.get("color"):
        values["color"] = await pick_protein_color(db)

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

    image_counts = await get_image_counts_by_protein(db)
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

    # Compute UMAP on-the-fly
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


# =============================================================================
# Protein CRUD by ID (must come after /umap to avoid route conflict)
# =============================================================================


@router.get("/{protein_id}", response_model=MapProteinDetailedResponse)
async def get_protein(
    protein_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get protein details."""
    protein = await get_protein_or_404(protein_id, db)
    image_count = await get_image_count_for_protein(protein_id, db)
    return MapProteinDetailedResponse.from_protein(protein, image_count)


@router.patch("/{protein_id}", response_model=MapProteinDetailedResponse)
async def update_protein(
    protein_id: int,
    data: MapProteinUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update a MAP protein."""
    protein = await get_protein_or_404(protein_id, db)

    # Check if new name conflicts (exclude current protein)
    if data.name and data.name != protein.name:
        await check_protein_name_unique(data.name, db, exclude_id=protein_id)

    # Update fields
    update_data = data.model_dump(exclude_unset=True)

    # An explicitly null colour means "assign me an unused one" (the UI's Auto
    # button). Omitting the field entirely still means "leave it alone" — the
    # two must stay distinguishable, which is why exclude_unset is load-bearing.
    if "color" in update_data and not update_data["color"]:
        update_data["color"] = await pick_protein_color(db)

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

    image_count = await get_image_count_for_protein(protein_id, db)
    return MapProteinDetailedResponse.from_protein(protein, image_count)


@router.delete("/{protein_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_protein(
    protein_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a MAP protein (only if no images are associated)."""
    protein = await get_protein_or_404(protein_id, db)
    image_count = await get_image_count_for_protein(protein_id, db)

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
