"""Deduplication of uploaded documents by content hash.

The same file must not be stored, or indexed, twice. Indexing is the expensive
part -- page rendering plus Qwen VL embeddings on a GPU shared with Spheroseg --
so a duplicate that slips through costs real time, not just disk.

Deduplication lives in ``save_uploaded_document``, the one function both the
manual upload endpoint and the discovery import call, so these tests assert on
that function rather than on either endpoint: the two paths cannot drift apart
if there is only one implementation to drift.
"""
import hashlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database as db_mod
import services.document_indexing_service as dind
from models.rag_document import DocumentStatus, document_dedupe_scope
from tests.unit.conftest import make_result


def _settings(tmp_path):
    return SimpleNamespace(rag_document_dir=tmp_path / "rag_documents")


def _existing(doc_id=7):
    return SimpleNamespace(id=doc_id, status=DocumentStatus.COMPLETED.value)


# --------------------------------------------------------------------------- #
# The core behaviour: a duplicate leaves no trace.
# --------------------------------------------------------------------------- #

async def test_duplicate_upload_returns_existing_and_writes_nothing(mock_db, tmp_path):
    existing = _existing()
    mock_db.execute.return_value = make_result(scalar=existing)

    with patch.object(dind, "settings", _settings(tmp_path)), \
         patch.object(dind, "get_user_group_id", AsyncMock(return_value=3)):
        doc, created = await dind.save_uploaded_document(
            user_id=1, filename="paper.pdf", content=b"%PDF-1.4 same",
            db=mock_db, thread_id=None,
        )

    assert doc is existing
    assert created is False
    mock_db.add.assert_not_called()          # no second row
    mock_db.flush.assert_not_awaited()
    # No directory is even created: the check happens before any filesystem work,
    # so a duplicate cannot leave an orphaned file behind.
    assert not (tmp_path / "rag_documents").exists()


async def test_new_content_is_stored_with_its_hash(mock_db, tmp_path):
    mock_db.execute.return_value = make_result(scalar=None)
    captured = {}
    mock_db.add = MagicMock(side_effect=lambda obj: captured.setdefault("doc", obj))

    content = b"%PDF-1.4 brand new"
    with patch.object(dind, "settings", _settings(tmp_path)), \
         patch.object(dind, "get_user_group_id", AsyncMock(return_value=3)):
        doc, created = await dind.save_uploaded_document(
            user_id=1, filename="paper.pdf", content=content, db=mock_db, thread_id=None,
        )

    assert created is True
    assert doc.content_hash == hashlib.sha256(content).hexdigest()


async def test_same_filename_different_bytes_is_not_a_duplicate(mock_db, tmp_path):
    # The name is not the key -- two different papers saved as "paper.pdf" are
    # two documents, and the same paper renamed is still one.
    mock_db.execute.return_value = make_result(scalar=None)
    hashes = []
    mock_db.add = MagicMock(side_effect=lambda obj: hashes.append(obj.content_hash))

    with patch.object(dind, "settings", _settings(tmp_path)), \
         patch.object(dind, "get_user_group_id", AsyncMock(return_value=3)):
        for content in (b"%PDF-1.4 first", b"%PDF-1.4 second"):
            await dind.save_uploaded_document(
                user_id=1, filename="paper.pdf", content=content,
                db=mock_db, thread_id=None,
            )

    assert len(set(hashes)) == 2


# --------------------------------------------------------------------------- #
# The lookup query itself: what it filters on is the whole safety story.
# --------------------------------------------------------------------------- #

async def test_lookup_excludes_failed_documents(mock_db, tmp_path):
    # Deduplicating to a FAILED document would hand the user a broken document
    # AND silently consume the re-upload that was their only way to fix it.
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(dind, "settings", _settings(tmp_path)), \
         patch.object(dind, "get_user_group_id", AsyncMock(return_value=3)):
        await dind.save_uploaded_document(
            user_id=1, filename="p.pdf", content=b"%PDF-x", db=mock_db, thread_id=None,
        )

    sql = str(mock_db.execute.await_args_list[0].args[0])
    assert "content_hash" in sql
    assert "status !=" in sql


async def test_lookup_runs_before_any_filesystem_work(mock_db, tmp_path):
    """Ordering is load-bearing, not incidental.

    If the file were written first, every duplicate upload would leave a stray
    file on disk that no DB row references and nothing ever reaps.
    """
    existing = _existing()
    mock_db.execute.return_value = make_result(scalar=existing)

    with patch.object(dind, "settings", _settings(tmp_path)), \
         patch.object(dind, "get_user_group_id", AsyncMock(return_value=3)):
        await dind.save_uploaded_document(
            user_id=1, filename="p.pdf", content=b"%PDF-x", db=mock_db, thread_id=None,
        )

    assert list(tmp_path.rglob("*.pdf")) == []


# --------------------------------------------------------------------------- #
# document_dedupe_scope: the library/attachment boundary must not be crossed.
# --------------------------------------------------------------------------- #

def test_library_upload_dedupes_group_wide():
    sql = str(document_dedupe_scope(user_id=1, thread_id=None, group_id=5))
    assert "thread_id IS NULL" in sql
    assert "group_id" in sql          # one lab indexes a paper once


def test_library_upload_without_a_group_is_owner_only():
    # Fail-closed: no group resolved -> never match a stranger's document.
    sql = str(document_dedupe_scope(user_id=1, thread_id=None, group_id=None))
    assert "group_id" not in sql
    assert "user_id" in sql


