"""The free trial: First cut + a capped per-account editing budget.

Owner decision D2 (25 Oct 2026, strategy-1025/DECISIONS.md), in the owner's
modified form of PLAN.md P2:

- A new account's FIRST project gets a finished first result automatically
  (the deterministic First cut, worker/first_cut.py) and a small trial budget
  for its own requests and revisions on that project. Trial model usage runs
  on Claude through the Anthropic API (worker/claude_lane.py), funded by the
  owner's Anthropic credit, never on the paid OpenAI wallet.
- The paywall sits on EXPORT and DOWNLOAD: a trial user watches in the Studio
  and subscribes to export. A second project follows the ordinary wall. The
  plan cards stay available, non-blocking.
- Budget: about $0.15 of model spend per account (so the credit lasts 3+
  months at ~440 uploaders a month), a global monthly cap (~$70) and a daily
  First-cut count cap; at either cap new admissions stop by themselves and
  the product falls back to today's wall.
- Payers get the First cut on their first project too (PLAN.md C2.4); their
  own requests stay on their contracted plan.

Everything is one ``app_kv`` row, ``free_result`` (JSON; the key shared
with track D, DECISIONS.md "Coordination for D2"), merged over DEFAULTS.
**It ships OFF** (owner override, 25 Oct: D2 is on hold, "keep the paywall
before edit as is"): with ``on`` false nothing is admitted, nobody's access
changes and every gate below is today's wall. Turning it on is one write;
turning it back off is ``{"on": false}``.

State lives in existing columns only (no schema change):
- ``projects.meta.free_result`` marks the one admitted project of an account
  ({"admitted_at", "kind": "trial"|"payer", "first_cut": bool}).
- worker results carry ``trial_usd`` (the measured model spend of a trial
  First cut or trial turn); spend is summed from ``video_jobs``.
"""
import json
import threading
import time
from datetime import datetime, timezone

import psycopg2
from flask import has_app_context

KEY = "free_result"

DEFAULTS = {
    "on": False,
    # Optional rollout time once switched on (ISO 8601); None = immediately.
    "start_at": None,
    # D2 (owner's final design): a $1.00 ceiling of Claude usage per trial
    # account, First cut included; stop granting at the $200 pool or at 40
    # new trial accounts a day.
    "per_account_usd_cap": 1.0,
    "pool_usd_cap": 200.0,
    "monthly_usd_cap": 200.0,
    "daily_count_cap": 40,
    # The First cut analyses at most the first 30 minutes of a long source;
    # larger uploads than this are walled as today.
    "max_upload_bytes": 2 * 1024 ** 3,
    "max_accounts_per_device_30d": 2,
    "payer_first_cut": True,
    # Trial REQUESTS run on the Claude lane in the executors, which need the
    # ANTHROPIC_API_KEY GitHub secret (deploy-cloudflare-executor.yml). Until
    # the owner adds it and this is set true, trial accounts get the First
    # cut and their typed requests meet the plans (kept for replay).
    "trial_requests": False,
    # Internal accounts (strategy-1025 exclusion list) skip start_at once the
    # flag is on (never while it is off). 60 is the owner's agent account and
    # is NOT listed: its MCP factory must never trigger First cuts.
    "internal_user_ids": [22, 29, 46, 61, 1058],
    # Never admitted: the owner's agent account (its MCP factory and the
    # admin bypass of the wall).
    "excluded_user_ids": [60],
}

# Throwaway inboxes. Signups are email-code or Google verified already, so this
# only stops the cheap repeat-trial path; it is not a spam filter.
DISPOSABLE_DOMAINS = frozenset({
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "sharklasers.com",
    "10minutemail.com", "10minutemail.net", "tempmail.com", "temp-mail.org",
    "tempmail.dev", "tempmailo.com", "tempr.email", "throwawaymail.com",
    "yopmail.com", "yopmail.net", "getnada.com", "nada.email", "dispostable.com",
    "trashmail.com", "trashmail.de", "maildrop.cc", "mailnesia.com",
    "fakeinbox.com", "mintemail.com", "mohmal.com", "emailondeck.com",
    "spamgourmet.com", "moakt.com", "tmail.ws", "tmpmail.org", "tmpmail.net",
    "burnermail.io", "inboxkitten.com", "mail.tm", "mailpoof.com",
    "luxusmail.org", "dropmail.me", "emailfake.com", "1secmail.com",
    "1secmail.org", "1secmail.net", "linshiyouxiang.net", "byom.de",
})

