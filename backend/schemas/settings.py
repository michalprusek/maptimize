"""User settings and profile schemas."""
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from models.user_settings import DisplayMode, Language, Theme

# =============================================================================
# Settings Schemas
# =============================================================================

class UserSettingsUpdate(BaseModel):
    """Schema for updating user display settings."""
    display_mode: Optional[DisplayMode] = None
    theme: Optional[Theme] = None
    language: Optional[Language] = None


class UserSettingsResponse(BaseModel):
    """Schema for user settings response."""
    display_mode: DisplayMode
    theme: Theme
    language: Language

    model_config = {"from_attributes": True}

    @field_validator("display_mode", "theme", "language", mode="before")
    @classmethod
    def convert_string_to_enum(cls, v, info):
        """Convert string values from DB to enum."""
        if not isinstance(v, str):
            return v
        enum_map = {
            "display_mode": DisplayMode,
            "theme": Theme,
            "language": Language,
        }
        enum_class = enum_map.get(info.field_name)
        return enum_class(v) if enum_class else v


# =============================================================================
# Profile Schemas
# =============================================================================

class ProfileUpdate(BaseModel):
    """Schema for updating user profile (name, email)."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None


class PasswordChange(BaseModel):
    """Schema for changing password.

    Requires current password for security verification.
    """
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, info):
        """Validate that new_password and confirm_password match."""
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Passwords do not match")
        return v


# =============================================================================
# Avatar Schemas
# =============================================================================

class AvatarUploadResponse(BaseModel):
    """Response after successful avatar upload."""
    avatar_url: str
    message: str = "Avatar uploaded successfully"


class AvatarDeleteResponse(BaseModel):
    """Response after avatar deletion."""
    message: str = "Avatar removed successfully"
