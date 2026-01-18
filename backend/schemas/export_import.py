"""Export/Import schemas for annotation data."""
from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ============================================================================
# Export Schemas
# ============================================================================


class BBoxFormat(str, PyEnum):
    """Supported bounding box annotation formats."""
    COCO = "coco"      # [x, y, width, height] absolute pixels
    YOLO = "yolo"      # class x_center y_center width height (normalized 0-1)
    VOC = "voc"        # <bndbox><xmin><ymin><xmax><ymax> absolute pixels (XML)
    CSV = "csv"        # Flat CSV format


class ExportOptions(BaseModel):
    """Options for what to include in the export."""
    include_fov_images: bool = Field(
        default=True,
        description="Include MIP/SUM projection images"
    )
    include_crop_images: bool = Field(
        default=True,
        description="Include cell crop images"
    )
    include_embeddings: bool = Field(
        default=True,
        description="Include DINOv3 embeddings as NPY files"
    )
    include_masks: bool = Field(
        default=True,
        description="Include segmentation masks as PNG files"
    )
    bbox_format: BBoxFormat = Field(
        default=BBoxFormat.COCO,
        description="Bounding box annotation format"
    )


class ExportPrepareRequest(BaseModel):
    """Request to prepare an export job."""
    experiment_ids: List[int] = Field(
        ...,
        min_length=1,
        description="List of experiment IDs to export"
    )
    options: ExportOptions = Field(default_factory=ExportOptions)


class ExportPrepareResponse(BaseModel):
    """Response after preparing export job."""
    job_id: str = Field(description="Unique job identifier for streaming download")
    estimated_size_bytes: int = Field(description="Estimated ZIP file size in bytes")
    experiment_count: int = Field(description="Number of experiments to export")
    image_count: int = Field(description="Total number of FOV images")
    crop_count: int = Field(description="Total number of cell crops")
    mask_count: int = Field(description="Number of segmentation masks")


class ExportStatusResponse(BaseModel):
    """Response for export job status."""
    job_id: str
    status: Literal["preparing", "streaming", "completed", "error"]
    progress_percent: float = Field(ge=0, le=100, default=0)
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


# ============================================================================
# Import Schemas
# ============================================================================


class ImportFormat(str, PyEnum):
    """Detected/supported import formats."""
    MAPTIMIZE = "maptimize"  # Native format with manifest.json
    COCO = "coco"            # images/ + annotations.json
    YOLO = "yolo"            # images/ + labels/*.txt + classes.txt
    VOC = "voc"              # JPEGImages/ + Annotations/*.xml
    CSV = "csv"              # images/ + annotations.csv


class ImportValidationResult(BaseModel):
    """Result of validating an import file."""
    job_id: str = Field(description="Job ID for executing the import")
    detected_format: ImportFormat
    is_valid: bool
    image_count: int = Field(description="Number of images found")
    annotation_count: int = Field(description="Number of annotations found")
    has_embeddings: bool = Field(default=False, description="Whether embeddings were found")
    has_masks: bool = Field(default=False, description="Whether masks were found")
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ImportExecuteRequest(BaseModel):
    """Request to execute an import."""
    job_id: str = Field(description="Job ID from validation step")
    experiment_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Name for the new experiment"
    )
    import_as_format: ImportFormat = Field(
        description="Confirmed import format"
    )
    create_crops_from_bboxes: bool = Field(
        default=True,
        description="Create CellCrop entries from bounding box annotations"
    )


class ImportStatusResponse(BaseModel):
    """Response for import job status."""
    job_id: str
    status: Literal["validating", "importing", "processing", "completed", "error"]
    progress_percent: float = Field(ge=0, le=100, default=0)
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    experiment_id: Optional[int] = Field(
        None,
        description="ID of created experiment (when completed)"
    )
    images_imported: int = 0
    crops_created: int = 0
    created_at: datetime
    completed_at: Optional[datetime] = None


# ============================================================================
# Internal Data Classes
# ============================================================================


class CropImportData(BaseModel):
    """Internal data structure for importing a crop annotation."""
    image_filename: str
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    class_name: Optional[str] = None
    confidence: Optional[float] = None


class ExportJobData(BaseModel):
    """Internal data structure for tracking export job."""
    job_id: str
    user_id: int
    experiment_ids: List[int]
    options: ExportOptions
    status: str = "preparing"
    progress_percent: float = 0
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    # Stats for UI
    experiment_count: int = 0
    image_count: int = 0
    crop_count: int = 0
    mask_count: int = 0
    estimated_size_bytes: int = 0


class ImportJobData(BaseModel):
    """Internal data structure for tracking import job."""
    job_id: str
    user_id: int
    file_path: str
    detected_format: Optional[ImportFormat] = None
    validation_result: Optional[ImportValidationResult] = None
    experiment_name: Optional[str] = None
    experiment_id: Optional[int] = None
    status: str = "validating"
    progress_percent: float = 0
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    images_imported: int = 0
    crops_created: int = 0
    created_at: datetime
    completed_at: Optional[datetime] = None
