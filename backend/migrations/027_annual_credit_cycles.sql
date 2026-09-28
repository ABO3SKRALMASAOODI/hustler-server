-- New September contracts only. A paid annual period buys twelve monthly
-- pools, not one pool stretched across twelve months. Historical plans do
-- not enter this ledger or change their existing entitlement behavior.
BEGIN;
CREATE TABLE IF NOT EXISTS subscription_credit_cycles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    subscription_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('mcp_connect', 'advanced')),
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    monthly_credits NUMERIC NOT NULL CHECK (monthly_credits > 0),
    last_cycle INTEGER NOT NULL DEFAULT 0 CHECK (last_cycle BETWEEN 0 AND 11),
    CHECK (period_end > period_start)
);
COMMIT;
