"""The owner's one control for the free trial (week of 29 Oct 2026).

GET /admin/video/trial and POST /admin/video/trial (routes/admin_video.py)
read and write it; the admin's Overview shows it as one card that works on a
phone. What it shows, in the owner's words:

* **ON**, **OFF** or **auto-OFF with the reason.** ON: new accounts get the
  First cut and "just enough" Claude credit, and export is the paywall. OFF:
  the owner switched it off; everyone meets today's pre-edit paywall.
  auto-OFF: the owner left it on, but the automatic credit switch
  (claude_credit.py) turned the Claude lane off because this cycle's spend
  reached the line, or Anthropic refused for billing or the key. Never-paid
  accounts then meet today's pre-edit paywall by themselves, and it comes
  back on by itself in the next cycle (a key problem: at the next probe).
* **Spend against the cycle budget** (claude_credit.status: the cycle's
  Claude spend, the budget, the line where it turns off).
* **Accounts served** (trial_budgets granted this cycle and in all, First
  cuts admitted today).
* **The model** (free_result.model, default claude-haiku-5-5).

The switch writes ONLY ``app_kv.free_result.on`` (plus who and when). It
never writes ``claude_credit_state``: the automatic OFF is the credit
switch's own, so pressing ON while auto-OFF records the owner's intent and
the card keeps saying auto-OFF until the credit is back. The backend and the
worker cache the flag for 30 s and 20 s, so a switch takes effect within
30 seconds.
"""
import json
from datetime import datetime, timezone

import claude_credit
import free_trial

DEFAULT_MODEL = "claude-haiku-5-5"
# The calibrated release values (C2_BRUTEFORCE.md §5). Filled in when the
# owner switches ON a row that does not carry them yet, so the first ON can
# never run on an uncalibrated cap.
RELEASE_VALUES = {"model": DEFAULT_MODEL, "per_account_usd_cap": 0.12}

REASON_TEXT = {
    claude_credit.REASON_BUDGET:
        "this cycle's Claude spend reached the line",
    claude_credit.REASON_BILLING:
        "Anthropic refused a call for billing or credit",
    claude_credit.REASON_AUTH: "Anthropic refused the key",
}


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def _stored_flag(cur):
    cur.execute("SELECT value, updated_at FROM app_kv WHERE key = %s",
                (free_trial.KEY,))
    row = cur.fetchone()
    raw = _v(row, "value", 0)
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        value = {}
    return (value if isinstance(value, dict) else {}), _v(row, "updated_at", 1)


def _tables(cur):
    cur.execute("SELECT to_regclass('public.trial_budgets') AS a, "
                "to_regclass('public.trial_budget_days') AS b")
    row = cur.fetchone()
    return bool(_v(row, "a", 0)) and bool(_v(row, "b", 1))


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def status(conn, now=None):
    """Everything the card shows. Read-only."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        stored, updated_at = _stored_flag(cur)
        ledger = _tables(cur)
    flag = dict(free_trial.DEFAULTS)
    flag.update(stored)
    owner_on = flag.get("on") is True or str(flag.get("on")).lower() == "true"
    model = str(flag.get("model") or "").strip()
    if not model.lower().startswith("claude-"):
        model = DEFAULT_MODEL
    credit = claude_credit.status(conn, now=now)
    reason = credit.get("reason")
    if not owner_on:
        state, why = "off", "switched off by the owner"
    elif not ledger:
        # Without migration 040 nothing can be metered, so nothing is
        # admitted: the trial is off whatever the flag says.
        state, why = "auto_off", "the trial ledger (migration 040) is missing"
        reason = "ledger_missing"
    elif not credit.get("on"):
        state = "auto_off"
        why = REASON_TEXT.get(reason, str(reason or "the credit switch is off"))
    else:
        state, why = "on", None
    served = {"cycle": None, "all": None, "first_cuts_today": None,
              "exhausted": None, "spent_usd": None}
    if ledger:
        with conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*) FILTER (
                                     WHERE granted_at >= %s AND granted_at < %s)
                                     AS cycle,
                                  COUNT(*) AS all_time,
                                  COUNT(*) FILTER (WHERE exhausted_at IS NOT NULL
                                                     AND granted_at >= %s)
                                     AS exhausted,
                                  COALESCE(SUM(spent_usd) FILTER (
                                     WHERE granted_at >= %s), 0) AS spent
                             FROM trial_budgets""",
                        (credit["cycle_start"], credit["cycle_end"],
                         credit["cycle_start"], credit["cycle_start"]))
            row = cur.fetchone()
            cur.execute("""SELECT COUNT(*) AS n FROM projects
                           WHERE meta ? 'free_result'
                             AND meta->'free_result'->>'kind' = 'trial'
                             AND (meta->'free_result'->>'admitted_at')
                                 ::timestamptz >= date_trunc('day', NOW())""")
            today = _v(cur.fetchone(), "n", 0)
        served = {"cycle": int(_v(row, "cycle", 0) or 0),
                  "all": int(_v(row, "all_time", 1) or 0),
                  "exhausted": int(_v(row, "exhausted", 2) or 0),
                  "spent_usd": round(float(_v(row, "spent", 3) or 0), 4),
                  "first_cuts_today": int(today or 0)}
    tests = [int(x) for x in flag.get("test_user_ids") or []
             if str(x).lstrip("-").isdigit()]
    return {
        "state": state,
        "owner_on": owner_on,
        "reason": reason if state == "auto_off" else None,
        "reason_text": why,
        "comes_back": (
            None if state != "auto_off" else
            "at the next probe once the key works"
            if reason == claude_credit.REASON_AUTH else
            "after migration 040 is applied" if reason == "ledger_missing"
            else f"by itself in the next cycle ({credit['cycle_end'][:10]})"),
        "since": credit.get("since") if state == "auto_off" else None,
        "model": model,
        "per_account_usd_cap": float(flag.get("per_account_usd_cap") or 0),
        "spend": {"spent_usd": credit.get("spent_usd"),
                  "budget_usd": credit.get("budget_usd"),
                  "line_usd": credit.get("line_usd"),
                  "cycle_start": credit.get("cycle_start"),
                  "cycle_end": credit.get("cycle_end"),
                  "trial_spent_usd": served.get("spent_usd")},
        "accounts": served,
        "test_user_ids": tests,
        "ledger_ready": ledger,
        "updated_at": _iso(updated_at),
        "updated_by": stored.get("updated_by"),
        "takes_effect_s": 30,
    }


def switch(conn, on, actor="admin", now=None):
    """Write the owner's choice into app_kv.free_result (merged; every other
    key kept). Returns the new stored row. The caller commits."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('free_result_switch'))")
        stored, _ = _stored_flag(cur)
        value = dict(stored)
        value["on"] = bool(on)
        if on:
            for key, default in RELEASE_VALUES.items():
                value.setdefault(key, default)
        value["updated_by"] = str(actor)[:80]
        value["switched_at"] = now.isoformat()
        cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                    (free_trial.KEY, json.dumps(value, sort_keys=True)))
    # This process reads its own write at once; the others within 30 s.
    with free_trial._lock:
        free_trial._cache.update(value=None, expires=0.0)
    return value
