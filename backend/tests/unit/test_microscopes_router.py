"""Microscopes router unit tests (handlers called directly, mocked db)."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.unit.conftest import make_result
from routers import microscopes as mod
from schemas.microscope import MicroscopeCreate, MicroscopeUpdate
from utils.colors import COLOR_PALETTE


def _user():
    return SimpleNamespace(id=1, name="Tester")


def _micro(**kw):
    base = dict(id=1, name="m", manufacturer=None, model=None, objective=None,
                magnification=None, description=None, color=None, created_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


async def test_create_microscope_auto_color(mock_db):
    # No name conflict, no existing colors → auto-picks palette[0].
    mock_db.execute.side_effect = [
        make_result(scalar=None),          # name-unique check
        make_result(fetchall=[]),          # used-colors query
    ]

    def _assign_id(obj):
        obj.id = 1  # a real commit+refresh would populate the PK

    mock_db.refresh.side_effect = _assign_id

    data = MicroscopeCreate(name="Zeiss LSM 880")
    resp = await mod.create_microscope(data, current_user=_user(), db=mock_db)
    assert resp.name == "Zeiss LSM 880"
    assert resp.color and resp.color.startswith("#")
    assert mock_db.add.called and mock_db.commit.await_count == 1


async def test_create_microscope_duplicate_name_400(mock_db):
    mock_db.execute.return_value = make_result(scalar=SimpleNamespace(id=9))
    with pytest.raises(HTTPException) as ei:
        await mod.create_microscope(
            MicroscopeCreate(name="dup"), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_delete_microscope_conflict_when_referenced(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=SimpleNamespace(id=2, name="m")),  # get_or_404
        make_result(scalar=3),                                # experiment count
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.delete_microscope(2, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 409


async def test_delete_microscope_ok_when_unreferenced(mock_db):
    m = SimpleNamespace(id=2, name="m")
    mock_db.execute.side_effect = [
        make_result(scalar=m),   # get_or_404
        make_result(scalar=0),   # experiment count
    ]
    await mod.delete_microscope(2, current_user=_user(), db=mock_db)
    assert mock_db.delete.await_count == 1 and mock_db.commit.await_count == 1


async def test_get_microscope_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.get_microscope(99, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 404


async def test_update_microscope_applies_fields(mock_db):
    m = SimpleNamespace(id=2, name="old", manufacturer=None, model=None,
                        objective=None, magnification=None, description=None,
                        color="#3b82f6", created_at=None)
    mock_db.execute.side_effect = [
        make_result(scalar=m),   # get_or_404
        make_result(scalar=None),  # name-unique check (name changed)
        make_result(scalar=0),   # experiment count
    ]
    resp = await mod.update_microscope(
        2, MicroscopeUpdate(name="new", magnification="63×"),
        current_user=_user(), db=mock_db,
    )
    assert m.name == "new" and m.magnification == "63×"
    assert resp.name == "new"


async def test_update_microscope_null_color_repicks(mock_db):
    # UI "Auto" button sends color=null on edit → must re-pick, not no-op.
    m = _micro(id=2, name="m", color=COLOR_PALETTE[0])
    mock_db.execute.side_effect = [
        make_result(scalar=m),                        # get_or_404
        make_result(fetchall=[(COLOR_PALETTE[0],)]),  # colours in use
        make_result(scalar=0),                        # experiment count
    ]
    await mod.update_microscope(
        2, MicroscopeUpdate(color=None), current_user=_user(), db=mock_db
    )
    assert m.color == COLOR_PALETTE[1]


async def test_update_microscope_rename_to_duplicate_400(mock_db):
    m = _micro(id=2, name="old")
    mock_db.execute.side_effect = [
        make_result(scalar=m),                       # get_or_404
        make_result(scalar=SimpleNamespace(id=9)),   # name-unique finds a dup
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.update_microscope(
            2, MicroscopeUpdate(name="taken"), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_list_microscopes_with_counts(mock_db):
    m1 = _micro(id=1, name="A")
    m2 = _micro(id=2, name="B")
    mock_db.execute.side_effect = [
        make_result(scalars_all=[m1, m2]),   # list
        make_result(fetchall=[(1, 3)]),      # experiment counts grouped by id
    ]
    out = await mod.list_microscopes(current_user=_user(), db=mock_db)
    assert len(out) == 2
    assert out[0].id == 1 and out[0].experiment_count == 3
    assert out[1].id == 2 and out[1].experiment_count == 0
