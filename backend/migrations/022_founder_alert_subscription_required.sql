-- 022 — every queued "new subscriber" alert must name its subscription.
--
-- The application already refuses transactions without subscription_id. This
-- database constraint makes the same invariant durable even if a future
-- caller bypasses the helper or writes the outbox directly. Kept separate
-- from 021 because 021 may already have been applied before this hardening.

ALTER TABLE founder_subscription_alerts
    ALTER COLUMN subscription_id SET NOT NULL;
