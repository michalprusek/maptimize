"""PTM routes — post-translational modifications of the microtubule lattice.

Shared reference data, like proteins and microscopes: no `user_id`, one list for
the whole lab, any authenticated user may create/update/delete.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.experiment import Experiment
from models.ptm import PTM, PTMKind
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


def _check_pairing_shape(
    kind: str,
    controls_ptm_id: Optional[int],
    *,
    row_id: Optional[int] = None,
) -> None:
    """The pairing invariant, read off the row that will exist after this write.

    A control names the modification it is the paired control for, and that link
    is **required**: a control naming nothing is the single pooled `Control` row
    this feature replaces, so the API must not be able to produce one. Everything
    else must leave it null — a modification that "controls" something, or an
    `Unmodified` row that does, means nothing.

    Judged on the resulting row rather than on the payload. That is what lets a
    PATCH touching only the description leave the link alone instead of having to
    resend it, and it is also what catches the other direction: flipping ``kind``
    to ``modification`` while the link is still set.

    Pure, and deliberately separate from the target lookup below — this half runs
    on every write, that half only when the link is actually being set.
    """
    if kind != PTMKind.CONTROL.value:
        if controls_ptm_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"controls_ptm_id is only meaningful on a control; this row "
                    f"is a '{kind}'. Set kind='control' or clear the link."
                ),
            )
        return

    if controls_ptm_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A control must say which modification it is the control for: "
                "pass controls_ptm_id. The control plasmid differs between PTM "
                "conditions, so one shared control row would pool samples that "
                "are not comparable."
            ),
        )

    if controls_ptm_id == row_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A control cannot be its own control.",
        )


async def _verify_control_target(db: AsyncSession, controls_ptm_id: int) -> None:
    """The partner must exist and must itself be a modification.

    Needs the other row, so it cannot live in the schema — and it runs only when
    a write actually names a partner. Re-reading an unchanged link on every PATCH
    would be a query per edit to re-answer a question this request did not ask.
    """
    target = await get_or_404(db, PTM, controls_ptm_id, LABEL)
    if target.kind != PTMKind.MODIFICATION.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"A control pairs with a modification, but PTM {controls_ptm_id} "
                f"('{target.name}') is a '{target.kind}'."
            ),
        )


async def _controls_pointing_at(db: AsyncSession, ptm_id: int) -> List[str]:
    """Names of the control rows paired with this PTM."""
    result = await db.execute(
        select(PTM.name).where(PTM.controls_ptm_id == ptm_id).order_by(PTM.name)
    )
    return list(result.scalars().all())


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
    _check_pairing_shape(values["kind"], values.get("controls_ptm_id"))
    if values.get("controls_ptm_id") is not None:
        await _verify_control_target(db, values["controls_ptm_id"])
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

    # Validate the row this patch WOULD produce, before mutating anything: kind
    # and the link move together, so checking either one alone lets the other
    # through. Doing it first also means a rejected patch leaves the instance
    # untouched rather than half-applied in the session.
    _check_pairing_shape(
        update_data.get("kind", ptm.kind),
        update_data.get("controls_ptm_id", ptm.controls_ptm_id),
        row_id=ptm_id,
    )
    if update_data.get("controls_ptm_id") is not None:
        await _verify_control_target(db, update_data["controls_ptm_id"])
    # The dangerous direction is AWAY from `modification`. A control's partner
    # must be one, so declassifying that partner strands the control on a row
    # that is no longer a valid target — the invariant broken from the end
    # `_check_pairing_shape` cannot see, because it only ever looks at the row
    # being written. Restating an unchanged kind must stay free: the editor sends
    # `kind` on every save, so guarding the no-op would reject an ordinary
    # description edit on any modification that has a control.
    changing_away = (
        "kind" in update_data
        and update_data["kind"] != PTMKind.MODIFICATION.value
        and ptm.kind == PTMKind.MODIFICATION.value
    )
    if changing_away:
        dependents = await _controls_pointing_at(db, ptm_id)
        if dependents:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot reclassify this PTM while it is the partner of "
                    f"{', '.join(dependents)} — a control pairs with a "
                    f"modification. Re-point or delete those first."
                ),
            )

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

    # The FK has no ON DELETE clause on purpose: SET NULL would leave a control
    # naming nothing, which is the pooled row again and arrived at in silence.
    # Naming the blockers lets the caller fix them instead of guessing.
    dependents = await _controls_pointing_at(db, ptm_id)
    if dependents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete PTM while it is the partner of "
                f"{', '.join(dependents)}. Delete or re-point those first."
            ),
        )

    await db.delete(ptm)
    await db.commit()
