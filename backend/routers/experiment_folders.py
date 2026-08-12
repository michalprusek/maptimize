"""Folders for organising experiments.

A shared tree, not a private one. Théo owns most of the corpus, so a per-user
tree would leave everyone else looking at a hundred unfiled experiments — the
same reason the microscope, PTM and protein assignments are group-writable.

Two rules carry the weight:

* **A folder's group is inherited, never chosen.** A new top-level folder is
  stamped from ``default_group_id``; a subfolder takes its parent's group. A
  subtree that straddled two groups would give "which group is this folder in?"
  two answers depending on where you started reading.
* **Deleting a folder dissolves it.** Subfolders and experiments move up to
  where the folder itself sat, so nothing can disappear behind a deletion.

⚠️ Filing an experiment does NOT change ``experiments.group_id`` — see
``routers.experiments.update_experiment_folder``.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.experiment import Experiment
from models.experiment_folder import ExperimentFolder, experiment_folder_scope
from models.user import User
from schemas.experiment_folder import (
    ExperimentFolderCreate,
    ExperimentFolderResponse,
    ExperimentFolderUpdate,
    ExperimentTreeResponse,
)
from utils.groups import experiment_owner_filter
from utils.groups import default_group_id, get_user_group_ids
from utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# A real folder id comes from a SERIAL and is never 0, so 0 can carry "unfiled"
# on the same parameter instead of a second one whose absence and whose false
# look identical on the wire. Same trick, same reason, as UNASSIGNED_FACET_ID.
UNFILED_FOLDER_ID = 0


@dataclass(frozen=True)
class FolderScope:
    """Which slice of the tree a listing is asking for.

    Three distinct answers and conflating any two is the bug: ``None`` = no
    filter, ``0`` = only the unfiled, an id = that folder.

    A dependency rather than two loose parameters, deliberately. Tests call these
    handlers directly, and any parameter carrying a ``Query(...)`` default
    arrives as that Query object when it is not passed — so ``if folder_id:``
    reads true and ``include_subfolders`` reads true, silently. As one object, a
    caller that forgets it gets an AttributeError instead of a wrong answer.
    """

    folder_id: Optional[int] = None
    include_subfolders: bool = True


def folder_scope(
    folder_id: Optional[int] = Query(
        None, ge=0, description="Folder to list; 0 = unfiled, omit for everything"
    ),
    include_subfolders: bool = Query(
        True, description="Also include folders nested below folder_id"
    ),
) -> FolderScope:
    """Collect the folder filter into the single value it is."""
    return FolderScope(folder_id=folder_id, include_subfolders=include_subfolders)


async def get_folder_for_user(
    db: AsyncSession,
    folder_id: int,
    user_id: int,
    group_ids: Sequence[int],
) -> ExperimentFolder:
    """Fetch a folder the caller may see, or 404.

    404 rather than 403 for a folder in someone else's group: the caller cannot
    tell an id that does not exist from one they may not touch, which is the only
    answer that does not leak the tree's shape.
    """
    result = await db.execute(
        select(ExperimentFolder).where(
            ExperimentFolder.id == folder_id,
            experiment_folder_scope(user_id, group_ids),
        )
    )
    folder = result.scalar_one_or_none()
    if folder is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        )
    return folder


def _response(folder: ExperimentFolder, experiment_count: int = 0):
    return ExperimentFolderResponse(
        id=folder.id,
        name=folder.name,
        parent_id=folder.parent_id,
        group_id=folder.group_id,
        owner_user_id=folder.user_id,
        experiment_count=experiment_count,
        created_at=folder.created_at,
    )


@router.get("", response_model=ExperimentTreeResponse)
async def list_folders(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The whole visible tree in one call, with per-folder experiment counts.

    Unpaginated on purpose: the client needs every node to draw a tree at all,
    and a truncated tree silently loses branches rather than erroring. Folder
    counts are small by nature — one row per folder, no experiments loaded.

    ``unfiled_count`` rides along because nothing is filed under "unfiled", so
    the client has no row to roll up; without it the sidebar would have to fetch
    a whole second listing on page load to show one integer. It is counted over
    the caller's own experiment scope, so it can never report work they cannot
    reach.
    """
    group_ids = await get_user_group_ids(current_user.id, db)

    result = await db.execute(
        select(ExperimentFolder, func.count(Experiment.id))
        .outerjoin(Experiment, Experiment.folder_id == ExperimentFolder.id)
        .where(experiment_folder_scope(current_user.id, group_ids))
        .group_by(ExperimentFolder.id)
        .order_by(ExperimentFolder.name, ExperimentFolder.id)
    )
    folders = [_response(folder, count or 0) for folder, count in result.all()]

    unfiled = await db.scalar(
        select(func.count()).select_from(Experiment).where(
            experiment_owner_filter(current_user.id, group_ids),
            Experiment.folder_id.is_(None),
        )
    )
    return ExperimentTreeResponse(folders=folders, unfiled_count=unfiled or 0)


