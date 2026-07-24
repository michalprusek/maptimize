"""Application configuration."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App
    app_name: str = "MAPtimize"
    debug: bool = True

    # Database
    database_url: str = "postgresql://maptimize:password@localhost:5432/maptimize"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # JWT
    jwt_secret: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 hours

    # Storage
    upload_dir: Path = Path("data/uploads")
    max_upload_size: int = 500 * 1024 * 1024  # 500MB

    # ML Models
    yolo_model_path: Path = Path("weights/best.pt")  # Relative for local dev, override in Docker

    # GPU Model Lifecycle
    gpu_model_idle_timeout_seconds: int = 300  # 5 minutes - unload idle models
    gpu_model_cleanup_interval_seconds: int = 60  # Check for idle models every minute
    ml_memory_limit_gb: float = 16.0  # Max GPU memory budget for ML models

    # TrueSkill parameters
    initial_mu: float = 25.0
    initial_sigma: float = 25.0 / 3
    target_sigma: float = 2.0
    exploration_pairs: int = 50

    # RAG / Chat Configuration
    gemini_api_key: str = ""  # Set via GEMINI_API_KEY env var
    # Unpaywall requires a contact address as a query parameter. Free API
    # indexing only legally deposited open-access copies -- never a paywall
    # bypass. Used solely as a fallback when Europe PMC's own PDF link fails.
    unpaywall_email: str = "maptimize@utia.cas.cz"

    # Single source of truth for Gemini model IDs. Must never name a model
    # Google has retired: gemini-2.0-flash was shut down on 2026-06-01, which
    # silently broke every caller still pinned to it.
    # - gemini_model: paper-discovery free-text query rewrite
    #   (services/paper_discovery_service.py:rewrite_topic_query).
    # - gemini_vision_model: document region extraction
    #   (services/rag_service.py).
    gemini_model: str = "gemini-3.6-flash"
    gemini_vision_model: str = "gemini-3.6-flash"

    rag_document_dir: Path = Path("data/rag_documents")
    rag_max_document_results: int = 20
    # Chat attachments (documents uploaded into a thread) are capped so a very
    # large PDF cannot flood the conversation's context.
    chat_attachment_max_pages: int = 100
    rag_max_fov_results: int = 20
    # Pages are re-encoded to WebP: a scanned journal page is photographic
    # content, the worst case for PNG's lossless compression.
    rag_page_format: Literal["WEBP", "PNG", "JPEG"] = "WEBP"
    rag_page_quality: int = Field(default=85, ge=1, le=100)
    # On-demand "zoom": a region crop is re-rendered from the source PDF at this
    # DPI, NOT cropped from the 150-DPI page raster. A small figure/table then
    # fills the vision model's pixel budget legibly. Full pages stay at 150 DPI
    # because the model downsamples any image to ~1568px anyway -- only a crop
    # benefits from more pixels. Longest edge is capped so the crop doesn't waste
    # tokens once it already exceeds that budget.
    rag_region_dpi: int = Field(default=300, ge=72, le=600)
    rag_region_max_edge: int = Field(default=1600, ge=512, le=4096)

    # Agent-generated images (plots, overlays) live here. Deliberately NOT
    # under uploads/temp, which a startup job reaps at 24h -- that reaper is
    # why images vanished from older conversations.
    #
    # Also deliberately OUTSIDE upload_dir: /uploads is an unauthenticated
    # StaticFiles mount, so anything under it is world-readable. These are
    # served through /api/chat-images/{user_id}/... instead, which checks the
    # caller's token. Per-user subdirectories make ownership path-derivable.
    chat_image_dir: Path = Path("data/chat_images")

    # Same reasoning. Exports previously sat at /uploads/exports/ with
    # second-resolution timestamps and no random component, so a URL like
    # experiment_PRC1_20260719_143000.xlsx was trivially enumerable by any
    # unauthenticated visitor.
    export_dir: Path = Path("data/exports")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore"  # Ignore extra env vars not defined in Settings
    }


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
