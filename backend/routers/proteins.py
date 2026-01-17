"""MAP Protein routes."""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from models.image import MapProtein, Image
from schemas.image import (
    MapProteinCreate,
    MapProteinUpdate,
    MapProteinDetailedResponse,
    UmapProteinPointResponse,
    UmapProteinDataResponse,
)
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

# Default MAP proteins with colors for visualization
DEFAULT_PROTEINS = [
    {"name": "PRC1", "full_name": "Protein Regulator of Cytokinesis 1", "color": "#00d4aa"},
    {"name": "Tau4R", "full_name": "Microtubule-Associated Protein Tau (4R)", "color": "#ff6b6b"},
    {"name": "MAP2d", "full_name": "Microtubule-Associated Protein 2d", "color": "#4ecdc4"},
    {"name": "MAP9", "full_name": "Microtubule-Associated Protein 9", "color": "#ffe66d"},
    {"name": "EML3", "full_name": "Echinoderm Microtubule-Associated Protein Like 3", "color": "#95e1d3"},
    {"name": "HMMR", "full_name": "Hyaluronan Mediated Motility Receptor", "color": "#f38181"},
]


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

    protein = MapProtein(**data.model_dump())
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
    from services.umap_service import compute_protein_umap_online
    import numpy as np

    result = await db.execute(
        select(MapProtein)
        .where(MapProtein.embedding.isnot(None))
        .order_by(MapProtein.name)
    )
    proteins = result.scalars().all()

    if len(proteins) < 3:
        return UmapProteinDataResponse(
            points=[],
            total_proteins=len(proteins),
            silhouette_score=None,
            is_precomputed=False,
            computed_at=None,
        )

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
    projection, silhouette = compute_protein_umap_online(embeddings)

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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Compute ESM-C 600M embedding for a protein's FASTA sequence."""
    from services.protein_embedding_service import compute_protein_embedding

    try:
        return await compute_protein_embedding(protein_id, db)
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
