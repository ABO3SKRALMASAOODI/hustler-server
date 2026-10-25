"""The automatic credit switch for the Claude lane (owner, 25 Oct 2026).

THIS FILE EXISTS TWICE, byte-identical: backend/claude_credit.py and
worker/claude_credit.py (a test asserts it). It only needs a DB-API
connection, so both processes and backend/scripts/claude_credit_probe.py
share one implementation.

The Claude lane (the free trial and, behind app_kv.studio_for_all_plans, the
Studio agent of subscribers who joined after the release) is funded by the
Anthropic promotional credit: about $200 per Max billing cycle, no rollover.
The current cycle ends 8 Nov 2026 00:00 UTC.

Owner: "If we finish our Claude quota we return to the pre-edit paywall
automatically, without changing anything."

* SPEND. Every Claude call's exact provider cost lands in the daily ledger
  ``trial_budget_days`` (migration 040): ``spent_usd - extras_usd`` for the
  trial (extras are its non-Anthropic generations), ``paid_usd`` for
  subscribers. The cycle total is their sum over the cycle's UTC days.
* OFF. When the cycle total reaches ``cycle_budget_usd - safety_margin_usd``,
  or on any Anthropic billing/credit refusal (or an auth refusal: the key is
  wrong), the state flips OFF: no new trial grants, never-paid accounts meet
  today's pre-edit paywall, a trial turn already running finishes (budget)
  or ends on the plan cards (refusal), and paying subscribers run on their
  existing OpenAI lane.
* ON AGAIN. While OFF, a probe (GitHub Actions every 3 h,
  backend/scripts/claude_credit_probe.py) makes one minimal call. When it
  succeeds in a NEW cycle, the state flips back ON by itself. A switch that
  went off on an auth refusal (a key problem, not the credit) comes back on
  as soon as a probe succeeds and the cycle is under the line.
* Each switch queues ONE admin alert (``trial_alerts``; the backend's alert
  scheduler sends it to the admin address, never a customer).

app_kv rows:
  ``claude_credit``        admin config, optional (DEFAULTS below):
                           {cycle_budget_usd, safety_margin_usd, cycle_end,
                            cycle_start, cycle_months, probe_every_s}
  ``claude_credit_state``  written ONLY by this module:
                           {state: "on"|"off", reason, since, cycle_start,
                            cycle_end, spent_usd, last_probe_at,
                            last_probe_ok, last_probe_error}
A missing state row means ON (nothing has switched it off). A manual
override is one write of {"state": "on", "reason": "manual"}.
"""

import json
import re
from datetime import datetime, timedelta, timezone

CONFIG_KEY = "claude_credit"
STATE_KEY = "claude_credit_state"

DEFAULTS = {
    "cycle_budget_usd": 200.0,
    # Stop this far below the budget: calls in flight, the metering lag and
    # the price card's rounding all land after the line is crossed.
    "safety_margin_usd": 10.0,
    # The Max billing cycle the credit belongs to (owner, 25 Oct 2026).
    "cycle_end": "2026-11-08T00:00:00+00:00",
    "cycle_start": None,
    "cycle_months": 1,
    "probe_every_s": 3 * 3600,
}

ON, OFF = "on", "off"
# Why the switch went off. Credit reasons come back only in a new cycle.
REASON_BUDGET = "budget"
REASON_BILLING = "billing_error"
REASON_AUTH = "auth_error"
CREDIT_REASONS = (REASON_BUDGET, REASON_BILLING)


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def now_utc():
    return datetime.now(timezone.utc)


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


def _num(value, default, lo=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out >= lo else default


def _add_months(moment, months):
    month0 = moment.month - 1 + int(months)
    year = moment.year + month0 // 12
    month = month0 % 12 + 1
    day = min(moment.day, [31, 29 if (year % 4 == 0 and (
        year % 100 != 0 or year % 400 == 0)) else 28, 31, 30, 31, 30, 31,
        31, 30, 31, 30, 31][month - 1])
    return moment.replace(year=year, month=month, day=day)


def parse_config(raw):
    """app_kv text (or dict) -> config with every default filled in."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = dict(DEFAULTS)
    out["cycle_budget_usd"] = _num(data.get("cycle_budget_usd"),
                                   DEFAULTS["cycle_budget_usd"])
    out["safety_margin_usd"] = _num(data.get("safety_margin_usd"),
                                    DEFAULTS["safety_margin_usd"])
    out["cycle_end"] = (_parse_time(data.get("cycle_end"))
                        or _parse_time(DEFAULTS["cycle_end"]))
    out["cycle_start"] = _parse_time(data.get("cycle_start"))
    out["cycle_months"] = max(1, int(_num(data.get("cycle_months"), 1, 1)))
    out["probe_every_s"] = _num(data.get("probe_every_s"),
                                DEFAULTS["probe_every_s"], 60.0)
    return out


def cycle_window(cfg, now=None):
    """(start, end) of the billing cycle that contains ``now``. The configured
    cycle repeats every ``cycle_months``: after 8 Nov the next one is
    8 Nov - 8 Dec, with nothing carried over."""
    now = now or now_utc()
    months = cfg["cycle_months"]
    end = cfg["cycle_end"]
    start = cfg.get("cycle_start") or _add_months(end, -months)
    for _ in range(600):
        if now < end:
            break
        start, end = end, _add_months(end, months)
    for _ in range(600):
        if now >= start:
            break
        start, end = _add_months(start, -months), start
    return start, end


def line_usd(cfg):
    """The spend at which the switch turns off."""
    return max(0.0, cfg["cycle_budget_usd"] - cfg["safety_margin_usd"])


# ------------------------------------------------------------------ #
#  Reads                                                               #
# ------------------------------------------------------------------ #

def _kv(cur, key):
    cur.execute("SELECT value FROM app_kv WHERE key = %s", (key,))
    return _v(cur.fetchone(), "value", 0)


def read_config(conn):
    with conn.cursor() as cur:
        return parse_config(_kv(cur, CONFIG_KEY))


def parse_state(raw):
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    state = OFF if str(data.get("state") or "").lower() == OFF else ON
    data["state"] = state
    return data


def read_state(conn):
    with conn.cursor() as cur:
        return parse_state(_kv(cur, STATE_KEY))


def ledger_ready(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'trial_budget_days'
                          AND column_name = 'paid_usd'""")
        return cur.fetchone() is not None


