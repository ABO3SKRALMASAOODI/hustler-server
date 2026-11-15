"""Hard internal OpenAI admission. Mirrored in backend and the workflow shell.

Customers return before configuration/DB access. All internal processes use ONE
central app_kv state (never a replay's copied DB), serialized by a transaction
advisory lock. Reserve a conservative request ceiling before transport; settle
from usage. Ambiguous failures/crashes keep their reservation: no free retry.
UTC daily limits reset; run limits do not. No prompts or keys are stored here.
"""
import contextlib
import contextvars
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

CONFIG = "spend_fence"
STATE = "openai_internal_budget"
DEFAULTS = dict(internal_day_usd=30.0, replay_run_usd=3.0,
                workflow_run_usd=8.0, test_run_usd=3.0, enabled=True,
                exhausted_until="2026-11-30T00:00:00+00:00", credit_available=False)
LOCAL_CONFIG = Path.home() / ".config/valmera/openai-spend.json"
ENV_FILE = Path("/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env")
_scope = contextvars.ContextVar("openai_internal_scope", default=None)
_stopped = {}
_resolved_scope = contextvars.ContextVar("openai_resolved_scope", default=None)


class OverBudget(RuntimeError):
    """Internal lane stopped BEFORE transport. Never a provider/fallback error."""


def propagate(exc):
    if isinstance(exc, OverBudget):
        raise exc


@contextlib.contextmanager
def scope(lane, run):
    token = _scope.set((lane, str(run)))
    try:
        yield
    finally:
        _scope.reset(token)


def identity(lane=None, run=None):
    inherited = _resolved_scope.get()
    if inherited is not None:
        return inherited
    bound = _scope.get()
    if bound:
        if bound[0] == "customer":
            return "customer", None
        lane, scoped_run = bound
        run = run or os.environ.get("OPENAI_INTERNAL_RUN") or os.environ.get("VALMERA_RUN") or os.environ.get("GAUNTLET_RUN") or scoped_run
    if os.environ.get("VALMERA_SKILL"):
        lane = "workflow"
    lane = lane or os.environ.get("SPEND_LANE")
    if not lane:
        llm = sys.modules.get("llm")
        if llm and hasattr(llm, "spend_lane"):
            lane = llm.spend_lane()
    lane = lane or "internal"  # standalone lane probes are company work
    if lane == "customer":
        return lane, None
    run = run or os.environ.get("OPENAI_INTERNAL_RUN") or os.environ.get("VALMERA_RUN") or os.environ.get("GAUNTLET_RUN")
    if not run and lane == "replay" and os.environ.get("WORKER_TMP_DIR"):
        run = str(Path(os.environ["WORKER_TMP_DIR"]).resolve().parent)
    if not run:
        llm = sys.modules.get("llm")
        rec = llm.get_recorder() if llm and hasattr(llm, "get_recorder") else None
        job = getattr(rec, "job_id", None)
        if job is not None:
            run = "job:" + str(job)
    if not run:
        raise OverBudget("OpenAI internal run identity missing; set OPENAI_INTERNAL_RUN (stable across retries).")
    return lane, hashlib.sha256((lane + ":" + str(run)).encode()).hexdigest()


class ThreadPoolExecutor(_ThreadPoolExecutor):
    """Propagate the owning spend identity, not a DB connection, into helpers."""
    def submit(self, fn, /, *args, **kwargs):
        owner = identity()
        def invoke():
            token = _resolved_scope.set(owner)
            try:
                return fn(*args, **kwargs)
            finally:
                _resolved_scope.reset(token)
        return super().submit(invoke)


def local_config():
    path = Path(os.environ.get("OPENAI_INTERNAL_CONFIG", str(LOCAL_CONFIG)))
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError()
        return data.get("openai", data)
    except (OSError, ValueError, TypeError) as exc:
        raise OverBudget("OpenAI internal budget configuration is unreadable; no call sent.") from exc


