"""Guards against list endpoints that silently return a prefix of the answer.

`GET /api/experiments` used to declare `limit: int = Query(50, ge=1, le=100)`
while every client -- all three dashboard pages and the MCP `list_experiments`
tool -- called it with no parameters at all. The default therefore applied
silently: no error, no warning, just a shorter array.

On 2026-08-06 that hid a whole microscope. The lab had 75 experiments; ordering
by `updated_at DESC` put the 38 "Airyscan calibrate" and 12 "3D SIM" ones first,
which is exactly 50, so all 25 plain "Airyscan" experiments fell off the end and
a colleague reported them as missing. `rag_documents` was in the same state at
79 rows.

The fix follows `list_images`/`list_fovs`, which already had it right: page only
when a caller asks to, and otherwise answer in full.
"""
import inspect

from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import routers.experiments as exp_r
import routers.rag as rag_r
from tests.unit.conftest import make_result


def _query_default(handler, param: str):
    """The value a caller gets when it omits `param` entirely."""
    default = inspect.signature(handler).parameters[param].default
    # FastAPI wraps it: Query(None) is a params.Query carrying `.default`.
    return getattr(default, "default", default)


def test_list_endpoints_do_not_truncate_unless_asked():
    """An omitted `limit` must mean "all of it", never "the newest 50".

    A capped default is indistinguishable from a complete answer at the call
    site, so the client cannot notice it is being lied to -- which is precisely
    how 25 experiments and 29 documents went missing while every test was green.
    """
    for handler in (exp_r.list_experiments, rag_r.list_documents):
        assert _query_default(handler, "limit") is None, (
            f"{handler.__name__} truncates by default; clients that omit "
            "`limit` would silently receive a prefix of their data"
        )


async def test_experiment_listing_orders_by_a_unique_tiebreaker(mock_db):
    """`skip`/`limit` paging is only correct over a total order.

    `updated_at` carries no uniqueness constraint, and experiments really are
    created in batches, so ties are expected rather than hypothetical. Under
    OFFSET/LIMIT, Postgres may order tied rows differently between the two
    queries -- which drops some rows from one page and repeats them on another.
    """
    mock_db.execute.return_value = SimpleNamespace(
        unique=lambda: SimpleNamespace(all=lambda: [])
    )
    with patch.object(exp_r, "get_user_group_ids", new=AsyncMock(return_value=[])):
        await exp_r.list_experiments(
            skip=0, limit=10,
            current_user=SimpleNamespace(id=1), db=mock_db,
        )

    stmt = mock_db.execute.await_args[0][0]
    # Stringify only the ORDER BY elements: rendering the whole statement would
    # also drag in the SELECT list, where "id" appears no matter what.
    ordering = [str(c) for c in stmt._order_by_clauses]
    assert any("experiments.id" in c for c in ordering), (
        f"ORDER BY {ordering} has no unique tiebreaker, so paging over it "
        "can skip and duplicate rows"
    )
