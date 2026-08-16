-- 021 — durable founder alerts for NEW paid subscriptions
--
-- The previous founder email was tied to `status = trialing`. New shopfront
-- prices charge immediately, so removing trials also removed the only signal
-- that a new customer had subscribed. This queue records that signal before
-- Brevo is called. A process restart or a temporary Brevo failure therefore
-- delays the email instead of losing it.
--
-- There are two independent dedupe keys:
--
--   * subscription_id UNIQUE — only the first positive payment on a
--     subscription is a "new subscriber"; renewals must not email again.
--   * transaction_id UNIQUE — Paddle may deliver transaction.paid,
--     transaction.completed, and retries for the same charge.
--
-- A transaction without subscription_id is not eligible. Waiting for the
-- completed event to carry it is safer than ever mistaking a one-off payment
-- for a subscriber or announcing a later renewal as new.

CREATE TABLE IF NOT EXISTS founder_subscription_alerts (
    id               BIGSERIAL PRIMARY KEY,
    dedupe_key       TEXT NOT NULL UNIQUE,
    transaction_id   TEXT NOT NULL UNIQUE,
    subscription_id  TEXT UNIQUE,
    user_id           INTEGER,
    user_email        TEXT,
    plan              TEXT,
    billing_period    TEXT,
    amount_cents      BIGINT NOT NULL,
    currency          TEXT NOT NULL DEFAULT 'USD',
    event_type        TEXT NOT NULL,
    subject           TEXT NOT NULL,
    html_content      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN
                             ('pending', 'sending', 'failed', 'sent',
                              'suppressed')),
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    next_attempt_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_attempt_at   TIMESTAMPTZ,
    sent_at           TIMESTAMPTZ,
    last_error        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_founder_subscription_alerts_due
    ON founder_subscription_alerts (next_attempt_at)
    WHERE status IN ('pending', 'failed', 'sending');

-- Do not announce a previously PAID customer as "new" on their next renewal.
-- Seed only subscriptions for which the money ledger proves a positive,
-- completed payment. `users.is_subscribed` / billing_status='active' are not
-- payment proof: Paddle can mark a legacy trial active before attempting its
-- card, and a past-due subscription can retain its ID. A trial that has never
-- paid must therefore remain eligible for its eventual first successful
-- conversion.
INSERT INTO founder_subscription_alerts (
    dedupe_key, transaction_id, subscription_id, user_id, user_email, plan,
    billing_period, amount_cents, currency, event_type, subject, html_content,
    status, sent_at, last_error
)
SELECT DISTINCT ON (p.subscription_id)
       'sub:' || p.subscription_id,
       p.transaction_id,
       p.subscription_id,
       p.user_id,
       u.email,
       p.plan,
       u.billing_period,
       p.amount_cents,
       p.currency,
       'historical',
       'Historical subscription — no alert sent',
       '',
       'suppressed',
       NOW(),
       'Seeded by migration 021 so an existing subscription renewal is not announced as new'
  FROM payments p
  LEFT JOIN users u ON u.id = p.user_id
 WHERE p.subscription_id IS NOT NULL
   AND p.status = 'completed'
   AND p.amount_cents > 0
 ORDER BY p.subscription_id,
          COALESCE(p.occurred_at, p.created_at),
          p.id
ON CONFLICT DO NOTHING;
