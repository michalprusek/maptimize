"""Pydantic schemas for bug reports."""
import html
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator

from models.bug_report import BugReportStatus, BugReportCategory


class BugReportCreate(BaseModel):
    """Schema for creating a bug report."""
    description: str = Field(..., min_length=10, max_length=5000)
    category: BugReportCategory = BugReportCategory.BUG

    # Auto-collected debug info from frontend
    browser_info: Optional[str] = Field(None, max_length=500)
    page_url: Optional[str] = Field(None, max_length=500)
    screen_resolution: Optional[str] = Field(None, max_length=50)
    user_settings_json: Optional[str] = Field(None, max_length=1000)

    @field_validator("description")
    @classmethod
    def sanitize_description(cls, v: str) -> str:
        """Escape HTML entities to prevent XSS attacks."""
        return html.escape(v)


class BugReportResponse(BaseModel):
    """Schema for bug report response."""
    id: int
    user_id: int
    user_name: str
    user_email: str
    description: str
    category: BugReportCategory
    status: BugReportStatus
    browser_info: Optional[str]
    page_url: Optional[str]
    screen_resolution: Optional[str]
    user_settings_json: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class BugReportListResponse(BaseModel):
    """Schema for list of bug reports."""
    reports: List[BugReportResponse]
    total: int