@router.post("", response_model=ExperimentFolderResponse,
             status_code=status.HTTP_201_CREATED)
async def create_folder(
    data: ExperimentFolderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a folder, optionally nested under a folder the caller can see."""
    group_ids = await get_user_group_ids(current_user.id, db)

    if data.parent_id is not None:
        parent = await get_folder_for_user(
            db, data.parent_id, current_user.id, group_ids
        )
        # Inherited, not chosen: a subtree must not straddle two groups.
        group_id = parent.group_id
    else:
        group_id = default_group_id(group_ids)

    folder = ExperimentFolder(
        name=data.name,
        parent_id=data.parent_id,
        user_id=current_user.id,
        group_id=group_id,
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return _response(folder, 0)


async def _descendant_ids(
    db: AsyncSession,
    folder_id: int,
    user_id: int,
    group_ids: Sequence[int],
) -> set:
    """Every folder below ``folder_id``, walked in Python over the visible tree.

    ``parent_id`` is a plain Integer with no FK, so the tree is small and
    unconstrained; loading the visible folders once and walking them beats a
    recursive CTE here and keeps the cycle check and the walk on the same data.
    """
    result = await db.execute(
        select(ExperimentFolder).where(
            experiment_folder_scope(user_id, group_ids)
        )
    )
    folders = list(result.scalars().all())

    below = set()
    frontier = [folder_id]
    while frontier:
        current = frontier.pop()
        for folder in folders:
            if folder.parent_id == current and folder.id not in below:
                below.add(folder.id)
                frontier.append(folder.id)
    return below


@router.patch("/{folder_id}", response_model=ExperimentFolderResponse)
async def update_folder(
    folder_id: int,
    data: ExperimentFolderUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename and/or move a folder.

    ``parent_id`` omitted leaves the folder where it is; an explicit null moves
    it to the top level. That distinction is why the router reads
    ``exclude_unset`` rather than the field's value — otherwise every rename
    would also move the folder to the root.
    """
    group_ids = await get_user_group_ids(current_user.id, db)
    folder = await get_folder_for_user(db, folder_id, current_user.id, group_ids)

    changes = data.model_dump(exclude_unset=True)

    if "parent_id" in changes:
        new_parent_id = changes["parent_id"]
        if new_parent_id == folder_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A folder cannot be its own parent.",
            )
        if new_parent_id is None:
            folder.parent_id = None
            folder.group_id = default_group_id(group_ids)
        else:
            parent = await get_folder_for_user(
                db, new_parent_id, current_user.id, group_ids
            )
            # A cycle detaches the whole subtree from every listing at once and
            # makes the breadcrumb walk non-terminating. There is no FK to catch
            # it, so it is caught here.
            if new_parent_id in await _descendant_ids(
                db, folder_id, current_user.id, group_ids
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A folder cannot be moved inside one of its own subfolders.",
                )
            folder.parent_id = new_parent_id
            folder.group_id = parent.group_id

    if changes.get("name"):
        folder.name = changes["name"]

    await db.commit()

    count = await db.scalar(
        select(func.count()).select_from(Experiment).where(
            Experiment.folder_id == folder_id
        )
    )
    return _response(folder, count or 0)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a folder, dissolving it: everything inside moves up to its parent.

    Nothing may vanish behind a deleted folder. A folder at the top level
    dissolves its contents to "unfiled", which is where they were before anyone
    made the folder — never to nowhere.
    """
    group_ids = await get_user_group_ids(current_user.id, db)
    folder = await get_folder_for_user(db, folder_id, current_user.id, group_ids)
    parent_id = folder.parent_id

    await db.execute(
        update(ExperimentFolder)
        .where(ExperimentFolder.parent_id == folder_id)
        .values(parent_id=parent_id)
    )
    await db.execute(
        update(Experiment)
        .where(Experiment.folder_id == folder_id)
        .values(folder_id=parent_id)
    )
    await db.delete(folder)
    await db.commit()

    logger.info(
        "User %s dissolved experiment folder %s into %s",
        current_user.id, folder_id, parent_id,
    )
