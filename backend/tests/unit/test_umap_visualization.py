"""In-process unit tests for services.umap_service and
services.visualization_service.

* UMAP fitting is mocked: ``_compute_umap_projection`` patches the heavy
  ``umap`` import with a fake reducer returning deterministic 2D coords, so we
  exercise the real branching (n_neighbors clamping, init selection) without the
  slow optimiser. Higher-level functions either run through that fake reducer or
  patch ``_compute_umap_projection`` directly.
* Visualization uses real matplotlib (Agg backend, configured by the service on
  import) and asserts the returned base64/bytes payloads.

DB access is always the AsyncMock ``mock_db`` fixture configured via
``make_result`` (see conftest).
"""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# conftest installs a MagicMock for ``torch`` in sys.modules. seaborn (pulled in
# by visualization_service) imports scipy.stats, whose array-API helper runs
# ``issubclass(cls, sys.modules['torch'].Tensor)`` at import time — that blows up
# on a MagicMock attribute. Give torch.Tensor a real class so the check is benign
# before importing the visualization service.
if "torch" in sys.modules and not isinstance(getattr(sys.modules["torch"], "Tensor", None), type):
    class _FakeTensor:  # noqa: D401 - placeholder so issubclass() works
        pass

    sys.modules["torch"].Tensor = _FakeTensor

import services.umap_service as umap_service  # noqa: E402
import services.visualization_service as viz  # noqa: E402
from schemas.embeddings import UmapType  # noqa: E402
from tests.unit.conftest import make_result  # noqa: E402


# =============================================================================
# Helpers / fakes
# =============================================================================
class _Protein:
    def __init__(self, pid):
        self.id = pid


def _item(embedding, protein_id=None):
    """A stand-in CellCrop / Image with embedding + optional map_protein."""
    return SimpleNamespace(
        embedding=embedding,
        map_protein=_Protein(protein_id) if protein_id is not None else None,
        umap_x=None,
        umap_y=None,
        umap_computed_at=None,
    )


def _fake_umap_module():
    """A fake ``umap`` module whose UMAP().fit_transform returns deterministic
    coordinates (row index repeated across the two components)."""
    module = MagicMock(name="umap")

    def _make_reducer(*args, **kwargs):
        reducer = MagicMock(name="UMAP")
        reducer._init_kwargs = kwargs

        def _fit_transform(data):
            n = len(data)
            return np.array([[float(i), float(i) + 0.5] for i in range(n)])

        reducer.fit_transform.side_effect = _fit_transform
        return reducer

    module.UMAP.side_effect = _make_reducer
    return module


def _content_umap_module(scale=10.0):
    """Fake ``umap`` whose coordinates derive from row CONTENT, not row index.

    The index-based fake above cannot detect a mis-mapping: any permutation
    that preserves groups still satisfies "twins match" and "distinct rows
    differ". Only content-derived output pins row i to its own embedding.
    """
    module = MagicMock(name="umap")

    def _make_reducer(*args, **kwargs):
        reducer = MagicMock(name="UMAP")
        reducer.fit_transform.side_effect = lambda d: np.asarray(d)[:, :2] * scale
        return reducer

    module.UMAP.side_effect = _make_reducer
    return module


# =============================================================================
# _normalize_embeddings
# =============================================================================
def test_normalize_embeddings_unit_norm():
    emb = np.array([[3.0, 4.0], [1.0, 0.0]])
    out = umap_service._normalize_embeddings(emb)
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0)


def test_normalize_embeddings_zero_row_uses_one():
    # Zero vector → norm replaced with 1, row stays all-zeros (no div-by-zero).
    emb = np.array([[0.0, 0.0], [0.0, 5.0]])
    out = umap_service._normalize_embeddings(emb)
    assert np.allclose(out[0], [0.0, 0.0])
    assert np.allclose(out[1], [0.0, 1.0])


# =============================================================================
# _compute_umap_projection (branching on n_neighbors / init method)
# =============================================================================
def test_compute_projection_spectral_init_large_dataset():
    fake = _fake_umap_module()
    data = np.random.rand(20, 8)
    with patch.dict("sys.modules", {"umap": fake}):
        proj = umap_service._compute_umap_projection(
            data, n_neighbors=15, min_dist=0.1
        )
    assert proj.shape == (20, 2)
    # 20 samples (>= 10), not forced random → spectral; n_neighbors clamped to 15.
    kwargs = fake.UMAP.call_args.kwargs
    assert kwargs["init"] == "spectral"
    assert kwargs["n_neighbors"] == 15


