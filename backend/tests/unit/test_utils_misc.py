"""Unit tests for backend util modules.

Covers four pure/near-pure utility modules in-process (no live server):
  * utils.rate_limit  - Redis sliding-window rate limiter (Redis mocked)
  * utils.rating       - OpenSkill/Plackett-Luce rating math (pure)
  * utils.export_helpers - filename/figure/DataFrame/cleanup helpers (pure-ish)
  * utils.security     - bcrypt hashing + jose JWT + auth dependencies

Redis and external libs are mocked; pure logic uses real inputs.
"""
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as redis_async
from fastapi import HTTPException

import utils.groups as groups
import utils.rate_limit as rate_limit
import utils.rating as rating
import utils.export_helpers as export_helpers
import utils.security as security
from models.user import UserRole
from tests.unit.conftest import make_result


def _scalar_result(scalar):
    """Minimal mock SQLAlchemy Result exposing scalar_one_or_none."""
    result = MagicMock(name="Result")
    result.scalar_one_or_none.return_value = scalar
    return result


# =============================================================================
# utils.rate_limit
# =============================================================================

@pytest.fixture(autouse=True)
def _reset_redis_pool():
    """Reset the lazily-initialised global Redis pool around each test."""
    rate_limit._redis_pool = None
    yield
    rate_limit._redis_pool = None


def _make_pipeline(results):
    """Build a mock async pipeline usable as `async with r.pipeline(...)`."""
    pipe = MagicMock(name="pipeline")
    pipe.zremrangebyscore = MagicMock()
    pipe.zcard = MagicMock()
    pipe.execute = AsyncMock(return_value=results)
    ctx = MagicMock(name="pipeline_ctx")
    ctx.__aenter__ = AsyncMock(return_value=pipe)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, pipe


def _make_redis(pipeline_results, oldest=None):
    r = MagicMock(name="redis")
    ctx, pipe = _make_pipeline(pipeline_results)
    r.pipeline = MagicMock(return_value=ctx)
    r.zrange = AsyncMock(return_value=oldest if oldest is not None else [])
    r.zadd = AsyncMock()
    r.expire = AsyncMock()
    return r, pipe


async def test_get_redis_lazy_init_and_cache():
    fake = MagicMock(name="redis_client")
    with patch.object(rate_limit.redis, "from_url", return_value=fake) as from_url:
        r1 = await rate_limit.get_redis()
        r2 = await rate_limit.get_redis()
    assert r1 is fake and r2 is fake
    # Cached: from_url only called once despite two get_redis calls.
    from_url.assert_called_once()


async def test_check_rate_limit_under_limit_records_request():
    r, _ = _make_redis(pipeline_results=[None, 2])  # zcard -> 2 < max 5
    with patch.object(rate_limit, "get_redis", AsyncMock(return_value=r)):
        await rate_limit.check_rate_limit(
            user_id=1, key_prefix="chat", max_requests=5, window_seconds=60
        )
    r.zadd.assert_awaited_once()
    r.expire.assert_awaited_once()
    # TTL = window + 60
    assert r.expire.await_args.args[1] == 120


async def test_check_rate_limit_exceeded_with_oldest_entry():
    now = rate_limit.time.time()
    oldest_ts = now - 10  # entry 10s into a 60s window
    r, _ = _make_redis(pipeline_results=[None, 5], oldest=[("member", oldest_ts)])
    with patch.object(rate_limit, "get_redis", AsyncMock(return_value=r)):
        with pytest.raises(HTTPException) as exc:
            await rate_limit.check_rate_limit(
                user_id=2, key_prefix="upload", max_requests=5,
                window_seconds=60, error_message="Too many uploads",
            )
    assert exc.value.status_code == 429
    assert "Too many uploads" in exc.value.detail
    # Retry-After header present and positive.
    assert int(exc.value.headers["Retry-After"]) >= 1
    # No request recorded when limit hit.
    r.zadd.assert_not_awaited()


