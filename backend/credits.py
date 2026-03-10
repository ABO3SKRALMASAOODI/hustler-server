"""
Credits logic — cost-based pricing.

Anthropic Claude Sonnet pricing (per million tokens):
  Input:        $3.00
  Output:       $15.00
  Cache write:  $3.75
  Cache read:   $0.30

1 credit = $0.01

Margin comes entirely from selling credit bundles at a premium price.
No markup is applied here — credits reflect actual API cost.

Free users   : 20 credits / day (resets daily, never accumulates)
Subscribed   : 20 credits / day (resets daily) + monthly pool from plan
Spend order  : daily first, then monthly pool
Monthly pool : wiped and refreshed on each billing cycle (handled by webhook)
"""

import datetime
import math

# ── Pricing ───────────────────────────────────────────────────────────────────
INPUT_COST_PER_M        = 3.00
OUTPUT_COST_PER_M       = 15.00
CACHE_WRITE_COST_PER_M  = 3.75
CACHE_READ_COST_PER_M   = 0.30

DOLLARS_PER_CREDIT  = 0.01
MARKUP              = 1.0   # No markup — margin comes from bundle pricing
FREE_DAILY_CREDITS  = 20
SUB_DAILY_CREDITS   = 20

# ── Core conversion ───────────────────────────────────────────────────────────
def tokens_to_credits(input_tokens, output_tokens, cache_write_tokens, cache_read_tokens):
    cost_dollars = (
        (input_tokens       * INPUT_COST_PER_M)       +
        (output_tokens      * OUTPUT_COST_PER_M)      +
        (cache_write_tokens * CACHE_WRITE_COST_PER_M) +
        (cache_read_tokens  * CACHE_READ_COST_PER_M)
    ) / 1_000_000
    cost_dollars *= MARKUP
    return round(cost_dollars / DOLLARS_PER_CREDIT, 2)

# ── Daily refresh ─────────────────────────────────────────────────────────────
def refresh_daily_credits(conn, user_id: int, is_subscribed: bool):
    """
    Every day: reset daily_credits to 20 (never accumulates).
    Monthly pool is untouched here — managed by webhook only.
    Combined balance shown to user = daily_credits + monthly_pool.

    Only hits the DB with a write when the date has actually changed,
    avoiding unnecessary FOR UPDATE locks on every status poll.
    """
    today = datetime.date.today()

    with conn.cursor() as cur:
        cur.execute(
            """SELECT credits_daily, credits_daily_reset, credits_monthly
               FROM users WHERE id = %s""",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return 0

        daily      = row["credits_daily"] if row.get("credits_daily") is not None else 20
        reset_date = row["credits_daily_reset"]
        monthly    = row["credits_monthly"] or 0

        if isinstance(reset_date, str):
            reset_date = datetime.date.fromisoformat(reset_date)

        if reset_date is None or reset_date < today:
            # Reset daily to full — never accumulate
            daily = SUB_DAILY_CREDITS if is_subscribed else FREE_DAILY_CREDITS
            cur.execute(
                """UPDATE users
                   SET credits_daily = %s,
                       credits_daily_reset = %s,
                       credits_balance = %s + credits_monthly
                   WHERE id = %s""",
                (daily, today, daily, user_id)
            )
            conn.commit()
        else:
            # Keep credits_balance in sync without a write lock
            cur.execute(
                "UPDATE users SET credits_balance = %s + credits_monthly WHERE id = %s",
                (daily, user_id)
            )
            conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT credits_balance FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return row["credits_balance"] if row else 0

# ── Get balance ───────────────────────────────────────────────────────────────
def get_balance(conn, user_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT credits_balance, is_subscribed FROM users WHERE id = %s",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return {"balance": 0, "is_subscribed": False}

    is_subscribed = bool(row["is_subscribed"])
    balance = refresh_daily_credits(conn, user_id, is_subscribed)
    return {"balance": balance, "is_subscribed": is_subscribed}

# ── Check before job ──────────────────────────────────────────────────────────
def check_and_reserve(conn, user_id: int, min_credits: float = 1.0) -> bool:
    info = get_balance(conn, int(user_id))
    return info["balance"] >= min_credits

# ── Concurrency check ─────────────────────────────────────────────────────────
def count_running_jobs(conn, user_id: int) -> int:
    """Return how many jobs this user currently has in 'running' state."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE user_id = %s AND state = 'running'",
            (user_id,)
        )
        row = cur.fetchone()
    return row["cnt"] if row else 0

# ── Deduct after job ──────────────────────────────────────────────────────────
def deduct_credits(conn, user_id: int, job_id: str, turn: int, tokens_used: int,
                   input_tokens: int = 0, output_tokens: int = 0,
                   cache_write_tokens: int = 0, cache_read_tokens: int = 0):
    if input_tokens or output_tokens or cache_write_tokens or cache_read_tokens:
        credits_used = tokens_to_credits(input_tokens, output_tokens, cache_write_tokens, cache_read_tokens)
    else:
        credits_used = tokens_to_credits(tokens_used, 0, 0, 0)

    with conn.cursor() as cur:
        # Deduct from daily first, then monthly
        cur.execute(
            "SELECT credits_daily, credits_monthly FROM users WHERE id = %s FOR UPDATE",
            (user_id,)
        )
        row = cur.fetchone()
        daily   = row["credits_daily"] or 0
        monthly = row["credits_monthly"] or 0

        remaining = credits_used
        if daily >= remaining:
            daily -= remaining
            remaining = 0
        else:
            remaining -= daily
            daily = 0
            monthly = max(0, monthly - remaining)

        cur.execute(
            """UPDATE users
               SET credits_daily = %s,
                   credits_monthly = %s,
                   credits_balance = %s + %s
               WHERE id = %s""",
            (daily, monthly, daily, monthly, user_id)
        )
        cur.execute(
            """INSERT INTO job_credits (job_id, user_id, turn, tokens_used, credits_used)
               VALUES (%s, %s, %s, %s, %s)""",
            (job_id, user_id, turn, tokens_used, credits_used)
        )
        conn.commit()

    return credits_used

# ── Per-job breakdown ─────────────────────────────────────────────────────────
def get_job_credits(conn, job_id: str) -> list:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT turn, tokens_used, credits_used FROM job_credits
               WHERE job_id = %s ORDER BY turn ASC""",
            (job_id,)
        )
        rows = cur.fetchall()
    return [{"turn": r["turn"], "tokens_used": r["tokens_used"], "credits_used": float(r["credits_used"])} for r in rows]