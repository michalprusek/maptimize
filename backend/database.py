"""Database connection and session management."""
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import get_settings

logger = logging.getLogger(__name__)
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
    # Import all models to ensure they are registered with SQLAlchemy Base
    # This is required for Base.metadata.create_all to create all tables
    import models  # noqa: F401 - imports all model classes

    async with engine.begin() as conn:
        # Enable pgvector extension for embedding storage
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    # Ensure schema is up-to-date (add missing columns, enum values, etc.)
    await ensure_schema_updates()

    # Seed default user and data
    await seed_default_data()


async def backfill_document_hashes(conn) -> int:
    """Hash existing documents so they participate in deduplication.

    Returns the number of rows that could NOT be hashed.

    Best-effort by design: a document row can outlive its file on disk, and a
    missing file must not stop the application from starting. But every failure
    is logged at error and counted. An earlier backfill in this file logged at
    debug under an INFO root logger AND forgot to append to `failed_updates`, so
    a genuine failure printed "Schema updates applied successfully" and nobody
    noticed -- hence both halves here, the error-level log and the count.
    """
    result = await conn.execute(text(
        "SELECT id, original_path FROM rag_documents WHERE content_hash IS NULL"
    ))
    rows = result.fetchall()
    if not rows:
        return 0

    failed = 0
    hashed = 0
    for doc_id, path in rows:
        try:
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except Exception as e:
            logger.error(f"Backfill: cannot hash document {doc_id} at {path}: {e}")
            failed += 1
            continue
        await conn.execute(
            text("UPDATE rag_documents SET content_hash = :h WHERE id = :i"),
            {"h": digest, "i": doc_id},
        )
        hashed += 1

    logger.info(f"content_hash backfill: hashed {hashed}, unreadable {failed}")
    return failed


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
    from sqlalchemy.exc import ProgrammingError, OperationalError

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
            # Rotation of the bounding box (degrees about its centre; NULL = axis-aligned)
            ("cell_crops", "bbox_angle", "FLOAT"),
            # Embedding status tracking for background tasks
            ("cell_crops", "embedding_status", "VARCHAR(20)"),
            ("cell_crops", "embedding_error", "VARCHAR(500)"),
            # Pre-computed UMAP coordinates for cell crops
            ("cell_crops", "umap_x", "FLOAT"),
            ("cell_crops", "umap_y", "FLOAT"),
            ("cell_crops", "umap_computed_at", "TIMESTAMP WITH TIME ZONE"),
            # Pre-computed UMAP coordinates for FOV images
            ("images", "umap_x", "FLOAT"),
            ("images", "umap_y", "FLOAT"),
            ("images", "umap_computed_at", "TIMESTAMP WITH TIME ZONE"),
            # User avatar support
            ("users", "avatar_url", "VARCHAR(500)"),
            # SAM embedding status for interactive segmentation
            ("images", "sam_embedding_status", "VARCHAR(20)"),
            # MAP protein assignment at experiment level
            ("experiments", "map_protein_id", "INTEGER REFERENCES map_proteins(id)"),
            # FASTA sequence storage for protein reference
            ("experiments", "fasta_sequence", "TEXT"),
            # Microscope assignment at experiment level
            ("experiments", "microscope_id", "INTEGER REFERENCES microscopes(id)"),
            # Microtubule post-translational modification at experiment level
            ("experiments", "ptm_id", "INTEGER REFERENCES ptms(id)"),
            # MAP protein extended fields for protein page
            ("map_proteins", "uniprot_id", "VARCHAR(20)"),
            ("map_proteins", "fasta_sequence", "TEXT"),
            ("map_proteins", "gene_name", "VARCHAR(100)"),
            ("map_proteins", "organism", "VARCHAR(100)"),
            ("map_proteins", "sequence_length", "INTEGER"),
            # ESM-C 600M embedding for proteins (1152-dim)
            ("map_proteins", "embedding", "vector(1152)"),
            ("map_proteins", "embedding_model", "VARCHAR(100)"),
            ("map_proteins", "embedding_computed_at", "TIMESTAMP WITH TIME ZONE"),
            # Pre-computed UMAP coordinates for proteins
            ("map_proteins", "umap_x", "FLOAT"),
            ("map_proteins", "umap_y", "FLOAT"),
            ("map_proteins", "umap_computed_at", "TIMESTAMP WITH TIME ZONE"),
            ("map_proteins", "created_at", "TIMESTAMP WITH TIME ZONE DEFAULT NOW()"),
            # RAG embedding for FOV images (2048-dim for Qwen VL)
            ("images", "rag_embedding", "vector(2048)"),
            ("images", "rag_indexed_at", "TIMESTAMP WITH TIME ZONE"),
            # File-explorer folder for library documents (NULL = root)
            ("rag_documents", "folder_id", "INTEGER"),
            # Group support for shared experiments and metrics
            ("experiments", "group_id", "INTEGER REFERENCES groups(id) ON DELETE SET NULL"),
            ("metrics", "group_id", "INTEGER REFERENCES groups(id) ON DELETE SET NULL"),
            ("metric_ratings", "user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE"),
            ("metric_comparisons", "user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE"),
            # Per-user image exclusion (soft-delete from user's view)
            ("metric_ratings", "excluded", "BOOLEAN DEFAULT FALSE"),
            # Previous rating values for exact undo (mirrors comparisons.prev_*)
            ("metric_comparisons", "prev_winner_mu", "FLOAT"),
            ("metric_comparisons", "prev_winner_sigma", "FLOAT"),
            ("metric_comparisons", "prev_loser_mu", "FLOAT"),
            ("metric_comparisons", "prev_loser_sigma", "FLOAT"),
            # Attachment scoping column (NULL = library). Formerly referenced the
            # chat_threads table, which was removed with the chat agent; kept as a
            # plain column so existing rows and thread-scoped queries still work.
            ("rag_documents", "thread_id", "INTEGER"),
            # True page count when an attachment was capped (NULL = not truncated)
            ("rag_documents", "truncated_from_pages", "INTEGER"),
            # Group support for shared library documents (thread_id IS NULL only)
            ("rag_documents", "group_id", "INTEGER REFERENCES groups(id) ON DELETE SET NULL"),
            # Provenance for papers imported from Europe PMC
            ("rag_documents", "doi", "VARCHAR(255)"),
            ("rag_documents", "source_url", "VARCHAR(1000)"),
            # sha256 of the file content: the deduplication key
            ("rag_documents", "content_hash", "VARCHAR(64)"),
            # Folder visibility ('group' | 'private') and seeded-folder kind
            # ('root' | 'common' | 'user' | 'custom'). Existing folders were all
            # group-shared and user-made, which is exactly what the defaults say.
            ("document_folders", "visibility", "VARCHAR(20) DEFAULT 'group' NOT NULL"),
            ("document_folders", "kind", "VARCHAR(20) DEFAULT 'custom' NOT NULL"),
        ]

        for table, column, col_type in updates:
            try:
                # Use savepoint so a failure doesn't abort the whole transaction
                await conn.execute(text("SAVEPOINT col_update"))
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"
                ))
                await conn.execute(text("RELEASE SAVEPOINT col_update"))
                logger.debug(f"Ensured column exists: {table}.{column}")
            except Exception as e:
                await conn.execute(text("ROLLBACK TO SAVEPOINT col_update"))
                error_msg = str(e).lower()
                if "already exists" in error_msg:
                    logger.debug(f"Column {table}.{column} already exists")
                else:
                    logger.error(f"Failed to add column {table}.{column}: {e}")
                    failed_updates.append(f"{table}.{column}")

        # Migrate unique constraint on metric_ratings (old: metric_id+metric_image_id, new: +user_id)
        try:
            await conn.execute(text("SAVEPOINT constraint_update"))
            await conn.execute(text(
                "ALTER TABLE metric_ratings DROP CONSTRAINT IF EXISTS uq_metric_image_rating"
            ))
            await conn.execute(text("RELEASE SAVEPOINT constraint_update"))
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT constraint_update"))
            logger.debug(f"Could not drop uq_metric_image_rating: {e}")

        try:
            await conn.execute(text("SAVEPOINT constraint_create"))
            await conn.execute(text(
                "ALTER TABLE metric_ratings ADD CONSTRAINT uq_metric_image_user_rating "
                "UNIQUE (metric_id, metric_image_id, user_id)"
            ))
            await conn.execute(text("RELEASE SAVEPOINT constraint_create"))
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT constraint_create"))
            error_msg = str(e).lower()
            if "already exists" in error_msg:
                logger.debug("Constraint uq_metric_image_user_rating already exists")
            else:
                logger.error(f"Failed to create uq_metric_image_user_rating: {e}")
                failed_updates.append("metric_ratings.uq_constraint")

        # Multi-group membership: a user may belong to several groups, but only
        # once to each. create_all never alters an existing table, so without this
        # swap the old one-group-per-user UNIQUE would survive in production
        # forever -- and the second membership would fail with a constraint error
        # that looks nothing like its cause.
        for constraint_sql, label in (
            ("ALTER TABLE group_members DROP CONSTRAINT IF EXISTS uq_user_one_group",
             "drop uq_user_one_group"),
            ("ALTER TABLE group_members ADD CONSTRAINT uq_group_member "
             "UNIQUE (group_id, user_id)",
             "add uq_group_member"),
        ):
            try:
                await conn.execute(text("SAVEPOINT group_member_constraint"))
                await conn.execute(text(constraint_sql))
                await conn.execute(text("RELEASE SAVEPOINT group_member_constraint"))
                logger.debug(f"Applied: {label}")
            except Exception as e:
                await conn.execute(text("ROLLBACK TO SAVEPOINT group_member_constraint"))
                if "already exists" in str(e).lower():
                    logger.debug(f"{label}: already applied")
                else:
                    logger.error(f"Failed to {label}: {e}")
                    failed_updates.append(f"group_members.{label}")

        # Backfill user_id for existing metric_ratings and metric_comparisons
        # Old rows have user_id=NULL — assign to the metric owner
        try:
            await conn.execute(text("SAVEPOINT backfill_user_id"))
            await conn.execute(text("""
                UPDATE metric_ratings SET user_id = m.user_id
                FROM metrics m WHERE metric_ratings.metric_id = m.id AND metric_ratings.user_id IS NULL
            """))
            await conn.execute(text("""
                UPDATE metric_comparisons SET user_id = m.user_id
                FROM metrics m WHERE metric_comparisons.metric_id = m.id AND metric_comparisons.user_id IS NULL
            """))
            await conn.execute(text("RELEASE SAVEPOINT backfill_user_id"))
            logger.info("Backfilled user_id on metric_ratings/metric_comparisons")
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT backfill_user_id"))
            logger.error(
                f"Backfill user_id on metric_ratings/metric_comparisons FAILED "
                f"(pre-existing ratings/comparisons may be missing user_id, breaking per-user filtering): {e}"
            )
            failed_updates.append("metric_ratings/metric_comparisons.backfill_user_id")

        # Backfill group_id for existing LIBRARY documents (thread_id IS NULL).
        # Stamp each with its owner's group so lab members see docs uploaded
        # before this feature existed. Attachments (thread_id set) stay private.
        # NOTE: a startup backfill used to stamp every library document that had
        # no group with its owner's group. It ran when groups were introduced and
        # `group_id IS NULL` meant "not yet backfilled".
        #
        # ⚠️ It is deliberately GONE, and must not come back. Once folders could
        # hold documents (2026-07-31), NULL became the ACL state meaning PRIVATE
        # -- it is what placement_group_id returns for a private folder and for an
        # unfiled document. The backfill therefore republished every private
        # folder's contents to the owner's group on EVERY restart, and because
        # membership is many-to-many, `UPDATE ... FROM group_members` picked an
        # arbitrary one: 45 documents in a private UTIA ZOI folder were stamped
        # with Dr. Janke Lab and read by twelve people.
        #
        # A document's audience comes from the folder it sits in
        # (utils/folder_placement.py). A bulk UPDATE cannot see the folder, so it
        # cannot honour that rule -- there is no correct version of this job.

        # Index for group_id lookups (create_all skips columns added via ALTER TABLE above)
        try:
            await conn.execute(text("SAVEPOINT rag_documents_group_id_index"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_rag_documents_group_id ON rag_documents (group_id)"
            ))
            await conn.execute(text("RELEASE SAVEPOINT rag_documents_group_id_index"))
            logger.debug("Ensured index exists: ix_rag_documents_group_id")
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT rag_documents_group_id_index"))
            logger.error(f"Failed to create ix_rag_documents_group_id: {e}")
            failed_updates.append("rag_documents.ix_group_id")

        # Index for doi lookups (create_all skips columns added via ALTER TABLE above)
        try:
            await conn.execute(text("SAVEPOINT idx_doc_doi"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_rag_documents_doi ON rag_documents (doi)"
            ))
            await conn.execute(text("RELEASE SAVEPOINT idx_doc_doi"))
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT idx_doc_doi"))
            logger.error(f"Failed to create ix_rag_documents_doi: {e}")
            failed_updates.append("ix_rag_documents_doi")

        # Index for content_hash lookups: read on EVERY upload, before the file
        # is written. The model declares index=True, but create_all only builds
        # indexes when it CREATES the table, so a column added by ALTER TABLE
        # above needs its index stated explicitly here.
        try:
            await conn.execute(text("SAVEPOINT idx_doc_hash"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_rag_documents_content_hash "
                "ON rag_documents (content_hash)"
            ))
            await conn.execute(text("RELEASE SAVEPOINT idx_doc_hash"))
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT idx_doc_hash"))
            logger.error(f"Failed to create ix_rag_documents_content_hash: {e}")
            failed_updates.append("ix_rag_documents_content_hash")

        # Hash pre-existing documents so they participate in deduplication.
        try:
            await conn.execute(text("SAVEPOINT backfill_doc_hash"))
            unhashed = await backfill_document_hashes(conn)
            await conn.execute(text("RELEASE SAVEPOINT backfill_doc_hash"))
            if unhashed:
                # Not fatal -- those rows simply never dedupe -- but it must be
                # visible, not inferred from documents mysteriously importing twice.
                failed_updates.append(f"content_hash backfill ({unhashed} unreadable)")
        except Exception as e:
            await conn.execute(text("ROLLBACK TO SAVEPOINT backfill_doc_hash"))
            logger.error(f"content_hash backfill failed: {e}")
            failed_updates.append("rag_documents.backfill_content_hash")

        # Ensure enum values exist (must be outside transaction for PostgreSQL)
        # We run this in a separate autocommit connection
    try:
        async with engine.connect() as raw_conn:
            await raw_conn.execution_options(isolation_level="AUTOCOMMIT")
            enum_updates = [
                ("uploadstatus", "UPLOADED", "UPLOADING"),
            ]
            for enum_name, new_value, after_value in enum_updates:
                try:
                    await raw_conn.execute(text(
                        f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{new_value}' AFTER '{after_value}'"
                    ))
                    logger.debug(f"Ensured enum value: {enum_name}.{new_value}")
                except Exception as e:
                    logger.debug(f"Enum update {enum_name}.{new_value}: {e}")
    except Exception as e:
        logger.warning(f"Enum updates failed: {e}")

    # Note: RAG embeddings use 2048 dimensions (Qwen3 VL) which exceeds
    # pgvector's 2000-dimension limit for HNSW/ivfflat indexes.
    logger.debug("Skipping RAG vector index creation (2048 dims > pgvector 2000 limit)")

    if failed_updates:
        logger.error(f"Schema updates FAILED for: {', '.join(failed_updates)}")
    else:
        logger.info("Schema updates applied successfully")


async def seed_default_data():
    """Create default user, MAP proteins, and experiment if they don't exist."""
    from sqlalchemy import select
    from models.user import User, UserRole
    from models.image import DEFAULT_PROTEINS, MapProtein
    from models.experiment import Experiment
    from models.ptm import DEFAULT_PTMS, PTM
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
            for p_data in DEFAULT_PROTEINS:
                db.add(MapProtein(**p_data))
            print("Created default MAP proteins")

        # Seed the tubulin code once. Guarded on the table being empty, not on
        # each name: re-adding a row the lab deliberately deleted would be worse
        # than leaving the vocabulary short.
        result = await db.execute(select(PTM).limit(1))
        if not result.scalar_one_or_none():
            for ptm_data in DEFAULT_PTMS:
                db.add(PTM(**ptm_data))
            print("Created default PTMs")

        await db.commit()
