"""Image routes."""
import os
import uuid
from pathlib import Path
from typing import List, Optional

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from config import get_settings
from models.user import User
from models.experiment import Experiment
from models.image import Image, MapProtein, UploadStatus
from models.cell_crop import CellCrop
from schemas.image import ImageResponse, ImageDetailResponse
from utils.security import get_current_user
from services.image_processor import process_image_background

router = APIRouter()
settings = get_settings()


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
    """List images in an experiment."""
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

    # Get images with protein info
    result = await db.execute(
        select(Image)
        .options(selectinload(Image.map_protein))
        .where(Image.experiment_id == experiment_id)
        .order_by(Image.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    images = result.scalars().all()

    # Add cell counts
    response = []
    for img in images:
        cell_result = await db.execute(
            select(func.count(CellCrop.id))
            .where(CellCrop.image_id == img.id)
        )
        cell_count = cell_result.scalar() or 0

        img_response = ImageResponse.model_validate(img)
        img_response.cell_count = cell_count
        response.append(img_response)

    return response


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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get image file (original, MIP, SUM projection, or thumbnail)."""
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
            os.remove(path)

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


# Cell crop endpoints
@router.get("/crops/{crop_id}/image")
async def get_crop_image(
    crop_id: int,
    type: str = Query("mip", enum=["mip", "sum"]),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get a cell crop image (MIP or SUM projection)."""
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
