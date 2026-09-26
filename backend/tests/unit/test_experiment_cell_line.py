"""Experiment ↔ cell line assignment, and the ACL boundary it must not cross.

The cell line joins the microscope, the PTM, the protein and the folder as a
deliberate exception to "experiment writes are owner-only" (CLAUDE.md calls it
the sixth group-write exception, counting crop curation). Both sides of that
boundary are locked here: the group *may* set the cell line, and the generic
experiment PATCH *stays* owner-only.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models.experiment import ExperimentStatus
from routers import experiments as mod
from schemas.experiment import ExperimentCreate, ExperimentUpdate
from tests.unit.conftest import make_result


def _user():
    return SimpleNamespace(id=1, name="Tester")


def _cell_line(**kw):
    base = dict(
        id=3,
        name="U2OS",
        description="Human osteosarcoma line.",
        color="#3b82f6",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _experiment(owner_id: int = 1, cell_line=None):
    return SimpleNamespace(
        id=1, user_id=owner_id, name="E", description=None,
        status=ExperimentStatus.DRAFT, group_id=2, map_protein=None,
        microscope=None, ptm=None, cell_line=cell_line, fasta_sequence=None,
        cell_line_id=cell_line.id if cell_line else None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def test_cell_line_is_assignable_at_creation_but_not_via_generic_patch():
    """One field, one endpoint, one ACL.

    `PATCH /experiments/{id}` is owner-only; the cell-line endpoint is open to
    the whole group. If `cell_line_id` were accepted by both, the narrow path
    would be reachable by accident and group members would silently get 403s.
    `extra="forbid"` makes a stale client fail loudly (422) instead of having its
    assignment quietly dropped.
    """
    assert "cell_line_id" in ExperimentCreate.model_fields
    assert "cell_line_id" not in ExperimentUpdate.model_fields
    with pytest.raises(ValidationError):
        ExperimentUpdate(cell_line_id=3)


def test_cell_line_column_is_added_to_existing_databases():
    """`create_all` builds the new `cell_lines` table but never the new column.

    A fresh database (every test run, every dev machine) gets
    `experiments.cell_line_id` from `create_all`, so omitting the
    `ensure_schema_updates` entry is invisible everywhere except production --
    where the column simply never appears and every experiment query fails.
    """
    import inspect

    import database

    source = inspect.getsource(database.ensure_schema_updates)
    assert '("experiments", "cell_line_id", "INTEGER REFERENCES cell_lines(id)")' in source


async def test_create_experiment_missing_cell_line_404(mock_db, monkeypatch):
    async def fake_group_id(uid, db):
        return None
    monkeypatch.setattr(mod, "get_user_group_ids", fake_group_id)
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.create_experiment(
            ExperimentCreate(name="E", cell_line_id=42), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 404
    assert "cell line" in ei.value.detail.lower()


async def test_create_experiment_with_valid_cell_line(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=_cell_line()),                          # existence check
        make_result(scalar=_experiment(cell_line=_cell_line())),   # response re-read
    ]
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[])):
        out = await mod.create_experiment(
            ExperimentCreate(name="E", cell_line_id=3), current_user=_user(), db=mock_db
        )
    assert out.cell_line is not None
    assert out.cell_line.id == 3 and out.cell_line.name == "U2OS"


async def test_assign_missing_cell_line_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=_experiment())):
        with pytest.raises(HTTPException) as ei:
            await mod.update_experiment_cell_line(
                1, cell_line_id=999, current_user=_user(), db=mock_db
            )
    assert ei.value.status_code == 404
    assert "cell line" in ei.value.detail.lower()


async def test_assign_valid_cell_line(mock_db):
    exp = _experiment()
    mock_db.execute.side_effect = [
        make_result(scalar=_cell_line()),
        make_result(scalar=_experiment(cell_line=_cell_line())),
    ]
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment_cell_line(
            1, cell_line_id=3, current_user=_user(), db=mock_db
        )
    assert exp.cell_line_id == 3
    assert out.cell_line is not None and out.cell_line.id == 3


async def test_group_member_may_assign_cell_line_to_another_users_experiment(mock_db):
    """The reason this endpoint exists.

    Most of the 181 experiments belong to the lab's annotator, so an owner-only
    assignment would leave the dashboard's cell-line facet covering almost
    nothing. `get_experiment_for_user` is group-scoped and there is deliberately
    NO owner re-check after it -- unlike `update_experiment`.
    """
    exp = _experiment(owner_id=22)  # a colleague's experiment
    mock_db.execute.side_effect = [
        make_result(scalar=_cell_line()),
        make_result(scalar=_experiment(owner_id=22, cell_line=_cell_line())),
    ]
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment_cell_line(
            1, cell_line_id=3, current_user=_user(), db=mock_db
        )
    assert exp.cell_line_id == 3
    assert out.cell_line.id == 3


async def test_clearing_cell_line_skips_the_existence_lookup(mock_db):
    """Omitting the id clears the assignment without looking a cell line up.

    Exactly one statement runs: the response re-read. A second one would mean the
    handler tried to validate `None` as a cell line id.
    """
    exp = _experiment(cell_line=_cell_line())
    mock_db.execute.side_effect = [make_result(scalar=_experiment())]

    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment_cell_line(
            1, cell_line_id=None, current_user=_user(), db=mock_db
        )
    assert exp.cell_line_id is None
    assert out.cell_line is None
    assert mock_db.execute.await_count == 1


def test_cell_line_widening_did_not_leak_into_the_owner_only_handlers():
    """Guard the boundary from the other side.

    `update_experiment` and `delete_experiment` must keep their explicit owner
    re-check after the group-scoped lookup. Losing it is invisible from the
    cell-line tests -- and would silently hand the whole group everyone's rename
    and delete.
    """
    import inspect

    source = inspect.getsource(mod)
    for handler in ("update_experiment", "delete_experiment"):
        body = source.split(f"async def {handler}(", 1)[1].split("\n@router", 1)[0]
        assert "user_id != current_user.id" in body, (
            f"{handler} lost its owner re-check"
        )

    body = source.split("async def update_experiment_cell_line(", 1)[1].split("\n@router", 1)[0]
    assert "user_id != current_user.id" not in body, (
        "the cell-line assignment is group-writable on purpose; an owner re-check "
        "here would make the facet unusable"
    )


def test_cell_line_relation_is_eager_loaded_on_the_write_response():
    """The response re-read must load `cell_line` like the other three.

    `load_experiment_response` runs in async context, so a relation it does not
    name is lazy-loaded at serialisation time and raises `MissingGreenlet` --
    a 500 that no mocked-session test can see, because an AsyncMock models no
    lazy IO. The same trap the docstring on that helper describes.
    """
    import inspect

    source = inspect.getsource(mod.load_experiment_response)
    assert "selectinload(Experiment.cell_line)" in source
