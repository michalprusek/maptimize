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
