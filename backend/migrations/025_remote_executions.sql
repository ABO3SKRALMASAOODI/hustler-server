-- Durable ownership for provider calls.
--
-- A Modal FunctionCall used to exist only in the Render dispatcher's RAM.
-- If Render restarted after spawn, the queue retained a running job but lost
-- the provider call id; after the heartbeat aged out, the same job could be
-- claimed and paid for again.  This table is the provider-neutral handoff
-- ledger used by Modal today and Cloudflare Containers during the canary.

CREATE TABLE IF NOT EXISTS remote_executions (
  job_id            INTEGER PRIMARY KEY
                    REFERENCES video_jobs(id) ON DELETE CASCADE,
  total_claims      INTEGER NOT NULL,
  provider          TEXT NOT NULL CHECK (provider IN
                    ('modal', 'cloudflare', 'cloud_run')),
  call_id           TEXT NOT NULL,
  function_name     TEXT,
  state             TEXT NOT NULL DEFAULT 'submitted' CHECK (state IN
                    ('submitted', 'running', 'done', 'failed', 'cancelled')),
  deadline_at       TIMESTAMPTZ NOT NULL,
  submitted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at        TIMESTAMPTZ,
  last_observed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at      TIMESTAMPTZ,
  error             TEXT,
  meta              JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (provider, call_id)
);

CREATE INDEX IF NOT EXISTS idx_remote_executions_active
  ON remote_executions (deadline_at, last_observed_at)
  WHERE state IN ('submitted', 'running');

CREATE INDEX IF NOT EXISTS idx_remote_executions_claim
  ON remote_executions (job_id, total_claims, state);
