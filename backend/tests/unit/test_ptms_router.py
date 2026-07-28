"""PTM router — shared reference data CRUD.

PTMs carry no `user_id`: any authenticated user may create, edit and delete them,
exactly like proteins and microscopes. What the router does have to protect is
referential integrity (no deleting a PTM experiments still point at) and name
uniqueness.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers import ptms as mod
from schemas.ptm import PTMCreate, PTMUpdate
from tests.unit.conftest import make_result


def _user():
    return SimpleNamespace(id=1, name="Tester")


def _ptm(**kw):
    base = dict(
        id=3,
        name="Polyglutamylation",
        abbreviation="polyE",
        modified_residue="α/β-tubulin C-terminal tails",
        enzyme="TTLL1-TTLL7",
        description=None,
        color="#ec4899",
        created_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_list_returns_experiment_counts(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalars_all=[_ptm(id=3), _ptm(id=4, name="Acetylation")]),
        make_result(fetchall=[(3, 12)]),  # only PTM 3 is in use
    ]
    out = await mod.list_ptms(current_user=_user(), db=mock_db)
    assert [p.id for p in out] == [3, 4]
    assert out[0].experiment_count == 12
    # A PTM nothing references reports 0, not a missing key.
    assert out[1].experiment_count == 0


def _populate_pk(mock_db, pk: int = 3):
    """A real commit+refresh assigns the PK; the AsyncMock does not."""
    def _assign_id(obj):
        obj.id = pk
    mock_db.refresh.side_effect = _assign_id


async def test_create_assigns_an_unused_colour_when_none_given(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=None),              # name uniqueness check
        make_result(fetchall=[("#3b82f6",)]),  # colours already in use
    ]
    _populate_pk(mock_db)
    with patch.object(mod, "pick_color", new=AsyncMock(return_value="#ef4444")):
        out = await mod.create_ptm(
            PTMCreate(name="Detyrosination"), current_user=_user(), db=mock_db
        )
    assert out.color == "#ef4444"
    mock_db.add.assert_called_once()


async def test_create_keeps_an_explicit_colour(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    _populate_pk(mock_db)
    with patch.object(mod, "pick_color", new=AsyncMock()) as picker:
        out = await mod.create_ptm(
            PTMCreate(name="Δ2-tubulin", color="#00d4aa"),
            current_user=_user(),
            db=mock_db,
        )
    assert out.color == "#00d4aa"
    picker.assert_not_awaited()


async def test_create_rejects_a_duplicate_name(mock_db):
    mock_db.execute.return_value = make_result(scalar=_ptm())
    with pytest.raises(HTTPException) as ei:
        await mod.create_ptm(
            PTMCreate(name="Polyglutamylation"), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400
    assert "already exists" in ei.value.detail


async def test_get_unknown_id_is_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.get_ptm(999, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 404
    assert "PTM" in ei.value.detail


async def test_update_changes_only_the_fields_passed(mock_db):
    ptm = _ptm()
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),   # get_or_404
        make_result(scalar=None),  # uniqueness re-check for the new name
        make_result(scalar=7),     # experiment count
    ]
    out = await mod.update_ptm(
        3, PTMUpdate(name="Polyglutamylation (long)"), current_user=_user(), db=mock_db
    )
    assert ptm.name == "Polyglutamylation (long)"
    # Untouched fields survive.
    assert ptm.abbreviation == "polyE"
    assert out.experiment_count == 7


async def test_update_to_an_existing_name_is_rejected(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=_ptm(id=3)),                    # get_or_404
        make_result(scalar=_ptm(id=4, name="Acetylation")),  # name taken by another row
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.update_ptm(
            3, PTMUpdate(name="Acetylation"), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_update_with_explicit_null_colour_repicks(mock_db):
    # Null means "give me an unused one"; omitting the field leaves it alone.
    ptm = _ptm()
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),
        make_result(scalar=2),
    ]
    with patch.object(mod, "pick_color", new=AsyncMock(return_value="#22c55e")):
        await mod.update_ptm(3, PTMUpdate(color=None), current_user=_user(), db=mock_db)
    assert ptm.color == "#22c55e"


async def test_delete_is_refused_while_experiments_reference_it(mock_db):
    # 409 rather than a cascade: losing which PTM a batch used is unrecoverable.
    mock_db.execute.side_effect = [
        make_result(scalar=_ptm()),
        make_result(scalar=4),  # 4 experiments still point at it
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.delete_ptm(3, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 409
    assert "4" in ei.value.detail
    mock_db.delete.assert_not_called()


async def test_delete_succeeds_when_unreferenced(mock_db):
    ptm = _ptm()
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),
        make_result(scalar=0),
    ]
    await mod.delete_ptm(3, current_user=_user(), db=mock_db)
    mock_db.delete.assert_awaited_once_with(ptm)
    mock_db.commit.assert_awaited()
