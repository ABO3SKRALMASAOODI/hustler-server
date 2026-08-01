-- 014: the non-refundable claim counter (round 73)
--
-- WHY. video_jobs.attempts is a FAIRNESS counter: db.release_jobs hands one
-- back on every dispatcher SIGTERM, because a deploy killing a job mid-render
-- is our fault and not the job's. That is correct and it stays. But it means
-- MAX_ATTEMPTS_MEDIA=3 bounds nothing: a job caught by deploys is handed
-- another life every time, and on Cloud Run each life is a full
-- FFMPEG_TIMEOUT_S (3000s) of an 8-vCPU / 32 GiB instance — about $0.82.
--
-- On 2026-07-26 final job=836 ran FIVE 50-minute encodes against a cap of
-- three; 50 deploys that week are what paid for it. Four jobs in that shape
-- burned 9.37 hours, roughly half of a $14 week.
--
-- So the fairness question and the money question get separate counters.
-- total_claims counts what we have PHYSICALLY SPENT running this job and is
-- never given back. claim_job stops selecting past config.MAX_CLAIMS_ABSOLUTE
-- and db.fail_ceilinged_jobs ends those visibly, so a bounded job surfaces in
-- chat instead of sitting under a spinner nothing will ever pick up.
--
-- Backfill: existing rows start at 0 rather than at `attempts`. In-flight work
-- keeps its full remaining budget across the deploy, which is the safe
-- direction — the alternative retires a live job mid-render on the strength of
-- history it was never warned about.
--
-- The worker survives this file not having run yet (db.claims_column_ready),
-- so deploy first, then psql, in either order.

ALTER TABLE video_jobs
    ADD COLUMN IF NOT EXISTS total_claims INTEGER NOT NULL DEFAULT 0;

-- claim_job filters on it on every poll, next to the existing state/type work.
CREATE INDEX IF NOT EXISTS idx_video_jobs_total_claims
    ON video_jobs (total_claims)
    WHERE state IN ('queued', 'running');
