"""Department ``compositing``: track 1C's verbs (INTERFACES_W1.md §4).

How a graphic joins the picture, and effects bounded by a region:

  set_composite         blend / opacity / footage fill / field / ghost /
                        stage / clip region on an existing motion graphic
                        (``MotionItem.composite``); clear=true restores
                        today's plain composite
  add_type_window       giant letters that are windows: a ``type_window``
                        motion item with its composite fill in one write
  add_region_effect     an EDL.fx ``region`` item: one effect inside a rect,
                        ellipse, polygon (static or keyframed), a graphic's
                        glyphs, the face or the picture card
  remove_region_effect  removes region items by id

Every write verb SOLVES its arguments into apply_edit_batch operations and
commits them through ``tools_ext._write.commit`` (edit_batch.apply_batch),
so a verb and a stale MCP client's apply_edit_batch validate the very same
items identically: every authoring rule lives in fx_schemas/composite.py.
The skill (skills/compositing.md) lists the exact operations.

Browserless: nothing here renders or opens a browser; the agent, MCP and
Shorts lanes run every verb.
"""

import json
import re

from tools_ext import _write

DEPARTMENT = "compositing"

# Pinned verb names (INTERFACES_W1.md §4). Renaming one is a round-lead
# change: 1F's planner, the skills and the refbench agent cases name them.
VERBS = ("set_composite", "add_type_window", "add_region_effect",
         "remove_region_effect")

# card ids by region effect family
TONE_EFFECTS = ("negate", "solarise", "mono", "posterize", "threshold",
                "exposure", "tint")
SOFT_EFFECTS = ("blur", "pixelate")
MATERIAL_EFFECTS = ("ribbed_glass", "refraction", "heat")
REGION_CARD = {**{e: "region_tone" for e in TONE_EFFECTS},
               **{e: "region_soften" for e in SOFT_EFFECTS},
               **{e: "region_material" for e in MATERIAL_EFFECTS}}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_NUM = r"-?\d+(?:\.\d+)?"


# ── argument shorthands (a verb accepts the exact JSON too) ─────────────

def parse_source(value):
    """A fill as FxSource JSON from a dict, JSON text or a shorthand:
    'next' | 'previous' | 'program' | 'program:<offset_s>' | a colour
    ('black', 'white', 'accent', '#rrggbb') | 'source:<src_t>' |
    'asset:<key>' or a bare storage key ('media/...'; '+loop' loops it)."""
    if value is None or isinstance(value, dict):
        return value
    v = str(value).strip()
    if v.startswith("{"):
        return json.loads(v)
    low = v.lower()
    if low in ("next", "previous", "program"):
        return {"kind": low}
    if low in ("black", "white", "accent") or re.fullmatch(
            r"#[0-9a-f]{6}", low):
        return {"kind": "colour", "colour": low}
    if low.startswith("program:"):
        return {"kind": "program", "offset_s": float(v.split(":", 1)[1])}
    if low.startswith("source:"):
        return {"kind": "source", "src_t": float(v.split(":", 1)[1])}
    loop = v.endswith("+loop")
    key = v[:-5] if loop else v
    if key.lower().startswith("asset:"):
        key = key.split(":", 1)[1]
    out = {"kind": "asset", "asset_key": key.strip()}
    if loop:
        out["loop"] = True
    return out


def parse_region(value):
    """A region as FxRegion JSON from a dict, JSON text or a shorthand:
    'face' | 'face:<n>' | 'card' | 'glyph:<motion id>' |
    'rect:x0,y0,x1,y1' | 'ellipse:x0,y0,x1,y1' (frame fractions)."""
    if value is None or isinstance(value, dict):
        return value
    v = str(value).strip()
    if v.startswith("{"):
        return json.loads(v)
    low = v.lower()
    if low in ("face", "card"):
        return {"shape": low}
    if low.startswith("face:"):
        return {"shape": "face", "ref": v.split(":", 1)[1].strip()}
    if low.startswith("glyph:"):
        return {"shape": "glyph", "ref": v.split(":", 1)[1].strip()}
    m = re.fullmatch(rf"(rect|ellipse):\s*({_NUM})\s*,\s*({_NUM})\s*,\s*"
                     rf"({_NUM})\s*,\s*({_NUM})", low)
    if m:
        return {"shape": m.group(1),
                "box": [float(m.group(k)) for k in range(2, 6)]}
    raise ValueError(f"region {v!r}: use face, card, glyph:<motion id>, "
                     "rect:x0,y0,x1,y1, ellipse:x0,y0,x1,y1 or the FxRegion "
                     "JSON (polygon points, keys)")


