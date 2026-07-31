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
    with patch.object(mod, "get_user_group_ids", new=AsyncMock(return_value=[])):
        yield


@pytest.fixture(autouse=True)
def fresh_corpus():
    """Default to "the corpus has not moved", so staleness is opt-in per test."""
    with patch.object(
        svc, "current_fingerprint", new=AsyncMock(return_value="fp")
    ):
        yield


def result(n=3, fingerprint="fp"):
    return DiscriminantResult(
        crop_ids=tuple(range(1, n + 1)),
        fingerprint=fingerprint,
        coords=np.array([[float(i), float(-i)] for i in range(n)]),
        balanced_accuracy=0.26,
        chance=0.071,
        null_mean=0.054,
        null_max=0.078,
        null_p95=0.076,
        unscoreable_proteins=("MAP7",),
        p_value=1 / 21,
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
    svc._inflight.add(svc.scope_key(1, []))
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
    # Recorded against the fingerprint the fixture reports, so the failure is
    # still current and must NOT be retired.
    svc.record_failure(svc.scope_key(1, []), "need at least 5 experiments", None, "fp")
    bg = MagicMock()

    out = await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(), background_tasks=bg,
        current_user=user(), db=mock_db,
    )

    assert out.is_computing is False
    assert "5 experiments" in out.compute_error
    bg.add_task.assert_not_called()


async def test_recompute_clears_the_failure_and_reschedules(mock_db, no_group):
    key = svc.scope_key(1, [])
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
    key = svc.scope_key(1, [])
    svc.store(key, result(), svc.generation(key))
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
    svc.store(svc.scope_key(1, []), result(n=2), svc.generation(svc.scope_key(1, [])))
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
    svc.store(svc.scope_key(1, []), result(), svc.generation(svc.scope_key(1, [])))
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
    svc.store(svc.scope_key(1, []), result(n=1), svc.generation(svc.scope_key(1, [])))
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


async def test_the_scope_key_is_the_corpus_identity():
    """The cache key must change whenever the fitted corpus can.

    It used to be the group id alone, on the assumption that joining a group
    adopted the joiner's group-less work, so members read identical corpora.
    Adoption is gone and membership is many-to-many, so neither half holds: two
    members of one group differ by their unshared experiments, and a member of
    {2, 5} reads strictly more than a member of {2}. The key therefore carries
    the user AND the sorted group set. Sharing a cached fit across users would
    report a balanced accuracy computed on a corpus the caller cannot see.
    """
    assert svc.scope_key(7, [2]) != svc.scope_key(9, [2])
    assert svc.scope_key(7, [2]) != svc.scope_key(7, [2, 5])
    # Order of membership rows must not create two keys for one corpus.
    assert svc.scope_key(7, [5, 2]) == svc.scope_key(7, [2, 5])
    # A user id and a group id sharing an integer must not collide.
    assert svc.scope_key(2, []) != svc.scope_key(1, [2])


# =============================================================================
# Staleness — the projection is a snapshot, the labels beside it are live
# =============================================================================

async def test_a_moved_corpus_is_reported_stale_and_refitted(mock_db, no_group):
    """Coordinates come from the fit; names and colours are read live.

    Re-annotate a batch and, without this, its crops are drawn at their OLD
    coordinates in their NEW colour — sitting in the wrong cluster wearing the
    right label, under a score cross-validated against labels that no longer
    exist.
    """
    svc.store(svc.scope_key(1, []), result(), svc.generation(svc.scope_key(1, [])))
    bg = MagicMock()
    mock_db.execute.side_effect = [
        make_result(fetchall=[]),                       # facet summary
        make_result(scalars_all=[crop(1), crop(2), crop(3)]),
    ]
    with patch.object(svc, "current_fingerprint", new=AsyncMock(return_value="moved")):
        out = await mod.get_discriminant_visualization(
            selection=mod.FacetSelection(), background_tasks=bg,
            current_user=user(), db=mock_db,
        )

    assert out.is_stale is True
    bg.add_task.assert_called_once()
    assert bg.add_task.call_args.args[0] is svc.refresh_discriminant_scope


