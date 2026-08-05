"""Experiment ↔ PTM assignment, and the ACL boundary it must not cross.

The PTM assignment is the second deliberate exception to "experiment writes are
owner-only" (after the microscope). Both sides of that boundary are locked here:
the group *may* set the PTM, and the generic experiment PATCH *stays* owner-only.
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


def _ptm(**kw):
    base = dict(
        id=6,
        name="Polyglutamylation",
        abbreviation="polyE",
        modified_residue="α/β-tubulin C-terminal tails",
        enzyme="TTLL1-TTLL7",
        color="#ec4899",
        # Required on the response: a source object with no `kind` must fail
        # loudly rather than serialise as "modification", which is the one value
        # that would draw a control as the sample it controls.
        kind="modification",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _experiment(owner_id: int = 1, ptm=None):
    return SimpleNamespace(
        id=1, user_id=owner_id, name="E", description=None,
        status=ExperimentStatus.DRAFT, group_id=2, map_protein=None,
        microscope=None, ptm=ptm, fasta_sequence=None,
        ptm_id=ptm.id if ptm else None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def test_ptm_is_assignable_at_creation_but_not_via_generic_patch():
    """One field, one endpoint, one ACL.

    `PATCH /experiments/{id}` is owner-only; the PTM endpoint is open to the
    whole group. If `ptm_id` were accepted by both, the narrow path would be
    reachable by accident and group members would silently get 403s.
    `extra="forbid"` makes a stale client fail loudly (422) instead of having its
    assignment quietly dropped.
    """
    assert "ptm_id" in ExperimentCreate.model_fields
    assert "ptm_id" not in ExperimentUpdate.model_fields
    with pytest.raises(ValidationError):
        ExperimentUpdate(ptm_id=6)


async def test_create_experiment_missing_ptm_404(mock_db, monkeypatch):
    async def fake_group_id(uid, db):
        return None
    monkeypatch.setattr(mod, "get_user_group_ids", fake_group_id)
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.create_experiment(
            ExperimentCreate(name="E", ptm_id=42), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 404
    assert "ptm" in ei.value.detail.lower()


async def test_create_experiment_with_valid_ptm(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=_ptm()),                    # existence check
        make_result(scalar=_experiment(ptm=_ptm())),   # response re-read
    ]
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[])):
        out = await mod.create_experiment(
            ExperimentCreate(name="E", ptm_id=6), current_user=_user(), db=mock_db
        )
    assert out.ptm is not None
    assert out.ptm.id == 6 and out.ptm.abbreviation == "polyE"


async def test_assign_missing_ptm_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=_experiment())):
        with pytest.raises(HTTPException) as ei:
            await mod.update_experiment_ptm(
                1, ptm_id=999, current_user=_user(), db=mock_db
            )
    assert ei.value.status_code == 404
    assert "ptm" in ei.value.detail.lower()


async def test_assign_valid_ptm(mock_db):
    exp = _experiment()
    mock_db.execute.side_effect = [
        make_result(scalar=_ptm()),
        make_result(scalar=_experiment(ptm=_ptm())),
    ]
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment_ptm(
            1, ptm_id=6, current_user=_user(), db=mock_db
        )
    assert exp.ptm_id == 6
    assert out.ptm is not None and out.ptm.id == 6


async def test_group_member_may_assign_ptm_to_another_users_experiment(mock_db):
    """The reason this endpoint exists.

    Every experiment starts with no PTM and most belong to the lab's annotator,
    so an owner-only assignment would leave the dashboard's PTM facet empty
    forever. `get_experiment_for_user` is group-scoped and there is deliberately
    NO owner re-check after it -- unlike `update_experiment`.
    """
    exp = _experiment(owner_id=22)  # a colleague's experiment
    mock_db.execute.side_effect = [
        make_result(scalar=_ptm()),
        make_result(scalar=_experiment(owner_id=22, ptm=_ptm())),
    ]
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment_ptm(
            1, ptm_id=6, current_user=_user(), db=mock_db
        )
    assert exp.ptm_id == 6
    assert out.ptm.id == 6


async def test_clearing_ptm_skips_the_existence_lookup(mock_db):
    """Omitting the id clears the assignment without looking a PTM up.

    Exactly one statement runs: the response re-read. A second one would mean the
    handler tried to validate `None` as a PTM id.
    """
    exp = _experiment(ptm=_ptm())
    mock_db.execute.side_effect = [make_result(scalar=_experiment())]

    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment_ptm(
            1, ptm_id=None, current_user=_user(), db=mock_db
        )
    assert exp.ptm_id is None
    assert out.ptm is None
    assert mock_db.execute.await_count == 1


def test_ptm_widening_did_not_leak_into_the_owner_only_handlers():
    """Guard the boundary from the other side.

    `update_experiment` and `delete_experiment` must keep their explicit owner
    re-check after the group-scoped lookup. Losing it is invisible from the PTM
    tests -- and would silently hand the whole group everyone's rename and delete.
    """
    import inspect

    source = inspect.getsource(mod)
    for handler in ("update_experiment", "delete_experiment"):
        body = source.split(f"async def {handler}(", 1)[1].split("\n@router", 1)[0]
        assert "user_id != current_user.id" in body, (
            f"{handler} lost its owner re-check"
        )

    ptm_body = source.split("async def update_experiment_ptm(", 1)[1].split("\n@router", 1)[0]
    assert "user_id != current_user.id" not in ptm_body, (
        "the PTM assignment is group-writable on purpose; an owner re-check here "
        "would make the facet unusable"
    )


def test_ptm_column_is_added_to_existing_databases():
    """`create_all` builds the new `ptms` table but never the new column.

    A fresh database (every test run, every dev machine) gets
    `experiments.ptm_id` from `create_all`, so omitting the
    `ensure_schema_updates` entry is invisible everywhere except production —
    where the column simply never appears and every experiment query fails.
    """
    import inspect

    import database

    source = inspect.getsource(database.ensure_schema_updates)
    assert '("experiments", "ptm_id", "INTEGER REFERENCES ptms(id)")' in source
