"""The folder handlers themselves, not just the helpers they call.

`_reject_if_seeded` was tested in isolation; nothing asserted that
`update_folder` and `delete_folder` actually call it. Delete either call and the
whole suite stayed green while `delete_folder(group_root)` drove `common` and
every member's private folder through `inherited_placement(None)` and re-stamped
the documents inside them -- the group's shared library, silently owner-only and
permanently so.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from models.document_folder import (
    DocumentFolder,
    FOLDER_KIND_COMMON,
    FOLDER_KIND_CUSTOM,
    FOLDER_KIND_ROOT,
    FOLDER_KIND_USER,
    FOLDER_VISIBILITY_GROUP,
    FOLDER_VISIBILITY_PRIVATE,
)
from routers import folders as folders_router
from tests.unit.conftest import make_result


def _user(uid=7):
    return SimpleNamespace(id=uid)


def _folder(fid=1, kind=FOLDER_KIND_CUSTOM, *, parent_id=None,
            visibility=FOLDER_VISIBILITY_GROUP, group_id=2):
    f = DocumentFolder(id=fid, user_id=7, group_id=group_id, parent_id=parent_id,
                       name=f"f{fid}", visibility=visibility)
    f.kind = kind
    return f


@pytest.mark.parametrize("kind", [FOLDER_KIND_ROOT, FOLDER_KIND_COMMON, FOLDER_KIND_USER])
async def test_the_router_refuses_to_rename_a_seeded_folder(mock_db, kind):
    mock_db.execute.side_effect = [
        make_result(scalars_all=[2]),
        make_result(scalar=_folder(kind=kind)),
    ]
    with pytest.raises(HTTPException) as exc:
        await folders_router.update_folder(
            folder_id=1, body=folders_router.FolderUpdate(name="nope"),
            current_user=_user(), db=mock_db,
        )
    assert exc.value.status_code == 400


@pytest.mark.parametrize("kind", [FOLDER_KIND_ROOT, FOLDER_KIND_COMMON, FOLDER_KIND_USER])
async def test_the_router_refuses_to_delete_a_seeded_folder(mock_db, kind):
    """Deleting a group root is the dangerous one: it would drive `common` and
    every private folder through inherited_placement(None)."""
    mock_db.execute.side_effect = [
        make_result(scalars_all=[2]),
        make_result(scalar=_folder(kind=kind)),
    ]
    with pytest.raises(HTTPException) as exc:
        await folders_router.delete_folder(
            folder_id=1, current_user=_user(), db=mock_db
        )
    assert exc.value.status_code == 400


async def test_a_folder_cannot_become_its_own_parent(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalars_all=[2]),
        make_result(scalar=_folder(fid=5)),
    ]
    with pytest.raises(HTTPException) as exc:
        await folders_router.update_folder(
            folder_id=5, body=folders_router.FolderUpdate(parent_id=5),
            current_user=_user(), db=mock_db,
        )
    assert exc.value.status_code == 400


async def test_a_folder_cannot_be_moved_into_its_own_subtree(mock_db):
    """Otherwise the branch is cut loose from the tree and unreachable, while its
    documents keep whatever audience they had."""
    moving = _folder(fid=5)
    target = _folder(fid=9, parent_id=5)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[2]),
        make_result(scalar=moving),
        make_result(scalar=target),
    ]
    with patch.object(folders_router, "_is_ancestor", AsyncMock(return_value=True)):
        with pytest.raises(HTTPException) as exc:
            await folders_router.update_folder(
                folder_id=5, body=folders_router.FolderUpdate(parent_id=9),
                current_user=_user(), db=mock_db,
            )
    assert exc.value.status_code == 400


async def test_moving_a_folder_repropagates_its_subtree(mock_db):
    """The move is the whole reason placement has to cascade: contents follow the
    destination's audience, or a private folder ends up holding shared documents."""
    moving = _folder(fid=5)
    private_target = _folder(fid=9, visibility=FOLDER_VISIBILITY_PRIVATE)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[2]),
        make_result(scalar=moving),
        make_result(scalar=private_target),
    ]
    with patch.object(folders_router, "_is_ancestor", AsyncMock(return_value=False)), \
         patch.object(folders_router, "apply_subtree_placement", AsyncMock()) as cascade:
        await folders_router.update_folder(
            folder_id=5, body=folders_router.FolderUpdate(parent_id=9),
            current_user=_user(), db=mock_db,
        )
    assert moving.visibility == FOLDER_VISIBILITY_PRIVATE
    cascade.assert_awaited_once()


async def test_a_new_subfolder_inherits_rather_than_choosing(mock_db):
    """Otherwise "new subfolder" is a one-click way to publish a private one."""
    parent = _folder(fid=9, visibility=FOLDER_VISIBILITY_PRIVATE)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[2]),
        make_result(scalar=parent),
    ]
    created = await folders_router.create_folder(
        body=folders_router.FolderCreate(name="drafts", parent_id=9),
        current_user=_user(), db=mock_db,
    )
    assert created.visibility == FOLDER_VISIBILITY_PRIVATE
    assert created.group_id == 2, "it stays in the group's tree, just unreadable by it"