async def test_check_rate_limit_exceeded_no_oldest_entry():
    """When zcard says full but zrange returns empty -> retry_after = window."""
    r, _ = _make_redis(pipeline_results=[None, 10], oldest=[])
    with patch.object(rate_limit, "get_redis", AsyncMock(return_value=r)):
        with pytest.raises(HTTPException) as exc:
            await rate_limit.check_rate_limit(
                user_id=3, key_prefix="x", max_requests=1, window_seconds=30
            )
    assert exc.value.headers["Retry-After"] == "30"


async def test_check_rate_limit_fails_open_on_redis_error():
    """Redis errors must NOT raise -> request allowed (fail-open)."""
    bad = AsyncMock(side_effect=redis_async.RedisError("down"))
    with patch.object(rate_limit, "get_redis", bad):
        # Should not raise.
        await rate_limit.check_rate_limit(
            user_id=4, key_prefix="y", max_requests=1, window_seconds=10
        )


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (7200, "2 hours"),
        (3600, "1 hour"),
        (120, "2 minutes"),
        (60, "1 minute"),
        (45, "45 seconds"),
        (1, "1 second"),
    ],
)
def test_format_window(seconds, expected):
    assert rate_limit._format_window(seconds) == expected


# =============================================================================
# utils.rating
# =============================================================================

def test_update_ratings_winner_gains_loser_loses():
    (nw_mu, nw_sigma), (nl_mu, nl_sigma) = rating.update_ratings(
        winner_mu=25.0, winner_sigma=8.333, loser_mu=25.0, loser_sigma=8.333
    )
    # Winner mu rises, loser mu falls; sigma shrinks for both (info gained).
    assert nw_mu > 25.0
    assert nl_mu < 25.0
    assert nw_sigma < 8.333
    assert nl_sigma < 8.333


def test_update_ratings_returns_four_floats():
    result = rating.update_ratings(30.0, 5.0, 20.0, 5.0)
    assert isinstance(result, tuple) and len(result) == 2
    for pair in result:
        assert len(pair) == 2
        assert all(isinstance(v, float) for v in pair)


def test_calculate_convergence_full_when_at_or_below_target():
    assert rating.calculate_convergence(avg_sigma=2.0, initial_sigma=8.0, target_sigma=2.0) == 100.0
    assert rating.calculate_convergence(avg_sigma=1.0, initial_sigma=8.0, target_sigma=2.0) == 100.0


def test_calculate_convergence_midpoint():
    # halfway between initial (8) and target (2) -> 50%
    conv = rating.calculate_convergence(avg_sigma=5.0, initial_sigma=8.0, target_sigma=2.0)
    assert conv == pytest.approx(50.0)


def test_calculate_convergence_clamped_to_range():
    # avg_sigma above initial -> negative raw -> clamped to 0
    assert rating.calculate_convergence(avg_sigma=10.0, initial_sigma=8.0, target_sigma=2.0) == 0
    # avg_sigma between target and initial but very close to target -> high but <=100
    val = rating.calculate_convergence(avg_sigma=2.5, initial_sigma=8.0, target_sigma=2.0)
    assert 0 <= val <= 100


def test_estimate_remaining_zero_when_converged():
    assert rating.estimate_remaining_comparisons(
        avg_sigma=1.0, initial_sigma=8.0, target_sigma=2.0
    ) == 0


def test_estimate_remaining_uses_item_count_heuristic():
    # rated_items_count*5 = 100 (>50), full remaining ratio at start (no progress).
    rem = rating.estimate_remaining_comparisons(
        avg_sigma=8.0, initial_sigma=8.0, target_sigma=2.0,
        rated_items_count=20, total_comparisons=5,
    )
    # remaining_ratio == 1.0 -> estimated_total == 100
    assert rem == 100


