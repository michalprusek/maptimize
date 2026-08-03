"""Post-translational modification (PTM) model."""
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import CheckConstraint, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class PTMKind(str, PyEnum):
    """What a row in this vocabulary actually is.

    The list is not homogeneous. `Unmodified` is the *absence* of a modification
    and `Control` is a transfection carried out with a catalytically inactive
    enzyme; neither is a tubulin mark. The lab runs a control alongside every PTM
    condition, so telling the three apart is the whole point of recording them.

    This drives the **second visual channel** on the projections — colour already
    carries the protein, so a control keeps its colour and is drawn as a
    translucent ring, while a modified sample gets a black centre dot.

    ⚠️ Stored as a plain VARCHAR, not a Postgres enum: `ensure_schema_updates()`
    adds columns with raw ALTER TABLE and cannot CREATE TYPE, so an enum column
    would exist on a fresh database and be silently missing in production. Same
    choice, and the same reason, as `document_folders.kind`.

    ⚠️ The class is read from here and **never from `name`**. Renaming the row,
    or the lab creating "Control (inactive VASH)" instead, must not quietly
    return every control point to the plain marker with nothing failing.
    """

    MODIFICATION = "modification"
    CONTROL = "control"
    NONE = "none"


def _kind_check_sql() -> str:
    """`kind IN (...)`, generated from the enum so the two cannot drift.

    A CHECK is NOT the thing the docstring above rules out — that is `CREATE
    TYPE`, and `ensure_schema_updates()` already adds constraints elsewhere. It
    matters because Pydantic guards only the API, while
    `scripts/ptm_control_backfill.sql` writes these values as hand-typed
    literals: a typo there (`'controls'`, `'Control'`) produces exactly the
    silence this feature exists to prevent, and it is the writer that has
    actually run against production.
    """
    return "kind IN (" + ", ".join(f"'{k.value}'" for k in PTMKind) + ")"


PTM_KIND_CHECK = "ck_ptms_kind"


