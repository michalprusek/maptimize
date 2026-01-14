"""SAM embedding model for pre-computed image encoder outputs."""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, DateTime, ForeignKey, LargeBinary, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

if TYPE_CHECKING:
    from .image import Image


class SAMEmbedding(Base):
    """
    Pre-computed SAM image encoder embedding for an FOV.

    SAM (Segment Anything Model) requires expensive encoder computation (~5-15s).
    By caching the embedding, interactive segmentation becomes instant (~10-50ms).

    The embedding is stored compressed (zlib + float16) to reduce database size
    from ~16MB to ~2-4MB per image.
    """

    __tablename__ = "sam_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"),
        unique=True,  # One embedding per image
        index=True
    )

    # SAM model variant used (sam3, sam2-hiera-large, etc.)
    model_variant: Mapped[str] = mapped_column(String(50))

    # Embedding stored as compressed binary (numpy array with zlib)
    # SAM3 embedding is ~256x64x256 float32 = ~16MB uncompressed
    # Compressed to ~2-4MB with zlib + float16
    embedding_data: Mapped[bytes] = mapped_column(LargeBinary)

    # Shape string for reconstruction (e.g., "256,64,256")
    embedding_shape: Mapped[str] = mapped_column(String(50))

    # Original image dimensions (needed for coordinate mapping during inference)
    original_width: Mapped[int] = mapped_column(Integer)
    original_height: Mapped[int] = mapped_column(Integer)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )

    # Relationship
    image: Mapped["Image"] = relationship(back_populates="sam_embedding")

    def __repr__(self) -> str:
        return f"<SAMEmbedding(id={self.id}, image_id={self.image_id}, model={self.model_variant})>"
