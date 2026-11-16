"""Which editing harness a request runs on (harness v2, 16 Nov 2026).

THIS FILE EXISTS TWICE, byte for byte: backend/video_services/harness_route.py
and worker/harness_route.py (the web service and the executors are separate
images). backend/tests/test_harness_route.py and
worker/tests/test_harness_route_enqueue.py fail when they differ.

Each request carries its own stamp on its job payload, decided once when the
job is queued, by ``stamp`` below: the backend's ``jobs.enqueue`` (a chat
message, an MCP call) and the worker's ``db.enqueue_job`` (a request typed
while the upload was analysed and resumed when the index ends, a request a
lifted gate runs, a short's instruction) apply the same rule, so a request
gets the same harness whichever process queues it. The switch is the
``app_kv`` row ``harness_v2``::

    {"mode": "off" | "list" | "on", "user_ids": [], "project_ids": [],
     "surfaces": ["studio", "mcp"], "override": false}

- A missing row, an unreadable row or ``mode="off"`` means v1 for everyone:
  nothing is stamped. There is no row until the owner adds one.
- ``list``: only the listed users and projects are stamped.
- ``on``: everyone on the listed surfaces is stamped.
- A request may carry ``harness: "v2" | "v1"`` (the chat POST body or MCP
  ``tools/call`` meta). It is honoured only when ``override`` is true and the
  account is internal (``spend_ledger.INTERNAL_USER_IDS``), so the owner can
  send one request through v2 without switching any customer.

The stamp is applied with ``setdefault``: a payload that already carries a
stamp (a continuation copies its payload) keeps it, so a request never
changes harness mid-turn, and ``mode="off"`` only affects new requests.
"""
import json

KEY = "harness_v2"
V2 = "v2"
MODES = ("off", "list", "on")
DEFAULT_SURFACES = ("studio", "mcp")
# The jobs a harness runs: a chat turn and an MCP editor call. Index,
# preview, final and shorts jobs have no harness.
HARNESS_JOB_TYPES = ("agent_turn", "mcp_tool")


def _v(row, key, index=0):
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _ids(values):
    out = set()
    for value in values if isinstance(values, (list, tuple, set)) else ():
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


def parse(raw):
    """The switch as a plain dict; anything unreadable is mode off."""
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (TypeError, ValueError):
        value = None
    if not isinstance(value, dict):
        value = {}
    mode = str(value.get("mode") or "off").strip().lower()
    if mode not in MODES:
        mode = "off"
    surfaces = value.get("surfaces")
    if not isinstance(surfaces, (list, tuple)) or not surfaces:
        surfaces = list(DEFAULT_SURFACES)
    return {"mode": mode,
            "user_ids": _ids(value.get("user_ids")),
            "project_ids": _ids(value.get("project_ids")),
            "surfaces": {str(s).strip().lower() for s in surfaces},
            "override": value.get("override") is True}


def _savepoint(cur, sql):
    try:
        cur.execute(sql)
        return True
    except Exception:  # noqa: BLE001 - autocommit: no transaction to guard
        return False


def read(cur):
    """The stored switch, parsed. A read that fails is mode off.

    The read runs inside the caller's transaction (the worker's resume holds
    the account and message locks while it queues), so it sits under a
    savepoint: a failed read rolls back to it and the caller's queueing
    carries on, instead of meeting an aborted transaction."""
    guarded = _savepoint(cur, "SAVEPOINT harness_route_read")
    try:
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (KEY,))
        row = cur.fetchone()
    except Exception:  # noqa: BLE001 - routing must never stop a request
        if guarded:
            _savepoint(cur, "ROLLBACK TO SAVEPOINT harness_route_read")
        return parse(None)
    if guarded:
        _savepoint(cur, "RELEASE SAVEPOINT harness_route_read")
    return parse(_v(row, "value", 0))


def surface_of(job_type, payload=None):
    if job_type == "mcp_tool":
        return "mcp"
    if isinstance(payload, dict) and str(payload.get("via") or "") == "mcp":
        return "mcp"
    return "studio"


def internal(user_id):
    try:
        import spend_ledger
        return int(user_id) in spend_ledger.INTERNAL_USER_IDS
    except Exception:  # noqa: BLE001 - unknown means customer
        return False


def pick(cur, user_id, project_id, job_type, requested=None, payload=None):
    """'v2' when this request runs on harness v2, else None (v1)."""
    if job_type not in HARNESS_JOB_TYPES:
        return None
    cfg = read(cur)
    want = str(requested or "").strip().lower()
    if want in ("v1", "v2") and cfg["override"] and internal(user_id):
        return V2 if want == "v2" else None
    if cfg["mode"] == "off":
        return None
    if surface_of(job_type, payload) not in cfg["surfaces"]:
        return None
    if cfg["mode"] == "on":
        return V2
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        uid = None
    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        pid = None
    if uid in cfg["user_ids"] or pid in cfg["project_ids"]:
        return V2
    return None


def stamp(cur, user_id, project_id, job_type, payload):
    """Stamp one job payload about to be queued, in place, and return it.

    A chat turn or MCP call the switch routes to v2 gets ``harness: "v2"``.
    A payload that already carries a stamp keeps it (``setdefault``), so a
    continuation never changes harness mid-turn. A one-request override rides
    in as ``harness_request`` and is never stored."""
    requested = payload.pop("harness_request", None)
    if job_type in HARNESS_JOB_TYPES and "harness" not in payload:
        if pick(cur, user_id, project_id, job_type,
                requested, payload) == V2:
            payload.setdefault("harness", V2)
    return payload


def requested_from(data):
    """The one-request override a client body or MCP meta may carry."""
    if not isinstance(data, dict):
        return None
    value = data.get("harness")
    if value is None and isinstance(data.get("_meta"), dict):
        value = data["_meta"].get("harness")
    value = str(value or "").strip().lower()
    return value if value in ("v1", "v2") else None
