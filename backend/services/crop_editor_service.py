"""Crop editor service - handles manual crop editing operations.

This service provides functions for:
- Validating bounding box coordinates
- Extracting crop pixels from FOV projections
- Saving crop images
- Regenerating crop features after bbox changes

SSOT for crop editing business logic.
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image as PILImage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.cell_crop import CellCrop
from models.image import Image
from ml.detection import normalize_image

logger = logging.getLogger(__name__)


# =============================================================================
# Validation Functions
# =============================================================================


def validate_bbox_within_image(
    bbox_x: int,
    bbox_y: int,
    bbox_w: int,
    bbox_h: int,
    image_width: int,
    image_height: int,
) -> Tuple[bool, Optional[str]]:
    """
    Validate that bbox is within image bounds.

    Args:
        bbox_x: Bounding box X coordinate (left)
        bbox_y: Bounding box Y coordinate (top)
        bbox_w: Bounding box width
        bbox_h: Bounding box height
        image_width: Parent image width
        image_height: Parent image height

    Returns:
        Tuple of (is_valid, error_message)
    """
    if bbox_x < 0 or bbox_y < 0:
        return False, "Bbox coordinates cannot be negative"
    if bbox_x + bbox_w > image_width:
        return False, f"Bbox exceeds image width ({bbox_x + bbox_w} > {image_width})"
    if bbox_y + bbox_h > image_height:
        return False, f"Bbox exceeds image height ({bbox_y + bbox_h} > {image_height})"
    if bbox_w < 10 or bbox_h < 10:
        return False, "Bbox dimensions must be at least 10 pixels"
    return True, None


# =============================================================================
# Image Processing Functions
# =============================================================================


def extract_crop_from_projection(
    projection: np.ndarray,
    bbox_x: int,
    bbox_y: int,
    bbox_w: int,
    bbox_h: int,
) -> np.ndarray:
    """
    Extract crop pixels from projection array.

    Args:
        projection: 2D numpy array of the projection image
        bbox_x: Bounding box X coordinate
        bbox_y: Bounding box Y coordinate
        bbox_w: Bounding box width
        bbox_h: Bounding box height

    Returns:
        Cropped numpy array
    """
    return projection[bbox_y:bbox_y + bbox_h, bbox_x:bbox_x + bbox_w]


def save_crop_image(
    crop_pixels: np.ndarray,
    crops_dir: Path,
    bbox_x: int,
    bbox_y: int,
    suffix: str,
) -> Path:
    """
    Save crop image to disk.

    Args:
        crop_pixels: Numpy array of crop pixels
        crops_dir: Directory to save crops
        bbox_x: Bbox X coordinate (for filename)
        bbox_y: Bbox Y coordinate (for filename)
        suffix: "mip" or "sum"

    Returns:
        Path to saved crop file
    """
    crop_8bit = normalize_image(crop_pixels)
    crops_dir.mkdir(exist_ok=True)

    crop_path = crops_dir / f"cell_{bbox_x}_{bbox_y}_{suffix}.png"
    pil_img = PILImage.fromarray(crop_8bit)
    pil_img.save(crop_path)

    return crop_path


def delete_crop_files(crop: CellCrop) -> None:
    """
    Delete crop image files from disk.

    Args:
        crop: CellCrop model instance
    """
    for path_str in [crop.mip_path, crop.sum_crop_path]:
        if path_str:
            path = Path(path_str)
            if path.exists():
                try:
                    path.unlink()
                    logger.debug(f"Deleted crop file: {path}")
                except OSError as e:
                    logger.warning(f"Failed to delete crop file {path}: {e}")


# =============================================================================
# Regeneration Functions
# =============================================================================


async def regenerate_crop_features(
    crop: CellCrop,
    image: Image,
    db: AsyncSession,
) -> dict:
    """
    Regenerate crop images and features after bbox change.

    This function:
    1. Loads the parent FOV MIP projection
    2. Validates the bbox is within bounds
    3. Deletes old crop files
    4. Extracts new crop from MIP
    5. Optionally extracts from SUM projection
    6. Saves new crop images
    7. Calculates mean_intensity
    8. Extracts new DINOv3 embedding
    9. Clears UMAP coordinates

    Args:
        crop: CellCrop to regenerate
        image: Parent FOV Image
        db: Database session

    Returns:
        dict with success status and details
    """
    from ml.features import extract_features_for_crops
    from services.umap_service import invalidate_crop_umap

    # Determine MIP source path
    if image.mip_path and Path(image.mip_path).exists():
        mip_source = image.mip_path
    elif Path(image.file_path).exists():
        # For 2D images, use original file
        mip_source = image.file_path
    else:
        return {"success": False, "error": "No MIP or source file available"}

    # Load MIP projection
    try:
        mip = np.array(PILImage.open(mip_source))
    except Exception as e:
        return {"success": False, "error": f"Failed to load MIP: {e}"}

    # Validate bbox within image bounds
    is_valid, error = validate_bbox_within_image(
        crop.bbox_x,
        crop.bbox_y,
        crop.bbox_w,
        crop.bbox_h,
        image.width,
        image.height,
    )
    if not is_valid:
        return {"success": False, "error": error}

    # Delete old crop files
    delete_crop_files(crop)

    # Determine crops directory
    upload_dir = Path(image.file_path).parent
    crops_dir = upload_dir / "crops"

    # Extract and save new MIP crop
    mip_crop = extract_crop_from_projection(
        mip, crop.bbox_x, crop.bbox_y, crop.bbox_w, crop.bbox_h
    )
    crop.mip_path = str(
        save_crop_image(mip_crop, crops_dir, crop.bbox_x, crop.bbox_y, "mip")
    )

    # Extract and save SUM crop if available
    if image.sum_path and Path(image.sum_path).exists():
        try:
            sum_proj = np.array(PILImage.open(image.sum_path))
            sum_crop = extract_crop_from_projection(
                sum_proj, crop.bbox_x, crop.bbox_y, crop.bbox_w, crop.bbox_h
            )
            crop.sum_crop_path = str(
                save_crop_image(sum_crop, crops_dir, crop.bbox_x, crop.bbox_y, "sum")
            )
        except Exception as e:
            logger.warning(f"Failed to extract SUM crop: {e}")
            crop.sum_crop_path = None

    # Calculate mean intensity from new MIP crop
    crop.mean_intensity = float(np.mean(mip_crop))

    # Clear embedding (will be recomputed)
    crop.embedding = None
    crop.embedding_model = None

    # Clear UMAP coordinates
    crop.umap_x = None
    crop.umap_y = None
    crop.umap_computed_at = None

    await db.flush()

    # Extract new DINOv3 embedding
    embedding_extracted = False
    embedding_error = None
    try:
        result = await extract_features_for_crops([crop.id], db)
        embedding_extracted = result.get("success", 0) > 0
        if not embedding_extracted:
            embedding_error = result.get("error", "Unknown embedding error")
    except Exception as e:
        logger.error(f"Failed to extract embedding for crop {crop.id}: {e}")
        embedding_error = str(e)

    # Invalidate UMAP for all crops in this experiment
    umap_invalidated = False
    try:
        await invalidate_crop_umap(db, image_id=image.id)
        umap_invalidated = True
    except Exception as e:
        logger.warning(f"Failed to invalidate UMAP: {e}")

    # Determine overall success status
    # Partial success = crop regenerated but embedding failed
    warnings = []
    if not embedding_extracted:
        warnings.append(f"Embedding extraction failed: {embedding_error}")
    if not umap_invalidated:
        warnings.append("UMAP invalidation failed")

    return {
        "success": True,  # Crop itself was regenerated
        "partial_success": len(warnings) > 0,
        "embedding_extracted": embedding_extracted,
        "umap_invalidated": umap_invalidated,
        "warnings": warnings if warnings else None,
        "mip_path": crop.mip_path,
        "sum_crop_path": crop.sum_crop_path,
        "mean_intensity": crop.mean_intensity,
    }


async def create_manual_crop(
    image: Image,
    bbox_x: int,
    bbox_y: int,
    bbox_w: int,
    bbox_h: int,
    db: AsyncSession,
    map_protein_id: Optional[int] = None,
) -> Tuple[Optional[CellCrop], Optional[str]]:
    """
    Create a new manual crop on an FOV image.

    Args:
        image: Parent FOV Image
        bbox_x: Bounding box X coordinate
        bbox_y: Bounding box Y coordinate
        bbox_w: Bounding box width
        bbox_h: Bounding box height
        db: Database session
        map_protein_id: Optional MAP protein ID (defaults to image's protein)

    Returns:
        Tuple of (CellCrop or None, error message or None)
    """
    # Validate bbox
    is_valid, error = validate_bbox_within_image(
        bbox_x, bbox_y, bbox_w, bbox_h, image.width, image.height
    )
    if not is_valid:
        return None, error

    # Determine MIP source
    if image.mip_path and Path(image.mip_path).exists():
        mip_source = image.mip_path
    elif Path(image.file_path).exists():
        mip_source = image.file_path
    else:
        return None, "No MIP or source file available"

    # Load MIP projection
    try:
        mip = np.array(PILImage.open(mip_source))
    except Exception as e:
        return None, f"Failed to load MIP: {e}"

    # Extract crop
    mip_crop = extract_crop_from_projection(mip, bbox_x, bbox_y, bbox_w, bbox_h)

    # Determine crops directory and save
    upload_dir = Path(image.file_path).parent
    crops_dir = upload_dir / "crops"
    mip_path = save_crop_image(mip_crop, crops_dir, bbox_x, bbox_y, "mip")

    # Extract SUM crop if available
    sum_crop_path = None
    if image.sum_path and Path(image.sum_path).exists():
        try:
            sum_proj = np.array(PILImage.open(image.sum_path))
            sum_crop = extract_crop_from_projection(
                sum_proj, bbox_x, bbox_y, bbox_w, bbox_h
            )
            sum_crop_path = save_crop_image(sum_crop, crops_dir, bbox_x, bbox_y, "sum")
        except Exception as e:
            logger.warning(f"Failed to extract SUM crop: {e}")

    # Create CellCrop record
    crop = CellCrop(
        image_id=image.id,
        map_protein_id=map_protein_id or image.map_protein_id,
        bbox_x=bbox_x,
        bbox_y=bbox_y,
        bbox_w=bbox_w,
        bbox_h=bbox_h,
        detection_confidence=None,  # Manual crops have no detection confidence
        mip_path=str(mip_path),
        sum_crop_path=str(sum_crop_path) if sum_crop_path else None,
        mean_intensity=float(np.mean(mip_crop)),
        bundleness_score=None,
        skewness=None,
        kurtosis=None,
    )

    db.add(crop)
    await db.flush()

    return crop, None


async def verify_experiment_ownership(
    experiment_id: int,
    user_id: int,
    db: AsyncSession,
) -> Tuple[bool, Optional[str]]:
    """
    Verify user owns an experiment.

    DRY: Common ownership check pattern used across multiple routers.

    Args:
        experiment_id: ID of the experiment
        user_id: ID of the user
        db: Database session

    Returns:
        Tuple of (is_owner, error message or None)
    """
    from models.experiment import Experiment

    result = await db.execute(
        select(Experiment).where(
            Experiment.id == experiment_id,
            Experiment.user_id == user_id
        )
    )
    experiment = result.scalar_one_or_none()

    if not experiment:
        return False, "Experiment not found"

    return True, None


async def get_image_with_ownership_check(
    image_id: int,
    user_id: int,
    db: AsyncSession,
) -> Tuple[Optional[Image], Optional[str]]:
    """
    Get an image and verify user ownership.

    Args:
        image_id: ID of the image
        user_id: ID of the user
        db: Database session

    Returns:
        Tuple of (Image or None, error message or None)
    """
    from models.experiment import Experiment

    result = await db.execute(
        select(Image)
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(Image.id == image_id, Experiment.user_id == user_id)
    )
    image = result.scalar_one_or_none()

    if not image:
        return None, "Image not found or access denied"

    return image, None


async def get_crop_with_ownership_check(
    crop_id: int,
    user_id: int,
    db: AsyncSession,
) -> Tuple[Optional[CellCrop], Optional[Image], Optional[str]]:
    """
    Get a crop and its parent image, verifying user ownership.

    Args:
        crop_id: ID of the crop
        user_id: ID of the user
        db: Database session

    Returns:
        Tuple of (CellCrop or None, Image or None, error message or None)
    """
    from models.experiment import Experiment
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(CellCrop)
        .options(selectinload(CellCrop.image).selectinload(Image.experiment))
        .where(CellCrop.id == crop_id)
    )
    crop = result.scalar_one_or_none()

    if not crop:
        return None, None, "Crop not found"

    # Check ownership via experiment
    if crop.image.experiment.user_id != user_id:
        return None, None, "Access denied"

    return crop, crop.image, None
