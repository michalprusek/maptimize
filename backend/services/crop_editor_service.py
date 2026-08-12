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

from sqlalchemy.ext.asyncio import AsyncSession

from models.cell_crop import CellCrop
from models.image import Image
from ml.detection import normalize_image

logger = logging.getLogger(__name__)


# =============================================================================
# Validation Functions
# =============================================================================


def _rotated_corners(
    bbox_x: int, bbox_y: int, bbox_w: int, bbox_h: int, bbox_angle: float
) -> list[Tuple[float, float]]:
    """Corners of the axis-aligned box rotated by ``bbox_angle`` degrees about its
    centre, as (x, y) pixel coordinates. Shared by validation and (conceptually) the
    de-rotated extraction so the two agree on what the rotated box covers."""
    cx = bbox_x + bbox_w / 2.0
    cy = bbox_y + bbox_h / 2.0
    theta = np.deg2rad(bbox_angle)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    corners = []
    for u in (-bbox_w / 2.0, bbox_w / 2.0):
        for v in (-bbox_h / 2.0, bbox_h / 2.0):
            corners.append((cx + u * cos_t - v * sin_t, cy + u * sin_t + v * cos_t))
    return corners


def validate_bbox_within_image(
    bbox_x: int,
    bbox_y: int,
    bbox_w: int,
    bbox_h: int,
    image_width: int,
    image_height: int,
    bbox_angle: float = 0.0,
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
        bbox_angle: Rotation in degrees about the box centre (0 = axis-aligned)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if bbox_angle:
        # A rotated box's CORNERS (not its top-left) must stay in bounds — the
        # centre being inside is not enough.
        if bbox_w < 10 or bbox_h < 10:
            return False, "Bbox dimensions must be at least 10 pixels"
        # Name the offending corner and where it landed. The axis-aligned branch
        # below reports numbers; this one used to say only "exceeds image bounds",
        # which told the user neither which edge nor by how much -- and the client
        # discarded even that, showing a bare "Failed to update cell".
        names = ("top-left", "bottom-left", "top-right", "bottom-right")
        for name, (px, py) in zip(
            names, _rotated_corners(bbox_x, bbox_y, bbox_w, bbox_h, bbox_angle)
        ):
            if px < 0 or py < 0 or px > image_width or py > image_height:
                return False, (
                    f"Rotated box leaves the image: its {name} corner is at "
                    f"({px:.1f}, {py:.1f}) but the image is "
                    f"{image_width}x{image_height}. Move the box toward the centre "
                    f"or make it smaller."
                )
        return True, None

    # Axis-aligned: keep the original checks (order + messages) unchanged.
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
    bbox_angle: float = 0.0,
) -> np.ndarray:
    """
    Extract crop pixels from a projection array.

    With ``bbox_angle == 0`` this is a plain axis-aligned slice (fast path,
    unchanged). With a non-zero angle the crop is extracted **de-rotated**: the box,
    rotated ``bbox_angle`` degrees about its centre, is resampled so the cell appears
    upright in the returned ``bbox_h × bbox_w`` array. ``affine_transform`` samples
    only the crop-sized output from the source (it does not rotate the whole FOV) and
    handles an arbitrary rotation centre — unlike ``ndimage.rotate``.

    Args:
        projection: 2D (H, W) or 3D (H, W, C) numpy array of the projection image
        bbox_x/bbox_y: top-left of the axis-aligned box (before rotation)
        bbox_w/bbox_h: box size
        bbox_angle: rotation in degrees about the box centre

    ⚠️ The frontend mirrors this GEOMETRY in
    ``frontend/lib/editor/canvasUtils.ts::extractCropFromImage`` for the live
    preview. Change the rotation map here and that must change too.

    ⚠️ **Interpolation order is a scientific choice, not a default.** An axis-aligned
    crop is an exact slice, a rotated one is resampled, so whatever the resampler
    does to texture becomes a systematic difference between rotated and unrotated
    crops -- and that difference is readable by the DINOv3 embedding and the UMAP,
    exactly the way the microscope turned out to be. Measured
    on six real production crops (HMMR FOVs, 531-689 px boxes), change in texture
    statistics versus the exact slice at 30 deg / 45 deg:

    ===== =================== ============== ==========================
    order Laplacian variance  mean gradient  out-of-range
    ===== =================== ============== ==========================
    0     **+162% / +216%**   -1.7%          none
    1     -40% / -38%         -12.0%         none
    3     **-17.7% / -17.5%** **-3.6%**      2.1% of range, 1.4% of px
    ===== =================== ============== ==========================

    ``order=0`` is disqualified: nearest-neighbour *manufactures* high-frequency
    structure, which is the worst possible artifact for a texture measure. ``order=3``
    halves the texture loss of bilinear and cuts the gradient artifact 3.4x; its only
    cost is slight spline overshoot, which is clipped to the source range below so the
    function cannot return intensities the microscope never recorded.

    This reduces the rotated/unrotated asymmetry; it does not remove it. Only
    resampling BOTH paths identically would, and that would rewrite every stored crop.

    Also measured: at 90 deg the sampling is an exact pixel permutation for every
    order (verified against ``np.rot90``), offset by one row because the centre
    convention here is ``w/2`` rather than ``(w-1)/2``. That convention is shared with
    ``_rotated_corners`` and the canvas mirror, so it is deliberate -- changing it
    would move every rotated crop by a pixel and desynchronise the three.

    Returns:
        Cropped numpy array of shape (bbox_h, bbox_w[, C])
    """
    if not bbox_angle:
        return projection[bbox_y:bbox_y + bbox_h, bbox_x:bbox_x + bbox_w]

    from scipy import ndimage

    cx = bbox_x + bbox_w / 2.0
    cy = bbox_y + bbox_h / 2.0
    theta = np.deg2rad(bbox_angle)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    # For output pixel (i, j): input = centre + rotate(j - w/2, i - h/2). Expressed as
    # affine_transform's out->in map (row, col): matrix @ (i, j) + offset.
    matrix = np.array([[cos_t, sin_t], [-sin_t, cos_t]])
    row_off = cy - (bbox_h / 2.0) * cos_t - (bbox_w / 2.0) * sin_t
    col_off = cx + (bbox_h / 2.0) * sin_t - (bbox_w / 2.0) * cos_t
    if projection.ndim == 3:  # (H, W, C): identity on the channel axis, don't mix colours
        full = np.eye(3)
        full[:2, :2] = matrix
        matrix = full
        offset = (row_off, col_off, 0.0)
        output_shape = (bbox_h, bbox_w, projection.shape[2])
    else:
        offset = (row_off, col_off)
        output_shape = (bbox_h, bbox_w)
    out = ndimage.affine_transform(
        projection, matrix, offset=offset, output_shape=output_shape,
        order=3, mode="constant", cval=0.0,
    )
    # Cubic splines overshoot near sharp edges. Clip to the source range so a
    # de-rotated crop can never contain an intensity the source did not, rather than
    # leaving it to whatever uint8 conversion happens downstream.
    return np.clip(out, projection.min(), projection.max())


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
# MIP Source Helper
# =============================================================================


