"""
Valmera newsletter + behavioral lifecycle email engine.

What this does
--------------
1. MANUAL broadcasts (unchanged surface): the admin composes a subject + HTML and
   sends it to a chosen segment of verified users.
2. AUTOMATED behavioral lifecycle emails, evaluated once a day by an in-process
   scheduler, targeting the RIGHT segment at the RIGHT moment to reduce churn:
       welcome_activation  new signup, no project yet          -> "make your first edit"
       export_nudge        has edits, never exported           -> "go get your video"   (the churn cliff)
       dormant             active before, idle 5-21 days       -> "your credits are waiting + what's new"
       winback             gone 30+ days                       -> "we shipped a lot"
       weekly_value        active/dormant, once a week         -> one genuinely useful tip
3. A real, working UNSUBSCRIBE (token-signed, one-click compatible) — a legal +
   deliverability requirement for recurring mail.

Correctness / safety
---------------------
* The daily tick is wrapped in a Postgres advisory lock, so even though all 3
  gunicorn workers each run a scheduler, only ONE actually sends on any fire.
* Every automated send is logged to `newsletter_sends`; eligibility queries read
  that log, so the tick is fully IDEMPOTENT — re-running it never double-sends,
  and each user is capped (one lifecycle email per day, per-campaign cooldowns).
* A send that Brevo rejects is logged (status='failed') and NOT counted as sent,
  so it is retried on the next tick instead of being silently swallowed.

Schema is self-provisioned idempotently (ensure_newsletter_schema) — additive
CREATE TABLE / ADD COLUMN IF NOT EXISTS only, never touching models.py. The exact
DDL is also documented for the owner to run by hand if preferred.
"""

import os
import hmac
import hashlib
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from functools import wraps
from datetime import datetime, timedelta
import jwt
from flask import Blueprint, request, jsonify, current_app, Response

from routes.newsletter_content import (
    DEFAULT_TEMPLATES, LIFECYCLE_ORDER, CAMPAIGN_LABELS, DEFAULT_CTA_URL,
    wrap_email, render_tokens,
)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:  # pragma: no cover - APScheduler optional at import time
    BackgroundScheduler = None

newsletter_bp = Blueprint('newsletter', __name__)

ADMIN_EMAIL = "thevalmera@gmail.com"
BREVO_BASE = "https://api.brevo.com/v3"
TICK_LOCK_ID = 918273645  # arbitrary constant for pg_try_advisory_lock

# Backend's own public URL — unsubscribe links must hit the BACKEND directly.
BACKEND_PUBLIC_URL = os.getenv(
    "BACKEND_PUBLIC_URL", "https://entrepreneur-bot-backend.onrender.com"
).rstrip("/")

# Successful "final export" states seen in video_jobs.
EXPORT_STATES = "('done','succeeded','success','completed','ready')"

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

BASE_FILTER = (
    "u.is_verified=1 AND u.email IS NOT NULL AND u.email <> '' "
    "AND u.unsubscribed_at IS NULL"
)
NOT_TODAY = (
    "NOT EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.user_id=u.id "
    "AND s.status='sent' AND s.sent_at::date = CURRENT_DATE)"
)


# ─────────────────────────────────────────────────────────────────────────────
#  DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    return psycopg2.connect(current_app.config['DATABASE_URL'], cursor_factory=RealDictCursor)


def ensure_newsletter_schema(conn):
    """Idempotent, additive DDL. Safe to call on every request / tick."""
    cur = conn.cursor()
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
    default = DEFAULT_TEMPLATES.get(key, {})
    cur = conn.cursor()
    cur.execute("SELECT subject, preheader, body_html, enabled FROM newsletter_templates WHERE key=%s", (key,))
    row = cur.fetchone()
    cur.close()
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
        "enabled": bool(row.get("enabled")),
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
    key = (current_app.config.get('SECRET_KEY') or 'supersecretkey').encode()
    return hmac.new(key, (email or '').lower().encode(), hashlib.sha256).hexdigest()[:40]


def _unsub_url(email):
    from urllib.parse import quote
    return f"{BACKEND_PUBLIC_URL}/newsletter/unsubscribe?e={quote(email or '')}&t={_unsub_token(email)}"


