"""In-process unit tests for the export services.

Covers:
  * ``services.data_export_service`` — CSV/Excel exports of experiments,
    cell crops, ranking comparisons and arbitrary analysis results.
  * ``services.export_service`` — streaming ZIP generation, size estimation,
    Redis job state, manifest/metadata serialization, mask encoding and
    embeddings.

The DB is an ``AsyncMock`` (see conftest ``mock_db``/``make_result``), Redis is a
plain ``AsyncMock``, and all file output is redirected to ``tmp_path`` so nothing
touches the real upload directory.
"""
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import services.data_export_service as des
from schemas.export_import import (
    BBoxFormat,
    ExportJobData,
    ExportOptions,
    MaskFormat,
)
from services.export_service import (
    ExportService,
    write_embeddings_to_zip,
    write_file_to_zip,
)
from tests.unit.conftest import make_result


# ============================================================================
# Helpers / lightweight model stand-ins
# ============================================================================


def _row(**kwargs):
    """A SQLAlchemy ``Row``-like object: attribute access returns the value."""
    return SimpleNamespace(**kwargs)


def make_protein(id=1, name="PRC1"):
    return SimpleNamespace(id=id, name=name)


def make_experiment(
    id=1,
    name="Exp A",
    description="desc",
    status=None,
    fasta_sequence="MKT",
    map_protein=None,
    images=None,
):
    status_obj = SimpleNamespace(value=status) if status else None
    return SimpleNamespace(
        id=id,
        name=name,
        description=description,
        status=status_obj,
        fasta_sequence=fasta_sequence,
        map_protein=map_protein,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 1, 2, 12, 0, 0),
        images=images if images is not None else [],
    )


def make_image(
    id=10,
    filename="img.tiff",
    width=100,
    height=80,
    cell_crops=None,
    fov_mask=None,
    embedding=None,
    mip_path=None,
    sum_path=None,
    thumbnail_path=None,
):
    return SimpleNamespace(
        id=id,
        original_filename=filename,
        width=width,
        height=height,
        z_slices=5,
        status=SimpleNamespace(value="completed"),
        embedding_model="dino",
        embedding=embedding,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        cell_crops=cell_crops if cell_crops is not None else [],
        fov_segmentation_mask=fov_mask,
        mip_path=mip_path,
        sum_path=sum_path,
        thumbnail_path=thumbnail_path,
    )


def make_crop(
    id=100,
    image_id=10,
    bbox_x=5,
    bbox_y=6,
    bbox_w=20,
    bbox_h=10,
    confidence=0.9,
    map_protein=None,
    embedding=None,
    mip_path=None,
    sum_crop_path=None,
):
    return SimpleNamespace(
        id=id,
        image_id=image_id,
        bbox_x=bbox_x,
        bbox_y=bbox_y,
        bbox_w=bbox_w,
        bbox_h=bbox_h,
        detection_confidence=confidence,
        map_protein=map_protein,
        bundleness_score=0.5,
        mean_intensity=42.0,
        embedding_model="dino",
        embedding=embedding,
        excluded=False,
        mip_path=mip_path,
        sum_crop_path=sum_crop_path,
        created_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def make_fov_mask(polygon=None, area=1000):
    return SimpleNamespace(
        polygon_points=polygon if polygon is not None else [[0, 0], [50, 0], [50, 40], [0, 40]],
        area_pixels=area,
    )


@pytest.fixture
def patch_export_dir(tmp_path, monkeypatch):
    """Redirect the data_export_service EXPORT_DIR to a temp directory."""
    monkeypatch.setattr(des, "EXPORT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def fake_redis():
    r = AsyncMock(name="redis")
    r.ping = AsyncMock()
    r.setex = AsyncMock()
    r.get = AsyncMock(return_value=None)
    return r


@pytest.fixture
def service(fake_redis):
    svc = ExportService()
    svc._redis = fake_redis  # bypass real connection setup
    return svc


# ============================================================================
# data_export_service.export_experiment_data
# ============================================================================


async def test_export_experiment_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await des.export_experiment_data(1, 7, mock_db)
    assert out == {"error": "Experiment not found or access denied"}


async def test_export_experiment_csv(mock_db, patch_export_dir):
    exp = make_experiment(map_protein=make_protein())
    rows = [
        _row(id=10, original_filename="a.tiff", width=100, height=80,
             created_at=datetime(2024, 1, 1), cell_count=3),
        _row(id=11, original_filename="b.tiff", width=120, height=90,
             created_at=None, cell_count=0),
    ]
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(fetchall=rows),
    ]
    out = await des.export_experiment_data(1, 7, mock_db, format="csv")
    assert out["success"] is True
    assert out["filename"].endswith(".csv")
    assert out["metadata"]["total_images"] == 2
    assert out["metadata"]["total_cells"] == 3
    assert out["metadata"]["protein"] == "PRC1"
    assert (patch_export_dir / out["filename"]).exists()


async def test_export_experiment_xlsx_empty(mock_db, patch_export_dir):
    """No images → empty DataFrame, total_cells defaults to 0, xlsx branch."""
    exp = make_experiment(map_protein=None)
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(fetchall=[]),
    ]
    out = await des.export_experiment_data(1, 7, mock_db, format="xlsx")
    assert out["success"] is True
    assert out["filename"].endswith(".xlsx")
    assert out["metadata"]["protein"] is None
    assert out["metadata"]["total_cells"] == 0
    assert out["metadata"]["total_images"] == 0
    assert (patch_export_dir / out["filename"]).exists()


