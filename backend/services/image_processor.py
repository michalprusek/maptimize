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

    Supports two modes:
    - detect_cells=True: Run YOLO, create crops, delete source images
    - detect_cells=False: Keep full projections, no detection
    """

    def __init__(self, image_id: int, detect_cells: bool = True):
        self.image_id = image_id
        self.detect_cells = detect_cells

    async def process(self) -> bool:
        """
        Run the full processing pipeline.

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

                    # Delete original Z-stack file (keep only projections)
                    original_path = Path(image.file_path)
                    if original_path.exists():
                        original_path.unlink()
                        logger.info(f"Deleted original Z-stack: {original_path}")
                    image.source_discarded = True
                else:
                    logger.info("Processing 2D image...")
                    # For 2D images, use as-is (no projections needed)
                    mip = data
                    sum_proj = None

                # Save thumbnail (always from MIP)
                thumb_path = await self._save_thumbnail(image, mip)
                image.thumbnail_path = str(thumb_path)

                await db.commit()

                if self.detect_cells:
                    # Run detection pipeline
                    await self._run_detection(db, image, mip, sum_proj, is_zstack)
                else:
                    # No detection - treat whole image as a single crop
                    logger.info("Detection disabled - creating whole-image crop")
                    await self._create_whole_image_crop(db, image, mip, sum_proj)
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

    async def _create_whole_image_crop(
        self,
        db: AsyncSession,
        image: Image,
        mip: np.ndarray,
        sum_proj: Optional[np.ndarray]
    ):
        """
        Create a single CellCrop representing the whole image.

        Used when detect_cells=False - treats the entire image as one "crop"
        so it appears in the gallery and can be imported into metrics.
        """
        height, width = mip.shape[:2]

        mip_crop_path = await self._save_whole_image_crop(image, mip, "mip")

        sum_crop_path = None
        if sum_proj is not None:
            sum_crop_path = await self._save_whole_image_crop(image, sum_proj, "sum")

        cell_crop = self._create_cell_crop(
            image=image,
            mip_crop=mip,
            mip_path=mip_crop_path,
            sum_crop_path=sum_crop_path,
            bbox=(0, 0, width, height),
            confidence=1.0,
        )
        db.add(cell_crop)
        await db.flush()

        await self._extract_features_for_crops([cell_crop], db)

    async def _save_whole_image_crop(
        self,
        image: Image,
        projection: np.ndarray,
        suffix: str
    ) -> Path:
        """Save whole image projection as a crop file."""
        proj_8bit = normalize_image(projection)

        # Create crops directory
        upload_dir = Path(image.file_path).parent
        crops_dir = upload_dir / "crops"
        crops_dir.mkdir(exist_ok=True)

        # Save with "whole" prefix to distinguish from detected crops
        crop_path = crops_dir / f"whole_image_{suffix}.png"
        pil_img = PILImage.fromarray(proj_8bit)
        pil_img.save(crop_path)

        return crop_path

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
