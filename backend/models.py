import psycopg2
from flask import current_app, g


def get_db():
    """Get a database connection, reuse if already exists in g."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = psycopg2.connect(current_app.config['DATABASE_URL'])
    return db


def upgrade_user_to_premium(user_id, expiry_date=None):
    """Set user as subscribed and optionally set subscription expiry."""
    conn = get_db()
    cursor = conn.cursor()
    if expiry_date:
        cursor.execute('UPDATE users SET is_subscribed = 1, subscription_expiry = %s WHERE id = %s', (expiry_date, user_id))
    else:
        cursor.execute('UPDATE users SET is_subscribed = 1 WHERE id = %s', (user_id,))
    conn.commit()
    cursor.close()


def update_user_subscription_status(user_id, is_subscribed, expiry_date=None, subscription_id=None, plan='free', monthly_credits=0, daily_credits=20, preserve_credits=False):
    """Update user's subscription status, expiry, and ID.

    `daily_credits` is the per-day top-up a subscriber gets on top of their
    monthly pool. It is 0 during a trial: the trial allowance is deliberately a
    fixed slice of the plan (credits.trial_allowance), and a daily top-up would
    quietly add three more days' worth to a three-day trial — roughly a third
    again on top of the cap, which would make the number the paywall quotes
    wrong.

    `preserve_credits` leaves the balance completely alone and updates only the
    subscription facts. The grant here is a SET rather than an add, which is
    what makes repeated Paddle events idempotent — but during a trial that same
    property refills a pool the user has been spending, so a trial could be
    reset indefinitely by ordinary subscription.updated traffic. The caller
    (paddle_webhook._grant_credits) passes True exactly when this event is a
    repeat for a trial that is already running.
    """
    conn = get_db()
    cursor = conn.cursor()
    if is_subscribed and preserve_credits:
        cursor.execute('''
            UPDATE users
            SET is_subscribed = 1, subscription_expiry = %s, subscription_id = %s, plan = %s,
                credits_monthly_limit = %s
            WHERE id = %s
        ''', (expiry_date, subscription_id, plan, monthly_credits, user_id))
    elif is_subscribed:
        cursor.execute('''
            UPDATE users
            SET is_subscribed = 1, subscription_expiry = %s, subscription_id = %s, plan = %s,
                credits_monthly_limit = %s, credits_monthly = %s,
                credits_daily = %s,
                credits_balance = %s + COALESCE(credits_bonus, 0) + %s
            WHERE id = %s
        ''', (expiry_date, subscription_id, plan, monthly_credits, monthly_credits, daily_credits, daily_credits, monthly_credits, user_id))
    else:
        cursor.execute('''
            UPDATE users
            SET is_subscribed = 0, subscription_expiry = NULL, subscription_id = NULL, plan = 'free', credits_monthly_limit = 0
            WHERE id = %s
        ''', (user_id,))
    conn.commit()
    cursor.close()


def get_user_subscription_id(user_id):
    """Get subscription ID for a user."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT subscription_id FROM users WHERE id = %s', (user_id,))
    result = cursor.fetchone()
    cursor.close()
    return result[0] if result else None


def init_db(app):
    """Initialize all database tables."""
    with app.app_context():
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute('''
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
                credits_monthly_limit INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(20) DEFAULT \'free\'')
        cursor.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_monthly_limit INTEGER DEFAULT 0')
        cursor.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_daily INTEGER DEFAULT 20')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS password_reset_codes (
                email TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES chat_sessions(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_codes (
                email TEXT PRIMARY KEY,
                code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS code_request_logs (
                email TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # ── Studio jobs table ──────────────────────────────────────────────
        # Tracks every generation job per user so the sidebar is sourced
        # from Postgres instead of localStorage.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id          SERIAL PRIMARY KEY,
                job_id      VARCHAR(16)  NOT NULL UNIQUE,
                user_id     INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title       TEXT         NOT NULL DEFAULT '',
                state       VARCHAR(20)  NOT NULL DEFAULT 'running',
                preview_url TEXT,
                created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMP    NOT NULL DEFAULT NOW()
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_job_id  ON jobs(job_id)')

        conn.commit()
        cursor.close()