"""PTM routes — post-translational modifications of the microtubule lattice.

Shared reference data, like proteins and microscopes: no `user_id`, one list for
the whole lab, any authenticated user may create/update/delete.
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.experiment import Experiment
from models.ptm import PTM
from models.user import User
from schemas.ptm import PTMCreate, PTMDetailedResponse, PTMUpdate
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

LABEL = "PTM"


@router.get("", response_model=List[PTMDetailedResponse])
async def list_ptms(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all PTMs with per-PTM experiment counts."""
    result = await db.execute(select(PTM).order_by(PTM.name))
    ptms = result.scalars().all()
    counts = await count_referencing_grouped(db, Experiment.ptm_id)
    return [PTMDetailedResponse.from_ptm(p, counts.get(p.id, 0)) for p in ptms]


@router.post("", response_model=PTMDetailedResponse, status_code=status.HTTP_201_CREATED)
async def create_ptm(
    data: PTMCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a PTM (shared reference data)."""
    await ensure_name_unique(db, PTM, data.name, LABEL)
    values = data.model_dump()
    if not values.get("color"):
        values["color"] = await pick_color(db, PTM)
    ptm = PTM(**values)
    db.add(ptm)
    await db.commit()
    await db.refresh(ptm)
    return PTMDetailedResponse.from_ptm(ptm, 0)


@router.get("/{ptm_id}", response_model=PTMDetailedResponse)
async def get_ptm(
    ptm_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get one PTM by id."""
    ptm = await get_or_404(db, PTM, ptm_id, LABEL)
    count = await count_referencing(db, Experiment.ptm_id, ptm_id)
    return PTMDetailedResponse.from_ptm(ptm, count)


@router.patch("/{ptm_id}", response_model=PTMDetailedResponse)
async def update_ptm(
    ptm_id: int,
    data: PTMUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a PTM (only the fields you pass are changed)."""
    ptm = await get_or_404(db, PTM, ptm_id, LABEL)
    if data.name and data.name != ptm.name:
        await ensure_name_unique(db, PTM, data.name, LABEL, exclude_id=ptm_id)

    update_data = data.model_dump(exclude_unset=True)
    # Explicit null color means "assign an unused one"; omitting leaves unchanged.
    if "color" in update_data and not update_data["color"]:
        update_data["color"] = await pick_color(db, PTM)
    for field, value in update_data.items():
        setattr(ptm, field, value)

    await db.commit()
    await db.refresh(ptm)
    count = await count_referencing(db, Experiment.ptm_id, ptm_id)
    return PTMDetailedResponse.from_ptm(ptm, count)


@router.delete("/{ptm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ptm(
    ptm_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a PTM (only if no experiments reference it)."""
    ptm = await get_or_404(db, PTM, ptm_id, LABEL)
    count = await count_referencing(db, Experiment.ptm_id, ptm_id)
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete PTM with {count} associated experiments",
        )
    await db.delete(ptm)
    await db.commit()
