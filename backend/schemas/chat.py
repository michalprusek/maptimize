"""Chat and RAG schemas."""
from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, Field


# ============== Chat Thread Schemas ==============

class ChatThreadCreate(BaseModel):
    """Schema for creating a chat thread."""
    name: Optional[str] = Field(None, max_length=255)


class ChatThreadUpdate(BaseModel):
    """Schema for updating a chat thread."""
    name: str = Field(..., min_length=1, max_length=255)


class ChatThreadResponse(BaseModel):
    """Schema for chat thread response."""
    id: int
    name: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    last_message_preview: Optional[str] = None

    class Config:
        from_attributes = True


# ============== Chat Message Schemas ==============

class Citation(BaseModel):
    """Citation reference in a message."""
    type: str  # "document", "fov", or "web"
    doc_id: Optional[int] = None
    page: Optional[int] = None
    image_id: Optional[int] = None
    title: Optional[str] = None
    url: Optional[str] = None  # For web citations
    confidence: Optional[float] = None  # Relevance score when available


class ImageRef(BaseModel):
    """Image reference in a message."""
    path: str
    caption: Optional[str] = None


class ToolCall(BaseModel):
    """Tool call made by the assistant."""
    tool: str
    args: dict = {}
    result: Optional[Any] = None


class ChatMessageCreate(BaseModel):
    """Schema for creating a chat message."""
    content: str = Field(..., min_length=1, max_length=10000)


class ChatMessageEdit(BaseModel):
    """Schema for editing a chat message."""
    content: str = Field(..., min_length=1, max_length=10000)


class ChatMessageResponse(BaseModel):
    """Schema for chat message response."""
    id: int
    thread_id: int
    role: str  # "user" or "assistant"
    content: str
    citations: List[Citation] = []
    image_refs: List[ImageRef] = []
    tool_calls: List[ToolCall] = []
    interaction_id: Optional[str] = None  # Gemini Interactions API ID (for assistant messages)
    created_at: datetime

    class Config:
        from_attributes = True


class ChatThreadDetailResponse(ChatThreadResponse):
    """Schema for detailed chat thread response with messages."""
    messages: List[ChatMessageResponse] = []


# ============== Generation Status Schemas ==============

class GenerationStatusResponse(BaseModel):
    """Status of AI response generation for a thread."""
    thread_id: int
    status: str  # idle, generating, completed, cancelled, error
    task_id: Optional[str] = None
    started_at: Optional[datetime] = None
    elapsed_seconds: Optional[int] = None
    error: Optional[str] = None
    # If completed, contains the new message
    message: Optional[ChatMessageResponse] = None


class SendMessageResponse(BaseModel):
    """Response after sending a message (async generation)."""
    user_message: ChatMessageResponse
    generation_status: str  # "generating" or "completed"
    task_id: Optional[str] = None


# ============== RAG Document Schemas ==============

class RAGDocumentUploadResponse(BaseModel):
    """Response after uploading a document."""
    id: int
    name: str
    file_type: str
    status: str
    page_count: int
    created_at: datetime
    # True when the upload was recognised as a copy of a document already
    # visible to the uploader: nothing was stored, nothing was indexed, and the
    # fields above describe the PRE-EXISTING document. The UI must say so
    # rather than showing this as a fresh upload.
    is_duplicate: bool = False

    class Config:
        from_attributes = True


class RAGDocumentResponse(BaseModel):
    """Schema for RAG document response."""
    id: int
    name: str
    file_type: str
    status: str
    progress: float
    page_count: int
    error_message: Optional[str] = None
    file_size: Optional[int] = None
    created_at: datetime
    indexed_at: Optional[datetime] = None
    # Default True is required for model_validate() to work (the ORM object has
    # no is_owner attribute); for_user() below is the only correct way to set it.
    # UI-only flag (hides delete/reindex controls) -- mutation endpoints
    # re-check ownership server-side regardless of this value.
    is_owner: bool = True

    class Config:
        from_attributes = True

    @classmethod
    def for_user(cls, doc, current_user_id: int) -> "RAGDocumentResponse":
        """Build a response with ``is_owner`` correctly set for the caller.

        The single correct way to construct this schema -- avoids hand-rolled
        ``model_validate`` + manual ``is_owner`` assignment at each call site.
        """
        resp = cls.model_validate(doc)
        resp.is_owner = doc.user_id == current_user_id
        return resp


