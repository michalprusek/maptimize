"""Schemas for the group join-request flow."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class JoinRequestCreate(BaseModel):
    """Body of a request to join a group."""
    message: Optional[str] = Field(None, max_length=1000)


class JoinRequestResponse(BaseModel):
    """A join request, as shown to the requester and to the group's admins.

    Carries the group and user names so neither side has to make a second call to
    render a queue -- an admin looking at "user 22 wants into group 2" learns
    nothing.
    """
    id: int
    group_id: int
    group_name: str
    user_id: int
    user_name: str
    user_email: str
    status: str
    message: Optional[str] = None
    created_at: datetime
    decided_at: Optional[datetime] = None
    decided_by_user_id: Optional[int] = None

    class Config:
        from_attributes = True


class JoinRequestListResponse(BaseModel):
    items: List[JoinRequestResponse]
    total: int
