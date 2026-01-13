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

    # Seed default user and data
    await seed_default_data()


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
