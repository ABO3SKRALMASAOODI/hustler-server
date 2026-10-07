-- Additive: historical rows stay unknown; do not infer past acquisition.
ALTER TABLE website_signups ADD COLUMN IF NOT EXISTS attribution JSONB;
ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS attribution JSONB;
CREATE INDEX IF NOT EXISTS website_signups_first_outreach_idx ON website_signups ((attribution->'first'->>'code'));
CREATE INDEX IF NOT EXISTS website_signups_last_outreach_idx ON website_signups ((attribution->'last'->>'code'));
