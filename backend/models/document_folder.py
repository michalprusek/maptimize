"""Folders for organizing the document library (a file-explorer tree).

Folders are group-shared like library documents: any member of the owner's group
sees and can organize them. ``parent_id`` builds the tree (NULL = a root folder);
it is a plain Integer (not a hard FK) so the tree can be reparented freely and
create_all needs no special ordering. Deletion "dissolves" a folder by moving its
contents up to the parent (handled in the router), so documents are never lost.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class DocumentFolder(Base):
    __tablename__ = "document_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<DocumentFolder(id={self.id}, name={self.name!r}, parent={self.parent_id})>"
