"""Valmera marketing email engine.

Twenty-seven distinct messages are selected by real editing progress and send
history. Recent signups receive priority within a shared daily budget; older
customers retain a reserved share. Hidden pre-relaunch/test accounts are out
of the marketing audience, with the owner's main admin account retained.

Automation respects unsubscribe, a 48-hour minimum gap, three marketing emails
per rolling week, one send per lifecycle topic and rotating weekly lessons.
Brevo acceptance is recorded as sent; it is not proof of inbox delivery.
"""

import os
import hmac
import hashlib
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from functools import wraps
from datetime import datetime, timedelta, timezone
import jwt
from flask import Blueprint, request, jsonify, current_app, Response

import brevo_delivery
from routes.admin import _scope as customer_scope
from routes.newsletter_content import (
    DEFAULT_TEMPLATES, LIFECYCLE_ORDER, CAMPAIGN_LABELS, DEFAULT_CTA_URL,
    LIFECYCLE_FAMILIES, CAMPAIGN_FAMILY, WEEKLY_ORDER, CONTENT_VERSION,
    wrap_email, render_tokens, plain_text,
)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover - APScheduler optional at import time
    BackgroundScheduler = None

newsletter_bp = Blueprint('newsletter', __name__)

ADMIN_EMAIL = "thevalmera@gmail.com"
BREVO_BASE = "https://api.brevo.com/v3"
BREVO_API_TIMEOUT = (3.05, 15)
TICK_LOCK_ID = 918273645  # arbitrary constant for pg_try_advisory_lock

# Backend's own public URL — unsubscribe links must hit the BACKEND directly.
BACKEND_PUBLIC_URL = os.getenv(
    "BACKEND_PUBLIC_URL", "https://entrepreneur-bot-backend.onrender.com"
).rstrip("/")

# Successful "final export" states seen in video_jobs.
EXPORT_STATES = "('done','succeeded','success','completed','ready')"

# Lifecycle steps are finite. Family cooldowns also honor historical sends;
# the shared 48-hour / three-per-week ceiling applies across all topics.
EXPORT_NUDGE_COOLDOWN_D = max(3, int(os.getenv("NL_EXPORT_NUDGE_COOLDOWN_D", "4")))
DORMANT_AFTER_D = max(3, int(os.getenv("NL_DORMANT_AFTER_D", "5")))
DORMANT_COOLDOWN_D = int(os.getenv("NL_DORMANT_COOLDOWN_D", "7"))
WINBACK_AFTER_D = int(os.getenv("NL_WINBACK_AFTER_D", "21"))
WINBACK_COOLDOWN_D = max(7, int(os.getenv("NL_WINBACK_COOLDOWN_D", "14")))
FAMILY_COOLDOWN_D = {
    "welcome_activation": 3, "first_cut": 3,
    "export_nudge": EXPORT_NUDGE_COOLDOWN_D, "first_export": 3,
    "dormant": max(3, DORMANT_COOLDOWN_D), "winback": WINBACK_COOLDOWN_D,
}
# The configured primary day (Tuesday by default) plus Thursday.
WEEKLY_EXTRA_WEEKDAY = int(os.getenv("NL_WEEKLY_EXTRA_WEEKDAY", "3"))

# last-activity per user across every signal we have.
LAST_ACTIVE = """GREATEST(
  COALESCE((SELECT MAX(created_at) FROM client_events ce WHERE ce.user_id=u.id), TIMESTAMPTZ 'epoch'),
  COALESCE((SELECT MAX(created_at) FROM video_jobs vj WHERE vj.user_id=u.id), TIMESTAMPTZ 'epoch'),
  COALESCE((SELECT MAX(created_at) FROM projects p WHERE p.user_id=u.id), TIMESTAMPTZ 'epoch'),
  COALESCE((SELECT MAX(created_at) FROM chat_sessions cs WHERE cs.user_id=u.id), TIMESTAMPTZ 'epoch'),
  u.created_at::timestamptz
)"""

HAS_EXPORT = (
    "EXISTS (SELECT 1 FROM video_jobs vj2 WHERE vj2.user_id=u.id "
    f"AND vj2.type ILIKE '%%final%%' AND vj2.state IN {EXPORT_STATES})"
)
HAS_PROJECT = "EXISTS (SELECT 1 FROM projects p WHERE p.user_id=u.id)"
HAS_CHAT = "EXISTS (SELECT 1 FROM chat_sessions cs WHERE cs.user_id=u.id)"
HAS_EDIT = (
    "EXISTS (SELECT 1 FROM edls e JOIN projects p ON p.id=e.project_id "
    "WHERE p.user_id=u.id AND e.version > 1)"
)