def test_compute_projection_clamps_neighbors_and_random_init_small():
    fake = _fake_umap_module()
    data = np.random.rand(4, 8)  # n_samples=4 (< 10) → random init
    with patch.dict("sys.modules", {"umap": fake}):
        proj = umap_service._compute_umap_projection(
            data, n_neighbors=15, min_dist=0.1
        )
    assert proj.shape == (4, 2)
    kwargs = fake.UMAP.call_args.kwargs
    # effective = min(15, 4-1)=3, init forced random for small dataset.
    assert kwargs["n_neighbors"] == 3
    assert kwargs["init"] == "random"


def test_compute_projection_clamps_neighbors_at_smallest_fittable_size():
    fake = _fake_umap_module()
    data = np.random.rand(3, 8)  # min(15, 3-1)=2 — the smallest fittable case
    with patch.dict("sys.modules", {"umap": fake}):
        umap_service._compute_umap_projection(data, n_neighbors=15, min_dist=0.1)
    assert fake.UMAP.call_args.kwargs["n_neighbors"] == 2


# =============================================================================
# Duplicate collapsing
#
# Two proteins with the same amino-acid sequence must land on the same point.
# UMAP's layout optimiser applies random negative sampling per row, so feeding
# it duplicate rows scatters them (measured on real data: bit-identical protein
# embeddings ended up ~7% of the plot diagonal apart). The fit therefore runs
# over unique rows and each group's coordinates are copied back to its members.
# =============================================================================
def test_identical_embeddings_get_identical_coordinates():
    fake = _fake_umap_module()
    base = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.25, 0.75]])
    data = np.repeat(base, 2, axis=0)  # 8 rows, 4 distinct

    with patch.dict("sys.modules", {"umap": fake}):
        proj = umap_service._compute_umap_projection(data, n_neighbors=15, min_dist=0.1)

    assert proj.shape == (8, 2)
    for i in range(4):
        assert np.array_equal(proj[2 * i], proj[2 * i + 1]), f"twin {i} was split"


def test_duplicate_rows_are_fitted_only_once():
    fake = _fake_umap_module()
    base = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.25, 0.75]])
    data = np.repeat(base, 2, axis=0)

    with patch.dict("sys.modules", {"umap": fake}):
        umap_service._compute_umap_projection(data, n_neighbors=15, min_dist=0.1)

    # The fake reducer echoes the row index, so the row count it saw is what
    # n_neighbors was clamped against: 4 unique rows → min(15, 4-1) = 3.
    assert fake.UMAP.call_args.kwargs["n_neighbors"] == 3


def test_distinct_embeddings_keep_distinct_coordinates():
    fake = _fake_umap_module()
    data = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.25, 0.75]])

    with patch.dict("sys.modules", {"umap": fake}):
        proj = umap_service._compute_umap_projection(data, n_neighbors=15, min_dist=0.1)

    assert len({tuple(row) for row in proj}) == 4


def test_each_row_gets_the_coordinates_of_its_own_embedding():
    """Row i must get the coordinates of the unique row whose CONTENT matches.

    ``np.unique`` returns rows in lexicographic order, so the inverse map is the
    only thing keeping each protein attached to its own point. A permutation
    here draws a plot that looks entirely plausible — right number of points,
    twins still coincident — while every label sits on someone else's dot.
    """
    fake = _content_umap_module()
    data = np.array([
        [0.9, 0.1, 0.0],
        [0.2, 0.8, 0.0],
        [0.9, 0.1, 0.0],   # duplicate of row 0
        [0.5, 0.5, 0.0],
        [0.2, 0.8, 0.0],   # duplicate of row 1
    ])

    with patch.dict("sys.modules", {"umap": fake}):
        proj = umap_service._compute_umap_projection(data, n_neighbors=15, min_dist=0.1)

    assert np.allclose(proj, data[:, :2] * 10.0)


