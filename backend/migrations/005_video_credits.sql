-- 005: credits for the video editor.
-- (a) Ensure the credit columns exist on fresh databases (local rigs create
--     users via models.init_db, which predates the credits system).
-- (b) Raise the new-user welcome bonus 80 -> 150 (defaults only; existing
--     users' balances are untouched).
-- Idempotent — safe to re-run on production where the columns already exist.

ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_daily         NUMERIC DEFAULT 20;
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_daily_reset   DATE    DEFAULT CURRENT_DATE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_bonus         NUMERIC DEFAULT 150;
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_monthly       NUMERIC DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_balance       NUMERIC DEFAULT 170;
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_monthly_limit NUMERIC DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_subscribed         INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan                  VARCHAR DEFAULT 'free';

ALTER TABLE users ALTER COLUMN credits_bonus   SET DEFAULT 150;
ALTER TABLE users ALTER COLUMN credits_balance SET DEFAULT 170;