# ============================================================================
# data_export_service.export_cell_crops
# ============================================================================


async def test_export_cell_crops_with_rows(mock_db, patch_export_dir):
    rows = [
        _row(id=100, bbox_x=5, bbox_y=6, bbox_w=20, bbox_h=10,
             detection_confidence=0.9, mean_intensity=42.0,
             created_at=datetime(2024, 1, 1), image_id=10,
             image_filename="img.tiff", experiment_id=1,
             experiment_name="Exp A", protein_name="PRC1"),
        # bbox_w None → area branch returns None and created_at None branch
        _row(id=101, bbox_x=0, bbox_y=0, bbox_w=None, bbox_h=10,
             detection_confidence=None, mean_intensity=None,
             created_at=None, image_id=10, image_filename="img.tiff",
             experiment_id=1, experiment_name="Exp A", protein_name=None),
    ]
    mock_db.execute.return_value = make_result(fetchall=rows)
    out = await des.export_cell_crops(7, mock_db, experiment_id=1, format="csv")
    assert out["success"] is True
    assert out["row_count"] == 2
    assert "_exp1_" in out["filename"]
    assert (patch_export_dir / out["filename"]).exists()


async def test_export_cell_crops_empty_no_experiment(mock_db, patch_export_dir):
    mock_db.execute.return_value = make_result(fetchall=[])
    out = await des.export_cell_crops(7, mock_db, format="xlsx")
    assert out["success"] is True
    assert out["row_count"] == 0
    assert "_exp" not in out["filename"]
    assert out["filename"].endswith(".xlsx")


# ============================================================================
# data_export_service.export_ranking_comparisons
# ============================================================================


async def test_export_ranking_comparisons(mock_db, patch_export_dir):
    """Exports comparison history; loser_id is derived from crop_a/crop_b vs winner."""
    rows = [
        # winner == crop_a -> loser is crop_b (20)
        SimpleNamespace(id=1, crop_a_id=10, crop_b_id=20, winner_id=10,
                        undone=False, timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc)),
        # winner == crop_b -> loser is crop_a (30); null timestamp
        SimpleNamespace(id=2, crop_a_id=30, crop_b_id=40, winner_id=40,
                        undone=True, timestamp=None),
    ]
    mock_db.execute.return_value = make_result(fetchall=rows)
    res = await des.export_ranking_comparisons(7, mock_db, format="csv")
    assert res["success"] is True
    assert res["row_count"] == 2


