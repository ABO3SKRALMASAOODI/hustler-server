-- 006: client-side event beacon.
--
-- Why this exists: on 2026-07-18 a user's captioned render sat correct in R2
-- while their browser refused to play it. The studio fell back to the raw
-- proxy and raised a Retry chip that could not help. Diagnosing it took five
-- parallel investigations and still could not name the trigger, because a
-- <video> element failing on the client leaves NO server-side trace at all --
-- the media bytes never pass through our API.
--
-- This table is that trace. It is deliberately generic (kind + jsonb detail)
-- so new client-side failures can be reported without another migration.
-- Idempotent -- safe to re-run.

CREATE TABLE IF NOT EXISTS client_events (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER     REFERENCES users(id)    ON DELETE CASCADE,
    project_id  INTEGER     REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT        NOT NULL,
    asset_id    INTEGER,
    detail      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_client_events_project ON client_events (project_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_client_events_kind    ON client_events (kind, created_at DESC);
