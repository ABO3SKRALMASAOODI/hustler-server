"""Durable founder email for the first real payment on a subscription.

The former alert lived on ``subscription.status == 'trialing'``. Current
prices have no trial and charge immediately, so a positive Paddle transaction
is now the only honest signal that somebody actually subscribed.

Webhook safety rules:

* Only ``transaction.completed`` with an amount above zero is eligible. A
  legacy trial's $0 opening transaction is not money, and Paddle's preceding
  ``paid`` lifecycle event is not yet the final ledger state.
* ``subscription_id`` is required and is the primary business dedupe key, so
  renewals do not announce an existing customer as new. ``transaction_id`` is
  independently unique so webhook retries cannot double-send.
* The request only inserts a small queue row. Brevo runs off-request on a new
  DB connection. Failed sends remain durable and are retried by a small
  scheduler; no email error can roll back a customer's activation.
"""

import datetime
import html
import os
import threading
from decimal import Decimal

import billing
from founder_alert import render_alert, send_founder_alert_now

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:                                      # pragma: no cover
    BackgroundScheduler = None


ELIGIBLE_EVENTS = frozenset(("transaction.completed",))
STALE_CLAIM_MINUTES = 20
MAX_BATCH = 20

PLAN_LABELS = {
    "ai": "Creator",
    "ai_pro": "Pro",
    "ai_max": "Frontier",
    "mcp": "MCP",
    "plus": "Plus (retired)",
    "pro": "Pro (retired)",
    "ultra": "Ultra (retired)",
    "titan": "Titan (retired)",
    "ace": "Ace (retired)",
}


def enabled():
    return os.getenv("PAID_ALERTS_ENABLED", "1") in ("1", "true", "True")


def _plan_label(plan):
    return PLAN_LABELS.get(plan, plan or "Unknown plan")


def _billing_period(data):
    """Read monthly/yearly from Paddle's recurring price, then custom_data."""
    for item in data.get("items") or []:
        price = (item or {}).get("price") or {}
        cycle = price.get("billing_cycle") or (item or {}).get("billing_cycle") or {}
        interval = (cycle.get("interval") or "").lower()
        try:
            frequency = int(cycle.get("frequency") or 1)
        except (TypeError, ValueError):
            frequency = 1
        if interval == "year" or (interval == "month" and frequency == 12):
            return "yearly"
        if interval == "month":
            return "monthly"
    raw = ((data.get("custom_data") or {}).get("billing") or "").lower()
    if raw in ("annual", "annually", "year", "yearly"):
        return "yearly"
    return "monthly"


def _is_subscription_transaction(data):
    """True only when Paddle attaches the authoritative subscription ID.

    A recurring price or browser-supplied custom_data suggests intent; neither
    proves which subscription should be deduplicated. If an early ``paid``
    event lacks the ID, its later ``completed`` event can safely enqueue.
    """
    return bool(data.get("subscription_id"))


def _dedupe_key(data):
    subscription_id = data.get("subscription_id")
    return f"sub:{subscription_id}" if subscription_id else None


