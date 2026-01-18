"""Image routes."""
import logging
import os
import uuid
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

import aiofiles
from PIL import Image as PILImage
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks
from fastapi.responses import FileResponse, Response
from sqlalchemy import select, func, delete, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from config import get_settings
from models.user import User
from models.experiment import Experiment
from models.image import Image, UploadStatus
from models.cell_crop import CellCrop
from models.metric import MetricImage, MetricRating, MetricComparison
from models.ranking import Comparison
from schemas.image import (
    ImageResponse,
    ImageDetailResponse,
    CellCropGalleryResponse,
    BatchProcessRequest,
    BatchProcessResponse,
    FOVResponse,
    CropBboxUpdateRequest,
    CropBboxUpdateResponse,
    ManualCropCreateRequest,
    ManualCropCreateResponse,
    CropRegenerateRequest,
    CropRegenerateResponse,
    CropBatchUpdateRequest,
    CropBatchUpdateResponse,
)
from utils.security import get_current_user, decode_token, TokenPayload
from services.image_processor import (
    process_image_background,
    process_upload_only_background,
    process_batch_background,
)

router = APIRouter()
settings = get_settings()


def safe_remove_file(path: Optional[str]) -> bool:
    """
    Safely remove a file, logging warnings on failure.

    Args:
        path: File path to remove, or None

    Returns:
        True if file was removed, False otherwise
    """
    if path and os.path.exists(path):
        try:
            os.remove(path)
            return True
        except OSError as e:
            logger.warning(f"Failed to delete file {path}: {e}")
    return False


def serve_image_file(file_path: str) -> Response | FileResponse:
    """
    Serve an image file, converting TIFF to PNG for browser compatibility.

    Browsers cannot display TIFF format directly, so we convert to PNG on-the-fly.
    16-bit and float TIFF images are normalized to 8-bit for display.

    Args:
        file_path: Path to the image file

    Returns:
        Response with PNG data or FileResponse for non-TIFF formats
    """
    file_lower = file_path.lower()
    if file_lower.endswith(('.tif', '.tiff')):
        try:
            import numpy as np
            with PILImage.open(file_path) as img:
                # Handle various TIFF modes
                if img.mode in ('I', 'I;16', 'I;16B', 'F'):
                    # 16-bit grayscale or float - normalize to 8-bit
                    arr = np.array(img, dtype=np.float64)
                    arr_min, arr_max = arr.min(), arr.max()
                    # Handle uniform images (avoid division by zero)
                    if arr_max - arr_min < 1e-10:
                        arr = np.full_like(arr, 127, dtype=np.uint8)
                    else:
                        arr = ((arr - arr_min) / (arr_max - arr_min) * 255).astype(np.uint8)
                    img = PILImage.fromarray(arr)
                elif img.mode == 'RGBA':
                    pass  # Keep RGBA
                elif img.mode != 'RGB':
                    img = img.convert('RGB')

                # Save to bytes buffer as PNG
                buffer = io.BytesIO()
                img.save(buffer, format='PNG', optimize=False)
                buffer.seek(0)

                return Response(
                    content=buffer.getvalue(),
                    media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"}
                )
        except Exception as e:
            logger.error(f"Failed to convert TIFF to PNG: {e}")
            # Fall through to return original file if conversion fails

    return FileResponse(file_path)


