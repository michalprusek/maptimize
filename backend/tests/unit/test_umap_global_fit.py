"""The UMAP is fitted once, globally, and filtered on read.

Coordinates live in ``cell_crops.umap_x/umap_y`` -- one projection, stored on the
row itself. That was safe only while every member of a scope read exactly the same
corpus, which multi-group membership ends: someone in groups A and B reads
``own ∪ A ∪ B`` while their colleague in A alone reads ``own ∪ A``, and both fits
write the same columns. Nothing raises; the plot just degrades.

So the fit corpus stops depending on who asked. This is the rule the discriminant
projection already follows -- the filter selects which points are returned, never
which are fitted.
"""
import inspect

from services import umap_service
from services.umap_service import UmapType, refresh_scope_key


def test_the_fit_corpus_does_not_depend_on_the_caller():
    for fn in (umap_service.compute_crop_umap, umap_service.compute_fov_umap):
        src = inspect.getsource(fn)
        assert "experiment_owner_filter" not in src, (
            f"{fn.__name__} must not scope its fit to the caller -- two members with "
            "different group sets would overwrite each other's coordinates"
        )
        assert "user_id" not in inspect.signature(fn).parameters, (
            f"{fn.__name__} still takes a user, which is how a per-caller fit creeps back"
        )


def test_scope_key_no_longer_carries_a_user_or_a_group():
    assert refresh_scope_key(UmapType.CROPPED) == UmapType.CROPPED.value
    assert refresh_scope_key(UmapType.FOV) == UmapType.FOV.value


def test_refresh_helpers_take_only_the_projection_type():
    for fn in (
        umap_service.refresh_umap_scope,
        umap_service.get_refresh_error,
        umap_service.clear_refresh_error,
    ):
        params = set(inspect.signature(fn).parameters)
        assert params == {"umap_type"}, f"{fn.__name__} takes {params}"


def test_read_endpoints_still_filter_by_acl():
    """The fit widens; what a caller is shown must not."""
    from routers import embeddings

    src = inspect.getsource(embeddings)
    assert src.count("experiment_owner_filter") >= 5, (
        "the read paths must keep scoping their result sets to the caller"
    )