BASE_FILTER = (
    "u.is_verified=1 AND u.email IS NOT NULL AND u.email <> '' "
    "AND u.unsubscribed_at IS NULL "
    f"AND (({customer_scope('u')}) OR lower(u.email)='thevalmera@gmail.com')"
)
NOT_TODAY = (
    "NOT EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.user_id=u.id "
    "AND s.status='sent' AND s.sent_at::date = CURRENT_DATE)"
)
CONTACT_CADENCE = (
    "NOT EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.user_id=u.id "
    "AND s.status='sent' AND s.sent_at >= NOW() - INTERVAL '48 hours') "
    "AND (SELECT COUNT(*) FROM newsletter_sends s WHERE s.user_id=u.id "
    "AND s.status='sent' AND s.sent_at >= NOW() - INTERVAL '7 days') < 3"
)
# ─────────────────────────────────────────────────────────────────────────────
#  DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    # The hourly scheduler deliberately uses a session-level advisory lock.
    # When ordinary application traffic is routed through Render's
    # transaction-pooled PgBouncer URL, keep this one low-volume connection on
    # the direct URL so the lock cannot jump server sessions mid-tick.
    direct_url = os.getenv("DIRECT_DATABASE_URL")
    return psycopg2.connect(
        direct_url or current_app.config['DATABASE_URL'],
        cursor_factory=RealDictCursor)


_nl_schema_ready = False


