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
        controls_ptm_id=None,
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
        make_result(scalars_all=[]),  # no control points at it
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
    mock_db.execute.side_effect = [
        make_result(scalar=None),                             # name uniqueness
        make_result(scalar=_ptm(id=2, kind="modification")),  # link target
    ]
    _populate_pk(mock_db)
    with patch.object(mod, "pick_color", new=AsyncMock(return_value="#94a3b8")):
        out = await mod.create_ptm(
            PTMCreate(name="Detyrosination control", kind="control",
                      controls_ptm_id=2),
            current_user=_user(), db=mock_db,
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
        make_result(scalar=ptm),       # get_or_404
        make_result(scalars_all=[]),   # no control depends on it
        make_result(scalar=0),         # experiment count
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
    ptm = _ptm(kind="control", controls_ptm_id=2)
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),   # get_or_404
        make_result(scalar=None),  # uniqueness re-check for the new name
        make_result(scalar=0),     # experiment count
    ]
    await mod.update_ptm(
        3, PTMUpdate(name="Renamed"), current_user=_user(), db=mock_db
    )
    assert ptm.kind == "control"


# =============================================================================
# Controls are paired to the modification they control (2026-08-12)
#
# The vocabulary was flat: one `Control` row shared by every PTM condition. But
# the control plasmid differs per modification, so pooling them asserts a
# sameness that does not exist. `controls_ptm_id` names the partner, and the
# rules below exist so a control can never be created without one — which is the
# bug being fixed, not a lesser version of it.
# =============================================================================

def _control(**kw):
    return _ptm(id=15, name="Detyrosination control", abbreviation="deTyr ctrl",
                kind="control", controls_ptm_id=2, **kw)


async def test_create_control_links_it_to_a_modification(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=None),                       # name uniqueness
        make_result(scalar=_ptm(id=2, kind="modification")),  # link target
    ]
    _populate_pk(mock_db, 15)
    out = await mod.create_ptm(
        PTMCreate(name="Acetylation control", kind="control",
                  controls_ptm_id=2, color="#94a3b8"),
        current_user=_user(), db=mock_db,
    )
    assert out.controls_ptm_id == 2


async def test_create_control_without_a_target_is_rejected(mock_db):
    # A control that names no modification is exactly the pooled `Control` row
    # this feature replaces, so the API must not be able to produce one.
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.create_ptm(
            PTMCreate(name="Some control", kind="control"),
            current_user=_user(), db=mock_db,
        )
    assert ei.value.status_code == 400
    assert "controls_ptm_id" in ei.value.detail


async def test_create_rejects_a_link_on_a_row_that_is_not_a_control(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.create_ptm(
            PTMCreate(name="Acetylation", kind="modification", controls_ptm_id=2),
            current_user=_user(), db=mock_db,
        )
    assert ei.value.status_code == 400


async def test_create_rejects_a_control_of_a_control(mock_db):
    # No chains: a control's partner is the mark it is compared against.
    mock_db.execute.side_effect = [
        make_result(scalar=None),
        make_result(scalar=_control()),
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.create_ptm(
            PTMCreate(name="Control of a control", kind="control", controls_ptm_id=15),
            current_user=_user(), db=mock_db,
        )
    assert ei.value.status_code == 400
    assert "modification" in ei.value.detail


async def test_create_rejects_a_link_to_a_missing_row(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=None),
        make_result(scalar=None),  # target does not exist
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.create_ptm(
            PTMCreate(name="Ghost control", kind="control", controls_ptm_id=999),
            current_user=_user(), db=mock_db,
        )
    assert ei.value.status_code == 404


async def test_update_leaves_the_link_alone_when_another_field_changes(mock_db):
    # The rule is about the ROW AFTER the patch, not about the payload. A PATCH
    # that only touches the description must not demand the link be resent.
    ptm = _control()
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),  # get_or_404
        make_result(scalar=1),    # experiment count
    ]
    await mod.update_ptm(
        15, PTMUpdate(description="Inactive VASH1"), current_user=_user(), db=mock_db
    )
    assert ptm.controls_ptm_id == 2


