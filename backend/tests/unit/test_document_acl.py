"""Unit tests for group-shared RAG document access control."""
from models.rag_document import RAGDocument


def test_rag_document_has_group_id_column():
    col = RAGDocument.__table__.columns.get("group_id")
    assert col is not None, "rag_documents needs a group_id column"
    assert col.nullable is True
    # FK targets groups.id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "groups"


from models.rag_document import document_scope, document_read_scope


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


def test_document_read_scope_group_shares_library_only():
    sql = _sql(document_read_scope(user_id=1, group_id=7))
    assert "rag_documents.user_id" in sql            # owner sees own (incl. attachments)
    assert "rag_documents.group_id" in sql           # + group-shared library
    assert "rag_documents.thread_id IS NULL" in sql  # group term gated to library


def test_document_read_scope_owner_only_without_group():
    sql = _sql(document_read_scope(user_id=1, group_id=None))
    assert "group_id" not in sql
