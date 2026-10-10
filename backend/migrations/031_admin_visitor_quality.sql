-- 031: visitor quality and honest signup sources (admin rebuild, Oct 2026).
-- Additive and replay-safe. Apply by hand in a controlled release; the
-- backend detects each column (information_schema, cached 5 min) and behaves
-- exactly as before until it exists, so deploy order does not matter.
--
--   page_visits.interacted   the browser sent a real input (pointer, touch,
--                            key, wheel or scroll). Separates people from
--                            link-preview robots, which never interact.
--   page_visits.signed_in    the visit came from a signed-in customer.
--   website_signups.tracking what the browser sent at signup:
--                              tracked | privacy_signal | no_identity
--                            (or estimated_from_referrer, migration 033).
--                            NULL = a row written before this migration.
--   device_id / session_id   nullable, so a privacy-signal or no-identity
--                            signup still gets its one row.
--
-- ADD COLUMN ... DEFAULT FALSE is metadata-only on PostgreSQL 11+ (no table
-- rewrite). DROP NOT NULL is a catalog change. lock_timeout keeps a busy
-- table from queueing every later query behind this DDL (the Jul 26 outage).
-- Existing rows deliberately keep tracking = NULL: the admin reads NULL with
-- an attribution as "tracked", and uses the first non-NULL value written by
-- the new code to date when "not recorded" reasons began to be saved.
BEGIN;
SET LOCAL lock_timeout = '2s';
ALTER TABLE page_visits     ADD COLUMN IF NOT EXISTS interacted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE page_visits     ADD COLUMN IF NOT EXISTS signed_in  BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE website_signups ADD COLUMN IF NOT EXISTS tracking   TEXT;
ALTER TABLE website_signups ALTER COLUMN device_id  DROP NOT NULL;
ALTER TABLE website_signups ALTER COLUMN session_id DROP NOT NULL;
COMMIT;
