"""Group schemas."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    """Schema for creating a group."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class GroupUpdate(BaseModel):
    """Schema for updating a group."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None


class GroupMemberResponse(BaseModel):
    """Schema for a group member."""
    id: int
    user_id: int
    user_name: str
    user_email: str
    role: str
    joined_at: datetime

    class Config:
        from_attributes = True


class GroupResponse(BaseModel):
    """Schema for group response."""
    id: int
    name: str
    description: Optional[str] = None
    created_by_user_id: int
    creator_name: str
    member_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class GroupDetailResponse(GroupResponse):
    """Schema for group detail with members list."""
    members: List[GroupMemberResponse] = []


class GroupListResponse(BaseModel):
    """List of groups."""
    items: List[GroupResponse]
    total: int


class MyGroupResponse(BaseModel):
    """Schema for current user's group (or null)."""
    group: Optional[GroupDetailResponse] = None
    role: Optional[str] = None
