"""Microscope model."""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Microscope(Base):
    """Microscope reference.

    Shared between all users (like MapProtein): reference data describing lab
    instruments that experiments can be assigned to. No user_id — one list for
    the whole lab.
    """

    __tablename__ = "microscopes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    objective: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # String, not numeric: magnifications are written "63×", "10×–100×", etc.
    magnification: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # Hex for UMAP legend
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Microscope(id={self.id}, name={self.name})>"
