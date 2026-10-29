"""The Claude-funded free trial: who gets it, what it costs, when it stops.

Owner decision D2 (25 Oct 2026, final), quoted in
``.valmera/strategy-1025/DECISIONS.md``:

* each new, never-paid account gets **$1.00 of Anthropic (Claude) API usage**
  to make the edit it wants with the in-app agent, from the ~$200 of
  Anthropic credit in the owner's subscription;
* export and download need a plan (track B's gate); when the $1 is used up,
  the user upgrades to continue — the project is kept;
* granted on the **first upload that indexes** (URL-only requests, which
  have no upload, are granted at their first agent turn), one grant per
  account, at most 2 accounts per device_id per 30 days, no disposable email
  domains;
* stop granting when the pool reaches $200 or the daily cap is hit (default
  40 new trial accounts a day); alert at 50/80/95 %;
* trial usage never touches the paid OpenAI wallet: it fails closed to the
  plan cards.

THE ONE RUNNING TOTAL (shared contract with track B): ``trial_budgets``
(migration 040), one row per granted account; ``spent_usd`` is the running
provider cost of every Claude call made for that account, plus the non-LLM
generations its turns bought, and ``cap_usd`` is its ceiling. Track B's gate
reads ``cap_usd - spent_usd`` (or ``exhausted_at``). ``trial_budget_days``
is the pool's daily ledger (every trial dollar, granted or not), which the
global caps and the 50/80/95 % alerts read. Each call's own cost is also on
its ``llm_calls`` row (``response->>'provider_cost_usd'``), as for every
other provider, so the total can always be reconciled.

The flag lives in ``app_kv.free_result`` (track B owns it):
``{on, per_account_usd_cap, monthly_usd_cap, daily_count_cap, pool_usd_cap,
daily_usd_cap, model, test_user_ids}``. Only ``on`` is required; every cap
defaults to the owner's numbers in config. ``test_user_ids`` routes internal
test accounts to the trial lane even while ``on`` is false (never
customers), which is how the lane is proven in production before the flip.

INTEGRATION (claude/agent-trial-release-1025): track B's backend admits the
project (``projects.meta.free_result.kind == "trial"``, backend/free_trial.py)
and this module only runs a never-paid account's job on Claude for an
ADMITTED project: anything else keeps today's lanes exactly. The pool is the
current Anthropic billing cycle's trial spend (claude_credit.cycle_window),
and the automatic credit switch (claude_credit.py) closes the lane for new
turns when the cycle's credit is used up or Anthropic refuses for billing.
"""

import json
import re
import threading
import time
from datetime import datetime, timezone

import config

ADMIN_EMAIL = "thevalmera@gmail.com"

# Throwaway-inbox domains (D2: "disposable email domains blocked"). A short
# list of the high-volume services; the device cap and the daily cap bound
# whatever is not on it.
DISPOSABLE_DOMAINS = frozenset("""
mailinator.com guerrillamail.com guerrillamail.net guerrillamail.org
sharklasers.com grr.la 10minutemail.com 10minutemail.net tempmail.com
temp-mail.org temp-mail.io tempmail.net tempmailo.com tempail.com
yopmail.com yopmail.fr yopmail.net trashmail.com trashmail.net
getnada.com nada.email dispostable.com maildrop.cc mailnesia.com
throwawaymail.com fakeinbox.com mintemail.com moakt.com emailondeck.com
mohmal.com burnermail.io spamgourmet.com mytemp.email tmail.ws tmpmail.org
tmpmail.net inboxkitten.com 1secmail.com 1secmail.org 1secmail.net
mailpoof.com emailfake.com fakemail.net discard.email spambox.us
mail.tm mail.gw linshiyouxiang.net 33mail.com getairmail.com
""".split())

_POOL_ALERT_PCTS = (50, 80, 95)


# ------------------------------------------------------------------ #
#  Flag                                                                #
# ------------------------------------------------------------------ #

_flag_cache = {"value": None, "at": 0.0}
_flag_lock = threading.Lock()


