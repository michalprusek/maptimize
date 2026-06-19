"""Unit tests for services.import_service (DB + Redis mocked, in-memory zips).

These tests run in-process under coverage with the AsyncMock AsyncSession from
``conftest.mock_db`` and an in-memory dict standing in for Redis job state. Real
SQLAlchemy models are instantiated; ``db.flush`` is patched to assign sequential
integer ids so id-mapping logic works without a live database.
"""
import io
import json
import os
import zipfile
from datetime import datetime, timezone

import numpy as np
import pytest
from PIL import Image as PILImage

from schemas.export_import import (
    CropImportData,
    ImportFormat,
    ImportJobData,
)
from services import import_service as mod
from services.import_service import (
    ImportService,
    create_error_validation_result,
    extract_projections_from_zip,
    find_subdirectories,
    is_annotation_file,
    is_image_file,
    load_embeddings_from_zip,
    lookup_protein_by_name,
    write_file_from_zip,
)
from models import CellCrop, Experiment, Image, MapProtein
from models.segmentation import FOVSegmentationMask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_result(*, scalar=None, scalars_all=None, first=None, fetchall=None,
                rowcount=None):
    """Local copy of conftest.make_result (conftest isn't importable as a module)."""
    from unittest.mock import MagicMock

    result = MagicMock(name="Result")
    result.scalar_one_or_none.return_value = scalar
    result.scalar.return_value = scalar if scalar is not None else (
        first if first is not None else None
    )
    scalars = MagicMock(name="ScalarResult")
    scalars.all.return_value = scalars_all if scalars_all is not None else []
    scalars.first.return_value = (scalars_all or [None])[0] if scalars_all else None
    result.scalars.return_value = scalars
    result.first.return_value = first
    result.fetchall.return_value = fetchall if fetchall is not None else []
    result.all.return_value = fetchall if fetchall is not None else []
    if rowcount is not None:
        result.rowcount = rowcount
    return result


