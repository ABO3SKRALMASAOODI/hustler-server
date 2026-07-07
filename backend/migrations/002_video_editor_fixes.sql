-- ============================================================================
-- Video editor fixes — additive, safe to re-run.
--   psql "$DATABASE_URL" -f backend/migrations/002_video_editor_fixes.sql
--
-- 1. assets.kind gains 'image_ref' (chat image attachments).
-- 2. Idempotent chat sends: one user message per (session, client_msg_id).
-- ============================================================================

ALTER TABLE assets DROP CONSTRAINT IF EXISTS assets_kind_check;
ALTER TABLE assets ADD CONSTRAINT assets_kind_check
  CHECK (kind IN ('original','proxy','audio','thumb','sheet','render',
                  'music','image_ref'));

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_messages_client_msg
  ON chat_messages (session_id, (meta->>'client_msg_id'))
  WHERE meta->>'client_msg_id' IS NOT NULL;
