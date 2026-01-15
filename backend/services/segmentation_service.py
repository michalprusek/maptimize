"""
High-level segmentation service coordinating encoder, decoder, and database.

This service provides the main API for:
- Computing and storing SAM embeddings
- Running interactive segmentation from click prompts
- Saving and retrieving segmentation masks
"""

import asyncio
import logging
from typing import List, Optional, Tuple, Dict, Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.image import Image
from models.sam_embedding import SAMEmbedding
from models.segmentation import SegmentationMask, UserSegmentationPrompt, FOVSegmentationMask
from models.cell_crop import CellCrop
from ml.segmentation.sam_encoder import get_sam_encoder
from ml.segmentation.sam_decoder import get_sam_decoder
from ml.segmentation.utils import mask_to_polygon, mask_to_polygons, calculate_polygon_area

logger = logging.getLogger(__name__)


async def compute_sam_embedding(
    image_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Compute and store SAM embedding for an image.

    Called during upload processing or on-demand when user opens
    segmentation mode.

    Args:
        image_id: Database ID of the image
        db: Async database session

    Returns:
        Dict with success status and details
    """
    # Get image
    result = await db.execute(select(Image).where(Image.id == image_id))
    image = result.scalar_one_or_none()

    if not image:
        return {"success": False, "error": "Image not found"}

    # Determine source path (prefer MIP projection)
    source_path = image.mip_path or image.file_path
    if not source_path:
        return {"success": False, "error": "No image file available"}

    # Update status to computing
    image.sam_embedding_status = "computing"
    await db.commit()

    try:
        # Get encoder and compute embedding
        encoder = get_sam_encoder()

        # Run encoding in thread pool to avoid blocking event loop
        loop = asyncio.get_running_loop()
        embedding, width, height = await loop.run_in_executor(
            None,
            lambda: encoder.encode_image(source_path)
        )

        # Compress for storage
        compressed = encoder.compress_embedding(embedding)

        # Check for existing embedding and delete
        existing = await db.execute(
            select(SAMEmbedding).where(SAMEmbedding.image_id == image_id)
        )
        existing_emb = existing.scalar_one_or_none()
        if existing_emb:
            await db.delete(existing_emb)
            await db.flush()

        # Create new embedding record
        sam_embedding = SAMEmbedding(
            image_id=image_id,
            model_variant=encoder.model_name,
            embedding_data=compressed,
            embedding_shape=",".join(map(str, embedding.shape)),
            original_width=width,
            original_height=height,
        )

        db.add(sam_embedding)
        image.sam_embedding_status = "ready"
        await db.commit()

        logger.info(
            f"SAM embedding computed for image {image_id}: "
            f"{len(compressed) / 1024 / 1024:.1f}MB"
        )

        return {
            "success": True,
            "embedding_size": len(compressed),
            "image_shape": (width, height),
            "embedding_shape": embedding.shape,
        }

    except Exception as e:
        logger.exception(f"Failed to compute SAM embedding for image {image_id}")
        image.sam_embedding_status = "error"
        await db.commit()
        return {"success": False, "error": str(e)}


async def get_embedding_status(
    image_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Get SAM embedding status for an image.

    Args:
        image_id: Database ID of the image
        db: Async database session

    Returns:
        Dict with status information
    """
    result = await db.execute(
        select(Image, SAMEmbedding)
        .outerjoin(SAMEmbedding, Image.id == SAMEmbedding.image_id)
        .where(Image.id == image_id)
    )
    row = result.one_or_none()

    if not row:
        return {"status": "not_found", "has_embedding": False}

    image, sam_embedding = row

    return {
        "image_id": image_id,
        "status": image.sam_embedding_status or "not_started",
        "has_embedding": sam_embedding is not None,
        "embedding_shape": sam_embedding.embedding_shape if sam_embedding else None,
        "model_variant": sam_embedding.model_variant if sam_embedding else None,
    }


async def segment_from_prompts(
    image_id: int,
    point_coords: List[Tuple[int, int]],
    point_labels: List[int],
    db: AsyncSession,
    multimask_output: bool = False,
) -> Dict[str, Any]:
    """
    Run interactive segmentation from click prompts.

    This is the main inference endpoint. Uses pre-computed embedding
    for fast response (~10-50ms).

    Args:
        image_id: Database ID of the image
        point_coords: List of (x, y) click coordinates
        point_labels: List of labels (1=foreground, 0=background)
        db: Async database session
        multimask_output: Whether to return multiple mask options

    Returns:
        Dict with polygon, IoU score, and area
    """
    # Get image and embedding
    result = await db.execute(
        select(Image, SAMEmbedding)
        .join(SAMEmbedding, Image.id == SAMEmbedding.image_id)
        .where(Image.id == image_id)
    )
    row = result.one_or_none()

    if not row:
        return {"success": False, "error": "Image or embedding not found"}

    image, sam_embedding = row

    # Decompress embedding
    encoder = get_sam_encoder()
    shape = tuple(map(int, sam_embedding.embedding_shape.split(",")))

    # Run decompression in thread pool
    loop = asyncio.get_running_loop()
    embedding = await loop.run_in_executor(
        None,
        lambda: encoder.decompress_embedding(sam_embedding.embedding_data, shape)
    )

    # Run decoder inference
    decoder = get_sam_decoder()

    def run_inference():
        return decoder.predict_mask(
            embedding=embedding,
            image_shape=(sam_embedding.original_height, sam_embedding.original_width),
            point_coords=point_coords,
            point_labels=point_labels,
            multimask_output=multimask_output,
        )

    mask, iou_score, _ = await loop.run_in_executor(None, run_inference)

    # Convert mask to polygon using utility function
    polygon = await loop.run_in_executor(
        None,
        lambda: mask_to_polygon(mask)
    )

    # Calculate area from mask (count True pixels)
    area = int(np.sum(mask))

    return {
        "success": True,
        "polygon": polygon,
        "iou_score": float(iou_score),
        "area_pixels": area,
        "mask_shape": list(mask.shape),
    }


async def save_segmentation_mask(
    crop_id: int,
    polygon: List[Tuple[int, int]],
    iou_score: float,
    prompt_count: int,
    db: AsyncSession,
    creation_method: str = "interactive",
) -> Dict[str, Any]:
    """
    Save finalized segmentation mask for a cell crop.

    Args:
        crop_id: Database ID of the cell crop
        polygon: List of (x, y) polygon points
        iou_score: SAM's IoU prediction score
        prompt_count: Number of click prompts used
        db: Async database session
        creation_method: How the mask was created

    Returns:
        Dict with success status
    """
    # Get crop
    result = await db.execute(select(CellCrop).where(CellCrop.id == crop_id))
    crop = result.scalar_one_or_none()

    if not crop:
        logger.warning(f"Crop not found: crop_id={crop_id}")
        return {"success": False, "error": "Crop not found"}

    # Calculate area using shoelace formula
    area = calculate_polygon_area(polygon)

    # Create or update mask
    existing = await db.execute(
        select(SegmentationMask).where(SegmentationMask.cell_crop_id == crop_id)
    )
    existing_mask = existing.scalar_one_or_none()

    if existing_mask:
        existing_mask.polygon_points = [list(p) for p in polygon]
        existing_mask.area_pixels = area
        existing_mask.iou_score = iou_score
        existing_mask.prompt_count = prompt_count
        existing_mask.creation_method = creation_method
    else:
        mask = SegmentationMask(
            cell_crop_id=crop_id,
            polygon_points=[list(p) for p in polygon],
            area_pixels=area,
            iou_score=iou_score,
            creation_method=creation_method,
            prompt_count=prompt_count,
        )
        db.add(mask)

    await db.commit()

    logger.info(f"Saved segmentation mask for crop {crop_id}: {len(polygon)} points")

    return {"success": True, "crop_id": crop_id, "area_pixels": area}


async def get_segmentation_mask(
    crop_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Get segmentation mask for a cell crop.

    Args:
        crop_id: Database ID of the cell crop
        db: Async database session

    Returns:
        Dict with mask data or has_mask=False
    """
    result = await db.execute(
        select(SegmentationMask).where(SegmentationMask.cell_crop_id == crop_id)
    )
    mask = result.scalar_one_or_none()

    if not mask:
        return {"has_mask": False}

    return {
        "has_mask": True,
        "polygon": mask.polygon_points,
        "iou_score": mask.iou_score,
        "area_pixels": mask.area_pixels,
        "creation_method": mask.creation_method,
        "prompt_count": mask.prompt_count,
    }


async def get_segmentation_masks_batch(
    crop_ids: List[int],
    db: AsyncSession,
) -> Dict[int, Dict[str, Any]]:
    """
    Get segmentation masks for multiple crops at once.

    Args:
        crop_ids: List of cell crop IDs
        db: Async database session

    Returns:
        Dict mapping crop_id to mask data
    """
    if not crop_ids:
        return {}

    result = await db.execute(
        select(SegmentationMask).where(SegmentationMask.cell_crop_id.in_(crop_ids))
    )
    masks = result.scalars().all()

    return {
        mask.cell_crop_id: {
            "polygon": mask.polygon_points,
            "iou_score": mask.iou_score,
            "area_pixels": mask.area_pixels,
            "creation_method": mask.creation_method,
        }
        for mask in masks
    }


async def delete_segmentation_mask(
    crop_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Delete segmentation mask for a cell crop.

    Args:
        crop_id: Database ID of the cell crop
        db: Async database session

    Returns:
        Dict with success status
    """
    result = await db.execute(
        select(SegmentationMask).where(SegmentationMask.cell_crop_id == crop_id)
    )
    mask = result.scalar_one_or_none()

    if not mask:
        return {"success": False, "error": "Mask not found"}

    await db.delete(mask)
    await db.commit()

    return {"success": True, "crop_id": crop_id}


async def save_user_prompt(
    user_id: int,
    image_id: int,
    click_points: List[Dict[str, int]],
    result_polygon: Optional[List[Tuple[int, int]]],
    db: AsyncSession,
    experiment_id: Optional[int] = None,
    crop_id: Optional[int] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Save user's click prompts as an exemplar for future use.

    Args:
        user_id: Database ID of the user
        image_id: Database ID of the source image
        click_points: List of click points [{"x": int, "y": int, "label": int}, ...]
        result_polygon: The resulting polygon from these prompts
        db: Async database session
        experiment_id: Optional experiment scope
        crop_id: Optional associated crop
        name: Optional descriptive name

    Returns:
        Dict with success status and prompt ID
    """
    prompt = UserSegmentationPrompt(
        user_id=user_id,
        experiment_id=experiment_id,
        source_image_id=image_id,
        source_crop_id=crop_id,
        click_points=click_points,
        result_polygon=[list(p) for p in result_polygon] if result_polygon else None,
        name=name,
    )

    db.add(prompt)
    await db.commit()
    await db.refresh(prompt)

    return {"success": True, "prompt_id": prompt.id}


async def queue_sam_embedding(image_id: int) -> None:
    """
    Queue SAM embedding computation as a background task.

    This is called from the upload pipeline to trigger async encoding.

    Args:
        image_id: Database ID of the image
    """
    from database import get_db_context

    try:
        async with get_db_context() as db:
            # Update status to pending
            result = await db.execute(select(Image).where(Image.id == image_id))
            image = result.scalar_one_or_none()

            if image and image.sam_embedding_status is None:
                image.sam_embedding_status = "pending"
                await db.commit()

            # Compute embedding
            await compute_sam_embedding(image_id, db)
    except Exception as e:
        logger.exception(f"Background SAM embedding task failed for image {image_id}")
        # Try to update status to error
        try:
            async with get_db_context() as db:
                result = await db.execute(select(Image).where(Image.id == image_id))
                image = result.scalar_one_or_none()
                if image:
                    image.sam_embedding_status = "error"
                    await db.commit()
        except Exception:
            logger.exception(f"Failed to update error status for image {image_id}")


# ============================================================================
# FOV-Level Segmentation Functions
# ============================================================================

async def save_fov_segmentation_mask(
    image_id: int,
    polygon: List[Tuple[int, int]],
    iou_score: float,
    prompt_count: int,
    db: AsyncSession,
    creation_method: str = "interactive",
) -> Dict[str, Any]:
    """
    Save FOV-level segmentation mask for an image.

    Args:
        image_id: Database ID of the image
        polygon: List of (x, y) polygon points in FOV coordinates
        iou_score: SAM's IoU prediction score
        prompt_count: Number of click prompts used
        db: Async database session
        creation_method: How the mask was created

    Returns:
        Dict with success status
    """
    # Get image
    result = await db.execute(select(Image).where(Image.id == image_id))
    image = result.scalar_one_or_none()

    if not image:
        logger.warning(f"Image not found for FOV mask: image_id={image_id}")
        return {"success": False, "error": "Image not found"}

    # Calculate area using shoelace formula
    area = calculate_polygon_area(polygon)

    # Create or update mask
    existing = await db.execute(
        select(FOVSegmentationMask).where(FOVSegmentationMask.image_id == image_id)
    )
    existing_mask = existing.scalar_one_or_none()

    if existing_mask:
        existing_mask.polygon_points = [list(p) for p in polygon]
        existing_mask.area_pixels = area
        existing_mask.iou_score = iou_score
        existing_mask.prompt_count = prompt_count
        existing_mask.creation_method = creation_method
    else:
        mask = FOVSegmentationMask(
            image_id=image_id,
            polygon_points=[list(p) for p in polygon],
            area_pixels=area,
            iou_score=iou_score,
            creation_method=creation_method,
            prompt_count=prompt_count,
        )
        db.add(mask)

    await db.commit()

    logger.info(f"Saved FOV segmentation mask for image {image_id}: {len(polygon)} points")

    return {"success": True, "image_id": image_id, "area_pixels": area}


async def save_fov_segmentation_mask_union(
    image_id: int,
    polygons: List[List[Tuple[int, int]]],
    iou_score: float,
    prompt_count: int,
    db: AsyncSession,
    creation_method: str = "interactive_union",
) -> Dict[str, Any]:
    """
    Save FOV-level segmentation mask with union support.

    Accepts multiple polygons and merges them with any existing mask.
    Uses OpenCV to create masks, union them, then extract ALL contours.
    Stores as list of polygons to preserve separate instances.

    Args:
        image_id: Database ID of the image
        polygons: List of polygons, each a list of (x, y) points
        iou_score: Average IoU score
        prompt_count: Total number of prompts used
        db: Async database session
        creation_method: How the mask was created

    Returns:
        Dict with success status and all polygons
    """
    import cv2

    # Get image to know dimensions
    result = await db.execute(select(Image).where(Image.id == image_id))
    image = result.scalar_one_or_none()

    if not image:
        logger.warning(f"Image not found for FOV mask union: image_id={image_id}")
        return {"success": False, "error": "Image not found"}

    # Get image dimensions
    img_width = image.width or 2048
    img_height = image.height or 2048

    # Create combined mask from all new polygons
    combined_mask = np.zeros((img_height, img_width), dtype=np.uint8)

    for polygon in polygons:
        if len(polygon) >= 3:
            pts = np.array([[int(p[0]), int(p[1])] for p in polygon], dtype=np.int32)
            cv2.fillPoly(combined_mask, [pts], 1)

    # Check for existing mask and union with it
    existing_result = await db.execute(
        select(FOVSegmentationMask).where(FOVSegmentationMask.image_id == image_id)
    )
    existing_mask = existing_result.scalar_one_or_none()

    if existing_mask and existing_mask.polygon_points:
        existing_data = existing_mask.polygon_points
        # Handle both formats: single polygon [[x,y],...] or multi [[poly1], [poly2],...]
        if existing_data and len(existing_data) > 0:
            # Check if it's multi-polygon format (list of lists of lists)
            if isinstance(existing_data[0], list) and len(existing_data[0]) > 0 and isinstance(existing_data[0][0], list):
                # Multi-polygon format
                for poly in existing_data:
                    if len(poly) >= 3:
                        pts = np.array([[int(p[0]), int(p[1])] for p in poly], dtype=np.int32)
                        cv2.fillPoly(combined_mask, [pts], 1)
            else:
                # Single polygon format
                if len(existing_data) >= 3:
                    pts = np.array([[int(p[0]), int(p[1])] for p in existing_data], dtype=np.int32)
                    cv2.fillPoly(combined_mask, [pts], 1)

    # Convert combined mask back to ALL polygons (not just largest)
    all_polygons = mask_to_polygons(combined_mask, simplify_tolerance=2.0, min_area=50)

    if len(all_polygons) == 0:
        logger.warning(f"Union resulted in no valid polygons")
        return {"success": False, "error": "Union resulted in no valid polygons"}

    # Calculate total area
    total_area = sum(calculate_polygon_area(poly) for poly in all_polygons)

    # Store as list of polygons (multi-polygon format)
    polygon_data = [[list(p) for p in poly] for poly in all_polygons]

    # Save all polygons
    if existing_mask:
        existing_mask.polygon_points = polygon_data
        existing_mask.area_pixels = total_area
        existing_mask.iou_score = iou_score
        existing_mask.prompt_count = prompt_count
        existing_mask.creation_method = creation_method
    else:
        mask = FOVSegmentationMask(
            image_id=image_id,
            polygon_points=polygon_data,
            area_pixels=total_area,
            iou_score=iou_score,
            creation_method=creation_method,
            prompt_count=prompt_count,
        )
        db.add(mask)

    await db.commit()

    logger.info(f"Saved FOV segmentation mask union for image {image_id}: {len(all_polygons)} polygons from {len(polygons)} input polygons")

    return {
        "success": True,
        "image_id": image_id,
        "area_pixels": total_area,
        "polygons": polygon_data,
        "polygon_count": len(all_polygons),
    }


async def get_fov_segmentation_mask(
    image_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Get FOV-level segmentation mask for an image.

    Args:
        image_id: Database ID of the image
        db: Async database session

    Returns:
        Dict with mask data or has_mask=False
    """
    result = await db.execute(
        select(FOVSegmentationMask).where(FOVSegmentationMask.image_id == image_id)
    )
    mask = result.scalar_one_or_none()

    if not mask:
        return {"has_mask": False}

    return {
        "has_mask": True,
        "polygon": mask.polygon_points,
        "iou_score": mask.iou_score,
        "area_pixels": mask.area_pixels,
        "creation_method": mask.creation_method,
        "prompt_count": mask.prompt_count,
    }


async def delete_fov_segmentation_mask(
    image_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Delete FOV-level segmentation mask for an image.

    Args:
        image_id: Database ID of the image
        db: Async database session

    Returns:
        Dict with success status
    """
    result = await db.execute(
        select(FOVSegmentationMask).where(FOVSegmentationMask.image_id == image_id)
    )
    mask = result.scalar_one_or_none()

    if not mask:
        return {"success": False, "error": "FOV mask not found"}

    await db.delete(mask)
    await db.commit()

    return {"success": True, "image_id": image_id}


# ============================================================================
# SAM 3 Text Segmentation Functions
# ============================================================================

def get_segmentation_capabilities() -> Dict[str, Any]:
    """
    Get capabilities of the current SAM setup.

    Returns device, model variant, and whether text prompting is supported.
    Text prompting requires SAM 3, which requires CUDA.

    Returns:
        Dict with device, variant, supports_text_prompts, model_name
    """
    from ml.segmentation.sam_factory import get_capabilities
    return get_capabilities()


async def segment_from_text(
    image_id: int,
    text_prompt: str,
    confidence_threshold: float,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Run text-based segmentation using SAM 3.

    Finds all instances matching the text description.
    Requires CUDA GPU (SAM 3).

    Args:
        image_id: Database ID of the image
        text_prompt: Natural language description (e.g., "cell", "nucleus")
        confidence_threshold: Minimum confidence to include (0.0-1.0)
        db: Async database session

    Returns:
        Dict with instances (polygons, boxes, scores) or error
    """
    from ml.segmentation.sam_factory import text_segmentation_available, detect_device

    # Check if text segmentation is available
    if not text_segmentation_available():
        return {
            "success": False,
            "error": f"Text segmentation requires CUDA GPU. Current device: {detect_device()}",
        }

    # Get image path
    result = await db.execute(select(Image).where(Image.id == image_id))
    image = result.scalar_one_or_none()

    if not image:
        return {"success": False, "error": "Image not found"}

    # Determine source path (prefer MIP projection)
    source_path = image.mip_path or image.file_path
    if not source_path:
        return {"success": False, "error": "No image file available"}

    try:
        from ml.segmentation.sam3_encoder import get_sam3_encoder

        encoder = get_sam3_encoder()

        # Run inference in thread pool
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: encoder.predict_with_text(
                image_path=source_path,
                text_prompt=text_prompt,
                confidence_threshold=confidence_threshold,
            )
        )

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "Unknown error")}

        # Format instances for API response
        instances = []
        for i in range(len(result.get("polygons", []))):
            instances.append({
                "index": i,
                "polygon": result["polygons"][i],
                "bbox": result["boxes"][i] if i < len(result["boxes"]) else [0, 0, 0, 0],
                "score": result["scores"][i] if i < len(result["scores"]) else 0.0,
                "area_pixels": result["areas"][i] if i < len(result["areas"]) else 0,
            })

        logger.info(f"Text segmentation found {len(instances)} instances for '{text_prompt}'")

        return {
            "success": True,
            "instances": instances,
            "prompt": text_prompt,
        }

    except Exception as e:
        logger.exception(f"Text segmentation failed for image {image_id}")
        return {"success": False, "error": str(e)}


async def refine_text_segmentation(
    image_id: int,
    text_prompt: str,
    instance_index: int,
    point_coords: List[Tuple[int, int]],
    point_labels: List[int],
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Refine a text-detected instance using point prompts.

    First runs text query, then uses point prompts to refine the selected instance.

    Args:
        image_id: Database ID of the image
        text_prompt: Original text prompt
        instance_index: Which detected instance to refine (0-indexed)
        point_coords: List of (x, y) click coordinates
        point_labels: List of labels (1=foreground, 0=background)
        db: Async database session

    Returns:
        Dict with refined polygon, score, and area
    """
    from ml.segmentation.sam_factory import text_segmentation_available

    if not text_segmentation_available():
        return {"success": False, "error": "Text segmentation not available"}

    # Get image path
    result = await db.execute(select(Image).where(Image.id == image_id))
    image = result.scalar_one_or_none()

    if not image:
        return {"success": False, "error": "Image not found"}

    source_path = image.mip_path or image.file_path
    if not source_path:
        return {"success": False, "error": "No image file available"}

    try:
        from ml.segmentation.sam3_encoder import get_sam3_encoder

        encoder = get_sam3_encoder()

        # Run refinement in thread pool
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: encoder.refine_with_points(
                image_path=source_path,
                text_prompt=text_prompt,
                instance_index=instance_index,
                point_coords=point_coords,
                point_labels=point_labels,
            )
        )

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "Refinement failed")}

        return {
            "success": True,
            "polygon": result["polygon"],
            "iou_score": result.get("score", 0.9),
            "area_pixels": result.get("area", 0),
        }

    except Exception as e:
        logger.exception(f"Text refinement failed for image {image_id}")
        return {"success": False, "error": str(e)}
