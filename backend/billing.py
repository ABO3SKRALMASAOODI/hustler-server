"""What Paddle actually says about the money — recorded, not inferred.

ROUND 59. THE BUG THIS EXISTS TO KILL: on Jul 29 2026 the admin showed a
customer as `converted` while Paddle showed his $30.00 charge refused with
`not_enough_balance`, and the revenue panel showed $0. Three contradictions,
one cause.

    Paddle flips a subscription to `active` when the TRIAL ENDS, and only
    then tries the card.

So `status == 'active'` means "this subscription is running", never "the money
arrived". We read it as conversion, released the full 2,000-credit pool, and
the `subscription.past_due` that arrived a second later was logged as
"no change (grace)" and changed nothing. The account then sat there, forever,
claiming to be a paying customer who had paid nothing — and burning real model
spend out of a pool nobody had bought.

THE RULE THIS MODULE ENFORCES: **money is a row in `payments`, and nothing
else counts as revenue.** A trial opens with a genuine, signed, `completed`
transaction whose `grand_total` is *zero* — so "count the completed
transactions" was never a revenue number either. Only the amount is.

WHAT IT DOES NOT DO: withhold from someone who has paid. Every failure path
here is graded by whether this account has EVER collected a payment
(`subscription_has_paid`):

  * a trial conversion that was refused → the pool is lifted immediately.
    They have paid nothing, ever, and the pool is our model spend.
  * a renewal that was refused on a customer with a payment history → they
    keep everything for PAID_GRACE_DAYS while Paddle retries. A card that
    expires on a two-year customer must not read the same as a card that never
    worked once. That grade is the whole reason the original code chose blanket
    "grace" — it was right about the paying customer and wrong about everyone
    else.

FAILS SOFT, ALWAYS. Every function here runs inside the Paddle webhook, where
an exception means Paddle retries the event and a real activation is at stake.
A bookkeeping failure logs and returns; the worst case is a stale badge, never
a lost plan. `columns_ready()` makes the whole module a no-op until
migrations/012_billing_truth.sql has run, so this deploys before the schema and
starts working the minute it lands, with no restart.
"""

import datetime
import time

# ── Prices ───────────────────────────────────────────────────────────────────
# THE SINGLE SOURCE OF TRUTH FOR WHAT A PLAN IS WORTH, in whole USD.
#
# There was no such thing before, which is the other half of the "revenue is
# zero" bug: `routes/admin.py` carried its OWN literal
# `{'plus': 20, 'pro': 50, 'ultra': 100}` in three separate places — three
# RETIRED plans. Nobody has been on any of them since the relaunch, so MRR
# summed to 0 for every real customer no matter how much they paid. Anything
# that needs a price imports it from here.
#
# Yearly is ten months of the monthly price (see the margin table in
# routes/paddle.py); `monthly_value` amortises it so a yearly customer shows up
# in MRR as what they are worth per month rather than as a lump or a zero.
PLAN_PRICES_USD = {
    # live
    'ai':     {'monthly': 15,  'yearly': 150},
    'ai_pro': {'monthly': 30,  'yearly': 300},
    'ai_max': {'monthly': 50,  'yearly': 500},
    # off the shopfront, one grandfathered subscription
    'mcp':    {'monthly': 15,  'yearly': 150},
    # retired, grandfathered only
    'plus':   {'monthly': 20,  'yearly': 200},
    'pro':    {'monthly': 50,  'yearly': 500},
    'ultra':  {'monthly': 100, 'yearly': 1000},
    'titan':  {'monthly': 200, 'yearly': 2000},
    'ace':    {'monthly': 500, 'yearly': 5000},
    'free':   {'monthly': 0,   'yearly': 0},
}


def monthly_value(plan, period='monthly'):
    """What this subscription is worth per month, in whole USD.

    A yearly plan is amortised over twelve months — it is a real MRR
    contribution, not a one-off and not a zero.
    """
    prices = PLAN_PRICES_USD.get(plan or 'free')
    if not prices:
        return 0
    if (period or 'monthly') == 'yearly':
        return round(prices['yearly'] / 12.0, 2)
    return prices['monthly']


