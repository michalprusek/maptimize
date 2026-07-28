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
    """Patch get_user_group_id to return None (user belongs to no group)."""
    with patch.object(mod, "get_user_group_id", new=AsyncMock(return_value=None)):
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
    assert "Microscope" in ei.value.detail


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
    assert "PTM" in ei.value.detail


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
