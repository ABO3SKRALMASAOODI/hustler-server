"""Department ``world``: track 4A's verbs (INTERFACES_W4.md §4).

  add_depth_effect   WRITE: a 2.5D move through a still or a frozen frame,
                     depth of field, a rack focus that lands on a word, fog
                     or light through depth, a LiDAR-style point cloud, the
                     classic Ken Burns move — and, only when the user asked
                     for a camera move, a push through a word in Z
  add_time_effect    WRITE: reverse, echo trails, step-printing, a stutter
                     loop, smooth (interpolated) slow motion, an eased time
                     remap — picture only, the program clock never moves;
                     ``ramp`` writes an eased speed ramp (speed spans) plus
                     its interpolation in one call
  remove_depth_time  WRITE: remove depth and time items by id

Every write SOLVES its arguments (the moment a word lands on, defaults with
taste built in) into apply_edit_batch operations and commits them through
``tools_ext._write.commit``: a stale MCP client writing the same items
through apply_edit_batch is validated identically (fx_schemas/depth.py).
A depth device asks for the ``depth.small`` sidecar at write time and never
waits ("measuring"). Nothing here adds music or a sound; ``push_through``
is a camera move and needs the user's ask.
"""

import json
import re

DEPARTMENT = "world"
VERBS = ("add_depth_effect", "add_time_effect", "remove_depth_time")

DEPTH_KINDS = ("parallax", "ken_burns", "dof", "rack_focus", "fog", "light",
               "point_cloud", "push_through")
TIME_KINDS = ("reverse", "echo", "posterize", "stutter", "interpolate",
              "remap", "ramp")
# Default windows (s) when only a moment is named.
DEFAULT_SPAN = {"parallax": 2.6, "ken_burns": 2.6, "dof": 3.0,
                "rack_focus": 2.0, "fog": 3.0, "light": 2.0,
                "point_cloud": 1.6, "push_through": 1.4}
_ZOOM_ASK = re.compile(r"\b(push|zoom|punch|dolly|camera move|fly through|"
                       r"through the (word|text|type))", re.I)


def _edl(ctx):
    row = ctx.latest_edl()
    return row["json"], row["version"]


