"""Unit tests for group-shared RAG document access control."""
from models.rag_document import RAGDocument, document_scope, document_read_scope


def test_rag_document_has_group_id_column():
    col = RAGDocument.__table__.columns.get("group_id")
    assert col is not None, "rag_documents needs a group_id column"
    assert col.nullable is True
    # FK targets groups.id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "groups"


def _sql(clause):
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_document_scope_library_widens_to_group():
    sql = _sql(document_scope(user_id=1, thread_id=None, group_id=7))
    assert "rag_documents.group_id" in sql          # group OR present
    assert "rag_documents.user_id" in sql
    assert "rag_documents.thread_id IS NULL" in sql  # still library-only


def test_document_scope_owner_only_without_group():
    sql = _sql(document_scope(user_id=1, thread_id=None, group_id=None))
    assert "group_id" not in sql                     # fail-closed to owner


def test_document_scope_thread_group_shares_library_not_attachments():
    # thread context: library shared to group, but the group term must be gated
    # by thread_id IS NULL so another member's attachment can never appear.
    sql = _sql(document_scope(user_id=1, thread_id=5, group_id=7))
    assert "rag_documents.group_id" in sql
    assert "rag_documents.thread_id = 5" in sql      # own attachments still visible
    # Lock the STRUCTURE, not just substring presence: the group term must be
    # AND-gated by thread_id IS NULL, adjacent and parenthesized exactly like
    # this. If `and_` here were ever swapped for `or_`, every group member
    # would see every thread's attachments -- a severe cross-user leak -- yet
    # the substring checks above would still pass. This adjacency assertion
    # would catch it.
    assert (
        "rag_documents.thread_id IS NULL AND "
        "(rag_documents.user_id = 1 OR rag_documents.group_id = 7)"
    ) in sql


def test_document_scope_thread_owner_only_without_group():
    # Fail-closed check in a thread context: no group_id at all -> no group
    # widening, even though a thread_id is present.
    sql = _sql(document_scope(user_id=1, thread_id=5, group_id=None))
    assert "group_id" not in sql


def test_document_read_scope_group_shares_library_only():
    sql = _sql(document_read_scope(user_id=1, group_id=7))
    assert "rag_documents.user_id" in sql            # owner sees own (incl. attachments)
    assert "rag_documents.group_id" in sql           # + group-shared library
    assert "rag_documents.thread_id IS NULL" in sql  # group term gated to library
    # Same structural lock as above: the group clause must be AND-gated by
    # thread_id IS NULL, not merely present somewhere in the compiled SQL.
    assert (
        "rag_documents.thread_id IS NULL AND rag_documents.group_id = 7"
    ) in sql


def test_document_read_scope_owner_only_without_group():
    sql = _sql(document_read_scope(user_id=1, group_id=None))
    assert "group_id" not in sql


from unittest.mock import AsyncMock, patch
import services.document_indexing_service as dis


async def test_library_upload_is_stamped_with_group(mock_db, tmp_path):
    with patch.object(dis, "get_user_group_id", AsyncMock(return_value=7)), \
         patch.object(dis.settings, "rag_document_dir", tmp_path):
        doc = await dis.save_uploaded_document(
            user_id=1, filename="paper.pdf", content=b"%PDF-1.4",
            db=mock_db, thread_id=None,
        )
    assert doc.group_id == 7


async def test_attachment_upload_is_not_stamped(mock_db, tmp_path):
    with patch.object(dis, "get_user_group_id", AsyncMock(return_value=7)), \
         patch.object(dis.settings, "rag_document_dir", tmp_path):
        doc = await dis.save_uploaded_document(
            user_id=1, filename="paper.pdf", content=b"%PDF-1.4",
            db=mock_db, thread_id=99,
        )
    assert doc.group_id is None


import utils.groups as groups_util
from tests.unit.conftest import make_result


async def test_adopt_orphan_documents_only_touches_library(mock_db):
    mock_db.execute = AsyncMock(return_value=make_result(rowcount=3))
    n = await groups_util.adopt_orphan_documents(mock_db, user_id=1, group_id=7)
    assert n == 3
    # the UPDATE must be gated to library docs (thread_id IS NULL) and orphans
    stmt = mock_db.execute.call_args.args[0]
    sql = str(stmt).lower()
    assert "thread_id is null" in sql
    assert "group_id is null" in sql


import services.rag_service as rag_service


async def test_search_documents_widens_precheck_to_group(mock_db):
    # No own indexed pages, but group has some -> pre-check must still find them.
    # We assert the pre-check SQL and its params include the group term.
    calls = []

    async def fake_execute(stmt, params=None):
        calls.append((str(stmt), params or {}))
        return make_result(first=None)  # pre-check returns "nothing" -> early []

    mock_db.execute = fake_execute
    out = await rag_service.search_documents(
        query="x", user_id=1, db=mock_db, thread_id=None, group_id=7,
    )
    assert out == []
    precheck_sql, precheck_params = calls[0]
    assert "group_id" in precheck_sql.lower()
    assert precheck_params.get("group_id") == 7


