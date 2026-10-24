-- migrate: no-transaction
-- Admin read paths (24 Oct 2026). Each index matches one predicate the admin
-- already runs, so the planner answers it without decompressing job results:
--
-- idx_video_jobs_mcp_non_success / idx_video_jobs_mcp_refusal
--   The WHERE clauses are routes/admin_video._mcp_non_success_sql('') and
--   _mcp_refusal_sql('') verbatim (tests/test_admin_read_indexes.py keeps
--   them in step). Counting a project's failed or refused tool calls reads a
--   few index entries instead of classifying every tool result: the Projects
--   list's tool columns took 12 s for the latest 100 projects and hit the
--   statement timeout.
-- idx_video_jobs_agent_turn_message
--   "Was this chat message ever picked up by an agent turn?"
--   (admin_video.UNSERVED_EXISTS, the worker's unanswered-message checks).
--   It scanned every agent_turn job once per user message: 5 s of the 20 s
--   People detail for a long-lived account.
--
-- CONCURRENTLY keeps job writes live while production builds them.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_jobs_mcp_non_success
    ON video_jobs (project_id)
    WHERE type = 'mcp_tool'
      AND (state = 'failed'
      OR COALESCE(result->'tool_outcome'->>'status', 'success')
      <> 'success'
      OR UPPER(LTRIM(COALESCE(result->>'text', ''))) LIKE ANY (ARRAY[
      'REJECTED%', 'CORRECTION_NEEDED%', 'CORRECTION NEEDED%',
      'RECIPE ABORTED%', 'PREREQUISITE%',
      'TRANSIENT_FAILURE%', 'TRANSIENT FAILURE%', 'TOOL %',
      'FAILED%', 'COULD NOT%', 'UNAVAILABLE%',
      'UNKNOWN TOOL%', 'UNSAFE%'])
      OR UPPER(LTRIM(COALESCE(result->>'text', ''))) LIKE '%PREREQUISITE:%'
      OR SPLIT_PART(UPPER(LTRIM(COALESCE(result->>'text', ''))), CHR(10), 1) ~
      '(^|[^A-Z])(FAILED|COULD NOT|UNAVAILABLE|ERRORED)([^A-Z]|$)'
      OR COALESCE(result->>'is_error', 'false') = 'true'
      OR result ? 'failure');

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_jobs_mcp_refusal
    ON video_jobs (project_id)
    WHERE type = 'mcp_tool'
      AND (COALESCE(
      result->'tool_outcome'->>'status', '')
      IN ('correction_needed', 'unsafe')
      OR UPPER(LTRIM(COALESCE(result->>'text', ''))) LIKE ANY (ARRAY[
      'REJECTED%', 'CORRECTION_NEEDED%', 'CORRECTION NEEDED%',
      'RECIPE ABORTED%', 'UNSAFE%']));

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_video_jobs_agent_turn_message
    ON video_jobs ((payload->>'message_id'))
    WHERE type = 'agent_turn';
