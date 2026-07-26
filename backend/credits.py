"""
Credits logic — per-model cost-based pricing.

Model tiers:
  V6      = Claude Haiku 4.5    (Free+ plans)
  V6 Pro  = Claude Sonnet 4.6   (Plus/Pro+ plans)
  V7      = Claude Opus 4.6     (Ultra/Titan/Ace plans)

Anthropic pricing (per million tokens):
                    Input   Output  Cache Write  Cache Read
  Haiku 4.5         $1.00   $5.00   $1.25        $0.10
  Sonnet 4.6        $3.00   $15.00  $3.75        $0.30
  Opus 4.6          $5.00   $25.00  $6.25        $0.50

1 credit = $0.005 of real model cost (model_prices.USD_PER_CREDIT), i.e. a
credit is spent at TWICE what the model costs us. That factor IS the margin —
see the plan table in routes/paddle.py. It was 1:1 with cost until round 49,
which left the annual tiers at 28-40% and made an intro discount on an annual
plan a below-cost sale.

Free users   : 20 credits / day (resets daily, never accumulates) + 80 one-time bonus
Subscribed   : 20 credits / day (resets daily) + monthly pool from plan + remaining bonus
Spend order  : daily first → bonus → monthly pool
Monthly pool : wiped and refreshed on each billing cycle (handled by webhook)
Bonus pool   : one-time 80 credits on registration, never refills
"""

import datetime
import math

# A credit costs the user twice what the model costs us (round 49). Imported
# rather than restated so the legacy app-builder lane and the video lane cannot
# drift into meaning two different things by the word "credit" — the worker
# charges video turns through this same constant (worker/db.charge_turn_credits,
# via the byte-identical worker/model_prices.py).
from model_prices import USD_PER_CREDIT

# ── Per-model pricing (per million tokens) ────────────────────────────────────

MODEL_PRICING = {
    "V6": {
        "anthropic_model": "claude-haiku-4-5-20251001",
        "input":       1.00,
        "output":      5.00,
        "cache_write": 1.25,
        "cache_read":  0.10,
    },
    "V6-pro": {
        "anthropic_model": "claude-sonnet-4-6",
        "input":       3.00,
        "output":      15.00,
        "cache_write": 3.75,
        "cache_read":  0.30,
    },
    "V7": {
        "anthropic_model": "claude-opus-4-6",
        "input":       5.00,
        "output":      25.00,
        "cache_write": 6.25,
        "cache_read":  0.50,
    },
}

# Fallback to Sonnet pricing if model not recognized
DEFAULT_MODEL = "V6-pro"

# ── Plan → allowed models ─────────────────────────────────────────────────────

PLAN_MODELS = {
    "free":  ["V6"],
    "plus":  ["V6", "V6-pro"],
    "pro":   ["V6", "V6-pro"],
    "ultra": ["V6", "V6-pro", "V7"],
    "titan": ["V6", "V6-pro", "V7"],
    "ace":   ["V6", "V6-pro", "V7"],
}

# ── Plan → monthly credit limits ──────────────────────────────────────────────

# Keep in sync with routes/paddle_webhook.py PLAN_CREDITS — the webhook is
# what actually grants the monthly pool on renewal.
PLAN_MONTHLY_LIMITS = {
    "free":  0,
    # The three live plans. Keep in step with PLAN_CREDITS in paddle_webhook.py
    # — that one GRANTS the credits, this one is the denominator the studio
    # shows. They were missing here, so an 'ai' subscriber paying $30 would have
    # seen a limit of 20 (the daily top-up alone) even while holding 2,400.
    "ai":     2000,     # Creator  $30 — round 49, see the table in paddle.py
    "ai_pro": 4000,     # Pro      $50
    "ai_max": 10000,    # Frontier $100
    # 'mcp' is 0 on purpose: that plan brings its own model, so it never draws
    # on our metered pool.
    "mcp":   0,
    "plus":  800,
    "pro":   2400,
    "ultra": 5000,
    "titan": 10000,
    "ace":   30000,
}

