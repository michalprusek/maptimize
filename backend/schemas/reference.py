"""Shared guards for the write schemas of rows with a NOT NULL name.

`map_proteins`, `microscopes` and `ptms` share a shape, and their routers already
share `utils/reference_data.py`. Their schemas are the other half of that: written
out three times, none of them picked up the `extra="forbid"` that
`ExperimentUpdate` has — and `ExperimentUpdate` never picked up the null-name
guard, so all four families were missing one of the two.

⚠️ Both guards are load-bearing and neither is visible at a call site, so they are
pinned by `tests/unit/test_reference_schema_guards.py`. They were once silently
removed with all 1512 tests still green; do not rely on review to catch that.
"""
from pydantic import BaseModel, ConfigDict, field_validator


class RejectsNullName(BaseModel):
    """Refuse an explicit `"name": null` on a PATCH.

    A PATCH distinguishes "omitted" from "explicitly null", and for a field like
    `color` that distinction is meaningful — null asks for a fresh unused colour.
    For `name` it is not: the column is NOT NULL, so an explicit null reaches the
    UPDATE and surfaces as a 500 where a 422 belongs.

    `check_fields=False` so schemas that declare `name` themselves can inherit
    this; one that has no `name` field simply never triggers it.
    """

    @field_validator("name", mode="before", check_fields=False)
    @classmethod
    def _reject_null_name(cls, value):
        if value is None:
            raise ValueError("name cannot be null; omit the field to leave it unchanged")
        return value


class ReferenceCreate(BaseModel):
    """Base for creating a reference row.

    `extra="forbid"` because the alternative is silence: a misspelled field is
    dropped, the row is created without it, and the client is told it worked.
    """

    model_config = ConfigDict(extra="forbid")


class ReferenceUpdate(ReferenceCreate, RejectsNullName):
    """Base for patching a reference row.

    Same `extra="forbid"`, where the consequence is sharper: a PATCH whose only
    field is misspelled returns 200 having changed nothing.
    """

    model_config = ConfigDict(extra="forbid")
