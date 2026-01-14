"""
Image processing service - handles the full pipeline:
1. Load Z-stack TIFF or 2D image
2. Create MIP and SUM projections (for Z-stacks only)
3. Optionally run YOLO detection
4. Crop detected cells from projections
5. Compute basic metrics (intensity)
6. Clean up original Z-stack file (2D images are kept)
7. Save to database
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime, timezone

import numpy as np
from PIL import Image as PILImage

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db_context
from models.image import Image, UploadStatus
from models.cell_crop import CellCrop
from ml.detection import detect_cells_in_image, Detection, create_mip, normalize_image

logger = logging.getLogger(__name__)
settings = get_settings()


def create_sum_projection(zstack: np.ndarray) -> np.ndarray:
    """
    Create SUM projection from Z-stack.

    The sum is normalized to 0-255 range for storage as PNG.
    """
    sum_proj = np.sum(zstack.astype(np.float64), axis=0)
    # Normalize to 0-255 range
    if sum_proj.max() > 0:
        sum_proj = (sum_proj / sum_proj.max() * 255).astype(np.uint8)
    else:
        sum_proj = sum_proj.astype(np.uint8)
    return sum_proj


class ImageProcessor:
    """
    Processes microscopy images through the analysis pipeline.

    Supports two-phase processing:
    - Phase 1 (upload_only): Load image, create projections, create thumbnail
    - Phase 2 (process_batch): Run detection and feature extraction

    Legacy mode (detect_cells parameter) is still supported for backward compatibility.
    """

    def __init__(self, image_id: int, detect_cells: bool = True):
        self.image_id = image_id
        self.detect_cells = detect_cells

    async def process_upload_only(self) -> bool:
        """
        Phase 1: Process uploaded image - create projections and thumbnail only.

        This method:
        - Loads the image/Z-stack
        - Creates MIP and SUM projections (for Z-stacks)
        - Creates thumbnail
        - Saves paths to database
        - Sets status to UPLOADED

        Does NOT run detection or feature extraction.

        Returns:
            True if successful, False otherwise
        """
        async with get_db_context() as db:
            try:
                # Get image record
                result = await db.execute(
                    select(Image).where(Image.id == self.image_id)
                )
                image = result.scalar_one_or_none()

                if not image:
                    logger.error(f"Image {self.image_id} not found")
                    return False

                logger.info(f"Phase 1 processing image {image.id}: {image.original_filename}")

                # Update status
                image.status = UploadStatus.PROCESSING
                await db.commit()

                # Load image
                data = await self._load_image(image.file_path)
                if data is None:
                    raise ValueError(f"Failed to load image: {image.file_path}")

                # Check if Z-stack (3D) or 2D image
                is_zstack = len(data.shape) == 3

                # Store dimensions
                if is_zstack:
                    image.z_slices = data.shape[0]
                    image.height = data.shape[1]
                    image.width = data.shape[2]
                else:
                    image.height = data.shape[0]
                    image.width = data.shape[1]

                # Create projections based on image type
                if is_zstack:
                    logger.info(f"Processing Z-stack with {data.shape[0]} slices...")

                    # Create MIP projection
                    mip = create_mip(data)
                    mip_path = await self._save_projection(image, mip, "mip")
                    image.mip_path = str(mip_path)

                    # Create SUM projection
                    sum_proj = create_sum_projection(data)
                    sum_path = await self._save_projection(image, sum_proj, "sum")
                    image.sum_path = str(sum_path)

                    # Mark source as discarded (will delete after commit succeeds)
                    image.source_discarded = True
                    original_path = Path(image.file_path)
                else:
                    logger.info("Processing 2D image...")
                    # For 2D images, use as-is (no projections needed)
                    mip = data
                    original_path = None

                # Save thumbnail (always from MIP)
                thumb_path = await self._save_thumbnail(image, mip)
                image.thumbnail_path = str(thumb_path)

                # Set status to UPLOADED (Phase 1 complete)
                image.status = UploadStatus.UPLOADED
                await db.commit()

                # Delete original Z-stack file AFTER successful commit (prevents data loss on rollback)
                if original_path and original_path.exists():
                    original_path.unlink()
                    logger.info(f"Deleted original Z-stack: {original_path}")

                logger.info(f"Phase 1 complete for image {image.id}")
                return True

            except Exception as e:
                logger.exception(f"Error in Phase 1 processing image {self.image_id}: {e}")

                # Update status to error
                result = await db.execute(
                    select(Image).where(Image.id == self.image_id)
                )
                image = result.scalar_one_or_none()
                if image:
                    image.status = UploadStatus.ERROR
                    image.error_message = str(e)
                    await db.commit()

                return False

    async def process_batch(self, detect_cells: bool, map_protein_id: Optional[int] = None) -> bool:
        """
        Phase 2: Run detection and optional feature extraction on an already-uploaded image.

        This method:
        - Loads existing MIP projection from Phase 1
        - If detect_cells=True: runs YOLO detection, creates cell crops, extracts DINOv2 embeddings
        - If detect_cells=False: marks image as READY without creating crops (FOV only mode)
        - Always extracts FOV-level embedding regardless of detect_cells setting
        - Sets status to READY

        Args:
            detect_cells: Whether to run YOLO detection and create cell crops
            map_protein_id: Optional MAP protein to assign to the image

        Returns:
            True if successful, False otherwise
        """
        async with get_db_context() as db:
            try:
                # Get image record
                result = await db.execute(
                    select(Image).where(Image.id == self.image_id)
                )
                image = result.scalar_one_or_none()

                if not image:
                    logger.error(f"Image {self.image_id} not found")
                    return False

                # Verify image is in UPLOADED status
                if image.status not in [UploadStatus.UPLOADED, UploadStatus.READY, UploadStatus.ERROR]:
                    logger.warning(f"Image {image.id} has status {image.status}, expected UPLOADED")

                logger.info(f"Phase 2 processing image {image.id}: detect_cells={detect_cells}")

                # Update settings
                image.detect_cells = detect_cells
                if map_protein_id is not None:
                    image.map_protein_id = map_protein_id
                image.status = UploadStatus.PROCESSING
                await db.commit()

                # Load MIP projection (should already exist from Phase 1)
                mip_fallback_used = False
                if image.mip_path and Path(image.mip_path).exists():
                    mip = await self._load_image(image.mip_path)
                elif not image.mip_path and Path(image.file_path).exists():
                    # For 2D images, mip_path is not set - use original file directly
                    logger.info(f"2D image {image.id}: using original file as MIP")
                    mip = await self._load_image(image.file_path)
                else:
                    # Fallback: try to load from original file if MIP is missing
                    mip_fallback_used = True
                    logger.warning(
                        f"MIP projection missing for image {image.id} (mip_path={image.mip_path}), "
                        f"falling back to original file: {image.file_path}. "
                        f"This may indicate Phase 1 did not complete properly."
                    )
                    mip = await self._load_image(image.file_path)
                    if mip is not None:
                        # Record that fallback was used (visible in error_message field)
                        existing_msg = image.error_message or ""
                        warning_msg = "Warning: Phase 1 may be incomplete (MIP fallback used). "
                        if warning_msg not in existing_msg:
                            image.error_message = warning_msg + existing_msg

                if mip is None:
                    raise ValueError(f"Cannot load MIP for image {image.id}")

                # Load SUM projection if exists
                sum_proj = None
                if image.sum_path and Path(image.sum_path).exists():
                    sum_proj = await self._load_image(image.sum_path)

                if detect_cells:
                    # Run detection pipeline
                    await self._run_detection(db, image, mip, sum_proj, sum_proj is not None)
                else:
                    # No detection - just mark as ready (FOV only)
                    logger.info("Detection disabled - FOV will be shown without crops")
                    image.status = UploadStatus.READY
                    image.processed_at = datetime.now(timezone.utc)

                # Extract FOV embedding (always, regardless of detect_cells)
                await self._extract_fov_embedding(db, image)

                await db.commit()
                logger.info(f"Phase 2 complete for image {image.id}")
                return True

            except Exception as e:
                logger.exception(f"Error in Phase 2 processing image {self.image_id}: {e}")

                # Update status to error
                result = await db.execute(
                    select(Image).where(Image.id == self.image_id)
                )
                image = result.scalar_one_or_none()
                if image:
                    image.status = UploadStatus.ERROR
                    image.error_message = str(e)
                    await db.commit()

                return False

    async def process(self) -> bool:
        """
        Run the full processing pipeline (legacy mode).

        This combines Phase 1 and Phase 2 for backward compatibility.

        Returns:
            True if successful, False otherwise
        """
        async with get_db_context() as db:
            try:
                # Get image record
                result = await db.execute(
                    select(Image).where(Image.id == self.image_id)
                )
                image = result.scalar_one_or_none()

                if not image:
                    logger.error(f"Image {self.image_id} not found")
                    return False

                logger.info(f"Processing image {image.id}: {image.original_filename} (detect_cells={self.detect_cells})")

                # Update status
                image.status = UploadStatus.PROCESSING
                image.detect_cells = self.detect_cells
                await db.commit()

                # Load image
                data = await self._load_image(image.file_path)
                if data is None:
                    raise ValueError(f"Failed to load image: {image.file_path}")

                # Check if Z-stack (3D) or 2D image
                is_zstack = len(data.shape) == 3

                # Store dimensions
                if is_zstack:
                    image.z_slices = data.shape[0]
                    image.height = data.shape[1]
                    image.width = data.shape[2]
                else:
                    image.height = data.shape[0]
                    image.width = data.shape[1]

                # Create projections based on image type
                if is_zstack:
                    logger.info(f"Processing Z-stack with {data.shape[0]} slices...")

                    # Create MIP projection
                    mip = create_mip(data)
                    mip_path = await self._save_projection(image, mip, "mip")
                    image.mip_path = str(mip_path)

                    # Create SUM projection
                    sum_proj = create_sum_projection(data)
                    sum_path = await self._save_projection(image, sum_proj, "sum")
                    image.sum_path = str(sum_path)

                    # Mark source as discarded (will delete after commit succeeds)
                    image.source_discarded = True
                    original_path = Path(image.file_path)
                else:
                    logger.info("Processing 2D image...")
                    # For 2D images, use as-is (no projections needed)
                    mip = data
                    sum_proj = None
                    original_path = None

                # Save thumbnail (always from MIP)
                thumb_path = await self._save_thumbnail(image, mip)
                image.thumbnail_path = str(thumb_path)

                await db.commit()

                # Delete original Z-stack file AFTER successful commit (prevents data loss on rollback)
                if original_path and original_path.exists():
                    original_path.unlink()
                    logger.info(f"Deleted original Z-stack: {original_path}")

                if self.detect_cells:
                    # Run detection pipeline
                    await self._run_detection(db, image, mip, sum_proj, is_zstack)
                else:
                    # No detection - just mark as ready (FOV only, no whole-image crop)
                    logger.info("Detection disabled - FOV will be shown without crops")
                    image.status = UploadStatus.READY
                    image.processed_at = datetime.now(timezone.utc)

                await db.commit()
                logger.info(f"Successfully processed image {image.id}")
                return True

            except Exception as e:
                logger.exception(f"Error processing image {self.image_id}: {e}")

                # Update status to error
                result = await db.execute(
                    select(Image).where(Image.id == self.image_id)
                )
                image = result.scalar_one_or_none()
                if image:
                    image.status = UploadStatus.ERROR
                    image.error_message = str(e)
                    await db.commit()

                return False

    async def _run_detection(
        self,
        db: AsyncSession,
        image: Image,
        mip: np.ndarray,
        sum_proj: Optional[np.ndarray],
        is_zstack: bool
    ):
        """Run YOLO detection and create cell crops."""
        # Update status for detection
        image.status = UploadStatus.DETECTING
        await db.commit()

        # Run detection on normalized MIP
        logger.info("Running YOLO detection...")
        mip_normalized = normalize_image(mip)
        detections = await detect_cells_in_image(mip_normalized)

        logger.info(f"Found {len(detections)} cells")

        # Update status for feature extraction
        image.status = UploadStatus.EXTRACTING_FEATURES
        await db.commit()

        # Create cell crops
        new_crops = []
        for det in detections:
            mip_crop = self._crop_cell(mip, det)
            mip_crop_path = await self._save_crop(image, det, mip_crop, "mip")

            sum_crop_path = None
            if sum_proj is not None:
                sum_crop = self._crop_cell(sum_proj, det)
                sum_crop_path = await self._save_crop(image, det, sum_crop, "sum")

            cell_crop = self._create_cell_crop(
                image=image,
                mip_crop=mip_crop,
                mip_path=mip_crop_path,
                sum_crop_path=sum_crop_path,
                bbox=(det.bbox_x, det.bbox_y, det.bbox_w, det.bbox_h),
                confidence=det.confidence,
            )
            db.add(cell_crop)
            new_crops.append(cell_crop)

        await db.flush()
        await self._extract_features_for_crops(new_crops, db)

        # Mark as complete
        image.status = UploadStatus.READY
        image.processed_at = datetime.now(timezone.utc)

    async def _load_image(self, file_path: str) -> Optional[np.ndarray]:
        """Load image from file (supports TIFF Z-stacks)."""
        try:
            import tifffile

            path = Path(file_path)

            if path.suffix.lower() in ['.tif', '.tiff']:
                # Load TIFF (potentially Z-stack)
                data = tifffile.imread(str(path))
                logger.info(f"Loaded TIFF with shape: {data.shape}, dtype: {data.dtype}")
                return data
            else:
                # Load regular image
                img = PILImage.open(path)
                return np.array(img)

        except Exception as e:
            logger.error(f"Failed to load image {file_path}: {e}")
            return None

    async def _save_projection(self, image: Image, proj: np.ndarray, suffix: str) -> Path:
        """Save projection (MIP or SUM) as PNG."""
        # Normalize to 8-bit if needed
        if proj.dtype != np.uint8:
            proj_8bit = normalize_image(proj)
        else:
            proj_8bit = proj

        # Create output path
        upload_dir = Path(image.file_path).parent
        stem = Path(image.file_path).stem
        proj_path = upload_dir / f"{stem}_{suffix}.png"

        # Save
        pil_img = PILImage.fromarray(proj_8bit)
        pil_img.save(proj_path)

        return proj_path

    async def _save_thumbnail(
        self,
        image: Image,
        mip: np.ndarray,
        size: Tuple[int, int] = (256, 256)
    ) -> Path:
        """Save thumbnail."""
        mip_8bit = normalize_image(mip)

        # Create output path
        upload_dir = Path(image.file_path).parent
        thumb_path = upload_dir / f"{Path(image.file_path).stem}_thumb.png"

        # Resize and save
        pil_img = PILImage.fromarray(mip_8bit)
        pil_img.thumbnail(size, PILImage.Resampling.LANCZOS)
        pil_img.save(thumb_path)

        return thumb_path

    def _crop_cell(self, projection: np.ndarray, det: Detection) -> np.ndarray:
        """Crop a cell from a projection based on detection bbox (no padding)."""
        x, y, w, h = det.bbox_x, det.bbox_y, det.bbox_w, det.bbox_h
        return projection[y:y+h, x:x+w]

    def _create_cell_crop(
        self,
        image: Image,
        mip_crop: np.ndarray,
        mip_path: Path,
        sum_crop_path: Optional[Path],
        bbox: Tuple[int, int, int, int],
        confidence: float,
    ) -> CellCrop:
        """Create a CellCrop database record."""
        return CellCrop(
            image_id=image.id,
            map_protein_id=image.map_protein_id,
            bbox_x=bbox[0],
            bbox_y=bbox[1],
            bbox_w=bbox[2],
            bbox_h=bbox[3],
            detection_confidence=confidence,
            mip_path=str(mip_path),
            sum_crop_path=str(sum_crop_path) if sum_crop_path else None,
            bundleness_score=None,
            mean_intensity=float(np.mean(mip_crop)),
            skewness=None,
            kurtosis=None,
        )

    async def _extract_features_for_crops(
        self,
        crops: List[CellCrop],
        db: AsyncSession
    ) -> None:
        """Extract DINOv2 embeddings for cell crops (non-fatal on failure)."""
        if not crops:
            return

        crop_ids = [crop.id for crop in crops]
        try:
            from ml.features import extract_features_for_crops
            result = await extract_features_for_crops(crop_ids, db)

            if result['failed'] > 0:
                logger.warning(
                    f"Feature extraction for image {crops[0].image_id}: "
                    f"{result['success']} success, {result['failed']} failed"
                )
            else:
                logger.info(
                    f"Feature extraction complete: {result['success']} embeddings created"
                )
        except ImportError as e:
            logger.error(f"Feature extraction module not available: {e}")
        except RuntimeError as e:
            logger.error(f"DINOv2 model error during feature extraction: {e}")
        except Exception as e:
            logger.exception(f"Unexpected feature extraction error: {e}")

    async def _extract_fov_embedding(
        self,
        db: AsyncSession,
        image: Image
    ) -> None:
        """Extract DINOv3 embedding for FOV MIP projection (non-fatal on failure)."""
        try:
            from ml.features import extract_features_for_images
            result = await extract_features_for_images([image.id], db)

            if result['success'] > 0:
                logger.info(f"FOV embedding created for image {image.id}")
            elif result['failed'] > 0:
                logger.warning(f"FOV embedding extraction failed for image {image.id}")
        except ImportError as e:
            logger.error(f"Feature extraction module not available: {e}")
        except RuntimeError as e:
            logger.error(f"DINOv3 model error during FOV feature extraction: {e}")
        except Exception as e:
            logger.exception(f"Unexpected FOV feature extraction error: {e}")

    async def _save_crop(
        self,
        image: Image,
        det: Detection,
        crop: np.ndarray,
        suffix: str
    ) -> Path:
        """Save cell crop as PNG."""
        crop_8bit = normalize_image(crop)

        # Create crops directory
        upload_dir = Path(image.file_path).parent
        crops_dir = upload_dir / "crops"
        crops_dir.mkdir(exist_ok=True)

        # Save with suffix to distinguish MIP vs SUM crops
        crop_path = crops_dir / f"cell_{det.bbox_x}_{det.bbox_y}_{suffix}.png"
        pil_img = PILImage.fromarray(crop_8bit)
        pil_img.save(crop_path)

        return crop_path


async def process_image(image_id: int, detect_cells: bool = True) -> bool:
    """
    Process an image through the full pipeline.

    Args:
        image_id: ID of the image to process
        detect_cells: Whether to run YOLO detection

    Returns:
        True if successful
    """
    processor = ImageProcessor(image_id, detect_cells=detect_cells)
    return await processor.process()


async def process_image_background(image_id: int, detect_cells: bool = True):
    """
    Process an image in the background.
    This is meant to be called from a background task.
    """
    try:
        await process_image(image_id, detect_cells=detect_cells)
    except asyncio.CancelledError:
        logger.info(f"Processing cancelled for image {image_id}")
        raise  # Always re-raise cancellation
    except (MemoryError, SystemExit, KeyboardInterrupt):
        logger.critical(f"System-level error during image {image_id} processing")
        raise  # Don't catch system-level errors
    except Exception as e:
        logger.exception(f"Background processing failed for image {image_id}: {e}")
        await _update_error_status(image_id, str(e))


async def _update_error_status(image_id: int, error_message: str):
    """Update image status to ERROR. Separate function for clarity."""
    try:
        async with get_db_context() as db:
            result = await db.execute(
                select(Image).where(Image.id == image_id)
            )
            image = result.scalar_one_or_none()
            if image and image.status != UploadStatus.ERROR:
                image.status = UploadStatus.ERROR
                image.error_message = f"Unexpected error: {error_message}"
                await db.commit()
    except Exception as db_err:
        logger.error(f"Failed to update error status for image {image_id}: {db_err}")


# ============================================================================
# Phase 1 & Phase 2 Processing Functions (Two-Phase Workflow)
# ============================================================================

async def process_upload_only(image_id: int) -> bool:
    """
    Phase 1: Process uploaded image - create projections and thumbnail.

    Args:
        image_id: ID of the image to process

    Returns:
        True if successful
    """
    processor = ImageProcessor(image_id)
    return await processor.process_upload_only()


async def process_upload_only_background(image_id: int):
    """
    Phase 1 background task: Process uploaded image.
    Creates projections and thumbnail, sets status to UPLOADED.
    """
    try:
        await process_upload_only(image_id)
    except asyncio.CancelledError:
        logger.info(f"Phase 1 processing cancelled for image {image_id}")
        raise
    except (MemoryError, SystemExit, KeyboardInterrupt):
        logger.critical(f"System-level error during Phase 1 for image {image_id}")
        raise
    except Exception as e:
        logger.exception(f"Phase 1 background processing failed for image {image_id}: {e}")
        await _update_error_status(image_id, str(e))


async def process_batch(image_id: int, detect_cells: bool, map_protein_id: Optional[int] = None) -> bool:
    """
    Phase 2: Run detection and feature extraction on an already-uploaded image.

    Args:
        image_id: ID of the image to process
        detect_cells: Whether to run YOLO detection
        map_protein_id: Optional MAP protein to assign

    Returns:
        True if successful
    """
    processor = ImageProcessor(image_id)
    return await processor.process_batch(detect_cells, map_protein_id)


async def process_batch_background(image_id: int, detect_cells: bool, map_protein_id: Optional[int] = None):
    """
    Phase 2 background task: Run detection and feature extraction.
    """
    try:
        await process_batch(image_id, detect_cells, map_protein_id)
    except asyncio.CancelledError:
        logger.info(f"Phase 2 processing cancelled for image {image_id}")
        raise
    except (MemoryError, SystemExit, KeyboardInterrupt):
        logger.critical(f"System-level error during Phase 2 for image {image_id}")
        raise
    except Exception as e:
        logger.exception(f"Phase 2 background processing failed for image {image_id}: {e}")
        await _update_error_status(image_id, str(e))