def test_estimate_remaining_min_50_floor():
    # small item count -> max(50, count*5) -> 50
    rem = rating.estimate_remaining_comparisons(
        avg_sigma=8.0, initial_sigma=8.0, target_sigma=2.0,
        rated_items_count=2, total_comparisons=1,
    )
    assert rem == 50


def test_estimate_remaining_scales_with_progress():
    # halfway through sigma range -> ~half of estimated_total
    rem = rating.estimate_remaining_comparisons(
        avg_sigma=5.0, initial_sigma=8.0, target_sigma=2.0,
        rated_items_count=20, total_comparisons=10,
    )
    assert rem == pytest.approx(50, abs=1)


def test_estimate_remaining_fallback_no_comparisons():
    # total_comparisons == 0 -> fallback branch: estimated_total - 0
    rem = rating.estimate_remaining_comparisons(
        avg_sigma=8.0, initial_sigma=8.0, target_sigma=2.0,
        rated_items_count=0, total_comparisons=0,
    )
    # rated_items_count == 0 -> estimated_total == 200 fallback
    assert rem == 200


def test_estimate_remaining_fallback_invalid_sigma_range():
    # sigma_range == 0 -> fallback branch even with total_comparisons > 0
    rem = rating.estimate_remaining_comparisons(
        avg_sigma=8.0, initial_sigma=5.0, target_sigma=5.0,
        rated_items_count=10, total_comparisons=10,
    )
    # not <= target so proceeds; estimated_total=max(50,50)=50; fallback 50-10=40
    assert rem == 40


def test_estimate_remaining_never_negative():
    # fallback where total_comparisons exceeds estimated_total -> clamp to 0
    rem = rating.estimate_remaining_comparisons(
        avg_sigma=8.0, initial_sigma=5.0, target_sigma=5.0,
        rated_items_count=2, total_comparisons=999,
    )
    assert rem == 0


# =============================================================================
# utils.export_helpers
# =============================================================================

def test_sanitize_filename_replaces_non_alnum():
    assert export_helpers.sanitize_filename("PRC1 / test.csv") == "PRC1___test_csv"


def test_sanitize_filename_truncates():
    long = "a" * 100
    assert export_helpers.sanitize_filename(long, max_length=10) == "a" * 10


def test_sanitize_filename_keeps_alnum():
    assert export_helpers.sanitize_filename("Exp123") == "Exp123"


def test_generate_timestamped_filename_format():
    name = export_helpers.generate_timestamped_filename("My Exp", "csv")
    assert name.startswith("My_Exp_")
    assert name.endswith(".csv")
    # body between prefix and extension is a YYYYMMDD_HHMMSS timestamp
    ts = name[len("My_Exp_"):-len(".csv")]
    datetime.strptime(ts, "%Y%m%d_%H%M%S")  # raises if malformed


def test_fig_to_base64_produces_data_uri():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    uri = export_helpers.fig_to_base64(fig, dpi=50)
    assert uri.startswith("data:image/png;base64,")
    payload = uri.split(",", 1)[1]
    # decodes to a valid PNG (magic header).
    raw = base64.b64decode(payload)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_fig_to_base64_raises_without_matplotlib():
    with patch.object(export_helpers, "HAS_MATPLOTLIB", False):
        with pytest.raises(ImportError, match="matplotlib is required"):
            export_helpers.fig_to_base64(MagicMock())


def test_export_dataframe_csv(tmp_path):
    import pandas as pd
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    out = tmp_path / "data.csv"
    export_helpers.export_dataframe(df, out, format="csv")
    assert out.exists()
    text = out.read_text()
    assert "a,b" in text and "1,x" in text


def test_export_dataframe_default_is_csv(tmp_path):
    import pandas as pd
    df = pd.DataFrame({"a": [1]})
    out = tmp_path / "default.csv"
    export_helpers.export_dataframe(df, out)  # no format -> csv
    assert out.exists()


