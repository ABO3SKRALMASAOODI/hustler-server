"""Who has to pick a plan before they can use the editor.

ROUND 50 — THE GATE IS THE FREE CREDITS RUNNING OUT, AND NOTHING ELSE.

Every account, old or new, holds `credits.FREE_GRANT_CREDITS` (50) it can
actually spend on real agent turns. While there is a credit left, there is no
gate: the visitor edits their own footage, watches the preview come back, and
decides what this product is worth from the thing itself. The moment the pool
is empty the gate closes and the ask is the 3-day trial.

That replaces two earlier rules, both of which are now gone:

  * GATE_START / `created_at` — round 45 gated only accounts created after the
    deploy so it would not retroactively lock out people mid-edit on the old
    free allowance. Everybody now holds the same 50 credits (existing rows were
    topped up by migrations/010_free_taste_credits.sql), so the cutoff no
    longer separates two groups that are treated differently — it only
    separated two groups that got a DIFFERENT ERROR MESSAGE for the same wall,
    which is drift, not policy.

  * FREE_TASTE_TURNS — a turn counter. Counting turns prices a 6-second clip
    the same as a 20-minute documentary; counting credits prices what each turn
    actually costs us. Same idea, honest denominator.

A trialling user IS a subscribed user — Paddle creates the subscription at
checkout and only charges on day 3 — so `is_subscribed` passes the gate and a
spent trial is a different wall with its own message (see routes/video.py).

WHAT THIS GATE IS NOT: the credit check. `credits.check_and_reserve` still runs
below it in routes/video.py and covers subscribers who have spent their pool.
This one answers "does this account have a plan-shaped reason to be refused",
so the studio can answer with the trial card rather than a refresh notice.
"""

import credits

# What the frontend and the API both say when the gate closes. One string, so
# the 402 body and the pricing page cannot drift apart.
GATE_CODE = "plan_required"
GATE_MESSAGE = ("That's your free credits used up. Start your 3-day free "
                "trial to keep editing — cancel inside the trial and you're "
                "not charged.")


def _row_get(row, key, index):
    """Works with RealDictCursor rows and plain tuples alike — this module is
    called from routes that use both."""
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        try:
            return row[index]
        except (KeyError, TypeError, IndexError):
            return None


def needs_plan(conn, user_id):
    """True when this user has spent the free taste and holds no subscription.

    Reads `credits_balance` directly rather than going through
    `credits.get_balance`: that function takes write locks to roll the daily
    pool over, and this is called on a status poll as well as on the send path.
    The column is authoritative for a free account — nothing refills it, and
    every deduction rewrites it in the same transaction.

    Fails OPEN on any error: a database hiccup must never lock a paying
    customer out of their own project. The worst case of an open failure is one
    free agent turn; the worst case of a closed one is a customer who cannot
    reach work they already paid for.
    """
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_subscribed, credits_balance FROM users "
                    "WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
        cur.close()
    except Exception as e:                                  # pragma: no cover
        print(f"[plan_gate] lookup failed for user {user_id}: {e}", flush=True)
        return False
    if row is None:
        return False
    if _row_get(row, "is_subscribed", 0):
        return False
    try:
        balance = float(_row_get(row, "credits_balance", 1) or 0)
    except (TypeError, ValueError):                         # pragma: no cover
        return False
    # Less than a whole credit cannot fund a turn (min_credits=1.0 everywhere
    # that spends), so that is the wall — not zero, which a fractional
    # remainder would never quite reach.
    return balance < 1.0


def free_state(conn, user_id):
    """{grant, remaining} — what the studio shows so the wall is never a
    surprise. Safe for any user; a subscriber's remaining is None because the
    free taste does not describe their balance."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_subscribed, credits_balance FROM users "
                    "WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
        cur.close()
    except Exception:                                       # pragma: no cover
        return {"grant": credits.FREE_GRANT_CREDITS, "remaining": None}
    if row is None or _row_get(row, "is_subscribed", 0):
        return {"grant": credits.FREE_GRANT_CREDITS, "remaining": None}
    try:
        remaining = float(_row_get(row, "credits_balance", 1) or 0)
    except (TypeError, ValueError):                         # pragma: no cover
        remaining = None
    return {"grant": credits.FREE_GRANT_CREDITS, "remaining": remaining}


def gate_response(jsonify):
    """The 402 body every gated route returns. `jsonify` is passed in so this
    module stays importable without a Flask app context.

    `free_credits` rides along so the studio can say "that's your 50" without
    hardcoding 50 in the frontend — the grant is defined once, in credits.py.
    """
    return jsonify({"error": GATE_MESSAGE, "code": GATE_CODE,
                    "plan_required": True,
                    "free_credits": credits.FREE_GRANT_CREDITS}), 402
