-- One atomic counter shared by every Brevo sender.  Routine/lifecycle mail is
-- stopped at 280 so the final 20 sends of a 300/day account remain available
-- to verification, billing, and founder subscription notifications.

CREATE TABLE IF NOT EXISTS brevo_daily_budget (
    day DATE PRIMARY KEY,
    bulk_sent INTEGER NOT NULL DEFAULT 0 CHECK (bulk_sent >= 0),
    critical_sent INTEGER NOT NULL DEFAULT 0 CHECK (critical_sent >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
