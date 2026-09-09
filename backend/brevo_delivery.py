"""Shared Brevo delivery and the account-wide daily send budget.

Brevo's entry plan accepts 300 transactional emails per UTC day.  Marketing
and lifecycle mail may consume at most 280; verification, dunning, and founder
alerts share the reserved final 20.  The reservation is a PostgreSQL row lock,
so separate Gunicorn workers and scheduler threads cannot race past the cap.
"""

import os

import psycopg2
import requests

from database_config import preferred_database_url


BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_ACCOUNT_URL = "https://api.brevo.com/v3/account"
DAILY_LIMIT = max(1, int(os.getenv("BREVO_DAILY_LIMIT", "300")))
CRITICAL_RESERVE = min(
    DAILY_LIMIT, max(0, int(os.getenv("BREVO_CRITICAL_RESERVE", "20"))))
BULK_LIMIT = min(
    DAILY_LIMIT - CRITICAL_RESERVE,
    max(0, int(os.getenv("BREVO_BULK_LIMIT", "280"))),
)


def provider_capacity():
    """Read Brevo's real credits; the local UTC counter cannot see its queue.

    Brevo can accept SMTP API requests with 201 while holding the messages for
    tomorrow's credits. Admission must therefore precede sending, not infer
    available credits from successful local requests. No account PII is logged.
    """
    key = os.getenv("BREVO_API_KEY")
    if not key:
        return {"remaining": None, "error": "Email provider is not configured"}
    try:
        response = requests.get(
            BREVO_ACCOUNT_URL, headers={"api-key": key, "accept": "application/json"},
            timeout=(3.05, 10))
        if response.status_code != 200:
            return {"remaining": None, "error": f"Email capacity lookup returned HTTP {response.status_code}"}
        plans = response.json().get("plan") or []
        credits = [max(0, int(plan["credits"])) for plan in plans
                   if plan.get("creditsType") == "sendLimit" and "credits" in plan]
        if not credits:
            return {"remaining": None, "error": "Email provider did not report a send allowance"}
        return {"remaining": sum(credits), "error": None}
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        return {"remaining": None, "error": f"Email capacity lookup unavailable ({type(exc).__name__})"}


def _connect():
    return psycopg2.connect(preferred_database_url())


