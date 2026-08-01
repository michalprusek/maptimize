"""Indexing a document the server downloads, given a URL.

This exists because an agent cannot drive the base64 upload: the model would have
to emit the file's bytes itself, and a megabyte of base64 is roughly 350k tokens.
Sending a link instead moves the download to the server, where the discovery
importer's SSRF, content-type and size guards already live.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers import rag as rag_router
from services.paper_discovery_service import PdfFetchError


def _user(uid=7):
    return SimpleNamespace(id=uid)


def _doc(**over):
    return SimpleNamespace(
        id=5, name="p.pdf", file_type="pdf", status="pending", page_count=0,
        created_at="2026-08-01T00:00:00Z", source_url=None,
        **{"folder_id": None, "group_id": None, **over},
    )


async def test_the_url_is_fetched_server_side_and_the_document_is_filed(mock_db):
    doc = _doc()
    common = SimpleNamespace(id=4, group_id=2, visibility="group")
    with patch.object(rag_router, "_check_upload_rate_limit", AsyncMock()), \
         patch.object(rag_router, "fetch_pdf", AsyncMock(return_value=b"%PDF-1.4")) as fetch, \
         patch.object(rag_router, "get_user_group_ids", AsyncMock(return_value=[2])), \
         patch.object(rag_router, "default_upload_folder", AsyncMock(return_value=common)), \
         patch.object(rag_router, "save_uploaded_document", AsyncMock(return_value=(doc, True))):
        out = await rag_router.index_document_from_url(
            payload={"url": "https://example.org/paper.pdf"},
            background_tasks=SimpleNamespace(add_task=lambda *a: None),
            current_user=_user(), db=mock_db,
        )

    fetch.assert_awaited_once_with("https://example.org/paper.pdf")
    assert (doc.folder_id, doc.group_id) == (4, 2), "an unfiled document is invisible"
    assert doc.source_url == "https://example.org/paper.pdf"
    assert out.is_duplicate is False


async def test_a_refused_download_reports_why(mock_db):
    """403 vs wrong content-type vs over-size call for different next steps, so
    the reason has to survive to the caller rather than becoming a bare 400."""
    with patch.object(rag_router, "_check_upload_rate_limit", AsyncMock()), \
         patch.object(rag_router, "fetch_pdf",
                      AsyncMock(side_effect=PdfFetchError("refused: not a PDF"))):
        with pytest.raises(HTTPException) as exc:
            await rag_router.index_document_from_url(
                payload={"url": "https://example.org/x"},
                background_tasks=SimpleNamespace(add_task=lambda *a: None),
                current_user=_user(), db=mock_db,
            )
    assert exc.value.status_code == 400
    assert "not a PDF" in exc.value.detail


async def test_a_missing_url_is_rejected_before_anything_is_fetched(mock_db):
    with patch.object(rag_router, "_check_upload_rate_limit", AsyncMock()), \
         patch.object(rag_router, "fetch_pdf", AsyncMock()) as fetch:
        with pytest.raises(HTTPException) as exc:
            await rag_router.index_document_from_url(
                payload={}, background_tasks=SimpleNamespace(add_task=lambda *a: None),
                current_user=_user(), db=mock_db,
            )
    assert exc.value.status_code == 400
    fetch.assert_not_awaited()


async def test_a_duplicate_is_not_refiled(mock_db):
    """The existing row may be a colleague's -- writes stay owner-only, so a
    dedupe hit must not be relocated into the caller's folder."""
    existing = _doc(folder_id=99, group_id=2)
    with patch.object(rag_router, "_check_upload_rate_limit", AsyncMock()), \
         patch.object(rag_router, "fetch_pdf", AsyncMock(return_value=b"%PDF-1.4")), \
         patch.object(rag_router, "get_user_group_ids", AsyncMock(return_value=[2])), \
         patch.object(rag_router, "default_upload_folder", AsyncMock()), \
         patch.object(rag_router, "save_uploaded_document",
                      AsyncMock(return_value=(existing, False))):
        out = await rag_router.index_document_from_url(
            payload={"url": "https://example.org/paper.pdf"},
            background_tasks=SimpleNamespace(add_task=lambda *a: None),
            current_user=_user(), db=mock_db,
        )
    assert (existing.folder_id, existing.group_id) == (99, 2)
    assert out.is_duplicate is True
