"""Unit tests for crop-curation access control.

Crops are group-curated annotation data: in this lab one person annotates a batch
and another corrects it, so anyone who can *see* an experiment may fix its
detections. The container (image, experiment) stays owner-only.

These tests lock that boundary in BOTH directions -- the widening for crops and
the absence of widening for images -- because the two policies live one function
apart in ``routers/images.py`` and a well-meaning "simplification" that collapses
them would silently hand every group member the right to delete a colleague's
FOVs.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routers.images as images_router
from tests.unit.conftest import make_result


def _sql(clause) -> str:
    """Render a WHERE clause with bound values inlined, for structural asserts."""
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def _fake_crop(owner_id: int):
    """A crop whose parent experiment is owned by ``owner_id`` and in group 2."""
    experiment = SimpleNamespace(id=10, user_id=owner_id, group_id=2)
    image = SimpleNamespace(id=100, experiment=experiment, width=512, height=512)
    return SimpleNamespace(id=200, image=image)


def _fake_image(owner_id: int):
    experiment = SimpleNamespace(id=10, user_id=owner_id, group_id=2)
    return SimpleNamespace(id=100, experiment=experiment, width=512, height=512)


# ============================================================================
# get_crop_for_curation -- the widening
# ============================================================================


async def test_group_member_may_curate_another_users_crop(mock_db):
    """The regression this policy exists for.

    A crop under Theo's experiment must be curatable by a group peer. The old
    implementation re-checked ``crop.image.experiment.user_id != user_id`` after
    loading and answered "Access denied", so bbox edits on a colleague's batch
    were impossible. There must be no such post-query owner comparison.
    """
    crop = _fake_crop(owner_id=22)  # owned by someone else
    mock_db.execute.side_effect = [
        make_result(scalar=2),       # caller's group
        make_result(scalar=crop),
    ]

    out = await images_router.get_crop_for_curation(mock_db, 200, 1)

    assert out is crop


async def test_crop_curation_query_widens_to_group(mock_db):
    """The scoping is enforced in SQL, not in Python.

    Asserts on ``stmt.whereclause`` rather than ``str(stmt)``: the rendered
    statement also contains the SELECT column list, so a substring check against
    the whole statement passes even when the filter is on a different column.
    """
    mock_db.execute.side_effect = [
        make_result(scalar=7),
        make_result(scalar=_fake_crop(owner_id=1)),
    ]

    await images_router.get_crop_for_curation(mock_db, 200, 1)

    sql = _sql(mock_db.execute.call_args_list[1][0][0].whereclause)
    assert "cell_crops.id = 200" in sql
    assert "experiments.user_id = 1" in sql
    assert "experiments.group_id = 7" in sql


async def test_crop_curation_fails_closed_without_a_group(mock_db):
    """A user in no group sees only their own crops -- never a bare OR TRUE."""
    mock_db.execute.side_effect = [
        make_result(scalar=None),   # no group membership
        make_result(scalar=_fake_crop(owner_id=1)),
    ]

    await images_router.get_crop_for_curation(mock_db, 200, 1)

    sql = _sql(mock_db.execute.call_args_list[1][0][0].whereclause)
    assert "experiments.user_id = 1" in sql
    assert "group_id" not in sql


async def test_crop_outside_scope_is_404_without_leaking_denial(mock_db):
    """Out-of-scope crops are indistinguishable from missing ones.

    The previous code raised 404 carrying the detail "Access denied", which both
    contradicted its own status code and confirmed the row exists to a caller in
    another group.
    """
    mock_db.execute.side_effect = [
        make_result(scalar=2),
        make_result(scalar=None),   # filtered out by the access predicate
    ]

    with pytest.raises(HTTPException) as exc:
        await images_router.get_crop_for_curation(mock_db, 200, 1)

    assert exc.value.status_code == 404
    assert "denied" not in str(exc.value.detail).lower()


# ============================================================================
# get_image_for_curation -- widening for crop creation on someone else's FOV
# ============================================================================


async def test_group_member_may_curate_crops_on_another_users_fov(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=2),
        make_result(scalar=_fake_image(owner_id=22)),
    ]

    out = await images_router.get_image_for_curation(mock_db, 100, 1)

    assert out.id == 100


async def test_image_curation_query_widens_to_group(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=7),
        make_result(scalar=_fake_image(owner_id=1)),
    ]

    await images_router.get_image_for_curation(mock_db, 100, 1)

    sql = _sql(mock_db.execute.call_args_list[1][0][0].whereclause)
    assert "images.id = 100" in sql
    assert "experiments.user_id = 1" in sql
    assert "experiments.group_id = 7" in sql


# ============================================================================
# The boundary: container writes must NOT have widened
# ============================================================================


async def test_image_write_stays_owner_only(mock_db):
    """Deleting or reprocessing an FOV remains the uploader's call.

    A group member can read the image (so this is a 403, not a 404) but must not
    be able to mutate the container itself.
    """
    mock_db.execute.side_effect = [
        make_result(scalar=2),
        make_result(scalar=_fake_image(owner_id=22)),
    ]

    with pytest.raises(HTTPException) as exc:
        await images_router.get_image_for_write(mock_db, 100, 1)

    assert exc.value.status_code == 403


async def test_image_owner_may_still_write(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=2),
        make_result(scalar=_fake_image(owner_id=1)),
    ]

    out = await images_router.get_image_for_write(mock_db, 100, 1)

    assert out.id == 100


async def test_curation_helpers_do_not_reintroduce_a_second_acl_path():
    """The duplicated owner-only guards in crop_editor_service are gone.

    They implemented the same policy a second time and returned "Access denied"
    mapped to 404. If anything reintroduces them, the next ACL change will be
    applied in one place and silently missed in the other.
    """
    import services.crop_editor_service as crop_svc

    for removed in (
        "verify_experiment_ownership",
        "get_image_with_ownership_check",
        "get_crop_with_ownership_check",
    ):
        assert not hasattr(crop_svc, removed), (
            f"{removed} is back in crop_editor_service -- crop ACL must stay in "
            "routers/images.py so there is exactly one place to change it"
        )
