"""MAP Protein routes."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from models.image import MapProtein
from schemas.image import MapProteinCreate, MapProteinResponse
from utils.security import get_current_user

router = APIRouter()

# Default MAP proteins with colors for visualization
DEFAULT_PROTEINS = [
    {"name": "PRC1", "full_name": "Protein Regulator of Cytokinesis 1", "color": "#00d4aa"},
    {"name": "Tau4R", "full_name": "Microtubule-Associated Protein Tau (4R)", "color": "#ff6b6b"},
    {"name": "MAP2d", "full_name": "Microtubule-Associated Protein 2d", "color": "#4ecdc4"},
    {"name": "MAP9", "full_name": "Microtubule-Associated Protein 9", "color": "#ffe66d"},
    {"name": "EML3", "full_name": "Echinoderm Microtubule-Associated Protein Like 3", "color": "#95e1d3"},
    {"name": "HMMR", "full_name": "Hyaluronan Mediated Motility Receptor", "color": "#f38181"},
]


@router.get("", response_model=List[MapProteinResponse])
async def list_proteins(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all MAP proteins."""
    result = await db.execute(
        select(MapProtein).order_by(MapProtein.name)
    )
    proteins = result.scalars().all()

    # If no proteins exist, create defaults
    if not proteins:
        for p_data in DEFAULT_PROTEINS:
            protein = MapProtein(**p_data)
            db.add(protein)
        await db.commit()

        result = await db.execute(
            select(MapProtein).order_by(MapProtein.name)
        )
        proteins = result.scalars().all()

    return [MapProteinResponse.model_validate(p) for p in proteins]


@router.post("", response_model=MapProteinResponse, status_code=status.HTTP_201_CREATED)
async def create_protein(
    data: MapProteinCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new MAP protein."""
    # Check if name exists
    result = await db.execute(
        select(MapProtein).where(MapProtein.name == data.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Protein with this name already exists"
        )

    protein = MapProtein(
        name=data.name,
        full_name=data.full_name,
        description=data.description,
        color=data.color,
    )
    db.add(protein)
    await db.commit()
    await db.refresh(protein)

    return MapProteinResponse.model_validate(protein)


@router.get("/{protein_id}", response_model=MapProteinResponse)
async def get_protein(
    protein_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get protein details."""
    result = await db.execute(
        select(MapProtein).where(MapProtein.id == protein_id)
    )
    protein = result.scalar_one_or_none()

    if not protein:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Protein not found"
        )

    return MapProteinResponse.model_validate(protein)