def test_export_dataframe_xlsx(tmp_path):
    import pandas as pd
    df = pd.DataFrame({"a": [1, 2]})
    out = tmp_path / "data.xlsx"
    export_helpers.export_dataframe(df, out, format="xlsx")
    assert out.exists()
    # openpyxl-readable round trip
    back = pd.read_excel(out, engine="openpyxl")
    assert list(back["a"]) == [1, 2]


def test_cleanup_old_files_missing_dir(tmp_path):
    missing = tmp_path / "nope"
    assert export_helpers.cleanup_old_files(missing) == 0


def test_cleanup_old_files_removes_old_keeps_new(tmp_path):
    import os
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("old")
    new.write_text("new")
    # backdate old file 48h
    old_time = datetime.now().timestamp() - (48 * 3600)
    os.utime(old, (old_time, old_time))
    removed = export_helpers.cleanup_old_files(tmp_path, max_age_hours=24, log_prefix="export")
    assert removed == 1
    assert not old.exists()
    assert new.exists()


def test_cleanup_old_files_handles_unlink_error(tmp_path):
    import os
    f = tmp_path / "old.txt"
    f.write_text("x")
    old_time = datetime.now().timestamp() - (48 * 3600)
    os.utime(f, (old_time, old_time))
    with patch.object(Path, "unlink", side_effect=OSError("locked")):
        removed = export_helpers.cleanup_old_files(tmp_path, max_age_hours=24)
    # unlink failed -> nothing counted as removed
    assert removed == 0
    assert f.exists()


def test_cleanup_old_files_skips_subdirectories(tmp_path):
    import os
    sub = tmp_path / "subdir"
    sub.mkdir()
    # backdate the directory itself; is_file() is False so it must be skipped
    old_time = datetime.now().timestamp() - (48 * 3600)
    os.utime(sub, (old_time, old_time))
    removed = export_helpers.cleanup_old_files(tmp_path, max_age_hours=24)
    assert removed == 0
    assert sub.exists()


def test_cleanup_old_files_recurses_into_per_user_subdirs(tmp_path):
    # Exports/chat images live in per-user subdirs; a flat glob("*") matched only
    # the directories and reaped nothing. rglob must reach the nested file.
    import os
    user_dir = tmp_path / "7"
    user_dir.mkdir()
    old_file = user_dir / "export_old.csv"
    old_file.write_text("a,b\n")
    old_time = datetime.now().timestamp() - (48 * 3600)
    os.utime(old_file, (old_time, old_time))
    removed = export_helpers.cleanup_old_files(tmp_path, max_age_hours=24)
    assert removed == 1
    assert not old_file.exists()


# =============================================================================
# utils.security  -  hashing
# =============================================================================

def test_hash_and_verify_password_roundtrip():
    pw = "S3cret-Password!"
    hashed = security.hash_password(pw)
    assert hashed != pw
    assert security.verify_password(pw, hashed) is True
    assert security.verify_password("wrong", hashed) is False


def test_hash_password_unique_salts():
    h1 = security.hash_password("same")
    h2 = security.hash_password("same")
    assert h1 != h2  # random salt
    assert security.verify_password("same", h1)
    assert security.verify_password("same", h2)


# =============================================================================
# utils.security  -  JWT create/decode
# =============================================================================

def test_create_and_decode_token_roundtrip():
    token = security.create_access_token(user_id=42, role="admin")
    payload = security.decode_token(token)
    assert payload is not None
    assert payload.sub == 42
    assert payload.role == UserRole.ADMIN
    assert payload.exp > datetime.now(timezone.utc)


def test_create_access_token_custom_expiry():
    token = security.create_access_token(
        user_id=1, role="viewer", expires_delta=timedelta(seconds=5)
    )
    payload = security.decode_token(token)
    assert payload is not None
    # expires within ~5s, well under the default 24h
    assert payload.exp < datetime.now(timezone.utc) + timedelta(minutes=1)


def test_decode_token_expired_returns_none():
    # negative delta -> already expired -> ExpiredSignatureError branch
    token = security.create_access_token(
        user_id=1, role="viewer", expires_delta=timedelta(seconds=-10)
    )
    assert security.decode_token(token) is None


