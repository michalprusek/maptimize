-- Migration: Add previous rating columns to comparisons table for undo support
-- Date: 2026-01-13
-- Description: Stores previous mu/sigma values before comparison update,
--              enabling proper rating restoration on undo.

ALTER TABLE comparisons
ADD COLUMN IF NOT EXISTS prev_winner_mu FLOAT,
ADD COLUMN IF NOT EXISTS prev_winner_sigma FLOAT,
ADD COLUMN IF NOT EXISTS prev_loser_mu FLOAT,
ADD COLUMN IF NOT EXISTS prev_loser_sigma FLOAT;

-- Note: Existing comparisons will have NULL values for these columns,
-- which means undo will only decrease comparison_count but not restore
-- mu/sigma (same behavior as before this fix). New comparisons will
-- have full undo support.
