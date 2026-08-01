"""Read access widened from one group to every group the user belongs to.

The rule "you see your own rows plus your groups'" is expressed in four places --
``experiment_owner_filter``, the ``document_*_scope`` builders, the raw-SQL
``owner_clause`` in ``rag_service``, and ``_inject_user_id_filter`` in
``sql_query_service``. All four are pinned here, because a widening that reaches
three of them is a data leak or an invisible-data bug depending on which one it
misses.

The empty-list case matters as much as the populated one: a user with no group
must degrade to owner-only, never to "no group term, therefore everything".
"""
import pytest

from models.rag_document import (
    document_dedupe_scope,
    document_read_scope,
    document_scope,
)
from services.rag_service import _owner_clause
from services.sql_query_service import _inject_user_id_filter
from utils.groups import experiment_owner_filter


def _sql(clause) -> str:
    """Render a SQLAlchemy clause with literals inlined, lowercased for matching."""
    return str(clause.compile(compile_kwargs={"literal_binds": True})).lower()


def _has_group_in(sql: str, *ids: int) -> bool:
    """True when the SQL contains an IN over exactly these group ids."""
    rendered = ", ".join(str(i) for i in ids)
    return f"in ({rendered})" in sql.replace(",".join(str(i) for i in ids), rendered)


# --- experiments -------------------------------------------------------------

def test_experiment_filter_covers_every_group_the_user_is_in():
    sql = _sql(experiment_owner_filter(7, [2, 5]))
    assert "user_id = 7" in sql
    assert _has_group_in(sql, 2, 5)


def test_experiment_filter_with_no_groups_is_owner_only():
    """An empty list must contribute no term at all -- fail closed."""
    sql = _sql(experiment_owner_filter(7, []))
    assert "user_id = 7" in sql
    assert "group_id" not in sql


# --- documents ---------------------------------------------------------------

@pytest.mark.parametrize("build", [
    pytest.param(lambda gids: document_read_scope(7, gids), id="read_scope"),
    pytest.param(lambda gids: document_scope(7, None, gids), id="scope"),
])
def test_document_scopes_take_a_group_list(build):
    assert _has_group_in(_sql(build([2, 5])), 2, 5)


@pytest.mark.parametrize("build", [
    pytest.param(lambda: document_read_scope(7, []), id="read_scope"),
    pytest.param(lambda: document_scope(7, None, []), id="scope"),
    pytest.param(lambda: document_dedupe_scope(7, None, []), id="dedupe_scope"),
])
def test_document_scopes_with_no_groups_are_owner_only(build):
    assert "group_id" not in _sql(build())


def test_attachments_never_widen_to_a_group():
    """The group term stays AND-gated on thread_id IS NULL, however many groups
    the caller has. Without the gate, a conversation's attachments become
    readable by the whole lab."""
    sql = _sql(document_scope(7, None, [2, 5]))
    assert "thread_id is null" in sql
    assert sql.index("thread_id is null") < sql.index("in (2, 5)")


def test_dedupe_scope_for_an_attachment_ignores_groups_entirely():
    """A chat attachment deduplicates only against the caller's own attachments in
    the same thread -- aliasing onto a group-shared library row would leave the
    thread pointing at a document its user cannot delete or reindex."""
    assert "group_id" not in _sql(document_dedupe_scope(7, 42, [2, 5]))


# --- raw-SQL mirrors ---------------------------------------------------------

def test_raw_owner_clause_uses_a_bound_array_not_interpolation():
    clause = _owner_clause([2, 5])
    assert "= any(:group_ids)" in clause.lower()
    assert "2" not in clause, "group ids must be bound, never interpolated"
    assert "thread_id is null" in clause.lower()


def test_raw_owner_clause_with_no_groups_is_owner_only():
    assert _owner_clause([]) == "rd.user_id = :user_id"


@pytest.mark.parametrize("table,expect_thread_gate", [
    ("experiments", False),
    ("rag_documents", True),
])
def test_sql_injection_predicate_widens_to_the_group_array(table, expect_thread_gate):
    out = _inject_user_id_filter(f"SELECT * FROM {table} e", table, "e", [2, 5]).lower()
    assert "e.group_id = any(:group_ids)" in out
    assert "e.user_id = :user_id" in out
    assert ("e.thread_id is null" in out) is expect_thread_gate


def test_sql_injection_predicate_qualifies_the_alias_not_the_table():
    """Postgres drops the base table name once an alias exists, so a predicate
    written as experiments.user_id fails with 'invalid reference to FROM-clause
    entry' on every aliased query."""
    out = _inject_user_id_filter("SELECT * FROM experiments e", "experiments", "e", [2])
    assert "experiments.user_id" not in out
    assert "e.user_id" in out


def test_sql_injection_predicate_with_no_groups_is_owner_only():
    out = _inject_user_id_filter("SELECT * FROM experiments e", "experiments", "e", [])
    assert "group_id" not in out


# --- what must be gone -------------------------------------------------------

def test_singular_helper_and_adoption_are_gone():
    """Leaving the singular get_user_group_id behind as a shim would let a call
    site keep the one-group semantics silently; adoption has no answer once a user
    can belong to several groups, so it was replaced by explicit assignment."""
    import utils.groups as g

    assert not hasattr(g, "get_user_group_id")
    assert hasattr(g, "get_user_group_ids"), "the plural helper is the replacement"
    assert not hasattr(g, "adopt_orphan_experiments")
    assert not hasattr(g, "adopt_orphan_documents")
