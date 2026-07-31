"""The dashboard UMAP faceted filter: experiment / microscope / protein / PTM.

Covers the two things that are easy to get subtly wrong and impossible to notice
from the UI: the combination semantics (OR within a facet, AND across facets,
plus the "unassigned" sentinel), and the fact that narrowing the plot must not
resurrect the `MIN_POINTS_FOR_UMAP` error.
"""
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from models.cell_crop import CellCrop
from models.experiment import Experiment
from models.image import Image
from routers import embeddings as mod
from tests.unit.conftest import make_result
from utils.facets import UNASSIGNED_FACET_ID, facet_clause, real_ids


def user(uid=1):
    return SimpleNamespace(id=uid)


@pytest.fixture
def no_group():
    """Patch get_user_group_ids to return None (user belongs to no group)."""
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[])):
        yield


# =============================================================================
# facet_clause — the combination semantics
# =============================================================================

def test_no_selection_means_no_constraint():
    # An untouched filter control must not be read as "match nothing".
    assert facet_clause(Experiment.microscope_id, None) is None
    assert facet_clause(Experiment.microscope_id, []) is None


def test_single_id_compiles_to_an_in_check():
    sql = str(facet_clause(Experiment.microscope_id, [3]))
    assert "microscope_id IN" in sql
    assert "IS NULL" not in sql


def test_several_ids_in_one_facet_are_ored():
    # OR within a facet: "AeryScan or 3D SIM", not "both at once" (impossible).
    clause = facet_clause(Experiment.microscope_id, [3, 4])
    sql = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "3" in sql and "4" in sql
    assert "IN" in sql


def test_unassigned_sentinel_matches_null_rows():
    # The whole reason the PTM facet is usable on day one: every experiment
    # starts with ptm_id NULL, so "Unassigned" must be selectable.
    sql = str(facet_clause(Experiment.ptm_id, [UNASSIGNED_FACET_ID]))
    assert "IS NULL" in sql
    assert "IN" not in sql


def test_unassigned_combines_with_real_ids():
    sql = str(facet_clause(Experiment.ptm_id, [UNASSIGNED_FACET_ID, 5]))
    assert "IS NULL" in sql
    assert "IN" in sql
    assert " OR " in sql


def test_real_ids_strips_the_sentinel():
    # Looking the sentinel up in the reference table would 404 every filter
    # that includes "Unassigned".
    assert real_ids([UNASSIGNED_FACET_ID, 2, 3]) == [2, 3]
    assert real_ids([UNASSIGNED_FACET_ID]) == []
    assert real_ids(None) == []


def test_zero_is_never_a_real_reference_id():
    # The sentinel only works because SERIAL ids start at 1.
    assert UNASSIGNED_FACET_ID == 0


# =============================================================================
# _apply_facets — AND across facets, and protein reads the right column
# =============================================================================

def test_facets_are_anded_across_dimensions():
    query = select(Experiment)
    out = mod._apply_facets(
        query,
        mod.FacetSelection(microscope_ids=[1], ptm_ids=[2]),
        Experiment.map_protein_id,
    )
    sql = str(out)
    assert "microscope_id IN" in sql
    assert "ptm_id IN" in sql
    assert " AND " in sql


def test_inactive_facets_add_no_clauses():
    query = select(Experiment)
    unchanged = mod._apply_facets(query, mod.FacetSelection(), Experiment.map_protein_id)
    assert str(unchanged) == str(query)


def test_protein_facet_uses_the_column_it_is_given():
    # Cropped mode filters on the crop's protein and FOV mode on the image's, so
    # filtering by protein always agrees with the colour the point is drawn in.
    cropped = str(
        mod._apply_facets(
            select(CellCrop),
            mod.FacetSelection(protein_ids=[7]),
            CellCrop.map_protein_id,
        )
    )
    fov = str(
        mod._apply_facets(
            select(Image), mod.FacetSelection(protein_ids=[7]), Image.map_protein_id
        )
    )
    assert "cell_crops.map_protein_id" in cropped
    assert "images.map_protein_id" in fov


# =============================================================================
# FacetSelection.is_active — what gates the minimum-points error
# =============================================================================

