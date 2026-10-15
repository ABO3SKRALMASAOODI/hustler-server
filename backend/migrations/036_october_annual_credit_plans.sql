-- Extend the annual monthly-refresh ledger without changing existing rows.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';
ALTER TABLE subscription_credit_cycles
    DROP CONSTRAINT IF EXISTS subscription_credit_cycles_plan_check;
ALTER TABLE subscription_credit_cycles
    ADD CONSTRAINT subscription_credit_cycles_plan_check
    CHECK (plan IN ('mcp_connect', 'advanced', 'plus_2026', 'pro_2026', 'max_2026'));
COMMIT;