def test_fewer_than_three_distinct_rows_raises_instead_of_inventing_a_layout():
    """All-identical input cannot be projected — and must say so.

    Returning a placeholder layout would be stored in umap_x/umap_y and served
    as a real projection, so nobody would ever learn the plot is fiction.
    """
    fake = _fake_umap_module()
    data = np.ones((5, 4))

    with patch.dict("sys.modules", {"umap": fake}):
        with pytest.raises(umap_service.DegenerateEmbeddingsError, match="distinct"):
            umap_service._compute_umap_projection(data, n_neighbors=15, min_dist=0.1)

    fake.UMAP.assert_not_called()


def test_two_distinct_rows_also_raise():
    fake = _fake_umap_module()
    data = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])

    with patch.dict("sys.modules", {"umap": fake}):
        with pytest.raises(umap_service.DegenerateEmbeddingsError):
            umap_service._compute_umap_projection(data, n_neighbors=15, min_dist=0.1)

    fake.UMAP.assert_not_called()


def test_degenerate_error_is_a_valueerror():
    """Callers that already handle the 'not enough samples' ValueError keep
    working without a new except clause."""
    assert issubclass(umap_service.DegenerateEmbeddingsError, ValueError)


def test_compute_projection_use_random_init_flag_large():
    fake = _fake_umap_module()
    data = np.random.rand(20, 8)
    with patch.dict("sys.modules", {"umap": fake}):
        umap_service._compute_umap_projection(
            data, n_neighbors=5, min_dist=0.1, use_random_init=True
        )
    # large dataset but explicit flag forces random init.
    assert fake.UMAP.call_args.kwargs["init"] == "random"


# =============================================================================
# compute_silhouette
# =============================================================================
def test_silhouette_none_when_too_few_labeled():
    items = [_item([0.0], protein_id=1) for _ in range(5)]  # < 10 labeled
    assert umap_service.compute_silhouette(np.random.rand(5, 4), items) is None


def test_silhouette_none_when_single_label():
    # 10 labeled but all same protein → < 2 distinct labels.
    items = [_item([0.0], protein_id=1) for _ in range(10)]
    assert umap_service.compute_silhouette(np.random.rand(10, 4), items) is None


def _patch_silhouette(value=0.42):
    """Patch the (lazily imported) sklearn silhouette_score with a stub.

    The real implementation pulls in sklearn's array-API compat layer, which
    raises ``AttributeError: numpy.dtypes has no attribute 'VoidDType'`` when run
    under coverage's C tracer (an environment-only interaction). Stubbing keeps
    the success branch of compute_silhouette covered deterministically.
    """
    fake_metrics = MagicMock()
    fake_metrics.silhouette_score.return_value = value
    return patch.dict("sys.modules", {"sklearn.metrics": fake_metrics})


def test_silhouette_success_two_labels():
    items = [_item([0.0], protein_id=(i % 2)) for i in range(12)]
    emb = np.random.rand(12, 6)
    with _patch_silhouette(0.42):
        score = umap_service.compute_silhouette(emb, items)
    assert score == pytest.approx(0.42)
    assert -1.0 <= score <= 1.0


def test_silhouette_handles_value_error(monkeypatch):
    items = [_item([0.0], protein_id=(i % 2)) for i in range(12)]
    fake_metrics = MagicMock()
    fake_metrics.silhouette_score.side_effect = ValueError("bad")
    with patch.dict("sys.modules", {"sklearn.metrics": fake_metrics}):
        assert umap_service.compute_silhouette(np.random.rand(12, 6), items) is None


def test_silhouette_ignores_unlabeled_items():
    # Mix labeled + unlabeled; only labeled ones contribute.
    items = [_item([0.0], protein_id=(i % 2)) for i in range(11)]
    items.append(_item([0.0], protein_id=None))  # unlabeled, skipped
    emb = np.random.rand(12, 5)
    with _patch_silhouette(0.1):
        assert umap_service.compute_silhouette(emb, items) is not None


# =============================================================================
# compute_umap_online / compute_protein_umap_online
# =============================================================================
def test_compute_umap_online_too_few_raises():
    with pytest.raises(ValueError, match="at least 3 samples"):
        umap_service.compute_umap_online(np.random.rand(2, 4), [])


