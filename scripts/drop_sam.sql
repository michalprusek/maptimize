-- Drop everything SAM interactive segmentation left in the database.
--
-- SAM and the segmentation editor were removed on 2026-11-08. What they left:
--   sam_embeddings              1605 rows, 3.0 GB — 97 % of the whole database,
--                               and therefore of every nightly dump
--   fov_segmentation_masks      63 masks (all Michal's, Jan–Mar 2026); dropped
--                               by his decision, they survive in the backups
--   segmentation_masks          empty
--   user_segmentation_prompts   empty
--   images.sam_embedding_status
--
-- Run ONCE, by hand, in this order — never from application startup:
--   1. scripts/backup.sh              (the masks' only copy afterwards)
--   2. deploy the backend that no longer maps these tables; a backend still
--      running the old code queries them and would 500
--   3. docker exec -i maptimize-db psql -U maptimize -d maptimize < scripts/drop_sam.sql
--
-- Idempotent: a second run is a no-op.

BEGIN;

DROP TABLE IF EXISTS sam_embeddings;
DROP TABLE IF EXISTS segmentation_masks;
DROP TABLE IF EXISTS user_segmentation_prompts;
DROP TABLE IF EXISTS fov_segmentation_masks;
ALTER TABLE images DROP COLUMN IF EXISTS sam_embedding_status;

COMMIT;
