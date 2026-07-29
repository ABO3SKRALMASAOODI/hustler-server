"""The trial lifecycle, recorded from Paddle's own status — never inferred.

Round 46. Both live plans sell a 3-day free trial that is configured on the
Paddle PRICE, so a checkout creates the subscription immediately with status
'trialing' and charges nothing until day 3. Two facts about that window were
not written down anywhere:

  * a trial STARTED — worth an email the moment it happens, not the next time
    someone opens the admin;
  * a trial was CANCELED before converting — the one number that says whether
    the product lands, and the thing you want beside a user's row before you
    read anything else about them.

Both come straight off the subscription object Paddle sends:

    status == 'trialing'                    → in trial
    scheduled_change.action == 'cancel'     → will not convert
    status == 'active' after a recorded trial → converted

Nothing here runs a timer or assumes "3 days": the dates stored are Paddle's
own trial_dates, so changing the trial period on the price changes what we
record, with no code edit and no drift.

WHY IT ALL FAILS SOFT. Every function here runs inside the payment webhook.
A stray exception there means Paddle retries the event and a paying customer's
activation is at stake, so a trial-bookkeeping failure logs and returns
instead of raising — the worst case is a missing badge, never a missing plan.

SCHEMA. Six columns on `users`, added by hand in the Render shell (this
project deliberately keeps schema out of models.py):

    ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_status          TEXT;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_started_at      TIMESTAMP;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_ends_at         TIMESTAMP;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_canceled_at     TIMESTAMP;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_plan            TEXT;
    ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_subscription_id TEXT;

Until they exist, `columns_ready()` returns False and everything here is a
no-op — so this can deploy before the migration is run and starts working the
minute it is, without a restart (a False answer is re-checked once a minute; a
True one is cached for the life of the process).
"""

import datetime
import time

from founder_alert import render_alert, send_founder_alert

TRIAL_COLUMNS = ("trial_status", "trial_started_at", "trial_ends_at",
                 "trial_canceled_at", "trial_plan", "trial_subscription_id")

# What the plan key is called in the product. Only for human-readable output —
# the authoritative plan → credits mapping lives in paddle_webhook.PLAN_CREDITS
# and is derived from the price the customer actually paid.
PLAN_LABELS = {
    'ai': 'Creator', 'ai_pro': 'Pro', 'ai_max': 'Frontier', 'mcp': 'MCP',
    'plus': 'Plus', 'pro': 'Pro (retired)', 'ultra': 'Ultra (retired)',
    'titan': 'Titan (retired)', 'ace': 'Ace (retired)',
}

_schema = {"ok": False, "checked_at": 0.0}
_RECHECK_SECONDS = 60


def plan_label(plan):
    return PLAN_LABELS.get(plan, (plan or 'unknown'))


