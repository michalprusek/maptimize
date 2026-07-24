"""Read-only SQL query endpoint — the agent's DB window (MCP query_database tool).

A single ``POST /api/query`` runs a validated, per-user ACL-scoped SELECT via
:mod:`services.sql_query_service`. It uses ``get_current_user`` (NOT
``require_interactive_user``), so the OAuth/PAT connector token is allowed — the
caller sees exactly its own and group-shared rows, the same as the UI.
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from services.sql_query_service import SqlQueryError, run_query
from utils.groups import get_user_group_id
from utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


class QueryRequest(BaseModel):
    sql: str = Field(..., description="A single read-only SELECT over the allowed tables.")
    limit: Optional[int] = Field(
        None, description="Max rows to return (default 100, capped at 1000)."
    )


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


@router.post("", response_model=QueryResponse)
async def query_database(
    payload: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QueryResponse:
    """Run a validated, per-user-scoped read-only SQL query.

    The ACL predicate is injected server-side after validation, so a caller can
    only ever read its own and group-shared rows. Validation/execution failures
    surface as HTTP 400 with a fixable message.
    """
    group_id = await get_user_group_id(current_user.id, db)
    try:
        result = await run_query(
            payload.sql,
            user_id=current_user.id,
            group_id=group_id,
            db=db,
            limit=payload.limit,
        )
    except SqlQueryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return QueryResponse(**result)
