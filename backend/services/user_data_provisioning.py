"""Auto-provision template data for newly registered users.

Copies user 1's (template admin) experiments, images and cell crops to
new users so they start with the full dataset. Uses INSERT...SELECT so the
embedding vectors never round-trip through Python.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

TEMPLATE_USER_ID = 1

_ID_MAPPING_SQL = """
    SELECT old_t.id AS old_id, new_t.id AS new_id
    FROM (
        SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS rn
        FROM {table} WHERE {fk_column} = :old_parent_id
    ) old_t
    JOIN (
        SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS rn
        FROM {table} WHERE {fk_column} = :new_parent_id
    ) new_t ON old_t.rn = new_t.rn
"""


async def _build_id_mapping(
    db: AsyncSession,
    table: str,
    fk_column: str,
    old_parent_id: int,
    new_parent_id: int,
) -> dict[int, int]:
    """Build old-to-new ID mapping after an INSERT...SELECT copy.

    Uses ROW_NUMBER ordering to pair rows from the original parent
    with the corresponding rows under the new parent.
    """
    # Table/column names are internal constants, not user input -- safe to format.
    result = await db.execute(
        text(_ID_MAPPING_SQL.format(table=table, fk_column=fk_column)),
        {"old_parent_id": old_parent_id, "new_parent_id": new_parent_id},
    )
    return {row.old_id: row.new_id for row in result.fetchall()}


async def provision_new_user_data(new_user_id: int, db: AsyncSession) -> None:
    """Copy template user's data to a newly registered user.

    Copies experiments, images and cell crops. All operations use
    INSERT...SELECT to keep large data (embeddings) in PostgreSQL.

    Runs within the caller's transaction -- rolls back automatically
    on failure.
    """
    result = await db.execute(
        text("SELECT COUNT(*) FROM experiments WHERE user_id = :uid"),
        {"uid": TEMPLATE_USER_ID},
    )
    if result.scalar() == 0:
        logger.warning("Template user %d has no experiments, skipping provisioning", TEMPLATE_USER_ID)
        return

    logger.info("Provisioning data for new user %d from template user %d", new_user_id, TEMPLATE_USER_ID)

    result = await db.execute(
        text("SELECT id, name, description, map_protein_id, fasta_sequence, status FROM experiments WHERE user_id = :uid ORDER BY id"),
        {"uid": TEMPLATE_USER_ID},
    )
    template_experiments = result.fetchall()

    # Accumulated old->new crop ID mapping across all experiments (counted in the summary log)
    crop_id_map: dict[int, int] = {}

    for exp in template_experiments:
        old_exp_id = exp.id

        # 1. Create new experiment
        new_exp = await db.execute(
            text("""
                INSERT INTO experiments (name, description, user_id, map_protein_id, fasta_sequence, status, created_at, updated_at)
                VALUES (:name, :description, :user_id, :map_protein_id, :fasta_sequence, :status, NOW(), NOW())
                RETURNING id
            """),
            {
                "name": exp.name,
                "description": exp.description,
                "user_id": new_user_id,
                "map_protein_id": exp.map_protein_id,
                "fasta_sequence": exp.fasta_sequence,
                "status": exp.status,
            },
        )
        new_exp_id = new_exp.scalar()

        # 2. Copy images (INSERT...SELECT keeps embeddings in DB)
        await db.execute(
            text("""
                INSERT INTO images (
                    experiment_id, map_protein_id, original_filename, file_path,
                    mip_path, sum_path, thumbnail_path, detect_cells, source_discarded,
                    width, height, z_slices, file_size, image_metadata,
                    status, error_message,
                    rag_embedding, rag_indexed_at,
                    created_at, processed_at
                )
                SELECT
                    :new_exp_id, map_protein_id, original_filename, file_path,
                    mip_path, sum_path, thumbnail_path, detect_cells, source_discarded,
                    width, height, z_slices, file_size, image_metadata,
                    status, error_message,
                    rag_embedding, rag_indexed_at,
                    NOW(), processed_at
                FROM images
                WHERE experiment_id = :old_exp_id
                ORDER BY id
            """),
            {"new_exp_id": new_exp_id, "old_exp_id": old_exp_id},
        )

        # 3. Build image ID mapping
        image_map = await _build_id_mapping(db, "images", "experiment_id", old_exp_id, new_exp_id)

        if not image_map:
            continue

        # 4. Copy cell crops for each image pair
        for old_img_id, new_img_id in image_map.items():
            await db.execute(
                text("""
                    INSERT INTO cell_crops (
                        image_id, map_protein_id,
                        bbox_x, bbox_y, bbox_w, bbox_h, bbox_angle, detection_confidence,
                        mip_path, sum_crop_path, std_path,
                        bundleness_score, bundleness_model, mean_intensity, skewness, kurtosis,
                        embedding, embedding_model, embedding_status, embedding_error,
                        umap_x, umap_y, umap_computed_at,
                        excluded, created_at
                    )
                    SELECT
                        :new_img_id, map_protein_id,
                        bbox_x, bbox_y, bbox_w, bbox_h, bbox_angle, detection_confidence,
                        mip_path, sum_crop_path, std_path,
                        bundleness_score, bundleness_model, mean_intensity, skewness, kurtosis,
                        embedding, embedding_model, embedding_status, embedding_error,
                        umap_x, umap_y, umap_computed_at,
                        excluded, NOW()
                    FROM cell_crops
                    WHERE image_id = :old_img_id
                    ORDER BY id
                """),
                {"new_img_id": new_img_id, "old_img_id": old_img_id},
            )

            crop_map = await _build_id_mapping(db, "cell_crops", "image_id", old_img_id, new_img_id)
            crop_id_map.update(crop_map)

    # Metrics are shared via groups — not copied per user

    logger.info(
        "Provisioned user %d: %d experiments, %d crop mappings",
        new_user_id, len(template_experiments), len(crop_id_map),
    )
