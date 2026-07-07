-- ============================================================================
-- Video editor pivot — additive schema. Applied manually via psql (project
-- convention: schema is managed through Render shell / psql, not models.py).
--
--   psql "$DATABASE_URL" -f backend/migrations/001_video_editor.sql
--
-- Safe to re-run: everything is IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.
-- Existing tables (users, jobs, chat_sessions, chat_messages, ...) untouched
-- except one additive column on chat_messages.
-- ============================================================================

-- One video-editing project per user workspace. Chat history lives in the
-- existing chat_sessions/chat_messages tables via chat_session_id.
CREATE TABLE IF NOT EXISTS projects (
  id              SERIAL PRIMARY KEY,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title           TEXT NOT NULL DEFAULT 'Untitled project',
  chat_session_id INTEGER REFERENCES chat_sessions(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);

-- Pointers to object storage. Never media bytes.
CREATE TABLE IF NOT EXISTS assets (
  id          SERIAL PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind        VARCHAR(16) NOT NULL
              CHECK (kind IN ('original','proxy','audio','thumb','sheet','render','music')),
  storage_key TEXT NOT NULL,
  bytes       BIGINT,
  duration_s  DOUBLE PRECISION,
  width       INTEGER,
  height      INTEGER,
  fps         DOUBLE PRECISION,
  sha256      VARCHAR(64),
  meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id);
CREATE INDEX IF NOT EXISTS idx_assets_sha ON assets(sha256);

-- Perception cache: one index per unique video file, keyed by content hash.
-- Re-uploading the same file (any project) is a free cache hit.
CREATE TABLE IF NOT EXISTS indexes (
  id           SERIAL PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  video_sha256 VARCHAR(64) NOT NULL UNIQUE,
  json         JSONB NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Append-only Edit Decision List versions. Writes create a new row, never
-- mutate.
CREATE TABLE IF NOT EXISTS edls (
  id         SERIAL PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version    INTEGER NOT NULL,
  json       JSONB NOT NULL,
  created_by VARCHAR(8) NOT NULL DEFAULT 'agent' CHECK (created_by IN ('user','agent')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (project_id, version)
);

-- Job queue for the media/agent worker. Named video_jobs because the legacy
-- app-builder already owns a `jobs` table with a different schema.
CREATE TABLE IF NOT EXISTS video_jobs (
  id           SERIAL PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type         VARCHAR(16) NOT NULL CHECK (type IN ('index','preview','final','agent_turn')),
  payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
  state        VARCHAR(10) NOT NULL DEFAULT 'queued'
               CHECK (state IN ('queued','running','done','failed')),
  progress     INTEGER NOT NULL DEFAULT 0,
  error        TEXT,
  result       JSONB,
  attempts     INTEGER NOT NULL DEFAULT 0,
  heartbeat_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_video_jobs_claim ON video_jobs(state, type, id);
CREATE INDEX IF NOT EXISTS idx_video_jobs_project ON video_jobs(project_id, id);

-- Reused chat tables get a JSONB side-channel for agent activity items
-- (tool calls, ask_user flags, attached previews).
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS meta JSONB;
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