async def test_export_ranking_comparisons_empty(mock_db, patch_export_dir):
    mock_db.execute.return_value = make_result(fetchall=[])
    res = await des.export_ranking_comparisons(7, mock_db, format="xlsx")
    assert res["success"] is True and res["row_count"] == 0


# ============================================================================
# data_export_service.export_analysis_results
# ============================================================================


async def test_export_analysis_results_empty():
    out = await des.export_analysis_results([], "myreport")
    assert out == {"error": "No data to export"}


async def test_export_analysis_results_with_data(patch_export_dir):
    data = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    out = await des.export_analysis_results(data, "My Report!", format="csv")
    assert out["success"] is True
    assert out["row_count"] == 2
    assert out["columns"] == ["a", "b"]
    assert (patch_export_dir / out["filename"]).exists()


# ============================================================================
# data_export_service.cleanup_old_exports
# ============================================================================


def test_cleanup_old_exports_delegates():
    with patch.object(des, "cleanup_old_files", return_value=3) as cleanup:
        assert des.cleanup_old_exports(max_age_hours=5) == 3
        cleanup.assert_called_once()
        # passes the module EXPORT_DIR + log prefix
        args, kwargs = cleanup.call_args
        assert args[0] == des.EXPORT_DIR
        assert kwargs.get("log_prefix") == "export"


# ============================================================================
# export_service module-level helpers
# ============================================================================


