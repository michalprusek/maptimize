"""Database connection and session management."""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import get_settings

settings = get_settings()

# Convert postgresql:// to postgresql+asyncpg://
database_url = settings.database_url.replace(
    "postgresql://", "postgresql+asyncpg://"
)

engine = create_async_engine(
    database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session as FastAPI dependency.

    Automatically commits on success and rolls back on exception.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Alias for context manager usage outside FastAPI dependencies
# (e.g., in background tasks, scripts)
get_db_context = asynccontextmanager(get_db)


async def init_db():
    """Initialize database tables, enable extensions, and seed default data."""
    async with engine.begin() as conn:
        # Enable pgvector extension for embedding storage
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    # Ensure schema is up-to-date (add missing columns, enum values, etc.)
    await ensure_schema_updates()

    # Seed default user and data
    await seed_default_data()


async def ensure_schema_updates():
    """
    Apply incremental schema updates that SQLAlchemy create_all doesn't handle.

    This function ensures the database schema matches the model definitions by:
    - Adding missing columns to existing tables
    - Adding missing enum values

    This is a lightweight alternative to full migration tools like Alembic,
    suitable for development and small deployments.

    Note: All table/column names are hardcoded constants - do not parameterize
    with external input to avoid SQL injection risks.
    """
    import logging
    from sqlalchemy.exc import ProgrammingError, OperationalError

    logger = logging.getLogger(__name__)
    failed_updates = []

    async with engine.begin() as conn:
        # Schema updates for images and cell_crops tables
        updates = [
            # Embedding columns for FOV feature extraction
            ("images", "embedding", "vector(1024)"),
            ("images", "embedding_model", "VARCHAR(100)"),
            # Embedding columns for cell crops
            ("cell_crops", "embedding", "vector(1024)"),
            ("cell_crops", "embedding_model", "VARCHAR(100)"),
            ("cell_crops", "map_protein_id", "INTEGER REFERENCES map_proteins(id)"),
        ]

        for table, column, col_type in updates:
            try:
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"
                ))
                logger.debug(f"Ensured column exists: {table}.{column}")
            except ProgrammingError as e:
                error_msg = str(e).lower()
                if "already exists" in error_msg:
                    logger.debug(f"Column {table}.{column} already exists")
                else:
                    logger.error(f"Failed to add column {table}.{column}: {e}")
                    failed_updates.append(f"{table}.{column}")
            except OperationalError as e:
                logger.error(f"Database error adding {table}.{column}: {e}")
                failed_updates.append(f"{table}.{column}")

        # Ensure enum values exist
        enum_updates = [
            ("uploadstatus", "UPLOADED", "UPLOADING"),
        ]

        for enum_name, new_value, after_value in enum_updates:
            try:
                await conn.execute(text(
                    f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{new_value}' AFTER '{after_value}'"
                ))
                logger.debug(f"Ensured enum value exists: {enum_name}.{new_value}")
            except ProgrammingError as e:
                # PostgreSQL cannot add enum values within multi-statement transactions
                # This is expected and acceptable - the value likely already exists
                logger.debug(f"Enum update {enum_name}.{new_value}: {e}")
            except OperationalError as e:
                logger.warning(f"Failed to add enum value {enum_name}.{new_value}: {e}")

        if failed_updates:
            logger.error(f"Schema updates FAILED for: {', '.join(failed_updates)}")
        else:
            logger.info("Schema updates applied successfully")


async def seed_default_data():
    """Create default user, MAP proteins, and experiment if they don't exist."""
    from sqlalchemy import select
    from models.user import User, UserRole
    from models.image import MapProtein
    from models.experiment import Experiment
    from utils.security import hash_password

    async with async_session_maker() as db:
        # Check if default user exists
        result = await db.execute(
            select(User).where(User.email == "12bprusek@gym-nymburk.cz")
        )
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                email="12bprusek@gym-nymburk.cz",
                name="Michal Prusek",
                password_hash=hash_password("82c17878"),
                role=UserRole.ADMIN
            )
            db.add(user)
            await db.flush()  # Get user ID
            print("Created default user: 12bprusek@gym-nymburk.cz")

        # Check if MAP proteins exist
        result = await db.execute(select(MapProtein).limit(1))
        if not result.scalar_one_or_none():
            proteins = [
                MapProtein(name="PRC1", full_name="Protein Regulator of Cytokinesis 1", color="#e91e8c"),
                MapProtein(name="Tau4R", full_name="Tau protein (4 repeat)", color="#00d4aa"),
                MapProtein(name="MAP2d", full_name="Microtubule-Associated Protein 2d", color="#ffc107"),
                MapProtein(name="MAP9", full_name="Microtubule-Associated Protein 9", color="#3b82f6"),
                MapProtein(name="EML3", full_name="Echinoderm Microtubule-Associated Protein-Like 3", color="#8b5cf6"),
                MapProtein(name="HMMR", full_name="Hyaluronan Mediated Motility Receptor", color="#ef4444"),
            ]
            for p in proteins:
                db.add(p)
            print("Created default MAP proteins")

        await db.commit()
