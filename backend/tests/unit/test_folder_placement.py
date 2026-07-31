"""A document's visibility follows its folder, by re-stamping its own group_id.

The alternative -- deriving visibility from the folder at query time -- would mean
adding a folder join to all four mirrored document-ACL predicates and keeping them
in step forever. So the column is re-stamped instead, and these tests pin every
path that can move a document: the folder itself moving, the folder dissolving,
and the document moving on its own.

The dangerous direction is group -> private: if a re-stamp is missed there, a
folder that looks private to its owner still contains documents the whole group
can read, and nothing in the UI says so.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models.document_folder import (
    DocumentFolder,
    FOLDER_VISIBILITY_GROUP,
    FOLDER_VISIBILITY_PRIVATE,
)
from utils.folder_placement import (
    apply_subtree_placement,
    placement_group_id,
    resolve_folder_scope,
)
from tests.unit.conftest import make_result


def _folder(fid, *, visibility=FOLDER_VISIBILITY_GROUP, group_id=2, parent_id=None):
    return DocumentFolder(
        id=fid, user_id=7, group_id=group_id, parent_id=parent_id,
        name=f"f{fid}", visibility=visibility,
    )


# --- placement_group_id ------------------------------------------------------

def test_a_document_at_the_library_root_is_owner_only():
    assert placement_group_id(None) is None


def test_a_document_in_a_private_folder_is_owner_only():
    """The folder's group_id places it in a tree; its visibility decides who
    reads it. Returning group_id here hands every private document to the group."""
    assert placement_group_id(_folder(1, visibility=FOLDER_VISIBILITY_PRIVATE)) is None


def test_a_document_in_a_group_folder_is_readable_by_that_group():
    assert placement_group_id(_folder(1, visibility=FOLDER_VISIBILITY_GROUP)) == 2


# --- subtree propagation -----------------------------------------------------

async def test_moving_a_folder_into_a_private_one_restamps_the_whole_subtree(mock_db):
    # group_id stays set -- this is a member's private folder inside the group's
    # tree, which is exactly the case where forgetting to clear the DOCUMENT's
    # group_id leaves the whole group reading a private folder's contents.
    parent = _folder(1, visibility=FOLDER_VISIBILITY_PRIVATE, group_id=2)
    child = _folder(2, parent_id=1)
    grandchild = _folder(3, parent_id=2)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[child]),        # children of 1
        make_result(scalars_all=[grandchild]),   # children of 2
        make_result(scalars_all=[]),             # children of 3
        make_result(rowcount=4),                 # the document UPDATE
    ]

    visited = await apply_subtree_placement(mock_db, parent)

    assert visited == 3
    assert child.visibility == FOLDER_VISIBILITY_PRIVATE and child.group_id == 2
    assert grandchild.visibility == FOLDER_VISIBILITY_PRIVATE and grandchild.group_id == 2

    stmt = mock_db.execute.call_args_list[-1].args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "SET group_id=NULL" in sql.replace(" =", "=").replace("= ", "=")
    assert "folder_id IN (1, 2, 3)" in sql


async def test_a_cycle_in_the_tree_does_not_hang_the_walk(mock_db):
    """parent_id has no FK, so a bad row can point back up. Spinning forever
    inside a request is worse than any wrong answer."""
    a = _folder(1)
    b = _folder(2, parent_id=1)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[b]),   # children of 1
        make_result(scalars_all=[a]),   # children of 2 -> back to 1
        make_result(rowcount=0),
    ]
    assert await apply_subtree_placement(mock_db, a) == 2


# --- folder scope resolution -------------------------------------------------

async def test_no_folder_filter_means_everything_the_caller_can_read(mock_db):
    assert await resolve_folder_scope(mock_db, None, True, None) is None
    assert await resolve_folder_scope(mock_db, [], True, None) is None


async def test_a_folder_selection_expands_to_its_subtree(mock_db):
    mock_db.execute.return_value = make_result(scalars_all=[
        _folder(1), _folder(2, parent_id=1), _folder(3, parent_id=2), _folder(9),
    ])
    assert await resolve_folder_scope(mock_db, [1], True, None) == [1, 2, 3]


async def test_subfolders_can_be_excluded(mock_db):
    mock_db.execute.return_value = make_result(scalars_all=[
        _folder(1), _folder(2, parent_id=1),
    ])
    assert await resolve_folder_scope(mock_db, [1], False, None) == [1]


async def test_a_folder_the_caller_cannot_see_is_dropped_not_trusted(mock_db):
    """The expansion runs against the caller's folder ACL, which is what makes
    the resulting id list safe to hand straight to the pgvector query."""
    mock_db.execute.return_value = make_result(scalars_all=[_folder(1)])
    assert await resolve_folder_scope(mock_db, [1, 4242], True, None) == [1]


async def test_asking_only_for_invisible_folders_returns_an_empty_scope(mock_db):
    """Empty list, not None: None means "no filter" and would search everything --
    the opposite of what was asked."""
    mock_db.execute.return_value = make_result(scalars_all=[])
    assert await resolve_folder_scope(mock_db, [4242], True, None) == []