def _user_facts(conn, user_id):
    if not user_id:
        return {}
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT email, created_at, auth_provider
              FROM users
             WHERE id = %s
        """, (user_id,))
        row = cur.fetchone()
    finally:
        cur.close()
    if not row:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return {"email": row[0], "created_at": row[1], "auth_provider": row[2]}


def _fmt_time(value):
    if not value:
        return None
    if not isinstance(value, datetime.datetime):
        try:
            value = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    value = value.astimezone(datetime.timezone.utc)
    return value.strftime("%b %d, %Y · %H:%M UTC")


def _money(cents, currency):
    amount = Decimal(cents) / Decimal(100)
    return f"{(currency or 'USD').upper()} {amount:,.2f}"


def _safe(value):
    return html.escape(str(value), quote=True) if value not in (None, "") else value


def _alert_content(user_id, plan, period, cents, currency, data, event_type,
                   facts):
    label = _plan_label(plan)
    email = facts.get("email") or f"user #{user_id}"
    amount = _money(cents, currency)
    title = f"💰 New paid subscriber — {label}"
    lines = [
        ("User", _safe(email)),
        ("User ID", _safe(user_id)),
        ("Plan", _safe(label)),
        ("Amount collected", _safe(amount)),
        ("Billing", _safe(period)),
        ("Paid at", _safe(_fmt_time(data.get("billed_at") or data.get("updated_at")
                                      or data.get("created_at")))),
        ("Signup method", _safe(facts.get("auth_provider"))),
        ("Signed up", _safe(_fmt_time(facts.get("created_at")))),
        ("Subscription", _safe(data.get("subscription_id"))),
        ("Transaction", _safe(data.get("id"))),
        ("Paddle origin", _safe(data.get("origin"))),
        ("Webhook event", _safe(event_type)),
    ]
    footer = ("Paddle reported a positive-amount subscription transaction. "
              "Renewals on this same subscription are deduplicated and will "
              "not generate another new-subscriber email.")
    return title, render_alert(title, "#10b981", lines, footer)


def enqueue(conn, user_id, plan, data, event_type):
    """Persist one eligible alert and return its dedupe key, else ``None``.

    This function never raises. In particular, migration 021 being absent is
    logged and leaves the already-committed payment/entitlement untouched.
    """
    try:
        if event_type not in ELIGIBLE_EVENTS or not _is_subscription_transaction(data):
            return None
        txn_id = data.get("id")
        key = _dedupe_key(data)
        cents, currency = billing.transaction_amount(data)
        if not txn_id or not key or cents <= 0:
            return None

        period = _billing_period(data)
        facts = _user_facts(conn, user_id)
        subject, content = _alert_content(
            user_id, plan, period, cents, currency, data, event_type, facts)
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO founder_subscription_alerts (
                    dedupe_key, transaction_id, subscription_id, user_id,
                    user_email, plan, billing_period, amount_cents, currency,
                    event_type, subject, html_content
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING dedupe_key
            """, (key, txn_id, data.get("subscription_id"), user_id,
                  facts.get("email"), plan, period, cents, currency,
                  event_type, subject, content))
            inserted = cur.fetchone()
            conn.commit()
        finally:
            cur.close()
        if inserted:
            print(f"📬 [paid_alert] queued {key} / {txn_id}", flush=True)
            return key
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"⚠️ [paid_alert] could not queue {data.get('id')}: {exc}",
              flush=True)
    return None


def enqueue_and_kick(conn, user_id, plan, data, event_type):
    """Webhook entry point: persist synchronously, deliver asynchronously."""
    if not enabled():
        return False
    key = enqueue(conn, user_id, plan, data, event_type)
    if key:
        kick(key)
    return bool(key)


def _connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return psycopg2.connect(os.environ["DATABASE_URL"],
                            cursor_factory=RealDictCursor)


def _claim(conn, key):
    cur = conn.cursor()
    try:
        cur.execute(f"""
            UPDATE founder_subscription_alerts
               SET status = 'sending',
                   attempt_count = attempt_count + 1,
                   last_attempt_at = NOW(),
                   updated_at = NOW(),
                   last_error = NULL
             WHERE dedupe_key = %s
               AND sent_at IS NULL
               AND next_attempt_at <= NOW()
               AND (
                    status IN ('pending', 'failed')
                    OR (status = 'sending'
                        AND last_attempt_at < NOW() - INTERVAL
                            '{STALE_CLAIM_MINUTES} minutes')
               )
            RETURNING dedupe_key, subject, html_content, attempt_count
        """, (key,))
        row = cur.fetchone()
        conn.commit()
        return row
    finally:
        cur.close()


def _retry_seconds(attempt):
    """5m, 15m, 1h, 6h, then daily until Brevo recovers."""
    schedule = (300, 900, 3600, 21600, 86400)
    return schedule[min(max(int(attempt or 1) - 1, 0), len(schedule) - 1)]


def _finish(conn, key, ok, attempt):
    cur = conn.cursor()
    try:
        if ok:
            cur.execute("""
                UPDATE founder_subscription_alerts
                   SET status = 'sent', sent_at = NOW(), updated_at = NOW(),
                       last_error = NULL
                 WHERE dedupe_key = %s AND status = 'sending'
                   AND attempt_count = %s
            """, (key, attempt))
        else:
            cur.execute("""
                UPDATE founder_subscription_alerts
                   SET status = 'failed', updated_at = NOW(),
                       next_attempt_at = NOW() + (%s * INTERVAL '1 second'),
                       last_error = 'Brevo rejected or could not receive the email; see Render logs'
                 WHERE dedupe_key = %s AND status = 'sending'
                   AND attempt_count = %s
            """, (_retry_seconds(attempt), key, attempt))
        conn.commit()
    finally:
        cur.close()


