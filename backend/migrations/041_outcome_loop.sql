-- 041: the outcome loop (owner design, 25 Oct 2026). Additive only.
--
-- 040 is reserved for the Claude trial budget on branch claude/claude-trial-1025
-- (strategy-1025 DECISIONS.md, track D), which is on hold and not applied.
--
-- The JUDGING and the FIXING are done by Claude Code sessions (Opus) on the
-- owner's Mac, never by the product's API keys and never by a backend model:
--   * the Reviewer (hourly) reads every settled edit session of a real user
--     with `backend/scripts/ops_review.py pending`, looks at the delivered
--     render frame by frame, and writes ONE session_reviews row per session
--     with picture evidence (images under ops/evidence/<session>/ in R2);
--   * each distinct problem is ONE tickets row (deduplicated by cause_key, so
--     a user's retry loop and the same cause in other accounts fold into one
--     ticket); ticket_reviews links it to the sessions that show it;
--   * the Fixer (daily 03:00 +04:00, or at once on a P0) fixes the cause and
--     closes the ticket with root_cause, fix_sha and regression_check; a closed
--     cause that comes back reopens with recurrence_count + 1.
-- ops_review.py is the agents' ONLY production write path, and it writes only
-- these four tables. ops_review_keys holds the SHA-256 of short-lived keys the
-- CLI mints so the backend can presign source/render downloads and store
-- evidence images for it (the Mac holds no storage credentials).
--
-- Apply (owner-authorised task, recorded in schema_migrations):
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/041_outcome_loop.sql
--   psql "$DATABASE_URL" -c "INSERT INTO schema_migrations (name) VALUES ('041_outcome_loop.sql') ON CONFLICT DO NOTHING"

BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS session_reviews (
    id               SERIAL PRIMARY KEY,
    -- p<project>-<window start epoch>: stable for one settled session.
    session_key      TEXT        NOT NULL UNIQUE,
    surface          TEXT        NOT NULL
                     CHECK (surface IN ('studio', 'shorts', 'mcp')),
    user_id          INTEGER     NOT NULL,
    project_id       INTEGER,
    -- chat message ids of the request and follow-ups, and the job ids.
    request_ids      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    started_at       TIMESTAMPTZ NOT NULL,
    settled_at       TIMESTAMPTZ NOT NULL,
    -- what the session looked like when it was judged; a new export, a
    -- rating or a follow-up changes it and puts the session back in pending.
    fingerprint      TEXT        NOT NULL,
    verdict          TEXT        NOT NULL
                     CHECK (verdict IN ('delivered_good', 'delivered_poor',
                                        'not_delivered')),
    score            SMALLINT    NOT NULL CHECK (score BETWEEN 1 AND 10),
    reasoning        TEXT        NOT NULL,
    root_cause       TEXT,
    request_summary  TEXT,
    -- [{kind, caption, storage_key}], at least one picture.
    evidence         JSONB       NOT NULL DEFAULT '[]'::jsonb
                     CHECK (jsonb_typeof(evidence) = 'array'),
    -- facts at review time: paying, exported, rating, errors, ...
    signals          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    reviewer         TEXT        NOT NULL DEFAULT 'claude-opus',
    review_count     INTEGER     NOT NULL DEFAULT 1,
    reviewed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_reviews_reviewed
    ON session_reviews (reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_reviews_started
    ON session_reviews (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_reviews_user
    ON session_reviews (user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS tickets (
    id                SERIAL PRIMARY KEY,
    -- a short slug naming the cause (e.g. index-fails-on-hevc-10bit); one
    -- ticket per cause, whatever the number of sessions or accounts.
    cause_key         TEXT        NOT NULL UNIQUE,
    title             TEXT        NOT NULL,
    -- P0: a paying or trial user is blocked right now, or an outage.
    severity          TEXT        NOT NULL
                      CHECK (severity IN ('P0', 'P1', 'P2')),
    status            TEXT        NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'fixing', 'fixed',
                                        'wont_fix')),
    summary           TEXT,
    root_cause        TEXT,
    fix_sha           TEXT,
    -- the test or journey that now guards the cause.
    regression_check  TEXT,
    wont_fix_reason   TEXT,
    recurrence_count  INTEGER     NOT NULL DEFAULT 0,
    occurrences       INTEGER     NOT NULL DEFAULT 0,
    first_seen        TIMESTAMPTZ NOT NULL,
    last_seen         TIMESTAMPTZ NOT NULL,
    closed_at         TIMESTAMPTZ,
    history           JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT tickets_fixed_has_proof
        CHECK (status <> 'fixed' OR (COALESCE(root_cause, '') <> ''
                                     AND COALESCE(fix_sha, '') <> ''
                                     AND COALESCE(regression_check, '') <> '')),
    CONSTRAINT tickets_wont_fix_has_reason
        CHECK (status <> 'wont_fix' OR COALESCE(wont_fix_reason, '') <> '')
);

CREATE INDEX IF NOT EXISTS idx_tickets_status
    ON tickets (status, last_seen DESC);

CREATE TABLE IF NOT EXISTS ticket_reviews (
    ticket_id    INTEGER     NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    review_id    INTEGER     NOT NULL
                 REFERENCES session_reviews(id) ON DELETE CASCADE,
    attached_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticket_id, review_id)
);

CREATE INDEX IF NOT EXISTS idx_ticket_reviews_review
    ON ticket_reviews (review_id);

CREATE TABLE IF NOT EXISTS ops_review_keys (
    id          SERIAL PRIMARY KEY,
    key_sha256  TEXT        NOT NULL UNIQUE,
    label       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL
);

COMMIT;
