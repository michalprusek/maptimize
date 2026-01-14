"""User settings model for display preferences."""
from typing import TYPE_CHECKING, Optional
from enum import Enum as PyEnum

from sqlalchemy import String, Enum, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .user import User


class DisplayMode(str, PyEnum):
    """Image display modes (LUT - Lookup Table).

    Based on ImageJ conventions:
    - GRAYSCALE: Standard white-on-black display (default)
    - INVERTED: Black-on-white display
    - GREEN: GFP-style green fluorescence on black
    - FIRE: Heat-map style (black-red-yellow-white)
    - HILO: Highlights under/over-exposed pixels
    """
    GRAYSCALE = "grayscale"
    INVERTED = "inverted"
    GREEN = "green"
    FIRE = "fire"
    HILO = "hilo"


class Theme(str, PyEnum):
    """UI theme options."""
    DARK = "dark"
    LIGHT = "light"


class Language(str, PyEnum):
    """Supported interface languages."""
    EN = "en"
    FR = "fr"


class UserSettings(Base):
    """User settings for display and interface preferences.

    Each user has exactly one settings record (one-to-one relationship).
    Settings are created automatically when a user first accesses the settings page.
    """

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True
    )

    # Display preferences
    display_mode: Mapped[str] = mapped_column(
        String(20),
        default=DisplayMode.GRAYSCALE.value
    )
    theme: Mapped[str] = mapped_column(
        String(10),
        default=Theme.DARK.value
    )
    language: Mapped[str] = mapped_column(
        String(5),
        default=Language.EN.value
    )

    # Relationship back to user
    user: Mapped["User"] = relationship(back_populates="settings")

    def __repr__(self) -> str:
        return f"<UserSettings(user_id={self.user_id}, display={self.display_mode}, theme={self.theme})>"
