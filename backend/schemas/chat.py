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

    class Config:
        from_attributes = True


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
