"""PTM (post-translational modification) schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from schemas.reference import ReferenceCreate, ReferenceUpdate


class PTMCreate(ReferenceCreate):
    """Schema for creating a PTM."""
    name: str = Field(..., min_length=1, max_length=100)
    abbreviation: Optional[str] = Field(None, max_length=50)
    modified_residue: Optional[str] = Field(None, max_length=100)
    enzyme: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class PTMUpdate(ReferenceUpdate):
    """Schema for updating a PTM (all optional)."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    abbreviation: Optional[str] = Field(None, max_length=50)
    modified_residue: Optional[str] = Field(None, max_length=100)
    enzyme: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class PTMResponse(BaseModel):
    """Basic PTM response (embedded in ExperimentResponse)."""
    id: int
    name: str
    abbreviation: Optional[str] = None
    modified_residue: Optional[str] = None
    enzyme: Optional[str] = None
    color: Optional[str] = None

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
            experiment_count=experiment_count,
            created_at=ptm.created_at,
        )