def columns_ready(conn):
    """True once the trial columns exist. Cheap, and cached once it is True."""
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
        """, (list(TRIAL_COLUMNS),))
        row = cur.fetchone()
        cur.close()
        # Callers arrive with either a plain cursor (the webhook) or a
        # RealDictCursor (the admin), and a dict row has no key 0.
        found = row[0] if not isinstance(row, dict) else next(iter(row.values()))
    except Exception as e:                                  # pragma: no cover
        print(f"⚠️ [trial] schema check failed: {e}", flush=True)
        return False
    _schema["ok"] = (found == len(TRIAL_COLUMNS))
    if not _schema["ok"]:
        print(f"ℹ️ [trial] {found}/{len(TRIAL_COLUMNS)} trial columns present "
              f"— trial tracking idle until the ALTER TABLEs are run",
              flush=True)
    return _schema["ok"]


# ── Reading Paddle's dates ───────────────────────────────────────────────────

def _naive_utc(value):
    """Paddle sends RFC-3339 with a Z. The columns are `timestamp without time
    zone` like every other timestamp on this table, so normalise to naive UTC
    here rather than letting Postgres silently drop an offset."""
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        try:
            dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


def _trial_dates(data):
    """(starts_at, ends_at) for the trialling subscription, or (None, None).

    Paddle puts trial_dates on each line item. next_billed_at is the fallback:
    for a trialling subscription it IS the moment the trial ends, because the
    first charge is what ends it.
    """
    for item in (data.get('items') or []):
        dates = (item or {}).get('trial_dates') or {}
        if dates.get('ends_at'):
            return _naive_utc(dates.get('starts_at')), _naive_utc(dates['ends_at'])
    if (data.get('status') or '').lower() == 'trialing':
        return None, _naive_utc(data.get('next_billed_at'))
    return None, None


# ── Writing what we learned ──────────────────────────────────────────────────

def _record_start(conn, user_id, plan, subscription_id, starts_at, ends_at):
    """Mark the trial; return True only for the call that actually started it.

    The grant path in the webhook runs on subscription.created AND
    subscription.updated AND subscription.activated, so one trial produces
    several events carrying status='trialing' — and the founder must get one
    email, not four. The WHERE clause makes that the database's job instead of
    a guess: the row only changes when this subscription is not already the
    recorded trial, so exactly one caller gets a row back.
    """
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
           SET trial_status          = 'trialing',
               trial_started_at      = COALESCE(%s, NOW()),
               trial_ends_at         = %s,
               trial_canceled_at     = NULL,
               trial_plan            = %s,
               trial_subscription_id = %s
         WHERE id = %s
           AND trial_subscription_id IS DISTINCT FROM %s
        RETURNING id
    """, (starts_at, ends_at, plan, subscription_id, user_id, subscription_id))
    started = cur.fetchone() is not None
    conn.commit()
    cur.close()
    return started


def is_recorded_trial(conn, user_id, subscription_id=None):
    """True when this account is ALREADY recorded as trialling.

    The credit grant needs this for two different questions and gets both from
    one fact (see paddle_webhook._grant_credits):

      * 'transaction.completed' carries the TRANSACTION's status, never the
        subscription's, so it cannot tell on its own whether the money it
        represents is a real charge or the $0 that opens a trial. A recorded
        trial answers that.
      * a repeat 'trialing' event must not RE-GRANT the trial pool. The grant
        is a SET, not an add, so without this a user who spent 180 of their 200
        trial credits gets them back the next time Paddle touches the
        subscription — an unbounded trial in three or four events.

    Fails to False, which grants rather than withholds: a user wrongly given
    their pool back is a bounded cost; a paying customer wrongly left at 10% of
    what they bought is a support ticket and a refund.
    """
    try:
        if not columns_ready(conn):
            return False
        cur = conn.cursor()
        if subscription_id:
            cur.execute("""SELECT 1 FROM users
                            WHERE id = %s AND trial_status = 'trialing'
                              AND (trial_subscription_id IS NULL
                                   OR trial_subscription_id = %s)""",
                        (user_id, subscription_id))
        else:
            cur.execute("SELECT 1 FROM users "
                        "WHERE id = %s AND trial_status = 'trialing'",
                        (user_id,))
        found = cur.fetchone() is not None
        cur.close()
        return found
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [trial] recorded-trial check failed for {user_id}: {e}",
              flush=True)
        return False


def _record_cancel(conn, user_id, subscription_id):
    """Mark a cancellation that happened DURING the trial.

    Guarded on trial_status='trialing' so an ordinary churn cancel months into
    a paid subscription is never relabelled as a failed trial — that would
    quietly inflate the one number this exists to report. It also makes the
    repeat events idempotent: Paddle sends the scheduled cancel first and the
    real subscription.canceled at trial end, and only the first one writes.
    """
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
           SET trial_status      = 'canceled',
               trial_canceled_at = COALESCE(trial_canceled_at, NOW())
         WHERE id = %s
           AND trial_status = 'trialing'
           AND (trial_subscription_id IS NULL
                OR trial_subscription_id = %s)
        RETURNING id
    """, (user_id, subscription_id))
    canceled = cur.fetchone() is not None
    conn.commit()
    cur.close()
    return canceled


def _record_converted(conn, user_id, subscription_id):
    """The trial ended AND the money arrived.

    ROUND 59 — 'converted' now requires a payment, and that is a correction.
    This used to be written on `status == 'active'` alone, and Paddle sets a
    subscription to `active` when the TRIAL ENDS — *before* it tries the card.
    On Jul 29 2026 a customer's $30.00 charge was refused for
    `not_enough_balance` seconds after that event, and the admin went on
    showing him as a converted paying customer indefinitely, next to a revenue
    figure of zero. Both were reporting the same subscription.

    The caller now supplies the proof (billing.subscription_has_paid). Trials
    that have genuinely converted still land here within seconds, via the
    transaction.completed that carries the amount.
    """
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
           SET trial_status = 'converted'
         WHERE id = %s
           AND trial_status IN ('trialing', 'payment_failed')
           AND trial_subscription_id = %s
        RETURNING id
    """, (user_id, subscription_id))
    converted = cur.fetchone() is not None
    conn.commit()
    cur.close()
    return converted


