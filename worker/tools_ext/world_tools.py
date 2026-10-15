"""Department ``world``: graphics that live IN the footage (Wave 3, 3A-3C).

  attach          a motion graphic attached to a face part, the head, a
                  hand part, a body joint, a tracked point, an object or a
                  plane (MotionItem.anchor); what passes in front of it
  track_point     a point to follow through its shot (an fx ``track``)
  select_object   an object to occlude or carry type (an fx ``object``)
  pin_to_plane    a graphic (or new words) laid on a surface — floor, wall,
                  desk, table, screen (an fx ``plane`` + the anchor)
  place_in_world  the attachment language in one call: "behind the laptop",
                  "on the desk", "follow her hand", "round his head"
  remove_world    removes definitions (detaching what names them) or
                  detaches graphics

Coordinates the agent gives are PROGRAM frame fractions at PROGRAM second
``t`` (what look_at shows); the verbs store them as SOURCE fractions on the
SOURCE clock (devices/INTERFACES_W3.md §3), so a reframe or a new aspect
never moves the world. Writes SOLVE into apply_edit_batch operations and
commit through tools_ext._write.commit: a stale client writing the same
ops validates identically (skills/world.md lists them). A write never
waits for analysis: it asks the batch lane for what it needs and says
"measuring (≈N s)".

Browserless: nothing here renders, opens a browser or runs a model.
"""

import json
import re

from tools_ext import _write

DEPARTMENT = "world"

VERBS = ("attach", "track_point", "select_object", "pin_to_plane",
         "place_in_world", "remove_world", "look_at_world")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_LANDMARK_KIND = {"face": "landmarks.face", "head": "landmarks.face",
                  "hand": "landmarks.hand", "pose": "landmarks.pose"}


# ── context helpers ─────────────────────────────────────────────────────

def _latest(ctx):
    return json.loads(json.dumps(ctx.latest_edl()["json"]))


