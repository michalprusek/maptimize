"""In-process unit tests for routers/metrics.py and routers/embeddings.py.

Handlers are called DIRECTLY with mocked dependencies (current_user, db,
background_tasks) as kwargs — no live server, DB, or ML libs. The DB is the
``mock_db`` AsyncMock from conftest; per-test query results are driven by
``make_result`` (single result) or ``db.execute.side_effect`` (ordered
multi-query endpoints).

``get_user_group_id`` is imported into each router's namespace, so it is
patched there (``routers.metrics.get_user_group_id`` /
``routers.embeddings.get_user_group_id``). ML/UMAP/feature-extraction helpers
are patched at the boundary where the router imports them.
"""
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from routers import metrics as m
from routers import embeddings as e
from tests.unit.conftest import make_result


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def user(uid=1, name="Alice"):
    return SimpleNamespace(
        id=uid,
        name=name,
        email=f"u{uid}@b.cz",
        role=SimpleNamespace(value="researcher"),
    )


@pytest.fixture
def no_group():
    """Patch get_user_group_id in BOTH routers to return None (no group)."""
    with patch.object(m, "get_user_group_id", new=AsyncMock(return_value=None)), \
         patch.object(e, "get_user_group_id", new=AsyncMock(return_value=None)):
        yield


@pytest.fixture
def with_group():
    """Patch get_user_group_id in BOTH routers to return group id 7."""
    with patch.object(m, "get_user_group_id", new=AsyncMock(return_value=7)), \
         patch.object(e, "get_user_group_id", new=AsyncMock(return_value=7)):
        yield


def metric_obj(metric_id=1, user_id=1, group_id=None, creator_name="Alice"):
    return SimpleNamespace(
        id=metric_id,
        user_id=user_id,
        group_id=group_id,
        name="Tubeness",
        description="desc",
        created_at=NOW,
        updated_at=NOW,
        user=SimpleNamespace(name=creator_name) if creator_name is not None else None,
    )


def metric_image(img_id=10, metric_id=1, cell_crop_id=None, file_path=None,
                 original_filename="a.png", ratings=None, cell_crop=None):
    return SimpleNamespace(
        id=img_id,
        metric_id=metric_id,
        cell_crop_id=cell_crop_id,
        file_path=file_path,
        original_filename=original_filename,
        image_url=None,
        created_at=NOW,
        ratings=ratings if ratings is not None else [],
        cell_crop=cell_crop,
    )


def rating_obj(mu=25.0, sigma=8.0, comparison_count=0, user_id=1, excluded=False,
               ordinal_score=1.0, metric_image=None, metric_image_id=None):
    return SimpleNamespace(
        mu=mu, sigma=sigma, comparison_count=comparison_count, user_id=user_id,
        excluded=excluded, ordinal_score=ordinal_score, metric_image=metric_image,
        metric_image_id=metric_image_id,
    )


def comparison_obj(cid=100, metric_id=1, image_a_id=10, image_b_id=11,
                   winner_id=10, undone=False,
                   prev_winner_mu=None, prev_winner_sigma=None,
                   prev_loser_mu=None, prev_loser_sigma=None):
    return SimpleNamespace(
        id=cid, metric_id=metric_id, image_a_id=image_a_id, image_b_id=image_b_id,
        winner_id=winner_id, undone=undone, created_at=NOW, response_time_ms=100,
        prev_winner_mu=prev_winner_mu, prev_winner_sigma=prev_winner_sigma,
        prev_loser_mu=prev_loser_mu, prev_loser_sigma=prev_loser_sigma,
    )


# =============================================================================
# metrics.py — helper: get_metric_for_user
# =============================================================================

async def test_get_metric_for_user_found(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.return_value = make_result(scalar=metric)
    out = await m.get_metric_for_user(mock_db, 1, 1)
    assert out is metric


async def test_get_metric_for_user_with_group_branch(mock_db, with_group):
    # group_id not None -> appends the group condition (covers line 58)
    metric = metric_obj(group_id=7)
    mock_db.execute.return_value = make_result(scalar=metric)
    out = await m.get_metric_for_user(mock_db, 1, 1)
    assert out is metric


async def test_get_metric_for_user_not_found(mock_db, no_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await m.get_metric_for_user(mock_db, 999, 1)
    assert ei.value.status_code == 404


# =============================================================================
# metrics.py — helper: get_or_create_metric_rating (lines 148-169)
# =============================================================================

async def test_get_or_create_rating_existing(mock_db):
    existing = rating_obj()
    mock_db.execute.return_value = make_result(scalar=existing)
    out = await m.get_or_create_metric_rating(mock_db, 1, 10, user_id=1)
    assert out is existing
    mock_db.add.assert_not_called()


async def test_get_or_create_rating_creates_new(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await m.get_or_create_metric_rating(mock_db, 1, 10, user_id=1)
    assert out.metric_id == 1
    assert out.metric_image_id == 10
    mock_db.add.assert_called_once()
    mock_db.flush.assert_awaited_once()


async def test_get_or_create_rating_no_user_filter(mock_db):
    # user_id=None path (skips the user_id where-clause)
    mock_db.execute.return_value = make_result(scalar=None)
    out = await m.get_or_create_metric_rating(mock_db, 1, 10, user_id=None)
    assert out.user_id is None


# =============================================================================
# metrics.py — helper: get_metric_counts (per-user exclusion branch)
# =============================================================================

async def test_get_metric_counts_with_user(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=5),   # image count
        make_result(scalar=3),   # comparison count
    ]
    images, comps = await m.get_metric_counts(mock_db, 1, user_id=1)
    assert (images, comps) == (5, 3)


async def test_get_metric_counts_without_user(mock_db):
    # user_id=None -> skips notin_/user where clauses; None scalars -> 0
    mock_db.execute.side_effect = [
        make_result(scalar=None),
        make_result(scalar=None),
    ]
    images, comps = await m.get_metric_counts(mock_db, 1, user_id=None)
    assert (images, comps) == (0, 0)


# =============================================================================
# metrics.py — list_metrics / create / get / update / delete
# =============================================================================

async def test_list_metrics(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalars_all=[metric]),  # metrics list
        make_result(scalar=2),               # image count
        make_result(scalar=1),               # comparison count
    ]
    out = await m.list_metrics(current_user=user(), db=mock_db)
    assert out.total == 1
    assert out.items[0].creator_name == "Alice"
    assert out.items[0].image_count == 2


async def test_list_metrics_with_group_branch(mock_db, with_group):
    # group_id not None -> appends group condition (covers list_metrics line 184)
    metric = metric_obj(group_id=7)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[metric]),
        make_result(scalar=1),
        make_result(scalar=1),
    ]
    out = await m.list_metrics(current_user=user(), db=mock_db)
    assert out.total == 1