_lock = threading.Lock()
_cache = {"value": None, "expires": 0.0}


def _now():
    return datetime.now(timezone.utc)


def _parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def settings(cur=None):
    """DEFAULTS merged with the app_kv row; cached for 30 s. Read through the
    caller's cursor when given, else a private read-only connection. A
    transient read failure keeps the last known value (or DEFAULTS)."""
    with _lock:
        if _cache["value"] is not None and time.monotonic() < _cache["expires"]:
            return dict(_cache["value"])
    stored = None
    try:
        if cur is not None:
            cur.execute("SAVEPOINT free_trial_settings")
            try:
                cur.execute("SELECT value FROM app_kv WHERE key = %s", (KEY,))
                row = cur.fetchone()
                cur.execute("RELEASE SAVEPOINT free_trial_settings")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT free_trial_settings")
                cur.execute("RELEASE SAVEPOINT free_trial_settings")
                raise
            stored = (row or {}).get("value") if isinstance(row, dict) else (
                row[0] if row else None)
        elif has_app_context():
            from database_config import preferred_database_url
            conn = psycopg2.connect(
                preferred_database_url(), connect_timeout=3,
                options="-c default_transaction_read_only=on "
                        "-c statement_timeout=1500")
            try:
                with conn.cursor() as c:
                    c.execute("SELECT value FROM app_kv WHERE key = %s", (KEY,))
                    row = c.fetchone()
                stored = row[0] if row else None
            finally:
                conn.close()
    except Exception:
        with _lock:
            if _cache["value"] is not None:
                return dict(_cache["value"])
        stored = None
    value = dict(DEFAULTS)
    if stored:
        try:
            parsed = json.loads(stored) if isinstance(stored, str) else stored
            if isinstance(parsed, dict):
                value.update(parsed)
        except (TypeError, ValueError):
            pass
    with _lock:
        _cache["value"] = dict(value)
        _cache["expires"] = time.monotonic() + 30
    return value


def _internal(cfg, user_id):
    try:
        return int(user_id) in {int(x) for x in cfg.get("internal_user_ids") or []}
    except (TypeError, ValueError):
        return False


def switched_on(cfg, user_id):
    """The flag and the clock, before any budget or account check."""
    if not cfg.get("on"):
        return False
    if _internal(cfg, user_id):
        return True
    start = _parse_time(cfg.get("start_at"))
    return start is None or _now() >= start


_spend_cache = {"value": None, "expires": 0.0}


def spend_status(cur, fresh=False):
    """Model spend of trial work this calendar month (UTC) and First cuts
    admitted today, from the rows themselves (cached 60 s per process; an
    admission passes fresh=True)."""
    with _lock:
        if not fresh and _spend_cache["value"] is not None \
                and time.monotonic() < _spend_cache["expires"]:
            return dict(_spend_cache["value"])
    value = _spend_status(cur)
    with _lock:
        _spend_cache["value"] = dict(value)
        _spend_cache["expires"] = time.monotonic() + 60
    return value


def _spend_status(cur):
    cur.execute("""SELECT COALESCE(SUM(CASE WHEN result ? 'trial_usd'
                                       THEN (result->>'trial_usd')::float
                                       ELSE 0 END), 0) AS usd
                   FROM video_jobs
                   WHERE type = 'agent_turn'
                     AND payload->>'trial_lane' = 'true'
                     AND created_at >= date_trunc('month', NOW())""")
    usd = float((cur.fetchone() or {}).get("usd") or 0.0)
    cur.execute("""SELECT COALESCE(SUM(CASE WHEN result ? 'trial_usd'
                                       THEN (result->>'trial_usd')::float
                                       ELSE 0 END), 0) AS usd
                   FROM video_jobs
                   WHERE type = 'agent_turn'
                     AND payload->>'trial_lane' = 'true'""")
    all_usd = float((cur.fetchone() or {}).get("usd") or 0.0)
    cur.execute("""SELECT COUNT(*) AS n FROM projects
                   WHERE meta ? 'free_result'
                     AND (meta->'free_result'->>'admitted_at')::timestamptz
                         >= date_trunc('day', NOW())""")
    today = int((cur.fetchone() or {}).get("n") or 0)
    return {"month_usd": usd, "all_usd": all_usd, "first_cuts_today": today}


