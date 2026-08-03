"""PTM (post-translational modification) schemas."""
import logging
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.ptm import PTMKind
from schemas.reference import ReferenceCreate, ReferenceUpdate

logger = logging.getLogger(__name__)

_KNOWN_KINDS = frozenset(k.value for k in PTMKind)


def _degrade_unknown_kind(value):
    """Read a `kind` nothing can draw as the plain marker, and say so.

    Two things have to be true at once here. A hand-edited or future value must
    NOT 500 the response — `PTMResponse` is embedded in `ExperimentResponse`, so
    one bad row would take out every experiment list, not just its own. And it
    must degrade to `none`, which is the same answer `pointMarker.ptmKindOf`
    gives on the client: one degradation rule, not two that disagree.

    ⚠️ It must also be *noticed*. Passing it through silently was the original
    shape of this field, and it meant a control could be drawn as the sample it
    exists to be compared against with nothing logged anywhere.
    """
    if value in _KNOWN_KINDS:
        return value
    logger.error(
        "PTM row carries unknown kind %r; every point assigned to it will be "
        "drawn as a plain non-PTM sample. Check the ck_ptms_kind constraint.",
        value,
    )
    return PTMKind.NONE.value


class PTMCreate(ReferenceCreate):
    """Schema for creating a PTM."""

    # `use_enum_values` makes a validated `kind` a plain string, and
    # `validate_default` extends that to the default — pydantic does NOT validate
    # defaults otherwise, so without it the common path (client omits `kind`)
    # hands `PTM(**values)` an enum member while the explicit path hands it a
    # str. That survives only because PTMKind subclasses str, and
    # `str(PTMKind.MODIFICATION)` is "PTMKind.MODIFICATION" on 3.12.
    #
    # `extra="forbid"` is restated for local legibility only — pydantic v2
    # MERGES model_config across bases, so it is inherited either way.
    # test_reference_schema_guards asserts the effective value.
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True
    )

    name: str = Field(..., min_length=1, max_length=100)
    abbreviation: Optional[str] = Field(None, max_length=50)
    modified_residue: Optional[str] = Field(None, max_length=100)
    enzyme: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    kind: PTMKind = PTMKind.MODIFICATION


class PTMUpdate(ReferenceUpdate):
    """Schema for updating a PTM (all optional)."""

    # No `validate_default` here: the default IS None (meaning "leave alone"),
    # and validating it would trip the null guard below at construction.
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    abbreviation: Optional[str] = Field(None, max_length=50)
    modified_residue: Optional[str] = Field(None, max_length=100)
    enzyme: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    kind: Optional[PTMKind] = None

    @field_validator("kind", mode="before")
    @classmethod
    def _reject_null_kind(cls, value):
        """`kind` is the second NOT NULL column on this table; `name` was the first.

        Exactly the failure `RejectsNullName` exists to prevent, and it did not
        cover this field because the guard was written per-field rather than per
        NOT NULL column: `exclude_unset` keeps an explicit null, the router
        setattrs it onto a NOT NULL column, and Postgres answers with a 500 where
        a 422 belongs. `color` is the deliberate exception (null means "pick me a
        fresh one"); `kind` has no such meaning.
        """
        if value is None:
            raise ValueError("kind cannot be null; omit the field to leave it unchanged")
        return value


class PTMResponse(BaseModel):
    """Basic PTM response (embedded in ExperimentResponse)."""
    id: int
    name: str
    abbreviation: Optional[str] = None
    modified_residue: Optional[str] = None
    enzyme: Optional[str] = None
    color: Optional[str] = None
    # Required, not defaulted: a source object with no `kind` at all is a
    # programming error and should say so, rather than silently reporting
    # "modification" — which is the one value that draws a control as a sample.
    kind: str

    _degrade_kind = field_validator("kind", mode="before")(_degrade_unknown_kind)

    class Config:
        from_attributes = True


class PTMDetailedResponse(BaseModel):
    """Detailed PTM response with stats."""
    id: int
    name: str
    abbreviation: Optional[str] = None
    modified_residue: Optional[str] = None
    enzyme: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    kind: str
    experiment_count: int = 0
    created_at: Optional[datetime] = None

    _degrade_kind = field_validator("kind", mode="before")(_degrade_unknown_kind)

    class Config:
        from_attributes = True

    @classmethod
    def from_ptm(cls, ptm, experiment_count: int = 0) -> "PTMDetailedResponse":
        return cls(
            id=ptm.id,
            name=ptm.name,
            abbreviation=ptm.abbreviation,
            modified_residue=ptm.modified_residue,
            enzyme=ptm.enzyme,
            description=ptm.description,
            color=ptm.color,
            kind=ptm.kind,
            experiment_count=experiment_count,
            created_at=ptm.created_at,
        )