async def test_list_metrics_creator_none(mock_db, no_group):
    metric = metric_obj(creator_name=None)  # metric.user is None
    mock_db.execute.side_effect = [
        make_result(scalars_all=[metric]),
        make_result(scalar=0),
        make_result(scalar=0),
    ]
    out = await m.list_metrics(current_user=user(), db=mock_db)
    assert out.items[0].creator_name is None


async def test_create_metric(mock_db, with_group):
    captured = {}

    def _add(obj):
        captured["m"] = obj

    async def _refresh(obj):
        obj.id = 42
        obj.created_at = NOW
        obj.updated_at = NOW

    mock_db.add.side_effect = _add
    mock_db.refresh.side_effect = _refresh
    out = await m.create_metric(
        m.MetricCreate(name="X", description="d"), current_user=user(), db=mock_db
    )
    assert out.id == 42
    assert captured["m"].group_id == 7
    assert out.creator_name == "Alice"


async def test_get_metric(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),  # get_metric_for_user
        make_result(scalar=4),        # image count
        make_result(scalar=2),        # comparison count
    ]
    out = await m.get_metric(1, current_user=user(), db=mock_db)
    assert out.id == 1
    assert out.image_count == 4


async def test_update_metric_owner(mock_db, no_group):
    metric = metric_obj(user_id=1)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),  # get_metric_for_user
        make_result(scalar=0),        # image count
        make_result(scalar=0),        # comparison count
    ]
    out = await m.update_metric(
        1, m.MetricUpdate(name="New", description="newdesc"),
        current_user=user(), db=mock_db,
    )
    assert out.name == "New"
    assert metric.description == "newdesc"


async def test_update_metric_not_owner_forbidden(mock_db, no_group):
    metric = metric_obj(user_id=2)  # owned by someone else
    mock_db.execute.return_value = make_result(scalar=metric)
    with pytest.raises(HTTPException) as ei:
        await m.update_metric(
            1, m.MetricUpdate(name="New"), current_user=user(uid=1), db=mock_db
        )
    assert ei.value.status_code == 403


async def test_update_metric_no_changes(mock_db, no_group):
    # data.name and data.description both None -> skip both update branches
    metric = metric_obj(user_id=1)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalar=0),
        make_result(scalar=0),
    ]
    out = await m.update_metric(
        1, m.MetricUpdate(), current_user=user(), db=mock_db
    )
    assert out.name == "Tubeness"  # unchanged


async def test_delete_metric_owner(mock_db, no_group):
    metric = metric_obj(user_id=1)
    img_no_file = metric_image(file_path=None)
    img_missing = metric_image(file_path="/nope/x.png")
    img_exists = metric_image(file_path="/exists/x.png")
    imgs = [img_no_file, img_missing, img_exists]
    # delete_metric iterates `img_result.scalars()` directly -> make it iterable
    img_result = make_result(scalars_all=imgs)
    img_result.scalars.return_value = iter(imgs)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),  # get_metric_for_user
        img_result,                   # images to delete
    ]
    with patch.object(m.os.path, "exists", side_effect=lambda p: p == "/exists/x.png"), \
         patch.object(m.os, "remove") as rm:
        await m.delete_metric(1, current_user=user(), db=mock_db)
    rm.assert_called_once_with("/exists/x.png")
    mock_db.delete.assert_awaited_once_with(metric)


async def test_delete_metric_not_owner_forbidden(mock_db, no_group):
    metric = metric_obj(user_id=2)
    mock_db.execute.return_value = make_result(scalar=metric)
    with pytest.raises(HTTPException) as ei:
        await m.delete_metric(1, current_user=user(uid=1), db=mock_db)
    assert ei.value.status_code == 403


# =============================================================================
# metrics.py — list_metric_images (rating selection + protein info branches)
# =============================================================================

async def test_list_metric_images(mock_db, no_group):
    metric = metric_obj()
    protein = SimpleNamespace(name="PRC1", color="#ff0000")
    crop = SimpleNamespace(map_protein=protein)
    my_rating = rating_obj(user_id=1, excluded=False)
    img_with_protein = metric_image(
        img_id=10, cell_crop_id=5, cell_crop=crop, ratings=[my_rating]
    )
    # second image: no cell_crop, no matching rating (excluded one filtered out)
    img_no_protein = metric_image(
        img_id=11, cell_crop=None,
        ratings=[rating_obj(user_id=1, excluded=True)],
    )
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalars_all=[img_with_protein, img_no_protein]),
    ]
    out = await m.list_metric_images(1, current_user=user(), db=mock_db)
    assert len(out) == 2
    assert out[0].map_protein_name == "PRC1"
    assert out[0].mu == 25.0
    assert out[1].map_protein_name is None
    assert out[1].mu is None  # no non-excluded rating


# =============================================================================
# metrics.py — import_crops_to_metric
# =============================================================================

