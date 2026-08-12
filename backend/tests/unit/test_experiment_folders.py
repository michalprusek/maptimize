"""Folders for experiments — the tree, and the two boundaries around it.

Two things here are easy to get wrong in a way nothing fails on:

* **Filing must not change who can read an experiment.** The document library
  re-stamps a document's group from its folder; doing the same here would mean
  dropping a colleague's experiment into a folder silently changed its audience.
* **Filing is group-writable, renaming and deleting the experiment are not.**
  Théo owns most of the corpus, so owner-only filing leaves the tree empty for
  everyone else — the same reason the microscope, PTM and protein endpoints are
  group-writable. The container itself still belongs to whoever uploaded it.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from models.experiment_folder import ExperimentFolder, experiment_folder_scope
from routers import experiment_folders as mod
from routers import experiments as exp_mod
from schemas.experiment_folder import ExperimentFolderCreate, ExperimentFolderUpdate
from tests.unit.conftest import make_result


def _user(uid=1):
    return SimpleNamespace(id=uid, name="Tester", email="t@x.cz")


def _folder(fid=1, name="Batch 1", parent_id=None, user_id=1, group_id=7):
    return SimpleNamespace(
        id=fid, name=name, parent_id=parent_id, user_id=user_id,
        group_id=group_id, created_at=None,
    )


@pytest.fixture
def one_group():
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[7])), \
         patch.object(exp_mod, "get_user_group_ids", new=AsyncMock(return_value=[7])):
        yield


@pytest.fixture
def no_group():
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[])), \
         patch.object(exp_mod, "get_user_group_ids", new=AsyncMock(return_value=[])):
        yield


# =============================================================================
# Scope
# =============================================================================

def test_scope_without_a_group_is_owner_only():
    clause = experiment_folder_scope(1, [])
    rendered = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "user_id" in rendered
    # Never `IN ()` — an error in one dialect, a silent false in another.
    assert "group_id" not in rendered


def test_scope_widens_to_every_group_the_caller_belongs_to():
    clause = experiment_folder_scope(1, [7, 9])
    rendered = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "user_id" in rendered and "group_id" in rendered
    assert "7" in rendered and "9" in rendered


def test_scope_has_no_visibility_term():
    # Deliberate: an experiment is group-readable wherever it sits, so a private
    # folder would hide the folder and not its contents.
    assert "visibility" not in str(experiment_folder_scope(1, [7]))
    assert not hasattr(ExperimentFolder, "visibility")


# =============================================================================
# Create / rename / move
# =============================================================================

async def test_create_stamps_the_only_group_the_caller_belongs_to(mock_db, one_group):
    mock_db.execute.return_value = make_result(scalar=None)
    mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", 3)
    await mod.create_folder(
        ExperimentFolderCreate(name="Batch 1"), current_user=_user(), db=mock_db
    )
    created = mock_db.add.call_args[0][0]
    assert created.group_id == 7


async def test_create_leaves_a_multi_group_member_unshared(mock_db):
    # Guessing which group they meant would publish work to an audience they
    # never chose; unshared is the mistake that is trivial to correct.
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[7, 9])):
        mock_db.execute.return_value = make_result(scalar=None)
        mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", 3)
        await mod.create_folder(
            ExperimentFolderCreate(name="Batch 1"), current_user=_user(), db=mock_db
        )
    assert mock_db.add.call_args[0][0].group_id is None


async def test_a_subfolder_inherits_its_parents_group(mock_db):
    # Otherwise a subtree straddles two groups and "which group is this folder
    # in?" has two answers depending on where you start reading.
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[7, 9])):
        mock_db.execute.return_value = make_result(scalar=_folder(fid=1, group_id=9))
        mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", 3)
        await mod.create_folder(
            ExperimentFolderCreate(name="Sub", parent_id=1),
            current_user=_user(), db=mock_db,
        )
    assert mock_db.add.call_args[0][0].group_id == 9


async def test_create_under_an_invisible_parent_is_404(mock_db, one_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.create_folder(
            ExperimentFolderCreate(name="Sub", parent_id=99),
            current_user=_user(), db=mock_db,
        )
    assert ei.value.status_code == 404


async def test_move_into_own_descendant_is_rejected(mock_db, one_group):
    # A cycle detaches the whole subtree from every listing at once, and the
    # breadcrumb walk would spin.
    parent = _folder(fid=1)
    child = _folder(fid=2, parent_id=1)
    mock_db.execute.side_effect = [
        make_result(scalar=parent),                 # the folder being moved
        make_result(scalar=child),                  # the new parent
        make_result(scalars_all=[parent, child]),   # tree for the cycle check
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.update_folder(
            1, ExperimentFolderUpdate(parent_id=2), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_move_into_itself_is_rejected(mock_db, one_group):
    mock_db.execute.side_effect = [make_result(scalar=_folder(fid=1))]
    with pytest.raises(HTTPException) as ei:
        await mod.update_folder(
            1, ExperimentFolderUpdate(parent_id=1), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_rename_keeps_the_folder_where_it_is(mock_db, one_group):
    folder = _folder(fid=1, parent_id=5)
    mock_db.execute.side_effect = [
        make_result(scalar=folder),
        make_result(scalar=0),  # experiment count for the response
    ]
    await mod.update_folder(
        1, ExperimentFolderUpdate(name="Renamed"), current_user=_user(), db=mock_db
    )
    assert folder.name == "Renamed"
    assert folder.parent_id == 5


# =============================================================================
# Delete dissolves
# =============================================================================

async def test_delete_moves_children_up_rather_than_removing_them(mock_db, one_group):
    # Nothing may disappear behind a deleted folder. Subfolders and experiments
    # are re-parented to where the folder itself sat.
    folder = _folder(fid=4, parent_id=1)
    mock_db.execute.side_effect = [
        make_result(scalar=folder),   # get folder
        make_result(rowcount=2),      # subfolders moved up
        make_result(rowcount=9),      # experiments moved up
    ]
    await mod.delete_folder(4, current_user=_user(), db=mock_db)
    statements = [str(call.args[0]) for call in mock_db.execute.await_args_list]
    assert any("UPDATE experiment_folders" in s for s in statements)
    assert any("UPDATE experiments" in s for s in statements)
    mock_db.delete.assert_awaited_once_with(folder)


# =============================================================================
# Filing an experiment — the fifth group-writable exception
# =============================================================================

async def test_filing_is_group_writable(mock_db, one_group):
    # A colleague's experiment can be filed: whoever can see it can organise it,
    # the same rule as the microscope / PTM / protein assignments.
    experiment = SimpleNamespace(id=5, user_id=999, group_id=7, folder_id=None)
    with patch.object(exp_mod, "get_experiment_for_user",
                      new=AsyncMock(return_value=experiment)), \
         patch.object(exp_mod, "load_experiment_response",
                      new=AsyncMock(return_value="ok")):
        mock_db.execute.return_value = make_result(scalar=_folder(fid=3))
        await exp_mod.update_experiment_folder(
            5, folder_id=3, current_user=_user(), db=mock_db
        )
    assert experiment.folder_id == 3


async def test_filing_does_not_change_who_can_read_the_experiment(mock_db):
    # The document library re-stamps a document's group from its folder. Doing
    # that here would let filing publish a colleague's work to a group the owner
    # never chose.
    experiment = SimpleNamespace(id=5, user_id=999, group_id=None, folder_id=None)
    with patch.object(exp_mod, "get_user_group_ids", new=AsyncMock(return_value=[7])), \
         patch.object(exp_mod, "get_experiment_for_user",
                      new=AsyncMock(return_value=experiment)), \
         patch.object(exp_mod, "load_experiment_response",
                      new=AsyncMock(return_value="ok")):
        mock_db.execute.return_value = make_result(scalar=_folder(fid=3, group_id=7))
        await exp_mod.update_experiment_folder(
            5, folder_id=3, current_user=_user(), db=mock_db
        )
    assert experiment.group_id is None


async def test_filing_into_an_invisible_folder_is_404(mock_db, one_group):
    experiment = SimpleNamespace(id=5, user_id=1, group_id=7, folder_id=None)
    with patch.object(exp_mod, "get_experiment_for_user",
                      new=AsyncMock(return_value=experiment)):
        mock_db.execute.return_value = make_result(scalar=None)
        with pytest.raises(HTTPException) as ei:
            await exp_mod.update_experiment_folder(
                5, folder_id=99, current_user=_user(), db=mock_db
            )
    assert ei.value.status_code == 404


async def test_unfiling_needs_no_folder_lookup(mock_db, one_group):
    experiment = SimpleNamespace(id=5, user_id=1, group_id=7, folder_id=3)
    with patch.object(exp_mod, "get_experiment_for_user",
                      new=AsyncMock(return_value=experiment)), \
         patch.object(exp_mod, "load_experiment_response",
                      new=AsyncMock(return_value="ok")):
        await exp_mod.update_experiment_folder(
            5, folder_id=None, current_user=_user(), db=mock_db
        )
    assert experiment.folder_id is None
    mock_db.execute.assert_not_awaited()


# =============================================================================
# Listing scoped to a folder
# =============================================================================

def _where(mock_db, index=0) -> str:
    """The WHERE clause of one statement the listing ran — never the whole SQL.

    `str(stmt)` renders the SELECT column list too, and `experiments.folder_id`
    is one of those columns: asserting on the full text passes whether or not the
    filter was applied, which is how the first version of these tests was
    vacuous. Same trap CLAUDE.md records for the dedupe queries.
    """
    return str(mock_db.execute.await_args_list[index].args[0].whereclause)


async def _list(mock_db, scope):
    """Drive `list_experiments` as a request with no pagination would.

    `skip`/`limit` carry `Query(...)` defaults, so a direct call that omits them
    hands SQLAlchemy the Query object itself. The folder filter is a dependency
    for exactly that reason; these two predate it.
    """
    return await exp_mod.list_experiments(
        skip=0, limit=None, scope=scope,
        current_user=_user(), db=mock_db,
    )


async def test_listing_without_a_folder_filter_carries_no_folder_clause(mock_db, one_group):
    mock_db.execute.return_value = make_result(fetchall=[])
    await _list(mock_db, exp_mod.FolderScope())
    assert "folder_id" not in _where(mock_db)


async def test_listing_a_folder_filters_to_exactly_that_folder(mock_db, one_group):
    mock_db.execute.side_effect = [
        make_result(scalar=_folder(fid=3)),  # the folder must be visible
        make_result(fetchall=[]),
    ]
    await _list(mock_db, exp_mod.FolderScope(folder_id=3, include_subfolders=False))
    assert "experiments.folder_id = " in _where(mock_db, 1)


async def test_unfiled_is_a_filter_and_not_the_absence_of_one(mock_db, one_group):
    # `folder_id=0` means "show me what nobody has filed" — the same sentinel the
    # UMAP facets use, and for the same reason: a real id is never 0, so it can
    # ride the same parameter instead of adding a second one whose absence and
    # whose false look identical on the wire.
    mock_db.execute.return_value = make_result(fetchall=[])
    await _list(mock_db, exp_mod.FolderScope(folder_id=0, include_subfolders=False))
    assert "experiments.folder_id IS NULL" in _where(mock_db)


async def test_listing_a_folder_with_subfolders_includes_the_whole_subtree(mock_db, one_group):
    # A parent folder reading "0 experiments" while its children hold the batch
    # is the failure this exists to avoid.
    parent = _folder(fid=3)
    child = _folder(fid=4, parent_id=3)
    mock_db.execute.side_effect = [
        make_result(scalar=parent),                # visibility check
        make_result(scalars_all=[parent, child]),  # tree walk
        make_result(fetchall=[]),
    ]
    await _list(mock_db, exp_mod.FolderScope(folder_id=3, include_subfolders=True))
    assert "experiments.folder_id IN " in _where(mock_db, 2)


async def test_listing_an_invisible_folder_is_404(mock_db, one_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await _list(mock_db, exp_mod.FolderScope(folder_id=99, include_subfolders=False))
    assert ei.value.status_code == 404


# =============================================================================
# The tree payload carries the counts the sidebar cannot derive
# =============================================================================

async def test_the_tree_reports_how_many_experiments_are_unfiled(mock_db, one_group):
    """"Unfiled" is a view the client cannot count for itself.

    Every other row in the sidebar sums `experiment_count` over its subtree, but
    nothing is filed under "unfiled" — the alternative was a second full listing
    on page load purely to get one integer.
    """
    mock_db.execute.return_value = make_result(fetchall=[(_folder(fid=1), 4)])
    mock_db.scalar.return_value = 12
    out = await mod.list_folders(current_user=_user(), db=mock_db)
    assert out.unfiled_count == 12
    assert [f.id for f in out.folders] == [1]
    assert out.folders[0].experiment_count == 4


async def test_the_unfiled_count_is_scoped_like_the_folders(mock_db, one_group):
    # A count taken over a wider population than the rows would tell the reader
    # there is unsorted work they cannot see.
    mock_db.execute.return_value = make_result(fetchall=[])
    mock_db.scalar.return_value = 0
    await mod.list_folders(current_user=_user(), db=mock_db)
    counted = str(mock_db.scalar.await_args[0][0].whereclause)
    assert "folder_id IS NULL" in counted
    assert "group_id" in counted and "user_id" in counted


# =============================================================================
# Naming a group explicitly
#
# `default_group_id` returns None for someone in several groups — correct, since
# guessing would publish to an audience they never chose. But the document
# library's escape hatch is a SEEDED group root to nest under, and this tree has
# none: without an explicit choice, a member of two groups could not start a
# shared tree at all. One of the two people who would use this is in two groups.
# =============================================================================

async def test_a_multi_group_member_can_name_the_group(mock_db):
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[7, 9])):
        mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", 3)
        await mod.create_folder(
            ExperimentFolderCreate(name="Shared", group_id=9),
            current_user=_user(), db=mock_db,
        )
    assert mock_db.add.call_args[0][0].group_id == 9


async def test_naming_a_group_you_do_not_belong_to_is_rejected(mock_db, one_group):
    # Otherwise the folder is a way to publish into a group you cannot see.
    with pytest.raises(HTTPException) as ei:
        await mod.create_folder(
            ExperimentFolderCreate(name="Shared", group_id=9),
            current_user=_user(), db=mock_db,
        )
    assert ei.value.status_code == 400


async def test_a_named_group_is_ignored_for_a_subfolder(mock_db):
    # The parent decides, always: a subtree that straddled two groups would give
    # "which group is this in?" two answers depending on where you start.
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[7, 9])):
        mock_db.execute.return_value = make_result(scalar=_folder(fid=1, group_id=7))
        mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", 3)
        await mod.create_folder(
            ExperimentFolderCreate(name="Sub", parent_id=1, group_id=9),
            current_user=_user(), db=mock_db,
        )
    assert mock_db.add.call_args[0][0].group_id == 7
