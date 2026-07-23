"""Authenticated serving of per-user generated files.

Exports and agent-generated chat images used to live under ``data/uploads``,
which is published by an unauthenticated ``StaticFiles`` mount. Export
filenames carried only a second-resolution timestamp, so
``/uploads/exports/experiment_PRC1_20260719_143000.xlsx`` was enumerable by
anyone who could reach the host.

Both file classes now live outside ``upload_dir`` (so the static mount cannot
reach them at all) and are served here, keyed by the owning user's id. Token
auth goes through the query parameter because these URLs are followed by the
browser directly -- ``<img src>`` and download anchors cannot set headers.
"""
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from config import get_settings
from models.user import User
from utils.security import get_current_user_from_query

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()

# Generated names only: <stem>_<timestamp>_<hex>.<ext>. Rejecting anything else
# means separators and traversal sequences never reach the filesystem.
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,190}$")


def _resolve_owned_file(base_dir: Path, user_id: int, filename: str,
                        current_user: User) -> Path:
    """Resolve ``base_dir/user_id/filename``, enforcing ownership and containment."""
    if current_user.id != user_id:
        # Same response as a missing file: do not confirm that another user's
        # file exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if not _SAFE_FILENAME.match(filename) or filename in (".", ".."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    user_dir = (base_dir / str(user_id)).resolve()
    path = (user_dir / filename).resolve()

    # Defence in depth: the regex already excludes separators, but a symlink
    # inside the directory could still point outside it.
    if not path.is_relative_to(user_dir) or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return path


@router.get("/exports/{user_id}/{filename}")
async def serve_export(
    user_id: int,
    filename: str,
    current_user: User = Depends(get_current_user_from_query),
) -> FileResponse:
    """Serve a generated data export to the user who created it."""
    path = _resolve_owned_file(Path(settings.export_dir), user_id, filename, current_user)
    return FileResponse(
        path=path,
        filename=filename,
        # Never render an export inline; it is user-controlled tabular data.
        content_disposition_type="attachment",
    )
