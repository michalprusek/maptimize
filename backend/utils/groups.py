"""Group utility functions shared across routers and services."""
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from models.experiment import Experiment
from models.group import GroupMember


async def get_user_group_id(user_id: int, db: AsyncSession) -> Optional[int]:
    """Get the group_id for a user, or None if not in a group."""
    result = await db.execute(
        select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    )
    return result.scalar_one_or_none()


def experiment_owner_filter(user_id: int, group_id: Optional[int] = None) -> ColumnElement:
    """Build a SQL filter matching experiments the user can read.

    Read access = direct ownership OR membership in the experiment's group.
    SSOT for the access rule — queries that scope experiments to a user must go
    through this, so widening access never has to be found in N places.
    """
    conditions = [Experiment.user_id == user_id]
    if group_id is not None:
        conditions.append(Experiment.group_id == group_id)
    return or_(*conditions)
