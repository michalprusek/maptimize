"""The discriminant endpoint: caching, the polling contract, and the ACL.

The scientific correctness lives in test_discriminant_service.py. What is worth
pinning here is that the endpoint never fits inside a request, never returns
points without the numbers that qualify them, and applies the same access rules
as the UMAP it sits beside.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi import HTTPException

from routers import embeddings as mod
from services import discriminant_service as svc
from services.discriminant_service import DiscriminantResult
from tests.unit.conftest import make_result


def user(uid=1):
    return SimpleNamespace(id=uid)


@pytest.fixture(autouse=True)
def clean_cache():
    svc.invalidate()
    svc._inflight.clear()
    yield
    svc.invalidate()
    svc._inflight.clear()


@pytest.fixture
def no_group():
    with patch.object(mod, "get_user_group_id", new=AsyncMock(return_value=None)):
        yield


def result(n=3):
    return DiscriminantResult(
        coords=np.array([[float(i), float(-i)] for i in range(n)]),
        balanced_accuracy=0.26,
        chance=0.071,
        null_mean=0.054,
        null_max=0.078,
        n_permutations=20,
        n_proteins=14,
        n_experiments=46,
    )


def crop(cid, experiment_id=9, protein=None):
    return SimpleNamespace(
        id=cid,
        image_id=cid * 10,
        image=SimpleNamespace(experiment_id=experiment_id),
        map_protein=protein,
        bundleness_score=0.5,
    )


# =============================================================================
# The polling contract
# =============================================================================

async def test_first_call_schedules_the_fit_instead_of_running_it(mock_db, no_group):
    # Fitting is minutes; doing it in the request would hold a worker hostage.
    mock_db.execute.return_value = make_result(fetchall=[])
    bg = MagicMock()

    out = await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(), background_tasks=bg,
        current_user=user(), db=mock_db,
    )

    assert out.is_computing is True
    assert out.points == []
    assert out.metrics is None
    bg.add_task.assert_called_once()
    assert bg.add_task.call_args.args[0] is svc.refresh_discriminant_scope


async def test_a_second_poll_does_not_schedule_a_second_fit(mock_db, no_group):
    mock_db.execute.return_value = make_result(fetchall=[])
    svc._inflight.add(svc.scope_key(1, None))
    bg = MagicMock()

    out = await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(), background_tasks=bg,
        current_user=user(), db=mock_db,
    )

    assert out.is_computing is True
    bg.add_task.assert_not_called()


async def test_a_failed_fit_is_reported_and_not_rescheduled(mock_db, no_group):
    # Otherwise every poll starts another doomed multi-minute computation, in
    # silence, forever — the same trap the UMAP refresh already documents.
    mock_db.execute.return_value = make_result(fetchall=[])
    svc.record_failure(svc.scope_key(1, None), "need at least 5 experiments")
    bg = MagicMock()

    out = await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(), background_tasks=bg,
        current_user=user(), db=mock_db,
    )

    assert out.is_computing is False
    assert "5 experiments" in out.compute_error
    bg.add_task.assert_not_called()


async def test_recompute_clears_the_failure_and_reschedules(mock_db, no_group):
    key = svc.scope_key(1, None)
    svc.record_failure(key, "boom")
    bg = MagicMock()

    await mod.trigger_discriminant_recomputation(
        background_tasks=bg, current_user=user(), db=mock_db
    )

    assert svc.compute_error(key) is None
    bg.add_task.assert_called_once()


# =============================================================================
# Points never travel without the numbers that qualify them
# =============================================================================

async def test_a_cached_projection_returns_points_and_metrics_together(mock_db, no_group):
    key = svc.scope_key(1, None)
    svc.store(key, [1, 2, 3], result())
    protein = SimpleNamespace(name="MAP7", color="#abc")
    mock_db.execute.side_effect = [
        make_result(fetchall=[]),  # facet summary
        make_result(scalars_all=[crop(1, protein=protein), crop(2), crop(3)]),
    ]

    out = await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(), background_tasks=MagicMock(),
        current_user=user(), db=mock_db,
    )

    assert len(out.points) == 3
    assert out.metrics is not None
    assert out.metrics.balanced_accuracy == 0.26
    assert out.metrics.null_max == 0.078
    assert out.points[0].protein_name == "MAP7"
    assert out.points[1].protein_color == "#888888"
    assert (out.points[0].x, out.points[0].y) == (0.0, 0.0)
    assert (out.points[2].x, out.points[2].y) == (2.0, -2.0)


async def test_a_crop_added_since_the_fit_is_skipped_not_invented(mock_db, no_group):
    # The projection is a fixed frame. Placing a crop it never saw at an
    # arbitrary coordinate would be a fabricated data point on a scientific plot.
    svc.store(svc.scope_key(1, None), [1, 2], result(n=2))
    mock_db.execute.side_effect = [
        make_result(fetchall=[]),
        make_result(scalars_all=[crop(1), crop(2), crop(99)]),
    ]

    out = await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(), background_tasks=MagicMock(),
        current_user=user(), db=mock_db,
    )

    assert [p.crop_id for p in out.points] == [1, 2]


async def test_the_filter_narrows_the_points_but_never_refits(mock_db, no_group):
    """Refitting per filter would change what the axes mean as the user clicks.

    The cached projection must be reused verbatim, so a filtered point keeps the
    coordinate it had in the unfiltered view.
    """
    svc.store(svc.scope_key(1, None), [1, 2, 3], result())
    mock_db.execute.side_effect = [
        make_result(scalars_all=[4]),   # PTM 4 exists
        make_result(fetchall=[]),        # facet summary
        make_result(scalars_all=[crop(2)]),
    ]

    out = await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(ptm_ids=[4]), background_tasks=MagicMock(),
        current_user=user(), db=mock_db,
    )

    assert len(out.points) == 1
    assert (out.points[0].x, out.points[0].y) == (1.0, -1.0)


async def test_the_point_query_carries_the_acl_and_the_facets(mock_db, no_group):
    svc.store(svc.scope_key(1, None), [1], result(n=1))
    mock_db.execute.side_effect = [
        make_result(scalars_all=[3]),   # microscope 3 exists
        make_result(fetchall=[]),        # facet summary
        make_result(scalars_all=[crop(1)]),
    ]

    await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(microscope_ids=[3]), background_tasks=MagicMock(),
        current_user=user(), db=mock_db,
    )

    sql = str(mock_db.execute.await_args_list[2].args[0].compile(
        compile_kwargs={"literal_binds": True}
    ))
    assert "experiments.user_id = 1" in sql
    assert "microscope_id IN" in sql
    # Unlabelled crops cannot take part in a projection fitted on labels.
    assert "map_protein_id IS NOT NULL" in sql


# =============================================================================
# Shared rules with the UMAP endpoint
# =============================================================================

async def test_a_stale_reference_id_is_404(mock_db):
    mock_db.execute.return_value = make_result(scalars_all=[])
    with pytest.raises(HTTPException) as ei:
        await mod.get_discriminant_visualization(
            selection=mod.FacetSelection(ptm_ids=[999]),
            background_tasks=MagicMock(), current_user=user(), db=mock_db,
        )
    assert ei.value.status_code == 404
    assert ei.value.detail == "PTM not found: 999"


async def test_the_scope_key_separates_users_from_groups():
    # Group members share a corpus and therefore a cached fit; a user id and a
    # group id with the same number must not collide.
    assert svc.scope_key(2, None) != svc.scope_key(1, 2)
    assert svc.scope_key(7, 3) == svc.scope_key(9, 3)
