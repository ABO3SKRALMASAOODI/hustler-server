"""Post-signup onboarding card + plan-intent tracking.

Two questions the product could not answer before this module existed:

  1. Where do users come from, and what do they actually want? Analytics gave
     us referrer strings, which say "google" for half the world and nothing
     about intent. Three questions asked once, right after signup, answer it
     directly.
  2. Is anyone interested in paying? Checkout completion is the only signal we
     had, and it is far too late — nobody completed. `plan_intents` records the
     PRESS of a trial button, so interest is visible even when it does not
     convert.

Device is NOT asked. The User-Agent already answers "phone or laptop", so
spending one of three precious onboarding questions on it would be a waste;
`stamp_device` writes it onto the user row on every auth event instead.
"""

import jwt
import psycopg2
from functools import wraps
from flask import Blueprint, request, jsonify, current_app
from psycopg2.extras import RealDictCursor

import plan_gate

onboarding_bp = Blueprint("onboarding", __name__)

ADMIN_EMAIL = "thevalmera@gmail.com"

# The answer sets. Stored as stable slugs, never as display text, so the
# labels can be reworded on the frontend without orphaning historic rows.
CHANNELS = {"tiktok", "instagram", "youtube", "x", "google", "reddit",
            "friend", "ai_chatbot", "other"}
USE_CASES = {"social_clips", "youtube_videos", "ads_marketing", "podcast",
             "course_tutorial", "client_work", "personal", "other"}
GOALS = {"save_time", "no_editing_skill", "more_output", "better_quality",
         "cut_costs", "just_exploring", "other"}

# A free-text "other" is a real answer, not a comment box — but it is also the
# one field a stranger can write anything into, so it is length-capped.
OTHER_MAX = 280


def get_db():
    return psycopg2.connect(current_app.config["DATABASE_URL"],
                            cursor_factory=RealDictCursor)


# ── device detection ─────────────────────────────────────────────────────

def parse_device(ua):
    """phone / tablet / laptop from a User-Agent.

    Deliberately returns the words the ADMIN wants to read ("phone",
    "laptop") rather than analytics jargon ("mobile", "desktop") — the whole
    point of this field is that a human glances at it and knows.

    iPad is checked BEFORE the generic mobile markers because iPadOS 13+
    sends a desktop-Safari UA on some builds and a "Mobile" one on others;
    the tablet markers are the only reliable discriminator and must win.
    """
    u = (ua or "").lower()
    if any(x in u for x in ("ipad", "tablet", "playbook", "silk")):
        return "tablet"
    if any(x in u for x in ("iphone", "ipod", "android", "mobile",
                            "blackberry", "windows phone", "opera mini")):
        # Android tablets say "Android" without "Mobile".
        if "android" in u and "mobile" not in u:
            return "tablet"
        return "phone"
    return "laptop"


def parse_browser(ua):
    u = (ua or "").lower()
    if "edg/" in u or "edge/" in u:
        return "Edge"
    if "opr/" in u or "opera" in u:
        return "Opera"
    if "chrome/" in u and "chromium" not in u:
        return "Chrome"
    if "firefox/" in u:
        return "Firefox"
    if "safari/" in u and "chrome" not in u:
        return "Safari"
    return "Other"


def stamp_device(conn, user_id, ua):
    """Record the device this user is on. Best-effort by contract: this runs
    inside login/register, and an analytics write must never be able to fail
    an authentication. Callers pass their own connection so it joins the
    surrounding transaction rather than opening a second one per login.
    """
    if not user_id:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE users
                      SET device_type = %s, device_browser = %s,
                          last_user_agent = %s, last_seen_at = NOW()
                    WHERE id = %s""",
                (parse_device(ua), parse_browser(ua), (ua or "")[:500],
                 user_id))
    except Exception as e:                                   # pragma: no cover
        print(f"[onboarding] stamp_device failed for {user_id}: {e}",
              flush=True)


# ── auth helpers ─────────────────────────────────────────────────────────

def _token_user():
    """(user_id, email) from the bearer token, or (None, None)."""
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Bearer "):
        return None, None
    try:
        data = jwt.decode(hdr[7:], current_app.config["SECRET_KEY"],
                          algorithms=["HS256"])
        return data.get("sub"), data.get("email", "")
    except Exception:
        return None, None


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id, email = _token_user()
        if not user_id:
            return jsonify({"error": "Token is missing or invalid"}), 401
        return f(user_id=user_id, email=email, *args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        _, email = _token_user()
        if not email:
            return jsonify({"error": "Unauthorized"}), 401
        if email != ADMIN_EMAIL:
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)
    return decorated


def _clean(value, allowed):
    v = (value or "").strip().lower()
    return v if v in allowed else None


def _clean_other(value):
    v = (value or "").strip()
    return v[:OTHER_MAX] or None


# ── user-facing ──────────────────────────────────────────────────────────

@onboarding_bp.route("/onboarding/status", methods=["GET"])
@token_required
def status(user_id, email):
    """What the studio must show before it lets someone in.

    Two gates, in order: the three questions, then the plan choice. They are
    reported together from one call because the studio needs both before it
    renders anything — asking twice would flash the editor in between.

    Also stamps the device, so a user who signed up before this shipped still
    gets classified on their next visit.
    """
    conn = get_db()
    try:
        stamp_device(conn, user_id, request.headers.get("User-Agent", ""))
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM onboarding_responses WHERE user_id=%s",
                        (user_id,))
            answered = cur.fetchone() is not None
        conn.commit()
        return jsonify({"needs_onboarding": not answered,
                        # Advisory only — the API enforces this itself on
                        # every route that costs money (backend/plan_gate.py).
                        "needs_plan": plan_gate.needs_plan(conn, user_id)}), 200
    finally:
        conn.close()


@onboarding_bp.route("/onboarding/submit", methods=["POST"])
@token_required
def submit(user_id, email):
    """Save the three answers. Idempotent: re-submitting updates the row
    rather than erroring, so a double-tap on a flaky connection is harmless."""
    data = request.get_json(silent=True) or {}
    skipped = bool(data.get("skipped"))
    ua = request.headers.get("User-Agent", "")

    channel = _clean(data.get("channel"), CHANNELS)
    use_case = _clean(data.get("use_case"), USE_CASES)
    goal = _clean(data.get("goal"), GOALS)

    conn = get_db()
    try:
        stamp_device(conn, user_id, ua)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO onboarding_responses
                       (user_id, channel, channel_other, use_case,
                        use_case_other, goal, goal_other, device_type,
                        skipped, completed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s THEN NULL ELSE NOW() END)
                   ON CONFLICT (user_id) DO UPDATE SET
                       channel        = EXCLUDED.channel,
                       channel_other  = EXCLUDED.channel_other,
                       use_case       = EXCLUDED.use_case,
                       use_case_other = EXCLUDED.use_case_other,
                       goal           = EXCLUDED.goal,
                       goal_other     = EXCLUDED.goal_other,
                       device_type    = EXCLUDED.device_type,
                       skipped        = EXCLUDED.skipped,
                       completed_at   = EXCLUDED.completed_at""",
                (user_id, channel, _clean_other(data.get("channel_other")),
                 use_case, _clean_other(data.get("use_case_other")),
                 goal, _clean_other(data.get("goal_other")),
                 parse_device(ua), skipped, skipped))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


