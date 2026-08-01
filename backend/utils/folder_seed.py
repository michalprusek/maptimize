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
from typing import Optional, Sequence, Tuple

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


async def default_upload_folder(
    db: AsyncSession, group_ids: Sequence[int]
) -> Optional[DocumentFolder]:
    """Where a document goes when the uploader named no folder.

    Exactly one group -> that group's ``common``, so the ordinary upload is
    shared with colleagues, which is what a single-group lab expects. Several
    groups -> None, for the same reason ``default_group_id`` returns None:
    guessing which one the user meant would publish work to the wrong audience.
    Their document stays unfiled and owner-only until they move it.

    Filing the default rather than stamping a group is what lets
    ``placement_group_id`` be the whole truth about a document's audience -- with
    an unfiled upload carrying a group, the root would have meant one thing on
    upload and another on move.
    """
    if len(group_ids) != 1:
        return None
    folder = await _find(db, group_ids[0], FOLDER_KIND_COMMON)
    if folder is None:
        # A THIRD outcome wearing the same None: not a policy refusal but a
        # seeding fault (a group created before folders existed, or a backfill
        # that never ran). Every upload by its members silently becomes
        # owner-only, and the only symptom is a colleague saying "I can't find
        # that paper" weeks later.
        logger.error(
            "Group %s has no 'common' folder, so uploads by its members land "
            "unfiled and owner-only. Run scripts/migrate_multi_group_folders.py.",
            group_ids[0],
        )
    return folder


async def dissolve_group_folders(db: AsyncSession, group_id: int) -> None:
    """Turn a disbanded group's seeded folders back into ordinary ones.

    ``document_folders.group_id`` is ON DELETE SET NULL, so deleting a group
    leaves its root, its ``common`` and every member's private folder behind with
    ``kind`` still seeded -- and ``_reject_if_seeded`` then refuses to rename,
    move or delete them **forever**, citing a group that no longer exists.
    Undeletable debris in someone's tree is a worse outcome than the tidy-up.

    Each folder returns to its owner as a private, top-level, ordinary folder.
    ⚠️ This selects the group's WHOLE tree, not only the seeded rows: custom
    folders inherit `group_id` from their parent, so nesting is flattened too.
    Private is the safe direction: ``common`` loses its group anyway, so leaving
    it group-visible would only mean nobody could see it. Callers must commit.
    """
    rows = list((await db.execute(
        select(DocumentFolder).where(DocumentFolder.group_id == group_id)
    )).scalars().all())
    for folder in rows:
        folder.parent_id = None
        folder.group_id = None
        folder.kind = FOLDER_KIND_CUSTOM
        folder.visibility = FOLDER_VISIBILITY_PRIVATE
    if rows:
        logger.info(
            f"Dissolved {len(rows)} folder(s) of group {group_id} into ordinary "
            f"private folders"
        )
    else:
        # Every group gets a root and a `common` at creation, so none at all
        # means the seed never ran or the rows were already detached -- and the
        # group row is about to be deleted, after which they can no longer be
        # found by group at all.
        logger.warning(
            "Disbanding group %s that has no folder tree; anything it should "
            "have owned is now unreachable by group id", group_id,
        )


async def rename_group_root_folder(db: AsyncSession, group_id: int, name: str) -> None:
    """Keep the root folder's title equal to the group's name."""
    root = await _find(db, group_id, FOLDER_KIND_ROOT)
    if root is not None:
        root.name = name
