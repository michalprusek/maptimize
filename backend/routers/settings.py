"""User settings and profile routes."""
import logging
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models.user import User
from models.user_settings import DisplayMode, Language, Theme, UserSettings
from schemas.settings import (
    AvatarDeleteResponse,
    AvatarUploadResponse,
    PasswordChange,
    ProfileUpdate,
    UserSettingsResponse,
    UserSettingsUpdate,
)
from schemas.user import UserResponse
from utils.security import get_current_user, hash_password, verify_password

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

# Allowed avatar file extensions and content types
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_AVATAR_SIZE = 5 * 1024 * 1024  # 5MB
AVATAR_SIZE = (256, 256)  # Resize avatars to this size


def get_avatar_dir(user_id: int) -> Path:
    """Get the avatar directory for a user."""
    avatar_dir = settings.upload_dir / "avatars" / str(user_id)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    return avatar_dir


async def get_or_create_settings(db: AsyncSession, user: User) -> UserSettings:
    """Get existing settings or create default settings for a user."""
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    user_settings = result.scalar_one_or_none()

    if not user_settings:
        user_settings = UserSettings(
            user_id=user.id,
            display_mode=DisplayMode.GRAYSCALE.value,
            theme=Theme.DARK.value,
            language=Language.EN.value,
        )
        db.add(user_settings)
        await db.commit()
        await db.refresh(user_settings)

    return user_settings


# =============================================================================
# Settings Endpoints
# =============================================================================

@router.get("", response_model=UserSettingsResponse)
async def get_settings_endpoint(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current user's display settings."""
    user_settings = await get_or_create_settings(db, current_user)
    return UserSettingsResponse.model_validate(user_settings)


@router.patch("", response_model=UserSettingsResponse)
async def update_settings(
    updates: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update user display settings (display_mode, theme, language)."""
    user_settings = await get_or_create_settings(db, current_user)

    # Apply updates
    if updates.display_mode is not None:
        user_settings.display_mode = updates.display_mode.value
    if updates.theme is not None:
        user_settings.theme = updates.theme.value
    if updates.language is not None:
        user_settings.language = updates.language.value

    await db.commit()
    await db.refresh(user_settings)

    logger.info(f"User {current_user.id} updated settings: {updates.model_dump(exclude_none=True)}")
    return UserSettingsResponse.model_validate(user_settings)


# =============================================================================
# Profile Endpoints
# =============================================================================

@router.patch("/profile", response_model=UserResponse)
async def update_profile(
    updates: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update user profile (name, email)."""
    # Check email uniqueness if changing email
    if updates.email and updates.email != current_user.email:
        result = await db.execute(select(User).where(User.email == updates.email))
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
        current_user.email = updates.email

    if updates.name:
        current_user.name = updates.name

    await db.commit()
    await db.refresh(current_user)

    logger.info(f"User {current_user.id} updated profile")
    return UserResponse.model_validate(current_user)


@router.post("/password")
async def change_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Change user password. Requires current password verification."""
    # Verify current password
    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    # Update password
    current_user.password_hash = hash_password(password_data.new_password)
    await db.commit()

    logger.info(f"User {current_user.id} changed password")
    return {"message": "Password changed successfully"}


# =============================================================================
# Avatar Endpoints
# =============================================================================

@router.get("/avatar")
async def get_avatar(
    current_user: User = Depends(get_current_user),
):
    """Get current user's avatar URL.

    Returns the avatar URL if set, otherwise returns 404.
    This endpoint handles stray GET requests to /avatar gracefully.
    """
    if not current_user.avatar_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No avatar set"
        )
    return {"avatar_url": current_user.avatar_url}


@router.post("/avatar", response_model=AvatarUploadResponse)
async def upload_avatar(
    request: Request,
    file: UploadFile = File(..., description="Avatar image file (required)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload or replace user avatar.

    Accepts JPG, PNG, GIF, WebP images up to 5MB.
    Images are resized to 256x256 for consistency.
    """
    logger.info(f"Avatar upload endpoint called for user {current_user.id}")
    logger.info(f"File details: filename={file.filename}, content_type={file.content_type}, size={file.size}")

    # Validate file type by extension and/or content type
    file_ext = Path(file.filename or "").suffix.lower()
    content_type = file.content_type or ""
    logger.info(f"Detected extension: '{file_ext}', content_type: '{content_type}'")

    # Accept if either extension OR content type is valid
    ext_valid = file_ext in ALLOWED_EXTENSIONS
    content_type_valid = content_type in ALLOWED_CONTENT_TYPES

    if not ext_valid and not content_type_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # Read and validate file size
    content = await file.read()
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size: {MAX_AVATAR_SIZE // (1024*1024)}MB"
        )

    # Delete old avatar if exists
    if current_user.avatar_url:
        old_path = settings.upload_dir / current_user.avatar_url.lstrip("/uploads/")
        if old_path.exists():
            try:
                old_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete old avatar: {e}")

    # Process and save new avatar
    try:
        img = PILImage.open(BytesIO(content))

        # Convert to RGB if necessary (handles RGBA, etc.)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Resize with aspect ratio preservation and center crop
        img.thumbnail((AVATAR_SIZE[0] * 2, AVATAR_SIZE[1] * 2), PILImage.Resampling.LANCZOS)

        # Center crop to square
        width, height = img.size
        left = (width - AVATAR_SIZE[0]) // 2
        top = (height - AVATAR_SIZE[1]) // 2
        right = left + AVATAR_SIZE[0]
        bottom = top + AVATAR_SIZE[1]
        img = img.crop((left, top, right, bottom))

        # Save with unique filename
        avatar_dir = get_avatar_dir(current_user.id)
        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = avatar_dir / filename
        img.save(filepath, "JPEG", quality=85)

        # Update user avatar URL
        avatar_url = f"/uploads/avatars/{current_user.id}/{filename}"
        current_user.avatar_url = avatar_url
        await db.commit()

        logger.info(f"User {current_user.id} uploaded new avatar: {avatar_url}")
        return AvatarUploadResponse(avatar_url=avatar_url)

    except Exception as e:
        logger.error(f"Failed to process avatar: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process image"
        )


@router.delete("/avatar", response_model=AvatarDeleteResponse)
async def delete_avatar(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete user avatar."""
    if not current_user.avatar_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No avatar to delete"
        )

    # Delete file
    avatar_path = settings.upload_dir / current_user.avatar_url.lstrip("/uploads/")
    if avatar_path.exists():
        try:
            avatar_path.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete avatar file: {e}")

    # Clear URL
    current_user.avatar_url = None
    await db.commit()

    logger.info(f"User {current_user.id} deleted avatar")
    return AvatarDeleteResponse()
