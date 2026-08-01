"""One-off backfill for multi-group membership and the per-group folder tree.

Idempotent by construction -- every step is a "create if absent" or a targeted
UPDATE, so re-running it is a no-op. Run it inside the backend container after
the schema has been brought up to date by the app's own startup
(``ensure_schema_updates`` adds document_folders.visibility/kind and swaps the
group_members UNIQUE):

    docker exec -i -w /app maptimize-backend python scripts/migrate_multi_group_folders.py

What it does, in order:

1. Seeds every existing group with a root folder named after the group and a
   ``common`` folder under it, plus one private folder per member.
2. Re-parents each group's existing top-level custom folders under that root,
   keeping them group-visible -- nothing changes who can see a document.
3. Creates the ``UTIA ZOI`` group with user 1 as its admin, seeded the same way.
4. Gives user 1 the ``admin`` role in every group. (They are already a global
   admin, which the API treats as admin everywhere; this makes it visible in the
   UI's member list rather than implicit.)

It deliberately does NOT touch rag_documents.group_id: existing library documents
are already stamped with their group, and the folders they sit in stay
group-visible, so the invariant "a document's group_id equals its folder's
placement" already holds for them.
"""
import asyncio
import logging
import sys

from sqlalchemy import select, update

from database import async_session_maker
from models.document_folder import (
    FOLDER_KIND_CUSTOM,
    FOLDER_KIND_ROOT,
    FOLDER_VISIBILITY_GROUP,
    DocumentFolder,
)
from models.group import Group, GroupMember
from models.user import User
from utils.folder_seed import ensure_group_folders, ensure_member_folder

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate")

ADMIN_USER_ID = 1
NEW_GROUP_NAME = "UTIA ZOI"
NEW_GROUP_DESCRIPTION = "ÚTIA — Department of Image Processing (ZOI)"


async def seed_group(db, group: Group) -> None:
    """Give one group its root, its ``common``, and a folder per member."""
    root, common = await ensure_group_folders(db, group)
    logger.info("group %s (%s): root=%s common=%s", group.id, group.name, root.id, common.id)

    members = list((await db.execute(
        select(GroupMember).where(GroupMember.group_id == group.id)
    )).scalars().all())
    for membership in members:
        user = (await db.execute(
            select(User).where(User.id == membership.user_id)
        )).scalar_one_or_none()
        folder = await ensure_member_folder(db, group, user)
        if folder is not None:
            logger.info("  private folder for %s: %s", getattr(user, "name", "?"), folder.id)

    # Existing top-level folders join the group's tree instead of floating beside
    # it. They keep visibility='group', so nobody loses sight of anything.
    moved = await db.execute(
        update(DocumentFolder)
        .where(
            DocumentFolder.group_id == group.id,
            DocumentFolder.parent_id.is_(None),
            DocumentFolder.kind == FOLDER_KIND_CUSTOM,
            DocumentFolder.id != root.id,
        )
        .values(parent_id=root.id, visibility=FOLDER_VISIBILITY_GROUP)
    )
    if moved.rowcount:
        logger.info("  re-parented %s existing folder(s) under the root", moved.rowcount)


async def main() -> int:
    async with async_session_maker() as db:
        admin = (await db.execute(
            select(User).where(User.id == ADMIN_USER_ID)
        )).scalar_one_or_none()
        if admin is None:
            logger.error("user %s not found -- refusing to guess an owner", ADMIN_USER_ID)
            return 1

        groups = list((await db.execute(select(Group).order_by(Group.id))).scalars().all())
        logger.info("found %s existing group(s)", len(groups))
        for group in groups:
            await seed_group(db, group)

        # --- the new group -------------------------------------------------
        existing = (await db.execute(
            select(Group).where(Group.name == NEW_GROUP_NAME)
        )).scalar_one_or_none()
        if existing is None:
            new_group = Group(
                name=NEW_GROUP_NAME,
                description=NEW_GROUP_DESCRIPTION,
                created_by_user_id=admin.id,
            )
            db.add(new_group)
            await db.flush()
            db.add(GroupMember(group_id=new_group.id, user_id=admin.id, role="admin"))
            await db.flush()
            logger.info("created group %s (%s)", new_group.id, NEW_GROUP_NAME)
        else:
            new_group = existing
            logger.info("group %s already exists (id %s)", NEW_GROUP_NAME, new_group.id)
            if not (await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == new_group.id,
                    GroupMember.user_id == admin.id,
                )
            )).scalar_one_or_none():
                db.add(GroupMember(group_id=new_group.id, user_id=admin.id, role="admin"))
                await db.flush()
        await seed_group(db, new_group)

        # --- the admin's role in every group --------------------------------
        promoted = await db.execute(
            update(GroupMember)
            .where(GroupMember.user_id == admin.id, GroupMember.role != "admin")
            .values(role="admin")
        )
        if promoted.rowcount:
            logger.info("promoted user %s to admin in %s group(s)", admin.id, promoted.rowcount)

        await db.commit()

    logger.info("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
