"""Private folders, inherited visibility, and immutable seeded folders.

A group's tree holds a shared ``common`` folder next to one private folder per
member. "Private" here is absolute: not the group, not the group's admin, not the
global admin. It is the only place in the application an admin cannot read, so it
is worth pinning from both sides.
"""
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
    folder_read_scope,
)
from routers.folders import _reject_if_seeded, inherited_placement


def _sql(clause) -> str:
    return str(clause.compile(compile_kwargs={"literal_binds": True})).lower()


# --- visibility --------------------------------------------------------------

def test_a_peers_private_folder_is_invisible_to_the_rest_of_the_group():
    sql = _sql(folder_read_scope(7, [2]))
    assert "user_id = 7" in sql
    assert "visibility = 'group'" in sql, (
        "without the visibility term every private folder in my group is listed to me"
    )


def test_visibility_with_no_groups_is_owner_only():
    sql = _sql(folder_read_scope(7, []))
    assert "user_id = 7" in sql
    assert "group_id" not in sql


def test_the_group_term_covers_every_group():
    sql = _sql(folder_read_scope(7, [2, 5]))
    assert "in (2, 5)" in sql


# --- inheritance -------------------------------------------------------------

def test_a_subfolder_of_a_private_folder_is_private():
    """Otherwise "make a subfolder" is a one-click way to publish a private
    document to the whole group.

    It keeps the group id: a member's private folder lives inside the group's
    tree (that is how it appears under the group root) while being readable only
    by its owner. Group membership places it; visibility decides who reads it.
    """
    parent = DocumentFolder(
        id=1, user_id=7, group_id=2, visibility=FOLDER_VISIBILITY_PRIVATE
    )
    assert inherited_placement(parent) == (FOLDER_VISIBILITY_PRIVATE, 2)


def test_a_subfolder_of_a_group_folder_is_group_visible():
    parent = DocumentFolder(
        id=1, user_id=7, group_id=2, visibility=FOLDER_VISIBILITY_GROUP
    )
    assert inherited_placement(parent) == (FOLDER_VISIBILITY_GROUP, 2)


def test_a_folder_at_the_library_root_is_private():
    """Nothing is shared by accident: sharing means putting it in a group's tree."""
    assert inherited_placement(None) == (FOLDER_VISIBILITY_PRIVATE, None)


# --- immutable seeded folders ------------------------------------------------

@pytest.mark.parametrize("kind", [FOLDER_KIND_ROOT, FOLDER_KIND_COMMON, FOLDER_KIND_USER])
def test_seeded_folders_cannot_be_renamed_moved_or_deleted(kind):
    """Renaming or deleting a group's `common` would leave every member's mental
    model wrong and orphan the part of the tree they navigate by."""
    with pytest.raises(HTTPException) as exc:
        _reject_if_seeded(DocumentFolder(id=1, user_id=7, name="common", kind=kind))
    assert exc.value.status_code == 400


def test_custom_folders_stay_editable():
    _reject_if_seeded(DocumentFolder(id=1, user_id=7, name="drafts", kind=FOLDER_KIND_CUSTOM))
