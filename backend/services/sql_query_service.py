"""Read-only SQL query service — the agent's safe window into the database.

A single public entry point, :func:`run_query`, lets an authenticated caller run
**SELECT-only** queries against a whitelist of tables. A per-user ACL predicate is
injected **after** validation, so the caller only ever sees its own rows,
group-shared experiments/documents, and shared reference data (map_proteins) —
the same scope the user has in the UI.

This module is the SSOT for agent DB access. It was recovered from the pre-2ba9181
Gemini agent (``git show 2ba9181~1:backend/services/gemini_agent_service.py``) and
distilled down to just the SQL surface. The correctness notes below encode real,
previously-shipped bugs — do not "simplify" them away without tests that cover
aliases, self-joins, comma-joins and comment/subquery injection.

Pipeline order is load-bearing: **validate → whitelist-check → inject ACL predicate
→ execute**. The predicate is injected only after the model-supplied SQL has passed
validation, so the model never sees (and cannot craft around) the predicate text.
"""
from __future__ import annotations

import logging
import re
from typing import Any, NamedTuple, Optional

import sqlparse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SqlQueryError(ValueError):
    """A query was rejected by validation or failed to execute.

    Carries a message meant for the caller/agent (what to fix). The router turns
    it into an HTTP 400 so the agent can correct its SQL rather than retrying the
    identical query.
    """


# Tables the agent may read (security whitelist). map_proteins is shared reference
# data (no per-user column). Everything else is scoped below.
ALLOWED_SQL_TABLES = {
    "experiments", "images", "cell_crops", "map_proteins",
    "rag_documents", "rag_document_pages", "comparisons", "user_ratings",
}

class TableRef(NamedTuple):
    """A table as it appears in a FROM/JOIN clause: its real ``table`` name and the
    ``ref`` used to qualify its columns (its alias, or the name if unaliased).

    The two are kept distinct on purpose: the ACL predicate MUST be qualified with
    ``ref`` (Postgres drops the base name once aliased), while the scoping rule is
    chosen by ``table``. A plain ``(str, str)`` tuple let those two be swapped
    silently — the whole ACL correctness argument rests on never confusing them.
    """
    table: str
    ref: str


# Tables that carry their own ``user_id`` column and get the ACL predicate directly.
DIRECT_SCOPED = {"experiments", "rag_documents", "user_ratings", "comparisons"}

# Tables with no ``user_id`` of their own. They are reachable ONLY by JOINing their
# parent, and we INJECT the FK correlation ourselves (child.<fk> = parent.<pk>) so
# the parent's ACL predicate provably scopes the child. We never trust the model's
# ``ON`` clause: a bogus ``ON true`` or a join on shared reference data
# (``ON c.map_protein_id = e.map_protein_id``) would otherwise cartesian-product the
# child past its parent's filter and leak every user's rows. Chains are followed to
# a directly-scoped root (cell_crops -> images -> experiments).
#   child_table: (parent_table, child_fk_column, parent_pk_column)
INDIRECT_SCOPED = {
    "images": ("experiments", "experiment_id", "id"),
    "cell_crops": ("images", "image_id", "id"),
    "rag_document_pages": ("rag_documents", "document_id", "id"),
}

# Compact schema handed to the model in the query_database tool description. Without
# it the model cannot know column names and probes with trial-and-error queries.
# Vector columns (embedding*, umap_*) are omitted on purpose — large and not useful
# to SELECT. ⚠️ SSOT: the MCP tool description in mcp-server/maptalk_mcp/tools.yaml
# mirrors this text — update BOTH when columns change.
SQL_SCHEMA_HINT = (
    "experiments(id, name, description, status, map_protein_id, group_id, fasta_sequence, created_at, updated_at)\n"
    "images(id, experiment_id, map_protein_id, original_filename, width, height, z_slices, file_size, status, created_at, processed_at)\n"
    "cell_crops(id, image_id, map_protein_id, bbox_x, bbox_y, bbox_w, bbox_h, "
    "detection_confidence, bundleness_score, mean_intensity, skewness, kurtosis, excluded, created_at)\n"
    "map_proteins(id, name, full_name, uniprot_id, gene_name, organism, sequence_length)  -- shared reference data, no user filter\n"
    "comparisons(id, user_id, crop_a_id, crop_b_id, winner_id, response_time_ms, undone, timestamp)\n"
    "user_ratings(id, user_id, cell_crop_id, mu, sigma, comparison_count, created_at, updated_at)\n"
    "rag_documents(id, name, file_type, status, page_count, thread_id, created_at)  -- thread_id NULL = library, set = attachment of that chat thread\n"
    "rag_document_pages(id, document_id, page_number)  -- must JOIN rag_documents; page text is NOT in SQL, use search_documents"
)

