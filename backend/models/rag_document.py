"""RAG document models for document indexing and retrieval."""
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    String, Text, Float, Integer, ForeignKey, DateTime, func,
    CheckConstraint, UniqueConstraint, and_, or_,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.elements import ColumnElement

from database import Base
from ml.rag import QWEN_VL_EMBEDDING_DIM

if TYPE_CHECKING:
    from .user import User


def _library_visible(user_id: int, group_id: Optional[int]) -> ColumnElement:
    """Who may see a LIBRARY document (thread_id IS NULL): owner, or -- when the
    caller is in a group -- any member of that group. group_id=None -> owner only."""
    if group_id is None:
        return RAGDocument.user_id == user_id
    return or_(RAGDocument.user_id == user_id, RAGDocument.group_id == group_id)


def document_scope(
    user_id: int,
    thread_id: Optional[int] = None,
    group_id: Optional[int] = None,
) -> ColumnElement:
    """SSOT for which documents a caller may see in a LISTING or SEARCH.

    Library documents (thread_id IS NULL) are shared group-wide; chat attachments
    belong to their thread and stay owner-private, so they never widen to a group.

    ``thread_id=None`` -> the shared library only.
    ``thread_id=N``    -> the shared library PLUS the caller's OWN attachments in N.
    ``group_id=None``  -> owner-only (fail-closed).

    Every listing/search query that scopes RAGDocument goes through this. Mirrors
    the ``experiment_owner_filter`` pattern in utils/groups.py.
    """
    library = and_(RAGDocument.thread_id.is_(None), _library_visible(user_id, group_id))
    if thread_id is None:
        return library
    own_attachment = and_(
        RAGDocument.user_id == user_id,
        RAGDocument.thread_id == thread_id,
    )
    return or_(library, own_attachment)


def document_read_scope(user_id: int, group_id: Optional[int] = None) -> ColumnElement:
    """SSOT for a single-document FETCH BY ID (serve pdf/pages, read content,
    extract region, cached passage).

    The owner may fetch any of their own documents -- including their own chat
    attachments, needed to serve attachment pages in the viewer. A group member
    may additionally fetch a group-shared LIBRARY document. ``group_id=None`` ->
    owner-only (fail-closed). Writes must NOT use this -- they stay owner-only.
    """
    owner = RAGDocument.user_id == user_id
    if group_id is None:
        return owner
    shared_library = and_(
        RAGDocument.thread_id.is_(None),
        RAGDocument.group_id == group_id,
    )
    return or_(owner, shared_library)


def document_dedupe_scope(
    user_id: int,
    thread_id: Optional[int],
    group_id: Optional[int],
) -> ColumnElement:
    """SSOT for which documents a new upload may be recognised as a duplicate OF.

    Deliberately NARROWER than document_scope. For a library upload the two are
    identical (that branch is library-only either way), but
    ``document_scope(user_id, thread_id=N, ...)`` returns *library OR own
    attachments in N*. Using it here would let an ATTACHMENT upload alias onto a
    group-shared library document: the thread would then reference a row its
    user does not own -- cannot delete, cannot reindex -- and which disappears
    entirely if the owner removes it. The reverse cannot happen, since a library
    upload never sees attachment rows under either function.

    Library uploads dedupe group-wide, so one lab indexes a paper once. Chat
    attachments dedupe only against the caller's own attachments in the SAME
    thread, and never widen to a group. ``group_id=None`` -> owner only.

    Callers must ALSO exclude ``status == FAILED``; that half of the eligibility
    rule lives at the call site in ``save_uploaded_document``, not here, because
    it is about the document's usefulness rather than its visibility.
    """
    if thread_id is None:
        return and_(RAGDocument.thread_id.is_(None), _library_visible(user_id, group_id))
    return and_(RAGDocument.user_id == user_id, RAGDocument.thread_id == thread_id)


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
    OFFICE = "office"


class RAGDocument(Base):
    """Uploaded document for RAG indexing."""

    __tablename__ = "rag_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True
    )
    # NULL = document library upload; set = attachment scoped to a (now removed)
    # chat thread. Read scope goes through document_scope() above -- never a bare
    # user_id filter. The chat agent and its chat_threads table were removed, so
    # this is a plain nullable column now: no live FK is declared (the referenced
    # table is gone from the ORM metadata, which would break create_all). Any
    # ON DELETE CASCADE still enforced by the real database is left to deployment.
    thread_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True, index=True,
    )
    # NULL = not shared; set = readable by every member of this group (library
    # uploads only). Stamped at creation for thread_id IS NULL docs; attachments
    # keep it NULL. Mirrors Experiment.group_id. ON DELETE SET NULL so deleting a
    # group orphans the doc back to owner-only rather than deleting it.
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # Provenance for documents imported from Europe PMC (NULL for manual uploads).
    # doi is indexed because it is the dedupe key: a paper already in the library
    # must be shown as such instead of being imported twice.
    doi: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    # sha256 of the uploaded bytes -- the deduplication key, checked before a
    # file is written or a row created. NULL for rows predating the column and
    # for any row whose file could not be read during backfill; NULL != NULL in
    # SQL, so those never match each other by accident.
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    # Store file_type as string but validate against DocumentType values
    file_type: Mapped[str] = mapped_column(String(50))

    # Storage paths
    original_path: Mapped[str] = mapped_column(String(500))  # Original file for PDF viewer

    # Processing status - stored as string, validated against DocumentStatus
    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.PENDING.value)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    # Set only when a chat attachment hit the page cap: the true length of the
    # source PDF, so the agent can say it only saw part of the document.
    truncated_from_pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Progress must be between 0.0 and 1.0
        CheckConstraint('progress >= 0.0 AND progress <= 1.0', name='check_progress_range'),
        # Page count must be non-negative
        CheckConstraint('page_count >= 0', name='check_page_count_non_negative'),
        # Status must be one of the allowed values
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name='check_status_valid'
        ),
    )

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

    __table_args__ = (
        # Each page number must be unique within a document
        UniqueConstraint('document_id', 'page_number', name='uq_document_page_number'),
        # Page numbers must be positive (1-indexed)
        CheckConstraint('page_number > 0', name='check_page_number_positive'),
    )

    # Relationships
    document: Mapped["RAGDocument"] = relationship(back_populates="pages")

    def __repr__(self) -> str:
        return f"<RAGDocumentPage(id={self.id}, doc_id={self.document_id}, page={self.page_number})>"