def _num(value, default, lo=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out >= lo else default


def parse_flag(raw):
    """app_kv text -> normalized flag dict. Anything unparsable is OFF."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    tests = set()
    for value in data.get("test_user_ids") or []:
        try:
            tests.add(int(value))
        except (TypeError, ValueError):
            continue
    model = str(data.get("model") or "").strip()
    if not model.lower().startswith("claude-"):
        model = config.CLAUDE_TRIAL_MODEL
    pool = _num(data.get("pool_usd_cap"), config.CLAUDE_TRIAL_POOL_USD)
    return {
        "on": data.get("on") is True or str(data.get("on")).lower() == "true",
        "per_account_usd_cap": _num(data.get("per_account_usd_cap"),
                                    config.CLAUDE_TRIAL_PER_ACCOUNT_USD, 0.001),
        "pool_usd_cap": pool,
        "monthly_usd_cap": _num(data.get("monthly_usd_cap"), pool),
        "daily_count_cap": int(_num(data.get("daily_count_cap"),
                                    config.CLAUDE_TRIAL_DAILY_COUNT)),
        "daily_usd_cap": _num(data.get("daily_usd_cap"),
                              config.CLAUDE_TRIAL_DAILY_USD),
        "model": model,
        "test_user_ids": tests,
    }


def read_flag(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.app_kv') AS t")
        row = cur.fetchone()
        if not (row.get("t") if isinstance(row, dict) else row[0]):
            return parse_flag(None)
        cur.execute("SELECT value FROM app_kv WHERE key = %s",
                    (config.CLAUDE_TRIAL_FLAG_KEY,))
        row = cur.fetchone()
    raw = (row.get("value") if isinstance(row, dict) else row[0]) if row else None
    return parse_flag(raw)


def flag(worker_db, max_age_s=20.0):
    """The flag, cached briefly per process. Any read error means OFF."""
    with _flag_lock:
        if (_flag_cache["value"] is not None
                and time.monotonic() - _flag_cache["at"] < max_age_s):
            return _flag_cache["value"]
    try:
        value = worker_db.run(read_flag)
    except Exception as exc:  # noqa: BLE001 - fail closed (lane off)
        print(f"[claude-trial] flag read failed ({str(exc)[:120]}) — off",
              flush=True)
        value = parse_flag(None)
    with _flag_lock:
        _flag_cache.update(value=value, at=time.monotonic())
    return value


def reset_flag_cache():
    with _flag_lock:
        _flag_cache.update(value=None, at=0.0)
        if "_credit_cache" in globals():
            _credit_cache.update(value=None, at=0.0)


# ------------------------------------------------------------------ #
#  Schema-level reads and writes (conn-first, run through Db.run)      #
# ------------------------------------------------------------------ #

def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def tables_ready(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.trial_budgets') AS a, "
                    "to_regclass('public.trial_budget_days') AS b")
        row = cur.fetchone()
    return bool(_v(row, "a", 0) and _v(row, "b", 1))


def get_budget(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT user_id, cap_usd, spent_usd, calls, granted_at,
                              exhausted_at, granted_via
                         FROM trial_budgets WHERE user_id = %s""",
                    (int(user_id),))
        return cur.fetchone()


def pool_totals(conn):
    """{'pool': this credit cycle, 'month': this UTC month, 'today': today,
    'grants_today'}. The credit has no rollover, so the pool is per cycle."""
    import claude_credit
    start, end = claude_credit.cycle_window(claude_credit.read_config(conn))
    with conn.cursor() as cur:
        cur.execute("""SELECT
              COALESCE(SUM(spent_usd) FILTER (
                  WHERE day >= %s AND day < %s), 0) AS pool,
              COALESCE(SUM(spent_usd) FILTER (
                  WHERE day >= date_trunc('month', (NOW() AT TIME ZONE 'UTC'))::date), 0) AS month,
              COALESCE(SUM(spent_usd) FILTER (
                  WHERE day = (NOW() AT TIME ZONE 'UTC')::date), 0) AS today,
              COALESCE(SUM(grants) FILTER (
                  WHERE day = (NOW() AT TIME ZONE 'UTC')::date), 0) AS grants_today
            FROM trial_budget_days""", (start.date(), end.date()))
        row = cur.fetchone()
    return {"pool": float(_v(row, "pool", 0) or 0),
            "month": float(_v(row, "month", 1) or 0),
            "today": float(_v(row, "today", 2) or 0),
            "grants_today": int(_v(row, "grants_today", 3) or 0)}


