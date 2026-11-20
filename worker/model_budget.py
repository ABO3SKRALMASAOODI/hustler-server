"""The model budget governor: the best Claude model the Anthropic credit can
pay for, request by request (owner, 20 Nov 2026).

THIS FILE EXISTS TWICE, byte-identical: backend/model_budget.py and
worker/model_budget.py (a test asserts it). It needs only a DB-API connection
and its twins claude_credit.py and spend_ledger.py.

Owner, verbatim: "Move the customers to the upper model, but put the available
credits at the Anthropic API into account, such as we give them the highest
quality without running out of credits, because if we run out of credits, we
will not be able to fill them."

What it does. At the start of a PAYING customer's request, the worker asks
``decide`` which model edits it. The answer is the highest model on the ladder
(Opus, then Sonnet, then the plan's model) whose expected cost still fits:

  * TODAY'S ALLOWANCE. What the Anthropic wallet held at the start of today
    (the owner-confirmed balance minus everything metered since, exactly as
    backend/anthropic_wallet.py estimates it, plus customers' spend so far
    today), less ``reserve_usd``, spread over the days left until
    ``refill_at``. Unused allowance rolls into tomorrow's by itself, so the
    credit is used before it expires and never runs out before. When the
    Claude credit switch's line (claude_credit) is lower, its room counts
    instead: an upgrade never trips the switch.
  * THE CUSTOMERS' SPEND TODAY (trial_budget_days, the wallet's own customer
    meter) plus the request's expected cost must fit the allowance;
  * ONE ACCOUNT'S PREMIUM SPEND TODAY plus the expected cost must fit
    ``account_share`` of the allowance, so one account cannot spend the day.

The plan's model is the floor and is always allowed: the governor never
refuses, holds or stops a request; it only chooses a model. Not paying, not a
customer account (spend_ledger lanes), the wallet unknown, the refill date
passed without a new confirmed balance, or any error: the plan's model, and
the reason is logged once per process.

Expected cost per request starts at ``expected_usd`` and learns from the
recent real requests on each model (llm_calls, customers only).

Credits: an upgraded request is charged as if it ran on ``charge_as`` (the
plan's Haiku card), like the edit rung: customers' credits buy as many edits
as before. llm_calls and the meters keep the real model and the real cost.

app_kv ``model_budget`` (JSON, read at most every 60 s). An absent row means
DEFAULTS, which are ON (the owner decided); {"on": false} is the behaviour
before the governor.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

KEY = "model_budget"
WALLET_KEY = "anthropic_wallet"     # backend/anthropic_wallet.py KEY
CACHE_S = 60.0

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"

DEFAULTS = {
    "on": True,
    "ladder": [OPUS, SONNET, HAIKU],
    "reserve_usd": 15.0,
    # The Claude credit cycle ends here and its promotional credit expires
    # with it (app_kv claude_credit). Nothing is assumed after it.
    "refill_at": "2026-12-08T00:00:00+00:00",
    "account_share": 0.4,
    "expected_usd": {HAIKU: 0.04, SONNET: 0.6, OPUS: 1.2},
    "charge_as": HAIKU,
    # The Claude credit switch's line (claude_credit.line_usd) bounds the
    # allowance too, so an upgrade never switches the free trial off.
    "respect_credit_line": True,
    # Learning: the last ``learn_requests`` finished customer requests on a
    # model in ``learn_days``, blended with the default at ``prior_weight``
    # requests' worth.
    "learn_days": 14,
    "learn_requests": 20,
    "prior_weight": 2.0,
    # After refill_at, a balance confirmed since then is spread over this many
    # days (a new refill_at should be set; until then this is the horizon).
    "horizon_days_after_refill": 30.0,
    # Internal accounts governed like customers (verification). Customers
    # need no listing.
    "test_user_ids": [],
}

# Why a request kept the plan's model (or got another), in plain words.
REASONS = {
    "fits": "the request fits today's allowance",
    "continuation": "the request keeps the model it started on",
    "after_error": "the previous slice failed on the upgraded model",
    "off": "the governor is switched off",
    "not_paying": "premium models go to paying accounts",
    "not_customer": "internal and replay work keep the plan's model",
    "plan_model_not_on_ladder": "the plan's model is not on the ladder",
    "daily_allowance": "nothing above the plan's model fits today's allowance",
    "account_share": "this account has used its share of today's allowance",
    "wallet_unknown": "the Anthropic wallet estimate is unknown",
    "refill_passed": "the refill date passed without a new confirmed balance",
    "error": "the budget lookup failed",
}

_lock = threading.Lock()
_cache = {}
_logged = set()


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def _now(now=None):
    moment = now or datetime.now(timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


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


def _num(value, default, lo=0.0, hi=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out or out < lo or (hi is not None and out > hi):
        return default
    return out


def _model(value):
    name = str(value or "").strip().lower()
    return name if name.startswith("claude-") else None


def _log_once(key, message):
    if key in _logged:
        return
    _logged.add(key)
    print(f"[model_budget] {message}", flush=True)


def reset_cache():
    """Forget cached config and budget state (tests, and after a write)."""
    with _lock:
        _cache.clear()
    _logged.clear()


# ------------------------------------------------------------------ config

def parse_config(raw):
    """app_kv text (or dict) -> config with every default filled in. Absent
    or unreadable is DEFAULTS (ON); only an explicit false switches it off."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = json.loads(json.dumps(DEFAULTS))
    on = data.get("on", True)
    out["on"] = not (on is False or str(on).strip().lower() in
                     ("false", "0", "off", "no"))
    ladder = []
    for name in data.get("ladder") or []:
        model = _model(name)
        if model and model not in ladder:
            ladder.append(model)
    if ladder:
        out["ladder"] = ladder
    out["reserve_usd"] = _num(data.get("reserve_usd"), DEFAULTS["reserve_usd"])
    refill = _parse_time(data.get("refill_at"))
    out["refill_at"] = (refill or _parse_time(DEFAULTS["refill_at"])).isoformat()
    out["account_share"] = _num(data.get("account_share"),
                                DEFAULTS["account_share"], 0.0, 1.0)
    expected = dict(DEFAULTS["expected_usd"])
    for name, usd in (data.get("expected_usd") or {}).items():
        model = _model(name)
        if model:
            expected[model] = _num(usd, expected.get(model, 0.0))
    out["expected_usd"] = expected
    out["charge_as"] = _model(data.get("charge_as")) or DEFAULTS["charge_as"]
    out["respect_credit_line"] = data.get("respect_credit_line", True) is not False
    out["learn_days"] = _num(data.get("learn_days"), DEFAULTS["learn_days"], 1.0, 90.0)
    out["learn_requests"] = int(_num(data.get("learn_requests"),
                                     DEFAULTS["learn_requests"], 1.0, 500.0))
    out["prior_weight"] = _num(data.get("prior_weight"), DEFAULTS["prior_weight"])
    out["horizon_days_after_refill"] = _num(
        data.get("horizon_days_after_refill"),
        DEFAULTS["horizon_days_after_refill"], 1.0)
    tests = []
    for value in data.get("test_user_ids") or []:
        try:
            tests.append(int(value))
        except (TypeError, ValueError):
            continue
    out["test_user_ids"] = tests
    return out


