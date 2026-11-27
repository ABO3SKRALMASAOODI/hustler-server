"""The most recent Sol conversation on this worker, for an immediately linked follow-up.

A snapshot replaces the overlapping recent-chat excerpt. A cold worker, different
kernel, compacted history, or unrelated turn uses the ordinary editor context.
Keep at most one bounded, short-lived snapshot per session in RAM.
"""
from collections import OrderedDict
import json
import threading
import time

_entries = OrderedDict()
_lock = threading.Lock()
_bytes = 0
MAX_BYTES = 32 * 1024 * 1024
MAX_ENTRY = 4 * 1024 * 1024
TTL = 30 * 60  # the evolving history's provider write lifetime


def remember(key, message_id, body, input_tokens):
    global _bytes
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    now = time.monotonic()
    with _lock:
        existing = _entries.get(key)
        if existing and existing[0] >= message_id:
            return
        if existing:
            _bytes -= len(_entries.pop(key)[3])
        while _entries and (now - next(iter(_entries.values()))[1] > TTL
                            or _bytes + min(len(payload), MAX_ENTRY) > MAX_BYTES):
            _bytes -= len(_entries.popitem(last=False)[1][3])
        if len(payload) <= MAX_ENTRY:
            _entries[key] = (message_id, now, input_tokens, payload)
            _bytes += len(payload)


def previous(key, message_id, token_room):
    with _lock:
        entry = _entries.get(key)
        if not entry or entry[0] != message_id or time.monotonic() - entry[1] > TTL or entry[2] > token_room:
            return None
        payload = entry[3]
    return json.loads(payload)


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
