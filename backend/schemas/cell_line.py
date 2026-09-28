"""Cell line schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.reference import ReferenceCreate, ReferenceUpdate


class CellLineCreate(ReferenceCreate):
    """Schema for creating a cell line.

    `str_strip_whitespace` because this name arrives from a free-text box in the
    experiment form, where a trailing space is invisible -- and would otherwise
    be the only thing distinguishing a new row from the one already there.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class CellLineUpdate(ReferenceUpdate):
    """Schema for updating a cell line (all optional)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class CellLineResponse(BaseModel):
    """Basic cell line response (embedded in ExperimentResponse)."""
    id: int
    name: str
    color: Optional[str] = None

    class Config:
        from_attributes = True


class CellLineDetailedResponse(BaseModel):
    """Detailed cell line response with stats."""
    id: int
    name: str
    description: Optional[str] = None
    color: Optional[str] = None
    experiment_count: int = 0
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_cell_line(cls, cell_line, experiment_count: int = 0) -> "CellLineDetailedResponse":
        return cls(
            id=cell_line.id,
            name=cell_line.name,
            description=cell_line.description,
            color=cell_line.color,
            experiment_count=experiment_count,
            created_at=cell_line.created_at,
        )
