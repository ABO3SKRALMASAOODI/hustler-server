-- Passwordless challenges are separate from legacy password-reset codes.
-- No plaintext login codes or raw network addresses are retained.
CREATE TABLE IF NOT EXISTS email_signin_challenges (
 id TEXT PRIMARY KEY,
 email TEXT NOT NULL,
 code_hash TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '5 minutes',
 attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
 sent BOOLEAN NOT NULL DEFAULT FALSE,
 consumed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS email_signin_email_time ON email_signin_challenges(email, created_at);
CREATE INDEX IF NOT EXISTS email_signin_created ON email_signin_challenges(created_at);
