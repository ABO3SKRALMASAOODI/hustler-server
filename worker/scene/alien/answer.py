"""answer: a picture that answers the exact word, born inside that word. On the word's first consonant the
word itself stands on the frame in display type, and its letters are a window: through them the picture
of the thing just named is already running, where it will stand. On the vowel the voice leans on, a few
frames later, that picture takes the whole frame. Then the next word does the same over it. A named thing
is seen inside its own name before it is seen at all, and every cut sits on a syllable of the voice, to
the frame. The judge's list bursts run on a fixed beat of 2 or 5 frames; these run on the speaker.

Which picture answers which word is the caller's (a model looked at both). The machine's part is the
rest: the frame the voice attacks, the steadiest sharp second of each clip, one camera push carried from
the letters into the full frame, and the same look inside the letters as outside.

ask  {"dev": "answer", "at": [a, z], "picks": [{"f": frame the word starts, "pic": asset id, "word": "VEGAS",
              "t": seconds into a clip (default: its steadiest), "focus": [x, y] of the picture to push toward}],
      "font": "InterDisplay-Black.ttf", "width": 0.82 (of the frame), "seat": [centre x, baseline y] (default 0.5, 0.5),
      "dim": 0.32 (what is left of the outgoing picture while a word stands), "audio": programme-time audio}
"""
import math

import numpy as np

from . import glyphs, sense
from .rhyme import _size
from .. import bake

PUSH = bake.DRIFT_RATE[1]            # the references' slow push: scale per second (median)
OPEN = 4                             # frames a word stands as a window before its picture takes the frame
SEEN = 0.50                          # the level (0..1, display) the picture inside the letters is brought up to, to read on a dimmed frame


