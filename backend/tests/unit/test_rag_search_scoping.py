"""Scoping a library read to folders or groups.

The default -- no params -- must cover everything the caller can read, across
every group they belong to plus their private folders. The params only narrow.

The dangerous direction is the other one: a folder id or group id the caller
cannot reach must contribute nothing, not widen the query. Both are resolved
against the caller's own ACL before they ever reach SQL, which is what makes the
resulting id list safe to interpolate into the pgvector query as a bound array.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from routers import rag as rag_router
from routers.rag import LibraryScope
from services import rag_service
from tests.unit.conftest import make_result


def _user(uid=7):
    return SimpleNamespace(id=uid)


# --- the dependency ----------------------------------------------------------

def test_the_scope_defaults_to_everything():
    scope = LibraryScope()
    assert scope.folder_ids is None
    assert scope.group_ids is None
    assert scope.include_subfolders is True


def test_handlers_take_the_scope_as_one_dependency():
    """Not three loose Query parameters.

    These handlers are called directly by the unit suite, and any parameter left
    at a ``Query(...)`` default arrives in the body as a Query OBJECT rather than
    None -- so ``if folder_ids:`` would be truthy and the filter would fire with
    a Query as its value. With three params that is three silent mines, which is
    exactly how the dashboard facet filter learned this.
    """
    import inspect

    for handler in (rag_router.list_documents, rag_router.search_documents_only):
        params = inspect.signature(handler).parameters
        assert "scope" in params, handler.__name__
        for leaked in ("folder_ids", "include_subfolders", "group_ids"):
            assert leaked not in params, f"{handler.__name__} takes {leaked} loose"


async def test_no_params_searches_every_group_the_caller_is_in(mock_db):
    with patch.object(rag_router, "get_user_group_ids", AsyncMock(return_value=[2, 5])):
        group_ids, folder_ids = await rag_router.resolve_scope(
            mock_db, 7, LibraryScope()
        )
    assert group_ids == [2, 5]
    assert folder_ids is None, "None means 'no folder filter', not 'no folders'"


async def test_a_group_filter_intersects_with_real_membership(mock_db):
    """Naming a group you are not in must not reach its documents."""
    mock_db.execute.return_value = make_result(scalars_all=[])
    with patch.object(rag_router, "get_user_group_ids", AsyncMock(return_value=[2, 5])):
        group_ids, _ = await rag_router.resolve_scope(
            mock_db, 7, LibraryScope(group_ids=[5, 999])
        )
    assert group_ids == [5]


async def test_the_folder_filter_is_resolved_against_the_callers_acl(mock_db):
    captured = {}

    async def fake_resolve(db, folder_ids, include_subfolders, visible_clause):
        captured.update(
            folder_ids=folder_ids,
            include_subfolders=include_subfolders,
            has_acl=visible_clause is not None,
        )
        return [3, 4]

    with patch.object(rag_router, "get_user_group_ids", AsyncMock(return_value=[2])), \
         patch.object(rag_router, "resolve_folder_scope", fake_resolve):
        _, folder_ids = await rag_router.resolve_scope(
            mock_db, 7, LibraryScope(folder_ids=[3], include_subfolders=True)
        )

    assert folder_ids == [3, 4]
    assert captured["folder_ids"] == [3]
    assert captured["has_acl"], "the expansion must be ACL-scoped, not a bare id walk"


# --- the SQL ----------------------------------------------------------------

def test_the_folder_filter_is_a_bound_array_never_interpolated():
    """This clause is assembled by string formatting, so an interpolated value
    here would be an injection rather than a shortcut."""
    import inspect

    src = inspect.getsource(rag_service._search_pages_by_embedding)
    assert 'folder_filter = "AND rd.folder_id = ANY(:folder_ids)"' in src
    assert "folder_ids" in src


async def test_an_empty_folder_scope_returns_nothing_not_everything(mock_db):
    """`if folder_ids:` here would treat "the folders you named hold nothing you
    can see" as "no filter at all" and hand back the whole library."""
    conds = rag_service._document_metadata_conditions(7, folder_ids=[])
    assert len(conds) == 2, "the scope predicate plus an (empty) folder predicate"

    sql = str(conds[1].compile(compile_kwargs={"literal_binds": True}))
    assert "folder_id IN (" in sql


async def test_a_populated_folder_scope_narrows_the_metadata_query():
    conds = rag_service._document_metadata_conditions(7, folder_ids=[3, 4])
    sql = str(conds[1].compile(compile_kwargs={"literal_binds": True}))
    assert "folder_id IN (3, 4)" in sql


async def test_search_forwards_the_folder_scope_to_the_vector_query(mock_db):
    captured = {}

    async def fake_search(*args, **kwargs):
        captured.update(kwargs)
        return []

    with patch.object(rag_router, "get_user_group_ids", AsyncMock(return_value=[2])), \
         patch.object(rag_router, "resolve_folder_scope", AsyncMock(return_value=[3])), \
         patch.object(rag_router, "search_documents", fake_search):
        await rag_router.search_documents_only(
            q="tubulin", limit=20, scope=LibraryScope(folder_ids=[3]),
            current_user=_user(), db=mock_db,
        )
    assert captured["folder_ids"] == [3]
    assert captured["group_ids"] == [2]
