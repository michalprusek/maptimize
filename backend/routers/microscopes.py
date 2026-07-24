"""Microscope routes (shared reference data, like proteins)."""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
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
from utils.colors import pick_unused_color
from utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_microscope_or_404(microscope_id: int, db: AsyncSession) -> Microscope:
    result = await db.execute(select(Microscope).where(Microscope.id == microscope_id))
    microscope = result.scalar_one_or_none()
    if not microscope:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Microscope not found")
    return microscope


async def get_experiment_count(microscope_id: int, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(Experiment.id)).where(Experiment.microscope_id == microscope_id)
    )
    return result.scalar() or 0


async def get_experiment_counts(db: AsyncSession) -> Dict[int, int]:
    result = await db.execute(
        select(Experiment.microscope_id, func.count(Experiment.id))
        .where(Experiment.microscope_id.isnot(None))
        .group_by(Experiment.microscope_id)
    )
    return dict(result.all())


async def check_name_unique(name: str, db: AsyncSession, exclude_id: Optional[int] = None) -> None:
    query = select(Microscope).where(Microscope.name == name)
    if exclude_id:
        query = query.where(Microscope.id != exclude_id)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Microscope with this name already exists",
        )


async def pick_microscope_color(db: AsyncSession) -> str:
    result = await db.execute(select(Microscope.color).where(Microscope.color.isnot(None)))
    used = {row[0].lower() for row in result.all() if row[0]}
    return pick_unused_color(used)


@router.get("", response_model=List[MicroscopeDetailedResponse])
async def list_microscopes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all microscopes with per-microscope experiment counts."""
    result = await db.execute(select(Microscope).order_by(Microscope.name))
    microscopes = result.scalars().all()
    counts = await get_experiment_counts(db)
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
    await check_name_unique(data.name, db)
    values = data.model_dump()
    if not values.get("color"):
        values["color"] = await pick_microscope_color(db)
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
    microscope = await get_microscope_or_404(microscope_id, db)
    count = await get_experiment_count(microscope_id, db)
    return MicroscopeDetailedResponse.from_microscope(microscope, count)


@router.patch("/{microscope_id}", response_model=MicroscopeDetailedResponse)
async def update_microscope(
    microscope_id: int,
    data: MicroscopeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a microscope (only the fields you pass are changed)."""
    microscope = await get_microscope_or_404(microscope_id, db)
    if data.name and data.name != microscope.name:
        await check_name_unique(data.name, db, exclude_id=microscope_id)

    update_data = data.model_dump(exclude_unset=True)
    # Explicit null color means "assign an unused one"; omitting leaves unchanged.
    if "color" in update_data and not update_data["color"]:
        update_data["color"] = await pick_microscope_color(db)
    for field, value in update_data.items():
        setattr(microscope, field, value)

    await db.commit()
    await db.refresh(microscope)
    count = await get_experiment_count(microscope_id, db)
    return MicroscopeDetailedResponse.from_microscope(microscope, count)


@router.delete("/{microscope_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_microscope(
    microscope_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a microscope (only if no experiments reference it)."""
    microscope = await get_microscope_or_404(microscope_id, db)
    count = await get_experiment_count(microscope_id, db)
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete microscope with {count} associated experiments",
        )
    await db.delete(microscope)
    await db.commit()
