"""Department ``world``: the layout stage (Wave 3, 3E).

  add_collage       a living wall of real footage: mosaic (pops on beats,
                    breathes, collapses into one tile + title), scroll_wall,
                    flatlay (cutouts dropped on a surface)
  set_stack         2-4 panels of independent footage, one can grow; answers
                    each panel's rect so accents can be clipped to it
  add_hud           frame furniture through the piece (labels, REC,
                    timecode, brackets), clear of the corner mark
  add_breakdown     the edit's own timeline with a live playhead (truthful:
                    drawn from the EDL at render)
  add_device_stage  a recording inside a drawn laptop / phone / tablet /
                    monitor with light spill; optional UI breakout
  set_brand_kit     a logo's palette (k-means in Lab) applied to the layout
                    graphics; the user's own sting placed before the end card

Footage for tiles / panels / screens: 'asset:<key>' (a project image or
video), 'source:<seconds>' (the main footage from that source second), or a
bare storage key. Writes go through tools_ext._write.commit (the
apply_edit_batch path). Browserless: nothing renders here.
"""

import json
import re

from tools_ext import _write

DEPARTMENT = "world"
VERBS = ("add_collage", "set_stack", "add_hud", "add_breakdown",
         "add_device_stage", "set_brand_kit")
LAYOUT_TEMPLATES = ("mosaic", "scroll_wall", "flatlay", "stack", "hud",
                    "breakdown", "device_stage")


def _latest(ctx):
    return json.loads(json.dumps(ctx.latest_edl()["json"]))


def _prog(edl):
    from timeline import Timeline
    return float(Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                          edl.get("speed") or []).out_duration)


def _next_id(items, prefix):
    have = {str(i.get("id")) for i in items or []}
    k = 1
    while f"{prefix}{k}" in have:
        k += 1
    return f"{prefix}{k}"


def _strs(v):
    if v is None:
        return []
    if isinstance(v, str):
        v = json.loads(v) if v.strip().startswith("[") else \
            [x for x in re.split(r"[\s,]+", v) if x]
    return [str(x) for x in v]


def _source(v):
    s = str(v).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return f"source:{float(s):g}"
    return s


def _window(edl, start, end, default):
    prog = _prog(edl)
    s = round(min(max(float(start or 0.0), 0.0), max(0.0, prog - 0.3)), 2)
    e = round(min(max(float(end) if end is not None else s + default,
                      s + 0.3), prog), 2)
    return s, e


def _op(item):
    return {"action": "upsert", "layer": "motion", "id": item["id"],
            "value": item}


def _commit(ctx, item, summary):
    return _write.commit(ctx, [_op(item)], summary)


def add_collage(ctx, kind, tiles, start, end=None, at=None, title=None,
                kicker=None, layout=None, hero=None, collapse_at=None,
                backdrop=None, id=None):
    """A wall of real footage (mosaic / scroll_wall / flatlay)."""
    if kind not in ("mosaic", "scroll_wall", "flatlay"):
        return "REJECTED: kind is mosaic, scroll_wall or flatlay."
    edl = _latest(ctx)
    t = [_source(x) for x in _strs(tiles)]
    if not t:
        return "REJECTED: give tiles (asset keys or source seconds)."
    s, e = _window(edl, start, end, 8.0 if kind == "mosaic" else 4.0)
    items = edl.get("motion") or []
    mid = str(id) if id else _next_id(items, "co")
    params = {}
    if kind == "flatlay":
        imgs = [x.split(":", 1)[1] if x.startswith("asset:") else x
                for x in t if not x.startswith("source:")][:8]
        if not imgs:
            return "REJECTED: a flat-lay needs images (ideally cutouts)."
        params.update({f"img{i + 1}": k for i, k in enumerate(imgs)})
    else:
        params["tiles"] = t[:64]
    if at:
        params["at"] = [str(round(float(x) - s, 3)) for x in _strs(at)]
    if title:
        params["title"] = str(title)[:40]
    if kicker and kind == "mosaic":
        params["kicker"] = str(kicker)[:40]
    if layout and kind == "mosaic":
        params["layout"] = layout
    if hero is not None and kind == "mosaic":
        params["hero"] = int(hero)
    if collapse_at is not None and kind == "mosaic":
        params["collapse_at"] = round(max(0.0, float(collapse_at) - s), 2)
    if backdrop:
        params["backdrop" if kind != "flatlay" else "surface"] = backdrop
    item = {"id": mid, "template": kind, "start": s, "end": e,
            "params": params, "layer": "above_captions"}
    return _commit(ctx, item, f"{kind} of {len(t)} tiles {s:g}-{e:g}s "
                   f"[{mid}]")


