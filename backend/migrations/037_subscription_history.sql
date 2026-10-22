-- Reporting-only history. No changes to customer billing or entitlements.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';
CREATE TABLE IF NOT EXISTS admin_subscription_history (
    subscription_id text NOT NULL,
    provider_updated_at timestamptz NOT NULL,
    status text NOT NULL,
    started_at timestamptz,
    canceled_at timestamptz,
    next_billed_at timestamptz,
    period_ends_at timestamptz,
    scheduled_action text,
    scheduled_effective_at timestamptz,
    event_id text,
    event_type text,
    occurred_at timestamptz,
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subscription_id, provider_updated_at)
);
COMMIT;
