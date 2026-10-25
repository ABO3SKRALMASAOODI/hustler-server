-- 040 — the Claude-funded trial budget (owner decision D2, 25 Oct 2026)
--
-- A never-paid account gets $1.00 of Anthropic (Claude) API usage for the
-- in-app agent, from the ~$200 of Anthropic credit in the owner's
-- subscription. Additive only: three new tables, no change to any existing
-- table or row. Safe to re-run (IF NOT EXISTS everywhere).
--
-- trial_budgets — THE per-account running total both tracks read.
--   One row per granted account (one grant per account, ever). spent_usd is
--   the provider cost of every Claude call made for the account plus the
--   non-LLM generations its turns bought (worker/claude_trial.add_spend);
--   cap_usd is its ceiling ($1.00 by default, app_kv.free_result
--   per_account_usd_cap at grant time). The account is out of trial when
--   spent_usd >= cap_usd (exhausted_at is stamped the first time).
--   Track B's export/second-request gate reads cap_usd - spent_usd.
--
-- trial_budget_days — the pool's UTC daily ledger: every trial dollar
--   (including an indexing first upload's spend before its grant exists) and
--   the grants made that day. The $200 pool, the monthly cap, the daily
--   count cap (default 40) and the daily USD cap all read it.
--
-- trial_alerts — durable admin alerts (pool at 50/80/95 %, Anthropic billing
--   or auth refusals). The worker queues; the backend sends to the admin
--   address through the existing transactional sender. Never customers.

BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS trial_budgets (
    user_id            INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    provider           TEXT NOT NULL DEFAULT 'anthropic',
    cap_usd            NUMERIC(10, 4) NOT NULL,
    spent_usd          NUMERIC(14, 6) NOT NULL DEFAULT 0,
    calls              INTEGER NOT NULL DEFAULT 0,
    granted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_project_id INTEGER,
    granted_via        TEXT,
    device_id          TEXT,
    last_spend_at      TIMESTAMPTZ,
    exhausted_at       TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trial_budgets_granted_at
    ON trial_budgets (granted_at);
CREATE INDEX IF NOT EXISTS idx_trial_budgets_device
    ON trial_budgets (device_id, granted_at)
    WHERE device_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS trial_budget_days (
    day         DATE PRIMARY KEY,
    spent_usd   NUMERIC(14, 6) NOT NULL DEFAULT 0,
    grants      INTEGER NOT NULL DEFAULT 0,
    calls       INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trial_alerts (
    id               BIGSERIAL PRIMARY KEY,
    dedupe_key       TEXT NOT NULL UNIQUE,
    kind             TEXT NOT NULL,
    subject          TEXT NOT NULL,
    html_content     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'sending', 'failed', 'sent')),
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_attempt_at  TIMESTAMPTZ,
    sent_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_trial_alerts_pending
    ON trial_alerts (created_at)
    WHERE sent_at IS NULL;

COMMIT;
