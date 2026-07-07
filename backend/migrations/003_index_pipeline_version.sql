-- ============================================================================
-- Version the index cache — additive, safe to re-run.
--   psql "$DATABASE_URL" -f backend/migrations/003_index_pipeline_version.sql
--
-- Cache hits now require sha256 AND pipeline_version to match, so indexes
-- built by an older pipeline (e.g. pre-fix sentence segmentation) are
-- rebuilt automatically instead of served stale. Existing rows default to
-- version 1 (= pre-versioning pipeline) and self-heal on next project open.
-- ============================================================================

ALTER TABLE indexes ADD COLUMN IF NOT EXISTS
  pipeline_version INTEGER NOT NULL DEFAULT 1;
