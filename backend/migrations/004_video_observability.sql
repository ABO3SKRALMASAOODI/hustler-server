-- ============================================================================
-- Round-4: timeline inserts + admin observability — additive, safe to re-run.
--   psql "$DATABASE_URL" -f backend/migrations/004_video_observability.sql
--
-- 1. assets.kind gains 'video_clip' (uploaded clips spliced into the edit).
-- 2. llm_calls: every model request/response per agent turn, for the admin
--    inspector. Payloads are size-capped and key-redacted BY THE WRITER
--    (worker) — this table never sees secrets.
-- ============================================================================

ALTER TABLE assets DROP CONSTRAINT IF EXISTS assets_kind_check;
ALTER TABLE assets ADD CONSTRAINT assets_kind_check
  CHECK (kind IN ('original','proxy','audio','thumb','sheet','render',
                  'music','image_ref','video_clip'));

CREATE TABLE IF NOT EXISTS llm_calls (
  id                SERIAL PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  job_id            INTEGER REFERENCES video_jobs(id) ON DELETE SET NULL,
  purpose           VARCHAR(32) NOT NULL,   -- agent | honesty_regen | vision_look | vision_selfcheck | vision_caption
  model             TEXT,
  request           JSONB NOT NULL,
  response          JSONB,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_project ON llm_calls(project_id, id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_job ON llm_calls(job_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created ON llm_calls(created_at);