def lift_for(path, t, crop, box, W, H):
    """The gain that brings the part of a picture a word will show up to a level a viewer reads in four frames."""
    try:
        g = sense.still(path, t, gray=True)
        v = sense.view(g, crop, W // 8, H // 8)
        x0, y0, x1, y1 = [int(max(c, 0)) for c in (box[0] * W / 8, box[1] * H / 8, box[2] * W / 8, box[3] * H / 8)]
        m = float(v[y0:max(y1, y0 + 1), x0:max(x1, x0 + 1)].mean()) / 255.0
    except Exception:      # noqa: BLE001 - a picture that cannot be measured is shown as it is
        return 1.0, 0.5
    return float(min(max(SEEN / max(m, 0.04), 1.0), 2.4)), m


def steady(path, need_s, step=2):
    """The second of a clip to show: its sharpest stretch that moves least. Returns seconds into the clip."""
    import cv2
    info = sense.probe(path)
    if info["fps"] == 0.0 or info["dur"] <= need_s + 0.05:
        return 0.0
    w, h = _size(info["w"], info["h"])
    rate = 30.0 / step
    sharp, move, prev = [], [], None
    for g in sense.frames(path, 0.0, None, rate, (w, h), True):
        sharp.append(float(cv2.Laplacian(g, cv2.CV_32F).var()))
        move.append(0.0 if prev is None else float(np.mean(np.abs(g.astype(np.int16) - prev))))
        prev = g.astype(np.int16)
    if len(sharp) < 3:
        return 0.0
    sharp, move = np.asarray(sharp), np.asarray(move)
    sharp, move = sharp / max(sharp.max(), 1e-6), move / max(np.percentile(move, 90), 1e-6)
    n = max(int(round(need_s * rate)), 1)
    last = max(len(sharp) - n, 1)
    score = [float(sharp[i:i + n].mean() - 0.6 * move[i:i + n].mean() - 2.0 * (move[i:i + n].max() > 4.0)) for i in range(last)]   # never across a cut
    return round(int(np.argmax(score)) / rate, 4)


def push_crop(pw, ph, W, H, zoom, focus=None):
    """The cover crop of a picture, closed in by ``zoom`` toward a point of it."""
    c = sense.cover(pw, ph, W, H)
    cw, ch = (c[2] - c[0]) / zoom, (c[3] - c[1]) / zoom
    fx, fy = focus or ((c[0] + c[2]) / 2, (c[1] + c[3]) / 2)
    x0 = min(max(fx - cw / 2, c[0]), c[2] - cw)
    y0 = min(max(fy - ch / 2, c[1]), c[3] - ch)
    return [round(x0, 6), round(y0, 6), round(x0 + cw, 6), round(y0 + ch, 6)]


def plan(l1, work, ask):
    fps = float(l1.get("fps") or 30)
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    a, z = [int(v) for v in ask["at"]]
    assets = l1.get("assets") or {}
    picks = sorted((p for p in ask.get("picks") or [] if p.get("pic") in assets and a <= int(p["f"]) < z - 3), key=lambda p: int(p["f"]))
    if not picks:
        return {"skip": "answer needs a word and a picture for it"}
    lev = sense.level(l1, work, ask)
    heard = float(lev.max()) > 0
    onset, land = [], []
    for i, p in enumerate(picks):
        o = int(p["f"])
        if onset and o < land[-1] + 3:
            continue                                         # two words inside three frames: the second keeps the first's picture
        pk = sense.peak_near(lev, o + 2, back=0, ahead=4) if heard else o + 3
        pk = min(max(pk, o + 2), o + 7, z - 2)
        o = max(min(o, pk - OPEN), (land[-1] + 2) if land else a)      # a word needs four frames to be read as a window
        onset.append(o)
        land.append(pk)
        p["_i"] = len(onset) - 1
    picks = [p for p in picks if "_i" in p]
    n = len(picks)
    ends = land[1:] + [z]
    vbase = {"font": ask.get("font") or "InterDisplay-Black.ttf", "track": 0}
    sx, sy = [float(v) for v in (ask.get("seat") or (0.5, 0.5))]
    dim = float(ask.get("dim") if ask.get("dim") is not None else 0.32)
    keep, gone, was = sense.carve(l1, land[0], z)
    inherit = {k: v for k, v in (was[0] if was else {}).items() if k in ("look", "grade", "shutter")}
    items, voices, log = list(keep), {}, []
    for i, p in enumerate(picks):
        o, c, e = onset[i], land[i], ends[i]
        path = sense.path_of(l1, work, p["pic"])
        info = sense.probe(path)
        video = info["fps"] > 0
        need = (e - o) / fps
        t0 = float(p["t"]) if p.get("t") is not None else (steady(path, need) if video else 0.0)
        if video:
            t0 = max(min(t0, info["dur"] - need - 1.0 / fps), 0.0)
            t1 = min(t0 + need, max(info["dur"] - 1.0 / fps, t0 + 0.05))
            rate = (t1 - t0) / max(e - o, 1)
        zoom = [math.exp(PUSH * (f - o) / fps) for f in range(o, e)]
        crops = [push_crop(info["w"], info["h"], W, H, s, p.get("focus")) for s in zoom]
        word = str(p.get("word") or "").strip()
        if word and c - o >= 2:
            vname = f"alien.{ask['id']}.{i}"
            v = dict(vbase)
            v["px"] = round(glyphs.fit_px(v, word, float(ask.get("width") or 0.82) * W, cap_px=0.2 * H), 2)
            _, b = glyphs.box(v, word, v["px"])
            voices[vname] = v
            m = c - o
            px0, py0 = sx - (b[0] + b[2]) / 2 / W, sy - (b[1] + b[3]) / 2 / H
            gain, seen = lift_for(path, t0, crops[0], (px0 + b[0] / W, py0 + b[1] / H, px0 + b[2] / W, py0 + b[3] / H), W, H)
            gr = dict({"lift": 0.0, "gamma": 1.0, "gain": 1.0, "sat": 1.0, "contrast": 1.0, "temp": 0.0, "tint": 0.0},
                      **(inherit.get("grade") or (l1.get("film") or {}).get("grade") or {}))
            gr["gain"] = round(gr["gain"] * gain, 4)
            deep = dim * (0.7 if seen < 0.2 else 1.0)          # a dark picture needs a darker frame round its letters
            j = dict(inherit, id=f"{ask['id']}.w{i}", k="join", mode="glyph", f=[o, c], pic=p["pic"], t=word, v=vname,
                     x0=round(px0, 6), y0=round(py0, 6), grade=gr,
                     s=[round(1.0 + 0.05 * (q / max(m - 1, 1)) ** 1.5, 5) for q in range(m)], a=1.0,
                     dim=[round(1.0 + (deep - 1.0) * min((q + 1) / 2.0, 1.0), 4) for q in range(m)], crop=crops[:m])
            if video:
                j["src"] = [round(t0, 5), round(t0 + rate * m, 5)]
            items.append(j)
            # the name stays a breath on its picture, so the eye ties the two: plain type on the same letters, gone in 6 frames
            n_after = min(6, e - c)
            if n_after >= 2:
                items.append({"id": f"{ask['id']}.n{i}", "k": "word", "f": [c, c + n_after], "t": word, "v": vname, "x": round(px0, 6), "y": round(py0, 6),
                              "s": 1.05, "ink": "#FFFFFF", "op": [round(0.9 * (1 - q / n_after) ** 1.6, 4) for q in range(n_after)], "role": "big", "on": c, "z": 7})
        sh = dict(inherit, id=f"{ask['id']}.p{i}", k="shot", f=[c, e], pic=p["pic"], crop=crops[c - o:])
        if video:
            sh["src"] = [round(t0 + rate * (c - o), 5), round(t1, 5)]
        items.append(sh)
        log.append({"word": word, "pic": p["pic"], "onset": o, "lands": c, "t": round(t0, 3), "frames": e - c})
    return {"items": items, "drop": gone, "voices": voices,
            "marks": [{"f": c, "what": "answer lands"} for c in land],
            "log": {"picks": log, "heard": heard}}
