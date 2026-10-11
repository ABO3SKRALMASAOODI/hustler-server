-- 032: one row per subscribed/billed account per admin-timezone day, written
-- by the hourly billing tick (billing_sync.snapshot_daily). Gives a true MRR
-- history and "stopped paying" without rebuilding the past from today's
-- state. New table only; replay-safe. Apply by hand in a controlled release.
BEGIN;
SET LOCAL lock_timeout = '2s';
CREATE TABLE IF NOT EXISTS billing_daily_status (
    day                 DATE        NOT NULL,
    user_id             INTEGER     NOT NULL,          -- no FK: history survives account deletion
    billing_status      TEXT,
    plan                TEXT,
    period              TEXT,
    paying              BOOLEAN     NOT NULL,          -- the admin paying predicate at capture time
    monthly_value_cents INTEGER     NOT NULL DEFAULT 0,
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (day, user_id)
);
CREATE INDEX IF NOT EXISTS idx_billing_daily_status_user ON billing_daily_status (user_id, day);
COMMIT;
