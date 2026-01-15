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
    x: float = Field(..., description="X coordinate in image pixels")
    y: float = Field(..., description="Y coordinate in image pixels")
    label: int = Field(..., ge=0, le=1, description="1 = foreground, 0 = background")


class SegmentRequest(BaseModel):
    """Request for interactive segmentation."""
    image_id: int = Field(..., description="ID of the image to segment")
    points: List[ClickPoint] = Field(..., min_length=1, description="Click points")


class SegmentResponse(BaseModel):
    """Response from interactive segmentation."""
    success: bool
    polygon: Optional[List[List[float]]] = None
    iou_score: Optional[float] = None
    area_pixels: Optional[int] = None
    error: Optional[str] = None


class SaveMaskRequest(BaseModel):
    """Request to save a finalized segmentation mask."""
    crop_id: int = Field(..., description="ID of the cell crop")
    polygon: List[List[float]] = Field(..., min_length=3, description="Polygon points [[x, y], ...]")
    iou_score: float = Field(..., ge=0, le=1, description="SAM IoU prediction score")
    prompt_count: int = Field(..., ge=0, description="Number of click prompts used")


class SaveFOVMaskRequest(BaseModel):
    """Request to save a FOV-level segmentation mask."""
    image_id: int = Field(..., description="ID of the FOV image")
    polygon: List[List[float]] = Field(..., min_length=3, description="Polygon points [[x, y], ...]")
    iou_score: float = Field(..., ge=0, le=1, description="SAM IoU prediction score")
    prompt_count: int = Field(..., ge=0, description="Number of click prompts used")


class SaveFOVMaskUnionRequest(BaseModel):
    """Request to save FOV mask with union - merges multiple polygons with existing."""
    image_id: int = Field(..., description="ID of the FOV image")
    polygons: List[List[List[float]]] = Field(..., min_length=1, description="List of polygons to merge")
    iou_score: float = Field(default=0.9, ge=0, le=1, description="Average IoU score")
    prompt_count: int = Field(default=1, ge=0, description="Number of prompts used")


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


class FOVMaskResponse(BaseModel):
    """FOV-level segmentation mask."""
    has_mask: bool
    polygon: Optional[List[List[float]]] = None
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
    # Convert points to tuples (round floats to int for SAM)
    point_coords = [(int(round(p.x)), int(round(p.y))) for p in request.points]
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


# ============================================================================
# FOV-Level Segmentation Endpoints
# ============================================================================

