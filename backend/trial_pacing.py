"""Adaptive pacing of the free trial's Claude credit (owner, 31 Oct 2026).

Owner: "we need to provide the trial to all users without running out of it
... the outcome is 10 out of ten edits to all users to convert the most and
managing the credits wisely".

Two numbers in ``app_kv.free_result`` decide how the credit is spent, and
both used to be fixed by hand ($15 a day, $0.12 per account). This module
recomputes them from the credit that is actually left, so the trial lasts to
the end of the cycle on a busy day and gives each account more on a quiet one:

* **daily_usd_cap** = (credit remaining - safety margin) / days remaining in
  the cycle. "Credit remaining" is ``claude_credit.cycle_budget_usd`` minus
  this cycle's metered Claude spend (claude_credit.status: the trial and the
  new subscribers' Studio share the one credit). Days remaining are counted
  to ``cycle_end`` and never fewer than one, so the last day may spend what
  is left and no more. The worker stops NEW trial grants once today's spend
  reaches it (worker/claude_trial.grant), so a busy day ends on today's
  paywall instead of eating tomorrow's credit, and the credit switch's own
  line (claude_credit.py) still turns everything off at the margin.
* **The wallet bounds it too** (owner, 27 Nov 2026: "make sure that we don't
  run out of API credits for the new subscribers and the existing ones and
  the free trials"). The cycle budget is what the credit was meant to hold;
  the Anthropic wallet (anthropic_wallet.py: the owner's last confirmed
  balance minus every metered dollar since) is what it holds now. "Credit
  remaining" is the smaller of the two, after PAYING subscribers' reserve:
  their trailing daily spend (plus company spend on the same wallet) times
  the days to the refill times PAID_RESERVE_FACTOR, never under
  PAID_RESERVE_FLOOR_USD. When the wallet is the smaller, each admitted
  account still gets a whole trial (the ceiling) and the daily cap admits
  fewer accounts: a first cut cut short by a tiny allowance converts no one.
* **per_account_usd_cap** = daily_usd_cap / expected new trial accounts a
  day, clamped to [FLOOR_USD, CEILING_USD]. The expected count is the
  trailing grant rate (trial_budgets, test and internal accounts excluded):
  the higher of the last 24 hours and the last 72 hours, each measured only
  since the trial was switched on and over at least MIN_WINDOW_H hours, and
  never below one a day. A busy day raises the rate and lowers the cap at
  once; quiet days lower it (the 72 h rate decays last) and raise the cap.
  The cap is fixed on an account when it is granted, so a change applies to
  accounts that start from then on.

The computation (``compute``) is pure and deterministic: the same inputs
give the same numbers, rounded down to the cent. ``run`` reads the inputs,
and when either number changes it writes them into ``app_kv.free_result``
(merged; every other key kept, under the same advisory lock as the owner's
switch, trial_toggle.switch) with a ``pacing`` record: the inputs, the one
sentence the admin "Claude features" card shows, and the last HISTORY_KEEP
changes (the daily cap only when it moved by 2 % or $0.25, so the
cent-a-quarter-hour drift of the days running down is not a write). Every
change is logged. Nothing else is written. The backend runs
it every RECOMPUTE_EVERY_S (and once shortly after boot); the owner can stop
it with ``free_result.pacing_off = true`` (the numbers then stay as stored).
"""
import json
import logging
import math
import os
import threading
from datetime import datetime, timedelta, timezone

import claude_credit
import free_trial

log = logging.getLogger("trial_pacing")

FLOOR_USD = 0.12
CEILING_USD = 0.40
WINDOW_SHORT_H = 24.0
WINDOW_LONG_H = 72.0
MIN_WINDOW_H = 6.0
MIN_ACCOUNTS_PER_DAY = 1.0
RECOMPUTE_EVERY_S = 15 * 60
DAILY_STEP_PCT = 0.02
DAILY_STEP_USD = 0.25
HISTORY_KEEP = 20
LOCK = "free_result_switch"
# Paying subscribers' share of the wallet until the refill (owner, 27 Nov).
PAID_RESERVE_FLOOR_USD = 10.0
PAID_RESERVE_FACTOR = 1.5
RESERVE_WINDOW_DAYS = 7


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def _cents_down(value):
    return math.floor(max(0.0, float(value)) * 100 + 1e-6) / 100.0


def _parse_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _rate(count, window_h, on_since, now):
    """Accounts a day from ``count`` grants in the trailing ``window_h``
    hours, measured only since the trial was switched on."""
    hours = window_h
    if on_since is not None:
        hours = min(hours, max(0.0, (now - on_since).total_seconds() / 3600.0))
    hours = max(MIN_WINDOW_H, hours)
    return float(count) * 24.0 / hours


