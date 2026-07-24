"""In-process unit tests for services.rag_service and
services.document_indexing_service.

Both modules do vector search and PDF/document indexing using the Qwen VL
encoder, which is imported INSIDE functions as
``from ml.rag import get_qwen_vl_encoder``. We mock it at that call boundary via
``patch("ml.rag.get_qwen_vl_encoder", return_value=fake_encoder)`` so no GPU/ML
libs load. The DB is the AsyncMock ``mock_db`` fixture configured with
``make_result``. PDF rendering (pdf2image) and the filesystem are mocked /
redirected to ``tmp_path``.
"""
import json
import types as pytypes
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from PIL import Image as PILImage

import services.rag_service as rag
import services.document_indexing_service as dind
from services.rag_service import RAGServiceError
from models.rag_document import DocumentStatus

from tests.unit.conftest import make_result


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def fake_encoder():
    """A stand-in Qwen VL encoder returning tiny numpy vectors."""
    enc = MagicMock(name="encoder")
    enc.encode_query.return_value = np.array([0.1, 0.2, 0.3])
    enc.encode_document.return_value = np.array([0.4, 0.5, 0.6])
    return enc


def patch_encoder(enc=None):
    """Patch the lazy ``from ml.rag import get_qwen_vl_encoder`` getter."""
    return patch("ml.rag.get_qwen_vl_encoder", return_value=enc or fake_encoder())


def db_row(**kw):
    """A row object exposing attributes (mimics a SQLAlchemy Row)."""
    return SimpleNamespace(**kw)


