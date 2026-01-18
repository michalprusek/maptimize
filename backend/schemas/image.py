"""Image schemas."""
from datetime import datetime
from typing import Optional, List, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from models.image import UploadStatus


class MapProteinCreate(BaseModel):
    """Schema for creating a MAP protein."""
    name: str = Field(..., min_length=1, max_length=100)
    full_name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    # Extended fields for protein page
    uniprot_id: Optional[str] = Field(None, max_length=20)
    fasta_sequence: Optional[str] = None
    gene_name: Optional[str] = Field(None, max_length=100)
    organism: Optional[str] = Field(None, max_length=100)


class MapProteinUpdate(BaseModel):
    """Schema for updating a MAP protein."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    full_name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    uniprot_id: Optional[str] = Field(None, max_length=20)
    fasta_sequence: Optional[str] = None
    gene_name: Optional[str] = Field(None, max_length=100)
    organism: Optional[str] = Field(None, max_length=100)


class MapProteinResponse(BaseModel):
    """Schema for MAP protein response (basic)."""
    id: int
    name: str
    full_name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None

    class Config:
        from_attributes = True


class MapProteinDetailedResponse(BaseModel):
    """Schema for detailed MAP protein response with all fields."""
    id: int
    name: str
    full_name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    # Extended fields
    uniprot_id: Optional[str] = None
    fasta_sequence: Optional[str] = None
    gene_name: Optional[str] = None
    organism: Optional[str] = None
    sequence_length: Optional[int] = None
    # Embedding info
    has_embedding: bool = False
    embedding_model: Optional[str] = None
    embedding_computed_at: Optional[datetime] = None
    # Stats
    image_count: int = 0
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_protein(cls, protein, image_count: int = 0) -> "MapProteinDetailedResponse":
        """Create response from MapProtein model with image count."""
        return cls(
            id=protein.id,
            name=protein.name,
            full_name=protein.full_name,
            description=protein.description,
            color=protein.color,
            uniprot_id=protein.uniprot_id,
            fasta_sequence=protein.fasta_sequence,
            gene_name=protein.gene_name,
            organism=protein.organism,
            sequence_length=protein.sequence_length,
            has_embedding=protein.embedding is not None,
            embedding_model=protein.embedding_model,
            embedding_computed_at=protein.embedding_computed_at,
            image_count=image_count,
            created_at=protein.created_at,
        )


class UmapProteinPointResponse(BaseModel):
    """UMAP point for protein visualization."""
    protein_id: int
    name: str
    x: float
    y: float
    color: str
    sequence_length: Optional[int] = None
    image_count: int = 0


class UmapProteinDataResponse(BaseModel):
    """Response for protein UMAP visualization."""
    points: List[UmapProteinPointResponse]
    total_proteins: int
    silhouette_score: Optional[float] = None
    is_precomputed: bool = False
    computed_at: Optional[str] = None


class CellCropSummary(BaseModel):
    """Brief cell crop info."""
    id: int
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    bundleness_score: Optional[float] = None
    sum_crop_path: Optional[str] = None  # SUM projection crop path
    excluded: bool = False

    class Config:
        from_attributes = True


class CellCropGalleryResponse(BaseModel):
    """Cell crop for gallery display with parent image info."""
    id: int
    image_id: int
    parent_filename: str
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    bundleness_score: Optional[float] = None
    detection_confidence: Optional[float] = None
    excluded: bool = False
    created_at: datetime
    map_protein_name: Optional[str] = None
    map_protein_color: Optional[str] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_crop(cls, crop) -> "CellCropGalleryResponse":
        """
        Create response from CellCrop model instance.

        DRY: Single source of truth for CellCrop -> GalleryResponse conversion.
        Requires CellCrop.image and CellCrop.map_protein to be loaded.
        """
        return cls(
            id=crop.id,
            image_id=crop.image_id,
            parent_filename=crop.image.original_filename,
            bbox_x=crop.bbox_x,
            bbox_y=crop.bbox_y,
            bbox_w=crop.bbox_w,
            bbox_h=crop.bbox_h,
            bundleness_score=crop.bundleness_score,
            detection_confidence=crop.detection_confidence,
            excluded=crop.excluded,
            created_at=crop.created_at,
            map_protein_name=crop.map_protein.name if crop.map_protein else None,
            map_protein_color=crop.map_protein.color if crop.map_protein else None,
        )


class ImageResponse(BaseModel):
    """Schema for image response."""
    id: int
    experiment_id: int
    original_filename: str
    status: UploadStatus
    width: Optional[int] = Field(None, gt=0)
    height: Optional[int] = Field(None, gt=0)
    z_slices: Optional[int] = Field(None, gt=0)
    file_size: Optional[int] = Field(None, gt=0)
    error_message: Optional[str] = None
    detect_cells: bool = True  # Whether detection was requested
    source_discarded: bool = False  # Original file was deleted
    created_at: datetime
    processed_at: Optional[datetime] = None
    map_protein: Optional[MapProteinResponse] = None
    cell_count: int = Field(default=0, ge=0)

    class Config:
        from_attributes = True


class ImageDetailResponse(ImageResponse):
    """Schema for detailed image response with cells."""
    cell_crops: List[CellCropSummary] = []
    mip_url: Optional[str] = None
    sum_url: Optional[str] = None  # SUM projection URL
    thumbnail_url: Optional[str] = None


class BatchProcessRequest(BaseModel):
    """Request schema for batch processing images."""
    image_ids: List[int] = Field(..., min_length=1, max_length=1000, description="List of image IDs to process")
    detect_cells: bool = Field(True, description="Whether to run YOLO detection")
    # Note: Protein assignment is now managed at experiment level, not during batch processing

    @field_validator("image_ids")
    @classmethod
    def unique_image_ids(cls, v: List[int]) -> List[int]:
        """Ensure image IDs are unique to prevent duplicate processing."""
        unique_ids = list(dict.fromkeys(v))  # Preserves order while removing duplicates
        if len(unique_ids) != len(v):
            # Return unique IDs instead of raising error for better UX
            return unique_ids
        return v


class BatchProcessResponse(BaseModel):
    """Response schema for batch processing."""
    processing_count: int = Field(..., ge=0, description="Number of images queued for processing")
    message: str = Field(..., min_length=1, description="Status message")


class FOVResponse(BaseModel):
    """FOV (Field of View) response - represents an uploaded image for FOV gallery."""
    id: int
    experiment_id: int
    original_filename: str
    status: UploadStatus
    width: Optional[int] = Field(None, gt=0)
    height: Optional[int] = Field(None, gt=0)
    z_slices: Optional[int] = Field(None, gt=0)
    file_size: Optional[int] = Field(None, gt=0)
    detect_cells: bool = False
    thumbnail_url: Optional[str] = None
    cell_count: int = Field(default=0, ge=0)
    map_protein: Optional[MapProteinResponse] = None
    created_at: datetime
    processed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# =============================================================================
# Crop Editor Schemas
# =============================================================================


class CropBboxUpdateRequest(BaseModel):
    """Schema for updating crop bounding box."""
    bbox_x: int = Field(..., ge=0)
    bbox_y: int = Field(..., ge=0)
    bbox_w: int = Field(..., gt=0, le=2048)
    bbox_h: int = Field(..., gt=0, le=2048)

    @field_validator("bbox_w", "bbox_h")
    @classmethod
    def validate_min_size(cls, v: int) -> int:
        """Ensure minimum viable crop size."""
        if v < 10:
            raise ValueError("Crop dimension must be at least 10 pixels")
        return v


class CropBboxUpdateResponse(BaseModel):
    """Response after bbox update."""
    id: int
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    needs_regeneration: bool = True

    class Config:
        from_attributes = True


class ManualCropCreateRequest(BaseModel):
    """Schema for creating a manual crop."""
    bbox_x: int = Field(..., ge=0)
    bbox_y: int = Field(..., ge=0)
    bbox_w: int = Field(..., gt=0, le=2048)
    bbox_h: int = Field(..., gt=0, le=2048)
    map_protein_id: Optional[int] = None

    @field_validator("bbox_w", "bbox_h")
    @classmethod
    def validate_min_size(cls, v: int) -> int:
        """Ensure minimum viable crop size."""
        if v < 10:
            raise ValueError("Crop dimension must be at least 10 pixels")
        return v


class ManualCropCreateResponse(BaseModel):
    """Response after creating a manual crop."""
    id: int
    image_id: int
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    detection_confidence: Optional[float] = None
    needs_processing: bool = True

    class Config:
        from_attributes = True


class CropRegenerateRequest(BaseModel):
    """Schema for crop regeneration options."""
    async_processing: bool = Field(
        default=False,
        description="If True, return immediately and process in background"
    )


class CropRegenerateResponse(BaseModel):
    """Response after crop regeneration."""
    id: int
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    mip_path: Optional[str] = None
    sum_crop_path: Optional[str] = None
    mean_intensity: Optional[float] = None
    embedding_model: Optional[str] = None
    has_embedding: bool = False
    processing_status: str  # "completed", "partial", "failed"
    warnings: Optional[List[str]] = None  # Warnings for partial success

    class Config:
        from_attributes = True


class CropBatchUpdateItem(BaseModel):
    """Single crop update in batch."""
    id: Optional[int] = None  # None for new crops
    action: Literal["create", "update", "delete"]
    bbox_x: Optional[int] = Field(None, ge=0)
    bbox_y: Optional[int] = Field(None, ge=0)
    bbox_w: Optional[int] = Field(None, gt=0, le=2048)
    bbox_h: Optional[int] = Field(None, gt=0, le=2048)
    map_protein_id: Optional[int] = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> "CropBatchUpdateItem":
        """Validate that required fields are present based on action type."""
        if self.action == "create":
            if self.id is not None:
                raise ValueError("Create action should not have id")
            if any(v is None for v in [self.bbox_x, self.bbox_y, self.bbox_w, self.bbox_h]):
                raise ValueError("Create action requires all bbox fields (bbox_x, bbox_y, bbox_w, bbox_h)")
        elif self.action in ("update", "delete"):
            if self.id is None:
                raise ValueError(f"{self.action.capitalize()} action requires id")
        return self


class CropBatchUpdateRequest(BaseModel):
    """Schema for batch crop updates."""
    changes: List[CropBatchUpdateItem] = Field(..., max_length=500)
    regenerate_features: bool = Field(
        default=True,
        description="Trigger feature regeneration for modified crops"
    )
    confirm_delete_comparisons: bool = Field(
        default=False,
        description="Required if any deleted crops have ranking comparisons"
    )


class CropBatchUpdateResponse(BaseModel):
    """Response after batch update."""
    created: List[int] = []
    updated: List[int] = []
    deleted: List[int] = []
    failed: List[dict] = []
    regeneration_queued: bool = False