def test_compute_umap_online_returns_projection_and_silhouette():
    items = [_item([0.0], protein_id=(i % 2)) for i in range(12)]
    emb = np.random.rand(12, 6)
    with patch.object(
        umap_service, "_compute_umap_projection",
        return_value=np.zeros((12, 2)),
    ), _patch_silhouette(0.3):
        proj, sil = umap_service.compute_umap_online(emb, items)
    assert proj.shape == (12, 2)
    assert sil is not None


def test_protein_umap_online_too_few_raises():
    with pytest.raises(ValueError, match="at least 3 proteins"):
        umap_service.compute_protein_umap_online(np.random.rand(2, 4))


def test_protein_umap_online_returns_none_silhouette():
    emb = np.random.rand(5, 6)
    with patch.object(
        umap_service, "_compute_umap_projection",
        return_value=np.zeros((5, 2)),
    ):
        proj, sil = umap_service.compute_protein_umap_online(emb)
    assert proj.shape == (5, 2)
    assert sil is None


# =============================================================================
# _compute_and_store_umap (via compute_crop_umap / compute_fov_umap)
# =============================================================================
async def test_compute_crop_umap_too_few(mock_db):
    crops = [_item([0.1, 0.2]) for _ in range(3)]  # < MIN_POINTS_FOR_UMAP
    # First execute() = group lookup, second = crops query.
    mock_db.execute.side_effect = [
        make_result(scalar=None),
        make_result(scalars_all=crops),
    ]
    result = await umap_service.compute_crop_umap(user_id=1, db=mock_db)
    assert "error" in result
    assert result["count"] == 3
    mock_db.commit.assert_not_awaited()


async def test_compute_crop_umap_success_with_group(mock_db):
    # Labeled crops → also exercise the silhouette success branch (stubbed sklearn).
    crops = [_item([float(i), float(i) + 1], protein_id=(i % 2)) for i in range(12)]
    mock_db.execute.side_effect = [
        make_result(scalar=99),  # user is in group 99
        make_result(scalars_all=crops),
    ]
    with patch.object(
        umap_service, "_compute_umap_projection",
        return_value=np.arange(24, dtype=float).reshape(12, 2),
    ), _patch_silhouette(0.55):
        result = await umap_service.compute_crop_umap(user_id=1, db=mock_db)
    assert result["success"] == 12
    assert result["silhouette_score"] == pytest.approx(0.55)
    assert "computed_at" in result
    mock_db.commit.assert_awaited_once()
    # coordinates written back onto items
    assert crops[0].umap_x == 0.0 and crops[0].umap_y == 1.0
    assert crops[0].umap_computed_at is not None


async def test_compute_fov_umap_success_with_group(mock_db):
    images = [_item([float(i), 0.0]) for i in range(11)]  # no protein labels
    mock_db.execute.side_effect = [
        make_result(scalar=88),  # user is in group 88 → group owner condition
        make_result(scalars_all=images),
    ]
    with patch.object(
        umap_service, "_compute_umap_projection",
        return_value=np.zeros((11, 2)),
    ):
        result = await umap_service.compute_fov_umap(user_id=2, db=mock_db)
    assert result["success"] == 11
    # silhouette None (no labels) → still fine
    assert result["silhouette_score"] is None
    mock_db.commit.assert_awaited_once()


async def test_compute_fov_umap_too_few(mock_db):
    images = [_item([0.0]) for _ in range(2)]
    mock_db.execute.side_effect = [
        make_result(scalar=None),
        make_result(scalars_all=images),
    ]
    result = await umap_service.compute_fov_umap(user_id=2, db=mock_db)
    assert "error" in result
    assert result["count"] == 2


