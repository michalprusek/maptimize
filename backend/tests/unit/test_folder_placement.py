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
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models.document_folder import (
    DocumentFolder,
    FOLDER_KIND_COMMON,
    FOLDER_KIND_ROOT,
    FOLDER_KIND_USER,
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


# --- the group root is the one node whose subtree is NOT uniform -------------

def _seeded(fid, kind, *, visibility, parent_id=None, group_id=2):
    folder = _folder(fid, visibility=visibility, group_id=group_id, parent_id=parent_id)
    folder.kind = kind
    return folder


async def test_the_walk_never_rewrites_a_seeded_folder(mock_db):
    """A group root holds `common` (group-visible) beside one private folder per
    member -- attached directly by folder_seed, not by inheritance. It is the one
    folder in the tree whose children are NOT visibility-uniform.

    Overwriting a member's private folder with the root's own visibility, and
    re-stamping the documents inside it, publishes their whole library to the
    group. Silently, and permanently: nothing recomputes it back.
    """
    root = _seeded(1, FOLDER_KIND_ROOT, visibility=FOLDER_VISIBILITY_GROUP)
    common = _seeded(2, FOLDER_KIND_COMMON, visibility=FOLDER_VISIBILITY_GROUP, parent_id=1)
    private = _seeded(3, FOLDER_KIND_USER, visibility=FOLDER_VISIBILITY_PRIVATE, parent_id=1)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[common, private]),  # children of the root
        make_result(rowcount=0),                     # the document UPDATE
    ]

    await apply_subtree_placement(mock_db, root)

    assert private.visibility == FOLDER_VISIBILITY_PRIVATE, \
        "the member's private folder was published to the group"
    assert common.visibility == FOLDER_VISIBILITY_GROUP

    stmt = mock_db.execute.call_args_list[-1].args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "folder_id IN (1)" in sql, \
        f"documents under a seeded folder were re-stamped: {sql}"


async def test_an_unfiled_upload_lands_in_the_groups_common_folder(mock_db):
    """"Where does a plain upload go" and "is the placement invariant true" are
    the same question. Answering `common` makes it true with no exception: the
    document is filed, so its group_id comes from its folder like every other."""
    from utils.folder_seed import default_upload_folder

    common = _seeded(2, FOLDER_KIND_COMMON, visibility=FOLDER_VISIBILITY_GROUP, parent_id=1)
    mock_db.execute.return_value = make_result(scalars_all=[common])

    folder = await default_upload_folder(mock_db, [2])

    assert folder is common
    assert placement_group_id(folder) == 2, "filing it there must share it"


async def test_a_member_of_several_groups_gets_no_default_folder(mock_db):
    """Same rule as default_group_id: guessing which group the user meant would
    publish work to the wrong audience. They pick a folder, or it stays private."""
    from utils.folder_seed import default_upload_folder

    assert await default_upload_folder(mock_db, [2, 5]) is None
    assert await default_upload_folder(mock_db, []) is None
    mock_db.execute.assert_not_called()


def test_no_startup_job_restamps_a_documents_group():
    """`rag_documents.group_id IS NULL` changed meaning under this feature.

    It used to mark "not yet backfilled", so a startup UPDATE that stamped every
    such row with its owner's group was correct. Once folders could hold
    documents it became the ACL state meaning PRIVATE -- exactly what
    placement_group_id returns for a private folder -- so the same UPDATE
    published every private folder's contents to the group, on every restart, and
    with many-to-many membership `UPDATE ... FROM group_members` picked an
    arbitrary one of the owner's groups. Observed in production: 45 documents in
    a private UTIA ZOI folder stamped with Dr. Janke Lab.

    The backfill has served its purpose and is gone. Nothing may write
    rag_documents.group_id outside utils.folder_placement.
    """
    src = pathlib.Path(__file__).resolve().parents[2] / "database.py"
    text = src.read_text()
    assert "rag_documents SET group_id" not in text, (
        "a bulk write to rag_documents.group_id cannot know which folder a "
        "document sits in, so it cannot honour the placement invariant"
    )
    assert "backfill_doc_group" not in text


def test_every_document_creator_files_what_it_created():
    """`save_uploaded_document` returns group_id=None by design, so FILING is the
    caller's job -- and a caller that forgets produces an unfiled, owner-only
    document with no error anywhere.

    import_discovered forgot exactly this: it was the one call site still relying
    on the service to stamp a group, so every imported paper became invisible to
    the lab the moment the service stopped. Asserted on the shape of the code
    because the alternative is discovering it from a colleague saying "I can't
    find that paper".
    """
    import re

    src = pathlib.Path(__file__).resolve().parents[2] / "routers" / "rag.py"
    text = src.read_text()
    creators = [
        m.start() for m in re.finditer(
            r"await (save_uploaded_document|index_text_snippet)\(", text
        )
    ]
    assert creators, "no document creators found -- the guard would be vacuous"
    for pos in creators:
        window = text[pos:pos + 1600]
        assert "file_document(" in window, (
            f"a document created at offset {pos} is never filed; it would be "
            "unfiled and owner-only"
        )


async def test_filing_sets_both_halves_together(mock_db):
    """One call, both fields -- the pair used to be open-coded at four sites, and
    the fourth was the one that drifted."""
    from types import SimpleNamespace

    from utils.folder_placement import file_document

    doc = SimpleNamespace(folder_id=None, group_id=None)
    shared = _folder(9, visibility=FOLDER_VISIBILITY_GROUP, group_id=2)
    file_document(doc, shared)
    assert (doc.folder_id, doc.group_id) == (9, 2)

    private = _folder(9, visibility=FOLDER_VISIBILITY_PRIVATE, group_id=2)
    file_document(doc, private)
    assert (doc.folder_id, doc.group_id) == (9, None)

    file_document(doc, None)
    assert (doc.folder_id, doc.group_id) == (None, None)


async def test_an_unfiled_document_is_owner_only(mock_db):
    """With `common` as the default, nothing reaches the library root by accident
    -- so the root can mean what placement_group_id has always said it means."""
    import inspect

    from services import document_indexing_service as dind

    for fn in (dind.save_uploaded_document, dind.index_text_snippet):
        src = inspect.getsource(fn)
        assert "default_group_id" not in src, (
            f"{fn.__name__} still stamps a group without a folder, so a document "
            "at the root disagrees with placement_group_id(None)"
        )


async def test_dissolving_a_folder_walks_what_moved_not_its_siblings(mock_db):
    """The reachable version: any member may create a folder under the group root
    and delete it again. Walking the PARENT afterwards would descend into every
    member's private folder; walking the moved children cannot."""
    import inspect

    from routers import folders as folders_router

    src = inspect.getsource(folders_router.delete_folder)
    assert "apply_subtree_placement(db, parent)" not in src, \
        "dissolve walks the surviving parent, whose other children never moved"
    assert "for child in moved" in src
