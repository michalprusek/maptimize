"""Admin panel schemas."""
from datetime import datetime
from math import ceil
from typing import Optional, List

from pydantic import BaseModel, Field, model_validator

from models.user import UserRole


class AdminUserListItem(BaseModel):
    """User item for admin list view."""
    id: int = Field(gt=0)
    email: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: UserRole
    avatar_url: Optional[str] = None
    created_at: datetime
    last_login: Optional[datetime] = None
    experiment_count: int = Field(ge=0, default=0)
    image_count: int = Field(ge=0, default=0)
    storage_bytes: int = Field(ge=0, default=0)

    class Config:
        from_attributes = True


class AdminUserDetail(BaseModel):
    """Detailed user info for admin view."""
    id: int = Field(gt=0)
    email: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: UserRole
    avatar_url: Optional[str] = None
    created_at: datetime
    last_login: Optional[datetime] = None

    # Counts
    experiment_count: int = Field(ge=0, default=0)
    image_count: int = Field(ge=0, default=0)
    document_count: int = Field(ge=0, default=0)
    chat_thread_count: int = Field(ge=0, default=0)

    # Storage breakdown
    images_storage_bytes: int = Field(ge=0, default=0)
    documents_storage_bytes: int = Field(ge=0, default=0)
    total_storage_bytes: int = Field(ge=0, default=0)

    class Config:
        from_attributes = True


class AdminUserUpdate(BaseModel):
    """Schema for updating user as admin."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    role: Optional[UserRole] = None

    @model_validator(mode="after")
    def check_at_least_one_field(self):
        """Ensure at least one field is provided for update."""
        if self.name is None and self.role is None:
            raise ValueError("At least one field (name or role) must be provided for update")
        return self


class AdminPasswordResetResponse(BaseModel):
    """Response after password reset."""
    new_password: str = Field(min_length=1)
    message: str = "Password has been reset successfully"


class AdminSystemStats(BaseModel):
    """System-wide statistics."""
    total_users: int = Field(ge=0)
    total_experiments: int = Field(ge=0)
    total_images: int = Field(ge=0)
    total_documents: int = Field(ge=0)
    total_storage_bytes: int = Field(ge=0)

    # Role breakdown
    admin_count: int = Field(ge=0)
    researcher_count: int = Field(ge=0)
    viewer_count: int = Field(ge=0)

    # Storage breakdown
    images_storage_bytes: int = Field(ge=0)
    documents_storage_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_totals(self):
        """Validate that breakdowns sum to totals."""
        # Validate role counts sum to total users
        role_sum = self.admin_count + self.researcher_count + self.viewer_count
        if role_sum != self.total_users:
            raise ValueError(
                f"Role counts ({role_sum}) do not match total_users ({self.total_users})"
            )

        # Validate storage breakdown sums to total
        storage_sum = self.images_storage_bytes + self.documents_storage_bytes
        if storage_sum != self.total_storage_bytes:
            raise ValueError(
                f"Storage breakdown ({storage_sum}) does not match total_storage_bytes ({self.total_storage_bytes})"
            )

        return self


class AdminTimelinePoint(BaseModel):
    """Single data point for timeline charts."""
    date: str  # ISO date string (YYYY-MM-DD)
    registrations: int = Field(ge=0, default=0)
    active_users: int = Field(ge=0, default=0)


class AdminTimelineStats(BaseModel):
    """Timeline statistics for charts."""
    data: List[AdminTimelinePoint]
    period_days: int = Field(ge=7, le=90, default=30)


class AdminChatThread(BaseModel):
    """Chat thread info for admin view."""
    id: int = Field(gt=0)
    name: str
    message_count: int = Field(ge=0, default=0)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AdminChatMessage(BaseModel):
    """Chat message for admin view."""
    id: int = Field(gt=0)
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
    id: int = Field(gt=0)
    name: str
    description: Optional[str] = None
    status: str
    image_count: int = Field(ge=0, default=0)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AdminUserListResponse(BaseModel):
    """Paginated user list response."""
    users: List[AdminUserListItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_pagination(self):
        """Validate pagination math consistency."""
        # Calculate expected total_pages
        expected_pages = ceil(self.total / self.page_size) if self.total > 0 else 0
        if self.total_pages != expected_pages:
            raise ValueError(
                f"total_pages ({self.total_pages}) does not match calculated value ({expected_pages})"
            )

        # Validate current page is within range (allow page 1 even when no results)
        if self.total_pages > 0 and self.page > self.total_pages:
            raise ValueError(f"page ({self.page}) exceeds total_pages ({self.total_pages})")

        # Validate users list size
        if len(self.users) > self.page_size:
            raise ValueError(
                f"users list length ({len(self.users)}) exceeds page_size ({self.page_size})"
            )

        return self


class AdminChatThreadListResponse(BaseModel):
    """Chat threads response."""
    threads: List[AdminChatThread]
    total: int = Field(ge=0)


class AdminChatMessagesResponse(BaseModel):
    """Chat messages response."""
    messages: List[AdminChatMessage]
    thread_name: str
    total: int = Field(ge=0)


class AdminExperimentsResponse(BaseModel):
    """Experiments response."""
    experiments: List[AdminExperiment]
    total: int = Field(ge=0)