def _account(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT id, email, is_subscribed FROM users WHERE id = %s",
                    (int(user_id),))
        user = cur.fetchone()
        paid = False
        try:
            cur.execute("SAVEPOINT trial_paid_lookup")
            cur.execute("""SELECT 1 FROM payments
                            WHERE user_id = %s AND amount_cents > 0
                              AND status IN ('completed', 'paid') LIMIT 1""",
                        (int(user_id),))
            paid = cur.fetchone() is not None
            cur.execute("RELEASE SAVEPOINT trial_paid_lookup")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT trial_paid_lookup")
        device = None
        try:
            cur.execute("SAVEPOINT trial_device_lookup")
            cur.execute("""SELECT device_id FROM website_signups
                            WHERE user_id = %s AND device_id IS NOT NULL
                              AND device_id <> '' LIMIT 1""", (int(user_id),))
            device = _v(cur.fetchone(), "device_id", 0)
            cur.execute("RELEASE SAVEPOINT trial_device_lookup")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT trial_device_lookup")
    return user, paid, device


def project_admitted(conn, project_id):
    """Track B's admission: this project is an account's free-trial project
    (backend/free_trial.admit_upload wrote projects.meta.free_result)."""
    if project_id is None:
        return False
    with conn.cursor() as cur:
        cur.execute("""SELECT meta->'free_result'->>'kind' AS kind
                         FROM projects WHERE id = %s""", (int(project_id),))
        return _v(cur.fetchone(), "kind", 0) == "trial"


def email_is_disposable(email):
    domain = str(email or "").rsplit("@", 1)[-1].strip().lower()
    if not domain or "." not in domain:
        return True
    parts = domain.split(".")
    return any(".".join(parts[i:]) in DISPOSABLE_DOMAINS
               for i in range(len(parts) - 1))


def grant(conn, user_id, project_id, via, flag_value, pending_usd=0.0,
          is_test=False, admitted=False):
    """Grant the account its trial once, under every D2 limit.

    Returns ``{"granted": bool, "row": row|None, "reason": str}``. Idempotent:
    an account that already holds a grant gets that grant back (one per
    account, ever). A transaction advisory lock serialises grants so the
    daily count cap cannot be raced past.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('trial_grant'))")
    existing = get_budget(conn, user_id)
    if existing:
        return {"granted": True, "row": existing, "reason": "existing"}
    user, paid, device = _account(conn, user_id)
    if not user:
        return {"granted": False, "row": None, "reason": "no_account"}
    email = str(_v(user, "email", 1) or "")
    if not is_test:
        if email.strip().lower() == ADMIN_EMAIL:
            return {"granted": False, "row": None, "reason": "admin"}
        if _v(user, "is_subscribed", 2) or paid:
            return {"granted": False, "row": None, "reason": "paid_account"}
        if email_is_disposable(email):
            return {"granted": False, "row": None, "reason": "disposable_email"}
        # An admitted project already passed the device limit (track B counts
        # admissions per signup device); counting grants again would refuse an
        # account the backend has just let in.
        if device and config.CLAUDE_TRIAL_ACCOUNTS_PER_DEVICE > 0 \
                and not admitted:
            with conn.cursor() as cur:
                cur.execute("""SELECT COUNT(*) AS n FROM trial_budgets
                                WHERE device_id = %s
                                  AND granted_at > NOW() - INTERVAL '30 days'""",
                            (device,))
                if int(_v(cur.fetchone(), "n", 0) or 0) >= \
                        config.CLAUDE_TRIAL_ACCOUNTS_PER_DEVICE:
                    return {"granted": False, "row": None,
                            "reason": "device_limit"}
    totals = pool_totals(conn)
    if totals["pool"] >= flag_value["pool_usd_cap"]:
        return {"granted": False, "row": None, "reason": "pool_spent"}
    if totals["month"] >= flag_value["monthly_usd_cap"]:
        return {"granted": False, "row": None, "reason": "monthly_cap"}
    if not is_test and not admitted:
        # Admission counted the day's new trial accounts already.
        if totals["grants_today"] >= flag_value["daily_count_cap"]:
            return {"granted": False, "row": None, "reason": "daily_count_cap"}
    if not is_test and totals["today"] >= flag_value["daily_usd_cap"]:
        return {"granted": False, "row": None, "reason": "daily_usd_cap"}
    cap = float(flag_value["per_account_usd_cap"])
    spent = max(0.0, float(pending_usd or 0.0))
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO trial_budgets
                         (user_id, provider, cap_usd, spent_usd, granted_project_id,
                          granted_via, device_id, exhausted_at)
                       VALUES (%s, 'anthropic', %s, %s, %s, %s, %s,
                               CASE WHEN %s >= %s THEN NOW() END)
                       ON CONFLICT (user_id) DO NOTHING""",
                    (int(user_id), cap, spent, project_id, str(via)[:32],
                     device, spent, cap))
        inserted = cur.rowcount > 0
        if inserted:
            cur.execute("""INSERT INTO trial_budget_days (day, grants)
                           VALUES ((NOW() AT TIME ZONE 'UTC')::date, 1)
                           ON CONFLICT (day) DO UPDATE
                           SET grants = trial_budget_days.grants + 1,
                               updated_at = NOW()""")
    return {"granted": True, "row": get_budget(conn, user_id),
            "reason": "granted" if inserted else "existing"}


