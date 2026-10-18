-- Kit marketing only. Authentication and billing email remain on Brevo.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

CREATE TABLE IF NOT EXISTS newsletter_permissions (
    email TEXT PRIMARY KEY CHECK (email = lower(email)),
    consented_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    disclosure TEXT NOT NULL,
    revoked_at TIMESTAMPTZ,
    kit_subscriber_id BIGINT,
    kit_state TEXT,
    synced_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One durable send intent per Dubai date. An uncertain API outcome is never
-- retried by creating a second broadcast; it must be reconciled first.
CREATE TABLE IF NOT EXISTS kit_daily_runs (
    day DATE PRIMARY KEY,
    topic TEXT NOT NULL,
    description TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'prepared',
    broadcast_id BIGINT UNIQUE,
    recipient_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kit_webhook_events (
    id TEXT PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
