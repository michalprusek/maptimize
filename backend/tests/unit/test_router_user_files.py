"""Unit tests for routers.user_files.

These endpoints replaced a public StaticFiles mount, so the tests focus on the
properties that made the old setup unsafe: cross-user access, guessable paths,
and traversal out of the per-user directory.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routers.user_files as uf


def _user(uid: int):
    return SimpleNamespace(id=uid)


@pytest.fixture
def export_root(tmp_path, monkeypatch):
    monkeypatch.setattr(uf.settings, "export_dir", tmp_path / "exports", raising=False)
    root = tmp_path / "exports" / "7"
    root.mkdir(parents=True)
    (root / "report_20260719_120000_abcdef0123456789.csv").write_text("a,b\n1,2\n")
    return root


@pytest.fixture
def chat_root(tmp_path, monkeypatch):
    monkeypatch.setattr(uf.settings, "chat_image_dir", tmp_path / "chat", raising=False)
    root = tmp_path / "chat" / "7"
    root.mkdir(parents=True)
    (root / "plot_20260719_120000_abcdef01.webp").write_bytes(b"RIFF____WEBPVP8 ")
    return root


# --- exports --------------------------------------------------------------- #
async def test_serve_export_owner_gets_file(export_root):
    resp = await uf.serve_export(7, "report_20260719_120000_abcdef0123456789.csv", _user(7))
    assert Path(resp.path).name == "report_20260719_120000_abcdef0123456789.csv"
    # Never rendered inline -- it is user-controlled tabular data.
    assert resp.headers["content-disposition"].startswith("attachment")


async def test_serve_export_other_user_is_404_not_403(export_root):
    # 404 rather than 403: a 403 would confirm the file exists.
    with pytest.raises(HTTPException) as exc:
        await uf.serve_export(7, "report_20260719_120000_abcdef0123456789.csv", _user(8))
    assert exc.value.status_code == 404


async def test_serve_export_missing_file_is_404(export_root):
    with pytest.raises(HTTPException) as exc:
        await uf.serve_export(7, "report_20260719_120000_deadbeefdeadbeef.csv", _user(7))
    assert exc.value.status_code == 404


@pytest.mark.parametrize("bad", [
    "../../../etc/passwd",
    "..",
    ".",
    "sub/dir.csv",
    "/etc/passwd",
    "report;rm -rf.csv",
    "",
])
async def test_serve_export_rejects_unsafe_filenames(export_root, bad):
    with pytest.raises(HTTPException) as exc:
        await uf.serve_export(7, bad, _user(7))
    assert exc.value.status_code in (400, 404)


async def test_serve_export_symlink_escape_blocked(export_root, tmp_path):
    # The regex bars separators, but a symlink planted inside the user's own
    # directory could still resolve outside it.
    secret = tmp_path / "secret.csv"
    secret.write_text("classified")
    (export_root / "link_20260719_120000_abcdef0123456789.csv").symlink_to(secret)
    with pytest.raises(HTTPException) as exc:
        await uf.serve_export(7, "link_20260719_120000_abcdef0123456789.csv", _user(7))
    assert exc.value.status_code == 404


# --- chat images ----------------------------------------------------------- #
async def test_serve_chat_image_owner_gets_webp_mime(chat_root):
    resp = await uf.serve_chat_image(7, "plot_20260719_120000_abcdef01.webp", _user(7))
    # Wrong MIME here makes browsers refuse to render the plot.
    assert resp.media_type == "image/webp"


async def test_serve_chat_image_other_user_denied(chat_root):
    with pytest.raises(HTTPException) as exc:
        await uf.serve_chat_image(7, "plot_20260719_120000_abcdef01.webp", _user(999))
    assert exc.value.status_code == 404


async def test_serve_chat_image_traversal_blocked(chat_root):
    with pytest.raises(HTTPException) as exc:
        await uf.serve_chat_image(7, "../../etc/passwd", _user(7))
    assert exc.value.status_code in (400, 404)