def set_stack(ctx, panels, start, end=None, labels=None, direction=None,
              grow=None, grow_at=None, id=None):
    """2-4 panels of independent footage."""
    edl = _latest(ctx)
    p = [_source(x) for x in _strs(panels)][:4]
    if len(p) < 2:
        return "REJECTED: a stack needs 2-4 panels."
    s, e = _window(edl, start, end, 4.0)
    items = edl.get("motion") or []
    mid = str(id) if id else _next_id(items, "sk")
    params = {"panels": p}
    if labels:
        params["labels"] = _strs(labels)[:4]
    if direction:
        params["direction"] = direction
    if grow is not None:
        params["grow"] = int(grow)
    if grow_at is not None:
        params["grow_at"] = round(max(0.0, float(grow_at) - s), 2)
    item = {"id": mid, "template": "stack", "start": s, "end": e,
            "params": params, "layer": "above_captions"}
    res = _commit(ctx, item, f"stack of {len(p)} panels {s:g}-{e:g}s [{mid}]")
    if res.startswith("REJECTED"):
        return res
    # the panels' rects (frame fractions), for accents clipped to one
    import renderer
    v = (ctx.index or {}).get("video") or {}
    W, H = renderer.frame_dims(float(v.get("width") or 1920),
                               float(v.get("height") or 1080),
                               (edl.get("frame") or {}).get("ratio"))
    n, g = len(p), 0.012
    vert = direction == "vertical" or (direction != "horizontal" and H >= W)
    rects = []
    for k in range(n):
        if vert:
            gy = g * W / H
            rects.append([round(g, 4), round(gy + k * (1 - gy) / n, 4),
                          round(1 - g, 4), round((k + 1) * (1 - gy) / n, 4)])
        else:
            rects.append([round(g + k * (1 - g) / n, 4), round(g * H / W, 4),
                          round((k + 1) * (1 - g) / n, 4),
                          round(1 - g * H / W, 4)])
    return res + "\nPanel rects (clip a flash or effect to one with " \
        "add_region_effect region rect:...): " + "; ".join(
            f"{k}: {r}" for k, r in enumerate(rects))


def add_hud(ctx, start=None, end=None, tl=None, tr=None, bl=None, br=None,
            rec=None, timecode=None, brackets=None, crosshair=None,
            grid=None, colour=None, accent=None, id=None):
    """Frame furniture through the piece."""
    import keepout
    import renderer
    edl = _latest(ctx)
    prog = _prog(edl)
    s, e = _window(edl, start, end if end is not None else prog, prog)
    v = (ctx.index or {}).get("video") or {}
    W, H = renderer.frame_dims(float(v.get("width") or 1920),
                               float(v.get("height") or 1080),
                               (edl.get("frame") or {}).get("ratio"))
    try:
        mark = keepout.watermark_zone(W, H)
    except Exception:  # noqa: BLE001
        mark = [0.09, 0.05, 0.5, 0.13]
    params = {"mark": [str(x) for x in mark]}
    for k, val in (("tl", tl), ("tr", tr), ("bl", bl), ("br", br),
                   ("colour", colour), ("accent", accent)):
        if val:
            params[k] = str(val)
    for k, val in (("rec", rec), ("timecode", timecode),
                   ("brackets", brackets), ("crosshair", crosshair),
                   ("grid", grid)):
        if val is not None:
            params[k] = bool(val)
    items = edl.get("motion") or []
    mid = str(id) if id else _next_id(items, "hud")
    item = {"id": mid, "template": "hud", "start": s, "end": e,
            "params": params, "layer": "above_captions",
            "mute_captions": False}
    return _commit(ctx, item, f"HUD {s:g}-{e:g}s [{mid}]")