def compute(budget_usd, margin_usd, spent_usd, cycle_end, now,
            grants_24h, grants_72h, on_since=None,
            floor_usd=FLOOR_USD, ceiling_usd=CEILING_USD,
            wallet_usd=None, reserve_usd=0.0):
    """The paced caps. Pure: no I/O, no clock. ``wallet_usd`` is the
    estimated Anthropic wallet (None when unknown) and ``reserve_usd`` the
    part of it kept for paying subscribers until the refill."""
    remaining = max(0.0, float(budget_usd) - float(spent_usd))
    limit = "cycle"
    if wallet_usd is not None:
        room = max(0.0, float(wallet_usd) - float(reserve_usd))
        if room < remaining:
            remaining, limit = room, "wallet"
    usable = max(0.0, remaining - float(margin_usd))
    days_left = max(1.0, (cycle_end - now).total_seconds() / 86400.0)
    daily = _cents_down(usable / days_left)
    expected = max(MIN_ACCOUNTS_PER_DAY,
                   _rate(grants_24h, WINDOW_SHORT_H, on_since, now),
                   _rate(grants_72h, WINDOW_LONG_H, on_since, now))
    raw = daily / expected
    per_account = _cents_down(min(ceiling_usd, max(floor_usd, raw)))
    if limit == "wallet" and raw < ceiling_usd:
        # Fewer whole trials, never many broken ones: the daily cap admits
        # fewer accounts and each keeps the full allowance.
        per_account = _cents_down(ceiling_usd)
        bound = "wallet"
    elif raw >= ceiling_usd:
        bound = "ceiling"
    elif raw <= floor_usd:
        bound = "floor"
    else:
        bound = None
    out = {
        "daily_usd_cap": daily,
        "per_account_usd_cap": per_account,
        "remaining_usd": round(remaining, 4),
        "usable_usd": round(usable, 4),
        "days_left": round(days_left, 3),
        "expected_accounts_per_day": round(expected, 2),
        "raw_per_account_usd": round(raw, 4),
        "bound": bound,
        "grants_24h": int(grants_24h),
        "grants_72h": int(grants_72h),
        "budget_usd": round(float(budget_usd), 4),
        "margin_usd": round(float(margin_usd), 4),
        "spent_usd": round(float(spent_usd), 4),
        "cycle_end": cycle_end.isoformat(),
        "floor_usd": floor_usd,
        "ceiling_usd": ceiling_usd,
        "limit": limit,
        "wallet_usd": None if wallet_usd is None else round(float(wallet_usd), 4),
        "reserve_usd": round(float(reserve_usd), 4),
    }
    out["why"] = why(out)
    return out


def _usd(value):
    return f"${value:,.2f}"


