-- Round 67d: the index greeting is idempotent against the chat.
--
-- An index job reaped after a worker death and re-claimed re-runs its whole
-- completion path with no reindex flag, and project 300 (2026-07-31) got
-- "Your video is ready to edit" twice from ONE job row, 3.5 minutes apart.
-- The greet is now keyed on the asset it announces (meta->>'index_greet' =
-- asset id): the worker checks before posting, and this partial unique index
-- settles the one path a check cannot — two live workers (a deploy window
-- runs old and new side by side) greeting the same asset at the same moment.
--
-- Same shape as idx_chat_messages_client_msg.

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_messages_index_greet
    ON chat_messages (session_id, (meta->>'index_greet'))
    WHERE meta->>'index_greet' IS NOT NULL;