async def test_import_crops_empty_experiment_ids(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.return_value = make_result(scalar=metric)
    with pytest.raises(HTTPException) as ei:
        await m.import_crops_to_metric(
            1, m.ImportCropsRequest(experiment_ids=[]),
            current_user=user(), db=mock_db,
        )
    assert ei.value.status_code == 400


async def test_import_crops_invalid_experiment(mock_db, with_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),          # get_metric_for_user
        make_result(scalars_all=[1]),        # valid_ids = {1}; requested {1,2}
    ]
    with pytest.raises(HTTPException) as ei:
        await m.import_crops_to_metric(
            1, m.ImportCropsRequest(experiment_ids=[1, 2]),
            current_user=user(), db=mock_db,
        )
    assert ei.value.status_code == 404


async def test_import_crops_success_with_skip(mock_db, no_group):
    metric = metric_obj()
    crop_existing = SimpleNamespace(id=100)  # already imported -> skipped
    crop_new = SimpleNamespace(id=200)       # new -> imported
    mock_db.execute.side_effect = [
        make_result(scalar=metric),                 # get_metric_for_user
        make_result(scalars_all=[1]),               # valid experiment ids
        make_result(scalars_all=[100]),             # existing crop ids
        make_result(scalars_all=[crop_existing, crop_new]),  # crops from experiments
    ]
    out = await m.import_crops_to_metric(
        1, m.ImportCropsRequest(experiment_ids=[1]),
        current_user=user(), db=mock_db,
    )
    assert out.imported_count == 1
    assert out.skipped_count == 1


# =============================================================================
# metrics.py — list_experiments_for_import
# =============================================================================

async def test_list_experiments_for_import(mock_db, with_group):
    metric = metric_obj()
    exp_row = SimpleNamespace(id=1, name="Exp", image_count=3, crop_count=10)
    imported_row = SimpleNamespace(experiment_id=1, imported_count=4)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),               # get_metric_for_user
        make_result(fetchall=[exp_row]),          # experiments .all()
        make_result(fetchall=[imported_row]),     # imported counts .all()
    ]
    out = await m.list_experiments_for_import(1, current_user=user(), db=mock_db)
    assert out[0].already_imported == 4
    assert out[0].crop_count == 10


async def test_list_experiments_for_import_null_counts(mock_db, no_group):
    metric = metric_obj()
    exp_row = SimpleNamespace(id=2, name="Exp2", image_count=None, crop_count=None)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(fetchall=[exp_row]),
        make_result(fetchall=[]),  # nothing imported
    ]
    out = await m.list_experiments_for_import(1, current_user=user(), db=mock_db)
    assert out[0].image_count == 0
    assert out[0].crop_count == 0
    assert out[0].already_imported == 0


# =============================================================================
# metrics.py — remove_metric_image (soft-exclude)
# =============================================================================

async def test_remove_metric_image_not_found(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),  # get_metric_for_user
        make_result(scalar=None),     # image lookup -> not found
    ]
    with pytest.raises(HTTPException) as ei:
        await m.remove_metric_image(1, 99, current_user=user(), db=mock_db)
    assert ei.value.status_code == 404


async def test_remove_metric_image_soft_excludes(mock_db, no_group):
    metric = metric_obj()
    image = metric_image(img_id=10)
    rating = rating_obj(excluded=False)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),  # get_metric_for_user
        make_result(scalar=image),    # image lookup
        make_result(scalar=rating),   # get_or_create_metric_rating -> existing
    ]
    await m.remove_metric_image(1, 10, current_user=user(), db=mock_db)
    assert rating.excluded is True
    mock_db.commit.assert_awaited()


# =============================================================================
# metrics.py — get_metric_pair
# =============================================================================

async def test_get_metric_pair_not_enough_images(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),           # get_metric_for_user
        make_result(scalars_all=[metric_image(img_id=10)]),  # only 1 image
    ]
    with pytest.raises(HTTPException) as ei:
        await m.get_metric_pair(1, current_user=user(), db=mock_db)
    assert ei.value.status_code == 400


async def test_get_metric_pair_exploration_phase(mock_db, no_group):
    metric = metric_obj()
    crop = SimpleNamespace()  # truthy cell_crop -> crop image url branch
    img_a = metric_image(img_id=10, cell_crop_id=5, cell_crop=crop)
    img_b = metric_image(img_id=11, cell_crop=None)  # file url branch
    mock_db.execute.side_effect = [
        make_result(scalar=metric),                       # get_metric_for_user
        make_result(scalars_all=[img_a, img_b]),          # images
        make_result(scalar=0),                            # total_comparisons (< exploration_pairs)
        make_result(scalars_all=[]),                      # recent comparisons
        make_result(scalars_all=[]),                      # existing ratings batch (none -> both created)
    ]
    with patch.object(m.random, "choice", side_effect=lambda seq: seq[0]), \
         patch.object(m.random, "random", return_value=0.1):  # no swap
        out = await m.get_metric_pair(1, current_user=user(), db=mock_db)
    assert {out.image_a.id, out.image_b.id} == {10, 11}
    assert out.total_comparisons == 0
    # one image must have crop-based URL, the other file-based
    urls = {out.image_a.image_url, out.image_b.image_url}
    assert "/api/images/crops/5/image" in urls


async def test_get_metric_pair_exploration_recent_fallback(mock_db, no_group):
    # All pairs recent -> available_pairs empty -> fallback to full set
    metric = metric_obj()
    img_a = metric_image(img_id=10, cell_crop=SimpleNamespace())
    img_b = metric_image(img_id=11, cell_crop=SimpleNamespace())
    recent = comparison_obj(image_a_id=10, image_b_id=11)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalars_all=[img_a, img_b]),
        make_result(scalar=0),
        make_result(scalars_all=[recent]),  # recent pair (10,11)
        make_result(scalars_all=[            # existing ratings batch
            rating_obj(metric_image_id=10),
            rating_obj(metric_image_id=11),
        ]),
    ]
    with patch.object(m.random, "choice", side_effect=lambda seq: seq[0]), \
         patch.object(m.random, "random", return_value=0.9):  # swap
        out = await m.get_metric_pair(1, current_user=user(), db=mock_db)
    assert {out.image_a.id, out.image_b.id} == {10, 11}