def get_mip_source_path(image: Image) -> Optional[str]:
    """
    Determine the MIP source path for an image.

    For Z-stacks: Uses the generated MIP projection.
    For 2D images: Falls back to the original file.

    DRY: Common logic used by regenerate_crop_features and create_manual_crop.

    Args:
        image: Image model instance

    Returns:
        Path string or None if no source available
    """
    if image.mip_path and Path(image.mip_path).exists():
        return image.mip_path
    if Path(image.file_path).exists():
        return image.file_path
    return None


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
    from services.umap_service import invalidate_crop_umap

    # Determine MIP source path
    mip_source = get_mip_source_path(image)
    if not mip_source:
        return {"success": False, "error": "No MIP or source file available"}

    # Load MIP projection
    try:
        mip = np.array(PILImage.open(mip_source))
    except Exception as e:
        return {"success": False, "error": f"Failed to load MIP: {e}"}

    # Validate bbox within image bounds (angle-aware: rotated corners must fit)
    is_valid, error = validate_bbox_within_image(
        crop.bbox_x,
        crop.bbox_y,
        crop.bbox_w,
        crop.bbox_h,
        image.width,
        image.height,
        crop.bbox_angle or 0.0,
    )
    if not is_valid:
        return {"success": False, "error": error}

    # Determine crops directory
    upload_dir = Path(image.file_path).parent
    crops_dir = upload_dir / "crops"

    old_paths = (crop.mip_path, crop.sum_crop_path)
    sum_error: Optional[str] = None

    # Extract and save the new MIP crop (de-rotated when bbox_angle is set).
    #
    # ⚠️ This MUST come BEFORE the old files are removed, and it MUST return the
    # service's {"success": False} contract rather than raise. Deleting first left
    # the row pointing at a file that no longer existed whenever this failed
    # (ENOSPC, a mkdir/save PermissionError on ./data, a corrupt source): the
    # session rolls back so the DB keeps the old path, but the unlink does not roll
    # back. The thumbnail then 404s forever, and the only repair is the very call
    # that just failed. A raise also escaped `if not result["success"]` in the
    # router and surfaced as an opaque 500.
    try:
        mip_crop = extract_crop_from_projection(
            mip, crop.bbox_x, crop.bbox_y, crop.bbox_w, crop.bbox_h,
            crop.bbox_angle or 0.0,
        )
        new_mip_path = str(
            save_crop_image(mip_crop, crops_dir, crop.bbox_x, crop.bbox_y, "mip")
        )
    except Exception as e:
        logger.error(
            "Crop %s re-cut failed; keeping the existing crop files. "
            "bbox=(%s,%s,%s,%s) angle=%s src=%s",
            crop.id, crop.bbox_x, crop.bbox_y, crop.bbox_w, crop.bbox_h,
            crop.bbox_angle, mip_source, exc_info=True,
        )
        return {"success": False, "error": f"Could not re-cut the crop: {e}"}

    crop.mip_path = new_mip_path

    # Extract and save SUM crop if available
    if image.sum_path and Path(image.sum_path).exists():
        try:
            sum_proj = np.array(PILImage.open(image.sum_path))
            sum_crop = extract_crop_from_projection(
                sum_proj, crop.bbox_x, crop.bbox_y, crop.bbox_w, crop.bbox_h,
                crop.bbox_angle or 0.0,
            )
            crop.sum_crop_path = str(
                save_crop_image(sum_crop, crops_dir, crop.bbox_x, crop.bbox_y, "sum")
            )
        except Exception as e:
            # The old SUM file is about to be removed below, so this is a net loss:
            # the crop had a SUM projection before the edit and has none after.
            # Log enough to identify which crop, and report it to the caller --
            # returning 200 with sum_crop_path: null and no warning reads as
            # "the edit fully succeeded".
            logger.error(
                "Crop %s: SUM re-cut failed, crop now has no SUM projection. "
                "src=%s bbox=(%s,%s,%s,%s) angle=%s",
                crop.id, image.sum_path, crop.bbox_x, crop.bbox_y,
                crop.bbox_w, crop.bbox_h, crop.bbox_angle, exc_info=True,
            )
            crop.sum_crop_path = None
            sum_error = str(e)

    # The new files are on disk and the row points at them, so the pre-edit files
    # can go. Skip any path the new crop still uses: a rotation-only edit does not
    # move the box origin, so it rewrites the very same filename.
    current = {crop.mip_path, crop.sum_crop_path}
    for path_str in old_paths:
        if path_str and path_str not in current:
            path = Path(path_str)
            if path.exists():
                try:
                    path.unlink()
                    logger.debug(f"Deleted superseded crop file: {path}")
                except OSError as e:
                    logger.warning(f"Failed to delete crop file {path}: {e}")

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

    # Invalidate UMAP for all crops in this experiment (synchronous - fast)
    umap_invalidated = False
    try:
        await invalidate_crop_umap(db, image_id=image.id)
        umap_invalidated = True
    except Exception as e:
        logger.warning(f"Failed to invalidate UMAP: {e}")

    # Note: Embedding extraction is done asynchronously by the caller
    # to avoid blocking the response

    return {
        "success": True,
        "needs_embedding": True,  # Signal caller to extract embedding async
        "umap_invalidated": umap_invalidated,
        "mip_path": crop.mip_path,
        "sum_crop_path": crop.sum_crop_path,
        "mean_intensity": crop.mean_intensity,
        "sum_error": sum_error,
    }


