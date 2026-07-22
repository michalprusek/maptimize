"""Group utility functions shared across routers and services."""
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from models.experiment import Experiment
from models.group import GroupMember
from models.rag_document import RAGDocument


async def get_user_group_id(user_id: int, db: AsyncSession) -> Optional[int]:
    """Get the group_id for a user, or None if not in a group."""
    result = await db.execute(
        select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    )
    return result.scalar_one_or_none()


def experiment_owner_filter(user_id: int, group_id: Optional[int] = None) -> ColumnElement:
    """Build a SQL filter matching experiments the user can read.

    Read access = direct ownership OR membership in the experiment's group.
    SSOT for the access rule: every query that scopes experiments to a user goes
    through this, so widening access never has to be found in N places.
    """
    conditions = [Experiment.user_id == user_id]
    if group_id is not None:
        conditions.append(Experiment.group_id == group_id)
    return or_(*conditions)


async def adopt_orphan_experiments(
    db: AsyncSession,
    user_id: int,
    group_id: int,
) -> int:
    """
    Move the user's group-less experiments into the group they just joined.

    Experiments are stamped with group_id at creation, so anything created before
    the owner had a group keeps group_id NULL forever. Such a row sits in its
    owner's read scope but not their peers' — and because umap_x/umap_y are ONE
    shared projection per scope, two members fitting different corpora would
    overwrite each other's coordinates with values from incompatible fits.

    Adopting the orphans keeps every member's corpus identical, which is the
    precondition that makes umap_service.refresh_scope_key's group-wide dedupe
    correct. Callers must commit.

    Returns the number of experiments adopted.
    """
    result = await db.execute(
        update(Experiment)
        .where(Experiment.user_id == user_id, Experiment.group_id.is_(None))
        .values(group_id=group_id)
    )
    return result.rowcount


async def adopt_orphan_documents(
    db: AsyncSession,
    user_id: int,
    group_id: int,
) -> int:
    """Share the joiner's group-less LIBRARY documents with the group they joined.

    Library docs are stamped with group_id at upload, so anything uploaded before
    the owner had a group keeps group_id NULL and is invisible to peers. Adopting
    them makes the joiner's existing library visible group-wide, matching
    adopt_orphan_experiments. Attachments (thread_id set) are never adopted -- they
    stay private to their conversation. Callers must commit.

    Returns the number of documents adopted.
    """
    result = await db.execute(
        update(RAGDocument)
        .where(
            RAGDocument.user_id == user_id,
            RAGDocument.thread_id.is_(None),
            RAGDocument.group_id.is_(None),
        )
        .values(group_id=group_id)
    )
    return result.rowcount
