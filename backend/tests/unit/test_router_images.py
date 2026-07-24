"""In-process unit tests for routers/images.py.

These call the FastAPI route handlers DIRECTLY (no app, no live DB), passing the
``mock_db`` AsyncMock and a fake ``current_user`` as kwargs. Services and ML
helpers the handlers import are patched at their source module boundary.

The goal is to exercise the error/permission/empty/ML-trigger branches that the
httpx integration suite cannot reach without a real DB and GPU.
"""
import io
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile

import routers.images as r
from models.image import UploadStatus
from schemas.image import (
    BatchProcessRequest,
    CropBboxUpdateRequest,
    CropBatchUpdateRequest,
    CropBatchUpdateItem,
    CropRegenerateRequest,
    ManualCropCreateRequest,
)
from tests.unit.conftest import make_result


# ============================================================================
# Fakes / helpers
# ============================================================================


def fake_user(uid=1, role="admin", email="a@b.cz"):
    return SimpleNamespace(id=uid, role=SimpleNamespace(value=role), email=email)


def fake_protein(pid=1, name="PRC1", color="#ff0000"):
    return SimpleNamespace(
        id=pid, name=name, full_name=None, description=None, color=color
    )


def fake_experiment(exp_id=10, user_id=1, group_id=None, map_protein_id=1):
    return SimpleNamespace(
        id=exp_id, user_id=user_id, group_id=group_id, map_protein_id=map_protein_id
    )


def fake_image(
    image_id=100,
    experiment_id=10,
    owner_id=1,
    *,
    status=UploadStatus.UPLOADED,
    source_discarded=False,
    detect_cells=True,
    file_path="/tmp/orig.tif",
    mip_path=None,
    sum_path=None,
    thumbnail_path=None,
    width=512,
    height=512,
):
    return SimpleNamespace(
        id=image_id,
        experiment_id=experiment_id,
        experiment=fake_experiment(experiment_id, owner_id),
        original_filename="img.tif",
        status=status,
        width=width,
        height=height,
        z_slices=5,
        file_size=1234,
        error_message=None,
        detect_cells=detect_cells,
        source_discarded=source_discarded,
        created_at=datetime.now(timezone.utc),
        processed_at=None,
        map_protein=fake_protein(),
        file_path=file_path,
        mip_path=mip_path,
        sum_path=sum_path,
        thumbnail_path=thumbnail_path,
        cell_crops=[],
    )


def fake_crop(
    crop_id=200,
    image_id=100,
    owner_id=1,
    *,
    excluded=False,
    mip_path="/tmp/crop_mip.png",
    sum_crop_path=None,
):
    return SimpleNamespace(
        id=crop_id,
        image_id=image_id,
        image=fake_image(image_id, owner_id=owner_id),
        original_filename="img.tif",
        bbox_x=10,
        bbox_y=10,
        bbox_w=50,
        bbox_h=50,
        bbox_angle=None,
        bundleness_score=0.5,
        detection_confidence=0.9,
        excluded=excluded,
        created_at=datetime.now(timezone.utc),
        map_protein=fake_protein(),
        mip_path=mip_path,
        sum_crop_path=sum_crop_path,
        embedding=None,
        embedding_model=None,
        embedding_status=None,
        embedding_error=None,
        mean_intensity=None,
        umap_x=None,
        umap_y=None,
        umap_computed_at=None,
        map_protein_id=1,
    )


def token_payload(sub=1, expired=False):
    exp = datetime.now(timezone.utc) + timedelta(
        seconds=-10 if expired else 3600
    )
    return SimpleNamespace(sub=sub, exp=exp)


@pytest.fixture
def no_group():
    """get_user_group_id returns None (user is in no group)."""
    with patch("routers.images.get_user_group_id", new=AsyncMock(return_value=None)):
        yield


# ============================================================================
# Helper functions: access filters & loaders
# ============================================================================


async def test_experiment_access_filter_no_group(mock_db, no_group):
    f = await r._experiment_access_filter(1, mock_db)
    sql = str(f.compile(compile_kwargs={"literal_binds": True}))
    # Owner-only filter when the user is in no group.
    assert "user_id" in sql and "1" in sql
    assert "group_id" not in sql


async def test_experiment_access_filter_with_group(mock_db):
    with patch("routers.images.get_user_group_id", new=AsyncMock(return_value=7)):
        f = await r._experiment_access_filter(1, mock_db)
    sql = str(f.compile(compile_kwargs={"literal_binds": True})).upper()
    # Shared access = own experiments OR the group's, joined by OR (the exact
    # shape that regressed in prod once — guard it behaviourally, not just != None).
    assert "USER_ID" in sql and "GROUP_ID" in sql
    assert "7" in sql and " OR " in sql


async def test_verify_experiment_ownership_found(mock_db):
    exp = fake_experiment()
    mock_db.execute.return_value = make_result(scalar=exp)
    out = await r.verify_experiment_ownership(10, 1, mock_db)
    assert out is exp


async def test_verify_experiment_ownership_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.verify_experiment_ownership(10, 1, mock_db)
    assert exc.value.status_code == 404


async def test_verify_experiment_read_access_found(mock_db, no_group):
    exp = fake_experiment()
    mock_db.execute.return_value = make_result(scalar=exp)
    out = await r.verify_experiment_read_access(10, 1, mock_db)
    assert out is exp


