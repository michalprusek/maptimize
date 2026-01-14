-- Migration: Add SAM segmentation tables and columns
-- Date: 2026-01-14
-- Description: Adds support for SAM 3 interactive segmentation
--              - sam_embeddings: Pre-computed SAM encoder outputs for fast inference
--              - segmentation_masks: Polygon boundaries for segmented cells
--              - user_segmentation_prompts: User's click patterns (exemplars)
--              - sam_embedding_status column on images table

-- 1. Add sam_embedding_status column to images table
ALTER TABLE images
ADD COLUMN IF NOT EXISTS sam_embedding_status VARCHAR(20) DEFAULT NULL;

-- Comment on the new column
COMMENT ON COLUMN images.sam_embedding_status IS 'SAM embedding computation status: NULL, pending, computing, ready, error';

-- 2. Create sam_embeddings table for pre-computed encoder outputs
CREATE TABLE IF NOT EXISTS sam_embeddings (
    id SERIAL PRIMARY KEY,
    image_id INTEGER NOT NULL UNIQUE REFERENCES images(id) ON DELETE CASCADE,
    model_variant VARCHAR(50) NOT NULL,
    embedding_data BYTEA NOT NULL,
    embedding_shape VARCHAR(50) NOT NULL,
    original_width INTEGER NOT NULL,
    original_height INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookup by image_id
CREATE INDEX IF NOT EXISTS ix_sam_embeddings_image_id ON sam_embeddings(image_id);

-- Comments
COMMENT ON TABLE sam_embeddings IS 'Pre-computed SAM image encoder embeddings for fast interactive segmentation';
COMMENT ON COLUMN sam_embeddings.embedding_data IS 'Compressed embedding (zlib + float16), ~2-4MB per image';
COMMENT ON COLUMN sam_embeddings.embedding_shape IS 'Shape string for reconstruction, e.g., "256,64,256"';

-- 3. Create segmentation_masks table for finalized cell boundaries
CREATE TABLE IF NOT EXISTS segmentation_masks (
    id SERIAL PRIMARY KEY,
    cell_crop_id INTEGER NOT NULL UNIQUE REFERENCES cell_crops(id) ON DELETE CASCADE,
    polygon_points JSONB NOT NULL,
    area_pixels INTEGER NOT NULL,
    iou_score REAL NOT NULL,
    creation_method VARCHAR(20) DEFAULT 'interactive',
    prompt_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookup by cell_crop_id
CREATE INDEX IF NOT EXISTS ix_segmentation_masks_cell_crop_id ON segmentation_masks(cell_crop_id);

-- Comments
COMMENT ON TABLE segmentation_masks IS 'Segmentation polygon boundaries for cell crops';
COMMENT ON COLUMN segmentation_masks.polygon_points IS 'JSON array of [x, y] points defining polygon boundary';
COMMENT ON COLUMN segmentation_masks.iou_score IS 'SAM IoU prediction confidence score';
COMMENT ON COLUMN segmentation_masks.creation_method IS 'How mask was created: interactive, auto, imported';

-- 4. Create user_segmentation_prompts table for storing exemplar click patterns
CREATE TABLE IF NOT EXISTS user_segmentation_prompts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    experiment_id INTEGER REFERENCES experiments(id) ON DELETE CASCADE,
    source_image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    source_crop_id INTEGER REFERENCES cell_crops(id) ON DELETE SET NULL,
    click_points JSONB NOT NULL,
    result_polygon JSONB,
    quality_rating REAL,
    name VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS ix_user_segmentation_prompts_user_id ON user_segmentation_prompts(user_id);
CREATE INDEX IF NOT EXISTS ix_user_segmentation_prompts_experiment_id ON user_segmentation_prompts(experiment_id);

-- Comments
COMMENT ON TABLE user_segmentation_prompts IS 'User exemplar click patterns for SAM segmentation (per-user "training")';
COMMENT ON COLUMN user_segmentation_prompts.click_points IS 'JSON array: [{"x": 100, "y": 150, "label": 1}, ...], label 1=foreground, 0=background';
COMMENT ON COLUMN user_segmentation_prompts.experiment_id IS 'If NULL, applies globally; otherwise scoped to experiment';

-- 5. Create function to auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_segmentation_masks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for auto-updating updated_at
DROP TRIGGER IF EXISTS trigger_segmentation_masks_updated_at ON segmentation_masks;
CREATE TRIGGER trigger_segmentation_masks_updated_at
    BEFORE UPDATE ON segmentation_masks
    FOR EACH ROW
    EXECUTE FUNCTION update_segmentation_masks_updated_at();