class RAGDocumentPageResponse(BaseModel):
    """Schema for RAG document page response."""
    id: int
    document_id: int
    page_number: int
    image_path: str
    has_embedding: bool = False

    class Config:
        from_attributes = True


class RAGIndexingStatusResponse(BaseModel):
    """Global RAG indexing status."""
    documents_pending: int
    documents_processing: int
    documents_completed: int
    documents_failed: int
    fov_images_pending: int
    fov_images_indexed: int


# ============== Search Result Schemas ==============

class DocumentSearchResult(BaseModel):
    """Search result from document pages."""
    document_id: int
    document_name: str
    page_number: int
    image_path: str
    score: float


class FOVSearchResult(BaseModel):
    """Search result from FOV images."""
    image_id: int
    experiment_id: int
    experiment_name: str
    original_filename: str
    thumbnail_path: Optional[str]
    score: float


class RAGSearchResponse(BaseModel):
    """Combined RAG search results."""
    documents: List[DocumentSearchResult] = []
    fov_images: List[FOVSearchResult] = []
    query: str


# ============== Paper Discovery Schemas ==============

class DiscoverRequest(BaseModel):
    """Request body for POST /discover.

    ``max_length`` bounds the classify_query() fan-out at the door: without it
    a pasted 300-entry bibliography turns into hundreds of sub-queries (see
    paper_discovery_service.discover's own MAX_SUBQUERIES cap for the second
    layer of defense).
    """
    query: str = Field(..., max_length=4000)


class DiscoveredPaper(BaseModel):
    """One candidate paper in the discovery picker."""
    doi: Optional[str] = None
    title: str
    authors: Optional[str] = None
    journal: Optional[str] = None
    year: Optional[str] = None
    abstract: Optional[str] = None
    source_url: str
    # True only when Europe PMC advertises a downloadable PDF for this record.
    importable: bool
    # Set when the same DOI is already in the caller's library.
    already_imported: bool = False


class DiscoverResponse(BaseModel):
    query: str
    results: List[DiscoveredPaper]
    # Sub-queries that errored (e.g. a transient Europe PMC timeout) but didn't
    # sink the whole search -- some results may still be missing.
    failed_queries: int = 0
    # Sub-queries never run at all because the request was capped (see
    # DiscoverRequest / MAX_SUBQUERIES) -- distinct from failed_queries.
    dropped_queries: int = 0
    # The query Europe PMC actually ran, set ONLY when the LLM rewrite of a
    # free-text topic search both succeeded AND changed the query -- None for
    # doi/titles searches (never rewritten), when the rewrite was
    # unavailable/failed (raw text used), and when the rewrite's results were
    # discarded in favour of a raw-text fallback (see rewrite_failed). Lets
    # the UI show "Searched as: ..." only when that's actually meaningful.
    effective_query: Optional[str] = None
    # True when a topic-search rewrite was attempted but the smart translation
    # is NOT what's behind the results shown -- either it produced nothing
    # usable (raw text searched instead) or it produced a query that came back
    # with zero results, so the raw text was retried once. False for
    # doi/titles searches and for a query that already used Europe PMC field
    # syntax (rewrite skipped, not "failed"). Lets the UI explain why an
    # author/lab search came back with plain-keyword-quality results.
    rewrite_failed: bool = False


class ImportRequest(BaseModel):
    """Request body for POST /discover/import."""
    dois: List[str]


class ImportFailure(BaseModel):
    doi: str
    reason: str


class ImportResponse(BaseModel):
    imported: int
    failed: List[ImportFailure]
    # DOIs already present, found either by the DOI pre-check (nothing was
    # fetched) or by the content hash after downloading (the PDF WAS fetched --
    # byte-identity cannot be known before the bytes exist). Either way nothing
    # was stored or indexed. "Present" means visible to the caller, which for a
    # library document includes a lab mate's copy. Neither a success nor a
    # failure -- reported separately so the summary can't claim an import that
    # never happened, nor call a duplicate an error.
    already_in_library: List[str] = []
