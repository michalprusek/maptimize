"""Document-library folders (file-explorer tree).

Folders are group-shared: any member of the caller's group can see and organize
them. Deleting a folder dissolves it — its subfolders and documents move up to the
parent — so documents are never lost.
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.document_folder import DocumentFolder
from models.rag_document import RAGDocument
from models.user import User
from utils.groups import get_user_group_id
from utils.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _visible(user_id: int, group_id: Optional[int]):
    if group_id is not None:
        return or_(DocumentFolder.user_id == user_id, DocumentFolder.group_id == group_id)
    return DocumentFolder.user_id == user_id


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

    class Config:
        from_attributes = True


async def _get_folder(db, folder_id, user_id, group_id) -> DocumentFolder:
    row = (await db.execute(
        select(DocumentFolder).where(DocumentFolder.id == folder_id, _visible(user_id, group_id))
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


@router.get("", response_model=List[FolderResponse])
async def list_folders(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    group_id = await get_user_group_id(current_user.id, db)
    rows = (await db.execute(
        select(DocumentFolder).where(_visible(current_user.id, group_id)).order_by(DocumentFolder.name)
    )).scalars().all()
    return list(rows)


@router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: FolderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group_id = await get_user_group_id(current_user.id, db)
    if body.parent_id is not None:
        await _get_folder(db, body.parent_id, current_user.id, group_id)  # parent must be visible
    folder = DocumentFolder(
        user_id=current_user.id, group_id=group_id,
        parent_id=body.parent_id, name=body.name.strip(),
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
    group_id = await get_user_group_id(current_user.id, db)
    folder = await _get_folder(db, folder_id, current_user.id, group_id)
    if body.name is not None:
        folder.name = body.name.strip()
    if "parent_id" in body.model_fields_set:  # a move (parent_id=null means root)
        new_parent = body.parent_id
        if new_parent == folder_id:
            raise HTTPException(status_code=400, detail="A folder cannot be its own parent")
        if new_parent is not None:
            await _get_folder(db, new_parent, current_user.id, group_id)
            if await _is_ancestor(db, folder_id, new_parent):
                raise HTTPException(status_code=400, detail="Cannot move a folder into its own subtree")
        folder.parent_id = new_parent
    return folder


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group_id = await get_user_group_id(current_user.id, db)
    folder = await _get_folder(db, folder_id, current_user.id, group_id)
    # Dissolve: move child folders + documents up to this folder's parent, then delete.
    await db.execute(
        update(DocumentFolder).where(DocumentFolder.parent_id == folder_id).values(parent_id=folder.parent_id)
    )
    await db.execute(
        update(RAGDocument).where(RAGDocument.folder_id == folder_id).values(folder_id=folder.parent_id)
    )
    await db.execute(sql_delete(DocumentFolder).where(DocumentFolder.id == folder_id))
    return None