def make_png(path: Path, size=(200, 200)):
    """Write a small valid PNG file to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, (123, 222, 64)).save(path, "PNG")
    return path


def page(**kw):
    """A document page stub."""
    return SimpleNamespace(**kw)


def document(**kw):
    """A RAGDocument stub with sane defaults."""
    defaults = dict(
        id=1, name="doc.pdf", file_type="pdf", page_count=2,
        status="completed", pages=[], user_id=7, original_path="/x/doc.pdf",
        thread_id=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


@asynccontextmanager
async def _ctx(db):
    yield db


# ============================================================================ #
# rag_service.search_documents
# ============================================================================ #
async def test_search_documents_nothing_indexed_returns_empty(mock_db):
    # has_indexed.first() -> None  => early return [] WITHOUT loading the encoder.
    mock_db.execute.return_value = make_result(first=None)
    with patch("ml.rag.get_qwen_vl_encoder") as get_enc:
        out = await rag.search_documents("q", 7, mock_db)
    assert out == []
    get_enc.assert_not_called()  # the whole point: skip the expensive model load


async def test_search_documents_with_results_text_truncation(mock_db):
    long_text = "x" * 2500
    rows = [
        db_row(id=1, document_id=10, page_number=1, image_path="/p.png",
               extracted_text=long_text, document_name="D", file_type="pdf",
               total_pages=3, distance=0.2),
        db_row(id=2, document_id=10, page_number=2, image_path="/p2.png",
               extracted_text="short", document_name="D", file_type="pdf",
               total_pages=3, distance=0.1),
    ]
    # 1st execute: has_indexed.first() truthy; 2nd execute: fetchall rows
    mock_db.execute.side_effect = [
        make_result(first=db_row(x=1)),
        make_result(fetchall=rows),
    ]
    with patch_encoder():
        out = await rag.search_documents("query", 7, mock_db, include_text=True)
    assert len(out) == 2
    assert out[0]["page_id"] == 1
    assert out[0]["similarity_score"] == round(1 - 0.2, 4)
    assert out[0]["extracted_text"].endswith("... [truncated]")
    assert out[0]["page_image_url"].startswith("/api/rag/documents/10/pages/1/image")
    assert out[1]["extracted_text"] == "short"


async def test_search_documents_exclude_text(mock_db):
    rows = [db_row(id=1, document_id=10, page_number=1, image_path="/p.png",
                   extracted_text="hi", document_name="D", file_type="pdf",
                   total_pages=1, distance=0.0)]
    mock_db.execute.side_effect = [
        make_result(first=db_row(x=1)),
        make_result(fetchall=rows),
    ]
    with patch_encoder():
        out = await rag.search_documents("q", 7, mock_db, include_text=False, limit=5)
    assert "extracted_text" not in out[0]


async def test_search_documents_error_raises_ragerror(mock_db):
    # has_indexed truthy, then encoder blows up -> RAGServiceError
    mock_db.execute.return_value = make_result(first=db_row(x=1))
    enc = fake_encoder()
    enc.encode_query.side_effect = RuntimeError("model boom")
    with patch_encoder(enc):
        with pytest.raises(RAGServiceError, match="Document search failed"):
            await rag.search_documents("q" * 100, 7, mock_db)


# ============================================================================ #
# rag_service.search_fov_images
# ============================================================================ #
async def test_search_fov_nothing_indexed(mock_db):
    mock_db.execute.return_value = make_result(first=None)
    with patch("ml.rag.get_qwen_vl_encoder") as get_enc:
        assert await rag.search_fov_images("q", 7, mock_db) == []
    get_enc.assert_not_called()  # skip the expensive model load when nothing indexed


async def test_search_fov_with_experiment_filter(mock_db):
    rows = [db_row(id=5, experiment_id=2, original_filename="img.tif", width=100,
                   height=200, experiment_name="Exp", distance=0.25)]
    mock_db.execute.side_effect = [
        make_result(first=db_row(x=1)),
        make_result(fetchall=rows),
    ]
    with patch_encoder():
        out = await rag.search_fov_images("q", 7, mock_db, experiment_id=2, limit=3)
    assert out[0]["image_id"] == 5
    assert out[0]["thumbnail_url"] == "/api/images/5/file?type=thumbnail"
    assert out[0]["similarity_score"] == round(1 - 0.25, 4)


async def test_search_fov_no_experiment_filter(mock_db):
    rows = [db_row(id=5, experiment_id=2, original_filename="img.tif", width=10,
                   height=20, experiment_name="Exp", distance=0.0)]
    mock_db.execute.side_effect = [
        make_result(first=db_row(x=1)),
        make_result(fetchall=rows),
    ]
    with patch_encoder():
        out = await rag.search_fov_images("q", 7, mock_db)
    assert out[0]["mip_url"] == "/api/images/5/file?type=mip"


async def test_search_fov_error_raises(mock_db):
    mock_db.execute.return_value = make_result(first=db_row(x=1))
    enc = fake_encoder()
    enc.encode_query.side_effect = RuntimeError("boom")
    with patch_encoder(enc):
        with pytest.raises(RAGServiceError, match="FOV image search failed"):
            await rag.search_fov_images("q" * 80, 7, mock_db)


# ============================================================================ #
# rag_service.combined_search
# ============================================================================ #
async def test_combined_search_both_succeed(mock_db):
    async def fake_docs(*a, **k):
        return [{"document_name": "D", "page_number": 1, "similarity_score": 0.9}]

    async def fake_fov(*a, **k):
        return [{"filename": "f", "experiment_name": "E", "similarity_score": 0.8}]

    with patch.object(rag, "search_documents", fake_docs), \
         patch.object(rag, "search_fov_images", fake_fov):
        out = await rag.combined_search("q", 7, mock_db)
    assert out["documents"] and out["fov_images"]
    assert "search_errors" not in out


async def test_combined_search_captures_both_errors(mock_db):
    async def boom_docs(*a, **k):
        raise RAGServiceError("docs failed")

    async def boom_fov(*a, **k):
        raise RAGServiceError("fov failed")

    with patch.object(rag, "search_documents", boom_docs), \
         patch.object(rag, "search_fov_images", boom_fov):
        out = await rag.combined_search("q", 7, mock_db, experiment_id=1,
                                        doc_limit=2, fov_limit=2)
    assert out["documents"] == [] and out["fov_images"] == []
    assert len(out["search_errors"]) == 2


# ============================================================================ #
# rag_service.get_context_for_chat
# ============================================================================ #
async def test_get_context_for_chat_with_results(mock_db):
    async def fake_combined(*a, **k):
        return {
            "query": "q",
            "documents": [{"document_name": "D", "page_number": 1,
                           "similarity_score": 0.95}],
            "fov_images": [{"filename": "img.tif", "experiment_name": "E",
                            "similarity_score": 0.88}],
        }

    with patch.object(rag, "combined_search", fake_combined):
        ctx = await rag.get_context_for_chat("q", 7, mock_db)
    assert "Relevant Documents" in ctx
    assert "Relevant Microscopy Images" in ctx
    assert "0.95" in ctx and "0.88" in ctx


async def test_get_context_for_chat_empty(mock_db):
    async def fake_combined(*a, **k):
        return {"query": "q", "documents": [], "fov_images": []}

    with patch.object(rag, "combined_search", fake_combined):
        ctx = await rag.get_context_for_chat("q", 7, mock_db)
    assert ctx == "No relevant documents or images found in the knowledge base."


# ============================================================================ #
# rag_service.index_fov_image
# ============================================================================ #
async def test_index_fov_image_success_mip(mock_db, tmp_path):
    img_file = make_png(tmp_path / "mip.png")
    image = SimpleNamespace(id=3, mip_path=str(img_file), file_path="/orig.tif",
                            rag_embedding=None, rag_indexed_at=None)
    mock_db.execute.return_value = make_result(scalar=image)
    with patch_encoder():
        ok = await rag.index_fov_image(3, mock_db)
    assert ok is True
    assert image.rag_embedding == [0.4, 0.5, 0.6]
    mock_db.commit.assert_awaited()


async def test_index_fov_image_fallback_file_path(mock_db, tmp_path):
    img_file = make_png(tmp_path / "orig.png")
    image = SimpleNamespace(id=3, mip_path=None, file_path=str(img_file),
                            rag_embedding=None, rag_indexed_at=None)
    mock_db.execute.return_value = make_result(scalar=image)
    with patch_encoder():
        assert await rag.index_fov_image(3, mock_db) is True


async def test_index_fov_image_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(RAGServiceError, match="not found for RAG indexing"):
        await rag.index_fov_image(99, mock_db)


async def test_index_fov_image_file_missing(mock_db):
    image = SimpleNamespace(id=3, mip_path="/no/such/file.png", file_path=None)
    mock_db.execute.return_value = make_result(scalar=image)
    with pytest.raises(RAGServiceError, match="Image file not found"):
        await rag.index_fov_image(3, mock_db)


async def test_index_fov_image_generic_exception(mock_db, tmp_path):
    img_file = make_png(tmp_path / "mip.png")
    image = SimpleNamespace(id=3, mip_path=str(img_file), file_path=None,
                            rag_embedding=None, rag_indexed_at=None)
    mock_db.execute.return_value = make_result(scalar=image)
    enc = fake_encoder()
    enc.encode_document.side_effect = RuntimeError("encode boom")
    with patch_encoder(enc):
        with pytest.raises(RAGServiceError, match="Failed to index image"):
            await rag.index_fov_image(3, mock_db)


# ============================================================================ #
# rag_service.get_document_content
# ============================================================================ #
async def test_get_document_content_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    assert await rag.get_document_content(1, 7, mock_db) is None


async def test_get_document_content_specific_pages_with_image(mock_db, tmp_path):
    img = make_png(tmp_path / "page1.png")
    p1 = page(page_number=1, extracted_text="text1", image_path=str(img))
    p2 = page(page_number=2, extracted_text=None, image_path=None)
    doc = document(id=10, page_count=2, pages=[p1, p2])
    mock_db.execute.return_value = make_result(scalar=doc)
    out = await rag.get_document_content(10, 7, mock_db, page_numbers=[1])
    assert len(out["pages"]) == 1
    assert "image_base64" in out["pages"][0]
    assert out["pages"][0]["image_mime_type"] == "image/png"
    # 1 of 2 pages shown -> "Showing" note
    assert out["note"].startswith("Showing 1 of 2")


async def test_get_document_content_default_max_pages_no_images(mock_db):
    pages = [page(page_number=i, extracted_text=None, image_path=None)
             for i in range(1, 4)]
    doc = document(id=10, page_count=3, pages=pages)
    mock_db.execute.return_value = make_result(scalar=doc)
    out = await rag.get_document_content(10, 7, mock_db, include_images=False,
                                         max_pages=10)
    # all 3 pages shown (<= page_count) -> "Use page images" note
    assert out["note"] == "Use page images to read content."
    assert all("image_base64" not in p for p in out["pages"])


async def test_get_document_content_caps_specific_pages(mock_db):
    # Regression: requesting many specific pages must still be capped -- a 200-page
    # PDF would otherwise inline every page into the context window.
    pages = [page(page_number=i, extracted_text=None, image_path=None)
             for i in range(1, 21)]
    doc = document(id=10, page_count=20, pages=pages)
    mock_db.execute.return_value = make_result(scalar=doc)
    out = await rag.get_document_content(
        10, 7, mock_db, page_numbers=list(range(1, 21)),
        include_images=False, max_pages=10)
    assert len(out["pages"]) == 10
    assert [p["page_number"] for p in out["pages"]] == list(range(1, 11))


async def test_get_document_content_image_read_error(mock_db, tmp_path):
    # image_path set + file exists, but open() raises -> warning branch.
    # get_document_content imports Path locally (from pathlib), so use a real
    # existing file and patch only builtins.open.
    img = make_png(tmp_path / "page1.png")
    p = page(page_number=1, extracted_text="t", image_path=str(img))
    doc = document(id=10, page_count=1, pages=[p])
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch("builtins.open", side_effect=OSError("read fail")):
        out = await rag.get_document_content(10, 7, mock_db)
    assert "image_base64" not in out["pages"][0]


# ============================================================================ #
# rag_service.get_all_documents_summary
# ============================================================================ #
async def test_get_all_documents_summary_with_preview(mock_db):
    long_text = "y" * 600
    d1 = document(id=1, name="A", pages=[page(page_number=2, extracted_text="zzz"),
                                         page(page_number=1, extracted_text=long_text)])
    d2 = document(id=2, name="B", pages=[])  # no pages
    d3 = document(id=3, name="C",
                  pages=[page(page_number=1, extracted_text=None)])  # no text
    mock_db.execute.return_value = make_result(scalars_all=[d1, d2, d3])
    out = await rag.get_all_documents_summary(7, mock_db)
    assert len(out) == 3
    assert out[0]["first_page_preview"].endswith("...")  # truncated long text
    assert "first_page_preview" not in out[1]
    assert "first_page_preview" not in out[2]


async def test_get_all_documents_summary_short_preview_and_flag_off(mock_db):
    d1 = document(id=1, name="A",
                  pages=[page(page_number=1, extracted_text="short")])
    mock_db.execute.return_value = make_result(scalars_all=[d1])
    out = await rag.get_all_documents_summary(7, mock_db,
                                              include_first_page_text=True)
    assert out[0]["first_page_preview"] == "short"

    mock_db.execute.return_value = make_result(scalars_all=[d1])
    out2 = await rag.get_all_documents_summary(7, mock_db,
                                               include_first_page_text=False)
    assert "first_page_preview" not in out2[0]


# ============================================================================ #
# rag_service.batch_index_fov_images
# ============================================================================ #
async def test_batch_index_experiment_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    out = await rag.batch_index_fov_images(1, 7, mock_db)
    assert out["error"] == "Experiment not found"


async def test_batch_index_success_and_failures(mock_db):
    exp = SimpleNamespace(id=1)
    images = [SimpleNamespace(id=i) for i in range(1, 9)]  # 8 images
    mock_db.execute.side_effect = [
        make_result(scalar=exp),          # ownership
        make_result(scalars_all=images),  # unindexed images
    ]

    call = {"n": 0}

    async def fake_index(image_id, db):
        call["n"] += 1
        # first 1 succeeds, rest 7 fail (so failed > 5 errors collected)
        if image_id == 1:
            return True
        raise RAGServiceError(f"fail {image_id}")

    with patch.object(rag, "index_fov_image", fake_index):
        out = await rag.batch_index_fov_images(1, 7, mock_db)
    assert out["indexed"] == 1
    assert out["failed"] == 7
    assert out["total"] == 8
    # only first 5 errors collected + a "... and N more" line
    assert len(out["error_samples"]) == 6
    assert "more errors" in out["error_samples"][-1]


async def test_batch_index_all_success_no_errorsamples(mock_db):
    exp = SimpleNamespace(id=1)
    images = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    mock_db.execute.side_effect = [
        make_result(scalar=exp),
        make_result(scalars_all=images),
    ]

    async def fake_index(image_id, db):
        return True

    with patch.object(rag, "index_fov_image", fake_index):
        out = await rag.batch_index_fov_images(1, 7, mock_db)
    assert out["indexed"] == 2 and out["failed"] == 0
    assert "error_samples" not in out


# ============================================================================ #
# rag_service passage helpers
# ============================================================================ #
def test_passage_hash_deterministic():
    h1 = rag._get_passage_hash(1, 2, [10, 20, 30, 40])
    h2 = rag._get_passage_hash(1, 2, [10, 20, 30, 40])
    h3 = rag._get_passage_hash(1, 2, [10, 20, 30, 41])
    assert h1 == h2 and h1 != h3
    assert len(h1) == 12


def test_passages_cache_path_creates_dir(tmp_path):
    fake_settings = SimpleNamespace(rag_document_dir=tmp_path / "rag_documents")
    with patch.object(rag, "settings", fake_settings):
        p = rag._get_passages_cache_path(7)
    assert p.exists()
    assert p.name == "7"
    assert p.parent.name == rag.PASSAGES_CACHE_DIR


# ============================================================================ #
# rag_service._get_document_page_image_path
# ============================================================================ #
async def test_page_image_path_doc_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    doc, path = await rag._get_document_page_image_path(1, 1, 7, mock_db)
    assert doc is None and path is None


async def test_page_image_path_page_missing(mock_db):
    doc = document(pages=[page(page_number=2, image_path="/p.png")])
    mock_db.execute.return_value = make_result(scalar=doc)
    d, p = await rag._get_document_page_image_path(1, 1, 7, mock_db)
    assert d is None and p is None


async def test_page_image_path_file_missing(mock_db):
    doc = document(pages=[page(page_number=1, image_path="/no/such.png")])
    mock_db.execute.return_value = make_result(scalar=doc)
    d, p = await rag._get_document_page_image_path(1, 1, 7, mock_db)
    assert d is None and p is None


async def test_page_image_path_success(mock_db, tmp_path):
    img = make_png(tmp_path / "p.png")
    doc = document(pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    d, p = await rag._get_document_page_image_path(1, 1, 7, mock_db)
    assert d is doc and p == Path(str(img))


# ============================================================================ #
# rag_service.extract_passage_image
# ============================================================================ #
async def test_extract_passage_bad_bbox_len(mock_db):
    assert await rag.extract_passage_image(1, 1, [1, 2, 3], 7, mock_db) is None


async def test_extract_passage_out_of_range(mock_db):
    assert await rag.extract_passage_image(1, 1, [0, 0, 2000, 500], 7, mock_db) is None


async def test_extract_passage_bad_order(mock_db):
    assert await rag.extract_passage_image(1, 1, [500, 100, 100, 600], 7, mock_db) is None


async def test_extract_passage_path_none(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    assert await rag.extract_passage_image(1, 1, [10, 10, 900, 900], 7, mock_db) is None


async def test_extract_passage_too_small(mock_db, tmp_path):
    img = make_png(tmp_path / "p.png", size=(100, 100))
    doc = document(pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    # tiny bbox -> crop < 50x50 even with padding clamped low; use padding 0
    out = await rag.extract_passage_image(1, 1, [10, 10, 20, 20], 7, mock_db,
                                          padding=0)
    assert out is None


async def test_extract_passage_full_page(mock_db, tmp_path):
    img = make_png(tmp_path / "p.png", size=(1000, 1000))
    doc = document(name="DocName", pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    fake_settings = SimpleNamespace(rag_document_dir=tmp_path / "rag_documents")
    with patch.object(rag, "settings", fake_settings):
        out = await rag.extract_passage_image(1, 1, [5, 5, 995, 995], 7, mock_db,
                                              padding=30)
    assert out["type"] == "full_page"
    assert out["document_name"] == "DocName"


async def test_extract_passage_success(mock_db, tmp_path):
    img = make_png(tmp_path / "p.png", size=(1000, 1000))
    doc = document(name="DocName", pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    fake_settings = SimpleNamespace(rag_document_dir=tmp_path / "rag_documents")
    with patch.object(rag, "settings", fake_settings):
        out = await rag.extract_passage_image(1, 1, [100, 100, 400, 400], 7,
                                              mock_db, padding=10)
    assert out["type"] == "passage"
    assert "image_base64" in out
    assert out["bbox_normalized"] == [100, 100, 400, 400]
    # cached passage png written to the user's cache dir
    saved = list((tmp_path / "rag_passages" / "7").glob("*.png"))
    assert len(saved) == 1


async def test_extract_passage_exception(mock_db, tmp_path):
    img = make_png(tmp_path / "p.png", size=(1000, 1000))
    doc = document(pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    # Make PIL.Image.open raise during processing
    with patch("PIL.Image.open", side_effect=RuntimeError("img boom")):
        out = await rag.extract_passage_image(1, 1, [100, 100, 400, 400], 7,
                                              mock_db)
    assert out is None


# ============================================================================ #
# rag_service.render_page_region  (on-demand high-DPI zoom)
# ============================================================================ #
_REGION_SETTINGS = SimpleNamespace(rag_region_dpi=300, rag_region_max_edge=1600)


@pytest.mark.parametrize("bbox", [[1, 2, 3], [0, 0, 2000, 500], [500, 100, 100, 600]])
async def test_render_region_invalid_bbox(mock_db, bbox):
    # Bad length / out-of-range / bad order are all rejected before any DB call.
    assert await rag.render_page_region(1, 1, bbox, 7, mock_db) is None


async def test_render_region_page_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    assert await rag.render_page_region(1, 1, [10, 10, 900, 900], 7, mock_db) is None


async def test_render_region_pdf_uses_hires_and_caps_edge(mock_db, tmp_path):
    # Stored raster is tiny (200x200); the hi-res PDF render is huge. If the crop
    # comes back near the max edge, the hi-res path (not the raster) was used.
    raster = make_png(tmp_path / "p.webp", size=(200, 200))
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    doc = document(file_type="pdf", original_path=str(pdf),
                   pages=[page(page_number=4, image_path=str(raster))])
    mock_db.execute.return_value = make_result(scalar=doc)
    hires = PILImage.new("RGB", (3000, 4000), (10, 20, 30))
    render = AsyncMock(return_value=hires)
    with patch.object(rag, "settings", _REGION_SETTINGS), \
         patch("services.document_indexing_service.render_single_pdf_page", render):
        out = await rag.render_page_region(4, 4, [0, 0, 1000, 1000], 7, mock_db)
    assert out is not None
    render.assert_awaited_once()
    got = PILImage.open(BytesIO(out))
    assert max(got.size) == 1600  # longest edge capped
    assert max(got.size) > 200    # proves the hi-res source, not the 200px raster


async def test_render_single_pdf_page_success(tmp_path):
    img = PILImage.new("RGB", (120, 160), (0, 0, 0))
    with patch("pdf2image.convert_from_path", return_value=[img]) as conv:
        out = await dind.render_single_pdf_page(tmp_path / "d.pdf", 3, 300)
    assert out is img
    # single page rendered in isolation -> first_page == last_page == 3
    _, kwargs = conv.call_args
    assert kwargs["first_page"] == 3 and kwargs["last_page"] == 3 and kwargs["dpi"] == 300


async def test_render_single_pdf_page_empty_returns_none(tmp_path):
    with patch("pdf2image.convert_from_path", return_value=[]):
        assert await dind.render_single_pdf_page(tmp_path / "d.pdf", 1, 300) is None


async def test_render_single_pdf_page_error_returns_none(tmp_path):
    with patch("pdf2image.convert_from_path", side_effect=RuntimeError("poppler boom")):
        assert await dind.render_single_pdf_page(tmp_path / "d.pdf", 1, 300) is None


async def test_render_region_non_pdf_falls_back_to_raster(mock_db, tmp_path):
    raster = make_png(tmp_path / "p.png", size=(800, 1000))
    doc = document(file_type="image", original_path=str(tmp_path / "img.png"),
                   pages=[page(page_number=1, image_path=str(raster))])
    mock_db.execute.return_value = make_result(scalar=doc)
    render = AsyncMock()
    with patch.object(rag, "settings", _REGION_SETTINGS), \
         patch("services.document_indexing_service.render_single_pdf_page", render):
        out = await rag.render_page_region(1, 1, [100, 100, 500, 500], 7, mock_db)
    assert out is not None
    render.assert_not_awaited()  # non-PDF never triggers a PDF render
    got = PILImage.open(BytesIO(out))
    assert max(got.size) <= 800  # cropped from the 800px raster, not upscaled


# ============================================================================ #
# rag_service.get_cached_passage
# ============================================================================ #
async def test_get_cached_passage_not_owned(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    assert await rag.get_cached_passage(1, "abc", 7, mock_db) is None


async def test_get_cached_passage_exists(mock_db, tmp_path):
    mock_db.execute.return_value = make_result(scalar=1)
    fake_settings = SimpleNamespace(rag_document_dir=tmp_path / "rag_documents")
    with patch.object(rag, "settings", fake_settings):
        cache = rag._get_passages_cache_path(7)
        (cache / "deadbeef.png").write_bytes(b"x")
        out = await rag.get_cached_passage(1, "deadbeef", 7, mock_db)
    assert out is not None and out.name == "deadbeef.png"


async def test_get_cached_passage_missing(mock_db, tmp_path):
    mock_db.execute.return_value = make_result(scalar=1)
    fake_settings = SimpleNamespace(rag_document_dir=tmp_path / "rag_documents")
    with patch.object(rag, "settings", fake_settings):
        out = await rag.get_cached_passage(1, "nope", 7, mock_db)
    assert out is None


async def test_get_cached_passage_traversal(mock_db, tmp_path):
    mock_db.execute.return_value = make_result(scalar=1)
    fake_settings = SimpleNamespace(rag_document_dir=tmp_path / "rag_documents")
    with patch.object(rag, "settings", fake_settings):
        # hash escapes the cache dir
        out = await rag.get_cached_passage(1, "../../etc/passwd", 7, mock_db)
    assert out is None


# ============================================================================ #
# rag_service.extract_relevant_passages
# ============================================================================ #
def _gemini_response(text):
    resp = SimpleNamespace(text=text)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    client = SimpleNamespace(aio=aio)
    return client


def _patch_genai(client):
    """Inject a fake google.genai module so the lazy import inside the function
    resolves to our stub."""
    genai = pytypes.ModuleType("google.genai")
    genai.Client = MagicMock(return_value=client)
    type_mod = pytypes.ModuleType("google.genai.types")
    type_mod.Content = MagicMock()
    type_mod.Part = MagicMock()
    type_mod.Blob = MagicMock()
    type_mod.GenerateContentConfig = MagicMock()
    genai.types = type_mod
    google_pkg = pytypes.ModuleType("google")
    google_pkg.genai = genai
    return patch.dict("sys.modules", {
        "google": google_pkg,
        "google.genai": genai,
        "google.genai.types": type_mod,
    })


async def test_extract_passages_no_api_key(mock_db):
    fake_settings = SimpleNamespace(gemini_api_key="")
    with patch.object(rag, "settings", fake_settings):
        assert await rag.extract_relevant_passages(1, 1, "q", 7, mock_db) == []


async def test_extract_passages_doc_not_found(mock_db):
    fake_settings = SimpleNamespace(gemini_api_key="key", gemini_vision_model="gemini-3.5-flash")
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(rag, "settings", fake_settings), \
         _patch_genai(_gemini_response("[]")):
        assert await rag.extract_relevant_passages(1, 1, "q", 7, mock_db) == []


async def test_extract_passages_success_with_markdown(mock_db, tmp_path):
    img = make_png(tmp_path / "p.png", size=(1000, 1000))
    doc = document(name="D", pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    fake_settings = SimpleNamespace(gemini_api_key="key",
                                    gemini_vision_model="gemini-3.5-flash",
                                    rag_document_dir=tmp_path / "rag_documents")
    # Response wrapped in markdown fence; one figure, one too-short bbox skipped
    payload = json.dumps([
        {"text": "fig", "box_2d": [100, 100, 400, 400], "type": "figure",
         "confidence": 0.9},
        {"text": "bad", "box_2d": [1, 2, 3]},  # len != 4 -> skipped
    ])
    fenced = "```json\n" + payload + "\n```"
    client = _gemini_response(fenced)
    with patch.object(rag, "settings", fake_settings), _patch_genai(client):
        out = await rag.extract_relevant_passages(1, 1, "q", 7, mock_db)
    assert len(out) == 1
    assert out[0]["passage_type"] == "figure"
    assert out[0]["confidence"] == 0.9
    assert out[0]["extracted_text"] == "fig"


async def test_extract_passages_non_list_response(mock_db, tmp_path):
    img = make_png(tmp_path / "p.png", size=(1000, 1000))
    doc = document(pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    fake_settings = SimpleNamespace(gemini_api_key="key",
                                    gemini_vision_model="gemini-3.5-flash",
                                    rag_document_dir=tmp_path / "rag_documents")
    client = _gemini_response('{"not": "a list"}')
    with patch.object(rag, "settings", fake_settings), _patch_genai(client):
        out = await rag.extract_relevant_passages(1, 1, "q", 7, mock_db)
    assert out == []


async def test_extract_passages_json_decode_error(mock_db, tmp_path):
    img = make_png(tmp_path / "p.png", size=(1000, 1000))
    doc = document(pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    fake_settings = SimpleNamespace(gemini_api_key="key",
                                    gemini_vision_model="gemini-3.5-flash",
                                    rag_document_dir=tmp_path / "rag_documents")
    client = _gemini_response("not valid json {")
    with patch.object(rag, "settings", fake_settings), _patch_genai(client):
        out = await rag.extract_relevant_passages(1, 1, "q", 7, mock_db)
    assert out == []


async def test_extract_passages_value_error(mock_db, tmp_path):
    # json.loads succeeds; a ValueError raised from inside the extraction loop
    # exercises the `except ValueError` handler.
    img = make_png(tmp_path / "p.png", size=(1000, 1000))
    doc = document(pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    fake_settings = SimpleNamespace(gemini_api_key="key",
                                    gemini_vision_model="gemini-3.5-flash",
                                    rag_document_dir=tmp_path / "rag_documents")
    payload = json.dumps([{"text": "t", "box_2d": [10, 10, 400, 400],
                           "type": "text"}])
    client = _gemini_response(payload)

    async def boom(*a, **k):
        raise ValueError("no usable text")

    with patch.object(rag, "settings", fake_settings), _patch_genai(client), \
         patch.object(rag, "extract_passage_image", boom):
        out = await rag.extract_relevant_passages(1, 1, "q", 7, mock_db)
    assert out == []


async def test_extract_passages_generic_exception(mock_db, tmp_path):
    img = make_png(tmp_path / "p.png", size=(1000, 1000))
    doc = document(pages=[page(page_number=1, image_path=str(img))])
    mock_db.execute.return_value = make_result(scalar=doc)
    fake_settings = SimpleNamespace(gemini_api_key="key",
                                    gemini_vision_model="gemini-3.5-flash",
                                    rag_document_dir=tmp_path / "rag_documents")
    payload = json.dumps([{"text": "t", "box_2d": [10, 10, 400, 400],
                           "type": "text"}])
    client = _gemini_response(payload)

    async def boom(*a, **k):
        raise RuntimeError("api boom")

    with patch.object(rag, "settings", fake_settings), _patch_genai(client), \
         patch.object(rag, "extract_passage_image", boom):
        out = await rag.extract_relevant_passages(1, 1, "q", 7, mock_db)
    assert out == []


# ============================================================================ #
# document_indexing_service.get_file_type / is_supported_file
# ============================================================================ #
def test_get_file_type_all_kinds():
    assert dind.get_file_type("a.pdf") == "pdf"
    assert dind.get_file_type("a.docx") == "office"
    assert dind.get_file_type("a.PNG") == "image"
    assert dind.get_file_type("a.mp4") == "video"
    assert dind.get_file_type("a.xyz") is None


def test_is_supported_file():
    assert dind.is_supported_file("a.pdf") is True
    assert dind.is_supported_file("a.unknown") is False


# ============================================================================ #
# document_indexing_service.save_uploaded_document
# ============================================================================ #
async def test_save_uploaded_unsupported(mock_db):
    with pytest.raises(ValueError, match="Unsupported file type"):
        await dind.save_uploaded_document(7, "bad.xyz", b"data", mock_db)


async def test_save_uploaded_success(mock_db, tmp_path):
    fake_settings = SimpleNamespace(rag_document_dir=tmp_path / "rag_documents")
    captured = {}

    def add(obj):
        captured["doc"] = obj

    mock_db.add = MagicMock(side_effect=add)
    mock_db.execute.return_value = make_result(scalar=None)  # no dedupe hit
    with patch.object(dind, "settings", fake_settings):
        doc, created = await dind.save_uploaded_document(
            7, "My Report!.pdf", b"hello", mock_db, thread_id=42)
    assert created is True
    assert doc.file_type == "pdf"
    assert doc.file_size == 5
    assert doc.status == DocumentStatus.PENDING.value
    # chat attachment -> scoped to the thread
    assert doc.thread_id == 42
    # file was actually written
    assert Path(doc.original_path).read_bytes() == b"hello"
    # special chars sanitized, .pdf extension preserved
    assert Path(doc.original_path).name.endswith(".pdf")
    mock_db.flush.assert_awaited()


async def test_save_uploaded_sanitizes_special_chars(mock_db, tmp_path):
    fake_settings = SimpleNamespace(rag_document_dir=tmp_path / "rag_documents")
    mock_db.add = MagicMock()
    mock_db.execute.return_value = make_result(scalar=None)  # no dedupe hit
    with patch.object(dind, "settings", fake_settings):
        # special chars replaced/stripped; alnum extension chars survive
        doc, _ = await dind.save_uploaded_document(7, "!!!.png", b"x", mock_db)
    name = Path(doc.original_path).name
    assert "!" not in name
    assert name.endswith(".png")
    # stays inside the per-user directory
    assert str(tmp_path / "rag_documents" / "7") in doc.original_path


# ============================================================================ #
# document_indexing_service.convert_office_to_pdf
# ============================================================================ #
async def test_convert_office_success(tmp_path):
    inp = tmp_path / "doc.docx"
    inp.write_bytes(b"x")
    out_pdf = tmp_path / "doc.pdf"
    out_pdf.write_bytes(b"%PDF")

    proc = AsyncMock()
    proc.communicate.return_value = (b"ok", b"")
    proc.returncode = 0
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await dind.convert_office_to_pdf(inp)
    assert result == out_pdf


async def test_convert_office_nonzero_returncode(tmp_path):
    inp = tmp_path / "doc.docx"
    inp.write_bytes(b"x")
    proc = AsyncMock()
    proc.communicate.return_value = (b"", b"error")
    proc.returncode = 1
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        assert await dind.convert_office_to_pdf(inp) is None


async def test_convert_office_pdf_missing(tmp_path):
    inp = tmp_path / "doc.docx"
    inp.write_bytes(b"x")
    proc = AsyncMock()
    proc.communicate.return_value = (b"ok", b"")
    proc.returncode = 0
    # returncode 0 but no output pdf produced
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        assert await dind.convert_office_to_pdf(inp) is None


async def test_convert_office_timeout(tmp_path):
    import asyncio
    inp = tmp_path / "doc.docx"
    inp.write_bytes(b"x")
    with patch("asyncio.create_subprocess_exec", AsyncMock()), \
         patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError())):
        assert await dind.convert_office_to_pdf(inp) is None


async def test_convert_office_generic_exception(tmp_path):
    inp = tmp_path / "doc.docx"
    inp.write_bytes(b"x")
    with patch("asyncio.create_subprocess_exec",
               AsyncMock(side_effect=RuntimeError("boom"))):
        assert await dind.convert_office_to_pdf(inp) is None


# ============================================================================ #
# document_indexing_service.render_pdf_to_images
# ============================================================================ #
async def test_render_pdf_success():
    imgs = [PILImage.new("RGB", (10, 10)), PILImage.new("RGB", (10, 10))]
    fake_pdf2image = pytypes.ModuleType("pdf2image")
    fake_pdf2image.convert_from_path = MagicMock(return_value=imgs)
    with patch.dict("sys.modules", {"pdf2image": fake_pdf2image}):
        out = await dind.render_pdf_to_images(Path("/x.pdf"))
    assert len(out) == 2
    assert out[0][0] == 1 and out[1][0] == 2
    # No page cap -> no first_page/last_page passed.
    assert "last_page" not in fake_pdf2image.convert_from_path.call_args.kwargs


async def test_render_pdf_caps_pages_for_attachments():
    fake_pdf2image = pytypes.ModuleType("pdf2image")
    fake_pdf2image.convert_from_path = MagicMock(return_value=[PILImage.new("RGB", (10, 10))])
    with patch.dict("sys.modules", {"pdf2image": fake_pdf2image}):
        await dind.render_pdf_to_images(Path("/x.pdf"), max_pages=100)
    kw = fake_pdf2image.convert_from_path.call_args.kwargs
    assert kw["first_page"] == 1 and kw["last_page"] == 100


async def test_render_pdf_import_error():
    # Force the lazy `from pdf2image import convert_from_path` to fail
    with patch.dict("sys.modules", {"pdf2image": None}):
        out = await dind.render_pdf_to_images(Path("/x.pdf"))
    assert out is None


async def test_render_pdf_generic_exception():
    fake_pdf2image = pytypes.ModuleType("pdf2image")
    fake_pdf2image.convert_from_path = MagicMock(side_effect=RuntimeError("boom"))
    with patch.dict("sys.modules", {"pdf2image": fake_pdf2image}):
        out = await dind.render_pdf_to_images(Path("/x.pdf"))
    assert out is None


# ============================================================================ #
# document_indexing_service.process_pdf_pages
# ============================================================================ #
async def test_process_pdf_pages_success_with_ocr(mock_db, tmp_path):
    doc = SimpleNamespace(id=5, original_path=str(tmp_path / "doc.pdf"), thread_id=None, truncated_from_pages=None,
                          status=None, progress=0.0, indexed_at=None,
                          error_message=None)
    images = [(1, PILImage.new("RGB", (10, 10))), (2, PILImage.new("RGB", (10, 10)))]
    fake_tess = pytypes.ModuleType("pytesseract")
    fake_tess.image_to_string = MagicMock(return_value="  page text  ")
    with patch_encoder(), patch.dict("sys.modules", {"pytesseract": fake_tess}):
        await dind.process_pdf_pages(doc, images, mock_db)
    assert doc.status == DocumentStatus.COMPLETED.value
    assert doc.progress == 1.0
    # 2 pages added
    assert mock_db.add.call_count == 2


async def test_process_pdf_pages_ocr_failure_still_indexes(mock_db, tmp_path):
    doc = SimpleNamespace(id=5, original_path=str(tmp_path / "doc.pdf"), thread_id=None, truncated_from_pages=None,
                          status=None, progress=0.0, indexed_at=None,
                          error_message=None)
    images = [(1, PILImage.new("RGB", (10, 10)))]
    fake_tess = pytypes.ModuleType("pytesseract")
    fake_tess.image_to_string = MagicMock(side_effect=RuntimeError("ocr boom"))
    with patch_encoder(), patch.dict("sys.modules", {"pytesseract": fake_tess}):
        await dind.process_pdf_pages(doc, images, mock_db)
    assert doc.status == DocumentStatus.COMPLETED.value


async def test_process_pdf_pages_partial_failure(mock_db, tmp_path):
    doc = SimpleNamespace(id=5, original_path=str(tmp_path / "doc.pdf"), thread_id=None, truncated_from_pages=None,
                          status=None, progress=0.0, indexed_at=None,
                          error_message=None)
    images = [(1, PILImage.new("RGB", (10, 10))), (2, PILImage.new("RGB", (10, 10)))]
    enc = fake_encoder()
    # First page OK, second page encode raises -> partial failure
    enc.encode_document.side_effect = [np.array([1.0]), RuntimeError("boom")]
    fake_tess = pytypes.ModuleType("pytesseract")
    fake_tess.image_to_string = MagicMock(return_value="")
    with patch_encoder(enc), patch.dict("sys.modules", {"pytesseract": fake_tess}):
        await dind.process_pdf_pages(doc, images, mock_db)
    assert doc.status == DocumentStatus.COMPLETED.value
    assert "Failed pages" in doc.error_message


async def test_process_pdf_pages_all_failed(mock_db, tmp_path):
    doc = SimpleNamespace(id=5, original_path=str(tmp_path / "doc.pdf"), thread_id=None, truncated_from_pages=None,
                          status=None, progress=0.0, indexed_at=None,
                          error_message=None)
    images = [(1, PILImage.new("RGB", (10, 10)))]
    enc = fake_encoder()
    enc.encode_document.side_effect = RuntimeError("boom")
    fake_tess = pytypes.ModuleType("pytesseract")
    fake_tess.image_to_string = MagicMock(return_value="")
    with patch_encoder(enc), patch.dict("sys.modules", {"pytesseract": fake_tess}):
        await dind.process_pdf_pages(doc, images, mock_db)
    assert doc.status == DocumentStatus.FAILED.value
    # Error message reports the actual last failure, not a hardcoded guess.
    assert "All 1 pages failed" in doc.error_message
    assert "RuntimeError: boom" in doc.error_message


# ============================================================================ #
# document_indexing_service.process_single_image
# ============================================================================ #
async def test_process_single_image_success(mock_db, tmp_path):
    img = make_png(tmp_path / "single.png")
    doc = SimpleNamespace(id=9, page_count=0, status=None, progress=0.0,
                          indexed_at=None, error_message=None)
    with patch_encoder():
        await dind.process_single_image(doc, img, mock_db)
    assert doc.status == DocumentStatus.COMPLETED.value
    assert doc.page_count == 1
    mock_db.add.assert_called_once()


async def test_process_single_image_failure(mock_db):
    doc = SimpleNamespace(id=9, page_count=0, status=None, progress=0.0,
                          indexed_at=None, error_message=None)
    # Image.open on a non-existent path raises -> FAILED branch
    await dind.process_single_image(doc, Path("/no/such/img.png"), mock_db)
    assert doc.status == DocumentStatus.FAILED.value
    assert doc.error_message


# ============================================================================ #
# document_indexing_service.delete_document
# ============================================================================ #
async def test_delete_document_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    assert await dind.delete_document(1, 7, mock_db) is False


async def test_delete_document_success(mock_db, tmp_path):
    orig = tmp_path / "doc.pdf"
    orig.write_bytes(b"x")
    pages_dir = tmp_path / "doc_3_pages"
    pages_dir.mkdir()
    (pages_dir / "page_0001.png").write_bytes(b"y")
    doc = SimpleNamespace(id=3, original_path=str(orig), thread_id=None, truncated_from_pages=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    assert await dind.delete_document(3, 7, mock_db) is True
    assert not orig.exists()
    assert not pages_dir.exists()
    mock_db.delete.assert_awaited_once()


async def test_delete_document_file_error_still_deletes_db(mock_db):
    doc = SimpleNamespace(id=3, original_path="/x/doc.pdf", thread_id=None, truncated_from_pages=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    # Path.exists raises -> warning branch, but DB delete still happens
    with patch("services.document_indexing_service.Path") as MockPath:
        MockPath.side_effect = RuntimeError("path boom")
        assert await dind.delete_document(3, 7, mock_db) is True
    mock_db.delete.assert_awaited_once()


# ============================================================================ #
# document_indexing_service.process_document_async
# ============================================================================ #
async def test_process_document_async_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)):
        await dind.process_document_async(1)  # returns silently


async def test_process_document_async_pdf_success(mock_db, tmp_path):
    doc = SimpleNamespace(id=1, original_path=str(tmp_path / "d.pdf"), thread_id=None, truncated_from_pages=None,
                          file_type="pdf", status=None, page_count=0,
                          error_message=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    imgs = [(1, PILImage.new("RGB", (5, 5)))]
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "render_pdf_to_images", AsyncMock(return_value=imgs)), \
         patch.object(dind, "process_pdf_pages", AsyncMock()) as ppp:
        await dind.process_document_async(1)
    assert doc.page_count == 1
    ppp.assert_awaited_once()


async def test_process_document_async_office_convert_fail(mock_db, tmp_path):
    doc = SimpleNamespace(id=1, original_path=str(tmp_path / "d.docx"), thread_id=None, truncated_from_pages=None,
                          file_type="office", status=None, error_message=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "convert_office_to_pdf", AsyncMock(return_value=None)):
        await dind.process_document_async(1)
    assert doc.status == DocumentStatus.FAILED.value
    assert "convert office" in doc.error_message


async def test_process_document_async_office_success(mock_db, tmp_path):
    pdf = tmp_path / "d.pdf"
    doc = SimpleNamespace(id=1, original_path=str(tmp_path / "d.docx"), thread_id=None, truncated_from_pages=None,
                          file_type="office", status=None, page_count=0,
                          error_message=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    imgs = [(1, PILImage.new("RGB", (5, 5)))]
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "convert_office_to_pdf", AsyncMock(return_value=pdf)), \
         patch.object(dind, "render_pdf_to_images", AsyncMock(return_value=imgs)), \
         patch.object(dind, "process_pdf_pages", AsyncMock()):
        await dind.process_document_async(1)
    assert doc.page_count == 1


async def test_process_document_async_image(mock_db, tmp_path):
    doc = SimpleNamespace(id=1, original_path=str(tmp_path / "d.png"), thread_id=None, truncated_from_pages=None,
                          file_type="image", status=None, error_message=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "process_single_image", AsyncMock()) as psi:
        await dind.process_document_async(1)
    psi.assert_awaited_once()


async def test_process_document_async_unsupported_type(mock_db, tmp_path):
    doc = SimpleNamespace(id=1, original_path=str(tmp_path / "d.xyz"), thread_id=None, truncated_from_pages=None,
                          file_type="video", status=None, error_message=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)):
        await dind.process_document_async(1)
    assert doc.status == DocumentStatus.FAILED.value
    assert "Unsupported file type for processing" in doc.error_message


async def test_process_document_async_render_none(mock_db, tmp_path):
    doc = SimpleNamespace(id=1, original_path=str(tmp_path / "d.pdf"), thread_id=None, truncated_from_pages=None,
                          file_type="pdf", status=None, error_message=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "render_pdf_to_images", AsyncMock(return_value=None)):
        await dind.process_document_async(1)
    assert doc.status == DocumentStatus.FAILED.value
    assert "Failed to render PDF" in doc.error_message


async def test_process_document_async_render_empty(mock_db, tmp_path):
    doc = SimpleNamespace(id=1, original_path=str(tmp_path / "d.pdf"), thread_id=None, truncated_from_pages=None,
                          file_type="pdf", status=None, error_message=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "render_pdf_to_images", AsyncMock(return_value=[])):
        await dind.process_document_async(1)
    assert doc.status == DocumentStatus.FAILED.value
    assert "no pages" in doc.error_message


async def test_process_document_async_top_level_exception(mock_db, tmp_path):
    doc = SimpleNamespace(id=1, original_path=str(tmp_path / "d.pdf"), thread_id=None, truncated_from_pages=None,
                          file_type="pdf", status=None, error_message=None)
    # First execute (in try) returns doc; raise during render; recovery
    # block re-fetches doc and marks FAILED.
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "render_pdf_to_images",
                      AsyncMock(side_effect=RuntimeError("kaboom"))):
        await dind.process_document_async(1)
    assert doc.status == DocumentStatus.FAILED.value
    assert "kaboom" in doc.error_message


# ============================================================================ #
# document_indexing_service.reindex_document
# ============================================================================ #
async def test_reindex_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)):
        out = await dind.reindex_document(1, 7)
    assert out["error"] == "Document not found"


async def test_reindex_original_missing(mock_db):
    doc = SimpleNamespace(id=1, original_path="/no/such/file.pdf", status=None,
                          progress=0.0, error_message=None, indexed_at=None,
                          page_count=0, thread_id=None, truncated_from_pages=None)
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)):
        out = await dind.reindex_document(1, 7)
    assert out["error"] == "Original file not found"
    assert doc.status == DocumentStatus.FAILED.value


async def test_reindex_render_none(mock_db, tmp_path):
    orig = tmp_path / "d.pdf"
    orig.write_bytes(b"x")
    doc = SimpleNamespace(id=1, original_path=str(orig), thread_id=None, truncated_from_pages=None, status=None,
                          progress=0.0, error_message=None, indexed_at=None,
                          page_count=0)
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "render_pdf_to_images", AsyncMock(return_value=None)):
        out = await dind.reindex_document(1, 7)
    assert "Failed to render PDF pages" in out["error"]


async def test_reindex_render_empty(mock_db, tmp_path):
    orig = tmp_path / "d.pdf"
    orig.write_bytes(b"x")
    doc = SimpleNamespace(id=1, original_path=str(orig), thread_id=None, truncated_from_pages=None, status=None,
                          progress=0.0, error_message=None, indexed_at=None,
                          page_count=0)
    mock_db.execute.return_value = make_result(scalar=doc)
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "render_pdf_to_images", AsyncMock(return_value=[])):
        out = await dind.reindex_document(1, 7)
    assert out["error"] == "PDF has no pages to index"


async def test_reindex_success(mock_db, tmp_path):
    orig = tmp_path / "d.pdf"
    orig.write_bytes(b"x")
    doc = SimpleNamespace(id=1, original_path=str(orig), thread_id=None, truncated_from_pages=None, status=None,
                          progress=0.0, error_message=None, indexed_at=None,
                          page_count=0)
    mock_db.execute.return_value = make_result(scalar=doc)
    imgs = [(1, PILImage.new("RGB", (5, 5))), (2, PILImage.new("RGB", (5, 5)))]
    with patch.object(dind, "get_db_context", lambda: _ctx(mock_db)), \
         patch.object(dind, "render_pdf_to_images", AsyncMock(return_value=imgs)), \
         patch.object(dind, "process_pdf_pages", AsyncMock()):
        out = await dind.reindex_document(1, 7)
    assert out["status"] == "completed"
    assert out["page_count"] == 2


# ============================================================================ #
# document_indexing_service.get_indexing_status
# ============================================================================ #
async def test_get_indexing_status(mock_db):
    doc_rows = [("pending", 2), ("completed", 5), ("failed", 1)]
    mock_db.execute.side_effect = [
        make_result(fetchall=doc_rows),  # doc counts grouped
        make_result(scalar=3),           # fov pending
        make_result(scalar=7),           # fov indexed
    ]
    out = await dind.get_indexing_status(7, mock_db)
    assert out["documents_pending"] == 2
    assert out["documents_completed"] == 5
    assert out["documents_failed"] == 1
    assert out["documents_processing"] == 0
    assert out["fov_images_pending"] == 3
    assert out["fov_images_indexed"] == 7


# ============================================================================ #
# chat attachments: page cap wiring + scope
# ============================================================================ #
def test_page_cap_for_attachment_vs_library():
    # SSOT: attachments capped, library uncapped. Both the initial index and
    # reindex go through this, so they cannot drift apart.
    assert dind._page_cap_for(document(thread_id=42)) == dind.settings.chat_attachment_max_pages
    assert dind._page_cap_for(document(thread_id=None)) is None


def test_document_scope_library_excludes_attachments():
    from models.rag_document import document_scope
    # No thread -> library only, so attachments never pollute the library list.
    assert "thread_id IS NULL" in str(document_scope(7))


def test_document_scope_thread_includes_own_attachments_only():
    from models.rag_document import document_scope
    sql = str(document_scope(7, 42))
    # library OR this thread's attachments -- never another thread's.
    assert "thread_id IS NULL" in sql and "thread_id =" in sql
