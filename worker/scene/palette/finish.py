"""Finishes by section: the surface a stretch of the film is printed on, switched on its cut and switched back.

    finish  f[a,b] surface:
            mono    black and white (the engine's mono_silver look on every picture of the span)
            gate    black and white inside a film gate (soft black edges, faint scratches)
            crt     television scanlines over the span
            bloom   black and white whose colour blooms back over its last frames
            thermal threshold halftone xray blueprint night_vision posterise solarise line   the frame re-rendered
            neg     the span held negative
            colour  the film's own look (nothing laid)

A surface another lane has not built yet (rew, paper, xerox, led, projector, tear) is printed on its nearest
built one, and the log says so. The type is never finished: flashes and looks act on the pictures under it.
"""
import math
import os

from ..alien import sense as AS

SURFACE_OF = {"bw": "mono", "black_and_white": "mono", "silver": "mono", "film": "gate", "projector": "gate",
              "archive": "gate", "tv": "crt", "rew": "crt", "vhs": "crt", "tape": "crt", "scanlines": "crt",
              "paper": "mono", "xerox": "threshold", "photocopy": "threshold", "led": "halftone", "dot": "halftone",
              "dots": "halftone", "tear": "neg", "negative": "neg", "x_ray": "xray", "schematic": "blueprint",
              "night": "night_vision", "posterize": "posterise", "solarize": "solarise", "color": "colour",
              "none": "colour", "colour_bloom": "bloom", "mono_to_colour": "bloom", "rewind": "crt", "monitor": "crt",
              "screen": "crt", "monochrome": "mono", "grayscale": "mono", "greyscale": "mono", "sepia": "mono",
              "film_gate": "gate", "vintage": "gate", "super8": "gate", "8mm": "gate", "color_bloom": "bloom"}
FRAME_SURFACES = ("thermal", "threshold", "halftone", "xray", "blueprint", "night_vision", "posterise", "solarise", "line")


def ease(t):
    t = min(max(float(t), 0.0), 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * t)


def r4(x):
    return round(float(x), 4)


