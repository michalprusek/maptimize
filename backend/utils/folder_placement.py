"""Keeping a document's visibility equal to its folder's.

``RAGDocument.group_id`` is the ACL truth -- NULL means owner-only, set means
readable by that group -- and that one rule has four implementations:
``_library_visible`` (backing both ``document_scope`` and
``document_dedupe_scope``), ``document_read_scope``, the raw-SQL ``_owner_clause``
in ``rag_service``, and ``_inject_user_id_filter`` in ``sql_query_service``.

Deriving a document's visibility from its folder at read time would mean adding a
folder join to all four and keeping them in step forever. Instead the column is
re-stamped whenever placement changes: on upload into a folder, on moving a
document, and -- across the subtree -- on moving or dissolving a folder. The
document-row ACL is therefore untouched by the folder feature. (Folders have
their own, separate predicate, ``folder_read_scope``; it decides which folders a
caller may see and can narrow a search, but it never widens a document read.)

The invariant this maintains, with no exception: **a document's group_id equals
``placement_group_id`` of the folder it sits in**, and an unfiled document is
owner-only. Anything that changes where a document lives must go through here, or
a private folder will quietly contain group-readable documents.

Uploads keep that true by being FILED rather than stamped: with no folder named
they land in the uploader's group ``common``
(``utils.folder_seed.default_upload_folder``), so the ordinary upload is shared
without the row carrying an audience of its own. A member of several groups has
no unambiguous ``common``, so their upload stays unfiled and private until they
move it -- the same rule, and the same reason, as ``default_group_id``.
"""
import logging
from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.document_folder import (
    FOLDER_VISIBILITY_PRIVATE,
    SEEDED_FOLDER_KINDS,
    DocumentFolder,
)
from models.rag_document import RAGDocument

logger = logging.getLogger(__name__)


def placement_group_id(folder: Optional[DocumentFolder]) -> Optional[int]:
    """The ``group_id`` a document in this folder must carry.

    A private folder yields None even though the folder itself belongs to a
    group's tree: the folder's group membership places it in the tree, its
    visibility decides who reads it. Returning ``folder.group_id`` here would
    hand every private document to the whole group.

    A document at the library root (``folder is None``) is owner-only. Sharing is
    an act: move it into a group's folder.
    """
    if folder is None or folder.visibility == FOLDER_VISIBILITY_PRIVATE:
        return None
    return folder.group_id


async def _children(db: AsyncSession, parent_id: int) -> list[DocumentFolder]:
    return list((await db.execute(
        select(DocumentFolder).where(DocumentFolder.parent_id == parent_id)
    )).scalars().all())


async def apply_subtree_placement(db: AsyncSession, folder: DocumentFolder) -> int:
    """Propagate ``folder``'s visibility down its subtree and re-stamp documents.

    Called after a folder moves. Walks breadth-first from ``folder``, giving each
    descendant the same visibility and group, then issues one UPDATE over all the
    affected folder ids -- so dragging a folder holding fifty documents costs two
    statements, not fifty.

    ⚠️ **A seeded folder owns its own visibility and the walk stops there.**
    Every folder's subtree is visibility-uniform except a group ROOT, which holds
    `common` (group-visible) beside one private folder per member -- those are
    attached directly by ``utils.folder_seed``, not by inheritance. Without this
    guard, walking from a root rewrites every member's private folder to the
    root's visibility and re-stamps the documents inside them, publishing the
    whole lab's private libraries. Silently, and permanently: nothing ever
    recomputes it back.

    Returns the number of folders visited (including ``folder`` itself), which is
    what the caller can log. Does not commit.
    """
    visited: list[DocumentFolder] = [folder]
    seen: set[int] = {folder.id}
    queue = [folder]
    while queue:
        current = queue.pop(0)
        for child in await _children(db, current.id):
            if child.id in seen:
                # parent_id carries no FK, so a bad row can point back up. Worth
                # saying out loud: nothing else will ever notice a loop.
                logger.error(
                    f"Cycle in document_folders: {current.id} -> {child.id}; "
                    f"stopping the walk here"
                )
                continue
            seen.add(child.id)
            if child.kind in SEEDED_FOLDER_KINDS:
                continue
            child.visibility = current.visibility
            child.group_id = current.group_id
            visited.append(child)
            queue.append(child)

    group_id = placement_group_id(folder)
    await db.execute(
        update(RAGDocument)
        .where(RAGDocument.folder_id.in_([f.id for f in visited]))
        .values(group_id=group_id)
    )
    logger.info(
        f"Re-stamped {len(visited)} folder(s) under {folder.id} to group {group_id}"
    )
    return len(visited)


async def resolve_folder_scope(
    db: AsyncSession,
    folder_ids: Optional[Sequence[int]],
    include_subfolders: bool,
    visible_clause,
) -> Optional[list[int]]:
    """Expand a caller's folder selection into the list of folder ids to search.

    Returns None when no folder filter was asked for -- meaning "everything the
    caller can read", which is the default.

    The expansion runs against ``visible_clause`` (the caller's folder ACL), so a
    folder id the caller cannot see contributes nothing rather than widening the
    search. That is why the resulting id list can be handed straight to the
    pgvector query: every id in it is already authorized.
    """
    if not folder_ids:
        return None

    visible = list((await db.execute(
        select(DocumentFolder).where(visible_clause)
    )).scalars().all())
    by_parent: dict[Optional[int], list[DocumentFolder]] = {}
    for f in visible:
        by_parent.setdefault(f.parent_id, []).append(f)

    requested = {int(f) for f in folder_ids}
    selected = {f.id for f in visible if f.id in requested}
    if not include_subfolders:
        return sorted(selected)

    queue = list(selected)
    while queue:
        current = queue.pop()
        for child in by_parent.get(current, []):
            if child.id not in selected:
                selected.add(child.id)
                queue.append(child.id)
    return sorted(selected)
