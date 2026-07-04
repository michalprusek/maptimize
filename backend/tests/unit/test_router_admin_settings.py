"""In-process unit tests for routers/admin.py and routers/settings.py.

Handlers are invoked directly with their dependencies supplied as kwargs (no
live server, no real DB, no GPU). The DB is an AsyncMock whose ``execute`` is
fed per-test mock Result objects. The two routers exercise distinct surfaces:

  * admin.py   - system stats, timeline, user list (filter/sort/paginate),
                 user detail/update/delete, password reset, conversations,
                 experiments, and GPU model management endpoints.
  * settings.py - get/update settings, profile update, password change,
                  avatar upload/get/delete.

These cover the success paths, the not-found / guard branches, and the broad
``except Exception`` -> 500 wrappers that the integration suite cannot reach.
"""
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from PIL import Image as PILImage

from routers import admin as admin_router
from routers import settings as settings_router
from models.user import UserRole
from models.user_settings import DisplayMode, Language, Theme
from schemas.admin import AdminUserUpdate
from schemas.settings import PasswordChange, ProfileUpdate, UserSettingsUpdate

from .conftest import make_result


# =============================================================================
# Helpers
# =============================================================================

def _iterable_result(rows, *, scalar=None, one=None, scalar_one=None,
                     scalars_all=None):
    """A mock SQLAlchemy Result that supports iteration AND the chained
    accessors. ``rows`` is what ``for row in result`` / dict-comprehensions
    iterate over; the others back the named accessors."""
    result = MagicMock(name="Result")
    result.__iter__.return_value = iter(rows)
    result.scalar.return_value = scalar
    result.one.return_value = one
    result.scalar_one_or_none.return_value = scalar_one
    scalars = MagicMock(name="ScalarResult")
    scalars.all.return_value = scalars_all if scalars_all is not None else []
    result.scalars.return_value = scalars
    return result


def _admin(user_id=1, email="admin@b.cz"):
    return SimpleNamespace(id=user_id, email=email, role=UserRole.ADMIN)


