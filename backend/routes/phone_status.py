"""Read-only founder metrics for the Valmera iPhone widgets."""

from datetime import datetime, time, timedelta, timezone
from functools import wraps
import hashlib
import hmac
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, jsonify, request
import psycopg2
from psycopg2.extras import RealDictCursor

from routes.admin import _scope


phone_status_bp = Blueprint("phone_status", __name__)
DEFAULT_TIMEZONE = "America/Los_Angeles"
TOKEN_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def get_db():
    return psycopg2.connect(current_app.config["DATABASE_URL"],
                            cursor_factory=RealDictCursor)


def _expected_token_digest():
    value = os.getenv("VALMERA_PHONE_TOKEN_SHA256", "").strip().lower()
    return value if TOKEN_DIGEST_RE.fullmatch(value) else None


def phone_token_required(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        expected = _expected_token_digest()
        if not expected:
            return jsonify({"error": "Phone status is not configured"}), 503
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not token:
            return jsonify({"error": "Unauthorized"}), 401
        actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(actual, expected):
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return decorated


def _timezone(name=None):
    candidate = (name or os.getenv("VALMERA_PHONE_TIMEZONE")
                 or DEFAULT_TIMEZONE).strip()
    if len(candidate) > 64 or not re.fullmatch(r"[A-Za-z0-9_+./-]+", candidate):
        candidate = DEFAULT_TIMEZONE
    try:
        return candidate, ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return DEFAULT_TIMEZONE, ZoneInfo(DEFAULT_TIMEZONE)


def _day_window(name=None, now=None):
    tz_name, tz = _timezone(name)
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_now = now_utc.astimezone(tz)
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=tz)
    return tz_name, local_start.astimezone(timezone.utc), now_utc


def _week_window(name=None, now=None):
    tz_name, tz = _timezone(name)
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_now = now_utc.astimezone(tz)
    week_date = local_now.date() - timedelta(days=local_now.weekday())
    current_local = datetime.combine(week_date, time.min, tzinfo=tz)
    previous_local = current_local - timedelta(days=7)
    current_start = current_local.astimezone(timezone.utc)
    previous_start = previous_local.astimezone(timezone.utc)
    previous_cutoff = previous_start + (now_utc - current_start)
    return (tz_name, current_start, now_utc,
            previous_start, previous_cutoff)


def _growth_percent(current, previous):
    if not previous:
        return None
    return round(((current - previous) / previous) * 100.0, 1)