def test_selection_is_inactive_when_nothing_is_ticked():
    assert mod.FacetSelection().is_active is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"experiment_ids": [1]},
        {"microscope_ids": [1]},
        {"protein_ids": [1]},
        {"ptm_ids": [UNASSIGNED_FACET_ID]},
    ],
)
def test_any_ticked_facet_makes_the_selection_active(kwargs):
    assert mod.FacetSelection(**kwargs).is_active is True


# =============================================================================
# The minimum-points gate must not fire on filtered views
# =============================================================================

def test_unfiltered_view_below_the_threshold_still_errors():
    with patch.object(mod, "MIN_POINTS_FOR_UMAP", 10):
        with pytest.raises(HTTPException) as ei:
            mod._guard_enough_points(2, mod.FacetSelection(), mod.UmapType.CROPPED)
    assert ei.value.status_code == 400


def test_filtered_view_below_the_threshold_is_allowed():
    # Coordinates come from one shared fit that already happened, so three
    # filtered points are a correct answer. Raising 400 here is what made the old
    # single-microscope filter report "not enough crops" for narrow selections.
    with patch.object(mod, "MIN_POINTS_FOR_UMAP", 10):
        mod._guard_enough_points(2, mod.FacetSelection(ptm_ids=[3]), mod.UmapType.CROPPED)


def test_filtered_view_with_zero_matches_is_allowed():
    with patch.object(mod, "MIN_POINTS_FOR_UMAP", 10):
        mod._guard_enough_points(0, mod.FacetSelection(microscope_ids=[3]), mod.UmapType.FOV)


# =============================================================================
# _load_facets — the summary is a second query, with its own ACL to get right
# =============================================================================