def add_spend(conn, user_id, usd, granted=True, calls=1, extras=False):
    """Atomically add one cost to the account total and the day ledger.
    ``extras`` marks a non-Anthropic generation (images, sfx): it counts
    against the account's $1 and the pool, not against the Anthropic credit.
    Returns (account_spent or None, pool_total for this credit cycle)."""
    usd = max(0.0, float(usd or 0.0))
    import claude_credit
    with_extras = claude_credit.ledger_ready(conn)
    spent = None
    with conn.cursor() as cur:
        if granted and user_id is not None:
            cur.execute("""UPDATE trial_budgets
                              SET spent_usd = spent_usd + %s,
                                  calls = calls + %s,
                                  last_spend_at = NOW(),
                                  updated_at = NOW(),
                                  exhausted_at = COALESCE(exhausted_at,
                                      CASE WHEN spent_usd + %s >= cap_usd
                                           THEN NOW() END)
                            WHERE user_id = %s
                        RETURNING spent_usd""",
                        (usd, int(calls), usd, int(user_id)))
            spent = _v(cur.fetchone(), "spent_usd", 0)
        if with_extras:
            cur.execute("""INSERT INTO trial_budget_days
                             (day, spent_usd, calls, extras_usd)
                           VALUES ((NOW() AT TIME ZONE 'UTC')::date, %s, %s, %s)
                           ON CONFLICT (day) DO UPDATE
                           SET spent_usd = trial_budget_days.spent_usd + EXCLUDED.spent_usd,
                               calls = trial_budget_days.calls + EXCLUDED.calls,
                               extras_usd = trial_budget_days.extras_usd
                                            + EXCLUDED.extras_usd,
                               updated_at = NOW()""",
                        (usd, int(calls), usd if extras else 0.0))
        else:
            cur.execute("""INSERT INTO trial_budget_days (day, spent_usd, calls)
                           VALUES ((NOW() AT TIME ZONE 'UTC')::date, %s, %s)
                           ON CONFLICT (day) DO UPDATE
                           SET spent_usd = trial_budget_days.spent_usd + EXCLUDED.spent_usd,
                               calls = trial_budget_days.calls + EXCLUDED.calls,
                               updated_at = NOW()""", (usd, int(calls)))
    pool = pool_totals(conn)["pool"]
    return (float(spent) if spent is not None else None), pool


def _meter_tx(conn, user_id, usd, granted, extras):
    """One metered cost and, in the same transaction, the credit line."""
    import claude_credit
    spent, pool = add_spend(conn, user_id, usd, granted, extras=extras)
    if usd > 0 and not extras:
        claude_credit.check_line(conn)
    return spent, pool


