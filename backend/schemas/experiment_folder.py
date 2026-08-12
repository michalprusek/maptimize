"""Schemas for the experiment folder tree."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ExperimentFolderCreate(BaseModel):
    """Create a folder, optionally nested under another one."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    parent_id: Optional[int] = None
    # Which group's tree a TOP-LEVEL folder joins. Ignored for a subfolder, which
    # always inherits its parent. Omit it and the server picks the caller's only
    # group, or leaves the folder private when they have several — the same rule
    # as everywhere else. Naming it is how someone in two groups starts a shared
    # tree at all; this tree has no seeded roots to nest under.
    group_id: Optional[int] = None


class ExperimentFolderUpdate(BaseModel):
    """Rename and/or move a folder. Only the fields passed are changed.

    ``parent_id`` is the one field where an explicit null means something
    different from omitting it: null moves the folder to the top level, omitting
    it leaves the folder where it is. ``exclude_unset`` in the router is what
    keeps those apart, so a rename never moves anything.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    parent_id: Optional[int] = None


class ExperimentFolderResponse(BaseModel):
    """One folder, with the number of experiments filed directly in it.

    ``experiment_count`` counts THIS folder only, not the subtree — the client
    already holds the whole tree and can roll it up, and a server-side rollup
    would need a recursive query to answer a question the client can answer for
    free. Anything showing a subtree total does it there.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: Optional[int] = None
    group_id: Optional[int] = None
    owner_user_id: int
    experiment_count: int = 0
    created_at: Optional[datetime] = None


class ExperimentTreeResponse(BaseModel):
    """The whole visible tree, plus the one count it cannot be derived from.

    Every folder row carries its own experiment count and the client rolls up
    subtrees itself. "Unfiled" has no folder to hang a count on, and the only
    other way to learn it is a second full listing on page load — so the server,
    which already knows, says it here.
    """

    folders: list[ExperimentFolderResponse]
    unfiled_count: int = Field(
        0, description="Experiments the caller can see that sit in no folder"
    )