def cycle_spend(conn, start, end):
    """Anthropic spend recorded for the cycle [start, end) (UTC days)."""
    if not ledger_ready(conn):
        return 0.0
    with conn.cursor() as cur:
        cur.execute("""SELECT COALESCE(SUM(GREATEST(spent_usd - extras_usd, 0)
                                           + paid_usd), 0) AS usd
                         FROM trial_budget_days
                        WHERE day >= %s AND day < %s""",
                    (start.date(), end.date()))
        return float(_v(cur.fetchone(), "usd", 0) or 0.0)


def status(conn, now=None):
    """The switch as every gate reads it. ``on`` is False when the state row
    says off OR the cycle's spend is already past the line (the row may not
    have been written yet). Never writes."""
    now = now or now_utc()
    cfg = read_config(conn)
    state = read_state(conn)
    start, end = cycle_window(cfg, now)
    spent = cycle_spend(conn, start, end)
    line = line_usd(cfg)
    on = state["state"] == ON and spent < line
    return {"on": on, "state": state["state"],
            "reason": (state.get("reason") if state["state"] == OFF
                       else (REASON_BUDGET if spent >= line else None)),
            "since": state.get("since"),
            "cycle_start": start.isoformat(), "cycle_end": end.isoformat(),
            "spent_usd": round(spent, 4),
            "budget_usd": cfg["cycle_budget_usd"], "line_usd": round(line, 4),
            "last_probe_at": state.get("last_probe_at"),
            "last_probe_ok": state.get("last_probe_ok")}


# ------------------------------------------------------------------ #
#  Writes (this module owns claude_credit_state)                      #
# ------------------------------------------------------------------ #

