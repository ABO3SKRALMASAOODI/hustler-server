-- Persist the name customers already provide during onboarding.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name varchar(120);
COMMIT;
