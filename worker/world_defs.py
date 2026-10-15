"""World definitions: the ``track``, ``object`` and ``plane`` fx items (Wave 3).

ROADMAP Wave 3 / 3A-3C; contract in devices/INTERFACES_W3.md §3, §5.

A definition draws nothing. This module is the ONE place that turns a
definition into the sidecar request that measures it (the keys must be the
same whoever asks: the verb that writes it, the render that reads it, the
planner that checks it), and it serves the fx graph:

  COMPILER          prepare() reports whether the definition is measured
                    (rung full) or not yet (rung substitute, "measuring");
                    stage() emits nothing — md5 identity for the picture
  object_mask       mask provider ``object:<fx id>``: the object's
                    program-space mask over its window, precomputed in
                    prepare as a gray clip and bound as a plan input
  plane_mask        ``plane:<fx id>``: the plane quad, filled, per frame

Every lane imports this (no cv2 at module level).
"""

import json
import math
import os

OBJECT_FPS = 30.0
WORK_W = 960


def _r2(v):
    return round(float(v), 2)


def src_window(item, tl, pad=0.0):
    """[a, b] SOURCE seconds the definition's program window plays (the
    widest span when it crosses a cut: min / max), or None."""
    if tl is None:
        return None
    s, e = float(item["start"]), float(item["end"])
    n = max(2, int(math.ceil((e - s) / 0.1)) + 1)
    pts = []
    for k in range(n):
        v = tl.out_to_src(min(e - 0.01, s + (e - s) * k / (n - 1)))
        if v is not None:
            pts.append(float(v))
    if not pts:
        return None
    a, b = min(pts), max(pts) + 0.02
    return [_r2(max(0.0, a - pad)), _r2(b + pad)]


def seed_time(item):
    t = item.get("type")
    if t == "track":
        return float(item["seed"]["src_t"])
    if t == "object":
        return float(item["prompt"]["src_t"])
    if t == "plane":
        return float(item["src_t"])
    return None


def sidecar_t(trim_t, clock_offset=0.0):
    return float(trim_t) - float(clock_offset or 0.0)


def request(item, tl, clock_offset=0.0):
    """(kind, ranges, params) that measure ``item`` (None for a definition
    that needs no sidecar: a static plane). Times on the SIDECAR clock."""
    win = src_window(item, tl)
    if win is None:
        return None
    off = float(clock_offset or 0.0)
    st = seed_time(item)
    a = min(win[0], st) - off
    b = max(win[1], st + 0.05) - off
    rng = [(max(0.0, _r2(a)), _r2(b))]
    typ = item.get("type")
    if typ == "track":
        s = item["seed"]
        if s.get("box"):
            x = (s["box"][0] + s["box"][2]) / 2.0
            y = (s["box"][1] + s["box"][3]) / 2.0
        else:
            x, y = s["x"], s["y"]
        params = {"seeds": [{"id": item["id"], "src_t": _r2(st - off),
                             "x": round(float(x), 4), "y": round(float(y), 4),
                             "radius_px": round(float(item.get("radius")
                                                      or 0.012) * WORK_W, 1)
                             }]}
        return "track.points", rng, params
    if typ == "object":
        p = item["prompt"]
        prompt = {"src_t": _r2(st - off)}
        if p.get("box"):
            prompt["box"] = [round(float(v), 4) for v in p["box"]]
        if p.get("points"):
            prompt["points"] = [[round(float(q[0]), 4), round(float(q[1]), 4),
                                 int(q[2]) if len(q) > 2 else 1]
                                for q in p["points"]]
        params = {"prompt": prompt,
                  "window": [max(0.0, _r2(a)), _r2(b)]}
        return "objects.sam2", rng, params
    if typ == "plane":
        if item.get("motion") == "static":
            return None
        params = {"quad": [[round(float(x), 4), round(float(y), 4)]
                           for x, y in item["quad"]],
                  "src_t": _r2(st - off),
                  "window": [max(0.0, _r2(a)), _r2(b)]}
        return "planes.h", rng, params
    return None


def ensure(item, tl, source, clock_offset=0.0, priority="interactive"):
    """Launch the definition's measurement (a write, never a wait). Returns
    (status, estimate seconds) or (None, 0) when nothing is needed."""
    req = request(item, tl, clock_offset)
    if req is None or source is None:
        return None, 0.0
    kind, ranges, params = req
    try:
        import sidecars
        h = sidecars.ensure(kind, source, ranges, priority=priority,
                            params=params)
        return sidecars.status(h), sidecars.estimate_s(h)
    except Exception as e:  # noqa: BLE001 — a write never fails on analysis
        print(f"[world] {item.get('id')}: {kind} not launched "
              f"({str(e)[:120]})", flush=True)
        return "absent", 0.0


