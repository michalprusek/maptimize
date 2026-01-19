"""RAG document models for document indexing and retrieval."""
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import String, Text, Float, Integer, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from ml.rag import QWEN_VL_EMBEDDING_DIM

if TYPE_CHECKING:
    from .user import User


class DocumentStatus(str, PyEnum):
    """Document processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentType(str, PyEnum):
    """Supported document types."""
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    IMAGE = "image"
    VIDEO = "video"


class RAGDocument(Base):
    """Uploaded document for RAG indexing."""

    __tablename__ = "rag_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(50))  # pdf, docx, pptx, xlsx, image, video

    # Storage paths
    original_path: Mapped[str] = mapped_column(String(500))  # Original file for PDF viewer

    # Processing status
    status: Mapped[str] = mapped_column(String(20), default="pending")
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0 to 1.0
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Metadata
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # bytes
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    indexed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="rag_documents")
    pages: Mapped[List["RAGDocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="RAGDocumentPage.page_number"
    )

    def __repr__(self) -> str:
        return f"<RAGDocument(id={self.id}, name={self.name!r}, status={self.status})>"


class RAGDocumentPage(Base):
    """Individual page from a RAG document with embedding."""

    __tablename__ = "rag_document_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("rag_documents.id", ondelete="CASCADE"),
        index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)

    # Rendered page image path
    image_path: Mapped[str] = mapped_column(String(500))

    # Vector embedding for similarity search (2048-dim for Qwen VL)
    embedding: Mapped[Optional[list]] = mapped_column(
        Vector(QWEN_VL_EMBEDDING_DIM),
        nullable=True
    )

    # Optional extracted text (for hybrid search)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    document: Mapped["RAGDocument"] = relationship(back_populates="pages")

    def __repr__(self) -> str:
        return f"<RAGDocumentPage(id={self.id}, doc_id={self.document_id}, page={self.page_number})>"