# Result-size guardrail. Default modest so a broad SELECT doesn't flood context;
# cap so a hallucinated limit=100000 can't. A missing or unparseable limit falls
# back to the default; a zero/negative value is floored to 1 (a raw negative would
# otherwise reach Postgres as ``LIMIT -1``). The model may NOT supply its own LIMIT
# (rejected in _validate) — run_query always appends this clamped one, so the cap
# can't be bypassed by writing ``LIMIT 100000`` in the query.
DEFAULT_ROW_LIMIT = 100
MAX_ROW_LIMIT = 1000

# Keywords that terminate a FROM or WHERE clause. Shared by the FROM-clause table
# parser and the WHERE-clause predicate injector so the two never disagree on
# where a clause ends.
_FROM_CLAUSE_END = r'\b(?:WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|WINDOW|FETCH)\b'
_JOIN_SPLIT = r'\b(?:LEFT|RIGHT|FULL|INNER|CROSS|OUTER)?\s*JOIN\b'

_FORBIDDEN_KEYWORDS = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE",
                       "ALTER", "TRUNCATE", "GRANT", "REVOKE"]
# Functions that let a read-only query burn server resources or reach the filesystem.
_FORBIDDEN_FUNCTIONS = ["PG_SLEEP", "PG_READ_FILE", "PG_LS_DIR",
                        "DBLINK", "LO_IMPORT", "LO_EXPORT"]


