"""Image routes."""
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy import select, func, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from config import get_settings
from models.user import User
from models.experiment import Experiment
from models.image import Image, MapProtein, UploadStatus
from models.cell_crop import CellCrop
from models.metric import MetricImage, MetricRating, MetricComparison
from schemas.image import ImageResponse, ImageDetailResponse, CellCropGalleryResponse
from utils.security import get_current_user, decode_token, TokenPayload
from services.image_processor import process_image_background

router = APIRouter()
settings = get_settings()


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
    map_protein_id: Optional[int] = Form(None),
    detect_cells: bool = Form(True),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a microscopy image and optionally trigger detection pipeline.

    Args:
        experiment_id: Target experiment ID
        map_protein_id: Optional MAP protein association
        detect_cells: If True (default), run YOLO detection and crop cells.
                     If False, keep full projections without detection.
        file: The image file (TIFF Z-stack, PNG, JPG)
    """
    # Verify experiment ownership
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

    # Create database record
    image = Image(
        experiment_id=experiment_id,
        map_protein_id=map_protein_id,
        original_filename=file.filename,
        file_path=str(file_path),
        file_size=len(content),
        status=UploadStatus.UPLOADING,
        detect_cells=detect_cells,
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

    # Trigger background processing (detection, feature extraction)
    background_tasks.add_task(
        process_image_background,
        image.id,
        detect_cells
    )

    return ImageResponse.model_validate(image)


@router.get("", response_model=List[ImageResponse])
async def list_images(
    experiment_id: int = Query(...),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
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
    result = await db.execute(
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
        .limit(limit)
    )
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

    return [
        CellCropGalleryResponse(
            id=c.id,
            image_id=c.image_id,
            parent_filename=c.image.original_filename,
            bbox_x=c.bbox_x,
            bbox_y=c.bbox_y,
            bbox_w=c.bbox_w,
            bbox_h=c.bbox_h,
            bundleness_score=c.bundleness_score,
            detection_confidence=c.detection_confidence,
            excluded=c.excluded,
            created_at=c.created_at,
            map_protein_name=c.map_protein.name if c.map_protein else None,
            map_protein_color=c.map_protein.color if c.map_protein else None,
        )
        for c in crops
    ]


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

    return FileResponse(file_path)


@router.delete("/crops/{crop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cell_crop(
    crop_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a cell crop."""
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

    # Delete MIP crop file
    if crop.mip_path and os.path.exists(crop.mip_path):
        try:
            os.remove(crop.mip_path)
        except OSError as e:
            logger.warning(f"Failed to delete MIP crop file {crop.mip_path}: {e}")

    # Delete SUM crop file if exists
    if crop.sum_crop_path and os.path.exists(crop.sum_crop_path):
        try:
            os.remove(crop.sum_crop_path)
        except OSError as e:
            logger.warning(f"Failed to delete SUM crop file {crop.sum_crop_path}: {e}")

    await db.delete(crop)
    await db.commit()


@router.patch("/crops/{crop_id}/protein")
async def update_cell_crop_protein(
    crop_id: int,
    map_protein_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update the MAP protein assignment for a cell crop."""
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

    # Verify protein exists if provided
    if map_protein_id is not None:
        protein_result = await db.execute(
            select(MapProtein).where(MapProtein.id == map_protein_id)
        )
        if not protein_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="MAP protein not found"
            )

    crop.map_protein_id = map_protein_id
    await db.commit()

    # Reload with protein relationship
    await db.refresh(crop)
    result = await db.execute(
        select(CellCrop)
        .options(selectinload(CellCrop.map_protein))
        .where(CellCrop.id == crop_id)
    )
    crop = result.scalar_one()

    return {
        "id": crop.id,
        "map_protein_id": crop.map_protein_id,
        "map_protein_name": crop.map_protein.name if crop.map_protein else None,
        "map_protein_color": crop.map_protein.color if crop.map_protein else None,
    }


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

    return FileResponse(file_path)


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
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError as e:
                logger.warning(f"Failed to delete image file {path}: {e}")

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
    from sqlalchemy import delete
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
