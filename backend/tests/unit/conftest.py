"""In-process unit-test harness.

These tests import service/util code directly and run it under coverage WITHOUT a
live server, a real database, or GPU/ML libraries. That is the only way to cover
the ML/external-integration modules (Gemini agent, RAG, encoders, import/export)
that the httpx integration suite cannot reach.

Two safety measures keep this from crashing under coverage:
  * heavy native libs are replaced with mocks in sys.modules (torch's docstring
    registration and cv2's typing loader crash when imported while coverage's
    tracer is active);
  * the DB is always an AsyncMock, so no asyncpg/greenlet runs (which segfaults
    under coverage's C tracer).

Service code lazy-imports its ML dependencies, so individual tests mock those
getters (e.g. ``get_qwen_vl_encoder``) at the call boundary.
"""
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# --- Replace heavy native libs with mocks BEFORE any service import. ----------
# setdefault: the real lib is installed but not yet imported in this process, so
# the mock wins; if something already imported it, we leave the real one.
for _name in ("torch", "torchvision", "cv2", "ultralytics"):
    sys.modules.setdefault(_name, MagicMock(name=_name))


def make_result(*, scalar=None, scalars_all=None, first=None, fetchall=None,
                rowcount=None):
    """Build a mock SQLAlchemy Result with the chained accessors services use."""
    result = MagicMock(name="Result")
    result.scalar_one_or_none.return_value = scalar
    result.scalar.return_value = scalar if scalar is not None else (
        first if first is not None else None
    )
    scalars = MagicMock(name="ScalarResult")
    scalars.all.return_value = scalars_all if scalars_all is not None else []
    scalars.first.return_value = (scalars_all or [None])[0] if scalars_all else None
    result.scalars.return_value = scalars
    result.first.return_value = first
    result.fetchall.return_value = fetchall if fetchall is not None else []
    result.all.return_value = fetchall if fetchall is not None else []
    if rowcount is not None:
        result.rowcount = rowcount
    return result


@pytest.fixture
def mock_db():
    """An AsyncMock AsyncSession. Configure ``execute`` per test via make_result.

    Example:
        mock_db.execute.return_value = make_result(scalar=my_obj)
    """
    db = AsyncMock(name="AsyncSession")
    db.execute.return_value = make_result()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    db.add = MagicMock()
    return db