def overlay(work, kind, W=1080, H=1920):
    """A full-frame RGBA layer drawn once into the work: the film gate or the scanlines."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    p = os.path.join(work, "palette", f"finish_{kind}.png")
    if os.path.exists(p):
        return p
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if kind == "gate":
        a = Image.new("L", (W, H), 255)
        ImageDraw.Draw(a).rounded_rectangle([int(0.035 * W), int(0.022 * H), int(0.965 * W), int(0.978 * H)], radius=int(0.05 * W), fill=0)
        al = np.asarray(a.filter(ImageFilter.GaussianBlur(9)), np.float32).copy()
        rgb = np.zeros((H, W, 3), np.uint8)
        for x in np.random.default_rng(3).integers(int(0.1 * W), int(0.9 * W), 3):
            al[:, x:x + 2] = np.maximum(al[:, x:x + 2], 26)
            rgb[:, x:x + 2] = 230
    else:
        al = np.zeros((H, W), np.float32)
        al[::4, :] = 64
        al[1::4, :] = 28
        yy, xx = np.mgrid[0:H, 0:W]
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        al = np.maximum(al, np.clip((r - 0.78) * 260, 0, 150))
        rgb = np.zeros((H, W, 3), np.uint8)
    Image.fromarray(np.dstack([rgb, al.clip(0, 255).astype(np.uint8)]), "RGBA").save(p)
    return p


def surface_of(name):
    s = str(name or "mono").lower().replace("-", "_").replace(" ", "_")
    return SURFACE_OF.get(s, s)


def looked(l1, a, b, ident, look=None, treat=None):
    """The base shots of [a, b) with a look (or a treatment), cut at a and b so the change lands on the span's edges.
    Returns (items, drop)."""
    keep, gone, was = AS.carve(l1, a, b)
    inside = []
    for k, sh in enumerate(was):
        lo, hi = max(a, sh["f"][0]), min(b, sh["f"][1])
        whole = lo == sh["f"][0] and hi == sh["f"][1]
        it = AS.slice_shot(sh, lo, hi, new_id=sh["id"] if whole else f"{sh['id']}~{ident}{k}")
        if sh.get("pic") == "plate":
            inside.append(it)
            continue
        if look:
            it["look"] = look
        if treat:
            it["treat"] = treat
        inside.append(it)
    return keep + inside, gone


# a section printed on one of the surface family's own surfaces (worker/scene/palette/surface.py) is laid by that
# device: the tape, the projector's gate, the lamp board, the photocopy, the page, the thermal scope
OWN_SURFACES = {"rew": "rew", "vhs": "vhs", "tape": "vhs", "projector": "projector", "led": "led", "xerox": "xerox",
                "photocopy": "xerox", "paper": "paper", "thermal_hud": "thermal_hud", "hud": "thermal_hud"}


def finish(l1, work, ask, sense):
    asked = str(ask.get("surface") or ask.get("mode") or ask.get("as") or "").lower().replace("-", "_").replace(" ", "_")
    own = OWN_SURFACES.get(asked)
    if own:
        from . import DEVICES
        if own in DEVICES:
            got = DEVICES[own]["plan"](l1, work, dict(ask, dev=own), sense)
            if isinstance(got, dict) and not got.get("skip"):
                return dict(got, log=dict(got.get("log") or {}, surface=own))
    a, b = AS_span(ask, l1)
    b = max(b, a + 12)
    b = min(b, int(l1["dur_f"]))
    a = sense.on_cut(a, reach=6)
    s = surface_of(ask.get("surface") or ask.get("mode") or ask.get("as"))
    i = ask["id"]
    W, H = sense.size
    ev = [{"f": a, "kind": f"finish_{s}", "strength": 0.4}]
    if s == "colour":
        return {"items": [], "events": [], "log": {"surface": "colour"}}
    if s in ("mono", "gate", "bloom"):
        items, drop = looked(l1, a, b, f"m{i}", look="mono_silver")
        if s == "gate":
            aid = "palette_gate"
            items.append({"id": f"{i}g", "k": "card", "f": [a, b], "pic": aid, "x": 0.5, "y": 0.5, "w": 1.0, "h": 1.0,
                          "s": 1, "rot": 0, "op": 1, "z": 40})
            return {"items": items, "drop": drop, "assets": {aid: {"kind": "image", "path": overlay(work, "gate", W, H)}},
                    "events": ev, "log": {"surface": s, "f": [a, b]}}
        if s == "bloom":
            n = b - a
            k = max(6, min(18, n // 2))
            items = []                      # bloom is a flash: the shots keep their colour under it
            alpha = [1.0] * (n - k) + [r4(1.0 - ease((q + 1) / k)) for q in range(k)]
            items.append({"id": f"{i}bl", "k": "flash", "f": [a, b], "mode": "mono", "a": alpha})
            return {"items": items, "events": ev + [{"f": b - k, "kind": "colour_bloom", "strength": 0.5}],
                    "log": {"surface": s, "f": [a, b], "bloom_f": k}}
        return {"items": items, "drop": drop, "events": ev, "log": {"surface": s, "f": [a, b]}}
    if s == "crt":
        aid = "palette_crt"
        return {"items": [{"id": f"{i}c", "k": "card", "f": [a, b], "pic": aid, "x": 0.5, "y": 0.5, "w": 1.0, "h": 1.0,
                           "s": 1, "rot": 0, "op": 1, "z": 40},
                          {"id": f"{i}cp", "k": "flash", "f": [a, b], "mode": "posterise", "a": 0.35, "th": 0.5}],
                "assets": {aid: {"kind": "image", "path": overlay(work, "crt", W, H)}}, "events": ev,
                "log": {"surface": s, "f": [a, b]}}
    if s == "neg":
        # printed negative where the frame is positive now; frames a device already turned negative keep that (a second
        # negative over them would print them positive and both would vanish)
        pol = sense.negative(a, b)
        runs, f0 = [], None
        for q, neg_ in enumerate(pol + [True]):
            if not neg_ and f0 is None:
                f0 = a + q
            elif neg_ and f0 is not None:
                runs.append((f0, a + q))
                f0 = None
        items = [{"id": f"{i}n" if k == 0 else f"{i}n{k}", "k": "flash", "f": [lo, hi], "mode": "neg", "a": 1.0}
                 for k, (lo, hi) in enumerate(runs)]
        return {"items": items, "lum": sorted({f for r in runs for f in r}), "events": ev,
                "log": {"surface": s, "f": [a, b], **({"kept_negative": sum(pol)} if any(pol) else {})}}
    if s not in FRAME_SURFACES:
        s = "mono"
        items, drop = looked(l1, a, b, f"m{i}", look="mono_silver")
        return {"items": items, "drop": drop, "events": ev, "log": {"surface": "mono", "asked": ask.get("surface")}}
    it = {"id": f"{i}f", "k": "flash", "f": [a, b], "mode": s, "a": 1.0}
    if s == "threshold":                    # split each picture at its own middle light, so it prints readable, never blobs
        from .surface import own_light
        it["th"] = own_light(l1, sense, a, b, lo=0.12, hi=0.88)
    return {"items": [it], "events": ev, "log": {"surface": s, "f": [a, b], **({"th": it["th"]} if "th" in it and not isinstance(it["th"], list) else {})}}


def AS_span(ask, l1):
    from . import span
    return span(ask, l1)


DEVICES = {
    "finish": {"plan": finish, "layer": "surface", "fallback": ("variant",),
               "card": "finish f[a,b] surface(mono|gate|crt|bloom|thermal|threshold|halftone|xray|blueprint|night_vision|posterise|solarise|line|neg|colour). The surface a section is printed on, switched on its cut. The past, a memory, a screen, a change of world; bloom: colour returns on the turn"},
}
ALIASES = {"surface": "finish", "section": "finish", "mono": "finish", "gate": "finish", "crt": "finish", "bloom": "finish",
           "colour": "finish", "color": "finish", "finish": "finish"}
# every surface the finish card names, and every name it reads as one (SURFACE_OF), is the finish device with the surface
# carried in "as" (a name the variant device also draws, a frame re-render, resolves to the variant: it draws the same
# mode over the ask's span)
ALIASES.update({k: "finish" for k in tuple(SURFACE_OF) + FRAME_SURFACES if k not in ("neg", "negative", "tear")})
ALIASES.update({"tear": "finish"})