def test_decode_token_invalid_signature_returns_none():
    token = security.create_access_token(user_id=1, role="viewer")
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    assert security.decode_token(tampered) is None


def test_decode_token_garbage_returns_none():
    assert security.decode_token("not.a.jwt") is None
    assert security.decode_token("") is None


def test_decode_token_claims_error_returns_none():
    """Force a JWTClaimsError from jose.jwt.decode -> None (warning branch)."""
    from jose.exceptions import JWTClaimsError
    with patch.object(security.jwt, "decode", side_effect=JWTClaimsError("bad claim")):
        assert security.decode_token("anything") is None


# =============================================================================
# utils.security  -  auth dependencies (async, DB mocked)
# =============================================================================

def _valid_payload(user_id=7, role=UserRole.RESEARCHER, exp_delta=timedelta(hours=1)):
    return security.TokenPayload(
        sub=user_id,
        exp=datetime.now(timezone.utc) + exp_delta,
        role=role,
    )


async def test_get_current_user_success(mock_db):
    user = MagicMock(name="User", id=7)
    mock_db.execute.return_value = _scalar_result(user)
    with patch.object(security, "decode_token", return_value=_valid_payload()):
        got = await security.get_current_user(MagicMock(), token="tok", db=mock_db)
    assert got is user


async def test_get_current_user_bad_token_raises_401(mock_db):
    with patch.object(security, "decode_token", return_value=None):
        with pytest.raises(HTTPException) as exc:
            await security.get_current_user(MagicMock(), token="bad", db=mock_db)
    assert exc.value.status_code == 401


async def test_get_current_user_expired_payload_raises_401(mock_db):
    expired = _valid_payload(exp_delta=timedelta(hours=-1))
    with patch.object(security, "decode_token", return_value=expired):
        with pytest.raises(HTTPException) as exc:
            await security.get_current_user(MagicMock(), token="tok", db=mock_db)
    assert exc.value.status_code == 401


async def test_get_current_user_unknown_user_raises_401(mock_db):
    mock_db.execute.return_value = _scalar_result(None)
    with patch.object(security, "decode_token", return_value=_valid_payload()):
        with pytest.raises(HTTPException) as exc:
            await security.get_current_user(MagicMock(), token="tok", db=mock_db)
    assert exc.value.status_code == 401


async def test_get_current_user_from_query_success(mock_db):
    user = MagicMock(name="User", id=7)
    mock_db.execute.return_value = _scalar_result(user)
    with patch.object(security, "decode_token", return_value=_valid_payload()):
        got = await security.get_current_user_from_query(MagicMock(), token="tok", db=mock_db)
    assert got is user


async def test_get_current_user_from_query_empty_token_raises_401(mock_db):
    with pytest.raises(HTTPException) as exc:
        await security.get_current_user_from_query(MagicMock(), token="", db=mock_db)
    assert exc.value.status_code == 401


async def test_get_current_user_from_query_bad_token_raises_401(mock_db):
    with patch.object(security, "decode_token", return_value=None):
        with pytest.raises(HTTPException) as exc:
            await security.get_current_user_from_query(MagicMock(), token="bad", db=mock_db)
    assert exc.value.status_code == 401


async def test_get_current_user_from_query_expired_raises_401(mock_db):
    expired = _valid_payload(exp_delta=timedelta(hours=-1))
    with patch.object(security, "decode_token", return_value=expired):
        with pytest.raises(HTTPException) as exc:
            await security.get_current_user_from_query(MagicMock(), token="tok", db=mock_db)
    assert exc.value.status_code == 401


async def test_get_current_user_from_query_unknown_user_raises_401(mock_db):
    mock_db.execute.return_value = _scalar_result(None)
    with patch.object(security, "decode_token", return_value=_valid_payload()):
        with pytest.raises(HTTPException) as exc:
            await security.get_current_user_from_query(MagicMock(), token="tok", db=mock_db)
    assert exc.value.status_code == 401


