"""Department ``world``: procedural elements and light (Wave 3, 3D).

  add_element   one family verb, ``kind``:
                  light_trail | light_dissolve | light_rim   (fx ``light`` on
                    the person matte)
                  embers | sparks | dust | confetti | snow | fireworks
                    (el_particles; emit from the subject's edge, a point, a
                    box or the frame; composited as light)
                  lightning                     (el_lightning, strikes on
                    ``at``)
                  arrow | underline | circle | scribble | burst | star |
                  check | cross | bracket | spiral   (el_doodle, boiling)
                  orbit                         (el_orbit: words on a ring)
                  shapes | spiral_grid         (el_shapes)
                  pictogram                     (el_pictogram: a runner from
                    the body's pose, or a library cycle)
                  sprites                       (el_sprites: cutouts in a
                    ring / row / swarm, popping and collapsing)
                ``attach`` rides it on the world (face, head, hand, track)
                in the same write. ``dissolve_embers`` true on
                light_dissolve also throws embers off the figure's edge.

Writes SOLVE into apply_edit_batch operations (motion items with their
composite, fx light items) and commit through tools_ext._write.commit.
Browserless: nothing renders here.
"""

import json
import re

from tools_ext import _write

DEPARTMENT = "world"
VERBS = ("add_element",)

PARTICLES = ("embers", "sparks", "dust", "confetti", "snow", "fireworks")
DOODLES = ("arrow", "underline", "circle", "scribble", "burst", "star",
           "check", "cross", "bracket", "spiral")
LIGHTS = ("light_trail", "light_dissolve", "light_rim")
KINDS = LIGHTS + PARTICLES + DOODLES + ("lightning", "orbit", "shapes",
                                        "pictogram", "sprites")
CARD = {**{k: "element_particles" for k in PARTICLES},
        **{k: "element_doodle" for k in DOODLES},
        "lightning": "element_lightning", "orbit": "element_orbit",
        "shapes": "element_shapes", "pictogram": "element_pictogram",
        "sprites": "element_sprites",
        "light_trail": "light_trail", "light_dissolve": "light_dissolve",
        "light_rim": "light_rim"}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _latest(ctx):
    return json.loads(json.dumps(ctx.latest_edl()["json"]))


def _program_s(edl):
    from timeline import Timeline
    return float(Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                          edl.get("speed") or []).out_duration)


def _next_id(items, prefix):
    have = {str(i.get("id")) for i in items or []}
    k = 1
    while f"{prefix}{k}" in have:
        k += 1
    return f"{prefix}{k}"


