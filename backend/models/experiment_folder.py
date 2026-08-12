"""Folders for organising experiments.

A plain tree over `experiments`: ``parent_id`` builds it (NULL = a top-level
folder), an experiment carries ``folder_id``, and NULL there means "unfiled".

**Not a copy of ``DocumentFolder``, and the difference is the point.**

*No ``visibility`` axis.* An experiment is readable by the whole group wherever
it sits (``experiment_owner_filter``), so a "private folder" would hide the
folder while leaving the experiment inside it visible — a control that lies about
what it does. A folder is visible to its owner and to the group whose tree it
sits in, and that is the whole rule.

*Placement is organisation, not publication.* Filing an experiment does NOT touch
``experiments.group_id``. This is the opposite of the document library, where a
move re-stamps the document, and the reason is that documents derive their
audience from their folder while experiments carry theirs on the row. Without
that separation, dropping a colleague's experiment into a folder would silently
change who can read it.

*No seeded folders.* The document library seeds root/common/user per group
because a document's placement decides its audience and needs somewhere correct
to land by default. Here it decides nothing, so an empty tree is the honest
starting point and every folder is one a person made.
"""
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import DateTime, ForeignKey, Integer, String, func, or_
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from database import Base


class ExperimentFolder(Base):
    __tablename__ = "experiment_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Which group's tree this sits in. Stamped from `default_group_id` at
    # creation and inherited from the parent on create/move, so a subtree cannot
    # straddle two groups.
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Plain Integer, not a self-FK: the tree reparents freely and `create_all`
    # needs no special ordering. Same choice, same reason, as DocumentFolder.
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ExperimentFolder(id={self.id}, name={self.name!r}, "
            f"parent={self.parent_id})>"
        )


def experiment_folder_scope(
    user_id: int, group_ids: Sequence[int] = ()
) -> ColumnElement:
    """SSOT for which folders a caller may see and file into.

    Their own, plus every folder in the tree of a group they belong to. It
    deliberately mirrors ``experiment_owner_filter``: a folder the caller can see
    must be one whose experiments they could already see, or the tree would offer
    somewhere to put work that then vanishes from their own view.

    An empty ``group_ids`` adds no term, leaving owner-only — never ``IN ()``,
    which is an error in one dialect and a silent false in another, and neither
    belongs in something guarding data.
    """
    mine = ExperimentFolder.user_id == user_id
    if not group_ids:
        return mine
    return or_(mine, ExperimentFolder.group_id.in_(list(group_ids)))
