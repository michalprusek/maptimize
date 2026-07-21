"""In-process unit tests for FastAPI router handlers.

These call the router endpoint coroutines DIRECTLY with their dependencies
injected as keyword arguments (``current_user``/``db``/etc.), bypassing the
ASGI/httpx layer. The services each router delegates to are patched at the
router-module boundary so no GPU / ML / Redis / filesystem work happens.

Routers covered: segmentation, rag, proteins, export_import, experiments,
bug_reports.

Conventions:
  * ``mock_db`` / ``make_result`` come from ``tests/unit/conftest.py``.
  * ``current_user`` is a ``SimpleNamespace`` mimicking the ``User`` model.
  * Service functions are replaced with ``AsyncMock``/``MagicMock`` so we only
    exercise the *router* logic (validation, error mapping, response shaping).
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.unit.conftest import make_result

import routers.segmentation as seg_r
import routers.rag as rag_r
import routers.proteins as prot_r
import routers.export_import as ei_r
import routers.experiments as exp_r
import routers.bug_reports as bug_r
from models.user import UserRole
from models.experiment import ExperimentStatus


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def user(id=1, role=UserRole.RESEARCHER, name="Alice", email="a@b.cz"):
    """A stand-in for the authenticated ``User`` model."""
    return SimpleNamespace(id=id, role=role, name=name, email=email)


def admin(id=2):
    return user(id=id, role=UserRole.ADMIN, name="Admin", email="admin@b.cz")


def _unique_result(rows):
    """A result whose ``.unique().all()`` returns ``rows`` (for list_experiments)."""
    res = MagicMock(name="UniqueResult")
    res.unique.return_value.all.return_value = rows
    return res


# ============================================================================ #
# segmentation router
# ============================================================================ #
async def test_seg_compute_embedding_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    bg = MagicMock()
    with pytest.raises(HTTPException) as e:
        await seg_r.compute_embedding(1, bg, current_user=user(), db=mock_db)
    assert e.value.status_code == 404
    bg.add_task.assert_not_called()


async def test_seg_compute_embedding_already_computing(mock_db):
    img = SimpleNamespace(sam_embedding_status="computing")
    mock_db.execute.return_value = make_result(scalar=img)
    bg = MagicMock()
    out = await seg_r.compute_embedding(5, bg, current_user=user(), db=mock_db)
    assert "already computing" in out["message"]
    bg.add_task.assert_not_called()


async def test_seg_compute_embedding_queues(mock_db):
    img = SimpleNamespace(sam_embedding_status="ready")
    mock_db.execute.return_value = make_result(scalar=img)
    bg = MagicMock()
    out = await seg_r.compute_embedding(5, bg, current_user=user(), db=mock_db)
    assert out["image_id"] == 5
    bg.add_task.assert_called_once()


async def test_seg_embedding_status_not_found(mock_db):
    with patch.object(seg_r.segmentation_service, "get_embedding_status",
                      new=AsyncMock(return_value={"status": "not_found",
                                                  "has_embedding": False})):
        with pytest.raises(HTTPException) as e:
            await seg_r.get_embedding_status(1, current_user=user(), db=mock_db)
    assert e.value.status_code == 404


async def test_seg_embedding_status_ok(mock_db):
    data = {"image_id": 3, "status": "ready", "has_embedding": True,
            "embedding_shape": "1,256,64,64", "model_variant": "mobile_sam"}
    with patch.object(seg_r.segmentation_service, "get_embedding_status",
                      new=AsyncMock(return_value=data)):
        resp = await seg_r.get_embedding_status(3, current_user=user(), db=mock_db)
    assert resp.status == "ready"
    assert resp.has_embedding is True


async def test_seg_segment_interactive_image_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    req = seg_r.SegmentRequest(image_id=1, points=[seg_r.ClickPoint(x=5, y=5, label=1)])
    resp = await seg_r.segment_interactive(req, current_user=user(), db=mock_db)
    assert resp.success is False
    assert resp.error == "Image not found"


async def test_seg_segment_interactive_clamps_and_succeeds(mock_db):
    img = SimpleNamespace(width=100, height=100)
    mock_db.execute.return_value = make_result(scalar=img)
    svc_out = {"success": True, "polygon": [[0, 0], [1, 1]],
               "iou_score": 0.9, "area_pixels": 42}
    with patch.object(seg_r.segmentation_service, "segment_from_prompts",
                      new=AsyncMock(return_value=svc_out)) as sp:
        # x beyond bounds gets clamped to width-1
        req = seg_r.SegmentRequest(
            image_id=2,
            points=[seg_r.ClickPoint(x=500, y=-3, label=1),
                    seg_r.ClickPoint(x=10, y=20, label=0)],
        )
        resp = await seg_r.segment_interactive(req, current_user=user(), db=mock_db)
    assert resp.success is True
    assert resp.area_pixels == 42
    coords = sp.await_args.kwargs["point_coords"]
    assert coords[0] == (99, 0)  # clamped
    assert sp.await_args.kwargs["point_labels"] == [1, 0]


async def test_seg_segment_interactive_default_dims(mock_db):
    # width/height None -> defaults to 2048
    img = SimpleNamespace(width=None, height=None)
    mock_db.execute.return_value = make_result(scalar=img)
    with patch.object(seg_r.segmentation_service, "segment_from_prompts",
                      new=AsyncMock(return_value={"success": False, "error": "no embedding"})):
        req = seg_r.SegmentRequest(image_id=2, points=[seg_r.ClickPoint(x=5, y=5, label=1)])
        resp = await seg_r.segment_interactive(req, current_user=user(), db=mock_db)
    assert resp.success is False
    assert resp.error == "no embedding"


async def test_seg_save_mask_success(mock_db):
    out = {"success": True, "crop_id": 7, "has_holes": False}
    with patch.object(seg_r.segmentation_service, "save_segmentation_mask",
                      new=AsyncMock(return_value=out)):
        req = seg_r.SaveMaskRequest(crop_id=7, polygon=[[0, 0], [1, 0], [1, 1]],
                                    iou_score=0.9, prompt_count=2)
        resp = await seg_r.save_mask(req, current_user=user(), db=mock_db)
    assert resp["crop_id"] == 7


async def test_seg_save_mask_failure(mock_db):
    with patch.object(seg_r.segmentation_service, "save_segmentation_mask",
                      new=AsyncMock(return_value={"success": False, "error": "Crop not found"})):
        req = seg_r.SaveMaskRequest(crop_id=7, polygon=[[0, 0], [1, 0], [1, 1]],
                                    iou_score=0.9, prompt_count=2)
        with pytest.raises(HTTPException) as e:
            await seg_r.save_mask(req, current_user=user(), db=mock_db)
    assert e.value.status_code == 400


async def test_seg_get_mask(mock_db):
    out = {"has_mask": True, "polygon": [[0, 0], [1, 1]], "iou_score": 0.9,
           "area_pixels": 5, "creation_method": "interactive", "prompt_count": 1}
    with patch.object(seg_r.segmentation_service, "get_segmentation_mask",
                      new=AsyncMock(return_value=out)):
        resp = await seg_r.get_mask(3, current_user=user(), db=mock_db)
    assert resp.has_mask is True


async def test_seg_masks_batch_invalid_format(mock_db):
    with pytest.raises(HTTPException) as e:
        await seg_r.get_masks_batch("1,foo,3", current_user=user(), db=mock_db)
    assert e.value.status_code == 400


async def test_seg_masks_batch_empty(mock_db):
    out = await seg_r.get_masks_batch("  , ", current_user=user(), db=mock_db)
    assert out == {"masks": {}}


async def test_seg_masks_batch_too_many(mock_db):
    ids = ",".join(str(i) for i in range(101))
    with pytest.raises(HTTPException) as e:
        await seg_r.get_masks_batch(ids, current_user=user(), db=mock_db)
    assert e.value.status_code == 400


async def test_seg_masks_batch_ok(mock_db):
    with patch.object(seg_r.segmentation_service, "get_segmentation_masks_batch",
                      new=AsyncMock(return_value={1: {"has_mask": True}})):
        out = await seg_r.get_masks_batch("1,2,3", current_user=user(), db=mock_db)
    assert out["masks"] == {1: {"has_mask": True}}


async def test_seg_delete_mask_not_found(mock_db):
    with patch.object(seg_r.segmentation_service, "delete_segmentation_mask",
                      new=AsyncMock(return_value={"success": False, "error": "Mask not found"})):
        with pytest.raises(HTTPException) as e:
            await seg_r.delete_mask(1, current_user=user(), db=mock_db)
    assert e.value.status_code == 404


async def test_seg_delete_mask_ok(mock_db):
    with patch.object(seg_r.segmentation_service, "delete_segmentation_mask",
                      new=AsyncMock(return_value={"success": True, "crop_id": 4})):
        out = await seg_r.delete_mask(4, current_user=user(), db=mock_db)
    assert out["success"] is True


async def test_seg_save_fov_mask_success(mock_db):
    with patch.object(seg_r.segmentation_service, "save_fov_segmentation_mask",
                      new=AsyncMock(return_value={"success": True, "image_id": 2})):
        req = seg_r.SaveFOVMaskRequest(image_id=2, polygon=[[0, 0], [1, 0], [1, 1]],
                                       iou_score=0.9, prompt_count=1)
        out = await seg_r.save_fov_mask(req, current_user=user(), db=mock_db)
    assert out["image_id"] == 2


async def test_seg_save_fov_mask_failure(mock_db):
    with patch.object(seg_r.segmentation_service, "save_fov_segmentation_mask",
                      new=AsyncMock(return_value={"success": False, "error": "bad"})):
        req = seg_r.SaveFOVMaskRequest(image_id=2, polygon=[[0, 0], [1, 0], [1, 1]],
                                       iou_score=0.9, prompt_count=1)
        with pytest.raises(HTTPException) as e:
            await seg_r.save_fov_mask(req, current_user=user(), db=mock_db)
    assert e.value.status_code == 400


async def test_seg_save_fov_union_not_owned(mock_db):
    # image exists but owned by another user
    img = SimpleNamespace(experiment=SimpleNamespace(user_id=99))
    mock_db.execute.return_value = make_result(scalar=img)
    req = seg_r.SaveFOVMaskUnionRequest(image_id=2, polygons=[[[0, 0], [1, 0], [1, 1]]])
    with pytest.raises(HTTPException) as e:
        await seg_r.save_fov_mask_union(req, current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 404


async def test_seg_save_fov_union_image_none(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    req = seg_r.SaveFOVMaskUnionRequest(image_id=2, polygons=[[[0, 0], [1, 0], [1, 1]]])
    with pytest.raises(HTTPException) as e:
        await seg_r.save_fov_mask_union(req, current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 404


async def test_seg_save_fov_union_success(mock_db):
    img = SimpleNamespace(experiment=SimpleNamespace(user_id=1))
    mock_db.execute.return_value = make_result(scalar=img)
    with patch.object(seg_r.segmentation_service, "save_fov_segmentation_mask_union",
                      new=AsyncMock(return_value={"success": True, "polygon_count": 1})) as su:
        req = seg_r.SaveFOVMaskUnionRequest(image_id=2,
                                            polygons=[[[0, 0], [1, 0], [1, 1]]])
        out = await seg_r.save_fov_mask_union(req, current_user=user(id=1), db=mock_db)
    assert out["polygon_count"] == 1
    # polygons converted to tuples
    assert su.await_args.kwargs["polygons"][0][0] == (0.0, 0.0)


async def test_seg_save_fov_union_service_failure(mock_db):
    img = SimpleNamespace(experiment=SimpleNamespace(user_id=1))
    mock_db.execute.return_value = make_result(scalar=img)
    with patch.object(seg_r.segmentation_service, "save_fov_segmentation_mask_union",
                      new=AsyncMock(return_value={"success": False, "error": "no polys"})):
        req = seg_r.SaveFOVMaskUnionRequest(image_id=2,
                                            polygons=[[[0, 0], [1, 0], [1, 1]]])
        with pytest.raises(HTTPException) as e:
            await seg_r.save_fov_mask_union(req, current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 400


async def test_seg_get_fov_mask(mock_db):
    out = {"has_mask": True, "polygon": [[[0, 0], [1, 1], [2, 2]]],
           "iou_score": 0.9, "area_pixels": 5, "creation_method": "interactive",
           "prompt_count": 1}
    with patch.object(seg_r.segmentation_service, "get_fov_segmentation_mask",
                      new=AsyncMock(return_value=out)):
        resp = await seg_r.get_fov_mask(2, current_user=user(), db=mock_db)
    assert resp.has_mask is True


async def test_seg_delete_fov_mask_not_found(mock_db):
    with patch.object(seg_r.segmentation_service, "delete_fov_segmentation_mask",
                      new=AsyncMock(return_value={"success": False, "error": "FOV mask not found"})):
        with pytest.raises(HTTPException) as e:
            await seg_r.delete_fov_mask(1, current_user=user(), db=mock_db)
    assert e.value.status_code == 404


async def test_seg_delete_fov_mask_ok(mock_db):
    with patch.object(seg_r.segmentation_service, "delete_fov_segmentation_mask",
                      new=AsyncMock(return_value={"success": True, "image_id": 9})):
        out = await seg_r.delete_fov_mask(9, current_user=user(), db=mock_db)
    assert out["image_id"] == 9


async def test_seg_capabilities():
    caps = {"device": "cuda", "variant": "sam3", "supports_text_prompts": True,
            "model_name": "SAM 3"}
    with patch.object(seg_r.segmentation_service, "get_segmentation_capabilities",
                      return_value=caps):
        resp = await seg_r.get_capabilities(current_user=user())
    assert resp.device == "cuda"
    assert resp.supports_text_prompts is True


async def test_seg_segment_text_failure(mock_db):
    with patch.object(seg_r.segmentation_service, "segment_from_text",
                      new=AsyncMock(return_value={"success": False, "error": "no gpu"})):
        req = seg_r.TextSegmentRequest(image_id=1, text_prompt="cell")
        resp = await seg_r.segment_text(req, current_user=user(), db=mock_db)
    assert resp.success is False
    assert resp.error == "no gpu"


async def test_seg_segment_text_success(mock_db):
    out = {"success": True, "prompt": "cell", "instances": [
        {"index": 0, "polygon": [[0, 0], [1, 1]], "bbox": [0, 0, 1, 1],
         "score": 0.9, "area_pixels": 10}]}
    with patch.object(seg_r.segmentation_service, "segment_from_text",
                      new=AsyncMock(return_value=out)):
        req = seg_r.TextSegmentRequest(image_id=1, text_prompt="cell")
        resp = await seg_r.segment_text(req, current_user=user(), db=mock_db)
    assert resp.success is True
    assert len(resp.instances) == 1
    assert resp.prompt == "cell"


async def test_seg_segment_text_refine_failure(mock_db):
    with patch.object(seg_r.segmentation_service, "refine_text_segmentation",
                      new=AsyncMock(return_value={"success": False, "error": "fail"})):
        req = seg_r.TextRefineRequest(image_id=1, text_prompt="cell",
                                      instance_index=0,
                                      points=[seg_r.ClickPoint(x=1, y=2, label=1)])
        resp = await seg_r.segment_text_refine(req, current_user=user(), db=mock_db)
    assert resp.success is False


async def test_seg_segment_text_refine_success(mock_db):
    out = {"success": True, "polygon": [[0, 0], [1, 1]], "iou_score": 0.8,
           "area_pixels": 50}
    with patch.object(seg_r.segmentation_service, "refine_text_segmentation",
                      new=AsyncMock(return_value=out)) as rt:
        req = seg_r.TextRefineRequest(image_id=1, text_prompt="cell",
                                      instance_index=0,
                                      points=[seg_r.ClickPoint(x=1.4, y=2.6, label=1)])
        resp = await seg_r.segment_text_refine(req, current_user=user(), db=mock_db)
    assert resp.success is True
    assert resp.iou_score == 0.8
    # coordinates rounded to ints
    assert rt.await_args.kwargs["point_coords"] == [(1, 3)]


# ============================================================================ #
# rag router
# ============================================================================ #
def _doc(id=1, user_id=7, file_type="pdf", original_path="/x/doc.pdf", name="doc.pdf"):
    return SimpleNamespace(id=id, user_id=user_id, file_type=file_type,
                           original_path=original_path, name=name)


async def test_rag_get_document_for_user_found(mock_db):
    doc = _doc()
    mock_db.execute.return_value = make_result(scalar=doc)
    out = await rag_r.get_document_for_user(mock_db, 1, 7)
    assert out is doc


async def test_rag_get_document_for_user_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as e:
        await rag_r.get_document_for_user(mock_db, 1, 7)
    assert e.value.status_code == 404


async def test_rag_check_rate_limit_under_limit():
    r = AsyncMock(name="redis")
    pipe = AsyncMock(name="pipe")
    pipe.zremrangebyscore = MagicMock()
    pipe.zcard = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, 3])  # count = 3 < 10
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=pipe)
    cm.__aexit__ = AsyncMock(return_value=False)
    r.pipeline = MagicMock(return_value=cm)
    r.zadd = AsyncMock()
    r.expire = AsyncMock()
    with patch.object(rag_r, "_get_redis", new=AsyncMock(return_value=r)):
        await rag_r._check_upload_rate_limit(7)  # no raise
    r.zadd.assert_awaited()


async def test_rag_check_rate_limit_exceeded():
    r = AsyncMock(name="redis")
    pipe = AsyncMock(name="pipe")
    pipe.zremrangebyscore = MagicMock()
    pipe.zcard = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, 10])  # at the limit
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=pipe)
    cm.__aexit__ = AsyncMock(return_value=False)
    r.pipeline = MagicMock(return_value=cm)
    r.zrange = AsyncMock(return_value=[("member", 1000.0)])
    with patch.object(rag_r, "_get_redis", new=AsyncMock(return_value=r)):
        with pytest.raises(HTTPException) as e:
            await rag_r._check_upload_rate_limit(7)
    assert e.value.status_code == 429
    assert "Retry-After" in e.value.headers


async def test_rag_check_rate_limit_exceeded_no_oldest():
    r = AsyncMock(name="redis")
    pipe = AsyncMock(name="pipe")
    pipe.zremrangebyscore = MagicMock()
    pipe.zcard = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, 12])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=pipe)
    cm.__aexit__ = AsyncMock(return_value=False)
    r.pipeline = MagicMock(return_value=cm)
    r.zrange = AsyncMock(return_value=[])  # no oldest entry -> fallback retry_after
    with patch.object(rag_r, "_get_redis", new=AsyncMock(return_value=r)):
        with pytest.raises(HTTPException) as e:
            await rag_r._check_upload_rate_limit(7)
    assert e.value.headers["Retry-After"] == str(rag_r.UPLOAD_RATE_LIMIT_WINDOW)


async def test_rag_check_rate_limit_redis_error_fails_open():
    with patch.object(rag_r, "_get_redis",
                      new=AsyncMock(side_effect=rag_r.redis.RedisError("down"))):
        # Must not raise — fail open
        await rag_r._check_upload_rate_limit(7)


async def test_rag_get_redis_lazy_init():
    rag_r._redis_pool = None
    fake = MagicMock(name="pool")
    with patch.object(rag_r.redis, "from_url", return_value=fake) as fu:
        out = await rag_r._get_redis()
        out2 = await rag_r._get_redis()  # cached, no second from_url
    assert out is fake and out2 is fake
    fu.assert_called_once()
    rag_r._redis_pool = None


async def test_rag_list_documents(mock_db):
    # A second doc owned by a different user (id=9), only visible because it is
    # group-shared -- exercises the per-row is_owner computation.
    docs = [
        SimpleNamespace(id=1, user_id=7, name="d", file_type="pdf",
                        status="completed", page_count=1, progress=1.0,
                        error_message=None, created_at=datetime.now(timezone.utc),
                        indexed_at=None),
        SimpleNamespace(id=2, user_id=9, name="shared", file_type="pdf",
                        status="completed", page_count=1, progress=1.0,
                        error_message=None, created_at=datetime.now(timezone.utc),
                        indexed_at=None),
    ]
    mock_db.execute.return_value = make_result(scalars_all=docs)
    # model_validate must return something that supports attribute assignment
    # (the router sets .is_owner on it) -- a plain dict cannot, a SimpleNamespace
    # models the real pydantic instance closely enough for this test.
    with patch.object(rag_r, "get_user_group_id", new=AsyncMock(return_value=7)), \
         patch.object(rag_r.RAGDocumentResponse, "model_validate",
                      side_effect=lambda d: SimpleNamespace(id=d.id)):
        out = await rag_r.list_documents(skip=0, limit=10, status_filter="completed",
                                         current_user=user(id=7), db=mock_db)
    assert [o.id for o in out] == [1, 2]
    assert out[0].is_owner is True   # own document
    assert out[1].is_owner is False  # group-shared, not owned


async def test_rag_upload_no_filename(mock_db):
    fobj = SimpleNamespace(filename="")
    with patch.object(rag_r, "_check_upload_rate_limit", new=AsyncMock()):
        with pytest.raises(HTTPException) as e:
            await rag_r.upload_document(MagicMock(), file=fobj,
                                        current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 400


async def test_rag_upload_unsupported(mock_db):
    fobj = SimpleNamespace(filename="bad.xyz")
    with patch.object(rag_r, "_check_upload_rate_limit", new=AsyncMock()), \
         patch.object(rag_r, "is_supported_file", return_value=False):
        with pytest.raises(HTTPException) as e:
            await rag_r.upload_document(MagicMock(), file=fobj,
                                        current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 400


async def test_rag_upload_too_large(mock_db):
    fobj = AsyncMock()
    fobj.filename = "doc.pdf"
    fobj.read = AsyncMock(return_value=b"x" * (100 * 1024 * 1024 + 1))
    with patch.object(rag_r, "_check_upload_rate_limit", new=AsyncMock()), \
         patch.object(rag_r, "is_supported_file", return_value=True):
        with pytest.raises(HTTPException) as e:
            await rag_r.upload_document(MagicMock(), file=fobj,
                                        current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 413


async def test_rag_upload_success(mock_db):
    fobj = AsyncMock()
    fobj.filename = "doc.pdf"
    fobj.read = AsyncMock(return_value=b"data")
    bg = MagicMock()
    doc = SimpleNamespace(id=11)
    with patch.object(rag_r, "_check_upload_rate_limit", new=AsyncMock()), \
         patch.object(rag_r, "is_supported_file", return_value=True), \
         patch.object(rag_r, "save_uploaded_document", new=AsyncMock(return_value=doc)), \
         patch.object(rag_r.RAGDocumentUploadResponse, "model_validate",
                      side_effect=lambda d: {"id": d.id}):
        out = await rag_r.upload_document(bg, file=fobj, current_user=user(id=7), db=mock_db)
    assert out == {"id": 11}
    bg.add_task.assert_called_once()
    mock_db.commit.assert_awaited()


async def test_rag_upload_foreign_thread_rejected(mock_db):
    # Security: you may not attach a document to someone else's conversation.
    # The check must run BEFORE anything is written to disk.
    fobj = AsyncMock()
    fobj.filename = "doc.pdf"
    fobj.read = AsyncMock(return_value=b"data")
    mock_db.execute.return_value = make_result(scalar=None)  # thread not owned
    saved = AsyncMock()
    with patch.object(rag_r, "_check_upload_rate_limit", new=AsyncMock()), \
         patch.object(rag_r, "is_supported_file", return_value=True), \
         patch.object(rag_r, "save_uploaded_document", new=saved):
        with pytest.raises(HTTPException) as exc:
            await rag_r.upload_document(MagicMock(), file=fobj, thread_id=99,
                                        current_user=user(id=7), db=mock_db)
    # 404 (not 403) so a foreign thread id cannot be enumerated.
    assert exc.value.status_code == 404
    saved.assert_not_awaited()


async def test_rag_upload_owned_thread_passes_thread_id(mock_db):
    fobj = AsyncMock()
    fobj.filename = "doc.pdf"
    fobj.read = AsyncMock(return_value=b"data")
    mock_db.execute.return_value = make_result(scalar=42)  # thread owned
    doc = SimpleNamespace(id=12)
    saved = AsyncMock(return_value=doc)
    with patch.object(rag_r, "_check_upload_rate_limit", new=AsyncMock()), \
         patch.object(rag_r, "is_supported_file", return_value=True), \
         patch.object(rag_r, "save_uploaded_document", new=saved), \
         patch.object(rag_r.RAGDocumentUploadResponse, "model_validate",
                      side_effect=lambda d: {"id": d.id}):
        await rag_r.upload_document(MagicMock(), file=fobj, thread_id=42,
                                    current_user=user(id=7), db=mock_db)
    assert saved.await_args.kwargs["thread_id"] == 42


async def test_rag_upload_defaults_to_library_when_no_thread(mock_db):
    # Regression: the Form default must be a real None on a direct call, or the
    # ownership branch fires with a sentinel object.
    fobj = AsyncMock()
    fobj.filename = "doc.pdf"
    fobj.read = AsyncMock(return_value=b"data")
    doc = SimpleNamespace(id=13)
    saved = AsyncMock(return_value=doc)
    with patch.object(rag_r, "_check_upload_rate_limit", new=AsyncMock()), \
         patch.object(rag_r, "is_supported_file", return_value=True), \
         patch.object(rag_r, "save_uploaded_document", new=saved), \
         patch.object(rag_r.RAGDocumentUploadResponse, "model_validate",
                      side_effect=lambda d: {"id": d.id}):
        await rag_r.upload_document(MagicMock(), file=fobj,
                                    current_user=user(id=7), db=mock_db)
    assert saved.await_args.kwargs["thread_id"] is None


async def test_rag_upload_value_error(mock_db):
    fobj = AsyncMock()
    fobj.filename = "doc.pdf"
    fobj.read = AsyncMock(return_value=b"data")
    with patch.object(rag_r, "_check_upload_rate_limit", new=AsyncMock()), \
         patch.object(rag_r, "is_supported_file", return_value=True), \
         patch.object(rag_r, "save_uploaded_document",
                      new=AsyncMock(side_effect=ValueError("bad type"))):
        with pytest.raises(HTTPException) as e:
            await rag_r.upload_document(MagicMock(), file=fobj,
                                        current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 400


async def test_rag_get_document(mock_db):
    doc = _doc()
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)), \
         patch.object(rag_r.RAGDocumentResponse, "model_validate",
                      side_effect=lambda d: {"id": d.id}):
        out = await rag_r.get_document(1, current_user=user(id=7), db=mock_db)
    assert out == {"id": 1}


async def test_rag_delete_document_not_found(mock_db):
    with patch.object(rag_r, "delete_document", new=AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as e:
            await rag_r.delete_document_endpoint(1, current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 404


async def test_rag_delete_document_ok(mock_db):
    with patch.object(rag_r, "delete_document", new=AsyncMock(return_value=True)):
        out = await rag_r.delete_document_endpoint(1, current_user=user(id=7), db=mock_db)
    assert out is None


async def test_rag_reindex_endpoint(mock_db):
    bg = MagicMock()
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=_doc())):
        out = await rag_r.reindex_document_endpoint(1, bg, current_user=user(id=7), db=mock_db)
    assert out["status"] == "reindexing"
    bg.add_task.assert_called_once()
    # Execute the queued closure to cover run_reindex()
    queued = bg.add_task.call_args.args[0]
    with patch.object(rag_r, "reindex_document", new=AsyncMock(return_value={"status": "completed"})):
        await queued()


async def test_rag_serve_pdf_not_pdf(mock_db):
    doc = _doc(file_type="image")
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)):
        with pytest.raises(HTTPException) as e:
            await rag_r.serve_pdf(1, current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 400


async def test_rag_serve_pdf_file_missing(mock_db):
    doc = _doc(original_path="/no/such/doc.pdf")
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)):
        with pytest.raises(HTTPException) as e:
            await rag_r.serve_pdf(1, current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 404


async def test_rag_serve_pdf_success(mock_db, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    doc = _doc(original_path=str(pdf))
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)):
        resp = await rag_r.serve_pdf(1, current_user=user(id=7), db=mock_db)
    assert resp.media_type == "application/pdf"


async def test_rag_list_document_pages(mock_db):
    doc = _doc()
    pages = [SimpleNamespace(id=1, document_id=1, page_number=1,
                             image_path="/p.png", embedding=[0.1])]
    mock_db.execute.return_value = make_result(scalars_all=pages)
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)):
        out = await rag_r.list_document_pages(1, current_user=user(id=7), db=mock_db)
    assert out[0].has_embedding is True
    assert out[0].page_number == 1


async def test_rag_serve_page_image_page_not_found(mock_db):
    doc = _doc()
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)):
        with pytest.raises(HTTPException) as e:
            await rag_r.serve_page_image(1, 5, current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 404


async def test_rag_serve_page_image_file_missing(mock_db):
    doc = _doc()
    page = SimpleNamespace(image_path="/no/such.png")
    mock_db.execute.return_value = make_result(scalar=page)
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)):
        with pytest.raises(HTTPException) as e:
            await rag_r.serve_page_image(1, 1, current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 404


async def test_rag_serve_page_image_success(mock_db, tmp_path):
    doc = _doc()
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG")
    page = SimpleNamespace(image_path=str(img))
    mock_db.execute.return_value = make_result(scalar=page)
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)):
        resp = await rag_r.serve_page_image(1, 1, current_user=user(id=7), db=mock_db)
    assert resp.media_type == "image/png"


async def test_rag_serve_passage_invalid_hash(mock_db):
    with pytest.raises(HTTPException) as e:
        await rag_r.serve_passage_image(1, "ZZZ", current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 400


async def test_rag_serve_passage_not_found(mock_db):
    with patch.object(rag_r, "get_cached_passage", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as e:
            await rag_r.serve_passage_image(1, "deadbeef0011",
                                            current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 404


async def test_rag_serve_passage_success(mock_db, tmp_path):
    p = tmp_path / "passage.png"
    p.write_bytes(b"\x89PNG")
    with patch.object(rag_r, "get_cached_passage", new=AsyncMock(return_value=p)):
        resp = await rag_r.serve_passage_image(1, "deadbeef0011",
                                               current_user=user(id=7), db=mock_db)
    assert resp.media_type == "image/png"


async def test_rag_indexing_status(mock_db):
    data = dict(documents_pending=1, documents_processing=0, documents_completed=2,
                documents_failed=0, fov_images_pending=3, fov_images_indexed=4)
    with patch.object(rag_r, "get_indexing_status", new=AsyncMock(return_value=data)):
        resp = await rag_r.get_indexing_status_endpoint(current_user=user(id=7), db=mock_db)
    assert resp.documents_completed == 2


async def test_rag_trigger_fov_indexing_error(mock_db):
    bg = MagicMock()
    with patch.object(rag_r, "batch_index_fov_images",
                      new=AsyncMock(return_value={"error": "Experiment not found"})):
        with pytest.raises(HTTPException) as e:
            await rag_r.trigger_fov_indexing(1, bg, current_user=user(id=7), db=mock_db)
    assert e.value.status_code == 404


async def test_rag_trigger_fov_indexing_ok(mock_db):
    bg = MagicMock()
    with patch.object(rag_r, "batch_index_fov_images",
                      new=AsyncMock(return_value={"indexed": 5, "total": 5})):
        out = await rag_r.trigger_fov_indexing(1, bg, current_user=user(id=7), db=mock_db)
    assert out["indexed"] == 5


async def test_rag_search(mock_db):
    results = {
        "query": "q",
        "documents": [{"document_id": 1, "document_name": "D", "page_number": 1,
                       "page_image_url": "/u", "similarity_score": 0.9}],
        "fov_images": [{"image_id": 2, "experiment_id": 3, "experiment_name": "E",
                        "original_filename": "f.tif", "thumbnail_path": "/t",
                        "similarity_score": 0.8}],
    }
    with patch.object(rag_r, "combined_search", new=AsyncMock(return_value=results)):
        resp = await rag_r.search(q="q", experiment_id=None, doc_limit=10, fov_limit=10,
                                  current_user=user(id=7), db=mock_db)
    assert resp.documents[0].document_id == 1
    assert resp.fov_images[0].image_id == 2


async def test_rag_search_documents_only(mock_db):
    with patch.object(rag_r, "search_documents", new=AsyncMock(return_value=[{"x": 1}])):
        out = await rag_r.search_documents_only(q="q", limit=20,
                                                current_user=user(id=7), db=mock_db)
    assert out["results"] == [{"x": 1}]


async def test_rag_search_fov_only(mock_db):
    with patch.object(rag_r, "search_fov_images", new=AsyncMock(return_value=[{"y": 2}])):
        out = await rag_r.search_fov_only(q="q", experiment_id=5, limit=20,
                                          current_user=user(id=7), db=mock_db)
    assert out["results"] == [{"y": 2}]


async def test_rag_search_within_document_matches(mock_db):
    doc = _doc()
    long_text = ("intro " * 20) + "needle" + (" tail" * 20)
    pages = [
        SimpleNamespace(page_number=1, extracted_text=long_text),
        SimpleNamespace(page_number=2, extracted_text="nothing here"),
        SimpleNamespace(page_number=3, extracted_text=None),  # skipped
    ]
    mock_db.execute.return_value = make_result(scalars_all=pages)
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)):
        out = await rag_r.search_within_document(1, q="needle",
                                                 current_user=user(id=7), db=mock_db)
    assert out["pages_with_matches"] == 1
    assert out["total_matches"] == 1
    assert out["matches"][0]["page_number"] == 1
    # leading + trailing "..." snippet markers present
    assert out["matches"][0]["snippet"].startswith("...")
    assert out["matches"][0]["snippet"].endswith("...")


async def test_rag_search_within_document_no_snippet_markers(mock_db):
    doc = _doc()
    pages = [SimpleNamespace(page_number=1, extracted_text="needle short")]
    mock_db.execute.return_value = make_result(scalars_all=pages)
    with patch.object(rag_r, "get_document_for_user", new=AsyncMock(return_value=doc)):
        out = await rag_r.search_within_document(1, q="needle",
                                                 current_user=user(id=7), db=mock_db)
    # match at position 0 -> no leading "...", short text -> no trailing "..."
    assert out["matches"][0]["snippet"] == "needle short"


# ============================================================================ #
# proteins router
# ============================================================================ #
def protein(id=1, name="PRC1", embedding=None, umap_x=None, umap_y=None):
    return SimpleNamespace(
        id=id, name=name, full_name="Full", description=None, color="#00d4aa",
        uniprot_id=None, fasta_sequence="MKT", gene_name=None, organism=None,
        sequence_length=3, embedding=embedding, embedding_model=None,
        embedding_computed_at=None, created_at=None, umap_x=umap_x, umap_y=umap_y,
        umap_computed_at=None,
    )


async def test_prot_get_or_404_found(mock_db):
    p = protein()
    mock_db.execute.return_value = make_result(scalar=p)
    assert await prot_r.get_protein_or_404(1, mock_db) is p


async def test_prot_get_or_404_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as e:
        await prot_r.get_protein_or_404(1, mock_db)
    assert e.value.status_code == 404


async def test_prot_check_name_unique_conflict(mock_db):
    mock_db.execute.return_value = make_result(scalar=protein())
    with pytest.raises(HTTPException) as e:
        await prot_r.check_protein_name_unique("PRC1", mock_db)
    assert e.value.status_code == 400


async def test_prot_check_name_unique_ok_with_exclude(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    await prot_r.check_protein_name_unique("New", mock_db, exclude_id=5)  # no raise


async def test_prot_list_existing(mock_db):
    proteins = [protein(id=1, name="PRC1"), protein(id=2, name="Tau4R")]
    mock_db.execute.side_effect = [
        make_result(scalars_all=proteins),  # list query
        make_result(fetchall=[(1, 3), (2, 1)]),  # image counts
    ]
    out = await prot_r.list_proteins(current_user=user(), db=mock_db)
    assert len(out) == 2
    assert out[0].image_count == 3


async def test_prot_list_creates_defaults(mock_db):
    new_proteins = [protein(id=i, name=p["name"])
                    for i, p in enumerate(prot_r.DEFAULT_PROTEINS, start=1)]
    mock_db.execute.side_effect = [
        make_result(scalars_all=[]),            # initial: empty
        make_result(scalars_all=new_proteins),  # re-query after creating defaults
        make_result(fetchall=[]),               # image counts
    ]
    out = await prot_r.list_proteins(current_user=user(), db=mock_db)
    assert len(out) == len(prot_r.DEFAULT_PROTEINS)
    # one add per default protein + a commit
    assert mock_db.add.call_count == len(prot_r.DEFAULT_PROTEINS)
    mock_db.commit.assert_awaited()


async def test_prot_create(mock_db):
    captured = {}
    mock_db.add.side_effect = lambda o: captured.setdefault("p", o)

    async def _refresh(obj, *a, **k):
        obj.id = 99
        obj.full_name = obj.full_name if hasattr(obj, "full_name") else None
        for attr in ("description", "color", "uniprot_id", "gene_name", "organism",
                     "sequence_length", "embedding", "embedding_model",
                     "embedding_computed_at", "created_at"):
            if not hasattr(obj, attr):
                setattr(obj, attr, None)

    mock_db.refresh.side_effect = _refresh
    mock_db.execute.return_value = make_result(scalar=None)  # name unique check
    data = prot_r.MapProteinCreate(name="NewProt", color="#123456")
    out = await prot_r.create_protein(data, current_user=user(), db=mock_db)
    assert out.name == "NewProt"
    assert out.id == 99


async def test_prot_umap_too_few(mock_db):
    mock_db.execute.return_value = make_result(scalars_all=[protein(embedding=[0.1])])
    out = await prot_r.get_protein_umap(current_user=user(), db=mock_db)
    assert out.total_proteins == 1
    assert out.points == []
    assert out.is_precomputed is False


async def test_prot_umap_precomputed(mock_db):
    proteins = [protein(id=i, name=f"P{i}", embedding=[0.1, 0.2],
                        umap_x=float(i), umap_y=float(i)) for i in range(1, 4)]
    proteins[0].umap_computed_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_db.execute.side_effect = [
        make_result(scalars_all=proteins),  # protein list
        make_result(fetchall=[(1, 2)]),     # image counts
    ]
    out = await prot_r.get_protein_umap(current_user=user(), db=mock_db)
    assert out.is_precomputed is True
    assert len(out.points) == 3
    assert out.computed_at is not None


async def test_prot_umap_online(mock_db):
    proteins = [protein(id=i, name=f"P{i}", embedding=[0.1 * i, 0.2 * i])
                for i in range(1, 4)]  # umap_x/y None -> not precomputed
    mock_db.execute.side_effect = [
        make_result(scalars_all=proteins),
        make_result(fetchall=[]),
    ]
    import numpy as np
    proj = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    with patch("services.umap_service.compute_protein_umap_online",
               return_value=(proj, 0.42)):
        out = await prot_r.get_protein_umap(current_user=user(), db=mock_db)
    assert out.is_precomputed is False
    assert out.silhouette_score == 0.42
    assert out.points[0].x == 1.0


async def test_prot_get(mock_db):
    p = protein()
    mock_db.execute.side_effect = [
        make_result(scalar=p),    # get_protein_or_404
        make_result(scalar=4),    # image count
    ]
    out = await prot_r.get_protein(1, current_user=user(), db=mock_db)
    assert out.image_count == 4


async def test_prot_update_name_conflict(mock_db):
    p = protein(id=1, name="Old")
    mock_db.execute.side_effect = [
        make_result(scalar=p),            # get_protein_or_404
        make_result(scalar=protein(id=2)),  # name unique check -> conflict
    ]
    data = prot_r.MapProteinUpdate(name="Taken")
    with pytest.raises(HTTPException) as e:
        await prot_r.update_protein(1, data, current_user=user(), db=mock_db)
    assert e.value.status_code == 400


async def test_prot_update_fasta_resets_embedding(mock_db):
    p = protein(id=1, name="P")
    p.embedding = [0.1]
    p.embedding_model = "esm"
    mock_db.execute.side_effect = [
        make_result(scalar=p),    # get_protein_or_404
        make_result(scalar=3),    # image count
    ]
    data = prot_r.MapProteinUpdate(fasta_sequence="MKTAYIAK")
    out = await prot_r.update_protein(1, data, current_user=user(), db=mock_db)
    assert p.embedding is None
    assert p.umap_x is None
    assert out.image_count == 3


async def test_prot_delete_with_images(mock_db):
    p = protein()
    mock_db.execute.side_effect = [
        make_result(scalar=p),    # get_protein_or_404
        make_result(scalar=2),    # image count > 0
    ]
    with pytest.raises(HTTPException) as e:
        await prot_r.delete_protein(1, current_user=user(), db=mock_db)
    assert e.value.status_code == 409


async def test_prot_delete_ok(mock_db):
    p = protein()
    mock_db.execute.side_effect = [
        make_result(scalar=p),
        make_result(scalar=0),  # no images
    ]
    out = await prot_r.delete_protein(1, current_user=user(), db=mock_db)
    assert out is None
    mock_db.delete.assert_awaited_once_with(p)


async def test_prot_compute_embedding_success(mock_db):
    with patch("services.protein_embedding_service.compute_protein_embedding",
               new=AsyncMock(return_value={"success": True})):
        out = await prot_r.compute_protein_embedding_endpoint(1, current_user=user(), db=mock_db)
    assert out["success"] is True


async def test_prot_compute_embedding_lookup_error(mock_db):
    with patch("services.protein_embedding_service.compute_protein_embedding",
               new=AsyncMock(side_effect=LookupError("not found"))):
        with pytest.raises(HTTPException) as e:
            await prot_r.compute_protein_embedding_endpoint(1, current_user=user(), db=mock_db)
    assert e.value.status_code == 404


async def test_prot_compute_embedding_value_error(mock_db):
    with patch("services.protein_embedding_service.compute_protein_embedding",
               new=AsyncMock(side_effect=ValueError("no fasta"))):
        with pytest.raises(HTTPException) as e:
            await prot_r.compute_protein_embedding_endpoint(1, current_user=user(), db=mock_db)
    assert e.value.status_code == 400


async def test_prot_compute_embedding_runtime_error(mock_db):
    with patch("services.protein_embedding_service.compute_protein_embedding",
               new=AsyncMock(side_effect=RuntimeError("gpu oom"))):
        with pytest.raises(HTTPException) as e:
            await prot_r.compute_protein_embedding_endpoint(1, current_user=user(), db=mock_db)
    assert e.value.status_code == 500


# ============================================================================ #
# export_import router
# ============================================================================ #
def _token_payload(sub=7, exp_offset=timedelta(hours=1)):
    return SimpleNamespace(sub=sub, exp=datetime.now(timezone.utc) + exp_offset)


async def test_ei_token_missing(mock_db):
    with pytest.raises(HTTPException) as e:
        await ei_r.get_user_from_query_token(None, mock_db)
    assert e.value.status_code == 401


async def test_ei_token_invalid(mock_db):
    with patch.object(ei_r, "decode_token", return_value=None):
        with pytest.raises(HTTPException) as e:
            await ei_r.get_user_from_query_token("tok", mock_db)
    assert e.value.status_code == 401


async def test_ei_token_expired(mock_db):
    with patch.object(ei_r, "decode_token",
                      return_value=_token_payload(exp_offset=timedelta(hours=-1))):
        with pytest.raises(HTTPException) as e:
            await ei_r.get_user_from_query_token("tok", mock_db)
    assert e.value.status_code == 401


async def test_ei_token_user_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(ei_r, "decode_token", return_value=_token_payload()):
        with pytest.raises(HTTPException) as e:
            await ei_r.get_user_from_query_token("tok", mock_db)
    assert e.value.status_code == 401


async def test_ei_token_success(mock_db):
    u = user(id=7)
    mock_db.execute.return_value = make_result(scalar=u)
    with patch.object(ei_r, "decode_token", return_value=_token_payload(sub=7)):
        out = await ei_r.get_user_from_query_token("tok", mock_db)
    assert out is u


async def test_ei_handle_service_call_success():
    async def op():
        return {"ok": True}
    out = await ei_r.handle_service_call(op, "err")
    assert out == {"ok": True}


async def test_ei_handle_service_call_value_error():
    async def op():
        raise ValueError("bad input")
    with pytest.raises(HTTPException) as e:
        await ei_r.handle_service_call(op, "err")
    assert e.value.status_code == 400
    assert e.value.detail == "bad input"


async def test_ei_handle_service_call_generic_error():
    async def op():
        raise RuntimeError("boom")
    with pytest.raises(HTTPException) as e:
        await ei_r.handle_service_call(op, "Failed to do thing")
    assert e.value.status_code == 500
    assert e.value.detail == "Failed to do thing"


def test_ei_cleanup_temp_files_ok(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    f = d / "f.zip"
    f.write_bytes(b"x")
    ei_r.cleanup_temp_files(str(f), str(d))
    assert not f.exists()
    assert not d.exists()


def test_ei_cleanup_temp_files_oserror():
    # nonexistent paths -> OSError swallowed
    ei_r.cleanup_temp_files("/no/such/file", "/no/such/dir")


def test_ei_validate_upload_no_filename():
    with pytest.raises(HTTPException) as e:
        ei_r.validate_upload_file(SimpleNamespace(filename=""))
    assert e.value.status_code == 400


def test_ei_validate_upload_not_zip():
    with pytest.raises(HTTPException) as e:
        ei_r.validate_upload_file(SimpleNamespace(filename="data.tar"))
    assert e.value.status_code == 400


def test_ei_validate_upload_ok():
    ei_r.validate_upload_file(SimpleNamespace(filename="data.ZIP"))  # no raise


async def test_ei_verify_job_ownership_found():
    svc = MagicMock()
    svc.get_job_for_user = AsyncMock(return_value={"job_id": "j1"})
    out = await ei_r.verify_job_ownership("j1", 7, svc, "Export")
    assert out == {"job_id": "j1"}


async def test_ei_verify_job_ownership_missing():
    svc = MagicMock()
    svc.get_job_for_user = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as e:
        await ei_r.verify_job_ownership("j1", 7, svc, "Import")
    assert e.value.status_code == 404
    assert "Import" in e.value.detail


async def test_ei_prepare_export(mock_db):
    with patch.object(ei_r.export_service, "prepare_export",
                      new=AsyncMock(return_value={"job_id": "j1"})) as pe:
        req = ei_r.ExportPrepareRequest(experiment_ids=[1, 2])
        out = await ei_r.prepare_export(req, current_user=user(id=7), db=mock_db)
    assert out == {"job_id": "j1"}
    pe.assert_awaited_once()


async def test_ei_stream_export(mock_db):
    u = user(id=7)
    with patch.object(ei_r, "get_user_from_query_token", new=AsyncMock(return_value=u)), \
         patch.object(ei_r, "verify_job_ownership", new=AsyncMock(return_value={})), \
         patch.object(ei_r.export_service, "generate_export_stream",
                      return_value=iter([b"PK"])):
        resp = await ei_r.stream_export("j1", token="tok", db=mock_db)
    assert resp.media_type == "application/zip"
    assert resp.headers["X-Job-Id"] == "j1"


async def test_ei_get_export_status():
    with patch.object(ei_r, "verify_job_ownership", new=AsyncMock(return_value={})), \
         patch.object(ei_r.export_service, "get_export_status",
                      new=AsyncMock(return_value={"status": "completed"})):
        out = await ei_r.get_export_status("j1", current_user=user(id=7))
    assert out == {"status": "completed"}


async def test_ei_validate_import_success(tmp_path):
    fobj = AsyncMock()
    fobj.filename = "data.zip"
    fobj.read = AsyncMock(return_value=b"content")
    with patch.object(ei_r.import_service, "validate_import",
                      new=AsyncMock(return_value={"is_valid": True})):
        out = await ei_r.validate_import(file=fobj, current_user=user(id=7))
    assert out == {"is_valid": True}


async def test_ei_validate_import_cleanup_on_error():
    fobj = AsyncMock()
    fobj.filename = "data.zip"
    fobj.read = AsyncMock(side_effect=RuntimeError("read fail"))
    with patch.object(ei_r, "cleanup_temp_files") as cl:
        with pytest.raises(RuntimeError):
            await ei_r.validate_import(file=fobj, current_user=user(id=7))
    cl.assert_called_once()


async def test_ei_execute_import(mock_db):
    with patch.object(ei_r.import_service, "execute_import",
                      new=AsyncMock(return_value={"status": "importing"})):
        from schemas.export_import import ImportFormat
        req = ei_r.ImportExecuteRequest(job_id="j1", experiment_name="Exp",
                                        import_as_format=ImportFormat.MAPTIMIZE)
        out = await ei_r.execute_import(req, current_user=user(id=7), db=mock_db)
    assert out["status"] == "importing"


async def test_ei_get_import_status():
    with patch.object(ei_r, "verify_job_ownership", new=AsyncMock(return_value={})), \
         patch.object(ei_r.import_service, "get_import_status",
                      new=AsyncMock(return_value={"status": "completed"})):
        out = await ei_r.get_import_status("j1", current_user=user(id=7))
    assert out == {"status": "completed"}


# ============================================================================ #
# experiments router
# ============================================================================ #
def _exp(id=1, user_id=1, group_id=None, name="Exp"):
    return SimpleNamespace(
        id=id, name=name, description=None,
        status=ExperimentStatus.ACTIVE,
        group_id=group_id, map_protein=None, fasta_sequence=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        images=[], user_id=user_id, user=None,
    )


async def test_exp_get_for_user_found_with_group(mock_db):
    exp = _exp()
    mock_db.execute.return_value = make_result(scalar=exp)
    with patch.object(exp_r, "get_user_group_id", new=AsyncMock(return_value=5)):
        out = await exp_r.get_experiment_for_user(mock_db, 1, 1)
    assert out is exp


async def test_exp_get_for_user_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(exp_r, "get_user_group_id", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as e:
            await exp_r.get_experiment_for_user(mock_db, 1, 1)
    assert e.value.status_code == 404


async def test_exp_list(mock_db):
    exp = _exp()
    rows = [(exp, 3, 5, 2, "Alice")]
    mock_db.execute.return_value = _unique_result(rows)
    with patch.object(exp_r, "get_user_group_id", new=AsyncMock(return_value=5)):
        out = await exp_r.list_experiments(skip=0, limit=50,
                                           current_user=user(id=1), db=mock_db)
    assert len(out) == 1
    assert out[0].image_count == 3
    assert out[0].cell_count == 5
    assert out[0].has_sum_projections is True
    assert out[0].creator_name == "Alice"


async def test_exp_list_zero_counts(mock_db):
    exp = _exp()
    rows = [(exp, None, None, 0, None)]  # None counts -> 0, sum 0 -> False
    mock_db.execute.return_value = _unique_result(rows)
    with patch.object(exp_r, "get_user_group_id", new=AsyncMock(return_value=None)):
        out = await exp_r.list_experiments(skip=0, limit=50,
                                           current_user=user(id=1), db=mock_db)
    assert out[0].image_count == 0
    assert out[0].has_sum_projections is False


async def test_exp_create_protein_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)  # protein lookup fails
    with patch.object(exp_r, "get_user_group_id", new=AsyncMock(return_value=None)):
        data = exp_r.ExperimentCreate(name="E", map_protein_id=99)
        with pytest.raises(HTTPException) as e:
            await exp_r.create_experiment(data, current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 404


async def test_exp_create_success(mock_db):
    async def _refresh(obj, *a, **k):
        obj.id = 50
        obj.status = ExperimentStatus.ACTIVE
        obj.map_protein = None
        obj.group_id = None
        obj.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        obj.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

    mock_db.refresh.side_effect = _refresh
    with patch.object(exp_r, "get_user_group_id", new=AsyncMock(return_value=None)):
        data = exp_r.ExperimentCreate(name="E", description="d")
        out = await exp_r.create_experiment(data, current_user=user(id=1, name="Alice"), db=mock_db)
    assert out.id == 50
    assert out.creator_name == "Alice"


async def test_exp_get_detail_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(exp_r, "get_user_group_id", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as e:
            await exp_r.get_experiment(1, current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 404


async def test_exp_get_detail_success(mock_db):
    img = SimpleNamespace(id=10, original_filename="i.tif", status="completed",
                          thumbnail_path=None, sum_path="/s.tif")
    exp = _exp()
    exp.images = [img]
    mock_db.execute.side_effect = [
        make_result(scalar=exp),  # experiment load
        make_result(scalar=7),    # cell count
    ]
    # group_id not None -> exercises the group filter branch
    with patch.object(exp_r, "get_user_group_id", new=AsyncMock(return_value=5)):
        out = await exp_r.get_experiment(1, current_user=user(id=1), db=mock_db)
    assert out.image_count == 1
    assert out.cell_count == 7
    assert out.has_sum_projections is True


async def test_exp_update_not_owner(mock_db):
    exp = _exp(user_id=99)  # owned by someone else, in group
    with patch.object(exp_r, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        data = exp_r.ExperimentUpdate(name="New")
        with pytest.raises(HTTPException) as e:
            await exp_r.update_experiment(1, data, current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 403


async def test_exp_update_success(mock_db):
    exp = _exp(user_id=1, name="Old")
    with patch.object(exp_r, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        data = exp_r.ExperimentUpdate(name="New Name")
        out = await exp_r.update_experiment(1, data, current_user=user(id=1), db=mock_db)
    assert exp.name == "New Name"
    assert out.name == "New Name"
    mock_db.commit.assert_awaited()


async def test_exp_delete_not_owner(mock_db):
    exp = _exp(user_id=99)
    with patch.object(exp_r, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        with pytest.raises(HTTPException) as e:
            await exp_r.delete_experiment(1, current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 403


async def test_exp_delete_success(mock_db):
    exp = _exp(user_id=1)
    with patch.object(exp_r, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await exp_r.delete_experiment(1, current_user=user(id=1), db=mock_db)
    assert out is None
    mock_db.delete.assert_awaited_once_with(exp)


async def test_exp_update_protein_not_found(mock_db):
    exp = _exp(user_id=1)
    mock_db.execute.return_value = make_result(scalar=None)  # protein lookup fails
    with patch.object(exp_r, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        with pytest.raises(HTTPException) as e:
            await exp_r.update_experiment_protein(1, map_protein_id=99,
                                                  current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 404


async def test_exp_update_protein_clear(mock_db):
    # map_protein_id=None -> skip protein lookup; cascade with image ids
    exp = _exp(user_id=1)
    mock_db.execute.side_effect = [
        make_result(fetchall=[(10,), (11,)]),  # image ids
        make_result(),  # update images
        make_result(),  # update crops
    ]
    with patch.object(exp_r, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await exp_r.update_experiment_protein(1, map_protein_id=None,
                                                    current_user=user(id=1), db=mock_db)
    assert out["images_updated"] == 2
    assert out["map_protein_name"] is None
    mock_db.commit.assert_awaited()


async def test_exp_update_protein_with_protein_no_images(mock_db):
    exp = _exp(user_id=1)
    p = SimpleNamespace(id=5, name="PRC1", color="#00d4aa")
    mock_db.execute.side_effect = [
        make_result(scalar=p),       # protein lookup
        make_result(fetchall=[]),    # no image ids
    ]
    with patch.object(exp_r, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await exp_r.update_experiment_protein(1, map_protein_id=5,
                                                    current_user=user(id=1), db=mock_db)
    assert out["images_updated"] == 0
    assert out["map_protein_name"] == "PRC1"
    assert out["map_protein_color"] == "#00d4aa"


async def test_exp_update_protein_db_error_rolls_back(mock_db):
    exp = _exp(user_id=1)
    # protein None branch; image id lookup raises -> except -> rollback -> 500
    mock_db.execute.side_effect = RuntimeError("db down")
    with patch.object(exp_r, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        with pytest.raises(HTTPException) as e:
            await exp_r.update_experiment_protein(1, map_protein_id=None,
                                                  current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 500
    mock_db.rollback.assert_awaited()


# ============================================================================ #
# bug_reports router
# ============================================================================ #
async def test_bug_create(mock_db):
    async def _refresh(obj, *a, **k):
        obj.id = 1
        obj.status = "open"
        obj.created_at = datetime.now(timezone.utc)

    mock_db.refresh.side_effect = _refresh
    data = bug_r.BugReportCreate(description="A real bug happened here")
    out = await bug_r.create_bug_report(data, current_user=user(id=7, name="Al", email="al@b.cz"),
                                        db=mock_db)
    assert out.user_id == 7
    assert out.user_name == "Al"
    mock_db.add.assert_called_once()


async def test_bug_get_mine(mock_db):
    rep = SimpleNamespace(id=1, user_id=7, description="d", category="bug",
                          status="open", browser_info=None, page_url=None,
                          screen_resolution=None, user_settings_json=None,
                          created_at=datetime.now(timezone.utc))
    mock_db.execute.return_value = make_result(scalars_all=[rep])
    out = await bug_r.get_my_bug_reports(current_user=user(id=7, name="Al", email="al@b.cz"),
                                         db=mock_db)
    assert out.total == 1
    assert out.reports[0].user_email == "al@b.cz"


async def test_bug_get_all_forbidden(mock_db):
    with pytest.raises(HTTPException) as e:
        await bug_r.get_all_bug_reports(current_user=user(id=1), db=mock_db)
    assert e.value.status_code == 403


async def test_bug_get_all_admin(mock_db):
    reporter = SimpleNamespace(name="Bob", email="bob@b.cz")
    rep = SimpleNamespace(id=1, user_id=3, description="d", category="bug",
                          status="open", browser_info=None, page_url=None,
                          screen_resolution=None, user_settings_json=None,
                          created_at=datetime.now(timezone.utc), user=reporter)
    mock_db.execute.return_value = make_result(scalars_all=[rep])
    out = await bug_r.get_all_bug_reports(current_user=admin(), db=mock_db)
    assert out.total == 1
    assert out.reports[0].user_name == "Bob"
