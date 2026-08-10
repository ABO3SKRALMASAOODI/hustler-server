-- 017: isolated reliability counters (2026-08-10).
--
-- One row per counter, incremented by the worker at the moment the thing
-- happens (worker/db.bump_metric), because the raw evidence does not keep:
-- video_jobs rows are deleted with their project (backend/routes/video.py),
-- so counting failures by SQL over jobs undercounts forever after the first
-- cleanup. These survive.
--
-- Counters written today:
--   worker_died   — a lane thread died, or the reaper buried jobs whose
--                   heartbeat went stale (hard process death: OOM, kill -9).
--   job_failed    — a job reached state 'failed' terminally (in-process
--                   failure or reaper burial). Requeues/retries not counted.
--   tool_refused  — a tool call answered REJECTED / unknown-name / bad-args
--                   at the dispatch chokepoint (in-house agent and MCP both).
--   tool_failed   — a tool call raised and was reported as an error string.
CREATE TABLE IF NOT EXISTS metrics_counters (
    name       TEXT PRIMARY KEY,
    count      BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
