"""Which work runs on the owner's render box (box/admission.py).

The box is a provider like Cloudflare (``execution_provider = "box"``), with
one difference the owner made a rule: it has NO fallback. When it is busy,
updating or briefly down, the job waits (in its claim, then in the durable
Postgres queue) and asks again; it is never failed for capacity and never
sent to Cloudflare.

Routing is the app_kv row ``box_routing`` (JSON), read every few seconds and
OFF when absent::

    {"enabled": true,
     "url": "https://13-140-241-231.sslip.io",
     "stages": {"owner": true, "mcp": false, "trial": false,
                "free": false, "paying": false},
     "owner_user_ids": [60],
     "skew": "hold"}

Stages are the cutover order: the owner's factory (user 60), every MCP tool
call, then trial and free users, then paying customers. A job's stage is
``owner`` for an owner id, else ``mcp`` for an mcp_tool call, else its user's
billing class. The stamp is immutable: once a claim chose the box, that job
stays on the box (turning a stage off moves only jobs not yet stamped).

Inside the box's own slot containers ``BOX_ROUTE_ALL=1`` sends every
synchronous child (frames, captures, ...) back to the box's admission, the
way Cloudflare containers route their children through the Worker.

``skew`` "hold" (default, the owner's rule): a job runs only on box code
whose fingerprint equals the dispatcher's; until the box reports it, the job
waits. "guard" lets an older box release run what its skew guard accepts.
"""

import json
import os
import threading
import time

KEY = "box_routing"
DEFAULT_URL = "https://13-140-241-231.sslip.io"
STAGES = ("owner", "mcp", "trial", "free", "paying")
# Queue job types the box serves (its five lanes).
BOX_QUEUE_TYPES = frozenset({
    "preview", "preview_check", "final", "index", "filmstrip",
    "agent_turn", "mcp_tool", "shorts_plan"})
BOX_SYNCHRONOUS_TYPES = frozenset({
    "capture", "frames", "track", "matte", "smatch", "clean", "stems",
    "fetch", "search", "stock_acquire", "ytprobe", "mcp_media", "faces",
    "sidecar"})
_ROUTING_CACHE_S = float(os.getenv("BOX_ROUTING_CACHE_S", "15"))
_USER_CACHE_S = 300.0

_lock = threading.Lock()
_routing = {"at": -1e9, "value": {}}
_users = {}


def route_all():
    """True inside the box's own slot containers."""
    return os.getenv("BOX_ROUTE_ALL") == "1"


def _read_row():
    import db as dbx
    probe = dbx.Db()
    try:
        raw = probe.run(dbx.kv_get, KEY)
    finally:
        probe.reset()
    if raw is None:
        return {}
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


def routing():
    """The current routing document ({} = off). A failed read keeps the last
    known document: a database blip must not move jobs between providers."""
    now = time.monotonic()
    with _lock:
        if now - _routing["at"] < _ROUTING_CACHE_S:
            return _routing["value"]
    try:
        value = _read_row()
    except Exception as exc:
        print(f"[box] routing read failed ({type(exc).__name__}); keeping "
              "the last known routing", flush=True)
        with _lock:
            _routing["at"] = now - _ROUTING_CACHE_S + 5.0
            return _routing["value"]
    with _lock:
        _routing.update({"at": now, "value": value})
    return value


def set_routing_for_tests(value):
    with _lock:
        _routing.update({"at": time.monotonic() + 1e9, "value": value or {}})
        _users.clear()


def url():
    explicit = os.getenv("BOX_EXECUTOR_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    if route_all():
        return ""
    return str(routing().get("url") or DEFAULT_URL).strip().rstrip("/")


def enabled():
    if route_all():
        return bool(url())
    r = routing()
    return bool(r.get("enabled")) and bool(url()) and any(
        bool((r.get("stages") or {}).get(s)) for s in STAGES)


def skew_mode():
    mode = str(routing().get("skew") or os.getenv("BOX_SKEW") or "hold")
    return mode if mode in {"hold", "guard"} else "hold"


def owner_ids():
    env = os.getenv("BOX_OWNER_USER_IDS", "").strip()
    if env:
        return {int(x) for x in env.split(",") if x.strip().isdigit()}
    ids = routing().get("owner_user_ids")
    if isinstance(ids, list) and ids:
        return {int(x) for x in ids if str(x).isdigit()}
    return {60}


def _user_row(user_id):
    import db as dbx
    probe = dbx.Db()
    try:
        def read(conn):
            with conn.cursor() as cur:
                cur.execute("""SELECT is_subscribed, plan, trial_status,
                                      trial_ends_at > NOW() AS trial_live
                               FROM users WHERE id = %s""", (user_id,))
                return cur.fetchone()
        return probe.run(read)
    finally:
        probe.reset()


def user_class(user_id):
    """owner | paying | trial | free (unknown and anonymous are free)."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return "free"
    if user_id in owner_ids():
        return "owner"
    now = time.monotonic()
    with _lock:
        hit = _users.get(user_id)
        if hit and now - hit[0] < _USER_CACHE_S:
            return hit[1]
    try:
        row = _user_row(user_id) or {}
    except Exception:
        # Unknown is treated as paying for PRIORITY (never demote a customer
        # on a database blip); routing uses the same answer consistently.
        return "paying"
    status = str(row.get("trial_status") or "").lower()
    plan = str(row.get("plan") or "free").strip().lower()
    if status in {"trialing", "active_trial", "trial"} or (
            row.get("trial_live") and status not in {"canceled",
                                                     "payment_failed"}):
        klass = "trial"
    elif row.get("is_subscribed") or plan not in ("", "free"):
        klass = "paying"
    else:
        klass = "free"
    with _lock:
        _users[user_id] = (now, klass)
    return klass


def stage_for(job):
    klass = user_class(job.get("user_id"))
    if klass == "owner":
        return "owner"
    if job.get("type") == "mcp_tool":
        return "mcp"
    return klass


def selected(job):
    """Should this job run on the box? (the provider selector)"""
    job_type = str(job.get("type") or "")
    if job_type not in BOX_QUEUE_TYPES and \
            job_type not in BOX_SYNCHRONOUS_TYPES:
        return False
    if route_all():
        return bool(url())
    if not enabled():
        return False
    return bool((routing().get("stages") or {}).get(stage_for(job)))


def priority_class(job):
    """Admission's class: customer (paying), trial (trial and free), owner."""
    klass = user_class(job.get("user_id"))
    if klass == "owner":
        # Load tests run on the owner's internal account only; a test job
        # may play a customer's or trial user's class there (never the
        # reverse: no real customer can be demoted this way).
        wanted = str((job.get("payload") or {}).get(
            "box_priority_class") or "")
        return wanted if wanted in {"customer", "trial"} else "owner"
    return {"paying": "customer"}.get(klass, "trial")
