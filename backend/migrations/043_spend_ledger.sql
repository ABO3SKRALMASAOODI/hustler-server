-- 043 — one ledger for every model dollar (census class 3, 6 Nov 2026)
--
-- spend_ledger: one row per UTC day, provider, lane and purpose. Additive: no
-- existing table or row changes, safe to re-run. Written by worker llm.record
-- and the backend concierge (spend_ledger.charge). lane: customer, internal or
-- replay. trial_budget_days stays the customers' credit line and the trial's
-- pool; company lanes are never written there.

BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS spend_ledger (
    day         DATE NOT NULL,
    provider    TEXT NOT NULL,
    lane        TEXT NOT NULL CHECK (lane IN ('customer', 'internal', 'replay')),
    purpose     TEXT NOT NULL,
    usd         NUMERIC(14, 6) NOT NULL DEFAULT 0,
    calls       INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (day, provider, lane, purpose)
);

-- The fence reads one lane on one provider before every company call.
CREATE INDEX IF NOT EXISTS idx_spend_ledger_lane
    ON spend_ledger (lane, provider, day);

COMMIT;