def connect():
    """Central authority, not DATABASE_URL from an isolated replay stack."""
    import psycopg2
    url = os.environ.get("OPENAI_BUDGET_DATABASE_URL")
    if not url and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip("\"'")
                break
    if not url and os.environ.get("SPEND_LANE") != "replay":
        url = os.environ.get("DATABASE_URL")
    if not url:
        raise OverBudget("OpenAI internal central budget database is unavailable; no call sent.")
    return psycopg2.connect(url, connect_timeout=8)


def _load(cur, key):
    cur.execute("SELECT value FROM app_kv WHERE key=%s", (key,))
    row = cur.fetchone()
    raw = (row.get("value") if isinstance(row, dict) else row[0]) if row else "{}"
    return json.loads(raw) if isinstance(raw, str) else raw


def _save(cur, state):
    cur.execute("INSERT INTO app_kv(key,value) VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()",
                (STATE, json.dumps(state, separators=(",", ":"))))


def _money(value):
    f = float(value)
    if not math.isfinite(f) or f < 0:
        raise OverBudget("Invalid OpenAI internal budget amount; no call sent.")
    return math.ceil(f * 1_000_000)  # integer microdollars, round up


def limits(remote, local):
    cfg = {**DEFAULTS, **remote}
    # Local configuration may tighten, never expand the central allowance.
    for name in ("internal_day_usd", "replay_run_usd", "workflow_run_usd", "test_run_usd"):
        cfg[name] = min(_money(cfg[name]), _money(local.get(name, cfg[name])))
    cfg["enabled"] = cfg.get("enabled") is True and local.get("enabled", True) is True
    return cfg


def reserve_state(state, cfg, lane, run, amount, day, baseline=0):
    """Pure admission transition; caller holds the central transaction lock."""
    if not cfg["enabled"]:
        raise OverBudget("OpenAI internal kill switch is OFF; lane stopped before call.")
    days, runs, pending = (state.setdefault(k, {}) for k in ("days", "runs", "pending"))
    days.setdefault(day, baseline)
    daily = days[day] + sum(p["amount"] for p in pending.values() if p["day"] == day)
    used = runs.get(run, 0) + sum(p["amount"] for p in pending.values() if p["run"] == run)
    run_cap = cfg["workflow_run_usd" if lane == "workflow" else "replay_run_usd" if lane == "replay" else "test_run_usd"]
    if amount <= 0:
        raise OverBudget("OpenAI internal reservation must be positive.")
    if daily >= cfg["internal_day_usd"] or used >= run_cap or daily + amount > cfg["internal_day_usd"] or used + amount > run_cap:
        raise OverBudget(f"OpenAI internal cap: today ${daily/1e6:.4f}/${cfg['internal_day_usd']/1e6:.2f}; "
                         f"run ${used/1e6:.4f}/${run_cap/1e6:.2f}; next-call ceiling ${amount/1e6:.4f}. No call sent.")
    token = uuid.uuid4().hex
    pending[token] = dict(day=day, run=run, amount=amount)
    return token


def settle_state(state, token, actual):
    p = state["pending"].pop(token, None)
    if p is None:
        return  # idempotent settlement
    state["days"][p["day"]] = state["days"].get(p["day"], 0) + actual
    state["runs"][p["run"]] = state["runs"].get(p["run"], 0) + actual
    if actual > p["amount"]:
        # Never pretend the bound held. Stop the entire internal pool until investigated.
        state["bound_breach"] = True


def transaction(action):
    conn = None
    try:
        conn = connect()
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='10000ms'")
            cur.execute("SELECT pg_advisory_xact_lock(74821115)")
            return action(cur)
    except OverBudget:
        raise
    except Exception as exc:
        # Driver errors can contain credentials. Never include their text.
        raise OverBudget(f"OpenAI internal budget unavailable ({type(exc).__name__}); no further call allowed.") from None
    finally:
        if conn is not None:
            conn.close()


