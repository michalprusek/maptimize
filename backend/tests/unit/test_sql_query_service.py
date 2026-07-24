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
    _scoping_plan,
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
    with pytest.raises(SqlQueryError, match="must JOIN experiments"):
        _scoping_plan(refs)


def test_indirect_table_scoped_via_anchor():
    refs = _validate("SELECT COUNT(*) FROM images i JOIN experiments e ON i.experiment_id = e.id")
    acl_targets, correlations = _scoping_plan(refs)
    # only the experiments anchor gets the ACL predicate (images has no user_id),
    # and we inject the FK correlation ourselves (not trusting the model's ON)
    assert acl_targets == [("experiments", "e")]
    assert correlations == ["i.experiment_id = e.id"]


def test_cell_crops_requires_full_chain_not_just_experiments():
    # cell_crops joined straight to experiments (skipping images) has no valid FK
    # path -> rejected; its parent is images, not experiments.
    refs = _validate("SELECT * FROM cell_crops c JOIN experiments e "
                     "ON c.map_protein_id = e.map_protein_id")
    with pytest.raises(SqlQueryError, match="must JOIN images"):
        _scoping_plan(refs)


def test_cell_crops_full_chain_injects_both_correlations():
    refs = _validate(
        "SELECT c.bundleness_score FROM cell_crops c JOIN images i ON c.image_id = i.id "
        "JOIN experiments e ON i.experiment_id = e.id"
    )
    acl_targets, correlations = _scoping_plan(refs)
    assert acl_targets == [("experiments", "e")]
    assert correlations == ["c.image_id = i.id", "i.experiment_id = e.id"]


def test_rag_document_pages_require_and_scope_via_rag_documents():
    with pytest.raises(SqlQueryError, match="must JOIN rag_documents"):
        _scoping_plan(_validate(
            "SELECT * FROM rag_document_pages p JOIN map_proteins m ON p.id = m.id"))
    acl_targets, correlations = _scoping_plan(_validate(
        "SELECT p.page_number FROM rag_document_pages p "
        "JOIN rag_documents d ON p.document_id = d.id"))
    assert acl_targets == [("rag_documents", "d")]
    assert correlations == ["p.document_id = d.id"]


def test_ambiguous_parent_is_rejected():
    # two experiments references -> which one scopes images? ambiguous, reject.
    refs = _validate(
        "SELECT * FROM images i JOIN experiments e1 ON i.experiment_id = e1.id "
        "JOIN experiments e2 ON i.map_protein_id = e2.map_protein_id"
    )
    with pytest.raises(SqlQueryError, match="ambiguous"):
        _scoping_plan(refs)


def test_self_join_scopes_every_alias():
    refs = _validate("SELECT * FROM comparisons c1 JOIN comparisons c2 ON c1.id = c2.id")
    acl_targets, correlations = _scoping_plan(refs)
    assert acl_targets == [("comparisons", "c1"), ("comparisons", "c2")]
    assert correlations == []


def test_map_proteins_only_is_unscoped_shared_data():
    refs = _validate("SELECT id, name FROM map_proteins")
    assert _scoping_plan(refs) == ([], [])


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


# ---------------------------------------------------------------------------
# Injected FK correlation — closes the "trust the model's ON" leak
# ---------------------------------------------------------------------------

async def test_run_query_injects_correlation_even_when_model_on_is_bogus():
    # The exploit: a JOIN whose ON does NOT correlate images to experiments (here a
    # tautology). Without the injected correlation, the experiments-only ACL predicate
    # would return EVERY user's images. We inject i.experiment_id = e.id regardless.
    db, cap = _capture_db(rows=[], cols=[])
    await run_query(
        "SELECT i.id FROM experiments e JOIN images i ON e.id = e.id",
        user_id=1, group_id=None, db=db,
    )
    assert "i.experiment_id = e.id" in cap["sql"]      # injected FK correlation
    assert "experiments.user_id = :user_id" not in cap["sql"]  # aliased, not bare name
    assert "e.user_id = :user_id" in cap["sql"]        # anchor ACL predicate


async def test_run_query_join_group_by_scopes_before_group_and_appends_limit():
    db, cap = _capture_db(rows=[], cols=[])
    await run_query(
        "SELECT status, COUNT(*) FROM images i JOIN experiments e "
        "ON i.experiment_id = e.id GROUP BY status",
        user_id=7, group_id=3, db=db,
    )
    sql = cap["sql"]
    assert "i.experiment_id = e.id" in sql
    assert "(e.user_id = :user_id OR e.group_id = :group_id)" in sql
    # ACL/correlation land in a WHERE BEFORE the GROUP BY, LIMIT is appended last
    assert sql.index(":user_id") < sql.index("GROUP BY")
    assert sql.rstrip().endswith("LIMIT :limit_val")


async def test_run_query_owner_only_omits_group_term_and_param():
    db, cap = _capture_db(rows=[], cols=[])
    await run_query("SELECT id FROM experiments", user_id=1, group_id=None, db=db)
    assert "experiments.user_id = :user_id" in cap["sql"]
    assert "group_id" not in cap["sql"]
    assert "group_id" not in cap["params"]


# ---------------------------------------------------------------------------
# Misc parsing/validation edge cases
# ---------------------------------------------------------------------------

def test_table_references_returns_named_tableref():
    ref = _table_references("SELECT * FROM experiments e")[0]
    assert ref.table == "experiments" and ref.ref == "e"


@pytest.mark.parametrize("sql", [
    "SELECT id FROM experiments LIMIT 5",
    "SELECT id FROM experiments OFFSET 10",
])
def test_validate_rejects_model_limit_offset(sql):
    with pytest.raises(SqlQueryError):
        _validate(sql)


def test_inject_preserves_where_and_trailing_order_by():
    out = _inject_user_id_filter(
        "SELECT * FROM experiments e WHERE e.status = 'active' ORDER BY e.created_at",
        "experiments", "e", None,
    )
    assert "WHERE e.user_id = :user_id AND (e.status = 'active') ORDER BY e.created_at" in out


def test_comma_inside_on_clause_is_not_a_comma_join():
    # a comma inside the ON predicate (IN (...)) must not be counted as a table sep
    refs = _validate(
        "SELECT * FROM experiments e JOIN images i "
        "ON e.id = i.experiment_id AND i.status IN ('a','b')"
    )
    assert {r.table for r in refs} == {"experiments", "images"}
