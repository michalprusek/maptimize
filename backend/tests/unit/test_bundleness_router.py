"""Bundleness on the dashboard projection and the /bundleness endpoint.

What these pin: the mean is taken over the SAME points and the SAME classes as
the separability score beside it, and a missing mean is never reported as zero.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from ml.bundleness.model import load_model
from routers import embeddings as mod
from schemas.embeddings import LabelAxis
from tests.unit.conftest import make_result

VERSION = load_model().version


def _crop(crop_id, score, *, protein=1, experiment=10, version=VERSION, umap=(0.0, 0.0)):
    return SimpleNamespace(
        id=crop_id,
        image_id=crop_id * 100,
        image=SimpleNamespace(experiment_id=experiment),
        map_protein=SimpleNamespace(id=protein, name=f"P{protein}", color="#123456")
        if protein else None,
        embedding=[float(crop_id), 1.0, 0.0],
        umap_x=umap[0] if umap else None,
        umap_y=umap[1] if umap else None,
        bundleness_score=score,
        bundleness_model=version,
    )


def _facet(experiment, microscope=None, protein=1, count=1):
    # Positional, in the order _load_facets unpacks.
    return (experiment, f"exp{experiment}", microscope, None, None, protein, count)


async def _projection(mock_db, crops, facets, axis=LabelAxis.PROTEIN, selection=None):
    mock_db.execute.side_effect = [
        make_result(scalars_all=crops),
        make_result(fetchall=facets),
    ]
    return await mod._get_cropped_umap(
        selection or mod.FacetSelection(experiment_ids=[10]),
        axis,
        mod.NO_GROUPS,
        SimpleNamespace(id=1),
        [],
        MagicMock(),
        mock_db,
    )


async def test_projection_reports_the_mean_over_the_plotted_points(mock_db):
    crops = [_crop(1, 1.0), _crop(2, 3.0), _crop(3, None, version=None)]
    response = await _projection(mock_db, crops, [_facet(10, count=3)])

    assert response.bundleness.mean == pytest.approx(2.0)
    assert response.bundleness.n_scored == 2
    assert response.bundleness.n_points == 3
    assert response.bundleness.model_version == VERSION
    assert response.bundleness.label_by is LabelAxis.PROTEIN


async def test_classes_are_the_classes_of_the_axis_being_scored(mock_db):
    crops = [
        _crop(1, 1.0, protein=4), _crop(2, 2.0, protein=4),
        _crop(3, 9.0, protein=6), _crop(4, 5.0, protein=None),
    ]
    response = await _projection(mock_db, crops, [_facet(10, count=4)])

    classes = {c.label: (c.mean, c.n_scored) for c in response.bundleness.classes}
    assert classes == {4: (pytest.approx(1.5), 2), 6: (pytest.approx(9.0), 1)}
    # The protein-less crop is on screen, so it is in the overall mean.
    assert response.bundleness.mean == pytest.approx(17.0 / 4)


async def test_an_experiment_level_axis_takes_its_classes_from_the_facets(mock_db):
    # Microscope lives on the experiment; the per-class means must use the same
    # join separability does, or the two badges describe different groupings.
    crops = [
        _crop(1, 1.0, experiment=10), _crop(2, 3.0, experiment=10),
        _crop(3, -4.0, experiment=20),
    ]
    facets = [_facet(10, microscope=7, count=2), _facet(20, microscope=9)]
    response = await _projection(
        mock_db, crops, facets, axis=LabelAxis.MICROSCOPE,
        selection=mod.FacetSelection(experiment_ids=[10, 20]),
    )

    assert response.bundleness.label_by is LabelAxis.MICROSCOPE
    assert {c.label: c.mean for c in response.bundleness.classes} == {
        7: pytest.approx(2.0), 9: pytest.approx(-4.0),
    }


async def test_points_carry_the_protein_id_the_class_means_are_keyed_by(mock_db):
    # The legend attaches a class mean to a swatch by ID. Without the id on the
    # point the client would have to match on the display name, and two
    # proteins that share one would trade means.
    crops = [_crop(1, 1.0, protein=4), _crop(2, 2.0, protein=None)]
    response = await _projection(mock_db, crops, [_facet(10, count=2)])
    assert [p.protein_id for p in response.points] == [4, None]
    assert {c.label for c in response.bundleness.classes} == {4}


async def test_a_point_scored_by_another_model_shows_no_score_and_joins_no_mean(mock_db):
    # Tooltip and badge must agree: a number the mean left out is not shown.
    crops = [_crop(1, 1.0), _crop(2, 50.0, version="v0")]
    response = await _projection(mock_db, crops, [_facet(10, count=2)])

    assert [p.bundleness_score for p in response.points] == [1.0, None]
    assert response.bundleness.mean == pytest.approx(1.0)
    assert (response.bundleness.n_scored, response.bundleness.n_points) == (1, 2)


async def test_no_scored_point_gives_null_not_zero(mock_db):
    crops = [_crop(1, None, version=None), _crop(2, None, version=None)]
    response = await _projection(mock_db, crops, [_facet(10, count=2)])
    assert response.points and response.bundleness is None


async def test_points_without_coordinates_are_not_in_the_mean(mock_db):
    # The mean describes what is drawn. A crop still waiting for its projection
    # is not on screen, so it must not move the number beside the plot.
    crops = [_crop(1, 1.0), _crop(2, 99.0, umap=None)]
    with patch.object(mod, "get_refresh_error", return_value=None):
        response = await _projection(mock_db, crops, [_facet(10, count=2)])
    assert len(response.points) == 1
    assert response.bundleness.mean == pytest.approx(1.0)
    assert response.bundleness.n_points == 1


# =============================================================================
# GET /bundleness
# =============================================================================

async def test_bundleness_endpoint_returns_the_projections_own_summary():
    summary = object()
    projection = SimpleNamespace(bundleness=summary, points=[1])
    selection, groups = mod.FacetSelection(ptm_ids=[2]), mod.NO_GROUPS
    with patch.object(mod, "get_umap_visualization",
                      new=AsyncMock(return_value=projection)) as umap:
        result = await mod.get_bundleness(
            selection=selection, label_by=LabelAxis.PTM, groups=groups,
            background_tasks=MagicMock(), current_user=SimpleNamespace(id=1), db=MagicMock(),
        )
    # Delegation is the point: one code path, so the number on the chart and the
    # number an agent is given cannot drift apart.
    assert result is summary
    assert umap.await_args.kwargs["selection"] is selection
    assert umap.await_args.kwargs["label_by"] is LabelAxis.PTM
    assert umap.await_args.kwargs["groups"] is groups


async def test_bundleness_endpoint_is_404_when_nothing_is_scored():
    projection = SimpleNamespace(bundleness=None, points=[1, 2, 3])
    with patch.object(mod, "get_umap_visualization", new=AsyncMock(return_value=projection)):
        with pytest.raises(HTTPException) as error:
            await mod.get_bundleness(
                selection=mod.FacetSelection(), label_by=LabelAxis.PROTEIN,
                groups=mod.NO_GROUPS, background_tasks=MagicMock(),
                current_user=SimpleNamespace(id=1), db=MagicMock(),
            )
    assert error.value.status_code == 404
    assert "3 plotted points" in error.value.detail


def test_bundleness_endpoint_is_registered_beside_separability():
    paths = {route.path for route in mod.router.routes}
    assert "/bundleness" in paths and "/separability" in paths
    assert "/extract-fov" not in paths
