"""
Image processing service - handles the full pipeline:
1. Load Z-stack TIFF or 2D image
2. Create MIP and SUM projections (for Z-stacks)
3. Optionally run YOLO detection
4. Crop detected cells from both projections
5. Compute metrics (bundleness, intensity)
6. Clean up source files
7. Save to database
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime, timezone

import numpy as np
from PIL import Image as PILImage
from scipy import stats

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
                    # No detection - just mark as ready
                    logger.info("Detection disabled - keeping full projections")
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
        for det in detections:
            # Crop the cell from MIP
            mip_crop = self._crop_cell(mip, det)
            mip_crop_path = await self._save_crop(image, det, mip_crop, "mip")

            # Crop from SUM projection if available
            sum_crop_path = None
            if sum_proj is not None:
                sum_crop = self._crop_cell(sum_proj, det)
                sum_crop_path = await self._save_crop(image, det, sum_crop, "sum")

            # Compute metrics from MIP crop
            bundleness, skewness, kurtosis = self._compute_bundleness(mip_crop)
            mean_intensity = float(np.mean(mip_crop))

            # Create database record
            cell_crop = CellCrop(
                image_id=image.id,
                bbox_x=det.bbox_x,
                bbox_y=det.bbox_y,
                bbox_w=det.bbox_w,
                bbox_h=det.bbox_h,
                detection_confidence=det.confidence,
                mip_path=str(mip_crop_path),
                sum_crop_path=str(sum_crop_path) if sum_crop_path else None,
                bundleness_score=bundleness,
                mean_intensity=mean_intensity,
                skewness=skewness,
                kurtosis=kurtosis,
            )
            db.add(cell_crop)

        # Delete source projections after cropping (keep only crops)
        if is_zstack:
            # Delete MIP and SUM projection files
            if image.mip_path:
                mip_file = Path(image.mip_path)
                if mip_file.exists():
                    mip_file.unlink()
                    logger.info(f"Deleted MIP projection: {mip_file}")

            if image.sum_path:
                sum_file = Path(image.sum_path)
                if sum_file.exists():
                    sum_file.unlink()
                    logger.info(f"Deleted SUM projection: {sum_file}")
        else:
            # For 2D images with detection, delete original
            original_path = Path(image.file_path)
            if original_path.exists():
                original_path.unlink()
                logger.info(f"Deleted original 2D image: {original_path}")

        image.source_discarded = True

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
        """Crop a cell from a projection based on detection."""
        x, y, w, h = det.bbox_x, det.bbox_y, det.bbox_w, det.bbox_h

        # Add padding
        pad = int(max(w, h) * 0.1)
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(projection.shape[1], x + w + pad)
        y2 = min(projection.shape[0], y + h + pad)

        return projection[y1:y2, x1:x2]

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

    def _compute_bundleness(self, crop: np.ndarray) -> Tuple[float, float, float]:
        """
        Compute bundleness score from intensity distribution.

        Bundleness = 0.7071 * z_skewness + 0.7071 * z_kurtosis

        Returns:
            (bundleness_score, skewness, kurtosis)
        """
        # Flatten and remove zeros/background
        flat = crop.flatten().astype(np.float64)
        threshold = np.percentile(flat, 10)
        signal = flat[flat > threshold]

        if len(signal) < 10:
            return 0.0, 0.0, 0.0

        # Compute statistics
        skewness = float(stats.skew(signal))
        kurtosis = float(stats.kurtosis(signal))

        # Z-score normalization (parameters from n=408 dataset)
        MEAN_SKEW = 1.1327
        STD_SKEW = 0.4717
        MEAN_KURT = 1.0071
        STD_KURT = 1.4920

        z_skew = (skewness - MEAN_SKEW) / STD_SKEW if STD_SKEW > 0 else 0
        z_kurt = (kurtosis - MEAN_KURT) / STD_KURT if STD_KURT > 0 else 0

        # PCA combined score
        bundleness = 0.7071 * z_skew + 0.7071 * z_kurt

        return bundleness, skewness, kurtosis


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
    except Exception as e:
        logger.exception(f"Background processing failed for image {image_id}: {e}")
