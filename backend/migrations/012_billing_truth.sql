-- 012 — BILLING TRUTH (round 59)
--
-- WHY. On Jul 29 2026 the admin said a customer had CONVERTED while Paddle
-- said his card was refused (`not_enough_balance` on a $30.00 charge), and the
-- revenue panel said $0. All three were produced by the same missing fact: we
-- had nowhere to write down that money had actually arrived.
--
--   * Paddle flips a subscription to `active` when the trial ENDS, then tries
--     the card. We read `active` as "converted", released the full 2,000-credit
--     pool, and the `subscription.past_due` that followed a second later was
--     logged as "no change (grace)". Nothing ever walked it back.
--   * Revenue was computed from `users.plan` counts against a price map of
--     {plus, pro, ultra} — three RETIRED plans. Every live customer is on
--     ai/ai_pro/ai_max, so MRR was structurally 0 no matter who paid.
--
-- So: a real ledger of money (`payments`), and Paddle's own subscription
-- status recorded verbatim on the user, so "is this person paying?" is a
-- column and not an inference.
--
-- Every column is nullable / defaulted. Rows that predate this read as
-- "unknown", which `billing.py` treats as the pre-existing behaviour.

-- ── What Paddle says about this account, recorded verbatim ──────────────────
-- One of: trialing | active | past_due | paused | canceled  (NULL = never had
-- a subscription, or predates this migration).
ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_status         TEXT;
-- The plan Paddle bills them for, which is NOT the same question as `plan`
-- (what they may spend on right now). A refused payment sets plan='free' so no
-- downstream grant can leak, while this remembers what to restore on recovery.
ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_plan           TEXT;
-- 'monthly' | 'yearly', read off Paddle's own billing_cycle. Without it a
-- yearly customer either books as a monthly one (understating MRR by 10x on
-- the yearly discount) or as nothing at all.
ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_period         TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_synced_at      TIMESTAMP;

-- ── The refused-payment lifecycle ───────────────────────────────────────────
ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_failed_at      TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_failed_reason  TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_failed_count   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_recovered_at   TIMESTAMP;
-- Last time we emailed them "your card was declined". Bounds the daily nudge
-- to one per day per account, and is what makes the tick idempotent if it runs
-- twice (three gunicorn workers each run a scheduler).
ALTER TABLE users ADD COLUMN IF NOT EXISTS dunning_emailed_at     TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS dunning_email_count    INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_users_billing_status
    ON users (billing_status) WHERE billing_status IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_subscription_id
    ON users (subscription_id) WHERE subscription_id IS NOT NULL;

-- ── The money ledger ────────────────────────────────────────────────────────
-- One row per Paddle transaction we have ever been told about, successful or
-- not. `amount_cents` is Paddle's own `details.totals.grand_total` (already in
-- the currency's minor unit), so a $30.00 charge is 3000 and the $0.00
-- transaction that OPENS a trial is 0 — which is exactly why counting
-- "completed transactions" was never a revenue number and this table stores
-- the amount.
--
-- transaction_id is UNIQUE: Paddle retries webhooks, and a ledger that
-- double-counts a retry is worse than one that misses an event.
CREATE TABLE IF NOT EXISTS payments (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER,
    transaction_id  TEXT NOT NULL UNIQUE,
    subscription_id TEXT,
    plan            TEXT,
    -- Paddle's transaction status: completed | past_due | billed | canceled…
    status          TEXT NOT NULL,
    amount_cents    BIGINT NOT NULL DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'USD',
    -- 'subscription_recurring' | 'web' | … straight off Paddle's `origin`.
    origin          TEXT,
    -- Why the card was refused, e.g. 'not_enough_balance'. NULL when it wasn't.
    error_code      TEXT,
    occurred_at     TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payments_user       ON payments (user_id);
CREATE INDEX IF NOT EXISTS idx_payments_sub        ON payments (subscription_id);
CREATE INDEX IF NOT EXISTS idx_payments_occurred   ON payments (occurred_at);
-- The index behind every revenue query: money that actually landed.
CREATE INDEX IF NOT EXISTS idx_payments_collected
    ON payments (occurred_at) WHERE status = 'completed' AND amount_cents > 0;
