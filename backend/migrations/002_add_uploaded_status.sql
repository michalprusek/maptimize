-- Migration: Add UPLOADED status to uploadstatus enum
-- Date: 2026-01-14
-- Description: Adds intermediate UPLOADED status for two-phase upload workflow.
--              Phase 1 (upload): Creates projections and thumbnails, sets status to UPLOADED
--              Phase 2 (process): Runs detection and feature extraction when triggered

-- Add the 'UPLOADED' value to the uploadstatus enum after 'UPLOADING'
-- This represents images that have been uploaded and processed (projections, thumbnails)
-- but have not yet been processed for cell detection and feature extraction.
DO $$
BEGIN
    -- Check if the value already exists to make migration idempotent
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'UPLOADED'
        AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'uploadstatus')
    ) THEN
        ALTER TYPE uploadstatus ADD VALUE 'UPLOADED' AFTER 'UPLOADING';
    END IF;
END $$;

-- Note: Existing images will not be affected. New uploads will use the two-phase workflow:
-- 1. Upload: UPLOADING -> (projections/thumbnail created) -> UPLOADED
-- 2. Process: UPLOADED -> PROCESSING -> DETECTING -> EXTRACTING_FEATURES -> READY