def add_breakdown(ctx, start=None, end=None, position=None, title=None,
                  follow=None, id=None):
    """The edit's own timeline with a live playhead."""
    edl = _latest(ctx)
    prog = _prog(edl)
    s, e = _window(edl, start, end if end is not None else prog, prog)
    params = {}
    if position:
        params["position"] = position
    if title:
        params["title"] = str(title)[:30]
    if follow is not None:
        params["follow"] = bool(follow)
    items = edl.get("motion") or []
    mid = str(id) if id else _next_id(items, "bd")
    item = {"id": mid, "template": "breakdown", "start": s, "end": e,
            "params": params, "layer": "above_captions",
            "mute_captions": False}
    return _commit(ctx, item, f"breakdown {s:g}-{e:g}s [{mid}]")


def add_device_stage(ctx, screen, start, end=None, device=None, title=None,
                     breakout=None, breakout_at=None, id=None):
    """A recording inside a drawn device with light spill."""
    edl = _latest(ctx)
    s, e = _window(edl, start, end, 5.0)
    params = {"screen": _source(screen)}
    if device:
        params["device"] = device
    if title:
        params["title"] = str(title)[:40]
    if breakout:
        params["breakout"] = [str(float(x)) for x in _strs(breakout)][:4]
        if breakout_at is not None:
            params["breakout_at"] = round(max(0.0, float(breakout_at) - s), 2)
    items = edl.get("motion") or []
    mid = str(id) if id else _next_id(items, "ds")
    item = {"id": mid, "template": "device_stage", "start": s, "end": e,
            "params": params, "layer": "above_captions"}
    return _commit(ctx, item, f"device stage ({device or 'laptop'}) "
                   f"{s:g}-{e:g}s [{mid}]")


def set_brand_kit(ctx, logo=None, accent=None, sting=None):
    """Brand colours onto the layout graphics; the user's sting before the
    end card."""
    edl = _latest(ctx)
    pal = None
    if logo:
        try:
            import os
            import tempfile
            import layouts
            local = ctx.fetch_asset(logo) if hasattr(ctx, "fetch_asset") \
                else None
            if not local:
                import storage
                local = os.path.join(tempfile.mkdtemp(prefix="brand_"),
                                     os.path.basename(str(logo)))
                storage.download_to(str(logo), local)
            pal = layouts.brand_palette(local)
        except Exception as e:  # noqa: BLE001
            return f"REJECTED: the logo could not be read ({str(e)[:120]})."
    acc = accent or (pal or {}).get("accent")
    ops = []
    if acc:
        for m in edl.get("motion") or []:
            if m.get("template") in LAYOUT_TEMPLATES + (
                    "el_doodle", "el_orbit", "glass_card", "spec_sheet"):
                p = dict(m.get("params") or {})
                key = "accent" if m.get("template") not in (
                    "el_doodle", "el_orbit") else "colour"
                p[key] = acc
                ops.append({"action": "upsert", "layer": "motion",
                            "id": m["id"], "value": {"id": m["id"],
                                                     "params": p}})
    if sting:
        prog = _prog(edl)
        ops.append({"action": "upsert", "layer": "inserts",
                    "id": "brand_sting", "value": {
                        "id": "brand_sting", "kind": "video",
                        "asset_key": str(sting), "at_output_s": round(prog, 2),
                        "duration_s": 2.0, "source_start_s": 0.0}})
    if not ops:
        return ("NO CHANGE: nothing to brand yet (add layout graphics, or "
                "give accent / logo / sting).")
    note = f" (palette {', '.join(pal['colours'])})" if pal else ""
    return _write.commit(ctx, ops, f"brand kit: accent {acc or '-'}"
                         f"{', sting before the end card' if sting else ''}"
                         + note)


_SRC = "asset:<key> | source:<seconds> | <storage key>"

