"""Guards against list endpoints that silently return a prefix of the answer.

`GET /api/experiments` used to declare `limit: int = Query(50, ge=1, le=100)`
while every client -- all three dashboard pages and the MCP `list_experiments`
tool -- called it with no parameters at all. The default therefore applied
silently: no error, no warning, just a shorter array.

On 2026-08-06 that hid a whole microscope. The lab had 75 experiments; ordering
by `updated_at DESC` put the 38 "Airyscan calibrate" and 12 "3D SIM" ones first,
which happened to total exactly 50 that day, so all 25 plain "Airyscan"
experiments fell off the end and a colleague reported them as missing.
`rag_documents` sat one page-load from the same fate at 79 rows. (Counts are as
measured on 2026-08-06 and will have drifted -- `updated_at` has `onupdate`, so
editing any one experiment reshuffles the boundary. What survives is the shape
of the failure, not the arithmetic.)

The fix follows `list_images`/`list_fovs`, which already had the limit right:
page only when a caller asks to, and otherwise answer in full.

These tests assert the SQL that comes out, not the signature that goes in. A
signature check is one indirection short of the mechanism: re-capping inside the
handler body (`limit = 50 if limit is None else limit`) restores the bug exactly
while leaving every declared default untouched.
"""
import inspect

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

import routers.experiments as exp_r
import routers.images as img_r
import routers.rag as rag_r
import services.rag_service as rag_s


def _query_default(handler, param: str):
    """The value a caller gets when it omits `param` entirely.

    Unit tests call handlers directly, so an omitted argument does NOT take the
    FastAPI default -- the `params.Query` object itself leaks into the body.
    Resolving it here is what lets these tests exercise the real default.
    """
    default = inspect.signature(handler).parameters[param].default
    return getattr(default, "default", default)


def _sql(stmt) -> str:
    """Render against Postgres specifically.

    The dialect matters: `.limit(None)` compiles to `LIMIT ALL` here but to
    `LIMIT -1` under SQLAlchemy's default dialect, and only the former is what
    production runs.
    """
    return str(stmt.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
    ))


def _order_by(stmt) -> str:
    """Just the ORDER BY tail of the compiled statement.

    Slicing first is the point. A bare `"experiments.id" in str(stmt)` passes on
    the *broken* query, because every column of the SELECT list is in there too
    -- the same trap CLAUDE.md records for the dedupe tests asserting on
    `str(stmt)` instead of `stmt.whereclause`.
    """
    sql = _sql(stmt)
    assert "ORDER BY" in sql, f"statement has no ORDER BY at all: {sql}"
    return sql.split("ORDER BY", 1)[1]


def _executed(mock_db):
    """The statement the handler actually handed to the session."""
    return mock_db.execute.await_args[0][0]


def _empty_rows():
    return SimpleNamespace(unique=lambda: SimpleNamespace(all=lambda: []))


async def _run_list_experiments(mock_db, **overrides):
    """Drive `list_experiments` the way a request with no query string would."""
    kwargs = dict(
        skip=_query_default(exp_r.list_experiments, "skip"),
        limit=_query_default(exp_r.list_experiments, "limit"),
    )
    kwargs.update(overrides)
    mock_db.execute.return_value = _empty_rows()
    with patch.object(exp_r, "get_user_group_ids", new=AsyncMock(return_value=[])):
        await exp_r.list_experiments(
            current_user=SimpleNamespace(id=1), db=mock_db, **kwargs
        )
    return _executed(mock_db)


async def _run_list_documents(mock_db, **overrides):
    """Drive the `list_documents` ROUTER as a request with no query string would.

    Going through the router rather than straight to the service is the whole
    point: reading the router's declared defaults and then handing them to
    `search_documents_metadata` skips over the router body, so a cap re-imposed
    there (`limit = 50 if limit is None else limit`) would sail past. That is
    the same one-indirection-short mistake this file exists to catch, and it was
    live in this helper until CodeRabbit flagged it on PR #57.
    """
    kwargs = dict(
        skip=_query_default(rag_r.list_documents, "skip"),
        limit=_query_default(rag_r.list_documents, "limit"),
        status_filter=None, name=None, doi=None, file_type=None,
        min_pages=None, max_pages=None,
    )
    kwargs.update(overrides)
    mock_db.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [])
    )
    # `response=None` keeps this to a single statement -- the listing -- so
    # `_executed` cannot pick up the X-Total-Count query by mistake.
    with patch.object(rag_r, "resolve_scope", new=AsyncMock(return_value=([], None))):
        await rag_r.list_documents(
            scope=rag_r.LibraryScope(), response=None,
            current_user=SimpleNamespace(id=1), db=mock_db, **kwargs,
        )
    return _executed(mock_db)


async def _run_search_documents(mock_db, **overrides):
    """Drive the metadata SERVICE directly, for assertions below the router."""
    kwargs = dict(skip=0, limit=None)
    kwargs.update(overrides)
    mock_db.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [])
    )
    await rag_s.search_documents_metadata(1, mock_db, **kwargs)
    return _executed(mock_db)


# --------------------------------------------------------------------------
# Truncation
# --------------------------------------------------------------------------

async def test_experiment_listing_is_unbounded_when_limit_is_omitted(mock_db):
    """Omitting `limit` must emit no cap -- not "the newest 50".

    Asserted on the compiled SQL rather than the signature, so that a cap
    re-introduced anywhere between the parameter and the query still fails.
    """
    sql = _sql(await _run_list_experiments(mock_db))
    assert "LIMIT ALL" in sql, f"experiments listing carries a cap: {sql}"


async def test_document_listing_is_unbounded_when_limit_is_omitted(mock_db):
    """Same guarantee on the documents path, through the real router body."""
    sql = _sql(await _run_list_documents(mock_db))
    assert "LIMIT ALL" in sql, f"documents listing carries a cap: {sql}"


