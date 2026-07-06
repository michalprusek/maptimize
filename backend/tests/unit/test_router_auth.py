"""In-process unit tests for the auth router.

Covers only paths the httpx integration suite cannot reach — currently the
duplicate-email race (IntegrityError on flush), which needs a mocked session
to simulate two concurrent registrations.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import routers.auth as auth_r
from schemas.user import UserCreate
from tests.unit.conftest import make_result


async def test_register_duplicate_race_maps_integrity_error_to_400(mock_db):
    """When the pre-check passes but flush hits the unique constraint
    (concurrent registration of the same email), the handler returns the
    same 400 as the sequential duplicate case, not a 500."""
    mock_db.execute.return_value = make_result(scalar=None)
    mock_db.flush = AsyncMock(
        side_effect=IntegrityError("INSERT INTO users ...", {}, Exception("duplicate key"))
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth_r.register(
            user_data=UserCreate(
                name="Race User",
                email="race@utia.cas.cz",
                password="securepass123",
            ),
            db=mock_db,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Email already registered"