# =============================================================================
# Invalidation functions
# =============================================================================
# =============================================================================
# refresh_umap_scope (self-healing background refresh)
# =============================================================================
def _db_context(db):
    """A get_db_context() stand-in yielding `db`."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def test_refresh_scope_key_groups_share_one_key():
    # Two members of the same group must collapse onto one key, so one
    # member's dashboard does not schedule a refresh the other duplicates.
    assert (
        umap_service.refresh_scope_key(UmapType.CROPPED, user_id=1, group_id=2)
        == umap_service.refresh_scope_key(UmapType.CROPPED, user_id=99, group_id=2)
    )
    # Ungrouped users each get their own key, and types never collide.
    assert (
        umap_service.refresh_scope_key(UmapType.CROPPED, 1, None)
        != umap_service.refresh_scope_key(UmapType.CROPPED, 2, None)
    )
    assert (
        umap_service.refresh_scope_key(UmapType.CROPPED, 1, 2)
        != umap_service.refresh_scope_key(UmapType.FOV, 1, 2)
    )


def test_refresh_scope_key_user_and_group_ids_do_not_collide():
    # The scope token is prefixed precisely so that group 2 and user 2 — which
    # share an integer space — cannot land on the same key.
    assert (
        umap_service.refresh_scope_key(UmapType.CROPPED, user_id=2, group_id=None)
        != umap_service.refresh_scope_key(UmapType.CROPPED, user_id=7, group_id=2)
    )


async def test_refresh_umap_scope_crops(mock_db):
    with patch("database.get_db_context", return_value=_db_context(mock_db)), \
         patch.object(umap_service, "compute_crop_umap",
                      new=AsyncMock(return_value={"success": 5})) as ccu, \
         patch.object(umap_service, "compute_fov_umap", new=AsyncMock()) as cfu:
        await umap_service.refresh_umap_scope(UmapType.CROPPED, user_id=1, group_id=2)
    # Always full-scope: passing an experiment would fit a subset into the
    # shared coordinate space.
    ccu.assert_awaited_once_with(1, mock_db)
    cfu.assert_not_awaited()
    assert not umap_service._inflight_refreshes  # key released


async def test_refresh_umap_scope_images(mock_db):
    with patch("database.get_db_context", return_value=_db_context(mock_db)), \
         patch.object(umap_service, "compute_fov_umap",
                      new=AsyncMock(return_value={"success": 3})) as cfu, \
         patch.object(umap_service, "compute_crop_umap", new=AsyncMock()) as ccu:
        await umap_service.refresh_umap_scope(UmapType.FOV, user_id=1)
    cfu.assert_awaited_once_with(1, mock_db)
    ccu.assert_not_awaited()


async def test_refresh_umap_scope_error_result_logged(mock_db):
    # "Too few points" is a normal outcome, not a crash — and not a failure the
    # read path should report to the user.
    with patch("database.get_db_context", return_value=_db_context(mock_db)), \
         patch.object(umap_service, "compute_crop_umap",
                      new=AsyncMock(return_value={"error": "too few"})):
        await umap_service.refresh_umap_scope(UmapType.CROPPED, user_id=1)
    assert not umap_service._inflight_refreshes
    assert umap_service.get_refresh_error(UmapType.CROPPED, 1, None) is None


async def test_refresh_umap_scope_swallows_exception_and_releases_key():
    # Fire-and-forget background task: a failure must never escape, and must
    # not wedge the guard shut against later refreshes.
    with patch("database.get_db_context", side_effect=RuntimeError("db down")):
        await umap_service.refresh_umap_scope(UmapType.CROPPED, user_id=1, group_id=2)
    assert not umap_service._inflight_refreshes


async def test_refresh_umap_scope_records_failure_for_the_scope():
    # A failed fit must be remembered, so the read path stops rescheduling a
    # doomed multi-second fit on every 5s poll.
    with patch("database.get_db_context", side_effect=RuntimeError("db down")):
        await umap_service.refresh_umap_scope(UmapType.CROPPED, 1, 2)

    err = umap_service.get_refresh_error(UmapType.CROPPED, 1, 2)
    assert err is not None and "db down" in err
    # Scoped: a different corpus / a different group is unaffected.
    assert umap_service.get_refresh_error(UmapType.FOV, 1, 2) is None
    assert umap_service.get_refresh_error(UmapType.CROPPED, 1, 3) is None
    # Group members share the scope, so they see the same failure.
    assert umap_service.get_refresh_error(UmapType.CROPPED, 99, 2) == err


async def test_refresh_umap_scope_success_clears_previous_failure(mock_db):
    with patch("database.get_db_context", side_effect=RuntimeError("boom")):
        await umap_service.refresh_umap_scope(UmapType.CROPPED, 1, 2)
    assert umap_service.get_refresh_error(UmapType.CROPPED, 1, 2) is not None

    with patch("database.get_db_context", return_value=_db_context(mock_db)), \
         patch.object(umap_service, "compute_crop_umap",
                      new=AsyncMock(return_value={"success": 5})):
        await umap_service.refresh_umap_scope(UmapType.CROPPED, 1, 2)
    assert umap_service.get_refresh_error(UmapType.CROPPED, 1, 2) is None


async def test_clear_refresh_error_allows_retry():
    with patch("database.get_db_context", side_effect=RuntimeError("boom")):
        await umap_service.refresh_umap_scope(UmapType.CROPPED, 1, 2)
    assert umap_service.get_refresh_error(UmapType.CROPPED, 1, 2) is not None

    umap_service.clear_refresh_error(UmapType.CROPPED, 1, 2)
    assert umap_service.get_refresh_error(UmapType.CROPPED, 1, 2) is None
    # Clearing an unknown scope is a no-op, not a KeyError.
    umap_service.clear_refresh_error(UmapType.FOV, 42, None)


async def test_refresh_umap_scope_skips_concurrent_duplicate(mock_db):
    # A second refresh for the same scope while one is in flight is dropped,
    # rather than duplicating seconds of CPU and racing on the same rows.
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_compute(user_id, db):
        started.set()
        await release.wait()
        return {"success": 1}

    with patch("database.get_db_context", return_value=_db_context(mock_db)), \
         patch.object(umap_service, "compute_crop_umap",
                      new=AsyncMock(side_effect=_slow_compute)) as ccu:
        first = asyncio.create_task(
            umap_service.refresh_umap_scope(UmapType.CROPPED, 1, 2)
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        # Same scope via a different group member -> must be skipped.
        # wait_for: without the guard this call blocks on `release` forever, and
        # a hung test is a far worse signal than a failed one.
        await asyncio.wait_for(
            umap_service.refresh_umap_scope(UmapType.CROPPED, 99, 2), timeout=5
        )
        assert ccu.await_count == 1
        release.set()
        await asyncio.wait_for(first, timeout=5)

    assert ccu.await_count == 1
    assert not umap_service._inflight_refreshes

    # Guard released -> a later refresh runs again.
    with patch("database.get_db_context", return_value=_db_context(mock_db)), \
         patch.object(umap_service, "compute_crop_umap",
                      new=AsyncMock(return_value={"success": 1})) as ccu2:
        await umap_service.refresh_umap_scope(UmapType.CROPPED, 1, 2)
    ccu2.assert_awaited_once()


async def test_invalidate_crop_umap_by_image(mock_db):
    mock_db.execute.return_value = make_result(rowcount=5)
    assert await umap_service.invalidate_crop_umap(mock_db, image_id=10) == 5


async def test_invalidate_crop_umap_by_experiment(mock_db):
    mock_db.execute.return_value = make_result(rowcount=3)
    assert await umap_service.invalidate_crop_umap(mock_db, experiment_id=4) == 3


async def test_invalidate_crop_umap_no_filter(mock_db):
    mock_db.execute.return_value = make_result(rowcount=0)
    assert await umap_service.invalidate_crop_umap(mock_db) == 0


async def test_invalidate_fov_umap_by_image(mock_db):
    mock_db.execute.return_value = make_result(rowcount=1)
    assert await umap_service.invalidate_fov_umap(mock_db, image_id=2) == 1


async def test_invalidate_fov_umap_by_experiment(mock_db):
    mock_db.execute.return_value = make_result(rowcount=7)
    assert await umap_service.invalidate_fov_umap(mock_db, experiment_id=9) == 7


async def test_invalidate_fov_umap_no_filter(mock_db):
    mock_db.execute.return_value = make_result(rowcount=2)
    assert await umap_service.invalidate_fov_umap(mock_db) == 2


async def test_invalidate_protein_umap(mock_db):
    mock_db.execute.return_value = make_result(rowcount=42)
    assert await umap_service.invalidate_protein_umap(mock_db) == 42


# =============================================================================
# compute_protein_umap
# =============================================================================
async def test_compute_protein_umap_too_few(mock_db):
    proteins = [_item([0.0]) for _ in range(4)]
    mock_db.execute.return_value = make_result(scalars_all=proteins)
    result = await umap_service.compute_protein_umap(mock_db)
    assert "error" in result
    assert result["count"] == 4
    mock_db.commit.assert_not_awaited()


async def test_compute_protein_umap_success(mock_db):
    proteins = [_item([float(i)]) for i in range(10)]
    mock_db.execute.return_value = make_result(scalars_all=proteins)
    with patch.object(
        umap_service, "_compute_umap_projection",
        return_value=np.arange(20, dtype=float).reshape(10, 2),
    ):
        result = await umap_service.compute_protein_umap(mock_db)
    assert result["success"] == 10
    assert "computed_at" in result
    mock_db.commit.assert_awaited_once()
    assert proteins[1].umap_x == 2.0 and proteins[1].umap_y == 3.0


# =============================================================================
# Visualization helpers
# =============================================================================
def _is_data_uri_png(value: str) -> bool:
    return isinstance(value, str) and value.startswith("data:image/png;base64,")


def _assert_chart_payload(result):
    assert result["success"] is True
    assert _is_data_uri_png(result["image_base64"])
    assert result["image_url"].startswith("/uploads/charts/")


def _row(**kwargs):
    """A row object supporting attribute access (like SQLAlchemy Row)."""
    return SimpleNamespace(**kwargs)


# --- create_cell_count_histogram ---------------------------------------------
async def test_histogram_no_data(mock_db):
    mock_db.execute.return_value = make_result(fetchall=[])
    result = await viz.create_cell_count_histogram(user_id=1, db=mock_db)
    assert result == {"error": "No data found"}


async def test_histogram_success(mock_db):
    rows = [_row(id=i, cell_count=c) for i, c in enumerate([3, 5, 8, 2, 10])]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_cell_count_histogram(
        user_id=1, db=mock_db, experiment_id=5, title="My Hist"
    )
    _assert_chart_payload(result)
    stats = result["statistics"]
    assert stats["count"] == 5
    assert stats["min"] == 2 and stats["max"] == 10


# --- create_experiment_comparison_bar ----------------------------------------
async def test_bar_no_data(mock_db):
    mock_db.execute.return_value = make_result(fetchall=[])
    result = await viz.create_experiment_comparison_bar(user_id=1, db=mock_db)
    assert result == {"error": "No experiments found"}


async def test_bar_success_cell_count(mock_db):
    rows = [
        _row(id=1, name="Experiment Alpha", image_count=4, cell_count=120),
        _row(id=2, name="Experiment Beta", image_count=2, cell_count=55),
    ]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_experiment_comparison_bar(
        user_id=1, db=mock_db, experiment_ids=[1, 2], metric="cell_count"
    )
    _assert_chart_payload(result)
    assert result["data"][0]["cell_count"] == 120


async def test_bar_success_many_experiments_rotates_labels(mock_db):
    # > 5 experiments triggers the label-rotation branch.
    rows = [
        _row(id=i, name=f"VeryLongExperimentName_{i}", image_count=i, cell_count=i * 10)
        for i in range(7)
    ]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_experiment_comparison_bar(
        user_id=1, db=mock_db, metric="image_count", title="Img counts"
    )
    _assert_chart_payload(result)
    assert len(result["data"]) == 7


# --- create_cell_area_scatter -------------------------------------------------
async def test_scatter_no_data(mock_db):
    mock_db.execute.return_value = make_result(fetchall=[])
    result = await viz.create_cell_area_scatter(user_id=1, db=mock_db)
    assert result == {"error": "No cell data found"}


async def test_scatter_success(mock_db):
    rows = [
        _row(bbox_w=10.0, bbox_h=20.0, detection_confidence=0.9, experiment_name="A"),
        _row(bbox_w=15.0, bbox_h=25.0, detection_confidence=None, experiment_name="A"),
        # zero width filtered out of the width/height/area lists
        _row(bbox_w=0, bbox_h=5.0, detection_confidence=0.5, experiment_name="A"),
    ]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_cell_area_scatter(
        user_id=1, db=mock_db, experiment_id=2, title="Areas"
    )
    _assert_chart_payload(result)
    # only 2 valid rows counted (third has bbox_w=0)
    assert result["statistics"]["count"] == 2


# --- create_ranking_heatmap ---------------------------------------------------
async def test_heatmap_no_data(mock_db):
    mock_db.execute.return_value = make_result(fetchall=[])
    result = await viz.create_ranking_heatmap(user_id=1, db=mock_db)
    assert result == {"error": "No ranking data found"}


async def test_heatmap_single_experiment_needs_two(mock_db):
    rows = [
        _row(mu=25.0, sigma=8.0, experiment_name="OnlyOne"),
        _row(mu=30.0, sigma=7.0, experiment_name="OnlyOne"),
    ]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_ranking_heatmap(user_id=1, db=mock_db)
    assert result == {"error": "Need at least 2 experiments for comparison"}


async def test_heatmap_success_two_experiments(mock_db):
    rows = [
        _row(mu=25.0, sigma=8.0, experiment_name="Alpha"),
        _row(mu=30.0, sigma=7.0, experiment_name="Alpha"),
        _row(mu=20.0, sigma=6.0, experiment_name="Beta"),
        _row(mu=22.0, sigma=5.0, experiment_name="Beta"),
    ]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_ranking_heatmap(
        user_id=1, db=mock_db, experiment_id=3, title="Ratings"
    )
    _assert_chart_payload(result)
    assert set(result["experiments"]) == {"Alpha", "Beta"}
    assert result["data_points"]["Alpha"] == 2


# --- create_custom_chart ------------------------------------------------------
async def test_custom_chart_no_data():
    assert await viz.create_custom_chart(data=[]) == {"error": "No data provided"}


async def test_custom_chart_bar_auto_columns():
    data = [{"x": "a", "y": 1}, {"x": "b", "y": 2}]
    result = await viz.create_custom_chart(data=data, chart_type="bar")
    _assert_chart_payload(result)


async def test_custom_chart_line_explicit_columns():
    data = [{"label": f"p{i}", "val": i} for i in range(8)]  # > 5 → rotate labels
    result = await viz.create_custom_chart(
        data=data, chart_type="line", x_column="label", y_column="val",
        title="Line",
    )
    _assert_chart_payload(result)


async def test_custom_chart_scatter():
    data = [{"a": i} for i in range(4)]  # single column → x_col == y_col
    result = await viz.create_custom_chart(data=data, chart_type="scatter")
    _assert_chart_payload(result)


# =============================================================================
# create_visualization dispatcher
# =============================================================================
async def test_create_visualization_unknown_type(mock_db):
    result = await viz.create_visualization(chart_type="pie", user_id=1, db=mock_db)
    assert "error" in result
    assert "available_types" in result


async def test_create_visualization_histogram(mock_db):
    rows = [_row(id=i, cell_count=i + 1) for i in range(5)]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_visualization(
        chart_type="cell_histogram", user_id=1, db=mock_db
    )
    _assert_chart_payload(result)


async def test_create_visualization_bar(mock_db):
    rows = [_row(id=1, name="A", image_count=2, cell_count=10)]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_visualization(
        chart_type="comparison", user_id=1, db=mock_db, metric="cell_count"
    )
    _assert_chart_payload(result)


async def test_create_visualization_scatter(mock_db):
    rows = [_row(bbox_w=5.0, bbox_h=6.0, detection_confidence=0.7, experiment_name="A")]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_visualization(
        chart_type="cell_scatter", user_id=1, db=mock_db
    )
    _assert_chart_payload(result)


async def test_create_visualization_heatmap(mock_db):
    rows = [
        _row(mu=25.0, sigma=8.0, experiment_name="A"),
        _row(mu=20.0, sigma=6.0, experiment_name="B"),
    ]
    mock_db.execute.return_value = make_result(fetchall=rows)
    result = await viz.create_visualization(
        chart_type="ranking", user_id=1, db=mock_db
    )
    _assert_chart_payload(result)


async def test_create_visualization_custom(mock_db):
    result = await viz.create_visualization(
        chart_type="custom", user_id=1, db=mock_db,
        data=[{"x": "a", "y": 1}, {"x": "b", "y": 2}],
    )
    _assert_chart_payload(result)


async def test_create_visualization_custom_without_data(mock_db):
    result = await viz.create_visualization(chart_type="custom", user_id=1, db=mock_db)
    assert result == {"error": "data required for custom chart type"}


async def test_create_visualization_exception_path(mock_db):
    # Force the underlying handler to raise → caught and returned as error dict.
    mock_db.execute.side_effect = RuntimeError("db exploded")
    result = await viz.create_visualization(chart_type="histogram", user_id=1, db=mock_db)
    assert result == {"error": "db exploded"}