async def test_get_metric_pair_skip_excludes_current_pair(mock_db, no_group):
    # Skip passes exclude_a/exclude_b -> that pair must NOT be returned again.
    metric = metric_obj()
    imgs = [metric_image(img_id=i, cell_crop=SimpleNamespace()) for i in (10, 11, 12)]
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalars_all=imgs),
        make_result(scalar=0),            # exploration phase
        make_result(scalars_all=[]),      # no recorded recent comparisons
        make_result(scalars_all=[]),      # existing ratings batch (create all)
    ]
    with patch.object(m.random, "choice", side_effect=lambda seq: seq[0]), \
         patch.object(m.random, "random", return_value=0.1):  # no swap
        out = await m.get_metric_pair(
            1, exclude_a=10, exclude_b=11, current_user=user(), db=mock_db
        )
    # The excluded (10,11) pair is skipped; a fresh pair is returned instead.
    assert {out.image_a.id, out.image_b.id} != {10, 11}


async def test_get_metric_pair_exploitation_phase(mock_db, no_group):
    metric = metric_obj()
    imgs = [metric_image(img_id=i, cell_crop=SimpleNamespace()) for i in (10, 11, 12)]
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalars_all=imgs),
        make_result(scalar=999),       # total_comparisons >> exploration_pairs
        make_result(scalars_all=[]),   # recent
        make_result(scalars_all=[      # existing ratings batch
            rating_obj(mu=25, sigma=9, metric_image_id=10),
            rating_obj(mu=20, sigma=8, metric_image_id=11),
            rating_obj(mu=15, sigma=2, metric_image_id=12),
        ]),
    ]
    with patch.object(m.random, "random", return_value=0.1):
        out = await m.get_metric_pair(1, current_user=user(), db=mock_db)
    assert out.image_a.id != out.image_b.id


async def test_get_metric_pair_exploitation_all_recent_random_fallback(mock_db, no_group):
    # exploitation candidates all recent -> best_pair stays None -> random.sample
    metric = metric_obj()
    imgs = [metric_image(img_id=i, cell_crop=SimpleNamespace()) for i in (10, 11)]
    recent = comparison_obj(image_a_id=10, image_b_id=11)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalars_all=imgs),
        make_result(scalar=999),
        make_result(scalars_all=[recent]),
        make_result(scalars_all=[      # existing ratings batch
            rating_obj(mu=25, sigma=9, metric_image_id=10),
            rating_obj(mu=25, sigma=9, metric_image_id=11),
        ]),
    ]
    with patch.object(m.random, "sample", side_effect=lambda seq, n: list(seq)[:n]), \
         patch.object(m.random, "random", return_value=0.1):
        out = await m.get_metric_pair(1, current_user=user(), db=mock_db)
    assert {out.image_a.id, out.image_b.id} == {10, 11}


# =============================================================================
# metrics.py — submit_metric_comparison
# =============================================================================

async def test_submit_comparison_invalid_winner(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.return_value = make_result(scalar=metric)
    # bypass schema validator by constructing via model_construct
    payload = m.MetricComparisonCreate.model_construct(
        image_a_id=10, image_b_id=11, winner_id=99, response_time_ms=None
    )
    with pytest.raises(HTTPException) as ei:
        await m.submit_metric_comparison(1, payload, current_user=user(), db=mock_db)
    assert ei.value.status_code == 400


async def test_submit_comparison_image_not_in_metric(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),   # get_metric_for_user
        make_result(scalar=None),      # image_a not found in metric
    ]
    payload = m.MetricComparisonCreate(image_a_id=10, image_b_id=11, winner_id=10)
    with pytest.raises(HTTPException) as ei:
        await m.submit_metric_comparison(1, payload, current_user=user(), db=mock_db)
    assert ei.value.status_code == 400


async def test_submit_comparison_success(mock_db, no_group):
    metric = metric_obj()
    winner_rating = rating_obj(mu=25, sigma=8, comparison_count=0)
    loser_rating = rating_obj(mu=20, sigma=8, comparison_count=0)
    img_a = metric_image(img_id=10)
    img_b = metric_image(img_id=11)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),    # get_metric_for_user
        make_result(scalar=img_a),      # verify image_a
        make_result(scalar=img_b),      # verify image_b
        make_result(scalar=winner_rating),  # winner rating (winner_id=10)
        make_result(scalar=loser_rating),   # loser rating (11)
    ]

    async def _refresh(obj):
        obj.id = 100
        obj.created_at = NOW

    mock_db.refresh.side_effect = _refresh
    payload = m.MetricComparisonCreate(image_a_id=10, image_b_id=11, winner_id=10)
    with patch.object(m, "update_ratings", return_value=((26.0, 7.0), (19.0, 7.0))) as ur:
        out = await m.submit_metric_comparison(1, payload, current_user=user(), db=mock_db)
    ur.assert_called_once()
    assert winner_rating.mu == 26.0
    assert winner_rating.comparison_count == 1
    assert loser_rating.mu == 19.0
    assert out.id == 100
    # previous values are stored on the comparison for exact undo
    added = mock_db.add.call_args[0][0]
    assert added.prev_winner_mu == 25.0
    assert added.prev_winner_sigma == 8.0
    assert added.prev_loser_mu == 20.0
    assert added.prev_loser_sigma == 8.0


async def test_submit_comparison_winner_is_b(mock_db, no_group):
    # winner_id == image_b_id -> loser_id = image_a_id branch
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalar=metric_image(img_id=10)),
        make_result(scalar=metric_image(img_id=11)),
        make_result(scalar=rating_obj()),  # winner (11)
        make_result(scalar=rating_obj()),  # loser (10)
    ]

    async def _refresh(obj):
        obj.id = 101
        obj.created_at = NOW

    mock_db.refresh.side_effect = _refresh
    payload = m.MetricComparisonCreate(image_a_id=10, image_b_id=11, winner_id=11)
    with patch.object(m, "update_ratings", return_value=((26.0, 7.0), (19.0, 7.0))):
        out = await m.submit_metric_comparison(1, payload, current_user=user(), db=mock_db)
    assert out.winner_id == 11


