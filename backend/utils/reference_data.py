"""Shared CRUD helpers for the lab's shared reference tables.

`map_proteins`, `microscopes` and `ptms` are all the same kind of thing: a
lab-wide lookup list with a unique name and a legend colour, referenced by a
nullable FK from another table. Their routers were drifting into three copies of
the same four helpers, so the copies live here once, parameterised by model.

These deliberately stay dumb about authorisation. Reference data carries no
`user_id` — any authenticated user may read and write it — so the routers supply
`get_current_user` and nothing else.
"""
import logging
from typing import Dict, Optional, Type, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute

from utils.colors import pick_unused_color

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def get_or_404(db: AsyncSession, model: Type[T], obj_id: int, label: str) -> T:
    """Fetch a reference row by id, or raise 404 naming it."""
    result = await db.execute(select(model).where(model.id == obj_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label} not found",
        )
    return obj


async def count_referencing(
    db: AsyncSession, fk_column: InstrumentedAttribute, obj_id: int
) -> int:
    """Count rows whose foreign key points at ``obj_id``.

    The FROM clause is inferred from the column itself, so callers pass e.g.
    ``Experiment.microscope_id`` and get the number of experiments using it.
    Counting the FK column rather than ``*`` is what lets the FROM be inferred;
    the ``WHERE`` already excludes the NULLs that ``count(col)`` would skip.
    """
    result = await db.execute(
        select(func.count(fk_column)).where(fk_column == obj_id)
    )
    return result.scalar() or 0


async def count_referencing_grouped(
    db: AsyncSession, fk_column: InstrumentedAttribute
) -> Dict[int, int]:
    """Reference id -> number of rows pointing at it. Ids with no rows are absent."""
    result = await db.execute(
        select(fk_column, func.count(fk_column))
        .where(fk_column.isnot(None))
        .group_by(fk_column)
    )
    return dict(result.all())


async def ensure_name_unique(
    db: AsyncSession,
    model: Type[T],
    name: str,
    label: str,
    exclude_id: Optional[int] = None,
) -> None:
    """Raise 400 if another row of this model already has ``name``."""
    query = select(model).where(model.name == name)
    if exclude_id:
        query = query.where(model.id != exclude_id)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label} with this name already exists",
        )


async def pick_color(db: AsyncSession, model: Type[T]) -> str:
    """Pick a colour no existing row of this model is using.

    Check-then-act: two concurrent creates can pick the same colour. Accepted for
    the same reason as the document dedup in CLAUDE.md — the cost is one
    duplicate legend colour, while a unique constraint on colour would reject
    perfectly legitimate user-chosen values.

    Colours are unique per table only, so a PTM and a microscope may share a hex.
    Nothing plots both dimensions at once, so they never collide on screen.
    """
    result = await db.execute(select(model.color).where(model.color.isnot(None)))
    used = {row[0].lower() for row in result.all() if row[0]}
    return pick_unused_color(used)