def _clamp_limit(value: Any) -> int:
    """Coerce a caller-supplied row limit into ``[1, MAX_ROW_LIMIT]``."""
    try:
        return max(1, min(MAX_ROW_LIMIT, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_ROW_LIMIT


def _inject_where_predicate(query_str: str, predicate: str) -> str:
    """AND ``predicate`` into ``query_str``'s WHERE clause (wrapping the original
    conditions in parentheses to prevent operator-precedence bypasses), or add a new
    WHERE before the first trailing clause (GROUP BY / ORDER BY / HAVING / ...) if
    there is none. Shared by the ACL and FK-correlation injectors."""
    where_match = re.search(r'\bWHERE\b', query_str, re.IGNORECASE)
    if where_match:
        pos = where_match.end()
        rest_of_query = query_str[pos:]
        # Reuse the shared clause terminators (plus end-of-string) so the WHERE
        # branch and the FROM parser agree; otherwise `WHERE x=1 OFFSET 5` folds
        # OFFSET into the injected predicate and produces invalid SQL.
        end_match = re.search(rf'{_FROM_CLAUSE_END}|$', rest_of_query, re.IGNORECASE)
        where_conditions = rest_of_query[:end_match.start()].strip()
        after_where = rest_of_query[end_match.start():]
        return query_str[:pos] + f" {predicate} AND ({where_conditions}) {after_where}"

    term = re.search(_FROM_CLAUSE_END, query_str, re.IGNORECASE)
    if term:
        pos = term.start()
        return f"{query_str[:pos]}WHERE {predicate} {query_str[pos:]}"
    return f"{query_str.rstrip()} WHERE {predicate}"


def _inject_user_id_filter(
    query_str: str, table: str, ref: str, group_id: Optional[int] = None
) -> str:
    """Inject the per-user ownership filter into ``query_str`` for one table reference.

    ``table`` is the real table name (selects the scoping rule); ``ref`` is how the
    query refers to it — its alias if it has one, else the table name.

    For ``experiments`` and ``rag_documents`` the predicate widens to group-shared
    rows so SQL answers match what the same user sees in the UI (both tables have a
    ``group_id`` column); other tables have no group column and stay owner-scoped.
    ``rag_documents`` additionally gates the group term on ``thread_id IS NULL`` —
    mirroring ``document_scope``/``document_read_scope`` and the pgvector
    ``owner_clause`` in rag_service — so chat attachments never leak to the group.

    ⚠️ The predicate is qualified with ``ref``, NEVER the bare table name. Postgres
    drops the base table name from scope once an alias is present, so
    ``FROM experiments e ... WHERE experiments.user_id = ...`` fails with "invalid
    reference to FROM-clause entry"; it must say ``e.user_id``. On a self-join each
    alias gets its own predicate (this function is called once per reference).
    """
    if table == "rag_documents" and group_id is not None:
        predicate = (
            f"({ref}.user_id = :user_id OR "
            f"({ref}.thread_id IS NULL AND {ref}.group_id = :group_id))"
        )
    elif table == "experiments" and group_id is not None:
        predicate = f"({ref}.user_id = :user_id OR {ref}.group_id = :group_id)"
    else:
        predicate = f"{ref}.user_id = :user_id"
    return _inject_where_predicate(query_str, predicate)


def _table_references(query_str: str) -> list[TableRef]:
    """Return a :class:`TableRef` for every table in a FROM/JOIN clause.

    ``ref`` is the table's alias when it has one, else the table name — it is what
    the ACL/correlation predicates must be qualified with.

    Returned as a LIST, not a set, so a self-join (``comparisons c1 JOIN
    comparisons c2``) surfaces BOTH references and each is scoped independently;
    collapsing to a set would leave the second alias unscoped and leak rows through
    it.

    A ``FROM\\s+(\\w+)`` scan would see only the first table, so a comma-join
    (``FROM experiments, images``) would hide the second table from the whitelist
    check and the injectors. Splitting the whole FROM clause on JOIN boundaries and
    commas exposes every table; ON/USING predicates are stripped first so their
    identifiers and commas are never mistaken for tables.
    """
    refs: list[TableRef] = []
    for from_match in re.finditer(r'\bFROM\b', query_str, re.IGNORECASE):
        rest = query_str[from_match.end():]
        end = re.search(_FROM_CLAUSE_END, rest, re.IGNORECASE)
        clause = rest[:end.start()] if end else rest
        for segment in re.split(_JOIN_SPLIT, clause, flags=re.IGNORECASE):
            segment = re.split(r'\b(?:ON|USING)\b', segment, flags=re.IGNORECASE)[0]
            for part in segment.split(','):
                # table_name [AS] alias  -> capture the name and the optional alias
                m = re.match(
                    r'\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?',
                    part, re.IGNORECASE,
                )
                if m:
                    refs.append(TableRef(m.group(1).lower(), m.group(2) or m.group(1)))
    return refs


def _validate(query_str: str) -> list[TableRef]:
    """Enforce the SELECT-only, whitelist-only, correlated-join contract. Returns
    the table references on success; raises :class:`SqlQueryError` with a fixable
    message on any violation."""
    query_upper = query_str.upper()
    try:
        parsed = sqlparse.parse(query_str)
    except Exception as exc:  # a malformed query should read as such, not crash
        logger.warning("SQL parse raised: %s", exc)
        raise SqlQueryError(f"Parse error: {exc}") from exc
    if not parsed or parsed[0].get_type() != "SELECT":
        raise SqlQueryError("Only SELECT queries are allowed.")

    # Word-boundary matches throughout, so a *column* like created_at/updated_at or
    # a `selected` flag isn't rejected by a naive substring test (which would also
    # count the SELECT inside "SELECTED" and misfire the subquery check).
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf'\b{kw}\b', query_upper):
            raise SqlQueryError(f"Forbidden keyword: {kw}")
    for marker in ["--", "/*", "*/"]:
        if marker in query_str:
            raise SqlQueryError(f"SQL comments are not allowed (found {marker!r}).")
    for fn in _FORBIDDEN_FUNCTIONS:
        if re.search(rf'\b{fn}\b', query_upper):
            raise SqlQueryError(f"Forbidden function: {fn.lower()}")
    if ";" in query_str:
        raise SqlQueryError("Multiple statements are not allowed (no semicolons).")
    # UNION/INTERSECT/EXCEPT and CTEs each introduce a second SELECT, so they must
    # be checked BEFORE the >1-SELECT subquery rule below — otherwise they would be
    # swallowed by it with a misleading "Subqueries not allowed" message.
    if re.search(r'\b(UNION|INTERSECT|EXCEPT)\b', query_upper):
        raise SqlQueryError("UNION/INTERSECT/EXCEPT are not allowed.")
    if query_upper.lstrip().startswith("WITH"):
        raise SqlQueryError("WITH (CTE) queries are not allowed.")
    # >1 SELECT, or a SELECT inside parentheses, means a subquery — block both.
    if len(re.findall(r'\bSELECT\b', query_upper)) > 1 or re.search(r'\([^)]*\bSELECT\b', query_upper):
        raise SqlQueryError("Subqueries are not allowed.")
    # Uncorrelated joins (comma / CROSS / NATURAL) can cartesian-product a table
    # past its anchor's user filter — require explicit, correlated JOIN ... ON.
    if re.search(r'\bCROSS\s+JOIN\b', query_upper):
        raise SqlQueryError("CROSS JOIN is not allowed; use explicit JOIN ... ON.")
    if re.search(r'\bNATURAL\b', query_upper):
        raise SqlQueryError("NATURAL JOIN is not allowed; use explicit JOIN ... ON.")
    # The model may not set its own LIMIT/OFFSET: run_query appends a clamped LIMIT,
    # and honoring the model's would let ``LIMIT 100000`` bypass MAX_ROW_LIMIT.
    if re.search(r'\bLIMIT\b', query_upper):
        raise SqlQueryError("Do not add your own LIMIT — use the limit parameter instead.")
    if re.search(r'\bOFFSET\b', query_upper):
        raise SqlQueryError("OFFSET is not supported — use the limit parameter.")

    refs = _table_references(query_str)
    if not refs:
        raise SqlQueryError("Could not determine the target table(s).")
    # An explicit JOIN adds exactly one table; the base FROM has one. More table
    # references than (joins + 1) means a comma-join smuggled a table in.
    if len(refs) != len(re.findall(_JOIN_SPLIT, query_upper)) + 1:
        raise SqlQueryError("Comma joins are not allowed; use explicit JOIN ... ON.")
    denied = {r.table for r in refs} - ALLOWED_SQL_TABLES
    if denied:
        raise SqlQueryError(f"Access denied to table(s): {', '.join(sorted(denied))}")
    return refs


def _scoping_plan(refs: list[TableRef]) -> tuple[list[TableRef], list[str]]:
    """Plan how to scope a validated query, returning ``(acl_targets, correlations)``.

    ``acl_targets`` are the references to directly-scoped tables (they carry a
    ``user_id``) that get the per-user ACL predicate — this covers self-joins, since
    every alias is a separate reference.

    ``correlations`` are FK-equality predicate strings (``child.fk = parent.pk``)
    that we inject for every indirectly-scoped table so it is provably tied to a
    scoped parent, **regardless of what the model wrote in its ON clause**. Without
    this, ``experiments e JOIN cell_crops c ON true`` (or a join on shared
    reference data) would return every user's crops. Each indirect table therefore
    REQUIRES its direct FK parent to be present (cell_crops needs images, which needs
    experiments); a missing parent, or an ambiguous one (referenced more than once),
    is rejected rather than left unscoped.
    """
    by_table: dict[str, list[TableRef]] = {}
    for r in refs:
        by_table.setdefault(r.table, []).append(r)

    acl_targets = [r for r in refs if r.table in DIRECT_SCOPED]

    correlations: list[str] = []
    for r in refs:
        spec = INDIRECT_SCOPED.get(r.table)
        if spec is None:
            continue
        parent_table, child_fk, parent_pk = spec
        parents = by_table.get(parent_table, [])
        if not parents:
            raise SqlQueryError(
                f"Queries on {r.table} must JOIN {parent_table} (per-user access control)."
            )
        if len(parents) > 1:
            raise SqlQueryError(
                f"Cannot scope {r.table}: {parent_table} is referenced more than "
                f"once (ambiguous per-user access control)."
            )
        correlations.append(f"{r.ref}.{child_fk} = {parents[0].ref}.{parent_pk}")
    return acl_targets, correlations


async def run_query(
    sql: str,
    *,
    user_id: int,
    group_id: Optional[int],
    db: AsyncSession,
    limit: Any = None,
) -> dict[str, Any]:
    """Validate, ACL-scope and execute a read-only SELECT.

    Returns ``{"columns": [...], "rows": [{...}], "row_count": n}``. Raises
    :class:`SqlQueryError` (→ HTTP 400) for any validation or execution failure so
    the caller learns what to fix instead of silently getting nothing.
    """
    query_str = (sql or "").strip()
    if not query_str:
        raise SqlQueryError("Query is empty.")

    refs = _validate(query_str)
    acl_targets, correlations = _scoping_plan(refs)

    scoped = query_str
    # Inject FK correlations first so every indirect table is tied to a parent, then
    # the ACL predicates on the (directly-scoped) parents. Both AND into the WHERE.
    for predicate in correlations:
        scoped = _inject_where_predicate(scoped, predicate)
    for target in acl_targets:
        scoped = _inject_user_id_filter(scoped, target.table, target.ref, group_id)

    # The model can't supply its own LIMIT (rejected in _validate), so always append
    # the clamped one — the cap can't be bypassed by a large literal in the query.
    limit_val = _clamp_limit(limit)
    final_q = f"{scoped} LIMIT :limit_val"

    params: dict[str, Any] = {"user_id": user_id, "limit_val": limit_val}
    if group_id is not None:
        params["group_id"] = group_id

    try:
        result = await db.execute(text(final_q), params)
        rows = result.fetchall()
        cols = list(result.keys())
    except SQLAlchemyError as exc:
        # A query that passed validation can still fail (unknown column, type
        # mismatch). Roll back so the shared session isn't left in a failed state,
        # then surface a fixable message.
        logger.warning("Query execution error for user %s: %s", user_id, exc)
        try:
            await db.rollback()
        except SQLAlchemyError as rb:
            logger.error("CRITICAL: rollback failed after query error: %s", rb)
        raise SqlQueryError(f"Query error: {exc}") from exc

    return {
        "columns": cols,
        "rows": [dict(zip(cols, r)) for r in rows],
        "row_count": len(rows),
    }