# ── Statuses ─────────────────────────────────────────────────────────────────
# Paddle's own subscription statuses, kept verbatim so this column can never
# drift from what the dashboard shows.
PAYING_STATUSES = ('active',)
# A trialling subscription is live and un-charged. It is NOT revenue, and every
# revenue query has to exclude it — that distinction is the entire reason the
# admin funnel counts trial and paid as two separate stages.
LIVE_STATUSES = ('active', 'trialing')
# The card was refused and Paddle is retrying. Not canceled, not paying.
FAILING_STATUSES = ('past_due', 'paused')

# How long a customer WHO HAS PAID BEFORE keeps their plan while Paddle retries
# a refused renewal. Paddle's dunning runs 7 attempts over 30 days; cutting a
# real customer off on attempt one because their bank blocked a payment is how
# you turn a temporary decline into a cancellation. Three days is roughly two
# retry attempts — long enough to recover a bank blip, short enough that a dead
# card is not a month of free model spend.
#
# An account that has NEVER collected a payment gets no grace at all: there is
# no relationship to protect and the pool is pure cost.
PAID_GRACE_DAYS = 3

# One nudge a day, and never more than this many. Past it, the email has
# stopped being a service and started being noise — Paddle is still retrying
# either way, and the in-app banner never goes away.
MAX_DUNNING_EMAILS = 6

BILLING_COLUMNS = ("billing_status", "billing_plan", "billing_period",
                   "billing_synced_at",
                   "payment_failed_at", "payment_failed_reason",
                   "payment_failed_count", "payment_recovered_at",
                   "dunning_emailed_at", "dunning_email_count")

_schema = {"ok": False, "checked_at": 0.0}
_RECHECK_SECONDS = 60


def columns_ready(conn):
    """True once 012_billing_truth.sql has run. Cached once it is True.

    Same pattern as trial_state.columns_ready and for the same reason: prod
    schema is applied by hand, so the code has to survive the window between
    the deploy and the psql.
    """
    if _schema["ok"]:
        return True
    if time.time() - _schema["checked_at"] < _RECHECK_SECONDS:
        return False
    _schema["checked_at"] = time.time()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM information_schema.columns
             WHERE table_name = 'users' AND column_name = ANY(%s)
        """, (list(BILLING_COLUMNS),))
        row = cur.fetchone()
        cur.execute("SELECT to_regclass('public.payments') IS NOT NULL")
        trow = cur.fetchone()
        cur.close()
        found = _scalar(row)
        has_table = bool(_scalar(trow))
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [billing] schema check failed: {e}", flush=True)
        return False
    _schema["ok"] = (found == len(BILLING_COLUMNS)) and has_table
    if not _schema["ok"]:
        print(f"ℹ️ [billing] {found}/{len(BILLING_COLUMNS)} columns, "
              f"payments table={has_table} — billing truth idle until "
              f"migrations/012_billing_truth.sql is applied", flush=True)
    return _schema["ok"]


def _scalar(row):
    """Callers arrive with either a plain cursor (the webhook) or a
    RealDictCursor (the admin), and a dict row has no key 0."""
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def _safe_rollback(conn):
    """A failed statement leaves psycopg2's connection aborted, and get_db()
    hands that SAME connection to everything else in the request."""
    try:
        conn.rollback()
    except Exception:
        pass


# ── Reading Paddle's numbers ─────────────────────────────────────────────────

def _naive_utc(value):
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        try:
            dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


def transaction_amount(data):
    """(amount_cents, currency) off a Paddle transaction object.

    `details.totals.grand_total` is a STRING of minor units — "3000" is $30.00
    — and it is 0 for the transaction that opens a trial. Reading it as a float
    of dollars (an easy mistake, the field is not named `_cents`) would report
    every charge at 100x.
    """
    totals = ((data.get('details') or {}).get('totals') or {})
    raw = totals.get('grand_total')
    if raw is None:
        # `payouts`/`adjusted` shapes and some webhook variants nest it here.
        raw = ((data.get('totals') or {}) or {}).get('grand_total')
    try:
        cents = int(float(raw))
    except (TypeError, ValueError):
        cents = 0
    return cents, (totals.get('currency_code')
                   or data.get('currency_code') or 'USD')


def payment_error_code(data):
    """Why the card was refused, from the transaction's payment attempts.

    Paddle appends an attempt per retry, so the LAST one is the current
    reason — reading the first would pin the message to a decline that may
    since have been superseded.
    """
    reason = None
    for p in (data.get('payments') or []):
        if (p.get('status') or '').lower() in ('error', 'failed', 'declined'):
            reason = p.get('error_code') or reason
    return reason


# ── The money ledger ─────────────────────────────────────────────────────────

def record_transaction(conn, user_id, data, status=None):
    """Write (or update) one Paddle transaction in `payments`. Never raises.

    Upsert on transaction_id because Paddle retries webhooks and a transaction
    legitimately changes status over its life (`billed` → `past_due` →
    `completed` when a retry succeeds). A ledger that appended a row per event
    would count one $30 charge three times.

    Returns the amount in cents that this transaction represents, or None if
    nothing was written.
    """
    try:
        if not columns_ready(conn):
            return None
        txn_id = data.get('id')
        if not txn_id:
            return None
        cents, currency = transaction_amount(data)
        status = (status or data.get('status') or 'unknown').lower()
        occurred = (_naive_utc(data.get('billed_at'))
                    or _naive_utc(data.get('created_at')))
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO payments (user_id, transaction_id, subscription_id,
                                  plan, status, amount_cents, currency,
                                  origin, error_code, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (transaction_id) DO UPDATE SET
                status       = EXCLUDED.status,
                amount_cents = EXCLUDED.amount_cents,
                currency     = EXCLUDED.currency,
                error_code   = EXCLUDED.error_code,
                occurred_at  = COALESCE(EXCLUDED.occurred_at,
                                        payments.occurred_at),
                -- user_id is only ever FILLED IN, never blanked: an adjustment
                -- event carries no custom_data, so a later write with a NULL
                -- user must not orphan a row we had already attributed.
                user_id      = COALESCE(EXCLUDED.user_id, payments.user_id),
                plan         = COALESCE(EXCLUDED.plan, payments.plan),
                updated_at   = NOW()
        """, (user_id, txn_id, data.get('subscription_id'),
              _plan_hint(data), status, cents, currency,
              data.get('origin'), payment_error_code(data), occurred))
        conn.commit()
        cur.close()
        return cents
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [billing] could not record transaction "
              f"{data.get('id')}: {e}", flush=True)
        return None