@onboarding_bp.route("/billing/intent", methods=["POST"])
def plan_intent():
    """Someone pressed a trial button. Recorded even when logged out — a
    signed-out press is still demand, and losing it would defeat the purpose.
    """
    data = request.get_json(silent=True) or {}
    plan = (data.get("plan") or "").strip().lower()
    # 'mcp' is off the pricing page but stays accepted: historic rows use it,
    # and rejecting it would only lose a press we could still learn from.
    if plan not in ("ai", "ai_pro", "mcp"):
        return jsonify({"error": "unknown plan"}), 400
    billing = "annual" if (data.get("billing") == "annual") else "monthly"

    user_id, email = _token_user()
    ua = request.headers.get("User-Agent", "")
    try:
        price = float(data.get("price_usd") or 0) or None
    except (TypeError, ValueError):
        price = None

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO plan_intents
                       (user_id, email, plan, billing, price_usd,
                        device_type, source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (user_id, email or (data.get("email") or None), plan, billing,
                 price, parse_device(ua),
                 (data.get("source") or "pricing")[:40]))
        conn.commit()
        return jsonify({"ok": True}), 200
    finally:
        conn.close()


# ── admin ────────────────────────────────────────────────────────────────

@onboarding_bp.route("/admin/onboarding", methods=["GET"])
@admin_required
def admin_onboarding():
    """Every answer, plus the roll-ups worth looking at first."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.id, o.user_id, u.email, u.plan, u.created_at AS signed_up,
                       o.channel, o.channel_other, o.use_case, o.use_case_other,
                       o.goal, o.goal_other,
                       COALESCE(o.device_type, u.device_type) AS device_type,
                       u.device_browser, o.skipped, o.created_at
                  FROM onboarding_responses o
                  JOIN users u ON u.id = o.user_id
                 ORDER BY o.created_at DESC
                 LIMIT 500
            """)
            rows = [dict(r) for r in cur.fetchall()]

            def breakdown(col):
                cur.execute(f"""
                    SELECT COALESCE({col}, 'unanswered') AS k, COUNT(*) AS n
                      FROM onboarding_responses
                     WHERE skipped = false
                     GROUP BY 1 ORDER BY n DESC
                """)
                return [dict(r) for r in cur.fetchall()]

            channels = breakdown("channel")
            use_cases = breakdown("use_case")
            goals = breakdown("goal")

            # Answered vs skipped vs never shown — the response rate matters
            # as much as the answers.
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM onboarding_responses
                      WHERE skipped = false)                       AS answered,
                    (SELECT COUNT(*) FROM onboarding_responses
                      WHERE skipped = true)                        AS skipped,
                    (SELECT COUNT(*) FROM users WHERE is_verified = 1) AS users
            """)
            totals = dict(cur.fetchone())

            cur.execute("""
                SELECT COALESCE(device_type, 'unknown') AS k, COUNT(*) AS n
                  FROM users WHERE is_verified = 1
                 GROUP BY 1 ORDER BY n DESC
            """)
            devices = [dict(r) for r in cur.fetchall()]

        return jsonify({"responses": rows, "channels": channels,
                        "use_cases": use_cases, "goals": goals,
                        "devices": devices, "totals": totals}), 200
    finally:
        conn.close()


@onboarding_bp.route("/admin/plan-intents", methods=["GET"])
@admin_required
def admin_plan_intents():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.id, i.user_id, COALESCE(i.email, u.email) AS email,
                       i.plan, i.billing, i.price_usd, i.device_type,
                       i.source, i.created_at, u.plan AS current_plan
                  FROM plan_intents i
                  LEFT JOIN users u ON u.id = i.user_id
                 ORDER BY i.created_at DESC
                 LIMIT 500
            """)
            rows = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT plan, billing, COUNT(*) AS presses,
                       COUNT(DISTINCT COALESCE(user_id::text, email)) AS people
                  FROM plan_intents
                 GROUP BY plan, billing
                 ORDER BY presses DESC
            """)
            summary = [dict(r) for r in cur.fetchall()]
        return jsonify({"intents": rows, "summary": summary}), 200
    finally:
        conn.close()
