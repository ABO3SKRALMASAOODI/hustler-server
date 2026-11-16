"""The seven tools: schemas and one dispatcher (the app's loop and MCP share
it). Each returns an Envelope; ``dispatch`` never raises for a tool's own
failure: it answers with a failed envelope naming what works instead."""
import json
import traceback

import agent_tools

from harness2 import envelope

READS = frozenset({"look", "timeline", "find", "wait"})
WRITES = frozenset({"edit", "undo", "render"})
NAMES = ("look", "timeline", "edit", "undo", "render", "find", "wait")

_NUM_LIST = {"type": "array", "items": {"type": "number"}}
_RANGE = {"type": "array", "items": {"type": "number"}, "minItems": 2,
          "maxItems": 2}

SCHEMAS = [
    {"name": "look", "description": (
        "Your eyes and ears on anything. target: source | program (the cut, "
        "assembled from source frames, no render needed) | render (the real "
        "rendered pixels) | asset:<key>. show: frames | sheet | junctions "
        "(frames either side of every cut) | removed (what a version "
        "dropped) | chapters | transcript | shots | audio | activity "
        "(measured motion and sound: active and idle stretches) | moments "
        "(query) | rank | skill:<name>. Times are seconds on the target's "
        "own clock. Every answer states its coverage."),
     "parameters": {"type": "object", "properties": {
         "target": {"type": "string"}, "show": {"type": "string"},
         "times": _NUM_LIST, "range": _RANGE, "every": {"type": "number"},
         "fps": {"type": "number"}, "tiles": {"type": "integer"},
         "query": {"type": "string"}, "version": {"type": "integer"}}}},
    {"name": "timeline", "description": (
        "The edit, measured: version, length, kept share, what plays where "
        "on the film clock (paged with range), layers, coverage, the last "
        "render. versions=true lists the history; schema='ops' gives every "
        "edit op with an example and the verbs for {tool}; schema=<layer> "
        "gives a layer's fields."),
     "parameters": {"type": "object", "properties": {
         "version": {"type": "integer"}, "range": _RANGE,
         "versions": {"type": "boolean"}, "schema": {"type": "string"}}}},
    {"name": "edit", "description": (
        "Apply any list of ops in one call; writes ONE new version and "
        "returns the measured result. Ops: keep, cut, restore (source "
        "seconds), speed, fit_length, card, text, captions, place, scenes, "
        "voiceover, music, sfx, audio, frame, grade, transition, zoom, "
        "clips, layer set/upsert/remove, tool. It repairs what it can and "
        "says so; anything impossible is listed under not_applied with what "
        "works instead, and the rest lands. timeline(schema='ops') shows "
        "every op's shape."),
     "parameters": {"type": "object", "properties": {
         "ops": {"type": "array", "items": {"type": "object"}},
         "note": {"type": "string"},
         "base_version": {"type": "integer"},
         "operation_id": {"type": "string"}},
         "required": ["ops"]}},
    {"name": "undo", "description": (
        "Restore any earlier version as the newest one (history only grows; "
        "default: the version before the head)."),
     "parameters": {"type": "object", "properties": {
         "to": {"type": "integer"}, "operation_id": {"type": "string"}}}},
    {"name": "render", "description": (
        "Eyes on the real result. scope: changes (the changed sections) | "
        "full (the whole preview: screening pages, duration, LUFS, true "
        "peak, mid-word cuts, picture checks). output: film | stills "
        "(times) | slides (a PowerPoint: slides=[{title, items: [{t, "
        "note}], frames?: [t], source?: 'asset:<key>'}], times on the "
        "original file's clock). Long renders return a handle and a sheet "
        "of the cut at once; wait(handle) settles them."),
     "parameters": {"type": "object", "properties": {
         "scope": {"type": "string"}, "final": {"type": "boolean"},
         "output": {"type": "string"}, "times": _NUM_LIST,
         "slides": {"type": "array", "items": {"type": "object"}},
         "quality": {"type": "string"}}}},
    {"name": "find", "description": (
        "Media from outside the project; many queries in one call. kind: "
        "stock_video | stock_photo | web_image | footage | song | music | "
        "sfx. Each query returns ids, licence, duration, a description and "
        "a board image with the ids on the tiles. Place a result with "
        "edit([{place: {id, at, dur}}]); music ids go in {music: {id}}, "
        "sounds in {sfx: {key}}."),
     "parameters": {"type": "object", "properties": {
         "kind": {"type": "string"},
         "query": {"anyOf": [{"type": "string"},
                             {"type": "array", "items": {"type": "string"}}]},
         "count": {"type": "integer"}, "orientation": {"type": "string"},
         "max_s": {"type": "number"}}, "required": ["query"]}},
    {"name": "wait", "description": (
        "Wait up to max_s (<=100) on a handle a tool returned: the finished "
        "result, or plain progress."),
     "parameters": {"type": "object", "properties": {
         "handle": {"type": "string"}, "max_s": {"type": "number"}},
         "required": ["handle"]}},
]


