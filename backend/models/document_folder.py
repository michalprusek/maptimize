"""Folders for organizing the document library (a file-explorer tree).

``parent_id`` builds the tree (NULL = a root folder); it is a plain Integer (not a
hard FK) so the tree can be reparented freely and create_all needs no special
ordering. Deletion "dissolves" a folder by moving its contents up to the parent
(handled in the router), so documents are never lost.

Two axes describe a folder:

``visibility``
    ``group`` = every member of ``group_id`` sees it; ``private`` = only
    ``user_id`` does, including against the group's admin and the global admin.
    It is inherited from the parent at creation and recomputed for the whole
    subtree on move, so a subfolder can never be more visible than the folder
    holding it.

``kind``
    ``root``/``common``/``user`` are seeded by ``utils.folder_seed`` and are
    immutable -- renaming or deleting a group's ``common`` would leave every
    member's mental model wrong. ``custom`` is anything a person created.

A document's own ``group_id`` is re-stamped from its folder's placement
(``utils.folder_placement``) rather than derived at query time: the document ACL
is mirrored in four places, and a folder join would have to be kept in step in
all of them.
"""
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import DateTime, ForeignKey, Integer, String, and_, func, or_
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from database import Base

FOLDER_VISIBILITY_GROUP = "group"
FOLDER_VISIBILITY_PRIVATE = "private"

FOLDER_KIND_ROOT = "root"
FOLDER_KIND_COMMON = "common"
FOLDER_KIND_USER = "user"
FOLDER_KIND_CUSTOM = "custom"

# The seeded structure of a group. Folders of these kinds cannot be renamed,
# moved or deleted through the API.
SEEDED_FOLDER_KINDS = (FOLDER_KIND_ROOT, FOLDER_KIND_COMMON, FOLDER_KIND_USER)


class DocumentFolder(Base):
    __tablename__ = "document_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    visibility: Mapped[str] = mapped_column(
        String(20),
        default=FOLDER_VISIBILITY_GROUP,
        server_default=FOLDER_VISIBILITY_GROUP,
    )
    kind: Mapped[str] = mapped_column(
        String(20),
        default=FOLDER_KIND_CUSTOM,
        server_default=FOLDER_KIND_CUSTOM,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<DocumentFolder(id={self.id}, name={self.name!r}, parent={self.parent_id})>"


def folder_read_scope(user_id: int, group_ids: Sequence[int] = ()) -> ColumnElement:
    """SSOT for which folders a caller may see.

    Their own -- private ones included -- plus the group-visible folders of every
    group they belong to. A colleague's private folder carries
    ``visibility='private'`` and a different ``user_id``, so it is excluded here:
    for peers, for the group's admin, and for the global admin. Dropping the
    visibility term would list every member's private folder to the whole group.

    Lives beside the model, like ``document_scope`` in ``models.rag_document``,
    because two routers need it: ``routers.folders`` to list and fetch, and
    ``routers.rag`` to validate an upload/move target and to resolve a search
    scope. An empty ``group_ids`` adds no term, leaving owner-only.
    """
    mine = DocumentFolder.user_id == user_id
    if not group_ids:
        return mine
    return or_(
        mine,
        and_(
            DocumentFolder.visibility == FOLDER_VISIBILITY_GROUP,
            DocumentFolder.group_id.in_(list(group_ids)),
        ),
    )
