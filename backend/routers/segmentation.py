"""Segmentation API endpoints for SAM-based interactive segmentation."""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from utils.security import get_current_user
from services import segmentation_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class ClickPoint(BaseModel):
    """A single click point for segmentation."""
    x: int = Field(..., description="X coordinate in image pixels")
    y: int = Field(..., description="Y coordinate in image pixels")
    label: int = Field(..., ge=0, le=1, description="1 = foreground, 0 = background")


class SegmentRequest(BaseModel):
    """Request for interactive segmentation."""
    image_id: int = Field(..., description="ID of the image to segment")
    points: List[ClickPoint] = Field(..., min_length=1, description="Click points")


class SegmentResponse(BaseModel):
    """Response from interactive segmentation."""
    success: bool
    polygon: Optional[List[List[int]]] = None
    iou_score: Optional[float] = None
    area_pixels: Optional[int] = None
    error: Optional[str] = None


class SaveMaskRequest(BaseModel):
    """Request to save a finalized segmentation mask."""
    crop_id: int = Field(..., description="ID of the cell crop")
    polygon: List[List[int]] = Field(..., min_length=3, description="Polygon points [[x, y], ...]")
    iou_score: float = Field(..., ge=0, le=1, description="SAM IoU prediction score")
    prompt_count: int = Field(..., ge=0, description="Number of click prompts used")


class EmbeddingStatusResponse(BaseModel):
    """SAM embedding status for an image."""
    image_id: int
    status: str  # "not_started", "pending", "computing", "ready", "error"
    has_embedding: bool
    embedding_shape: Optional[str] = None
    model_variant: Optional[str] = None


class MaskResponse(BaseModel):
    """Segmentation mask for a cell crop."""
    has_mask: bool
    polygon: Optional[List[List[int]]] = None
    iou_score: Optional[float] = None
    area_pixels: Optional[int] = None
    creation_method: Optional[str] = None
    prompt_count: Optional[int] = None


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/compute-embedding/{image_id}")
async def compute_embedding(
    image_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger SAM embedding computation for an image.

    This is CPU/GPU intensive (~5-15s) and runs in background.
    Poll the /embedding-status endpoint to check progress.
    """
    from sqlalchemy import select
    from models.image import Image
    from models.experiment import Experiment

    # Verify ownership
    result = await db.execute(
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(Image.id == image_id)
        .where(Experiment.user_id == current_user.id)
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found or access denied"
        )

    # Check if already computing
    if image.sam_embedding_status == "computing":
        return {"message": "SAM embedding already computing", "image_id": image_id}

    # Queue background task
    background_tasks.add_task(
        segmentation_service.queue_sam_embedding,
        image_id
    )

    return {"message": "SAM embedding computation started", "image_id": image_id}


@router.get("/embedding-status/{image_id}", response_model=EmbeddingStatusResponse)
async def get_embedding_status(
    image_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Check SAM embedding status for an image.

    Status values:
    - not_started: No embedding computation triggered
    - pending: Queued for computation
    - computing: Currently processing
    - ready: Embedding available for segmentation
    - error: Computation failed
    """
    result = await segmentation_service.get_embedding_status(image_id, db)

    if result["status"] == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )

    return EmbeddingStatusResponse(**result)


@router.post("/segment", response_model=SegmentResponse)
async def segment_interactive(
    request: SegmentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Run interactive segmentation from click prompts.

    This is the fast decoder inference (~10-50ms).
    Requires pre-computed embedding (status = "ready").

    Points with label=1 indicate foreground (object to segment).
    Points with label=0 indicate background (exclude from mask).
    """
    # Convert points to tuples
    point_coords = [(p.x, p.y) for p in request.points]
    point_labels = [p.label for p in request.points]

    result = await segmentation_service.segment_from_prompts(
        image_id=request.image_id,
        point_coords=point_coords,
        point_labels=point_labels,
        db=db,
    )

    if not result["success"]:
        return SegmentResponse(success=False, error=result.get("error"))

    return SegmentResponse(
        success=True,
        polygon=result["polygon"],
        iou_score=result["iou_score"],
        area_pixels=result["area_pixels"],
    )


@router.post("/save-mask")
async def save_mask(
    request: SaveMaskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Save finalized segmentation mask for a crop.

    The polygon becomes the authoritative cell boundary for this crop.
    """
    result = await segmentation_service.save_segmentation_mask(
        crop_id=request.crop_id,
        polygon=[tuple(p) for p in request.polygon],
        iou_score=request.iou_score,
        prompt_count=request.prompt_count,
        db=db,
    )

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error")
        )

    return result


@router.get("/mask/{crop_id}", response_model=MaskResponse)
async def get_mask(
    crop_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get segmentation mask for a cell crop.

    Returns has_mask=false if no mask has been saved for this crop.
    """
    result = await segmentation_service.get_segmentation_mask(crop_id, db)
    return MaskResponse(**result)


@router.get("/masks/batch")
async def get_masks_batch(
    crop_ids: str,  # Comma-separated list
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get segmentation masks for multiple crops at once.

    Args:
        crop_ids: Comma-separated list of crop IDs (e.g., "1,2,3,4")

    Returns:
        Dict mapping crop_id to mask data (only includes crops with masks)
    """
    try:
        ids = [int(id.strip()) for id in crop_ids.split(",") if id.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid crop_ids format. Use comma-separated integers."
        )

    if not ids:
        return {"masks": {}}

    if len(ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 100 crop IDs per request"
        )

    masks = await segmentation_service.get_segmentation_masks_batch(ids, db)

    return {"masks": masks}


@router.delete("/mask/{crop_id}")
async def delete_mask(
    crop_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete segmentation mask for a cell crop.

    This removes the polygon boundary, reverting to bbox-only representation.
    """
    result = await segmentation_service.delete_segmentation_mask(crop_id, db)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("error")
        )

    return result