def read_config(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (KEY,))
        return parse_config(_v(cur.fetchone(), "value", 0))


def _cached(key, compute, max_age_s=CACHE_S):
    stamp = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit is not None and stamp - hit[0] < max_age_s:
            return hit[1]
    value = compute()
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value


def config(conn):
    """The live config, read at most every 60 s."""
    return _cached("config", lambda: read_config(conn))


# ------------------------------------------------------------------ meters

def _wallet_spend(cur):
    """backend/anthropic_wallet._spend: customers (trial_budget_days, all
    time) plus company Anthropic spend (spend_ledger, non-customer lanes)."""
    cur.execute("""SELECT COALESCE(SUM(GREATEST(spent_usd-extras_usd,0)+paid_usd),0)
                   AS usd FROM trial_budget_days""")
    customers = float(_v(cur.fetchone(), "usd", 0) or 0.0)
    cur.execute("SELECT to_regclass('public.spend_ledger') AS t")
    if not _v(cur.fetchone(), "t", 0):
        return customers
    cur.execute("""SELECT COALESCE(SUM(usd),0) AS usd FROM spend_ledger
                   WHERE provider='anthropic' AND lane<>'customer'""")
    return customers + float(_v(cur.fetchone(), "usd", 0) or 0.0)


def wallet(conn):
    """{"estimate_usd", "confirmed_at"}: the owner-confirmed Anthropic balance
    minus Valmera's metered spend since (anthropic_wallet.estimate). The
    estimate is None when no balance was confirmed or the meter went
    backwards (unknown, never a funded or an empty wallet)."""
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (WALLET_KEY,))
        raw = _v(cur.fetchone(), "value", 0)
        try:
            cfg = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, ValueError):
            cfg = {}
        if not isinstance(cfg, dict) or "confirmed_balance_usd" not in cfg:
            return {"estimate_usd": None, "confirmed_at": None}
        spend = _wallet_spend(cur)
    try:
        balance = float(cfg["confirmed_balance_usd"])
        baseline = float(cfg.get("meter_baseline_usd") or 0.0)
    except (TypeError, ValueError):
        return {"estimate_usd": None, "confirmed_at": cfg.get("confirmed_at")}
    if spend != spend or spend < baseline - 0.000001:
        return {"estimate_usd": None, "confirmed_at": cfg.get("confirmed_at")}
    return {"estimate_usd": round(balance - max(0.0, spend - baseline), 6),
            "confirmed_at": cfg.get("confirmed_at")}