def validate_image_token(token: Optional[str]) -> TokenPayload:
    """
    Validate JWT token for image requests.

    Args:
        token: JWT token from query parameter

    Returns:
        TokenPayload with user info

    Raises:
        HTTPException: If token is missing, invalid, or expired
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token required"
        )

    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )

    # Explicitly check expiration (defense in depth)
    if payload.exp < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired"
        )

    return payload


@router.post("/upload", response_model=ImageResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(
    background_tasks: BackgroundTasks,
    experiment_id: int = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a microscopy image (Phase 1 of two-phase workflow).

    This endpoint:
    - Saves the file to disk
    - Triggers background processing to create projections and thumbnail
    - Sets status to UPLOADED when Phase 1 is complete
    - Inherits protein assignment from the experiment

    Detection is NOT triggered here. Use /batch-process endpoint after upload
    to configure detection settings and start Phase 2.

    Args:
        experiment_id: Target experiment ID
        file: The image file (TIFF Z-stack, PNG, JPG)
    """
    # Verify experiment ownership and get experiment's protein assignment
    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.user_id == current_user.id
        )
    )
    experiment = result.scalar_one_or_none()

    if not experiment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found"
        )

    # Validate file type
    allowed_extensions = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {allowed_extensions}"
        )

    # Create upload directory
    upload_dir = settings.upload_dir / str(current_user.id) / str(experiment_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename
    unique_id = uuid.uuid4().hex[:8]
    safe_filename = f"{unique_id}_{file.filename}"
    file_path = upload_dir / safe_filename

    # Save file
    async with aiofiles.open(file_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    # Create database record - inherit protein from experiment
    image = Image(
        experiment_id=experiment_id,
        map_protein_id=experiment.map_protein_id,  # Inherit from experiment
        original_filename=file.filename,
        file_path=str(file_path),
        file_size=len(content),
        status=UploadStatus.UPLOADING,
        detect_cells=False,  # Will be set in Phase 2
    )
    db.add(image)
    await db.commit()

    # Reload with relationship to avoid async loading issues
    result = await db.execute(
        select(Image)
        .options(selectinload(Image.map_protein))
        .where(Image.id == image.id)
    )
    image = result.scalar_one()

    # Trigger Phase 1 background processing (projections, thumbnail only)
    background_tasks.add_task(
        process_upload_only_background,
        image.id
    )

    return ImageResponse.model_validate(image)


@router.post("/batch-process", response_model=BatchProcessResponse)
async def batch_process_images(
    request: BatchProcessRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Start Phase 2 processing for multiple images (batch processing).

    This endpoint:
    - Validates that all images exist and are owned by the user
    - Validates that images are in UPLOADED status (Phase 1 complete)
    - Queues background processing for detection and feature extraction
    - Images inherit protein assignment from their experiment

    Args:
        request: BatchProcessRequest with image_ids and detect_cells
    """
    # Get all images and verify ownership
    result = await db.execute(
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(
            Image.id.in_(request.image_ids),
            Experiment.user_id == current_user.id
        )
    )
    images = result.scalars().all()

    if len(images) != len(request.image_ids):
        found_ids = {img.id for img in images}
        missing_ids = set(request.image_ids) - found_ids
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Images not found or not accessible: {missing_ids}"
        )

    # Verify all images are in appropriate status for processing
    invalid_images = [
        img for img in images
        if img.status not in [UploadStatus.UPLOADED, UploadStatus.READY, UploadStatus.ERROR]
    ]
    if invalid_images:
        invalid_info = [(img.id, img.status.value) for img in invalid_images]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Images not ready for processing (still uploading?): {invalid_info}"
        )

    # Queue background processing for each image (protein is already set from experiment)
    for image in images:
        background_tasks.add_task(
            process_batch_background,
            image.id,
            request.detect_cells,
            None  # Protein is inherited from experiment, not passed here
        )

    return BatchProcessResponse(
        processing_count=len(images),
        message=f"Processing started for {len(images)} images"
    )


@router.get("/fovs", response_model=List[FOVResponse])
async def list_fovs(
    experiment_id: int = Query(...),
    skip: int = Query(0, ge=0),
    limit: Optional[int] = Query(None, ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List FOV (Field of View) images in an experiment.

    Returns Image records representing the original uploaded images (FOVs),
    not the detected cell crops. Use this for the FOV gallery view.

    Note: limit is optional. If not provided, all FOVs are returned.
    Frontend handles pagination for display.
    """
    # Verify experiment ownership
    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.user_id == current_user.id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found"
        )

    # Get images with protein info and cell counts
    query = (
        select(
            Image,
            func.count(CellCrop.id).label("cell_count")
        )
        .options(selectinload(Image.map_protein))
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .where(Image.experiment_id == experiment_id)
        .group_by(Image.id)
        .order_by(Image.created_at.desc())
        .offset(skip)
    )
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    rows = result.all()

    response = []
    for img, cell_count in rows:
        # Build thumbnail URL
        thumbnail_url = None
        if img.thumbnail_path:
            thumbnail_url = f"/api/images/{img.id}/file?type=thumbnail"

        fov_response = FOVResponse(
            id=img.id,
            experiment_id=img.experiment_id,
            original_filename=img.original_filename,
            status=img.status,
            width=img.width,
            height=img.height,
            z_slices=img.z_slices,
            file_size=img.file_size,
            detect_cells=img.detect_cells,
            thumbnail_url=thumbnail_url,
            cell_count=cell_count or 0,
            map_protein=img.map_protein,
            created_at=img.created_at,
            processed_at=img.processed_at,
        )
        response.append(fov_response)

    return response


@router.get("", response_model=List[ImageResponse])
async def list_images(
    experiment_id: int = Query(...),
    skip: int = Query(0, ge=0),
    limit: Optional[int] = Query(None, ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List images in an experiment with cell counts in a single query."""
    # Verify experiment ownership
    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.user_id == current_user.id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found"
        )

    # Get images with protein info and cell counts in a single query
    query = (
        select(
            Image,
            func.count(CellCrop.id).label("cell_count")
        )
        .options(selectinload(Image.map_protein))
        .outerjoin(CellCrop, Image.id == CellCrop.image_id)
        .where(Image.experiment_id == experiment_id)
        .group_by(Image.id)
        .order_by(Image.created_at.desc())
        .offset(skip)
    )
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    rows = result.all()

    response = []
    for img, cell_count in rows:
        img_response = ImageResponse.model_validate(img)
        img_response.cell_count = cell_count or 0
        response.append(img_response)

    return response


# Cell crop endpoints - MUST be before /{image_id} to avoid route conflict
@router.get("/crops", response_model=List[CellCropGalleryResponse])
async def list_cell_crops(
    experiment_id: int = Query(...),
    exclude_excluded: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all cell crops for an experiment."""
    # Verify experiment ownership
    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.user_id == current_user.id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Experiment not found"
        )

    # Build query with optional exclusion filter
    query = (
        select(CellCrop)
        .join(Image, CellCrop.image_id == Image.id)
        .options(
            selectinload(CellCrop.image),
            selectinload(CellCrop.map_protein)
        )
        .where(Image.experiment_id == experiment_id)
        .order_by(CellCrop.created_at.desc())
    )

    if exclude_excluded:
        query = query.where(CellCrop.excluded == False)

    result = await db.execute(query)
    crops = result.scalars().all()

    return [CellCropGalleryResponse.from_crop(c) for c in crops]


@router.get("/{fov_id}/crops", response_model=List[CellCropGalleryResponse])
async def list_fov_crops(
    fov_id: int,
    exclude_excluded: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all cell crops for a specific FOV image."""
    # Verify image ownership
    result = await db.execute(
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(
            Image.id == fov_id,
            Experiment.user_id == current_user.id
        )
    )
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )

    # Build query with optional exclusion filter
    query = (
        select(CellCrop)
        .options(
            selectinload(CellCrop.image),
            selectinload(CellCrop.map_protein)
        )
        .where(CellCrop.image_id == fov_id)
        .order_by(CellCrop.created_at.desc())
    )

    if exclude_excluded:
        query = query.where(CellCrop.excluded == False)

    result = await db.execute(query)
    crops = result.scalars().all()

    return [CellCropGalleryResponse.from_crop(c) for c in crops]


@router.get("/crops/{crop_id}/image")
async def get_crop_image(
    crop_id: int,
    type: str = Query("mip", enum=["mip", "sum"]),
    token: Optional[str] = Query(None, description="JWT token for image requests"),
    db: AsyncSession = Depends(get_db)
):
    """Get a cell crop image (MIP or SUM projection)."""
    payload = validate_image_token(token)

    result = await db.execute(select(User).where(User.id == payload.sub))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    result = await db.execute(
        select(CellCrop)
        .options(
            selectinload(CellCrop.image).selectinload(Image.experiment)
        )
        .where(CellCrop.id == crop_id)
    )
    crop = result.scalar_one_or_none()

    if not crop or crop.image.experiment.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cell crop not found"
        )

    # Select file path based on type
    if type == "sum" and crop.sum_crop_path:
        file_path = crop.sum_crop_path
    else:
        file_path = crop.mip_path

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Crop image file not found ({type})"
        )

    return serve_image_file(file_path)


@router.delete("/crops/{crop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cell_crop(
    crop_id: int,
    confirm_delete_comparisons: bool = Query(
        False,
        description="Confirm deletion of ranking comparison history"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a cell crop.

    If the crop has ranking comparisons, you must pass confirm_delete_comparisons=true
    to acknowledge that comparison history will be permanently deleted.
    """
    result = await db.execute(
        select(CellCrop)
        .options(
            selectinload(CellCrop.image).selectinload(Image.experiment)
        )
        .where(CellCrop.id == crop_id)
    )
    crop = result.scalar_one_or_none()

    if not crop or crop.image.experiment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cell crop not found"
        )

    # Check for ranking comparisons that will be deleted (CASCADE)
    comparison_count_result = await db.execute(
        select(func.count(Comparison.id)).where(
            or_(
                Comparison.crop_a_id == crop_id,
                Comparison.crop_b_id == crop_id,
                Comparison.winner_id == crop_id
            ),
            Comparison.undone == False
        )
    )
    comparison_count = comparison_count_result.scalar() or 0

    if comparison_count > 0 and not confirm_delete_comparisons:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Deleting this crop will permanently remove {comparison_count} ranking comparison(s). "
                   f"Add ?confirm_delete_comparisons=true to proceed."
        )

    # Find all MetricImage records that reference this cell crop
    metric_images_result = await db.execute(
        select(MetricImage).where(MetricImage.cell_crop_id == crop_id)
    )
    metric_images = metric_images_result.scalars().all()

    # Delete related metric data for each MetricImage
    for mi in metric_images:
        # Delete comparisons involving this metric image
        await db.execute(
            delete(MetricComparison).where(
                or_(
                    MetricComparison.image_a_id == mi.id,
                    MetricComparison.image_b_id == mi.id,
                    MetricComparison.winner_id == mi.id
                )
            )
        )
        # Delete rating for this metric image
        await db.execute(
            delete(MetricRating).where(MetricRating.metric_image_id == mi.id)
        )
        # Delete the metric image itself
        await db.delete(mi)

    # Delete crop files
    safe_remove_file(crop.mip_path)
    safe_remove_file(crop.sum_crop_path)

    await db.delete(crop)
    await db.commit()


# =============================================================================
# Crop Editor Endpoints
# =============================================================================


@router.patch("/crops/{crop_id}/bbox", response_model=CropBboxUpdateResponse)
async def update_crop_bbox(
    crop_id: int,
    request: CropBboxUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update bounding box coordinates for a cell crop.

    This updates the bbox coordinates in the database. Use the /regenerate
    endpoint afterwards to regenerate crop images and features.
    """
    from services.crop_editor_service import (
        get_crop_with_ownership_check,
        validate_bbox_within_image,
    )

    crop, image, error = await get_crop_with_ownership_check(crop_id, current_user.id, db)
    if error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error
        )

    # Validate bbox within image bounds
    is_valid, validation_error = validate_bbox_within_image(
        request.bbox_x,
        request.bbox_y,
        request.bbox_w,
        request.bbox_h,
        image.width,
        image.height,
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=validation_error
        )

    # Update bbox coordinates
    crop.bbox_x = request.bbox_x
    crop.bbox_y = request.bbox_y
    crop.bbox_w = request.bbox_w
    crop.bbox_h = request.bbox_h

    # Mark features as stale
    crop.embedding = None
    crop.embedding_model = None
    crop.mean_intensity = None
    crop.umap_x = None
    crop.umap_y = None
    crop.umap_computed_at = None

    await db.commit()

    return CropBboxUpdateResponse(
        id=crop.id,
        bbox_x=crop.bbox_x,
        bbox_y=crop.bbox_y,
        bbox_w=crop.bbox_w,
        bbox_h=crop.bbox_h,
        needs_regeneration=True,
    )


@router.post("/crops/{crop_id}/regenerate", response_model=CropRegenerateResponse)
async def regenerate_crop_features(
    crop_id: int,
    request: CropRegenerateRequest = CropRegenerateRequest(),
    background_tasks: BackgroundTasks = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Regenerate crop images and features from current bbox coordinates.

    This extracts new pixels from the parent FOV, saves new crop images,
    calculates mean_intensity, and extracts new DINOv3 embedding.
    """
    from services.crop_editor_service import (
        get_crop_with_ownership_check,
        regenerate_crop_features as do_regenerate,
    )

    crop, image, error = await get_crop_with_ownership_check(crop_id, current_user.id, db)
    if error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error
        )

    # Perform regeneration
    result = await do_regenerate(crop, image, db)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Regeneration failed")
        )

    await db.commit()

    # Determine processing status (completed vs partial)
    processing_status = "partial" if result.get("partial_success") else "completed"

    return CropRegenerateResponse(
        id=crop.id,
        bbox_x=crop.bbox_x,
        bbox_y=crop.bbox_y,
        bbox_w=crop.bbox_w,
        bbox_h=crop.bbox_h,
        mip_path=crop.mip_path,
        sum_crop_path=crop.sum_crop_path,
        mean_intensity=crop.mean_intensity,
        embedding_model=crop.embedding_model,
        has_embedding=crop.embedding is not None,
        processing_status=processing_status,
        warnings=result.get("warnings"),
    )


@router.post("/{fov_id}/crops", response_model=ManualCropCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_manual_crop(
    fov_id: int,
    request: ManualCropCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new manual crop on an FOV image.

    Creates a crop with the specified bounding box, extracts pixels from
    the parent FOV, and queues feature extraction.
    """
    from services.crop_editor_service import (
        get_image_with_ownership_check,
        create_manual_crop as do_create,
    )
    from ml.features import extract_features_for_crops

    image, error = await get_image_with_ownership_check(fov_id, current_user.id, db)
    if error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error
        )

    # Verify image is ready for manual bbox creation
    # Allow both UPLOADED (manual-only workflow) and READY (post-detection) statuses
    if image.status not in [UploadStatus.UPLOADED, UploadStatus.READY]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Image is not ready for cell annotation (status: {image.status.value})"
        )

    # Create the crop
    crop, error = await do_create(
        image,
        request.bbox_x,
        request.bbox_y,
        request.bbox_w,
        request.bbox_h,
        db,
        request.map_protein_id,
    )

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )

    await db.commit()

    # Extract features in background
    async def extract_features_task(crop_id: int):
        from database import get_db_context
        async with get_db_context() as task_db:
            try:
                await extract_features_for_crops([crop_id], task_db)
            except Exception as e:
                logger.error(f"Failed to extract features for crop {crop_id}: {e}")

    background_tasks.add_task(extract_features_task, crop.id)

    return ManualCropCreateResponse(
        id=crop.id,
        image_id=crop.image_id,
        bbox_x=crop.bbox_x,
        bbox_y=crop.bbox_y,
        bbox_w=crop.bbox_w,
        bbox_h=crop.bbox_h,
        detection_confidence=crop.detection_confidence,
        needs_processing=True,
    )


@router.patch("/{fov_id}/crops/batch", response_model=CropBatchUpdateResponse)
async def batch_update_crops(
    fov_id: int,
    request: CropBatchUpdateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Apply batch changes to crops (create, update, delete).

    Processes multiple crop changes atomically and optionally queues
    feature regeneration for modified crops.
    """
    from services.crop_editor_service import (
        get_image_with_ownership_check,
        validate_bbox_within_image,
        create_manual_crop as do_create,
        delete_crop_files,
    )
    from services.umap_service import invalidate_crop_umap
    from ml.features import extract_features_for_crops

    image, error = await get_image_with_ownership_check(fov_id, current_user.id, db)
    if error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error
        )

    created_ids = []
    updated_ids = []
    deleted_ids = []
    failed = []
    crops_to_regenerate = []

    for change in request.changes:
        try:
            if change.action == "create":
                # Validate required fields
                if change.bbox_x is None or change.bbox_y is None or change.bbox_w is None or change.bbox_h is None:
                    failed.append({"action": "create", "error": "Missing bbox coordinates"})
                    continue

                # Create new crop
                crop, err = await do_create(
                    image,
                    change.bbox_x,
                    change.bbox_y,
                    change.bbox_w,
                    change.bbox_h,
                    db,
                    change.map_protein_id,
                )
                if err:
                    failed.append({"action": "create", "error": err})
                else:
                    created_ids.append(crop.id)
                    crops_to_regenerate.append(crop.id)

            elif change.action == "update":
                if change.id is None:
                    failed.append({"action": "update", "error": "Missing crop id"})
                    continue

                # Get crop and verify ownership
                result = await db.execute(
                    select(CellCrop).where(
                        CellCrop.id == change.id,
                        CellCrop.image_id == fov_id
                    )
                )
                crop = result.scalar_one_or_none()

                if not crop:
                    failed.append({"action": "update", "id": change.id, "error": "Crop not found"})
                    continue

                # Validate bbox if provided
                bbox_x = change.bbox_x if change.bbox_x is not None else crop.bbox_x
                bbox_y = change.bbox_y if change.bbox_y is not None else crop.bbox_y
                bbox_w = change.bbox_w if change.bbox_w is not None else crop.bbox_w
                bbox_h = change.bbox_h if change.bbox_h is not None else crop.bbox_h

                is_valid, err = validate_bbox_within_image(
                    bbox_x, bbox_y, bbox_w, bbox_h, image.width, image.height
                )
                if not is_valid:
                    failed.append({"action": "update", "id": change.id, "error": err})
                    continue

                # Update crop
                crop.bbox_x = bbox_x
                crop.bbox_y = bbox_y
                crop.bbox_w = bbox_w
                crop.bbox_h = bbox_h
                if change.map_protein_id is not None:
                    crop.map_protein_id = change.map_protein_id

                # Mark for regeneration
                crop.embedding = None
                crop.embedding_model = None
                crop.mean_intensity = None
                crop.umap_x = None
                crop.umap_y = None

                updated_ids.append(crop.id)
                crops_to_regenerate.append(crop.id)

            elif change.action == "delete":
                if change.id is None:
                    failed.append({"action": "delete", "error": "Missing crop id"})
                    continue

                # Get crop
                result = await db.execute(
                    select(CellCrop).where(
                        CellCrop.id == change.id,
                        CellCrop.image_id == fov_id
                    )
                )
                crop = result.scalar_one_or_none()

                if not crop:
                    failed.append({"action": "delete", "id": change.id, "error": "Crop not found"})
                    continue

                # Check for comparisons if not confirmed
                if not request.confirm_delete_comparisons:
                    comparison_result = await db.execute(
                        select(func.count(Comparison.id)).where(
                            or_(
                                Comparison.crop_a_id == change.id,
                                Comparison.crop_b_id == change.id,
                            ),
                            Comparison.undone == False
                        )
                    )
                    count = comparison_result.scalar() or 0
                    if count > 0:
                        failed.append({
                            "action": "delete",
                            "id": change.id,
                            "error": f"Has {count} comparisons, set confirm_delete_comparisons=true"
                        })
                        continue

                # Delete crop files and record
                delete_crop_files(crop)
                await db.delete(crop)
                deleted_ids.append(change.id)

        except Exception as e:
            failed.append({"action": change.action, "id": change.id, "error": str(e)})

    # Commit all changes
    await db.commit()

    # Invalidate UMAP for the image
    if updated_ids or deleted_ids:
        await invalidate_crop_umap(db, image_id=fov_id)
        await db.commit()

    # Queue feature regeneration if requested
    regeneration_queued = False
    if request.regenerate_features and crops_to_regenerate:
        async def regenerate_task(crop_ids: list):
            from database import get_db_context
            from services.crop_editor_service import regenerate_crop_features as do_regen
            async with get_db_context() as task_db:
                for crop_id in crop_ids:
                    try:
                        result = await task_db.execute(
                            select(CellCrop)
                            .options(selectinload(CellCrop.image))
                            .where(CellCrop.id == crop_id)
                        )
                        crop = result.scalar_one_or_none()
                        if crop:
                            await do_regen(crop, crop.image, task_db)
                    except Exception as e:
                        logger.error(f"Failed to regenerate crop {crop_id}: {e}")
                await task_db.commit()

        background_tasks.add_task(regenerate_task, crops_to_regenerate)
        regeneration_queued = True

    return CropBatchUpdateResponse(
        created=created_ids,
        updated=updated_ids,
        deleted=deleted_ids,
        failed=failed,
        regeneration_queued=regeneration_queued,
    )


@router.get("/{image_id}", response_model=ImageDetailResponse)
async def get_image(
    image_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get image details with detected cells."""
    result = await db.execute(
        select(Image)
        .options(
            selectinload(Image.map_protein),
            selectinload(Image.cell_crops),
            selectinload(Image.experiment)
        )
        .where(Image.id == image_id)
    )
    image = result.scalar_one_or_none()

    if not image or image.experiment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )

    response = ImageDetailResponse.model_validate(image)
    response.cell_count = len(image.cell_crops)

    return response


@router.get("/{image_id}/file")
async def get_image_file(
    image_id: int,
    type: str = Query("original", enum=["original", "mip", "sum", "thumbnail"]),
    token: Optional[str] = Query(None, description="JWT token for image requests from <img> tags"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get image file (original, MIP, SUM projection, or thumbnail).

    Supports authentication via query parameter 'token' (for <img src=""> tags).
    """
    payload = validate_image_token(token)

    result = await db.execute(select(User).where(User.id == payload.sub))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    result = await db.execute(
        select(Image)
        .options(selectinload(Image.experiment))
        .where(Image.id == image_id)
    )
    image = result.scalar_one_or_none()

    if not image or image.experiment.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )

    # Select file path based on type
    if type == "mip" and image.mip_path:
        file_path = image.mip_path
    elif type == "sum" and image.sum_path:
        file_path = image.sum_path
    elif type == "thumbnail" and image.thumbnail_path:
        file_path = image.thumbnail_path
    else:
        file_path = image.file_path

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found (may have been discarded after processing)"
        )

    return serve_image_file(file_path)


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    image_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete an image and its cells."""
    result = await db.execute(
        select(Image)
        .options(selectinload(Image.experiment))
        .where(Image.id == image_id)
    )
    image = result.scalar_one_or_none()

    if not image or image.experiment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )

    # Delete files (including SUM projection)
    for path in [image.file_path, image.mip_path, image.sum_path, image.thumbnail_path]:
        safe_remove_file(path)

    await db.delete(image)
    await db.commit()


@router.post("/{image_id}/reprocess", response_model=ImageResponse)
async def reprocess_image(
    image_id: int,
    background_tasks: BackgroundTasks,
    detect_cells: Optional[bool] = Query(None, description="Override detection setting. If None, uses original setting."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Manually trigger reprocessing of an image.

    Note: If the source file was discarded during previous processing,
    reprocessing will fail. This only works if the image still has
    its source files (MIP/SUM projections or original).
    """
    result = await db.execute(
        select(Image)
        .options(selectinload(Image.experiment), selectinload(Image.map_protein))
        .where(Image.id == image_id)
    )
    image = result.scalar_one_or_none()

    if not image or image.experiment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )

    # Check if source files exist
    if image.source_discarded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reprocess: source files were discarded during previous processing"
        )

    # Use provided detect_cells or fall back to original setting
    should_detect = detect_cells if detect_cells is not None else image.detect_cells

    # Reset status and delete existing crops
    image.status = UploadStatus.PROCESSING
    image.detect_cells = should_detect

    # Delete existing cell crops
    await db.execute(
        delete(CellCrop).where(CellCrop.image_id == image_id)
    )
    await db.commit()

    # Reload image with relationships
    result = await db.execute(
        select(Image)
        .options(selectinload(Image.map_protein))
        .where(Image.id == image_id)
    )
    image = result.scalar_one()

    # Trigger reprocessing
    background_tasks.add_task(
        process_image_background,
        image.id,
        should_detect
    )

    return ImageResponse.model_validate(image)


