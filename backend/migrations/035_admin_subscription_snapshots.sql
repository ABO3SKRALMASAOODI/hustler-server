-- Reporting only: retain Paddle's renewal choice and ended contracts without
-- changing customer access, credit balances, or the payment ledger.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';
CREATE TABLE IF NOT EXISTS admin_subscription_snapshots (
    subscription_id text PRIMARY KEY,
    status text NOT NULL,
    started_at timestamptz,
    canceled_at timestamptz,
    next_billed_at timestamptz,
    period_ends_at timestamptz,
    scheduled_action text,
    scheduled_effective_at timestamptz,
    provider_updated_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