from sqlalchemy import select as _select


async def test_get_document_content_uses_read_scope(mock_db):
    captured = {}

    async def fake_execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return make_result(scalar=None)  # not found -> returns None, fine

    mock_db.execute = fake_execute
    out = await rag_service.get_document_content(
        document_id=5, user_id=1, db=mock_db, group_id=7,
    )
    assert out is None
    assert "rag_documents.group_id" in captured["sql"]  # group widening applied


import json as _json
import types as _pytypes
from types import SimpleNamespace
from unittest.mock import MagicMock


def _patch_genai_module(fake_client):
    """Inject a fake ``google.genai`` module so the lazy
    ``import google.genai as genai`` inside extract_relevant_passages resolves
    to our stub instead of hitting the real (network) client."""
    genai_mod = _pytypes.ModuleType("google.genai")
    genai_mod.Client = MagicMock(return_value=fake_client)
    types_mod = _pytypes.ModuleType("google.genai.types")
    types_mod.Content = MagicMock()
    types_mod.Part = MagicMock()
    types_mod.Blob = MagicMock()
    types_mod.GenerateContentConfig = MagicMock()
    genai_mod.types = types_mod
    google_pkg = _pytypes.ModuleType("google")
    google_pkg.genai = genai_mod
    return patch.dict("sys.modules", {
        "google": google_pkg,
        "google.genai": genai_mod,
        "google.genai.types": types_mod,
    })


async def test_extract_relevant_passages_forwards_group_id(mock_db, tmp_path):
    # Regression test: extract_relevant_passages resolves the source document via
    # _get_document_page_image_path(..., group_id=group_id) -- widened, so a
    # group-shared (non-owned) doc is found -- but then looped over Gemini's
    # detected regions calling extract_passage_image(...) per region. If that
    # per-region call drops group_id, extract_passage_image's OWN ownership
    # recheck defaults to group_id=None (owner-only) and rejects every crop of a
    # document the caller doesn't own, silently returning []. Assert the forward
    # actually happens.
    page_image = tmp_path / "page.png"
    page_image.write_bytes(b"not-a-real-png-just-needs-bytes")

    fake_document = SimpleNamespace(id=5, name="shared.pdf")

    gemini_payload = _json.dumps([
        {"box_2d": [0, 0, 100, 100], "type": "text", "text": "x", "confidence": 0.9},
    ])
    resp = SimpleNamespace(text=gemini_payload)
    aio = SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(return_value=resp)))
    fake_client = SimpleNamespace(aio=aio)

    fake_passage = {"passage_hash": "h", "document_id": 5, "page_number": 1}
    fake_settings = SimpleNamespace(gemini_api_key="fake-key", gemini_vision_model="gemini-3.5-flash")

    with patch.object(rag_service, "settings", fake_settings), \
         patch.object(rag_service, "_get_document_page_image_path",
                       AsyncMock(return_value=(fake_document, page_image))), \
         patch.object(rag_service, "extract_passage_image",
                       AsyncMock(return_value=fake_passage)) as mock_extract, \
         _patch_genai_module(fake_client):
        out = await rag_service.extract_relevant_passages(
            document_id=5, page_number=1, query="figure",
            user_id=1, db=mock_db, group_id=7,
        )

    assert len(out) == 1
    mock_extract.assert_awaited_once()
    # The bug: this call omitted group_id entirely, so extract_passage_image
    # fell back to its own default (None) -> owner-only ownership recheck.
    assert mock_extract.await_args.kwargs.get("group_id") == 7


import routers.rag as rag_router


async def test_get_document_for_user_widens_to_group(mock_db):
    captured = {}

    async def fake_execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return make_result(scalar=object())  # found -> returns the doc

    mock_db.execute = fake_execute
    await rag_router.get_document_for_user(mock_db, document_id=5, user_id=1, group_id=7)
    assert "rag_documents.group_id" in captured["sql"]


from services.gemini_agent_service import _inject_user_id_filter


def test_inject_filter_widens_rag_documents_to_group():
    out = _inject_user_id_filter(
        "SELECT * FROM rag_documents", "rag_documents", group_id=7
    )
    assert "rag_documents.user_id = :user_id" in out
    assert "rag_documents.group_id = :group_id" in out


def test_inject_filter_rag_documents_owner_only_without_group():
    out = _inject_user_id_filter("SELECT * FROM rag_documents", "rag_documents")
    assert "group_id" not in out