TOOL_SPECS = {
    "add_collage": (
        add_collage,
        "A living wall of REAL footage: mosaic (tiles pop on 'at' beats, "
        "breathe, collapse tile by tile into the hero tile, then the title), "
        "scroll_wall (rows of thumbnails scrolling), flatlay (cutouts "
        "dropped on a surface). tiles: " + _SRC + ". Up to 64; video plays.",
        {"kind": {"type": "string", "enum": ["mosaic", "scroll_wall",
                                             "flatlay"]},
         "tiles": {"type": "array", "items": {"type": "string"}},
         "start": {"type": "number"}, "end": {"type": "number"},
         "at": {"type": "array", "items": {"type": "number"}},
         "title": {"type": "string"}, "kicker": {"type": "string"},
         "layout": {"type": "string", "enum": ["grid", "justified",
                                               "treemap", "overload"]},
         "hero": {"type": "integer"}, "collapse_at": {"type": "number"},
         "backdrop": {"type": "string"}, "id": {"type": "string"}}),
    "set_stack": (
        set_stack,
        "2-4 panels of INDEPENDENT footage stacked (" + _SRC + "), labels, "
        "one panel may grow to full frame at grow_at. Replies with each "
        "panel's rect so a flash or effect can be clipped to one.",
        {"panels": {"type": "array", "items": {"type": "string"}},
         "start": {"type": "number"}, "end": {"type": "number"},
         "labels": {"type": "array", "items": {"type": "string"}},
         "direction": {"type": "string", "enum": ["auto", "vertical",
                                                  "horizontal"]},
         "grow": {"type": "integer"}, "grow_at": {"type": "number"},
         "id": {"type": "string"}}),
    "add_hud": (
        add_hud,
        "Frame furniture through the piece: corner labels (tl/tr/bl/br), "
        "REC dot, timecode, brackets, crosshair, grid — small, steady, in "
        "the brand colour, clear of the corner mark. Defaults to the whole "
        "program.",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "tl": {"type": "string"}, "tr": {"type": "string"},
         "bl": {"type": "string"}, "br": {"type": "string"},
         "rec": {"type": "boolean"}, "timecode": {"type": "boolean"},
         "brackets": {"type": "boolean"}, "crosshair": {"type": "boolean"},
         "grid": {"type": "boolean"}, "colour": {"type": "string"},
         "accent": {"type": "string"}, "id": {"type": "string"}}),
    "add_breakdown": (
        add_breakdown,
        "The edit explaining itself: its real timeline (cut, graphics, "
        "devices, world, camera, sound lanes, true names) with a live "
        "playhead, drawn from the EDL at render. A meta beat.",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "position": {"type": "string", "enum": ["bottom", "top"]},
         "title": {"type": "string"}, "follow": {"type": "boolean"},
         "id": {"type": "string"}}),
    "add_device_stage": (
        add_device_stage,
        "A recording (screen: " + _SRC + ") inside a drawn laptop, phone, "
        "tablet or monitor, its light spilling on the stage; breakout "
        "[x0,y0,x1,y1] (screen fractions) lifts that part of the UI out at "
        "breakout_at.",
        {"screen": {"type": "string"}, "start": {"type": "number"},
         "end": {"type": "number"},
         "device": {"type": "string", "enum": ["laptop", "phone", "tablet",
                                               "monitor"]},
         "title": {"type": "string"},
         "breakout": {"type": "array", "items": {"type": "number"}},
         "breakout_at": {"type": "number"}, "id": {"type": "string"}}),
    "set_brand_kit": (
        set_brand_kit,
        "The user's brand: logo (an image key: its palette by k-means) or "
        "accent (#hex) applied to the layout graphics; sting (their own "
        "short logo clip) placed before the Valmera end card.",
        {"logo": {"type": "string"}, "accent": {"type": "string"},
         "sting": {"type": "string"}}),
}
REQUIRED_ARGS = {"add_collage": ["kind", "tiles", "start"],
                 "set_stack": ["panels", "start"], "add_hud": [],
                 "add_breakdown": [], "add_device_stage": ["screen", "start"],
                 "set_brand_kit": []}
WRITE_TOOLS = set(VERBS)
COMPACT = {
    "add_collage": ("Wall of real footage: mosaic (pops on at, collapses into "
                    "hero + title) | scroll_wall | flatlay; tiles asset:<key>"
                    " or source:<s>."),
    "set_stack": "2-4 independent panels, labels, grow; replies panel rects.",
    "add_hud": ("HUD furniture (tl/tr/bl/br labels, REC, timecode, "
                "brackets) clear of the corner mark."),
    "add_breakdown": "The edit's own live-playhead timeline (truthful).",
    "add_device_stage": ("Recording in a drawn laptop/phone/tablet/monitor "
                         "with light spill; UI breakout."),
    "set_brand_kit": "Logo palette / accent onto layouts; sting before end card.",
}