async def test_get_current_user_pat_success(mock_db):
    """A valid personal access token authenticates and marks the principal 'pat'."""
    mtok = MagicMock(user_id=7, last_used_at=None)
    mock_db.execute.return_value = _scalar_result(mtok)
    mock_db.get.return_value = MagicMock(id=7)
    req = MagicMock()
    got = await security.get_current_user(req, token="mtk_pat_valid", db=mock_db)
    assert got.id == 7
    assert req.state.principal_kind == "pat"


async def test_get_current_user_pat_unknown_or_revoked_raises_401(mock_db):
    mock_db.execute.return_value = _scalar_result(None)  # no active token matches
    with pytest.raises(HTTPException) as exc:
        await security.get_current_user(MagicMock(), token="mtk_pat_bad", db=mock_db)
    assert exc.value.status_code == 401


async def test_require_interactive_user_rejects_pat():
    req = MagicMock()
    req.state.principal_kind = "pat"
    with pytest.raises(HTTPException) as exc:
        await security.require_interactive_user(req, user=MagicMock())
    assert exc.value.status_code == 403


async def test_require_interactive_user_allows_jwt():
    req = MagicMock()
    req.state.principal_kind = "jwt"
    user = MagicMock()
    assert await security.require_interactive_user(req, user=user) is user


async def test_get_current_admin_allows_admin():
    admin = MagicMock(name="User")
    admin.role = UserRole.ADMIN
    got = await security.get_current_admin(current_user=admin)
    assert got is admin


async def test_get_current_admin_rejects_non_admin():
    viewer = MagicMock(name="User")
    viewer.role = UserRole.VIEWER
    with pytest.raises(HTTPException) as exc:
        await security.get_current_admin(current_user=viewer)
    assert exc.value.status_code == 403


# =============================================================================
# utils.groups - experiment access filter + group adoption
# =============================================================================

async def test_get_user_group_id_returns_none_when_ungrouped(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    assert await groups.get_user_group_id(1, mock_db) is None


async def test_get_user_group_id_returns_group(mock_db):
    mock_db.execute.return_value = make_result(scalar=7)
    assert await groups.get_user_group_id(1, mock_db) == 7


def test_experiment_owner_filter_ungrouped_is_owner_only():
    # No group -> the filter must not widen beyond the owner.
    sql = str(groups.experiment_owner_filter(5, None).compile(
        compile_kwargs={"literal_binds": True}))
    assert "experiments.user_id = 5" in sql
    assert "group_id" not in sql


def test_experiment_owner_filter_grouped_is_owner_or_group():
    sql = str(groups.experiment_owner_filter(5, 2).compile(
        compile_kwargs={"literal_binds": True}))
    assert "experiments.user_id = 5" in sql
    assert "experiments.group_id = 2" in sql
    assert " OR " in sql  # widening, not narrowing


async def test_adopt_orphan_experiments_claims_only_groupless_rows(mock_db):
    mock_db.execute.return_value = make_result(rowcount=3)
    adopted = await groups.adopt_orphan_experiments(mock_db, user_id=5, group_id=2)
    assert adopted == 3

    stmt = str(mock_db.execute.call_args.args[0].compile(
        compile_kwargs={"literal_binds": True}))
    # Only this user's rows, and only the ones with no group — never reassign an
    # experiment that already belongs to a group.
    assert "UPDATE experiments" in stmt
    assert "SET group_id=2" in stmt.replace(" ", "").replace("SETgroup_id=2", "SET group_id=2")
    assert "experiments.user_id = 5" in stmt
    assert "experiments.group_id IS NULL" in stmt


async def test_adopt_orphan_experiments_returns_zero_when_nothing_to_adopt(mock_db):
    mock_db.execute.return_value = make_result(rowcount=0)
    assert await groups.adopt_orphan_experiments(mock_db, 5, 2) == 0
