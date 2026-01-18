"""Export/Import routes for experiment data."""
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Optional, TypeVar

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from schemas.export_import import (
    ExportPrepareRequest,
    ExportPrepareResponse,
    ExportStatusResponse,
    ImportExecuteRequest,
    ImportStatusResponse,
    ImportValidationResult,
)
from services.export_service import export_service
from services.import_service import import_service
from utils.security import decode_token, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

T = TypeVar("T")


async def get_user_from_query_token(
    token: Optional[str],
    db: AsyncSession
) -> User:
    """
    Validate JWT token from query parameter and return user.

    Used for streaming endpoints where browser cannot send Authorization header.

    Args:
        token: JWT token from query parameter
        db: Database session

    Returns:
        User object

    Raises:
        HTTPException: If token is missing, invalid, expired, or user not found
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token required. Please log in and try again."
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please log in and try again."
        )

    # Check expiration
    if payload.exp < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in and try again."
        )

    result = await db.execute(select(User).where(User.id == payload.sub))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found. Please log in and try again."
        )

    return user


async def handle_service_call(
    operation: Callable[[], Awaitable[T]],
    error_message: str
) -> T:
    """Execute a service call with standardized error handling."""
    try:
        return await operation()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.exception(f"{error_message}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message
        )


def cleanup_temp_files(temp_path: str, temp_dir: str) -> None:
    """Clean up temporary file and directory, logging warnings on failure."""
    try:
        os.unlink(temp_path)
        os.rmdir(temp_dir)
    except OSError as e:
        logger.warning(f"Failed to clean up temp file {temp_path}: {e}")


def validate_upload_file(file: UploadFile) -> None:
    """Validate uploaded file has filename and is a ZIP file."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided"
        )
    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only ZIP files are supported"
        )


async def verify_job_ownership(
    job_id: str,
    user_id: int,
    service: object,
    job_type: str
) -> dict:
    """
    Verify user owns a job, raise HTTPException if not.

    Args:
        job_id: ID of the job to verify
        user_id: ID of the user
        service: Service instance with get_job_for_user method
        job_type: Type of job for error message (e.g., "Export", "Import")

    Returns:
        Job dict if found and owned

    Raises:
        HTTPException: 404 if job not found or access denied
    """
    job = await service.get_job_for_user(job_id, user_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{job_type} job not found or access denied"
        )
    return job


# ============================================================================
# Export Endpoints
# ============================================================================


@router.post("/export/prepare", response_model=ExportPrepareResponse)
async def prepare_export(
    request: ExportPrepareRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Prepare an export job.

    Validates experiments, counts files, estimates size, and returns a job_id
    for streaming download.
    """
    return await handle_service_call(
        lambda: export_service.prepare_export(
            experiment_ids=request.experiment_ids,
            options=request.options,
            user_id=current_user.id,
            db=db
        ),
        "Failed to prepare export"
    )


@router.get("/export/stream/{job_id}")
async def stream_export(
    job_id: str,
    token: Optional[str] = Query(None, description="JWT token for download (browser cannot send headers)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Stream export ZIP file.

    Downloads the export as a streaming response to handle large files
    without loading everything into memory.

    Authentication via query parameter 'token' (for browser downloads where
    Authorization header cannot be sent).
    """
    current_user = await get_user_from_query_token(token, db)
    await verify_job_ownership(job_id, current_user.id, export_service, "Export")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"maptimize_export_{timestamp}.zip"

    return StreamingResponse(
        export_service.generate_export_stream(job_id, db),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Job-Id": job_id,
        }
    )


@router.get("/export/status/{job_id}", response_model=ExportStatusResponse)
async def get_export_status(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get export job status. Poll this endpoint to track export progress."""
    await verify_job_ownership(job_id, current_user.id, export_service, "Export")
    return await export_service.get_export_status(job_id)


# ============================================================================
# Import Endpoints
# ============================================================================


@router.post("/import/validate", response_model=ImportValidationResult)
async def validate_import(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """
    Validate an import file.

    Uploads the file, detects format, validates structure, and returns
    information about what will be imported.
    """
    validate_upload_file(file)

    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, file.filename)

    try:
        with open(temp_path, 'wb') as f:
            content = await file.read()
            f.write(content)

        return await handle_service_call(
            lambda: import_service.validate_import(
                file_path=temp_path,
                user_id=current_user.id
            ),
            "Failed to validate import file"
        )
    except Exception:
        cleanup_temp_files(temp_path, temp_dir)
        raise


@router.post("/import/execute", response_model=ImportStatusResponse)
async def execute_import(
    request: ImportExecuteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Execute an import.

    Creates experiment and imports images/annotations from the validated file.
    """
    return await handle_service_call(
        lambda: import_service.execute_import(
            job_id=request.job_id,
            experiment_name=request.experiment_name,
            import_format=request.import_as_format,
            create_crops=request.create_crops_from_bboxes,
            user_id=current_user.id,
            db=db
        ),
        "Failed to execute import"
    )


@router.get("/import/status/{job_id}", response_model=ImportStatusResponse)
async def get_import_status(
    job_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get import job status. Poll this endpoint to track import progress."""
    await verify_job_ownership(job_id, current_user.id, import_service, "Import")
    return await import_service.get_import_status(job_id)
