"""Faceted-filter clause building for the dashboard UMAP.

One helper for all facets so they cannot drift apart. The semantics the UI
promises are: **OR within a facet, AND across facets** — "AeryScan or 3D SIM,
carrying MAP7 or Tau4R" — which falls out of building one clause per facet and
letting the caller AND them together in the WHERE.
"""
from typing import Optional, Sequence

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

# Reserved id meaning "this facet is not assigned on the row". Real ids come from
# SERIAL columns and are always >= 1, so 0 can never collide with one. Expressing
# "unassigned" as an id rather than a separate boolean query parameter keeps every
# facet a single repeatable `?x_id=` param, so adding a facet never adds a param
# shape.
UNASSIGNED_FACET_ID = 0


def facet_clause(
    column: ColumnElement, ids: Optional[Sequence[int]]
) -> Optional[ColumnElement]:
    """Build the WHERE clause for one facet, or None when the facet is inactive.

    ``ids`` is what the client sent for this facet. An empty or missing list
    means "no constraint" — NOT "match nothing" — because that is what an
    untouched filter control means. Including ``UNASSIGNED_FACET_ID`` widens the
    clause to also match rows where the column is NULL, which is the only way the
    PTM facet is usable at all before the lab has backfilled it.
    """
    if not ids:
        return None

    real_ids = [i for i in ids if i != UNASSIGNED_FACET_ID]
    clauses = []
    if real_ids:
        clauses.append(column.in_(real_ids))
    if UNASSIGNED_FACET_ID in ids:
        clauses.append(column.is_(None))

    if len(clauses) == 1:
        return clauses[0]
    return or_(*clauses)


def real_ids(ids: Optional[Sequence[int]]) -> list[int]:
    """The ids in a facet selection that refer to actual rows.

    Used for existence-checking a selection: the unassigned sentinel must never
    be looked up in the reference table, or every filter including "Unassigned"
    would 404.
    """
    return [i for i in (ids or []) if i != UNASSIGNED_FACET_ID]
