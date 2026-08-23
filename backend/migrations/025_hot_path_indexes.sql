-- migrate: no-transaction
-- Hot reads used by Studio, Podcast Shorts, admin inspectors, and the worker
-- accounting janitor.  CONCURRENTLY keeps uploads, jobs, and chat writes live
-- while production builds the indexes.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_projects_user_parent_id
    ON projects (user_id, id DESC)
    WHERE parent_project_id IS NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_projects_parent_id
    ON projects (parent_project_id, id DESC)
    WHERE parent_project_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_jobs_project_type_id
    ON video_jobs (project_id, type, id DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_jobs_user_updated
    ON video_jobs (user_id, updated_at DESC, id DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_jobs_pending_accounting
    ON video_jobs (id)
    WHERE state = 'done'
      AND (result->>'billing_pending' = 'true'
           OR result->>'qualification_pending' = 'true');

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_assets_project_kind_id
    ON assets (project_id, kind, id DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_assets_render_variant
    ON assets (project_id, (meta->>'variant'), id DESC)
    WHERE kind = 'render';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chat_messages_session_role_id
    ON chat_messages (session_id, role, id DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_client_events_project_kind_created
    ON client_events (project_id, kind, created_at DESC, id DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_client_events_user_kind_created
    ON client_events (user_id, kind, created_at DESC, id DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_edls_project_version
    ON edls (project_id, version DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_page_visits_visited
    ON page_visits (visited_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_page_visits_session_visited
    ON page_visits (session_id, visited_at DESC)
    WHERE session_id IS NOT NULL AND session_id <> '';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_verified_created
    ON users (created_at DESC, id DESC)
    WHERE is_verified = 1;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_job_credits_user_created
    ON job_credits (user_id, created_at DESC);