def _write_state(cur, state):
    cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = NOW()""",
                (STATE_KEY, json.dumps(state, default=str, sort_keys=True)))


def _lock(cur):
    # One switch at a time across every worker, the backend and the probe.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('claude_credit_switch'))")


def _scrub(text):
    return re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "[REDACTED]", str(text or ""))[:300]


def queue_alert(cur, dedupe_key, kind, subject, html):
    """Durable admin alert (trial_alerts, migration 040). The backend's alert
    scheduler sends it to the admin address. Never a customer."""
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


_REASON_TEXT = {
    REASON_BUDGET: "the cycle's Claude spend reached the line",
    REASON_BILLING: "Anthropic refused a call for billing or credit",
    REASON_AUTH: "Anthropic refused the key",
}


def switch_off(conn, reason, detail="", now=None):
    """Turn the Claude lane off. Idempotent: returns True only for the call
    that actually switched it (and queued the alert)."""
    now = now or now_utc()
    cfg = read_config(conn)
    start, end = cycle_window(cfg, now)
    with conn.cursor() as cur:
        _lock(cur)
        state = parse_state(_kv(cur, STATE_KEY))
        if state["state"] == OFF:
            return False
        spent = cycle_spend(conn, start, end)
        new = {"state": OFF, "reason": str(reason), "since": now.isoformat(),
               "cycle_start": start.isoformat(), "cycle_end": end.isoformat(),
               "spent_usd": round(spent, 4), "detail": _scrub(detail),
               "last_probe_at": state.get("last_probe_at"),
               "last_probe_ok": state.get("last_probe_ok")}
        _write_state(cur, new)
        why = _REASON_TEXT.get(str(reason), str(reason))
        queue_alert(
            cur, f"credit_off_{end:%Y%m%d}_{now:%Y%m%d%H%M%S}", "credit_switch_off",
            f"Claude lane OFF: {why} (${spent:.2f} of "
            f"${cfg['cycle_budget_usd']:.0f} this cycle)",
            "<p>The Claude lane switched <b>OFF</b> by itself: "
            f"{why}.</p>"
            f"<p>Cycle {start:%d %b} - {end:%d %b %Y} UTC: "
            f"<b>${spent:.2f}</b> of ${cfg['cycle_budget_usd']:.0f} "
            f"(line ${line_usd(cfg):.2f}).</p>"
            "<p>What changed: no new free-trial grants; never-paid accounts "
            "see today's pre-edit paywall; trial turns already running "
            "finish or end on the plan cards; paying subscribers continue "
            "on their OpenAI lane.</p>"
            "<p>It comes back on by itself when a probe succeeds in the next "
            "cycle (a key problem: as soon as a probe succeeds). Override: "
            "app_kv claude_credit_state = {\"state\": \"on\", \"reason\": "
            "\"manual\"}.</p>"
            + (f"<p>Provider said: <code>{_scrub(detail)}</code></p>"
               if detail else ""))
    return True


def switch_on(conn, reason, now=None):
    now = now or now_utc()
    cfg = read_config(conn)
    start, end = cycle_window(cfg, now)
    with conn.cursor() as cur:
        _lock(cur)
        state = parse_state(_kv(cur, STATE_KEY))
        if state["state"] == ON:
            return False
        new = {"state": ON, "reason": str(reason), "since": now.isoformat(),
               "cycle_start": start.isoformat(), "cycle_end": end.isoformat(),
               "previous_reason": state.get("reason"),
               "last_probe_at": now.isoformat(), "last_probe_ok": True}
        _write_state(cur, new)
        queue_alert(
            cur, f"credit_on_{end:%Y%m%d}_{now:%Y%m%d%H%M%S}", "credit_switch_on",
            "Claude lane ON again: the probe succeeded",
            "<p>The Claude lane switched back <b>ON</b> by itself "
            f"({reason}).</p><p>Cycle {start:%d %b} - {end:%d %b %Y} UTC, "
            f"budget ${cfg['cycle_budget_usd']:.0f}. Free-trial grants and "
            "the Claude Studio agent for new subscribers resume.</p>")
    return True


def note_probe(conn, ok, error="", now=None):
    """Record a probe that did not switch anything (OFF stays OFF)."""
    now = now or now_utc()
    with conn.cursor() as cur:
        _lock(cur)
        state = parse_state(_kv(cur, STATE_KEY))
        if state["state"] != OFF:
            return
        state.update(last_probe_at=now.isoformat(), last_probe_ok=bool(ok),
                     last_probe_error=_scrub(error) if error else None)
        _write_state(cur, state)


def check_line(conn, now=None):
    """After a spend: switch off when the cycle crossed the line."""
    now = now or now_utc()
    cfg = read_config(conn)
    start, end = cycle_window(cfg, now)
    if cycle_spend(conn, start, end) < line_usd(cfg):
        return False
    return switch_off(conn, REASON_BUDGET, now=now)


def record_paid_spend(conn, usd, calls=1):
    """A subscriber's Claude call (the paid lane): the cycle ledger only."""
    usd = max(0.0, float(usd or 0.0))
    if not ledger_ready(conn):
        return False
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO trial_budget_days (day, paid_usd, paid_calls)
                       VALUES ((NOW() AT TIME ZONE 'UTC')::date, %s, %s)
                       ON CONFLICT (day) DO UPDATE
                       SET paid_usd = trial_budget_days.paid_usd + EXCLUDED.paid_usd,
                           paid_calls = trial_budget_days.paid_calls
                                        + EXCLUDED.paid_calls,
                           updated_at = NOW()""", (usd, int(calls)))
    return True


# ------------------------------------------------------------------ #
#  The probe's decision (pure; the probe script makes the call)        #
# ------------------------------------------------------------------ #

def probe_plan(conn, now=None):
    """What the probe should do now: {"action": "none"|"enforce_line"|
    "probe", "may_switch_on": bool, ...}. Pure read."""
    now = now or now_utc()
    cfg = read_config(conn)
    state = read_state(conn)
    start, end = cycle_window(cfg, now)
    spent = cycle_spend(conn, start, end)
    line = line_usd(cfg)
    if state["state"] == ON:
        return {"action": "enforce_line" if spent >= line else "none",
                "spent_usd": spent, "line_usd": line}
    last = _parse_time(state.get("last_probe_at"))
    if last and (now - last) < timedelta(seconds=cfg["probe_every_s"] - 60):
        return {"action": "none", "why": "probed recently",
                "spent_usd": spent, "line_usd": line}
    off_end = _parse_time(state.get("cycle_end"))
    new_cycle = off_end is None or now >= off_end
    reason = state.get("reason")
    if reason in CREDIT_REASONS:
        may = new_cycle and spent < line
    else:
        may = spent < line
    return {"action": "probe", "may_switch_on": may, "new_cycle": new_cycle,
            "reason": reason, "spent_usd": spent, "line_usd": line}


def after_probe(conn, plan, ok, error="", now=None):
    """Apply one probe result. Returns "switched_on" | "recorded"."""
    if ok and plan.get("may_switch_on"):
        if switch_on(conn, "probe succeeded" + (
                " in a new cycle" if plan.get("new_cycle") else ""), now=now):
            return "switched_on"
    note_probe(conn, ok, error, now=now)
    return "recorded"
