"""diecut: type cut from the footage itself. On its word the letters are stamped out of the live picture:
they lift off the frame carrying the very pixels that were under them (the take keeps playing inside the
letters, the speaker keeps moving through them), the hole they leave stays dark, and on the last frame
they drop back and the picture heals. No ink is laid on the film; the film is the ink.

The engine draws all of it (a glyph junction of the shot's own picture through a displaced crop, over a
word of the hole's colour), so the letters take the shot's look, crop and clock on every frame. What the
machine adds is the registration: on each frame the letters' crop is solved from that frame's crop of the
shot, so a camera move under the word never tears it. The seat is found by signal: where the picture has
the most texture to cut, off any face handed in.

ask  {"dev": "diecut", "at": [a, z], "t": "GONE", "font": "InterDisplay-Black.ttf", "width": 0.84 (of the frame),
      "seat": [centre x, baseline y] (default found), "faces": [[x, y, w, h]] frame fractions to stay off,
      "lift": 0.018 (frame widths the letters rise), "dim": 0.70 (what is left of the frame while they are up),
      "hole": "#0A0908", "up": 7, "down": 5 (frames)}
"""
import math

import numpy as np

from . import glyphs, sense
from .. import bake


def seat_of(l1, work, a, z, block, faces):
    """The baseline for a word of block (w, h) frame fractions. Letters cut from a picture read by what they
    carry against a frame that has stepped back: so the band where the picture is brightest and has grain to
    show, clear of any face, inside the safe area. Read off three frames of the span."""
    import cv2
    gw, gh = 108, 192
    fs = sense.shot_frames(l1, work, a, z, size=(gw, gh), gray=True)
    if not fs:
        return 0.5, 0.46
    keys = sorted(fs)
    tex, lum = np.zeros((gh, gw), np.float32), np.zeros((gh, gw), np.float32)
    for f in (keys[0], keys[len(keys) // 2], keys[-1]):
        g = cv2.GaussianBlur(fs[f], (0, 0), 1.0).astype(np.float32)
        tex += np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
        lum += g / 3.0
    tex /= max(float(np.percentile(tex, 95)), 1e-3)
    lum /= max(float(np.percentile(lum, 98)), 1.0)
    bw, bh = block
    best, at = -1e9, 0.46
    for y1 in np.arange(0.12 + bh, 0.78, 0.02):                      # the block's bottom edge (the baseline)
        x0, x1, y0 = 0.5 - bw / 2, 0.5 + bw / 2, y1 - bh
        box = (slice(int(y0 * gh), max(int(y1 * gh), int(y0 * gh) + 1)), slice(int(x0 * gw), int(x1 * gw)))
        s = float(lum[box].mean()) + 0.35 * float(tex[box].mean())
        for fx, fy, fw, fh in faces or []:
            ox = max(0.0, min(x1, fx + fw) - max(x0, fx))
            oy = max(0.0, min(y1, fy + fh * 1.12) - max(y0, fy - fh * 0.22))
            s -= 8.0 * (ox * oy) / max(bw * bh, 1e-6)
        if s > best:
            best, at = s, float(y1)
    return 0.5, at


def lift_curve(n, up=7, down=5):
    """0..1 per frame: up fast with a small pass over the top (the measured ``out`` move), held, then back in
    and seated on the last frame."""
    up, down = min(up, max(n // 3, 2)), min(down, max(n // 3, 2))
    rise = bake.move(up, 0.0, 1.0, "out", frames=up, over=bake.MOVE_OVER)
    fall = bake.move(down, 1.0, 0.0, "in", frames=down)
    hold = [1.0] * max(n - up - down, 0)
    e = (list(rise) + hold + list(fall))[:n]
    e[-1] = 0.0
    return [float(v) for v in e]


def plan(l1, work, ask):
    fps = float(l1.get("fps") or 30)
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    a, z = [int(v) for v in ask["at"]]
    text = str(ask.get("t") or "").strip()
    if not text or z - a < 8:
        return {"skip": "diecut needs a word and 8 frames"}
    voice = {"font": ask.get("font") or "InterDisplay-Black.ttf", "track": 0, "shadow": {"a": 0.5, "blur": 0.016}}
    voice["px"] = round(glyphs.fit_px(voice, text, float(ask.get("width") or 0.84) * W, cap_px=0.2 * H), 2)
    _, b = glyphs.box(voice, text, voice["px"])
    bw, bh = (b[2] - b[0]) / W, (b[3] - b[1]) / H
    if ask.get("seat"):
        sx, sy = [float(v) for v in ask["seat"]]
    else:
        sx, sy = seat_of(l1, work, a, z, (bw, bh), ask.get("faces"))
    pen_x = sx - (b[0] + b[2]) / 2 / W                        # the ink's middle on the seat's x
    pen_y = sy - b[3] / H                                     # the ink's bottom edge on the seat's y
    cx, cy = sx, pen_y + (b[1] + b[3]) / 2 / H                # the middle of the ink: what the letters grow about
    lift = float(ask.get("lift") or 0.018)
    dim = float(ask.get("dim") if ask.get("dim") is not None else 0.70)
    ang = math.radians(float(ask.get("angle", -122.0)))       # up and a little left: the light is top left
    ux, uy = math.cos(ang), math.sin(ang)
    e = lift_curve(z - a, int(ask.get("up") or 7), int(ask.get("down") or 5))
    vname = f"alien.{ask['id']}"
    hole = {"id": f"{ask['id']}.hole", "k": "word", "f": [a, z], "t": text, "v": vname, "x": round(pen_x, 6), "y": round(pen_y, 6),
            "ink": ask.get("hole") or "#0A0908", "op": [round(min(1.0, 3.0 * v), 4) for v in e[:-1]] + [0.0], "role": "big", "on": a, "z": 9}
    # a hair of light on the edge that faces the lamp: a pale copy of the letters a pixel and a half past them,
    # covered by the letters themselves except for that edge
    rim = {"id": f"{ask['id']}.rim", "k": "word", "f": [a, z], "t": text, "v": vname + ".rim",
           "x": [round(pen_x + ux * (lift * v + 0.0016), 6) for v in e], "y": [round(pen_y + uy * (lift * v + 0.0016) * W / H, 6) for v in e],
           "s": [round(1.0 + 0.022 * v, 5) for v in e], "ink": "#FFF4E2", "op": [round(min(0.55, 2.0 * v), 4) for v in e], "role": "big", "on": a, "z": 10}
    items, f = [hole, rim], a
    while f < z:
        sh = sense.shot_at(l1, f)
        g = f + 1
        while g < z and abs(e[g - a] - e[f - a]) < 1e-9 and sense.shot_at(l1, g) is sh:
            g += 1                                           # frames at one height under one shot are one junction
        p = sense.path_of(l1, work, (sh or {}).get("pic"))
        if sh is None or not p:
            f = g
            continue
        info = sense.probe(p)
        v = e[f - a]
        dx, dy = ux * lift * v, uy * lift * v * W / H         # the lift in frame fractions (x of the width, y of the height)
        m = 1.0 + 0.022 * v                                   # nearer the lens: a little larger
        crops = []
        for q in range(f, g):
            c = sense.crop_at(sh, q, info["w"], info["h"], W, H)
            cw, ch = c[2] - c[0], c[3] - c[1]
            # a letter at screen point p shows the source that sat at  centre + (p - d - centre) / m
            crops.append([round(c[0] + (cx - (dx + cx) / m) * cw, 6), round(c[1] + (cy - (dy + cy) / m) * ch, 6),
                          round(c[0] + (cx + (1 - dx - cx) / m) * cw, 6), round(c[1] + (cy + (1 - dy - cy) / m) * ch, 6)])
        it = {"id": f"{ask['id']}.l{f - a:03d}", "k": "join", "mode": "glyph", "stage": "top", "f": [f, g], "pic": sh["pic"],
              "t": text, "v": vname, "x0": round(pen_x + dx, 6), "y0": round(pen_y + dy, 6), "s": round(m, 5), "a": 1.0 if v > 0 else 0.0,
              "dim": round(1.0 - (1.0 - dim) * min(v, 1.0), 4), "crop": crops if len(crops) > 1 else crops[0], "z": 9}
        if sh.get("src"):
            it["src"] = [round(sense.src_time(sh, f, fps), 5), round(sense.src_time(sh, g, fps), 5)]
        for k in ("look", "window", "treat", "step"):
            if k in sh:
                it[k] = sh[k]
        gr = dict({"lift": 0.0, "gamma": 1.0, "gain": 1.0, "sat": 1.0, "contrast": 1.0, "temp": 0.0, "tint": 0.0},
                  **(sh.get("grade") or (l1.get("film") or {}).get("grade") or {}))
        gr["gain"] = round(gr["gain"] * (1.0 + 0.20 * v), 4)   # lifted into the light
        it["grade"] = gr
        items.append(it)
        f = g
    return {"items": items, "voices": {vname: voice, vname + ".rim": {k: v for k, v in voice.items() if k != "shadow"}},
            "marks": [{"f": a, "what": "diecut lift"}, {"f": z - 1, "what": "diecut seat"}],
            "seat": {"x": sx, "y": sy, "w": bw, "h": bh, "f": [a, z]},
            "log": {"px": voice["px"], "seat": [round(sx, 4), round(sy, 4)], "junctions": len(items) - 2}}
