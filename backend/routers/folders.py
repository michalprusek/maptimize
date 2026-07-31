"""Document-library folders (a file-explorer tree).

Each group owns a tree: a root named after the group, a ``common`` folder every
member reads and writes, and one private folder per member (see
``utils.folder_seed``). Folders created outside a group tree are private to their
owner, so nothing is shared by accident -- sharing means putting a document in a
group's folder.

Two rules carry the weight here:

* **Visibility is inherited, never chosen freely.** A subfolder takes its
  parent's ``visibility`` and ``group_id``, so a folder can never be more visible
  than the one holding it. Moving a folder recomputes its whole subtree.
* **A document's own ``group_id`` is re-stamped from its folder**
  (``utils.folder_placement``) rather than derived at read time. The document ACL
  is mirrored in four places; a folder join would have to be kept in step in all
  of them.

Deleting a folder dissolves it -- subfolders and documents move up to the parent
and are re-stamped for their new home -- so documents are never lost, and never
silently promoted to group-visible on the way up.
"""
import logging
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.document_folder import (
    FOLDER_VISIBILITY_GROUP,
    FOLDER_VISIBILITY_PRIVATE,
    SEEDED_FOLDER_KINDS,
    DocumentFolder,
)
from models.group import Group
from models.rag_document import RAGDocument
from models.user import User
from utils.folder_placement import apply_subtree_placement, placement_group_id
from utils.groups import get_user_group_ids
from utils.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _visible(user_id: int, group_ids: Sequence[int]):
    """Folders the caller may see.

    Their own -- private ones included -- plus the group-visible folders of every
    group they belong to. A colleague's private folder carries
    ``visibility='private'`` and a different ``user_id``, so it is excluded here:
    for peers, for the group's admin, and for the global admin. Dropping the
    visibility term would list every member's private folder to the whole group.
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


def inherited_placement(parent: Optional[DocumentFolder]) -> Tuple[str, Optional[int]]:
    """The ``(visibility, group_id)`` a child of ``parent`` must take.

    The two are separate axes and stay separate: ``group_id`` says which group's
    tree the folder sits in, ``visibility`` says who may read it. A member's
    private folder is *in* the group's tree (so it appears under the group root)
    while being readable only by its owner -- which is why a child of it inherits
    the group AND the privacy, rather than being cut loose from the tree.

    At the library root (no parent) a folder is private and belongs to no tree:
    nothing becomes shared unless it is put inside a group's folder.
    """
    if parent is None:
        return FOLDER_VISIBILITY_PRIVATE, None
    return parent.visibility, parent.group_id


def _reject_if_seeded(folder: DocumentFolder) -> None:
    """Seeded folders are structure, not content.

    A group's root, its ``common`` and a member's private folder are created by
    ``utils.folder_seed`` and every member navigates by them. Letting one be
    renamed or deleted would leave the rest of the group looking for a folder
    that no longer exists, so the API refuses.
    """
    if folder.kind in SEEDED_FOLDER_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The '{folder.name}' folder is part of the group structure and cannot be changed",
        )


class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: Optional[int] = None


class FolderUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    parent_id: Optional[int] = None  # set (incl. null=root) to move


class FolderResponse(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    created_at: datetime
    # Where this folder sits and who can see it. The agent reconstructs the tree
    # from parent_id and needs the rest to know which group's `common` it is
    # looking at, and which folders are somebody's private space.
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    visibility: str = FOLDER_VISIBILITY_GROUP
    kind: str = "custom"
    owner_user_id: Optional[int] = None
    path: str = ""
    document_count: int = 0

    class Config:
        from_attributes = True


async def _get_folder(db, folder_id, user_id, group_ids) -> DocumentFolder:
    row = (await db.execute(
        select(DocumentFolder).where(DocumentFolder.id == folder_id, _visible(user_id, group_ids))
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    return row


async def _is_ancestor(db, ancestor_id: int, node_id: Optional[int]) -> bool:
    """True if ancestor_id is on the parent chain of node_id (cycle guard)."""
    seen: set[int] = set()
    cur = node_id
    while cur is not None and cur not in seen:
        if cur == ancestor_id:
            return True
        seen.add(cur)
        cur = (await db.execute(
            select(DocumentFolder.parent_id).where(DocumentFolder.id == cur)
        )).scalar_one_or_none()
    return False


def _build_paths(folders: List[DocumentFolder]) -> dict[int, str]:
    """Slash-separated path per folder, built once for the whole listing.

    Walking parents per row would be N queries; the listing already holds every
    folder the caller can see, so the chain is resolvable in memory. A parent
    that is not visible (a private folder of someone else) simply stops the walk,
    which is why paths are display strings and never an identifier.
    """
    by_id = {f.id: f for f in folders}
    paths: dict[int, str] = {}

    def resolve(folder: DocumentFolder, seen: frozenset) -> str:
        if folder.id in paths:
            return paths[folder.id]
        parent = by_id.get(folder.parent_id) if folder.parent_id else None
        if parent is None or parent.id in seen:
            path = folder.name
        else:
            path = f"{resolve(parent, seen | {folder.id})}/{folder.name}"
        paths[folder.id] = path
        return path

    for f in folders:
        resolve(f, frozenset())
    return paths


@router.get("", response_model=List[FolderResponse])
async def list_folders(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Every folder the caller can see, across all their groups.

    Carries the group name, visibility and a document count so a client -- the UI
    or the agent -- can render the tree and choose a search scope without a
    second round trip per folder.
    """
    group_ids = await get_user_group_ids(current_user.id, db)
    folders = list((await db.execute(
        select(DocumentFolder)
        .where(_visible(current_user.id, group_ids))
        .order_by(DocumentFolder.name)
    )).scalars().all())

    group_names: dict[int, str] = {}
    referenced = {f.group_id for f in folders if f.group_id is not None}
    if referenced:
        rows = await db.execute(
            select(Group.id, Group.name).where(Group.id.in_(referenced))
        )
        group_names = {gid: name for gid, name in rows.all()}

    # One grouped count, not one query per folder.
    counts: dict[int, int] = {}
    if folders:
        rows = await db.execute(
            select(RAGDocument.folder_id, func.count(RAGDocument.id))
            .where(RAGDocument.folder_id.in_([f.id for f in folders]))
            .group_by(RAGDocument.folder_id)
        )
        counts = {fid: n for fid, n in rows.all()}

    paths = _build_paths(folders)
    return [
        FolderResponse(
            id=f.id,
            name=f.name,
            parent_id=f.parent_id,
            created_at=f.created_at,
            group_id=f.group_id,
            group_name=group_names.get(f.group_id),
            visibility=f.visibility,
            kind=f.kind,
            owner_user_id=f.user_id,
            path=paths.get(f.id, f.name),
            document_count=counts.get(f.id, 0),
        )
        for f in folders
    ]