async def test_document_service_default_is_unbounded_on_its_own(mock_db):
    """And again with the service's OWN default, which is a separate value.

    `list_documents` always forwards `limit=limit`, so the router's default
    masks the service's: reverting `search_documents_metadata` to `limit=50`
    truncates nothing today and the test above stays green. That makes this a
    guard against a future second caller, not a restatement -- the endpoint is
    one `limit=`-less call away from the original bug.
    """
    mock_db.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [])
    )
    await rag_s.search_documents_metadata(1, mock_db)
    sql = _sql(_executed(mock_db))
    assert "LIMIT ALL" in sql, f"documents service caps by default: {sql}"


@pytest.mark.parametrize("cap", [50, 100])
async def test_the_guard_would_notice_a_reintroduced_cap(mock_db, cap):
    """The assertion above is only worth as much as its ability to fail.

    Pins that an explicit `limit` really does render as a LIMIT, so the
    unbounded assertions cannot be passing for some unrelated reason (a renamed
    parameter, a statement that never got a LIMIT applied at all).
    """
    sql = _sql(await _run_list_experiments(mock_db, limit=cap))
    assert f"LIMIT {cap}" in sql and "LIMIT ALL" not in sql


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------
#
# `skip`/`limit` paging is only correct over a TOTAL order. None of these
# timestamp columns carries a uniqueness constraint, and under OFFSET/LIMIT
# Postgres is free to order tied rows differently between the two queries that
# make up two pages -- dropping some rows and repeating others.
#
# Measured on production 2026-08-06, ties are not evenly spread: `experiments
# .updated_at`, `rag_documents.created_at` and `images.created_at` had none at
# all (each row is written by its own request, so the transaction timestamps
# differ), while `cell_crops.created_at` had up to 6 rows sharing a value --
# crops are written by one batch loop inside a single transaction, and
# `func.now()` is the transaction clock. So the tiebreaker is defended by the
# schema guaranteeing nothing, not by how often ties happen to show up today.

async def test_experiment_listing_orders_by_a_unique_tiebreaker(mock_db):
    ordering = _order_by(await _run_list_experiments(mock_db))
    assert "experiments.id" in ordering, (
        f"ORDER BY {ordering} has no unique tiebreaker, so paging over it can "
        "skip and duplicate rows"
    )


async def test_document_listing_orders_by_a_unique_tiebreaker(mock_db):
    """The documents half is the one that actually pages.

    MCP `find_documents` declares `limit: 50` and pages with `skip`, so this
    ordering is under OFFSET/LIMIT on every connector call -- unlike the
    experiments listing, which every current caller fetches whole.
    """
    ordering = _order_by(await _run_search_documents(mock_db))
    assert "rag_documents.id" in ordering, (
        f"ORDER BY {ordering} has no unique tiebreaker, so paging over it can "
        "skip and duplicate rows"
    )


def test_paged_image_listings_order_by_a_unique_tiebreaker():
    """`list_fovs`/`list_images` take skip/limit and must be totally ordered too.

    This PR's rationale covers them: they were cited as the precedent for
    getting `limit` right, but they never had the ordering right. Asserted on
    source rather than by driving the handlers, which would need an experiment
    read-access check mocked for no extra signal.
    """
    source = inspect.getsource(img_r)
    for handler in ("list_fovs", "list_images"):
        body = source.split(f"async def {handler}(", 1)[1].split("\n@router", 1)[0]
        assert "Image.id.desc()" in body, (
            f"{handler} pages over a non-unique created_at with no tiebreaker"
        )


# --------------------------------------------------------------------------
# The pagination total
# --------------------------------------------------------------------------

async def test_experiment_listing_reports_the_total_it_scoped_over(mock_db):
    """`X-Total-Count` must exist, and must count the caller's own scope.

    Without the header a caller that passes `limit` holds a prefix it cannot
    tell from the whole answer -- the original bug, merely opt-in. Counting over
    a different filter than the listing would be worse than omitting it: the
    number would describe someone else's population.
    """
    headers = {}
    response = SimpleNamespace(headers=headers)
    mock_db.execute.return_value = _empty_rows()
    mock_db.scalar.return_value = 75

    with patch.object(exp_r, "get_user_group_ids", new=AsyncMock(return_value=[2])):
        await exp_r.list_experiments(
            skip=0, limit=None, response=response,
            current_user=SimpleNamespace(id=1), db=mock_db,
        )

    assert headers["X-Total-Count"] == "75"
    counted = mock_db.scalar.await_args[0][0]
    listed = _executed(mock_db)
    assert str(counted.whereclause) == str(listed.whereclause), (
        "the total is scoped differently than the rows it describes"
    )


async def test_document_count_filters_identically_to_the_listing(mock_db):
    """`X-Total-Count` must count exactly the rows the listing would return.

    This header is the connector's ONLY protection: `find_documents` still caps
    at 50 by design and tells the agent "Showing 50 of N -- call again with
    skip=50". A total that drifts above the real match count therefore invents
    documents the agent will page for and never find, which is the original bug
    wearing the opposite sign. Both queries are meant to share
    `_document_metadata_conditions`; this pins that they do.
    """
    filters = dict(name="tubulin", file_type="pdf", group_ids=[2], thread_id=None)

    listing = await _run_search_documents(mock_db, **filters)

    mock_db.execute.return_value = SimpleNamespace(scalar=lambda: 0)
    await rag_s.count_documents_metadata(1, mock_db, **filters)
    count = _executed(mock_db)

    assert str(listing.whereclause) == str(count.whereclause), (
        "the total counts a different set of rows than the listing returns"
    )
