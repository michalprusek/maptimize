"""Group utility functions shared across routers and services.

Read access is "your own rows, plus the rows of every group you belong to".
Membership is many-to-many, so the scope is a LIST of group ids: an empty list
contributes no group term at all, which fails closed to owner-only rather than
widening to everything.

Write access is unchanged by this module: experiments and images stay owner-only,
with the four deliberate group-write exceptions (crops, microscope, PTM, protein)
enforced at their own endpoints.
"""
from typing import Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from models.experiment import Experiment
from models.group import GroupMember


async def get_user_group_ids(user_id: int, db: AsyncSession) -> list[int]:
    """Every group the user belongs to. An empty list means no group.

    Returns a list rather than a single id because membership is many-to-many:
    a person can sit in the lab group and an institutional group at once. There
    is deliberately no singular variant -- one would let a call site keep the old
    one-group semantics without anything looking wrong.
    """
    result = await db.execute(
        select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    )
    return list(result.scalars().all())


def experiment_owner_filter(
    user_id: int, group_ids: Sequence[int] = ()
) -> ColumnElement:
    """Build a SQL filter matching experiments the user can read.

    Read access = direct ownership OR membership in the experiment's group.
    SSOT for the access rule: every query that scopes experiments to a user goes
    through this, so widening access never has to be found in N places.

    An empty ``group_ids`` adds no term, leaving owner-only. Do not "simplify"
    that to an unconditional ``IN``: an empty ``IN ()`` is a SQL error in some
    dialects and a silent false in others, and neither failure mode is one you
    want guarding data.
    """
    conditions = [Experiment.user_id == user_id]
    if group_ids:
        conditions.append(Experiment.group_id.in_(list(group_ids)))
    return or_(*conditions)


def default_group_id(group_ids: Sequence[int]) -> Optional[int]:
    """Which group a newly created object is shared with by default.

    Exactly one group -> that group. This is what every member of a single-group
    lab expects: what I make is visible to my colleagues, without a second step.

    Several groups -> None. Guessing which one the user meant would silently
    publish work to the wrong audience, and "shared with nobody" is the mistake
    that is trivial to correct. They assign it explicitly instead
    (``PATCH /api/experiments/{id}/group``, or by moving a document into a
    group's folder).
    """
    return group_ids[0] if len(group_ids) == 1 else None


async def is_group_admin(user_id: int, group_id: int, db: AsyncSession) -> bool:
    """True when the user holds the 'admin' role in this specific group."""
    result = await db.execute(
        select(GroupMember.role).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() == "admin"


async def require_group_admin(
    user_id: int,
    group_id: int,
    db: AsyncSession,
    *,
    actor: Optional[object] = None,
) -> None:
    """Authorize a group-administration action, or raise 403.

    A global ``users.role == ADMIN`` counts as admin of every group, including
    groups that do not exist yet -- which is what makes "this person administers
    everything" durable, with no rows to remember to add later. Pass the User
    object as ``actor`` to enable that path.

    ``Group.created_by_user_id`` grants nothing; it is provenance. It used to be
    the only check, which meant the group's founder was the sole administrator
    and the ``role`` column was decorative.
    """
    if actor is not None:
        role = getattr(actor, "role", None)
        if getattr(role, "value", role) == "admin":
            return
    if not await is_group_admin(user_id, group_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a group admin can do this",
        )