@router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: FolderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a folder. Its visibility is inherited from its parent, never chosen."""
    group_ids = await get_user_group_ids(current_user.id, db)
    parent = None
    if body.parent_id is not None:
        parent = await _get_folder(db, body.parent_id, current_user.id, group_ids)

    visibility, group_id = inherited_placement(parent)
    folder = DocumentFolder(
        user_id=current_user.id,
        group_id=group_id,
        parent_id=body.parent_id,
        name=body.name.strip(),
        visibility=visibility,
    )
    db.add(folder)
    await db.flush()
    await db.refresh(folder)
    return folder


@router.patch("/{folder_id}", response_model=FolderResponse)
async def update_folder(
    folder_id: int,
    body: FolderUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group_ids = await get_user_group_ids(current_user.id, db)
    folder = await _get_folder(db, folder_id, current_user.id, group_ids)
    _reject_if_seeded(folder)

    if body.name is not None:
        folder.name = body.name.strip()
    if "parent_id" in body.model_fields_set:  # a move (parent_id=null means root)
        new_parent = body.parent_id
        if new_parent == folder_id:
            raise HTTPException(status_code=400, detail="A folder cannot be its own parent")
        parent = None
        if new_parent is not None:
            parent = await _get_folder(db, new_parent, current_user.id, group_ids)
            if await _is_ancestor(db, folder_id, new_parent):
                raise HTTPException(status_code=400, detail="Cannot move a folder into its own subtree")
        folder.parent_id = new_parent
        # The destination decides visibility, and it must reach the whole subtree
        # and the documents in it: dragging a folder out of `common` into a
        # private one has to stop the group reading what is inside it.
        folder.visibility, folder.group_id = inherited_placement(parent)
        await apply_subtree_placement(db, folder)
    return folder


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group_ids = await get_user_group_ids(current_user.id, db)
    folder = await _get_folder(db, folder_id, current_user.id, group_ids)
    _reject_if_seeded(folder)

    # Dissolve: child folders and documents move up to this folder's parent. They
    # take the parent's placement, so contents never gain visibility by the
    # deletion of the folder that was containing them.
    parent = None
    if folder.parent_id is not None:
        parent = (await db.execute(
            select(DocumentFolder).where(DocumentFolder.id == folder.parent_id)
        )).scalar_one_or_none()
    visibility, group_id = inherited_placement(parent)

    await db.execute(
        update(DocumentFolder)
        .where(DocumentFolder.parent_id == folder_id)
        .values(parent_id=folder.parent_id, visibility=visibility, group_id=group_id)
    )
    await db.execute(
        update(RAGDocument)
        .where(RAGDocument.folder_id == folder_id)
        .values(folder_id=folder.parent_id, group_id=placement_group_id(parent))
    )
    await db.execute(sql_delete(DocumentFolder).where(DocumentFolder.id == folder_id))

    # The children that just moved up carry subtrees of their own.
    if folder.parent_id is not None and parent is not None:
        await apply_subtree_placement(db, parent)
    return None
