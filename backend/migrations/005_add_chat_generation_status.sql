-- Migration 005: Add generation status fields to chat_threads
-- This enables:
-- 1. Async message generation (non-blocking)
-- 2. Persistence of "thinking" state across page refresh
-- 3. Cancellation of message generation

-- Add generation status columns to chat_threads
ALTER TABLE chat_threads
ADD COLUMN IF NOT EXISTS generation_status VARCHAR(20) DEFAULT 'idle',
ADD COLUMN IF NOT EXISTS generation_task_id VARCHAR(64),
ADD COLUMN IF NOT EXISTS generation_started_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS generation_error TEXT;

-- Index for finding threads with active generation
CREATE INDEX IF NOT EXISTS idx_chat_threads_generation_status
ON chat_threads(generation_status)
WHERE generation_status = 'generating';

-- Index for task lookup
CREATE INDEX IF NOT EXISTS idx_chat_threads_task_id
ON chat_threads(generation_task_id)
WHERE generation_task_id IS NOT NULL;

-- CHECK constraint to ensure valid generation_status values
-- This prevents invalid states from being written directly to DB
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'check_generation_status_valid'
    ) THEN
        ALTER TABLE chat_threads
        ADD CONSTRAINT check_generation_status_valid
        CHECK (generation_status IN ('idle', 'generating', 'completed', 'cancelled', 'error'));
    END IF;
END $$;

-- Comment for documentation
COMMENT ON COLUMN chat_threads.generation_status IS 'Status of AI response generation: idle, generating, completed, cancelled, error';
COMMENT ON COLUMN chat_threads.generation_task_id IS 'Unique task ID for tracking async generation';
COMMENT ON COLUMN chat_threads.generation_started_at IS 'When generation started (for timeout handling)';
COMMENT ON COLUMN chat_threads.generation_error IS 'Error message if generation failed';