class Lease:
    def __init__(self, token=None, model=None):
        self.token, self.model = token, model

    def settle(self, usage=None, *, usd=None, payload=None):
        if self.token is None:
            return
        import openai_cache
        if usd is None:
            if usage is None:
                return  # unknown billing stays reserved
            if payload and payload.get("service_tier"):
                if not isinstance(usage, dict) and hasattr(usage, "model_dump"):
                    usage = usage.model_dump()
                if isinstance(usage, dict):
                    usage = dict(usage, service_tier=payload["service_tier"])
            usd = openai_cache.cost(self.model, usage)
            if payload:
                usd += .01 * sum(i.get("type") == "web_search_call" for i in payload.get("output", []))
        amount = _money(usd)
        token = self.token
        def finish(cur):
            state = _load(cur, STATE)
            settle_state(state, token, amount)
            _save(cur, state)
            return state.get("bound_breach")
        breached = transaction(finish)
        self.token = None
        if breached:
            raise OverBudget("OpenAI internal request exceeded its reserved ceiling; pool stopped for reconciliation.")


def upper_bound(body):
    """Conservative byte/token and image ceilings; unknown paid tools fail closed.

    Built-in web search can repeatedly fill the whole context: reserve that
    ceiling, not a guessed average. Oversized/unbounded requests must be split
    or use function tools whose next model request is admitted separately.
    """
    import openai_cache
    model = body.get("model", "")
    if model not in openai_cache.PRICES:
        raise OverBudget(f"OpenAI internal model {model!r} has no audited price ceiling.")
    pin, _, pout = openai_cache.PRICES[model]
    cap = body.get("max_output_tokens") or body.get("max_completion_tokens") or body.get("max_tokens")
    if not isinstance(cap, int) or cap <= 0:
        raise OverBudget("OpenAI internal request requires an explicit positive output token cap.")
    def size(v):
        if isinstance(v, dict):
            if v.get("type") in ("input_image", "image_url"):
                return 32768
            if v.get("type") in ("input_audio", "input_file"):
                raise OverBudget("OpenAI internal audio/file request needs a separately bounded media adapter.")
            return 32 + sum(len(str(k).encode()) + size(x) for k, x in v.items())
        if isinstance(v, list):
            return sum(size(x) for x in v)
        return len(str(v).encode()) + 8
    tin = size(body) + 1024
    if body.get("previous_response_id") or body.get("conversation"):
        raise OverBudget("OpenAI internal request must carry its complete context for admission.")
    hosted = [] if body.get("tool_choice") == "none" else [t for t in body.get("tools", []) if t.get("type") not in ("function", "custom")]
    extra = 0
    if hosted:
        n = body.get("max_tool_calls")
        if not isinstance(n, int) or n < 1 or any(t.get("type") != "web_search" for t in hosted):
            raise OverBudget("OpenAI internal hosted tools require bounded web_search max_tool_calls; no call sent.")
        tin += 1_050_000 * n
        extra = .01 * n
    # Every input token may be a long cache write; reserve 2.5x, and the
    # long-context/priority surcharges. Savings release only after usage.
    tier_name = body.get("service_tier") or (body.get("extra_body") or {}).get("service_tier")
    tier = 2 if tier_name in ("priority", "fast") else 1
    long = 2 if tin > 272000 and model in ("gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol") else 1
    return tier * (tin * pin * 2.5 * long + cap * pout * (1.5 if long == 2 else 1)) / 1e6 + extra


def legacy_workflow_total(day):
    """Import pre-guard journal costs once per UTC day, before a local call.

    New guarded calls settle centrally; never rescan them as extra charges.
    Unknown legacy billing closes the day rather than treating it as zero.
    """
    import openai_cache
    path = Path(os.environ.get("VALMERA_OPENAI_SPEND_LOG", openai_cache.DEFAULT_LOG))
    if not path.exists():
        return None
    total = 0
    try:
        with path.open() as f:
            for line in f:
                row = json.loads(line)
                if str(row.get("ts", ""))[:10] != day:
                    continue
                if row.get("usd") is None:
                    if row.get("billing_unconfirmed"):
                        raise OverBudget("OpenAI workflow journal has unresolved billing today; reconcile before calling.")
                    continue
                total += _money(row["usd"])
    except (OSError, ValueError, TypeError) as exc:
        raise OverBudget("OpenAI workflow journal unreadable; no call sent.") from None
    return total