def account_spend(cur, user_id):
    """Trial model spend of one account. Track D's shared running total
    (backend/claude_trial.account_status, table trial_budgets) is the source
    of truth once it is merged; until then the trial_usd recorded on this
    account's trial jobs."""
    try:
        import claude_trial  # track D (claude/claude-trial-1025)
        status = claude_trial.account_status(cur, user_id) or {}
        if status.get("spent_usd") is not None:
            return float(status["spent_usd"])
    except ImportError:
        pass
    return _own_account_spend(cur, user_id)


def _own_account_spend(cur, user_id):
    cur.execute("""SELECT COALESCE(SUM(CASE WHEN result ? 'trial_usd'
                                       THEN (result->>'trial_usd')::float
                                       ELSE 0 END), 0) AS usd
                   FROM video_jobs
                   WHERE user_id = %s AND type = 'agent_turn'
                     AND payload->>'trial_lane' = 'true'""", (int(user_id),))
    return float((cur.fetchone() or {}).get("usd") or 0.0)


def _account_reason(cur, cfg, user_id):
    """None when this account may be admitted, else the reason it may not."""
    cur.execute("""SELECT email, is_verified FROM users WHERE id = %s""",
                (int(user_id),))
    user = cur.fetchone() or {}
    email = str(user.get("email") or "").lower()
    if not user.get("is_verified"):
        return "unverified"
    if email.rsplit("@", 1)[-1] in DISPOSABLE_DOMAINS:
        return "disposable_email"
    cur.execute("""SELECT device_id FROM website_signups WHERE user_id = %s""",
                (int(user_id),))
    device = (cur.fetchone() or {}).get("device_id")
    if device and not _internal(cfg, user_id):
        cur.execute("""SELECT COUNT(DISTINCT p.user_id) AS n
                       FROM website_signups s
                       JOIN projects p ON p.user_id = s.user_id
                       WHERE s.device_id = %s AND s.user_id <> %s
                         AND p.meta ? 'free_result'
                         AND (p.meta->'free_result'->>'admitted_at')::timestamptz
                             > NOW() - INTERVAL '30 days'""",
                    (device, int(user_id)))
        others = int((cur.fetchone() or {}).get("n") or 0)
        if others + 1 > int(cfg.get("max_accounts_per_device_30d") or 2):
            return "device_limit"
    return None


def project_admission(cur, project_id):
    cur.execute("SELECT meta FROM projects WHERE id = %s", (int(project_id),))
    meta = (cur.fetchone() or {}).get("meta") or {}
    admission = meta.get("free_result")
    return admission if isinstance(admission, dict) else None


def _other_admission(cur, user_id, project_id):
    cur.execute("""SELECT id FROM projects
                   WHERE user_id = %s AND id <> %s AND meta ? 'free_result'
                   LIMIT 1""", (int(user_id), int(project_id)))
    return bool(cur.fetchone())


