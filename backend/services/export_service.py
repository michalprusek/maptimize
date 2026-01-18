"""
Export service for streaming ZIP generation.

Handles:
- Preparing export jobs (counting files, estimating size)
- Streaming ZIP generation in batches
- Progress tracking via Redis
"""
import io
import json
import logging
import os
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import numpy as np
from PIL import Image as PILImage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models import CellCrop, Experiment, Image, MapProtein
from models.segmentation import FOVSegmentationMask, SegmentationMask
from schemas.export_import import (
    BBoxFormat,
    ExportJobData,
    ExportOptions,
    ExportPrepareResponse,
    ExportStatusResponse,
    MaskFormat,
)
from services.annotation_converters import to_coco, to_csv, to_voc, to_yolo, to_yolo_classes
from services.job_manager import BaseJobManager

logger = logging.getLogger(__name__)

# Batch size for processing images
BATCH_SIZE = 50


def write_file_to_zip(
    zf: zipfile.ZipFile,
    source_path: str | None,
    zip_path: str
) -> None:
    """Write a file to ZIP if it exists."""
    if source_path and os.path.exists(source_path):
        with open(source_path, 'rb') as f:
            zf.writestr(zip_path, f.read())


def write_embeddings_to_zip(
    zf: zipfile.ZipFile,
    items: list,
    embeddings_path: str,
    ids_path: str
) -> None:
    """Write embeddings and IDs to ZIP."""
    embeddings = []
    ids = []
    for item in items:
        if item.embedding is not None:
            embeddings.append(item.embedding)
            ids.append(item.id)

    if embeddings:
        arr = np.array(embeddings, dtype=np.float32)
        buffer = io.BytesIO()
        np.save(buffer, arr)
        buffer.seek(0)
        zf.writestr(embeddings_path, buffer.read())
        zf.writestr(ids_path, json.dumps(ids))


