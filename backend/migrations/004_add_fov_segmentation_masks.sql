-- Migration: Add FOV segmentation masks table
-- Date: 2026-01-15
-- Description: Adds support for FOV-level segmentation masks.
--              Cell masks are then extracted as clips from the FOV mask based on bbox.

-- 1. Create fov_segmentation_masks table
CREATE TABLE IF NOT EXISTS fov_segmentation_masks (
    id SERIAL PRIMARY KEY,
    image_id INTEGER NOT NULL UNIQUE REFERENCES images(id) ON DELETE CASCADE,
    polygon_points JSONB NOT NULL,
    area_pixels INTEGER NOT NULL,
    iou_score REAL NOT NULL,
    creation_method VARCHAR(20) DEFAULT 'interactive',
    prompt_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookup by image_id
CREATE INDEX IF NOT EXISTS ix_fov_segmentation_masks_image_id ON fov_segmentation_masks(image_id);

-- Comments
COMMENT ON TABLE fov_segmentation_masks IS 'FOV-level segmentation polygon for entire field of view';
COMMENT ON COLUMN fov_segmentation_masks.polygon_points IS 'JSON array of [x, y] points defining polygon boundary in FOV coordinates';
COMMENT ON COLUMN fov_segmentation_masks.iou_score IS 'SAM IoU prediction confidence score';
COMMENT ON COLUMN fov_segmentation_masks.creation_method IS 'How mask was created: interactive, auto, imported';

-- 2. Create trigger for auto-updating updated_at
CREATE OR REPLACE FUNCTION update_fov_segmentation_masks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_fov_segmentation_masks_updated_at ON fov_segmentation_masks;
CREATE TRIGGER trigger_fov_segmentation_masks_updated_at
    BEFORE UPDATE ON fov_segmentation_masks
    FOR EACH ROW
    EXECUTE FUNCTION update_fov_segmentation_masks_updated_at();