# =============================================================================
# metrics.py — undo_metric_comparison
# =============================================================================

async def test_undo_no_comparison(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),  # get_metric_for_user
        make_result(scalar=None),     # no comparison to undo
    ]
    with pytest.raises(HTTPException) as ei:
        await m.undo_metric_comparison(1, current_user=user(), db=mock_db)
    assert ei.value.status_code == 404


async def test_undo_success(mock_db, no_group):
    metric = metric_obj()
    comparison = comparison_obj(image_a_id=10, image_b_id=11)
    rating_a = rating_obj(comparison_count=2)
    rating_b = rating_obj(comparison_count=0)  # count==0 -> not decremented
    img_a = metric_image(img_id=10)
    img_b = metric_image(img_id=11)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),        # get_metric_for_user
        make_result(scalar=comparison),     # last comparison
        make_result(scalar=rating_a),       # rating for image_a
        make_result(scalar=rating_b),       # rating for image_b
        make_result(scalars_all=[img_a, img_b]),  # images for pair re-display
        make_result(scalar=3),              # remaining comparison count
    ]
    out = await m.undo_metric_comparison(1, current_user=user(), db=mock_db)
    assert comparison.undone is True
    assert rating_a.comparison_count == 1   # decremented
    assert rating_b.comparison_count == 0   # unchanged (was 0)
    assert out.id == comparison.id
    # legacy comparison (prev_* is None) -> mu/sigma untouched
    assert rating_a.mu == 25.0
    # the undone pair is returned for re-display
    assert out.pair is not None
    assert out.pair.image_a.id == 10
    assert out.pair.image_b.id == 11
    assert out.pair.total_comparisons == 3
    assert out.pair.comparison_number == 4


async def test_undo_restores_prev_ratings(mock_db, no_group):
    # winner is image_b -> exercises the loser=image_a branch
    metric = metric_obj()
    comparison = comparison_obj(
        image_a_id=10, image_b_id=11, winner_id=11,
        prev_winner_mu=24.0, prev_winner_sigma=8.1,
        prev_loser_mu=26.0, prev_loser_sigma=8.2,
    )
    rating_a = rating_obj(mu=27.0, sigma=7.0, comparison_count=1)  # loser
    rating_b = rating_obj(mu=23.0, sigma=7.0, comparison_count=1)  # winner
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalar=comparison),
        make_result(scalar=rating_a),
        make_result(scalar=rating_b),
        make_result(scalars_all=[metric_image(img_id=10), metric_image(img_id=11)]),
        make_result(scalar=0),
    ]
    out = await m.undo_metric_comparison(1, current_user=user(), db=mock_db)
    assert rating_b.mu == 24.0 and rating_b.sigma == 8.1   # winner restored
    assert rating_a.mu == 26.0 and rating_a.sigma == 8.2   # loser restored
    assert rating_a.comparison_count == 0
    assert rating_b.comparison_count == 0
    assert out.pair.comparison_number == 1


async def test_undo_rating_none(mock_db, no_group):
    # rating lookup returns None for one image -> skip decrement branch
    metric = metric_obj()
    comparison = comparison_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalar=comparison),
        make_result(scalar=None),  # rating for image_a -> None
        make_result(scalar=None),  # rating for image_b -> None
        make_result(scalars_all=[metric_image(img_id=10), metric_image(img_id=11)]),
        make_result(scalar=0),
    ]
    out = await m.undo_metric_comparison(1, current_user=user(), db=mock_db)
    assert comparison.undone is True
    assert out.id == comparison.id


async def test_undo_pair_none_when_image_deleted(mock_db, no_group):
    # one of the pair's images no longer exists -> pair is None
    metric = metric_obj()
    comparison = comparison_obj(image_a_id=10, image_b_id=11)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalar=comparison),
        make_result(scalar=rating_obj(comparison_count=1)),
        make_result(scalar=rating_obj(comparison_count=1)),
        make_result(scalars_all=[metric_image(img_id=10)]),  # image_b missing
        make_result(scalar=0),
    ]
    out = await m.undo_metric_comparison(1, current_user=user(), db=mock_db)
    assert comparison.undone is True
    assert out.pair is None


# =============================================================================
# metrics.py — get_metric_leaderboard
# =============================================================================

async def test_leaderboard(mock_db, no_group):
    metric = metric_obj()
    img_crop = metric_image(img_id=10, cell_crop_id=5, cell_crop=SimpleNamespace())
    img_file = metric_image(img_id=11, cell_crop=None)
    r1 = rating_obj(mu=30, sigma=2, comparison_count=5, metric_image=img_crop)
    r2 = rating_obj(mu=20, sigma=3, comparison_count=4, metric_image=img_file)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),          # get_metric_for_user
        make_result(scalar=2),                # total count
        make_result(scalars_all=[r1, r2]),    # ratings page
    ]
    out = await m.get_metric_leaderboard(1, page=1, per_page=500,
                                         current_user=user(), db=mock_db)
    assert out.total == 2
    assert out.items[0].rank == 1
    assert out.items[0].image_url == "/api/images/crops/5/image"
    assert out.items[1].image_url == "/api/metrics/1/images/11/file"


async def test_leaderboard_pagination_offset(mock_db, no_group):
    metric = metric_obj()
    img = metric_image(img_id=10, cell_crop=SimpleNamespace(), cell_crop_id=5)
    r1 = rating_obj(metric_image=img)
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalar=10),
        make_result(scalars_all=[r1]),
    ]
    out = await m.get_metric_leaderboard(2, page=2, per_page=5,
                                         current_user=user(), db=mock_db)
    # rank = (page-1)*per_page + i + 1 = 5 + 0 + 1
    assert out.items[0].rank == 6
    assert out.page == 2


# =============================================================================
# metrics.py — get_metric_progress
# =============================================================================