async def test_verify_experiment_read_access_not_found(mock_db, no_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.verify_experiment_read_access(10, 1, mock_db)
    assert exc.value.status_code == 404


async def test_get_image_for_read_found(mock_db, no_group):
    img = fake_image()
    mock_db.execute.return_value = make_result(scalar=img)
    out = await r.get_image_for_read(mock_db, 100, 1)
    assert out is img


async def test_get_image_for_read_not_found(mock_db, no_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.get_image_for_read(mock_db, 100, 1)
    assert exc.value.status_code == 404


async def test_get_crop_for_read_found(mock_db, no_group):
    crop = fake_crop()
    mock_db.execute.return_value = make_result(scalar=crop)
    out = await r.get_crop_for_read(mock_db, 200, 1)
    assert out is crop


async def test_get_crop_for_read_not_found(mock_db, no_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.get_crop_for_read(mock_db, 200, 1)
    assert exc.value.status_code == 404


async def test_get_image_for_write_owner_ok(mock_db, no_group):
    img = fake_image(owner_id=1)
    mock_db.execute.return_value = make_result(scalar=img)
    out = await r.get_image_for_write(mock_db, 100, 1)
    assert out is img


async def test_get_image_for_write_not_owner_403(mock_db, no_group):
    img = fake_image(owner_id=2)  # different owner
    mock_db.execute.return_value = make_result(scalar=img)
    with pytest.raises(HTTPException) as exc:
        await r.get_image_for_write(mock_db, 100, 1)
    assert exc.value.status_code == 403


async def test_get_crop_for_write_owner_ok(mock_db, no_group):
    crop = fake_crop(owner_id=1)
    mock_db.execute.return_value = make_result(scalar=crop)
    out = await r.get_crop_for_write(mock_db, 200, 1)
    assert out is crop


async def test_get_crop_for_write_not_owner_403(mock_db, no_group):
    crop = fake_crop(owner_id=2)
    mock_db.execute.return_value = make_result(scalar=crop)
    with pytest.raises(HTTPException) as exc:
        await r.get_crop_for_write(mock_db, 200, 1)
    assert exc.value.status_code == 403


# ============================================================================
# safe_remove_file / serve_image_file / validate_image_token
# ============================================================================


def test_safe_remove_file_none():
    assert r.safe_remove_file(None) is False


def test_safe_remove_file_missing():
    assert r.safe_remove_file("/nonexistent/path/xyz.png") is False


def test_safe_remove_file_success(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("x")
    assert r.safe_remove_file(str(p)) is True
    assert not p.exists()


def test_safe_remove_file_oserror():
    with patch("routers.images.os.path.exists", return_value=True), patch(
        "routers.images.os.remove", side_effect=OSError("boom")
    ):
        assert r.safe_remove_file("/some/path") is False


def test_serve_image_file_non_tiff():
    out = r.serve_image_file("/tmp/photo.png")
    # FileResponse for non-TIFF
    assert out.__class__.__name__ == "FileResponse"


def test_serve_image_file_tiff_conversion(tmp_path):
    from PIL import Image as PILImage

    p = tmp_path / "g.tif"
    PILImage.new("RGB", (8, 8), (10, 20, 30)).save(str(p), format="TIFF")
    out = r.serve_image_file(str(p))
    assert out.media_type == "image/png"


def test_serve_image_file_tiff_16bit(tmp_path):
    import numpy as np
    from PIL import Image as PILImage

    arr = (np.arange(64, dtype=np.uint16) * 1000).reshape(8, 8)
    p = tmp_path / "g16.tif"
    PILImage.fromarray(arr, mode="I;16").save(str(p), format="TIFF")
    out = r.serve_image_file(str(p))
    assert out.media_type == "image/png"


def test_serve_image_file_tiff_rgba(tmp_path):
    from PIL import Image as PILImage

    p = tmp_path / "rgba.tif"
    PILImage.new("RGBA", (8, 8), (1, 2, 3, 4)).save(str(p), format="TIFF")
    out = r.serve_image_file(str(p))
    assert out.media_type == "image/png"


def test_serve_image_file_tiff_grayscale_convert(tmp_path):
    from PIL import Image as PILImage

    # mode 'L' is not in the 16-bit set, not RGBA, not RGB -> convert('RGB')
    p = tmp_path / "gray.tif"
    PILImage.new("L", (8, 8), 100).save(str(p), format="TIFF")
    out = r.serve_image_file(str(p))
    assert out.media_type == "image/png"


def test_serve_image_file_tiff_uniform(tmp_path):
    import numpy as np
    from PIL import Image as PILImage

    arr = np.full((8, 8), 5, dtype=np.uint16)
    p = tmp_path / "uniform.tif"
    PILImage.fromarray(arr, mode="I;16").save(str(p), format="TIFF")
    out = r.serve_image_file(str(p))
    assert out.media_type == "image/png"


def test_serve_image_file_tiff_failure_fallback():
    # File does not exist -> PILImage.open raises -> falls back to FileResponse
    out = r.serve_image_file("/nonexistent/broken.tif")
    assert out.__class__.__name__ == "FileResponse"


def test_validate_image_token_missing():
    with pytest.raises(HTTPException) as exc:
        r.validate_image_token(None)
    assert exc.value.status_code == 401


def test_validate_image_token_invalid():
    with patch("routers.images.decode_token", return_value=None):
        with pytest.raises(HTTPException) as exc:
            r.validate_image_token("bad")
    assert exc.value.status_code == 401


def test_validate_image_token_expired():
    with patch("routers.images.decode_token", return_value=token_payload(expired=True)):
        with pytest.raises(HTTPException) as exc:
            r.validate_image_token("tok")
    assert exc.value.status_code == 401


def test_validate_image_token_ok():
    payload = token_payload()
    with patch("routers.images.decode_token", return_value=payload):
        out = r.validate_image_token("tok")
    assert out is payload


# ============================================================================
# upload_image
# ============================================================================


async def test_upload_image_bad_extension(mock_db):
    bt = BackgroundTasks()
    upload = UploadFile(filename="bad.gif", file=io.BytesIO(b"x"))
    mock_db.execute.return_value = make_result(scalar=fake_experiment())
    with pytest.raises(HTTPException) as exc:
        await r.upload_image(
            bt, experiment_id=10, file=upload, current_user=fake_user(), db=mock_db
        )
    assert exc.value.status_code == 400


async def test_upload_image_experiment_not_found(mock_db):
    bt = BackgroundTasks()
    upload = UploadFile(filename="ok.png", file=io.BytesIO(b"x"))
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.upload_image(
            bt, experiment_id=10, file=upload, current_user=fake_user(), db=mock_db
        )
    assert exc.value.status_code == 404


async def test_upload_image_success(mock_db, tmp_path):
    bt = BackgroundTasks()
    upload = UploadFile(filename="ok.png", file=io.BytesIO(b"hello"))
    exp = fake_experiment()
    saved = fake_image(status=UploadStatus.UPLOADING)
    # 1st execute = verify ownership; 2nd execute = reload image via scalar_one()
    reload_result = make_result()
    reload_result.scalar_one.return_value = saved
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        reload_result,
    ]
    settings_stub = SimpleNamespace(upload_dir=tmp_path)
    with patch.object(r, "settings", settings_stub):
        out = await r.upload_image(
            bt, experiment_id=10, file=upload, current_user=fake_user(), db=mock_db
        )
    assert out.id == saved.id
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited()
    assert len(bt.tasks) == 1


# ============================================================================
# batch_process_images
# ============================================================================


async def test_batch_process_missing_images(mock_db):
    bt = BackgroundTasks()
    req = BatchProcessRequest(image_ids=[1, 2, 3], detect_cells=True)
    # Only one image found
    mock_db.execute.return_value = make_result(
        scalars_all=[fake_image(image_id=1)]
    )
    with pytest.raises(HTTPException) as exc:
        await r.batch_process_images(req, bt, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 404


async def test_batch_process_invalid_status(mock_db):
    bt = BackgroundTasks()
    req = BatchProcessRequest(image_ids=[1], detect_cells=True)
    img = fake_image(image_id=1, status=UploadStatus.UPLOADING)  # not ready
    mock_db.execute.return_value = make_result(scalars_all=[img])
    with pytest.raises(HTTPException) as exc:
        await r.batch_process_images(req, bt, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 400


async def test_batch_process_success(mock_db):
    bt = BackgroundTasks()
    req = BatchProcessRequest(image_ids=[1, 2], detect_cells=True)
    imgs = [
        fake_image(image_id=1, status=UploadStatus.UPLOADED),
        fake_image(image_id=2, status=UploadStatus.READY),
    ]
    mock_db.execute.return_value = make_result(scalars_all=imgs)
    out = await r.batch_process_images(req, bt, current_user=fake_user(), db=mock_db)
    assert out.processing_count == 2
    assert len(bt.tasks) == 2


# ============================================================================
# list_fovs / list_images
# ============================================================================


async def test_list_fovs_success(mock_db, no_group):
    exp = fake_experiment()
    img = fake_image(thumbnail_path="/tmp/thumb.png")
    mock_db.execute.side_effect = [
        make_result(scalar=exp),  # read access check
        make_result(fetchall=[(img, 3)]),  # rows
    ]
    out = await r.list_fovs(
        experiment_id=10, skip=0, limit=10, current_user=fake_user(), db=mock_db
    )
    assert len(out) == 1
    assert out[0].cell_count == 3
    assert out[0].thumbnail_url is not None


async def test_list_fovs_no_limit_no_thumb(mock_db, no_group):
    exp = fake_experiment()
    img = fake_image(thumbnail_path=None)
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(fetchall=[(img, 0)]),
    ]
    out = await r.list_fovs(
        experiment_id=10, skip=0, limit=None, current_user=fake_user(), db=mock_db
    )
    assert out[0].thumbnail_url is None
    assert out[0].cell_count == 0


async def test_list_fovs_no_access(mock_db, no_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.list_fovs(
            experiment_id=10, skip=0, limit=None, current_user=fake_user(), db=mock_db
        )
    assert exc.value.status_code == 404


async def test_list_images_success(mock_db, no_group):
    exp = fake_experiment()
    img = fake_image()
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(fetchall=[(img, 5)]),
    ]
    out = await r.list_images(
        experiment_id=10, skip=0, limit=20, current_user=fake_user(), db=mock_db
    )
    assert len(out) == 1
    assert out[0].cell_count == 5


async def test_list_images_no_limit(mock_db, no_group):
    exp = fake_experiment()
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(fetchall=[]),
    ]
    out = await r.list_images(
        experiment_id=10, skip=0, limit=None, current_user=fake_user(), db=mock_db
    )
    assert out == []


# ============================================================================
# list_cell_crops / list_fov_crops
# ============================================================================


async def test_list_cell_crops_success(mock_db, no_group):
    exp = fake_experiment()
    crop = fake_crop()
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(scalars_all=[crop]),
    ]
    out = await r.list_cell_crops(
        experiment_id=10, exclude_excluded=True, current_user=fake_user(), db=mock_db
    )
    assert len(out) == 1
    assert out[0].id == crop.id


async def test_list_cell_crops_include_excluded(mock_db, no_group):
    exp = fake_experiment()
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(scalars_all=[]),
    ]
    out = await r.list_cell_crops(
        experiment_id=10, exclude_excluded=False, current_user=fake_user(), db=mock_db
    )
    assert out == []


async def test_list_fov_crops_success(mock_db, no_group):
    img = fake_image()
    crop = fake_crop()
    mock_db.execute.side_effect = [
        make_result(scalar=img),  # get_image_for_read
        make_result(scalars_all=[crop]),
    ]
    out = await r.list_fov_crops(
        fov_id=100, exclude_excluded=True, current_user=fake_user(), db=mock_db
    )
    assert len(out) == 1


async def test_list_fov_crops_image_not_found(mock_db, no_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.list_fov_crops(
            fov_id=100, exclude_excluded=False, current_user=fake_user(), db=mock_db
        )
    assert exc.value.status_code == 404


# ============================================================================
# get_crop_image
# ============================================================================


async def test_get_crop_image_file_missing(mock_db, no_group):
    crop = fake_crop(mip_path="/nonexistent/x.png", sum_crop_path=None)
    mock_db.execute.return_value = make_result(scalar=crop)
    with patch("routers.images.decode_token", return_value=token_payload()):
        with pytest.raises(HTTPException) as exc:
            await r.get_crop_image(200, type="mip", token="t", db=mock_db)
    assert exc.value.status_code == 404


async def test_get_crop_image_success_mip(mock_db, no_group, tmp_path):
    p = tmp_path / "crop.png"
    p.write_bytes(b"x")
    crop = fake_crop(mip_path=str(p))
    mock_db.execute.return_value = make_result(scalar=crop)
    with patch("routers.images.decode_token", return_value=token_payload()):
        out = await r.get_crop_image(200, type="mip", token="t", db=mock_db)
    assert out.__class__.__name__ == "FileResponse"


async def test_get_crop_image_success_sum(mock_db, no_group, tmp_path):
    p = tmp_path / "crop_sum.png"
    p.write_bytes(b"x")
    crop = fake_crop(mip_path="/tmp/mip.png", sum_crop_path=str(p))
    mock_db.execute.return_value = make_result(scalar=crop)
    with patch("routers.images.decode_token", return_value=token_payload()):
        out = await r.get_crop_image(200, type="sum", token="t", db=mock_db)
    assert out.__class__.__name__ == "FileResponse"


# ============================================================================
# delete_cell_crop
# ============================================================================


async def test_delete_cell_crop_conflict(mock_db, no_group):
    crop = fake_crop(owner_id=1)
    # 1: get_crop_for_write loader, 2: comparison count > 0
    mock_db.execute.side_effect = [
        make_result(scalar=crop),
        make_result(scalar=2),
    ]
    with pytest.raises(HTTPException) as exc:
        await r.delete_cell_crop(
            200, confirm_delete_comparisons=False, current_user=fake_user(), db=mock_db
        )
    assert exc.value.status_code == 409


async def test_delete_cell_crop_success(mock_db, no_group):
    crop = fake_crop(owner_id=1)
    metric_img = SimpleNamespace(id=300, cell_crop_id=200)
    mock_db.execute.side_effect = [
        make_result(scalar=crop),  # get_crop_for_write
        make_result(scalar=0),  # comparison count
        make_result(scalars_all=[metric_img]),  # metric images
        make_result(rowcount=1),  # delete MetricComparison
        make_result(rowcount=1),  # delete MetricRating
    ]
    with patch("routers.images.safe_remove_file", return_value=True):
        await r.delete_cell_crop(
            200, confirm_delete_comparisons=False, current_user=fake_user(), db=mock_db
        )
    mock_db.delete.assert_awaited()
    mock_db.commit.assert_awaited()


async def test_delete_cell_crop_confirmed_with_comparisons(mock_db, no_group):
    crop = fake_crop(owner_id=1)
    mock_db.execute.side_effect = [
        make_result(scalar=crop),
        make_result(scalar=5),  # has comparisons but confirmed
        make_result(scalars_all=[]),  # no metric images
    ]
    with patch("routers.images.safe_remove_file", return_value=True):
        await r.delete_cell_crop(
            200, confirm_delete_comparisons=True, current_user=fake_user(), db=mock_db
        )
    mock_db.commit.assert_awaited()


# ============================================================================
# update_crop_bbox
# ============================================================================


async def test_update_crop_bbox_not_found(mock_db):
    req = CropBboxUpdateRequest(bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20)
    with patch(
        "services.crop_editor_service.get_crop_with_ownership_check",
        new=AsyncMock(return_value=(None, None, "Crop not found")),
    ):
        with pytest.raises(HTTPException) as exc:
            await r.update_crop_bbox(
                200, req, current_user=fake_user(), db=mock_db
            )
    assert exc.value.status_code == 404


async def test_update_crop_bbox_invalid(mock_db):
    req = CropBboxUpdateRequest(bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20)
    crop = fake_crop()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_crop_with_ownership_check",
        new=AsyncMock(return_value=(crop, img, None)),
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(False, "out of bounds"),
    ):
        with pytest.raises(HTTPException) as exc:
            await r.update_crop_bbox(200, req, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 400


async def test_update_crop_bbox_success(mock_db):
    req = CropBboxUpdateRequest(bbox_x=5, bbox_y=6, bbox_w=20, bbox_h=22)
    crop = fake_crop()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_crop_with_ownership_check",
        new=AsyncMock(return_value=(crop, img, None)),
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ):
        out = await r.update_crop_bbox(200, req, current_user=fake_user(), db=mock_db)
    assert out.bbox_x == 5
    assert out.needs_regeneration is True
    assert crop.embedding is None


async def test_update_crop_bbox_persists_angle(mock_db):
    req = CropBboxUpdateRequest(bbox_x=5, bbox_y=6, bbox_w=20, bbox_h=22, bbox_angle=30.0)
    crop = fake_crop()
    img = fake_image()
    captured = {}

    def _record_validate(*args):
        captured["angle"] = args[6]  # the angle-aware 7th positional arg
        return (True, None)

    with patch(
        "services.crop_editor_service.get_crop_with_ownership_check",
        new=AsyncMock(return_value=(crop, img, None)),
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        side_effect=_record_validate,
    ):
        out = await r.update_crop_bbox(200, req, current_user=fake_user(), db=mock_db)
    assert crop.bbox_angle == 30.0          # persisted on the model
    assert out.bbox_angle == 30.0           # echoed in the response
    assert captured["angle"] == 30.0        # angle reached the (angle-aware) validator


async def test_update_crop_bbox_zero_angle_stored_as_none(mock_db):
    req = CropBboxUpdateRequest(bbox_x=5, bbox_y=6, bbox_w=20, bbox_h=22, bbox_angle=0.0)
    crop = fake_crop()
    with patch(
        "services.crop_editor_service.get_crop_with_ownership_check",
        new=AsyncMock(return_value=(crop, fake_image(), None)),
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ):
        await r.update_crop_bbox(200, req, current_user=fake_user(), db=mock_db)
    assert crop.bbox_angle is None  # 0 collapses to NULL (axis-aligned)


# ============================================================================
# regenerate_crop_features
# ============================================================================


async def test_regenerate_crop_not_found(mock_db):
    req = CropRegenerateRequest()
    bt = BackgroundTasks()
    with patch(
        "services.crop_editor_service.get_crop_with_ownership_check",
        new=AsyncMock(return_value=(None, None, "Crop not found")),
    ), patch(
        "services.crop_editor_service.regenerate_crop_features", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.regenerate_crop_features(
                200, req, background_tasks=bt, current_user=fake_user(), db=mock_db
            )
    assert exc.value.status_code == 404


async def test_regenerate_crop_failure(mock_db):
    req = CropRegenerateRequest()
    bt = BackgroundTasks()
    crop = fake_crop()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_crop_with_ownership_check",
        new=AsyncMock(return_value=(crop, img, None)),
    ), patch(
        "services.crop_editor_service.regenerate_crop_features",
        new=AsyncMock(return_value={"success": False, "error": "regen fail"}),
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.regenerate_crop_features(
                200, req, background_tasks=bt, current_user=fake_user(), db=mock_db
            )
    assert exc.value.status_code == 400


async def test_regenerate_crop_success_with_embedding(mock_db):
    req = CropRegenerateRequest()
    bt = BackgroundTasks()
    crop = fake_crop()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_crop_with_ownership_check",
        new=AsyncMock(return_value=(crop, img, None)),
    ), patch(
        "services.crop_editor_service.regenerate_crop_features",
        new=AsyncMock(
            return_value={
                "success": True,
                "needs_embedding": True,
                "umap_invalidated": True,
            }
        ),
    ), patch(
        "services.crop_editor_service.run_embedding_extraction_task", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.regenerate_crop_features(
            200, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.id == crop.id
    assert len(bt.tasks) == 1
    assert crop.embedding_status == "pending"


async def test_regenerate_crop_success_umap_warning(mock_db):
    req = CropRegenerateRequest()
    bt = BackgroundTasks()
    crop = fake_crop()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_crop_with_ownership_check",
        new=AsyncMock(return_value=(crop, img, None)),
    ), patch(
        "services.crop_editor_service.regenerate_crop_features",
        new=AsyncMock(
            return_value={
                "success": True,
                "needs_embedding": False,
                "umap_invalidated": False,
            }
        ),
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.regenerate_crop_features(
            200, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.warnings is not None
    assert len(bt.tasks) == 0


# ============================================================================
# create_manual_crop
# ============================================================================


async def test_create_manual_crop_image_not_found(mock_db):
    req = ManualCropCreateRequest(bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20)
    bt = BackgroundTasks()
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(None, "Image not found")),
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.create_manual_crop(
                100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
            )
    assert exc.value.status_code == 404


async def test_create_manual_crop_bad_status(mock_db):
    req = ManualCropCreateRequest(bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20)
    bt = BackgroundTasks()
    img = fake_image(status=UploadStatus.PROCESSING)
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.create_manual_crop(
                100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
            )
    assert exc.value.status_code == 400


async def test_create_manual_crop_create_error(mock_db):
    req = ManualCropCreateRequest(bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20)
    bt = BackgroundTasks()
    img = fake_image(status=UploadStatus.UPLOADED)
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop",
        new=AsyncMock(return_value=(None, "create failed")),
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.create_manual_crop(
                100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
            )
    assert exc.value.status_code == 400


async def test_create_manual_crop_success(mock_db):
    req = ManualCropCreateRequest(bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20)
    bt = BackgroundTasks()
    img = fake_image(status=UploadStatus.READY)
    crop = fake_crop()
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop",
        new=AsyncMock(return_value=(crop, None)),
    ), patch(
        "services.crop_editor_service.run_embedding_extraction_task", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.create_manual_crop(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.id == crop.id
    assert crop.embedding_status == "pending"
    assert len(bt.tasks) == 1


# ============================================================================
# batch_update_crops
# ============================================================================


def _batch_patches():
    """Common context-manager bundle for batch_update_crops dependencies."""
    return (
        patch("services.umap_service.invalidate_crop_umap", new=AsyncMock()),
        patch("ml.features.extract_features_for_crops", new=AsyncMock()),
    )


async def test_batch_update_image_not_found(mock_db):
    req = CropBatchUpdateRequest(changes=[], regenerate_features=False)
    bt = BackgroundTasks()
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(None, "Image not found")),
    ), patch("services.umap_service.invalidate_crop_umap", new=AsyncMock()), patch(
        "ml.features.extract_features_for_crops", new=AsyncMock()
    ):
        with pytest.raises(HTTPException) as exc:
            await r.batch_update_crops(
                100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
            )
    assert exc.value.status_code == 404


async def test_batch_update_create_missing_bbox(mock_db):
    # A "create" item that passes schema validation but we bypass via construct
    item = CropBatchUpdateItem.model_construct(
        id=None, action="create", bbox_x=None, bbox_y=None, bbox_w=None, bbox_h=None,
        map_protein_id=None,
    )
    req = CropBatchUpdateRequest.model_construct(
        changes=[item], regenerate_features=False, confirm_delete_comparisons=False
    )
    bt = BackgroundTasks()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop", new=AsyncMock()
    ), patch(
        "services.crop_editor_service.delete_crop_files"
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.failed and out.failed[0]["error"] == "Missing bbox coordinates"


async def test_batch_update_create_success(mock_db):
    item = CropBatchUpdateItem(
        action="create", bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20
    )
    req = CropBatchUpdateRequest(changes=[item], regenerate_features=False)
    bt = BackgroundTasks()
    img = fake_image()
    new_crop = fake_crop(crop_id=500)
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop",
        new=AsyncMock(return_value=(new_crop, None)),
    ), patch(
        "services.crop_editor_service.delete_crop_files"
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.created == [500]


async def test_batch_update_create_error(mock_db):
    item = CropBatchUpdateItem(
        action="create", bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20
    )
    req = CropBatchUpdateRequest(changes=[item], regenerate_features=False)
    bt = BackgroundTasks()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop",
        new=AsyncMock(return_value=(None, "create boom")),
    ), patch(
        "services.crop_editor_service.delete_crop_files"
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.failed[0]["error"] == "create boom"


async def test_batch_update_update_missing_id(mock_db):
    item = CropBatchUpdateItem.model_construct(
        id=None, action="update", bbox_x=1, bbox_y=1, bbox_w=20, bbox_h=20,
        map_protein_id=None,
    )
    req = CropBatchUpdateRequest.model_construct(
        changes=[item], regenerate_features=False, confirm_delete_comparisons=False
    )
    bt = BackgroundTasks()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop", new=AsyncMock()
    ), patch(
        "services.crop_editor_service.delete_crop_files"
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.failed[0]["error"] == "Missing crop id"


async def test_batch_update_update_crop_not_found(mock_db):
    item = CropBatchUpdateItem(
        id=999, action="update", bbox_x=1, bbox_y=1, bbox_w=20, bbox_h=20
    )
    req = CropBatchUpdateRequest(changes=[item], regenerate_features=False)
    bt = BackgroundTasks()
    img = fake_image()
    mock_db.execute.return_value = make_result(scalar=None)  # crop lookup -> None
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.failed[0]["error"] == "Crop not found"


async def test_batch_update_update_invalid_bbox(mock_db):
    item = CropBatchUpdateItem(
        id=200, action="update", bbox_x=1, bbox_y=1, bbox_w=20, bbox_h=20
    )
    req = CropBatchUpdateRequest(changes=[item], regenerate_features=False)
    bt = BackgroundTasks()
    img = fake_image()
    crop = fake_crop()
    mock_db.execute.return_value = make_result(scalar=crop)
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(False, "bbox bad"),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.failed[0]["error"] == "bbox bad"


async def test_batch_update_update_success_and_regen(mock_db):
    item = CropBatchUpdateItem(
        id=200, action="update", bbox_x=1, bbox_y=1, bbox_w=20, bbox_h=20,
        map_protein_id=3,
    )
    req = CropBatchUpdateRequest(changes=[item], regenerate_features=True)
    bt = BackgroundTasks()
    img = fake_image()
    crop = fake_crop(crop_id=200)
    # crop lookup, then update-status execute calls
    mock_db.execute.return_value = make_result(scalar=crop)
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ) as inval, patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.updated == [200]
    assert out.regeneration_queued is True
    assert crop.map_protein_id == 3
    inval.assert_awaited()
    assert len(bt.tasks) == 1


async def test_batch_update_delete_missing_id(mock_db):
    item = CropBatchUpdateItem.model_construct(
        id=None, action="delete", bbox_x=None, bbox_y=None, bbox_w=None, bbox_h=None,
        map_protein_id=None,
    )
    req = CropBatchUpdateRequest.model_construct(
        changes=[item], regenerate_features=False, confirm_delete_comparisons=False
    )
    bt = BackgroundTasks()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.delete_crop_files"
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.failed[0]["error"] == "Missing crop id"


async def test_batch_update_delete_crop_not_found(mock_db):
    item = CropBatchUpdateItem(id=999, action="delete")
    req = CropBatchUpdateRequest(changes=[item], regenerate_features=False)
    bt = BackgroundTasks()
    img = fake_image()
    mock_db.execute.return_value = make_result(scalar=None)
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.delete_crop_files"
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.failed[0]["error"] == "Crop not found"


async def test_batch_update_delete_has_comparisons(mock_db):
    item = CropBatchUpdateItem(id=200, action="delete")
    req = CropBatchUpdateRequest(
        changes=[item], regenerate_features=False, confirm_delete_comparisons=False
    )
    bt = BackgroundTasks()
    img = fake_image()
    crop = fake_crop(crop_id=200)
    mock_db.execute.side_effect = [
        make_result(scalar=crop),  # crop lookup
        make_result(scalar=3),  # comparison count > 0
    ]
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.delete_crop_files"
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert "comparisons" in out.failed[0]["error"]


async def test_batch_update_delete_success(mock_db):
    item = CropBatchUpdateItem(id=200, action="delete")
    req = CropBatchUpdateRequest(
        changes=[item], regenerate_features=False, confirm_delete_comparisons=True
    )
    bt = BackgroundTasks()
    img = fake_image()
    crop = fake_crop(crop_id=200)
    mock_db.execute.return_value = make_result(scalar=crop)  # crop lookup
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.delete_crop_files"
    ) as del_files, patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ) as inval, patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.deleted == [200]
    del_files.assert_called_once()
    inval.assert_awaited()


