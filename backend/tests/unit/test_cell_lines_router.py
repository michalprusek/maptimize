"""Cell lines router unit tests (handlers called directly, mocked db).

The fourth table on `utils/reference_data.py`. These are deliberately the same
tests as the microscope router's: the point of the shared helpers is that the
four families behave identically, and a copy that quietly diverged (its own
colour picking, its own delete guard) is exactly what they were extracted to
prevent.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.unit.conftest import make_result
from routers import cell_lines as mod
from schemas.cell_line import CellLineCreate, CellLineUpdate
from utils.colors import COLOR_PALETTE


def _user():
    return SimpleNamespace(id=1, name="Tester")


def _line(**kw):
    base = dict(id=1, name="U2OS", description=None, color=None, created_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


async def test_create_cell_line_auto_color(mock_db):
    """A line typed at the experiment form gets a legend colour without asking.

    There is no cell-line admin page, so nothing ever offers a colour picker --
    if the router did not assign one the facet pills and the coloured plot would
    render every line the same grey.
    """
    mock_db.execute.side_effect = [
        make_result(scalar=None),          # name-unique check
        make_result(fetchall=[]),          # used-colors query
    ]

    def _assign_id(obj):
        obj.id = 1  # a real commit+refresh would populate the PK

    mock_db.refresh.side_effect = _assign_id

    resp = await mod.create_cell_line(
        CellLineCreate(name="HeLa"), current_user=_user(), db=mock_db
    )
    assert resp.name == "HeLa"
    assert resp.color and resp.color.startswith("#")
    assert mock_db.add.called and mock_db.commit.await_count == 1


async def test_create_cell_line_duplicate_name_400(mock_db):
    """Typing an existing name must not mint a second row.

    This is the whole reason the cell line is a table: the picker's "create"
    action fires on whatever the user typed, so without this a stray keystroke
    produces two rows the facet then shows as two separate populations.
    """
    mock_db.execute.return_value = make_result(scalar=SimpleNamespace(id=9))
    with pytest.raises(HTTPException) as ei:
        await mod.create_cell_line(
            CellLineCreate(name="U2OS"), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_create_cell_line_matches_names_case_insensitively(mock_db):
    """"u2os" must not become a second row beside "U2OS".

    Unlike the other three reference tables, a cell line is minted by FREE TYPING
    in the experiment form -- there is no admin page to pick from -- so a case or
    spacing variant is a realistic keystroke, not a contrived one. Two rows split
    one population into two facet values that no longer compare, and nothing
    anywhere reports an error.

    The picker already offers the existing row rather than "create" for a case
    variant, but a frontend-only guard is a guard in appearance only: the API and
    the MCP connector reach this code directly.
    """
    mock_db.execute.return_value = make_result(scalar=SimpleNamespace(id=9))
    with pytest.raises(HTTPException) as ei:
        await mod.create_cell_line(
            CellLineCreate(name=" u2os "), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400

    # The comparison has to reach the DATABASE, not just the Python string:
    # the lookup is what decides, and `name == "u2os"` finds nothing.
    stmt = mock_db.execute.await_args.args[0]
    assert "lower(" in str(stmt).lower()
    # ...and the surrounding whitespace is gone before it gets there, or the
    # lowered comparison still misses on " u2os".
    assert "u2os" in stmt.compile().params.values()


async def test_create_cell_line_stores_the_name_as_typed(mock_db):
    """Case-insensitive MATCHING, not case-folding.

    "HeLa" and "HEK293T" are how the lab writes them; normalising the stored
    value would relabel the facet with something nobody uses.
    """
    mock_db.execute.side_effect = [
        make_result(scalar=None),   # name-unique check
        make_result(fetchall=[]),   # used-colors query
    ]
    mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", 1)
    resp = await mod.create_cell_line(
        CellLineCreate(name="HeLa"), current_user=_user(), db=mock_db
    )
    assert resp.name == "HeLa"


async def test_delete_cell_line_conflict_when_referenced(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=_line(id=2)),  # get_or_404
        make_result(scalar=3),            # experiment count
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.delete_cell_line(2, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 409


async def test_delete_cell_line_ok_when_unreferenced(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=_line(id=2)),
        make_result(scalar=0),
    ]
    await mod.delete_cell_line(2, current_user=_user(), db=mock_db)
    assert mock_db.delete.await_count == 1 and mock_db.commit.await_count == 1


async def test_get_cell_line_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.get_cell_line(99, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 404


async def test_get_cell_line_reports_its_experiment_count(mock_db):
    """The success path, which the 404 test cannot reach.

    The count is the part worth pinning: it comes from a second query keyed on
    `Experiment.cell_line_id`, and a handler that returned the row without it
    would report every line as unused -- which is exactly the number someone
    checks before deleting one.
    """
    mock_db.execute.side_effect = [
        make_result(scalar=_line(id=2, name="U2OS")),  # get_or_404
        make_result(scalar=181),                       # experiments referencing it
    ]
    out = await mod.get_cell_line(2, current_user=_user(), db=mock_db)
    assert out.id == 2 and out.name == "U2OS"
    assert out.experiment_count == 181


async def test_update_cell_line_applies_fields(mock_db):
    line = _line(id=2, name="old", color="#3b82f6")
    mock_db.execute.side_effect = [
        make_result(scalar=line),   # get_or_404
        make_result(scalar=None),   # name-unique check (name changed)
        make_result(scalar=0),      # experiment count
    ]
    resp = await mod.update_cell_line(
        2, CellLineUpdate(name="HEK293T", description="Human embryonic kidney."),
        current_user=_user(), db=mock_db,
    )
    assert line.name == "HEK293T" and line.description == "Human embryonic kidney."
    assert resp.name == "HEK293T"


async def test_update_cell_line_null_color_repicks(mock_db):
    line = _line(id=2, color=COLOR_PALETTE[0])
    mock_db.execute.side_effect = [
        make_result(scalar=line),                     # get_or_404
        make_result(fetchall=[(COLOR_PALETTE[0],)]),  # colours in use
        make_result(scalar=0),                        # experiment count
    ]
    await mod.update_cell_line(
        2, CellLineUpdate(color=None), current_user=_user(), db=mock_db
    )
    assert line.color == COLOR_PALETTE[1]


async def test_update_cell_line_rename_to_duplicate_400(mock_db):
    line = _line(id=2, name="old")
    mock_db.execute.side_effect = [
        make_result(scalar=line),                    # get_or_404
        make_result(scalar=SimpleNamespace(id=9)),   # name-unique finds a dup
    ]
    with pytest.raises(HTTPException) as ei:
        await mod.update_cell_line(
            2, CellLineUpdate(name="U2OS"), current_user=_user(), db=mock_db
        )
    assert ei.value.status_code == 400


async def test_list_cell_lines_with_counts(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalars_all=[_line(id=1, name="A"), _line(id=2, name="B")]),
        make_result(fetchall=[(1, 3)]),      # experiment counts grouped by id
    ]
    out = await mod.list_cell_lines(current_user=_user(), db=mock_db)
    assert len(out) == 2
    assert out[0].id == 1 and out[0].experiment_count == 3
    assert out[1].id == 2 and out[1].experiment_count == 0


def test_cell_lines_router_has_no_second_copy_of_the_reference_helpers():
    """The four families share one CRUD implementation.

    A hand-rolled name check or colour pick here would drift from the other three
    without any test noticing -- which is the history `utils/reference_data.py`
    was extracted from.
    """
    import inspect

    source = inspect.getsource(mod)
    for helper in (
        "ensure_name_unique",
        "pick_color",
        "get_or_404",
        "count_referencing",
        "count_referencing_grouped",
    ):
        assert helper in source, f"{helper} should come from utils.reference_data"
    assert "COLOR_PALETTE" not in source, "colour choice belongs in pick_color"