async def test_progress_exploration_phase(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),  # get_metric_for_user
        make_result(scalar=3),        # total_comparisons (< exploration_pairs=50)
        make_result(scalar=20),       # image count
        make_result(scalar=6.0),      # avg sigma
    ]
    out = await m.get_metric_progress(1, current_user=user(), db=mock_db)
    assert out.phase == "exploration"
    assert out.total_comparisons == 3
    assert out.image_count == 20


async def test_progress_exploitation_and_default_sigma(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalar=100),     # > exploration_pairs -> exploitation
        make_result(scalar=0),       # image count
        make_result(scalar=None),    # avg sigma None -> default initial_sigma
    ]
    out = await m.get_metric_progress(1, current_user=user(), db=mock_db)
    assert out.phase == "exploitation"
    assert out.average_sigma == round(m.settings.initial_sigma, 3)


# =============================================================================
# metrics.py — get_metric_image_file
# =============================================================================

async def test_image_file_image_not_found(mock_db, no_group):
    metric = metric_obj()
    mock_db.execute.side_effect = [
        make_result(scalar=metric),  # get_metric_for_user
        make_result(scalar=None),     # image not found
    ]
    with pytest.raises(HTTPException) as ei:
        await m.get_metric_image_file(1, 99, current_user=user(), db=mock_db)
    assert ei.value.status_code == 404
    assert ei.value.detail == "Image not found"


async def test_image_file_missing_on_disk(mock_db, no_group):
    metric = metric_obj()
    image = metric_image(file_path=None)  # no file_path -> file not found
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalar=image),
    ]
    with pytest.raises(HTTPException) as ei:
        await m.get_metric_image_file(1, 10, current_user=user(), db=mock_db)
    assert ei.value.status_code == 404
    assert ei.value.detail == "Image file not found"


async def test_image_file_success(mock_db, no_group):
    metric = metric_obj()
    image = metric_image(file_path="/data/x.png")
    mock_db.execute.side_effect = [
        make_result(scalar=metric),
        make_result(scalar=image),
    ]
    with patch.object(m.os.path, "exists", return_value=True):
        resp = await m.get_metric_image_file(1, 10, current_user=user(), db=mock_db)
    # FileResponse object built with the path
    assert getattr(resp, "path", None) == "/data/x.png"


# =============================================================================
# embeddings.py — helpers and _verify_experiment_ownership
# =============================================================================

def test_experiment_owner_filter_no_group():
    # group_id None -> single condition; just ensure it builds without error
    clause = e._experiment_owner_filter(1, None)
    assert clause is not None


def test_experiment_owner_filter_with_group():
    clause = e._experiment_owner_filter(1, 7)
    assert clause is not None


async def test_verify_ownership_found(mock_db, no_group):
    mock_db.execute.return_value = make_result(scalar=SimpleNamespace(id=1))
    await e._verify_experiment_ownership(1, 1, mock_db)  # no raise