def ensure_newsletter_schema(conn):
    """Idempotent, additive DDL — but NOT free, so it runs once per process.

    THIS FUNCTION TOOK THE WHOLE SITE DOWN (Jul 26 2026, ~22:26-22:35 UTC).

    `ALTER TABLE users ADD COLUMN IF NOT EXISTS ...` needs an ACCESS EXCLUSIVE
    lock on `users` even when the column already exists and the statement is a
    no-op. That is harmless until something else holds any lock on `users` —
    and then it is catastrophic, because Postgres queues lock requests in
    order: once an ACCESS EXCLUSIVE request is waiting, EVERY later query on
    that table waits behind it. `users` is read by essentially every request,
    so one stalled session turned into a total outage in seconds.

    What actually happened: an admin page load left a session `idle in
    transaction` holding a read lock on `users`; the next newsletter request
    queued an ALTER behind it; every subsequent request queued behind the
    ALTER. The backend stopped answering while Postgres, the app and the code
    were all individually "fine".

    Three changes, each of which alone would have prevented it:
      * a process-level flag, so this runs once rather than per request;
      * the ALTER is skipped entirely unless the column is genuinely missing
        (information_schema is a cheap catalog read, no table lock);
      * `lock_timeout` so any DDL that cannot get its lock FAILS in seconds
        instead of queueing and taking the table down with it.
    """
    global _nl_schema_ready
    if _nl_schema_ready:
        return
    cur = conn.cursor()
    # Never let DDL sit in the lock queue. Session-local; the app's normal
    # queries are unaffected.
    cur.execute("SET LOCAL lock_timeout = '3s'")
    cur.execute("""SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'users'
                      AND column_name = 'unsubscribed_at'""")
    if cur.fetchone() is None:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS unsubscribed_at TIMESTAMP")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS newsletter_sends (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            email TEXT,
            campaign TEXT NOT NULL,
            status TEXT DEFAULT 'sent',
            sent_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nl_sends_user ON newsletter_sends(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nl_sends_campaign ON newsletter_sends(campaign)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nl_sends_sent_at ON newsletter_sends(sent_at)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS newsletter_templates (
            key TEXT PRIMARY KEY,
            subject TEXT,
            preheader TEXT,
            body_html TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS newsletter_settings (
            id INTEGER PRIMARY KEY,
            master_enabled BOOLEAN DEFAULT TRUE,
            weekly_enabled BOOLEAN DEFAULT TRUE,
            weekly_weekday INTEGER DEFAULT 1,
            send_hour_utc INTEGER DEFAULT 15,
            last_daily_run DATE,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("INSERT INTO newsletter_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    conn.commit()
    cur.close()
    # Only after a clean run — a failure (including the 3s lock_timeout above)
    # must leave this False so the next request retries rather than assuming a
    # half-built schema is finished.
    _nl_schema_ready = True


def _brevo_headers():
    return {
        "accept": "application/json",
        "api-key": os.getenv("BREVO_API_KEY"),
        "content-type": "application/json",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Auth
# ─────────────────────────────────────────────────────────────────────────────

def _token_email():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    try:
        data = jwt.decode(auth_header[7:], current_app.config['SECRET_KEY'], algorithms=['HS256'])
        return data.get('email')
    except Exception:
        return None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if _token_email() != ADMIN_EMAIL:
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
#  Templates + settings resolution (DB override -> code default)
# ─────────────────────────────────────────────────────────────────────────────

def get_template(conn, key):
    """Merge a DB override (if any) over the code default for `key`."""
    cur = conn.cursor()
    cur.execute("SELECT subject, preheader, body_html, enabled FROM newsletter_templates WHERE key=%s", (key,))
    row = cur.fetchone()
    cur.close()
    return _resolved_template(key, row)


def get_all_templates(conn):
    rows = _fetch(conn, "SELECT key, subject, preheader, body_html, enabled FROM newsletter_templates")
    overrides = {row["key"]: row for row in rows}
    return {key: _resolved_template(key, overrides.get(key)) for key in DEFAULT_TEMPLATES}


def _resolved_template(key, row):
    default = DEFAULT_TEMPLATES.get(key, {})
    if not row:
        return {
            "key": key,
            "subject": default.get("subject", ""),
            "preheader": default.get("preheader", ""),
            "body_html": default.get("body_html", ""),
            "enabled": default.get("enabled", True),
            "is_default": True,
        }
    # A row exists: its content wins where present; enabled always from the row.
    return {
        "key": key,
        "subject": row.get("subject") or default.get("subject", ""),
        "preheader": row.get("preheader") if row.get("preheader") is not None else default.get("preheader", ""),
        "body_html": row.get("body_html") or default.get("body_html", ""),
        "enabled": bool(row.get("enabled")) and key != "offer_50",
        "is_default": row.get("body_html") is None,
    }


def get_settings(conn):
    cur = conn.cursor()
    cur.execute("SELECT master_enabled, weekly_enabled, weekly_weekday, send_hour_utc, last_daily_run FROM newsletter_settings WHERE id=1")
    row = cur.fetchone()
    cur.close()
    if not row:
        return {"master_enabled": True, "weekly_enabled": True, "weekly_weekday": 1,
                "send_hour_utc": 15, "last_daily_run": None}
    return dict(row)


# ─────────────────────────────────────────────────────────────────────────────
#  Unsubscribe tokens
# ─────────────────────────────────────────────────────────────────────────────

def _unsub_token(email):
    key = current_app.config['SECRET_KEY'].encode()
    return hmac.new(key, (email or '').lower().encode(), hashlib.sha256).hexdigest()[:40]


def _unsub_url(email):
    from urllib.parse import quote
    return f"{BACKEND_PUBLIC_URL}/newsletter/unsubscribe?e={quote(email or '')}&t={_unsub_token(email)}"


# ─────────────────────────────────────────────────────────────────────────────
#  Sending
# ─────────────────────────────────────────────────────────────────────────────

def _send_one(email, subject, html, unsub_url, campaign=None):
    """Send one transactional email via Brevo. Returns True on success (HTTP 201).

    Logs the real Brevo status+body on failure (same honesty discipline as the
    verification-code sender) so an outage is diagnosable, not silent.
    """
    payload = {
        "sender": {
            "name": os.getenv("FROM_NAME", "Valmera"),
            "email": os.getenv("FROM_EMAIL", "support@valmera.io"),
        },
        "to": [{"email": email}],
        "subject": subject,
        "htmlContent": html,
        "textContent": plain_text(html),
        "replyTo": {"email": os.getenv("FROM_EMAIL", "support@valmera.io"),
                    "name": os.getenv("FROM_NAME", "Valmera")},
        "headers": {
            "List-Unsubscribe": f"<{unsub_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    }
    if campaign:
        payload["tags"] = ["valmera-lifecycle", campaign, CONTENT_VERSION]
    return brevo_delivery.send_email(
        payload, category="bulk", logger=current_app.logger)


def _render_for(tmpl, email, credits):
    """Render a template into a full email for one recipient."""
    unsub = _unsub_url(email)
    from urllib.parse import urlencode
    cta_url = DEFAULT_CTA_URL + "?" + urlencode({
        "utm_source": "valmera", "utm_medium": "email",
        "utm_campaign": tmpl.get("key", "newsletter"),
        "utm_content": CONTENT_VERSION,
    })
    body = render_tokens(tmpl["body_html"], cta_url=cta_url,
                         credits=credits, unsub_url=unsub, html=True)
    preheader = render_tokens(tmpl.get("preheader", ""), credits=credits, unsub_url=unsub)
    subject = render_tokens(tmpl["subject"], credits=credits)
    html = wrap_email(body, unsub, preheader=preheader)
    return subject, html, unsub


def _record_send(conn, user_id, email, campaign, status):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO newsletter_sends (user_id, email, campaign, status) VALUES (%s,%s,%s,%s)",
        (user_id, email, campaign, status),
    )
    conn.commit()
    cur.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Segment / eligibility queries
# ─────────────────────────────────────────────────────────────────────────────

def _fetch(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    cur.close()
    return rows


def _utc_naive(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _campaign_audience(conn):
    """Read progress once, rather than rescan activity for every email topic."""
    rows = _fetch(conn, f"""
        SELECT u.id, u.email, u.credits_balance, u.created_at,
            {LAST_ACTIVE} AS last_active,
            {HAS_PROJECT} AS has_project, {HAS_EDIT} AS has_edit,
            {HAS_EXPORT} AS has_export, {HAS_CHAT} AS has_chat,
            (SELECT MIN(vj.updated_at) FROM video_jobs vj
             WHERE vj.user_id=u.id AND vj.type ILIKE '%%final%%'
                 AND vj.state IN {EXPORT_STATES}) AS first_export_at
        FROM users u WHERE {BASE_FILTER} AND {NOT_TODAY} AND {CONTACT_CADENCE}
        ORDER BY u.id
    """)
    if not rows:
        return []
    rows = [dict(row, sent_history={}) for row in rows]
    by_id = {row["id"]: row for row in rows}
    history = _fetch(conn, """
        SELECT user_id, campaign, MAX(sent_at) AS sent_at FROM newsletter_sends
        WHERE user_id=ANY(%s) AND status='sent' GROUP BY user_id, campaign
    """, (list(by_id),))
    for item in history:
        by_id[item["user_id"]]["sent_history"][item["campaign"]] = _utc_naive(item["sent_at"])
    for row in rows:
        for key in ("created_at", "last_active", "first_export_at"):
            row[key] = _utc_naive(row.get(key))
        row["last_contact_at"] = max(row["sent_history"].values(), default=None)
    return rows


def _matches_campaign(row, campaign, now, weekly_key=None):
    history = row["sent_history"]
    if campaign == "weekly_value":
        return (row["last_active"] >= now - timedelta(days=30)
                and not any(key == weekly_key or key.startswith((weekly_key or "weekly") + ":")
                            for key in history))
    family = CAMPAIGN_FAMILY.get(campaign)
    if not family or campaign in history:
        return False
    last_family = max((history[key] for key in LIFECYCLE_FAMILIES[family] if key in history), default=datetime.min)
    if last_family >= now - timedelta(days=FAMILY_COOLDOWN_D[family]):
        return False
    if family == "welcome_activation":
        return (now - timedelta(days=10) <= row["created_at"] <= now - timedelta(hours=1)
                and not row["has_project"])
    if family == "first_cut":
        return (row["has_project"] and not row["has_edit"] and not row["has_export"]
                and row["last_active"] >= now - timedelta(days=7)
                and row["created_at"] <= now - timedelta(days=1))
    if family == "export_nudge":
        return (row["has_edit"] and not row["has_export"]
                and row["last_active"] >= now - timedelta(days=DORMANT_AFTER_D)
                and row["created_at"] <= now - timedelta(days=1))
    if family == "first_export":
        return bool(row["first_export_at"] and row["first_export_at"] >= now - timedelta(days=7))
    if family == "dormant":
        return (now - timedelta(days=WINBACK_AFTER_D) < row["last_active"] <= now - timedelta(days=DORMANT_AFTER_D)
                and (row["has_project"] or row["has_chat"]))
    return row["last_active"] <= now - timedelta(days=WINBACK_AFTER_D)


def _eligible(conn, campaign, weekly_key=None):
    """Compatibility entry point; retired offers never query the database."""
    if campaign == "offer_50" or (campaign not in CAMPAIGN_FAMILY and campaign != "weekly_value"):
        return []
    now = datetime.utcnow()
    return [row for row in _campaign_audience(conn)
            if _matches_campaign(row, campaign, now, weekly_key)]


def _weekly_choices(conn, recips, templates, now=None):
    """Choose an unseen lesson before repeating one, no sooner than six weeks."""
    now = now or datetime.utcnow()
    choices = []
    for recipient in recips:
        last = {}
        for campaign, stamp in recipient["sent_history"].items():
            if campaign.startswith("weekly-"):
                topic = campaign.partition(":")[2]
                if topic in WEEKLY_ORDER:
                    last[topic] = max(last.get(topic, datetime.min), stamp)
        available = [key for key in WEEKLY_ORDER if key in templates]
        available.sort(key=lambda key: (last.get(key, datetime.min), WEEKLY_ORDER.index(key)))
        if available and last.get(available[0], datetime.min) <= now - timedelta(days=42):
            choices.append((recipient, available[0]))
    return choices


def _segment_recipients(conn, segment):
    """Recipients for a MANUAL broadcast segment."""
    cols = """SELECT u.id, u.email, u.credits_balance, u.created_at,
        (SELECT MAX(s.sent_at) FROM newsletter_sends s
         WHERE s.user_id=u.id AND s.status='sent') AS last_contact_at
        FROM users u WHERE """
    seg = (segment or "all").lower()
    if seg == "active":
        sql = cols + f"{BASE_FILTER} AND {LAST_ACTIVE} >= NOW() - INTERVAL '3 days'"
    elif seg == "dormant":
        sql = cols + f"{BASE_FILTER} AND {LAST_ACTIVE} <= NOW() - INTERVAL '3 days' AND {LAST_ACTIVE} > NOW() - INTERVAL '30 days'"
    elif seg == "inactive":
        sql = cols + f"{BASE_FILTER} AND {LAST_ACTIVE} <= NOW() - INTERVAL '30 days'"
    elif seg == "new":
        sql = cols + f"{BASE_FILTER} AND u.created_at >= NOW() - INTERVAL '7 days'"
    elif seg == "paid":
        sql = cols + f"{BASE_FILTER} AND u.plan IS NOT NULL AND u.plan <> 'free'"
    else:  # all
        sql = cols + BASE_FILTER
    return _fetch(conn, sql + f" AND {CONTACT_CADENCE}")


# ─────────────────────────────────────────────────────────────────────────────
#  The daily tick — the heart of the automation
# ─────────────────────────────────────────────────────────────────────────────

def _prioritize_recipients(plan, quota, now):
    """Give recent signups 75% of capacity and older users at least 25%.

    Unused shares flow to the other group. Older users are ordered by their
    last successful contact so a fixed handful cannot monopolize that share.
    """
    cutoff = now - timedelta(days=30)
    recent = [item for item in plan if item["recipient"]["created_at"] >= cutoff]
    older = [item for item in plan if item["recipient"]["created_at"] < cutoff]
    recent.sort(key=lambda item: (item["recipient"]["created_at"], item["recipient"]["id"]), reverse=True)
    older.sort(key=lambda item: (item["recipient"].get("last_contact_at") or datetime.min,
                                 item["recipient"]["id"]))
    quota = max(0, int(quota))
    older_share = max(1, quota // 4) if older and quota > 1 else 0
    recent_take = min(len(recent), quota - older_share)
    older_take = min(len(older), quota - recent_take)
    selected = recent[:recent_take] + older[:older_take]
    spare = quota - len(selected)
    selected += recent[recent_take:recent_take + spare]
    return selected


def _campaign_plan(conn, now, settings):
    """Plan once, deduplicate across campaigns, then allocate the daily quota."""
    templates = get_all_templates(conn)
    audience = _campaign_audience(conn)
    plan, assigned = [], set()
    def add(recipient, topic, campaign=None):
        if recipient["id"] not in assigned:
            assigned.add(recipient["id"])
            plan.append({"recipient": recipient, "topic": topic,
                         "campaign": campaign or topic, "template": templates[topic]})

    def lifecycle(keys):
        for key in keys:
            if templates[key]["enabled"]:
                for recipient in audience:
                    if _matches_campaign(recipient, key, now):
                        add(recipient, key)

    early = [key for key in LIFECYCLE_ORDER
             if CAMPAIGN_FAMILY[key] not in ("dormant", "winback")]
    lifecycle(early)
    primary = settings.get("weekly_weekday")
    primary = int(primary if primary is not None else 1)
    extra = WEEKLY_EXTRA_WEEKDAY >= 0 and WEEKLY_EXTRA_WEEKDAY != primary
    weekly_due = now.weekday() == primary or (extra and now.weekday() == WEEKLY_EXTRA_WEEKDAY)
    weekly_key = None
    if settings.get("weekly_enabled") and weekly_due:
        iso = now.isocalendar()
        weekly_key = f"weekly-{iso[0]}-W{iso[1]:02d}"
        if now.weekday() != primary:
            weekly_key += "-b"
        topics = {key: templates[key] for key in WEEKLY_ORDER if templates[key]["enabled"]}
        recips = [r for r in audience if r["id"] not in assigned
                  and _matches_campaign(r, "weekly_value", now, weekly_key)]
        for recipient, topic in _weekly_choices(conn, recips, topics, now):
            add(recipient, topic, f"{weekly_key}:{topic}")
    lifecycle([key for key in LIFECYCLE_ORDER if key not in early])
    return plan, weekly_due, weekly_key


def run_daily_tick(force=False, dry_run=False):
    """Send the quota-limited plan; a dry run uses the identical allocation.

    Force only bypasses timing. It never revives retired templates, overrides
    the master pause, ignores opt-outs, or increases the account's send budget.
    """
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS got", (TICK_LOCK_ID,))
        got_lock = cur.fetchone()["got"]
        cur.close()
        if not got_lock:
            return {"skipped": "locked"}
        try:
            settings = get_settings(conn)
            if not settings.get("master_enabled"):
                return {"skipped": "disabled"}
            now = datetime.utcnow()
            today = now.date()
            if not force and not dry_run:
                if settings.get("last_daily_run") == today:
                    return {"skipped": "already_ran_today"}
                hour = settings.get("send_hour_utc")
                if now.hour < int(hour if hour is not None else 15):
                    return {"skipped": "before_send_hour"}
            budget = brevo_delivery.budget_status()
            # A zero/unknown provider allowance must not create another Brevo
            # backlog. Leave the daily marker open so an hourly tick can resume.
            if budget["bulk_remaining"] <= 0 and not dry_run:
                return {"skipped": "email_capacity", "budget": budget}
            plan, weekly_due, weekly_key = _campaign_plan(conn, now, settings)
            selected = _prioritize_recipients(plan, budget["bulk_remaining"], now)
            summary = {
                "dry_run": dry_run, "content_version": CONTENT_VERSION,
                "campaigns": {key: 0 for key in DEFAULT_TEMPLATES if key != "offer_50"},
                "recipients": {}, "eligible_total": len(plan),
                "scheduled_total": len(selected), "deferred": len(plan) - len(selected),
                "weekly_due": weekly_due, "weekly_key": weekly_key,
                "recent_selected": sum(item["recipient"]["created_at"] >= now - timedelta(days=30)
                                       for item in selected),
                "older_selected": sum(item["recipient"]["created_at"] < now - timedelta(days=30)
                                      for item in selected),
                "budget": budget,
            }
            for item in selected:
                r, topic, campaign = item["recipient"], item["topic"], item["campaign"]
                summary["recipients"].setdefault(topic, []).append(r["email"])
                if dry_run:
                    summary["campaigns"][topic] += 1
                    continue
                subject, html, unsub = _render_for(item["template"], r["email"], r["credits_balance"])
                ok = _send_one(r["email"], subject, html, unsub, campaign=topic)
                _record_send(conn, r["id"], r["email"], campaign, "sent" if ok else "failed")
                if ok:
                    summary["campaigns"][topic] += 1
                else:
                    summary["failed"] = summary.get("failed", 0) + 1
                    # A provider outage or depleted allowance must not burn
                    # through the rest of the audience with futile sends.
                    if brevo_delivery.budget_status()["bulk_remaining"] <= 0:
                        break
            if not force and not dry_run:
                c2 = conn.cursor()
                c2.execute("UPDATE newsletter_settings SET last_daily_run=%s, updated_at=NOW() WHERE id=1", (today,))
                conn.commit()
                c2.close()
            return summary
        finally:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_unlock(%s)", (TICK_LOCK_ID,))
            cur.close()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Scheduler (in-process, guarded)
# ─────────────────────────────────────────────────────────────────────────────

_scheduler = None


def start_newsletter_scheduler(app):
    """Start the hourly tick. Called once per gunicorn worker; the advisory lock
    inside run_daily_tick guarantees only one worker sends per fire."""
    global _scheduler
    if os.getenv("NEWSLETTER_SCHEDULER", "1") != "1":
        return
    if not app.config.get("DATABASE_URL"):
        return
    if BackgroundScheduler is None:
        app.logger.warning("APScheduler not installed — newsletter automation OFF (manual send still works)")
        return
    if _scheduler is not None:
        return

    def job():
        try:
            with app.app_context():
                result = run_daily_tick()
                if result and not result.get("skipped"):
                    app.logger.info("newsletter tick: %s", result.get("campaigns"))
        except Exception as e:  # never let the scheduler thread die
            app.logger.error("newsletter tick error: %s", e)

    sched = BackgroundScheduler(daemon=True, timezone="UTC")
    sched.add_job(job, "interval", hours=1,
                  next_run_time=(datetime.now(timezone.utc)
                                 + timedelta(seconds=60)),
                  id="nl_tick", max_instances=1, coalesce=True)
    sched.start()
    _scheduler = sched
    app.logger.info("newsletter scheduler started")


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@newsletter_bp.route('/segments', methods=['GET'])
@admin_required
def segments():
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        counts = _fetch(conn, f"""
            SELECT
              COUNT(*) FILTER (WHERE {BASE_FILTER}) AS verified,
              COUNT(*) FILTER (WHERE u.is_verified=1 AND u.unsubscribed_at IS NOT NULL) AS unsubscribed,
              COUNT(*) FILTER (WHERE {BASE_FILTER} AND u.created_at >= NOW() - INTERVAL '7 days') AS new_7d,
              COUNT(*) FILTER (WHERE {BASE_FILTER} AND {LAST_ACTIVE} >= NOW() - INTERVAL '3 days') AS active,
              COUNT(*) FILTER (WHERE {BASE_FILTER} AND {LAST_ACTIVE} <= NOW() - INTERVAL '3 days' AND {LAST_ACTIVE} > NOW() - INTERVAL '30 days') AS dormant,
              COUNT(*) FILTER (WHERE {BASE_FILTER} AND {LAST_ACTIVE} <= NOW() - INTERVAL '30 days') AS inactive,
              COUNT(*) FILTER (WHERE {BASE_FILTER} AND u.plan IS NOT NULL AND u.plan <> 'free') AS paid,
              COUNT(*) FILTER (WHERE {BASE_FILTER} AND {HAS_PROJECT} AND NOT {HAS_EXPORT}) AS never_exported
            FROM users u
        """)[0]

        # Live eligibility counts for each automated campaign right now (dry-run).
        preview = run_daily_tick(dry_run=True)
        eligible = {k: (v if isinstance(v, int) else 0) for k, v in (preview.get("campaigns") or {}).items()}

        return jsonify({"counts": dict(counts), "eligible_now": eligible}), 200
    finally:
        conn.close()


@newsletter_bp.route('/templates', methods=['GET'])
@admin_required
def list_templates():
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        out = []
        for key, t in get_all_templates(conn).items():
            out.append({
                "key": key,
                "label": CAMPAIGN_LABELS.get(key, key),
                "subject": t["subject"],
                "preheader": t["preheader"],
                "enabled": t["enabled"],
                "is_default": t["is_default"],
            })
        return jsonify({"templates": out, "content_version": CONTENT_VERSION}), 200
    finally:
        conn.close()


@newsletter_bp.route('/templates/<key>', methods=['GET'])
@admin_required
def get_one_template(key):
    if key not in DEFAULT_TEMPLATES:
        return jsonify({"error": "Unknown template"}), 404
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        t = get_template(conn, key)
        # A rendered preview with sample values.
        subject, html, _ = _render_for(t, "you@example.com", 150)
        return jsonify({
            "key": key,
            "label": CAMPAIGN_LABELS.get(key, key),
            "subject": t["subject"],
            "preheader": t["preheader"],
            "body_html": t["body_html"],
            "enabled": t["enabled"],
            "is_default": t["is_default"],
            "preview_html": html,
            "preview_subject": subject,
        }), 200
    finally:
        conn.close()


@newsletter_bp.route('/templates/<key>', methods=['PUT'])
@admin_required
def update_template(key):
    if key not in DEFAULT_TEMPLATES:
        return jsonify({"error": "Unknown template"}), 404
    if key == "offer_50":
        return jsonify({"error": "This discount campaign is permanently retired."}), 410
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        current = get_template(conn, key)
        subject = data.get("subject", current["subject"])
        preheader = data.get("preheader", current["preheader"])
        body_html = data.get("body_html", current["body_html"])
        enabled = bool(data.get("enabled", current["enabled"]))
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO newsletter_templates (key, subject, preheader, body_html, enabled, updated_at)
            VALUES (%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (key) DO UPDATE SET
              subject=EXCLUDED.subject, preheader=EXCLUDED.preheader,
              body_html=EXCLUDED.body_html, enabled=EXCLUDED.enabled, updated_at=NOW()
        """, (key, subject, preheader, body_html, enabled))
        conn.commit()
        cur.close()
        return jsonify({"message": "Saved", "template": get_template(conn, key)}), 200
    finally:
        conn.close()


@newsletter_bp.route('/templates/<key>/reset', methods=['POST'])
@admin_required
def reset_template(key):
    if key not in DEFAULT_TEMPLATES:
        return jsonify({"error": "Unknown template"}), 404
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        cur = conn.cursor()
        cur.execute("DELETE FROM newsletter_templates WHERE key=%s", (key,))
        conn.commit()
        cur.close()
        return jsonify({"message": "Reset to default", "template": get_template(conn, key)}), 200
    finally:
        conn.close()


@newsletter_bp.route('/settings', methods=['GET'])
@admin_required
def read_settings():
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        s = get_settings(conn)
        s["last_daily_run"] = str(s["last_daily_run"]) if s.get("last_daily_run") else None
        return jsonify({"settings": s}), 200
    finally:
        conn.close()


@newsletter_bp.route('/settings', methods=['PUT'])
@admin_required
def write_settings():
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        s = get_settings(conn)
        master = bool(data.get("master_enabled", s["master_enabled"]))
        weekly = bool(data.get("weekly_enabled", s["weekly_enabled"]))
        weekday = int(data.get("weekly_weekday", s["weekly_weekday"]))
        hour = int(data.get("send_hour_utc", s["send_hour_utc"]))
        weekday = max(0, min(6, weekday))
        hour = max(0, min(23, hour))
        cur = conn.cursor()
        cur.execute("""UPDATE newsletter_settings SET master_enabled=%s, weekly_enabled=%s,
                       weekly_weekday=%s, send_hour_utc=%s, updated_at=NOW() WHERE id=1""",
                    (master, weekly, weekday, hour))
        conn.commit()
        cur.close()
        return jsonify({"message": "Saved"}), 200
    finally:
        conn.close()


@newsletter_bp.route('/test-send', methods=['POST'])
@admin_required
def test_send():
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    if key not in DEFAULT_TEMPLATES:
        return jsonify({"error": "Unknown template"}), 404
    if key == "offer_50":
        return jsonify({"error": "This discount campaign is permanently retired."}), 410
    to = (data.get("email") or _token_email() or ADMIN_EMAIL).strip()
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        t = get_template(conn, key)
        subject, html, unsub = _render_for(t, to, 150)
        ok = _send_one(to, f"[TEST] {subject}", html, unsub)
        if not ok:
            return jsonify({"error": "Send failed — check server logs / Brevo"}), 502
        return jsonify({"message": f"Test '{key}' sent to {to}"}), 200
    finally:
        conn.close()


@newsletter_bp.route('/run-tick', methods=['POST'])
@admin_required
def manual_tick():
    dry = str(request.args.get("dry", "")).lower() in ("1", "true", "yes")
    result = run_daily_tick(force=not dry, dry_run=dry)
    return jsonify(result), 200


@newsletter_bp.route('/sends', methods=['GET'])
@admin_required
def recent_sends():
    limit = min(int(request.args.get("limit", 100)), 500)
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        rows = _fetch(conn, """
            SELECT email, campaign, status, sent_at FROM newsletter_sends
            ORDER BY sent_at DESC LIMIT %s
        """, (limit,))
        stats = _fetch(conn, """
            SELECT campaign, COUNT(*) FILTER (WHERE status='sent') AS sent,
                   COUNT(*) FILTER (WHERE status='failed') AS failed
            FROM newsletter_sends GROUP BY campaign ORDER BY MAX(sent_at) DESC
        """)
        return jsonify({
            "sends": [{"email": r["email"], "campaign": r["campaign"], "status": r["status"],
                       "sent_at": str(r["sent_at"])} for r in rows],
            "by_campaign": [dict(s) for s in stats],
            "budget": brevo_delivery.budget_status(),
        }), 200
    finally:
        conn.close()


@newsletter_bp.route('/subscribers', methods=['GET'])
@admin_required
def get_subscribers():
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS count FROM users u WHERE {BASE_FILTER}")
        total = cur.fetchone()['count']
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        cur.execute(f"""SELECT email, plan, created_at FROM users u WHERE {BASE_FILTER}
                        ORDER BY created_at DESC LIMIT %s OFFSET %s""", (limit, offset))
        users = cur.fetchall()
        cur.close()
        return jsonify({
            'subscribers': [{"email": u['email'], "plan": u.get('plan', 'free'), "joined": str(u.get('created_at', ''))} for u in users],
            'total': total,
        }), 200
    finally:
        conn.close()


@newsletter_bp.route('/send', methods=['POST'])
@admin_required
def send_newsletter():
    """Manual broadcast to a chosen segment (per-recipient, personalized unsub)."""
    data = request.get_json(silent=True) or {}
    subject = (data.get('subject') or '').strip()
    html_content = (data.get('htmlContent') or '').strip()
    segment = (data.get('segment') or 'all').strip().lower()
    if not subject:
        return jsonify({'error': 'Subject is required'}), 400
    if not html_content:
        return jsonify({'error': 'HTML content is required'}), 400

    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        recips = _segment_recipients(conn, segment)
        if not recips:
            return jsonify({'error': f'No recipients in segment "{segment}"'}), 400

        budget = brevo_delivery.budget_status()
        quota = budget["bulk_remaining"]
        original_total = len(recips)
        recips = [item["recipient"] for item in _prioritize_recipients(
            [{"recipient": row} for row in recips], quota, datetime.utcnow())]
        deferred = original_total - len(recips)
        campaign = "manual-" + datetime.utcnow().strftime("%Y%m%d%H%M")
        sent = failed = 0
        for r in recips:
            unsub = _unsub_url(r["email"])
            body = render_tokens(html_content, credits=r["credits_balance"], unsub_url=unsub, html=True)
            html = wrap_email(body, unsub, preheader=render_tokens(subject, credits=r["credits_balance"]))
            subj = render_tokens(subject, credits=r["credits_balance"])
            ok = _send_one(r["email"], subj, html, unsub)
            _record_send(conn, r["id"], r["email"], campaign, "sent" if ok else "failed")
            sent += 1 if ok else 0
            failed += 0 if ok else 1

        return jsonify({
            'message': f'Sent to {sent} users' + (f' ({failed} failed)' if failed else '') + (f' · {deferred} deferred by daily reserve' if deferred else '') + f' · segment: {segment}',
            'sent': sent, 'failed': failed, 'deferred': deferred,
            'total': original_total, 'budget': brevo_delivery.budget_status(),
        }), 200
    finally:
        conn.close()


@newsletter_bp.route('/campaigns', methods=['GET'])
@admin_required
def get_campaigns():
    try:
        res = requests.get(
            f"{BREVO_BASE}/smtp/statistics/aggregatedReport",
            headers=_brevo_headers(), timeout=BREVO_API_TIMEOUT)
    except requests.RequestException as error:
        print(f"⚠️ Brevo campaign statistics unavailable: {error}",
              flush=True)
        return jsonify({"error": "Email statistics are temporarily "
                                 "unavailable.",
                        "retryable": True}), 503
    if res.status_code != 200:
        return jsonify({"error": "Email statistics provider rejected the "
                                 "request.",
                        "retryable": res.status_code >= 500}), 502
    try:
        stats = res.json()
    except (TypeError, ValueError):
        return jsonify({"error": "Email statistics provider returned an "
                                 "invalid response.",
                        "retryable": True}), 502
    if not isinstance(stats, dict):
        return jsonify({"error": "Email statistics provider returned an "
                                 "invalid response.",
                        "retryable": True}), 502
    return jsonify({'stats': {
        'delivered': stats.get('delivered', 0),
        'opens': stats.get('uniqueOpens', 0),
        'clicks': stats.get('uniqueClicks', 0),
        'blocked': stats.get('blocked', 0),
    }}), 200


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC unsubscribe / resubscribe (no auth — token-signed)
# ─────────────────────────────────────────────────────────────────────────────

def _unsub_page(title, message, show_resub=False, email=None):
    resub = ""
    if show_resub and email:
        from urllib.parse import quote
        url = f"{BACKEND_PUBLIC_URL}/newsletter/resubscribe?e={quote(email)}&t={_unsub_token(email)}"
        resub = f'<p style="margin:18px 0 0;font:400 14px Arial,sans-serif;color:#888;">Changed your mind? <a href="{url}" style="color:#dc2626;">Re-subscribe</a>.</p>'
    return Response(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Valmera</title></head>
<body style="margin:0;background:#0a0a0a;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" height="100%" style="min-height:100vh;"><tr><td align="center" valign="middle" style="padding:40px 16px;">
<table role="presentation" width="480" style="max-width:480px;background:#111;border:1px solid #222;border-radius:16px;"><tr><td style="padding:40px;text-align:center;">
<div style="font:800 24px Arial,sans-serif;color:#fff;margin-bottom:18px;">Valmera<span style="color:#dc2626;">.</span></div>
<h1 style="font:700 20px Arial,sans-serif;color:#fff;margin:0 0 10px;">{title}</h1>
<p style="font:400 15px/1.6 Arial,sans-serif;color:#c9c9c9;margin:0;">{message}</p>
{resub}
</td></tr></table></td></tr></table></body></html>""", mimetype="text/html")


@newsletter_bp.route('/unsubscribe', methods=['GET', 'POST'])
def unsubscribe():
    email = (request.args.get('e') or request.form.get('e') or '').strip()
    token = (request.args.get('t') or request.form.get('t') or '').strip()
    if not email or not token or not hmac.compare_digest(token, _unsub_token(email)):
        if request.method == 'POST':
            return ('', 400)
        return _unsub_page("Invalid link", "This unsubscribe link isn't valid. Please use the link from a recent email."), 400

    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        cur = conn.cursor()
        cur.execute("UPDATE users SET unsubscribed_at=NOW() WHERE lower(email)=lower(%s) AND unsubscribed_at IS NULL", (email,))
        conn.commit()
        cur.close()
    finally:
        conn.close()

    if request.method == 'POST':  # one-click (List-Unsubscribe-Post)
        return ('', 200)
    return _unsub_page("You're unsubscribed",
                       "You won't receive Valmera product emails or editing tips. Essential verification and billing emails remain active.",
                       show_resub=True, email=email)


@newsletter_bp.route('/resubscribe', methods=['GET'])
def resubscribe():
    email = (request.args.get('e') or '').strip()
    token = (request.args.get('t') or '').strip()
    if not email or not token or not hmac.compare_digest(token, _unsub_token(email)):
        return _unsub_page("Invalid link", "This link isn't valid."), 400
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)
        cur = conn.cursor()
        cur.execute("UPDATE users SET unsubscribed_at=NULL WHERE lower(email)=lower(%s)", (email,))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return _unsub_page("You're back in", "You'll receive Valmera emails again. Welcome back.")
