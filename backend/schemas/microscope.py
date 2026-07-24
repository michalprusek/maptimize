"""Microscope schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MicroscopeCreate(BaseModel):
    """Schema for creating a microscope."""
    name: str = Field(..., min_length=1, max_length=100)
    manufacturer: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    objective: Optional[str] = Field(None, max_length=100)
    magnification: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class MicroscopeUpdate(BaseModel):
    """Schema for updating a microscope (all optional)."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    manufacturer: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    objective: Optional[str] = Field(None, max_length=100)
    magnification: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")


class MicroscopeResponse(BaseModel):
    """Basic microscope response (embedded in ExperimentResponse)."""
    id: int
    name: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    objective: Optional[str] = None
    magnification: Optional[str] = None
    color: Optional[str] = None

    class Config:
        from_attributes = True


class MicroscopeDetailedResponse(BaseModel):
    """Detailed microscope response with stats."""
    id: int
    name: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    objective: Optional[str] = None
    magnification: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    experiment_count: int = 0
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_microscope(cls, microscope, experiment_count: int = 0) -> "MicroscopeDetailedResponse":
        return cls(
            id=microscope.id,
            name=microscope.name,
            manufacturer=microscope.manufacturer,
            model=microscope.model,
            objective=microscope.objective,
            magnification=microscope.magnification,
            description=microscope.description,
            color=microscope.color,
            experiment_count=experiment_count,
            created_at=microscope.created_at,
        )
