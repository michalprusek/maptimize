"""Cell line routes (shared reference data, like proteins, microscopes and PTMs).

⚠️ There is no `/dashboard/cell-lines` admin page: a line is created straight
from the experiment form, by typing a name the list does not yet hold. So these
endpoints are the *only* way to rename or delete one — from the API or from the
MCP connector. Do not assume a UI exists to fix a typo.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.cell_line import CellLine
from models.experiment import Experiment
from models.user import User
from schemas.cell_line import (
    CellLineCreate,
    CellLineDetailedResponse,
    CellLineUpdate,
)
from utils.reference_data import (
    count_referencing,
    count_referencing_grouped,
    ensure_name_unique,
    get_or_404,
    pick_color,
)
from utils.security import get_current_user

router = APIRouter()

LABEL = "Cell line"


@router.get("", response_model=List[CellLineDetailedResponse])
async def list_cell_lines(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all cell lines with per-line experiment counts."""
    result = await db.execute(select(CellLine).order_by(CellLine.name))
    cell_lines = result.scalars().all()
    counts = await count_referencing_grouped(db, Experiment.cell_line_id)
    return [
        CellLineDetailedResponse.from_cell_line(c, counts.get(c.id, 0))
        for c in cell_lines
    ]


@router.post("", response_model=CellLineDetailedResponse, status_code=status.HTTP_201_CREATED)
async def create_cell_line(
    data: CellLineCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a cell line (shared reference data).

    Called by the experiment form when the typed name matches nothing, so the
    duplicate-name 400 is a live path rather than a formality.
    """
    await ensure_name_unique(db, CellLine, data.name, LABEL, case_insensitive=True)
    values = data.model_dump()
    if not values.get("color"):
        values["color"] = await pick_color(db, CellLine)
    cell_line = CellLine(**values)
    db.add(cell_line)
    await db.commit()
    await db.refresh(cell_line)
    return CellLineDetailedResponse.from_cell_line(cell_line, 0)


@router.get("/{cell_line_id}", response_model=CellLineDetailedResponse)
async def get_cell_line(
    cell_line_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get one cell line by id."""
    cell_line = await get_or_404(db, CellLine, cell_line_id, LABEL)
    count = await count_referencing(db, Experiment.cell_line_id, cell_line_id)
    return CellLineDetailedResponse.from_cell_line(cell_line, count)


@router.patch("/{cell_line_id}", response_model=CellLineDetailedResponse)
async def update_cell_line(
    cell_line_id: int,
    data: CellLineUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a cell line (only the fields you pass are changed)."""
    cell_line = await get_or_404(db, CellLine, cell_line_id, LABEL)
    if data.name and data.name != cell_line.name:
        await ensure_name_unique(
            db, CellLine, data.name, LABEL,
            exclude_id=cell_line_id, case_insensitive=True,
        )

    update_data = data.model_dump(exclude_unset=True)
    # Explicit null color means "assign an unused one"; omitting leaves unchanged.
    if "color" in update_data and not update_data["color"]:
        update_data["color"] = await pick_color(db, CellLine)
    for field, value in update_data.items():
        setattr(cell_line, field, value)

    await db.commit()
    await db.refresh(cell_line)
    count = await count_referencing(db, Experiment.cell_line_id, cell_line_id)
    return CellLineDetailedResponse.from_cell_line(cell_line, count)


@router.delete("/{cell_line_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cell_line(
    cell_line_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a cell line (only if no experiments reference it)."""
    cell_line = await get_or_404(db, CellLine, cell_line_id, LABEL)
    count = await count_referencing(db, Experiment.cell_line_id, cell_line_id)
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete cell line with {count} associated experiments",
        )
    await db.delete(cell_line)
    await db.commit()
