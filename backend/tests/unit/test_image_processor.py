"""Unit tests for services.image_processor.

Strategy
--------
* The module imports its ML detection helpers at module level
  (``from ml.detection import detect_cells_in_image, Detection, create_mip,
  normalize_image``) so the names live in ``services.image_processor``. We
  patch them there.
* The DB is the shared ``mock_db`` AsyncMock. ``get_db_context`` is patched to
  yield it through an async context manager.
* ``Image`` records are lightweight ``SimpleNamespace`` stand-ins (no SQLAlchemy
  mapper configuration needed). ``CellCrop`` is patched with a plain fake.
* Real numpy arrays / PIL images and ``tmp_path`` exercise the image-math and
  file-IO helpers for real.
"""
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from PIL import Image as PILImage

import services.image_processor as ip
from models.image import UploadStatus


# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #
@dataclass
class FakeDetection:
    """Mirror of ml.detection.Detection (dataclass with bbox + confidence)."""
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    confidence: float
    class_id: int = 0


class FakeCellCrop:
    """Plain stand-in for the SQLAlchemy CellCrop model."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        # id assigned during db.flush() in real code; emulate after-flush state
        self.id = kwargs.get("id")


def make_image(file_path, **overrides):
    """Build a lightweight Image-like record."""
    attrs = dict(
        id=1,
        original_filename="sample.tif",
        file_path=str(file_path),
        mip_path=None,
        sum_path=None,
        thumbnail_path=None,
        status=UploadStatus.UPLOADED,
        z_slices=None,
        height=None,
        width=None,
        source_discarded=False,
        error_message=None,
        detect_cells=True,
        map_protein_id=None,
        processed_at=None,
    )
    attrs.update(overrides)
    return SimpleNamespace(**attrs)


@pytest.fixture
def mock_db():
    """AsyncSession mock with an async-context-manager begin_nested()."""
    from tests.unit.conftest import make_result

    db = AsyncMock(name="AsyncSession")
    db.execute.return_value = make_result()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    @asynccontextmanager
    async def _nested():
        yield MagicMock()

    db.begin_nested = MagicMock(side_effect=lambda: _nested())
    return db


@pytest.fixture
def patch_db(mock_db):
    """Patch get_db_context to yield the mock_db."""
    @asynccontextmanager
    async def _ctx():
        yield mock_db

    with patch.object(ip, "get_db_context", _ctx):
        yield mock_db


@pytest.fixture(autouse=True)
def stub_ml_modules():
    """Stub the lazily-imported ML service modules so their imports succeed and
    the happy-path feature-extraction branches run without GPU work."""
    features = MagicMock(name="ml.features")
    features.extract_features_for_crops = AsyncMock(
        return_value={"success": 1, "failed": 0}
    )
    features.extract_features_for_images = AsyncMock(
        return_value={"success": 1, "failed": 0}
    )

    seg = MagicMock(name="services.segmentation_service")
    seg.compute_sam_embedding = AsyncMock(
        return_value={"success": True, "embedding_size": 2 * 1024 * 1024}
    )

    rag = MagicMock(name="services.rag_service")
    rag.index_fov_image = AsyncMock(return_value=True)

    with patch.dict(sys.modules, {
        "ml.features": features,
        "services.segmentation_service": seg,
        "services.rag_service": rag,
    }):
        yield SimpleNamespace(features=features, seg=seg, rag=rag)


def _detection_result(*dets):
    """Configure execute() to return an Image; helper to set scalar afterwards."""


# --------------------------------------------------------------------------- #
# create_sum_projection (pure math)
# --------------------------------------------------------------------------- #
def test_create_sum_projection_normalizes_to_uint8():
    zstack = np.array(
        [[[0, 100], [50, 200]], [[10, 50], [100, 55]]], dtype=np.uint16
    )
    out = ip.create_sum_projection(zstack)
    assert out.dtype == np.uint8
    assert out.max() == 255  # the brightest summed pixel is scaled to 255
    assert out.shape == (2, 2)


def test_create_sum_projection_all_zero():
    zstack = np.zeros((3, 4, 4), dtype=np.uint8)
    out = ip.create_sum_projection(zstack)
    assert out.dtype == np.uint8
    assert out.max() == 0  # max==0 branch: no normalization


# --------------------------------------------------------------------------- #
# _load_image
# --------------------------------------------------------------------------- #
async def test_load_image_tiff_zstack(tmp_path):
    import tifffile

    path = tmp_path / "stack.tif"
    data = np.random.randint(0, 255, size=(3, 8, 8), dtype=np.uint8)
    tifffile.imwrite(str(path), data)

    proc = ip.ImageProcessor(1)
    out = await proc._load_image(str(path))
    assert out is not None
    assert out.shape == (3, 8, 8)


async def test_load_image_png(tmp_path):
    path = tmp_path / "img.png"
    PILImage.fromarray(
        np.random.randint(0, 255, size=(8, 8), dtype=np.uint8)
    ).save(path)

    proc = ip.ImageProcessor(1)
    out = await proc._load_image(str(path))
    assert out is not None
    assert out.shape == (8, 8)


async def test_load_image_failure_returns_none(tmp_path):
    proc = ip.ImageProcessor(1)
    out = await proc._load_image(str(tmp_path / "does_not_exist.png"))
    assert out is None  # exception path -> None


# --------------------------------------------------------------------------- #
# _save_projection / _save_thumbnail / _save_crop  (real PIL + real normalize)
# --------------------------------------------------------------------------- #
async def test_save_projection_uint8_passthrough(tmp_path):
    img = make_image(tmp_path / "img.tif")
    proj = np.random.randint(0, 255, size=(8, 8), dtype=np.uint8)
    proc = ip.ImageProcessor(1)
    path = await proc._save_projection(img, proj, "mip")
    assert path.exists()
    assert path.name == "img_mip.png"


async def test_save_projection_non_uint8_normalizes(tmp_path):
    img = make_image(tmp_path / "img.tif")
    proj = (np.arange(64).reshape(8, 8)).astype(np.float32)
    proc = ip.ImageProcessor(1)
    # real normalize_image is mocked away in conftest's torch/cv2 stubs but
    # ml.detection.normalize_image is real numpy -> patch only if needed.
    with patch.object(ip, "normalize_image", lambda a: a.astype(np.uint8)):
        path = await proc._save_projection(img, proj, "sum")
    assert path.exists()
    assert path.name == "img_sum.png"


async def test_save_thumbnail(tmp_path):
    img = make_image(tmp_path / "img.tif")
    mip = np.random.randint(0, 255, size=(512, 512), dtype=np.uint8)
    proc = ip.ImageProcessor(1)
    with patch.object(ip, "normalize_image", lambda a: a.astype(np.uint8)):
        path = await proc._save_thumbnail(img, mip)
    assert path.exists()
    assert path.name == "img_thumb.png"
    # thumbnail must be downscaled to <= 256x256
    with PILImage.open(path) as t:
        assert max(t.size) <= 256


async def test_save_crop(tmp_path):
    img = make_image(tmp_path / "img.tif")
    det = FakeDetection(bbox_x=2, bbox_y=3, bbox_w=4, bbox_h=4, confidence=0.9)
    crop = np.random.randint(0, 255, size=(4, 4), dtype=np.uint8)
    proc = ip.ImageProcessor(1)
    with patch.object(ip, "normalize_image", lambda a: a.astype(np.uint8)):
        path = await proc._save_crop(img, det, crop, "mip")
    assert path.exists()
    assert path.parent.name == "crops"
    assert path.name == "cell_2_3_mip.png"


# --------------------------------------------------------------------------- #
# _crop_cell / _create_cell_crop (pure)
# --------------------------------------------------------------------------- #
def test_crop_cell_slices_bbox():
    proj = np.arange(100).reshape(10, 10)
    det = FakeDetection(bbox_x=2, bbox_y=1, bbox_w=3, bbox_h=4, confidence=0.5)
    proc = ip.ImageProcessor(1)
    crop = proc._crop_cell(proj, det)
    assert crop.shape == (4, 3)
    np.testing.assert_array_equal(crop, proj[1:5, 2:5])


def test_create_cell_crop_builds_record():
    img = make_image("/tmp/x.tif", id=7, map_protein_id=42)
    mip_crop = np.full((4, 4), 10, dtype=np.uint8)
    proc = ip.ImageProcessor(1)
    with patch.object(ip, "CellCrop", FakeCellCrop):
        crop = proc._create_cell_crop(
            image=img,
            mip_crop=mip_crop,
            mip_path="/tmp/crops/cell.png",
            sum_crop_path="/tmp/crops/cell_sum.png",
            bbox=(1, 2, 3, 4),
            confidence=0.77,
        )
    assert crop.image_id == 7
    assert crop.map_protein_id == 42
    assert crop.bbox_x == 1 and crop.bbox_w == 3
    assert crop.detection_confidence == 0.77
    assert crop.mip_path == "/tmp/crops/cell.png"
    assert crop.sum_crop_path == "/tmp/crops/cell_sum.png"
    assert crop.mean_intensity == 10.0


def test_create_cell_crop_no_sum_path():
    img = make_image("/tmp/x.tif")
    proc = ip.ImageProcessor(1)
    with patch.object(ip, "CellCrop", FakeCellCrop):
        crop = proc._create_cell_crop(
            image=img,
            mip_crop=np.zeros((2, 2), dtype=np.uint8),
            mip_path="/tmp/c.png",
            sum_crop_path=None,
            bbox=(0, 0, 2, 2),
            confidence=0.1,
        )
    assert crop.sum_crop_path is None  # None branch


# --------------------------------------------------------------------------- #
# _extract_features_for_crops
# --------------------------------------------------------------------------- #
async def test_extract_features_for_crops_empty(mock_db):
    proc = ip.ImageProcessor(1)
    # empty list returns immediately, no import attempted
    await proc._extract_features_for_crops([], mock_db)


async def test_extract_features_for_crops_success(mock_db, stub_ml_modules):
    proc = ip.ImageProcessor(1)
    crops = [FakeCellCrop(id=1, image_id=5)]
    await proc._extract_features_for_crops(crops, mock_db)
    stub_ml_modules.features.extract_features_for_crops.assert_awaited_once()


async def test_extract_features_for_crops_partial_failure(mock_db, stub_ml_modules):
    stub_ml_modules.features.extract_features_for_crops.return_value = {
        "success": 1, "failed": 2,
    }
    proc = ip.ImageProcessor(1)
    crops = [FakeCellCrop(id=1, image_id=5)]
    await proc._extract_features_for_crops(crops, mock_db)  # warning branch


async def test_extract_features_for_crops_import_error(mock_db):
    proc = ip.ImageProcessor(1)
    crops = [FakeCellCrop(id=1, image_id=5)]
    with patch.dict(sys.modules, {"ml.features": None}):  # forces ImportError
        await proc._extract_features_for_crops(crops, mock_db)


async def test_extract_features_for_crops_runtime_error(mock_db, stub_ml_modules):
    stub_ml_modules.features.extract_features_for_crops.side_effect = RuntimeError("gpu")
    proc = ip.ImageProcessor(1)
    crops = [FakeCellCrop(id=1, image_id=5)]
    await proc._extract_features_for_crops(crops, mock_db)


async def test_extract_features_for_crops_unexpected_error(mock_db, stub_ml_modules):
    stub_ml_modules.features.extract_features_for_crops.side_effect = ValueError("boom")
    proc = ip.ImageProcessor(1)
    crops = [FakeCellCrop(id=1, image_id=5)]
    await proc._extract_features_for_crops(crops, mock_db)


# --------------------------------------------------------------------------- #
# _extract_fov_embedding
# --------------------------------------------------------------------------- #
async def test_extract_fov_embedding_success(mock_db, stub_ml_modules):
    proc = ip.ImageProcessor(1)
    await proc._extract_fov_embedding(mock_db, make_image("/tmp/x.tif"))
    stub_ml_modules.features.extract_features_for_images.assert_awaited_once()


async def test_extract_fov_embedding_failed(mock_db, stub_ml_modules):
    stub_ml_modules.features.extract_features_for_images.return_value = {
        "success": 0, "failed": 1,
    }
    proc = ip.ImageProcessor(1)
    await proc._extract_fov_embedding(mock_db, make_image("/tmp/x.tif"))


async def test_extract_fov_embedding_import_error(mock_db):
    proc = ip.ImageProcessor(1)
    with patch.dict(sys.modules, {"ml.features": None}):
        await proc._extract_fov_embedding(mock_db, make_image("/tmp/x.tif"))


async def test_extract_fov_embedding_runtime_error(mock_db, stub_ml_modules):
    stub_ml_modules.features.extract_features_for_images.side_effect = RuntimeError("gpu")
    proc = ip.ImageProcessor(1)
    await proc._extract_fov_embedding(mock_db, make_image("/tmp/x.tif"))


async def test_extract_fov_embedding_unexpected_error(mock_db, stub_ml_modules):
    stub_ml_modules.features.extract_features_for_images.side_effect = ValueError("boom")
    proc = ip.ImageProcessor(1)
    await proc._extract_fov_embedding(mock_db, make_image("/tmp/x.tif"))


# --------------------------------------------------------------------------- #
# _compute_sam_embedding
# --------------------------------------------------------------------------- #
async def test_compute_sam_embedding_success(mock_db, stub_ml_modules):
    proc = ip.ImageProcessor(1)
    await proc._compute_sam_embedding(mock_db, make_image("/tmp/x.tif"))
    stub_ml_modules.seg.compute_sam_embedding.assert_awaited_once()


async def test_compute_sam_embedding_failed(mock_db, stub_ml_modules):
    stub_ml_modules.seg.compute_sam_embedding.return_value = {
        "success": False, "error": "no model",
    }
    proc = ip.ImageProcessor(1)
    await proc._compute_sam_embedding(mock_db, make_image("/tmp/x.tif"))


async def test_compute_sam_embedding_import_error(mock_db):
    proc = ip.ImageProcessor(1)
    with patch.dict(sys.modules, {"services.segmentation_service": None}):
        await proc._compute_sam_embedding(mock_db, make_image("/tmp/x.tif"))


async def test_compute_sam_embedding_runtime_error(mock_db, stub_ml_modules):
    stub_ml_modules.seg.compute_sam_embedding.side_effect = RuntimeError("gpu")
    proc = ip.ImageProcessor(1)
    await proc._compute_sam_embedding(mock_db, make_image("/tmp/x.tif"))


async def test_compute_sam_embedding_unexpected_error(mock_db, stub_ml_modules):
    stub_ml_modules.seg.compute_sam_embedding.side_effect = ValueError("boom")
    proc = ip.ImageProcessor(1)
    await proc._compute_sam_embedding(mock_db, make_image("/tmp/x.tif"))


# --------------------------------------------------------------------------- #
# _extract_rag_embedding
# --------------------------------------------------------------------------- #
async def test_extract_rag_embedding_success(mock_db, stub_ml_modules):
    proc = ip.ImageProcessor(1)
    await proc._extract_rag_embedding(mock_db, make_image("/tmp/x.tif"))
    stub_ml_modules.rag.index_fov_image.assert_awaited_once()


async def test_extract_rag_embedding_failed(mock_db, stub_ml_modules):
    stub_ml_modules.rag.index_fov_image.return_value = False
    proc = ip.ImageProcessor(1)
    await proc._extract_rag_embedding(mock_db, make_image("/tmp/x.tif"))


async def test_extract_rag_embedding_import_error(mock_db):
    proc = ip.ImageProcessor(1)
    with patch.dict(sys.modules, {"services.rag_service": None}):
        await proc._extract_rag_embedding(mock_db, make_image("/tmp/x.tif"))


async def test_extract_rag_embedding_runtime_error(mock_db, stub_ml_modules):
    stub_ml_modules.rag.index_fov_image.side_effect = RuntimeError("gpu")
    proc = ip.ImageProcessor(1)
    await proc._extract_rag_embedding(mock_db, make_image("/tmp/x.tif"))


async def test_extract_rag_embedding_unexpected_error(mock_db, stub_ml_modules):
    stub_ml_modules.rag.index_fov_image.side_effect = ValueError("boom")
    proc = ip.ImageProcessor(1)
    await proc._extract_rag_embedding(mock_db, make_image("/tmp/x.tif"))


# --------------------------------------------------------------------------- #
# _run_detection
# --------------------------------------------------------------------------- #
async def test_run_detection_with_cells(mock_db, stub_ml_modules):
    img = make_image("/tmp/x.tif", id=3)
    mip = np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    sum_proj = np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    dets = [
        FakeDetection(bbox_x=1, bbox_y=1, bbox_w=4, bbox_h=4, confidence=0.9),
        FakeDetection(bbox_x=5, bbox_y=5, bbox_w=3, bbox_h=3, confidence=0.8),
    ]
    proc = ip.ImageProcessor(3)
    with patch.object(ip, "normalize_image", lambda a: a.astype(np.uint8)), \
         patch.object(ip, "detect_cells_in_image", AsyncMock(return_value=dets)), \
         patch.object(ip, "CellCrop", FakeCellCrop), \
         patch.object(proc, "_save_crop", AsyncMock(return_value="/tmp/crop.png")):
        await proc._run_detection(mock_db, img, mip, sum_proj, True)

    assert img.status == UploadStatus.READY
    assert img.processed_at is not None
    # two detections -> two db.add calls
    assert mock_db.add.call_count == 2
    mock_db.flush.assert_awaited()


async def test_run_detection_empty(mock_db, stub_ml_modules):
    img = make_image("/tmp/x.tif", id=3)
    mip = np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    proc = ip.ImageProcessor(3)
    with patch.object(ip, "normalize_image", lambda a: a.astype(np.uint8)), \
         patch.object(ip, "detect_cells_in_image", AsyncMock(return_value=[])), \
         patch.object(ip, "CellCrop", FakeCellCrop):
        await proc._run_detection(mock_db, img, mip, None, False)

    assert img.status == UploadStatus.READY
    assert mock_db.add.call_count == 0  # no detections -> no crops


async def test_run_detection_no_sum_proj(mock_db, stub_ml_modules):
    """detections present but sum_proj None -> no sum crop branch."""
    img = make_image("/tmp/x.tif", id=3)
    mip = np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    dets = [FakeDetection(bbox_x=1, bbox_y=1, bbox_w=4, bbox_h=4, confidence=0.9)]
    proc = ip.ImageProcessor(3)
    saved_suffixes = []

    async def fake_save_crop(image, det, crop, suffix):
        saved_suffixes.append(suffix)
        return f"/tmp/{suffix}.png"

    with patch.object(ip, "normalize_image", lambda a: a.astype(np.uint8)), \
         patch.object(ip, "detect_cells_in_image", AsyncMock(return_value=dets)), \
         patch.object(ip, "CellCrop", FakeCellCrop), \
         patch.object(proc, "_save_crop", side_effect=fake_save_crop):
        await proc._run_detection(mock_db, img, mip, None, False)

    assert saved_suffixes == ["mip"]  # only mip crop saved, never "sum"


# --------------------------------------------------------------------------- #
# process_upload_only
# --------------------------------------------------------------------------- #
async def test_process_upload_only_image_not_found(patch_db):
    db = patch_db
    from tests.unit.conftest import make_result
    db.execute.return_value = make_result(scalar=None)

    proc = ip.ImageProcessor(99)
    assert await proc.process_upload_only() is False


async def test_process_upload_only_zstack(patch_db, tmp_path):
    import tifffile

    src = tmp_path / "stack.tif"
    data = np.random.randint(0, 255, size=(3, 16, 16), dtype=np.uint8)
    tifffile.imwrite(str(src), data)
    img = make_image(src, id=10)

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(10)
    with patch.object(ip, "normalize_image", lambda a: a.astype(np.uint8)), \
         patch.object(ip, "create_mip", lambda d: np.max(d, axis=0).astype(np.uint8)):
        ok = await proc.process_upload_only()

    assert ok is True
    assert img.status == UploadStatus.UPLOADED
    assert img.z_slices == 3
    assert img.height == 16 and img.width == 16
    assert img.mip_path is not None
    assert img.sum_path is not None
    assert img.thumbnail_path is not None
    assert img.source_discarded is True
    # original z-stack file deleted after commit
    assert not src.exists()


async def test_process_upload_only_2d_tiff_converts(patch_db, tmp_path):
    import tifffile

    src = tmp_path / "flat.tif"
    data = np.random.randint(0, 255, size=(16, 16), dtype=np.uint8)
    tifffile.imwrite(str(src), data)
    img = make_image(src, id=11)

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(11)
    with patch.object(ip, "normalize_image", lambda a: a.astype(np.uint8)):
        ok = await proc.process_upload_only()

    assert ok is True
    assert img.status == UploadStatus.UPLOADED
    assert img.height == 16 and img.width == 16
    assert img.mip_path is not None  # 2D TIFF converted to PNG
    assert img.thumbnail_path is not None
    assert src.exists()  # 2D source kept (original_path is None)


async def test_process_upload_only_2d_png_no_conversion(patch_db, tmp_path):
    src = tmp_path / "flat.png"
    PILImage.fromarray(
        np.random.randint(0, 255, size=(16, 16), dtype=np.uint8)
    ).save(src)
    img = make_image(src, id=12, original_filename="flat.png")

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(12)
    with patch.object(ip, "normalize_image", lambda a: a.astype(np.uint8)):
        ok = await proc.process_upload_only()

    assert ok is True
    assert img.mip_path is None  # PNG: no mip conversion
    assert img.thumbnail_path is not None
    assert src.exists()


async def test_process_upload_only_load_failure(patch_db, tmp_path):
    img = make_image(tmp_path / "missing.png", id=13)
    from tests.unit.conftest import make_result
    # first execute -> image found; second (in except) -> image found again
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(13)
    ok = await proc.process_upload_only()

    assert ok is False
    assert img.status == UploadStatus.ERROR
    assert "Failed to load image" in img.error_message


async def test_process_upload_only_error_image_gone_in_except(patch_db, tmp_path):
    """Exception occurs, and the re-fetch in except finds no image."""
    img = make_image(tmp_path / "missing.png", id=14)
    from tests.unit.conftest import make_result

    # First call returns image, the except-block re-fetch returns None.
    patch_db.execute.side_effect = [
        make_result(scalar=img),
        make_result(scalar=None),
    ]

    proc = ip.ImageProcessor(14)
    ok = await proc.process_upload_only()
    assert ok is False


# --------------------------------------------------------------------------- #
# process_batch
# --------------------------------------------------------------------------- #
async def test_process_batch_image_not_found(patch_db):
    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=None)
    proc = ip.ImageProcessor(99)
    assert await proc.process_batch(detect_cells=True) is False


async def test_process_batch_detect_cells_with_mip(patch_db, tmp_path):
    mip_file = tmp_path / "img_mip.png"
    PILImage.fromarray(
        np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    ).save(mip_file)
    img = make_image(tmp_path / "img.tif", id=20, mip_path=str(mip_file),
                     status=UploadStatus.UPLOADED)

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(20)
    with patch.object(proc, "_run_detection", AsyncMock()) as rd, \
         patch.object(proc, "_extract_fov_embedding", AsyncMock()), \
         patch.object(proc, "_compute_sam_embedding", AsyncMock()), \
         patch.object(proc, "_extract_rag_embedding", AsyncMock()):
        ok = await proc.process_batch(detect_cells=True, map_protein_id=7)

    assert ok is True
    assert img.detect_cells is True
    assert img.map_protein_id == 7
    rd.assert_awaited_once()


async def test_process_batch_no_detect(patch_db, tmp_path):
    mip_file = tmp_path / "img_mip.png"
    PILImage.fromarray(
        np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    ).save(mip_file)
    img = make_image(tmp_path / "img.tif", id=21, mip_path=str(mip_file))

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(21)
    with patch.object(proc, "_extract_fov_embedding", AsyncMock()), \
         patch.object(proc, "_compute_sam_embedding", AsyncMock()), \
         patch.object(proc, "_extract_rag_embedding", AsyncMock()):
        ok = await proc.process_batch(detect_cells=False)

    assert ok is True
    assert img.status == UploadStatus.READY
    assert img.processed_at is not None


async def test_process_batch_2d_no_mip_uses_original(patch_db, tmp_path):
    """mip_path is None but file_path exists -> uses original as MIP."""
    src = tmp_path / "flat.png"
    PILImage.fromarray(
        np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    ).save(src)
    img = make_image(src, id=22, mip_path=None)

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(22)
    with patch.object(proc, "_run_detection", AsyncMock()), \
         patch.object(proc, "_extract_fov_embedding", AsyncMock()), \
         patch.object(proc, "_compute_sam_embedding", AsyncMock()), \
         patch.object(proc, "_extract_rag_embedding", AsyncMock()):
        ok = await proc.process_batch(detect_cells=True)

    assert ok is True


async def test_process_batch_mip_fallback(patch_db, tmp_path):
    """mip_path set but file missing -> warning fallback to original file."""
    src = tmp_path / "orig.png"
    PILImage.fromarray(
        np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    ).save(src)
    img = make_image(src, id=23, mip_path=str(tmp_path / "ghost_mip.png"),
                     error_message=None)

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(23)
    with patch.object(proc, "_run_detection", AsyncMock()), \
         patch.object(proc, "_extract_fov_embedding", AsyncMock()), \
         patch.object(proc, "_compute_sam_embedding", AsyncMock()), \
         patch.object(proc, "_extract_rag_embedding", AsyncMock()):
        ok = await proc.process_batch(detect_cells=False)

    assert ok is True
    assert "MIP fallback used" in img.error_message


async def test_process_batch_mip_fallback_existing_warning(patch_db, tmp_path):
    """Fallback when the warning is already present in error_message (no dup)."""
    src = tmp_path / "orig.png"
    PILImage.fromarray(
        np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    ).save(src)
    existing = "Warning: Phase 1 may be incomplete (MIP fallback used). prior"
    img = make_image(src, id=24, mip_path=str(tmp_path / "ghost_mip.png"),
                     error_message=existing)

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(24)
    with patch.object(proc, "_run_detection", AsyncMock()), \
         patch.object(proc, "_extract_fov_embedding", AsyncMock()), \
         patch.object(proc, "_compute_sam_embedding", AsyncMock()), \
         patch.object(proc, "_extract_rag_embedding", AsyncMock()):
        ok = await proc.process_batch(detect_cells=False)

    assert ok is True
    # warning not duplicated
    assert img.error_message.count("MIP fallback used") == 1


async def test_process_batch_mip_cannot_load_raises(patch_db, tmp_path):
    """All MIP paths missing AND load fails -> ValueError -> ERROR status."""
    img = make_image(tmp_path / "ghost.png", id=25,
                     mip_path=str(tmp_path / "ghost_mip.png"))

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(25)
    with patch.object(proc, "_extract_fov_embedding", AsyncMock()), \
         patch.object(proc, "_compute_sam_embedding", AsyncMock()), \
         patch.object(proc, "_extract_rag_embedding", AsyncMock()):
        ok = await proc.process_batch(detect_cells=False)

    assert ok is False
    assert img.status == UploadStatus.ERROR
    assert "Cannot load MIP" in img.error_message


async def test_process_batch_with_sum_proj(patch_db, tmp_path):
    """sum_path exists -> sum_proj loaded and passed to _run_detection."""
    mip_file = tmp_path / "img_mip.png"
    sum_file = tmp_path / "img_sum.png"
    arr = np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    PILImage.fromarray(arr).save(mip_file)
    PILImage.fromarray(arr).save(sum_file)
    img = make_image(tmp_path / "img.tif", id=26, mip_path=str(mip_file),
                     sum_path=str(sum_file))

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(26)
    with patch.object(proc, "_run_detection", AsyncMock()) as rd, \
         patch.object(proc, "_extract_fov_embedding", AsyncMock()), \
         patch.object(proc, "_compute_sam_embedding", AsyncMock()), \
         patch.object(proc, "_extract_rag_embedding", AsyncMock()):
        ok = await proc.process_batch(detect_cells=True)

    assert ok is True
    # is_zstack arg (5th positional) is True because sum_proj is not None
    args = rd.await_args.args
    assert args[-1] is True


async def test_process_batch_wrong_status_warns(patch_db, tmp_path):
    """Status not in allowed set logs a warning but still proceeds."""
    mip_file = tmp_path / "img_mip.png"
    PILImage.fromarray(
        np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    ).save(mip_file)
    img = make_image(tmp_path / "img.tif", id=27, mip_path=str(mip_file),
                     status=UploadStatus.PROCESSING)

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(27)
    with patch.object(proc, "_extract_fov_embedding", AsyncMock()), \
         patch.object(proc, "_compute_sam_embedding", AsyncMock()), \
         patch.object(proc, "_extract_rag_embedding", AsyncMock()):
        ok = await proc.process_batch(detect_cells=False)

    assert ok is True


async def test_process_batch_exception_sets_error(patch_db, tmp_path):
    mip_file = tmp_path / "img_mip.png"
    PILImage.fromarray(
        np.random.randint(0, 255, size=(20, 20), dtype=np.uint8)
    ).save(mip_file)
    img = make_image(tmp_path / "img.tif", id=28, mip_path=str(mip_file))

    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    proc = ip.ImageProcessor(28)
    with patch.object(proc, "_run_detection",
                      AsyncMock(side_effect=RuntimeError("detect boom"))):
        ok = await proc.process_batch(detect_cells=True)

    assert ok is False
    assert img.status == UploadStatus.ERROR
    assert "detect boom" in img.error_message


# --------------------------------------------------------------------------- #
# process() (legacy combined pipeline)
# --------------------------------------------------------------------------- #
async def test_process_phase1_fails_short_circuits():
    proc = ip.ImageProcessor(1)
    with patch.object(proc, "process_upload_only", AsyncMock(return_value=False)) as p1, \
         patch.object(proc, "process_batch", AsyncMock(return_value=True)) as p2:
        ok = await proc.process()
    assert ok is False
    p1.assert_awaited_once()
    p2.assert_not_awaited()


async def test_process_runs_both_phases():
    proc = ip.ImageProcessor(1, detect_cells=False)
    with patch.object(proc, "process_upload_only", AsyncMock(return_value=True)), \
         patch.object(proc, "process_batch", AsyncMock(return_value=True)) as p2:
        ok = await proc.process()
    assert ok is True
    p2.assert_awaited_once_with(detect_cells=False)


# --------------------------------------------------------------------------- #
# Module-level entrypoints
# --------------------------------------------------------------------------- #
async def test_process_image_entrypoint():
    with patch.object(ip.ImageProcessor, "process",
                      AsyncMock(return_value=True)) as p:
        ok = await ip.process_image(5, detect_cells=False)
    assert ok is True
    p.assert_awaited_once()


async def test_process_upload_only_entrypoint():
    with patch.object(ip.ImageProcessor, "process_upload_only",
                      AsyncMock(return_value=True)) as p:
        ok = await ip.process_upload_only(5)
    assert ok is True
    p.assert_awaited_once()


async def test_process_batch_entrypoint():
    with patch.object(ip.ImageProcessor, "process_batch",
                      AsyncMock(return_value=True)) as p:
        ok = await ip.process_batch(5, detect_cells=True, map_protein_id=3)
    assert ok is True
    p.assert_awaited_once_with(True, 3)


# --------------------------------------------------------------------------- #
# _run_background_task + background wrappers
# --------------------------------------------------------------------------- #
async def test_run_background_task_success():
    ran = {}

    async def coro():
        ran["x"] = True

    await ip._run_background_task("Task", 1, coro())
    assert ran["x"] is True


async def test_run_background_task_cancelled_reraises():
    import asyncio

    async def coro():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await ip._run_background_task("Task", 1, coro())


async def test_run_background_task_system_error_reraises():
    async def coro():
        raise MemoryError("oom")

    with pytest.raises(MemoryError):
        await ip._run_background_task("Task", 1, coro())


async def test_run_background_task_generic_error_updates_status():
    async def coro():
        raise ValueError("regular boom")

    with patch.object(ip, "_update_error_status", AsyncMock()) as upd:
        await ip._run_background_task("Task", 42, coro())
    upd.assert_awaited_once_with(42, "regular boom")


async def test_process_image_background():
    # MagicMock (not AsyncMock) so no un-awaited coroutine is created.
    with patch.object(ip, "process_image", MagicMock(return_value="coro")), \
         patch.object(ip, "_run_background_task", AsyncMock()) as run:
        await ip.process_image_background(5, detect_cells=True)
    run.assert_awaited_once_with("Background processing", 5, "coro")


async def test_process_upload_only_background():
    with patch.object(ip, "process_upload_only", MagicMock(return_value="coro")), \
         patch.object(ip, "_run_background_task", AsyncMock()) as run:
        await ip.process_upload_only_background(5)
    run.assert_awaited_once_with("Phase 1 processing", 5, "coro")


async def test_process_batch_background():
    with patch.object(ip, "process_batch", MagicMock(return_value="coro")), \
         patch.object(ip, "_run_background_task", AsyncMock()) as run:
        await ip.process_batch_background(5, detect_cells=True, map_protein_id=2)
    run.assert_awaited_once_with("Phase 2 processing", 5, "coro")


# --------------------------------------------------------------------------- #
# _update_error_status
# --------------------------------------------------------------------------- #
async def test_update_error_status_sets_error(patch_db):
    img = make_image("/tmp/x.tif", id=30, status=UploadStatus.PROCESSING)
    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    await ip._update_error_status(30, "boom")
    assert img.status == UploadStatus.ERROR
    assert "Unexpected error: boom" in img.error_message


async def test_update_error_status_already_error_noop(patch_db):
    img = make_image("/tmp/x.tif", id=31, status=UploadStatus.ERROR,
                     error_message="original")
    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=img)

    await ip._update_error_status(31, "boom")
    # already ERROR -> not overwritten
    assert img.error_message == "original"


async def test_update_error_status_image_missing(patch_db):
    from tests.unit.conftest import make_result
    patch_db.execute.return_value = make_result(scalar=None)
    # should not raise
    await ip._update_error_status(99, "boom")


async def test_update_error_status_db_exception_swallowed():
    """DB failure inside the helper is caught and logged, not raised."""
    @asynccontextmanager
    async def _ctx():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    with patch.object(ip, "get_db_context", _ctx):
        await ip._update_error_status(99, "boom")  # must not raise
