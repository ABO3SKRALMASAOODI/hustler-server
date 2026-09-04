-- Base account/chat tables inherited from the original application.
--
-- These used to be created by models.init_db() every time each Gunicorn
-- worker constructed the Flask app. Keep schema ownership in the migration
-- runner so web-process startup is read-only with respect to DDL.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_subscribed INTEGER DEFAULT 0,
    subscription_expiry TIMESTAMP,
    subscription_id TEXT,
    is_verified INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    plan VARCHAR(20) DEFAULT 'free',
    credits_monthly_limit NUMERIC(10,2) DEFAULT 0,
    credits_daily NUMERIC(10,2) DEFAULT 20,
    credits_daily_reset DATE NOT NULL DEFAULT CURRENT_DATE,
    credits_bonus NUMERIC(10,2) NOT NULL DEFAULT 150,
    credits_monthly NUMERIC(10,2) NOT NULL DEFAULT 0,
    credits_balance NUMERIC(10,2) NOT NULL DEFAULT 170,
    unsubscribed_at TIMESTAMP,
    auth_provider VARCHAR,
    device_type TEXT,
    device_browser TEXT,
    last_user_agent TEXT,
    last_seen_at TIMESTAMPTZ,
    trial_status TEXT,
    trial_started_at TIMESTAMP,
    trial_ends_at TIMESTAMP,
    trial_canceled_at TIMESTAMP,
    trial_plan TEXT,
    trial_subscription_id TEXT
);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS plan VARCHAR(20) DEFAULT 'free';
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS credits_monthly_limit NUMERIC(10,2) DEFAULT 0;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS credits_daily NUMERIC(10,2) DEFAULT 20;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS credits_daily_reset DATE NOT NULL
        DEFAULT CURRENT_DATE;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS credits_bonus NUMERIC(10,2) NOT NULL DEFAULT 150;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS credits_monthly NUMERIC(10,2) NOT NULL DEFAULT 0;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS credits_balance NUMERIC(10,2) NOT NULL DEFAULT 170;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS unsubscribed_at TIMESTAMP;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS auth_provider VARCHAR;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS device_type TEXT;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS device_browser TEXT;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS last_user_agent TEXT;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS trial_status TEXT;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMP;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS trial_canceled_at TIMESTAMP;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS trial_plan TEXT;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS trial_subscription_id TEXT;

CREATE TABLE IF NOT EXISTS password_reset_codes (
    email TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES chat_sessions(id)
);

CREATE TABLE IF NOT EXISTS email_codes (
    email TEXT PRIMARY KEY,
    code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS code_request_logs (
    email TEXT NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    job_id VARCHAR(16) NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    state VARCHAR(20) NOT NULL DEFAULT 'running',
    preview_url TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_job_id ON jobs(job_id);

-- Legacy operational tables that already exist in production but were once
-- provisioned manually. A fresh/disaster-recovery database must have the same
-- billing, onboarding, OAuth-return, and analytics foundations before the web
-- process starts; request handlers are not schema migration runners.
CREATE TABLE IF NOT EXISTS job_credits (
    id           SERIAL PRIMARY KEY,
    job_id       VARCHAR(16) NOT NULL,
    user_id      INTEGER REFERENCES users(id),
    turn         INTEGER NOT NULL DEFAULT 1,
    tokens_used  INTEGER NOT NULL DEFAULT 0,
    credits_used NUMERIC(6,2) NOT NULL DEFAULT 0,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_job_credits_job_id ON job_credits(job_id);
CREATE INDEX IF NOT EXISTS idx_job_credits_user_id ON job_credits(user_id);

CREATE TABLE IF NOT EXISTS page_visits (
    id              SERIAL PRIMARY KEY,
    page            TEXT NOT NULL DEFAULT '/',
    ip              TEXT,
    user_agent      TEXT,
    visited_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    country         TEXT DEFAULT 'Unknown',
    session_id      TEXT,
    referrer        TEXT,
    referrer_source TEXT DEFAULT 'direct',
    device_type     TEXT DEFAULT 'desktop',
    browser         TEXT DEFAULT 'unknown',
    time_on_page    INTEGER DEFAULT 0,
    device_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_page_visits_date ON page_visits(visited_at);
CREATE INDEX IF NOT EXISTS idx_page_visits_device ON page_visits(device_id);
CREATE INDEX IF NOT EXISTS idx_page_visits_referrer
    ON page_visits(referrer_source);
CREATE INDEX IF NOT EXISTS idx_page_visits_session ON page_visits(session_id);

CREATE TABLE IF NOT EXISTS onboarding_responses (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL UNIQUE
                   REFERENCES users(id) ON DELETE CASCADE,
    channel        TEXT,
    channel_other  TEXT,
    use_case       TEXT,
    use_case_other TEXT,
    goal           TEXT,
    goal_other     TEXT,
    device_type    TEXT,
    skipped        BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_onboarding_channel
    ON onboarding_responses(channel);
CREATE INDEX IF NOT EXISTS idx_onboarding_created
    ON onboarding_responses(created_at DESC);

CREATE TABLE IF NOT EXISTS plan_intents (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    email       TEXT,
    plan        TEXT NOT NULL,
    billing     TEXT NOT NULL DEFAULT 'monthly',
    price_usd   NUMERIC(8,2),
    device_type TEXT,
    source      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_plan_intents_created
    ON plan_intents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_plan_intents_user ON plan_intents(user_id);

CREATE TABLE IF NOT EXISTS google_auth_codes (
    code       TEXT PRIMARY KEY,
    token      TEXT NOT NULL,
    plan       TEXT DEFAULT 'free',
    email      TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
