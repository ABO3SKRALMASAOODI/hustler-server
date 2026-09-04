import psycopg2
from flask import current_app, g


def get_db():
    """Get a database connection, reuse if already exists in g."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = psycopg2.connect(current_app.config['DATABASE_URL'])
    return db


def close_db(_error=None):
    """Roll back unfinished request work and release the cached connection."""
    db = g.pop('_database', None)
    if db is None:
        return
    try:
        # Commits in this module are explicit. Anything still pending when the
        # request/app context ends is necessarily partial work and must not
        # depend on interpreter finalization for its rollback.
        db.rollback()
    except Exception:
        pass
    finally:
        db.close()


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
    subscription facts. The grant here is a SET rather than an add. The caller
    preserves on repeated subscription lifecycle events and resets only when a
    specific positive Paddle transaction first crosses into paid/completed, so
    webhook retries cannot refill a pool the user has already spent.
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