def test_write_file_to_zip_existing(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        write_file_to_zip(zf, str(src), "out/x.bin")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert zf.read("out/x.bin") == b"hello"


def test_write_file_to_zip_missing_and_none(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        write_file_to_zip(zf, str(tmp_path / "nope.bin"), "out/x.bin")
        write_file_to_zip(zf, None, "out/y.bin")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert zf.namelist() == []


def test_write_embeddings_to_zip_with_values():
    items = [
        SimpleNamespace(id=1, embedding=[0.1, 0.2]),
        SimpleNamespace(id=2, embedding=None),  # skipped
        SimpleNamespace(id=3, embedding=[0.3, 0.4]),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        write_embeddings_to_zip(zf, items, "emb/data.npy", "emb/ids.json")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert json.loads(zf.read("emb/ids.json")) == [1, 3]
        arr = np.load(io.BytesIO(zf.read("emb/data.npy")))
        assert arr.shape == (2, 2)


def test_write_embeddings_to_zip_no_values():
    items = [SimpleNamespace(id=1, embedding=None)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        write_embeddings_to_zip(zf, items, "emb/data.npy", "emb/ids.json")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert zf.namelist() == []


# ============================================================================
# ExportService.prepare_export
# ============================================================================


async def test_prepare_export_success_with_masks(service, mock_db, fake_redis):
    exps = [make_experiment(id=1), make_experiment(id=2)]
    mock_db.execute.side_effect = [
        make_result(scalars_all=exps),   # ownership check
        make_result(scalar=4),           # image count
        make_result(scalar=10),          # crop count
        make_result(scalar=2),           # fov masks
        make_result(scalar=3),           # crop masks
    ]
    opts = ExportOptions(include_masks=True)
    resp = await service.prepare_export([1, 2], opts, user_id=7, db=mock_db)
    assert resp.experiment_count == 2
    assert resp.image_count == 4
    assert resp.crop_count == 10
    assert resp.mask_count == 5
    assert resp.estimated_size_bytes > 0
    fake_redis.setex.assert_awaited()  # job saved


async def test_prepare_export_no_masks(service, mock_db):
    exps = [make_experiment(id=1)]
    mock_db.execute.side_effect = [
        make_result(scalars_all=exps),
        make_result(scalar=None),  # image count → 0 via `or 0`
        make_result(scalar=None),  # crop count → 0
    ]
    opts = ExportOptions(include_masks=False)
    resp = await service.prepare_export([1], opts, user_id=7, db=mock_db)
    assert resp.mask_count == 0
    assert resp.image_count == 0
    assert resp.crop_count == 0


async def test_prepare_export_ownership_mismatch(service, mock_db):
    mock_db.execute.return_value = make_result(scalars_all=[make_experiment(id=1)])
    with pytest.raises(ValueError, match="not found or not owned"):
        await service.prepare_export([1, 2], ExportOptions(), user_id=7, db=mock_db)


# ============================================================================
# ExportService._estimate_export_size
# ============================================================================


def test_estimate_size_all_options(service):
    opts = ExportOptions(
        include_fov_images=True,
        include_crop_images=True,
        include_embeddings=True,
        include_masks=True,
    )
    size = service._estimate_export_size(2, 5, 3, opts)
    expected = (
        2 * 2 * 1024 * 1024      # fov images
        + 5 * 100 * 1024         # crop images
        + 5 * 4 * 1024 + 2 * 4 * 1024  # embeddings
        + 3 * 50 * 1024          # masks
        + 5 * 200                # annotations
        + 2 * 500                # metadata
    )
    assert size == expected


def test_estimate_size_minimal(service):
    # only embeddings enabled (validator needs at least one)
    opts = ExportOptions(
        include_fov_images=False,
        include_crop_images=False,
        include_embeddings=False,
        include_masks=True,
    )
    size = service._estimate_export_size(0, 0, 0, opts)
    assert size == 0


# ============================================================================
# ExportService.get_export_status
# ============================================================================


async def test_get_export_status_found(service, fake_redis):
    job = ExportJobData(
        job_id="j1", user_id=7, experiment_ids=[1], options=ExportOptions(),
        status="streaming", created_at=datetime.now(timezone.utc),
        progress_percent=42.0, current_step="working",
    )
    fake_redis.get.return_value = job.model_dump_json()
    status = await service.get_export_status("j1")
    assert status.job_id == "j1"
    assert status.progress_percent == 42.0
    assert status.current_step == "working"


async def test_get_export_status_missing(service, fake_redis):
    fake_redis.get.return_value = None
    assert await service.get_export_status("nope") is None


# ============================================================================
# ExportService serialization helpers
# ============================================================================


async def test_create_manifest(service, mock_db):
    job = ExportJobData(
        job_id="j1", user_id=7, experiment_ids=[1, 2], options=ExportOptions(),
        status="streaming", created_at=datetime.now(timezone.utc),
        experiment_count=2, image_count=4, crop_count=6, mask_count=1,
    )
    manifest = await service._create_manifest(job, mock_db)
    assert manifest["format_version"] == "1.0"
    assert manifest["source"] == "MAPtimize"
    assert manifest["statistics"]["image_count"] == 4
    assert manifest["experiment_ids"] == [1, 2]
    assert "options" in manifest


def test_experiment_to_dict_with_and_without_protein(service):
    exp = make_experiment(status="completed", map_protein=make_protein())
    d = service._experiment_to_dict(exp)
    assert d["status"] == "completed"
    assert d["map_protein"] == {"id": 1, "name": "PRC1"}
    assert d["created_at"] is not None

    bare = make_experiment(status=None, map_protein=None)
    bare.created_at = None
    bare.updated_at = None
    d2 = service._experiment_to_dict(bare)
    assert d2["status"] is None
    assert d2["map_protein"] is None
    assert d2["created_at"] is None


def test_image_to_dict(service):
    img = make_image()
    d = service._image_to_dict(img)
    assert d["id"] == 10
    assert d["status"] == "completed"
    assert d["created_at"] is not None

    img.status = None
    img.created_at = None
    d2 = service._image_to_dict(img)
    assert d2["status"] is None
    assert d2["created_at"] is None


def test_crop_to_dict(service):
    crop = make_crop(map_protein=make_protein())
    d = service._crop_to_dict(crop)
    assert d["map_protein"] == {"id": 1, "name": "PRC1"}
    assert d["created_at"] is not None

    crop.map_protein = None
    crop.created_at = None
    d2 = service._crop_to_dict(crop)
    assert d2["map_protein"] is None
    assert d2["created_at"] is None


# ============================================================================
# ExportService file writers
# ============================================================================


async def test_write_image_files(service, tmp_path):
    mip = tmp_path / "mip.tiff"
    mip.write_bytes(b"mip")
    img = make_image(mip_path=str(mip), sum_path=None, thumbnail_path=None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_image_files(zf, 1, img)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert "experiments/1/images/10/mip.tiff" in zf.namelist()


async def test_write_crop_files(service, tmp_path):
    sumf = tmp_path / "sum.tiff"
    sumf.write_bytes(b"sum")
    crop = make_crop(mip_path=None, sum_crop_path=str(sumf))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_crop_files(zf, 1, crop)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert "experiments/1/crops/100/sum.tiff" in zf.namelist()


# ============================================================================
# ExportService._write_fov_mask
# ============================================================================


def _read_zip_json(buf, name):
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        return json.loads(zf.read(name))


async def test_write_fov_mask_no_mask(service):
    img = make_image(fov_mask=None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_fov_mask(zf, 1, img, MaskFormat.PNG)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert zf.namelist() == []


async def test_write_fov_mask_no_polygon(service):
    img = make_image(fov_mask=make_fov_mask(polygon=[]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_fov_mask(zf, 1, img, MaskFormat.PNG)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert zf.namelist() == []


async def test_write_fov_mask_too_few_points(service):
    img = make_image(fov_mask=make_fov_mask(polygon=[[0, 0], [1, 1]]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_fov_mask(zf, 1, img, MaskFormat.PNG)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert zf.namelist() == []


async def test_write_fov_mask_png(service):
    img = make_image(fov_mask=make_fov_mask())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_fov_mask(zf, 1, img, MaskFormat.PNG)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert "experiments/1/masks/fov_10.png" in zf.namelist()


async def test_write_fov_mask_coco_rle(service):
    img = make_image(fov_mask=make_fov_mask())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_fov_mask(zf, 1, img, MaskFormat.COCO_RLE)
    data = _read_zip_json(buf, "experiments/1/masks/fov_10.json")
    assert "counts" in data["segmentation"]
    assert data["image_id"] == 10


async def test_write_fov_mask_coco_string(service):
    img = make_image(fov_mask=make_fov_mask())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_fov_mask(zf, 1, img, MaskFormat.COCO)
    data = _read_zip_json(buf, "experiments/1/masks/fov_10.json")
    assert data["iscrowd"] == 1
    assert isinstance(data["segmentation"]["counts"], str)


async def test_write_fov_mask_polygon(service):
    img = make_image(fov_mask=make_fov_mask(area=None))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_fov_mask(zf, 1, img, MaskFormat.POLYGON)
    data = _read_zip_json(buf, "experiments/1/masks/fov_10.json")
    assert data["area"] == 0  # None → 0
    assert data["polygon"]
    assert data["width"] == 100


async def test_write_fov_mask_exception_logged(service):
    """Exception inside the mask branch is caught and logged, not raised."""
    img = make_image(fov_mask=make_fov_mask())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        with patch.object(
            service, "_polygon_to_coco_rle", side_effect=RuntimeError("boom")
        ):
            # must not raise
            await service._write_fov_mask(zf, 1, img, MaskFormat.COCO_RLE)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        assert zf.namelist() == []


# ============================================================================
# ExportService RLE encoding internals
# ============================================================================


def test_polygon_to_rle_counts_and_formats(service):
    polygon = [(0, 0), (5, 0), (5, 5), (0, 5)]
    counts, h, w = service._polygon_to_rle_counts(polygon, 10, 10)
    assert h == 10 and w == 10
    assert sum(counts) == 100  # full mask area
    assert all(isinstance(c, int) for c in counts)

    rle = service._polygon_to_coco_rle(polygon, 10, 10)
    assert rle["size"] == [10, 10]

    srle = service._polygon_to_coco_string_rle(polygon, 10, 10)
    assert isinstance(srle["counts"], str)


def test_polygon_rle_starts_with_foreground(service):
    """A polygon covering pixel (0,0) inserts a leading 0 count."""
    # Cover the whole image so the first flattened pixel is foreground.
    polygon = [(0, 0), (4, 0), (4, 4), (0, 4)]
    counts, _, _ = service._polygon_to_rle_counts(polygon, 4, 4)
    assert counts[0] == 0


def test_encode_rle_counts_zero_and_multibyte(service):
    # zero count → '0' char
    assert service._encode_rle_counts([0]) == "0"
    # large value triggers the continuation-bit loop
    encoded = service._encode_rle_counts([1000])
    assert isinstance(encoded, str) and len(encoded) >= 2


# ============================================================================
# ExportService._write_embeddings / _get_class_names
# ============================================================================


async def test_write_embeddings(service):
    images = [make_image(id=10, embedding=[0.1, 0.2])]
    crops = [make_crop(id=100, embedding=[0.3, 0.4])]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        await service._write_embeddings(zf, images, crops)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        names = zf.namelist()
        assert "embeddings/fov_embeddings.npy" in names
        assert "embeddings/crop_embeddings.npy" in names


async def test_get_class_names_inserts_default(service, mock_db):
    mock_db.execute.return_value = make_result(fetchall=[("PRC1",), (None,)])
    names = await service._get_class_names(mock_db, [1])
    assert names[0] == "cell"  # default inserted at front
    assert "PRC1" in names
    assert None not in names


async def test_get_class_names_keeps_existing_cell(service, mock_db):
    mock_db.execute.return_value = make_result(fetchall=[("cell",), ("PRC1",)])
    names = await service._get_class_names(mock_db, [1])
    assert names.count("cell") == 1


# ============================================================================
# ExportService.generate_export_stream
# ============================================================================


async def test_generate_export_stream_job_not_found(service, mock_db, fake_redis):
    fake_redis.get.return_value = None
    with pytest.raises(ValueError, match="not found"):
        async for _ in service.generate_export_stream("missing", mock_db):
            pass


def _make_job(experiment_ids, options=None, image_count=1, crop_count=1):
    return ExportJobData(
        job_id="job1",
        user_id=7,
        experiment_ids=experiment_ids,
        options=options or ExportOptions(),
        status="preparing",
        created_at=datetime.now(timezone.utc),
        image_count=image_count,
        crop_count=crop_count,
    )


async def _collect_stream(service, job_id, db):
    chunks = b""
    async for chunk in service.generate_export_stream(job_id, db):
        chunks += chunk
    return chunks


async def test_generate_export_stream_coco_full(service, mock_db, fake_redis):
    """Happy path: COCO annotations, fov + crop images, masks, embeddings."""
    protein = make_protein()
    crop = make_crop(id=100, image_id=10, map_protein=protein, embedding=[0.1, 0.2])
    img = make_image(
        id=10, cell_crops=[crop], fov_mask=make_fov_mask(), embedding=[0.3, 0.4]
    )
    exp = make_experiment(id=1, map_protein=protein, images=[img])

    job = _make_job([1], ExportOptions(), image_count=1, crop_count=1)
    fake_redis.get.return_value = job.model_dump_json()

    # _create_manifest (no execute), then experiment load, then _get_class_names
    mock_db.execute.side_effect = [
        make_result(scalar=exp),                 # experiment load
        make_result(fetchall=[("PRC1",)]),       # class names
    ]
    data = await _collect_stream(service, "job1", mock_db)
    assert data[:2] == b"PK"  # valid ZIP magic

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "annotations/coco.json" in names
        assert "experiments/1/experiment.json" in names
        assert any("metadata.json" in n for n in names)
        assert "embeddings/fov_embeddings.npy" in names


async def test_generate_export_stream_yolo(service, mock_db, fake_redis):
    crop = make_crop(id=100, image_id=10, map_protein=make_protein())
    img = make_image(id=10, filename="frame.tiff", cell_crops=[crop])
    exp = make_experiment(id=1, images=[img])

    opts = ExportOptions(
        bbox_format=BBoxFormat.YOLO,
        include_embeddings=False,
        include_masks=True,  # kept on to satisfy the "at least one" validator
        include_fov_images=False,
        include_crop_images=False,
    )
    job = _make_job([1], opts, image_count=1, crop_count=1)
    fake_redis.get.return_value = job.model_dump_json()
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(fetchall=[("PRC1",)]),
    ]
    data = await _collect_stream(service, "job1", mock_db)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "annotations/yolo/classes.txt" in names
        assert "annotations/yolo/frame.txt" in names


async def test_generate_export_stream_voc(service, mock_db, fake_redis):
    crop = make_crop(id=100, image_id=10)
    img = make_image(id=10, filename="scan.tiff", cell_crops=[crop])
    exp = make_experiment(id=1, images=[img])

    opts = ExportOptions(
        bbox_format=BBoxFormat.VOC,
        include_embeddings=False,
        include_masks=True,  # kept on to satisfy the "at least one" validator
        include_fov_images=False,
        include_crop_images=False,
    )
    job = _make_job([1], opts, image_count=1, crop_count=1)
    fake_redis.get.return_value = job.model_dump_json()
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(fetchall=[]),
    ]
    data = await _collect_stream(service, "job1", mock_db)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "annotations/voc/scan.xml" in zf.namelist()


async def test_generate_export_stream_csv(service, mock_db, fake_redis):
    crop = make_crop(id=100, image_id=10)
    img = make_image(id=10, cell_crops=[crop])
    exp = make_experiment(id=1, images=[img])

    opts = ExportOptions(
        bbox_format=BBoxFormat.CSV,
        include_embeddings=False,
        include_masks=True,  # kept on to satisfy the "at least one" validator
        include_fov_images=False,
        include_crop_images=False,
    )
    job = _make_job([1], opts, image_count=1, crop_count=1)
    fake_redis.get.return_value = job.model_dump_json()
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(fetchall=[]),
    ]
    data = await _collect_stream(service, "job1", mock_db)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "annotations/annotations.csv" in zf.namelist()


async def test_generate_export_stream_missing_experiment_skipped(
    service, mock_db, fake_redis
):
    """An experiment that is None is logged and skipped, stream still completes."""
    opts = ExportOptions(
        include_embeddings=False,
        include_masks=True,  # kept on to satisfy the "at least one" validator
        include_fov_images=False,
        include_crop_images=False,
    )
    job = _make_job([99], opts, image_count=0, crop_count=0)
    fake_redis.get.return_value = job.model_dump_json()
    mock_db.execute.side_effect = [
        make_result(scalar=None),    # experiment missing → skip
        make_result(fetchall=[]),    # class names
    ]
    data = await _collect_stream(service, "job1", mock_db)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert not any("experiments/99" in n for n in names)


async def test_generate_export_stream_exception_marks_error(
    service, mock_db, fake_redis
):
    """A failure mid-stream marks the job 'error' and re-raises."""
    job = _make_job([1], image_count=1, crop_count=1)
    fake_redis.get.return_value = job.model_dump_json()
    # First execute (experiment load) raises → triggers except branch
    mock_db.execute.side_effect = RuntimeError("db exploded")

    with pytest.raises(RuntimeError, match="db exploded"):
        await _collect_stream(service, "job1", mock_db)

    # The job was re-saved with error status — inspect the last setex payload.
    saved = ExportJobData.model_validate_json(fake_redis.setex.await_args.args[2])
    assert saved.status == "error"
    assert "RuntimeError" in saved.error_message


async def test_generate_export_stream_error_truncates_long_ids(
    service, mock_db, fake_redis
):
    """Error message truncates experiment_ids list past 5 entries."""
    job = _make_job([1, 2, 3, 4, 5, 6, 7], image_count=1, crop_count=1)
    fake_redis.get.return_value = job.model_dump_json()
    mock_db.execute.side_effect = RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        await _collect_stream(service, "job1", mock_db)
    saved = ExportJobData.model_validate_json(fake_redis.setex.await_args.args[2])
    assert "..." in saved.error_message
