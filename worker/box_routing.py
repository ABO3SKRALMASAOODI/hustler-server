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

Two (or more) boxes: an optional ``boxes`` list replaces ``url``::

    "boxes": [{"id": "h1", "url": "https://13-140-241-231.sslip.io",
               "classes": ["customer", "trial"]},
              {"id": "h2", "url": "https://<box 2>.sslip.io",
               "classes": ["owner"]}],
    "overflow_wait_s": 30

Each box has a stable id (``h1`` .. ``h99``; the first box is ``h1``) that
its call ids carry (``bx-h2-...``; ``h1`` calls keep today's untagged
``bx-...`` ids), so a reconnect, completion or abandonment after any restart
reaches the box that holds the call. ``classes`` are the admission classes a
box is HOME for; ``accepts`` (default: every class) are the classes it also
takes as overflow. A job asks its home box first (admission's full wait), and
when that box refuses with proof that nothing ran it asks the next box at
once under that box's own identity (the overflow wait), round after round,
until its in-claim window ends and it goes back to the queue unspent. Every
box keeps its own priority queue. Children never move: inside a slot
``BOX_ROUTE_ALL`` sends them to that box's own admission. Without ``boxes``
there is one box, ``h1``, and everything is exactly the single-box path.

A box is removed in two steps: first ``"classes": [], "accepts": []`` (no
new work; its calls stay reachable), then, once it is idle, the entry.
Never give a listed id another host.
"""

import json
import os
import re
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


PRIMARY = "h1"
CLASSES = ("customer", "trial", "owner")
_BOX_ID = re.compile(r"^h[1-9][0-9]?$")
_CALL_TAG = re.compile(r"^bx-(h[1-9][0-9]?)-")
OVERFLOW_WAIT_S = 30.0


def url():
    """The primary box's address (``h1``; the only box without ``boxes``)."""
    explicit = os.getenv("BOX_EXECUTOR_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    if route_all():
        return ""
    listed = _listed_boxes()
    for box in listed:
        if box["id"] == PRIMARY:
            return box["url"]
    if listed:
        return listed[0]["url"]
    return str(routing().get("url") or DEFAULT_URL).strip().rstrip("/")


def _class_set(value, default):
    if not isinstance(value, list):
        return frozenset(default)
    return frozenset(str(c) for c in value if str(c) in CLASSES)


def _listed_boxes():
    """The routing document's ``boxes`` entries, validated, in its order."""
    raw = routing().get("boxes")
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        box_id = str(entry.get("id") or (PRIMARY if index == 0 else "")
                     ).strip().lower()
        address = str(entry.get("url") or "").strip().rstrip("/")
        if not _BOX_ID.match(box_id) or not address or box_id in seen:
            continue
        seen.add(box_id)
        classes = _class_set(entry.get("classes"), CLASSES)
        accepts = _class_set(entry.get("accepts"), CLASSES) | classes
        out.append({"id": box_id, "url": address, "classes": classes,
                    "accepts": accepts})
    return out


def boxes():
    """Every box the dispatcher may use: [{id, url, classes, accepts}].

    Without a ``boxes`` list this is the single box ``h1`` at url(), home of
    every class: today's routing exactly. Inside a slot it is that box's own
    admission only."""
    if not route_all():
        listed = _listed_boxes()
        if listed:
            return listed
    primary = url()
    return [{"id": PRIMARY, "url": primary, "classes": frozenset(CLASSES),
             "accepts": frozenset(CLASSES)}] if primary else []


def multi_box():
    return len(boxes()) > 1


def box_url(box_id):
    """The address of box ``box_id`` ("" when it is not configured).

    Inside a slot every call goes to that box's own admission."""
    if route_all():
        return url()
    box_id = str(box_id or PRIMARY)
    for box in _listed_boxes():
        if box["id"] == box_id:
            return box["url"]
    return url() if box_id == PRIMARY else ""


def call_box(call_id):
    """The box a ``bx-`` call id lives on: its tag, else the primary."""
    m = _CALL_TAG.match(str(call_id or ""))
    return m.group(1) if m else PRIMARY


def call_tag(box_id):
    """The call-id tag of a box: none for the primary (today's ids)."""
    box_id = str(box_id or PRIMARY)
    return "" if box_id == PRIMARY or not _BOX_ID.match(box_id) \
        else f"{box_id}-"


def box_id_for_url(address):
    """The configured id of the box at ``address`` (None if unlisted)."""
    address = str(address or "").strip().rstrip("/")
    for box in boxes():
        if box["url"] == address:
            return box["id"]
    return None


def overflow_wait_s():
    try:
        value = float(routing().get("overflow_wait_s", OVERFLOW_WAIT_S))
    except (TypeError, ValueError):
        value = OVERFLOW_WAIT_S
    return max(5.0, value)


def candidates(job):
    """[(box id, home)] in the order this job asks: its home boxes first,
    then the boxes that take its class as overflow. One box: [("h1", True)].

    An owner-account job may pin itself to one box (``payload.box_pin``,
    the add-box parity smoke); no other job can."""
    listed = boxes()
    if not listed:
        return [(PRIMARY, True)]
    if route_all() or len(listed) == 1:
        return [(listed[0]["id"], True)]
    if user_class(job.get("user_id")) == "owner":
        pin = str((job.get("payload") or {}).get("box_pin") or "")
        if pin and any(b["id"] == pin for b in listed):
            return [(pin, True)]
    klass = priority_class(job)
    home = [(b["id"], True) for b in listed if klass in b["classes"]]
    over = [(b["id"], False) for b in listed
            if klass not in b["classes"] and klass in b["accepts"]]
    # A class no box takes is a configuration slip: never strand the job.
    return (home + over) or [(listed[0]["id"], True)]


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
