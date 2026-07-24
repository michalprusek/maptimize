"""Unit tests for services/sql_query_service.py — the read-only agent SQL window.

Covers the validation gate (SELECT-only, no injection/subquery/union/cte/comment,
whitelist, correlated-join requirement) and the per-user ACL predicate injection,
with special attention to the two historically-buggy shapes: ALIASED tables and
SELF-JOINS (each alias must be scoped independently, qualified by the alias).

No live DB: db.execute is an AsyncMock; we assert on the rewritten SQL text.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from services.sql_query_service import (
    SqlQueryError,
    _inject_user_id_filter,
    _references_to_scope,
    _table_references,
    _validate,
    run_query,
)


def _exec_result(rows, cols):
    r = MagicMock(name="Result")
    r.fetchall.return_value = rows
    r.keys.return_value = cols
    return r


def _capture_db(rows=None, cols=None):
    """A mock session whose execute records the rewritten SQL + params."""
    captured = {}

    async def _execute(clause, params):
        captured["sql"] = str(clause)
        captured["params"] = params
        return _exec_result(rows or [], cols or [])

    db = AsyncMock(name="AsyncSession")
    db.execute = AsyncMock(side_effect=_execute)
    db.rollback = AsyncMock()
    return db, captured


# ---------------------------------------------------------------------------
# FROM-clause parsing (_table_references)
# ---------------------------------------------------------------------------

def test_table_references_resolves_alias():
    assert _table_references("SELECT * FROM experiments e") == [("experiments", "e")]


def test_table_references_alias_with_as():
    assert _table_references("SELECT * FROM experiments AS e") == [("experiments", "e")]


def test_table_references_no_alias_reference_is_table_name():
    assert _table_references("SELECT * FROM user_ratings") == [("user_ratings", "user_ratings")]


def test_table_references_self_join_returns_both_aliases():
    refs = _table_references(
        "SELECT * FROM comparisons c1 JOIN comparisons c2 ON c1.winner_id = c2.crop_a_id"
    )
    assert refs == [("comparisons", "c1"), ("comparisons", "c2")]


def test_table_references_strips_on_clause_identifiers():
    refs = _table_references(
        "SELECT * FROM images i JOIN experiments e ON i.experiment_id = e.id"
    )
    assert refs == [("images", "i"), ("experiments", "e")]


# ---------------------------------------------------------------------------
# ACL predicate injection (_inject_user_id_filter) — qualified by the REFERENCE
# ---------------------------------------------------------------------------

def test_inject_uses_alias_not_table_name():
    out = _inject_user_id_filter(
        "SELECT * FROM experiments e WHERE e.status = 'active'", "experiments", "e", 3
    )
    # The predicate must be alias-qualified; the bare table name would be an
    # "invalid reference to FROM-clause entry" in Postgres once aliased.
    assert "e.user_id = :user_id" in out
    assert "e.group_id = :group_id" in out
    assert "experiments.user_id" not in out
    # the original WHERE condition is preserved, wrapped in parens
    assert "(e.status = 'active')" in out


def test_inject_experiments_without_group_is_owner_only():
    out = _inject_user_id_filter("SELECT * FROM experiments", "experiments", "experiments", None)
    assert out.endswith("WHERE experiments.user_id = :user_id")
    assert "group_id" not in out


def test_inject_rag_documents_gates_group_on_thread_null():
    out = _inject_user_id_filter("SELECT * FROM rag_documents d", "rag_documents", "d", 9)
    assert "d.thread_id IS NULL AND d.group_id = :group_id" in out


def test_inject_preserves_trailing_clause():
    out = _inject_user_id_filter(
        "SELECT * FROM experiments ORDER BY created_at", "experiments", "experiments", None
    )
    assert "WHERE experiments.user_id = :user_id ORDER BY created_at" in out


# ---------------------------------------------------------------------------
# Validation rejections
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DELETE FROM experiments",
    "UPDATE experiments SET name = 'x'",
    "INSERT INTO experiments (name) VALUES ('x')",
    "SELECT * FROM experiments; DROP TABLE images",
    "SELECT * FROM experiments WHERE id IN (SELECT id FROM images)",
    "SELECT (SELECT 1) FROM experiments",
    "SELECT * FROM experiments UNION SELECT * FROM images",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT * FROM experiments -- comment",
    "SELECT pg_sleep(10) FROM experiments",
    "SELECT * FROM experiments, images",           # comma join
    "SELECT * FROM experiments e CROSS JOIN images i",
    "SELECT * FROM experiments NATURAL JOIN images",
    "SELECT * FROM users",                          # not whitelisted
    "SELECT * FROM secret_table",
])
def test_validate_rejects(sql):
    with pytest.raises(SqlQueryError):
        _validate(sql)


def test_validate_comma_join_hides_no_table():
    # the second (non-whitelisted) table must not be smuggled past the whitelist
    with pytest.raises(SqlQueryError):
        _validate("SELECT * FROM experiments, users")


def test_validate_semicolon_without_forbidden_keyword():
    # a bare second statement (no DROP/etc.) is caught by the multi-statement guard
    with pytest.raises(SqlQueryError, match="Multiple statements"):
        _validate("SELECT id FROM experiments; SELECT 2")


def test_validate_no_from_clause():
    with pytest.raises(SqlQueryError, match="target table"):
        _validate("SELECT 1")


def test_union_reports_union_not_subquery():
    # UNION/CTE are checked before the >1-SELECT rule, so the message is specific
    with pytest.raises(SqlQueryError, match="UNION"):
        _validate("SELECT * FROM experiments UNION SELECT * FROM images")


def test_cte_reports_cte_not_subquery():
    with pytest.raises(SqlQueryError, match="CTE"):
        _validate("WITH x AS (SELECT 1) SELECT * FROM x")


def test_validate_parser_exception_is_wrapped(monkeypatch):
    import services.sql_query_service as sqs

    def _boom(_):
        raise RuntimeError("parser blew up")

    monkeypatch.setattr(sqs.sqlparse, "parse", _boom)
    with pytest.raises(SqlQueryError, match="Parse error"):
        _validate("SELECT id FROM experiments")


def test_validate_allows_created_at_column():
    # a column containing a forbidden keyword substring must stay reachable
    refs = _validate("SELECT created_at, updated_at FROM experiments WHERE status = 'active'")
    assert ("experiments", "experiments") in refs


def test_validate_allows_selected_like_column():
    # 'SELECT'-as-substring in a column must not trip the subquery check
    refs = _validate("SELECT excluded FROM cell_crops c JOIN images i ON c.image_id = i.id "
                     "JOIN experiments e ON i.experiment_id = e.id")
    names = {n for n, _ in refs}
    assert names == {"cell_crops", "images", "experiments"}


# ---------------------------------------------------------------------------
# Scope planning (_references_to_scope)
# ---------------------------------------------------------------------------

def test_indirect_table_requires_anchor():
    refs = _validate("SELECT * FROM images i JOIN map_proteins p ON i.map_protein_id = p.id")
    with pytest.raises(SqlQueryError):
        _references_to_scope(refs)


def test_indirect_table_scoped_via_anchor():
    refs = _validate("SELECT COUNT(*) FROM images i JOIN experiments e ON i.experiment_id = e.id")
    # only the experiments anchor gets a predicate (images has no user_id)
    assert _references_to_scope(refs) == [("experiments", "e")]


def test_self_join_scopes_every_alias():
    refs = _validate("SELECT * FROM comparisons c1 JOIN comparisons c2 ON c1.id = c2.id")
    assert _references_to_scope(refs) == [("comparisons", "c1"), ("comparisons", "c2")]


def test_map_proteins_only_is_unscoped_shared_data():
    refs = _validate("SELECT id, name FROM map_proteins")
    assert _references_to_scope(refs) == []


# ---------------------------------------------------------------------------
# run_query end to end (mocked execute)
# ---------------------------------------------------------------------------

async def test_run_query_scopes_group_and_limits():
    db, cap = _capture_db(rows=[(1, "a"), (2, "b")], cols=["id", "name"])
    out = await run_query(
        "SELECT id, name FROM experiments", user_id=7, group_id=3, db=db
    )
    assert out == {"columns": ["id", "name"], "rows": [
        {"id": 1, "name": "a"}, {"id": 2, "name": "b"}], "row_count": 2}
    assert "experiments.user_id = :user_id" in cap["sql"]
    assert "experiments.group_id = :group_id" in cap["sql"]
    assert "LIMIT :limit_val" in cap["sql"]
    assert cap["params"]["user_id"] == 7 and cap["params"]["group_id"] == 3
    assert cap["params"]["limit_val"] == 100  # default


async def test_run_query_respects_explicit_limit_and_clamps():
    db, cap = _capture_db(rows=[], cols=[])
    await run_query("SELECT id FROM experiments", user_id=1, group_id=None, db=db, limit=99999)
    assert cap["params"]["limit_val"] == 1000  # clamped to MAX_ROW_LIMIT


async def test_run_query_self_join_binds_both_aliases():
    db, cap = _capture_db(rows=[], cols=[])
    await run_query(
        "SELECT * FROM comparisons c1 JOIN comparisons c2 ON c1.winner_id = c2.crop_a_id "
        "WHERE c1.undone = false",
        user_id=5, group_id=None, db=db,
    )
    assert "c1.user_id = :user_id" in cap["sql"]
    assert "c2.user_id = :user_id" in cap["sql"]


async def test_run_query_empty_sql_rejected():
    db, _ = _capture_db()
    with pytest.raises(SqlQueryError):
        await run_query("   ", user_id=1, group_id=None, db=db)
    db.execute.assert_not_awaited()


async def test_run_query_execution_error_rolls_back():
    db = AsyncMock(name="AsyncSession")
    db.execute = AsyncMock(side_effect=SQLAlchemyError("boom"))
    db.rollback = AsyncMock()
    with pytest.raises(SqlQueryError):
        await run_query("SELECT id FROM experiments", user_id=1, group_id=None, db=db)
    db.rollback.assert_awaited_once()


async def test_run_query_still_raises_when_rollback_also_fails():
    # a failed rollback must be logged but must not mask the original query error
    db = AsyncMock(name="AsyncSession")
    db.execute = AsyncMock(side_effect=SQLAlchemyError("boom"))
    db.rollback = AsyncMock(side_effect=SQLAlchemyError("rollback boom"))
    with pytest.raises(SqlQueryError):
        await run_query("SELECT id FROM experiments", user_id=1, group_id=None, db=db)
