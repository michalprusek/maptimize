"""
Import service for processing uploaded annotation files.

Handles:
- Validating uploaded ZIP files
- Detecting annotation format
- Executing imports (creating experiments, images, crops)
- Native MAPtimize format reimport with full data restoration
- Progress tracking via Redis
"""
import io
import json
import logging
import os
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from models import CellCrop, Experiment, Image, MapProtein
from models.experiment import ExperimentStatus
from models.image import UploadStatus
from models.segmentation import FOVSegmentationMask
from schemas.export_import import (
    CropImportData,
    ImportFormat,
    ImportJobData,
    ImportStatusResponse,
    ImportValidationResult,
)
from services.annotation_converters import detect_import_format, parse_annotations
from services.job_manager import BaseJobManager

logger = logging.getLogger(__name__)
settings = get_settings()

# File extension constants (SSOT)
ANNOTATION_EXTENSIONS = ('.json', '.xml', '.txt', '.csv')
IMAGE_EXTENSIONS = ('.tiff', '.tif', '.png', '.jpg', '.jpeg')

# Security limits
MAX_ANNOTATION_FILE_SIZE = 100 * 1024 * 1024  # 100MB per annotation file
MAX_TOTAL_UNCOMPRESSED_SIZE = 10 * 1024 * 1024 * 1024  # 10GB total
MAX_COMPRESSION_RATIO = 100  # Prevent ZIP bombs


def is_annotation_file(filename: str) -> bool:
    """Check if file is an annotation file (not an image)."""
    return filename.lower().endswith(ANNOTATION_EXTENSIONS)


def is_image_file(filename: str) -> bool:
    """Check if file is an image file."""
    return filename.lower().endswith(IMAGE_EXTENSIONS)


def create_error_validation_result(
    job_id: str,
    errors: list[str],
    warnings: list[str] | None = None
) -> ImportValidationResult:
    """Create a validation result for error cases."""
    return ImportValidationResult(
        job_id=job_id,
        detected_format=ImportFormat.COCO,
        is_valid=False,
        image_count=0,
        annotation_count=0,
        errors=errors,
        warnings=warnings or [],
    )


async def lookup_protein_by_name(db: AsyncSession, protein_name: str | None) -> int | None:
    """Look up a protein by name and return its ID, or None if not found."""
    if not protein_name:
        return None
    result = await db.execute(
        select(MapProtein).where(MapProtein.name == protein_name)
    )
    protein = result.scalar_one_or_none()
    return protein.id if protein else None


def find_subdirectories(
    file_list: list[str],
    base_path: str,
    marker_file: str
) -> set[str]:
    """
    Find subdirectory IDs in a ZIP file list.

    Looks for paths like "{base_path}/{id}/{marker_file}" and returns
    the set of IDs found.
    """
    dirs = set()
    prefix = f"{base_path}/"
    for f in file_list:
        if f.startswith(prefix) and f.endswith(marker_file):
            parts = f[len(prefix):].split("/")
            if len(parts) >= 2:
                dirs.add(parts[0])
    return dirs


def write_file_from_zip(
    zf: zipfile.ZipFile,
    zip_path: str,
    save_path: Path,
    file_list: list[str],
    base_dir: Path | None = None
) -> str | None:
    """
    Extract a file from ZIP and save it.

    Args:
        zf: Open ZipFile object
        zip_path: Path within the ZIP file
        save_path: Destination path to save the file
        file_list: List of valid files in the ZIP
        base_dir: Optional base directory for path traversal protection

    Returns:
        The saved path as string, or None if file not found
    """
    if zip_path not in file_list:
        return None

    # Path traversal protection: ensure save_path stays within base_dir
    if base_dir is not None:
        resolved = save_path.resolve()
        base_resolved = base_dir.resolve()
        if not str(resolved).startswith(str(base_resolved)):
            logger.warning(f"Path traversal attempt detected: {save_path} is outside {base_dir}")
            return None

    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, 'wb') as f:
        f.write(zf.read(zip_path))
    return str(save_path)


def load_embeddings_from_zip(
    zf: zipfile.ZipFile,
    embeddings_path: str,
    ids_path: str,
    file_list: list[str]
) -> tuple[np.ndarray | None, list[int]]:
    """Load embeddings and IDs from ZIP. Returns (embeddings, ids) or (None, [])."""
    if embeddings_path not in file_list or ids_path not in file_list:
        return None, []
    embeddings = np.load(io.BytesIO(zf.read(embeddings_path)))
    ids = json.loads(zf.read(ids_path).decode('utf-8'))
    return embeddings, ids