async def test_verify_ownership_not_found(mock_db, no_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await e._verify_experiment_ownership(99, 1, mock_db)
    assert ei.value.status_code == 404


# =============================================================================
# embeddings.py — get_umap_visualization dispatch + _get_cropped_umap
# =============================================================================

def crop_obj(cid=1, embedding=None, umap_x=None, umap_y=None, protein=None,
             bundleness=0.5, experiment_id=1, image_id=2):
    return SimpleNamespace(
        id=cid,
        embedding=embedding if embedding is not None else [0.1, 0.2, 0.3],
        umap_x=umap_x,
        umap_y=umap_y,
        map_protein=protein,
        bundleness_score=bundleness,
        image_id=image_id,
        image=SimpleNamespace(experiment_id=experiment_id),
    )


async def test_cropped_umap_too_few_crops(mock_db, no_group):
    # fewer than MIN_POINTS_FOR_UMAP -> 400
    crops = [crop_obj(cid=i) for i in range(2)]
    mock_db.execute.return_value = make_result(scalars_all=crops)
    with patch.object(e, "MIN_POINTS_FOR_UMAP", 10):
        with pytest.raises(HTTPException) as ei:
            await e.get_umap_visualization(
                umap_type=e.UmapType.CROPPED, experiment_id=None,
                n_neighbors=15, min_dist=0.1, current_user=user(), db=mock_db,
            )
    assert ei.value.status_code == 400


async def test_cropped_umap_no_precomputed_returns_empty(mock_db, no_group):
    crops = [crop_obj(cid=i, umap_x=None, umap_y=None) for i in range(5)]
    mock_db.execute.return_value = make_result(scalars_all=crops)
    with patch.object(e, "MIN_POINTS_FOR_UMAP", 3):
        out = await e.get_umap_visualization(
            umap_type=e.UmapType.CROPPED, experiment_id=None,
            n_neighbors=15, min_dist=0.1, current_user=user(), db=mock_db,
        )
    assert out.points == []
    assert out.total_crops == 5
    assert out.silhouette_score is None


async def test_cropped_umap_precomputed_with_experiment_filter(mock_db, no_group):
    protein = SimpleNamespace(name="PRC1", color="#abc")
    crops = [
        crop_obj(cid=1, umap_x=0.1, umap_y=0.2, protein=protein),
        crop_obj(cid=2, umap_x=0.3, umap_y=0.4, protein=None),  # default color branch
        crop_obj(cid=3, umap_x=0.5, umap_y=0.6, protein=protein),
    ]
    mock_db.execute.side_effect = [
        make_result(scalar=SimpleNamespace(id=9)),  # _verify_experiment_ownership
        make_result(scalars_all=crops),              # crops query
    ]
    with patch.object(e, "MIN_POINTS_FOR_UMAP", 3), \
         patch.object(e, "compute_silhouette", return_value=0.42) as sil:
        out = await e.get_umap_visualization(
            umap_type=e.UmapType.CROPPED, experiment_id=9,
            n_neighbors=15, min_dist=0.1, current_user=user(), db=mock_db,
        )
    sil.assert_called_once()
    assert out.silhouette_score == 0.42
    assert len(out.points) == 3
    assert out.points[0].protein_name == "PRC1"
    assert out.points[1].protein_color == "#888888"


# =============================================================================
# embeddings.py — _get_fov_umap (only reachable branches; success path hits a
# NameError bug at router line 250 — see report)
# =============================================================================

def image_obj(iid=1, embedding=None, umap_x=None, umap_y=None, protein=None,
              experiment_id=1, filename="f.png", umap_computed_at=None):
    return SimpleNamespace(
        id=iid,
        embedding=embedding if embedding is not None else [0.1, 0.2],
        umap_x=umap_x,
        umap_y=umap_y,
        map_protein=protein,
        experiment_id=experiment_id,
        original_filename=filename,
        umap_computed_at=umap_computed_at,
    )


async def test_fov_umap_too_few(mock_db, no_group):
    imgs = [image_obj(iid=i) for i in range(2)]
    mock_db.execute.return_value = make_result(scalars_all=imgs)
    with patch.object(e, "MIN_POINTS_FOR_UMAP", 10):
        with pytest.raises(HTTPException) as ei:
            await e.get_umap_visualization(
                umap_type=e.UmapType.FOV, experiment_id=None,
                n_neighbors=15, min_dist=0.1, current_user=user(), db=mock_db,
            )
    assert ei.value.status_code == 400


async def test_fov_umap_no_precomputed_returns_empty(mock_db, no_group):
    imgs = [image_obj(iid=i, umap_x=None, umap_y=None) for i in range(5)]
    mock_db.execute.return_value = make_result(scalars_all=imgs)
    with patch.object(e, "MIN_POINTS_FOR_UMAP", 3):
        out = await e.get_umap_visualization(
            umap_type=e.UmapType.FOV, experiment_id=None,
            n_neighbors=15, min_dist=0.1, current_user=user(), db=mock_db,
        )
    assert out.points == []
    assert out.total_images == 5


async def test_fov_umap_experiment_filter_empty(mock_db, no_group):
    # FOV with experiment_id -> _verify_experiment_ownership + where filter
    # (covers router lines 193-194); no pre-computed UMAP -> empty response.
    imgs = [image_obj(iid=i, umap_x=None, umap_y=None) for i in range(4)]
    mock_db.execute.side_effect = [
        make_result(scalar=SimpleNamespace(id=9)),  # _verify_experiment_ownership
        make_result(scalars_all=imgs),               # images query
    ]
    with patch.object(e, "MIN_POINTS_FOR_UMAP", 3):
        out = await e.get_umap_visualization(
            umap_type=e.UmapType.FOV, experiment_id=9,
            n_neighbors=15, min_dist=0.1, current_user=user(), db=mock_db,
        )
    assert out.points == []
    assert out.total_images == 4


async def test_fov_umap_precomputed_success(mock_db, no_group):
    # FOV success path: all images have pre-computed UMAP coords -> is_precomputed=True.
    imgs = [image_obj(iid=i, umap_x=0.1, umap_y=0.2) for i in range(3)]
    mock_db.execute.return_value = make_result(scalars_all=imgs)
    with patch.object(e, "MIN_POINTS_FOR_UMAP", 3), \
         patch.object(e, "compute_silhouette", return_value=0.1):
        out = await e.get_umap_visualization(
            umap_type=e.UmapType.FOV, experiment_id=None,
            n_neighbors=15, min_dist=0.1, current_user=user(), db=mock_db,
        )
    assert out.is_precomputed is True
    assert len(out.points) == 3
    assert out.silhouette_score == 0.1


# =============================================================================
# embeddings.py — _compute_umap_with_error_handling
# =============================================================================

def test_compute_umap_success():
    with patch.object(e, "compute_umap_online", return_value=("proj", "extra")):
        out = e._compute_umap_with_error_handling(
            __import__("numpy").zeros((3, 2)), [1, 2, 3], 15, 0.1
        )
    assert out == ("proj", "extra")


def test_compute_umap_value_error():
    with patch.object(e, "compute_umap_online", side_effect=ValueError("bad")):
        with pytest.raises(HTTPException) as ei:
            e._compute_umap_with_error_handling(None, [1], 15, 0.1)
    assert ei.value.status_code == 400


def test_compute_umap_memory_error():
    with patch.object(e, "compute_umap_online", side_effect=MemoryError()):
        with pytest.raises(HTTPException) as ei:
            e._compute_umap_with_error_handling(None, [1], 15, 0.1)
    assert ei.value.status_code == 413


def test_compute_umap_unexpected_error():
    with patch.object(e, "compute_umap_online", side_effect=RuntimeError("boom")):
        with pytest.raises(HTTPException) as ei:
            e._compute_umap_with_error_handling(None, [1], 15, 0.1)
    assert ei.value.status_code == 500


# =============================================================================
# embeddings.py — trigger_umap_recomputation + background task
# =============================================================================

async def test_trigger_umap_recompute_no_experiment(mock_db, no_group):
    bg = MagicMock()
    out = await e.trigger_umap_recomputation(
        umap_type=e.UmapType.CROPPED, experiment_id=None,
        background_tasks=bg, current_user=user(), db=mock_db,
    )
    bg.add_task.assert_called_once()
    assert e.UmapType.CROPPED.value in out["message"]


async def test_trigger_umap_recompute_with_experiment(mock_db, no_group):
    bg = MagicMock()
    mock_db.execute.return_value = make_result(scalar=SimpleNamespace(id=5))  # ownership ok
    out = await e.trigger_umap_recomputation(
        umap_type=e.UmapType.FOV, experiment_id=5,
        background_tasks=bg, current_user=user(), db=mock_db,
    )
    bg.add_task.assert_called_once()
    assert "fov" in out["message"]


async def test_trigger_umap_recompute_experiment_not_owned(mock_db, no_group):
    bg = MagicMock()
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await e.trigger_umap_recomputation(
            umap_type=e.UmapType.FOV, experiment_id=99,
            background_tasks=bg, current_user=user(), db=mock_db,
        )
    assert ei.value.status_code == 404
    bg.add_task.assert_not_called()


async def test_recompute_background_fov_success():
    db = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("database.get_db_context", return_value=ctx), \
         patch("services.umap_service.compute_fov_umap",
               new=AsyncMock(return_value={"updated": 5})) as cfu, \
         patch("services.umap_service.compute_crop_umap", new=AsyncMock()):
        await e._recompute_umap_background(e.UmapType.FOV, 1, None)
    cfu.assert_awaited_once()


async def test_recompute_background_crop_error_result():
    db = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("database.get_db_context", return_value=ctx), \
         patch("services.umap_service.compute_crop_umap",
               new=AsyncMock(return_value={"error": "nope"})) as ccu, \
         patch("services.umap_service.compute_fov_umap", new=AsyncMock()):
        await e._recompute_umap_background(e.UmapType.CROPPED, 1, 5)
    ccu.assert_awaited_once()


async def test_recompute_background_exception_swallowed():
    with patch("database.get_db_context", side_effect=RuntimeError("db down")):
        # Should not raise — exception is logged
        await e._recompute_umap_background(e.UmapType.FOV, 1, None)


# =============================================================================
# embeddings.py — get_embedding_status
# =============================================================================

async def test_embedding_status(mock_db, no_group):
    row = SimpleNamespace(total=10, with_emb=4)
    mock_db.execute.return_value = make_result(scalar=None)
    mock_db.execute.return_value.one.return_value = row
    out = await e.get_embedding_status(experiment_id=None, current_user=user(), db=mock_db)
    assert out.total == 10
    assert out.with_embeddings == 4
    assert out.without_embeddings == 6
    assert out.percentage == 40.0


async def test_embedding_status_zero_total_and_experiment_filter(mock_db, no_group):
    row = SimpleNamespace(total=0, with_emb=0)
    res = make_result()
    res.one.return_value = row
    mock_db.execute.return_value = res
    out = await e.get_embedding_status(experiment_id=5, current_user=user(), db=mock_db)
    assert out.total == 0
    assert out.percentage == 0


# =============================================================================
# embeddings.py — trigger_feature_extraction (+ background task)
# =============================================================================

async def test_trigger_feature_extraction_none_pending(mock_db, no_group):
    bg = MagicMock()
    mock_db.execute.side_effect = [
        make_result(scalar=SimpleNamespace(id=1)),  # ownership
        make_result(scalar=0),                       # pending_count = 0
    ]
    out = await e.trigger_feature_extraction(
        experiment_id=1, background_tasks=bg, current_user=user(), db=mock_db
    )
    assert out.pending == 0
    bg.add_task.assert_not_called()


async def test_trigger_feature_extraction_with_pending(mock_db, no_group):
    bg = MagicMock()
    mock_db.execute.side_effect = [
        make_result(scalar=SimpleNamespace(id=1)),  # ownership
        make_result(scalar=2),                       # pending_count
        make_result(fetchall=[(10,), (11,)]),        # crop ids
    ]
    out = await e.trigger_feature_extraction(
        experiment_id=1, background_tasks=bg, current_user=user(), db=mock_db
    )
    assert out.pending == 2
    bg.add_task.assert_called_once()
    args = bg.add_task.call_args[0]
    assert args[0] is e._extract_features_background
    assert args[1] == [10, 11]


async def test_extract_features_background_success():
    db = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("database.get_db_context", return_value=ctx), \
         patch("ml.features.extract_features_for_crops",
               new=AsyncMock(return_value={"success": 2, "failed": 0})) as ef:
        await e._extract_features_background([10, 11], 1)
    ef.assert_awaited_once()


async def test_extract_features_background_runtime_error():
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("database.get_db_context", return_value=ctx), \
         patch("ml.features.extract_features_for_crops",
               new=AsyncMock(side_effect=RuntimeError("model gone"))):
        await e._extract_features_background([10], 1)  # logged, no raise


async def test_extract_features_background_generic_exception():
    with patch("database.get_db_context", side_effect=ValueError("x")):
        await e._extract_features_background([10], 1)  # logged, no raise


# =============================================================================
# embeddings.py — trigger_fov_feature_extraction (+ background task)
# =============================================================================

async def test_trigger_fov_extraction_none_pending_no_experiment(mock_db, no_group):
    bg = MagicMock()
    mock_db.execute.return_value = make_result(scalar=0)  # pending_count = 0
    out = await e.trigger_fov_feature_extraction(
        experiment_id=None, background_tasks=bg, current_user=user(), db=mock_db
    )
    assert out.pending == 0
    bg.add_task.assert_not_called()


async def test_trigger_fov_extraction_with_pending_and_experiment(mock_db, with_group):
    bg = MagicMock()
    mock_db.execute.side_effect = [
        make_result(scalar=SimpleNamespace(id=1)),  # ownership verify
        make_result(scalar=3),                       # pending_count
        make_result(fetchall=[(1,), (2,), (3,)]),    # image ids
    ]
    out = await e.trigger_fov_feature_extraction(
        experiment_id=1, background_tasks=bg, current_user=user(), db=mock_db
    )
    assert out.pending == 3
    bg.add_task.assert_called_once()
    args = bg.add_task.call_args[0]
    assert args[0] is e._extract_fov_features_background
    assert args[1] == [1, 2, 3]


async def test_extract_fov_background_success():
    db = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("database.get_db_context", return_value=ctx), \
         patch("ml.features.extract_features_for_images",
               new=AsyncMock(return_value={"success": 3, "failed": 0})) as ef:
        await e._extract_fov_features_background([1, 2, 3])
    ef.assert_awaited_once()


async def test_extract_fov_background_runtime_error():
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("database.get_db_context", return_value=ctx), \
         patch("ml.features.extract_features_for_images",
               new=AsyncMock(side_effect=RuntimeError("model gone"))):
        await e._extract_fov_features_background([1])


async def test_extract_fov_background_generic_exception():
    with patch("database.get_db_context", side_effect=ValueError("x")):
        await e._extract_fov_features_background([1])
