"""Who has to pick a plan before they can use the editor.

Round 45. New accounts now answer the three onboarding questions and then land
on the pricing page: they choose Creator or Pro, the 3-day trial starts, and
only then does the studio open. That is a product decision, not a technical
one, and it needs two properties the frontend alone cannot give it:

  1. IT MUST BE SERVER-ENFORCED. A gate that lives in the studio page is a
     `localStorage.removeItem` away from being no gate at all — and the thing
     it is protecting (an agent turn) costs us real model money per call.
  2. IT MUST NOT TOUCH ANYONE WHO ALREADY SIGNED UP. Every existing account
     was promised a free allowance and is mid-edit on it. Locking them out
     retroactively to sell a plan would be a bait-and-switch on people who are
     already customers-in-waiting.

Both fall out of one rule: the gate applies only to accounts created at or
after GATE_START, and only while they hold no subscription. No new column, no
migration, no backfill — `users.created_at` already records exactly the fact
the rule turns on. (Schema changes here are run by hand through the Render
shell; deriving the gate from a column that already exists means this ships as
one deploy with nothing to run first.)

A trialling user IS a subscribed user — Paddle creates the subscription at
checkout and only charges on day 3 — so `is_subscribed` is the right column
and someone on day one of their trial passes the gate.
"""

import datetime

# Accounts created from this instant on must choose a plan. It is a UTC
# timestamp, not a date, because `users.created_at` is a timestamp and a bare
# date would sweep in everyone who signed up earlier the same day.
#
# Set to the round-45 deploy. Moving it EARLIER retroactively gates people who
# were promised the free allowance; don't, without deciding to do that.
GATE_START = datetime.datetime(2026, 7, 26, 12, 0, 0)

# What the frontend and the API both say when the gate closes. One string, so
# the 402 body and the pricing page cannot drift apart.
GATE_CODE = "plan_required"
GATE_MESSAGE = ("Pick a plan to start your 3-day free trial — it takes a "
                "minute and you can cancel inside the trial without being "
                "charged.")


def _naive_utc(value):
    """Postgres may hand back either an aware or a naive timestamp depending
    on the column type; GATE_START is naive UTC, and comparing the two kinds
    raises. Normalise rather than assume."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
    if getattr(value, "tzinfo", None) is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


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
    """True when this user must choose a plan before using the editor.

    Fails OPEN on any error: a database hiccup must never lock a paying
    customer out of their own project. The worst case of an open failure is
    one free agent turn; the worst case of a closed one is a customer who
    cannot reach work they already paid for.
    """
    try:
        cur = conn.cursor()
        cur.execute("SELECT created_at, is_subscribed FROM users "
                    "WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
        cur.close()
    except Exception as e:                                  # pragma: no cover
        print(f"[plan_gate] lookup failed for user {user_id}: {e}", flush=True)
        return False
    if row is None:
        return False
    if _row_get(row, "is_subscribed", 1):
        return False
    created = _naive_utc(_row_get(row, "created_at", 0))
    if created is None:
        # An account with no registration timestamp predates this gate by
        # definition — the column has been written on every insert for years.
        return False
    return created >= GATE_START


def gate_response(jsonify):
    """The 402 body every gated route returns. `jsonify` is passed in so this
    module stays importable without a Flask app context."""
    return jsonify({"error": GATE_MESSAGE, "code": GATE_CODE,
                    "plan_required": True}), 402
