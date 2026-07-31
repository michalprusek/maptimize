"""Seeding and upkeep of a group's folder tree.

Every group owns a tree shaped like this::

    Dr. Janke Lab/          kind=root,   visibility=group
    ├── common/             kind=common, visibility=group   (everyone reads and writes)
    ├── Michal Prusek/      kind=user,   visibility=private (only Michal)
    └── Theo/               kind=user,   visibility=private (only Theo)

The root exists as a real row rather than being implied by ``group_id`` so that
the tree the agent and the UI walk is one structure with one set of rules, and so
that "move this into the group's common folder" has an unambiguous target when a
person belongs to several groups.

Every function here is idempotent and keyed on ``kind`` (plus ``user_id`` for a
member folder), never on the folder's name -- names are user-visible and change.
None of them commit; the caller owns the transaction.
"""
import logging
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.document_folder import (
    FOLDER_KIND_COMMON,
    FOLDER_KIND_CUSTOM,
    FOLDER_KIND_ROOT,
    FOLDER_KIND_USER,
    FOLDER_VISIBILITY_GROUP,
    FOLDER_VISIBILITY_PRIVATE,
    DocumentFolder,
)

logger = logging.getLogger(__name__)

COMMON_FOLDER_NAME = "common"


async def _find(
    db: AsyncSession, group_id: int, kind: str, user_id: Optional[int] = None
) -> Optional[DocumentFolder]:
    query = select(DocumentFolder).where(
        DocumentFolder.group_id == group_id, DocumentFolder.kind == kind
    )
    if user_id is not None:
        query = query.where(DocumentFolder.user_id == user_id)
    return (await db.execute(query)).scalars().first()


async def ensure_group_folders(
    db: AsyncSession, group
) -> Tuple[DocumentFolder, DocumentFolder]:
    """Create (or find) the group's root and ``common`` folders.

    Returns ``(root, common)``. Both are owned by the group's creator, but
    ownership is only bookkeeping here: visibility is ``group``, so every member
    sees and can organize them.
    """
    root = await _find(db, group.id, FOLDER_KIND_ROOT)
    if root is None:
        root = DocumentFolder(
            user_id=group.created_by_user_id,
            group_id=group.id,
            parent_id=None,
            name=group.name,
            visibility=FOLDER_VISIBILITY_GROUP,
            kind=FOLDER_KIND_ROOT,
        )
        db.add(root)
        await db.flush()

    common = await _find(db, group.id, FOLDER_KIND_COMMON)
    if common is None:
        common = DocumentFolder(
            user_id=group.created_by_user_id,
            group_id=group.id,
            parent_id=root.id,
            name=COMMON_FOLDER_NAME,
            visibility=FOLDER_VISIBILITY_GROUP,
            kind=FOLDER_KIND_COMMON,
        )
        db.add(common)
        await db.flush()

    return root, common


async def ensure_member_folder(
    db: AsyncSession, group, user
) -> Optional[DocumentFolder]:
    """Create (or find) this member's private folder inside the group's tree.

    Private means private: ``visibility='private'`` keeps it out of every other
    member's listing, including the group's admin and the global admin. It is the
    one place in the application an admin cannot read.
    """
    if user is None:  # requester's account vanished between request and approval
        return None

    existing = await _find(db, group.id, FOLDER_KIND_USER, user_id=user.id)
    if existing is not None:
        return existing

    root, _ = await ensure_group_folders(db, group)
    folder = DocumentFolder(
        user_id=user.id,
        group_id=group.id,
        parent_id=root.id,
        name=getattr(user, "name", None) or f"User {user.id}",
        visibility=FOLDER_VISIBILITY_PRIVATE,
        kind=FOLDER_KIND_USER,
    )
    db.add(folder)
    await db.flush()
    return folder


async def detach_member_folder(db: AsyncSession, group_id: int, user_id: int) -> None:
    """Cut a departing member's private folder loose from the group's tree.

    Without this the folder keeps hanging under a root the ex-member can no
    longer see, so it vanishes from their tree while still holding their files.
    It moves to the library root and becomes an ordinary folder; the documents
    inside were already owner-only, so their visibility does not change.
    """
    folder = await _find(db, group_id, FOLDER_KIND_USER, user_id=user_id)
    if folder is None:
        return
    folder.parent_id = None
    folder.group_id = None
    folder.kind = FOLDER_KIND_CUSTOM
    folder.visibility = FOLDER_VISIBILITY_PRIVATE
    logger.info(
        f"Detached user {user_id}'s private folder {folder.id} from group {group_id}"
    )


async def rename_group_root_folder(db: AsyncSession, group_id: int, name: str) -> None:
    """Keep the root folder's title equal to the group's name."""
    root = await _find(db, group_id, FOLDER_KIND_ROOT)
    if root is not None:
        root.name = name
