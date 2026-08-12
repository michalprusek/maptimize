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
from pydantic import ValidationError

from routers import ptms as mod
from schemas.ptm import PTMCreate, PTMDetailedResponse, PTMResponse, PTMUpdate
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
        kind="modification",
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


# -- kind: what a row in this vocabulary actually is -------------------------
#
# The list is not homogeneous — `Unmodified` is the absence of a modification
# and `Control` is a transfection with an inactive enzyme — and the projections
# draw the three kinds as three markers. A row whose kind is wrong is drawn as
# something it is not, with nothing failing anywhere, so the write path is
# pinned here.


async def test_create_defaults_to_a_modification(mock_db):
    """A PTM created without `kind` is an ordinary modification.

    The default has to live in the schema, not only in the column: the router
    builds `PTM(**model_dump())`, so an absent field would pass None straight
    into a NOT NULL column instead of falling back to the DDL default.
    """
    mock_db.execute.return_value = make_result(scalar=None)
    _populate_pk(mock_db)
    with patch.object(mod, "pick_color", new=AsyncMock(return_value="#ef4444")):
        out = await mod.create_ptm(
            PTMCreate(name="Acetylation"), current_user=_user(), db=mock_db
        )
    assert out.kind == "modification"
    stored = mock_db.add.call_args[0][0].kind
    assert stored == "modification"
    # ⚠️ Asserted on the DEFAULT path, which is the common one and the only one
    # where it can fail: pydantic does not validate defaults, so without
    # `validate_default=True` this is the enum member, not a str — and `==`
    # cannot tell, because PTMKind subclasses str. The version of this test that
    # asserted the type only on the explicit path could never have gone red.
    assert type(stored) is str


async def test_create_persists_the_control_kind(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    _populate_pk(mock_db)
    with patch.object(mod, "pick_color", new=AsyncMock(return_value="#94a3b8")):
        out = await mod.create_ptm(
            PTMCreate(name="Control", kind="control"), current_user=_user(), db=mock_db
        )
    assert out.kind == "control"
    assert type(mock_db.add.call_args[0][0].kind) is str


@pytest.mark.parametrize("schema", [PTMCreate, PTMUpdate])
@pytest.mark.parametrize("bad", ["Control", "controls", "", "modification "])
def test_an_unrecognised_kind_is_refused(schema, bad):
    """422, not a row carrying a class nothing can read.

    Case and trailing whitespace included on purpose: "Control" is the exact
    value a person would type, and storing it would leave every control drawn
    as a plain point with no error raised anywhere.

    ⚠️ Both schemas. Pinning only the POST left the PATCH path completely
    unguarded — `PTMUpdate.kind` could be widened to `Optional[str]` with the
    whole 1772-test suite still green. The MCP tool advertises an `enum` but the
    registry never enforces it at dispatch, so this schema is the only guard.
    """
    with pytest.raises(ValidationError):
        schema(name="x", kind=bad)


@pytest.mark.parametrize("schema", [PTMCreate, PTMUpdate])
def test_the_kind_enum_is_coerced_to_a_plain_string(schema):
    # `use_enum_values` on both, so what reaches the VARCHAR column is a str
    # regardless of which endpoint wrote it. Unpinned on PTMUpdate, removing it
    # there changed nothing in the suite.
    assert type(schema(name="x", kind="control").kind) is str


def test_an_explicit_null_kind_is_refused():
    """422, not the 500 a NOT NULL column answers with.

    `kind` is the second NOT NULL column on this table; `name` was the first and
    is guarded by RejectsNullName. `exclude_unset` deliberately keeps an
    explicit null — that is what makes `color: null` mean "pick me a fresh one"
    — so without a guard the router setattrs None onto the column and Postgres
    raises. Verified against the live database before this guard existed.
    """
    with pytest.raises(ValidationError):
        PTMUpdate(kind=None)


def test_omitting_kind_is_still_a_valid_patch():
    # The guard must reject an explicit null, not make the field required.
    assert "kind" not in PTMUpdate(name="x").model_dump(exclude_unset=True)


@pytest.mark.parametrize("schema", [PTMResponse, PTMDetailedResponse])
def test_a_kind_no_client_can_draw_degrades_to_the_plain_marker(schema):
    """Read, never 500 — and read as the SAME thing the client would read it as.

    `PTMResponse` is embedded in `ExperimentResponse`, so validating strictly
    would let one hand-edited row take out every experiment list, not just its
    own. But passing it through untouched left two degradation rules that
    disagreed: a missing value read as "modification" here and an unreadable one
    read as "none" in the browser — and "modification" is precisely the value
    that draws a control as the sample it controls.
    """
    out = schema.model_validate(
        {"id": 1, "name": "Legacy", "kind": "Control"}
    )
    assert out.kind == "none"


@pytest.mark.parametrize("schema", [PTMResponse, PTMDetailedResponse])
def test_a_missing_kind_is_an_error_rather_than_a_guess(schema):
    # A source object with no `kind` at all is a programming error. Defaulting
    # it silently reported controls as modifications.
    with pytest.raises(ValidationError):
        schema.model_validate({"id": 1, "name": "Legacy"})


async def test_update_can_change_the_kind(mock_db):
    ptm = _ptm(kind="modification")
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),  # get_or_404
        make_result(scalar=0),    # experiment count
    ]
    out = await mod.update_ptm(
        3, PTMUpdate(kind="none"), current_user=_user(), db=mock_db
    )
    assert ptm.kind == "none"
    assert out.kind == "none"


async def test_a_patch_that_omits_kind_leaves_it_alone(mock_db):
    # `exclude_unset` is what makes this work. Without it the field's default —
    # None, not "modification" — would reach the NOT NULL column and 500 the
    # request; the same is true of every other omitted field on this schema.
    ptm = _ptm(kind="control")
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),   # get_or_404
        make_result(scalar=None),  # uniqueness re-check for the new name
        make_result(scalar=0),     # experiment count
    ]
    await mod.update_ptm(
        3, PTMUpdate(name="Renamed"), current_user=_user(), db=mock_db
    )
    assert ptm.kind == "control"