async def test_update_rejects_clearing_the_link_of_a_control(mock_db):
    mock_db.execute.side_effect = [make_result(scalar=_control())]
    with pytest.raises(HTTPException) as ei:
        await mod.update_ptm(
            15, PTMUpdate(controls_ptm_id=None), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_update_rejects_turning_a_linked_control_into_a_modification(mock_db):
    # kind and the link move together: the resulting row is what is checked.
    mock_db.execute.side_effect = [make_result(scalar=_control())]
    with pytest.raises(HTTPException) as ei:
        await mod.update_ptm(
            15, PTMUpdate(kind="modification"), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_delete_refuses_a_modification_a_control_still_points_at(mock_db):
    # ON DELETE SET NULL would leave a control naming nothing — the pooled row
    # again, arrived at silently. Refusing says which rows are in the way.
    mock_db.execute.side_effect = [
        make_result(scalar=_ptm(id=2, kind="modification")),  # get_or_404
        make_result(scalar=0),                                 # no experiments
        make_result(scalars_all=["Detyrosination control"]),   # controls pointing here
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.delete_ptm(2, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 409
    assert "Detyrosination control" in ei.value.detail


async def test_update_refuses_to_declassify_a_modification_a_control_points_at(mock_db):
    """The dangerous direction is AWAY from `modification`, not towards it.

    A control's partner must be a modification. Flipping that partner to `none`
    or `control` strands the control on a row that is no longer a valid target —
    the invariant broken from the other end, where `_check_pairing_shape` on the
    control's own row cannot see it.

    Found by running the handler against the real database: the first version of
    this guard fired on `kind == "modification"`, which is the no-op case, and
    let the actual one through.
    """
    mock_db.execute.side_effect = [
        make_result(scalar=_ptm(id=2, kind="modification")),   # get_or_404
        make_result(scalars_all=["Detyrosination control"]),   # dependents
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.update_ptm(2, PTMUpdate(kind="none"), current_user=_user(), db=mock_db)
    assert ei.value.status_code == 409
    assert "Detyrosination control" in ei.value.detail


async def test_update_allows_a_patch_that_restates_the_kind_unchanged(mock_db):
    """The editor sends `kind` on every save, so a no-op must not 409.

    Also found live: with the guard on the wrong side, editing the description of
    a modification that any control pointed at was rejected outright.
    """
    ptm = _ptm(id=2, kind="modification")
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),   # get_or_404
        make_result(scalar=26),    # experiment count
    ]
    await mod.update_ptm(
        2, PTMUpdate(kind="modification", description="Glu-tubulin"),
        current_user=_user(), db=mock_db,
    )
    assert ptm.description == "Glu-tubulin"


async def test_a_control_cannot_be_its_own_control(mock_db):
    # Reachable through the API by passing the row's own id, and the FK cannot
    # refuse it — a self-referencing row is perfectly valid to Postgres.
    ptm = _control()
    mock_db.execute.side_effect = [make_result(scalar=ptm)]
    with pytest.raises(HTTPException) as ei:
        await mod.update_ptm(
            15, PTMUpdate(controls_ptm_id=15), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400
    assert "its own control" in ei.value.detail


async def test_a_control_can_be_repointed_at_another_modification(mock_db):
    # The lab renames and re-pairs; the target is re-validated on the way in
    # rather than trusted because it was valid when the row was created.
    ptm = _control()
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),                              # get_or_404
        make_result(scalar=_ptm(id=5, kind="modification")),  # new target
        make_result(scalar=1),                                # experiment count
    ]
    out = await mod.update_ptm(
        15, PTMUpdate(controls_ptm_id=5), current_user=_user(), db=mock_db
    )
    assert ptm.controls_ptm_id == 5
    assert out.controls_ptm_id == 5


async def test_repointing_a_control_at_a_non_modification_is_rejected(mock_db):
    ptm = _control()
    mock_db.execute.side_effect = [
        make_result(scalar=ptm),
        make_result(scalar=_ptm(id=10, name="Unmodified", kind="none")),
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.update_ptm(
            15, PTMUpdate(controls_ptm_id=10), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400
    assert "modification" in ei.value.detail
