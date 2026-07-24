-- Migration: Add bbox_angle column to cell_crops for rotated bounding boxes.
-- The crop is extracted de-rotated (the cell appears upright); NULL means the
-- box is axis-aligned, so existing rows need no backfill.
-- Also applied at runtime by database.ensure_schema_updates() (ADD COLUMN IF NOT EXISTS).

ALTER TABLE cell_crops ADD COLUMN IF NOT EXISTS bbox_angle FLOAT;

COMMENT ON COLUMN cell_crops.bbox_angle IS
'Rotation of the bounding box in degrees about its centre. NULL/0 = axis-aligned. The crop image is extracted de-rotated (cell upright).';
