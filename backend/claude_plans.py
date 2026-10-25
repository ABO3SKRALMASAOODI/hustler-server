"""New subscribers: the Studio agent on Claude, and the Studio for Plus (D1).

THIS FILE EXISTS TWICE, byte-identical: backend/claude_plans.py and
worker/claude_plans.py (a test asserts it). It needs only a DB-API
connection.

Owner (25 Oct 2026): accounts that subscribe AFTER the release get the Studio
agent on the Claude lane, with the model the bake-off picks per plan, plus
MCP, and Plus includes the Studio AI editor (D1). EXISTING subscribers keep
EXACTLY their current contracted lanes and behaviour: they are grandfathered
by subscription start date. A paying user never breaks when the promotional
credit runs out: the Claude lane is only used while the automatic credit
switch (claude_credit.py) is on, and an Anthropic billing refusal mid-turn
fails over to the account's existing OpenAI lane for that turn.

One app_kv row, ``studio_for_all_plans`` (JSON), default OFF:

  {"on": false,
   "cutoff_at": null,             # ISO time of the release; REQUIRED when on
   "studio_plans": ["plus_2026"], # MCP-only plans whose NEW subscribers get
                                  # the Studio AI editor (MCP Connect stays
                                  # MCP-only)
   "claude_lane": true,           # new subscribers' Studio agent on Claude
   "plan_models": {...},          # per plan (the bake-off's pick)
   "default_model": "claude-haiku-5-5",
   "credit_parity": true,         # credits charged at the plan's contracted
                                  # OpenAI rates, so a plan's allowance
                                  # lasts as long on either lane
   "test_user_ids": []}           # internal accounts: apply while off

NEW SUBSCRIBER = subscribed now AND no evidence the subscription started
before ``cutoff_at``: no paid payment (``payments``, amount > 0, completed or
paid) before it, and no Paddle snapshot of the account's subscription with
``started_at`` before it. Every one of the 23 accounts subscribed on 25 Oct
2026 has a paid payment row, so every one of them is grandfathered.
"""

import json
from datetime import datetime, timezone

KEY = "studio_for_all_plans"

# The plan's contracted OpenAI editor model (llm.paid_editor_lanes): what its
# credits are priced at today. Credit parity charges Claude-lane turns at
# these rates.
_SOL_PLANS = frozenset({"advanced", "pro_2026", "max_2026"})
LUNA = "gpt-6-luna"
SOL = "gpt-6.1-sol"

DEFAULT_PLAN_MODELS = {
    # Price parity with the contracted lane until the bake-off decides:
    # Haiku 5.5 = Luna's card, Sonnet 5.5 = Sol's card.
    "plus_2026": "claude-haiku-5-5",
    "pro_2026": "claude-sonnet-5-5",
    "max_2026": "claude-sonnet-5-5",
}
DEFAULT_MODEL = "claude-haiku-5-5"


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def _parse_time(value):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _claude(model, default):
    model = str(model or "").strip()
    return model if model.lower().startswith("claude-") else default


def parse_flag(raw):
    """app_kv text -> normalized flag. Anything unparsable is OFF; ON without
    a valid cutoff_at is OFF too (nobody could be told apart)."""
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
    cutoff = _parse_time(data.get("cutoff_at"))
    on = (data.get("on") is True or str(data.get("on")).lower() == "true")
    default_model = _claude(data.get("default_model"), DEFAULT_MODEL)
    models = dict(DEFAULT_PLAN_MODELS)
    for plan, model in (data.get("plan_models") or {}).items():
        if str(model or "").lower().startswith("claude-"):
            models[str(plan)] = str(model).strip()
    studio = data.get("studio_plans")
    if not isinstance(studio, list):
        studio = ["plus_2026"]
    return {
        "on": bool(on and cutoff is not None),
        "cutoff_at": cutoff,
        "studio_plans": frozenset(str(p) for p in studio
                                  if str(p) != "mcp_connect"),
        "claude_lane": data.get("claude_lane", True) is not False,
        "plan_models": models,
        "default_model": default_model,
        "credit_parity": data.get("credit_parity", True) is not False,
        "test_user_ids": tests,
    }


def read_flag(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.app_kv') AS t")
        if not _v(cur.fetchone(), "t", 0):
            return parse_flag(None)
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (KEY,))
        return parse_flag(_v(cur.fetchone(), "value", 0))


def grandfathered(conn, user_id, cutoff):
    """True when anything shows this account's subscription began before the
    cutoff. A lookup that fails reads as grandfathered (today's behaviour)."""
    if cutoff is None:
        return True
    try:
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT claude_plans_grandfather")
            cur.execute("""SELECT 1 FROM payments
                            WHERE user_id = %s AND amount_cents > 0
                              AND status IN ('completed', 'paid')
                              AND COALESCE(occurred_at, created_at) < %s
                            LIMIT 1""", (int(user_id), cutoff))
            if cur.fetchone() is not None:
                cur.execute("RELEASE SAVEPOINT claude_plans_grandfather")
                return True
            cur.execute("SELECT to_regclass('public.admin_subscription_snapshots') AS t")
            if _v(cur.fetchone(), "t", 0):
                cur.execute("""SELECT 1 FROM admin_subscription_snapshots s
                                 JOIN users u ON u.subscription_id = s.subscription_id
                                WHERE u.id = %s AND s.started_at < %s
                                LIMIT 1""", (int(user_id), cutoff))
                if cur.fetchone() is not None:
                    cur.execute("RELEASE SAVEPOINT claude_plans_grandfather")
                    return True
            cur.execute("RELEASE SAVEPOINT claude_plans_grandfather")
    except Exception:
        try:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT claude_plans_grandfather")
                cur.execute("RELEASE SAVEPOINT claude_plans_grandfather")
        except Exception:
            pass
        return True
    return False


def applies(conn, user_id, subscribed, flag=None):
    """Does the new-subscriber offer apply to this account right now?"""
    if not subscribed or user_id is None:
        return False
    flag = flag if flag is not None else read_flag(conn)
    if int(user_id) in flag["test_user_ids"]:
        return True
    if not flag["on"]:
        return False
    return not grandfathered(conn, user_id, flag["cutoff_at"])


def studio_included(conn, user_id, plan, subscribed, flag=None):
    """True when a plan that is MCP-only today includes the Studio AI editor
    for THIS account (a new subscriber, D1). False means today's rule."""
    flag = flag if flag is not None else read_flag(conn)
    if str(plan or "") not in flag["studio_plans"]:
        return False
    return applies(conn, user_id, subscribed, flag)


def lane_model(flag, plan):
    return flag["plan_models"].get(str(plan or ""), flag["default_model"])


def contracted_model(plan):
    """The OpenAI model this plan's credits are priced at today."""
    return SOL if str(plan or "") in _SOL_PLANS else LUNA


def safe(conn, fn, *args, default=None):
    """fn(conn, *args) inside a savepoint: any error -> ``default`` and the
    caller's transaction is left usable (today's behaviour on any doubt)."""
    if conn is None:
        return default
    try:
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT claude_plans_safe")
        try:
            out = fn(conn, *args)
        except Exception:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT claude_plans_safe")
                cur.execute("RELEASE SAVEPOINT claude_plans_safe")
            return default
        with conn.cursor() as cur:
            cur.execute("RELEASE SAVEPOINT claude_plans_safe")
        return out
    except Exception:
        return default
