-- Private, disposable wire histories for a follow-up dispatched to another worker.
-- A database restart may empty this table. The normal project context handles a cold start.
BEGIN;
SET LOCAL lock_timeout = '5s';
CREATE UNLOGGED TABLE IF NOT EXISTS edit_session_prefixes (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    input_tokens INTEGER NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    body BYTEA NOT NULL,
    PRIMARY KEY (project_id, session_id)
);
CREATE INDEX IF NOT EXISTS edit_session_prefixes_expiry ON edit_session_prefixes(expires_at);
COMMIT;