def status(item, tl, source, clock_offset=0.0):
    """ready | partial | absent | static (no sidecar needed) for one
    definition: a status read, never a launch."""
    req = request(item, tl, clock_offset)
    if req is None:
        return "static"
    if source is None:
        return "absent"
    kind, ranges, params = req
    try:
        import sidecars
        spans = sidecars.chunks_for(source.shots, source.duration_s, ranges)
        ready = sidecars.ready_chunks(kind, source.sha256, source.shots,
                                      source.duration_s, ranges, params)
    except Exception:  # noqa: BLE001
        return "absent"
    if spans and len(ready) == len(spans):
        return "ready"
    return "partial" if ready else "absent"


def definitions(edl):
    """{fx id: item} of the EDL's world definitions."""
    return {it.get("id"): it for it in (edl or {}).get("fx") or []
            if isinstance(it, dict)
            and it.get("type") in ("track", "object", "plane")}


# ── the fx graph side ────────────────────────────────────────────────────

class _Defs:
    """Compiler for definitions: a status report, no filters."""

    FILTERS = ()
    KEEPS_FRAME_TAGS = True

    def prepare(self, plan, item):
        tl = getattr(plan, "tl", None)
        src = getattr(plan, "source", None)
        off = getattr(getattr(plan, "pm", None), "clock_offset", 0.0) or 0.0
        st = status(item, tl, src, off)
        if st in ("ready", "static"):
            if item.get("type") == "object":
                _precompute_object(plan, item, off)
            return None
        what = {"track": "point track", "object": "object mask",
                "plane": "plane track"}.get(item.get("type"), "measurement")
        return ("substitute", f"its {what} is not measured yet "
                f"({st}); devices that use it draw their fallback")

    def stage(self, ctx, items):
        return ctx.label

    def cost_ms(self, plan, item):
        return 0.0

    def landing(self, plan, item):
        return []


COMPILER = _Defs()


# ── object masks in program space (``object:<fx id>``) ───────────────────

def _precompute_object(plan, item, clock_offset):
    """Render the object's program-space mask over its window into a gray
    clip and bind it as a plan input; the mask provider hands it out."""
    if not plan.workdir or not plan.W or not plan.H:
        return
    try:
        import world_layer
        path = world_layer.object_mask_clip(plan, item, clock_offset)
    except Exception as e:  # noqa: BLE001 — absent mask: consumers degrade
        print(f"[world] object {item.get('id')} mask not built "
              f"({str(e)[:160]})", flush=True)
        return
    if path:
        plan.add_input(f"objmask:{item['id']}", ["-i", path])
        if not hasattr(plan, "_w3_objects"):
            plan._w3_objects = {}
        plan._w3_objects[item["id"]] = item


def object_mask(plan, name, space, ctx):
    """Mask provider for ``object:<fx id>``: a fresh gray pad over the whole
    program (black outside the object's window), or None."""
    fid = name.split(":", 1)[1] if ":" in name else None
    items = getattr(plan, "_w3_objects", None) or {}
    it = items.get(fid)
    lab = plan.input_label(f"objmask:{fid}") if it else None
    if lab is None or ctx is None:
        return None
    if space == "screen" and ((getattr(plan, "edl", None) or {}).get(
            "effects") or {}).get("zooms"):
        # the clip is WORLD space (before the camera); under a camera move
        # a screen-space consumer would read it misaligned: absent instead
        return None
    import fxprims
    f0, f1 = fxprims.frame_span(it["start"], it["end"], plan.fps)
    u = plan.uid("objm")
    ctx.parts.append(
        f"[{lab}]format=gray,setpts=PTS-STARTPTS,"
        f"tpad=start={f0}:stop={max(0, plan.n_frames - f1) + 2}:"
        f"start_mode=add:stop_mode=add:color=black,"
        f"trim=end_frame={plan.n_frames},setpts=PTS-STARTPTS[{u}]")
    return u


def plane_mask(plan, name, space, ctx):
    """``plane:<fx id>``: not served to fx compilers in Wave 3 (the world
    compositor reads planes directly); absent = the consumer's substitute."""
    return None


def hands_mask(plan, name, space, ctx):
    return None


def describe_request(item, tl, clock_offset=0.0):
    req = request(item, tl, clock_offset)
    if req is None:
        return "static"
    kind, ranges, params = req
    return json.dumps({"kind": kind, "ranges": ranges, "params": params},
                      sort_keys=True)