def customers_spent_today(conn, now=None):
    """Customers' Anthropic spend so far today (UTC), on the wallet's own
    customer meter (trial_budget_days)."""
    day = _now(now).date()
    with conn.cursor() as cur:
        cur.execute("""SELECT COALESCE(SUM(GREATEST(spent_usd-extras_usd,0)+paid_usd),0)
                       AS usd FROM trial_budget_days WHERE day = %s""", (day,))
        return float(_v(cur.fetchone(), "usd", 0) or 0.0)


def _internal_ids():
    import spend_ledger
    return sorted(int(x) for x in spend_ledger.INTERNAL_USER_IDS)


def account_premium_today(conn, user_id, models, now=None):
    """This account's spend today on the premium models (the real provider
    cost its requests' calls recorded)."""
    models = [m for m in models if m]
    if not models or user_id is None:
        return 0.0
    start = datetime.combine(_now(now).date(), datetime.min.time(),
                             tzinfo=timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""SELECT COALESCE(SUM(COALESCE(
                                  (l.response->>'provider_cost_usd')::float, 0)), 0)
                              AS usd
                         FROM llm_calls l JOIN video_jobs j ON j.id = l.job_id
                        WHERE l.created_at >= %s AND l.model = ANY(%s)
                          AND j.user_id = %s""",
                    (start, list(models), int(user_id)))
        return float(_v(cur.fetchone(), "usd", 0) or 0.0)


def learned_samples(conn, models, cfg, now=None):
    """{model: [usd, ...]}: real per-request costs on each model, the latest
    finished customer requests first (a request is its root agent job and
    every continuation; its cost on a model is what its calls on that model
    cost). Only requests this governor ran on that model teach it (their
    jobs carry payload.model_budget): the free trial's one Opus planning
    call is not an Opus request. One query for the whole ladder."""
    now = _now(now)
    out = {m: [] for m in models}
    with conn.cursor() as cur:
        cur.execute("""SELECT model, usd FROM (
                           SELECT l.model AS model,
                                  SUM(COALESCE(
                                      (l.response->>'provider_cost_usd')::float,
                                      0)) AS usd,
                                  ROW_NUMBER() OVER (
                                      PARTITION BY l.model
                                      ORDER BY MAX(l.created_at) DESC) AS rn
                             FROM llm_calls l
                             JOIN video_jobs j ON j.id = l.job_id
                            WHERE l.created_at > %s AND l.model = ANY(%s)
                              AND j.type = 'agent_turn'
                              AND j.payload->'model_budget'->>'model' = l.model
                              AND NOT (j.user_id = ANY(%s))
                            GROUP BY l.model,
                                     COALESCE((j.payload->>'root_agent_job_id')::int,
                                              j.id)
                           HAVING MAX(l.created_at) < %s) AS requests
                        WHERE rn <= %s""",
                    (now - timedelta(days=float(cfg["learn_days"])),
                     list(models), _internal_ids(),
                     now - timedelta(minutes=10), int(cfg["learn_requests"])))
        for row in cur.fetchall():
            model = _v(row, "model", 0)
            if model in out:
                out[model].append(float(_v(row, "usd", 1) or 0.0))
    return out


def blend(prior, samples, prior_weight):
    """The default counts as ``prior_weight`` requests; real ones take over."""
    samples = [max(0.0, float(s)) for s in samples or []]
    weight = max(0.0, float(prior_weight))
    if not samples and weight == 0:
        return float(prior)
    return (float(prior) * weight + sum(samples)) / (weight + len(samples))


def expected_costs(conn, cfg, now=None):
    try:
        learned = learned_samples(conn, cfg["ladder"], cfg, now)
    except Exception as exc:  # noqa: BLE001 - the defaults stand
        _log_once("learn:" + type(exc).__name__,
                  f"learning skipped ({str(exc)[:120]}); defaults stand")
        learned = {}
    out = {}
    for model in cfg["ladder"]:
        prior = float(cfg["expected_usd"].get(model, DEFAULTS["expected_usd"][OPUS]))
        samples = learned.get(model) or []
        out[model] = {"usd": round(blend(prior, samples, cfg["prior_weight"]), 4),
                      "samples": len(samples)}
    return out


# ------------------------------------------------------------------ allowance

def _days_between(start, end):
    return (end - start).total_seconds() / 86400.0


def budget_state(conn, cfg, now=None):
    """Today's allowance and spend (not per account). Cached by callers."""
    now = _now(now)
    today = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
    purse = wallet(conn)
    estimate = purse["estimate_usd"]
    spent = customers_spent_today(conn, now)
    refill = _parse_time(cfg["refill_at"])
    state = {"wallet_usd": estimate, "spent_today_usd": round(spent, 6),
             "refill_at": refill.isoformat(), "allowance_usd": 0.0,
             "days_left": None, "line_allowance_usd": None, "problem": None}
    if estimate is None:
        state["problem"] = "wallet_unknown"
        return state
    if now >= refill:
        confirmed = _parse_time(purse.get("confirmed_at"))
        if confirmed is None or confirmed < refill:
            state["problem"] = "refill_passed"
            return state
        days = float(cfg["horizon_days_after_refill"])
    else:
        days = _days_between(today, refill)
    days = max(1.0, days)
    # The wallet as it stood at the start of today: today's own spend is
    # counted once, against the allowance, not twice.
    start_of_day = estimate + spent
    allowance = max(0.0, start_of_day - float(cfg["reserve_usd"])) / days
    state["days_left"] = round(days, 3)
    if cfg.get("respect_credit_line"):
        import claude_credit
        ccfg = claude_credit.read_config(conn)
        start, end = claude_credit.cycle_window(ccfg, now)
        cycle = claude_credit.cycle_spend(conn, start, end)
        room = max(0.0, claude_credit.line_usd(ccfg) - (cycle - spent))
        line_allowance = room / max(1.0, _days_between(today, end))
        state["line_allowance_usd"] = round(line_allowance, 6)
        allowance = min(allowance, line_allowance)
    state["allowance_usd"] = round(allowance, 6)
    return state


def _state(conn, cfg, now=None):
    if now is not None:          # an explicit clock is a test or a report
        return budget_state(conn, cfg, now), expected_costs(conn, cfg, now)
    return _cached("state", lambda: (budget_state(conn, cfg),
                                     expected_costs(conn, cfg)))


# ------------------------------------------------------------------ the pick

def pick(ladder, floor, allowance, spent_today, account_spent, expected, share):
    """(model, reason): the highest ladder model above ``floor`` whose
    expected cost fits both today's allowance and this account's share of
    it; ``floor`` when none does. Pure."""
    if floor not in ladder:
        return floor, "plan_model_not_on_ladder"
    reason = "daily_allowance"
    for model in ladder[:ladder.index(floor)]:
        cost = float(expected.get(model, float("inf")))
        if spent_today + cost > allowance:
            continue
        if account_spent + cost > share * allowance:
            reason = "account_share"
            continue
        return model, "fits"
    return floor, reason


def _decision(model, plan_model, reason, cfg=None, **numbers):
    out = {"model": model, "plan_model": plan_model, "reason": reason,
           "upgraded": model != plan_model,
           "charge_as": (cfg or DEFAULTS)["charge_as"],
           "decided_at": _now().isoformat()}
    out.update({k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in numbers.items()})
    return out


def decide(conn, user_id, plan_model, paying, now=None):
    """The model for a new request. Never raises; any doubt is the plan's
    model. ``paying``: the account pays (subscribed, not on a trial)."""
    try:
        cfg = config(conn) if now is None else read_config(conn)
        if not cfg["on"]:
            return _decision(plan_model, plan_model, "off", cfg)
        if not paying:
            return _decision(plan_model, plan_model, "not_paying", cfg)
        import spend_ledger
        if spend_ledger.lane_of(user_id) != spend_ledger.CUSTOMER and \
                int(user_id or 0) not in cfg["test_user_ids"]:
            return _decision(plan_model, plan_model, "not_customer", cfg)
        if _model(plan_model) not in cfg["ladder"]:
            return _decision(plan_model, plan_model, "plan_model_not_on_ladder", cfg)
        floor = _model(plan_model)
        state, expected = _state(conn, cfg, now)
        if state["problem"]:
            _log_once(state["problem"], REASONS[state["problem"]] +
                      "; paying requests keep the plan's model")
            return _decision(plan_model, plan_model, state["problem"], cfg,
                             wallet_usd=state["wallet_usd"])
        premium = cfg["ladder"][:cfg["ladder"].index(floor)]
        mine = account_premium_today(conn, user_id, premium, now)
        costs = {m: expected[m]["usd"] for m in expected}
        model, reason = pick(cfg["ladder"], floor, state["allowance_usd"],
                             state["spent_today_usd"], mine, costs,
                             cfg["account_share"])
        return _decision(model if model != floor else plan_model, plan_model,
                         reason, cfg,
                         allowance_usd=float(state["allowance_usd"]),
                         spent_today_usd=float(state["spent_today_usd"]),
                         account_premium_usd=float(mine),
                         expected_usd=float(costs.get(model, 0.0)),
                         wallet_usd=state["wallet_usd"],
                         days_left=state["days_left"])
    except Exception as exc:  # noqa: BLE001 - the plan's model on any doubt
        _log_once("error:" + type(exc).__name__,
                  f"decision failed ({type(exc).__name__}: {str(exc)[:160]}); "
                  "the plan's model")
        return _decision(plan_model, plan_model, "error")


def kept(previous, root_id, plan_model, continuation_state=None):
    """A continuation of the same request keeps the model it started on.
    None when ``previous`` is not this request's decision. A slice that
    failed with an exception on an upgraded model continues on the plan's
    model: the request still finishes."""
    if not isinstance(previous, dict) or not previous.get("model"):
        return None
    try:
        if int(previous.get("root") or 0) != int(root_id or 0):
            return None
    except (TypeError, ValueError):
        return None
    out = dict(previous)
    state = continuation_state if isinstance(continuation_state, dict) else {}
    if out["model"] != plan_model and state.get("exception_fingerprint"):
        out.update(model=plan_model, upgraded=False, reason="after_error",
                   failed_model=previous["model"])
        return out
    out["reason"] = "continuation"
    return out


def summary(conn, now=None):
    """The admin card's line: the switch, today's allowance and spend, and the
    model a paying account with no premium spend today would get now."""
    cfg = read_config(conn)
    out = {"on": cfg["on"], "ladder": cfg["ladder"],
           "reserve_usd": cfg["reserve_usd"], "refill_at": cfg["refill_at"],
           "account_share": cfg["account_share"], "charge_as": cfg["charge_as"]}
    if not cfg["on"]:
        return out
    state = budget_state(conn, cfg, now)
    expected = expected_costs(conn, cfg, now)
    out.update(state)
    out["expected_usd"] = expected
    floor = cfg["ladder"][-1]
    if state["problem"]:
        out["next_model"] = floor
        out["reason"] = state["problem"]
    else:
        out["next_model"], out["reason"] = pick(
            cfg["ladder"], floor, state["allowance_usd"],
            state["spent_today_usd"], 0.0,
            {m: expected[m]["usd"] for m in expected}, cfg["account_share"])
    out["reason_text"] = REASONS.get(out["reason"], out["reason"])
    return out
