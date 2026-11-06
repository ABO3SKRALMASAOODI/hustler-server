"""One ledger for every model dollar, and the fence around company spend.

THIS FILE EXISTS TWICE, byte-identical: backend/spend_ledger.py and
worker/spend_ledger.py (a test asserts it), like claude_credit.py beside it.

4 Nov 2026: a batch of company repairs spent $60.90 of the 14-day $71.87 ($52.71
of it on Opus in one hour), its Claude calls landed on the customers' credit
line, the line tripped and the automatic switch turned the free trial off.
Every customer in 14 days together cost $10.55. OpenAI spend was on no ledger at
all, so its wallet ran dry without warning the same morning.

A LANE says who a call was for:

  customer  a real account's work: trial, paid, free, concierge.
  internal  our own accounts (ops_loop.EXCLUDED_USER_IDS; a test ties the lists).
  replay    a replay on a copy, on a stack that runs with SPEND_LANE=replay.

The rules:

* Every call lands here, on every provider, with or without a recorder:
  llm.record in the worker, _meter_concierge in the backend.
* A customer's call is never refused here and this module never reads a
  customer row to decide anything. Their limits stay what they were: the
  account's own credits, the trial's own budget, the provider's own refusal.
* internal and replay stop at their share, BEFORE the call (llm.fence). When a
  share runs out the purpose stops; nothing a customer sees changes.
* The credit switch (claude_credit.py) keeps reading trial_budget_days, and
  only customer calls are written there now, so company spend cannot turn the
  trial off. The shares come out of what the budget leaves unallocated:
  cycle_budget_usd + the shares = the funded credit, so nothing has to be
  subtracted by hand.

app_kv ``spend_fence`` is optional, DEFAULT_SHARES below fills it in:
  {"internal": {"*": {"day_usd": 5, "cycle_usd": 20}},
   "replay": {"anthropic": {...}, "*": {...}}}
A share is per lane and provider; "*" is any provider not named. The cycle is
the Anthropic credit cycle (claude_credit.cycle_window), for every provider.
"""

import json
import os
from datetime import datetime, timezone

import claude_credit

CUSTOMER, INTERNAL, REPLAY = "customer", "internal", "replay"
LANES = (CUSTOMER, INTERNAL, REPLAY)
COMPANY_LANES = (INTERNAL, REPLAY)

CONFIG_KEY = "spend_fence"
# Funded on 6 Nov: Anthropic $199.99 (customers $149.33), OpenAI $406.
DEFAULT_SHARES = {
    INTERNAL: {"*": {"day_usd": 5.0, "cycle_usd": 20.0}},
    REPLAY: {"anthropic": {"day_usd": 15.0, "cycle_usd": 30.0},
             "*": {"day_usd": 100.0, "cycle_usd": 300.0}},
}

INTERNAL_USER_IDS = frozenset({22, 28, 29, 45, 46, 49, 53, 60, 61, 62, 68, 108,
                               398, 1058, 2330} | {
    int(x) for x in os.getenv("COCKPIT_EXTRA_INTERNAL_IDS", "").split(",")
    if x.strip().isdigit()})


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def provider_of(model):
    name = str(model or "").lower()
    if name.startswith("claude"):
        return "anthropic"
    for prefix, provider in (("gpt-", "openai"), ("o1", "openai"),
                             ("o3", "openai"), ("o4", "openai"),
                             ("whisper", "openai"), ("grok", "xai"),
                             ("deepseek", "deepseek")):
        if name.startswith(prefix):
            return provider
    return "other"


def lane_of(user_id=None):
    """A stack that runs replays says so for all of its work; otherwise the
    account decides. Unknown means customer: the side that is never refused."""
    forced = os.getenv("SPEND_LANE", "")
    if forced in LANES:
        return forced
    try:
        return INTERNAL if int(user_id) in INTERNAL_USER_IDS else CUSTOMER
    except (TypeError, ValueError):
        return CUSTOMER