def test_attachment_dedupes_only_within_its_own_thread():
    sql = str(document_dedupe_scope(user_id=1, thread_id=42, group_id=5))
    # Never widens to a group, and never matches a library document: an
    # attachment dies with its thread, so aliasing the two would make a library
    # document vanish when someone deletes a conversation.
    assert "group_id" not in sql
    assert "thread_id IS NULL" not in sql
    assert "thread_id =" in sql
    assert "user_id" in sql


# --------------------------------------------------------------------------- #
# Backfill of pre-existing rows.
# --------------------------------------------------------------------------- #

async def test_backfill_hashes_readable_files(tmp_path):
    good = tmp_path / "a.pdf"
    good.write_bytes(b"%PDF-1.4 hello")
    conn = AsyncMock()
    conn.execute.return_value = make_result(fetchall=[(1, str(good))])

    failed = await db_mod.backfill_document_hashes(conn)

    assert failed == 0
    update = conn.execute.await_args_list[-1]
    assert update.args[1]["h"] == hashlib.sha256(b"%PDF-1.4 hello").hexdigest()
    assert update.args[1]["i"] == 1


async def test_backfill_counts_unreadable_files_instead_of_swallowing_them(tmp_path):
    # A document row can outlive its file, which must not stop the app booting --
    # but it must be COUNTED. A silent backfill is how a real failure once hid
    # behind "Schema updates applied successfully" (CLAUDE.md).
    good = tmp_path / "a.pdf"
    good.write_bytes(b"data")
    conn = AsyncMock()
    conn.execute.return_value = make_result(
        fetchall=[(1, str(good)), (2, str(tmp_path / "gone.pdf"))])

    failed = await db_mod.backfill_document_hashes(conn)

    assert failed == 1
    updates = [c for c in conn.execute.await_args_list if len(c.args) > 1]
    assert len(updates) == 1          # only the readable one


async def test_backfill_is_a_noop_when_every_row_is_hashed():
    conn = AsyncMock()
    conn.execute.return_value = make_result(fetchall=[])
    assert await db_mod.backfill_document_hashes(conn) == 0
    assert conn.execute.await_count == 1     # the SELECT only, no UPDATEs


# --------------------------------------------------------------------------- #
# The endpoints must REPORT the duplicate, not silently swallow it: a skipped
# upload otherwise looks identical to a successful one and the user never learns
# why their library did not grow.
# --------------------------------------------------------------------------- #

async def test_upload_endpoint_reports_duplicate_and_skips_indexing(mock_db):
    import routers.rag as rag_r

    existing = SimpleNamespace(id=11, name="p.pdf", file_type="pdf",
                               status="completed", page_count=3,
                               created_at=datetime.now())
    fobj = AsyncMock()
    fobj.filename = "p.pdf"
    fobj.read = AsyncMock(return_value=b"%PDF-x")
    bg = MagicMock()

    with patch.object(rag_r, "_check_upload_rate_limit", AsyncMock()), \
         patch.object(rag_r, "is_supported_file", return_value=True), \
         patch.object(rag_r, "save_uploaded_document",
                      AsyncMock(return_value=(existing, False))):
        out = await rag_r.upload_document(
            bg, file=fobj, current_user=SimpleNamespace(id=7), db=mock_db)

    assert out.is_duplicate is True
    assert out.id == 11                  # points at the PRE-EXISTING document
    bg.add_task.assert_not_called()      # no second indexing run


async def test_upload_endpoint_flags_a_fresh_upload_as_not_duplicate(mock_db):
    import routers.rag as rag_r

    fresh = SimpleNamespace(id=12, name="p.pdf", file_type="pdf",
                            status="pending", page_count=0,
                            created_at=datetime.now())
    fobj = AsyncMock()
    fobj.filename = "p.pdf"
    fobj.read = AsyncMock(return_value=b"%PDF-x")
    bg = MagicMock()

    with patch.object(rag_r, "_check_upload_rate_limit", AsyncMock()), \
         patch.object(rag_r, "is_supported_file", return_value=True), \
         patch.object(rag_r, "save_uploaded_document",
                      AsyncMock(return_value=(fresh, True))):
        out = await rag_r.upload_document(
            bg, file=fobj, current_user=SimpleNamespace(id=7), db=mock_db)

    assert out.is_duplicate is False
    bg.add_task.assert_called_once()


async def test_import_reports_duplicates_separately_from_imports(mock_db):
    import routers.rag as rag_r
    import services.paper_discovery_service as pds

    paper = pds.PaperResult(
        doi="10.1/x", title="T", authors="A", journal="J", year="2026",
        abstract=None, pmid=None, pmcid=None,
        pdf_urls=["https://epmc.example/a.pdf"], source_url="https://example.org/abs")
    existing = SimpleNamespace(id=3, original_path="/data/existing.pdf",
                               doi=None, source_url=None)
    bg = MagicMock()

    with patch.object(rag_r, "_check_discovery_rate_limit", AsyncMock()), \
         patch.object(rag_r, "_resolve_paper_by_doi", AsyncMock(return_value=paper)), \
         patch.object(rag_r, "fetch_paper_pdf", AsyncMock(return_value=b"%PDF-x")), \
         patch.object(rag_r, "save_uploaded_document",
                      AsyncMock(return_value=(existing, False))):
        out = await rag_r.import_discovered(
            payload=rag_r.ImportRequest(dois=["10.1/x"]),
            background_tasks=bg,
            current_user=SimpleNamespace(id=1),
            db=mock_db,
        )

    assert out.imported == 0                        # never claim an import
    assert out.failed == []                         # ...but it is not a failure
    assert out.already_in_library == ["10.1/x"]
    bg.add_task.assert_not_called()
    # The existing document may belong to a lab mate: writes stay owner-only.
    assert existing.doi is None
    assert existing.source_url is None