def _ensure_schema(cur):
    """Safe rolling-deploy bridge; migration 026 remains the schema record."""
    cur.execute("SELECT to_regclass('brevo_daily_budget')")
    if cur.fetchone()[0]:
        return
    # Creating a new standalone table does not lock users/newsletter_sends,
    # but still bound catalog-lock waiting so email can fail according to its
    # bulk/critical policy instead of queuing the whole service.
    cur.execute("SET LOCAL lock_timeout = '3s'")
    cur.execute("""CREATE TABLE IF NOT EXISTS brevo_daily_budget (
                       day DATE PRIMARY KEY,
                       bulk_sent INTEGER NOT NULL DEFAULT 0
                           CHECK (bulk_sent >= 0),
                       critical_sent INTEGER NOT NULL DEFAULT 0
                           CHECK (critical_sent >= 0),
                       updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")


def _seed_bulk_count(cur):
    """Count successful newsletter rows already sent before this code booted.

    This matters only on the deployment day.  From the next UTC midnight every
    Brevo sender goes through this module and the counter is complete itself.
    """
    cur.execute("SELECT to_regclass('newsletter_sends')")
    if not cur.fetchone()[0]:
        return 0
    cur.execute("""SELECT COUNT(*) FROM newsletter_sends
                    WHERE status = 'sent'
                      AND sent_at >= CURRENT_DATE
                      AND sent_at < CURRENT_DATE + INTERVAL '1 day'""")
    return min(BULK_LIMIT, int(cur.fetchone()[0] or 0))


def _reserve(category):
    category = "bulk" if category == "bulk" else "critical"
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                seeded_bulk = _seed_bulk_count(cur)
                cur.execute("""INSERT INTO brevo_daily_budget
                                    (day, bulk_sent, critical_sent)
                               VALUES (CURRENT_DATE, %s, 0)
                               ON CONFLICT (day) DO NOTHING""",
                            (seeded_bulk,))
                cur.execute("""SELECT bulk_sent, critical_sent
                                 FROM brevo_daily_budget
                                WHERE day = CURRENT_DATE FOR UPDATE""")
                bulk, critical = (int(v or 0) for v in cur.fetchone())
                total = bulk + critical
                allowed = total < DAILY_LIMIT
                if category == "bulk":
                    allowed = allowed and bulk < BULK_LIMIT
                if not allowed:
                    return False
                column = "bulk_sent" if category == "bulk" else "critical_sent"
                cur.execute(f"""UPDATE brevo_daily_budget
                                   SET {column} = {column} + 1,
                                       updated_at = NOW()
                                 WHERE day = CURRENT_DATE""")
        return True
    finally:
        conn.close()


def _release(category):
    category = "bulk" if category == "bulk" else "critical"
    column = "bulk_sent" if category == "bulk" else "critical_sent"
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute(f"""UPDATE brevo_daily_budget
                                   SET {column} = GREATEST({column} - 1, 0),
                                       updated_at = NOW()
                                 WHERE day = CURRENT_DATE""")
    finally:
        conn.close()


def _report(logger, message, *args):
    if logger is not None:
        logger.error(message, *args)
    else:
        print("⚠️ [brevo] " + (message % args), flush=True)


def send_email(payload, category="critical", logger=None):
    """Send one email if its account-wide budget has room.

    Routine mail fails closed when the budget table is unavailable: losing a
    campaign send is recoverable on tomorrow's tick, while accidentally
    exhausting Brevo is not.  Critical mail fails open during schema/database
    incidents so verification and payment notifications still have a chance.
    A failed Brevo request releases its reservation for a later send.
    """
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        _report(logger, "BREVO_API_KEY unset — not sent: %s",
                payload.get("subject", "(no subject)"))
        return False

    capacity = provider_capacity()
    provider_remaining = capacity["remaining"]
    required = CRITICAL_RESERVE if category == "bulk" else 0
    if provider_remaining is not None and provider_remaining <= required:
        _report(logger, "send deferred: Brevo has %s credits; %s reserve for %s",
                provider_remaining, required, category)
        return False
    if provider_remaining is None and category == "bulk":
        _report(logger, "bulk send deferred: %s", capacity["error"])
        return False

    reserved = False
    budget_unavailable = False
    try:
        reserved = _reserve(category)
    except Exception as exc:
        if category == "bulk":
            _report(logger, "bulk send blocked because budget is unavailable: %s",
                    exc)
            return False
        budget_unavailable = True
        _report(logger, "critical send proceeding without budget lock: %s", exc)

    if not reserved and category == "bulk":
        _report(logger, "bulk daily ceiling reached (%s); reserved %s critical sends",
                BULK_LIMIT, CRITICAL_RESERVE)
        return False
    if not reserved and category != "bulk" and not budget_unavailable:
        # False without an exception means the real 300/day counter is full.
        _report(logger, "critical daily ceiling reached (%s)", DAILY_LIMIT)
        return False

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }
    try:
        response = requests.post(BREVO_SEND_URL, json=payload,
                                 headers=headers, timeout=15)
    except requests.RequestException as exc:
        if reserved:
            try:
                _release(category)
            except Exception:
                pass
        _report(logger, "send failed (network): %s", exc)
        return False
    if response.status_code != 201:
        if reserved:
            try:
                _release(category)
            except Exception:
                pass
        _report(logger, "send failed: HTTP %s %s", response.status_code,
                (response.text or "")[:400])
        return False
    return True


def budget_status():
    """Usable capacity is the smaller of local limits and Brevo's allowance."""
    row = {"bulk_sent": 0, "critical_sent": 0}
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute("""SELECT bulk_sent, critical_sent
                                 FROM brevo_daily_budget
                                WHERE day = CURRENT_DATE""")
                found = cur.fetchone()
                if found:
                    row = {"bulk_sent": int(found[0] or 0),
                           "critical_sent": int(found[1] or 0)}
    finally:
        conn.close()
    total = row["bulk_sent"] + row["critical_sent"]
    provider = provider_capacity()
    available = provider["remaining"]
    local_total_remaining = max(0, DAILY_LIMIT - total)
    local_bulk_remaining = min(max(0, BULK_LIMIT - row["bulk_sent"]), local_total_remaining)
    bulk_remaining = (0 if available is None else
                      min(local_bulk_remaining, max(0, available - CRITICAL_RESERVE)))
    return {
        **row,
        "total_sent": total,
        "bulk_limit": BULK_LIMIT,
        "daily_limit": DAILY_LIMIT,
        "critical_reserve": CRITICAL_RESERVE,
        "bulk_remaining": bulk_remaining,
        "total_remaining": (local_total_remaining if available is None else
                            min(local_total_remaining, available)),
        "provider_remaining": available,
        "provider_capacity_error": provider["error"],
        "local_bulk_remaining": local_bulk_remaining,
    }
