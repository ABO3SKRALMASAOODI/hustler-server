"""Which editing harness a request runs on (harness v2, 16 Nov 2026).

Each request carries its own stamp on its job payload, decided once when the
job is queued (``jobs.enqueue``). The switch is the ``app_kv`` row
``harness_v2``::

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


def read(cur):
    """The stored switch, parsed. A read that fails is mode off."""
    try:
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (KEY,))
        return parse(_v(cur.fetchone(), "value", 0))
    except Exception:  # noqa: BLE001 - routing must never stop a request
        return parse(None)


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


def requested_from(data):
    """The one-request override a client body or MCP meta may carry."""
    if not isinstance(data, dict):
        return None
    value = data.get("harness")
    if value is None and isinstance(data.get("_meta"), dict):
        value = data["_meta"].get("harness")
    value = str(value or "").strip().lower()
    return value if value in ("v1", "v2") else None
