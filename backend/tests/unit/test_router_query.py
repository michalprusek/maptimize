"""Unit tests for routers/query.py — the POST /api/query endpoint.

The handler coroutine is invoked directly with a mock user and DB; the service
and the group resolver are patched at the router boundary.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers import query as query_router
from routers.query import QueryRequest
from services.sql_query_service import SqlQueryError


async def test_query_endpoint_resolves_group_and_returns_rows(mock_db):
    user = SimpleNamespace(id=7)
    service_out = {"columns": ["c"], "rows": [{"c": 1}], "row_count": 1}
    with patch("routers.query.get_user_group_id", AsyncMock(return_value=3)) as group, \
         patch("routers.query.run_query", AsyncMock(return_value=service_out)) as run:
        resp = await query_router.query_database(
            QueryRequest(sql="SELECT id FROM experiments", limit=50),
            current_user=user, db=mock_db,
        )
    assert resp.row_count == 1 and resp.columns == ["c"]
    group.assert_awaited_once_with(7, mock_db)
    # sql passed positionally; user/group/limit forwarded as kwargs
    args, kwargs = run.call_args
    assert args[0] == "SELECT id FROM experiments"
    assert kwargs["user_id"] == 7 and kwargs["group_id"] == 3 and kwargs["limit"] == 50


async def test_query_endpoint_maps_service_error_to_400(mock_db):
    user = SimpleNamespace(id=1)
    with patch("routers.query.get_user_group_id", AsyncMock(return_value=None)), \
         patch("routers.query.run_query",
               AsyncMock(side_effect=SqlQueryError("Only SELECT queries are allowed."))):
        with pytest.raises(HTTPException) as exc:
            await query_router.query_database(
                QueryRequest(sql="DELETE FROM experiments"), current_user=user, db=mock_db,
            )
    assert exc.value.status_code == 400
    assert "SELECT" in exc.value.detail