DOLLARS_PER_CREDIT  = USD_PER_CREDIT
MARKUP              = 1.0   # No markup — the margin is in the burn rate above

# ── The trial allowance ───────────────────────────────────────────────────────
#
# A trial is three days of the real product, not three days of the whole
# month's pool. Before this, a trialling account was granted the FULL monthly
# grant and could burn every credit of it before Paddle had charged a cent —
# $50 of model spend on a Frontier trial somebody then cancels.
#
# So a trial grants TRIAL_CREDIT_FRACTION of the plan, and conversion to paid
# is what releases the rest (backend/routes/paddle_webhook.py grants the full
# figure the moment Paddle reports status 'active'). 10% is still a genuinely
# usable window — 200 credits on Creator is several complete edits at the
# measured cost of a turn — and it is spent at the plan's own model, so what
# they are trying is what they would buy.
TRIAL_CREDIT_FRACTION = 0.10


def trial_allowance(plan):
    """Credits a trialling account on `plan` may spend before it converts.

    Rounded UP so no plan can ever land on a 0-credit trial through a rounding
    edge, and floored at 1 for the same reason: a trial that cannot run a
    single turn is worse than no trial, because the user finds out only after
    entering a card."""
    full = PLAN_MONTHLY_LIMITS.get(plan, 0)
    if full <= 0:
        return 0
    return max(1, int(math.ceil(full * TRIAL_CREDIT_FRACTION)))

# THERE IS NO FREE CREDIT ALLOWANCE (round 49).
#
# It was 120, granted once at registration. That made sense while a free
# account could spend them; it stopped making sense the moment editing required
# a plan (FREE_TASTE_TURNS = 0, see backend/plan_gate.py). What was left was a
# credit bar sitting at 120 / 120, full and green, on an account that could not
# spend a single one of them — a number that promises something the product
# then refuses. A founder hit exactly that on his own free account.
#
# What a free account gets instead is the part that costs us almost nothing and
# demonstrates the most: unlimited uploads, a full index of every video, the
# transcript, and the concierge answering questions about it. The wall is the
# agent turn, and the number attached to it is the trial's.
#
# Existing free balances were drained in the same change (a data migration, not
# a code path) so no account is left holding a number it cannot use.
FREE_GRANT_CREDITS  = 0
FREE_DAILY_CREDITS  = 0     # free credits do NOT refill — see FREE_GRANT_CREDITS
SUB_DAILY_CREDITS   = 20    # subscribers keep their daily top-up on top of the
                            # monthly pool their plan buys
INITIAL_BONUS       = FREE_GRANT_CREDITS   # legacy name, same number

# ── Core conversion (model-aware) ─────────────────────────────────────────────

def tokens_to_credits(input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, model="V6-pro"):
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


def get_anthropic_model(V_model):
    """Convert V model name to Anthropic API model string."""
    pricing = MODEL_PRICING.get(V_model, MODEL_PRICING[DEFAULT_MODEL])
    return pricing["anthropic_model"]


def is_model_allowed(plan, V_model):
    """Check if a plan allows access to a given model."""
    allowed = PLAN_MODELS.get(plan, PLAN_MODELS["free"])
    return V_model in allowed


# ── Daily refresh ─────────────────────────────────────────────────────────────

