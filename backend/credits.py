"""
Credits logic — per-model cost-based pricing.

Model tiers:
  HB-6      = Claude Haiku 4.5    (Free+ plans)
  HB-6 Pro  = Claude Sonnet 4.6   (Plus/Pro+ plans)
  HB-7      = Claude Opus 4.6     (Ultra/Titan/Ace plans)

Anthropic pricing (per million tokens):
                    Input   Output  Cache Write  Cache Read
  Haiku 4.5         $1.00   $5.00   $1.25        $0.10
  Sonnet 4.6        $3.00   $15.00  $3.75        $0.30
  Opus 4.6          $5.00   $25.00  $6.25        $0.50

1 credit = $0.01

Margin comes entirely from selling credit bundles at a premium price.
No markup is applied here — credits reflect actual API cost.

Free users   : 20 credits / day (resets daily, never accumulates) + 80 one-time bonus
Subscribed   : 20 credits / day (resets daily) + monthly pool from plan + remaining bonus
Spend order  : daily first → bonus → monthly pool
Monthly pool : wiped and refreshed on each billing cycle (handled by webhook)
Bonus pool   : one-time 80 credits on registration, never refills
"""

import datetime
import math

# ── Per-model pricing (per million tokens) ────────────────────────────────────

MODEL_PRICING = {
    "hb-6": {
        "anthropic_model": "claude-haiku-4-5-20251001",
        "input":       1.00,
        "output":      5.00,
        "cache_write": 1.25,
        "cache_read":  0.10,
    },
    "hb-6-pro": {
        "anthropic_model": "claude-sonnet-4-6",
        "input":       3.00,
        "output":      15.00,
        "cache_write": 3.75,
        "cache_read":  0.30,
    },
    "hb-7": {
        "anthropic_model": "claude-opus-4-6",
        "input":       5.00,
        "output":      25.00,
        "cache_write": 6.25,
        "cache_read":  0.50,
    },
}

# Fallback to Sonnet pricing if model not recognized
DEFAULT_MODEL = "hb-6-pro"

# ── Plan → allowed models ─────────────────────────────────────────────────────

PLAN_MODELS = {
    "free":  ["hb-6"],
    "plus":  ["hb-6", "hb-6-pro"],
    "pro":   ["hb-6", "hb-6-pro"],
    "ultra": ["hb-6", "hb-6-pro", "hb-7"],
    "titan": ["hb-6", "hb-6-pro", "hb-7"],
    "ace":   ["hb-6", "hb-6-pro", "hb-7"],
}

# ── Plan → monthly credit limits ──────────────────────────────────────────────

PLAN_MONTHLY_LIMITS = {
    "free":  0,
    "plus":  1000,
    "pro":   2400,
    "ultra": 5000,
    "titan": 10000,
    "ace":   25000,
}

DOLLARS_PER_CREDIT  = 0.01
MARKUP              = 1.0   # No markup — margin comes from bundle pricing
FREE_DAILY_CREDITS  = 20
SUB_DAILY_CREDITS   = 20
INITIAL_BONUS       = 80    # One-time bonus on registration

# ── Core conversion (model-aware) ─────────────────────────────────────────────

def tokens_to_credits(input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, model="hb-6-pro"):
    """Convert token usage to credits based on the model's pricing."""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])

    cost_dollars = (
        (input_tokens       * pricing["input"])       +
        (output_tokens      * pricing["output"])      +
        (cache_write_tokens * pricing["cache_write"]) +
        (cache_read_tokens  * pricing["cache_read"])
    ) / 1_000_000

    cost_dollars *= MARKUP
    return round(cost_dollars / DOLLARS_PER_CREDIT, 2)


def get_anthropic_model(hb_model):
    """Convert HB model name to Anthropic API model string."""
    pricing = MODEL_PRICING.get(hb_model, MODEL_PRICING[DEFAULT_MODEL])
    return pricing["anthropic_model"]


def is_model_allowed(plan, hb_model):
    """Check if a plan allows access to a given model."""
    allowed = PLAN_MODELS.get(plan, PLAN_MODELS["free"])
    return hb_model in allowed


# ── Daily refresh ─────────────────────────────────────────────────────────────