async def test_an_unchanged_corpus_is_not_stale_and_schedules_nothing(mock_db, no_group):
    svc.store(svc.scope_key(1, []), result(), svc.generation(svc.scope_key(1, [])))
    bg = MagicMock()
    mock_db.execute.side_effect = [
        make_result(fetchall=[]),
        make_result(scalars_all=[crop(1), crop(2), crop(3)]),
    ]
    out = await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(), background_tasks=bg,
        current_user=user(), db=mock_db,
    )
    assert out.is_stale is False
    bg.add_task.assert_not_called()


async def test_crops_the_fit_never_saw_are_counted_even_though_they_cannot_be_drawn(
    mock_db, no_group
):
    # Reporting only what was plotted would make an out-of-date projection look
    # like a smaller corpus — the mismatch UMAP's own code comment forbids.
    svc.store(svc.scope_key(1, []), result(n=2), svc.generation(svc.scope_key(1, [])))
    mock_db.execute.side_effect = [
        make_result(fetchall=[]),
        make_result(scalars_all=[crop(1), crop(2), crop(99)]),
    ]
    out = await mod.get_discriminant_visualization(
        selection=mod.FacetSelection(), background_tasks=MagicMock(),
        current_user=user(), db=mock_db,
    )
    assert len(out.points) == 2
    assert out.total_crops == 3
    assert out.is_stale is True


async def test_a_fit_superseded_while_it_ran_is_discarded(mock_db):
    """Retry during a fit must not be overwritten by the fit it replaced.

    `invalidate` cannot cancel work already running, so without the generation
    guard the older fit writes its stale result back afterwards AND clears the
    failure flag, marking it fresh.
    """
    key = svc.scope_key(1, [])
    gen = svc.generation(key)
    svc.invalidate(key)                       # the user pressed Retry
    svc.store(key, result(), gen)             # the older fit finishes late
    assert svc.cached(key) is None


# =============================================================================
# refresh_discriminant_scope — the background fit, whose ACL decides whose crops
# the projection is fitted on. Every line below was droppable with the whole
# suite green before these tests existed.
# =============================================================================

async def _run_refresh(rows, monkeypatch, user_id=7, group_ids=()):
    """Drive the background fit with a stubbed session and a stubbed compute."""
    import contextlib

    db = AsyncMock()
    db.execute.return_value = make_result(fetchall=rows)

    @contextlib.asynccontextmanager
    async def session():
        yield db

    seen = {}

    def fake_compute(
        embeddings, labels, groups, microscopes, n_perm, crop_ids, fp, names
    ):
        seen.update(
            labels=list(labels), groups=list(groups),
            microscopes=list(microscopes), crop_ids=list(crop_ids), fingerprint=fp,
            label_names=names,
        )
        return result(n=len(labels))

    monkeypatch.setattr("database.async_session_maker", session)
    monkeypatch.setattr(svc, "compute_discriminant", fake_compute)
    await svc.refresh_discriminant_scope(user_id, group_ids)
    return db, seen


