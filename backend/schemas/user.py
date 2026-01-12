"""User schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from models.user import UserRole


class UserCreate(BaseModel):
    """Schema for user registration."""
    email: EmailStr
    name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=8)


class UserLogin(BaseModel):
    """Schema for user login."""
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """Schema for user response."""
    id: int
    email: str
    name: str
    role: UserRole
    avatar_url: Optional[str] = None
    created_at: datetime
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class TokenPayload(BaseModel):
    """JWT token payload."""
    sub: int  # user_id
    exp: datetime
    role: UserRole
