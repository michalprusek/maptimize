"""Microscope model + schema unit tests."""
from models.microscope import Microscope
from schemas.microscope import (
    MicroscopeCreate,
    MicroscopeDetailedResponse,
    MicroscopeResponse,
    MicroscopeUpdate,
)


def test_microscope_table_and_columns():
    assert Microscope.__tablename__ == "microscopes"
    cols = set(Microscope.__table__.columns.keys())
    assert cols == {
        "id", "name", "manufacturer", "model", "objective",
        "magnification", "description", "color", "created_at",
    }
    assert Microscope.__table__.columns["name"].unique is True


def test_microscope_shared_reference_no_owner():
    # Shared reference data: must NOT carry a user/group owner column.
    cols = set(Microscope.__table__.columns.keys())
    assert "user_id" not in cols
    assert "group_id" not in cols


def test_microscope_create_schema_defaults():
    m = MicroscopeCreate(name="Zeiss LSM 880")
    assert m.name == "Zeiss LSM 880"
    assert m.manufacturer is None and m.magnification is None


def test_microscope_detailed_from_model():
    class FakeM:
        id = 3
        name = "Leica SP8"
        manufacturer = "Leica"
        model = "SP8"
        objective = "HC PL APO 63×/1.40"
        magnification = "63×"
        description = None
        color = "#3b82f6"
        created_at = None
    resp = MicroscopeDetailedResponse.from_microscope(FakeM(), 5)
    assert resp.id == 3 and resp.experiment_count == 5
    assert resp.magnification == "63×"


def test_microscope_update_all_optional():
    assert MicroscopeUpdate().model_dump(exclude_unset=True) == {}