def _plan_hint(data):
    """Best-effort plan name for the ledger row. Purely descriptive — the
    authoritative grant still comes from paddle_webhook._plan_from_data."""
    try:
        from routes.paddle_webhook import PRICE_TO_PLAN
    except Exception:
        return None
    for it in (data.get('items') or []):
        pid = (it.get('price') or {}).get('id') or it.get('price_id')
        if pid and pid in PRICE_TO_PLAN:
            return PRICE_TO_PLAN[pid]
    return None


def subscription_has_paid(conn, subscription_id=None, user_id=None):
    """Has real money EVER been collected here?

    The one question that grades every failure path in this module, and the one
    question the old code could not ask. A trial's opening transaction is
    `completed` with grand_total 0, so the amount filter is not an optimisation
    — it is the whole test.

    Fails to False, which is the SAFE direction for a grant (withhold a pool
    that costs us model spend) but the harsh one for grace. The daily
    reconciler re-asks with a live Paddle read, so a transient DB error costs
    at most one day of premature strictness, never a wrong permanent state.
    """
    if not subscription_id and not user_id:
        return False
    try:
        if not columns_ready(conn):
            return False
        cur = conn.cursor()
        if subscription_id:
            cur.execute("""SELECT 1 FROM payments
                            WHERE subscription_id = %s
                              AND status = 'completed'
                              AND amount_cents > 0 LIMIT 1""",
                        (subscription_id,))
        else:
            cur.execute("""SELECT 1 FROM payments
                            WHERE user_id = %s
                              AND status = 'completed'
                              AND amount_cents > 0 LIMIT 1""", (user_id,))
        found = cur.fetchone() is not None
        cur.close()
        return found
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [billing] paid-check failed "
              f"({subscription_id or user_id}): {e}", flush=True)
        return False


# ── Recording what Paddle says about the subscription ────────────────────────

