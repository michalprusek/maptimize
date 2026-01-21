"""Admin panel schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field

from models.user import UserRole


class AdminUserListItem(BaseModel):
    """User item for admin list view."""
    id: int
    email: str
    name: str
    role: UserRole
    avatar_url: Optional[str] = None
    created_at: datetime
    last_login: Optional[datetime] = None
    experiment_count: int = 0
    image_count: int = 0
    storage_bytes: int = 0

    class Config:
        from_attributes = True


class AdminUserDetail(BaseModel):
    """Detailed user info for admin view."""
    id: int
    email: str
    name: str
    role: UserRole
    avatar_url: Optional[str] = None
    created_at: datetime
    last_login: Optional[datetime] = None

    # Counts
    experiment_count: int = 0
    image_count: int = 0
    document_count: int = 0
    chat_thread_count: int = 0

    # Storage breakdown
    images_storage_bytes: int = 0
    documents_storage_bytes: int = 0
    total_storage_bytes: int = 0

    class Config:
        from_attributes = True


class AdminUserUpdate(BaseModel):
    """Schema for updating user as admin."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    role: Optional[UserRole] = None


class AdminPasswordResetResponse(BaseModel):
    """Response after password reset."""
    new_password: str
    message: str = "Password has been reset successfully"


class AdminSystemStats(BaseModel):
    """System-wide statistics."""
    total_users: int
    total_experiments: int
    total_images: int
    total_documents: int
    total_storage_bytes: int

    # Role breakdown
    admin_count: int
    researcher_count: int
    viewer_count: int

    # Storage breakdown
    images_storage_bytes: int
    documents_storage_bytes: int


class AdminTimelinePoint(BaseModel):
    """Single data point for timeline charts."""
    date: str  # ISO date string (YYYY-MM-DD)
    registrations: int = 0
    active_users: int = 0


class AdminTimelineStats(BaseModel):
    """Timeline statistics for charts."""
    data: List[AdminTimelinePoint]
    period_days: int = 30


class AdminChatThread(BaseModel):
    """Chat thread info for admin view."""
    id: int
    name: str
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AdminChatMessage(BaseModel):
    """Chat message for admin view."""
    id: int
    role: str
    content: str
    created_at: datetime
    # Exclude sensitive tool_calls to prevent data leakage
    has_citations: bool = False
    has_images: bool = False

    class Config:
        from_attributes = True


class AdminExperiment(BaseModel):
    """Experiment info for admin view."""
    id: int
    name: str
    description: Optional[str] = None
    status: str
    image_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AdminUserListResponse(BaseModel):
    """Paginated user list response."""
    users: List[AdminUserListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminChatThreadListResponse(BaseModel):
    """Chat threads response."""
    threads: List[AdminChatThread]
    total: int


class AdminChatMessagesResponse(BaseModel):
    """Chat messages response."""
    messages: List[AdminChatMessage]
    thread_name: str
    total: int


class AdminExperimentsResponse(BaseModel):
    """Experiments response."""
    experiments: List[AdminExperiment]
    total: int