async def test_batch_update_exception_in_change(mock_db):
    item = CropBatchUpdateItem(
        action="create", bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20
    )
    req = CropBatchUpdateRequest(changes=[item], regenerate_features=False)
    bt = BackgroundTasks()
    img = fake_image()
    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop",
        new=AsyncMock(side_effect=RuntimeError("kaboom")),
    ), patch(
        "services.crop_editor_service.delete_crop_files"
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch("ml.features.extract_features_for_crops", new=AsyncMock()):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
    assert out.failed[0]["error"] == "kaboom"


async def test_batch_update_regenerate_task_runs(mock_db):
    """Cover the inner regenerate_task closure by invoking the queued task."""
    item = CropBatchUpdateItem(
        action="create", bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20
    )
    req = CropBatchUpdateRequest(changes=[item], regenerate_features=True)
    bt = BackgroundTasks()
    img = fake_image()
    new_crop = fake_crop(crop_id=500)

    # Build a task_db usable inside the closure
    from contextlib import asynccontextmanager
    task_db = AsyncMock(name="task_db")
    task_db.execute.return_value = make_result(scalar=new_crop)
    task_db.commit = AsyncMock()

    @asynccontextmanager
    async def fake_ctx():
        yield task_db

    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop",
        new=AsyncMock(return_value=(new_crop, None)),
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch(
        "services.crop_editor_service.regenerate_crop_features", new=AsyncMock()
    ), patch(
        "services.crop_editor_service.update_crop_embedding_status", new=AsyncMock()
    ), patch(
        "ml.features.extract_features_for_crops", new=AsyncMock()
    ), patch("database.get_db_context", fake_ctx):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
        assert out.regeneration_queued is True
        # Execute the queued background task to cover the closure body
        task = bt.tasks[0]
        await task.func(*task.args, **task.kwargs)
    task_db.commit.assert_awaited()


async def test_batch_update_regenerate_task_handles_errors(mock_db):
    """Closure: one crop regenerates OK then embedding extraction raises, one crop
    lookup returns None (deleted). Also exercises the error-status update failing.
    """
    item_a = CropBatchUpdateItem(
        action="create", bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20
    )
    item_b = CropBatchUpdateItem(
        action="create", bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20
    )
    req = CropBatchUpdateRequest(changes=[item_a, item_b], regenerate_features=True)
    bt = BackgroundTasks()
    img = fake_image()
    crop_a = fake_crop(crop_id=501)

    from contextlib import asynccontextmanager
    task_db = AsyncMock(name="task_db")
    # First crop lookup returns a crop (regenerates OK), second returns None (deleted)
    task_db.execute.side_effect = [
        make_result(scalar=crop_a),
        make_result(scalar=None),
    ]
    task_db.commit = AsyncMock()

    @asynccontextmanager
    async def fake_ctx():
        yield task_db

    create_mock = AsyncMock(
        side_effect=[
            (fake_crop(crop_id=501), None),
            (fake_crop(crop_id=502), None),
        ]
    )
    # update_crop_embedding_status raises when called with "error" -> covers the
    # nested try/except that logs the status-update failure (lines 1176-1179).
    async def status_side_effect(db, crop_id, status_val, *a):
        if status_val == "error":
            raise RuntimeError("status update boom")

    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop", new=create_mock
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch(
        "services.crop_editor_service.regenerate_crop_features", new=AsyncMock()
    ), patch(
        "services.crop_editor_service.update_crop_embedding_status",
        new=AsyncMock(side_effect=status_side_effect),
    ), patch(
        "ml.features.extract_features_for_crops",
        new=AsyncMock(side_effect=RuntimeError("embed boom")),
    ), patch("database.get_db_context", fake_ctx):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
        task = bt.tasks[0]
        await task.func(*task.args, **task.kwargs)
    task_db.commit.assert_awaited()


async def test_batch_update_regenerate_task_regen_raises(mock_db):
    """Closure: regenerate_crop_features raises -> per-crop error path, and the
    nested update_crop_embedding_status('error') also raises (lines 1156-1159)."""
    item = CropBatchUpdateItem(
        action="create", bbox_x=0, bbox_y=0, bbox_w=20, bbox_h=20
    )
    req = CropBatchUpdateRequest(changes=[item], regenerate_features=True)
    bt = BackgroundTasks()
    img = fake_image()
    crop = fake_crop(crop_id=601)

    from contextlib import asynccontextmanager
    task_db = AsyncMock(name="task_db")
    task_db.execute.return_value = make_result(scalar=crop)
    task_db.commit = AsyncMock()

    @asynccontextmanager
    async def fake_ctx():
        yield task_db

    async def status_side_effect(db, crop_id, status_val, *a):
        if status_val == "error":
            raise RuntimeError("nested status boom")

    with patch(
        "services.crop_editor_service.get_image_with_ownership_check",
        new=AsyncMock(return_value=(img, None)),
    ), patch(
        "services.crop_editor_service.create_manual_crop",
        new=AsyncMock(return_value=(crop, None)),
    ), patch(
        "services.crop_editor_service.validate_bbox_within_image",
        return_value=(True, None),
    ), patch(
        "services.umap_service.invalidate_crop_umap", new=AsyncMock()
    ), patch(
        "services.crop_editor_service.regenerate_crop_features",
        new=AsyncMock(side_effect=RuntimeError("regen boom")),
    ), patch(
        "services.crop_editor_service.update_crop_embedding_status",
        new=AsyncMock(side_effect=status_side_effect),
    ), patch(
        "ml.features.extract_features_for_crops", new=AsyncMock()
    ), patch("database.get_db_context", fake_ctx):
        out = await r.batch_update_crops(
            100, req, background_tasks=bt, current_user=fake_user(), db=mock_db
        )
        task = bt.tasks[0]
        await task.func(*task.args, **task.kwargs)
    task_db.commit.assert_awaited()


# ============================================================================
# get_image
# ============================================================================


async def test_get_image_success(mock_db, no_group):
    img = fake_image()
    img.cell_crops = [SimpleNamespace(
        id=1, bbox_x=0, bbox_y=0, bbox_w=10, bbox_h=10,
        bundleness_score=None, sum_crop_path=None, excluded=False,
    )]
    mock_db.execute.return_value = make_result(scalar=img)
    out = await r.get_image(100, current_user=fake_user(), db=mock_db)
    assert out.cell_count == 1


async def test_get_image_not_found(mock_db, no_group):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.get_image(100, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 404


# ============================================================================
# get_image_file
# ============================================================================


async def test_get_image_file_missing(mock_db, no_group):
    img = fake_image(file_path="/nonexistent/orig.tif")
    mock_db.execute.return_value = make_result(scalar=img)
    with patch("routers.images.decode_token", return_value=token_payload()):
        with pytest.raises(HTTPException) as exc:
            await r.get_image_file(100, type="original", token="t", db=mock_db)
    assert exc.value.status_code == 404


async def test_get_image_file_mip(mock_db, no_group, tmp_path):
    p = tmp_path / "mip.png"
    p.write_bytes(b"x")
    img = fake_image(mip_path=str(p))
    mock_db.execute.return_value = make_result(scalar=img)
    with patch("routers.images.decode_token", return_value=token_payload()):
        out = await r.get_image_file(100, type="mip", token="t", db=mock_db)
    assert out.__class__.__name__ == "FileResponse"


async def test_get_image_file_sum(mock_db, no_group, tmp_path):
    p = tmp_path / "sum.png"
    p.write_bytes(b"x")
    img = fake_image(sum_path=str(p))
    mock_db.execute.return_value = make_result(scalar=img)
    with patch("routers.images.decode_token", return_value=token_payload()):
        out = await r.get_image_file(100, type="sum", token="t", db=mock_db)
    assert out.__class__.__name__ == "FileResponse"


async def test_get_image_file_thumbnail(mock_db, no_group, tmp_path):
    p = tmp_path / "thumb.png"
    p.write_bytes(b"x")
    img = fake_image(thumbnail_path=str(p))
    mock_db.execute.return_value = make_result(scalar=img)
    with patch("routers.images.decode_token", return_value=token_payload()):
        out = await r.get_image_file(100, type="thumbnail", token="t", db=mock_db)
    assert out.__class__.__name__ == "FileResponse"


async def test_get_image_file_original_fallback(mock_db, no_group, tmp_path):
    p = tmp_path / "orig.tif"
    from PIL import Image as PILImage
    PILImage.new("RGB", (4, 4)).save(str(p), format="TIFF")
    # type=mip but no mip_path -> falls through to original file_path
    img = fake_image(file_path=str(p), mip_path=None)
    mock_db.execute.return_value = make_result(scalar=img)
    with patch("routers.images.decode_token", return_value=token_payload()):
        out = await r.get_image_file(100, type="mip", token="t", db=mock_db)
    assert out.media_type == "image/png"


# ============================================================================
# delete_image
# ============================================================================


async def test_delete_image_success(mock_db, no_group):
    img = fake_image(owner_id=1)
    mock_db.execute.return_value = make_result(scalar=img)
    with patch("routers.images.safe_remove_file", return_value=True) as srf:
        await r.delete_image(100, current_user=fake_user(), db=mock_db)
    assert srf.call_count == 4
    mock_db.delete.assert_awaited_with(img)
    mock_db.commit.assert_awaited()


async def test_delete_image_not_owner(mock_db, no_group):
    img = fake_image(owner_id=2)
    mock_db.execute.return_value = make_result(scalar=img)
    with pytest.raises(HTTPException) as exc:
        await r.delete_image(100, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 403


# ============================================================================
# reprocess_image
# ============================================================================


async def test_reprocess_image_source_discarded(mock_db, no_group):
    img = fake_image(owner_id=1, source_discarded=True)
    mock_db.execute.return_value = make_result(scalar=img)
    bt = BackgroundTasks()
    with pytest.raises(HTTPException) as exc:
        await r.reprocess_image(
            100, bt, detect_cells=None, current_user=fake_user(), db=mock_db
        )
    assert exc.value.status_code == 400


async def test_reprocess_image_success_default_detect(mock_db, no_group):
    img = fake_image(owner_id=1, detect_cells=True)
    reloaded = fake_image(owner_id=1)
    reload_result = make_result()
    reload_result.scalar_one.return_value = reloaded
    mock_db.execute.side_effect = [
        make_result(scalar=img),  # get_image_for_write
        make_result(rowcount=2),  # delete crops
        reload_result,  # reload via scalar_one()
    ]
    bt = BackgroundTasks()
    out = await r.reprocess_image(
        100, bt, detect_cells=None, current_user=fake_user(), db=mock_db
    )
    assert out.id == reloaded.id
    assert len(bt.tasks) == 1


async def test_reprocess_image_override_detect(mock_db, no_group):
    img = fake_image(owner_id=1, detect_cells=True)
    reloaded = fake_image(owner_id=1)
    reload_result = make_result()
    reload_result.scalar_one.return_value = reloaded
    mock_db.execute.side_effect = [
        make_result(scalar=img),
        make_result(rowcount=0),
        reload_result,
    ]
    bt = BackgroundTasks()
    out = await r.reprocess_image(
        100, bt, detect_cells=False, current_user=fake_user(), db=mock_db
    )
    assert img.detect_cells is False
    assert img.status == UploadStatus.PROCESSING


async def test_reprocess_image_not_owner(mock_db, no_group):
    img = fake_image(owner_id=2)
    mock_db.execute.return_value = make_result(scalar=img)
    bt = BackgroundTasks()
    with pytest.raises(HTTPException) as exc:
        await r.reprocess_image(
            100, bt, detect_cells=None, current_user=fake_user(), db=mock_db
        )
    assert exc.value.status_code == 403


# ============================================================================
# batch_redetect_cells
# ============================================================================


async def test_batch_redetect_empty(mock_db):
    req = r.BatchRedetectRequest(image_ids=[])
    bt = BackgroundTasks()
    with pytest.raises(HTTPException) as exc:
        await r.batch_redetect_cells(req, bt, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 400


async def test_batch_redetect_missing(mock_db, no_group):
    req = r.BatchRedetectRequest(image_ids=[1, 2])
    bt = BackgroundTasks()
    mock_db.execute.return_value = make_result(scalars_all=[fake_image(image_id=1)])
    with pytest.raises(HTTPException) as exc:
        await r.batch_redetect_cells(req, bt, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 404


async def test_batch_redetect_not_owner(mock_db, no_group):
    req = r.BatchRedetectRequest(image_ids=[1])
    bt = BackgroundTasks()
    mock_db.execute.return_value = make_result(
        scalars_all=[fake_image(image_id=1, owner_id=2)]
    )
    with pytest.raises(HTTPException) as exc:
        await r.batch_redetect_cells(req, bt, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 403


async def test_batch_redetect_success_and_skip(mock_db, no_group):
    req = r.BatchRedetectRequest(image_ids=[1, 2])
    bt = BackgroundTasks()
    ok = fake_image(image_id=1, owner_id=1, source_discarded=False)
    skipped = fake_image(image_id=2, owner_id=1, source_discarded=True)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[ok, skipped]),  # fetch images
        make_result(rowcount=1),  # delete crops for ok
    ]
    out = await r.batch_redetect_cells(req, bt, current_user=fake_user(), db=mock_db)
    assert out.processed_count == 1  # skipped one
    assert len(bt.tasks) == 1