def _tl(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _next_id(items, prefix):
    have = {str(i.get("id")) for i in items or []}
    k = 1
    while f"{prefix}{k}" in have:
        k += 1
    return f"{prefix}{k}"


def _ids(value):
    if isinstance(value, str):
        value = [p for p in re.split(r"[\s,]+", value) if p]
    return [str(v) for v in (value or [])]


def _num_list(value, n=None):
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        value = json.loads(v) if v.startswith("[") else \
            [float(x) for x in re.split(r"[\s,]+", v) if x]
    out = [float(x) if not isinstance(x, list) else [float(y) for y in x]
           for x in value]
    if n is not None and len(out) != n:
        raise ValueError(f"expected {n} numbers")
    return out


def _video(ctx):
    return (ctx.index or {}).get("video") or {}


def _frame_size(edl, ctx):
    import renderer
    v = _video(ctx)
    fr = edl.get("frame") or {}
    return renderer.frame_dims(float(v.get("width") or 1920),
                               float(v.get("height") or 1080),
                               fr.get("ratio"))


def _world_ctx(ctx, edl):
    import world_layer
    W, H = _frame_size(edl, ctx)
    tl = _tl(edl)
    src = None
    try:
        import matte_track
        src = matte_track.read_source(ctx.index)
    except Exception:  # noqa: BLE001
        src = None
    return world_layer.Ctx(edl, W, H, float(_video(ctx).get("fps") or 30.0),
                           tl=tl, source=src, index=ctx.index,
                           out_duration=tl.out_duration)


def to_source(ctx, edl, t, pts):
    """(src_t, [[x, y] SOURCE fractions]) for PROGRAM fractions ``pts`` at
    PROGRAM second ``t``; raises ValueError where the program shows no main
    footage."""
    import world_layer
    wc = _world_ctx(ctx, edl)
    tr = wc.tl.out_to_src(float(t))
    if tr is None:
        raise ValueError(f"the program shows no main footage at {t:g}s "
                         "(an insert or a hold)")
    A = world_layer.program_affine(wc.geometry("world"), float(t), tr)
    if A is None:
        raise ValueError(f"the footage is not on screen at {t:g}s")
    out = []
    for x, y in pts:
        out.append([round((float(x) - A[0, 2]) / A[0, 0], 4),
                    round((float(y) - A[1, 2]) / A[1, 1], 4)])
    return round(float(tr), 2), out


def _launch_source(ctx):
    """A SourceRef that may launch sidecars (track 1A's helper), or None."""
    try:
        import db as dbx
        import fxgraph
        original = ctx.db.run(dbx.latest_asset, ctx.project_id, "original")
        proxy = ctx.db.run(dbx.latest_asset, ctx.project_id, "proxy")
        if not original or not proxy or not original.get("sha256"):
            return None
        launch = fxgraph.peer_fn("matte_track:launch_source")
        if launch is None:
            return None
        return launch(original["sha256"], proxy, ctx.index, ctx.project_id)
    except Exception:  # noqa: BLE001 — analysis never fails a write
        return None


def _measuring(ctx, edl, defs=(), landmark_items=()):
    """Ask the batch lane for what these definitions and anchored items
    need (never waits). Returns a reply note ('' when all is ready)."""
    try:
        import sidecars
        import world_defs
        src = _launch_source(ctx)
        if src is None:
            return ""
        tl = _tl(edl)
        est, pending = 0.0, []
        for d in defs:
            st, e = world_defs.ensure(d, tl, src)
            if st not in (None, "ready", "static"):
                pending.append(d.get("label") or d["type"])
                est = max(est, e)
        for m in landmark_items:
            kind = _LANDMARK_KIND.get(str(m["anchor"]["to"]).split(".")[0])
            if kind is None:
                continue
            a = tl.out_to_src(float(m["start"]))
            b = tl.out_to_src(max(float(m["start"]), float(m["end"]) - 0.02))
            if a is None or b is None:
                continue
            h = sidecars.ensure(kind, src, [(min(a, b), max(a, b) + 0.1)],
                                priority="interactive")
            if sidecars.status(h) != "ready":
                pending.append(kind.split(".")[1])
                est = max(est, sidecars.estimate_s(h))
        if not pending:
            return ""
        return (f"Measuring {', '.join(dict.fromkeys(pending))} (≈"
                f"{max(1, int(est))} s): previews until then show the "
                "graphic where it was authored; finals wait up to 90 s.")
    except Exception:  # noqa: BLE001
        return ""


def _done(res, notes):
    notes = [n for n in notes if n]
    if res.startswith("REJECTED") or not notes:
        return res
    return res + "\nNOTE: " + " ".join(notes)


def _executor_refusal():
    try:
        import remote
        ok = remote.executor_supports("fx:object")
    except Exception:  # noqa: BLE001
        return None
    if ok is False:
        return ("REJECTED: the render service does not place graphics in "
                "the world yet (it is on an older version); try again "
                "after it updates.")
    return None


def _motion(edl, motion_id):
    for m in edl.get("motion") or []:
        if m.get("id") == motion_id:
            return m
    return None


def _anchor_op(motion_id, anchor):
    return {"action": "upsert", "layer": "motion", "id": str(motion_id),
            "value": {"id": str(motion_id), "anchor": anchor}}


def _window(edl, start, end, around=None, default_len=3.0):
    tl = _tl(edl)
    prog = float(tl.out_duration)
    if start is None and around is not None:
        start = max(0.0, float(around) - 0.5)
    s = float(start if start is not None else 0.0)
    e = float(end) if end is not None else s + default_len
    s = round(min(max(s, 0.0), max(0.0, prog - 0.2)), 2)
    e = round(min(max(e, s + 0.2), prog), 2)
    return s, e


# ── verbs ───────────────────────────────────────────────────────────────

def attach(ctx, motion_id, to=None, which=None, pin=None, offset=None,
           follow=None, occluders=None, lock=None, lost=None, smooth=None,
           place=None, shadow=None, clear=False):
    """Attach a motion graphic to the world (or detach it)."""
    refusal = _executor_refusal()
    if refusal:
        return refusal
    edl = _latest(ctx)
    m = _motion(edl, motion_id)
    if m is None:
        have = ", ".join(str(x.get("id")) for x in edl.get("motion") or [])
        return (f"REJECTED: no motion graphic '{motion_id}'. Existing: "
                f"{have or 'none'}.")
    if clear:
        if m.get("anchor") is None:
            return f"NO CHANGE: motion '{motion_id}' is not attached."
        return _write.commit(ctx, [_anchor_op(motion_id, None)],
                             f"motion [{motion_id}] detached")
    if not to:
        return "REJECTED: say what to attach it to (to=...), or clear=true."
    anchor = dict(m.get("anchor") or {})
    anchor["to"] = str(to).strip()
    try:
        given = {"which": which, "pin": _num_list(pin, 2),
                 "offset": _num_list(offset, 2), "lock": lock, "lost": lost,
                 "smooth": None if smooth is None else float(smooth),
                 "place": _num_list(place, 4), "shadow": shadow}
        if follow is not None:
            given["follow"] = _ids(follow)
        if occluders is not None:
            given["occluders"] = _ids(occluders)
    except (TypeError, ValueError) as e:
        return f"REJECTED: {e}"
    if isinstance(given.get("which"), str) and given["which"].isdigit():
        given["which"] = int(given["which"])
    for k, v in given.items():
        if v is not None:
            anchor[k] = v
    if anchor["to"].startswith("hand.") and "lost" not in anchor:
        anchor["lost"] = "hide"
    res = _write.commit(ctx, [_anchor_op(motion_id, anchor)],
                        f"motion [{motion_id}] attached to {anchor['to']}")
    if res.startswith("REJECTED"):
        return res
    item = dict(m, anchor=anchor)
    return _done(res, [_measuring(ctx, edl, landmark_items=[item])])


def track_point(ctx, t, x, y, start=None, end=None, label=None, id=None,
                radius=None):
    """A point followed through its shot (an fx track definition)."""
    refusal = _executor_refusal()
    if refusal:
        return refusal
    edl = _latest(ctx)
    try:
        src_t, [[sx, sy]] = to_source(ctx, edl, float(t),
                                      [[float(x), float(y)]])
    except (TypeError, ValueError) as e:
        return f"REJECTED: {e}"
    s, e = _window(edl, start, end, around=t)
    fx = edl.get("fx") or []
    fid = str(id) if id and _ID_RE.match(str(id)) else _next_id(fx, "tr")
    item = {"id": fid, "type": "track", "stage": "world", "start": s,
            "end": e, "seed": {"src_t": src_t, "x": sx, "y": sy},
            "device": "anchor_track"}
    if label:
        item["label"] = str(label)[:60]
    if radius is not None:
        item["radius"] = float(radius)
    res = _write.commit(ctx, [_write.upsert_fx(item)],
                        f"track {fid} at ({float(x):.2f}, {float(y):.2f}) "
                        f"{s:g}-{e:g}s")
    if res.startswith("REJECTED"):
        return res
    return _done(res, [f"Attach graphics with to='track:{fid}'.",
                       _measuring(ctx, edl, defs=[item])])


def select_object(ctx, t, box=None, points=None, start=None, end=None,
                  label=None, depth=None, id=None):
    """An object segmented through its window (an fx object definition)."""
    refusal = _executor_refusal()
    if refusal:
        return refusal
    edl = _latest(ctx)
    try:
        prompt = _object_prompt(ctx, edl, t, box, points)
    except (TypeError, ValueError) as e:
        return f"REJECTED: {e}"
    s, e = _window(edl, start, end, around=t, default_len=4.0)
    if e - s > 6.0:
        e = round(s + 6.0, 2)
    fx = edl.get("fx") or []
    fid = str(id) if id and _ID_RE.match(str(id)) else _next_id(fx, "ob")
    item = {"id": fid, "type": "object", "stage": "world", "start": s,
            "end": e, "prompt": prompt, "device": "object_occluder"}
    if label:
        item["label"] = str(label)[:60]
    if depth:
        item["depth"] = depth
    res = _write.commit(ctx, [_write.upsert_fx(item)],
                        f"object {fid}{' ' + label if label else ''} "
                        f"{s:g}-{e:g}s")
    if res.startswith("REJECTED"):
        return res
    return _done(res, [f"Name it as an occluder (object:{fid}) or attach "
                       f"to it (to='object:{fid}').",
                       _measuring(ctx, edl, defs=[item])])


def _object_prompt(ctx, edl, t, box, points):
    box = _num_list(box, 4) if box is not None else None
    pts = _num_list(points) if points is not None else None
    if box is None and not pts:
        raise ValueError("give the object's box [x0, y0, x1, y1] (program "
                         "fractions from look_at) or points [[x, y], ...]")
    corners = []
    if box is not None:
        corners += [[box[0], box[1]], [box[2], box[3]]]
    for p in pts or []:
        corners.append([p[0], p[1]])
    src_t, mapped = to_source(ctx, edl, float(t), corners)
    prompt = {"src_t": src_t}
    k = 0
    if box is not None:
        (x0, y0), (x1, y1) = mapped[0], mapped[1]
        prompt["box"] = [x0, y0, x1, y1]
        k = 2
    if pts:
        prompt["points"] = [[mapped[k + i][0], mapped[k + i][1],
                             int(p[2]) if len(p) > 2 else 1]
                            for i, p in enumerate(pts)]
    return prompt


def pin_to_plane(ctx, motion_id=None, text=None, plane=None, quad=None,
                 t=None, surface=None, start=None, end=None, place=None,
                 shadow=None, occluders=None, blend=None, id=None):
    """Lay a graphic (or new words) on a surface."""
    refusal = _executor_refusal()
    if refusal:
        return refusal
    edl = _latest(ctx)
    ops, defs = [], []
    fx = edl.get("fx") or []
    if plane:
        pid = str(plane).split(":", 1)[-1]
        if not any(i.get("id") == pid and i.get("type") == "plane"
                   for i in fx):
            have = [i.get("id") for i in fx if i.get("type") == "plane"]
            return (f"REJECTED: no plane '{pid}'. Planes here: "
                    f"{', '.join(have) or 'none'} (or give quad + t).")
    else:
        if quad is None or t is None:
            return ("REJECTED: give plane=<id>, or the surface's quad (4 "
                    "corners TL, TR, BR, BL in program fractions from "
                    "look_at) and its time t.")
        try:
            q = _num_list(quad)
            if len(q) == 8 and not isinstance(q[0], list):
                q = [q[i:i + 2] for i in range(0, 8, 2)]
            src_t, mapped = to_source(ctx, edl, float(t), q)
        except (TypeError, ValueError) as e:
            return f"REJECTED: {e}"
        s, e = _window(edl, start, end, around=t)
        pid = str(id) if id and _ID_RE.match(str(id)) else \
            _next_id(fx, "pl")
        d = {"id": pid, "type": "plane", "stage": "world", "start": s,
             "end": e, "src_t": src_t, "quad": mapped,
             "surface": surface or "other", "device": "plane_type"}
        ops.append(_write.upsert_fx(d))
        defs.append(d)
    items = edl.get("motion") or []
    if motion_id:
        m = _motion(edl, motion_id)
        if m is None:
            return f"REJECTED: no motion graphic '{motion_id}'."
        mid = motion_id
    elif text:
        mid = _next_id(items, "wt")
        s, e = _window(edl, start, end, around=t)
        m = {"id": mid, "template": "word_slam", "start": s, "end": e,
             "params": {"text": str(text)[:40], "entrance": "rise",
                        "role": "condensed", "width": 0.8,
                        "exit": "fade"},
             "layer": "below_captions"}
        ops.append({"action": "upsert", "layer": "motion", "id": mid,
                    "value": m})
    else:
        return "REJECTED: give motion_id (a graphic) or text (new words)."
    anchor = {"to": f"plane:{pid}"}
    try:
        if place is not None:
            anchor["place"] = _num_list(place, 4)
        if occluders is not None:
            anchor["occluders"] = _ids(occluders)
    except (TypeError, ValueError) as e:
        return f"REJECTED: {e}"
    if shadow is not None:
        anchor["shadow"] = bool(shadow)
    ops.append(_anchor_op(mid, anchor))
    if blend:
        ops.append(_write.set_composite(mid, dict(m.get("composite") or {},
                                                  blend=blend)))
    res = _write.commit(ctx, ops, f"motion [{mid}] on plane {pid}")
    if res.startswith("REJECTED"):
        return res
    return _done(res, [_measuring(ctx, edl, defs=defs)])


def place_in_world(ctx, where, motion_id=None, text=None, t=None, box=None,
                   quad=None, start=None, end=None):
    """The attachment language in one call."""
    import attachment
    refusal = _executor_refusal()
    if refusal:
        return refusal
    parsed = attachment.parse(where)
    if parsed is None:
        return (f"REJECTED: could not read where {where!r} is. Say 'behind "
                "the <object>', 'on the desk/wall/floor/screen', 'follow "
                "his hand', 'on her fingertip', 'between his hands', 'round "
                "his head', 'next to his face' or 'on his chest'.")
    edl = _latest(ctx)
    tgt = parsed["target"]
    if tgt == "plane":
        return pin_to_plane(ctx, motion_id=motion_id, text=text, quad=quad,
                            t=t, surface=parsed.get("surface"), start=start,
                            end=end, blend="multiply"
                            if parsed.get("surface") in ("wall", "screen",
                                                         "desk", "table",
                                                         "floor") else None)
    items = edl.get("motion") or []
    ops = []
    if motion_id:
        m = _motion(edl, motion_id)
        if m is None:
            return f"REJECTED: no motion graphic '{motion_id}'."
        mid = motion_id
    elif text:
        mid = _next_id(items, "ww")
        s, e = _window(edl, start, end, around=t)
        if tgt == "head" and parsed["relation"] == "around":
            m = {"id": mid, "template": "el_orbit", "start": s, "end": e,
                 "params": {"text": str(text)[:60]},
                 "layer": "below_captions"}
        else:
            m = {"id": mid, "template": "word_slam", "start": s, "end": e,
                 "params": {"text": str(text)[:40], "entrance": "rise",
                            "role": "condensed", "width": 0.6,
                            "exit": "fade"},
                 "layer": "above_captions"}
        ops.append({"action": "upsert", "layer": "motion", "id": mid,
                    "value": m})
    else:
        return "REJECTED: give motion_id (a graphic) or text (new words)."
    defs = []
    extra_note = None
    if tgt == "object":
        if box is None or t is None:
            return (f"REJECTED: to hide it behind the {parsed.get('name')}, "
                    "give the object's box (program fractions from look_at) "
                    "and the time t you looked at.")
        try:
            prompt = _object_prompt(ctx, edl, t, box, None)
        except (TypeError, ValueError) as e:
            return f"REJECTED: {e}"
        fx = edl.get("fx") or []
        oid = _next_id(fx, "ob")
        s, e = float(m["start"]), float(m["end"])
        d = {"id": oid, "type": "object", "stage": "world",
             "start": round(max(0.0, s - 0.3), 2),
             "end": round(min(_tl(edl).out_duration, max(s + 0.5, e)), 2),
             "prompt": prompt, "label": parsed.get("name"),
             "device": "object_occluder"}
        if d["end"] - d["start"] > 6.0:
            d["end"] = round(d["start"] + 6.0, 2)
        ops.insert(0, _write.upsert_fx(d))
        defs.append(d)
        if parsed["relation"] == "behind":
            anchor = {"to": "screen", "occluders": [f"object:{oid}"]}
            try:
                bx = _num_list(box, 4)
                W, H = _frame_size(edl, ctx)
                where_p = attachment.behind_params(bx, W, H)
            except (TypeError, ValueError):
                where_p = None
            if where_p:
                # seated at render on the object's own edge (its measured
                # mask), so the object really cuts the letters
                anchor["pin"] = attachment.behind_pin(bx, W, H)
            if where_p and not motion_id and \
                    m.get("template") == "word_slam":
                # the word where the object can hide it (its lower part
                # behind a wide object's top edge; a tall one crossing it)
                m["params"].update(where_p)
            elif where_p and motion_id:
                p = (m.get("params") or {})
                y = p.get("y")
                if y is None or abs(float(y) - where_p["y"]) > 0.08:
                    extra_note = (
                        f"'{motion_id}' sits at y={y if y is not None else 'its default'}: "
                        f"for the {parsed.get('name')} to hide part of it, "
                        f"set its params y≈{where_p['y']}, x≈{where_p['x']}.")
        else:
            anchor = {"to": f"object:{oid}", "follow": ["position"]}
    else:
        anchor = attachment.anchor_for(parsed)
        if anchor is None:
            return f"REJECTED: nothing to attach to in {where!r}."
    ops.append(_anchor_op(mid, anchor))
    res = _write.commit(ctx, ops, f"motion [{mid}] {parsed['relation']} "
                        f"{parsed.get('name') or tgt}")
    if res.startswith("REJECTED"):
        return res
    item = dict(m, anchor=anchor)
    lm = [item] if str(anchor["to"]).split(".")[0] in _LANDMARK_KIND else []
    return _done(res, [extra_note,
                       _measuring(ctx, edl, defs=defs, landmark_items=lm)])


def remove_world(ctx, ids):
    """Remove definitions (detaching what names them) or detach graphics."""
    edl = _latest(ctx)
    want = _ids(ids)
    fx = {i.get("id"): i for i in edl.get("fx") or []}
    motion = {m.get("id"): m for m in edl.get("motion") or []}
    ops, done = [], []
    for i in want:
        if (fx.get(i) or {}).get("type") in ("track", "object", "plane",
                                             "light"):
            for m in motion.values():
                a = m.get("anchor") or {}
                refs = [a.get("to")] + list(a.get("occluders") or [])
                if any(str(r).endswith(f":{i}") for r in refs if r):
                    occ = [o for o in a.get("occluders") or []
                           if o != f"object:{i}"]
                    if str(a.get("to", "")).endswith(f":{i}"):
                        ops.append(_anchor_op(m["id"], None))
                    else:
                        ops.append(_anchor_op(m["id"],
                                              dict(a, occluders=occ)))
            ops.append(_write.remove_fx(i))
            done.append(i)
        elif i in motion and motion[i].get("anchor") is not None:
            ops.append(_anchor_op(i, None))
            done.append(i)
    if not want or len(done) != len(want):
        bad = [i for i in want if i not in done]
        return (f"REJECTED: not world items or attached graphics: "
                f"{', '.join(bad) or '(none)'}.")
    return _write.commit(ctx, ops, f"removed/detached {', '.join(done)}")


def look_at_world(ctx, times=None, output_times=None, start=None, end=None):
    """READ: look_at with the measured anchors drawn (faces, hands, pose,
    every track/object/plane definition) — the world's own eyes. It is
    look_at(overlay='anchors'), reachable where the core enum is fixed."""
    import agent_tools
    return agent_tools.look_at(ctx, times=times, output_times=output_times,
                               start=start, end=end, overlay="anchors")


# ── catalog ──────────────────────────────────────────────────────────────

_TO = ("face, face.<eye_l|eye_r|iris_l|iris_r|nose|mouth|chin|forehead|"
       "ear_l|ear_r>, head, hand.<index_tip|thumb_tip|palm|wrist|pinch|"
       "frame>, pose.<chest|shoulder_l|...>, track:<id>, object:<id>, "
       "plane:<id>, screen")

TOOL_SPECS = {
    "attach": (
        attach,
        "Attach a motion graphic to the world (MotionItem.anchor): it rides "
        "a face part, the head, a fingertip, both hands' frame, a joint, a "
        "tracked point, an object or a plane. pin = the graphic's own point "
        "set on the anchor ([0.5,1] = bottom centre); follow adds scale / "
        "rotation; occluders = what passes in front (person, hands, "
        "object:<id>); lost = hold|hide|static. clear=true detaches.",
        {"motion_id": {"type": "string"},
         "to": {"type": "string", "description": _TO},
         "which": {"type": "string",
                   "description": "any|left|right (on screen)|0..7"},
         "pin": {"type": "array", "items": {"type": "number"}},
         "offset": {"type": "array", "items": {"type": "number"}},
         "follow": {"type": "array", "items": {"type": "string", "enum": [
             "position", "scale", "rotation"]}},
         "occluders": {"type": "array", "items": {"type": "string"}},
         "lock": {"type": "string", "enum": ["world", "screen"]},
         "lost": {"type": "string", "enum": ["hold", "hide", "static"]},
         "smooth": {"type": "number"},
         "place": {"type": "array", "items": {"type": "number"}},
         "shadow": {"type": "boolean"},
         "clear": {"type": "boolean"}}),
    "track_point": (
        track_point,
        "Follow a point through its shot (fx track): t and x, y in program "
        "fractions from look_at. Attach graphics with to='track:<id>' "
        "(arrows, doodles, labels on a moving thing).",
        {"t": {"type": "number"}, "x": {"type": "number"},
         "y": {"type": "number"}, "start": {"type": "number"},
         "end": {"type": "number"}, "label": {"type": "string"},
         "radius": {"type": "number"}, "id": {"type": "string"}}),
    "select_object": (
        select_object,
        "Segment an object through a window (fx object, <=6 s): its box "
        "(or points) at t, program fractions from look_at. Type hides "
        "behind it (occluders object:<id>) or rides it (to='object:<id>').",
        {"t": {"type": "number"},
         "box": {"type": "array", "items": {"type": "number"}},
         "points": {"type": "array", "items": {"type": "array",
                                               "items": {"type": "number"}}},
         "start": {"type": "number"}, "end": {"type": "number"},
         "label": {"type": "string"},
         "depth": {"type": "string", "enum": ["auto", "front", "behind"]},
         "id": {"type": "string"}}),
    "pin_to_plane": (
        pin_to_plane,
        "Lay a graphic (motion_id) or new words (text) on a surface: an "
        "existing plane, or quad (4 corners TL,TR,BR,BL along the surface, "
        "program fractions) at t. The person in front occludes it "
        "(occluders=[] draws over them); shadow=true lets real shadows darken it; "
        "blend=multiply prints it into the surface.",
        {"motion_id": {"type": "string"}, "text": {"type": "string"},
         "plane": {"type": "string"},
         "quad": {"type": "array", "items": {"type": "array",
                                             "items": {"type": "number"}}},
         "t": {"type": "number"},
         "surface": {"type": "string", "enum": [
             "floor", "wall", "desk", "table", "screen", "ceiling",
             "other"]},
         "start": {"type": "number"}, "end": {"type": "number"},
         "place": {"type": "array", "items": {"type": "number"}},
         "shadow": {"type": "boolean"},
         "occluders": {"type": "array", "items": {"type": "string"}},
         "blend": {"type": "string"}, "id": {"type": "string"}}),
    "place_in_world": (
        place_in_world,
        "Say where in words: 'behind the laptop' (give box + t), 'on the "
        "desk' (give quad + t), 'follow her hand', 'on his fingertip', "
        "'between his hands', 'round his head', 'next to his face', 'on "
        "his chest'. Places motion_id or new text, writing whatever it "
        "needs in one batch.",
        {"where": {"type": "string"}, "motion_id": {"type": "string"},
         "text": {"type": "string"}, "t": {"type": "number"},
         "box": {"type": "array", "items": {"type": "number"}},
         "quad": {"type": "array", "items": {"type": "array",
                                             "items": {"type": "number"}}},
         "start": {"type": "number"}, "end": {"type": "number"}}),
    "look_at_world": (
        look_at_world,
        "READ: frames (source times, or output_times of the edit) with the "
        "measured anchors drawn: face mesh, hands, pose and every track/"
        "object/plane definition. Check a hand is up, an object's outline, "
        "a quad, before and after a world write.",
        {"times": {"type": "array", "items": {"type": "number"}},
         "output_times": {"type": "array", "items": {"type": "number"}},
         "start": {"type": "number"}, "end": {"type": "number"}}),
    "remove_world": (
        remove_world,
        "Remove world definitions (track/object/plane/light ids; graphics "
        "that name them are detached) or detach graphics (motion ids).",
        {"ids": {"type": "array", "items": {"type": "string"}}}),
}

REQUIRED_ARGS = {
    "attach": ["motion_id"],
    "track_point": ["t", "x", "y"],
    "select_object": ["t"],
    "pin_to_plane": [],
    "place_in_world": ["where"],
    "remove_world": ["ids"],
}

WRITE_TOOLS = set(VERBS) - {"look_at_world"}

COMPACT = {
    "attach": (
        "Attach a graphic to the world: to=face.<part>|head|hand.<part>|"
        "pose.<joint>|track:<id>|object:<id>|plane:<id>|screen; pin, "
        "offset, follow scale/rotation, occluders (person, hands, "
        "object:<id>), lost hold|hide|static; clear=true detaches."),
    "track_point": "Follow a point (t, x, y program fractions) -> track:<id>.",
    "select_object": (
        "Segment an object (box or points at t, program fractions) -> "
        "object:<id>, an occluder or an anchor; <=6 s."),
    "pin_to_plane": (
        "Graphic or text on a surface: plane=<id> or quad+t (TL,TR,BR,BL); "
        "shadow, occluders, blend=multiply."),
    "place_in_world": (
        "Where in words: behind the <object> (box+t), on the desk/wall/"
        "floor/screen (quad+t), follow her hand, on his fingertip, between "
        "his hands, round his head, next to his face."),
    "remove_world": "Remove world definitions or detach graphics (ids).",
    "look_at_world": (
        "READ: look_at with measured anchors drawn (face mesh, hands, pose, "
        "tracks, objects, planes)."),
}