def extract_projections_from_zip(
    zf: zipfile.ZipFile,
    base_path: str,
    upload_dir: Path,
    file_id: str,
    file_list: list[str]
) -> tuple[str | None, str | None]:
    """
    Extract MIP and SUM projections from ZIP to storage directory.

    Args:
        zf: Open ZipFile object
        base_path: Base path within ZIP (e.g., "experiments/1/images/2")
        upload_dir: Target directory for extracted files
        file_id: Unique identifier prefix for filenames
        file_list: List of valid files in the ZIP

    Returns:
        Tuple of (mip_path, sum_path), either may be None if not found
    """
    mip_path = write_file_from_zip(
        zf, f"{base_path}/mip.tiff",
        upload_dir / f"{file_id}_mip.tiff", file_list
    )
    sum_path = write_file_from_zip(
        zf, f"{base_path}/sum.tiff",
        upload_dir / f"{file_id}_sum.tiff", file_list
    )
    return mip_path, sum_path


class ImportService(BaseJobManager[ImportJobData]):
    """Service for importing experiment data."""

    _redis_key_prefix = "import_job:"
    _job_model = ImportJobData

    async def validate_import(
        self,
        file_path: str,
        user_id: int
    ) -> ImportValidationResult:
        """
        Validate an import file and detect its format.

        Args:
            file_path: Path to uploaded ZIP file
            user_id: Current user ID

        Returns:
            ImportValidationResult with format detection and file counts
        """
        job_id = str(uuid.uuid4())
        errors: List[str] = []
        warnings: List[str] = []

        # Create job record
        job = ImportJobData(
            job_id=job_id,
            user_id=user_id,
            file_path=file_path,
            status="validating",
            created_at=datetime.now(timezone.utc),
        )
        await self._save_job(job)

        try:
            # Read ZIP contents
            if not os.path.exists(file_path):
                errors.append("File not found")
                return create_error_validation_result(job_id, errors, warnings)

            with zipfile.ZipFile(file_path, 'r') as zf:
                # Get file listing
                file_list = zf.namelist()
                zip_contents = {}

                # ZIP bomb protection: check total uncompressed size and compression ratio
                total_uncompressed = sum(info.file_size for info in zf.filelist)
                total_compressed = sum(info.compress_size for info in zf.filelist)

                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_SIZE:
                    errors.append(
                        f"ZIP file too large: {total_uncompressed / (1024**3):.1f}GB "
                        f"exceeds {MAX_TOTAL_UNCOMPRESSED_SIZE / (1024**3):.0f}GB limit"
                    )
                    return create_error_validation_result(job_id, errors, warnings)

                if total_compressed > 0:
                    compression_ratio = total_uncompressed / total_compressed
                    if compression_ratio > MAX_COMPRESSION_RATIO:
                        errors.append(
                            f"Suspicious compression ratio ({compression_ratio:.0f}:1) - "
                            "possible ZIP bomb detected"
                        )
                        return create_error_validation_result(job_id, errors, warnings)

                # Read annotation files with size limits (skip large image files)
                for name in file_list:
                    if is_annotation_file(name):
                        try:
                            # Check file size before reading
                            info = zf.getinfo(name)
                            if info.file_size > MAX_ANNOTATION_FILE_SIZE:
                                warnings.append(
                                    f"Skipping {name}: file too large "
                                    f"({info.file_size / (1024*1024):.1f}MB)"
                                )
                                continue
                            zip_contents[name] = zf.read(name)
                        except MemoryError:
                            errors.append(f"Out of memory reading {name}")
                            return create_error_validation_result(job_id, errors, warnings)
                        except Exception as e:
                            warnings.append(f"Could not read {name}: {e}")

                # Detect format
                detected_format = detect_import_format(zip_contents)

                # Count images
                image_files = [f for f in file_list if is_image_file(f)]
                image_count = len(image_files)

                # Parse annotations based on format
                annotation_count = 0
                has_embeddings = False
                has_masks = False
                crops: List[CropImportData] = []

                if detected_format == ImportFormat.MAPTIMIZE:
                    # Native format - uses manifest for counts
                    manifest_key = next(
                        (k for k in zip_contents if k.endswith("manifest.json")),
                        None
                    )
                    if manifest_key:
                        manifest = json.loads(zip_contents[manifest_key])
                        has_embeddings = any("embeddings" in f for f in file_list)
                        has_masks = any("masks" in f for f in file_list)
                        annotation_count = manifest.get("statistics", {}).get("crop_count", 0)
                else:
                    # Use unified parser for COCO, YOLO, VOC, CSV formats
                    crops, parse_errors, parse_warnings = parse_annotations(
                        zip_contents, image_files, detected_format
                    )
                    errors.extend(parse_errors)
                    warnings.extend(parse_warnings)
                    annotation_count = len(crops)

                # Check for embeddings and masks (if not already set by MAPtimize format)
                if not has_embeddings:
                    has_embeddings = any(".npy" in f for f in file_list)
                if not has_masks:
                    has_masks = any("mask" in f.lower() and f.endswith(".png") for f in file_list)

            # Validate
            is_valid = len(errors) == 0 and image_count > 0

            # Update job with validation result
            result = ImportValidationResult(
                job_id=job_id,
                detected_format=detected_format,
                is_valid=is_valid,
                image_count=image_count,
                annotation_count=annotation_count,
                has_embeddings=has_embeddings,
                has_masks=has_masks,
                errors=errors,
                warnings=warnings,
            )

            job.detected_format = detected_format
            job.validation_result = result
            job.status = "validated" if is_valid else "validation_failed"
            await self._save_job(job)

            return result

        except zipfile.BadZipFile:
            errors.append("Invalid ZIP file")
            return create_error_validation_result(job_id, errors, warnings)
        except Exception as e:
            logger.exception(f"Import validation failed: {e}")
            errors.append(f"Validation error: {str(e)}")
            return create_error_validation_result(job_id, errors, warnings)

    async def execute_import(
        self,
        job_id: str,
        experiment_name: str,
        import_format: ImportFormat,
        create_crops: bool,
        user_id: int,
        db: AsyncSession
    ) -> ImportStatusResponse:
        """
        Execute an import job.

        Args:
            job_id: Import job ID from validation
            experiment_name: Name for new experiment
            import_format: Confirmed import format
            create_crops: Whether to create CellCrop entries
            user_id: Current user ID
            db: Database session

        Returns:
            ImportStatusResponse with import results
        """
        job = await self._get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        if job.user_id != user_id:
            raise ValueError("Access denied")

        if job.status not in ("validated",):
            raise ValueError(f"Job is not ready for import (status: {job.status})")

        job.experiment_name = experiment_name
        job.status = "importing"
        await self._save_job(job)

        try:
            # Create placeholder experiment (will be used for non-MAPtimize formats)
            experiment = Experiment(
                name=experiment_name,
                user_id=user_id,
                status=ExperimentStatus.ACTIVE,
            )
            db.add(experiment)
            await db.flush()

            job.experiment_id = experiment.id
            await self._save_job(job)

            # Process the ZIP file
            with zipfile.ZipFile(job.file_path, 'r') as zf:
                created_experiments = await self._import_from_zip(
                    zf=zf,
                    job=job,
                    experiment=experiment,
                    import_format=import_format,
                    create_crops=create_crops,
                    db=db
                )

            # For MAPtimize format, delete the placeholder if it wasn't used
            if import_format == ImportFormat.MAPTIMIZE and created_experiments:
                # Check if placeholder was replaced
                if experiment not in created_experiments:
                    await db.delete(experiment)
                # Update job with first created experiment ID
                job.experiment_id = created_experiments[0].id

            await self._save_job(job)

            # Commit everything
            await db.commit()

            # Mark complete
            job.status = "completed"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            await self._save_job(job)

            # Clean up temp file
            try:
                os.unlink(job.file_path)
                shutil.rmtree(os.path.dirname(job.file_path), ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {job.file_path}: {e}")

            return ImportStatusResponse(
                job_id=job_id,
                status="completed",
                progress_percent=100,
                experiment_id=job.experiment_id,
                images_imported=job.images_imported,
                crops_created=job.crops_created,
                created_at=job.created_at,
                completed_at=job.completed_at,
            )

        except Exception as e:
            await db.rollback()
            logger.exception(f"Import execution failed: {e}")

            job.status = "error"
            job.error_message = str(e)
            await self._save_job(job)

            raise

    async def _import_from_zip(
        self,
        zf: zipfile.ZipFile,
        job: ImportJobData,
        experiment: Experiment,
        import_format: ImportFormat,
        create_crops: bool,
        db: AsyncSession
    ) -> List[Experiment]:
        """Import data from ZIP file. Returns list of created experiments."""
        # For MAPtimize format, use specialized importer
        if import_format == ImportFormat.MAPTIMIZE:
            return await self._import_maptimize_format(
                zf=zf,
                job=job,
                base_experiment_name=experiment.name,
                user_id=experiment.user_id,
                db=db
            )

        # For other formats, use the provided experiment
        file_list = zf.namelist()
        image_files = [f for f in file_list if is_image_file(f)]
        total_images = len(image_files)

        # Read annotation files
        zip_contents = {}
        for name in file_list:
            if is_annotation_file(name):
                zip_contents[name] = zf.read(name)

        # Parse annotations using unified parser
        crops: List[CropImportData] = []
        if create_crops:
            crops, _, _ = parse_annotations(zip_contents, image_files, import_format)

        # Group crops by image filename
        crops_by_filename: Dict[str, List[CropImportData]] = {}
        for crop in crops:
            crops_by_filename.setdefault(crop.image_filename, []).append(crop)

        # Process images
        for idx, image_file in enumerate(image_files):
            await self._update_job_progress(
                job.job_id,
                (idx / max(total_images, 1)) * 100,
                f"Importing image {idx + 1}/{total_images}"
            )

            # Extract and save image
            image_data = zf.read(image_file)
            image_record = await self._import_image(
                image_data=image_data,
                original_filename=os.path.basename(image_file),
                experiment=experiment,
                db=db
            )

            if image_record:
                job.images_imported += 1

                # Create crops for this image
                filename = os.path.basename(image_file)
                image_crops = crops_by_filename.get(filename, [])

                # Also try without extension
                stem = Path(filename).stem
                if stem != filename:
                    image_crops.extend(crops_by_filename.get(stem, []))

                for crop_data in image_crops:
                    await self._create_crop(
                        image=image_record,
                        crop_data=crop_data,
                        db=db
                    )
                    job.crops_created += 1

                await self._save_job(job)

        return [experiment]

    async def _import_maptimize_format(
        self,
        zf: zipfile.ZipFile,
        job: ImportJobData,
        base_experiment_name: str,
        user_id: int,
        db: AsyncSession
    ) -> List[Experiment]:
        """
        Import native MAPtimize format with full data restoration.

        Handles multiple experiments, images with MIP/SUM projections,
        crops, embeddings, and segmentation masks.
        """
        file_list = zf.namelist()
        created_experiments: List[Experiment] = []

        # Read manifest
        manifest_data = {}
        manifest_key = next((k for k in file_list if k.endswith("manifest.json")), None)
        if manifest_key:
            manifest_data = json.loads(zf.read(manifest_key).decode('utf-8'))

        # Find all experiment directories
        exp_dirs = set()
        for f in file_list:
            if f.startswith("experiments/") and "/experiment.json" in f:
                # Extract experiment ID from path like "experiments/123/experiment.json"
                parts = f.split("/")
                if len(parts) >= 3:
                    exp_dirs.add(parts[1])

        total_experiments = len(exp_dirs)
        if total_experiments == 0:
            logger.warning("No experiments found in MAPtimize export")
            return []

        # ID mappings for embeddings
        old_to_new_image_ids: Dict[int, int] = {}
        old_to_new_crop_ids: Dict[int, int] = {}

        # Process each experiment
        for exp_idx, old_exp_id in enumerate(sorted(exp_dirs)):
            exp_base_path = f"experiments/{old_exp_id}"

            # Read experiment metadata
            exp_json_path = f"{exp_base_path}/experiment.json"
            if exp_json_path not in file_list:
                continue

            exp_meta = json.loads(zf.read(exp_json_path).decode('utf-8'))

            # Create experiment name
            original_name = exp_meta.get("name", f"Experiment {old_exp_id}")
            if total_experiments == 1:
                exp_name = base_experiment_name
            else:
                exp_name = f"{base_experiment_name} - {original_name}"

            # Find protein by name if specified
            protein_name = (exp_meta.get("map_protein") or {}).get("name")
            map_protein_id = await lookup_protein_by_name(db, protein_name)

            # Create experiment
            experiment = Experiment(
                name=exp_name,
                description=exp_meta.get("description") or f"Imported from: {original_name}",
                user_id=user_id,
                status=ExperimentStatus.ACTIVE,
                map_protein_id=map_protein_id,
                fasta_sequence=exp_meta.get("fasta_sequence"),
            )
            db.add(experiment)
            await db.flush()
            created_experiments.append(experiment)

            await self._update_job_progress(
                job.job_id,
                (exp_idx / total_experiments) * 30,
                f"Created experiment: {exp_name}"
            )

            # Find all images for this experiment
            image_dirs = find_subdirectories(file_list, f"{exp_base_path}/images", "metadata.json")

            # Import images
            for img_idx, old_image_id in enumerate(sorted(image_dirs)):
                img_base_path = f"{exp_base_path}/images/{old_image_id}"

                # Read image metadata
                img_meta_path = f"{img_base_path}/metadata.json"
                if img_meta_path not in file_list:
                    continue

                img_meta = json.loads(zf.read(img_meta_path).decode('utf-8'))

                # Import image with its files
                new_image = await self._import_maptimize_image(
                    zf=zf,
                    img_base_path=img_base_path,
                    img_meta=img_meta,
                    experiment=experiment,
                    file_list=file_list,
                    db=db
                )

                if new_image:
                    old_id = int(old_image_id)
                    old_to_new_image_ids[old_id] = new_image.id
                    job.images_imported += 1

                    # Update progress
                    progress = 30 + (exp_idx / total_experiments) * 40 + \
                               (img_idx / max(len(image_dirs), 1)) * (40 / total_experiments)
                    await self._update_job_progress(
                        job.job_id,
                        progress,
                        f"Imported image {img_idx + 1}/{len(image_dirs)}"
                    )

            # Find all crops for this experiment
            crop_dirs = find_subdirectories(file_list, f"{exp_base_path}/crops", "metadata.json")

            # Import crops
            for crop_idx, old_crop_id in enumerate(sorted(crop_dirs)):
                crop_base_path = f"{exp_base_path}/crops/{old_crop_id}"

                # Read crop metadata
                crop_meta_path = f"{crop_base_path}/metadata.json"
                if crop_meta_path not in file_list:
                    continue

                crop_meta = json.loads(zf.read(crop_meta_path).decode('utf-8'))

                # Map old image ID to new
                old_image_id = crop_meta.get("image_id")
                new_image_id = old_to_new_image_ids.get(old_image_id)
                if not new_image_id:
                    logger.warning(f"Could not find image for crop {old_crop_id}")
                    continue

                # Import crop
                new_crop = await self._import_maptimize_crop(
                    zf=zf,
                    crop_base_path=crop_base_path,
                    crop_meta=crop_meta,
                    new_image_id=new_image_id,
                    experiment=experiment,
                    file_list=file_list,
                    db=db
                )

                if new_crop:
                    old_id = int(old_crop_id)
                    old_to_new_crop_ids[old_id] = new_crop.id
                    job.crops_created += 1

            # Import masks for this experiment
            await self._import_maptimize_masks(
                zf=zf,
                exp_base_path=exp_base_path,
                old_to_new_image_ids=old_to_new_image_ids,
                file_list=file_list,
                db=db
            )

            await self._save_job(job)

        # Import embeddings (global, after all experiments)
        await self._import_maptimize_embeddings(
            zf=zf,
            old_to_new_image_ids=old_to_new_image_ids,
            old_to_new_crop_ids=old_to_new_crop_ids,
            file_list=file_list,
            db=db
        )

        await self._update_job_progress(job.job_id, 100, "Import complete")

        return created_experiments

    async def _import_maptimize_image(
        self,
        zf: zipfile.ZipFile,
        img_base_path: str,
        img_meta: Dict[str, Any],
        experiment: Experiment,
        file_list: List[str],
        db: AsyncSession
    ) -> Optional[Image]:
        """Import a single image from MAPtimize format."""
        try:
            # Setup storage directory
            upload_dir = settings.upload_dir / str(experiment.user_id) / str(experiment.id)
            file_id = str(uuid.uuid4())[:8]
            original_filename = img_meta.get("original_filename", "image.tiff")

            # Import projections and thumbnail
            mip_path, sum_path = extract_projections_from_zip(
                zf, img_base_path, upload_dir, file_id, file_list
            )
            thumb_path = write_file_from_zip(
                zf, f"{img_base_path}/thumbnail.png",
                upload_dir / f"{file_id}_thumb.png", file_list
            )

            # Create database record
            image = Image(
                experiment_id=experiment.id,
                original_filename=original_filename,
                file_path=mip_path or sum_path,
                mip_path=mip_path,
                sum_path=sum_path,
                thumbnail_path=thumb_path,
                width=img_meta.get("width"),
                height=img_meta.get("height"),
                z_slices=img_meta.get("z_slices"),
                status=UploadStatus.READY,
                embedding_model=img_meta.get("embedding_model"),
            )
            db.add(image)
            await db.flush()

            return image

        except Exception as e:
            logger.warning(f"Failed to import MAPtimize image: {e}")
            return None

    async def _import_maptimize_crop(
        self,
        zf: zipfile.ZipFile,
        crop_base_path: str,
        crop_meta: Dict[str, Any],
        new_image_id: int,
        experiment: Experiment,
        file_list: List[str],
        db: AsyncSession
    ) -> Optional[CellCrop]:
        """Import a single crop from MAPtimize format."""
        try:
            # Setup storage directory
            upload_dir = settings.upload_dir / str(experiment.user_id) / str(experiment.id) / "crops"
            file_id = str(uuid.uuid4())[:8]

            # Import crop images
            mip_path, sum_path = extract_projections_from_zip(
                zf, crop_base_path, upload_dir, file_id, file_list
            )

            # Find protein by name
            protein_name = (crop_meta.get("map_protein") or {}).get("name")
            map_protein_id = await lookup_protein_by_name(db, protein_name)

            # Create database record
            crop = CellCrop(
                image_id=new_image_id,
                bbox_x=crop_meta.get("bbox_x", 0),
                bbox_y=crop_meta.get("bbox_y", 0),
                bbox_w=crop_meta.get("bbox_w", 0),
                bbox_h=crop_meta.get("bbox_h", 0),
                detection_confidence=crop_meta.get("detection_confidence"),
                map_protein_id=map_protein_id,
                bundleness_score=crop_meta.get("bundleness_score"),
                mean_intensity=crop_meta.get("mean_intensity"),
                mip_path=mip_path,
                sum_crop_path=sum_path,
                embedding_model=crop_meta.get("embedding_model"),
                excluded=crop_meta.get("excluded", False),
            )
            db.add(crop)
            await db.flush()

            return crop

        except Exception as e:
            logger.warning(f"Failed to import MAPtimize crop: {e}")
            return None

    async def _import_maptimize_masks(
        self,
        zf: zipfile.ZipFile,
        exp_base_path: str,
        old_to_new_image_ids: Dict[int, int],
        file_list: List[str],
        db: AsyncSession
    ) -> None:
        """Import FOV segmentation masks from MAPtimize format."""
        masks_path = f"{exp_base_path}/masks/"

        for f in file_list:
            if not f.startswith(masks_path) or not f.endswith(".png"):
                continue

            # Extract image ID from filename like "fov_123.png"
            filename = os.path.basename(f)
            if not filename.startswith("fov_"):
                continue

            try:
                old_image_id = int(filename[4:-4])  # Remove "fov_" and ".png"
                new_image_id = old_to_new_image_ids.get(old_image_id)
                if not new_image_id:
                    continue

                # Read mask PNG and convert to polygon
                mask_data = zf.read(f)
                polygon_points = self._png_mask_to_polygon(mask_data)

                if polygon_points and len(polygon_points) >= 3:
                    mask = FOVSegmentationMask(
                        image_id=new_image_id,
                        polygon_points=polygon_points,
                    )
                    db.add(mask)

            except Exception as e:
                logger.warning(f"Failed to import mask {f}: {e}")

    def _png_mask_to_polygon(self, png_data: bytes) -> Optional[List[List[int]]]:
        """Convert PNG binary mask to polygon points."""
        try:
            img = PILImage.open(io.BytesIO(png_data)).convert('L')
            img_array = np.array(img)

            # Find contours using simple edge detection
            # This is a simplified approach - for production, consider using cv2.findContours
            from scipy import ndimage

            # Find edges
            edges = ndimage.binary_erosion(img_array > 127) ^ (img_array > 127)

            # Get edge points
            points = np.argwhere(edges)
            if len(points) < 3:
                return None

            # Convert to [x, y] format and simplify
            # Sort points by angle from centroid to create ordered polygon
            centroid = points.mean(axis=0)
            angles = np.arctan2(points[:, 0] - centroid[0], points[:, 1] - centroid[1])
            sorted_indices = np.argsort(angles)
            sorted_points = points[sorted_indices]

            # Simplify by taking every Nth point
            step = max(1, len(sorted_points) // 100)
            simplified = sorted_points[::step]

            # Convert to [x, y] (swap row/col to x/y)
            return [[int(p[1]), int(p[0])] for p in simplified]

        except Exception as e:
            logger.warning(f"Failed to convert PNG mask to polygon: {e}")
            return None

    async def _import_maptimize_embeddings(
        self,
        zf: zipfile.ZipFile,
        old_to_new_image_ids: Dict[int, int],
        old_to_new_crop_ids: Dict[int, int],
        file_list: List[str],
        db: AsyncSession
    ) -> None:
        """Import embeddings from MAPtimize format."""
        # Define embedding sources to import
        embedding_configs = [
            ("fov", "embeddings/fov_embeddings.npy", "embeddings/fov_ids.json", old_to_new_image_ids, Image),
            ("crop", "embeddings/crop_embeddings.npy", "embeddings/crop_ids.json", old_to_new_crop_ids, CellCrop),
        ]

        for name, emb_path, ids_path, id_mapping, model_class in embedding_configs:
            try:
                embeddings, old_ids = load_embeddings_from_zip(zf, emb_path, ids_path, file_list)
                if embeddings is None:
                    continue

                for i, old_id in enumerate(old_ids):
                    new_id = id_mapping.get(old_id)
                    if new_id and i < len(embeddings):
                        result = await db.execute(select(model_class).where(model_class.id == new_id))
                        record = result.scalar_one_or_none()
                        if record:
                            record.embedding = embeddings[i].tolist()
            except Exception as e:
                logger.warning(f"Failed to import {name} embeddings: {e}")

    async def _import_image(
        self,
        image_data: bytes,
        original_filename: str,
        experiment: Experiment,
        db: AsyncSession
    ) -> Optional[Image]:
        """Import a single image."""
        try:
            # Determine save path
            upload_dir = settings.upload_dir / str(experiment.user_id) / str(experiment.id)
            upload_dir.mkdir(parents=True, exist_ok=True)

            # Generate unique filename
            file_id = str(uuid.uuid4())[:8]
            save_filename = f"{file_id}_{original_filename}"
            save_path = upload_dir / save_filename

            # Save file
            with open(save_path, 'wb') as f:
                f.write(image_data)

            # Get image dimensions
            width, height = None, None
            try:
                img = PILImage.open(io.BytesIO(image_data))
                width, height = img.size
            except Exception:
                pass

            # Create database record
            image = Image(
                experiment_id=experiment.id,
                original_filename=original_filename,
                file_path=str(save_path),
                mip_path=str(save_path),  # For imported images, source is the MIP
                width=width,
                height=height,
                status=UploadStatus.READY,
                file_size=len(image_data),
            )
            db.add(image)
            await db.flush()

            return image

        except Exception as e:
            logger.warning(f"Failed to import image {original_filename}: {e}")
            return None

    async def _create_crop(
        self,
        image: Image,
        crop_data: CropImportData,
        db: AsyncSession
    ) -> Optional[CellCrop]:
        """Create a cell crop from import data."""
        try:
            # Look up protein by name if specified (skip default "cell" class)
            protein_name = crop_data.class_name if crop_data.class_name != "cell" else None
            map_protein_id = await lookup_protein_by_name(db, protein_name)

            crop = CellCrop(
                image_id=image.id,
                bbox_x=crop_data.bbox_x,
                bbox_y=crop_data.bbox_y,
                bbox_w=crop_data.bbox_w,
                bbox_h=crop_data.bbox_h,
                detection_confidence=crop_data.confidence,
                map_protein_id=map_protein_id,
            )
            db.add(crop)
            await db.flush()

            return crop

        except Exception as e:
            logger.warning(f"Failed to create crop: {e}")
            return None

    async def get_import_status(self, job_id: str) -> Optional[ImportStatusResponse]:
        """Get current status of an import job."""
        job = await self._get_job(job_id)
        if not job:
            return None

        return ImportStatusResponse(
            job_id=job.job_id,
            status=job.status,
            progress_percent=job.progress_percent,
            current_step=job.current_step,
            error_message=job.error_message,
            experiment_id=job.experiment_id,
            images_imported=job.images_imported,
            crops_created=job.crops_created,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )


# Singleton instance
import_service = ImportService()
