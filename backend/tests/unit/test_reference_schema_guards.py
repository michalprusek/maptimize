"""The two guards every write schema for a NOT NULL-named row must keep.

Both failed quietly rather than loudly before 2026-07-28: a misspelled field was
dropped and the request still returned 200/201, and an explicit `{"name": null}`
reached the column and came back 500.

⚠️ This file exists because both guards were once removed again with all 1512
other tests still green — they are configuration, not code, so nothing else
observes them. Parametrised over every schema that carries them, so a fourth
entity, or one that forgets to inherit the base, is covered by construction.
"""
import pytest
from pydantic import ValidationError

from schemas.experiment import ExperimentUpdate
from schemas.image import MapProteinCreate, MapProteinUpdate
from schemas.microscope import MicroscopeCreate, MicroscopeUpdate
from schemas.ptm import PTMCreate, PTMUpdate

REFERENCE_UPDATES = [MapProteinUpdate, MicroscopeUpdate, PTMUpdate]
CREATE_SCHEMAS = [MapProteinCreate, MicroscopeCreate, PTMCreate]
# ExperimentUpdate maps to a NOT NULL name too, and was the family that kept the
# extra="forbid" but never gained the null guard.
UPDATE_SCHEMAS = REFERENCE_UPDATES + [ExperimentUpdate]


@pytest.mark.parametrize("schema", CREATE_SCHEMAS + UPDATE_SCHEMAS)
def test_unknown_fields_are_rejected(schema):
    # Dropping a typo silently is the failure: the write "succeeds" without it.
    with pytest.raises(ValidationError):
        schema.model_validate({"name": "x", "abbrevation": "typo"})


@pytest.mark.parametrize("schema", CREATE_SCHEMAS + UPDATE_SCHEMAS)
def test_the_forbid_config_is_actually_set(schema):
    # Asserted directly as well as behaviourally: the guard is inherited config,
    # and an inheritance change can drop it without changing any call site.
    assert schema.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("schema", UPDATE_SCHEMAS)
def test_explicitly_null_name_is_rejected(schema):
    with pytest.raises(ValidationError):
        schema.model_validate({"name": None})


@pytest.mark.parametrize("schema", UPDATE_SCHEMAS)
def test_omitting_name_is_still_a_valid_patch(schema):
    # The guard must reject an explicit null, not make the field required.
    assert "name" not in schema.model_validate({}).model_dump(exclude_unset=True)


@pytest.mark.parametrize("schema", REFERENCE_UPDATES)
def test_explicitly_null_colour_still_means_repick(schema):
    # The one field where an explicit null IS meaningful — the routers read it as
    # "assign me an unused colour". A blanket null rejection would break it.
    assert schema.model_validate({"color": None}).model_dump(exclude_unset=True) == {
        "color": None
    }


@pytest.mark.parametrize("schema", CREATE_SCHEMAS)
def test_create_still_accepts_its_own_fields(schema):
    assert schema.model_validate({"name": "Something"}).name == "Something"