def _compiled(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize("umap_type", [mod.UmapType.CROPPED, mod.UmapType.FOV])
async def test_facet_summary_is_scoped_to_what_the_user_may_read(mock_db, umap_type):
    """The facet payload carries experiment NAMES and point counts.

    It is a separate query from the points, so the ACL has to be right twice.
    Asserted on the compiled SQL rather than on the returned rows because a mock
    cannot enforce a predicate: dropping `experiment_owner_filter` here leaves
    every other test in the suite green while the filter panel starts listing
    other groups' experiment names.
    """
    mock_db.execute.return_value = make_result(fetchall=[])
    await mod._load_facets(umap_type, user_id=7, group_ids=[], db=mock_db)

    sql = _compiled(mock_db.execute.await_args.args[0])
    assert "experiments.user_id = 7" in sql
    assert "cell_crops.embedding IS NOT NULL" in sql or "images.embedding IS NOT NULL" in sql


@pytest.mark.parametrize("umap_type", [mod.UmapType.CROPPED, mod.UmapType.FOV])
async def test_facet_summary_widens_to_the_group_but_no_further(mock_db, umap_type):
    # Group sharing is an OR on top of ownership, never a replacement for it —
    # swapping the two would hand every group's data to anyone in any group.
    mock_db.execute.return_value = make_result(fetchall=[])
    await mod._load_facets(umap_type, user_id=7, group_ids=[3], db=mock_db)

    sql = _compiled(mock_db.execute.await_args.args[0])
    assert "experiments.user_id = 7" in sql
    assert "experiments.group_id IN (3)" in sql
    assert " OR " in sql


async def test_facet_summary_ignores_the_active_selection(mock_db):
    # The panel must keep offering a value after you untick it, and must show how
    # many points each *other* value would bring back. Filtering the summary by
    # the current selection would make both impossible.
    mock_db.execute.return_value = make_result(fetchall=[])
    await mod._load_facets(mod.UmapType.CROPPED, user_id=7, group_ids=[], db=mock_db)

    sql = _compiled(mock_db.execute.await_args.args[0])
    assert "ptm_id IN" not in sql
    assert "microscope_id IN" not in sql


async def test_facet_summary_unpacks_the_rows_it_selects(mock_db):
    """Every other summary test feeds zero rows, so the unpack never runs.

    What this pins is the column ORDER: the six positions are unlabelled, and
    transposing `ptm_id` with `protein_id` mislabels every filter pill with no
    error anywhere.

    ⚠️ It cannot pin the column COUNT. Under an AsyncMock the row shape comes
    from this test, not from the query, so widening `buckets` without widening
    the unpack stays green here — it fails loudly at runtime instead, on the
    first dashboard load in any environment with data.
    """
    mock_db.execute.return_value = make_result(
        fetchall=[
            (1, "AeryScan MAP7", 10, 20, 30, 5),
            (1, "AeryScan MAP7", 10, 20, None, 2),
            (2, "SIM Tau", None, None, 30, 4),
        ]
    )
    rows = await mod._load_facets(mod.UmapType.CROPPED, 7, None, mock_db)

    assert [r.experiment_id for r in rows] == [1, 1, 2]
    assert rows[0].experiment_name == "AeryScan MAP7"
    assert (rows[0].microscope_id, rows[0].ptm_id, rows[0].protein_id) == (10, 20, 30)
    assert rows[1].protein_id is None  # a bucket of crops with no protein
    assert (rows[2].microscope_id, rows[2].ptm_id) == (None, None)
    assert sum(r.count for r in rows) == 11


async def test_selected_experiments_are_checked_against_the_acl(mock_db):
    """Not just "does it exist" — "may this user see it".

    Without the predicate a foreign id returns 200-with-zero-points instead of
    404, which is an existence oracle for other groups' experiments; the
    docstring claims that cannot happen, so pin the predicate itself.
    """
    mock_db.execute.return_value = make_result(scalars_all=[9])
    await mod._verify_experiments_visible([9], user_id=7, group_ids=[3], db=mock_db)

    sql = _compiled(mock_db.execute.await_args.args[0])
    assert "experiments.user_id = 7" in sql
    assert "experiments.group_id IN (3)" in sql


# =============================================================================
# The filter is actually wired into the endpoint
#
# Every test above exercises the helpers in isolation. That leaves the one
# mistake that matters most completely uncovered: the endpoint not calling them.
# Deleting the `_apply_facets` call from either corpus used to leave the whole
# suite green while the plot silently ignored every filter the user ticked.
# =============================================================================

async def _point_query(umap_type, selection, mock_db):
    """Run one corpus and hand back the compiled SQL of its point query."""
    mock_db.execute.return_value = make_result(scalars_all=[], fetchall=[])
    runner = (
        mod._get_fov_umap if umap_type is mod.UmapType.FOV else mod._get_cropped_umap
    )
    await runner(selection, user(), None, MagicMock(), mock_db)
    return _compiled(mock_db.execute.await_args_list[0].args[0])


@pytest.mark.parametrize("umap_type", [mod.UmapType.CROPPED, mod.UmapType.FOV])
async def test_every_facet_reaches_the_point_query(mock_db, no_group, umap_type):
    sql = await _point_query(
        umap_type,
        mod.FacetSelection(
            experiment_ids=[1], microscope_ids=[2], protein_ids=[3], ptm_ids=[4]
        ),
        mock_db,
    )
    assert "experiment_id IN" in sql
    assert "microscope_id IN" in sql
    assert "map_protein_id IN" in sql
    assert "ptm_id IN" in sql
    # And the ACL is still there alongside them.
    assert "experiments.user_id" in sql


async def test_cropped_filters_on_the_crop_protein(mock_db, no_group):
    # The point is coloured by the crop's protein, so filtering on the image's
    # would silently disagree with what the user sees.
    sql = await _point_query(
        mod.UmapType.CROPPED, mod.FacetSelection(protein_ids=[3]), mock_db
    )
    assert "cell_crops.map_protein_id IN" in sql


async def test_fov_filters_on_the_image_protein(mock_db, no_group):
    sql = await _point_query(
        mod.UmapType.FOV, mod.FacetSelection(protein_ids=[3]), mock_db
    )
    assert "images.map_protein_id IN" in sql


@pytest.mark.parametrize("umap_type", [mod.UmapType.CROPPED, mod.UmapType.FOV])
async def test_unfiltered_query_carries_no_facet_clause(mock_db, no_group, umap_type):
    # `facet_clause` returns None for an untouched facet, and SQLAlchemy renders
    # a bare `.where(None)` as `WHERE NULL` — which matches nothing. An empty
    # selection must therefore add no clause at all, not a null one.
    with patch.object(mod, "MIN_POINTS_FOR_UMAP", 0):
        sql = await _point_query(umap_type, mod.FacetSelection(), mock_db)
    assert "NULL" not in sql.replace("IS NOT NULL", "")


async def test_facet_summary_only_counts_rows_that_can_be_plotted(mock_db):
    # Counting unembedded rows would make the panel promise points the plot
    # cannot draw, and the mismatch has no error to explain it.
    mock_db.execute.return_value = make_result(fetchall=[])
    await mod._load_facets(mod.UmapType.CROPPED, 7, None, mock_db)
    assert "cell_crops.embedding IS NOT NULL" in _compiled(
        mock_db.execute.await_args.args[0]
    )


async def test_facet_summary_counts_the_corpus_it_was_asked_for(mock_db):
    mock_db.execute.return_value = make_result(fetchall=[])
    await mod._load_facets(mod.UmapType.FOV, 7, None, mock_db)
    sql = _compiled(mock_db.execute.await_args.args[0])
    assert "count(images.id)" in sql
    assert "cell_crops" not in sql


# =============================================================================
# Endpoint wiring
# =============================================================================

def test_endpoint_takes_the_selection_as_one_dependency():
    sig = inspect.signature(mod.get_umap_visualization)
    assert "selection" in sig.parameters


def test_dependency_exposes_all_four_facets_as_query_params():
    sig = inspect.signature(mod.facet_selection)
    assert set(sig.parameters) == {
        "experiment_id",
        "microscope_id",
        "protein_id",
        "ptm_id",
    }


def test_dependency_turns_missing_params_into_empty_lists():
    assert mod.facet_selection(None, None, None, None) == mod.FacetSelection()


async def test_stale_microscope_id_returns_404(mock_db):
    # Reference data is shared, so anyone can delete a value another user's open
    # tab still has ticked. That must fail loudly, not silently match nothing.
    mock_db.execute.return_value = make_result(scalars_all=[])
    with pytest.raises(HTTPException) as ei:
        await mod.get_umap_visualization(
            selection=mod.FacetSelection(microscope_ids=[999]),
            background_tasks=MagicMock(),
            current_user=user(),
            db=mock_db,
        )
    assert ei.value.status_code == 404
    # Exact text, not a substring: the frontend parses this to work out which
    # facet to prune, so the two literals have to fail together.
    assert ei.value.detail == "Microscope not found: 999"


async def test_stale_ptm_id_returns_404(mock_db):
    mock_db.execute.return_value = make_result(scalars_all=[])
    with pytest.raises(HTTPException) as ei:
        await mod.get_umap_visualization(
            selection=mod.FacetSelection(ptm_ids=[999]),
            background_tasks=MagicMock(),
            current_user=user(),
            db=mock_db,
        )
    assert ei.value.status_code == 404
    assert ei.value.detail == "PTM not found: 999"


async def test_stale_protein_id_returns_404(mock_db):
    # The protein facet had no endpoint-level coverage: dropping MapProtein from
    # the validation list left the whole suite green.
    mock_db.execute.return_value = make_result(scalars_all=[])
    with pytest.raises(HTTPException) as ei:
        await mod.get_umap_visualization(
            selection=mod.FacetSelection(protein_ids=[999]),
            background_tasks=MagicMock(),
            current_user=user(),
            db=mock_db,
        )
    assert ei.value.status_code == 404
    assert ei.value.detail == "MAP protein not found: 999"


async def test_unassigned_only_selection_needs_no_reference_lookup(mock_db, no_group):
    # "Unassigned" names the absence of a row. Validating it would 404 the most
    # useful PTM filter there is while the lab is still backfilling.
    mock_db.execute.return_value = make_result(scalars_all=[])
    with patch.object(mod, "MIN_POINTS_FOR_UMAP", 3):
        out = await mod.get_umap_visualization(
            selection=mod.FacetSelection(ptm_ids=[UNASSIGNED_FACET_ID]),
            background_tasks=MagicMock(),
            current_user=user(),
            db=mock_db,
        )
    assert out.points == []