def _reserve(body, purpose, *, lane=None, run=None, ceiling_usd=None, run_cap_usd=None):
    lane, run = identity(lane, run)
    if lane == "customer":
        return Lease()
    local = local_config()
    ceiling = _money(upper_bound(body) if ceiling_usd is None else ceiling_usd)
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    def begin(cur):
        cfg = limits((_load(cur, CONFIG) or {}).get("openai", {}), local)
        if run_cap_usd is not None:
            key = "workflow_run_usd" if lane == "workflow" else "replay_run_usd" if lane == "replay" else "test_run_usd"
            cfg[key] = min(cfg[key], _money(run_cap_usd))
        state = _load(cur, STATE) or {}
        if state.get("bound_breach"):
            raise OverBudget("OpenAI internal pool stopped after a reservation breach; reconcile before reopening.")
        baseline = 0
        if day not in state.get("days", {}):
            cur.execute("SELECT COALESCE(SUM(usd),0) FROM spend_ledger WHERE provider='openai' AND lane IN ('internal','replay') AND day=%s", (day,))
            baseline = _money(cur.fetchone()[0])
        # A local skills journal may predate the new central admission ledger.
        # Import once under this same lock; duplicates are conservative, never free.
        legacy = state.setdefault("legacy_workflow_days", [])
        if day not in legacy:
            previous = legacy_workflow_total(day)
            if previous is not None:
                state.setdefault("days", {}).setdefault(day, baseline)
                state["days"][day] += previous
                legacy.append(day)
        token = reserve_state(state, cfg, lane, run, ceiling, day, baseline)
        _save(cur, state)
        return token
    return Lease(transaction(begin), body.get("model"))


def raise_if_stopped(lane=None, run=None):
    try:
        lane, ident = identity(lane, run)
    except OverBudget:
        return  # the actual admission still requires a run identity
    if lane != "customer" and ident in _stopped:
        raise OverBudget(_stopped[ident])


def reserve(body, purpose, **kw):
    raise_if_stopped(kw.get("lane"), kw.get("run"))
    try:
        return _reserve(body, purpose, **kw)
    except OverBudget as exc:
        try:
            lane, ident = identity(kw.get("lane"), kw.get("run"))
            if lane != "customer":
                _stopped[ident] = str(exc)
        except OverBudget:
            pass
        raise


@contextlib.contextmanager
def call(body, purpose, *, endpoint=None, **kw):
    lease = Lease() if endpoint is not None and "api.openai.com" not in str(endpoint) else reserve(body, purpose, **kw)
    try:
        yield lease
    except BaseException as exc:
        # Only a definite pre-generation HTTP rejection releases the money.
        code = getattr(exc, "status_code", None) or getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        if code in (400, 401, 403, 404, 422, 429):
            lease.settle(usd=0)
        raise


def post(send, url, *, json, purpose="internal", lane=None, run=None, **kw):
    if "api.openai.com" not in str(url):
        return send(url, json=json, **kw)
    with call(json, purpose, lane=lane, run=run) as lease:
        response = send(url, json=json, **kw)
        status = getattr(response, "status_code", 200)
        if status in (400, 401, 403, 404, 422, 429):
            lease.settle(usd=0)
        elif status < 400:
            payload = response.json()
            lease.settle(payload.get("usage"), payload=payload)
        return response


def completion(client, *, purpose="editor", lane=None, run=None, **body):
    if "api.openai.com" not in str(getattr(client, "base_url", "")):
        return client.chat.completions.create(**body)
    with call(body, purpose, lane=lane, run=run) as lease:
        # SDK retries are separate paid attempts; the caller owns admission.
        if lease.token is not None:
            client = client.with_options(max_retries=0)
        response = client.chat.completions.create(**body)
        lease.settle(getattr(response, "usage", None), payload={"service_tier": getattr(response, "service_tier", None)})
        return response
