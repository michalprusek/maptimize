"""Post-translational modification (PTM) model."""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class PTM(Base):
    """A post-translational modification of the microtubule lattice.

    Shared between all users (like MapProtein and Microscope): reference data
    describing the tubulin-code state the cells' microtubules carried, which
    experiments can be assigned to. No user_id — one list for the whole lab.
    """

    __tablename__ = "ptms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    # Short form used on plot legends and chips, where the full name never fits:
    # "polyE", "deTyr", "Δ2".
    abbreviation: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Where on tubulin the modification sits, e.g. "α-tubulin K40".
    modified_residue: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Writer and/or eraser enzymes, e.g. "TTLL1-TTLL7 / CCP1-CCP6".
    enzyme: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # Hex for UMAP legend
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<PTM(id={self.id}, name={self.name})>"


# The canonical tubulin code, seeded once so the lab starts from a usable
# vocabulary instead of an empty page. These are ordinary editable rows, not an
# enum: rename, delete or add to them freely. Colours come from the front of
# utils.colors.COLOR_PALETTE so the seeded set is legible on a plot immediately.
DEFAULT_PTMS = [
    {
        "name": "Tyrosination",
        "abbreviation": "Tyr",
        "modified_residue": "α-tubulin C-terminal tyrosine",
        "enzyme": "TTL (writer)",
        "description": "The default state of most cytoplasmic microtubules.",
        "color": "#3b82f6",
    },
    {
        "name": "Detyrosination",
        "abbreviation": "deTyr",
        "modified_residue": "α-tubulin C-terminal tyrosine",
        "enzyme": "VASH1/VASH2-SVBP, MATCAP (erasers) / TTL (writer)",
        "description": "Removal of the C-terminal tyrosine, exposing glutamate (Glu-tubulin). Marks long-lived microtubules.",
        "color": "#ef4444",
    },
    {
        "name": "Δ2-tubulin",
        "abbreviation": "Δ2",
        "modified_residue": "α-tubulin C-terminal glutamate",
        "enzyme": "CCP1, CCP5",
        "description": "Further loss of the penultimate glutamate. Irreversible — cannot be re-tyrosinated.",
        "color": "#00d4aa",
    },
    {
        "name": "Δ3-tubulin",
        "abbreviation": "Δ3",
        "modified_residue": "α-tubulin C-terminus",
        "enzyme": "CCP1-CCP6",
        "description": "Loss of a third C-terminal residue, downstream of Δ2.",
        "color": "#f59e0b",
    },
    {
        "name": "Acetylation",
        "abbreviation": "K40ac",
        "modified_residue": "α-tubulin K40 (luminal)",
        "enzyme": "ATAT1 (writer) / HDAC6, SIRT2 (erasers)",
        "description": "Luminal modification associated with mechanically resilient, long-lived microtubules.",
        "color": "#8b5cf6",
    },
    {
        "name": "Polyglutamylation",
        "abbreviation": "polyE",
        "modified_residue": "α/β-tubulin C-terminal tails",
        "enzyme": "TTLL1/4/5/6/7/11/13 (writers) / CCP1-CCP6 (erasers)",
        "description": "Branched glutamate chains on the E-hooks. A principal regulator of MAP and motor binding.",
        "color": "#ec4899",
    },
    {
        "name": "Monoglutamylation",
        "abbreviation": "monoE",
        "modified_residue": "α/β-tubulin C-terminal tails",
        "enzyme": "TTLL4, TTLL5, TTLL7 (writers) / CCP5 (eraser)",
        "description": "A single glutamate branch point, the initiating step of glutamylation.",
        "color": "#22c55e",
    },
    {
        "name": "Polyglycylation",
        "abbreviation": "polyG",
        "modified_residue": "α/β-tubulin C-terminal tails",
        "enzyme": "TTLL3, TTLL8, TTLL10",
        "description": "Glycine chains on the E-hooks; abundant in axonemes.",
        "color": "#06b6d4",
    },
    {
        "name": "Phosphorylation",
        "abbreviation": "phospho",
        "modified_residue": "β-tubulin S172, α-tubulin Y432",
        "enzyme": "CDK1, Syk",
        "description": "Modulates tubulin polymerisation rather than lattice-surface binding.",
        "color": "#f97316",
    },
    {
        "name": "Unmodified",
        "abbreviation": "none",
        "modified_residue": None,
        "enzyme": None,
        "description": "Recombinant or subtilisin-treated tubulin carrying no modification. The control condition.",
        "color": "#a855f7",
    },
]