@router.post("/save-fov-mask")
async def save_fov_mask(
    request: SaveFOVMaskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Save FOV-level segmentation mask.

    The polygon covers the entire field of view. Individual cell masks
    are then extracted as clips from this FOV mask based on their bounding boxes.
    """
    result = await segmentation_service.save_fov_segmentation_mask(
        image_id=request.image_id,
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


@router.post("/save-fov-mask-union")
async def save_fov_mask_union(
    request: SaveFOVMaskUnionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Save FOV-level segmentation mask with union support.

    Accepts multiple polygons and merges them with any existing mask.
    This is useful for accumulating segmentation results before saving.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from models.image import Image
    from models.experiment import Experiment

    # Verify ownership - user must own the image to save segmentation
    result = await db.execute(
        select(Image)
        .options(selectinload(Image.experiment))
        .where(Image.id == request.image_id)
    )
    image = result.scalar_one_or_none()

    if not image or image.experiment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found or access denied"
        )

    # Convert polygon points to tuples
    polygons = [
        [tuple(p) for p in polygon]
        for polygon in request.polygons
    ]

    result = await segmentation_service.save_fov_segmentation_mask_union(
        image_id=request.image_id,
        polygons=polygons,
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


@router.get("/fov-mask/{image_id}", response_model=FOVMaskResponse)
async def get_fov_mask(
    image_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get FOV-level segmentation mask for an image.

    Returns has_mask=false if no mask has been saved for this image.
    """
    result = await segmentation_service.get_fov_segmentation_mask(image_id, db)
    return FOVMaskResponse(**result)


@router.delete("/fov-mask/{image_id}")
async def delete_fov_mask(
    image_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete FOV-level segmentation mask.
    """
    result = await segmentation_service.delete_fov_segmentation_mask(image_id, db)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("error")
        )

    return result


# ============================================================================
# SAM 3 Text Segmentation Endpoints
# ============================================================================

class TextSegmentRequest(BaseModel):
    """Request for text-based segmentation."""
    image_id: int = Field(..., description="ID of the image to segment")
    text_prompt: str = Field(..., min_length=1, max_length=200, description="Natural language description")
    confidence_threshold: float = Field(default=0.5, ge=0.1, le=1.0, description="Minimum confidence")


class TextSegmentInstance(BaseModel):
    """A single detected instance from text segmentation."""
    index: int
    polygon: List[List[float]]
    bbox: List[float]  # [x1, y1, x2, y2]
    score: float
    area_pixels: int


class TextSegmentResponse(BaseModel):
    """Response from text-based segmentation."""
    success: bool
    instances: Optional[List[TextSegmentInstance]] = None
    prompt: Optional[str] = None
    error: Optional[str] = None


class TextRefineRequest(BaseModel):
    """Request to refine a text-detected instance with point prompts."""
    image_id: int = Field(..., description="ID of the image")
    text_prompt: str = Field(..., min_length=1, max_length=200, description="Original text prompt")
    instance_index: int = Field(..., ge=0, description="Index of instance to refine")
    points: List[ClickPoint] = Field(..., min_length=1, description="Refinement click points")


class CapabilitiesResponse(BaseModel):
    """SAM capabilities response."""
    device: str  # "cuda", "mps", "cpu"
    variant: str  # "mobilesam", "sam3"
    supports_text_prompts: bool
    model_name: str


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(
    current_user: User = Depends(get_current_user),
):
    """
    Get segmentation capabilities.

    Returns information about the current SAM setup:
    - device: Current compute device (cuda, mps, cpu)
    - variant: SAM variant in use (mobilesam, sam3)
    - supports_text_prompts: Whether text prompting is available
    - model_name: Human-readable model name

    Text prompting requires SAM 3, which requires CUDA GPU.
    """
    caps = segmentation_service.get_segmentation_capabilities()
    return CapabilitiesResponse(
        device=caps["device"],
        variant=caps["variant"],
        supports_text_prompts=caps["supports_text_prompts"],
        model_name=caps["model_name"],
    )


@router.post("/segment-text", response_model=TextSegmentResponse)
async def segment_text(
    request: TextSegmentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Run text-based segmentation using SAM 3.

    Finds all instances matching the text description (e.g., "cell", "nucleus").
    Returns a list of detected instances with polygons, bounding boxes, and scores.

    Requires:
    - CUDA GPU (SAM 3 text prompts not supported on MPS/CPU)
    - Valid image that exists in the database

    Note: This is slower than point-based segmentation (~200-500ms vs ~10-50ms).
    """
    result = await segmentation_service.segment_from_text(
        image_id=request.image_id,
        text_prompt=request.text_prompt,
        confidence_threshold=request.confidence_threshold,
        db=db,
    )

    if not result["success"]:
        return TextSegmentResponse(success=False, error=result.get("error"))

    instances = [
        TextSegmentInstance(
            index=inst["index"],
            polygon=inst["polygon"],
            bbox=inst["bbox"],
            score=inst["score"],
            area_pixels=inst["area_pixels"],
        )
        for inst in result.get("instances", [])
    ]

    return TextSegmentResponse(
        success=True,
        instances=instances,
        prompt=result.get("prompt"),
    )


@router.post("/segment-text-refine", response_model=SegmentResponse)
async def segment_text_refine(
    request: TextRefineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Refine a text-detected instance using point prompts.

    After running text segmentation, use this endpoint to refine a specific
    instance with additional point clicks. Left-click adds to mask,
    right-click removes from mask.

    This combines the initial text detection with interactive refinement.
    """
    point_coords = [(int(round(p.x)), int(round(p.y))) for p in request.points]
    point_labels = [p.label for p in request.points]

    result = await segmentation_service.refine_text_segmentation(
        image_id=request.image_id,
        text_prompt=request.text_prompt,
        instance_index=request.instance_index,
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
