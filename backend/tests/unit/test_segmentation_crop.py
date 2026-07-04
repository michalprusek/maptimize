"""In-process unit tests for segmentation_service and crop_editor_service.

All GPU/ML calls (SAM encoders/decoders, feature extraction) are patched at the
call boundary in the service module; DB is the AsyncMock ``mock_db`` fixture.
Polygon helpers and numpy/PIL run for real on small synthetic arrays.
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from PIL import Image as PILImage

import services.segmentation_service as seg
import services.crop_editor_service as crop_svc

from tests.unit.conftest import make_result


# ============================================================================
# Helpers
# ============================================================================


def _row_result(row):
    """Build a result whose ``.one_or_none()`` returns ``row`` (a tuple or None)."""
    result = MagicMock(name="RowResult")
    result.one_or_none.return_value = row
    return result


@asynccontextmanager
async def _sync_executor():
    """Run ``loop.run_in_executor`` callables inline so coverage traces the
    closures (the real threadpool worker thread is not traced by ctrace)."""
    loop = asyncio.get_running_loop()
    orig = loop.run_in_executor

    async def _inline(_executor, func, *args):
        return func(*args)

    loop.run_in_executor = _inline
    try:
        yield
    finally:
        loop.run_in_executor = orig


def _make_mask(square=True):
    """Small real binary mask: a filled rectangle big enough to yield a polygon."""
    mask = np.zeros((40, 40), dtype=bool)
    mask[5:35, 5:35] = True
    if not square:
        # carve a hole in the centre -> ring shape
        mask[15:25, 15:25] = False
    return mask


# ============================================================================
# _normalize_polygon_response
# ============================================================================


def test_normalize_polygon_response_with_holes():
    data = {"outer": [[0, 0], [1, 0], [1, 1]], "holes": [[[0, 0]]]}
    simple, with_holes, has_holes = seg._normalize_polygon_response(data)
    assert simple == data["outer"]
    assert with_holes is data
    assert has_holes is True


def test_normalize_polygon_response_with_holes_empty():
    data = {"outer": [[0, 0], [1, 0]], "holes": []}
    simple, with_holes, has_holes = seg._normalize_polygon_response(data)
    assert simple == data["outer"]
    assert has_holes is False


def test_normalize_polygon_response_legacy():
    data = [[0, 0], [1, 0], [1, 1]]
    simple, with_holes, has_holes = seg._normalize_polygon_response(data)
    assert simple == data
    assert with_holes == {"outer": data, "holes": []}
    assert has_holes is False


# ============================================================================
# categorize_segmentation_error
# ============================================================================


@pytest.mark.parametrize(
    "msg,expected_type",
    [
        ("CUDA out of memory", "gpu_oom"),
        ("OOM detected on gpu", "gpu_oom"),
        ("CUDA driver fault", "gpu_error"),
        ("gpu kernel crashed", "gpu_error"),
        ("operation timeout", "timeout"),
        ("connection refused", "network"),
        ("something weird", "unknown"),
    ],
)
def test_categorize_segmentation_error(msg, expected_type):
    etype, emsg = seg.categorize_segmentation_error(Exception(msg))
    assert etype == expected_type
    assert isinstance(emsg, str)


# ============================================================================
# compute_sam_embedding
# ============================================================================


async def test_compute_sam_embedding_image_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await seg.compute_sam_embedding(1, mock_db)
    assert out == {"success": False, "error": "Image not found"}


async def test_compute_sam_embedding_no_source(mock_db):
    image = MagicMock(mip_path=None, file_path=None)
    mock_db.execute.return_value = make_result(scalar=image)
    out = await seg.compute_sam_embedding(1, mock_db)
    assert out["success"] is False
    assert "No image file" in out["error"]


async def test_compute_sam_embedding_success_with_existing(mock_db):
    image = MagicMock(mip_path="/tmp/mip.png", file_path="/tmp/f.png")
    existing_emb = MagicMock(name="existing")
    # image lookup, then existing-embedding lookup
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=existing_emb),
    ]
    encoder = MagicMock()
    emb = np.zeros((1, 256, 64, 64), dtype=np.float32)
    encoder.encode_image.return_value = (emb, 1024, 768)
    encoder.compress_embedding.return_value = b"x" * 2048
    encoder.model_name = "mobile_sam"

    with patch("ml.segmentation.sam_encoder.get_sam_encoder", return_value=encoder):
        out = await seg.compute_sam_embedding(7, mock_db)

    assert out["success"] is True
    assert out["embedding_size"] == 2048
    assert out["image_shape"] == (1024, 768)
    assert image.sam_embedding_status == "ready"
    mock_db.delete.assert_awaited_once_with(existing_emb)
    mock_db.add.assert_called_once()


async def test_compute_sam_embedding_success_no_existing(mock_db):
    image = MagicMock(mip_path="/tmp/mip.png", file_path="/tmp/f.png")
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=None),  # no existing embedding
    ]
    encoder = MagicMock()
    encoder.encode_image.return_value = (np.zeros((1, 4, 4), dtype=np.float32), 100, 50)
    encoder.compress_embedding.return_value = b"y" * 1024
    encoder.model_name = "mobile_sam"

    with patch("ml.segmentation.sam_encoder.get_sam_encoder", return_value=encoder):
        out = await seg.compute_sam_embedding(8, mock_db)

    assert out["success"] is True
    mock_db.delete.assert_not_awaited()


async def test_compute_sam_embedding_encoder_raises(mock_db):
    image = MagicMock(mip_path="/tmp/mip.png", file_path="/tmp/f.png")
    mock_db.execute.return_value = make_result(scalar=image)
    encoder = MagicMock()
    encoder.encode_image.side_effect = RuntimeError("CUDA out of memory")

    with patch("ml.segmentation.sam_encoder.get_sam_encoder", return_value=encoder):
        out = await seg.compute_sam_embedding(9, mock_db)

    assert out["success"] is False
    assert out["error_type"] == "gpu_oom"
    assert image.sam_embedding_status == "error"


# ============================================================================
# get_embedding_status
# ============================================================================


async def test_get_embedding_status_not_found(mock_db):
    mock_db.execute.return_value = _row_result(None)
    out = await seg.get_embedding_status(1, mock_db)
    assert out == {"status": "not_found", "has_embedding": False}


async def test_get_embedding_status_with_embedding(mock_db):
    image = MagicMock(sam_embedding_status="ready")
    emb = MagicMock(embedding_shape="1,256,64,64", model_variant="mobile_sam")
    mock_db.execute.return_value = _row_result((image, emb))
    out = await seg.get_embedding_status(3, mock_db)
    assert out["has_embedding"] is True
    assert out["status"] == "ready"
    assert out["embedding_shape"] == "1,256,64,64"


async def test_get_embedding_status_no_embedding_default(mock_db):
    image = MagicMock(sam_embedding_status=None)
    mock_db.execute.return_value = _row_result((image, None))
    out = await seg.get_embedding_status(3, mock_db)
    assert out["has_embedding"] is False
    assert out["status"] == "not_started"
    assert out["embedding_shape"] is None
    assert out["model_variant"] is None


# ============================================================================
# segment_from_prompts
# ============================================================================


async def test_segment_from_prompts_not_found(mock_db):
    mock_db.execute.return_value = _row_result(None)
    out = await seg.segment_from_prompts(1, [(5, 5)], [1], mock_db)
    assert out == {"success": False, "error": "Image or embedding not found"}


async def test_segment_from_prompts_success(mock_db):
    image = MagicMock()
    emb = MagicMock(embedding_shape="1,4,4", original_height=40, original_width=40)
    mock_db.execute.return_value = _row_result((image, emb))

    encoder = MagicMock()
    encoder.decompress_embedding.return_value = np.zeros((1, 4, 4), dtype=np.float32)
    decoder = MagicMock()
    mask = _make_mask(square=True)
    decoder.predict_mask.return_value = (mask, 0.95, None)
    # cv2 is mocked in conftest, so patch the polygon helper used by the service.
    poly = {"outer": [[5, 5], [34, 5], [34, 34], [5, 34]], "holes": []}

    async with _sync_executor():
        with patch("ml.segmentation.sam_encoder.get_sam_encoder", return_value=encoder), \
             patch("ml.segmentation.sam_decoder.get_sam_decoder", return_value=decoder), \
             patch.object(seg, "mask_to_polygon_with_holes", return_value=poly):
            out = await seg.segment_from_prompts(5, [(10, 10)], [1], mock_db, multimask_output=True)

    assert out["success"] is True
    assert out["iou_score"] == pytest.approx(0.95)
    assert out["area_pixels"] == int(np.sum(mask))
    assert out["polygon"] == poly["outer"]
    assert out["mask_shape"] == [40, 40]
    assert out["has_holes"] is False


async def test_segment_from_prompts_ring_has_holes(mock_db):
    image = MagicMock()
    emb = MagicMock(embedding_shape="1,4,4", original_height=40, original_width=40)
    mock_db.execute.return_value = _row_result((image, emb))
    encoder = MagicMock()
    encoder.decompress_embedding.return_value = np.zeros((1, 4, 4), dtype=np.float32)
    decoder = MagicMock()
    decoder.predict_mask.return_value = (_make_mask(square=False), 0.8, None)
    poly = {"outer": [[5, 5], [34, 5], [34, 34]], "holes": [[[15, 15], [24, 15], [24, 24]]]}

    with patch("ml.segmentation.sam_encoder.get_sam_encoder", return_value=encoder), \
         patch("ml.segmentation.sam_decoder.get_sam_decoder", return_value=decoder), \
         patch.object(seg, "mask_to_polygon_with_holes", return_value=poly):
        out = await seg.segment_from_prompts(5, [(10, 10)], [1], mock_db)

    assert out["success"] is True
    assert out["has_holes"] is True


# ============================================================================
# save_segmentation_mask
# ============================================================================


async def test_save_segmentation_mask_crop_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await seg.save_segmentation_mask(1, [[0, 0]], 0.9, 1, mock_db)
    assert out == {"success": False, "error": "Crop not found"}


async def test_save_segmentation_mask_legacy_new(mock_db):
    crop = MagicMock()
    mock_db.execute.side_effect = [
        make_result(scalar=crop),     # crop lookup
        make_result(scalar=None),     # no existing mask
    ]
    polygon = [[0, 0], [10, 0], [10, 10], [0, 10]]
    out = await seg.save_segmentation_mask(2, polygon, 0.91, 3, mock_db)
    assert out["success"] is True
    assert out["crop_id"] == 2
    assert out["has_holes"] is False
    mock_db.add.assert_called_once()


async def test_save_segmentation_mask_with_holes_existing(mock_db):
    crop = MagicMock()
    existing_mask = MagicMock()
    mock_db.execute.side_effect = [
        make_result(scalar=crop),
        make_result(scalar=existing_mask),
    ]
    polygon = {
        "outer": [[0, 0], [10, 0], [10, 10], [0, 10]],
        "holes": [[[3, 3], [6, 3], [6, 6], [3, 6]]],
    }
    out = await seg.save_segmentation_mask(3, polygon, 0.8, 2, mock_db, creation_method="manual")
    assert out["success"] is True
    assert out["has_holes"] is True
    assert existing_mask.polygon_points == polygon
    assert existing_mask.creation_method == "manual"
    mock_db.add.assert_not_called()


# ============================================================================
# get_segmentation_mask / batch
# ============================================================================


async def test_get_segmentation_mask_none(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await seg.get_segmentation_mask(1, mock_db)
    assert out == {"has_mask": False}


async def test_get_segmentation_mask_found(mock_db):
    mask = MagicMock(
        polygon_points=[[0, 0], [1, 0], [1, 1]],
        iou_score=0.9,
        area_pixels=123,
        creation_method="interactive",
        prompt_count=2,
    )
    mock_db.execute.return_value = make_result(scalar=mask)
    out = await seg.get_segmentation_mask(2, mock_db)
    assert out["has_mask"] is True
    assert out["polygon"] == [[0, 0], [1, 0], [1, 1]]
    assert out["area_pixels"] == 123


async def test_get_segmentation_masks_batch_empty():
    assert await seg.get_segmentation_masks_batch([], AsyncMock()) == {}


async def test_get_segmentation_masks_batch_found(mock_db):
    m1 = MagicMock(
        cell_crop_id=10,
        polygon_points=[[0, 0], [1, 0], [1, 1]],
        iou_score=0.5,
        area_pixels=10,
        creation_method="interactive",
    )
    m2 = MagicMock(
        cell_crop_id=11,
        polygon_points={"outer": [[0, 0]], "holes": [[[1, 1]]]},
        iou_score=0.7,
        area_pixels=20,
        creation_method="manual",
    )
    mock_db.execute.return_value = make_result(scalars_all=[m1, m2])
    out = await seg.get_segmentation_masks_batch([10, 11], mock_db)
    assert set(out.keys()) == {10, 11}
    assert out[10]["has_holes"] is False
    assert out[11]["has_holes"] is True


# ============================================================================
# delete_segmentation_mask
# ============================================================================


async def test_delete_segmentation_mask_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await seg.delete_segmentation_mask(1, mock_db)
    assert out == {"success": False, "error": "Mask not found"}


async def test_delete_segmentation_mask_success(mock_db):
    mask = MagicMock()
    mock_db.execute.return_value = make_result(scalar=mask)
    out = await seg.delete_segmentation_mask(4, mock_db)
    assert out == {"success": True, "crop_id": 4}
    mock_db.delete.assert_awaited_once_with(mask)


# ============================================================================
# save_user_prompt
# ============================================================================


async def test_save_user_prompt_with_polygon(mock_db):
    created = {}

    def _add(obj):
        obj.id = 99
        created["obj"] = obj

    mock_db.add.side_effect = _add
    out = await seg.save_user_prompt(
        user_id=1,
        image_id=2,
        click_points=[{"x": 1, "y": 2, "label": 1}],
        result_polygon=[(1, 2), (3, 4)],
        db=mock_db,
        experiment_id=5,
        crop_id=6,
        name="exemplar",
    )
    assert out == {"success": True, "prompt_id": 99}
    assert created["obj"].result_polygon == [[1, 2], [3, 4]]


async def test_save_user_prompt_no_polygon(mock_db):
    def _add(obj):
        obj.id = 1
    mock_db.add.side_effect = _add
    out = await seg.save_user_prompt(1, 2, [], None, mock_db)
    assert out["success"] is True


# ============================================================================
# queue_sam_embedding
# ============================================================================


def _db_context(db):
    @asynccontextmanager
    async def _ctx():
        yield db
    return _ctx


async def test_queue_sam_embedding_success(mock_db):
    image = MagicMock(sam_embedding_status=None)
    mock_db.execute.return_value = make_result(scalar=image)

    with patch("database.get_db_context", _db_context(mock_db)), \
         patch.object(seg, "compute_sam_embedding", new=AsyncMock(return_value={"success": True})) as comp:
        await seg.queue_sam_embedding(42)

    assert image.sam_embedding_status == "pending"
    comp.assert_awaited_once()


async def test_queue_sam_embedding_exception_then_marks_error(mock_db):
    image = MagicMock(sam_embedding_status="computing")
    mock_db.execute.return_value = make_result(scalar=image)

    # First context: compute raises; subsequent retry context marks error.
    with patch("database.get_db_context", _db_context(mock_db)), \
         patch.object(seg, "compute_sam_embedding",
                      new=AsyncMock(side_effect=RuntimeError("CUDA error"))):
        await seg.queue_sam_embedding(43)

    # Retry loop set status to error and broke out
    assert image.sam_embedding_status == "error"


async def test_queue_sam_embedding_retry_exhausts(mock_db):
    # get_db_context itself raises every time after the first compute failure.
    calls = {"n": 0}

    @asynccontextmanager
    async def _flaky_ctx():
        calls["n"] += 1
        if calls["n"] == 1:
            # first context used for compute -> compute raises inside
            yield mock_db
        else:
            raise RuntimeError("network connection lost")

    with patch("database.get_db_context", _flaky_ctx), \
         patch.object(seg, "compute_sam_embedding",
                      new=AsyncMock(side_effect=RuntimeError("network connection lost"))), \
         patch.object(seg.asyncio, "sleep", new=AsyncMock()):
        # Should not raise despite all retries failing
        await seg.queue_sam_embedding(44)

    assert calls["n"] >= 4  # 1 compute + 3 retries


# ============================================================================
# save_fov_segmentation_mask
# ============================================================================


async def test_save_fov_mask_image_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await seg.save_fov_segmentation_mask(1, [(0, 0)], 0.9, 1, mock_db)
    assert out == {"success": False, "error": "Image not found"}


async def test_save_fov_mask_new(mock_db):
    image = MagicMock()
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=None),
    ]
    out = await seg.save_fov_segmentation_mask(2, [(0, 0), (10, 0), (10, 10)], 0.9, 1, mock_db)
    assert out["success"] is True
    assert out["image_id"] == 2
    mock_db.add.assert_called_once()


async def test_save_fov_mask_existing(mock_db):
    image = MagicMock()
    existing = MagicMock()
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=existing),
    ]
    out = await seg.save_fov_segmentation_mask(3, [(0, 0), (10, 0), (10, 10)], 0.5, 4, mock_db, "auto")
    assert out["success"] is True
    assert existing.creation_method == "auto"
    assert existing.polygon_points == [[0, 0], [10, 0], [10, 10]]
    mock_db.add.assert_not_called()


# ============================================================================
# save_fov_segmentation_mask_union
# ============================================================================


async def test_save_fov_union_image_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await seg.save_fov_segmentation_mask_union(1, [[(0, 0)]], 0.9, 1, mock_db)
    assert out == {"success": False, "error": "Image not found"}


async def test_save_fov_union_no_valid_polygons(mock_db):
    image = MagicMock()
    mock_db.execute.return_value = make_result(scalar=image)
    out = await seg.save_fov_segmentation_mask_union(2, [[(0, 0), (1, 1)]], 0.9, 1, mock_db)
    assert out == {"success": False, "error": "No valid polygons provided"}


async def test_save_fov_union_new(mock_db):
    image = MagicMock()
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=None),
    ]
    polys = [[(0, 0), (10, 0), (10, 10)], [(20, 20), (30, 20), (30, 30)]]
    out = await seg.save_fov_segmentation_mask_union(3, polys, 0.7, 2, mock_db)
    assert out["success"] is True
    assert out["polygon_count"] == 2
    mock_db.add.assert_called_once()


async def test_save_fov_union_existing_merges(mock_db):
    image = MagicMock()
    existing = MagicMock(polygon_points=[[[0, 0], [5, 0], [5, 5]]])
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=existing),
    ]
    polys = [[(10, 10), (20, 10), (20, 20)]]
    out = await seg.save_fov_segmentation_mask_union(4, polys, 0.6, 1, mock_db)
    assert out["success"] is True
    assert out["polygon_count"] == 2  # 1 existing + 1 new
    mock_db.add.assert_not_called()


async def test_save_fov_union_value_error(mock_db):
    image = MagicMock()
    mock_db.execute.return_value = make_result(scalar=image)
    # polygons with bad point structure -> TypeError on int(p[0]) inside comprehension
    polys = [[("a", "b"), ("c", "d"), ("e", "f")]]
    out = await seg.save_fov_segmentation_mask_union(5, polys, 0.5, 1, mock_db)
    assert out["success"] is False
    assert out["error_type"] == "validation"


async def test_save_fov_union_generic_exception(mock_db):
    image = MagicMock()
    # First execute returns image; second (existing lookup) raises a non-validation error
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        RuntimeError("CUDA failure"),
    ]
    polys = [[(0, 0), (10, 0), (10, 10)]]
    out = await seg.save_fov_segmentation_mask_union(6, polys, 0.5, 1, mock_db)
    assert out["success"] is False
    assert out["error_type"] == "gpu_error"


# ============================================================================
# get_fov_segmentation_mask / delete
# ============================================================================


async def test_get_fov_mask_none(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await seg.get_fov_segmentation_mask(1, mock_db)
    assert out == {"has_mask": False}


async def test_get_fov_mask_found(mock_db):
    mask = MagicMock(
        polygon_points=[[0, 0], [1, 1]],
        iou_score=0.9,
        area_pixels=5,
        creation_method="interactive",
        prompt_count=1,
    )
    mock_db.execute.return_value = make_result(scalar=mask)
    out = await seg.get_fov_segmentation_mask(2, mock_db)
    assert out["has_mask"] is True
    assert out["polygon"] == [[0, 0], [1, 1]]


async def test_delete_fov_mask_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await seg.delete_fov_segmentation_mask(1, mock_db)
    assert out == {"success": False, "error": "FOV mask not found"}


async def test_delete_fov_mask_success(mock_db):
    mask = MagicMock()
    mock_db.execute.return_value = make_result(scalar=mask)
    out = await seg.delete_fov_segmentation_mask(9, mock_db)
    assert out == {"success": True, "image_id": 9}
    mock_db.delete.assert_awaited_once_with(mask)


# ============================================================================
# get_segmentation_capabilities
# ============================================================================


def test_get_segmentation_capabilities():
    fake_caps = {"device": "cpu", "supports_text_prompts": False}
    with patch("ml.segmentation.sam_factory.get_capabilities", return_value=fake_caps):
        assert seg.get_segmentation_capabilities() == fake_caps


# ============================================================================
# segment_from_text
# ============================================================================


async def test_segment_from_text_unavailable(mock_db):
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=False), \
         patch("ml.segmentation.sam_factory.detect_device", return_value="cpu"):
        out = await seg.segment_from_text(1, "cell", 0.5, mock_db)
    assert out["success"] is False
    assert "CUDA GPU" in out["error"]


async def test_segment_from_text_image_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True), \
         patch("ml.segmentation.sam_factory.detect_device", return_value="cuda"):
        out = await seg.segment_from_text(1, "cell", 0.5, mock_db)
    assert out == {"success": False, "error": "Image not found"}


async def test_segment_from_text_no_source(mock_db):
    image = MagicMock(mip_path=None, file_path=None)
    mock_db.execute.return_value = make_result(scalar=image)
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True), \
         patch("ml.segmentation.sam_factory.detect_device", return_value="cuda"):
        out = await seg.segment_from_text(1, "cell", 0.5, mock_db)
    assert out["success"] is False
    assert "No image file" in out["error"]


async def test_segment_from_text_success(mock_db):
    image = MagicMock(mip_path="/tmp/mip.png", file_path="/tmp/f.png")
    mock_db.execute.return_value = make_result(scalar=image)
    encoder = MagicMock()
    encoder.predict_with_text.return_value = {
        "success": True,
        "polygons": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]],
        "boxes": [[0, 0, 1, 1]],          # second box missing -> default
        "scores": [0.9],                  # second score missing -> default
        "areas": [10],                    # second area missing -> default
    }
    sam3_mod = MagicMock()
    sam3_mod.get_sam3_encoder.return_value = encoder
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True), \
         patch("ml.segmentation.sam_factory.detect_device", return_value="cuda"), \
         patch.dict("sys.modules", {"ml.segmentation.sam3_encoder": sam3_mod}):
        out = await seg.segment_from_text(5, "cell", 0.5, mock_db)
    assert out["success"] is True
    assert len(out["instances"]) == 2
    assert out["instances"][1]["bbox"] == [0, 0, 0, 0]
    assert out["instances"][1]["score"] == 0.0
    assert out["instances"][1]["area_pixels"] == 0


async def test_segment_from_text_inference_failure(mock_db):
    image = MagicMock(mip_path="/tmp/mip.png", file_path=None)
    mock_db.execute.return_value = make_result(scalar=image)
    encoder = MagicMock()
    encoder.predict_with_text.return_value = {"success": False, "error": "model load failed"}
    sam3_mod = MagicMock()
    sam3_mod.get_sam3_encoder.return_value = encoder
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True), \
         patch("ml.segmentation.sam_factory.detect_device", return_value="cuda"), \
         patch.dict("sys.modules", {"ml.segmentation.sam3_encoder": sam3_mod}):
        out = await seg.segment_from_text(5, "cell", 0.5, mock_db)
    assert out == {"success": False, "error": "model load failed"}


async def test_segment_from_text_exception(mock_db):
    image = MagicMock(mip_path="/tmp/mip.png", file_path=None)
    mock_db.execute.return_value = make_result(scalar=image)
    encoder = MagicMock()
    encoder.predict_with_text.side_effect = RuntimeError("connection lost")
    sam3_mod = MagicMock()
    sam3_mod.get_sam3_encoder.return_value = encoder
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True), \
         patch("ml.segmentation.sam_factory.detect_device", return_value="cuda"), \
         patch.dict("sys.modules", {"ml.segmentation.sam3_encoder": sam3_mod}):
        out = await seg.segment_from_text(5, "cell", 0.5, mock_db)
    assert out["success"] is False
    assert out["error_type"] == "network"


# ============================================================================
# refine_text_segmentation
# ============================================================================


async def test_refine_text_unavailable(mock_db):
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=False):
        out = await seg.refine_text_segmentation(1, "cell", 0, [(1, 1)], [1], mock_db)
    assert out == {"success": False, "error": "Text segmentation not available"}


async def test_refine_text_image_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True):
        out = await seg.refine_text_segmentation(1, "cell", 0, [(1, 1)], [1], mock_db)
    assert out == {"success": False, "error": "Image not found"}


async def test_refine_text_no_source(mock_db):
    image = MagicMock(mip_path=None, file_path=None)
    mock_db.execute.return_value = make_result(scalar=image)
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True):
        out = await seg.refine_text_segmentation(1, "cell", 0, [(1, 1)], [1], mock_db)
    assert out["success"] is False
    assert "No image file" in out["error"]


async def test_refine_text_success(mock_db):
    image = MagicMock(mip_path="/tmp/mip.png", file_path=None)
    mock_db.execute.return_value = make_result(scalar=image)
    encoder = MagicMock()
    encoder.refine_with_points.return_value = {
        "success": True, "polygon": [[0, 0], [1, 1]], "score": 0.88, "area": 50,
    }
    sam3_mod = MagicMock()
    sam3_mod.get_sam3_encoder.return_value = encoder
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True), \
         patch.dict("sys.modules", {"ml.segmentation.sam3_encoder": sam3_mod}):
        out = await seg.refine_text_segmentation(5, "cell", 0, [(1, 1)], [1], mock_db)
    assert out["success"] is True
    assert out["iou_score"] == 0.88
    assert out["area_pixels"] == 50


async def test_refine_text_inference_failure(mock_db):
    image = MagicMock(mip_path="/tmp/mip.png", file_path=None)
    mock_db.execute.return_value = make_result(scalar=image)
    encoder = MagicMock()
    encoder.refine_with_points.return_value = {"success": False}
    sam3_mod = MagicMock()
    sam3_mod.get_sam3_encoder.return_value = encoder
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True), \
         patch.dict("sys.modules", {"ml.segmentation.sam3_encoder": sam3_mod}):
        out = await seg.refine_text_segmentation(5, "cell", 0, [(1, 1)], [1], mock_db)
    assert out == {"success": False, "error": "Refinement failed"}


async def test_refine_text_exception(mock_db):
    image = MagicMock(mip_path="/tmp/mip.png", file_path=None)
    mock_db.execute.return_value = make_result(scalar=image)
    encoder = MagicMock()
    encoder.refine_with_points.side_effect = RuntimeError("timeout reached")
    sam3_mod = MagicMock()
    sam3_mod.get_sam3_encoder.return_value = encoder
    with patch("ml.segmentation.sam_factory.text_segmentation_available", return_value=True), \
         patch.dict("sys.modules", {"ml.segmentation.sam3_encoder": sam3_mod}):
        out = await seg.refine_text_segmentation(5, "cell", 0, [(1, 1)], [1], mock_db)
    assert out["success"] is False
    assert out["error_type"] == "timeout"


# ============================================================================
# crop_editor_service: validate_bbox_within_image
# ============================================================================


@pytest.mark.parametrize(
    "args,valid,fragment",
    [
        ((-1, 0, 50, 50, 100, 100), False, "negative"),
        ((0, -5, 50, 50, 100, 100), False, "negative"),
        ((60, 0, 50, 50, 100, 100), False, "width"),
        ((0, 60, 50, 50, 100, 100), False, "height"),
        ((0, 0, 5, 50, 100, 100), False, "at least 10"),
        ((0, 0, 50, 5, 100, 100), False, "at least 10"),
        ((0, 0, 50, 50, 100, 100), True, None),
    ],
)
def test_validate_bbox(args, valid, fragment):
    ok, err = crop_svc.validate_bbox_within_image(*args)
    assert ok is valid
    if fragment:
        assert fragment in err
    else:
        assert err is None


# ============================================================================
# extract_crop_from_projection / save_crop_image / delete_crop_files
# ============================================================================


def test_extract_crop_from_projection():
    proj = np.arange(100).reshape(10, 10)
    out = crop_svc.extract_crop_from_projection(proj, 2, 3, 4, 5)
    assert out.shape == (5, 4)
    assert np.array_equal(out, proj[3:8, 2:6])


def test_save_crop_image(tmp_path):
    pixels = (np.random.rand(20, 20) * 1000).astype(np.uint16)
    with patch.object(crop_svc, "normalize_image",
                      return_value=(pixels % 256).astype(np.uint8)):
        path = crop_svc.save_crop_image(pixels, tmp_path / "crops", 12, 34, "mip")
    assert path.exists()
    assert path.name == "cell_12_34_mip.png"


def test_delete_crop_files_existing(tmp_path):
    f1 = tmp_path / "a.png"
    f1.write_bytes(b"x")
    crop = MagicMock(mip_path=str(f1), sum_crop_path=None)
    crop_svc.delete_crop_files(crop)
    assert not f1.exists()


def test_delete_crop_files_missing_and_oserror(tmp_path):
    f1 = tmp_path / "exists.png"
    f1.write_bytes(b"x")
    crop = MagicMock(mip_path=str(f1), sum_crop_path=str(tmp_path / "nope.png"))
    with patch.object(crop_svc.Path, "unlink", side_effect=OSError("locked")):
        crop_svc.delete_crop_files(crop)  # should swallow OSError
    # file still there because unlink was patched to raise
    assert f1.exists()


# ============================================================================
# get_mip_source_path
# ============================================================================


def test_get_mip_source_path_uses_mip():
    image = MagicMock(mip_path="/data/mip.png", file_path="/data/f.png")
    with patch.object(crop_svc.Path, "exists", return_value=True):
        assert crop_svc.get_mip_source_path(image) == "/data/mip.png"


def test_get_mip_source_path_falls_back_to_file():
    image = MagicMock(mip_path=None, file_path="/data/f.png")
    with patch.object(crop_svc.Path, "exists", return_value=True):
        assert crop_svc.get_mip_source_path(image) == "/data/f.png"


def test_get_mip_source_path_none():
    image = MagicMock(mip_path=None, file_path="/data/f.png")
    with patch.object(crop_svc.Path, "exists", return_value=False):
        assert crop_svc.get_mip_source_path(image) is None


# ============================================================================
# regenerate_crop_features
# ============================================================================


def _write_png(path: Path, shape=(100, 100)):
    arr = (np.random.rand(*shape) * 255).astype(np.uint8)
    PILImage.fromarray(arr).save(path)
    return path


async def test_regenerate_no_source(mock_db):
    image = MagicMock()
    crop = MagicMock()
    with patch("services.umap_service.invalidate_crop_umap", new=AsyncMock()), \
         patch.object(crop_svc, "get_mip_source_path", return_value=None):
        out = await crop_svc.regenerate_crop_features(crop, image, mock_db)
    assert out == {"success": False, "error": "No MIP or source file available"}


async def test_regenerate_mip_load_fails(mock_db):
    image = MagicMock()
    crop = MagicMock()
    with patch("services.umap_service.invalidate_crop_umap", new=AsyncMock()), \
         patch.object(crop_svc, "get_mip_source_path", return_value="/bad/path.png"):
        out = await crop_svc.regenerate_crop_features(crop, image, mock_db)
    assert out["success"] is False
    assert "Failed to load MIP" in out["error"]


async def test_regenerate_invalid_bbox(mock_db, tmp_path):
    mip = _write_png(tmp_path / "mip.png", (100, 100))
    image = MagicMock(width=100, height=100, file_path=str(tmp_path / "f.png"))
    crop = MagicMock(bbox_x=90, bbox_y=0, bbox_w=50, bbox_h=50)  # exceeds width
    with patch("services.umap_service.invalidate_crop_umap", new=AsyncMock()), \
         patch.object(crop_svc, "get_mip_source_path", return_value=str(mip)):
        out = await crop_svc.regenerate_crop_features(crop, image, mock_db)
    assert out["success"] is False
    assert "width" in out["error"]


async def test_regenerate_success_with_sum(mock_db, tmp_path):
    mip = _write_png(tmp_path / "mip.png", (100, 100))
    sum_path = _write_png(tmp_path / "sum.png", (100, 100))
    image = MagicMock(
        width=100, height=100,
        file_path=str(tmp_path / "f.png"),
        sum_path=str(sum_path),
        id=7,
    )
    crop = MagicMock(bbox_x=10, bbox_y=10, bbox_w=30, bbox_h=30)
    with patch("services.umap_service.invalidate_crop_umap", new=AsyncMock()) as inv, \
         patch.object(crop_svc, "get_mip_source_path", return_value=str(mip)):
        out = await crop_svc.regenerate_crop_features(crop, image, mock_db)
    assert out["success"] is True
    assert out["needs_embedding"] is True
    assert out["umap_invalidated"] is True
    assert out["sum_crop_path"] is not None
    assert crop.embedding is None
    assert crop.umap_x is None
    inv.assert_awaited_once()


async def test_regenerate_sum_extract_fails(mock_db, tmp_path):
    mip = _write_png(tmp_path / "mip.png", (100, 100))
    # sum_path "exists" but PIL.open will fail because content invalid
    bad_sum = tmp_path / "sum.png"
    bad_sum.write_bytes(b"not a png")
    image = MagicMock(
        width=100, height=100,
        file_path=str(tmp_path / "f.png"),
        sum_path=str(bad_sum),
        id=8,
    )
    crop = MagicMock(bbox_x=10, bbox_y=10, bbox_w=30, bbox_h=30)
    with patch("services.umap_service.invalidate_crop_umap", new=AsyncMock()), \
         patch.object(crop_svc, "get_mip_source_path", return_value=str(mip)):
        out = await crop_svc.regenerate_crop_features(crop, image, mock_db)
    assert out["success"] is True
    assert out["sum_crop_path"] is None  # set to None after failure


async def test_regenerate_umap_invalidation_fails(mock_db, tmp_path):
    mip = _write_png(tmp_path / "mip.png", (100, 100))
    image = MagicMock(
        width=100, height=100,
        file_path=str(tmp_path / "f.png"),
        sum_path=None,
        id=9,
    )
    crop = MagicMock(bbox_x=10, bbox_y=10, bbox_w=30, bbox_h=30)
    with patch("services.umap_service.invalidate_crop_umap",
               new=AsyncMock(side_effect=RuntimeError("db down"))), \
         patch.object(crop_svc, "get_mip_source_path", return_value=str(mip)):
        out = await crop_svc.regenerate_crop_features(crop, image, mock_db)
    assert out["success"] is True
    assert out["umap_invalidated"] is False


# ============================================================================
# create_manual_crop
# ============================================================================


async def test_create_manual_crop_invalid_bbox(mock_db):
    image = MagicMock(width=100, height=100)
    crop, err = await crop_svc.create_manual_crop(image, 0, 0, 5, 5, mock_db)
    assert crop is None
    assert "at least 10" in err


async def test_create_manual_crop_no_source(mock_db):
    image = MagicMock(width=100, height=100)
    with patch.object(crop_svc, "get_mip_source_path", return_value=None):
        crop, err = await crop_svc.create_manual_crop(image, 0, 0, 30, 30, mock_db)
    assert crop is None
    assert err == "No MIP or source file available"


async def test_create_manual_crop_mip_load_fails(mock_db):
    image = MagicMock(width=100, height=100)
    with patch.object(crop_svc, "get_mip_source_path", return_value="/bad.png"):
        crop, err = await crop_svc.create_manual_crop(image, 0, 0, 30, 30, mock_db)
    assert crop is None
    assert "Failed to load MIP" in err


async def test_create_manual_crop_success_with_sum(mock_db, tmp_path):
    mip = _write_png(tmp_path / "mip.png", (100, 100))
    sum_path = _write_png(tmp_path / "sum.png", (100, 100))
    image = MagicMock(
        id=3, width=100, height=100,
        file_path=str(tmp_path / "f.png"),
        sum_path=str(sum_path),
        map_protein_id=11,
    )
    with patch.object(crop_svc, "get_mip_source_path", return_value=str(mip)):
        crop, err = await crop_svc.create_manual_crop(
            image, 10, 10, 30, 30, mock_db, map_protein_id=None
        )
    assert err is None
    assert crop is not None
    assert crop.map_protein_id == 11  # defaulted from image
    assert crop.sum_crop_path is not None
    mock_db.add.assert_called_once()


async def test_create_manual_crop_sum_fails(mock_db, tmp_path):
    mip = _write_png(tmp_path / "mip.png", (100, 100))
    bad_sum = tmp_path / "sum.png"
    bad_sum.write_bytes(b"garbage")
    image = MagicMock(
        id=3, width=100, height=100,
        file_path=str(tmp_path / "f.png"),
        sum_path=str(bad_sum),
        map_protein_id=11,
    )
    with patch.object(crop_svc, "get_mip_source_path", return_value=str(mip)):
        crop, err = await crop_svc.create_manual_crop(
            image, 10, 10, 30, 30, mock_db, map_protein_id=99
        )
    assert err is None
    assert crop.sum_crop_path is None
    assert crop.map_protein_id == 99  # explicit id wins


# ============================================================================
# verify_experiment_ownership / get_image_with_ownership_check / get_crop_...
# ============================================================================


async def test_verify_experiment_ownership_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=MagicMock())
    ok, err = await crop_svc.verify_experiment_ownership(1, 2, mock_db)
    assert ok is True and err is None


async def test_verify_experiment_ownership_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    ok, err = await crop_svc.verify_experiment_ownership(1, 2, mock_db)
    assert ok is False and err == "Experiment not found"


async def test_get_image_with_ownership_check_found(mock_db):
    img = MagicMock()
    mock_db.execute.return_value = make_result(scalar=img)
    out, err = await crop_svc.get_image_with_ownership_check(1, 2, mock_db)
    assert out is img and err is None


async def test_get_image_with_ownership_check_denied(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out, err = await crop_svc.get_image_with_ownership_check(1, 2, mock_db)
    assert out is None and "access denied" in err.lower()


async def test_get_crop_with_ownership_check_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    crop, img, err = await crop_svc.get_crop_with_ownership_check(1, 2, mock_db)
    assert crop is None and img is None and err == "Crop not found"


async def test_get_crop_with_ownership_check_access_denied(mock_db):
    crop = MagicMock()
    crop.image.experiment.user_id = 99
    mock_db.execute.return_value = make_result(scalar=crop)
    out_crop, img, err = await crop_svc.get_crop_with_ownership_check(1, 2, mock_db)
    assert out_crop is None and img is None and err == "Access denied"


async def test_get_crop_with_ownership_check_success(mock_db):
    crop = MagicMock()
    crop.image.experiment.user_id = 2
    mock_db.execute.return_value = make_result(scalar=crop)
    out_crop, img, err = await crop_svc.get_crop_with_ownership_check(1, 2, mock_db)
    assert out_crop is crop and img is crop.image and err is None


# ============================================================================
# truncate_error_message / update_crop_embedding_status / run_embedding_task
# ============================================================================


def test_truncate_error_message_short():
    assert crop_svc.truncate_error_message("short") == "short"


def test_truncate_error_message_long():
    long = "a" * 600
    out = crop_svc.truncate_error_message(long, max_length=500)
    assert len(out) == 500
    assert out.endswith("...")


async def test_update_crop_embedding_status_with_error(mock_db):
    await crop_svc.update_crop_embedding_status(mock_db, 5, "error", "boom")
    mock_db.execute.assert_awaited_once()
    mock_db.commit.assert_awaited_once()


async def test_update_crop_embedding_status_no_error(mock_db):
    await crop_svc.update_crop_embedding_status(mock_db, 5, "ready")
    mock_db.execute.assert_awaited_once()


async def test_run_embedding_extraction_task_success(mock_db):
    with patch("database.get_db_context", _db_context(mock_db)), \
         patch("ml.features.extract_features_for_crops", new=AsyncMock()) as extract:
        await crop_svc.run_embedding_extraction_task(7)
    extract.assert_awaited_once()
    # computing + ready -> two commits
    assert mock_db.commit.await_count == 2


async def test_run_embedding_extraction_task_failure(mock_db):
    with patch("database.get_db_context", _db_context(mock_db)), \
         patch("ml.features.extract_features_for_crops",
               new=AsyncMock(side_effect=RuntimeError("gpu oom"))):
        await crop_svc.run_embedding_extraction_task(8)
    # computing + error commits
    assert mock_db.commit.await_count == 2


async def test_run_embedding_extraction_task_failure_and_status_update_fails():
    # commit raises on the error-status update path -> inner except logged
    db = AsyncMock(name="db")
    commit_calls = {"n": 0}

    async def _commit():
        commit_calls["n"] += 1
        if commit_calls["n"] >= 2:  # the "error" status commit fails
            raise RuntimeError("db gone")

    db.commit = _commit
    db.execute = AsyncMock()

    with patch("database.get_db_context", _db_context(db)), \
         patch("ml.features.extract_features_for_crops",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        # Must not raise
        await crop_svc.run_embedding_extraction_task(9)
    assert commit_calls["n"] >= 2


async def test_run_embedding_extraction_task_propagates_system_exit(mock_db):
    with patch("database.get_db_context", _db_context(mock_db)), \
         patch("ml.features.extract_features_for_crops",
               new=AsyncMock(side_effect=KeyboardInterrupt())):
        with pytest.raises(KeyboardInterrupt):
            await crop_svc.run_embedding_extraction_task(10)
