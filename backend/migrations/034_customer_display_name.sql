-- Persist the name customers already provide during onboarding.
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name varchar(120);
