"""Unit tests for paper discovery & import."""
from models.rag_document import RAGDocument


def test_rag_document_has_provenance_columns():
    cols = RAGDocument.__table__.columns
    assert "doi" in cols, "rag_documents needs a doi column (dedupe key)"
    assert "source_url" in cols, "rag_documents needs a source_url column"
    assert cols["doi"].nullable is True
    assert cols["source_url"].nullable is True
    assert cols["doi"].index is True, "doi is the dedupe lookup key"
