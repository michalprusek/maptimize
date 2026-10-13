"""Bundleness on crop rows: who writes the score, and how a selection is averaged.

The score is derived data stored beside what it was derived from, so the failure
this file guards is the two drifting apart — a crop whose pixels changed keeping
the old number, or a mean mixing two model versions — none of which raises.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from ml.bundleness import BundlenessModel
from ml.bundleness.model import load_model
from services import bundleness_service as service
from tests.unit.conftest import make_result

MODEL = BundlenessModel("vT", "enc", np.array([1.0, -2.0]), 0.25)


def _crop(embedding=(3.0, 1.0), encoder="enc", score=None, version=None):
    return SimpleNamespace(
        embedding=list(embedding) if embedding is not None else None,
        embedding_model=encoder,
        bundleness_score=score,
        bundleness_model=version,
    )


# =============================================================================
# apply_score
# =============================================================================

def test_apply_score_writes_the_score_and_the_version_together():
    crop = _crop()
    service.apply_score(crop, MODEL)
    assert crop.bundleness_score == pytest.approx(3.0 - 2.0 + 0.25)
    assert crop.bundleness_model == "vT"


def test_apply_score_clears_a_score_whose_embedding_is_gone():
    crop = _crop(embedding=None, score=1.7, version="vT")
    service.apply_score(crop, MODEL)
    assert crop.bundleness_score is None
    assert crop.bundleness_model is None


def test_apply_score_clears_rather_than_scores_another_encoders_embedding():
    # Right length, wrong meaning: it would score without complaint.
    crop = _crop(encoder="dinov2-large", score=1.7, version="vT")
    service.apply_score(crop, MODEL)
    assert crop.bundleness_score is None
    assert crop.bundleness_model is None


def test_apply_score_defaults_to_the_shipped_model():
    crop = _crop(embedding=np.zeros(1024), encoder=load_model().embedding_model)
    service.apply_score(crop)
    assert crop.bundleness_model == load_model().version
    assert crop.bundleness_score == pytest.approx(load_model().bias)


# =============================================================================
# summarise
# =============================================================================

def test_summary_is_the_mean_and_sample_sd_of_the_scored_points():
    summary = service.summarise([1.0, 2.0, 3.0], ["vT"] * 3, [None] * 3, MODEL)
    assert summary.mean == pytest.approx(2.0)
    assert summary.sd == pytest.approx(1.0)  # ddof=1; population SD would be 0.816
    assert (summary.n_scored, summary.n_points) == (3, 3)
    assert summary.model_version == "vT"


def test_unscored_points_are_counted_but_not_averaged():
    summary = service.summarise([4.0, None, None], ["vT", None, None], [None] * 3, MODEL)
    assert summary.mean == pytest.approx(4.0)
    assert (summary.n_scored, summary.n_points) == (1, 3)


def test_a_score_from_another_model_version_is_left_out():
    # Two versions are two scales; their average belongs to neither.
    summary = service.summarise([1.0, 100.0], ["vT", "v0"], [None, None], MODEL)
    assert summary.mean == pytest.approx(1.0)
    assert (summary.n_scored, summary.n_points) == (1, 2)


def test_a_non_finite_score_is_left_out():
    summary = service.summarise([1.0, float("nan")], ["vT", "vT"], [None, None], MODEL)
    assert summary.mean == pytest.approx(1.0)
    assert summary.n_scored == 1


def test_no_scored_point_means_no_summary_not_a_zero():
    # Zero is a real value on this scale: "exactly the average annotated crop".
    assert service.summarise([None, 2.0], [None, "v0"], [None, None], MODEL) is None
    assert service.summarise([], [], [], MODEL) is None


def test_a_single_point_has_no_spread():
    summary = service.summarise([0.5], ["vT"], [7], MODEL)
    assert summary.sd is None
    assert summary.classes[0].sd is None


def test_classes_follow_the_labels_and_unlabelled_points_join_no_class():
    summary = service.summarise(
        [1.0, 3.0, 10.0, 20.0, 5.0],
        ["vT"] * 5,
        [8, 8, 2, None, 2],
        MODEL,
    )
    assert [(c.label, c.n_scored) for c in summary.classes] == [(2, 2), (8, 2)]
    assert summary.classes[0].mean == pytest.approx(7.5)
    assert summary.classes[1].mean == pytest.approx(2.0)
    # The unlabelled 20.0 is in the overall mean — the badge describes every
    # point on screen — but in no class.
    assert summary.mean == pytest.approx(39.0 / 5)
    assert sum(c.n_scored for c in summary.classes) == 4


def test_group_index_zero_is_a_class_like_any_other():
    # Label 0 is the first user-defined group. A truthiness check would drop it.
    summary = service.summarise([1.0, 2.0], ["vT", "vT"], [0, 1], MODEL)
    assert [c.label for c in summary.classes] == [0, 1]


def test_misaligned_inputs_raise_instead_of_pairing_the_wrong_rows():
    with pytest.raises(ValueError):
        service.summarise([1.0, 2.0], ["vT"], [None, None], MODEL)


# =============================================================================
# backfill
# =============================================================================

def _sql(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


async def test_backfill_scores_what_the_active_model_has_not_scored(mock_db):
    rows = [(11, np.array([1.0, 0.0])), (12, np.array([0.0, 1.0]))]
    mock_db.execute.side_effect = [
        make_result(),                      # clear unscoreable rows
        make_result(scalars_all=[11, 12]),  # pending ids
        make_result(fetchall=rows),         # their embeddings
        make_result(),                      # the write
    ]

    assert await service.backfill(mock_db, MODEL) == 2

    calls = mock_db.execute.await_args_list
    # Selecting on the stored VERSION (not on a NULL score) is what makes a
    # version bump rescore the corpus.
    pending_where = _sql(calls[1].args[0].whereclause)
    assert "bundleness_model IS DISTINCT FROM 'vT'" in pending_where
    assert "embedding IS NOT NULL" in pending_where
    assert "embedding_model = 'enc'" in pending_where

    write, params = calls[3].args
    assert "bundleness_model='vT'" in _sql(write).replace(" ", "")
    assert "WHERE cell_crops.id = " in _sql(write)
    assert params == [
        {"crop_id": 11, "score": pytest.approx(1.25)},
        {"crop_id": 12, "score": pytest.approx(-1.75)},
    ]
    mock_db.commit.assert_awaited_once()


async def test_backfill_clears_scores_on_rows_that_can_no_longer_be_scored(mock_db):
    mock_db.execute.side_effect = [make_result(), make_result(scalars_all=[])]
    await service.backfill(mock_db, MODEL)

    clear = mock_db.execute.await_args_list[0].args[0]
    sql = _sql(clear)
    assert sql.startswith("UPDATE cell_crops SET")
    assert "bundleness_score=NULL" in sql.replace(" ", "")
    assert "bundleness_model=NULL" in sql.replace(" ", "")
    # Only rows that are NOT scoreable — never a blanket wipe.
    where = _sql(clear.whereclause)
    assert where.startswith("NOT (")
    assert "embedding IS NOT NULL" in where and "embedding_model = 'enc'" in where


async def test_backfill_with_nothing_pending_writes_no_scores(mock_db):
    # The normal start: every crop already carries the active version.
    mock_db.execute.side_effect = [make_result(), make_result(scalars_all=[])]
    assert await service.backfill(mock_db, MODEL) == 0
    assert mock_db.execute.await_count == 2


async def test_backfill_works_in_batches(mock_db, monkeypatch):
    monkeypatch.setattr(service, "BACKFILL_BATCH", 2)
    embedding = np.array([1.0, 1.0])
    mock_db.execute.side_effect = [
        make_result(),
        make_result(scalars_all=[1, 2, 3]),
        make_result(fetchall=[(1, embedding), (2, embedding)]),
        make_result(),
        make_result(fetchall=[(3, embedding)]),
        make_result(),
    ]
    assert await service.backfill(mock_db, MODEL) == 3
    written = [call.args[1] for call in mock_db.execute.await_args_list[3::2]]
    assert [len(batch) for batch in written] == [2, 1]


# =============================================================================
# Wiring: every path that assigns an embedding rescored the crop
# =============================================================================

async def test_extracting_a_crop_embedding_scores_it_in_the_same_pass(mock_db):
    from ml.features import feature_extractor

    model = load_model()
    crop = SimpleNamespace(
        id=5, mip_path="/x.png", embedding=None, embedding_model=None,
        bundleness_score=9.9, bundleness_model="stale",
    )
    mock_db.execute.return_value = make_result(scalars_all=[crop])
    extractor = MagicMock()
    extractor.extract_batch_async = AsyncMock(return_value=[np.ones(1024)])

    with patch.object(feature_extractor, "FeatureExtractor", return_value=extractor), \
         patch.object(feature_extractor, "get_encoder",
                      return_value=SimpleNamespace(model_name=model.embedding_model)):
        result = await feature_extractor.extract_features_for_crops([5], mock_db)

    assert result["success"] == 1
    assert crop.bundleness_model == model.version
    assert crop.bundleness_score == pytest.approx(float(model.score(np.ones((1, 1024)))[0]))


async def test_importing_crop_embeddings_rescored_them_instead_of_trusting_the_archive(mock_db):
    from services import import_service as import_module

    model = load_model()
    crop = SimpleNamespace(
        id=70, embedding=None, embedding_model=model.embedding_model,
        bundleness_score=None, bundleness_model=None,
    )
    mock_db.execute.return_value = make_result(scalar=crop)

    with patch.object(import_module, "load_embeddings_from_zip",
                      return_value=(np.ones((1, 1024), dtype=np.float32), [7])):
        await import_module.ImportService()._import_maptimize_embeddings(
            zf=MagicMock(), old_to_new_crop_ids={7: 70}, file_list=[], db=mock_db,
        )

    assert crop.embedding is not None
    assert crop.bundleness_model == model.version
    assert crop.bundleness_score == pytest.approx(float(model.score(np.ones((1, 1024)))[0]))


def test_startup_runs_the_backfill():
    # Shipping a new model version reaches existing rows ONLY through this call;
    # if it is dropped, new uploads are scored and the corpus quietly is not.
    import inspect
    import main

    source = inspect.getsource(main.lifespan)
    assert "from services.bundleness_service import backfill as backfill_bundleness" in source
    assert "await backfill_bundleness(" in source


def test_provisioning_copies_the_version_with_the_score():
    # A copied score without its version is left out of every mean (and its
    # tooltip), then rescored at the next start: correct, but only by luck.
    import inspect
    from services import user_data_provisioning

    source = inspect.getsource(user_data_provisioning)
    assert source.count("bundleness_score, bundleness_model,") == 2