def make_zip(files: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP from a {arcname: bytes} mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_tiff(width: int = 8, height: int = 6) -> bytes:
    """Real single-page TIFF image bytes."""
    buf = io.BytesIO()
    PILImage.fromarray(np.zeros((height, width), dtype=np.uint8)).save(buf, format="TIFF")
    return buf.getvalue()


def make_png(width: int = 8, height: int = 6) -> bytes:
    buf = io.BytesIO()
    PILImage.fromarray(np.zeros((height, width), dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def make_mask_png(size: int = 40) -> bytes:
    """A filled-square binary mask PNG that yields >=3 polygon edge points."""
    a = np.zeros((size, size), dtype=np.uint8)
    a[10:30, 10:30] = 255
    buf = io.BytesIO()
    PILImage.fromarray(a).save(buf, format="PNG")
    return buf.getvalue()


def coco_annotations(filename: str = "a.png") -> bytes:
    """Minimal valid COCO annotation JSON referencing one image and one bbox."""
    return json.dumps({
        "images": [{"id": 1, "file_name": filename}],
        "categories": [{"id": 1, "name": "cell"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [1, 2, 5, 6], "score": 0.9}
        ],
    }).encode("utf-8")


class IdAssigningDB:
    """Wrap mock_db so flush() assigns sequential ids to freshly-added objects."""

    def __init__(self, mock_db):
        self.db = mock_db
        self._pending: list = []
        self._counter = 0

        # MagicMock still records call_args even with a side_effect set, so the
        # side_effect only needs to track pending objects (do NOT re-call add).
        def add(obj):
            self._pending.append(obj)

        async def flush():
            for obj in self._pending:
                if getattr(obj, "id", None) is None:
                    self._counter += 1
                    obj.id = self._counter
            self._pending.clear()

        mock_db.add.side_effect = add
        mock_db.flush.side_effect = flush


def fresh_service():
    """ImportService with in-memory job storage replacing Redis."""
    svc = ImportService()
    store: dict[str, str] = {}

    async def save_job(job):
        store[job.job_id] = job.model_dump_json()

    async def get_job(job_id):
        data = store.get(job_id)
        return ImportJobData.model_validate_json(data) if data else None

    svc._save_job = save_job
    svc._get_job = get_job
    svc._store = store  # exposed for assertions
    return svc


def make_job(svc, store, **kw):
    """Create + persist a job in the service's in-memory store."""
    defaults = dict(
        job_id=kw.pop("job_id", "job-1"),
        user_id=kw.pop("user_id", 1),
        file_path=kw.pop("file_path", "/tmp/x.zip"),
        status=kw.pop("status", "validated"),
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    job = ImportJobData(**defaults)
    store[job.job_id] = job.model_dump_json()
    return job


# ===========================================================================
# Module-level pure helpers
# ===========================================================================


def test_is_annotation_and_image_file():
    assert is_annotation_file("a.JSON") and is_annotation_file("b.csv")
    assert not is_annotation_file("c.png")
    assert is_image_file("x.TIFF") and is_image_file("y.jpeg")
    assert not is_image_file("z.json")


def test_create_error_validation_result_defaults():
    r = create_error_validation_result("j", ["boom"])
    assert r.is_valid is False
    assert r.errors == ["boom"]
    assert r.warnings == []
    assert r.detected_format == ImportFormat.COCO


def test_create_error_validation_result_with_warnings():
    r = create_error_validation_result("j", ["e"], ["w"])
    assert r.warnings == ["w"]


def test_find_subdirectories():
    file_list = [
        "experiments/5/images/1/metadata.json",
        "experiments/5/images/2/metadata.json",
        "experiments/5/images/metadata.json",  # too few parts -> ignored
        "other/3/metadata.json",  # wrong prefix
    ]
    found = find_subdirectories(file_list, "experiments/5/images", "metadata.json")
    assert found == {"1", "2"}


async def test_lookup_protein_by_name_none(mock_db):
    assert await lookup_protein_by_name(mock_db, None) is None
    mock_db.execute.assert_not_called()


async def test_lookup_protein_by_name_found(mock_db):
    protein = MapProtein(name="PRC1")
    protein.id = 77
    mock_db.execute.return_value = make_result(scalar=protein)
    assert await lookup_protein_by_name(mock_db, "PRC1") == 77


async def test_lookup_protein_by_name_missing(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    assert await lookup_protein_by_name(mock_db, "nope") is None


def test_write_file_from_zip_not_in_list(tmp_path):
    data = make_zip({"a.txt": b"hi"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        out = write_file_from_zip(zf, "missing.txt", tmp_path / "o.txt", zf.namelist())
    assert out is None


def test_write_file_from_zip_writes(tmp_path):
    data = make_zip({"a.txt": b"payload"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        dest = tmp_path / "sub" / "out.txt"
        out = write_file_from_zip(zf, "a.txt", dest, zf.namelist())
    assert out == str(dest)
    assert dest.read_bytes() == b"payload"


def test_write_file_from_zip_path_traversal_blocked(tmp_path):
    data = make_zip({"a.txt": b"x"})
    base = tmp_path / "base"
    base.mkdir()
    evil = tmp_path / "evil.txt"  # outside base
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        out = write_file_from_zip(zf, "a.txt", evil, zf.namelist(), base_dir=base)
    assert out is None
    assert not evil.exists()


def test_write_file_from_zip_within_base(tmp_path):
    data = make_zip({"a.txt": b"ok"})
    base = tmp_path / "base"
    base.mkdir()
    dest = base / "out.txt"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        out = write_file_from_zip(zf, "a.txt", dest, zf.namelist(), base_dir=base)
    assert out == str(dest)


def test_load_embeddings_from_zip_missing():
    data = make_zip({"a.txt": b"x"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        emb, ids = load_embeddings_from_zip(zf, "e.npy", "i.json", zf.namelist())
    assert emb is None and ids == []


def test_load_embeddings_from_zip_present():
    arr = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    buf = io.BytesIO()
    np.save(buf, arr)
    data = make_zip({
        "embeddings/fov_embeddings.npy": buf.getvalue(),
        "embeddings/fov_ids.json": json.dumps([10, 11]).encode(),
    })
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        emb, ids = load_embeddings_from_zip(
            zf, "embeddings/fov_embeddings.npy", "embeddings/fov_ids.json", zf.namelist()
        )
    assert ids == [10, 11]
    assert emb.shape == (2, 2)


def test_extract_projections_from_zip(tmp_path):
    data = make_zip({
        "base/mip.tiff": make_tiff(),
        "base/sum.tiff": make_tiff(),
    })
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        mip, sm = extract_projections_from_zip(zf, "base", tmp_path, "fid", zf.namelist())
    assert mip is not None and sm is not None
    assert os.path.exists(mip) and os.path.exists(sm)


def test_extract_projections_missing(tmp_path):
    data = make_zip({"base/other.txt": b"x"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        mip, sm = extract_projections_from_zip(zf, "base", tmp_path, "fid", zf.namelist())
    assert mip is None and sm is None


# ===========================================================================
# validate_import
# ===========================================================================


async def test_validate_import_file_not_found():
    svc = fresh_service()
    res = await svc.validate_import("/no/such/file.zip", user_id=1)
    assert res.is_valid is False
    assert "File not found" in res.errors


async def test_validate_import_bad_zip(tmp_path):
    svc = fresh_service()
    p = tmp_path / "bad.zip"
    p.write_bytes(b"not a zip at all")
    res = await svc.validate_import(str(p), user_id=1)
    assert res.is_valid is False
    assert "Invalid ZIP file" in res.errors


async def test_validate_import_coco_valid(tmp_path):
    svc = fresh_service()
    p = tmp_path / "ok.zip"
    p.write_bytes(make_zip({
        "annotations.json": coco_annotations("a.png"),
        "images/a.png": make_png(),
        "embeddings/fov.npy": b"\x00",  # triggers has_embeddings
        "masks/fov_1.png": make_mask_png(),  # triggers has_masks
    }))
    res = await svc.validate_import(str(p), user_id=1)
    assert res.is_valid is True
    assert res.detected_format == ImportFormat.COCO
    # both images/a.png and masks/fov_1.png count as image files
    assert res.image_count == 2
    assert res.annotation_count == 1
    assert res.has_embeddings is True
    assert res.has_masks is True
    # job persisted as validated
    job = ImportJobData.model_validate_json(svc._store[res.job_id])
    assert job.status == "validated"


async def test_validate_import_no_images_hits_exception_path(tmp_path):
    """image_count==0 with no errors makes the result validator raise; the
    generic except converts it into a 'Validation error' result (is_valid False)."""
    svc = fresh_service()
    p = tmp_path / "noimg.zip"
    p.write_bytes(make_zip({"annotations.json": coco_annotations("a.png")}))
    res = await svc.validate_import(str(p), user_id=1)
    assert res.is_valid is False
    assert any("Validation error" in e for e in res.errors)


async def test_validate_import_parse_error_validation_failed(tmp_path, monkeypatch):
    """Parse error + images present -> is_valid False -> job 'validation_failed'."""
    svc = fresh_service()
    monkeypatch.setattr(
        mod, "parse_annotations",
        lambda *a, **k: ([], ["bad annotation"], []),
    )
    p = tmp_path / "img.zip"
    p.write_bytes(make_zip({
        "annotations.json": coco_annotations("a.png"),
        "images/a.png": make_png(),
    }))
    res = await svc.validate_import(str(p), user_id=1)
    assert res.is_valid is False
    assert "bad annotation" in res.errors
    job = ImportJobData.model_validate_json(svc._store[res.job_id])
    assert job.status == "validation_failed"


async def test_validate_import_maptimize_manifest(tmp_path):
    svc = fresh_service()
    manifest = json.dumps({"statistics": {"crop_count": 7}}).encode()
    p = tmp_path / "mt.zip"
    p.write_bytes(make_zip({
        "manifest.json": manifest,
        "experiments/1/images/1/mip.tiff": make_tiff(),
        "embeddings/fov_embeddings.npy": b"\x00",
        "experiments/1/masks/fov_1.png": make_mask_png(),
    }))
    res = await svc.validate_import(str(p), user_id=1)
    assert res.detected_format == ImportFormat.MAPTIMIZE
    assert res.annotation_count == 7
    assert res.has_embeddings is True
    assert res.has_masks is True


async def test_validate_import_zip_too_large(tmp_path, monkeypatch):
    svc = fresh_service()
    monkeypatch.setattr(mod, "MAX_TOTAL_UNCOMPRESSED_SIZE", 10)
    p = tmp_path / "big.zip"
    p.write_bytes(make_zip({"images/a.png": make_png(64, 64)}))
    res = await svc.validate_import(str(p), user_id=1)
    assert res.is_valid is False
    assert any("too large" in e for e in res.errors)


async def test_validate_import_zip_bomb_ratio(tmp_path, monkeypatch):
    svc = fresh_service()
    monkeypatch.setattr(mod, "MAX_COMPRESSION_RATIO", 1)
    # Highly compressible content -> ratio > 1
    p = tmp_path / "bomb.zip"
    p.write_bytes(make_zip({"images/a.png": make_png(), "annotations.json": b"0" * 5000}))
    res = await svc.validate_import(str(p), user_id=1)
    assert res.is_valid is False
    assert any("ZIP bomb" in e for e in res.errors)


class _FakeZipCtx:
    """Minimal ZipFile stand-in: read() raises a configurable error."""

    def __init__(self, names, read_error):
        self._names = names
        self._read_error = read_error
        self.filelist = []
        for n in names:
            info = type("I", (), {})()
            info.file_size = 10
            info.compress_size = 10
            self.filelist.append(info)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def namelist(self):
        return self._names

    def getinfo(self, name):
        info = type("I", (), {})()
        info.file_size = 10
        return info

    def read(self, name):
        raise self._read_error


async def test_validate_import_memory_error_on_read(tmp_path, monkeypatch):
    svc = fresh_service()
    p = tmp_path / "x.zip"
    p.write_bytes(make_zip({"annotations.json": b"{}"}))

    fake = _FakeZipCtx(["annotations.json"], MemoryError("oom"))
    monkeypatch.setattr(mod.zipfile, "ZipFile", lambda *a, **k: fake)
    res = await svc.validate_import(str(p), user_id=1)
    assert res.is_valid is False
    assert any("Out of memory" in e for e in res.errors)


async def test_validate_import_generic_read_error_warns(tmp_path, monkeypatch):
    svc = fresh_service()
    p = tmp_path / "x.zip"
    p.write_bytes(make_zip({"annotations.json": b"{}", "images/a.png": make_png()}))

    fake = _FakeZipCtx(["annotations.json", "images/a.png"], ValueError("boom"))
    monkeypatch.setattr(mod.zipfile, "ZipFile", lambda *a, **k: fake)
    res = await svc.validate_import(str(p), user_id=1)
    assert any("Could not read" in w for w in res.warnings)


async def test_validate_import_large_annotation_skipped(tmp_path, monkeypatch):
    svc = fresh_service()
    monkeypatch.setattr(mod, "MAX_ANNOTATION_FILE_SIZE", 5)
    p = tmp_path / "skip.zip"
    p.write_bytes(make_zip({
        "annotations.json": coco_annotations("a.png"),  # > 5 bytes -> skipped
        "images/a.png": make_png(),
    }))
    res = await svc.validate_import(str(p), user_id=1)
    assert any("too large" in w for w in res.warnings)


# ===========================================================================
# execute_import
# ===========================================================================


async def test_execute_import_job_not_found():
    svc = fresh_service()
    with pytest.raises(ValueError, match="not found"):
        await svc.execute_import("missing", "exp", ImportFormat.COCO, True, 1, None)


async def test_execute_import_access_denied():
    svc = fresh_service()
    make_job(svc, svc._store, user_id=2)
    with pytest.raises(ValueError, match="Access denied"):
        await svc.execute_import("job-1", "exp", ImportFormat.COCO, True, user_id=999, db=None)


async def test_execute_import_wrong_status():
    svc = fresh_service()
    make_job(svc, svc._store, status="importing")
    with pytest.raises(ValueError, match="not ready"):
        await svc.execute_import("job-1", "exp", ImportFormat.COCO, True, 1, None)


async def test_execute_import_coco_success(tmp_path, mock_db, monkeypatch):
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    mock_db.execute.return_value = make_result(scalar=None)  # protein lookup -> None

    svc = fresh_service()
    zip_path = tmp_path / "src.zip"
    zip_path.write_bytes(make_zip({
        "annotations.json": coco_annotations("a.png"),
        "images/a.png": make_png(),
    }))
    make_job(svc, svc._store, file_path=str(zip_path), status="validated")

    res = await svc.execute_import("job-1", "MyExp", ImportFormat.COCO, True, 1, mock_db)
    assert res.status == "completed"
    assert res.progress_percent == 100
    assert res.images_imported == 1
    assert res.crops_created == 1
    mock_db.commit.assert_awaited()
    # temp file cleaned up
    assert not zip_path.exists()


async def test_execute_import_no_crops(tmp_path, mock_db, monkeypatch):
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    svc = fresh_service()
    zip_path = tmp_path / "src.zip"
    zip_path.write_bytes(make_zip({
        "annotations.json": coco_annotations("a.png"),
        "images/a.png": make_png(),
    }))
    make_job(svc, svc._store, file_path=str(zip_path))

    res = await svc.execute_import("job-1", "MyExp", ImportFormat.COCO, False, 1, mock_db)
    assert res.images_imported == 1
    assert res.crops_created == 0


async def test_execute_import_error_rollback(tmp_path, mock_db, monkeypatch):
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    svc = fresh_service()
    make_job(svc, svc._store, file_path="/does/not/exist.zip", status="validated")

    with pytest.raises(Exception):
        await svc.execute_import("job-1", "MyExp", ImportFormat.COCO, True, 1, mock_db)
    mock_db.rollback.assert_awaited()
    job = ImportJobData.model_validate_json(svc._store["job-1"])
    assert job.status == "error"
    assert job.error_message


async def test_execute_import_cleanup_failure_warns(tmp_path, mock_db, monkeypatch):
    # os.unlink raises but execution still completes (warning path).
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)

    def boom(_):
        raise OSError("cannot unlink")

    monkeypatch.setattr(mod.os, "unlink", boom)

    svc = fresh_service()
    zip_path = tmp_path / "src.zip"
    zip_path.write_bytes(make_zip({
        "annotations.json": coco_annotations("a.png"),
        "images/a.png": make_png(),
    }))
    make_job(svc, svc._store, file_path=str(zip_path))
    res = await svc.execute_import("job-1", "MyExp", ImportFormat.COCO, True, 1, mock_db)
    assert res.status == "completed"


# ===========================================================================
# _import_from_zip (non-MAPtimize) edge cases
# ===========================================================================


async def test_import_from_zip_stem_matching(tmp_path, mock_db, monkeypatch):
    """Crop keyed by filename stem (no extension) is matched too."""
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    svc = fresh_service()

    exp = Experiment(name="e", user_id=1)
    exp.id = 1
    job = make_job(svc, svc._store)

    crops = [
        CropImportData(image_filename="a", bbox_x=0, bbox_y=0, bbox_w=4, bbox_h=4),
    ]
    monkeypatch.setattr(mod, "parse_annotations", lambda *a, **k: (crops, [], []))

    data = make_zip({"images/a.png": make_png()})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        result = await svc._import_from_zip(
            zf=zf, job=job, experiment=exp, import_format=ImportFormat.COCO,
            create_crops=True, db=mock_db,
        )
    assert result == [exp]
    assert job.crops_created == 1  # matched by stem "a"


async def test_import_from_zip_image_import_failure(tmp_path, mock_db, monkeypatch):
    """When _import_image returns None, image is not counted."""
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    svc = fresh_service()
    exp = Experiment(name="e", user_id=1)
    exp.id = 1
    job = make_job(svc, svc._store)

    async def fail_import(**kw):
        return None

    svc._import_image = fail_import
    data = make_zip({"images/a.png": make_png()})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_from_zip(
            zf=zf, job=job, experiment=exp, import_format=ImportFormat.COCO,
            create_crops=False, db=mock_db,
        )
    assert job.images_imported == 0


# ===========================================================================
# _import_maptimize_format (full restoration)
# ===========================================================================


def build_maptimize_zip() -> bytes:
    arr = np.array([[0.1, 0.2]], dtype=np.float32)
    fov_buf = io.BytesIO(); np.save(fov_buf, arr)
    crop_buf = io.BytesIO(); np.save(crop_buf, arr)
    return make_zip({
        "manifest.json": json.dumps({"statistics": {"crop_count": 1}}).encode(),
        "experiments/10/experiment.json": json.dumps({
            "name": "OrigExp",
            "description": "desc",
            "map_protein": {"name": "PRC1"},
            "fasta_sequence": "MKT",
        }).encode(),
        "experiments/10/images/100/metadata.json": json.dumps({
            "original_filename": "img.tiff",
            "width": 8, "height": 6, "z_slices": 3,
            "embedding_model": "dino",
        }).encode(),
        "experiments/10/images/100/mip.tiff": make_tiff(),
        "experiments/10/images/100/sum.tiff": make_tiff(),
        "experiments/10/images/100/thumbnail.png": make_png(),
        "experiments/10/crops/200/metadata.json": json.dumps({
            "image_id": 100,
            "bbox_x": 1, "bbox_y": 2, "bbox_w": 3, "bbox_h": 4,
            "detection_confidence": 0.8,
            "map_protein": {"name": "PRC1"},
            "bundleness_score": 0.5,
            "mean_intensity": 12.0,
            "embedding_model": "dino",
            "excluded": False,
        }).encode(),
        "experiments/10/crops/200/mip.tiff": make_tiff(),
        "experiments/10/masks/fov_100.png": make_mask_png(),
        "embeddings/fov_embeddings.npy": fov_buf.getvalue(),
        "embeddings/fov_ids.json": json.dumps([100]).encode(),
        "embeddings/crop_embeddings.npy": crop_buf.getvalue(),
        "embeddings/crop_ids.json": json.dumps([200]).encode(),
    })


async def test_import_maptimize_full(tmp_path, mock_db, monkeypatch):
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    protein = MapProtein(name="PRC1"); protein.id = 5

    def exec_side(stmt):
        # protein lookup returns a protein; embedding record lookups return real
        # Image/CellCrop so embeddings get assigned. Return generic scalar; tests
        # only need it non-failing.
        return make_result(scalar=protein)

    mock_db.execute.side_effect = exec_side

    svc = fresh_service()
    zip_path = tmp_path / "mt.zip"
    zip_path.write_bytes(build_maptimize_zip())
    make_job(svc, svc._store, file_path=str(zip_path))

    res = await svc.execute_import("job-1", "Imported", ImportFormat.MAPTIMIZE, True, 1, mock_db)
    assert res.status == "completed"
    assert res.images_imported == 1
    assert res.crops_created == 1


async def test_import_maptimize_no_experiments(tmp_path, mock_db, monkeypatch):
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    svc = fresh_service()
    job = make_job(svc, svc._store)
    exp = Experiment(name="ph", user_id=1); exp.id = 1
    data = make_zip({"manifest.json": json.dumps({}).encode()})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        result = await svc._import_maptimize_format(
            zf=zf, job=job, base_experiment_name="X", user_id=1, db=mock_db,
        )
    assert result == []


async def test_import_maptimize_multi_experiment_naming(tmp_path, mock_db, monkeypatch):
    """Two experiments -> names get suffixed; missing experiment.json skipped."""
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    mock_db.execute.return_value = make_result(scalar=None)
    svc = fresh_service()
    job = make_job(svc, svc._store)

    data = make_zip({
        "manifest.json": json.dumps({}).encode(),
        "experiments/1/experiment.json": json.dumps({"name": "A"}).encode(),
        "experiments/2/experiment.json": json.dumps({"name": "B"}).encode(),
    })
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        result = await svc._import_maptimize_format(
            zf=zf, job=job, base_experiment_name="Base", user_id=1, db=mock_db,
        )
    names = sorted(e.name for e in result)
    assert names == ["Base - A", "Base - B"]


async def test_import_maptimize_crop_missing_image_mapping(tmp_path, mock_db, monkeypatch):
    """A crop whose image_id has no mapping is skipped (logged warning)."""
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    mock_db.execute.return_value = make_result(scalar=None)
    svc = fresh_service()
    job = make_job(svc, svc._store)

    data = make_zip({
        "manifest.json": json.dumps({}).encode(),
        "experiments/1/experiment.json": json.dumps({"name": "A"}).encode(),
        "experiments/1/crops/9/metadata.json": json.dumps({
            "image_id": 999, "bbox_x": 0, "bbox_y": 0, "bbox_w": 1, "bbox_h": 1,
        }).encode(),
        "experiments/1/crops/9/mip.tiff": make_tiff(),
    })
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_maptimize_format(
            zf=zf, job=job, base_experiment_name="Base", user_id=1, db=mock_db,
        )
    assert job.crops_created == 0


async def test_import_maptimize_experiment_json_missing_skipped(tmp_path, mock_db, monkeypatch):
    """Experiment dir discovered but experiment.json missing -> skip (line 589)."""
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    svc = fresh_service()
    job = make_job(svc, svc._store)

    # exp_dirs is found by scanning for ".../experiment.json"; craft a path that
    # matches the discovery scan but whose exact experiment.json key differs.
    class FakeZip:
        def namelist(self):
            return ["experiments/7/sub/experiment.json"]

        def read(self, name):  # pragma: no cover - not reached for skipped exp
            return b"{}"

    result = await svc._import_maptimize_format(
        zf=FakeZip(), job=job, base_experiment_name="Base", user_id=1, db=mock_db,
    )
    assert result == []  # the only experiment was skipped


async def test_import_maptimize_image_and_crop_metadata_missing(tmp_path, mock_db, monkeypatch):
    """Image/crop dirs discovered but exact metadata.json absent -> skip (633, 671)."""
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "uploads")
    IdAssigningDB(mock_db)
    mock_db.execute.return_value = make_result(scalar=None)
    svc = fresh_service()
    job = make_job(svc, svc._store)

    # find_subdirectories matches the marker suffix; nested paths make the exact
    # "{base}/{id}/metadata.json" lookup miss so the continue branches fire.
    data = make_zip({
        "manifest.json": json.dumps({}).encode(),
        "experiments/1/experiment.json": json.dumps({"name": "A"}).encode(),
        "experiments/1/images/5/sub/metadata.json": b"{}",   # img dir 5, exact miss
        "experiments/1/crops/8/sub/metadata.json": b"{}",    # crop dir 8, exact miss
    })
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_maptimize_format(
            zf=zf, job=job, base_experiment_name="Base", user_id=1, db=mock_db,
        )
    assert job.images_imported == 0
    assert job.crops_created == 0


# ===========================================================================
# _import_maptimize_image / _import_maptimize_crop error paths
# ===========================================================================


async def test_import_maptimize_image_exception_returns_none(mock_db, monkeypatch):
    svc = fresh_service()
    exp = Experiment(name="e", user_id=1); exp.id = 1
    # Force an exception inside by making extract raise via bad settings.upload_dir
    monkeypatch.setattr(mod.settings, "upload_dir", None)
    img = await svc._import_maptimize_image(
        zf=None, img_base_path="b", img_meta={}, experiment=exp, file_list=[], db=mock_db,
    )
    assert img is None


async def test_import_maptimize_crop_exception_returns_none(mock_db, monkeypatch):
    svc = fresh_service()
    exp = Experiment(name="e", user_id=1); exp.id = 1
    monkeypatch.setattr(mod.settings, "upload_dir", None)
    crop = await svc._import_maptimize_crop(
        zf=None, crop_base_path="b", crop_meta={}, new_image_id=1,
        experiment=exp, file_list=[], db=mock_db,
    )
    assert crop is None


# ===========================================================================
# _import_maptimize_masks
# ===========================================================================


async def test_import_masks_happy_and_skips(mock_db):
    svc = fresh_service()
    mask = make_mask_png()
    data = make_zip({
        "experiments/1/masks/fov_100.png": mask,   # valid, mapped
        "experiments/1/masks/fov_999.png": mask,   # no mapping -> skip
        "experiments/1/masks/notfov.png": mask,    # wrong prefix -> skip
        "experiments/1/masks/readme.txt": b"x",    # not png -> skip
        "experiments/1/other/fov_100.png": mask,   # wrong dir -> skip
    })
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_maptimize_masks(
            zf=zf, exp_base_path="experiments/1",
            old_to_new_image_ids={100: 555}, file_list=zf.namelist(), db=mock_db,
        )
    # exactly one mask added
    added = [c.args[0] for c in mock_db.add.call_args_list]
    masks = [a for a in added if isinstance(a, FOVSegmentationMask)]
    assert len(masks) == 1
    assert masks[0].image_id == 555


async def test_import_masks_bad_filename_handled(mock_db):
    svc = fresh_service()
    # "fov_abc.png" -> int() raises -> caught, no mask added
    data = make_zip({"experiments/1/masks/fov_abc.png": make_mask_png()})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_maptimize_masks(
            zf=zf, exp_base_path="experiments/1",
            old_to_new_image_ids={1: 2}, file_list=zf.namelist(), db=mock_db,
        )
    added = [c.args[0] for c in mock_db.add.call_args_list]
    assert not [a for a in added if isinstance(a, FOVSegmentationMask)]


async def test_import_masks_too_few_points_skipped(mock_db, monkeypatch):
    svc = fresh_service()
    monkeypatch.setattr(svc, "_png_mask_to_polygon", lambda d: [[0, 0]])  # < 3 points
    data = make_zip({"experiments/1/masks/fov_1.png": make_mask_png()})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_maptimize_masks(
            zf=zf, exp_base_path="experiments/1",
            old_to_new_image_ids={1: 2}, file_list=zf.namelist(), db=mock_db,
        )
    added = [c.args[0] for c in mock_db.add.call_args_list]
    assert not [a for a in added if isinstance(a, FOVSegmentationMask)]


# ===========================================================================
# _png_mask_to_polygon
# ===========================================================================


def test_png_mask_to_polygon_ok():
    svc = ImportService()
    pts = svc._png_mask_to_polygon(make_mask_png())
    assert pts is not None
    assert len(pts) >= 3
    assert all(len(p) == 2 for p in pts)


def test_png_mask_to_polygon_empty_mask():
    svc = ImportService()
    # all-zero mask -> no edges -> None
    a = np.zeros((20, 20), dtype=np.uint8)
    buf = io.BytesIO(); PILImage.fromarray(a).save(buf, format="PNG")
    assert svc._png_mask_to_polygon(buf.getvalue()) is None


def test_png_mask_to_polygon_bad_bytes():
    svc = ImportService()
    assert svc._png_mask_to_polygon(b"not a png") is None


# ===========================================================================
# _import_maptimize_embeddings
# ===========================================================================


async def test_import_embeddings_assigns(mock_db):
    svc = fresh_service()
    arr = np.array([[1.0, 2.0]], dtype=np.float32)
    fov_buf = io.BytesIO(); np.save(fov_buf, arr)
    crop_buf = io.BytesIO(); np.save(crop_buf, arr)
    data = make_zip({
        "embeddings/fov_embeddings.npy": fov_buf.getvalue(),
        "embeddings/fov_ids.json": json.dumps([100]).encode(),
        "embeddings/crop_embeddings.npy": crop_buf.getvalue(),
        "embeddings/crop_ids.json": json.dumps([200]).encode(),
    })
    image = Image(experiment_id=1, original_filename="a"); image.id = 1
    crop = CellCrop(image_id=1); crop.id = 2
    # First execute -> image record, second -> crop record
    mock_db.execute.side_effect = [
        make_result(scalar=image),
        make_result(scalar=crop),
    ]
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_maptimize_embeddings(
            zf=zf,
            old_to_new_image_ids={100: 1},
            old_to_new_crop_ids={200: 2},
            file_list=zf.namelist(), db=mock_db,
        )
    assert image.embedding == [1.0, 2.0]
    assert crop.embedding == [1.0, 2.0]


async def test_import_embeddings_missing_files_noop(mock_db):
    svc = fresh_service()
    data = make_zip({"other.txt": b"x"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_maptimize_embeddings(
            zf=zf, old_to_new_image_ids={}, old_to_new_crop_ids={},
            file_list=zf.namelist(), db=mock_db,
        )
    mock_db.execute.assert_not_called()


async def test_import_embeddings_record_not_found(mock_db):
    svc = fresh_service()
    arr = np.array([[1.0, 2.0]], dtype=np.float32)
    fov_buf = io.BytesIO(); np.save(fov_buf, arr)
    data = make_zip({
        "embeddings/fov_embeddings.npy": fov_buf.getvalue(),
        "embeddings/fov_ids.json": json.dumps([100, 555]).encode(),
    })
    # mapping has 100->1 only (555 missing); record lookup returns None
    mock_db.execute.return_value = make_result(scalar=None)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_maptimize_embeddings(
            zf=zf, old_to_new_image_ids={100: 1}, old_to_new_crop_ids={},
            file_list=zf.namelist(), db=mock_db,
        )
    # one execute for the mapped id; no embedding assigned since record is None
    assert mock_db.execute.await_count == 1


async def test_import_embeddings_exception_caught(mock_db, monkeypatch):
    svc = fresh_service()
    arr = np.array([[1.0, 2.0]], dtype=np.float32)
    fov_buf = io.BytesIO(); np.save(fov_buf, arr)
    data = make_zip({
        "embeddings/fov_embeddings.npy": fov_buf.getvalue(),
        "embeddings/fov_ids.json": json.dumps([100]).encode(),
    })
    mock_db.execute.side_effect = RuntimeError("db down")
    # Should not raise (exception is logged and swallowed)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        await svc._import_maptimize_embeddings(
            zf=zf, old_to_new_image_ids={100: 1}, old_to_new_crop_ids={},
            file_list=zf.namelist(), db=mock_db,
        )


# ===========================================================================
# _import_image
# ===========================================================================


async def test_import_image_success(tmp_path, mock_db, monkeypatch):
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "u")
    svc = fresh_service()
    exp = Experiment(name="e", user_id=1); exp.id = 1
    img = await svc._import_image(
        image_data=make_png(10, 12), original_filename="a.png", experiment=exp, db=mock_db,
    )
    assert img is not None
    assert img.width == 10 and img.height == 12
    assert img.file_size > 0


async def test_import_image_bad_dimensions_warns(tmp_path, mock_db, monkeypatch):
    monkeypatch.setattr(mod.settings, "upload_dir", tmp_path / "u")
    svc = fresh_service()
    exp = Experiment(name="e", user_id=1); exp.id = 1
    img = await svc._import_image(
        image_data=b"not an image", original_filename="a.bin", experiment=exp, db=mock_db,
    )
    assert img is not None
    assert img.width is None and img.height is None


async def test_import_image_write_failure_returns_none(mock_db, monkeypatch):
    monkeypatch.setattr(mod.settings, "upload_dir", None)  # mkdir will raise
    svc = fresh_service()
    exp = Experiment(name="e", user_id=1); exp.id = 1
    img = await svc._import_image(
        image_data=make_png(), original_filename="a.png", experiment=exp, db=mock_db,
    )
    assert img is None


# ===========================================================================
# _create_crop
# ===========================================================================


async def test_create_crop_with_protein(mock_db):
    svc = fresh_service()
    image = Image(experiment_id=1, original_filename="a"); image.id = 1
    protein = MapProtein(name="PRC1"); protein.id = 9
    mock_db.execute.return_value = make_result(scalar=protein)
    cd = CropImportData(image_filename="a", bbox_x=0, bbox_y=0, bbox_w=4, bbox_h=4,
                        class_name="PRC1", confidence=0.5)
    crop = await svc._create_crop(image=image, crop_data=cd, db=mock_db)
    assert crop is not None
    assert crop.map_protein_id == 9


async def test_create_crop_default_cell_class_skips_lookup(mock_db):
    svc = fresh_service()
    image = Image(experiment_id=1, original_filename="a"); image.id = 1
    cd = CropImportData(image_filename="a", bbox_x=0, bbox_y=0, bbox_w=4, bbox_h=4,
                        class_name="cell")
    crop = await svc._create_crop(image=image, crop_data=cd, db=mock_db)
    assert crop is not None
    mock_db.execute.assert_not_called()  # "cell" -> None -> no protein lookup


async def test_create_crop_exception_returns_none(mock_db):
    svc = fresh_service()
    image = Image(experiment_id=1, original_filename="a"); image.id = 1
    mock_db.flush.side_effect = RuntimeError("flush failed")
    cd = CropImportData(image_filename="a", bbox_x=0, bbox_y=0, bbox_w=4, bbox_h=4)
    crop = await svc._create_crop(image=image, crop_data=cd, db=mock_db)
    assert crop is None


# ===========================================================================
# get_import_status
# ===========================================================================


async def test_get_import_status_found():
    svc = fresh_service()
    make_job(svc, svc._store, status="completed", images_imported=3, crops_created=5)
    status = await svc.get_import_status("job-1")
    assert status.status == "completed"
    assert status.images_imported == 3
    assert status.crops_created == 5


async def test_get_import_status_missing():
    svc = fresh_service()
    assert await svc.get_import_status("nope") is None