def _iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _count(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone() or {}
    return int(row.get("n") or 0)


@phone_status_bp.route("/admin/phone/status", methods=["GET"])
@phone_token_required
def phone_status():
    tz_name, starts_at, now_utc = _day_window(request.args.get("tz"))
    (_, week_started_at, _, previous_week_started_at,
     previous_week_cutoff) = _week_window(tz_name, now=now_utc)
    scope = _scope("u")
    conn = get_db()
    try:
        with conn.cursor() as cur:
            new_users = _count(cur, f"""
                SELECT COUNT(*) AS n
                  FROM users u
                 WHERE u.is_verified = 1
                   AND {scope}
                   AND u.created_at >= %s AND u.created_at <= %s
            """, (starts_at, now_utc))

            new_subscribers = _count(cur, """
                SELECT COUNT(*) AS n
                  FROM founder_subscription_alerts
                 WHERE event_type != 'historical'
                   AND created_at >= %s AND created_at <= %s
            """, (starts_at, now_utc))

            active_now = _count(cur, """
                SELECT COUNT(DISTINCT COALESCE(NULLIF(device_id, ''), ip)) AS n
                  FROM page_visits
                 WHERE visited_at >= NOW() - INTERVAL '5 minutes'
            """)
            jobs_running = _count(cur, """
                SELECT COUNT(*) AS n FROM video_jobs WHERE state = 'running'
            """)
            total_users = _count(cur, f"""
                SELECT COUNT(*) AS n
                  FROM users u
                 WHERE u.is_verified = 1 AND {scope}
            """)

            cur.execute(f"""
                SELECT
                    COUNT(*) FILTER (
                        WHERE u.created_at >= %s AND u.created_at <= %s
                    ) AS current_week_users,
                    COUNT(*) FILTER (
                        WHERE u.created_at >= %s AND u.created_at < %s
                    ) AS previous_week_users
                  FROM users u
                 WHERE u.is_verified = 1 AND {scope}
            """, (week_started_at, now_utc,
                  previous_week_started_at, previous_week_cutoff))
            week = cur.fetchone() or {}
            current_week_users = int(week.get("current_week_users") or 0)
            previous_week_users = int(week.get("previous_week_users") or 0)

            cur.execute(f"""
                WITH days AS (
                    SELECT generate_series(
                        CURRENT_DATE - INTERVAL '29 days',
                        CURRENT_DATE,
                        INTERVAL '1 day')::date AS day
                ),
                baseline AS (
                    SELECT COUNT(*) AS n
                      FROM users u
                     WHERE u.is_verified = 1
                       AND {scope}
                       AND u.created_at::date < CURRENT_DATE - 29
                ),
                daily AS (
                    SELECT u.created_at::date AS day, COUNT(*) AS added
                      FROM users u
                     WHERE u.is_verified = 1
                       AND {scope}
                       AND u.created_at::date >= CURRENT_DATE - 29
                     GROUP BY u.created_at::date
                )
                SELECT TO_CHAR(d.day, 'YYYY-MM-DD') AS day,
                       COALESCE(a.added, 0) AS added,
                       b.n + SUM(COALESCE(a.added, 0))
                           OVER (ORDER BY d.day) AS total
                  FROM days d
                  CROSS JOIN baseline b
                  LEFT JOIN daily a ON a.day = d.day
                 ORDER BY d.day
            """)
            cumulative = [{
                "day": row["day"],
                "added": int(row.get("added") or 0),
                "total": int(row.get("total") or 0),
            } for row in cur.fetchall()]

            cur.execute(f"""
                WITH weeks AS (
                    SELECT generate_series(
                        DATE_TRUNC('week', CURRENT_DATE) - INTERVAL '7 weeks',
                        DATE_TRUNC('week', CURRENT_DATE),
                        INTERVAL '1 week')::date AS week
                ),
                signup_counts AS (
                    SELECT DATE_TRUNC('week', u.created_at)::date AS week,
                           COUNT(*) AS users
                      FROM users u
                     WHERE u.is_verified = 1
                       AND {scope}
                       AND u.created_at >= DATE_TRUNC('week', CURRENT_DATE)
                                          - INTERVAL '7 weeks'
                     GROUP BY DATE_TRUNC('week', u.created_at)::date
                )
                SELECT TO_CHAR(w.week, 'YYYY-MM-DD') AS week,
                       COALESCE(s.users, 0) AS users
                  FROM weeks w
                  LEFT JOIN signup_counts s ON s.week = w.week
                 ORDER BY w.week
            """)
            weekly = [{
                "week": row["week"],
                "users": int(row.get("users") or 0),
            } for row in cur.fetchall()]

            cur.execute(f"""
                SELECT COUNT(*) AS failed_jobs,
                       COUNT(DISTINCT j.project_id) AS failed_projects
                  FROM video_jobs j
                  JOIN users u ON u.id = j.user_id
                 WHERE j.state = 'failed'
                   AND j.updated_at >= %s AND j.updated_at <= %s
                   AND {scope}
            """, (starts_at, now_utc))
            failed = cur.fetchone() or {}
            failed_jobs = int(failed.get("failed_jobs") or 0)
            failed_projects = int(failed.get("failed_projects") or 0)

            # Literal LIKE wildcards are doubled because this parameterized
            # psycopg2 query also contains ``%s`` placeholders.
            refusal_predicate = """
                (UPPER(cm.content) LIKE '%%→ REJECTED%%'
                 OR UPPER(cm.content) LIKE '%%→ CORRECTION_NEEDED%%'
                 OR UPPER(cm.content) LIKE '%%→ CORRECTION NEEDED%%'
                 OR UPPER(cm.content) LIKE '%%→ RECIPE ABORTED%%')
            """
            tool_refusals = _count(cur, f"""
                SELECT COUNT(*) AS n
                  FROM chat_messages cm
                  JOIN projects p ON p.chat_session_id = cm.session_id
                  JOIN users u ON u.id = p.user_id
                 WHERE cm.role = 'activity'
                   AND cm.created_at >= %s AND cm.created_at <= %s
                   AND {refusal_predicate}
                   AND {scope}
            """, (starts_at, now_utc))

            upload_failures = _count(cur, f"""
                SELECT COUNT(*) AS n
                  FROM client_events ce
                  JOIN users u ON u.id = ce.user_id
                 WHERE ce.kind IN ('upload_rejected', 'upload_failed')
                   AND ce.created_at >= %s AND ce.created_at <= %s
                   AND {scope}
            """, (starts_at, now_utc))

        return jsonify({
            "meta": {
                "generated_at": _iso(now_utc),
                "day_started_at": _iso(starts_at),
                "timezone": tz_name,
                "refresh_seconds": 900,
            },
            "summary": {
                "total_users": total_users,
                "new_users": new_users,
                "new_subscribers": new_subscribers,
                "active_now": active_now,
                "jobs_running": jobs_running,
                "failed_jobs": failed_jobs,
                "failed_projects": failed_projects,
                "tool_refusals": tool_refusals,
                "upload_failures": upload_failures,
            },
            "growth": {
                "weekly_rate": _growth_percent(
                    current_week_users, previous_week_users),
                "current_week_users": current_week_users,
                "previous_week_users": previous_week_users,
                "cumulative": cumulative,
                "weekly": weekly,
            },
            "cohorts": [],
            "warnings": [],
        }), 200
    finally:
        conn.close()