def ready(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.spend_ledger') AS t")
        return bool(_v(cur.fetchone(), "t", 0))


def charge(conn, provider, lane, purpose, usd, calls=1):
    """Add one cost. False when migration 043 has not landed yet."""
    usd = max(0.0, float(usd or 0.0))
    if not ready(conn):
        return False
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO spend_ledger
                         (day, provider, lane, purpose, usd, calls)
                       VALUES ((NOW() AT TIME ZONE 'UTC')::date, %s, %s, %s, %s, %s)
                       ON CONFLICT (day, provider, lane, purpose) DO UPDATE
                       SET usd = spend_ledger.usd + EXCLUDED.usd,
                           calls = spend_ledger.calls + EXCLUDED.calls,
                           updated_at = NOW()""",
                    (str(provider)[:24], str(lane)[:24], str(purpose)[:48],
                     usd, int(calls)))
    return True


def shares(conn):
    """DEFAULT_SHARES with app_kv.spend_fence laid over it, lane by lane."""
    out = {lane: dict(rows) for lane, rows in DEFAULT_SHARES.items()}
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (CONFIG_KEY,))
        raw = _v(cur.fetchone(), "value", 0)
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        data = {}
    for lane in COMPANY_LANES:
        rows = data.get(lane) if isinstance(data, dict) else None
        if isinstance(rows, dict):
            out[lane].update(rows)
    return out


def _spent(cur, lane, provider, since):
    cur.execute("""SELECT COALESCE(SUM(usd), 0) AS usd FROM spend_ledger
                    WHERE lane = %s AND provider = %s AND day >= %s""",
                (lane, provider, since))
    return float(_v(cur.fetchone(), "usd", 0) or 0.0)


def room(conn, lane, provider, now=None):
    """What a company lane may still spend on a provider. ``migrated`` False
    means there is no ledger to read yet: open, and the caller says so."""
    if not ready(conn):
        return {"open": True, "migrated": False, "left_usd": None}
    now = now or datetime.now(timezone.utc)
    table = shares(conn)[lane]
    share = table.get(provider) or table["*"]
    start, end = claude_credit.cycle_window(claude_credit.read_config(conn), now)
    with conn.cursor() as cur:
        cycle = _spent(cur, lane, provider, start.date())
        day = _spent(cur, lane, provider, now.date())
    left = min(float(share.get("day_usd", 0)) - day,
               float(share.get("cycle_usd", 0)) - cycle)
    return {"open": left > 0, "migrated": True, "left_usd": round(max(left, 0), 6),
            "day_spent_usd": round(day, 6), "cycle_spent_usd": round(cycle, 6),
            "day_usd": share.get("day_usd"), "cycle_usd": share.get("cycle_usd"),
            "cycle_end": end.isoformat()}


def cycle_rows(conn, now=None):
    """This cycle's spend by provider, lane and purpose, largest first."""
    now = now or datetime.now(timezone.utc)
    start, end = claude_credit.cycle_window(claude_credit.read_config(conn), now)
    if not ready(conn):
        return {"cycle_start": start.isoformat(), "cycle_end": end.isoformat(),
                "migrated": False, "rows": []}
    with conn.cursor() as cur:
        cur.execute("""SELECT provider, lane, purpose, SUM(usd) AS usd,
                              SUM(calls) AS calls
                         FROM spend_ledger WHERE day >= %s AND day < %s
                        GROUP BY 1, 2, 3 ORDER BY 4 DESC""",
                    (start.date(), end.date()))
        rows = [{"provider": _v(r, "provider", 0), "lane": _v(r, "lane", 1),
                 "purpose": _v(r, "purpose", 2),
                 "usd": round(float(_v(r, "usd", 3) or 0), 4),
                 "calls": int(_v(r, "calls", 4) or 0)} for r in cur.fetchall()]
    return {"cycle_start": start.isoformat(), "cycle_end": end.isoformat(),
            "migrated": True, "rows": rows}