def _record_payment_failed(conn, user_id, subscription_id):
    """The conversion charge was refused.

    Guarded on the trial states so an ordinary renewal failure months into a
    paid subscription is never relabelled as a failed trial — the same
    discipline _record_cancel follows, and for the same reason: this column
    feeds the one number that says whether the product lands.
    """
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
           SET trial_status = 'payment_failed'
         WHERE id = %s
           AND trial_status IN ('trialing', 'converted')
           AND (trial_subscription_id IS NULL
                OR trial_subscription_id = %s)
        RETURNING id
    """, (user_id, subscription_id))
    failed = cur.fetchone() is not None
    conn.commit()
    cur.close()
    return failed


# ── The alert ────────────────────────────────────────────────────────────────

def _facts(conn, user_id):
    """Everything worth knowing about the person who just started a trial.

    Best-effort: the alert is worth sending even if the onboarding table is
    empty or unreachable, so a failure here degrades the email rather than
    cancelling it.
    """
    out = {}
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.email, u.created_at, u.auth_provider,
                   u.device_type, u.device_browser,
                   (SELECT o.channel  FROM onboarding_responses o
                     WHERE o.user_id = u.id),
                   (SELECT o.use_case FROM onboarding_responses o
                     WHERE o.user_id = u.id)
              FROM users u WHERE u.id = %s
        """, (user_id,))
        row = cur.fetchone()
        cur.close()
        if row:
            out = {"email": row[0], "created_at": row[1], "provider": row[2],
                   "device": row[3], "browser": row[4],
                   "channel": row[5], "use_case": row[6]}
    except Exception as e:
        print(f"⚠️ [trial] alert facts lookup failed for {user_id}: {e}",
              flush=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE trial_started_at IS NOT NULL")
        out["trial_number"] = cur.fetchone()[0]
        cur.close()
    except Exception:
        pass
    return out


def _fmt(dt):
    return dt.strftime("%b %d, %Y · %H:%M UTC") if dt else None


def _alert_started(conn, user_id, plan, subscription_id, ends_at):
    f = _facts(conn, user_id)
    email = f.get("email") or f"user #{user_id}"
    label = plan_label(plan)

    signed_up = f.get("created_at")
    gap = None
    if signed_up:
        try:
            delta = datetime.datetime.utcnow() - signed_up
            mins = int(delta.total_seconds() // 60)
            gap = (f"{mins} min after signing up" if mins < 180 else
                   f"{mins // 60} h after signing up" if mins < 48 * 60 else
                   f"{mins // 1440} days after signing up")
        except Exception:
            gap = None

    device = f.get("device")
    if device and f.get("browser"):
        device = f"{device} · {f['browser']}"

    n = f.get("trial_number")
    title = f"🎉 Trial started — {label}"
    lines = [
        ("User", email),
        ("Plan", label),
        ("Trial ends", _fmt(ends_at) or "unknown (Paddle sent no trial dates)"),
        ("Signed up", _fmt(signed_up)),
        ("", gap),
        ("Signup method", f.get("provider") or "unknown"),
        ("Device", device),
        ("Found us via", f.get("channel")),
        ("Wants to", f.get("use_case")),
        ("Subscription", subscription_id),
    ]
    footer = ("They are not charged until the trial ends — if they cancel "
              "before then, the Users tab in the admin marks them "
              "<b>trial canceled</b>.")
    if n:
        footer = f"That is trial #{n} overall. " + footer
    send_founder_alert(title, render_alert(title, "#10b981", lines, footer))


# ── The one entry point the webhook calls ────────────────────────────────────

def sync_from_subscription(conn, user_id, plan, subscription_id, data):
    """Record what this subscription.* event says about the trial.

    Called for every grant event that carries a subscription status. Swallows
    everything: see the module docstring on why this must not be able to break
    an activation.
    """
    try:
        if not columns_ready(conn):
            return
        status = (data.get('status') or '').lower()
        scheduled = ((data.get('scheduled_change') or {})
                     .get('action') or '').lower()

        if status == 'trialing':
            starts_at, ends_at = _trial_dates(data)
            if _record_start(conn, user_id, plan, subscription_id,
                             starts_at, ends_at):
                print(f"🎉 User {user_id} started a {plan} trial "
                      f"(ends {ends_at})", flush=True)
                _alert_started(conn, user_id, plan, subscription_id, ends_at)
            # A cancel scheduled while still trialing is the "they won't
            # convert" signal — Paddle keeps the status at 'trialing' until
            # the trial actually runs out, so this is the only place it shows.
            if scheduled == 'cancel' and _record_cancel(conn, user_id,
                                                        subscription_id):
                print(f"🚫 User {user_id} canceled during their trial",
                      flush=True)

        elif status == 'active':
            # 'active' is Paddle saying the subscription is RUNNING, which on
            # the last day of a trial it says before it has tried the card. So
            # it is not enough on its own — the ledger has to show money. If
            # the charge is still in flight, the transaction.completed that
            # follows a second later calls record_paid_conversion and this
            # lands then; if it is refused, _handle_failure marks it instead.
            import billing
            if not billing.subscription_has_paid(conn, subscription_id,
                                                 user_id):
                print(f"⏸ User {user_id} subscription is active but no "
                      f"payment is recorded yet — not marking converted",
                      flush=True)
            elif _record_converted(conn, user_id, subscription_id):
                print(f"💚 User {user_id} converted from trial to paid",
                      flush=True)
    except Exception as e:
        _safe_rollback(conn)
        print(f"⚠️ [trial] bookkeeping failed for user {user_id}: {e}",
              flush=True)


def record_paid_conversion(conn, user_id, subscription_id):
    """Money landed on this subscription — promote the trial to converted.

    Called from the transaction.completed branch of the webhook, which is the
    only place on this system that sees an amount. Also recovers a trial we had
    already marked `payment_failed`: a Paddle dunning retry that succeeds is a
    conversion, just a late one, and the badge has to be able to move back.
    """
    try:
        if not columns_ready(conn) or not subscription_id:
            return False
        if _record_converted(conn, user_id, subscription_id):
            print(f"💚 User {user_id} converted from trial to paid",
                  flush=True)
            return True
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [trial] paid-conversion bookkeeping failed for "
              f"{user_id}: {e}", flush=True)
    return False


def record_payment_failure(conn, user_id, subscription_id):
    """The charge that would have converted this trial was refused."""
    try:
        if not columns_ready(conn):
            return False
        if _record_payment_failed(conn, user_id, subscription_id):
            print(f"💳 User {user_id} trial conversion payment was refused",
                  flush=True)
            return True
    except Exception as e:                                  # pragma: no cover
        _safe_rollback(conn)
        print(f"⚠️ [trial] payment-failure bookkeeping failed for "
              f"{user_id}: {e}", flush=True)
    return False


def record_cancel(conn, user_id, subscription_id):
    """For the subscription.canceled event, which carries no trial status.

    A no-op unless we had recorded this user as trialing, so only a genuine
    trial cancellation is labelled one.
    """
    try:
        if not columns_ready(conn):
            return False
        if _record_cancel(conn, user_id, subscription_id):
            print(f"🚫 User {user_id} canceled during their trial", flush=True)
            return True
    except Exception as e:
        _safe_rollback(conn)
        print(f"⚠️ [trial] cancel bookkeeping failed for user {user_id}: {e}",
              flush=True)
    return False


def _safe_rollback(conn):
    """A failed statement leaves psycopg2's connection in an aborted state, and
    get_db() hands that SAME connection to everything else in the request. Roll
    back so a trial-tracking failure cannot poison an unrelated query."""
    try:
        conn.rollback()
    except Exception:
        pass