async def test_the_background_fit_is_scoped_to_what_the_user_may_read(monkeypatch):
    """The ACL that decides WHICH crops are fitted.

    Dropping it fits the projection on every user's crops, and then reports a
    balanced accuracy, protein count and experiment count describing a corpus the
    caller cannot see — a scientific claim about someone else's data, rendered in
    green. Asserted on the compiled SQL because a mock cannot enforce a predicate.
    """
    db, _ = await _run_refresh([(1, [0.0, 1.0], 5, 9, 2, "MAP7")], monkeypatch, user_id=7)

    sql = str(db.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "experiments.user_id = 7" in sql


async def test_the_background_fit_widens_to_the_group(monkeypatch):
    db, _ = await _run_refresh([(1, [0.0, 1.0], 5, 9, 2, "MAP7")], monkeypatch, user_id=7, group_ids=[3])

    sql = str(db.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "experiments.user_id = 7" in sql
    assert "experiments.group_id IN (3)" in sql


async def test_the_background_fit_only_takes_labelled_embedded_crops(monkeypatch):
    # An unlabelled crop has no class to be fitted to; feeding NULL labels in
    # either crashes the fit or invents a "protein" made of unassigned crops.
    db, _ = await _run_refresh([(1, [0.0, 1.0], 5, 9, 2, "MAP7")], monkeypatch)

    sql = str(db.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "cell_crops.embedding IS NOT NULL" in sql
    assert "cell_crops.map_protein_id IS NOT NULL" in sql


async def test_the_background_fit_passes_the_right_columns_through(monkeypatch):
    # Transposing any of these silently fits on the wrong thing: groups drive the
    # leak-free split, microscopes drive the centering.
    rows = [
        (11, [0.0, 1.0], 5, 90, 2, "CLIP170"),
        (12, [1.0, 0.0], 6, 91, 3, "TRIM46"),
    ]
    _, seen = await _run_refresh(rows, monkeypatch)

    assert seen["crop_ids"] == [11, 12]
    assert seen["labels"] == [5, 6]
    assert seen["groups"] == [90, 91]
    assert seen["microscopes"] == [2, 3]
    assert seen["fingerprint"]
    # Protein ids are what the model is fitted on, but names are what the metrics
    # report. Without the map the strip prints "5 0.79" at a biologist.
    assert seen["label_names"] == {5: "CLIP170", 6: "TRIM46"}


async def test_an_empty_corpus_is_recorded_rather_than_left_spinning(monkeypatch):
    await _run_refresh([], monkeypatch)
    key = svc.scope_key(7, [])
    assert "protein assignment" in svc.compute_error(key)
    assert not svc.is_computing(key)


async def test_a_degenerate_corpus_records_why_instead_of_polling_forever(monkeypatch):
    import contextlib

    db = AsyncMock()
    db.execute.return_value = make_result(fetchall=[(1, [0.0], 5, 9, 2, "MAP7")])

    @contextlib.asynccontextmanager
    async def session():
        yield db

    def boom(*a, **k):
        raise svc.NotEnoughDataError("need at least 5 experiments to cross-validate across")

    monkeypatch.setattr("database.async_session_maker", session)
    monkeypatch.setattr(svc, "compute_discriminant", boom)
    await svc.refresh_discriminant_scope(7, [])

    key = svc.scope_key(7, [])
    assert "5 experiments" in svc.compute_error(key)
    assert not svc.is_computing(key)


async def test_the_scope_is_released_even_when_the_fit_explodes(monkeypatch):
    # A wedged in-flight flag would block every future fit for that scope.
    import contextlib

    db = AsyncMock()
    db.execute.return_value = make_result(fetchall=[(1, [0.0], 5, 9, 2, "MAP7")])

    @contextlib.asynccontextmanager
    async def session():
        yield db

    def boom(*a, **k):
        raise RuntimeError("sklearn exploded")

    monkeypatch.setattr("database.async_session_maker", session)
    monkeypatch.setattr(svc, "compute_discriminant", boom)
    await svc.refresh_discriminant_scope(7, [])

    key = svc.scope_key(7, [])
    assert not svc.is_computing(key)
    assert "Projection failed" in svc.compute_error(key)


async def test_a_failure_is_retired_once_the_corpus_moves():
    """"Need at least 5 experiments" must not outlive the condition it describes."""
    key = svc.scope_key(7, [])
    svc.record_failure(key, "need at least 5 experiments", None, "old-corpus")

    assert svc.clear_failure_if_corpus_moved(key, "old-corpus") is False
    assert svc.compute_error(key) is not None

    assert svc.clear_failure_if_corpus_moved(key, "new-corpus") is True
    assert svc.compute_error(key) is None


def test_invalidating_one_scope_leaves_every_other_alone():
    # One user retrying their own failed fit must not cost every other user on
    # this worker minutes of recompute.
    mine, theirs = svc.scope_key(1, []), svc.scope_key(2, [])
    svc.store(mine, result(), svc.generation(mine))
    svc.store(theirs, result(), svc.generation(theirs))

    svc.invalidate(mine)

    assert svc.cached(mine) is None
    assert svc.cached(theirs) is not None
