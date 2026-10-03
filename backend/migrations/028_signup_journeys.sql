-- Additive: legacy beacons remain readable during the rolling deployment.
ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS analytics_id UUID;
ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP;
ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS scroll_depth INTEGER NOT NULL DEFAULT 0;
ALTER TABLE page_visits ADD COLUMN IF NOT EXISTS exit_reason TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS page_visits_analytics_id ON page_visits(analytics_id);
CREATE INDEX IF NOT EXISTS page_visits_device_time ON page_visits(device_id, visited_at);
CREATE TABLE IF NOT EXISTS website_events (
 id UUID PRIMARY KEY, visit_id UUID NOT NULL REFERENCES page_visits(analytics_id) ON DELETE CASCADE,
 kind TEXT NOT NULL, active_seconds INTEGER NOT NULL DEFAULT 0,
 status INTEGER, created_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS website_events_visit ON website_events(visit_id,created_at);
CREATE INDEX IF NOT EXISTS website_events_created ON website_events(created_at);
CREATE TABLE IF NOT EXISTS website_signups (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 device_id TEXT NOT NULL, session_id TEXT NOT NULL,
 completed_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS website_signups_device ON website_signups(device_id);
ALTER TABLE google_auth_codes ADD COLUMN IF NOT EXISTS is_signup BOOLEAN NOT NULL DEFAULT FALSE;
-- Old clients wrote one entry (seconds=0) AND another row for time spent.
-- Keep the originals; report only real entries and the new upserted visits.
CREATE OR REPLACE VIEW analytics_page_visits AS
 SELECT * FROM page_visits
 WHERE (analytics_id IS NOT NULL OR COALESCE(time_on_page,0)=0)
 AND COALESCE(user_agent,'') !~* '(bot|crawler|spider|slurp|headless|notebooklm|vercel-screenshot)';