def why(p):
    """The admin card's one sentence."""
    accounts = p["expected_accounts_per_day"]
    rate = (f"about {accounts:.0f} new trial accounts a day" if accounts >= 1.5
            else "about 1 new trial account a day")
    head = (f"{_usd(p['per_account_usd_cap'])} per new account because "
            f"{_usd(p['daily_usd_cap'])} a day is left for the trial "
            f"({_usd(p['usable_usd'])} of credit above the "
            f"{_usd(p['margin_usd'])} margin over {p['days_left']:.1f} days) "
            f"and {rate} are signing up")
    if p["bound"] == "wallet":
        admitted = int(p["daily_usd_cap"] // p["per_account_usd_cap"]) if p["per_account_usd_cap"] else 0
        return (f"{_usd(p['per_account_usd_cap'])} per new account and "
                f"{_usd(p['daily_usd_cap'])} a day for the trial (about {admitted} "
                f"whole trials a day) because the Anthropic wallet holds an "
                f"estimated {_usd(p['wallet_usd'])}, of which "
                f"{_usd(p['reserve_usd'])} is kept for paying subscribers until "
                f"the credit refills in {p['days_left']:.1f} days, while {rate} "
                f"are signing up; a top-up raises it again.")
    if p["bound"] == "ceiling":
        return (head + f", which would allow {_usd(p['raw_per_account_usd'])}"
                f" each, held at the {_usd(p['ceiling_usd'])} ceiling.")
    if p["bound"] == "floor":
        return (head + f", which would allow only "
                f"{_usd(p['raw_per_account_usd'])} each, kept at the "
                f"{_usd(p['floor_usd'])} floor; the daily limit stops new "
                "trials for the day first.")
    return head + "."


# ------------------------------------------------------------------ #
#  Reads                                                               #
# ------------------------------------------------------------------ #

def material(old, new):
    """A daily cap moves by a cent or two every recompute as the days run
    down; it is rewritten (and logged) only when it moved by DAILY_STEP_PCT
    or DAILY_STEP_USD, whichever is larger. The per-account cap is rewritten
    on any cent."""
    return abs(new - old) >= max(DAILY_STEP_USD, abs(old) * DAILY_STEP_PCT)


def _stored(cur):
    cur.execute("SELECT value FROM app_kv WHERE key = %s", (free_trial.KEY,))
    raw = _v(cur.fetchone(), "value", 0)
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _excluded(flag):
    ids = set()
    for key in ("test_user_ids", "internal_user_ids", "excluded_user_ids"):
        for x in flag.get(key) or free_trial.DEFAULTS.get(key) or []:
            try:
                ids.add(int(x))
            except (TypeError, ValueError):
                continue
    return sorted(ids)


def grant_counts(cur, now, excluded):
    cur.execute("SELECT to_regclass('public.trial_budgets') AS t")
    if not _v(cur.fetchone(), "t", 0):
        return 0, 0
    cur.execute("""SELECT
                     COUNT(*) FILTER (WHERE granted_at >= %s) AS d1,
                     COUNT(*) FILTER (WHERE granted_at >= %s) AS d3
                   FROM trial_budgets
                  WHERE granted_at < %s
                    AND NOT (user_id = ANY(%s))""",
                (now - timedelta(hours=WINDOW_SHORT_H),
                 now - timedelta(hours=WINDOW_LONG_H), now,
                 list(excluded) or [0]))
    row = cur.fetchone()
    return int(_v(row, "d1", 0) or 0), int(_v(row, "d3", 1) or 0)


def wallet_room(conn, now, cycle_end):
    """(estimated wallet, paying subscribers' reserve) or (None, 0.0) when
    the owner never confirmed a balance or the meter needs a resync.
    Read-only."""
    import anthropic_wallet
    days_left = max(1.0, (cycle_end - now).total_seconds() / 86400.0)
    since = (now - timedelta(days=RESERVE_WINDOW_DAYS)).date()
    with conn.cursor() as cur:
        config = anthropic_wallet._read(cur, anthropic_wallet.KEY)
        if not config:
            return None, 0.0
        wallet = anthropic_wallet.estimate(config, anthropic_wallet._spend(cur))
        if wallet is None:
            return None, 0.0
        cur.execute("""SELECT COALESCE(SUM(paid_usd), 0) AS usd
                         FROM trial_budget_days WHERE day >= %s""", (since,))
        paid = float(_v(cur.fetchone(), "usd", 0) or 0.0)
        company = 0.0
        cur.execute("SELECT to_regclass('public.spend_ledger') AS t")
        if _v(cur.fetchone(), "t", 0):
            cur.execute("""SELECT COALESCE(SUM(usd), 0) AS usd FROM spend_ledger
                            WHERE provider = 'anthropic' AND lane <> 'customer'
                              AND day >= %s""", (since,))
            company = float(_v(cur.fetchone(), "usd", 0) or 0.0)
    daily = (paid + company) / RESERVE_WINDOW_DAYS
    reserve = max(PAID_RESERVE_FLOOR_USD, daily * days_left * PAID_RESERVE_FACTOR)
    return float(wallet), round(reserve, 4)


def current(conn, now=None):
    """What the pacing would set now. Read-only."""
    now = now or datetime.now(timezone.utc)
    cfg = claude_credit.read_config(conn)
    credit = claude_credit.status(conn, now=now)
    with conn.cursor() as cur:
        flag = _stored(cur)
        d1, d3 = grant_counts(cur, now, _excluded(flag))
    on_since = _parse_time(flag.get("switched_at")) if flag.get("on") else None
    cycle_end = _parse_time(credit["cycle_end"])
    wallet, reserve = wallet_room(conn, now, cycle_end)
    return compute(cfg["cycle_budget_usd"], cfg["safety_margin_usd"],
                   credit["spent_usd"], cycle_end, now, d1, d3, on_since,
                   wallet_usd=wallet, reserve_usd=reserve)


def card(flag):
    """The stored pacing as the admin card shows it (trial_toggle.status)."""
    p = (flag or {}).get("pacing")
    if not isinstance(p, dict):
        return None
    return {"on": not bool((flag or {}).get("pacing_off")),
            "why": p.get("why"),
            "per_account_usd_cap": p.get("per_account_usd_cap"),
            "daily_usd_cap": p.get("daily_usd_cap"),
            "expected_accounts_per_day": p.get("expected_accounts_per_day"),
            "days_left": p.get("days_left"),
            "changed_at": p.get("changed_at"),
            "bound": p.get("bound")}


# ------------------------------------------------------------------ #
#  The write                                                           #
# ------------------------------------------------------------------ #

def run(conn, now=None, actor="pacing"):
    """Recompute; write ``free_result`` only when a cap changed. Returns
    {"changed": bool, "skipped": reason|None, "pacing": {...}}. The caller
    commits."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK,))
        flag = _stored(cur)
    if flag.get("pacing_off"):
        return {"changed": False, "skipped": "pacing_off", "pacing": None}
    if not flag:
        return {"changed": False, "skipped": "no_flag", "pacing": None}
    paced = current(conn, now=now)
    old_daily = flag.get("daily_usd_cap")
    old_cap = flag.get("per_account_usd_cap")
    same = (old_daily is not None and old_cap is not None
            and not material(float(old_daily), paced["daily_usd_cap"])
            and abs(float(old_cap) - paced["per_account_usd_cap"]) < 0.005)
    if same:
        return {"changed": False, "skipped": None, "pacing": paced}
    previous = flag.get("pacing") if isinstance(flag.get("pacing"), dict) else {}
    history = list(previous.get("history") or [])[-(HISTORY_KEEP - 1):]
    history.append({"at": now.isoformat(),
                    "daily_usd_cap": [old_daily, paced["daily_usd_cap"]],
                    "per_account_usd_cap": [old_cap,
                                            paced["per_account_usd_cap"]],
                    "expected_accounts_per_day":
                        paced["expected_accounts_per_day"],
                    "usable_usd": paced["usable_usd"],
                    "days_left": paced["days_left"]})
    value = dict(flag)
    value["daily_usd_cap"] = paced["daily_usd_cap"]
    value["per_account_usd_cap"] = paced["per_account_usd_cap"]
    value["pacing"] = dict(paced, changed_at=now.isoformat(), by=actor,
                           history=history)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                    (free_trial.KEY, json.dumps(value, sort_keys=True,
                                                default=str)))
    with free_trial._lock:
        free_trial._cache.update(value=None, expires=0.0)
    msg = (f"trial pacing: daily_usd_cap {old_daily} -> "
           f"{paced['daily_usd_cap']}, per_account_usd_cap {old_cap} -> "
           f"{paced['per_account_usd_cap']} (usable ${paced['usable_usd']:.2f}"
           f" over {paced['days_left']:.2f} days, "
           f"{paced['expected_accounts_per_day']:.2f} accounts/day, "
           f"bound={paced['bound']})")
    log.warning(msg)
    print(msg, flush=True)
    return {"changed": True, "skipped": None, "pacing": value["pacing"]}


# ------------------------------------------------------------------ #
#  Scheduler                                                           #
# ------------------------------------------------------------------ #

_scheduler = None
_scheduler_lock = threading.Lock()


def _connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from database_config import preferred_database_url
    return psycopg2.connect(preferred_database_url(),
                            cursor_factory=RealDictCursor, connect_timeout=5)


def tick():
    try:
        conn = _connect()
    except Exception as exc:                     # pragma: no cover
        print(f"[trial_pacing] database unavailable: {exc}", flush=True)
        return {"error": str(exc)[:200]}
    try:
        result = run(conn)
        conn.commit()
        return result
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:                        # pragma: no cover
            pass
        print(f"[trial_pacing] tick failed: {exc}", flush=True)
        return {"error": str(exc)[:200]}
    finally:
        conn.close()


def start_scheduler(app):
    """Every RECOMPUTE_EVERY_S and once a minute after boot. The advisory
    lock and the change-only write make N gunicorn schedulers safe."""
    global _scheduler
    if os.getenv("TRIAL_PACING_ENABLED", "1") not in ("1", "true"):
        return
    url = str(app.config.get("DATABASE_URL") or "")
    if not url or "//stub/" in url:          # tests and CI stub the database
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:                          # pragma: no cover
        app.logger.warning("apscheduler missing: trial pacing not started")
        return
    with _scheduler_lock:
        if _scheduler is not None:
            return
        scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
        scheduler.add_job(
            tick, "interval", seconds=RECOMPUTE_EVERY_S, id="trial_pacing",
            replace_existing=True,
            next_run_time=(datetime.now(timezone.utc)
                           + timedelta(seconds=60)))
        scheduler.start()
        _scheduler = scheduler
