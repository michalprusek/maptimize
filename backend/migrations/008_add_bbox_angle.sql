-- Migration: Add bbox_angle column to cell_crops for rotated bounding boxes.
-- The crop is extracted de-rotated (the cell appears upright); NULL means the
-- box is axis-aligned, so existing rows need no backfill.
-- This file is DOCUMENTATION ONLY -- nothing executes backend/migrations/*.sql.
-- The authoritative path is database.ensure_schema_updates() at startup
-- (ADD COLUMN IF NOT EXISTS), which is where this column actually comes from.

ALTER TABLE cell_crops ADD COLUMN IF NOT EXISTS bbox_angle FLOAT;

COMMENT ON COLUMN cell_crops.bbox_angle IS
'Rotation of the bounding box in degrees about its centre. NULL/0 = axis-aligned. The crop image is extracted de-rotated (cell upright).';
