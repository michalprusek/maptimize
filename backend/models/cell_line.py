"""Cell line model."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class CellLine(Base):
    """The cultured cell line the MAP was expressed in.

    Shared between all users (like MapProtein, Microscope and PTM): reference
    data naming a biological background experiments can be assigned to. No
    user_id -- one list for the whole lab.

    Deliberately a table rather than a free-text column on `experiments`, even
    though any name may be typed into it. The dashboard's facet machinery is
    built on integer ids throughout -- `facet_clause` emits `IN (:ids)`,
    `UNASSIGNED_FACET_ID` is 0, the URL round-trip parses with `Number()` -- so a
    string-valued facet would be a second parallel implementation of all of it.
    Writing the name once also means "U2OS", "U2os" and "U-2 OS" cannot become
    three silently unmerged facets.
    """

    __tablename__ = "cell_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # Hex for UMAP legend
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<CellLine(id={self.id}, name={self.name})>"


# The line this lab actually works in, seeded so the facet is populated from the
# first start rather than being an empty control nobody fills in.
#
# ⚠️ Like DEFAULT_PTMS this only ever runs against an EMPTY table, so it is not
# what assigned production's existing experiments -- `scripts/cell_line_backfill.sql`
# did that, once. Re-adding a row the lab deliberately deleted would be worse
# than a short list.
DEFAULT_CELL_LINES = [
    {
        "name": "U2OS",
        "description": "Human osteosarcoma line; the lab's default background for MAP expression.",
        # Front of utils.colors.COLOR_PALETTE, so the seeded row is legible on a
        # plot immediately.
        "color": "#3b82f6",
    },
]
