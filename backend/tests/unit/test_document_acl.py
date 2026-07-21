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