def refresh_daily_credits(conn, user_id: int, is_subscribed: bool):
    """
    Every day: reset daily_credits to 20 (never accumulates).
    Monthly pool is untouched here — managed by webhook only.
    Bonus pool is untouched — one-time only.
    Combined balance shown to user = daily + bonus + monthly.

    Only hits the DB with a write when the date has actually changed,
    avoiding unnecessary FOR UPDATE locks on every status poll.
    """
    today = datetime.date.today()

    with conn.cursor() as cur:
        cur.execute(
            """SELECT credits_daily, credits_daily_reset, credits_monthly, credits_bonus
               FROM users WHERE id = %s""",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return 0

        daily      = float(row["credits_daily"]) if row.get("credits_daily") is not None else 20.0
        reset_date = row["credits_daily_reset"]
        monthly    = float(row["credits_monthly"] or 0)
        bonus      = float(row.get("credits_bonus") or 0)

        if isinstance(reset_date, str):
            reset_date = datetime.date.fromisoformat(reset_date)

        if reset_date is None or reset_date < today:
            daily = SUB_DAILY_CREDITS if is_subscribed else FREE_DAILY_CREDITS
            cur.execute(
                """UPDATE users
                   SET credits_daily = %s,
                       credits_daily_reset = %s,
                       credits_balance = %s + credits_bonus + credits_monthly
                   WHERE id = %s""",
                (daily, today, daily, user_id)
            )
            conn.commit()
        else:
            cur.execute(
                "UPDATE users SET credits_balance = %s + credits_bonus + credits_monthly WHERE id = %s",
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
            "SELECT credits_balance, is_subscribed, plan FROM users WHERE id = %s",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            return {"balance": 0, "is_subscribed": False, "plan": "free", "plan_limit": 20}

    is_subscribed = bool(row["is_subscribed"])
    plan = row.get("plan") or "free"
    balance = refresh_daily_credits(conn, user_id, is_subscribed)

    # Total plan limit = daily credits + bonus + monthly pool
    monthly = PLAN_MONTHLY_LIMITS.get(plan, 0)
    daily = SUB_DAILY_CREDITS if is_subscribed else FREE_DAILY_CREDITS
    plan_limit = daily + INITIAL_BONUS + monthly

    return {
        "balance": balance,
        "is_subscribed": is_subscribed,
        "plan": plan,
        "plan_limit": plan_limit,
    }


# ── Check before job ──────────────────────────────────────────────────────────

def check_and_reserve(conn, user_id: int, min_credits: float = 1.0) -> bool:
    info = get_balance(conn, int(user_id))
    return info["balance"] >= min_credits


# ── Concurrency check ─────────────────────────────────────────────────────────

def count_running_jobs(conn, user_id: int, stale_minutes: int = 15) -> int:
    """
    Return how many jobs this user currently has in 'running' state.
    Auto-expires jobs that have been stuck in 'running' for longer than
    stale_minutes (default 15) — handles crashes, API failures, etc.
    """
    with conn.cursor() as cur:
        # First: mark any stale running jobs as failed
        cur.execute(
            """
            UPDATE jobs
            SET state = 'failed'
            WHERE user_id = %s
              AND state = 'running'
              AND updated_at < NOW() - INTERVAL '%s minutes'
            """,
            (user_id, stale_minutes)
        )
        conn.commit()

        # Then count what's genuinely still running
        cur.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE user_id = %s AND state = 'running'",
            (user_id,)
        )
        row = cur.fetchone()
        return row["cnt"] if row else 0

# ── Deduct after job ──────────────────────────────────────────────────────────

def deduct_credits(conn, user_id: int, job_id: str, turn: int, tokens_used: int,
                   input_tokens: int = 0, output_tokens: int = 0,
                   cache_write_tokens: int = 0, cache_read_tokens: int = 0,
                   model: str = "hb-6-pro"):
    if input_tokens or output_tokens or cache_write_tokens or cache_read_tokens:
        credits_used = tokens_to_credits(input_tokens, output_tokens,
                                         cache_write_tokens, cache_read_tokens,
                                         model=model)
    else:
        credits_used = tokens_to_credits(tokens_used, 0, 0, 0, model=model)

    with conn.cursor() as cur:
        # Deduct in order: daily → bonus → monthly
        cur.execute(
            "SELECT credits_daily, credits_bonus, credits_monthly FROM users WHERE id = %s FOR UPDATE",
            (user_id,)
        )
        row = cur.fetchone()
        daily   = float(row["credits_daily"] or 0)
        bonus   = float(row.get("credits_bonus") or 0)
        monthly = float(row["credits_monthly"] or 0)

        remaining = credits_used

        # 1. Deduct from daily first
        if daily >= remaining:
            daily -= remaining
            remaining = 0
        else:
            remaining -= daily
            daily = 0

        # 2. Then from bonus
        if remaining > 0:
            if bonus >= remaining:
                bonus -= remaining
                remaining = 0
            else:
                remaining -= bonus
                bonus = 0

        # 3. Then from monthly
        if remaining > 0:
            monthly = max(0, monthly - remaining)

        cur.execute(
            """UPDATE users
               SET credits_daily = %s,
                   credits_bonus = %s,
                   credits_monthly = %s,
                   credits_balance = %s + %s + %s
               WHERE id = %s""",
            (daily, bonus, monthly, daily, bonus, monthly, user_id)
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