def queue_alert(conn, dedupe_key, kind, subject, html):
    """Durable admin alert; the backend's scheduler sends it (never customers)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.trial_alerts') AS t")
        if not _v(cur.fetchone(), "t", 0):
            return False
        cur.execute("""INSERT INTO trial_alerts (dedupe_key, kind, subject,
                                                 html_content)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (dedupe_key) DO NOTHING""",
                    (str(dedupe_key)[:200], str(kind)[:40], str(subject)[:300],
                     str(html)[:20000]))
        return cur.rowcount > 0


# ------------------------------------------------------------------ #
#  The per-thread scope                                                #
# ------------------------------------------------------------------ #

_tls = threading.local()
_meter_db = None
_meter_lock = threading.Lock()


class Scope:
    """One trial account's live budget for the job running on this thread.

    ``granted`` False means a first upload is indexing before its grant
    exists: spend accrues to the pool ledger and to ``pending_usd`` and is
    carried into the grant when the index succeeds."""

    def __init__(self, user_id, cap_usd, spent_usd, model, granted=True,
                 job_id=None, is_test=False, pool_cap=None):
        self.user_id = int(user_id)
        self.cap_usd = float(cap_usd)
        self.spent_at_start = float(spent_usd or 0.0)
        self.spent_usd = float(spent_usd or 0.0)
        self.model = model
        self.granted = bool(granted)
        self.job_id = job_id
        self.is_test = bool(is_test)
        self.pool_cap = float(pool_cap if pool_cap is not None
                              else config.CLAUDE_TRIAL_POOL_USD)
        self.pending_usd = 0.0
        self.calls = 0
        self.extras_metered_usd = 0.0
        self.meter_errors = 0

    @property
    def remaining_usd(self):
        return max(0.0, self.cap_usd - self.spent_usd)

    def as_dict(self):
        return {"user_id": self.user_id, "model": self.model,
                "cap_usd": round(self.cap_usd, 4),
                "spent_usd": round(self.spent_usd, 6),
                "turn_usd": round(self.spent_usd - self.spent_at_start, 6),
                "calls": self.calls, "granted": self.granted,
                "test": self.is_test}


def current():
    """The scope for THIS thread: set directly, or carried by the turn's
    llm recorder (pool threads install the caller's recorder, so the scope
    travels with it — exactly the propagation llm_calls already relies on)."""
    scope = getattr(_tls, "scope", None)
    if scope is not None:
        return scope
    try:
        import llm
        recorder = llm.get_recorder()
    except Exception:  # pragma: no cover
        recorder = None
    return getattr(recorder, "claude_trial_scope", None)


def activate(scope, recorder=None):
    _tls.scope = scope
    if recorder is not None:
        try:
            recorder.claude_trial_scope = scope
        except Exception:  # pragma: no cover - builtins cannot take attrs
            pass


def deactivate(recorder=None):
    _tls.scope = None
    if recorder is not None and hasattr(recorder, "claude_trial_scope"):
        try:
            recorder.claude_trial_scope = None
        except Exception:  # pragma: no cover
            pass


def _metering_db():
    global _meter_db
    if _meter_db is None:
        import db as dbx
        _meter_db = dbx.Db()
    return _meter_db


def guard_call():
    """Hard backstop under the loop's own graceful stop: a scope that is
    15 % past its ceiling makes no further Claude calls (critics, vision)."""
    scope = current()
    if scope is None or scope.is_test:
        return
    if scope.spent_usd >= scope.cap_usd * 1.15:
        import claude_lane
        raise claude_lane.ClaudeLaneUnavailable(
            "free trial budget used up for this account (its credit balance "
            "for the trial is spent)")


def meter(model, usd, kind="llm"):
    """Record one Claude call's cost against the scope's account and pool."""
    scope = current()
    if scope is None:
        return None
    usd = max(0.0, float(usd or 0.0))
    with _meter_lock:
        scope.calls += 1
        scope.spent_usd += usd
        if not scope.granted:
            scope.pending_usd += usd
        try:
            spent, pool = _metering_db().run(
                _meter_tx, scope.user_id, usd, scope.granted,
                kind == "extras")
            if spent is not None:
                scope.spent_usd = max(scope.spent_usd, float(spent))
        except Exception as exc:  # noqa: BLE001 - the live scope still caps
            scope.meter_errors += 1
            print(f"[claude-trial] meter write failed for user "
                  f"{scope.user_id}: {str(exc)[:160]}", flush=True)
            return None
    _pool_alerts(pool, scope.pool_cap)
    return scope.spent_usd


def _paid_tx(conn, usd):
    import claude_credit
    claude_credit.record_paid_spend(conn, usd)
    if usd > 0:
        claude_credit.check_line(conn)


def meter_paid(model, usd):
    """A subscriber's Claude-lane call: the credit cycle's ledger and line.
    Never raises (the turn is the customer's; metering is ours)."""
    usd = max(0.0, float(usd or 0.0))
    try:
        _metering_db().run(_paid_tx, usd)
    except Exception as exc:  # noqa: BLE001
        print(f"[claude-lane] paid meter write failed ({model}): "
              f"{str(exc)[:160]}", flush=True)


def meter_extras(scope, total_extras_usd):
    """Non-LLM generations a trial turn bought (images, sfx, video), as the
    delta since this scope last metered them."""
    if scope is None:
        return
    delta = max(0.0, float(total_extras_usd or 0.0) - scope.extras_metered_usd)
    if delta <= 0:
        return
    scope.extras_metered_usd += delta
    previous = getattr(_tls, "scope", None)
    _tls.scope = scope
    try:
        meter("generation", delta, kind="extras")
    finally:
        _tls.scope = previous


# ------------------------------------------------------------------ #
#  Alerts                                                              #
# ------------------------------------------------------------------ #

_alerted = set()


def _alert(key, kind, subject, html):
    if key in _alerted:
        return
    try:
        _metering_db().run(queue_alert, key, kind, subject, html)
        _alerted.add(key)
    except Exception as exc:  # noqa: BLE001 - an alert never breaks a turn
        print(f"[claude-trial] alert queue failed ({key}): {str(exc)[:120]}",
              flush=True)


def _pool_alerts(pool, pool_cap):
    if not pool_cap or pool_cap <= 0:
        return
    pct = 100.0 * float(pool) / float(pool_cap)
    if pct < _POOL_ALERT_PCTS[0]:
        return
    try:
        import claude_credit
        cycle = claude_credit.cycle_window(_metering_db().run(
            claude_credit.read_config))[1].strftime("%Y-%m-%d")
    except Exception:  # pragma: no cover
        cycle = ""
    for mark in _POOL_ALERT_PCTS:
        if pct >= mark:
            _alert(f"pool_{mark}_of_{int(round(pool_cap))}_{cycle}",
                   "pool_threshold",
                   f"Claude trial pool at {mark}%: ${pool:.2f} of "
                   f"${pool_cap:.0f}",
                   f"<p>The Claude-funded trial has spent <b>${pool:.2f}</b> "
                   f"of its ${pool_cap:.0f} pool ({pct:.1f}%).</p>"
                   "<p>At 100% no new trial accounts are granted and trial "
                   "turns end on the plan cards. Raise "
                   "<code>app_kv.free_result.pool_usd_cap</code> only after "
                   "the Anthropic balance is confirmed.</p>")


def provider_refused(kind, exc):
    """Anthropic refused for billing or auth: flip the automatic credit
    switch OFF (claude_credit; one alert per switch) and alert the admin
    (hourly per process)."""
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    detail = re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "[REDACTED]", str(exc))[:400]
    try:
        import claude_credit
        _metering_db().run(
            claude_credit.switch_off,
            claude_credit.REASON_BILLING if kind == "billing"
            else claude_credit.REASON_AUTH, detail)
        reset_flag_cache()
    except Exception as switch_exc:  # noqa: BLE001 - never breaks a turn
        print(f"[claude-trial] credit switch write failed: "
              f"{str(switch_exc)[:160]}", flush=True)
    _alert(f"provider_{kind}_{hour}", f"provider_{kind}",
           f"Claude trial lane closed: Anthropic {kind} refusal",
           f"<p>Anthropic refused a trial-lane call ({kind}). The lane is "
           "closed for this worker process for "
           f"{int(config.CLAUDE_TRIAL_OUTAGE_LATCH_S // 60)} minutes and trial "
           "users see the plan cards (never the OpenAI wallet).</p>"
           f"<p>Provider said: <code>{detail}</code></p>"
           "<p>Check the Anthropic console balance and that "
           "ANTHROPIC_API_KEY belongs to the account holding the credit.</p>")