def deliver(key, conn=None):
    """Claim and send one row. Safe across concurrent gunicorn workers."""
    own = conn is None
    if own:
        try:
            conn = _connect()
        except Exception as exc:
            # The queue row already exists. A DB outage here is therefore a
            # delayed email, not a lost alert or a failed payment webhook; the
            # scheduler will try the pending row again when Postgres recovers.
            print(f"⚠️ [paid_alert] database unavailable for {key}: {exc}",
                  flush=True)
            return False
    try:
        row = _claim(conn, key)
        if not row:
            return False
        subject = row["subject"] if isinstance(row, dict) else row[1]
        content = row["html_content"] if isinstance(row, dict) else row[2]
        attempt = row["attempt_count"] if isinstance(row, dict) else row[3]
        try:
            ok = bool(send_founder_alert_now(subject, content))
        except Exception as exc:                            # pragma: no cover
            print(f"⚠️ [paid_alert] sender raised for {key}: {exc}", flush=True)
            ok = False
        _finish(conn, key, ok, attempt)
        return ok
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"⚠️ [paid_alert] delivery failed for {key}: {exc}", flush=True)
        return False
    finally:
        if own:
            try:
                conn.close()
            except Exception:
                pass


def _deliver_background(key):
    deliver(key)


def kick(key):
    """Best-effort immediate attempt; the durable scheduler is the fallback."""
    try:
        threading.Thread(target=_deliver_background, args=(key,), daemon=True,
                         name="paid-subscription-alert").start()
    except Exception as exc:                               # pragma: no cover
        print(f"⚠️ [paid_alert] could not start immediate sender: {exc}",
              flush=True)


def retry_due(limit=MAX_BATCH):
    """Retry durable pending/failed rows. Each send claims atomically."""
    try:
        conn = _connect()
    except Exception as exc:
        print(f"⚠️ [paid_alert] retry database unavailable: {exc}",
              flush=True)
        return {"due": 0, "sent": 0, "error": str(exc)}
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"""
                SELECT dedupe_key
                  FROM founder_subscription_alerts
                 WHERE sent_at IS NULL
                   AND next_attempt_at <= NOW()
                   AND (
                        status IN ('pending', 'failed')
                        OR (status = 'sending'
                            AND last_attempt_at < NOW() - INTERVAL
                                '{STALE_CLAIM_MINUTES} minutes')
                   )
                 ORDER BY created_at
                 LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
        finally:
            cur.close()
        keys = [r["dedupe_key"] if isinstance(r, dict) else r[0] for r in rows]
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"⚠️ [paid_alert] retry scan failed: {exc}", flush=True)
        return {"due": 0, "sent": 0, "error": str(exc)}
    finally:
        conn.close()

    sent = sum(1 for key in keys if deliver(key))
    return {"due": len(keys), "sent": sent}


_scheduler = None
_scheduler_lock = threading.Lock()


def start_scheduler(app):
    """Retry every five minutes; claims make N gunicorn schedulers safe."""
    global _scheduler
    if not enabled():
        app.logger.info("paid subscription alerts disabled by env")
        return
    if BackgroundScheduler is None:
        app.logger.warning("apscheduler missing — paid alert retries not started")
        return
    with _scheduler_lock:
        if _scheduler is not None:
            return

        def job():
            try:
                result = retry_due()
                if result.get("due") or result.get("error"):
                    app.logger.info("paid subscription alerts: %s", result)
            except Exception as exc:                       # pragma: no cover
                app.logger.error("paid subscription alert retry failed: %s", exc)

        scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
        scheduler.add_job(
            job, "interval", minutes=5, id="paid_subscription_alerts",
            replace_existing=True,
            next_run_time=datetime.datetime.utcnow() + datetime.timedelta(minutes=1))
        scheduler.start()
        _scheduler = scheduler
        app.logger.info("paid subscription alert scheduler started")