def _nums(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = json.loads(v) if v.strip().startswith("[") else \
            [x for x in re.split(r"[\s,]+", v) if x]
    return [float(x) for x in v]


def add_element(ctx, kind, start, end=None, text=None, at=None, x=None,
                y=None, box=None, colour=None, palette=None, count=None,
                emit=None, intensity=None, attach=None, style=None,
                images=None, dissolve_embers=None, id=None):
    """One procedural element or light device."""
    if kind not in KINDS:
        return f"REJECTED: kind is one of {', '.join(KINDS)}."
    edl = _latest(ctx)
    prog = _program_s(edl)
    try:
        s = round(min(max(float(start), 0.0), max(0.0, prog - 0.2)), 2)
        default = {"light_dissolve": 2.0, "lightning": 1.0}.get(kind, 2.5)
        e = round(min(max(float(end) if end is not None else s + default,
                          s + 0.2), prog), 2)
        times = [round(float(v) - s, 3) for v in _nums(at) or []]
        bx = _nums(box)
    except (TypeError, ValueError) as err:
        return f"REJECTED: {err}"
    ops = []
    if kind in LIGHTS:
        fx = edl.get("fx") or []
        fid = str(id) if id and _ID_RE.match(str(id)) else _next_id(fx, "lt")
        item = {"id": fid, "type": "light", "stage": "world", "start": s,
                "end": e, "kind": kind.split("_", 1)[1],
                "device": CARD[kind]}
        if colour:
            item["colour"] = colour
        if intensity is not None:
            item["intensity"] = float(intensity)
        if kind == "light_dissolve":
            item["duration_s"] = round(min(10.0, max(0.2, e - s)), 2)
        ops.append(_write.upsert_fx(item))
        summary = f"{kind} {s:g}-{e:g}s [{fid}]"
        if kind == "light_dissolve" and dissolve_embers is not False:
            mid = _next_id(edl.get("motion") or [], "em")
            ops.append(_motion_op(mid, "el_particles", s, min(prog, e + 0.8),
                                  {"kind": "embers", "emit": "edge",
                                   "count": int(count or 420),
                                   "palette": palette or "warm",
                                   "seed": 7}, "screen"))
            summary += f" + embers off the figure [{mid}]"
        return _write.commit(ctx, ops, summary)
    items = edl.get("motion") or []
    mid = str(id) if id and _ID_RE.match(str(id)) else _next_id(items, "el")
    params, tpl, blend = {}, None, None
    if kind in PARTICLES:
        tpl, blend = "el_particles", "screen" if kind != "snow" else None
        params = {"kind": kind, "emit": emit or ("box" if bx else "point")}
        if count:
            params["count"] = int(count)
        if palette:
            params["palette"] = palette
        if times:
            params["at"] = [str(v) for v in times]
    elif kind == "lightning":
        tpl, blend = "el_lightning", "screen"
        if bx:
            params.update(x0=bx[0], y0=bx[1], x1=bx[2], y1=bx[3])
        params["at"] = [str(v) for v in (times or [0.0])]
        if colour:
            params["colour"] = colour
    elif kind in DOODLES:
        tpl = "el_doodle"
        params = {"kind": kind}
        if colour:
            params["colour"] = colour
    elif kind == "orbit":
        if not text:
            return "REJECTED: an orbit needs text."
        tpl = "el_orbit"
        params = {"text": str(text)[:60]}
        if colour:
            params["colour"] = colour
    elif kind == "shapes":
        tpl, blend = "el_shapes", None
        params = {"kind": style or "bounce"}
    elif kind == "pictogram":
        tpl = "el_pictogram"
        params = {"mode": style or "pose"}
        if colour:
            params["colour"] = colour
    elif kind == "sprites":
        imgs = [str(v) for v in (images or [])]
        if not imgs:
            return ("REJECTED: sprites need images (project image keys, "
                    "ideally cutouts).")
        tpl = "el_sprites"
        params = {"choreo": style or "ring"}
        params.update({f"img{i + 1}": k for i, k in enumerate(imgs[:8])})
    if bx and tpl in ("el_particles", "el_doodle"):
        params["box"] = [str(round(v, 4)) for v in bx]
    if x is not None:
        params["x"] = float(x)
    if y is not None:
        params["y"] = float(y)
    if intensity is not None and tpl == "el_particles":
        params["intensity"] = float(intensity)
    ops.append(_motion_op(mid, tpl, s, e, params, blend))
    anchor = _anchor(attach, kind)
    if anchor:
        ops.append({"action": "upsert", "layer": "motion", "id": mid,
                    "value": {"id": mid, "anchor": anchor}})
    return _write.commit(ctx, ops, f"{kind} ({tpl}) {s:g}-{e:g}s [{mid}]"
                         + (f" on {anchor['to']}" if anchor else ""))


def _motion_op(mid, tpl, s, e, params, blend):
    v = {"id": mid, "template": tpl, "start": s, "end": e, "params": params,
         "layer": "above_captions"}
    if blend:
        v["composite"] = {"blend": blend}
    return {"action": "upsert", "layer": "motion", "id": mid, "value": v}


def _anchor(attach, kind):
    if not attach:
        return None
    if isinstance(attach, dict):
        return attach
    a = str(attach).strip()
    if a.startswith("{"):
        return json.loads(a)
    import attachment
    if re.match(r"^(face|head|hand|pose|track|object|plane|screen)", a):
        out = {"to": a}
    else:
        out = attachment.anchor_for(attachment.parse(a))
    if out is None:
        return None
    if kind == "arrow":
        out.setdefault("pin", [1.0, 1.0])    # the arrow's head on the target
    if kind == "orbit" and out.get("to") == "head":
        out["follow"] = ["position", "scale"]
    return out


TOOL_SPECS = {
    "add_element": (
        add_element,
        "A procedural element or a light device, one family verb. "
        "light_dissolve (the figure turns to light, embers off its edge), "
        "light_trail, light_rim; embers/sparks/dust/confetti/snow/fireworks "
        "(emit edge|point|box|frame; at = burst times); lightning (box = "
        "from x0,y0 to x1,y1; at = strikes); doodles arrow/underline/"
        "circle/scribble/burst/star/check/cross/bracket/spiral; orbit "
        "(text on a ring); shapes; pictogram (from pose); sprites (images). "
        "attach rides it on the world ('head', 'hand.index_tip', "
        "'track:tr1', or words like 'round his head'). Light composites as "
        "light; nothing here adds a sound or a zoom.",
        {"kind": {"type": "string", "enum": list(KINDS)},
         "start": {"type": "number"}, "end": {"type": "number"},
         "text": {"type": "string"},
         "at": {"type": "array", "items": {"type": "number"}},
         "x": {"type": "number"}, "y": {"type": "number"},
         "box": {"type": "array", "items": {"type": "number"}},
         "colour": {"type": "string"}, "palette": {"type": "string"},
         "count": {"type": "integer"},
         "emit": {"type": "string", "enum": ["edge", "point", "box",
                                             "frame"]},
         "intensity": {"type": "number"},
         "attach": {"type": "string"},
         "style": {"type": "string"},
         "images": {"type": "array", "items": {"type": "string"}},
         "dissolve_embers": {"type": "boolean"},
         "id": {"type": "string"}}),
}
REQUIRED_ARGS = {"add_element": ["kind", "start"]}
WRITE_TOOLS = {"add_element"}
COMPACT = {
    "add_element": (
        "Procedural element or light: light_dissolve|light_trail|light_rim "
        "(person matte), embers/sparks/dust/confetti/snow/fireworks (emit "
        "edge|point|box|frame, at), lightning (box from->to, at), doodles "
        "(arrow, underline, circle, ...), orbit (text ring), shapes, "
        "pictogram, sprites; attach rides it on the world."),
}