# ── helpers ─────────────────────────────────────────────────────────────

def _latest(ctx):
    return json.loads(json.dumps(ctx.latest_edl()["json"]))


def _program_s(edl):
    import compositor
    try:
        return float(compositor._timeline(edl).out_duration)
    except Exception:  # noqa: BLE001
        return 0.0


def _executor_refusal():
    """A write is refused only when the render executor answers and lacks
    1C's types (version skew); unknown is permission (remote's rule)."""
    try:
        import remote
        ok = remote.executor_supports("fx:region")
    except Exception:  # noqa: BLE001
        return None
    if ok is False:
        return ("REJECTED: the render service does not draw composites yet "
                "(it is on an older version); try again after it updates.")
    return None


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


def _spec_composite(template):
    """The composite default a template's spec carries (the light
    templates: screen / add light), or None."""
    try:
        import motion_templates
        c = motion_templates.spec(template).get("composite")
        return dict(c) if isinstance(c, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _notes(edl, motion=None, region=None):
    out = []
    comp = (motion or {}).get("composite") or {}
    layer = (motion or {}).get("layer") or "above_captions"
    if comp.get("ghost") not in (None, "off") and layer != "behind_subject":
        out.append("ghost draws only on a behind_subject graphic")
    if comp.get("stage") and layer != "behind_subject":
        out.append("stage applies only to a behind_subject graphic")
    shape = (region or comp.get("region") or {}).get("shape")
    if region is None and shape in ("face", "card"):
        # a graphic's clip region: the render resolves face and card masks
        # only for fx items (their stage hands the compiler the masks)
        out.append(f"a {shape} clip region on a graphic is not drawn yet: "
                   "the render leaves the graphic out and says so — clip "
                   "it to a rect, ellipse or polygon instead")
    elif shape in ("face", "card"):
        import fxgraph
        peer = {"face": "matte_track:face_mask",
                "card": "regions:card_pad"}[shape]
        if fxgraph.peer_fn(peer) is None or (
                shape == "card" and _card_stub()):
            out.append(f"the {shape} region draws once the render serves "
                       f"{shape} masks; until then the render omits it and "
                       "says so")
    return out


def _card_stub():
    try:
        import regions
        return "STUB" in (regions.card_pad.__doc__ or "")
    except Exception:  # noqa: BLE001
        return True


def _done(res, notes):
    if res.startswith("REJECTED") or not notes:
        return res
    return res + "\nNOTE: " + "; ".join(notes) + "."


# ── verbs ───────────────────────────────────────────────────────────────

def set_composite(ctx, motion_id, blend=None, opacity=None, fill=None,
                  fill_mode=None, field=None, ghost=None, stage=None,
                  region=None, clear=False):
    """Set (merge) or clear one motion graphic's composite."""
    refusal = _executor_refusal()
    if refusal:
        return refusal
    edl = _latest(ctx)
    items = edl.get("motion") or []
    hit = next((m for m in items if m.get("id") == motion_id), None)
    if hit is None:
        have = ", ".join(str(m.get("id")) for m in items) or "none"
        return f"REJECTED: no motion graphic '{motion_id}'. Existing: {have}."
    if clear:
        if hit.get("composite") is None:
            return f"NO CHANGE: motion '{motion_id}' has no composite."
        return _write.commit(ctx, [_write.set_composite(motion_id, None)],
                             f"composite cleared on motion [{motion_id}]")
    try:
        comp = dict(hit.get("composite") or {})
        if not comp:
            comp = _spec_composite(hit.get("template")) or {}
        given = {"blend": blend, "opacity": opacity,
                 "fill": parse_source(fill), "fill_mode": fill_mode,
                 "field": field, "region": parse_region(region),
                 "stage": stage}
        if isinstance(ghost, str) and ghost.strip().startswith("{"):
            ghost = json.loads(ghost)
        given["ghost"] = ghost
        for k, v in given.items():
            if v is not None:
                comp[k] = v
    except (ValueError, TypeError) as e:
        return f"REJECTED: {e}"
    if not comp:
        return ("REJECTED: say what to change (blend, opacity, fill, "
                "fill_mode, field, ghost, stage, region) or clear=true.")
    def brief(v):
        if not isinstance(v, dict):
            return v
        return v.get("kind") or v.get("shape") or "set"
    what = ", ".join(f"{k}={brief(v)}" for k, v in comp.items()
                     if v is not None)
    res = _write.commit(ctx, [_write.set_composite(motion_id, comp)],
                        f"composite on motion [{motion_id}]: {what}")
    return _done(res, _notes(edl, dict(hit, composite=comp)))


def add_type_window(ctx, text, fill_source, reveal="words_open", start=None,
                    end=None, fill_mode="inside", field=None, font=None,
                    y=None, open_at=None, id=None):
    """Giant letters that are windows onto footage, in one write."""
    refusal = _executor_refusal()
    if refusal:
        return refusal
    edl = _latest(ctx)
    prog = _program_s(edl)
    if prog <= 0.3:
        return ("REJECTED: there is no program yet — place footage first, "
                "then add a type window.")
    try:
        s = float(start if start is not None else 0.0)
        e = float(end) if end is not None else s + 3.0
        fill = parse_source(fill_source)
    except (TypeError, ValueError) as err:
        return f"REJECTED: {err}"
    s = round(min(max(s, 0.0), max(0.0, prog - 0.4)), 2)
    e = round(min(max(e, s + 0.4), prog), 2)
    items = edl.get("motion") or []
    if id is not None and not _ID_RE.match(str(id)):
        return "REJECTED: id must be 1-40 letters, digits, '_' or '-'."
    mid = str(id) if id else _next_id(items, "tw")
    if any(m.get("id") == mid for m in items):
        return (f"REJECTED: a motion graphic '{mid}' already exists — use "
                "set_composite / set_motion_graphic to change it.")
    params = {"text": str(text or "").strip(), "reveal": reveal or "hold"}
    if font:
        params["font"] = font
    if y is not None:
        params["y"] = float(y)
    if open_at is not None:
        params["open_at"] = float(open_at)
    comp = {"fill": fill, "fill_mode": fill_mode or "inside"}
    if field:
        comp["field"] = field
    item = {"id": mid, "template": "type_window", "start": s, "end": e,
            "params": params, "layer": "above_captions", "composite": comp}
    op = {"action": "upsert", "layer": "motion", "id": mid, "value": item}
    res = _write.commit(ctx, [op], f"type window '{params['text']}' "
                        f"({params['reveal']}, fill {fill.get('kind')}) at "
                        f"{s:g}-{e:g}s [{mid}]")
    return _done(res, _notes(edl, item))


def add_region_effect(ctx, effect, region, start, end, strength=None,
                      colour=None, ev=None, scale=None, stage=None, id=None):
    """One effect inside a region (an EDL.fx ``region`` item)."""
    refusal = _executor_refusal()
    if refusal:
        return refusal
    edl = _latest(ctx)
    try:
        reg = parse_region(region)
        s, e = round(float(start), 2), round(float(end), 2)
    except (TypeError, ValueError) as err:
        return f"REJECTED: {err}"
    fx = edl.get("fx") or []
    if id is not None and not _ID_RE.match(str(id)):
        return "REJECTED: id must be 1-40 letters, digits, '_' or '-'."
    fid = str(id) if id else _next_id(fx, "rg")
    if any(i.get("id") == fid for i in fx) and id is None:
        fid = _next_id(fx, "rg")
    st = stage or ("over_type" if (reg or {}).get("shape") == "glyph"
                   else "pre_captions")
    item = {"id": fid, "type": "region", "stage": st, "start": s, "end": e,
            "effect": effect, "region": reg}
    if effect in REGION_CARD:
        item["device"] = REGION_CARD[effect]
    for k, v in (("strength", strength), ("colour", colour), ("ev", ev),
                 ("scale", scale)):
        if v is not None:
            item[k] = v
    flash = _flash_refusal(edl, item)
    if flash:
        return flash
    res = _write.commit(ctx, [_write.upsert_fx(item)],
                        f"region {effect} in {(reg or {}).get('shape')} at "
                        f"{s:g}-{e:g}s [{fid}]")
    return _done(res, _notes(edl, region=reg))


def _flash_refusal(edl, item):
    """Flash safety (owner rule): an automatic write never takes the
    program past 3 full-frame flashes in a second."""
    try:
        import flash_safety
        before = flash_safety.assess(edl)
        after = flash_safety.assess(dict(edl, fx=list(edl.get("fx") or [])
                                         + [item]))
    except Exception:  # noqa: BLE001 — the export gate judges it again
        return None
    if after["over_limit"] and not before["over_limit"]:
        return (f"REJECTED: that would play {after['peak_per_s']} "
                f"full-frame flashes in one second (the limit is "
                f"{after['max_per_s']}); use a smaller region, a softer "
                "effect or a longer window.")
    return None


def remove_region_effect(ctx, ids):
    """Remove region effects by id."""
    edl = _latest(ctx)
    want = _ids(ids)
    have = {i.get("id"): i for i in edl.get("fx") or []}
    bad = [i for i in want if (have.get(i) or {}).get("type") != "region"]
    if not want or bad:
        existing = ", ".join(i for i, it in have.items()
                             if it.get("type") == "region") or "none"
        return (f"REJECTED: not region effects: {', '.join(bad) or '(none)'}"
                f". Region effects here: {existing}.")
    return _write.commit(ctx, [_write.remove_fx(i) for i in want],
                         f"removed region effect(s) {', '.join(want)}")


# ── catalog ──────────────────────────────────────────────────────────────

_SRC = ("next|previous|program[:s]|black|white|accent|#rrggbb|source:<s>|"
        "<asset key>[+loop], or FxSource JSON")
_REG = ("face|card|glyph:<motion id>|rect:x0,y0,x1,y1|ellipse:x0,y0,x1,y1, "
        "or FxRegion JSON (polygon points, keyframed keys)")

TOOL_SPECS = {
    "set_composite": (
        set_composite,
        "How a motion graphic joins the picture (MotionItem.composite; "
        "merges into what it has). blend: difference reads over a busy plate "
        "(interruptions), screen/add make light (leaks, flashes, burns never "
        "darken), auto picks difference over busy plates and screen over "
        "dark ones by measuring the plate. fill puts footage inside the "
        "glyphs (fill_mode inside; field colours around them) or makes the "
        "glyphs windows onto the picture with the fill around (outside). "
        "ghost draws the hidden letters of a behind_subject graphic as "
        "outlines over the subject. stage screen keeps a behind_subject "
        "graphic screen-locked under a camera move. region clips it. "
        "clear=true restores the plain composite.",
        {"motion_id": {"type": "string"},
         "blend": {"type": "string", "enum": [
             "normal", "screen", "add", "multiply", "overlay", "softlight",
             "difference", "exclusion", "lighten", "darken", "auto"]},
         "opacity": {"type": "number"},
         "fill": {"type": "string", "description": _SRC},
         "fill_mode": {"type": "string", "enum": ["inside", "outside"]},
         "field": {"type": "string"},
         "ghost": {"type": "string",
                   "description": "auto|off|{opacity,width_px,threshold}"},
         "stage": {"type": "string", "enum": ["world", "screen"]},
         "region": {"type": "string", "description": _REG},
         "clear": {"type": "boolean"}}),
    "add_type_window": (
        add_type_window,
        "Giant condensed letters that are windows (template type_window + "
        "its composite, one item). fill_source fills the glyphs: footage "
        "(an asset key), next (the shot after the cut: the cross-cut "
        "reveal), a colour. field='black' sets footage-in-letters on black; "
        "fill_mode outside makes the letters windows onto the picture with "
        "the fill around them. reveal: hold, words (one word per hard cut), "
        "open (a letter grows into the full frame, eased), words_open. Use "
        "for a title or a scene change, never as decoration.",
        {"text": {"type": "string"},
         "fill_source": {"type": "string", "description": _SRC},
         "reveal": {"type": "string",
                    "enum": ["hold", "words", "open", "words_open"]},
         "start": {"type": "number"}, "end": {"type": "number"},
         "fill_mode": {"type": "string", "enum": ["inside", "outside"]},
         "field": {"type": "string"},
         "font": {"type": "string", "enum": ["Anton", "Bebas Neue",
                                             "Archivo Black",
                                             "Inter Display"]},
         "y": {"type": "number"}, "open_at": {"type": "number"},
         "id": {"type": "string"}}),
    "add_region_effect": (
        add_region_effect,
        "One effect inside a region over start-end (EDL.fx region): negate, "
        "solarise (colour = duotone), mono, posterize, threshold, blur, "
        "pixelate, exposure (ev), tint (colour), or a glass material: "
        "ribbed_glass, refraction, heat (scale). Outside the region the "
        "picture is untouched. glyph:<id> works inside a graphic's letters; "
        "stage defaults to pre_captions (over_type for glyphs).",
        {"effect": {"type": "string", "enum": [
            "negate", "solarise", "mono", "posterize", "threshold", "blur",
            "pixelate", "exposure", "tint", "ribbed_glass", "refraction",
            "heat"]},
         "region": {"type": "string", "description": _REG},
         "start": {"type": "number"}, "end": {"type": "number"},
         "strength": {"type": "number"}, "colour": {"type": "string"},
         "ev": {"type": "number"}, "scale": {"type": "number"},
         "stage": {"type": "string", "enum": [
             "world", "screen", "pre_captions", "over_type"]},
         "id": {"type": "string"}}),
    "remove_region_effect": (
        remove_region_effect,
        "Remove region effects by id (ids: a list or 'rg1,rg2').",
        {"ids": {"type": "array", "items": {"type": "string"}}}),
}

REQUIRED_ARGS = {
    "set_composite": ["motion_id"],
    "add_type_window": ["text", "fill_source"],
    "add_region_effect": ["effect", "region", "start", "end"],
    "remove_region_effect": ["ids"],
}

WRITE_TOOLS = {"set_composite", "add_type_window", "add_region_effect",
               "remove_region_effect"}

COMPACT = {
    "set_composite": (
        "Composite of a motion graphic (merges): blend (difference over "
        "busy plates; screen/add = light; auto measures), opacity, fill "
        "(footage in glyphs; fill_mode outside = glyphs are windows), field "
        "(colour around), ghost (outlines of hidden letters, behind_subject "
        "only), stage, region; clear=true restores plain."),
    "add_type_window": (
        "Letters as windows in one item: fill_source (asset key, next = "
        "cross-cut reveal, colour), field='black' for footage-in-letters on "
        "black, fill_mode outside, reveal hold|words|open|words_open (a "
        "letter grows into the frame). Titles and scene turns only."),
    "add_region_effect": (
        "Effect inside a region (face, card, glyph:<id>, rect/ellipse box, "
        "JSON polygon/keys) over start-end: negate, solarise, mono, "
        "posterize, threshold, blur, pixelate, exposure, tint, ribbed_glass, "
        "refraction, heat. Outside untouched."),
    "remove_region_effect": "Remove region effects by id.",
}
