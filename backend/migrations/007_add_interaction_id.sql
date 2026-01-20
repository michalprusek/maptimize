-- Migration: Add interaction_id column for Gemini Interactions API
-- This enables server-side conversation state management, reducing token usage
-- by not sending full history with each request.

-- Add interaction_id column to chat_messages table
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS interaction_id VARCHAR(255);

-- Index for fast lookup by interaction_id (only for non-null values)
CREATE INDEX IF NOT EXISTS idx_chat_messages_interaction_id
ON chat_messages(interaction_id) WHERE interaction_id IS NOT NULL;

-- Comment explaining the column's purpose
COMMENT ON COLUMN chat_messages.interaction_id IS
'Gemini Interactions API interaction ID for server-side state management. Each assistant response stores the interaction_id returned by Gemini, which is used as previous_interaction_id for subsequent messages in the conversation.';
