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

State:
- ``projects.meta.free_result`` marks the one admitted project of an account
  ({"admitted_at", "kind": "trial"|"payer", "first_cut": bool}).
- Spend is track D's ledger (migration 040): ``trial_budgets`` per account
  (backend/claude_trial.account_status) and ``trial_budget_days`` for the
  pool, the month and the day. Without migration 040 nothing is admitted.

INTEGRATION (claude/agent-trial-release-1025):
- ``test_user_ids``: internal accounts for which the trial works while
  ``on`` is false (the bake-off and the production proof before the flip).
  Never customers.
- THE AUTOMATIC CREDIT SWITCH (claude_credit.py): while it is off (the
  Anthropic credit of this billing cycle is used up, or Anthropic refused for
  billing) nothing is admitted and admitted projects meet today's pre-edit
  paywall again; the export paywall of a trial project stays either way.
- Accounts that ever paid are not trial accounts (a lapsed subscriber keeps
  today's wall); payers still get the First cut on their first project.
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
    # D2: "just enough" Claude usage per trial account, First cut included,
    # calibrated from the measured cost of a good first outcome (owner, 25
    # Oct: not an arbitrary $1). C2 (29 Oct): Haiku 5.5 p90 $0.034 an edit,
    # worst turns $0.066-0.074, so $0.12 covers a First cut plus 2-3
    # requests. Stop granting at the $200 pool or at 40 new trial accounts a
    # day.
    "per_account_usd_cap": 0.12,
    "pool_usd_cap": 200.0,
    "monthly_usd_cap": 200.0,
    "daily_count_cap": 40,
    # The First cut analyses at most the first 30 minutes of a long source;
    # larger uploads than this are walled as today.
    "max_upload_bytes": 2 * 1024 ** 3,
    "max_accounts_per_device_30d": 2,
    "payer_first_cut": True,
    # Trial REQUESTS run on the Claude lane in the executors (the
    # ANTHROPIC_API_KEY GitHub secret, deploy-cloudflare-executor.yml). A
    # kill switch for requests only: false keeps the First cut and sends
    # typed requests to the plans (kept for replay). Without the key the
    # worker answers a trial request with the plan cards by itself.
    "trial_requests": True,
    # Internal accounts for which the whole trial works while "on" is false
    # (the bake-off; never customers). Empty by default.
    "test_user_ids": [],
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


def is_test(cfg, user_id):
    try:
        return int(user_id) in {int(x) for x in cfg.get("test_user_ids") or []}
    except (TypeError, ValueError):
        return False


def switched_on(cfg, user_id):
    """The flag and the clock, before any budget or account check. An
    internal test account is on while the flag is off."""
    if is_test(cfg, user_id):
        return True
    if not cfg.get("on"):
        return False
    if _internal(cfg, user_id):
        return True
    start = _parse_time(cfg.get("start_at"))
    return start is None or _now() >= start


_credit_cache = {"value": None, "expires": 0.0}


def credit_on(cur, fresh=False):
    """The automatic credit switch (claude_credit.status), cached 20 s per
    process. Any failure reads as OFF: today's wall."""
    with _lock:
        if not fresh and _credit_cache["value"] is not None \
                and time.monotonic() < _credit_cache["expires"]:
            return _credit_cache["value"]
    value = False
    try:
        import claude_credit
        cur.execute("SAVEPOINT free_trial_credit")
        try:
            value = bool(claude_credit.status(cur.connection).get("on"))
            cur.execute("RELEASE SAVEPOINT free_trial_credit")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT free_trial_credit")
            cur.execute("RELEASE SAVEPOINT free_trial_credit")
            value = False
    except Exception:
        value = False
    with _lock:
        _credit_cache["value"] = value
        _credit_cache["expires"] = time.monotonic() + 20
    return value


def live(cur, user_id, cfg=None):
    """The trial is running for this account right now: switched on (or a
    test account) AND the credit switch is on. Off means today's wall."""
    cfg = cfg or settings(cur)
    return switched_on(cfg, user_id) and credit_on(cur)


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


def _ledger_ready(cur):
    cur.execute("SELECT to_regclass('public.trial_budget_days') AS a, "
                "to_regclass('public.trial_budgets') AS b")
    row = cur.fetchone() or {}
    a = row.get("a") if isinstance(row, dict) else row[0]
    b = row.get("b") if isinstance(row, dict) else row[1]
    return bool(a and b)


def _spend_status(cur):
    """Track D's ledger: the trial's spend this calendar month, in this
    Anthropic credit cycle (the pool), and First cuts admitted today."""
    import claude_credit
    if not _ledger_ready(cur):
        # Migration 040 is not applied: nothing can be metered, so nothing
        # is admitted (the caps read as reached).
        inf = float("inf")
        return {"month_usd": inf, "all_usd": inf, "first_cuts_today": 0,
                "ready": False}
    start, end = claude_credit.cycle_window(
        claude_credit.read_config(cur.connection))
    cur.execute("""SELECT
          COALESCE(SUM(spent_usd) FILTER (
              WHERE day >= date_trunc('month', (NOW() AT TIME ZONE 'UTC'))::date),
              0) AS month,
          COALESCE(SUM(spent_usd) FILTER (WHERE day >= %s AND day < %s), 0)
              AS cycle
        FROM trial_budget_days""", (start.date(), end.date()))
    row = cur.fetchone() or {}
    month = float((row.get("month") if isinstance(row, dict) else row[0]) or 0)
    cycle = float((row.get("cycle") if isinstance(row, dict) else row[1]) or 0)
    cur.execute("""SELECT COUNT(*) AS n FROM projects
                   WHERE meta ? 'free_result'
                     AND (meta->'free_result'->>'admitted_at')::timestamptz
                         >= date_trunc('day', NOW())""")
    today = int((cur.fetchone() or {}).get("n") or 0)
    return {"month_usd": month, "all_usd": cycle, "first_cuts_today": today,
            "ready": True}


def account_spend(cur, user_id):
    """Trial model spend of one account. Track D's shared running total
    (backend/claude_trial.account_status, table trial_budgets) is the source
    of truth once it is merged; until then the trial_usd recorded on this
    account's trial jobs."""
    import claude_trial  # track D: trial_budgets, the one running total
    status = claude_trial.account_status(cur, user_id) or {}
    return float(status.get("spent_usd") or 0.0)


def account_remaining(cur, cfg, user_id):
    """What the account's trial budget has left: its grant's cap minus spend,
    or the whole per-account cap before the grant exists."""
    import claude_trial
    status = claude_trial.account_status(cur, user_id) or {}
    if status.get("granted"):
        return float(status.get("remaining_usd") or 0.0)
    return float(cfg.get("per_account_usd_cap") or 0.0)


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
    if email == "thevalmera@gmail.com":
        return "admin"
    # A lapsed subscriber is not a new account (D2: never-paid accounts).
    cur.execute("""SELECT 1 FROM payments
                   WHERE user_id = %s AND amount_cents > 0
                     AND status IN ('completed', 'paid') LIMIT 1""",
                (int(user_id),))
    if cur.fetchone():
        return "paid_before"
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


def _new_subscriber(cur, user_id):
    """A subscriber who joined after the release cutoff (claude_plans).
    Any failure reads as grandfathered: today's behaviour."""
    try:
        import claude_plans
        cur.execute("SAVEPOINT free_trial_new_subscriber")
        try:
            out = bool(claude_plans.applies(cur.connection, user_id, True))
            cur.execute("RELEASE SAVEPOINT free_trial_new_subscriber")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT free_trial_new_subscriber")
            cur.execute("RELEASE SAVEPOINT free_trial_new_subscriber")
            out = False
        return out
    except Exception:
        return False


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
    if not live(cur, user_id, cfg):
        return None
    try:
        if int(user_id) in {int(x) for x in cfg.get("excluded_user_ids") or []}:
            return None
    except (TypeError, ValueError):
        return None
    if subscribed and not cfg.get("payer_first_cut", True):
        return None
    if subscribed and not _new_subscriber(cur, user_id):
        # D1 grandfathering (DECISIONS.md, 25 Oct): EXISTING subscribers keep
        # exactly today's behaviour, so no automatic First cut on their next
        # upload. Only subscribers who joined after the release cutoff
        # (claude_plans: studio_for_all_plans on, no payment or Paddle start
        # before cutoff_at) get it. Found by the T1 gating proof (30 Oct).
        return None
    if nbytes and int(nbytes) > int(cfg.get("max_upload_bytes") or 0) > 0:
        return None
    if _other_admission(cur, user_id, project_id):
        return None
    spend = spend_status(cur, fresh=True)
    if not spend.get("ready", True):
        return None
    if spend["month_usd"] >= float(cfg.get("monthly_usd_cap") or 0):
        return None
    if spend["first_cuts_today"] >= int(cfg.get("daily_count_cap") or 0):
        return None
    if spend["all_usd"] >= float(cfg.get("pool_usd_cap") or 0):
        return None
    if not subscribed and not is_test(cfg, user_id) \
            and _account_reason(cur, cfg, user_id):
        return None
    admission = {"admitted_at": _now().isoformat(),
                 "kind": "payer" if subscribed else "trial",
                 "first_cut": True}
    cur.execute("""UPDATE projects
                   SET meta = COALESCE(meta, '{}'::jsonb)
                              || jsonb_build_object('free_result', %s::jsonb)
                   WHERE id = %s""", (json.dumps(admission), int(project_id)))
    return admission


def trial_project(cur, user_id, project_id, live_only=False):
    """The admission when this is the account's trial project (unsubscribed
    accounts only use it as a trial), else None. ``live_only``: only while
    the trial is running for the account (flag and credit switch); the
    upload and edit gates use it, the export paywall does not."""
    admission = project_admission(cur, project_id)
    if not admission or admission.get("kind") != "trial":
        return None
    if live_only and not live(cur, user_id):
        return None
    return admission


def request_allowance(cur, user_id, project_id):
    """May an unsubscribed account's request on this project run as a trial
    turn? {"allowed": bool, "reason", "remaining_usd"}."""
    admission = trial_project(cur, user_id, project_id)
    if not admission:
        return {"allowed": False, "reason": "not_trial_project"}
    cfg = settings(cur)
    if not switched_on(cfg, user_id):
        return {"allowed": False, "reason": "switched_off"}
    if not credit_on(cur):
        return {"allowed": False, "reason": "credit_switch_off"}
    if cfg.get("trial_requests", True) is False:
        return {"allowed": False, "reason": "trial_requests_off"}
    spend = spend_status(cur)
    if spend["month_usd"] >= float(cfg.get("monthly_usd_cap") or 0):
        return {"allowed": False, "reason": "monthly_cap"}
    remaining = account_remaining(cur, cfg, user_id)
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
