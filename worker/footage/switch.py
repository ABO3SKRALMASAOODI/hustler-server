"""Who may get real footage. Rights are the owner's decision, so customers are OFF by default.

app_kv 'footage' (JSON): {"mode": "off" | "on", "test_user_ids": [..]}. No row, a bad row or no
database reads as off. "on" opens it to every customer; test_user_ids opens it to named accounts only.
The owner's workflows (the shell) are a different scope: VALMERA_FOOTAGE=0 turns them off.
"""
import json
import os

KEY = "footage"
DEFAULTS = {"mode": "off", "test_user_ids": []}


def parse(raw):
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (TypeError, ValueError):
        return dict(DEFAULTS)
    if not isinstance(data, dict) or data.get("mode") not in ("off", "on"):
        return dict(DEFAULTS)
    try:
        ids = [int(v) for v in data.get("test_user_ids") or []]
    except (TypeError, ValueError):
        return dict(DEFAULTS)
    return {"mode": data["mode"], "test_user_ids": ids}


def read_row(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (KEY,))
        row = cur.fetchone()
    if not row:
        return None
    return row.get("value") if isinstance(row, dict) else row[0]


def read(worker_db=None):
    """Never raises: anything but a good row is off."""
    if worker_db is None:
        return dict(DEFAULTS)
    try:
        return parse(worker_db.run(read_row))
    except Exception:  # noqa: BLE001 a bad row or no database means off
        return dict(DEFAULTS)


def allowed(scope, user_id=None, cfg=None):
    """scope "workflow": the owner's own films (the shell), on unless VALMERA_FOOTAGE=0.
    scope "customer": only what the row opens; cfg is read(worker_db), absent means off."""
    if scope == "workflow":
        return os.environ.get("VALMERA_FOOTAGE", "1") != "0"
    cfg = cfg or DEFAULTS
    if cfg.get("mode") == "on":
        return True
    return user_id is not None and int(user_id) in (cfg.get("test_user_ids") or [])
