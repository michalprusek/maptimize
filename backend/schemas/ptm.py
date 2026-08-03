"""PTM (post-translational modification) schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from models.ptm import PTMKind
from schemas.reference import ReferenceCreate, ReferenceUpdate


class PTMCreate(ReferenceCreate):
    """Schema for creating a PTM."""

    # `use_enum_values` so `model_dump()` yields the plain string the VARCHAR
    # column wants — the router feeds it straight into `PTM(**values)`.
    # `extra="forbid"` is restated rather than inherited, because overriding
    # model_config replaces it wholesale; test_reference_schema_guards pins it.
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    name: str = Field(..., min_length=1, max_length=100)
    abbreviation: Optional[str] = Field(None, max_length=50)
    modified_residue: Optional[str] = Field(None, max_length=100)
    enzyme: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    kind: PTMKind = PTMKind.MODIFICATION


class PTMUpdate(ReferenceUpdate):
    """Schema for updating a PTM (all optional)."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    abbreviation: Optional[str] = Field(None, max_length=50)
    modified_residue: Optional[str] = Field(None, max_length=100)
    enzyme: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    kind: Optional[PTMKind] = None


class PTMResponse(BaseModel):
    """Basic PTM response (embedded in ExperimentResponse)."""
    id: int
    name: str
    abbreviation: Optional[str] = None
    modified_residue: Optional[str] = None
    enzyme: Optional[str] = None
    color: Optional[str] = None
    # `str`, not PTMKind: the column carries no CHECK constraint, so one
    # hand-edited row would otherwise 500 every list that embeds a PTM. The
    # client normalises anything it does not recognise back to the plain marker.
    kind: str = PTMKind.MODIFICATION.value

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
    kind: str = PTMKind.MODIFICATION.value
    experiment_count: int = 0
    created_at: Optional[datetime] = None

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