def _program_s(edl):
    import schemas
    try:
        return float(schemas.program_duration(edl) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _tl(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _obj(v, name):
    if v is None or isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            pass
    raise ValueError(f"{name} must be an object")


def _new_id(edl, prefix):
    used = {str(i.get("id")) for i in edl.get("fx") or []
            if isinstance(i, dict)}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def _rejected(msg):
    return f"REJECTED: {msg}"


def _commit(ctx, ops, summary):
    from tools_ext import _write
    return _write.commit(ctx, ops, summary)


def _upsert(item):
    from tools_ext import _write
    return _write.upsert_fx({k: v for k, v in item.items() if v is not None})


def _unavailable(fx_type):
    try:
        import remote
        if remote.executor_supports(f"fx:{fx_type}") is False:
            return (f"UNAVAILABLE: the render service does not draw "
                    f"{fx_type} yet (a deploy is rolling out); nothing was "
                    "written.")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _fps(ctx):
    try:
        return float(((ctx.index or {}).get("video") or {}).get("fps")
                     or 30.0)
    except (TypeError, ValueError, AttributeError):
        return 30.0


def find_word(edl, index, word, near=None, window=None):
    """(program onset, source word index, text) of ``word`` (a word or a
    short phrase, case-insensitive; or an int source index) in the kept
    transcript — the occurrence nearest ``near`` / inside ``window`` —
    or None."""
    import device_planner
    words = device_planner.program_words(edl, index or {})
    if isinstance(word, int) or (isinstance(word, str) and word.isdigit()):
        k = int(word)
        for w in words:
            if w["i"] == k:
                return w["t0"], w["i"], w["w"]
        return None
    toks = [t for t in re.findall(r"[a-z0-9']+", str(word or "").lower())]
    if not toks:
        return None
    cands = []
    for j, w in enumerate(words):
        run = [re.sub(r"[^a-z0-9']", "", str(x["w"] or "").lower())
               for x in words[j:j + len(toks)]]
        if run == toks:
            if window and not (window[0] - 0.05 <= w["t0"] <= window[1]):
                continue
            cands.append(w)
    if not cands:
        return None
    if near is not None:
        cands.sort(key=lambda w: abs(w["t0"] - float(near)))
    return cands[0]["t0"], cands[0]["i"], cands[0]["w"]


def _ensure_depth(ctx, edl, windows, asset=None):
    """Ask for depth.small over the items' source windows (the main video)
    or on an asset still; never waits. Returns a short note, or ''."""
    try:
        import db as dbx
        import fxgraph
        import sidecars
        if asset is not None:
            if not asset.get("sha256"):
                return ""
            src = sidecars.source_from(asset["sha256"],
                                       asset["storage_key"],
                                       {"duration": 0.04, "fps": 25.0,
                                        "width": asset.get("width"),
                                        "height": asset.get("height")},
                                       ctx.project_id, [])
            ranges = [(0.0, 0.04)]
        else:
            original = ctx.db.run(dbx.latest_asset, ctx.project_id,
                                  "original")
            proxy = ctx.db.run(dbx.latest_asset, ctx.project_id, "proxy")
            if not original or not proxy or not original.get("sha256"):
                return ""
            launch = fxgraph.peer_fn("matte_track:launch_source")
            src = launch(original["sha256"], proxy, ctx.index,
                         ctx.project_id) if launch else None
            if src is None:
                return ""
            tl = _tl(edl)
            ranges = []
            for a, b in windows:
                sa, sb = tl.out_to_src(a), tl.out_to_src(max(a, b - 0.01))
                if sa is not None and sb is not None:
                    ranges.append((min(sa, sb), max(sa, sb) + 0.05))
            if not ranges:
                return ""
        h = sidecars.ensure("depth.small", src, ranges,
                            priority="interactive")
        if sidecars.status(h) == "ready":
            return ""
        est = sidecars.estimate_s(h)
        return (f"Measuring depth for this (≈{max(1, int(est))} s on the "
                "batch lane): previews until then draw the stand-in (two "
                "planes from the person matte, or a Ken Burns move for a "
                "still); finals wait up to 90 s for it.")
    except Exception:  # noqa: BLE001 — analysis never fails a write
        return ""


def _asset_row(ctx, key):
    try:
        import db as dbx
        return ctx.db.run(dbx.asset_by_key, ctx.project_id, key)
    except Exception:  # noqa: BLE001
        return None


# ── add_depth_effect ────────────────────────────────────────────────────

def add_depth_effect(ctx, kind, start=None, end=None, word=None,
                     asset_key=None, src_t=None, move=None, amount=None,
                     focus=None, focus_from=None, rack_at=None, text=None,
                     colour=None, blur=None, strength=None, backdrop=None,
                     density=None, stage=None, user_asked=False, id=None):
    edl, _v = _edl(ctx)
    kind = str(kind or "").strip().lower()
    if kind not in DEPTH_KINDS:
        return _rejected("kind is one of " + ", ".join(DEPTH_KINDS))
    if kind == "push_through" and not (
            user_asked or _ZOOM_ASK.search(str(getattr(ctx, "user_message",
                                                       "") or ""))):
        return _rejected("push_through is a camera move: zooms are optional "
                         "and only when the user asks for one. Use it when "
                         "they asked for a push/zoom (or pass user_asked "
                         "true when they did); otherwise a rack_focus or a "
                         "parallax lands the moment without moving the "
                         "camera.")
    prog = _program_s(edl)
    index = getattr(ctx, "index", None) or {}
    bind = None
    try:
        move = _obj(move, "move")
        focus = _obj(focus, "focus")
        focus_from = _obj(focus_from, "focus_from")
        if isinstance(focus, str):
            focus = {"on": focus}
        if isinstance(focus_from, str):
            focus_from = {"on": focus_from}
        hit = None
        if word is not None:
            hit = find_word(edl, index, word, near=start,
                            window=(float(start), float(end))
                            if start is not None and end is not None
                            else None)
            if hit is None:
                return _rejected(f"'{word}' is not in the kept transcript "
                                 "(get_kept_transcript names the words)")
        span = DEFAULT_SPAN[kind]
        if kind == "rack_focus":
            land = float(rack_at) if rack_at is not None else (
                hit[0] if hit else None)
            if land is None:
                return _rejected("a rack focus lands on a word: pass word "
                                 "(or rack_at)")
            s = float(start) if start is not None else max(0.0, land - 1.0)
            e = float(end) if end is not None else min(prog, land + 1.0)
            rack_at = round(land, 2)
            if hit:
                bind = {"on": "word", "ref": hit[1], "rhythm": "speech"}
        else:
            if start is None and hit is not None:
                s = hit[0] - (0.0 if kind in ("parallax", "ken_burns",
                                              "point_cloud") else 0.3)
                bind = {"on": "word", "ref": hit[1], "rhythm": "speech"}
            else:
                s = float(start) if start is not None else None
            if s is None:
                return _rejected("name the moment: start (program s) or a "
                                 "word")
            e = float(end) if end is not None else s + span
        s, e = round(max(0.0, s), 2), round(min(prog, e), 2)
        if e - s < 0.2:
            return _rejected(f"the window {s:g}-{e:g}s is too short for a "
                             "depth effect (>= 0.2 s)")
    except (TypeError, ValueError) as x:
        return _rejected(str(x))
    source = None
    asset = None
    if asset_key:
        asset = _asset_row(ctx, asset_key)
        source = {"kind": "asset", "asset_key": str(asset_key)}
    elif src_t is not None:
        source = {"kind": "source", "src_t": round(float(src_t), 3)}
    if kind == "ken_burns" and source is None:
        tl = _tl(edl)
        st = tl.out_to_src(s)
        if st is None:
            return _rejected("ken_burns moves a still: pass asset_key (a "
                             "project image) or src_t (a main-video second)")
        source = {"kind": "source", "src_t": round(float(st), 3)}
    if move is None and kind in ("parallax", "ken_burns", "point_cloud",
                                 "push_through", "light"):
        move = {"path": "orbit_left" if kind == "point_cloud" else
                "push_in", "amount": float(amount) if amount is not None
                else (0.35 if kind != "point_cloud" else 0.4),
                "ease": "in_out"}
    elif move is not None and amount is not None:
        move = dict(move, amount=float(amount))
    if kind == "rack_focus":
        focus = focus or {"on": "subject"}
        focus_from = focus_from or {"on": "background"
                                    if focus.get("on") != "background"
                                    else "subject"}
    if kind == "dof":
        focus = focus or {"on": "subject"}
    if kind == "point_cloud":
        focus = focus or {"on": "subject"}
        backdrop = backdrop or "black"
    if kind not in ("rack_focus", "dof", "fog", "point_cloud"):
        focus = None if kind != "fog" else focus
    stage = stage or ("screen" if kind in ("parallax", "ken_burns")
                      and source is not None and source.get("kind")
                      == "asset" else "world")
    item = {"id": id or _new_id(edl, f"dp_{kind}_"), "type": "depth",
            "stage": stage, "start": s, "end": e, "kind": kind,
            "source": source, "move": move, "focus": focus,
            "focus_from": focus_from if kind == "rack_focus" else None,
            "rack_at": rack_at if kind == "rack_focus" else None,
            "text": text if kind == "push_through" else None,
            "colour": colour, "blur": blur, "strength": strength,
            "backdrop": backdrop if kind == "point_cloud" else None,
            "density": density if kind == "point_cloud" else None,
            "device": f"depth_{kind}", "bind": bind}
    gone = _unavailable("depth")
    if gone:
        return gone
    out = _commit(ctx, [_upsert(item)], f"{kind} depth effect {item['id']} "
                  f"{s:g}-{e:g}s")
    if out.startswith("EDL "):
        note = _ensure_depth(ctx, edl, [(s, e)], asset=asset) \
            if kind != "ken_burns" else ""
        if note:
            out += "\n" + note
    return out


# ── add_time_effect ─────────────────────────────────────────────────────

def _speed_ramp_ops(edl, s, e, factor, steps=5):
    """An eased speed ramp over program [s, e]: ``steps`` speed spans
    easing 1.0 -> factor -> 1.0 over the window's SOURCE seconds."""
    tl = _tl(edl)
    a, b = tl.out_to_src(s), tl.out_to_src(max(s, e - 0.01))
    if a is None or b is None or b - a < 0.4:
        raise ValueError("a ramp needs at least 0.4 s of main footage")
    edges = [a + (b - a) * k / steps for k in range(steps + 1)]
    spans = []
    for k in range(steps):
        x = (k + 0.5) / steps
        w = 1 - abs(2 * x - 1)               # 0 at the ends, 1 mid-window
        f = round(1.0 + (float(factor) - 1.0) * (w * w * (3 - 2 * w)), 3)
        spans.append({"id": f"rp{k}", "start": round(edges[k], 3),
                      "end": round(edges[k + 1], 3),
                      "factor": max(0.1, f)})
    return spans


def add_time_effect(ctx, kind, start=None, end=None, word=None,
                    frames=None, repeats=None, echo=None, curve=None,
                    factor=None, id=None, allow_speech=False):
    edl, _v = _edl(ctx)
    kind = str(kind or "").strip().lower()
    if kind not in TIME_KINDS:
        return _rejected("kind is one of " + ", ".join(TIME_KINDS))
    prog = _program_s(edl)
    index = getattr(ctx, "index", None) or {}
    bind = None
    try:
        echo = _obj(echo, "echo")
        if word is not None and start is None:
            hit = find_word(edl, index, word)
            if hit is None:
                return _rejected(f"'{word}' is not in the kept transcript")
            start = hit[0]
            bind = {"on": "word", "ref": hit[1], "rhythm": "speech"}
        if start is None:
            return _rejected("name the window: start (program s) or a word")
        s = round(max(0.0, float(start)), 2)
        e = round(min(prog, float(end) if end is not None else s + 1.5), 2)
        if e - s < 0.2:
            return _rejected("the window is too short (>= 0.2 s)")
    except (TypeError, ValueError) as x:
        return _rejected(str(x))
    if kind in ("reverse", "remap", "stutter") and not allow_speech:
        import device_planner
        talk = [w for w in device_planner.program_words(edl, index)
                if s < w["t0"] < e]
        if len(talk) >= 2:
            return _rejected(f"{kind} on {len(talk)} spoken words breaks lip "
                             "sync (the audio keeps playing forward). Use it "
                             "on B-roll, a gesture or a pause — or pass "
                             "allow_speech true if the user wants it there.")
    if kind == "ramp":
        f = float(factor) if factor is not None else 0.5
        if not 0.1 <= f < 1.0:
            return _rejected("a ramp's factor is the slowest speed, 0.1-0.95")
        try:
            spans = _speed_ramp_ops(edl, s, e, f)
        except ValueError as x:
            return _rejected(str(x))
        speed = [sp for sp in edl.get("speed") or []
                 if not any(sp["end"] > x["start"] and sp["start"] < x["end"]
                            for x in spans)]
        for sp in spans:
            sp["id"] = f"{id or 'ramp'}_{sp['id']}"
        ops = [{"action": "set", "layer": "speed", "value": speed + spans}]
        # the slowed window grows on the program clock: interpolate it there
        edl2 = dict(edl, speed=speed + spans)
        tl2 = _tl(edl2)
        ps = tl2.src_to_out(spans[0]["start"] + 0.001)
        pe = tl2.src_to_out(spans[-1]["end"] - 0.001)
        if ps is not None and pe is not None and pe - ps > 0.3:
            ops.append(_upsert({"id": f"{id or 'ramp'}_interp",
                                "type": "time", "stage": "world",
                                "start": round(ps, 2), "end": round(pe, 2),
                                "kind": "interpolate",
                                "device": "time_interpolate", "bind": bind}))
        return _commit(ctx, ops, f"eased speed ramp to {f:g}x {s:g}-{e:g}s")
    item = {"id": id or _new_id(edl, f"tm_{kind}_"), "type": "time",
            "stage": "world", "start": s, "end": e, "kind": kind,
            "frames": frames if kind in ("posterize", "stutter") else None,
            "repeats": repeats if kind == "stutter" else None,
            "echo": echo if kind == "echo" else None,
            "curve": curve if kind == "remap" else None,
            "device": f"time_{kind}", "bind": bind}
    gone = _unavailable("time")
    if gone:
        return gone
    return _commit(ctx, [_upsert(item)], f"{kind} time effect {item['id']} "
                   f"{s:g}-{e:g}s")


# ── remove ──────────────────────────────────────────────────────────────

def remove_depth_time(ctx, ids):
    from tools_ext import _write
    edl, _v = _edl(ctx)
    if isinstance(ids, str):
        ids = [i.strip() for i in ids.split(",") if i.strip()]
    ids = list(dict.fromkeys(str(i) for i in ids or []))
    if not ids:
        return _rejected("name the ids to remove")
    have = {str(i.get("id")): i.get("type") for i in edl.get("fx") or []
            if isinstance(i, dict)}
    bad = [i for i in ids if have.get(i) not in ("depth", "time")]
    if bad:
        return _rejected(f"not a depth or time item here: {', '.join(bad)}")
    return _commit(ctx, [_write.remove_fx(i) for i in ids],
                   f"removed {', '.join(ids)}")


# ── registration ────────────────────────────────────────────────────────

_S = {"type": "number", "description": "program s"}
_MOVE = {"type": "object", "description": "{path: push_in|pull_out|"
         "drift_left|drift_right|rise|fall|orbit_left|orbit_right, "
         "amount 0.05-1, ease}"}
_FOCUS = {"type": "string", "enum": ["subject", "background", "near",
                                     "far"]}

TOOL_SPECS = {
    "add_depth_effect": (
        add_depth_effect,
        "Depth devices from the shot's measured depth: parallax (a 2.5D "
        "move through a still: asset_key, a main-video second src_t, or the "
        "frame at start — archive photos come alive), ken_burns (2D move), "
        "dof (subject sharp, world soft), rack_focus (focus pulls from "
        "focus_from to focus, landing ON word), fog / light (through "
        "depth), point_cloud (LiDAR-style cloud of the person or scene), "
        "push_through (a camera push through text; only when the user "
        "asked for a push). word binds it to a spoken word. Ideas, not "
        "decoration: one per moment that earns it.",
        {"kind": {"type": "string", "enum": list(DEPTH_KINDS)},
         "start": _S, "end": _S, "word": {"type": "string"},
         "asset_key": {"type": "string"}, "src_t": {"type": "number"},
         "move": _MOVE, "amount": {"type": "number"},
         "focus": _FOCUS, "focus_from": _FOCUS,
         "rack_at": _S, "text": {"type": "string"},
         "colour": {"type": "string"}, "blur": {"type": "number"},
         "strength": {"type": "number"},
         "backdrop": {"type": "string", "enum": ["black", "dim",
                                                 "program"]},
         "density": {"type": "number"},
         "stage": {"type": "string", "enum": ["world", "screen"]},
         "user_asked": {"type": "boolean"}, "id": {"type": "string"}}),
    "add_time_effect": (
        add_time_effect,
        "Picture-only time devices (audio and program length never move): "
        "reverse, echo (trails), posterize (step-print, frames 2-8), "
        "stutter (loop frames x repeats), interpolate (smooth slow motion "
        "in a slowed window), remap (curve ease_in|ease_out|ease_in_out|"
        "hold_then_catch). ramp writes an eased speed ramp to factor plus "
        "its interpolation. Reverse/remap/stutter refuse spoken words "
        "(lip sync) unless allow_speech.",
        {"kind": {"type": "string", "enum": list(TIME_KINDS)},
         "start": _S, "end": _S, "word": {"type": "string"},
         "frames": {"type": "integer"}, "repeats": {"type": "integer"},
         "echo": {"type": "object", "description": "{count 2-8, spacing_f "
                  "1-6, decay 0.2-0.9}"},
         "curve": {"type": "string", "enum": ["ease_in", "ease_out",
                                              "ease_in_out",
                                              "hold_then_catch"]},
         "factor": {"type": "number"}, "allow_speech": {"type": "boolean"},
         "id": {"type": "string"}}),
    "remove_depth_time": (
        remove_depth_time,
        "Remove depth and time effects by id.",
        {"ids": {"type": "array", "items": {"type": "string"}}}),
}

REQUIRED_ARGS = {"add_depth_effect": ["kind"], "add_time_effect": ["kind"],
                 "remove_depth_time": ["ids"]}
WRITE_TOOLS = {"add_depth_effect", "add_time_effect", "remove_depth_time"}
COMPACT = {
    "add_depth_effect": "Depth devices: parallax (2.5D move through a still/"
                        "asset/frame), ken_burns, dof, rack_focus (lands on "
                        "word), fog, light, point_cloud (LiDAR), "
                        "push_through (only if user asked a push).",
    "add_time_effect": "Picture-only time: reverse, echo, posterize, "
                       "stutter, interpolate (smooth slow-mo), remap; ramp "
                       "= eased speed ramp + interpolation.",
}
