-- Admin observability reads rolling 24-hour/14-day windows on every refresh.
-- Without time-leading indexes PostgreSQL scans the full wide heaps; this was
-- especially costly after llm_calls' TOAST reached ~695 MB. Run each statement
-- directly with psql (not inside a transaction): CONCURRENTLY keeps customer
-- writes available while the indexes build.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_jobs_updated_type_state
    ON video_jobs (updated_at DESC, type, state, id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_jobs_created_type
    ON video_jobs (created_at DESC, type);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_llm_calls_created_purpose_model
    ON llm_calls (created_at DESC, purpose, model);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chat_messages_created_role
    ON chat_messages (created_at DESC, role);
