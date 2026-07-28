"""Microscope routes (shared reference data, like proteins and PTMs)."""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.experiment import Experiment
from models.microscope import Microscope
from models.user import User
from schemas.microscope import (
    MicroscopeCreate,
    MicroscopeDetailedResponse,
    MicroscopeUpdate,
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

LABEL = "Microscope"


@router.get("", response_model=List[MicroscopeDetailedResponse])
async def list_microscopes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all microscopes with per-microscope experiment counts."""
    result = await db.execute(select(Microscope).order_by(Microscope.name))
    microscopes = result.scalars().all()
    counts = await count_referencing_grouped(db, Experiment.microscope_id)
    return [
        MicroscopeDetailedResponse.from_microscope(m, counts.get(m.id, 0))
        for m in microscopes
    ]


@router.post("", response_model=MicroscopeDetailedResponse, status_code=status.HTTP_201_CREATED)
async def create_microscope(
    data: MicroscopeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a microscope (shared reference data)."""
    await ensure_name_unique(db, Microscope, data.name, LABEL)
    values = data.model_dump()
    if not values.get("color"):
        values["color"] = await pick_color(db, Microscope)
    microscope = Microscope(**values)
    db.add(microscope)
    await db.commit()
    await db.refresh(microscope)
    return MicroscopeDetailedResponse.from_microscope(microscope, 0)


@router.get("/{microscope_id}", response_model=MicroscopeDetailedResponse)
async def get_microscope(
    microscope_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get one microscope by id."""
    microscope = await get_or_404(db, Microscope, microscope_id, LABEL)
    count = await count_referencing(db, Experiment.microscope_id, microscope_id)
    return MicroscopeDetailedResponse.from_microscope(microscope, count)


@router.patch("/{microscope_id}", response_model=MicroscopeDetailedResponse)
async def update_microscope(
    microscope_id: int,
    data: MicroscopeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a microscope (only the fields you pass are changed)."""
    microscope = await get_or_404(db, Microscope, microscope_id, LABEL)
    if data.name and data.name != microscope.name:
        await ensure_name_unique(db, Microscope, data.name, LABEL, exclude_id=microscope_id)

    update_data = data.model_dump(exclude_unset=True)
    # Explicit null color means "assign an unused one"; omitting leaves unchanged.
    if "color" in update_data and not update_data["color"]:
        update_data["color"] = await pick_color(db, Microscope)
    for field, value in update_data.items():
        setattr(microscope, field, value)

    await db.commit()
    await db.refresh(microscope)
    count = await count_referencing(db, Experiment.microscope_id, microscope_id)
    return MicroscopeDetailedResponse.from_microscope(microscope, count)


@router.delete("/{microscope_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_microscope(
    microscope_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a microscope (only if no experiments reference it)."""
    microscope = await get_or_404(db, Microscope, microscope_id, LABEL)
    count = await count_referencing(db, Experiment.microscope_id, microscope_id)
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete microscope with {count} associated experiments",
        )
    await db.delete(microscope)
    await db.commit()
