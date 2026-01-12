"""Application configuration."""
from functools import lru_cache
from pathlib import Path
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

    # TrueSkill parameters
    initial_mu: float = 25.0
    initial_sigma: float = 25.0 / 3
    target_sigma: float = 2.0
    exploration_pairs: int = 50

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