def _db_user(**kw):
    """A stand-in User row with the attributes the admin handlers read."""
    defaults = dict(
        id=2,
        email="bob@b.cz",
        name="Bob",
        role=UserRole.RESEARCHER,
        avatar_url=None,
        created_at=__import__("datetime").datetime(2026, 1, 1),
        last_login=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _detail_results():
    """The four execute() results consumed by get_user_detail after the
    get_user_or_404 lookup: experiments count, image row, doc row, chat count."""
    return [
        make_result(scalar=3),                       # experiment_count
        _iterable_result([], one=(5, 1024)),         # image_count, storage
        _iterable_result([], one=(2, 2048)),         # document_count, storage
        make_result(scalar=7),                       # chat_thread_count
    ]


# =============================================================================
# admin.py  -  get_system_stats
# =============================================================================

async def test_get_system_stats_success(mock_db):
    admin = _admin()
    mock_db.execute.side_effect = [
        # role_counts: iterated as (role, count) tuples
        _iterable_result([
            (UserRole.ADMIN, 1),
            (UserRole.RESEARCHER, 2),
            (UserRole.VIEWER, 3),
        ]),
        make_result(scalar=10),                # total_experiments
        _iterable_result([], one=(100, 5000)), # images count + storage
        _iterable_result([], one=(20, 3000)),  # documents count + storage
    ]
    res = await admin_router.get_system_stats(current_admin=admin, db=mock_db)
    assert res.total_users == 6
    assert res.admin_count == 1
    assert res.researcher_count == 2
    assert res.viewer_count == 3
    assert res.total_experiments == 10
    assert res.total_images == 100
    assert res.total_documents == 20
    assert res.total_storage_bytes == 8000
    assert res.images_storage_bytes == 5000
    assert res.documents_storage_bytes == 3000


async def test_get_system_stats_db_error_returns_500(mock_db):
    mock_db.execute.side_effect = RuntimeError("db down")
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_system_stats(current_admin=_admin(), db=mock_db)
    assert exc.value.status_code == 500


# =============================================================================
# admin.py  -  get_timeline_stats
# =============================================================================

async def test_get_timeline_stats_success(mock_db):
    # registrations + active rows; func.date(...) accessed as row.date / row.count
    reg_row = SimpleNamespace(date="2026-06-01", count=2)
    active_row = SimpleNamespace(date="2026-06-02", count=5)
    mock_db.execute.side_effect = [
        _iterable_result([reg_row]),
        _iterable_result([active_row]),
    ]
    res = await admin_router.get_timeline_stats(
        days=7, current_admin=_admin(), db=mock_db
    )
    assert res.period_days == 7
    # 7-day window inclusive of both endpoints -> 8 points
    assert len(res.data) == 8


async def test_get_timeline_stats_db_error_returns_500(mock_db):
    mock_db.execute.side_effect = RuntimeError("boom")
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_timeline_stats(
            days=30, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 500


# =============================================================================
# admin.py  -  list_users (filter / sort / paginate branches)
# =============================================================================

async def test_list_users_default_sort_desc(mock_db):
    user = _db_user(id=2)
    mock_db.execute.side_effect = [
        make_result(scalar=1),                          # count total
        _iterable_result([], scalars_all=[user]),       # page of users
        _iterable_result([(2, 4)]),                     # exp_counts
        _iterable_result([(2, 6, 9000)]),               # img_stats
        _iterable_result([(2, 1000)]),                  # doc_storage
    ]
    res = await admin_router.list_users(
        page=1, page_size=20, search=None, role=None,
        sort_by="created_at", sort_order="desc",
        current_admin=_admin(), db=mock_db,
    )
    assert res.total == 1
    assert res.total_pages == 1
    assert len(res.users) == 1
    item = res.users[0]
    assert item.experiment_count == 4
    assert item.image_count == 6
    assert item.storage_bytes == 10000  # 9000 img + 1000 doc


async def test_list_users_with_search_and_role_and_asc(mock_db):
    """Search filter + role filter + ascending sort branches, with the
    no-results-on-empty-user_ids path (skips the 3 aggregate queries)."""
    mock_db.execute.side_effect = [
        make_result(scalar=0),                # count total -> 0
        _iterable_result([], scalars_all=[]), # no users
    ]
    res = await admin_router.list_users(
        page=1, page_size=10, search="bo%b_", role=UserRole.RESEARCHER,
        sort_by="email", sort_order="asc",
        current_admin=_admin(), db=mock_db,
    )
    assert res.total == 0
    assert res.total_pages == 0
    assert res.users == []
    # Only 2 queries ran (count + page); aggregates skipped on empty user_ids.
    assert mock_db.execute.call_count == 2


async def test_list_users_invalid_sort_column_raises_400(mock_db):
    # count + page queries run before the allowlist check.
    mock_db.execute.side_effect = [
        make_result(scalar=0),
        _iterable_result([], scalars_all=[]),
    ]
    with pytest.raises(HTTPException) as exc:
        await admin_router.list_users(
            page=1, page_size=20, search=None, role=None,
            sort_by="password_hash", sort_order="desc",
            current_admin=_admin(), db=mock_db,
        )
    assert exc.value.status_code == 400


async def test_list_users_db_error_returns_500(mock_db):
    mock_db.execute.side_effect = RuntimeError("fail")
    with pytest.raises(HTTPException) as exc:
        await admin_router.list_users(
            page=1, page_size=20, search=None, role=None,
            sort_by="created_at", sort_order="desc",
            current_admin=_admin(), db=mock_db,
        )
    assert exc.value.status_code == 500


# =============================================================================
# admin.py  -  get_user_or_404 / get_user_detail
# =============================================================================

async def test_get_user_or_404_found(mock_db):
    user = _db_user()
    mock_db.execute.return_value = make_result(scalar=user)
    got = await admin_router.get_user_or_404(mock_db, 2, "admin@b.cz")
    assert got is user


async def test_get_user_or_404_missing_raises_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_user_or_404(mock_db, 99, "admin@b.cz")
    assert exc.value.status_code == 404


async def test_get_user_detail_success(mock_db):
    user = _db_user(id=2)
    mock_db.execute.side_effect = [
        make_result(scalar=user),  # get_user_or_404
        *_detail_results(),
    ]
    res = await admin_router.get_user_detail(
        user_id=2, current_admin=_admin(), db=mock_db
    )
    assert res.id == 2
    assert res.experiment_count == 3
    assert res.image_count == 5
    assert res.document_count == 2
    assert res.chat_thread_count == 7
    assert res.images_storage_bytes == 1024
    assert res.documents_storage_bytes == 2048
    assert res.total_storage_bytes == 3072


async def test_get_user_detail_not_found_propagates_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_user_detail(
            user_id=99, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 404


async def test_get_user_detail_db_error_returns_500(mock_db):
    user = _db_user(id=2)
    # lookup ok, then aggregate query blows up
    mock_db.execute.side_effect = [
        make_result(scalar=user),
        RuntimeError("agg fail"),
    ]
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_user_detail(
            user_id=2, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 500


# =============================================================================
# admin.py  -  update_user
# =============================================================================

async def test_update_user_changes_name_and_role(mock_db):
    user = _db_user(id=2, name="Old", role=UserRole.VIEWER)
    mock_db.execute.side_effect = [
        make_result(scalar=user),   # get_user_or_404 in update_user
        make_result(scalar=user),   # get_user_or_404 in nested get_user_detail
        *_detail_results(),
    ]
    payload = AdminUserUpdate(name="New Name", role=UserRole.RESEARCHER)
    res = await admin_router.update_user(
        user_id=2, update_data=payload, current_admin=_admin(), db=mock_db
    )
    assert user.name == "New Name"
    assert user.role == UserRole.RESEARCHER
    mock_db.commit.assert_awaited()
    assert res.id == 2


async def test_update_user_no_changes_skips_commit(mock_db):
    user = _db_user(id=2, name="Same", role=UserRole.RESEARCHER)
    mock_db.execute.side_effect = [
        make_result(scalar=user),   # get_user_or_404
        make_result(scalar=user),   # nested detail lookup
        *_detail_results(),
    ]
    # name/role equal to current -> no changes -> else branch
    payload = AdminUserUpdate(name="Same", role=UserRole.RESEARCHER)
    await admin_router.update_user(
        user_id=2, update_data=payload, current_admin=_admin(), db=mock_db
    )
    mock_db.commit.assert_not_awaited()


async def test_update_user_self_demotion_blocked(mock_db):
    admin = _admin(user_id=1)
    user = _db_user(id=1, role=UserRole.ADMIN)
    mock_db.execute.return_value = make_result(scalar=user)
    payload = AdminUserUpdate(role=UserRole.VIEWER)
    with pytest.raises(HTTPException) as exc:
        await admin_router.update_user(
            user_id=1, update_data=payload, current_admin=admin, db=mock_db
        )
    assert exc.value.status_code == 400


async def test_update_user_commit_failure_returns_500(mock_db):
    user = _db_user(id=2, name="Old", role=UserRole.VIEWER)
    mock_db.execute.return_value = make_result(scalar=user)
    mock_db.commit.side_effect = RuntimeError("commit boom")
    payload = AdminUserUpdate(name="Changed")
    with pytest.raises(HTTPException) as exc:
        await admin_router.update_user(
            user_id=2, update_data=payload, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 500
    mock_db.rollback.assert_awaited()


async def test_update_user_not_found_propagates_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    payload = AdminUserUpdate(name="Whatever")
    with pytest.raises(HTTPException) as exc:
        await admin_router.update_user(
            user_id=99, update_data=payload, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 404


# =============================================================================
# admin.py  -  delete_user
# =============================================================================

async def test_delete_user_success(mock_db):
    user = _db_user(id=2)
    mock_db.execute.side_effect = [
        make_result(scalar=user),  # get_user_or_404
        make_result(scalar=4),     # exp_count
    ]
    res = await admin_router.delete_user(
        user_id=2, current_admin=_admin(), db=mock_db
    )
    assert res is None
    mock_db.delete.assert_awaited_once_with(user)
    mock_db.commit.assert_awaited()


async def test_delete_user_self_blocked(mock_db):
    admin = _admin(user_id=1)
    with pytest.raises(HTTPException) as exc:
        await admin_router.delete_user(
            user_id=1, current_admin=admin, db=mock_db
        )
    assert exc.value.status_code == 400
    mock_db.execute.assert_not_called()


async def test_delete_user_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await admin_router.delete_user(
            user_id=99, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 404


async def test_delete_user_commit_failure_returns_500(mock_db):
    user = _db_user(id=2)
    mock_db.execute.side_effect = [
        make_result(scalar=user),
        make_result(scalar=4),
    ]
    mock_db.commit.side_effect = RuntimeError("commit fail")
    with pytest.raises(HTTPException) as exc:
        await admin_router.delete_user(
            user_id=2, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 500
    mock_db.rollback.assert_awaited()


# =============================================================================
# admin.py  -  reset_user_password
# =============================================================================

async def test_reset_user_password_success(mock_db):
    user = _db_user(id=2)
    mock_db.execute.return_value = make_result(scalar=user)
    res = await admin_router.reset_user_password(
        user_id=2, current_admin=_admin(), db=mock_db
    )
    assert res.new_password
    assert user.password_hash  # hash_password ran and set it
    mock_db.commit.assert_awaited()


async def test_reset_user_password_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await admin_router.reset_user_password(
            user_id=99, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 404


async def test_reset_user_password_commit_failure_returns_500(mock_db):
    user = _db_user(id=2)
    mock_db.execute.return_value = make_result(scalar=user)
    mock_db.commit.side_effect = RuntimeError("commit fail")
    with pytest.raises(HTTPException) as exc:
        await admin_router.reset_user_password(
            user_id=2, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 500
    mock_db.rollback.assert_awaited()


# =============================================================================
# admin.py  -  get_user_conversations
# =============================================================================

async def test_get_user_conversations_success(mock_db):
    user = _db_user(id=2)
    dt = __import__("datetime").datetime(2026, 1, 1)
    thread = SimpleNamespace(
        id=11, name="Chat", created_at=dt, updated_at=dt
    )
    mock_db.execute.side_effect = [
        make_result(scalar=user),         # get_user_or_404
        _iterable_result([(thread, 5)]),  # (ChatThread, message_count) rows
    ]
    res = await admin_router.get_user_conversations(
        user_id=2, current_admin=_admin(), db=mock_db
    )
    assert res.total == 1
    assert res.threads[0].id == 11
    assert res.threads[0].message_count == 5


async def test_get_user_conversations_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_user_conversations(
            user_id=99, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 404


async def test_get_user_conversations_db_error_returns_500(mock_db):
    user = _db_user(id=2)
    mock_db.execute.side_effect = [
        make_result(scalar=user),
        RuntimeError("query fail"),
    ]
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_user_conversations(
            user_id=2, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 500


# =============================================================================
# admin.py  -  get_conversation_messages
# =============================================================================

async def test_get_conversation_messages_success(mock_db):
    dt = __import__("datetime").datetime(2026, 1, 1)
    thread = SimpleNamespace(id=11, name="Chat")
    msg = SimpleNamespace(
        id=1, role="user", content="hi", created_at=dt,
        citations=["c"], image_refs=None,
    )
    mock_db.execute.side_effect = [
        make_result(scalar=thread),                   # thread lookup
        _iterable_result([], scalars_all=[msg]),      # messages
    ]
    res = await admin_router.get_conversation_messages(
        user_id=2, thread_id=11, current_admin=_admin(), db=mock_db
    )
    assert res.total == 1
    assert res.thread_name == "Chat"
    assert res.messages[0].has_citations is True
    assert res.messages[0].has_images is False


async def test_get_conversation_messages_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)  # no thread
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_conversation_messages(
            user_id=2, thread_id=99, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 404


async def test_get_conversation_messages_db_error_returns_500(mock_db):
    thread = SimpleNamespace(id=11, name="Chat")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        RuntimeError("msg fail"),
    ]
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_conversation_messages(
            user_id=2, thread_id=11, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 500


# =============================================================================
# admin.py  -  get_user_experiments
# =============================================================================

async def test_get_user_experiments_success(mock_db):
    user = _db_user(id=2)
    dt = __import__("datetime").datetime(2026, 1, 1)
    exp = SimpleNamespace(
        id=21, name="Exp", description="desc",
        status=SimpleNamespace(value="completed"),
        created_at=dt, updated_at=dt,
    )
    mock_db.execute.side_effect = [
        make_result(scalar=user),       # get_user_or_404
        _iterable_result([(exp, 9)]),   # (Experiment, image_count) rows
    ]
    res = await admin_router.get_user_experiments(
        user_id=2, current_admin=_admin(), db=mock_db
    )
    assert res.total == 1
    assert res.experiments[0].id == 21
    assert res.experiments[0].image_count == 9
    assert res.experiments[0].status == "completed"


async def test_get_user_experiments_not_found_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_user_experiments(
            user_id=99, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 404


async def test_get_user_experiments_db_error_returns_500(mock_db):
    user = _db_user(id=2)
    mock_db.execute.side_effect = [
        make_result(scalar=user),
        RuntimeError("exp fail"),
    ]
    with pytest.raises(HTTPException) as exc:
        await admin_router.get_user_experiments(
            user_id=2, current_admin=_admin(), db=mock_db
        )
    assert exc.value.status_code == 500


# =============================================================================
# admin.py  -  GPU management endpoints
# =============================================================================

async def test_get_gpu_status_returns_manager_status():
    manager = MagicMock()
    manager.get_status.return_value = {"loaded": []}
    with patch("ml.gpu_manager.get_gpu_manager", return_value=manager):
        res = await admin_router.get_gpu_status(current_admin=_admin())
    assert res == {"loaded": []}


async def test_unload_model_success():
    manager = MagicMock()
    manager.release.return_value = True
    with patch("ml.gpu_manager.get_gpu_manager", return_value=manager):
        res = await admin_router.unload_model(
            model_name="yolo", current_admin=_admin()
        )
    assert res["status"] == "ok"
    manager.release.assert_called_once_with("yolo")


async def test_unload_model_not_found_404():
    manager = MagicMock()
    manager.release.return_value = False
    with patch("ml.gpu_manager.get_gpu_manager", return_value=manager):
        with pytest.raises(HTTPException) as exc:
            await admin_router.unload_model(
                model_name="missing", current_admin=_admin()
            )
    assert exc.value.status_code == 404


async def test_unload_all_models_success():
    manager = MagicMock()
    manager.release_all.return_value = 3
    with patch("ml.gpu_manager.get_gpu_manager", return_value=manager):
        res = await admin_router.unload_all_models(current_admin=_admin())
    assert res["models_unloaded"] == 3


# =============================================================================
# settings.py  -  get_or_create_settings / get_settings_endpoint
# =============================================================================

def _settings_row():
    return SimpleNamespace(
        user_id=1,
        display_mode=DisplayMode.GRAYSCALE.value,
        theme=Theme.DARK.value,
        language=Language.EN.value,
    )


def _settings_user(user_id=1, **kw):
    defaults = dict(
        id=user_id, email="u@b.cz", name="User", avatar_url=None,
        password_hash="hashed", role=UserRole.RESEARCHER,
        created_at=__import__("datetime").datetime(2026, 1, 1),
        last_login=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _avatar_disk_path(avatar_url):
    """Reproduce the router's own (lstrip-based) avatar_url -> filesystem path
    so a file created here is the one the handler will actually find/unlink."""
    return settings_router.settings.upload_dir / avatar_url.lstrip("/uploads/")


async def test_get_settings_endpoint_existing(mock_db):
    row = _settings_row()
    mock_db.execute.return_value = make_result(scalar=row)
    res = await settings_router.get_settings_endpoint(
        current_user=_settings_user(), db=mock_db
    )
    assert res.theme == Theme.DARK
    assert res.language == Language.EN
    mock_db.add.assert_not_called()


async def test_get_settings_endpoint_creates_defaults(mock_db):
    # No existing settings -> create branch (db.add + commit + refresh)
    mock_db.execute.return_value = make_result(scalar=None)
    res = await settings_router.get_settings_endpoint(
        current_user=_settings_user(), db=mock_db
    )
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited()
    assert res.display_mode == DisplayMode.GRAYSCALE


# =============================================================================
# settings.py  -  update_settings (each field branch)
# =============================================================================

async def test_update_settings_all_fields(mock_db):
    row = _settings_row()
    mock_db.execute.return_value = make_result(scalar=row)
    updates = UserSettingsUpdate(
        display_mode=DisplayMode.FIRE,
        theme=Theme.LIGHT,
        language=Language.FR,
    )
    res = await settings_router.update_settings(
        updates=updates, current_user=_settings_user(), db=mock_db
    )
    assert row.display_mode == DisplayMode.FIRE.value
    assert row.theme == Theme.LIGHT.value
    assert row.language == Language.FR.value
    assert res.theme == Theme.LIGHT
    mock_db.commit.assert_awaited()


async def test_update_settings_no_fields_keeps_values(mock_db):
    row = _settings_row()
    mock_db.execute.return_value = make_result(scalar=row)
    updates = UserSettingsUpdate()  # all None -> no field branch taken
    res = await settings_router.update_settings(
        updates=updates, current_user=_settings_user(), db=mock_db
    )
    assert row.display_mode == DisplayMode.GRAYSCALE.value
    assert res.theme == Theme.DARK


# =============================================================================
# settings.py  -  update_profile
# =============================================================================

async def test_update_profile_name_and_email(mock_db):
    user = _settings_user(email="old@b.cz", name="Old")
    # email-uniqueness lookup returns nobody -> email change allowed
    mock_db.execute.return_value = make_result(scalar=None)
    updates = ProfileUpdate(name="New", email="new@b.cz")
    res = await settings_router.update_profile(
        updates=updates, current_user=user, db=mock_db
    )
    assert user.email == "new@b.cz"
    assert user.name == "New"
    assert res.email == "new@b.cz"
    mock_db.commit.assert_awaited()


async def test_update_profile_email_taken_raises_400(mock_db):
    user = _settings_user(email="old@b.cz")
    other = _settings_user(user_id=99, email="taken@b.cz")
    mock_db.execute.return_value = make_result(scalar=other)
    updates = ProfileUpdate(email="taken@b.cz")
    with pytest.raises(HTTPException) as exc:
        await settings_router.update_profile(
            updates=updates, current_user=user, db=mock_db
        )
    assert exc.value.status_code == 400
    # email not changed
    assert user.email == "old@b.cz"


async def test_update_profile_name_only_skips_email_lookup(mock_db):
    user = _settings_user(email="same@b.cz", name="Old")
    updates = ProfileUpdate(name="JustName")
    res = await settings_router.update_profile(
        updates=updates, current_user=user, db=mock_db
    )
    assert user.name == "JustName"
    assert res.name == "JustName"
    # no email lookup performed
    mock_db.execute.assert_not_called()


# =============================================================================
# settings.py  -  change_password
# =============================================================================

async def test_change_password_success(mock_db):
    from utils.security import hash_password
    user = _settings_user(password_hash=hash_password("OldPassw0rd"))
    data = PasswordChange(
        current_password="OldPassw0rd",
        new_password="NewPassw0rd1",
        confirm_password="NewPassw0rd1",
    )
    res = await settings_router.change_password(
        password_data=data, current_user=user, db=mock_db
    )
    assert res["message"] == "Password changed successfully"
    assert user.password_hash != hash_password("OldPassw0rd")  # changed
    mock_db.commit.assert_awaited()


async def test_change_password_wrong_current_raises_400(mock_db):
    from utils.security import hash_password
    user = _settings_user(password_hash=hash_password("RealPassw0rd"))
    data = PasswordChange(
        current_password="WrongPassw0rd",
        new_password="NewPassw0rd1",
        confirm_password="NewPassw0rd1",
    )
    with pytest.raises(HTTPException) as exc:
        await settings_router.change_password(
            password_data=data, current_user=user, db=mock_db
        )
    assert exc.value.status_code == 400
    mock_db.commit.assert_not_awaited()


def test_password_change_schema_mismatch_rejected():
    """confirm_password != new_password is rejected at the schema layer."""
    with pytest.raises(ValueError):
        PasswordChange(
            current_password="x",
            new_password="NewPassw0rd1",
            confirm_password="Different0001",
        )


# =============================================================================
# settings.py  -  get_avatar
# =============================================================================

async def test_get_avatar_success():
    user = _settings_user(avatar_url="/uploads/avatars/1/x.jpg")
    res = await settings_router.get_avatar(current_user=user)
    assert res["avatar_url"] == "/uploads/avatars/1/x.jpg"


async def test_get_avatar_none_raises_404():
    user = _settings_user(avatar_url=None)
    with pytest.raises(HTTPException) as exc:
        await settings_router.get_avatar(current_user=user)
    assert exc.value.status_code == 404


async def test_get_avatar_invalid_url_raises_404():
    user = _settings_user(avatar_url="http://evil/x.jpg")
    with pytest.raises(HTTPException) as exc:
        await settings_router.get_avatar(current_user=user)
    assert exc.value.status_code == 404


# =============================================================================
# settings.py  -  upload_avatar
# =============================================================================

def _png_bytes():
    buf = BytesIO()
    PILImage.new("RGB", (300, 300), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def _rgba_png_bytes():
    buf = BytesIO()
    PILImage.new("RGBA", (300, 300), (10, 20, 30, 128)).save(buf, "PNG")
    return buf.getvalue()


def _upload_file(content, filename="pic.png", content_type="image/png"):
    f = MagicMock()
    f.filename = filename
    f.content_type = content_type
    f.size = len(content)
    f.read = AsyncMock(return_value=content)
    return f


async def test_upload_avatar_success(mock_db, tmp_path):
    user = _settings_user(avatar_url=None)
    upload = _upload_file(_png_bytes())
    saved = {}

    def fake_save(self, fp, fmt, quality=85):
        saved["path"] = fp

    with patch.object(settings_router, "get_avatar_dir", return_value=tmp_path), \
         patch.object(PILImage.Image, "save", fake_save):
        res = await settings_router.upload_avatar(
            request=MagicMock(), file=upload, current_user=user, db=mock_db
        )
    assert res.avatar_url.startswith(f"/uploads/avatars/{user.id}/")
    assert user.avatar_url == res.avatar_url
    mock_db.commit.assert_awaited()
    assert "path" in saved


async def test_upload_avatar_rgba_composite_success(mock_db, tmp_path):
    """RGBA image goes through the white-background composite branch."""
    user = _settings_user(avatar_url=None)
    upload = _upload_file(_rgba_png_bytes())
    with patch.object(settings_router, "get_avatar_dir", return_value=tmp_path), \
         patch.object(PILImage.Image, "save", lambda *a, **k: None):
        res = await settings_router.upload_avatar(
            request=MagicMock(), file=upload, current_user=user, db=mock_db
        )
    assert res.avatar_url.startswith("/uploads/avatars/")


async def test_upload_avatar_replaces_old_file(mock_db, tmp_path):
    """Existing avatar file is unlinked before saving the new one."""
    avatar_url = "/uploads/avatars/1/old.jpg"
    old_file = _avatar_disk_path(avatar_url)
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_bytes(b"old")
    user = _settings_user(avatar_url=avatar_url)
    upload = _upload_file(_png_bytes())
    with patch.object(settings_router, "get_avatar_dir", return_value=tmp_path), \
         patch.object(PILImage.Image, "save", lambda *a, **k: None):
        await settings_router.upload_avatar(
            request=MagicMock(), file=upload, current_user=user, db=mock_db
        )
    assert not old_file.exists()


async def test_upload_avatar_bad_type_raises_400(mock_db):
    user = _settings_user()
    upload = _upload_file(b"x", filename="doc.pdf", content_type="application/pdf")
    with pytest.raises(HTTPException) as exc:
        await settings_router.upload_avatar(
            request=MagicMock(), file=upload, current_user=user, db=mock_db
        )
    assert exc.value.status_code == 400


async def test_upload_avatar_too_large_raises_400(mock_db):
    user = _settings_user()
    big = b"\x00" * (settings_router.MAX_AVATAR_SIZE + 1)
    upload = _upload_file(big, filename="big.png", content_type="image/png")
    with pytest.raises(HTTPException) as exc:
        await settings_router.upload_avatar(
            request=MagicMock(), file=upload, current_user=user, db=mock_db
        )
    assert exc.value.status_code == 400


async def test_upload_avatar_corrupt_image_raises_500(mock_db, tmp_path):
    user = _settings_user(avatar_url=None)
    # passes type/size checks (png ext) but bytes are not a real image
    upload = _upload_file(b"not-an-image", filename="x.png", content_type="image/png")
    with patch.object(settings_router, "get_avatar_dir", return_value=tmp_path):
        with pytest.raises(HTTPException) as exc:
            await settings_router.upload_avatar(
                request=MagicMock(), file=upload, current_user=user, db=mock_db
            )
    assert exc.value.status_code == 500


# =============================================================================
# settings.py  -  delete_avatar
# =============================================================================

async def test_delete_avatar_success(mock_db):
    avatar_url = "/uploads/avatars/1/del.jpg"
    f = _avatar_disk_path(avatar_url)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"data")
    user = _settings_user(avatar_url=avatar_url)
    res = await settings_router.delete_avatar(current_user=user, db=mock_db)
    assert res.message
    assert user.avatar_url is None
    assert not f.exists()
    mock_db.commit.assert_awaited()


async def test_delete_avatar_none_raises_404(mock_db):
    user = _settings_user(avatar_url=None)
    with pytest.raises(HTTPException) as exc:
        await settings_router.delete_avatar(current_user=user, db=mock_db)
    assert exc.value.status_code == 404


async def test_delete_avatar_missing_file_still_clears(mock_db):
    # avatar_url set but file does not exist -> exists() False branch
    user = _settings_user(avatar_url="/uploads/avatars/1/ghost.jpg")
    res = await settings_router.delete_avatar(current_user=user, db=mock_db)
    assert user.avatar_url is None
    assert res.message


async def test_delete_avatar_unlink_error_swallowed(mock_db):
    """unlink() raising is logged but does not abort clearing the URL."""
    avatar_url = "/uploads/avatars/1/locked.jpg"
    f = _avatar_disk_path(avatar_url)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"data")
    user = _settings_user(avatar_url=avatar_url)
    with patch("pathlib.Path.unlink", side_effect=OSError("locked")):
        res = await settings_router.delete_avatar(current_user=user, db=mock_db)
    assert user.avatar_url is None
    assert res.message
    f.unlink()  # cleanup


def test_get_avatar_dir_creates_directory(tmp_path):
    """get_avatar_dir builds <upload_dir>/avatars/<id> and mkdirs it."""
    with patch.object(settings_router.settings, "upload_dir", tmp_path):
        path = settings_router.get_avatar_dir(42)
    assert path == tmp_path / "avatars" / "42"
    assert path.is_dir()