class ExportService(BaseJobManager[ExportJobData]):
    """Service for exporting experiment data."""

    _redis_key_prefix = "export_job:"
    _job_model = ExportJobData

    async def prepare_export(
        self,
        experiment_ids: List[int],
        options: ExportOptions,
        user_id: int,
        db: AsyncSession
    ) -> ExportPrepareResponse:
        """
        Prepare an export job by counting files and estimating size.

        Args:
            experiment_ids: List of experiment IDs to export
            options: Export options
            user_id: Current user ID
            db: Database session

        Returns:
            ExportPrepareResponse with job_id and estimates
        """
        job_id = str(uuid.uuid4())

        # Verify user owns all experiments
        result = await db.execute(
            select(Experiment)
            .where(
                Experiment.id.in_(experiment_ids),
                Experiment.user_id == user_id
            )
        )
        experiments = result.scalars().all()

        if len(experiments) != len(experiment_ids):
            raise ValueError("Some experiments not found or not owned by user")

        # Count images and crops
        image_result = await db.execute(
            select(func.count(Image.id))
            .where(Image.experiment_id.in_(experiment_ids))
        )
        image_count = image_result.scalar() or 0

        crop_result = await db.execute(
            select(func.count(CellCrop.id))
            .join(Image, CellCrop.image_id == Image.id)
            .where(Image.experiment_id.in_(experiment_ids))
        )
        crop_count = crop_result.scalar() or 0

        # Count masks
        mask_count = 0
        if options.include_masks:
            fov_mask_result = await db.execute(
                select(func.count(FOVSegmentationMask.id))
                .join(Image, FOVSegmentationMask.image_id == Image.id)
                .where(Image.experiment_id.in_(experiment_ids))
            )
            fov_mask_count = fov_mask_result.scalar() or 0

            crop_mask_result = await db.execute(
                select(func.count(SegmentationMask.id))
                .join(CellCrop, SegmentationMask.cell_crop_id == CellCrop.id)
                .join(Image, CellCrop.image_id == Image.id)
                .where(Image.experiment_id.in_(experiment_ids))
            )
            crop_mask_count = crop_mask_result.scalar() or 0
            mask_count = fov_mask_count + crop_mask_count

        # Estimate size
        estimated_size = self._estimate_export_size(
            image_count, crop_count, mask_count, options
        )

        # Create job
        job = ExportJobData(
            job_id=job_id,
            user_id=user_id,
            experiment_ids=experiment_ids,
            options=options,
            status="preparing",
            created_at=datetime.now(timezone.utc),
            experiment_count=len(experiments),
            image_count=image_count,
            crop_count=crop_count,
            mask_count=mask_count,
            estimated_size_bytes=estimated_size,
        )
        await self._save_job(job)

        return ExportPrepareResponse(
            job_id=job_id,
            estimated_size_bytes=estimated_size,
            experiment_count=len(experiments),
            image_count=image_count,
            crop_count=crop_count,
            mask_count=mask_count,
        )

    def _estimate_export_size(
        self,
        image_count: int,
        crop_count: int,
        mask_count: int,
        options: ExportOptions
    ) -> int:
        """Estimate total export size in bytes."""
        size = 0

        # FOV images: ~2MB each (MIP + SUM)
        if options.include_fov_images:
            size += image_count * 2 * 1024 * 1024

        # Crop images: ~100KB each
        if options.include_crop_images:
            size += crop_count * 100 * 1024

        # Embeddings: 1024 floats * 4 bytes = ~4KB per crop
        if options.include_embeddings:
            size += crop_count * 4 * 1024
            size += image_count * 4 * 1024  # FOV embeddings

        # Masks: ~50KB each
        if options.include_masks:
            size += mask_count * 50 * 1024

        # Annotations (JSON/TXT/XML): relatively small
        size += crop_count * 200  # ~200 bytes per annotation

        # Metadata files: minimal
        size += image_count * 500

        return size

    async def get_export_status(self, job_id: str) -> Optional[ExportStatusResponse]:
        """Get current status of an export job."""
        job = await self._get_job(job_id)
        if not job:
            return None

        return ExportStatusResponse(
            job_id=job.job_id,
            status=job.status,
            progress_percent=job.progress_percent,
            current_step=job.current_step,
            error_message=job.error_message,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )

    async def generate_export_stream(
        self,
        job_id: str,
        db: AsyncSession
    ) -> AsyncGenerator[bytes, None]:
        """
        Generate streaming ZIP content for export.

        Yields chunks of ZIP data for streaming response.

        Args:
            job_id: Export job ID
            db: Database session

        Yields:
            Bytes chunks of ZIP content
        """
        job = await self._get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        # Update status
        await self._update_job_progress(job_id, 0, "Starting export", "streaming")

        # Create in-memory buffer for ZIP
        buffer = io.BytesIO()

        try:
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                # Write manifest
                manifest = await self._create_manifest(job, db)
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))

                # Collect all data for annotations
                all_images: List[Image] = []
                all_crops: List[CellCrop] = []

                # Process experiments
                total_items = job.image_count + job.crop_count
                processed_items = 0

                for exp_idx, exp_id in enumerate(job.experiment_ids):
                    # Load experiment with images
                    result = await db.execute(
                        select(Experiment)
                        .options(
                            selectinload(Experiment.images)
                            .selectinload(Image.cell_crops)
                            .selectinload(CellCrop.map_protein),
                            selectinload(Experiment.images)
                            .selectinload(Image.fov_segmentation_mask),
                            selectinload(Experiment.map_protein),
                        )
                        .where(Experiment.id == exp_id)
                    )
                    experiment = result.scalar_one_or_none()
                    if not experiment:
                        logger.error(
                            f"Experiment {exp_id} not found during export for job {job_id}. "
                            f"Skipping - user requested {len(job.experiment_ids)} experiments."
                        )
                        continue

                    # Write experiment metadata
                    exp_meta = self._experiment_to_dict(experiment)
                    zf.writestr(
                        f"experiments/{exp_id}/experiment.json",
                        json.dumps(exp_meta, indent=2)
                    )

                    # Process images in batches
                    images = experiment.images
                    for batch_start in range(0, len(images), BATCH_SIZE):
                        batch = images[batch_start:batch_start + BATCH_SIZE]

                        for image in batch:
                            all_images.append(image)

                            # Write image files
                            if job.options.include_fov_images:
                                await self._write_image_files(zf, exp_id, image)

                            # Write image metadata
                            img_meta = self._image_to_dict(image)
                            zf.writestr(
                                f"experiments/{exp_id}/images/{image.id}/metadata.json",
                                json.dumps(img_meta, indent=2)
                            )

                            # Write crops
                            for crop in image.cell_crops:
                                all_crops.append(crop)

                                if job.options.include_crop_images:
                                    await self._write_crop_files(zf, exp_id, crop)

                                # Write crop metadata
                                crop_meta = self._crop_to_dict(crop)
                                zf.writestr(
                                    f"experiments/{exp_id}/crops/{crop.id}/metadata.json",
                                    json.dumps(crop_meta, indent=2)
                                )

                                processed_items += 1

                            # Write FOV mask
                            if job.options.include_masks and image.fov_segmentation_mask:
                                await self._write_fov_mask(
                                    zf, exp_id, image, job.options.mask_format
                                )

                            processed_items += 1

                        # Update progress
                        progress = (processed_items / max(total_items, 1)) * 80
                        await self._update_job_progress(
                            job_id,
                            progress,
                            f"Processing experiment {exp_idx + 1}/{len(job.experiment_ids)}"
                        )
                        # NOTE: Don't yield/truncate buffer during ZIP generation!
                        # ZIP files require the Central Directory to be written at the end,
                        # and truncating the buffer corrupts the internal offset tracking.

                # Write annotations in all formats
                await self._update_job_progress(job_id, 85, "Writing annotations")

                # Get unique class names
                class_names = await self._get_class_names(db, job.experiment_ids)

                # Write COCO format (always include as it's the default)
                coco_data = to_coco(all_images, all_crops)
                zf.writestr("annotations/coco.json", json.dumps(coco_data, indent=2))

                # Write format-specific annotations based on option
                if job.options.bbox_format == BBoxFormat.YOLO:
                    # Write classes.txt
                    zf.writestr("annotations/yolo/classes.txt", to_yolo_classes(class_names))
                    # Write per-image label files
                    for image in all_images:
                        img_crops = [c for c in all_crops if c.image_id == image.id]
                        if img_crops:
                            label_name = Path(image.original_filename).stem + ".txt"
                            yolo_content = to_yolo(image, img_crops, class_names)
                            zf.writestr(f"annotations/yolo/{label_name}", yolo_content)

                elif job.options.bbox_format == BBoxFormat.VOC:
                    # Write per-image XML files
                    for image in all_images:
                        img_crops = [c for c in all_crops if c.image_id == image.id]
                        if img_crops:
                            xml_name = Path(image.original_filename).stem + ".xml"
                            voc_content = to_voc(image, img_crops)
                            zf.writestr(f"annotations/voc/{xml_name}", voc_content)

                elif job.options.bbox_format == BBoxFormat.CSV:
                    csv_content = to_csv(all_images, all_crops)
                    zf.writestr("annotations/annotations.csv", csv_content)

                # Write embeddings
                if job.options.include_embeddings:
                    await self._update_job_progress(job_id, 90, "Writing embeddings")
                    await self._write_embeddings(zf, all_images, all_crops)

            # ZIP file is now properly closed (Central Directory written)
            # Yield the complete ZIP content
            buffer.seek(0)

            # Stream the ZIP in chunks for memory efficiency
            CHUNK_SIZE = 64 * 1024  # 64KB chunks
            while True:
                chunk = buffer.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

            # Mark job complete
            await self._update_job_progress(job_id, 100, "Complete", "completed")
            job = await self._get_job(job_id)
            if job:
                job.completed_at = datetime.now(timezone.utc)
                await self._save_job(job)

        except Exception as e:
            logger.exception(
                f"Export failed for job {job_id}. "
                f"Experiments: {job.experiment_ids if job else 'unknown'}"
            )
            job = await self._get_job(job_id)
            if job:
                job.status = "error"
                # Include more context in error message for debugging
                job.error_message = (
                    f"{type(e).__name__}: {str(e)}. "
                    f"Experiments: {job.experiment_ids[:5]}{'...' if len(job.experiment_ids) > 5 else ''}"
                )
                await self._save_job(job)
            raise

    async def _create_manifest(self, job: ExportJobData, db: AsyncSession) -> Dict[str, Any]:
        """Create export manifest."""
        return {
            "format_version": "1.0",
            "export_date": datetime.now(timezone.utc).isoformat(),
            "source": "MAPtimize",
            "options": job.options.model_dump(),
            "statistics": {
                "experiment_count": job.experiment_count,
                "image_count": job.image_count,
                "crop_count": job.crop_count,
                "mask_count": job.mask_count,
            },
            "experiment_ids": job.experiment_ids,
        }

    def _experiment_to_dict(self, exp: Experiment) -> Dict[str, Any]:
        """Convert experiment to serializable dict."""
        return {
            "id": exp.id,
            "name": exp.name,
            "description": exp.description,
            "status": exp.status.value if exp.status else None,
            "fasta_sequence": exp.fasta_sequence,
            "map_protein": {
                "id": exp.map_protein.id,
                "name": exp.map_protein.name,
            } if exp.map_protein else None,
            "created_at": exp.created_at.isoformat() if exp.created_at else None,
            "updated_at": exp.updated_at.isoformat() if exp.updated_at else None,
        }

    def _image_to_dict(self, img: Image) -> Dict[str, Any]:
        """Convert image to serializable dict."""
        return {
            "id": img.id,
            "original_filename": img.original_filename,
            "width": img.width,
            "height": img.height,
            "z_slices": img.z_slices,
            "status": img.status.value if img.status else None,
            "embedding_model": img.embedding_model,
            "created_at": img.created_at.isoformat() if img.created_at else None,
        }

    def _crop_to_dict(self, crop: CellCrop) -> Dict[str, Any]:
        """Convert crop to serializable dict."""
        return {
            "id": crop.id,
            "image_id": crop.image_id,
            "bbox_x": crop.bbox_x,
            "bbox_y": crop.bbox_y,
            "bbox_w": crop.bbox_w,
            "bbox_h": crop.bbox_h,
            "detection_confidence": crop.detection_confidence,
            "map_protein": {
                "id": crop.map_protein.id,
                "name": crop.map_protein.name,
            } if crop.map_protein else None,
            "bundleness_score": crop.bundleness_score,
            "mean_intensity": crop.mean_intensity,
            "embedding_model": crop.embedding_model,
            "excluded": crop.excluded,
            "created_at": crop.created_at.isoformat() if crop.created_at else None,
        }

    async def _write_image_files(
        self,
        zf: zipfile.ZipFile,
        exp_id: int,
        image: Image
    ) -> None:
        """Write image files (MIP, SUM, thumbnail) to ZIP."""
        base_path = f"experiments/{exp_id}/images/{image.id}"
        write_file_to_zip(zf, image.mip_path, f"{base_path}/mip.tiff")
        write_file_to_zip(zf, image.sum_path, f"{base_path}/sum.tiff")
        write_file_to_zip(zf, image.thumbnail_path, f"{base_path}/thumbnail.png")

    async def _write_crop_files(
        self,
        zf: zipfile.ZipFile,
        exp_id: int,
        crop: CellCrop
    ) -> None:
        """Write crop files (MIP, SUM) to ZIP."""
        base_path = f"experiments/{exp_id}/crops/{crop.id}"
        write_file_to_zip(zf, crop.mip_path, f"{base_path}/mip.tiff")
        write_file_to_zip(zf, crop.sum_crop_path, f"{base_path}/sum.tiff")

    async def _write_fov_mask(
        self,
        zf: zipfile.ZipFile,
        exp_id: int,
        image: Image,
        mask_format: MaskFormat = MaskFormat.PNG
    ) -> None:
        """Write FOV segmentation mask in specified format."""
        if not image.fov_segmentation_mask:
            return

        mask = image.fov_segmentation_mask
        polygon_points = mask.polygon_points

        if not polygon_points or not image.width or not image.height:
            return

        try:
            # Convert polygon points to flat tuple list
            points = [(p[0], p[1]) for p in polygon_points]
            if len(points) < 3:
                return

            base_path = f"experiments/{exp_id}/masks"

            if mask_format == MaskFormat.PNG:
                # Binary mask as PNG
                from PIL import ImageDraw
                img = PILImage.new('L', (image.width, image.height), 0)
                draw = ImageDraw.Draw(img)
                draw.polygon(points, fill=255)

                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
                buffer.seek(0)
                zf.writestr(f"{base_path}/fov_{image.id}.png", buffer.read())

            elif mask_format == MaskFormat.COCO_RLE:
                # COCO RLE encoding (integer counts)
                rle_data = self._polygon_to_coco_rle(points, image.width, image.height)
                mask_json = {
                    "image_id": image.id,
                    "segmentation": rle_data,
                    "area": mask.area_pixels or 0,
                }
                zf.writestr(
                    f"{base_path}/fov_{image.id}.json",
                    json.dumps(mask_json, indent=2)
                )

            elif mask_format == MaskFormat.COCO:
                # COCO 1.0 format (compressed string RLE)
                rle_data = self._polygon_to_coco_string_rle(points, image.width, image.height)
                mask_json = {
                    "image_id": image.id,
                    "segmentation": rle_data,
                    "area": mask.area_pixels or 0,
                    "iscrowd": 1,  # RLE format requires iscrowd=1 in COCO
                }
                zf.writestr(
                    f"{base_path}/fov_{image.id}.json",
                    json.dumps(mask_json, indent=2)
                )

            elif mask_format == MaskFormat.POLYGON:
                # Polygon coordinates as JSON
                mask_json = {
                    "image_id": image.id,
                    "polygon": points,
                    "area": mask.area_pixels or 0,
                    "width": image.width,
                    "height": image.height,
                }
                zf.writestr(
                    f"{base_path}/fov_{image.id}.json",
                    json.dumps(mask_json, indent=2)
                )

        except Exception as e:
            # Log full traceback for debugging - this is a data integrity concern
            logger.exception(
                f"Failed to write FOV mask for image {image.id} in experiment {exp_id}. "
                f"Mask format: {mask_format.value}. Users should be informed of missing masks."
            )

    def _polygon_to_rle_counts(
        self,
        polygon: list,
        width: int,
        height: int
    ) -> tuple[list[int], int, int]:
        """
        Convert polygon to RLE counts.

        Returns:
            Tuple of (counts list, height, width) for COCO format.
        """
        from PIL import ImageDraw

        # Create binary mask
        img = PILImage.new('L', (width, height), 0)
        draw = ImageDraw.Draw(img)
        draw.polygon(polygon, fill=1)

        # Convert to numpy array and flatten in column-major (Fortran) order for COCO
        mask_array = np.array(img, dtype=np.uint8)
        flat_mask = mask_array.flatten(order='F')

        # Run-length encode
        counts = []
        current_val = 0
        count = 0

        for val in flat_mask:
            if val == current_val:
                count += 1
            else:
                counts.append(count)
                count = 1
                current_val = val
        counts.append(count)

        # COCO RLE starts with 0s count
        if flat_mask[0] == 1:
            counts.insert(0, 0)

        return counts, height, width

    def _polygon_to_coco_rle(
        self,
        polygon: list,
        width: int,
        height: int
    ) -> dict:
        """Convert polygon to COCO RLE format (integer counts)."""
        counts, h, w = self._polygon_to_rle_counts(polygon, width, height)
        return {"size": [h, w], "counts": counts}

    def _polygon_to_coco_string_rle(
        self,
        polygon: list,
        width: int,
        height: int
    ) -> dict:
        """Convert polygon to COCO compressed string RLE format."""
        counts, h, w = self._polygon_to_rle_counts(polygon, width, height)
        compressed = self._encode_rle_counts(counts)
        return {"size": [h, w], "counts": compressed}

    def _encode_rle_counts(self, counts: list) -> str:
        """Encode RLE counts as compressed COCO string."""
        # COCO uses a custom LEB128-like encoding for counts
        # Each value is encoded in groups of 6 bits + continuation bit
        result = []
        for count in counts:
            if count == 0:
                result.append(48)  # '0' character
            else:
                while count > 0:
                    # Take 5 bits
                    val = count & 0x1F
                    count >>= 5
                    # Add offset and continuation bit if more data
                    if count > 0:
                        val |= 0x20  # Set continuation bit
                    result.append(val + 48)  # Add to '0' base
        return ''.join(chr(c) for c in result)

    async def _write_embeddings(
        self,
        zf: zipfile.ZipFile,
        images: List[Image],
        crops: List[CellCrop]
    ) -> None:
        """Write embeddings as NPY files."""
        write_embeddings_to_zip(
            zf, images, "embeddings/fov_embeddings.npy", "embeddings/fov_ids.json"
        )
        write_embeddings_to_zip(
            zf, crops, "embeddings/crop_embeddings.npy", "embeddings/crop_ids.json"
        )

    async def _get_class_names(
        self,
        db: AsyncSession,
        experiment_ids: List[int]
    ) -> List[str]:
        """Get unique class names from experiments."""
        result = await db.execute(
            select(MapProtein.name)
            .join(CellCrop, CellCrop.map_protein_id == MapProtein.id)
            .join(Image, CellCrop.image_id == Image.id)
            .where(Image.experiment_id.in_(experiment_ids))
            .distinct()
        )
        names = [r[0] for r in result.all() if r[0]]

        # Always include "cell" as default
        if "cell" not in names:
            names.insert(0, "cell")

        return names


# Singleton instance
export_service = ExportService()