def set_status(conn, user_id, status, plan=None, period=None):
    """Record Paddle's subscription status verbatim. Never raises."""
    try:
        if not columns_ready(conn):
            return
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
               SET billing_status    = %s,
                   billing_plan      = COALESCE(%s, billing_plan),
                   billing_period    = COALESCE(%s, billing_period),
                   billing_synced_at = NOW()
             WHERE id = %s
        """, ((status or None), plan, period, user_id))
        conn.commit()
        cur.close()
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [billing] status write failed for {user_id}: {e}",
              flush=True)


def record_failure(conn, user_id, subscription_id, plan, reason,
                   status='past_due'):
    """A charge was refused. Returns a dict describing what we did.

    Two outcomes, decided by whether this account has ever collected money:

      lifted  — the paid pool is taken back NOW. This is the trial-conversion
                case: they entered a card, spent nothing, the card was refused,
                and every credit in that pool is our API bill.
      grace   — a paying customer's renewal failed. Everything stays as it is
                for PAID_GRACE_DAYS while Paddle retries; the daily tick lifts
                it if the grace expires without a payment.

    In BOTH cases the failure is recorded, so the admin, the studio banner and
    the daily nudge all see it immediately. The difference is only what the
    customer keeps while it is being fixed.
    """
    result = {"action": "none", "reason": reason}
    try:
        if not columns_ready(conn):
            return result
        paid_before = subscription_has_paid(conn, subscription_id, user_id)
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
               SET billing_status        = %s,
                   billing_plan          = COALESCE(%s, billing_plan, plan),
                   billing_synced_at     = NOW(),
                   payment_failed_at     = COALESCE(payment_failed_at, NOW()),
                   payment_failed_reason = %s,
                   -- Only counts up on a NEW failure episode. Paddle resends
                   -- subscription.updated freely while past_due, and a counter
                   -- that ticked on every event would read as ten refused
                   -- cards when there was one.
                   payment_failed_count  = payment_failed_count
                                           + CASE WHEN payment_failed_at IS NULL
                                                  THEN 1 ELSE 0 END,
                   payment_recovered_at  = NULL
             WHERE id = %s
        """, (status, plan, reason, user_id))
        conn.commit()
        cur.close()
        result["paid_before"] = paid_before

        if paid_before:
            result["action"] = "grace"
            print(f"⏳ [billing] user {user_id} payment refused ({reason}) — "
                  f"has paid before, {PAID_GRACE_DAYS}d grace while Paddle "
                  f"retries", flush=True)
        else:
            lift_paid_credits(conn, user_id)
            result["action"] = "lifted"
            print(f"🧊 [billing] user {user_id} payment refused ({reason}) "
                  f"and has NEVER paid — plan credits lifted", flush=True)
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [billing] failure bookkeeping failed for {user_id}: {e}",
              flush=True)
    return result


def lift_paid_credits(conn, user_id):
    """Take back the pool the refused payment was supposed to buy.

    Deliberately NOT models.update_user_subscription_status(..., False, ...):
    that NULLs `subscription_id`, and the subscription id is how every later
    Paddle event — including the retry that finally succeeds — finds this user
    again. Blanking it would make the account unrecoverable by webhook and
    invisible to the reconciler in one stroke.

    So: strip the entitlement, keep the identity.

      * credits_monthly / credits_monthly_limit → 0. The pool is our model
        spend and it was never paid for.
      * credits_daily → 0. The 20-a-day top-up is a SUBSCRIBER benefit.
      * credits_bonus is untouched. That is the 50 free credits every account
        on the platform holds (round 50); it was never part of the plan and
        taking it would leave a refused customer worse off than a stranger.
      * plan → 'free', so nothing downstream can grant a paid model tier off a
        plan nobody paid for. What they were buying lives on in `billing_plan`.
    """
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
           SET is_subscribed         = 0,
               plan                  = 'free',
               credits_monthly       = 0,
               credits_monthly_limit = 0,
               credits_daily         = 0,
               credits_balance       = COALESCE(credits_bonus, 0)
         WHERE id = %s
    """, (user_id,))
    conn.commit()
    cur.close()


def record_recovery(conn, user_id, plan=None):
    """A payment landed after a failure. Clear the failure state.

    The credit grant itself is NOT done here — that is
    models.update_user_subscription_status on the ordinary grant path, which
    has always been the one place a plan is released. This only erases the
    failure, so a recovered customer stops seeing a decline banner for a card
    that now works.
    """
    try:
        if not columns_ready(conn):
            return False
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
               SET billing_status        = 'active',
                   billing_plan          = COALESCE(%s, billing_plan),
                   billing_synced_at     = NOW(),
                   payment_failed_at     = NULL,
                   payment_failed_reason = NULL,
                   payment_recovered_at  = CASE WHEN payment_failed_at IS NOT NULL
                                                THEN NOW()
                                                ELSE payment_recovered_at END,
                   dunning_emailed_at    = NULL,
                   dunning_email_count   = 0
             WHERE id = %s
             RETURNING payment_recovered_at
        """, (plan, user_id))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        return row is not None
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [billing] recovery bookkeeping failed for {user_id}: {e}",
              flush=True)
        return False