def admit_upload(cur, user_id, project_id, subscribed, nbytes=None):
    """Called under the project lock when a Studio main-video upload lands.

    Returns the admission dict when this project gets the First cut (and,
    for an unsubscribed account, the trial), else None. Idempotent: an
    already-admitted project returns its admission."""
    existing = project_admission(cur, project_id)
    if existing:
        return existing
    cfg = settings(cur)
    if not switched_on(cfg, user_id):
        return None
    try:
        if int(user_id) in {int(x) for x in cfg.get("excluded_user_ids") or []}:
            return None
    except (TypeError, ValueError):
        return None
    if subscribed and not cfg.get("payer_first_cut", True):
        return None
    if nbytes and int(nbytes) > int(cfg.get("max_upload_bytes") or 0) > 0:
        return None
    if _other_admission(cur, user_id, project_id):
        return None
    spend = spend_status(cur, fresh=True)
    if spend["month_usd"] >= float(cfg.get("monthly_usd_cap") or 0):
        return None
    if spend["first_cuts_today"] >= int(cfg.get("daily_count_cap") or 0):
        return None
    if spend["all_usd"] >= float(cfg.get("pool_usd_cap") or 0):
        return None
    if not subscribed and _account_reason(cur, cfg, user_id):
        return None
    admission = {"admitted_at": _now().isoformat(),
                 "kind": "payer" if subscribed else "trial",
                 "first_cut": True}
    cur.execute("""UPDATE projects
                   SET meta = COALESCE(meta, '{}'::jsonb)
                              || jsonb_build_object('free_result', %s::jsonb)
                   WHERE id = %s""", (json.dumps(admission), int(project_id)))
    return admission


def trial_project(cur, user_id, project_id):
    """The admission when this is the account's trial project (unsubscribed
    accounts only use it as a trial), else None."""
    admission = project_admission(cur, project_id)
    if not admission or admission.get("kind") != "trial":
        return None
    return admission


def request_allowance(cur, user_id, project_id):
    """May an unsubscribed account's request on this project run as a trial
    turn? {"allowed": bool, "reason", "remaining_usd"}."""
    admission = trial_project(cur, user_id, project_id)
    if not admission:
        return {"allowed": False, "reason": "not_trial_project"}
    cfg = settings(cur)
    if not cfg.get("on"):
        return {"allowed": False, "reason": "switched_off"}
    if not cfg.get("trial_requests"):
        return {"allowed": False, "reason": "trial_requests_off"}
    spend = spend_status(cur)
    if spend["month_usd"] >= float(cfg.get("monthly_usd_cap") or 0):
        return {"allowed": False, "reason": "monthly_cap"}
    used = account_spend(cur, user_id)
    remaining = float(cfg.get("per_account_usd_cap") or 0) - used
    if remaining <= 0.002:
        return {"allowed": False, "reason": "trial_spent",
                "remaining_usd": 0.0}
    return {"allowed": True, "reason": None,
            "remaining_usd": round(remaining, 4)}


def status_for_studio(cur, user_id, project_id, subscribed):
    """What the Studio shows: whether this project is the trial project, how
    much trial editing is left (as a share, never dollars), and that export
    needs a plan."""
    admission = project_admission(cur, project_id)
    if not admission:
        return None
    out = {"kind": admission.get("kind"),
           "first_cut": bool(admission.get("first_cut"))}
    if admission.get("kind") == "trial" and not subscribed:
        allowance = request_allowance(cur, user_id, project_id)
        cfg = settings(cur)
        total = float(cfg.get("per_account_usd_cap") or 0) or 1.0
        out.update({
            "export_requires_plan": True,
            "edits_available": bool(allowance.get("allowed")),
            "edits_reason": allowance.get("reason"),
            "edits_left_share": round(max(0.0, min(
                1.0, float(allowance.get("remaining_usd") or 0) / total)), 2),
        })
    return out


# The "your cut is ready" email. NOT SENT anywhere: D2 approves the trial but
# not this copy yet (DECISIONS.md). The owner reviews these words first.
READY_EMAIL_SUBJECT = "Your first cut is ready"
READY_EMAIL_TEXT = (
    "Hi {name},\n\n"
    "Valmera finished a first cut of {title}: {summary}\n\n"
    "Watch it here: {project_url}\n\n"
    "You can ask for changes in the Studio before you decide. To download it "
    "and keep editing, choose a plan in the Studio.\n\n"
    "— Valmera\n"
)


def ready_email(name, title, summary, project_url):
    """(subject, text) for the draft "your cut is ready" email. Not wired to
    any sender."""
    return READY_EMAIL_SUBJECT, READY_EMAIL_TEXT.format(
        name=name or "there", title=title or "your video",
        summary=summary or "a vertical version with captions.",
        project_url=project_url)
