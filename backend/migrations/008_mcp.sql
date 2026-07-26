-- Round 49 — MCP surface: Valmera's editor driven by an OUTSIDE model
-- (Claude in the user's own Claude Code session, on their own subscription).
--
-- Two objects and one relaxed constraint. Nothing here is visible to a user
-- who holds no token, and tokens can only be minted by the admin account.

-- 1. A tool call from the outside model is a job like any other, so the queue
--    keeps the heartbeats, the reaper and the admin views it already has. The
--    original CHECK was written before this type existed and rejects it.
ALTER TABLE video_jobs DROP CONSTRAINT IF EXISTS video_jobs_type_check;
ALTER TABLE video_jobs ADD CONSTRAINT video_jobs_type_check
    CHECK (type IN ('index', 'preview', 'final', 'agent_turn', 'mcp_tool'));

-- 2. Bearer tokens. Only the sha256 is stored — the plaintext is shown once,
--    at mint time, and is unrecoverable afterwards.
--
--    active_project_id is the "which project am I editing" pointer. It lives
--    on the token rather than in an MCP session because MCP sessions do not
--    survive a client restart and a re-connect that silently forgot which
--    project it was editing would be far worse than one that remembers.
CREATE TABLE IF NOT EXISTS mcp_tokens (
    id                SERIAL PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_sha256      CHAR(64) NOT NULL UNIQUE,
    label             TEXT,
    active_project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    calls             INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at      TIMESTAMPTZ,
    revoked_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_mcp_tokens_user ON mcp_tokens(user_id);

-- 3. The tool catalog the worker publishes on boot.
--
--    tools/list has to answer with the EXACT tools this deployment can run —
--    including the ones hidden because their backing service is unconfigured
--    (no image key, no stock key, no music pack). Only the worker knows that;
--    the backend has neither the imports nor the env. So the worker writes it
--    here every time it starts, and the backend reads it. One row, id = 1.
CREATE TABLE IF NOT EXISTS mcp_catalog (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    json       JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