# ─────────────────────────────────────────────────────────────────────────────
#  Sending
# ─────────────────────────────────────────────────────────────────────────────

def _send_one(email, subject, html, unsub_url):
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
        "headers": {
            "List-Unsubscribe": f"<{unsub_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    }
    try:
        res = requests.post(f"{BREVO_BASE}/smtp/email", json=payload, headers=_brevo_headers(), timeout=15)
    except requests.RequestException as e:
        current_app.logger.error("Newsletter send to %s failed (network): %s", email, e)
        return False
    if res.status_code != 201:
        current_app.logger.error("Newsletter send to %s failed: HTTP %s %s",
                                 email, res.status_code, (res.text or "")[:400])
        return False
    return True


def _render_for(tmpl, email, credits):
    """Render a template into a full email for one recipient."""
    unsub = _unsub_url(email)
    body = render_tokens(tmpl["body_html"], cta_url=DEFAULT_CTA_URL, credits=credits, unsub_url=unsub)
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


def _eligible(conn, campaign, weekly_key=None):
    """Recipients (id, email, credits_balance) eligible for a lifecycle campaign."""
    cols = "SELECT u.id, u.email, u.credits_balance FROM users u WHERE "
    if campaign == "welcome_activation":
        sql = cols + f"""{BASE_FILTER}
            AND u.created_at >= NOW() - INTERVAL '4 days'
            AND NOT {HAS_PROJECT}
            AND NOT EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.user_id=u.id AND s.campaign='welcome_activation' AND s.status='sent')
            AND {NOT_TODAY}"""
    elif campaign == "export_nudge":
        sql = cols + f"""{BASE_FILTER}
            AND {HAS_PROJECT}
            AND NOT {HAS_EXPORT}
            AND {LAST_ACTIVE} >= NOW() - INTERVAL '21 days'
            AND u.created_at <= NOW() - INTERVAL '1 day'
            AND NOT EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.user_id=u.id AND s.campaign='export_nudge' AND s.status='sent' AND s.sent_at >= NOW() - INTERVAL '14 days')
            AND {NOT_TODAY}"""
    elif campaign == "dormant":
        sql = cols + f"""{BASE_FILTER}
            AND {LAST_ACTIVE} <= NOW() - INTERVAL '5 days'
            AND {LAST_ACTIVE} > NOW() - INTERVAL '21 days'
            AND ({HAS_PROJECT} OR {HAS_CHAT})
            AND NOT EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.user_id=u.id AND s.campaign='dormant' AND s.status='sent' AND s.sent_at >= NOW() - INTERVAL '21 days')
            AND {NOT_TODAY}"""
    elif campaign == "winback":
        sql = cols + f"""{BASE_FILTER}
            AND {LAST_ACTIVE} <= NOW() - INTERVAL '30 days'
            AND NOT EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.user_id=u.id AND s.campaign='winback' AND s.status='sent' AND s.sent_at >= NOW() - INTERVAL '45 days')
            AND {NOT_TODAY}"""
    elif campaign == "weekly_value":
        sql = cols + f"""{BASE_FILTER}
            AND {LAST_ACTIVE} >= NOW() - INTERVAL '30 days'
            AND NOT EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.user_id=u.id AND s.campaign=%s AND s.status='sent')
            AND {NOT_TODAY}"""
        return _fetch(conn, sql, (weekly_key,))
    else:
        return []
    return _fetch(conn, sql)


def _segment_recipients(conn, segment):
    """Recipients for a MANUAL broadcast segment."""
    cols = "SELECT u.id, u.email, u.credits_balance FROM users u WHERE "
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
    return _fetch(conn, sql)


# ─────────────────────────────────────────────────────────────────────────────
#  The daily tick — the heart of the automation
# ─────────────────────────────────────────────────────────────────────────────

def run_daily_tick(force=False, dry_run=False):
    """Evaluate every lifecycle campaign and send. Idempotent + advisory-locked.

    force=True   ignore the once-a-day / send-hour gate (manual admin trigger).
    dry_run=True compute who WOULD receive each campaign, send nothing.
    """
    conn = get_db()
    try:
        ensure_newsletter_schema(conn)

        # Advisory lock: only one worker/instance runs the body at a time.
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS got", (TICK_LOCK_ID,))
        got_lock = cur.fetchone()["got"]
        cur.close()
        if not got_lock:
            return {"skipped": "locked"}

        try:
            settings = get_settings(conn)
            if not settings.get("master_enabled") and not force:
                return {"skipped": "disabled"}

            now = datetime.utcnow()
            today = now.date()
            if not force and not dry_run:
                if settings.get("last_daily_run") == today:
                    return {"skipped": "already_ran_today"}
                if now.hour < int(settings.get("send_hour_utc") or 15):
                    return {"skipped": "before_send_hour"}

            summary = {"dry_run": dry_run, "campaigns": {}, "recipients": {}}
            emailed = set()

            def process(campaign, recips, tmpl):
                sent = 0
                who = []
                for r in recips:
                    if r["id"] in emailed:
                        continue
                    who.append(r["email"])
                    if dry_run:
                        sent += 1
                        continue
                    subject, html, unsub = _render_for(tmpl, r["email"], r["credits_balance"])
                    ok = _send_one(r["email"], subject, html, unsub)
                    _record_send(conn, r["id"], r["email"], campaign, "sent" if ok else "failed")
                    if ok:
                        emailed.add(r["id"])
                        sent += 1
                summary["campaigns"][campaign] = sent
                summary["recipients"][campaign] = who

            # Lifecycle, in priority order (each user gets at most one per tick).
            for campaign in LIFECYCLE_ORDER:
                tmpl = get_template(conn, campaign)
                if not tmpl["enabled"]:
                    summary["campaigns"][campaign] = "disabled"
                    continue
                process(campaign, _eligible(conn, campaign), tmpl)

            # Weekly value — only on its configured weekday (dry-run previews anytime).
            weekly_day = int(settings.get("weekly_weekday") if settings.get("weekly_weekday") is not None else 1)
            weekly_due = (now.weekday() == weekly_day)
            if settings.get("weekly_enabled") and (weekly_due or dry_run):
                tmpl = get_template(conn, "weekly_value")
                if tmpl["enabled"]:
                    iso = now.isocalendar()
                    weekly_key = f"weekly-{iso[0]}-W{iso[1]:02d}"
                    process(weekly_key, _eligible(conn, "weekly_value", weekly_key=weekly_key), tmpl)
                    summary["weekly_key"] = weekly_key
                    summary["weekly_due"] = weekly_due

            # Mark the automatic run done for today (not on manual force / dry).
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
                  next_run_time=datetime.utcnow() + timedelta(seconds=60),
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
        for key in list(DEFAULT_TEMPLATES.keys()):
            t = get_template(conn, key)
            out.append({
                "key": key,
                "label": CAMPAIGN_LABELS.get(key, key),
                "subject": t["subject"],
                "preheader": t["preheader"],
                "enabled": t["enabled"],
                "is_default": t["is_default"],
            })
        return jsonify({"templates": out}), 200
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

        campaign = "manual-" + datetime.utcnow().strftime("%Y%m%d%H%M")
        sent = failed = 0
        for r in recips:
            unsub = _unsub_url(r["email"])
            body = render_tokens(html_content, credits=r["credits_balance"], unsub_url=unsub)
            html = wrap_email(body, unsub, preheader=render_tokens(subject, credits=r["credits_balance"]))
            subj = render_tokens(subject, credits=r["credits_balance"])
            ok = _send_one(r["email"], subj, html, unsub)
            _record_send(conn, r["id"], r["email"], campaign, "sent" if ok else "failed")
            sent += 1 if ok else 0
            failed += 0 if ok else 1

        return jsonify({
            'message': f'Sent to {sent} users' + (f' ({failed} failed)' if failed else '') + f' · segment: {segment}',
            'sent': sent, 'failed': failed, 'total': len(recips),
        }), 200
    finally:
        conn.close()


@newsletter_bp.route('/campaigns', methods=['GET'])
@admin_required
def get_campaigns():
    res = requests.get(f"{BREVO_BASE}/smtp/statistics/aggregatedReport", headers=_brevo_headers())
    stats = res.json() if res.status_code == 200 else {}
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
                       "You won't receive any more emails from Valmera. Sorry to see you go.",
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
