"""Shared validation rules for the lab's reference-data schemas.

`map_proteins`, `microscopes` and `ptms` share a shape, and their routers already
share `utils/reference_data.py`. Their schemas are the other half of that: written
out three times, they drifted from `ExperimentUpdate` and lost two guards it has.
"""
from pydantic import BaseModel, ConfigDict, field_validator


class ReferenceCreate(BaseModel):
    """Base for creating a reference row.

    `extra="forbid"` for the same reason `ExperimentUpdate` has it: without it a
    misspelled field (`abbrevation`) is silently dropped and the request returns
    200 having changed nothing, which reads as a backend that lost the write.
    """

    model_config = ConfigDict(extra="forbid")


class ReferenceUpdate(ReferenceCreate):
    """Base for patching a reference row.

    A PATCH distinguishes "omitted" from "explicitly null", and for `color` that
    distinction is meaningful — null asks for a fresh unused colour. For `name` it
    is not: the column is NOT NULL, so an explicit null used to reach the database
    and surface as a 500 instead of a 422.
    """

    @field_validator("name", mode="before", check_fields=False)
    @classmethod
    def _reject_null_name(cls, value):
        if value is None:
            raise ValueError("name cannot be null; omit the field to leave it unchanged")
        return value