def openai_tools():
    return [{"type": "function", "function": dict(s)} for s in SCHEMAS]


def _args(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw or "{}")
            return value if isinstance(value, dict) else {}
        except ValueError:
            value = _lenient(raw)
            return value if isinstance(value, dict) else {}
    return {}


def _lenient(raw):
    """Arguments a model cut short or wrapped: the object it began, closed.
    A repair of the transport, never of what was asked."""
    text = str(raw or "").strip().strip("`")
    start = text.find("{")
    if start < 0:
        return None
    text = text[start:]
    try:
        value, _end = json.JSONDecoder().raw_decode(text)
        return value
    except ValueError:
        pass
    stack, in_str, esc = [], False, False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    closed = text + ('"' if in_str else "") + "".join(reversed(stack))
    try:
        return json.loads(closed.replace(",}", "}").replace(",]", "]"))
    except ValueError:
        return None


def _only(args, names):
    return {k: v for k, v in args.items() if k in names}


def dispatch(ctx, name, raw_args, wait_s=None):
    """One tool call -> Envelope."""
    args = _args(raw_args)
    try:
        if name == "look":
            from harness2 import look
            return look.look(ctx, **_only(args, (
                "target", "show", "times", "range", "every", "fps", "tiles",
                "query", "version")))
        if name == "timeline":
            from harness2 import timeline_tool
            return timeline_tool.timeline(ctx, **_only(args, (
                "version", "range", "versions", "schema")))
        if name == "edit":
            from harness2 import commit
            ops = args.get("ops")
            if isinstance(ops, str):
                ops = _args('{"ops": ' + ops + "}").get("ops")
            return commit.edit(ctx, ops if ops is not None else [],
                               args.get("note") or "",
                               base_version=args.get("base_version"),
                               operation_id=args.get("operation_id"))
        if name == "undo":
            from harness2 import commit
            return commit.undo(ctx, args.get("to"),
                               operation_id=args.get("operation_id"))
        if name == "render":
            from harness2 import render_tool
            return render_tool.render(ctx, wait_s=wait_s, **_only(args, (
                "scope", "final", "output", "times", "slides", "quality")))
        if name == "find":
            from harness2 import find_tool
            return find_tool.find(ctx, **_only(args, (
                "kind", "query", "count", "orientation", "max_s")))
        if name == "wait":
            from harness2 import wait_tool
            return wait_tool.wait(ctx, args.get("handle"),
                                  args.get("max_s", 60))
    except (agent_tools.AskUser, agent_tools.FeatureUpgrade):
        raise
    except Exception as exc:  # noqa: BLE001 - an engine fault is an answer
        print(f"[harness2] {name} raised: "
              f"{traceback.format_exc()[-1500:]}", flush=True)
        try:
            from harness2 import tickets
            tickets.file_refusals(ctx, [{"tool": f"harness2.{name}",
                                         "text": f"{type(exc).__name__}: "
                                                 f"{exc}"}])
        except Exception:  # noqa: BLE001
            pass
        return envelope.failed(
            name, f"{name} stopped on an internal error "
                  f"({type(exc).__name__}); nothing was lost",
            "the same call again, or another route to the same result")
    return envelope.failed(name, f"there is no tool {name!r}",
                           "look, timeline, edit, undo, render, find, wait")
