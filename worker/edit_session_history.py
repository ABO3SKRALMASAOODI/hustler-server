"""The most recent Sol conversation in a project session, for an immediately linked follow-up.

A snapshot replaces the overlapping recent-chat excerpt. A cold worker, different
kernel, compacted history, or unrelated turn uses the ordinary editor context.
Keep one expiring copy per session in the private database and a bounded worker-local copy.
"""
from collections import OrderedDict
import json
import threading
import time
import zlib

_entries = OrderedDict()
_lock = threading.Lock()
_bytes = 0
MAX_BYTES = 32 * 1024 * 1024
MAX_ENTRY = 4 * 1024 * 1024
TTL = 30 * 60  # the evolving history's provider write lifetime


def _save(conn, project, session, message, tokens, payload):
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO edit_session_prefixes
            (project_id,session_id,message_id,input_tokens,expires_at,body)
            VALUES (%s,%s,%s,%s,NOW() + %s * interval '1 second',%s)
            ON CONFLICT (project_id,session_id) DO UPDATE SET
                message_id=EXCLUDED.message_id, input_tokens=EXCLUDED.input_tokens,
                expires_at=EXCLUDED.expires_at, body=EXCLUDED.body
            WHERE edit_session_prefixes.message_id < EXCLUDED.message_id""",
            (project, session, message, tokens, TTL, payload))


def _load(conn, project, session, message, room):
    with conn.cursor() as cur:
        cur.execute("""SELECT body FROM edit_session_prefixes WHERE project_id=%s AND session_id=%s
            AND message_id=%s AND input_tokens<=%s AND expires_at>NOW()""",
            (project, session, message, room))
        row = cur.fetchone()
        return bytes(row["body"]) if row else None


def expire(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM edit_session_prefixes WHERE expires_at<=NOW()")


def _shared(worker_db, operation, *args):
    if worker_db is None:
        return None
    try:
        return worker_db.run(operation, *args)
    except Exception as exc:
        # A rolling release, cache outage or concurrent project deletion must
        # leave the ordinary editor context available. Keep content out of logs.
        print(f"[edit-cache] {operation.__name__}: {type(exc).__name__}", flush=True)
        return None


def remember(key, message_id, body, input_tokens, *, worker_db=None, scope=None):
    global _bytes
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > MAX_ENTRY:
        return
    now = time.monotonic()
    with _lock:
        existing = _entries.get(key)
        if existing and existing[0] >= message_id:
            return
        if existing:
            _bytes -= len(_entries.pop(key)[3])
        while _entries and (now - next(iter(_entries.values()))[1] > TTL
                            or _bytes + len(payload) > MAX_BYTES):
            _bytes -= len(_entries.popitem(last=False)[1][3])
        _entries[key] = (message_id, now, input_tokens, payload)
        _bytes += len(payload)


    if scope:
        _shared(worker_db, _save, *scope, message_id, input_tokens, zlib.compress(payload, level=1))


def previous(key, message_id, token_room, *, worker_db=None, scope=None):
    with _lock:
        entry = _entries.get(key)
        payload = entry[3] if (entry and entry[0] == message_id and time.monotonic() - entry[1] <= TTL
                               and entry[2] <= token_room) else None
    if payload:
        return json.loads(payload)
    if token_room > 0 and scope:
        compressed = _shared(worker_db, _load, *scope, message_id, token_room)
        if compressed:
            try:
                return json.loads(zlib.decompress(compressed))
            except (ValueError, zlib.error):
                pass
    return None


def join(body, previous_body, static_count, catalog):
    """Continue the exact stored wire history when its instructions and tools still apply."""
    if any(previous_body.get(k) != body.get(k) for k in ("model", "tools", "reasoning", "prompt_cache_key")):
        return None
    current, history = body["input"], previous_body["input"]
    if current[:static_count] != history[:static_count]:
        return None
    # All the old real tool results stay associated with their original calls.
    # Reuse a loaded schema once. A product upgrade changing one starts fresh.
    loaded = {}
    last_effort = body["reasoning"]["effort"]
    for item in history:
        if item.get("type") == "additional_tools":
            loaded.update({tool["name"]: tool for tool in item["tools"]})
        elif item.get("type") == "configuration_update":
            last_effort = item["reasoning"]["effort"]
    if any(catalog.get(name) != schema for name, schema in loaded.items()):
        return None
    tail = []
    if last_effort != body["reasoning"]["effort"]:
        tail.append({"type": "configuration_update", "reasoning": body["reasoning"]})
    for item in current[static_count:]:
        if item.get("type") == "additional_tools":
            if any(tool["name"] in loaded and loaded[tool["name"]] != tool for tool in item["tools"]):
                return None
            fresh = [tool for tool in item["tools"] if tool["name"] not in loaded]
            if fresh:
                tail.append(dict(item, tools=fresh))
        else:
            tail.append(item)
    return dict(body, input=history + tail)