class BatchRedetectRequest(BaseModel):
    """Request to batch re-detect cells on multiple images."""
    image_ids: List[int]


class BatchRedetectResponse(BaseModel):
    """Response from batch re-detect operation."""
    processed_count: int
    message: str


@router.post("/batch-redetect", response_model=BatchRedetectResponse)
async def batch_redetect_cells(
    request: BatchRedetectRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Batch re-run YOLO cell detection on multiple images.

    This deletes existing crops and runs detection again.
    Useful when detection parameters have changed or user wants fresh detection.
    """
    if not request.image_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No image IDs provided"
        )

    # Fetch all images and verify ownership
    result = await db.execute(
        select(Image)
        .options(selectinload(Image.experiment), selectinload(Image.map_protein))
        .where(Image.id.in_(request.image_ids))
    )
    images = result.scalars().all()

    # Filter to only images owned by user
    user_images = [
        img for img in images
        if img.experiment.user_id == current_user.id
    ]

    if not user_images:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No valid images found"
        )

    processed_count = 0

    for image in user_images:
        # Skip if source files were discarded
        if image.source_discarded:
            logger.warning(f"Skipping image {image.id}: source files discarded")
            continue

        # Reset status
        image.status = UploadStatus.PROCESSING
        image.detect_cells = True

        # Delete existing cell crops
        await db.execute(
            delete(CellCrop).where(CellCrop.image_id == image.id)
        )

        # Queue background processing
        background_tasks.add_task(
            process_image_background,
            image.id,
            True  # detect_cells
        )
        processed_count += 1

    await db.commit()

    return BatchRedetectResponse(
        processed_count=processed_count,
        message=f"Started re-detection for {processed_count} image(s)"
    )


