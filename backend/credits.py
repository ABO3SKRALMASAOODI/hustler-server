"""
Credits logic — cost-based pricing.

Anthropic Claude Sonnet pricing (per million tokens):
  Input:        $3.00
  Output:       $15.00
  Cache write:  $3.75
  Cache read:   $0.30

1 credit = $0.01
Free users  : 20 credits / day  (~2-3 generations)
Subscribed  : 20 credits / day  + monthly balance from plan
"""

import datetime
import math

# ── Pricing ──────────────────────────────────────────────────────────────────

INPUT_COST_PER_M        = 3.00
OUTPUT_COST_PER_M       = 15.00
CACHE_WRITE_COST_PER_M  = 3.75
CACHE_READ_COST_PER_M   = 0.30

DOLLARS_PER_CREDIT  = 0.01   # 1 credit = $0.01
FREE_DAILY_CREDITS  = 20
SUB_DAILY_CREDITS   = 20


# ── Core conversion ──────────────────────────────────────────────────────────

def tokens_to_credits(input_tokens, output_tokens, cache_write_tokens, cache_read_tokens):
    """
    Convert token counts to credits using real Anthropic pricing.
    Cache reads are 90% cheaper than input — this rewards users fairly.
    """
    cost_dollars = (
        (input_tokens       * INPUT_COST_PER_M)       +
        (output_tokens      * OUTPUT_COST_PER_M)      +
        (cache_write_tokens * CACHE_WRITE_COST_PER_M) +
        (cache_read_tokens  * CACHE_READ_COST_PER_M)
    ) / 1_000_000

    credits = cost_dollars / DOLLARS_PER_CREDIT
    return round(credits, 2)


# ── Daily refresh ─────────────────────────────────────────────────────────────

def refresh_daily_credits(conn, user_id: int, is_subscribed: bool):
    """
    Top up daily credits if reset date is before today.
    Call at the start of each request. Returns current balance.
    """
    today = datetime.date.today()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT credits_balance, credits_daily_reset FROM users WHERE id = %s FOR UPDATE",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return 0

        balance    = row["credits_balance"]
        reset_date = row["credits_daily_reset"]

        if isinstance(reset_date, str):
            reset_date = datetime.date.fromisoformat(reset_date)

        if reset_date < today:
            daily   = SUB_DAILY_CREDITS if is_subscribed else FREE_DAILY_CREDITS
            balance += daily
            cur.execute(
                "UPDATE users SET credits_balance = %s, credits_daily_reset = %s WHERE id = %s",
                (balance, today, user_id)
            )
            conn.commit()

    return balance


# ── Get balance ───────────────────────────────────────────────────────────────

def get_balance(conn, user_id: int) -> dict:
    """Return current credits balance, triggering daily refresh if needed."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT credits_balance, is_subscribed FROM users WHERE id = %s",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return {"balance": 0, "is_subscribed": False}

    is_subscribed = bool(row["is_subscribed"])
    balance       = refresh_daily_credits(conn, user_id, is_subscribed)
    return {"balance": balance, "is_subscribed": is_subscribed}


# ── Check before job ──────────────────────────────────────────────────────────

def check_and_reserve(conn, user_id: int, min_credits: float = 1.0) -> bool:
    """
    Returns True if user has enough credits to start a job.
    Also triggers daily refresh.
    """
    info = get_balance(conn, int(user_id))
    return info["balance"] >= min_credits


# ── Deduct after job ──────────────────────────────────────────────────────────

def deduct_credits(conn, user_id: int, job_id: str, turn: int, tokens_used: int,
                   input_tokens: int = 0, output_tokens: int = 0,
                   cache_write_tokens: int = 0, cache_read_tokens: int = 0):
    """
    Deduct cost-based credits after a completed turn.
    Falls back to raw token count if breakdown not provided.
    """
    if input_tokens or output_tokens or cache_write_tokens or cache_read_tokens:
        credits_used = tokens_to_credits(
            input_tokens, output_tokens, cache_write_tokens, cache_read_tokens
        )
    else:
        # Fallback: treat all as input tokens
        credits_used = tokens_to_credits(tokens_used, 0, 0, 0)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET credits_balance = GREATEST(0, credits_balance - %s) WHERE id = %s",
            (credits_used, user_id)
        )
        cur.execute(
            """
            INSERT INTO job_credits (job_id, user_id, turn, tokens_used, credits_used)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (job_id, user_id, turn, tokens_used, credits_used)
        )
        conn.commit()

    return credits_used


# ── Per-job breakdown ─────────────────────────────────────────────────────────

def get_job_credits(conn, job_id: str) -> list:
    """Return per-turn credit usage for a job (used by the ··· tooltip)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT turn, tokens_used, credits_used
            FROM job_credits
            WHERE job_id = %s
            ORDER BY turn ASC
            """,
            (job_id,)
        )
        rows = cur.fetchall()

    return [
        {
            "turn":         r["turn"],
            "tokens_used":  r["tokens_used"],
            "credits_used": float(r["credits_used"]),
        }
        for r in rows
    ]