class PTM(Base):
    """A post-translational modification of the microtubule lattice.

    Shared between all users (like MapProtein and Microscope): reference data
    describing the tubulin-code state the cells' microtubules carried, which
    experiments can be assigned to. No user_id — one list for the whole lab.
    """

    __tablename__ = "ptms"
    # Fresh databases get the constraint here; existing ones get the identical
    # constraint from ensure_schema_updates(), both generated from PTMKind.
    __table_args__ = (CheckConstraint(_kind_check_sql(), name=PTM_KIND_CHECK),)

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
    # Modification / inactive-enzyme control / no modification. See PTMKind.
    kind: Mapped[str] = mapped_column(
        String(20),
        default=PTMKind.MODIFICATION.value,
        server_default=PTMKind.MODIFICATION.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<PTM(id={self.id}, name={self.name})>"


# The canonical tubulin code plus the two entries that are not marks at all,
# seeded once so the lab starts from a usable vocabulary instead of an empty
# page. These are ordinary editable rows, not an enum: rename, delete or add to
# them freely. Colours come from the front of utils.colors.COLOR_PALETTE so the
# seeded set is legible on a plot immediately.
#
# ⚠️ Every entry states its `kind` explicitly rather than letting the column
# default supply it — otherwise the seed and the column drift apart silently the
# moment that default moves, and `kind` is what the projections draw from.
#
# ⚠️ This only ever runs against an EMPTY table (`seed_default_data`), so it is
# not how production got `Control`: re-adding a row the lab deliberately deleted
# would be worse than a short vocabulary. Production was seeded once by
# scripts/ptm_control_backfill.sql.
DEFAULT_PTMS = [
    {
        "name": "Tyrosination",
        "abbreviation": "Tyr",
        "modified_residue": "α-tubulin C-terminal tyrosine",
        "enzyme": "TTL (writer)",
        "description": "The default state of most cytoplasmic microtubules.",
        "color": "#3b82f6",
        "kind": PTMKind.MODIFICATION.value,
    },
    {
        "name": "Detyrosination",
        "abbreviation": "deTyr",
        "modified_residue": "α-tubulin C-terminal tyrosine",
        # Writer/eraser are named relative to THIS row's state, not to
        # tyrosination: the vasohibins and MATCAP create the detyrosinated
        # lattice, and TTL is what reverses it.
        "enzyme": "VASH1/VASH2-SVBP, MATCAP (writers) / TTL (eraser)",
        "description": "Removal of the C-terminal tyrosine, exposing glutamate (Glu-tubulin). Marks long-lived microtubules.",
        "color": "#ef4444",
        "kind": PTMKind.MODIFICATION.value,
    },
    {
        "name": "Δ2-tubulin",
        "abbreviation": "Δ2",
        "modified_residue": "α-tubulin C-terminal glutamate",
        "enzyme": "CCP1, CCP4, CCP6",
        "description": "Further loss of the penultimate glutamate. Irreversible — cannot be re-tyrosinated.",
        "color": "#00d4aa",
        "kind": PTMKind.MODIFICATION.value,
    },
    {
        "name": "Δ3-tubulin",
        "abbreviation": "Δ3",
        "modified_residue": "α-tubulin C-terminus",
        "enzyme": "CCP1-CCP6",
        "description": "Loss of a third C-terminal residue, downstream of Δ2.",
        "color": "#f59e0b",
        "kind": PTMKind.MODIFICATION.value,
    },
    {
        "name": "Acetylation",
        "abbreviation": "K40ac",
        "modified_residue": "α-tubulin K40 (luminal)",
        "enzyme": "ATAT1 (writer) / HDAC6, SIRT2 (erasers)",
        "description": "Luminal modification associated with mechanically resilient, long-lived microtubules.",
        "color": "#8b5cf6",
        "kind": PTMKind.MODIFICATION.value,
    },
    {
        "name": "Polyglutamylation",
        "abbreviation": "polyE",
        "modified_residue": "α/β-tubulin C-terminal tails",
        "enzyme": "TTLL1/4/5/6/7/11/13 (writers) / CCP1-CCP6 (erasers)",
        "description": "Branched glutamate chains on the E-hooks. A principal regulator of MAP and motor binding.",
        "color": "#ec4899",
        "kind": PTMKind.MODIFICATION.value,
    },
    {
        "name": "Monoglutamylation",
        "abbreviation": "monoE",
        "modified_residue": "α/β-tubulin C-terminal tails",
        "enzyme": "TTLL4, TTLL5, TTLL7 (writers) / CCP5 (eraser)",
        "description": "A single glutamate branch point, the initiating step of glutamylation.",
        "color": "#22c55e",
        "kind": PTMKind.MODIFICATION.value,
    },
    {
        "name": "Polyglycylation",
        "abbreviation": "polyG",
        "modified_residue": "α/β-tubulin C-terminal tails",
        "enzyme": "TTLL3, TTLL8, TTLL10",
        "description": "Glycine chains on the E-hooks; abundant in axonemes.",
        "color": "#06b6d4",
        "kind": PTMKind.MODIFICATION.value,
    },
    {
        "name": "Phosphorylation",
        "abbreviation": "phospho",
        "modified_residue": "β-tubulin S172, α-tubulin Y432",
        "enzyme": "CDK1, Syk",
        "description": "Modulates tubulin polymerisation rather than lattice-surface binding.",
        "color": "#f97316",
        "kind": PTMKind.MODIFICATION.value,
    },
    {
        "name": "Unmodified",
        "abbreviation": "none",
        "modified_residue": None,
        "enzyme": None,
        # It used to call itself "the control condition", which conflated two
        # different things: a sample nothing was done to, and a sample that went
        # through the transfection with a dead enzyme. They are separate rows now.
        "description": "Recombinant or subtilisin-treated tubulin carrying no modification.",
        "color": "#a855f7",
        "kind": PTMKind.NONE.value,
    },
    {
        "name": "Control",
        "abbreviation": "ctrl",
        "modified_residue": None,
        "enzyme": None,
        "description": (
            "Paired control for a PTM condition: the same transfection carried "
            "out with a catalytically inactive enzyme, so the lattice is "
            "unmodified. Run alongside the modified sample it is compared to."
        ),
        # Neutral grey on purpose: for a value that is not a modification, grey
        # is the right answer wherever the PTM's own colour is shown — the
        # colour-by-PTM legend, the facet pills, and the dot on its card in
        # /dashboard/ptms. (Under the default colour-by, protein, points take
        # the protein's hue and this is not what draws them.)
        "color": "#94a3b8",
        "kind": PTMKind.CONTROL.value,
    },
]