# ------------------------------------------------------------------ #
#  Turn resolution                                                     #
# ------------------------------------------------------------------ #

class Decision:
    NOT_TRIAL = "not_trial"        # paid/admin/flag off: today's behaviour
    ACTIVE = "active"              # run this turn on Claude under `scope`
    EXHAUSTED = "exhausted"        # the account's $1 is spent: plan cards
    UNAVAILABLE = "unavailable"    # lane or pool closed: plan cards

    def __init__(self, kind, scope=None, reason=""):
        self.kind = kind
        self.scope = scope
        self.reason = reason

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Decision({self.kind!r}, reason={self.reason!r})"


def _is_admin(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
    return str(_v(row, "email", 0) or "").strip().lower() == ADMIN_EMAIL


_credit_cache = {"value": None, "at": 0.0}


def credit_status(worker_db, max_age_s=20.0):
    """The automatic credit switch (claude_credit.status), cached briefly per
    process. Any read error reads as OFF: the lane fails closed."""
    with _flag_lock:
        if (_credit_cache["value"] is not None
                and time.monotonic() - _credit_cache["at"] < max_age_s):
            return _credit_cache["value"]
    try:
        import claude_credit
        value = worker_db.run(claude_credit.status)
    except Exception as exc:  # noqa: BLE001 - fail closed
        print(f"[claude-trial] credit switch read failed "
              f"({str(exc)[:120]}) — off", flush=True)
        value = {"on": False, "reason": "unreadable"}
    with _flag_lock:
        _credit_cache.update(value=value, at=time.monotonic())
    return value


def resolve(worker_db, user_id, subscribed, project_id=None,
            via="agent_turn", operator_repair=False, allow_grant=True,
            continuing=False):
    """Which lane a job for this account runs on. Never raises.

    NOT_TRIAL (today's behaviour, exactly) unless ALL of: the flag is on (or
    the account is an internal test account), the account has no plan, and
    the project is its admitted free-trial project. ``continuing`` is a later
    slice of a request that already started: when the credit switch went off
    only because the cycle reached its line, that request still finishes."""
    if operator_repair or user_id is None:
        return Decision(Decision.NOT_TRIAL, reason="operator_or_anonymous")
    value = flag(worker_db)
    is_test = int(user_id) in value["test_user_ids"]
    if not value["on"] and not is_test:
        return Decision(Decision.NOT_TRIAL, reason="flag_off")
    if subscribed:
        return Decision(Decision.NOT_TRIAL, reason="subscribed")
    try:
        if not worker_db.run(project_admitted, project_id):
            return Decision(Decision.NOT_TRIAL, reason="not_admitted")
        if not is_test and worker_db.run(_is_admin, user_id):
            return Decision(Decision.NOT_TRIAL, reason="admin")
    except Exception:
        return Decision(Decision.UNAVAILABLE, reason="account_lookup")
    import claude_lane
    if not claude_lane.configured():
        return Decision(Decision.UNAVAILABLE, reason="no_anthropic_key")
    if not claude_lane.available():
        return Decision(Decision.UNAVAILABLE,
                        reason="provider_closed: " + claude_lane.closed_reason())
    credit = credit_status(worker_db)
    if not credit.get("on"):
        import claude_credit
        finishing = continuing and credit.get("reason") == \
            claude_credit.REASON_BUDGET
        if not finishing:
            return Decision(Decision.UNAVAILABLE,
                            reason="credit_switch_off: "
                                   + str(credit.get("reason") or ""))
    try:
        if not worker_db.run(tables_ready):
            return Decision(Decision.UNAVAILABLE, reason="migration_040_missing")
        totals = worker_db.run(pool_totals)
        if totals["pool"] >= value["pool_usd_cap"] and not continuing:
            return Decision(Decision.UNAVAILABLE, reason="pool_spent")
        if totals["month"] >= value["monthly_usd_cap"] and not continuing:
            return Decision(Decision.UNAVAILABLE, reason="monthly_cap")
        row = worker_db.run(get_budget, user_id)
        if row is None:
            if not allow_grant:
                return Decision(Decision.ACTIVE, scope=Scope(
                    user_id, value["per_account_usd_cap"], 0.0,
                    value["model"], granted=False, is_test=is_test,
                    pool_cap=value["pool_usd_cap"]), reason="pending_grant")
            got = worker_db.run(grant, user_id, project_id, via, value,
                                0.0, is_test, True)
            if not got["granted"]:
                return Decision(Decision.UNAVAILABLE,
                                reason="not_granted: " + got["reason"])
            row = got["row"]
    except Exception as exc:  # noqa: BLE001 - fail closed
        print(f"[claude-trial] budget lookup failed for {user_id}: "
              f"{str(exc)[:160]}", flush=True)
        return Decision(Decision.UNAVAILABLE, reason="budget_lookup")
    cap = float(_v(row, "cap_usd", 1) or value["per_account_usd_cap"])
    spent = float(_v(row, "spent_usd", 2) or 0.0)
    if not is_test and spent >= cap - min(0.005, cap * 0.05):
        return Decision(Decision.EXHAUSTED, reason="budget_spent")
    if is_test:
        cap = max(cap, spent + value["per_account_usd_cap"])
    return Decision(Decision.ACTIVE, scope=Scope(
        user_id, cap, spent, value["model"], granted=True, is_test=is_test,
        pool_cap=value["pool_usd_cap"]), reason="ok")


def settle_grant(worker_db, scope, project_id, via="index"):
    """A first upload indexed: grant now, carrying its pending spend."""
    if scope is None or scope.granted:
        return None
    try:
        value = flag(worker_db)
        got = worker_db.run(grant, scope.user_id, project_id, via, value,
                            scope.pending_usd, scope.is_test, True)
    except Exception as exc:  # noqa: BLE001
        print(f"[claude-trial] grant after index failed for "
              f"{scope.user_id}: {str(exc)[:160]}", flush=True)
        return None
    if got.get("granted"):
        scope.granted = True
    return got


# ------------------------------------------------------------------ #
#  User-facing stops (the plan cards; never a raw error)              #
# ------------------------------------------------------------------ #

# The trial's one standing instruction, placed right after the editor kernel
# and capability directory (byte-identical for every trial turn, so it rides
# the cached prefix). It changes DEFAULTS only — tools, skills, safety and
# verification are the paid lane's. A first-time visitor gets one finished,
# watchable result instead of a questionnaire.
FIRST_RESULT_NOTE = (
    "FREE TRIAL — FIRST RESULT. This account is trying Valmera on a small, "
    "one-time budget, and this reply decides whether they keep using it. "
    "Deliver ONE finished, polished result in this turn; read_skill("
    "'first-requests') gives the request's chain. No questions unless the "
    "request cannot start; say in the reply what you chose.\n"
    "BUILD ON THE FIRST CUT: the project already holds an automatic First "
    "cut (trimmed, framed, a hook title, key lines slammed in big type, "
    "captions). Change what the request asks and keep the rest; never "
    "reset_edit, and never remove its hook, slams, captions or framing "
    "unless the user asked. When the First cut already "
    "does what was asked, say so and fix only MAJOR defects. A [system: "
    "TRIAL TURN PREPARED] note means code already did the request's main "
    "work (a whole-take clean-up included): follow it, and never redo that "
    "work by hand. A highlight montage is already cut to its strongest "
    "shots: never keep_segments over it.\n"
    "THE CRAFT BAR (the owner's reference reels): 1) Hook: speech at 0.0 s "
    "on a complete line (never 'and', 'so', 'um'), and a poster title over "
    "the first 2-3 s (add_motion_graphic template='hook_title', the opening "
    "line in Anton caps with one *serif* word). 2) Ending: a complete "
    "sentence, then 0.3-0.6 s of live picture; never a freeze or a slowed "
    "tail. 3) Pace: on speech cut_silences(min_silence_s=0.35, "
    "padding_s=0.1) and remove_filler_words, always; no pause over ~0.4 s. "
    "4) Frame: a talking head is a full 9:16 face crop (auto_reframe); a "
    "screen recording keeps its own shape unless asked. 5) Captions: 1-3 "
    "words at a time at chest height, emphasis on meaning words, never on "
    "the face.\n"
    "A website or product demo: record_website_demo(url, orientation="
    "'portrait', steps=[{do:'wait', seconds:0.8}, {do:'tour', by:2}]) "
    "unless the user named the steps, then showcase_demo, then the site's "
    "name as a hook_title; 15-30 s. A website brief that names sections, "
    "pages or actions the First cut's recording does not show: "
    "record_website_demo again with steps that click exactly those, by "
    "their visible link text, in the brief's order, then showcase_demo and "
    "the hook_title, at the brief's length and shape. A long source asked for a short or the "
    "best moment: one 20-45 s moment with a hook line and a payoff, 9:16 on "
    "the speaker, captioned. No music unless the user asks (never pick "
    "one); a silent source is delivered silent.\n"
    "Render once, look at the frames, fix MAJOR defects only (a fragment at "
    "the start or end, a face cut off, unreadable text) and render once "
    "more at most. A picture finding still there after one repair is the "
    "footage's own: keep it and say so in one clause; do not loop. Reply "
    "briefly with what you made, and end on a statement, never a question.")

EXHAUSTED_TEXT = (
    "That used up your free trial — everything I finished is saved and you "
    "can watch it here. Pick a plan to keep going on this project; your "
    "request picks up right where it stopped.")
EXHAUSTED_BEFORE_TEXT = (
    "Your free trial is used up, so I haven't started this one. Your project "
    "and every edit so far are saved. Pick a plan to keep going; your request "
    "picks up right here.")
UNAVAILABLE_TEXT = (
    "The free trial editor is paused right now, so I haven't started this "
    "edit. Your upload is saved — pick a plan to start editing it now.")


def stop_meta(kind, request_message_id=None, extra=None):
    """Chat meta the Studio already renders as plan cards (PlanCTACard keys
    on subscribe_required / credits_exhausted), plus the trial facts track
    B's UI uses for its "keep going" card."""
    meta = {"subscribe_required": True, "credits_exhausted": True,
            "free_trial_exhausted": kind == Decision.EXHAUSTED,
            "claude_trial": True, "keep_going": True,
            "trial_budget_exhausted": kind == Decision.EXHAUSTED,
            "trial_unavailable": kind == Decision.UNAVAILABLE}
    if request_message_id is not None:
        meta["request_message_id"] = int(request_message_id)
    meta.update(extra or {})
    return meta


def is_lane_refusal(exc):
    import claude_lane
    import llm
    return isinstance(exc, claude_lane.ClaudeLaneUnavailable) or \
        llm.is_quota_error(exc)
