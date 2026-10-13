"""The UMAP is fitted once, globally, and filtered on read.

Coordinates live in ``cell_crops.umap_x/umap_y`` -- one projection, stored on the
row itself. That was safe only while every member of a scope read exactly the same
corpus, which multi-group membership ends: someone in groups A and B reads
``own ∪ A ∪ B`` while their colleague in A alone reads ``own ∪ A``, and both fits
write the same columns. Nothing raises; the plot just degrades.

So the fit corpus stops depending on who asked: the filter selects which points
are returned, never which are fitted.
"""
import inspect

from services import umap_service
from tests.unit.conftest import make_result


def test_the_fit_corpus_does_not_depend_on_the_caller():
    fn = umap_service.compute_crop_umap
    src = inspect.getsource(fn)
    assert "experiment_owner_filter" not in src, (
        "compute_crop_umap must not scope its fit to the caller -- two members with "
        "different group sets would overwrite each other's coordinates"
    )
    assert set(inspect.signature(fn).parameters) == {"db"}, (
        "compute_crop_umap takes a caller again, which is how a per-caller fit creeps back"
    )


async def test_the_fit_query_selects_every_embedded_crop(mock_db):
    """The emitted SQL, not the source text: no owner, no group, no experiment."""
    mock_db.execute.return_value = make_result(scalars_all=[])
    await umap_service.compute_crop_umap(mock_db)

    stmt = mock_db.execute.await_args.args[0]
    where = str(stmt.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert where == "cell_crops.embedding IS NOT NULL"


def test_refresh_helpers_take_no_caller_and_no_projection_key():
    # One projection, fitted over everything: nothing to key the refresh by.
    for fn in (
        umap_service.refresh_umap,
        umap_service.get_refresh_error,
        umap_service.clear_refresh_error,
    ):
        params = set(inspect.signature(fn).parameters)
        assert params == set(), f"{fn.__name__} takes {params}"


def test_read_endpoints_still_filter_by_acl():
    """The fit widens; what a caller is shown must not."""
    from routers import embeddings

    src = inspect.getsource(embeddings)
    assert src.count("experiment_owner_filter") >= 5, (
        "the read paths must keep scoping their result sets to the caller"
    )