async def create_manual_crop(
    image: Image,
    bbox_x: int,
    bbox_y: int,
    bbox_w: int,
    bbox_h: int,
    db: AsyncSession,
    map_protein_id: Optional[int] = None,
    bbox_angle: float = 0.0,
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
        bbox_angle: Rotation in degrees about the box centre (0 = axis-aligned).
            The crop is extracted de-rotated, and validation checks the ROTATED
            corners, so a box that fits axis-aligned may still be rejected.

    Returns:
        Tuple of (CellCrop or None, error message or None)
    """
    # Validate bbox (angle-aware: rotated corners must fit)
    is_valid, error = validate_bbox_within_image(
        bbox_x, bbox_y, bbox_w, bbox_h, image.width, image.height, bbox_angle
    )
    if not is_valid:
        return None, error

    # Determine MIP source
    mip_source = get_mip_source_path(image)
    if not mip_source:
        return None, "No MIP or source file available"

    # Load MIP projection
    try:
        mip = np.array(PILImage.open(mip_source))
    except Exception as e:
        return None, f"Failed to load MIP: {e}"

    # Extract crop (de-rotated when bbox_angle is set)
    mip_crop = extract_crop_from_projection(mip, bbox_x, bbox_y, bbox_w, bbox_h, bbox_angle)

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
                sum_proj, bbox_x, bbox_y, bbox_w, bbox_h, bbox_angle
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
        bbox_angle=bbox_angle or None,
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


# =============================================================================
# Embedding Status Tracking (DRY helper for background tasks)
# =============================================================================


def truncate_error_message(error: str, max_length: int = 500) -> str:
    """Truncate error message with indicator if truncated."""
    if len(error) > max_length:
        return error[:max_length - 3] + "..."
    return error


async def update_crop_embedding_status(
    db: AsyncSession,
    crop_id: int,
    status: str,
    error_msg: Optional[str] = None,
) -> None:
    """
    Update embedding status for a crop.

    DRY helper to avoid repeating status update logic in background tasks.

    Args:
        db: Database session
        crop_id: ID of the crop to update
        status: One of "pending", "computing", "ready", "error"
        error_msg: Error message (only for "error" status)
    """
    from sqlalchemy import update
    from models.cell_crop import CellCrop

    truncated_error = truncate_error_message(error_msg) if error_msg else None

    await db.execute(
        update(CellCrop)
        .where(CellCrop.id == crop_id)
        .values(
            embedding_status=status,
            embedding_error=truncated_error
        )
    )
    await db.commit()
    logger.debug(f"Updated crop {crop_id} embedding status to '{status}'")


async def run_embedding_extraction_task(crop_id: int) -> None:
    """
    Background task for extracting embeddings for a single crop.

    DRY: Common logic used by regenerate_crop_features and create_manual_crop endpoints.
    Handles status tracking, error handling, and logging.

    Args:
        crop_id: ID of the crop to extract embeddings for
    """
    from database import get_db_context
    from ml.features import extract_features_for_crops

    async with get_db_context() as task_db:
        try:
            await update_crop_embedding_status(task_db, crop_id, "computing")
            await extract_features_for_crops([crop_id], task_db)
            await update_crop_embedding_status(task_db, crop_id, "ready")
            logger.info(f"Successfully extracted embedding for crop {crop_id}")
        except (KeyboardInterrupt, SystemExit):
            raise  # Always propagate system-level errors
        except Exception as e:
            logger.error(f"Background embedding extraction failed for crop {crop_id}: {e}")
            try:
                await update_crop_embedding_status(task_db, crop_id, "error", str(e))
            except Exception as db_err:
                logger.error(f"Failed to update error status for crop {crop_id}: {db_err}")
