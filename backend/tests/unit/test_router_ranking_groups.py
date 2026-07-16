"""In-process unit tests for routers/ranking.py and routers/groups.py.

Route handlers are called directly with their FastAPI deps supplied as kwargs
(current_user, db). The DB is the AsyncMock ``mock_db`` fixture from conftest;
each test drives ``db.execute`` results via ``make_result`` (single result) or
``.side_effect=[...]`` (sequence of queries in handler order). Domain objects are
plain ``SimpleNamespace`` instances so attribute access works without real ORM.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers import ranking as r
from routers import groups as g
from tests.unit.conftest import make_result


def result_scalar_one(obj):
    """A mock Result whose ``scalar_one()`` returns ``obj`` (for reload queries)."""
    res = make_result(scalar=obj)
    res.scalar_one.return_value = obj
    return res


# =============================================================================
# Helpers
# =============================================================================


def fake_user(uid=1, email="a@b.cz", name="Alice"):
    return SimpleNamespace(id=uid, email=email, name=name,
                           role=SimpleNamespace(value="researcher"))


def crop(cid, image_id=10, mu=25.0, sigma=8.0, protein_name="PRC1"):
    """A CellCrop-like object with a loaded image+map_protein chain."""
    map_protein = SimpleNamespace(name=protein_name) if protein_name else None
    image = SimpleNamespace(id=image_id, map_protein=map_protein,
                            experiment_id=99)
    return SimpleNamespace(
        id=cid, image_id=image_id, image=image,
        bundleness_score=0.5, excluded=False,
    )


def rating(cid, mu=25.0, sigma=8.0, count=0):
    rt = SimpleNamespace(
        user_id=1, cell_crop_id=cid, mu=mu, sigma=sigma,
        comparison_count=count,
    )
    return rt


def comparison(cid=1, crop_a=1, crop_b=2, winner=1, undone=False,
               prev_w_mu=25.0, prev_w_sigma=8.0, prev_l_mu=25.0,
               prev_l_sigma=8.0):
    return SimpleNamespace(
        id=cid, user_id=1, crop_a_id=crop_a, crop_b_id=crop_b,
        winner_id=winner, undone=undone, timestamp=datetime.now(timezone.utc),
        prev_winner_mu=prev_w_mu, prev_winner_sigma=prev_w_sigma,
        prev_loser_mu=prev_l_mu, prev_loser_sigma=prev_l_sigma,
        response_time_ms=120,
    )


# =============================================================================
# ranking.get_or_create_rating
# =============================================================================


async def test_get_or_create_rating_existing(mock_db):
    existing = rating(5)
    mock_db.execute.return_value = make_result(scalar=existing)
    got = await r.get_or_create_rating(mock_db, user_id=1, cell_crop_id=5)
    assert got is existing
    mock_db.add.assert_not_called()


async def test_get_or_create_rating_creates_when_missing(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    got = await r.get_or_create_rating(mock_db, user_id=1, cell_crop_id=7)
    assert got.cell_crop_id == 7
    assert got.mu == r.settings.initial_mu
    assert got.sigma == r.settings.initial_sigma
    mock_db.add.assert_called_once()
    mock_db.flush.assert_awaited_once()


# =============================================================================
# ranking.get_next_pair
# =============================================================================


async def test_get_next_pair_no_sources_400(mock_db):
    # No experiment_id, ranking sources empty -> 400
    mock_db.execute.return_value = make_result(scalars_all=[])
    with pytest.raises(HTTPException) as e:
        await r.get_next_pair(experiment_id=None, current_user=fake_user(),
                              db=mock_db)
    assert e.value.status_code == 400
    assert "No experiments selected" in e.value.detail


async def test_get_next_pair_not_enough_crops_400(mock_db):
    # explicit experiment_id -> first query is the crops query, returns <2
    mock_db.execute.return_value = make_result(scalars_all=[crop(1)])
    with pytest.raises(HTTPException) as e:
        await r.get_next_pair(experiment_id=42, current_user=fake_user(),
                              db=mock_db)
    assert e.value.status_code == 400
    assert "Not enough cells" in e.value.detail


async def test_get_next_pair_success_explicit_experiment(mock_db):
    crops = [crop(1), crop(2), crop(3)]
    # Query order with explicit experiment_id:
    # 1) crops, 2) comparison count, 3) recent comparisons,
    # 4) batch-fetch existing ratings (empty here -> all created in-memory).
    mock_db.execute.side_effect = [
        make_result(scalars_all=crops),   # crops
        make_result(scalar=0),            # total_comparisons count
        make_result(scalars_all=[]),      # recent comparisons
        make_result(scalars_all=[]),      # existing ratings batch (none -> create all)
    ]
    resp = await r.get_next_pair(experiment_id=42, current_user=fake_user(),
                                 db=mock_db)
    assert resp.crop_a.id != resp.crop_b.id
    assert {resp.crop_a.id, resp.crop_b.id} <= {1, 2, 3}
    assert resp.comparison_number == 1
    assert resp.total_comparisons == 0
    mock_db.commit.assert_awaited()


async def test_get_next_pair_uses_ranking_sources(mock_db):
    crops = [crop(1), crop(2)]
    mock_db.execute.side_effect = [
        make_result(scalars_all=[42]),    # ranking sources -> exp ids
        make_result(scalars_all=crops),   # crops
        make_result(scalar=5),            # total_comparisons
        make_result(scalars_all=[comparison(crop_a=1, crop_b=2)]),  # recent
        make_result(scalars_all=[rating(1), rating(2)]),  # existing ratings batch
    ]
    resp = await r.get_next_pair(experiment_id=None, current_user=fake_user(),
                                 db=mock_db)
    assert {resp.crop_a.id, resp.crop_b.id} == {1, 2}
    assert resp.total_comparisons == 5


async def test_get_next_pair_skips_recently_compared(mock_db):
    # 3 crops with pair (1,2) recently compared -> a fresh pair (incl. crop 3)
    # must be chosen. With only 2 crops the algorithm falls back to "any pair",
    # so >=3 crops is required to actually prove exclusion works.
    crops = [crop(1), crop(2), crop(3)]
    mock_db.execute.side_effect = [
        make_result(scalars_all=crops),                              # crops
        make_result(scalar=0),                                       # total (exploration)
        make_result(scalars_all=[comparison(crop_a=1, crop_b=2)]),   # recent -> (1,2)
        make_result(scalars_all=[rating(1), rating(2), rating(3)]),  # existing ratings batch
    ]
    resp = await r.get_next_pair(experiment_id=42, current_user=fake_user(),
                                 db=mock_db)
    pair = {resp.crop_a.id, resp.crop_b.id}
    assert pair != {1, 2}   # the recently-compared pair is skipped...
    assert 3 in pair        # ...in favour of a fresh pair involving crop 3


async def test_get_next_pair_insufficient_items_error(mock_db):
    crops = [crop(1), crop(2)]
    mock_db.execute.side_effect = [
        make_result(scalars_all=crops),
        make_result(scalar=0),
        make_result(scalars_all=[]),
        make_result(scalars_all=[]),   # existing ratings batch
    ]
    with patch.object(r, "select_pair",
                      side_effect=r.InsufficientItemsError("boom")):
        with pytest.raises(HTTPException) as e:
            await r.get_next_pair(experiment_id=42, current_user=fake_user(),
                                  db=mock_db)
    assert e.value.status_code == 400
    assert "boom" in e.value.detail


# =============================================================================
# ranking.submit_comparison
# =============================================================================


def _comparison_payload(crop_a=1, crop_b=2, winner=1, rt=100):
    return r.ComparisonCreate(crop_a_id=crop_a, crop_b_id=crop_b,
                              winner_id=winner, response_time_ms=rt)


async def test_submit_comparison_invalid_winner_400(mock_db):
    payload = _comparison_payload(crop_a=1, crop_b=2, winner=99)
    with pytest.raises(HTTPException) as e:
        await r.submit_comparison(payload, current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 400
    assert "Winner must be one" in e.value.detail


async def test_submit_comparison_success_winner_is_a(mock_db):
    win_rating = rating(1, mu=25.0, sigma=8.0, count=2)
    lose_rating = rating(2, mu=25.0, sigma=8.0, count=2)
    # winner_id == crop_a_id (1): get_or_create winner(1) then loser(2)
    mock_db.execute.side_effect = [
        make_result(scalar=win_rating),
        make_result(scalar=lose_rating),
    ]

    created = {}

    def capture(obj):
        created["c"] = obj
    mock_db.add.side_effect = capture

    async def fake_refresh(obj):
        obj.id = 123
        obj.timestamp = datetime.now(timezone.utc)
    mock_db.refresh.side_effect = fake_refresh

    payload = _comparison_payload(crop_a=1, crop_b=2, winner=1)
    resp = await r.submit_comparison(payload, current_user=fake_user(),
                                     db=mock_db)
    assert resp.crop_a_id == 1 and resp.crop_b_id == 2 and resp.winner_id == 1
    # counts incremented and TrueSkill actually updated (not a no-op):
    assert win_rating.comparison_count == 3
    assert lose_rating.comparison_count == 3
    assert win_rating.mu > 25.0    # winner skill rises above the equal prior
    assert lose_rating.mu < 25.0   # loser skill falls below it
    assert win_rating.sigma < 8.0  # uncertainty shrinks for both
    assert lose_rating.sigma < 8.0
    mock_db.commit.assert_awaited_once()


async def test_submit_comparison_winner_is_b(mock_db):
    # winner_id == crop_b_id -> loser is crop_a
    win_rating = rating(2)
    lose_rating = rating(1)
    mock_db.execute.side_effect = [
        make_result(scalar=win_rating),
        make_result(scalar=lose_rating),
    ]

    async def fake_refresh(obj):
        obj.id = 5
        obj.timestamp = datetime.now(timezone.utc)
    mock_db.refresh.side_effect = fake_refresh

    payload = _comparison_payload(crop_a=1, crop_b=2, winner=2)
    resp = await r.submit_comparison(payload, current_user=fake_user(),
                                     db=mock_db)
    assert resp.winner_id == 2


# =============================================================================
# ranking.undo_last_comparison
# =============================================================================


async def test_undo_no_comparison_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as e:
        await r.undo_last_comparison(current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 404


async def test_undo_missing_rating_500(mock_db):
    comp = comparison(winner=1, crop_a=1, crop_b=2)
    mock_db.execute.side_effect = [
        make_result(scalar=comp),     # find comparison
        make_result(scalar=None),     # winner rating missing
        make_result(scalar=None),     # loser rating missing
    ]
    with pytest.raises(HTTPException) as e:
        await r.undo_last_comparison(current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 500
    assert "rating records missing" in e.value.detail


async def test_undo_success_restores_prev_values(mock_db):
    comp = comparison(winner=1, crop_a=1, crop_b=2,
                      prev_w_mu=20.0, prev_w_sigma=7.0,
                      prev_l_mu=22.0, prev_l_sigma=6.5)
    win_rating = rating(1, mu=30.0, sigma=5.0, count=3)
    lose_rating = rating(2, mu=15.0, sigma=5.0, count=3)
    mock_db.execute.side_effect = [
        make_result(scalar=comp),
        make_result(scalar=win_rating),
        make_result(scalar=lose_rating),
    ]
    resp = await r.undo_last_comparison(current_user=fake_user(), db=mock_db)
    assert comp.undone is True
    assert win_rating.mu == 20.0 and win_rating.sigma == 7.0
    assert lose_rating.mu == 22.0 and lose_rating.sigma == 6.5
    assert win_rating.comparison_count == 2
    assert lose_rating.comparison_count == 2
    assert resp.id == comp.id
    mock_db.commit.assert_awaited_once()


async def test_undo_legacy_comparison_no_prev_values(mock_db):
    # prev_* None -> legacy path: only decrement counts, mu/sigma untouched
    comp = comparison(winner=2, crop_a=1, crop_b=2,
                      prev_w_mu=None, prev_w_sigma=None,
                      prev_l_mu=None, prev_l_sigma=None)
    win_rating = rating(2, mu=30.0, sigma=5.0, count=0)  # count 0 -> no decrement
    lose_rating = rating(1, mu=15.0, sigma=5.0, count=1)
    mock_db.execute.side_effect = [
        make_result(scalar=comp),
        make_result(scalar=win_rating),
        make_result(scalar=lose_rating),
    ]
    await r.undo_last_comparison(current_user=fake_user(), db=mock_db)
    # mu/sigma unchanged (legacy)
    assert win_rating.mu == 30.0
    assert lose_rating.mu == 15.0
    # winner count stays 0 (guard), loser decremented to 0
    assert win_rating.comparison_count == 0
    assert lose_rating.comparison_count == 0


# =============================================================================
# ranking.get_leaderboard
# =============================================================================


async def test_leaderboard_with_items(mock_db):
    rt1 = rating(1, mu=30.0, sigma=4.0, count=5)
    rt1.cell_crop = crop(1)
    rt1.ordinal_score = 18.0
    rt2 = rating(2, mu=20.0, sigma=6.0, count=3)
    rt2.cell_crop = crop(2, protein_name=None)
    rt2.ordinal_score = 2.0
    mock_db.execute.side_effect = [
        make_result(scalar=2),                  # count
        make_result(scalars_all=[rt1, rt2]),    # paginated ratings
    ]
    resp = await r.get_leaderboard(experiment_id=None, page=1, per_page=500,
                                   current_user=fake_user(), db=mock_db)
    assert resp.total == 2
    assert len(resp.items) == 2
    assert resp.items[0].rank == 1 and resp.items[0].cell_crop_id == 1
    assert resp.items[0].map_protein_name == "PRC1"
    assert resp.items[1].map_protein_name is None


async def test_leaderboard_with_experiment_filter_and_paging(mock_db):
    rt = rating(3, mu=25.0, sigma=8.0, count=1)
    rt.cell_crop = crop(3)
    rt.ordinal_score = 1.0
    mock_db.execute.side_effect = [
        make_result(scalar=11),            # count
        make_result(scalars_all=[rt]),     # page 2
    ]
    resp = await r.get_leaderboard(experiment_id=42, page=2, per_page=10,
                                   current_user=fake_user(), db=mock_db)
    assert resp.total == 11
    assert resp.page == 2 and resp.per_page == 10
    # rank offset = (2-1)*10 + 0 + 1 = 11
    assert resp.items[0].rank == 11


# =============================================================================
# ranking.get_progress
# =============================================================================


async def test_progress_no_experiment(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=10),    # total comparisons
        make_result(scalar=6.0),   # avg sigma
        make_result(scalar=20),    # rated count
    ]
    resp = await r.get_progress(experiment_id=None, current_user=fake_user(),
                                db=mock_db)
    assert resp.total_comparisons == 10
    assert 0 <= resp.convergence_percent <= 100
    assert resp.phase == "exploration"  # 10 < 50 exploration_pairs


async def test_progress_with_experiment_exploitation_phase(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=100),   # total comparisons (> 50 -> exploitation)
        make_result(scalar=2.0),   # avg sigma == target -> 100% convergence
        make_result(scalar=15),    # rated count
    ]
    resp = await r.get_progress(experiment_id=42, current_user=fake_user(),
                                db=mock_db)
    assert resp.phase == "exploitation"
    assert resp.convergence_percent == 100.0
    assert resp.estimated_remaining == 0


async def test_progress_defaults_when_no_data(mock_db):
    # all scalar() return None -> defaults kick in
    mock_db.execute.side_effect = [
        make_result(scalar=None),
        make_result(scalar=None),
        make_result(scalar=None),
    ]
    resp = await r.get_progress(experiment_id=None, current_user=fake_user(),
                                db=mock_db)
    assert resp.total_comparisons == 0
    assert resp.average_sigma == round(r.settings.initial_sigma, 3)


# =============================================================================
# ranking.list_import_sources
# =============================================================================


async def test_list_import_sources(mock_db):
    exp1 = SimpleNamespace(id=1, name="Exp1", image_count=4, crop_count=20)
    exp2 = SimpleNamespace(id=2, name="Exp2", image_count=0, crop_count=0)
    mock_db.execute.side_effect = [
        make_result(fetchall=[exp1, exp2]),    # experiments .all()
        make_result(scalars_all=[1]),          # included source ids
    ]
    resp = await r.list_import_sources(current_user=fake_user(), db=mock_db)
    assert len(resp) == 2
    assert resp[0].experiment_id == 1 and resp[0].included is True
    assert resp[1].experiment_id == 2 and resp[1].included is False


# =============================================================================
# ranking.add_import_sources
# =============================================================================


async def test_add_import_sources_empty_400(mock_db):
    payload = r.ImportSourcesRequest(experiment_ids=[])
    with pytest.raises(HTTPException) as e:
        await r.add_import_sources(payload, current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 400


async def test_add_import_sources_invalid_ids_404(mock_db):
    # requested {1,2} but only {1} valid -> {2} invalid
    mock_db.execute.return_value = make_result(scalars_all=[1])
    payload = r.ImportSourcesRequest(experiment_ids=[1, 2])
    with pytest.raises(HTTPException) as e:
        await r.add_import_sources(payload, current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 404
    assert "not found" in e.value.detail


async def test_add_import_sources_creates_and_updates(mock_db):
    # exp 1 already exists (included False -> update), exp 2 is new
    existing_source = SimpleNamespace(user_id=1, experiment_id=1, included=False)
    stats = SimpleNamespace(image_count=7, crop_count=30)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[1, 2]),       # valid ids
        make_result(scalars_all=[1]),          # existing ids
        make_result(scalar=existing_source),   # fetch existing source (exp 1)
        make_result(),                         # stats query (.one())
    ]
    # the stats query uses .one(); make_result().one isn't configured, so patch it
    stats_result = make_result()
    stats_result.one.return_value = stats
    mock_db.execute.side_effect = [
        make_result(scalars_all=[1, 2]),
        make_result(scalars_all=[1]),
        make_result(scalar=existing_source),
        stats_result,
    ]
    payload = r.ImportSourcesRequest(experiment_ids=[1, 2])
    resp = await r.add_import_sources(payload, current_user=fake_user(),
                                      db=mock_db)
    assert existing_source.included is True
    assert resp.added_experiments == 2  # 1 updated + 1 created
    assert resp.total_images == 7
    assert resp.total_crops == 30
    mock_db.add.assert_called_once()  # only the new source added


async def test_add_import_sources_existing_already_included(mock_db):
    # existing source already included -> not counted
    existing_source = SimpleNamespace(user_id=1, experiment_id=1, included=True)
    stats = SimpleNamespace(image_count=1, crop_count=2)
    stats_result = make_result()
    stats_result.one.return_value = stats
    mock_db.execute.side_effect = [
        make_result(scalars_all=[1]),          # valid ids
        make_result(scalars_all=[1]),          # existing ids
        make_result(scalar=existing_source),   # fetch existing source
        stats_result,
    ]
    payload = r.ImportSourcesRequest(experiment_ids=[1])
    resp = await r.add_import_sources(payload, current_user=fake_user(),
                                      db=mock_db)
    assert resp.added_experiments == 0


# =============================================================================
# ranking.remove_import_source
# =============================================================================


async def test_remove_import_source_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as e:
        await r.remove_import_source(experiment_id=5, current_user=fake_user(),
                                     db=mock_db)
    assert e.value.status_code == 404


async def test_remove_import_source_soft_deletes(mock_db):
    source = SimpleNamespace(user_id=1, experiment_id=5, included=True)
    mock_db.execute.return_value = make_result(scalar=source)
    await r.remove_import_source(experiment_id=5, current_user=fake_user(),
                                 db=mock_db)
    assert source.included is False
    mock_db.commit.assert_awaited_once()


# =============================================================================
# groups helpers
# =============================================================================


_UNSET = object()


def group_obj(gid=1, name="Lab", created_by=1, members=None, creator=_UNSET):
    if creator is _UNSET:
        creator = SimpleNamespace(name="Owner")
    return SimpleNamespace(
        id=gid, name=name, description="desc",
        created_by_user_id=created_by, creator=creator,
        members=members if members is not None else [],
        created_at=datetime.now(timezone.utc),
    )


def member_obj(mid=1, user_id=1, role="admin", user=_UNSET):
    if user is _UNSET:
        user = SimpleNamespace(name="Alice", email="a@b.cz")
    return SimpleNamespace(
        id=mid, user_id=user_id, role=role, user=user,
        joined_at=datetime.now(timezone.utc),
    )


async def test_get_user_group_membership(mock_db):
    m = member_obj()
    mock_db.execute.return_value = make_result(scalar=m)
    got = await g.get_user_group_membership(mock_db, user_id=1)
    assert got is m


def test_build_group_response_unknown_creator():
    grp = group_obj(creator=None)
    resp = g.build_group_response(grp, member_count=3)
    assert resp.creator_name == "Unknown"
    assert resp.member_count == 3


def test_build_group_detail_response_unknown_user():
    m_no_user = member_obj(user=None)
    grp = group_obj(members=[m_no_user], creator=None)
    resp = g.build_group_detail_response(grp)
    assert resp.members[0].user_name == "Unknown"
    assert resp.members[0].user_email == ""
    assert resp.creator_name == "Unknown"
    assert resp.member_count == 1


# =============================================================================
# groups.create_group
# =============================================================================


async def test_create_group_already_in_group_409(mock_db):
    mock_db.execute.return_value = make_result(scalar=member_obj())
    payload = g.GroupCreate(name="New", description=None)
    with pytest.raises(HTTPException) as e:
        await g.create_group(payload, current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 409


async def test_create_group_success(mock_db):
    created_group = group_obj(members=[member_obj()])

    async def fake_flush():
        # simulate group.id being populated after flush
        pass

    mock_db.flush.side_effect = fake_flush
    mock_db.execute.side_effect = [
        make_result(scalar=None),               # membership check -> none
        make_result(rowcount=0),                # adopt_orphan_experiments
        result_scalar_one(created_group),       # reload group (scalar_one)
    ]
    payload = g.GroupCreate(name="New Lab", description="d")
    resp = await g.create_group(payload, current_user=fake_user(), db=mock_db)
    assert resp.id == created_group.id
    assert resp.name == created_group.name
    # group + membership added
    assert mock_db.add.call_count == 2
    mock_db.commit.assert_awaited_once()


async def test_create_group_adopts_pre_group_experiments(mock_db):
    # Experiments made before the creator had a group must join it, so every
    # member's readable corpus is identical — umap_service's group-wide refresh
    # key depends on that.
    created_group = group_obj(members=[member_obj()])
    # adopt_orphan_experiments is patched below, so it issues no execute() here.
    mock_db.execute.side_effect = [
        make_result(scalar=None),               # membership check -> none
        result_scalar_one(created_group),       # reload group
    ]
    with patch.object(g, "adopt_orphan_experiments",
                      new=AsyncMock(return_value=4)) as adopt:
        await g.create_group(
            g.GroupCreate(name="New Lab", description="d"),
            current_user=fake_user(), db=mock_db,
        )
    adopt.assert_awaited_once()
    # Adopted before the commit, or the UPDATE would be lost.
    assert mock_db.commit.await_count == 1


# =============================================================================
# groups.list_groups
# =============================================================================


async def test_list_groups(mock_db):
    grp1 = group_obj(gid=1, name="A")
    grp2 = group_obj(gid=2, name="B")
    result = make_result(fetchall=[(grp1, 3), (grp2, 1)])
    result.unique.return_value = result
    mock_db.execute.return_value = result
    resp = await g.list_groups(current_user=fake_user(), db=mock_db)
    assert resp.total == 2
    assert resp.items[0].member_count == 3
    assert resp.items[1].member_count == 1


# =============================================================================
# groups.get_my_group
# =============================================================================


async def test_get_my_group_no_membership(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    resp = await g.get_my_group(current_user=fake_user(), db=mock_db)
    assert resp.group is None
    assert resp.role is None


async def test_get_my_group_membership_but_group_gone(mock_db):
    m = member_obj(role="member")
    m.group_id = 1
    mock_db.execute.side_effect = [
        make_result(scalar=m),       # membership
        make_result(scalar=None),    # group reload -> None
    ]
    resp = await g.get_my_group(current_user=fake_user(), db=mock_db)
    assert resp.group is None
    assert resp.role is None


async def test_get_my_group_success(mock_db):
    m = member_obj(role="member")
    m.group_id = 1
    grp = group_obj(gid=1, members=[m])
    mock_db.execute.side_effect = [
        make_result(scalar=m),       # membership
        make_result(scalar=grp),     # group reload
    ]
    resp = await g.get_my_group(current_user=fake_user(), db=mock_db)
    assert resp.group is not None
    assert resp.group.id == 1
    assert resp.role == "member"


# =============================================================================
# groups.get_group
# =============================================================================


async def test_get_group_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as e:
        await g.get_group(group_id=1, current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 404


async def test_get_group_success(mock_db):
    grp = group_obj(gid=1, members=[member_obj()])
    mock_db.execute.return_value = make_result(scalar=grp)
    resp = await g.get_group(group_id=1, current_user=fake_user(), db=mock_db)
    assert resp.id == 1
    assert len(resp.members) == 1


# =============================================================================
# groups.update_group
# =============================================================================


async def test_update_group_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    payload = g.GroupUpdate(name="X")
    with pytest.raises(HTTPException) as e:
        await g.update_group(group_id=1, data=payload,
                             current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 404


async def test_update_group_not_creator_403(mock_db):
    grp = group_obj(gid=1, created_by=999)  # owned by someone else
    mock_db.execute.return_value = make_result(scalar=grp)
    payload = g.GroupUpdate(name="X")
    with pytest.raises(HTTPException) as e:
        await g.update_group(group_id=1, data=payload,
                             current_user=fake_user(uid=1), db=mock_db)
    assert e.value.status_code == 403


async def test_update_group_success(mock_db):
    grp = group_obj(gid=1, created_by=1, members=[member_obj()])
    reloaded = group_obj(gid=1, created_by=1, name="Renamed",
                         members=[member_obj()])
    mock_db.execute.side_effect = [
        make_result(scalar=grp),          # find group
        result_scalar_one(reloaded),      # reload group (scalar_one)
    ]
    payload = g.GroupUpdate(name="Renamed", description="newdesc")
    resp = await g.update_group(group_id=1, data=payload,
                                current_user=fake_user(uid=1), db=mock_db)
    # name/description applied on the original object
    assert grp.name == "Renamed"
    assert grp.description == "newdesc"
    assert resp.name == "Renamed"
    mock_db.commit.assert_awaited_once()


async def test_update_group_partial_no_fields(mock_db):
    # name and description both None -> no mutation, still reloads
    grp = group_obj(gid=1, created_by=1, name="Keep")
    reloaded = group_obj(gid=1, created_by=1, name="Keep")
    mock_db.execute.side_effect = [
        make_result(scalar=grp),
        result_scalar_one(reloaded),
    ]
    payload = g.GroupUpdate(name=None, description=None)
    resp = await g.update_group(group_id=1, data=payload,
                                current_user=fake_user(uid=1), db=mock_db)
    assert grp.name == "Keep"
    assert resp.name == "Keep"


# =============================================================================
# groups.delete_group
# =============================================================================


async def test_delete_group_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as e:
        await g.delete_group(group_id=1, current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 404


async def test_delete_group_not_creator_403(mock_db):
    grp = group_obj(gid=1, created_by=999)
    mock_db.execute.return_value = make_result(scalar=grp)
    with pytest.raises(HTTPException) as e:
        await g.delete_group(group_id=1, current_user=fake_user(uid=1),
                             db=mock_db)
    assert e.value.status_code == 403


async def test_delete_group_success(mock_db):
    grp = group_obj(gid=1, created_by=1)
    mock_db.execute.return_value = make_result(scalar=grp)
    await g.delete_group(group_id=1, current_user=fake_user(uid=1), db=mock_db)
    mock_db.delete.assert_awaited_once_with(grp)
    mock_db.commit.assert_awaited_once()


# =============================================================================
# groups.join_group
# =============================================================================


async def test_join_group_already_in_group_409(mock_db):
    mock_db.execute.return_value = make_result(scalar=member_obj())
    with pytest.raises(HTTPException) as e:
        await g.join_group(group_id=1, current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 409


async def test_join_group_group_not_found_404(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=None),   # membership check -> none
        make_result(scalar=None),   # group lookup -> none
    ]
    with pytest.raises(HTTPException) as e:
        await g.join_group(group_id=1, current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 404


async def test_join_group_success(mock_db):
    grp = group_obj(gid=1, members=[member_obj(user_id=2, role="member")])
    mock_db.execute.side_effect = [
        make_result(scalar=None),   # membership check
        make_result(scalar=grp),    # group lookup
        make_result(rowcount=0),    # adopt_orphan_experiments
        result_scalar_one(grp),     # reload group (scalar_one)
    ]
    resp = await g.join_group(group_id=1, current_user=fake_user(uid=2),
                              db=mock_db)
    assert resp.id == 1
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


async def test_join_group_adopts_pre_group_experiments(mock_db):
    # A user who created experiments before joining brings them into the group,
    # keeping every member's corpus identical (see refresh_scope_key).
    grp = group_obj(gid=1, members=[member_obj(user_id=2, role="member")])
    # adopt_orphan_experiments is patched below, so it issues no execute() here.
    mock_db.execute.side_effect = [
        make_result(scalar=None),   # membership check
        make_result(scalar=grp),    # group lookup
        result_scalar_one(grp),     # reload group
    ]
    with patch.object(g, "adopt_orphan_experiments",
                      new=AsyncMock(return_value=2)) as adopt:
        await g.join_group(group_id=1, current_user=fake_user(uid=2), db=mock_db)
    adopt.assert_awaited_once_with(mock_db, 2, 1)
    assert mock_db.commit.await_count == 1


async def test_join_group_integrity_error_409(mock_db):
    grp = group_obj(gid=1)
    mock_db.execute.side_effect = [
        make_result(scalar=None),   # membership check
        make_result(scalar=grp),    # group lookup
        make_result(rowcount=0),    # adopt_orphan_experiments
    ]
    mock_db.commit.side_effect = g.IntegrityError("x", "y", "z")
    with pytest.raises(HTTPException) as e:
        await g.join_group(group_id=1, current_user=fake_user(uid=2),
                           db=mock_db)
    assert e.value.status_code == 409
    mock_db.rollback.assert_awaited_once()


# =============================================================================
# groups.leave_group
# =============================================================================


async def test_leave_group_not_member_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as e:
        await g.leave_group(group_id=1, current_user=fake_user(), db=mock_db)
    assert e.value.status_code == 404


async def test_leave_group_regular_member(mock_db):
    membership = member_obj(user_id=2, role="member")
    grp = group_obj(gid=1, created_by=1)  # current user (2) is not creator
    mock_db.execute.side_effect = [
        make_result(scalar=membership),   # membership lookup
        make_result(scalar=grp),          # group lookup
    ]
    await g.leave_group(group_id=1, current_user=fake_user(uid=2), db=mock_db)
    mock_db.delete.assert_awaited_once_with(membership)
    mock_db.commit.assert_awaited_once()


async def test_leave_group_creator_transfers_ownership(mock_db):
    membership = member_obj(user_id=1, role="admin")
    grp = group_obj(gid=1, created_by=1)
    other = member_obj(mid=2, user_id=5, role="member")
    mock_db.execute.side_effect = [
        make_result(scalar=membership),   # membership lookup
        make_result(scalar=grp),          # group lookup (creator == user)
        make_result(scalar=other),        # other member found
    ]
    await g.leave_group(group_id=1, current_user=fake_user(uid=1), db=mock_db)
    assert grp.created_by_user_id == 5
    assert other.role == "admin"
    mock_db.delete.assert_awaited_once_with(membership)


async def test_leave_group_creator_last_member_deletes_group(mock_db):
    membership = member_obj(user_id=1, role="admin")
    grp = group_obj(gid=1, created_by=1)
    mock_db.execute.side_effect = [
        make_result(scalar=membership),   # membership lookup
        make_result(scalar=grp),          # group lookup
        make_result(scalar=None),         # no other member
    ]
    await g.leave_group(group_id=1, current_user=fake_user(uid=1), db=mock_db)
    # group deleted, returns early before deleting membership
    mock_db.delete.assert_awaited_once_with(grp)
    mock_db.commit.assert_awaited_once()


async def test_leave_group_group_lookup_none(mock_db):
    # membership exists but group lookup returns None (edge) -> just delete member
    membership = member_obj(user_id=2, role="member")
    mock_db.execute.side_effect = [
        make_result(scalar=membership),
        make_result(scalar=None),   # group not found
    ]
    await g.leave_group(group_id=1, current_user=fake_user(uid=2), db=mock_db)
    mock_db.delete.assert_awaited_once_with(membership)


# =============================================================================
# groups.kick_member
# =============================================================================


async def test_kick_member_group_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as e:
        await g.kick_member(group_id=1, user_id=2, current_user=fake_user(),
                            db=mock_db)
    assert e.value.status_code == 404


async def test_kick_member_not_creator_403(mock_db):
    grp = group_obj(gid=1, created_by=999)
    mock_db.execute.return_value = make_result(scalar=grp)
    with pytest.raises(HTTPException) as e:
        await g.kick_member(group_id=1, user_id=2, current_user=fake_user(uid=1),
                            db=mock_db)
    assert e.value.status_code == 403


async def test_kick_member_self_400(mock_db):
    grp = group_obj(gid=1, created_by=1)
    mock_db.execute.return_value = make_result(scalar=grp)
    with pytest.raises(HTTPException) as e:
        await g.kick_member(group_id=1, user_id=1, current_user=fake_user(uid=1),
                            db=mock_db)
    assert e.value.status_code == 400


async def test_kick_member_not_member_404(mock_db):
    grp = group_obj(gid=1, created_by=1)
    mock_db.execute.side_effect = [
        make_result(scalar=grp),     # group lookup
        make_result(scalar=None),    # membership lookup -> none
    ]
    with pytest.raises(HTTPException) as e:
        await g.kick_member(group_id=1, user_id=2, current_user=fake_user(uid=1),
                            db=mock_db)
    assert e.value.status_code == 404


async def test_kick_member_success(mock_db):
    grp = group_obj(gid=1, created_by=1)
    membership = member_obj(user_id=2, role="member")
    mock_db.execute.side_effect = [
        make_result(scalar=grp),
        make_result(scalar=membership),
    ]
    await g.kick_member(group_id=1, user_id=2, current_user=fake_user(uid=1),
                        db=mock_db)
    mock_db.delete.assert_awaited_once_with(membership)
    mock_db.commit.assert_awaited_once()