def refresh_daily_credits(conn, user_id: int, is_subscribed: bool,
                          daily_top_up: bool = None):
    """
    Every day: reset a SUBSCRIBER's daily pool to 20 (never accumulates).
    Free users are not topped up at all — their allowance is the one-time
    FREE_GRANT_CREDITS in the bonus pool, so the number they see only ever
    goes down. Monthly pool is untouched here — managed by webhook only.
    Combined balance shown to user = daily + bonus + monthly.

    `daily_top_up` separates "is a subscriber" from "gets 20 a day", which
    stopped being the same question when trials were capped at 10% of the plan
    (round 49). A trialling account IS subscribed, so without this it collects
    20 more credits every day of a 3-day trial — 60 on top of a 200-credit cap,
    which is 30% more than the paywall says it is. Defaults to is_subscribed,
    so every existing caller behaves exactly as before.

    Only hits the DB with a write when the date has actually changed,
    avoiding unnecessary FOR UPDATE locks on every status poll.
    """
    if daily_top_up is None:
        daily_top_up = is_subscribed
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

        # NULL means "no daily pool", not "20". Defaulting a missing column to
        # a number hands out credits nobody granted.
        daily      = float(row["credits_daily"] or 0)
        reset_date = row["credits_daily_reset"]
        monthly    = float(row["credits_monthly"] or 0)
        bonus      = float(row.get("credits_bonus") or 0)

        if isinstance(reset_date, str):
            reset_date = datetime.date.fromisoformat(reset_date)

        # A free user's daily pool is never refilled, but the date still has to
        # advance — otherwise this branch re-runs on every poll, taking a write
        # lock each time. Whatever is left in their daily column (legacy users
        # carry up to 20) is theirs to spend; it just never grows back.
        if (reset_date is None or reset_date < today) and not daily_top_up:
            cur.execute(
                "UPDATE users SET credits_daily_reset = %s, "
                "credits_balance = credits_daily + credits_bonus "
                "+ credits_monthly WHERE id = %s",
                (today, user_id)
            )
            conn.commit()
        elif reset_date is None or reset_date < today:
            daily = SUB_DAILY_CREDITS
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

def _trial_status(conn, user_id: int):
    """This account's Paddle-reported trial status, or None.

    Its own query rather than a column on the main SELECT because the trial
    columns are added by hand in the Render shell (see trial_state.py) and this
    function must keep working on a database where they do not exist yet.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT trial_status FROM users WHERE id = %s",
                        (user_id,))
            row = cur.fetchone()
        return (row or {}).get("trial_status")
    except Exception:
        try:
            conn.rollback()     # a missing column poisons the transaction
        except Exception:
            pass
        return None


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
    trialing = is_subscribed and _trial_status(conn, user_id) == "trialing"
    # A trial gets no daily top-up — otherwise three days of 20 lands 30% on
    # top of the 10% cap the paywall quotes.
    balance = refresh_daily_credits(conn, user_id, is_subscribed,
                                    daily_top_up=is_subscribed and not trialing)

    monthly = PLAN_MONTHLY_LIMITS.get(plan, 0)
    if trialing:
        # The denominator during a trial is the trial allowance itself. Showing
        # "x / 2,000" to someone who can only spend 200 of them is the kind of
        # number that reads as a bug the moment the wall arrives.
        plan_limit = trial_allowance(plan)
    elif is_subscribed:
        plan_limit = SUB_DAILY_CREDITS + monthly
    else:
        # 0 since round 49. A free account has no credit allowance at all, so
        # there is no denominator — the studio hides the bar entirely rather
        # than drawing an empty one, which reads as a fault rather than a
        # price. See the note on FREE_GRANT_CREDITS.
        plan_limit = FREE_GRANT_CREDITS

    empty = float(balance or 0) < 1
    return {
        "balance": balance,
        "is_subscribed": is_subscribed,
        "plan": plan,
        "plan_limit": plan_limit,
        "trialing": trialing,
        # The free allowance is spent and does not come back — the studio uses
        # this to offer the trial instead of telling people to wait for a
        # refresh that will never arrive.
        "free_trial_exhausted": (not is_subscribed) and empty,
        # Spent the trial slice. A DIFFERENT wall from both of the above: these
        # users already entered a card, so the answer is not "start a trial"
        # (they are in one) and not "wait for your cycle" (the rest of the plan
        # is released by converting, not by waiting).
        "trial_cap_reached": trialing and empty,
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
                   model: str = "V6-pro"):
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