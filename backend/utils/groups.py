"""Group utility functions shared across routers."""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.group import GroupMember


async def get_user_group_id(user_id: int, db: AsyncSession) -> Optional[int]:
    """Get the group_id for a user, or None if not in a group."""
    result = await db.execute(
        select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    )
    return result.scalar_one_or_none()