def clear_billing(conn, user_id):
    """Wipe the billing state on a full downgrade (cancel / refund).

    Called alongside the existing clawback so a canceled account does not keep
    a `past_due` badge and a decline banner forever — it is not past due, it is
    gone.
    """
    try:
        if not columns_ready(conn):
            return
        cur = conn.cursor()
        cur.execute("""
            UPDATE users
               SET billing_status        = 'canceled',
                   billing_synced_at     = NOW(),
                   payment_failed_at     = NULL,
                   payment_failed_reason = NULL,
                   dunning_emailed_at    = NULL,
                   dunning_email_count   = 0
             WHERE id = %s
        """, (user_id,))
        conn.commit()
        cur.close()
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [billing] clear failed for {user_id}: {e}", flush=True)


# ── What the app should say about it ─────────────────────────────────────────

def payment_state(conn, user_id):
    """The decline, for the studio banner and the /credits payload.

    Returns {} when there is nothing wrong, so a caller can spread it into a
    response and every existing client keeps working untouched.
    """
    try:
        if not columns_ready(conn):
            return {}
        cur = conn.cursor()
        cur.execute("""
            SELECT billing_status, billing_plan, payment_failed_at,
                   payment_failed_reason, subscription_id
              FROM users WHERE id = %s
        """, (user_id,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return {}
        if isinstance(row, dict):
            status, plan, failed_at, reason, sub_id = (
                row.get('billing_status'), row.get('billing_plan'),
                row.get('payment_failed_at'), row.get('payment_failed_reason'),
                row.get('subscription_id'))
        else:
            status, plan, failed_at, reason, sub_id = row
        if not failed_at or (status or '') not in FAILING_STATUSES:
            return {}
        return {
            "payment_failed": True,
            "payment_failed_reason": reason,
            "payment_failed_message": decline_message(reason),
            "payment_failed_at": (failed_at.isoformat()
                                  if hasattr(failed_at, 'isoformat')
                                  else failed_at),
            "billing_status": status,
            "billing_plan": plan,
            "subscription_id": sub_id,
        }
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [billing] payment_state failed for {user_id}: {e}",
              flush=True)
        return {}


# Paddle's error codes, said out loud. The generic fallback is deliberately
# vague about WHY — telling somebody "your card was declined" when the real
# reason was a bank block sends them to cancel a working card.
_DECLINE_COPY = {
    'not_enough_balance': "Your card didn't have enough available balance.",
    'insufficient_funds': "Your card didn't have enough available balance.",
    'card_declined': "Your bank declined the payment.",
    'declined': "Your bank declined the payment.",
    'expired_card': "Your card has expired.",
    'invalid_card_details': "Your card details didn't go through.",
    'invalid_amount': "Your bank rejected the amount.",
    'authentication_failed': "The payment needed confirmation from your bank "
                             "and didn't get it.",
    'three_d_secure_not_authenticated': "Your bank asked you to confirm the "
                                        "payment and it wasn't confirmed.",
    'transaction_not_permitted': "Your bank doesn't allow this type of "
                                 "payment.",
    'issuer_unavailable': "Your bank couldn't be reached.",
    'blocked_card': "Your card is blocked for online payments.",
    'fraud': "Your bank flagged the payment.",
}


def decline_message(reason):
    return _DECLINE_COPY.get((reason or '').lower(),
                             "Your payment didn't go through.")
