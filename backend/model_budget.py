"""The model budget governor: the best Claude model the Anthropic credit can
pay for, request by request and slice by slice (owner, 20 Nov 2026).

THIS FILE EXISTS TWICE, byte-identical: backend/model_budget.py and
worker/model_budget.py (a test asserts it). It needs only a DB-API connection
and its twins claude_credit.py, spend_ledger.py and model_prices.py.

Owner, verbatim: "Move the customers to the upper model, but put the available
credits at the Anthropic API into account, such as we give them the highest
quality without running out of credits, because if we run out of credits, we
will not be able to fill them." And: "maximize the value to users while
keeping the API credits without running out of credits."

THE WALLET. The owner-confirmed Anthropic balance minus everything metered
since (exactly as backend/anthropic_wallet.py estimates it). The credit is
refilled by the owner's subscription: ``refill_usd`` of promotional credit
monthly from ``refill_at`` (claude_credit's own monthly cycle when its
cycle_end is at or past refill_at), and unused promotional credit is lost at each
refill; ``nonexpiring_usd`` (purchased credit) is kept. When a refill moment
has passed and no balance was confirmed after it, the wallet is ASSUMED to be
``nonexpiring_usd + refill_usd`` minus the spend metered since the refill. A
balance confirmed after the refill always wins.

TODAY'S ALLOWANCE. The wallet as it stood at the start of today, less what the
company lanes (replay + internal, spend_ledger's own shares) may still spend
this cycle, less what is kept (``max(reserve_usd, nonexpiring_usd)``), spread
over the days left until the next refill. Unused allowance rolls into
tomorrow's by itself, so the credit that would expire is used and the wallet
never runs dry before the refill. When the Claude credit switch's line
(claude_credit) leaves less room, that counts instead.

THE PICK. At the start of a PAYING customer's request, ``decide`` picks the
highest model configured for the plan whose expected cost fits today's wallet
allowance after spend and outstanding reservations.

At every slice boundary ``reconsider`` decides again from what the request
has really spent: when its next slice no longer fits, it continues on the
next cheaper rung, down to the plan's model. The plan's model is the floor
and is always allowed: the governor never refuses, holds or stops a request;
it only chooses a model. Not paying, not a customer account (spend_ledger
lanes), the wallet unknown, or any error: the plan's model.

Credits use the actual editing model and the configured upgrade markup.

app_kv ``model_budget`` (JSON, read at most every 60 s). An absent row means
DEFAULTS, which are ON (the owner decided); {"on": false} is the behaviour
before the governor. ``test_user_ids`` lists internal accounts governed like
customers; the entry "replay" governs every account of a replay stack
(SPEND_LANE=replay), whose synthetic accounts have no fixed id.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

KEY = "model_budget"
WALLET_KEY = "anthropic_wallet"     # backend/anthropic_wallet.py KEY
CACHE_S = 60.0
MIX_CACHE_S = 600.0

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"

# The main editing call's purpose in llm_calls (agent_loop records "agent").
EDIT_PURPOSE = "agent"
REPLAY_TOKEN = "replay"

DEFAULTS = {
    "on": True,
    "ladder": [OPUS, SONNET, HAIKU],
    "reserve_usd": 15.0,
    # The Anthropic credit refills by itself (owner, 20 Nov 2026): the Ultra
    # subscription grants refill_usd of promotional credit at each cycle and
    # what is left of the last grant expires then. Read from the Console on
    # 20 Nov: $4.26 purchased (no expiry) + $64.78 promotional, which expires
    # 8 Dec 00:00 UTC.
    "refill_at": "2026-12-08T00:00:00+00:00",
    "refill_usd": 200.0,
    "nonexpiring_usd": 4.26,
    "cycle_days": 30.0,
    # The flat cost of a request on each model, used only when there is no
    # token history to price (a fresh install); learned from real requests.
    "expected_usd": {HAIKU: 0.04, SONNET: 0.6, OPUS: 1.2},
    "plan_ladder": {"plus_2026": [OPUS], "pro_2026": [OPUS], "max_2026": [OPUS],
                    "grandfathered": [OPUS]},
    "markup": 1.5,
    # The Claude credit switch's line (claude_credit.line_usd) bounds the
    # allowance too, so an upgrade never switches the free trial off.
    "respect_credit_line": True,
    # Learning: the last ``learn_requests`` finished customer requests on a
    # model in ``learn_days``, blended with the default at ``prior_weight``
    # requests' worth.
    "learn_days": 14,
    "learn_requests": 20,
    "prior_weight": 2.0,
    # Size: a request is priced from its project's last ``size_requests``
    # requests, else from the typical request scaled by its source length
    # (between size_scale_min and size_scale_max times the typical one).
    "size_requests": 3,
    "size_scale_min": 0.5,
    "size_scale_max": 4.0,
    # Upgraded jobs queued or running count at what they are still expected
    # to spend; a row older than this is not in flight.
    "inflight_hours": 12.0,
    # Internal accounts governed like customers (verification); "replay"
    # governs every account of a SPEND_LANE=replay stack.
    "test_user_ids": [],
}

# Why a request kept the plan's model (or got another), in plain words.
REASONS = {
    "fits": "the request fits today's allowance",
    "continuation": "the request's next slice still fits; it keeps its model",
    "stepped_down": "the request's next slice no longer fits on its model; "
                    "it continues on a cheaper one",
    "off": "the governor is switched off",
    "not_paying": "premium models go to paying accounts",
    "not_customer": "internal and replay work keep the plan's model",
    "plan_model_not_on_ladder": "the plan's model is not on the ladder",
    "daily_allowance": "nothing above the plan's model fits today's allowance",
    "wallet_unknown": "the Anthropic wallet estimate is unknown",
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
    out["refill_usd"] = _num(data.get("refill_usd"), DEFAULTS["refill_usd"])
    out["nonexpiring_usd"] = _num(data.get("nonexpiring_usd"),
                                  DEFAULTS["nonexpiring_usd"])
    out["cycle_days"] = _num(data.get("cycle_days"), DEFAULTS["cycle_days"],
                             1.0, 366.0)
    expected = dict(DEFAULTS["expected_usd"])
    for name, usd in (data.get("expected_usd") or {}).items():
        model = _model(name)
        if model:
            expected[model] = _num(usd, expected.get(model, 0.0))
    out["expected_usd"] = expected
    plans = data.get("plan_ladder")
    if isinstance(plans, dict):
        for plan, rungs in plans.items():
            if isinstance(rungs, list):
                out["plan_ladder"][plan] = list(dict.fromkeys(m for m in rungs if m in (OPUS, SONNET)))
    out["markup"] = _num(data.get("markup"), DEFAULTS["markup"], lo=0.01)
    out["respect_credit_line"] = data.get("respect_credit_line", True) is not False
    out["learn_days"] = _num(data.get("learn_days"), DEFAULTS["learn_days"], 1.0, 90.0)
    out["learn_requests"] = int(_num(data.get("learn_requests"),
                                     DEFAULTS["learn_requests"], 1.0, 500.0))
    out["prior_weight"] = _num(data.get("prior_weight"), DEFAULTS["prior_weight"])
    out["size_requests"] = int(_num(data.get("size_requests"),
                                    DEFAULTS["size_requests"], 1.0, 50.0))
    out["size_scale_min"] = _num(data.get("size_scale_min"),
                                 DEFAULTS["size_scale_min"], 0.01, 100.0)
    out["size_scale_max"] = max(out["size_scale_min"], _num(
        data.get("size_scale_max"), DEFAULTS["size_scale_max"], 0.01, 100.0))
    out["inflight_hours"] = _num(data.get("inflight_hours"),
                                 DEFAULTS["inflight_hours"], 0.1, 168.0)
    tests = []
    for value in data.get("test_user_ids") or []:
        if str(value).strip().lower() == REPLAY_TOKEN:
            if REPLAY_TOKEN not in tests:
                tests.append(REPLAY_TOKEN)
            continue
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


def governed(user_id, cfg):
    """Whether this account's requests are governed: a customer, a listed
    internal account, or any account of a replay stack when "replay" is
    listed (a replay's synthetic account has no fixed id)."""
    import spend_ledger
    lane = spend_ledger.lane_of(user_id)
    if lane == spend_ledger.CUSTOMER:
        return True
    tests = cfg.get("test_user_ids") or []
    if lane == spend_ledger.REPLAY and REPLAY_TOKEN in tests:
        return True
    try:
        return int(user_id or 0) in tests
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ refills

def refill_window(conn, cfg, now=None, ccfg=None):
    """(last_refill, next_refill, source): the refill moments around ``now``.
    claude_credit's own cycle (its configured cycle_end, monthly) when that
    cycle_end is at or past ``refill_at`` (both are the subscription's monthly
    date, 8 Dec in Nov 2026); else ``refill_at`` repeating by calendar month
    on the same day (``cycle_days`` / 30, at least one month) in both
    directions, so the refills never drift off the subscription's date.
    ``ccfg``: claude_credit's config when the caller already read it."""
    now = _now(now)
    base = _parse_time(cfg["refill_at"])
    try:
        import claude_credit
        ccfg = ccfg or claude_credit.read_config(conn)
        if ccfg.get("cycle_end") and ccfg["cycle_end"] >= base:
            start, end = claude_credit.cycle_window(ccfg, now)
            return start, end, "claude_credit"
    except Exception as exc:  # noqa: BLE001 - our own refill_at stands
        _log_once("refill:" + type(exc).__name__,
                  f"claude_credit cycle unreadable ({str(exc)[:120]})")
    months = max(1, int(round(float(cfg["cycle_days"]) / 30.0)))
    n = (now.year - base.year) * 12 + (now.month - base.month)
    n -= n % months
    if _add_months(base, n) > now:
        n -= months
    return _add_months(base, n), _add_months(base, n + months), "model_budget"


def _add_months(moment, months):
    """``moment`` moved by whole calendar months, the day clamped to the
    month's length (31 Jan + 1 month = 28 or 29 Feb)."""
    month0 = moment.month - 1 + int(months)
    year = moment.year + month0 // 12
    month = month0 % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return moment.replace(year=year, month=month,
                          day=min(moment.day, days[month - 1]))


# ------------------------------------------------------------------ meters

def _wallet_spend(cur, since=None):
    """backend/anthropic_wallet._spend: customers (trial_budget_days) plus
    company Anthropic spend (spend_ledger, non-customer lanes); all time, or
    from the UTC day ``since``."""
    if since is None:
        cur.execute("""SELECT COALESCE(SUM(GREATEST(spent_usd-extras_usd,0)+paid_usd),0)
                       AS usd FROM trial_budget_days""")
    else:
        cur.execute("""SELECT COALESCE(SUM(GREATEST(spent_usd-extras_usd,0)+paid_usd),0)
                       AS usd FROM trial_budget_days WHERE day >= %s""", (since,))
    customers = float(_v(cur.fetchone(), "usd", 0) or 0.0)
    cur.execute("SELECT to_regclass('public.spend_ledger') AS t")
    if not _v(cur.fetchone(), "t", 0):
        return customers
    if since is None:
        cur.execute("""SELECT COALESCE(SUM(usd),0) AS usd FROM spend_ledger
                       WHERE provider='anthropic' AND lane<>'customer'""")
    else:
        cur.execute("""SELECT COALESCE(SUM(usd),0) AS usd FROM spend_ledger
                       WHERE provider='anthropic' AND lane<>'customer'
                         AND day >= %s""", (since,))
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


def wallet_now(conn, cfg, now=None, window=None):
    """The wallet with the refill applied: {"estimate_usd", "confirmed_at",
    "basis"}. basis "confirmed" is the owner's balance minus the meter since;
    "assumed_refill" is nonexpiring_usd + refill_usd minus the spend since the
    last refill, used once ``refill_at`` has passed and no balance was
    confirmed after the last refill. A confirmation after it always wins."""
    now = _now(now)
    last, _next, _source = window or refill_window(conn, cfg, now)
    purse = dict(wallet(conn), basis="confirmed")
    confirmed = _parse_time(purse.get("confirmed_at"))
    base = _parse_time(cfg["refill_at"])
    refilled = (last is not None and last >= base and now >= last
                and (confirmed is None or confirmed < last))
    if not refilled:
        return purse
    with conn.cursor() as cur:
        since = _wallet_spend(cur, last.date())
    assumed = float(cfg["nonexpiring_usd"]) + float(cfg["refill_usd"]) - since
    return {"estimate_usd": round(assumed, 6),
            "confirmed_at": purse.get("confirmed_at"),
            "basis": "assumed_refill", "refilled_at": last.isoformat(),
            "spent_since_refill_usd": round(since, 6)}


def customers_spent_today(conn, now=None):
    """Customers' Anthropic spend so far today (UTC), on the wallet's own
    customer meter (trial_budget_days)."""
    day = _now(now).date()
    with conn.cursor() as cur:
        cur.execute("""SELECT COALESCE(SUM(GREATEST(spent_usd-extras_usd,0)+paid_usd),0)
                       AS usd FROM trial_budget_days WHERE day = %s""", (day,))
        return float(_v(cur.fetchone(), "usd", 0) or 0.0)


def company_room(conn, now=None, ccfg=None):
    """What the company lanes (replay + internal) may still spend on
    Anthropic this cycle: each lane's cycle share (spend_ledger.shares, the
    fence's own table) less its spend on the ledger since the cycle started
    (claude_credit.cycle_window, as spend_ledger.room reads it), in one
    query. Before migration 043 the whole cycle share counts."""
    import claude_credit
    import spend_ledger
    table = spend_ledger.shares(conn)
    lanes = list(spend_ledger.COMPANY_LANES)
    spent = {}
    if spend_ledger.ready(conn):
        start, _end = claude_credit.cycle_window(
            ccfg or claude_credit.read_config(conn), _now(now))
        with conn.cursor() as cur:
            cur.execute("""SELECT lane, COALESCE(SUM(usd), 0) AS usd
                             FROM spend_ledger
                            WHERE provider = 'anthropic' AND lane = ANY(%s)
                              AND day >= %s
                            GROUP BY lane""", (lanes, start.date()))
            for row in cur.fetchall():
                spent[_v(row, "lane", 0)] = float(_v(row, "usd", 1) or 0.0)
    out = {}
    for lane in lanes:
        share = table[lane].get("anthropic") or table[lane].get("*") or {}
        cycle = _num(share.get("cycle_usd"), 0.0)
        out[lane] = round(max(0.0, cycle - spent.get(lane, 0.0)), 6)
    return {"usd": round(sum(out.values()), 6), "lanes": out}


def _internal_ids():
    import spend_ledger
    return sorted(int(x) for x in spend_ledger.INTERNAL_USER_IDS)


def inflight(conn, cfg, user_id=None, root=None, job_id=None, now=None):
    """{"usd", "mine_usd", "jobs"}: what upgraded agent jobs queued or running
    (payload.model_budget.upgraded) are still expected to spend: each one's
    expected_usd less what its own calls cost so far. Several requests that
    start together see each other here, before any of them is metered. This
    request's own jobs (``root``, ``job_id``) are left out."""
    now = _now(now)
    since = now - timedelta(hours=float(cfg["inflight_hours"]))
    with conn.cursor() as cur:
        cur.execute("""SELECT j.id AS id, j.user_id AS user_id,
                              COALESCE((j.payload->'model_budget'->>'expected_usd')::float,
                                       0) AS expected,
                              COALESCE((SELECT SUM(COALESCE(
                                  (l.response->>'provider_cost_usd')::float, 0))
                                          FROM llm_calls l WHERE l.job_id = j.id),
                                       0) AS spent
                         FROM video_jobs j
                        WHERE j.type = 'agent_turn'
                          AND j.state IN ('queued', 'running')
                          AND j.payload->'model_budget'->>'upgraded' = 'true'
                          AND j.created_at > %s
                          AND j.id <> %s
                          AND COALESCE((j.payload->>'root_agent_job_id')::int, j.id)
                              <> %s""",
                    (since, int(job_id or 0), int(root or 0)))
        rows = cur.fetchall()
    total = mine = 0.0
    for row in rows:
        left = max(0.0, float(_v(row, "expected", 2) or 0.0)
                   - float(_v(row, "spent", 3) or 0.0))
        total += left
        try:
            if user_id is not None and int(_v(row, "user_id", 1)) == int(user_id):
                mine += left
        except (TypeError, ValueError):
            pass
    return {"usd": round(total, 6), "mine_usd": round(mine, 6), "jobs": len(rows)}


# ------------------------------------------------------------------ prices

_MIX_SQL = """SUM(COALESCE(l.prompt_tokens, 0)) AS prompt,
              SUM(COALESCE((l.response->>'cached_in')::float, 0)) AS cached,
              SUM(COALESCE((l.response->>'cache_write_total_in')::float,
                           (l.response->>'cache_write_in')::float, 0)) AS written,
              SUM(COALESCE(l.completion_tokens, 0)) AS completion,
              SUM(COALESCE((l.response->>'cache_write_1h_in')::float, 0)) AS written_hour"""


def _mix(row, offset=0):
    """A token mix {fresh, cached, written, out} from a _MIX_SQL row."""
    prompt = float(_v(row, "prompt", offset) or 0.0)
    cached = float(_v(row, "cached", offset + 1) or 0.0)
    written = float(_v(row, "written", offset + 2) or 0.0)
    out = float(_v(row, "completion", offset + 3) or 0.0)
    return {"fresh": max(0.0, prompt - cached - written), "cached": cached,
            "written": written, "out": out,
            "written_hour": min(written, float(_v(row, "written_hour", offset + 4) or 0))}


def mix_cost(model, mix):
    """USD of a token mix on ``model``'s card (model_prices, the rates the
    Claude lane meters). A request's mix is the sum of many calls, so the
    short-prompt card applies; the premium models have no other card."""
    import model_prices
    card = (model_prices.MODEL_PRICES.get(str(model or "").lower())
            or model_prices.MODEL_PRICES[SONNET])
    return (mix["fresh"] * card["in"] + mix["cached"] * card["cached_in"]
            + mix["written"] * card.get("cache_write_in", card["in"])
            + mix.get("written_hour", 0) * (2 * card["in"] - card["cache_write_in"])
            + mix["out"] * card["out"]) / 1e6


def _scaled(mix, factor):
    return {k: v * factor for k, v in mix.items()}


def project_mix(conn, cfg, project_id, root=None, now=None):
    """The mean editing-token mix of this project's last ``size_requests``
    requests (any model), this request left out. None without history."""
    if not project_id:
        return None
    now = _now(now)
    with conn.cursor() as cur:
        cur.execute(f"""SELECT {_MIX_SQL}
                          FROM llm_calls l JOIN video_jobs j ON j.id = l.job_id
                         WHERE l.project_id = %s AND l.purpose = %s
                           AND l.created_at > %s AND j.type = 'agent_turn'
                           AND COALESCE((j.payload->>'root_agent_job_id')::int, j.id)
                               <> %s
                         GROUP BY COALESCE((j.payload->>'root_agent_job_id')::int,
                                           j.id)
                         ORDER BY MAX(l.created_at) DESC
                         LIMIT %s""",
                    (int(project_id), EDIT_PURPOSE,
                     now - timedelta(days=float(cfg["learn_days"])),
                     int(root or 0), int(cfg["size_requests"])))
        rows = cur.fetchall()
    mixes = [_mix(r) for r in rows]
    mixes = [m for m in mixes if sum(m.values()) > 0]
    if not mixes:
        return None
    return {k: sum(m[k] for m in mixes) / len(mixes) for k in mixes[0]}


def typical_mix(conn, cfg, now=None):
    """The typical customer request's editing-token mix and source length
    (the last 200 requests in ``learn_days``). None without history."""
    now = _now(now)
    with conn.cursor() as cur:
        cur.execute(f"""SELECT AVG(r.prompt) AS prompt, AVG(r.cached) AS cached,
                               AVG(r.written) AS written,
                               AVG(r.completion) AS completion, AVG(r.written_hour) AS written_hour,
                               AVG(a.dur) AS duration_s, COUNT(*) AS n
                          FROM (SELECT {_MIX_SQL}, MAX(j.project_id) AS pid
                                  FROM llm_calls l JOIN video_jobs j ON j.id = l.job_id
                                 WHERE l.purpose = %s AND l.created_at > %s
                                   AND j.type = 'agent_turn'
                                   AND NOT (j.user_id = ANY(%s))
                                 GROUP BY COALESCE((j.payload->>'root_agent_job_id')::int,
                                                   j.id)
                                 ORDER BY MAX(l.created_at) DESC
                                 LIMIT 200) AS r
                          LEFT JOIN LATERAL (
                                SELECT SUM(duration_s) AS dur FROM assets
                                 WHERE project_id = r.pid AND kind = 'original') AS a
                            ON TRUE""",
                    (EDIT_PURPOSE, now - timedelta(days=float(cfg["learn_days"])),
                     _internal_ids()))
        row = cur.fetchone()
    if not row or not int(_v(row, "n", 6) or 0):
        return None
    mix = _mix(row)
    if sum(mix.values()) <= 0:
        return None
    dur = _v(row, "duration_s", 5)
    return {"mix": mix, "duration_s": float(dur) if dur else None}


def source_seconds(conn, project_id):
    """The project's source length: its original uploads' durations."""
    if not project_id:
        return None
    with conn.cursor() as cur:
        cur.execute("""SELECT SUM(duration_s) AS dur FROM assets
                        WHERE project_id = %s AND kind = 'original'""",
                    (int(project_id),))
        dur = _v(cur.fetchone(), "dur", 0)
    return float(dur) if dur else None


def request_estimate(conn, cfg, project_id=None, root=None, now=None,
                     typical=None):
    """{"basis", "mix"} for a new request: this project's own recent requests
    ("project"), else the typical request scaled by this source's length
    against the typical source ("source_length"; "typical" when either length
    is unknown), else None (the flat learned cost stands)."""
    mix = project_mix(conn, cfg, project_id, root, now)
    if mix:
        return {"basis": "project", "mix": mix}
    if typical is None:
        # an explicit clock is a test or a report: no cache
        typical = (typical_mix(conn, cfg, now) if now is not None else
                   _cached("typical_mix", lambda: typical_mix(conn, cfg),
                           MIX_CACHE_S))
    if not typical:
        return None
    seconds = source_seconds(conn, project_id)
    if seconds and typical.get("duration_s"):
        factor = min(float(cfg["size_scale_max"]),
                     max(float(cfg["size_scale_min"]),
                         seconds / float(typical["duration_s"])))
        return {"basis": "source_length", "mix": _scaled(typical["mix"], factor),
                "scale": round(factor, 3), "source_s": round(seconds, 1)}
    return {"basis": "typical", "mix": dict(typical["mix"])}


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
    """{model: {"usd", "samples"}}: the flat per-request cost, learned. It
    prices a request only when no token history exists to price."""
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


def _costs(models, estimate, flat):
    """{model: usd}: the estimate's token mix on each model's card, else the
    flat learned cost."""
    out = {}
    for model in models:
        if estimate and estimate.get("mix"):
            out[model] = round(mix_cost(model, estimate["mix"]), 6)
        else:
            out[model] = float((flat.get(model) or {}).get("usd", float("inf")))
    return out


# ------------------------------------------------------------------ allowance

def _days_between(start, end):
    return (end - start).total_seconds() / 86400.0


def budget_state(conn, cfg, now=None):
    """Today's allowance and spend (not per account). Cached by callers."""
    now = _now(now)
    today = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
    import claude_credit
    ccfg = claude_credit.read_config(conn)
    window = refill_window(conn, cfg, now, ccfg)
    last, nxt, source = window
    purse = wallet_now(conn, cfg, now, window)
    estimate = purse["estimate_usd"]
    spent = customers_spent_today(conn, now)
    room = company_room(conn, now, ccfg)
    keep = max(float(cfg["reserve_usd"]), float(cfg["nonexpiring_usd"]))
    state = {"wallet_usd": estimate, "wallet_basis": purse.get("basis"),
             "confirmed_at": purse.get("confirmed_at"),
             "spent_today_usd": round(spent, 6),
             "company_room_usd": room["usd"], "company_room": room["lanes"],
             "keep_usd": round(keep, 6),
             "last_refill_at": last.isoformat() if last else None,
             "next_refill_at": nxt.isoformat(), "refill_source": source,
             "refill_usd": cfg["refill_usd"],
             "nonexpiring_usd": cfg["nonexpiring_usd"],
             "allowance_usd": 0.0, "spreadable_usd": 0.0, "days_left": None,
             "line_allowance_usd": None, "problem": None}
    if purse.get("basis") == "assumed_refill":
        state["assumed"] = {
            "wallet_usd": estimate, "refilled_at": purse.get("refilled_at"),
            "spent_since_refill_usd": purse.get("spent_since_refill_usd"),
            "why": "the refill moment passed and no balance was confirmed "
                   "after it: nonexpiring + refill - spend since the refill"}
    if estimate is None:
        state["problem"] = "wallet_unknown"
        return state
    days = max(1.0, _days_between(today, nxt))
    # The wallet as it stood at the start of today: today's own spend is
    # counted once, against the allowance, not twice. What the company lanes
    # may still spend this cycle and what is kept come off before spreading.
    start_of_day = estimate + spent
    spreadable = max(0.0, start_of_day - room["usd"] - keep)
    allowance = spreadable / days
    state["days_left"] = round(days, 3)
    state["spreadable_usd"] = round(spreadable, 6)
    if cfg.get("respect_credit_line"):
        start, end = claude_credit.cycle_window(ccfg, now)
        cycle = claude_credit.cycle_spend(conn, start, end)
        line_room = max(0.0, claude_credit.line_usd(ccfg) - (cycle - spent))
        line_allowance = line_room / max(1.0, _days_between(today, end))
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

def pick(ladder, floor, allowance, spent_today, expected):
    """Choose the first model whose estimated cost fits the unreserved wallet."""
    if floor not in ladder:
        return floor, "plan_model_not_on_ladder"
    for model in ladder[:ladder.index(floor)]:
        if spent_today + expected.get(model, float("inf")) <= allowance:
            return model, "fits"
    return floor, "daily_allowance"


def plan_rungs(cfg, plan, floor):
    if plan in ("free", "mcp_connect"):
        return [floor]
    plans = cfg["plan_ladder"]
    return [rung for rung in plans.get(plan, plans["grandfathered"]) if rung != floor] + [floor]


def _decision(model, plan_model, reason, cfg=None, **numbers):
    # The meter starts at zero. Settlement replaces it on the root job only
    # when credits are debited. Unbilled requests retain their actual zero.
    out = {"model": model, "plan_model": plan_model, "reason": reason, "credits_charged": 0.0,
           "upgraded": model != plan_model,
           "markup": (cfg or DEFAULTS)["markup"] if model != plan_model else 2.0,
           "decided_at": _now().isoformat()}
    out.update({k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in numbers.items()})
    return out


def decide(conn, user_id, plan_model, paying, project_id=None, root=None,
           now=None, plan="grandfathered"):
    """The model for a new request. Never raises; any doubt is the plan's
    model. ``paying``: the account pays (subscribed, not on a trial).
    ``project_id`` sizes the request; ``root`` is its root agent job."""
    try:
        cfg = config(conn) if now is None else read_config(conn)
        if not cfg["on"]:
            return _decision(plan_model, plan_model, "off", cfg)
        if not paying:
            return _decision(plan_model, plan_model, "not_paying", cfg)
        if not governed(user_id, cfg):
            return _decision(plan_model, plan_model, "not_customer", cfg)
        if _model(plan_model) not in cfg["ladder"]:
            return _decision(plan_model, plan_model, "plan_model_not_on_ladder", cfg)
        floor = _model(plan_model)
        state, flat = _state(conn, cfg, now)
        if state["problem"]:
            _log_once(state["problem"], REASONS[state["problem"]] +
                      "; paying requests keep the plan's model")
            return _decision(plan_model, plan_model, state["problem"], cfg,
                             wallet_usd=state["wallet_usd"])
        ladder = plan_rungs(cfg, plan, floor)
        spent = customers_spent_today(conn, now)
        flying = inflight(conn, cfg, user_id, root, root, now)
        estimate = request_estimate(conn, cfg, project_id, root, now)
        costs = _costs(ladder, estimate, flat)
        model, reason = pick(ladder, floor, state["allowance_usd"],
                             spent + flying["usd"], costs)
        return _decision(model if model != floor else plan_model, plan_model,
                         reason, cfg,
                         allowance_usd=float(state["allowance_usd"]),
                         spent_today_usd=float(spent),
                         inflight_usd=float(flying["usd"]),
                         plan=plan,
                         expected_usd=float(costs.get(model, 0.0)),
                         estimate_basis=(estimate or {}).get("basis", "flat"),
                         costs={m: round(float(c), 4) for m, c in costs.items()},
                         wallet_usd=state["wallet_usd"],
                         wallet_basis=state.get("wallet_basis"),
                         days_left=state["days_left"])
    except Exception as exc:  # noqa: BLE001 - the plan's model on any doubt
        _log_once("error:" + type(exc).__name__,
                  f"decision failed ({type(exc).__name__}: {str(exc)[:160]}); "
                  "the plan's model")
        return _decision(plan_model, plan_model, "error")


def kept(previous, root_id, plan_model, continuation_state=None):
    """A continuation of the same request starts from the model it ran on.
    None when ``previous`` is not this request's decision. A retried slice
    rechecks the same model against the current company allowance."""
    if not isinstance(previous, dict) or not previous.get("model"):
        return None
    try:
        if int(previous.get("root") or 0) != int(root_id or 0):
            return None
    except (TypeError, ValueError):
        return None
    out = dict(previous)
    out["reason"] = "continuation"
    return out


def request_progress(conn, root, project_id=None, premium=(), now=None):
    """{"mix", "slices", "premium_usd"}: what this request's own jobs have
    used so far: the editing-token mix, the slices that made editing calls,
    and the real cost of its calls on the premium models."""
    now = _now(now)
    since = now - timedelta(days=2)
    with conn.cursor() as cur:
        cur.execute(f"""SELECT {_MIX_SQL},
                               COUNT(DISTINCT l.job_id) AS slices
                          FROM llm_calls l JOIN video_jobs j ON j.id = l.job_id
                         WHERE l.purpose = %s AND l.created_at > %s
                           AND (%s = 0 OR l.project_id = %s)
                           AND COALESCE((j.payload->>'root_agent_job_id')::int, j.id)
                               = %s""",
                    (EDIT_PURPOSE, since, int(project_id or 0),
                     int(project_id or 0), int(root)))
        row = cur.fetchone()
        mix = _mix(row) if row else None
        slices = int(_v(row, "slices", 4) or 0) if row else 0
        cur.execute("""SELECT COALESCE(SUM(COALESCE(
                                  (l.response->>'provider_cost_usd')::float, 0)), 0)
                              AS usd
                         FROM llm_calls l JOIN video_jobs j ON j.id = l.job_id
                        WHERE l.created_at > %s AND l.model = ANY(%s)
                          AND (%s = 0 OR l.project_id = %s)
                          AND COALESCE((j.payload->>'root_agent_job_id')::int, j.id)
                              = %s""",
                    (since, list(premium) or [""], int(project_id or 0),
                     int(project_id or 0), int(root)))
        premium_usd = float(_v(cur.fetchone(), "usd", 0) or 0.0)
    return {"mix": mix if slices else None, "slices": slices,
            "premium_usd": round(premium_usd, 6)}


def reconsider(conn, user_id, plan_model, previous, root, project_id=None,
               continuation_state=None, now=None):
    """A continuation slice's model. None when ``previous`` is not this
    request's decision (a new request: ``decide``). An upgraded request
    decides again at every slice boundary from its own spend: its next slice
    (what its slices so far used on average, priced on each rung) must still
    fit today's allowance, counting what is in
    flight; when it no longer does, the request continues on the next cheaper
    rung, down to the plan's model. Never moves up; never raises."""
    out = kept(previous, root, plan_model, continuation_state)
    if out is None or out["model"] == plan_model or not out.get("upgraded"):
        return out
    current = out["model"]

    def step(model, reason, **numbers):
        out.update(numbers)
        if model != current:
            out["stepped_from"] = current
        out.update(model=model if model != _model(plan_model) else plan_model,
                   upgraded=model != _model(plan_model) and model != plan_model,
                   reason=reason, decided_at=_now().isoformat())
        return out

    try:
        cfg = config(conn) if now is None else read_config(conn)
        if not cfg["on"]:
            return step(plan_model, "off")
        floor = _model(plan_model)
        ladder = plan_rungs(cfg, out.get("plan", "grandfathered"), floor)
        if floor not in ladder or current not in ladder or \
                ladder.index(current) >= ladder.index(floor):
            return step(plan_model, "plan_model_not_on_ladder")
        state = budget_state(conn, cfg, now)     # fresh at every boundary
        if state["problem"]:
            return step(plan_model, state["problem"])
        premium = ladder[:ladder.index(floor)]
        progress = request_progress(conn, root, project_id, premium, now)
        if progress["mix"]:
            per_slice = {"basis": "request",
                         "mix": _scaled(progress["mix"],
                                        1.0 / max(1, progress["slices"]))}
        else:
            per_slice = request_estimate(conn, cfg, project_id, root, now)
        costs = _costs(ladder, per_slice,
                       expected_costs(conn, cfg, now) if not per_slice else {})
        flying = inflight(conn, cfg, user_id, root, root, now)
        rungs = ladder[ladder.index(current):]
        model, reason = pick(rungs, floor, state["allowance_usd"],
                             state["spent_today_usd"] + flying["usd"],
                             costs)
        return step(model, "continuation" if model == current else "stepped_down",
                    allowance_usd=float(state["allowance_usd"]),
                    spent_today_usd=float(state["spent_today_usd"]),
                    inflight_usd=float(flying["usd"]),
                    markup=cfg["markup"] if model != floor else 2.0,
                    request_premium_usd=float(progress["premium_usd"]),
                    slices=int(progress["slices"]),
                    expected_usd=float(costs.get(model, 0.0)),
                    estimate_basis=(per_slice or {}).get("basis", "flat"),
                    wallet_usd=state["wallet_usd"],
                    days_left=state["days_left"])
    except Exception as exc:  # noqa: BLE001 - the plan's model on any doubt
        _log_once("reconsider:" + type(exc).__name__,
                  f"slice decision failed ({type(exc).__name__}: "
                  f"{str(exc)[:160]}); the plan's model")
        return step(plan_model, "error")


def summary(conn, now=None):
    """The admin card's line: the switch, the wallet (confirmed or assumed
    after a refill), the next refill, what the company lanes may still
    spend, today's allowance and spend, and the model a typical paying
    request with no premium spend today would get now."""
    cfg = read_config(conn)
    out = {"on": cfg["on"], "ladder": cfg["ladder"],
           "reserve_usd": cfg["reserve_usd"], "refill_at": cfg["refill_at"],
           "refill_usd": cfg["refill_usd"],
           "nonexpiring_usd": cfg["nonexpiring_usd"],
           "cycle_days": cfg["cycle_days"],
           "plan_ladder": cfg["plan_ladder"], "markup": cfg["markup"]}
    if not cfg["on"]:
        return out
    now = _now(now)
    state = budget_state(conn, cfg, now)
    expected = expected_costs(conn, cfg, now)
    out.update(state)
    out["expected_usd"] = expected
    floor = cfg["ladder"][-1]
    if state["problem"]:
        out["next_model"] = floor
        out["reason"] = state["problem"]
    else:
        flying = inflight(conn, cfg, now=now)
        typical = typical_mix(conn, cfg, now)
        estimate = {"basis": "typical", "mix": typical["mix"]} if typical else None
        costs = _costs(cfg["ladder"], estimate, expected)
        out["inflight_usd"] = flying["usd"]
        out["typical_request_usd"] = {m: round(c, 4) for m, c in costs.items()}
        out["next_model"], out["reason"] = pick(
            plan_rungs(cfg, "grandfathered", floor), floor, state["allowance_usd"],
            state["spent_today_usd"] + flying["usd"], costs)
    out["reason_text"] = REASONS.get(out["reason"], out["reason"])
    return out
