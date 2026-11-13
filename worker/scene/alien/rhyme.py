"""rhyme: a cut found by shape. Every frame of every picture the film holds is searched for its round thing
(a wheel, a dial, a lens, a face, a coin); a chain of pictures is cut so that thing stands on one seat,
centre and radius registered to the pixel, and keeps growing through every cut while the world round it
changes. A human editor finds three such pairs by eye in a day (metromedia DdO49: 25 shots in 3 chains,
unregistered); this reads thousands of frames, locks the shape inside moving video and solves every crop.

ask  {"dev": "rhyme", "at": [a, z], "pool": [asset ids] (default: every picture of the scene),
      "cuts": [frames each picture starts on, in [a, z)] or "n": how many pictures,
      "seat": [x, y] of the shape on the frame (default found), "r": its radius in frame widths (default found),
      "grow": 1.22 (how much it grows across the chain), "mono": true|false (default: true from 3 pictures),
      "order": [asset ids] to hold an order the words ask for, "step": source frames between looks (3),
      "hints": {asset id: {"t": seconds, "at": [x, y]}} where the caller points at the round thing it means}

Signal only: gradients, a Hough vote and a support count. Which words earn a rhyme is the caller's call.
"""
import math

import numpy as np

from . import sense

SIDE = 240            # the short side a picture is searched at: a round thing a viewer notices is 30 px or more here
MAX_UP = 2.6          # the tightest a found picture is drawn (output pixels a source pixel); past it the shape goes soft
MIN_SUPPORT = 0.50    # the share of a circle's rim that has to be an edge pointing along its radius, all the way round


def _grad(gray):
    import cv2
    g = cv2.GaussianBlur(gray, (0, 0), 1.2).astype(np.float32)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    return gx, gy, mag, float(np.percentile(mag, 80)) + 1e-3


_ANG = np.linspace(0, 2 * math.pi, 96, endpoint=False)
_COS, _SIN = np.cos(_ANG).astype(np.float32), np.sin(_ANG).astype(np.float32)


def support(G, cx, cy, r):
    """How much of a circle's rim is a real edge whose gradient runs along the radius: 0..1. Vectorised over
    arrays of candidates (cx, cy, r of one shape)."""
    gx, gy, mag, ref = G
    h, w = mag.shape
    cx, cy, r = [np.atleast_1d(np.asarray(v, np.float32)) for v in (cx, cy, r)]
    px = cx[:, None] + r[:, None] * _COS[None]
    py = cy[:, None] + r[:, None] * _SIN[None]
    inside = (px >= 1) & (px < w - 1) & (py >= 1) & (py < h - 1)
    xi, yi = np.clip(px.astype(np.int32), 0, w - 1), np.clip(py.astype(np.int32), 0, h - 1)
    m = mag[yi, xi]
    along = np.abs(gx[yi, xi] * _COS[None] + gy[yi, xi] * _SIN[None]) / np.maximum(m, 1e-3)
    hit = (m > ref) * along * inside
    whole = hit.reshape(len(hit), 8, -1).mean(axis=2).min(axis=1)     # the weakest eighth of the rim: an arc is not a circle
    s = hit.mean(axis=1) * (0.35 + 0.65 * inside.mean(axis=1))        # a circle half out of the picture is half a circle
    return s * np.clip(0.4 + whole / np.maximum(0.45 * hit.mean(axis=1), 1e-3) * 0.6, 0.4, 1.0)


def circles(gray):
    """The round things of one picture, best first: [(cx, cy, r, support)] in pixels of ``gray``."""
    import cv2
    h, w = gray.shape
    lo, hi = int(0.10 * min(w, h)), int(0.60 * min(w, h))
    G = _grad(gray)
    found = cv2.HoughCircles(cv2.medianBlur(gray, 5), cv2.HOUGH_GRADIENT, dp=1.5, minDist=lo, param1=120, param2=26, minRadius=lo, maxRadius=hi)
    if found is None:
        return [], G
    c = found[0][:14]
    c, s = refine(G, c[:, 0], c[:, 1], c[:, 2])
    order = np.argsort(-s)
    return [(float(c[i, 0]), float(c[i, 1]), float(c[i, 2]), float(s[i])) for i in order if s[i] >= MIN_SUPPORT * 0.8], G


_DX, _DY, _DR = [a.ravel().astype(np.float32) for a in np.meshgrid(np.arange(-3, 4), np.arange(-3, 4), np.arange(-2, 3), indexing="ij")]


def refine(G, cx, cy, r, reach=1.0):
    """Each circle moved to the place and size near it with the most rim: sub-vote accuracy, and the step a
    tracker takes from one frame to the next."""
    out, sc = [], []
    for x, y, q in zip(np.atleast_1d(cx), np.atleast_1d(cy), np.atleast_1d(r)):
        s = support(G, x + _DX * reach, y + _DY * reach, q + _DR * reach)
        i = int(np.argmax(s))
        out.append((x + _DX[i] * reach, y + _DY[i] * reach, q + _DR[i] * reach))
        sc.append(float(s[i]))
    return np.asarray(out, np.float32).reshape(-1, 3), np.asarray(sc, np.float32)


def _size(pw, ph):
    k = SIDE / min(pw, ph)
    return max(int(pw * k) // 2 * 2, 2), max(int(ph * k) // 2 * 2, 2)


def scan(l1, work, pool, step=3, need_s=0.0):
    """Every candidate frame of the pool: [{"pic", "t", "c": (cx, cy, r) as fractions of the source's width
    (cx, r) and height (cy), "s": support, "pw", "ph", "video"}], and how many frames were looked at."""
    out, looked = [], 0
    for aid in pool:
        p = sense.path_of(l1, work, aid)
        if not p:
            continue
        try:
            info = sense.probe(p)
        except Exception:      # noqa: BLE001 - a picture that cannot be read is no candidate
            continue
        if not info["w"] or not info["h"]:
            continue
        w, h = _size(info["w"], info["h"])
        if info["fps"] == 0.0:
            grays = [(0.0, sense.still(p, size=(w, h), gray=True))]
        else:
            rate = 30.0 / max(int(step), 1)
            last = max(info["dur"] - need_s, 0.0)
            grays = ((i / rate, g) for i, g in enumerate(sense.frames(p, 0.0, None, rate, (w, h), True)) if i / rate <= last + 1e-6)
        for t, g in grays:
            if g is None:
                continue
            looked += 1
            cs, _ = circles(g)
            for cx, cy, r, s in cs[:2]:
                out.append({"pic": aid, "t": round(float(t), 4), "c": (cx / w, cy / h, r / w), "s": s, "pw": info["w"], "ph": info["h"],
                            "video": info["fps"] > 0})
    return out, looked


def clusters(cands):
    """Candidates grouped into sightings: one round thing seen through consecutive looks of one picture is one
    sighting, and a long upload holds many. Each sighting is given by its best frame, with ``key`` and ``span``."""
    out = []
    for pic in sorted({c["pic"] for c in cands}):
        run = []
        for c in sorted((c for c in cands if c["pic"] == pic), key=lambda c: (c["t"], -c["s"])):
            near = [g for g in run if c["t"] - g["last"] <= 0.45 and abs(c["c"][0] - g["c"][0]) < 0.6 * g["c"][2]
                    and abs(c["c"][1] - g["c"][1]) * c["ph"] / c["pw"] < 0.6 * g["c"][2] and 0.7 < c["c"][2] / g["c"][2] < 1.4]
            if near:
                g = near[0]
                g["last"], g["c"] = c["t"], c["c"]
                if c["s"] > g["best"]["s"]:
                    g["best"] = c
            else:
                run.append({"first": c["t"], "last": c["t"], "c": c["c"], "best": c})
        for i, g in enumerate(run):
            out.append(dict(g["best"], key=f"{pic}@{i}", span=[g["first"], g["last"]]))
    return out


def crop_for(c, seat, R, pw, ph, W, H):
    """The crop that sets a source circle c on the seat at radius R (frame widths), and the upscale it costs."""
    cw = c[2] / R
    ch = cw * (pw / ph) * (H / W)
    x0, y0 = c[0] - seat[0] * cw, c[1] - seat[1] * ch
    return [x0, y0, x0 + cw, y0 + ch], W / max(cw * pw, 1e-6)


def fits(c, seat, R, pw, ph, W, H, slack=0.004):
    crop, up = crop_for(c, seat, R, pw, ph, W, H)
    return up <= MAX_UP and crop[0] >= -slack and crop[1] >= -slack and crop[2] <= 1 + slack and crop[3] <= 1 + slack


def track(path, t, n, fps, c, pw, ph):
    """A circle followed through n frames of its video from second t: [(cx, cy, r)] as fractions, and how many
    frames kept their rim. A frame that loses it keeps the last place: the picture drifts, the cut still lands."""
    w, h = _size(pw, ph)
    cur = np.array([c[0] * w, c[1] * h, c[2] * w], np.float32)
    out, kept = [], 0
    for i, g in enumerate(sense.frames(path, t, n, fps, (w, h), True)):
        if i >= n:
            break
        G = _grad(g)
        q, s = refine(G, cur[0], cur[1], cur[2], reach=1.0)
        if float(s[0]) < MIN_SUPPORT * 0.5:                    # it moved far in one frame (a drawn film jumps): look wider
            q, s = refine(G, cur[0], cur[1], cur[2], reach=2.5)
        if float(s[0]) >= MIN_SUPPORT * 0.5:
            cur = 0.5 * cur + 0.5 * q[0] if i else q[0]         # half a step: a rim that flickers does not shake the frame
            kept += 1
        out.append((float(cur[0]) / w, float(cur[1]) / h, float(cur[2]) / w))
    while len(out) < n:
        out.append(out[-1] if out else c)
    return out, kept


def plan(l1, work, ask):
    fps = float(l1.get("fps") or 30)
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    a, z = [int(v) for v in ask["at"]]
    cuts = sorted(int(f) for f in (ask.get("cuts") or []) if a < int(f) < z)
    if not cuts and ask.get("n"):
        n = max(int(ask["n"]), 2)
        cuts = [a + int(round((z - a) * i / n)) for i in range(1, n)]
    starts = [a] + cuts
    ends = cuts + [z]
    n = len(starts)
    if n < 2:
        return {"skip": "a rhyme needs two pictures"}
    assets = l1.get("assets") or {}
    pool = [p for p in (ask.get("pool") or sorted(assets)) if p in assets and p != "matte" and assets[p].get("kind") in ("image", "video")]
    cands, looked = scan(l1, work, pool, int(ask.get("step") or 3), need_s=0.2)
    hints = ask.get("hints") or {}

    def hinted(c):
        h = hints.get(c["pic"])
        if not h:
            return True
        if h.get("t") is not None and abs(c["t"] - float(h["t"])) > float(h.get("within") or 0.35):
            return False
        if h.get("at"):
            dx, dy = c["c"][0] - float(h["at"][0]), (c["c"][1] - float(h["at"][1])) * c["ph"] / c["pw"]
            return (dx * dx + dy * dy) ** 0.5 <= max(c["c"][2], 0.08)
        return True
    cands = [c for c in cands if hinted(c)]                # the caller pointed at the round thing it means
    best = {}
    for c in clusters(cands):                              # one candidate a sighting: its roundest frame
        if c["s"] >= MIN_SUPPORT:
            best[c["key"]] = c
    centre = (0.5, 0.42)
    if ask.get("seat"):
        seats = [tuple(float(v) for v in ask["seat"])]
    else:                                                  # every seat a shape may take, the middle first
        seats = sorted(((x / 100.0, y / 100.0) for x in range(32, 69, 4) for y in range(30, 63, 4)),
                       key=lambda q: (q[0] - centre[0]) ** 2 + (q[1] - centre[1]) ** 2)
    radii = [float(ask["r"])] if ask.get("r") else [round(0.40 - 0.02 * i, 2) for i in range(13)]
    grows = [float(ask["grow"])] if ask.get("grow") else [1.22, 1.12, 1.06, 1.03]
    must = set(ask.get("order") or [])
    pick, grow = None, grows[0]
    for R in radii:                                        # the largest shape that n pictures can hold on one seat, as it grows
        for g in grows:
            for seat in seats:
                ok = [c for c in best.values() if fits(c["c"], seat, R, c["pw"], c["ph"], W, H) and fits(c["c"], seat, R * g, c["pw"], c["ph"], W, H)]
                have = {c["pic"] for c in ok}
                if len(ok) >= n and must <= have | (must - {c["pic"] for c in best.values()}):
                    pick, grow = (R, seat, ok), g
                    break
            if pick:
                break
        if pick:
            break
    if not pick:
        return {"skip": f"no {n} pictures share a round shape on one seat ({len(best)} had one, {looked} frames read)",
                "log": {"frames_read": looked, "round": len(best)}}
    R0, seat, ok = pick
    ok.sort(key=lambda c: -c["s"])
    want = [p for p in (ask.get("order") or []) if p in {c["pic"] for c in ok}]
    chain = [next(c for c in ok if c["pic"] == p) for p in want[:n]]
    for c in ok:                                           # then the roundest sightings, no picture twice while others wait
        if len(chain) >= n:
            break
        if c not in chain and c["pic"] not in {q["pic"] for q in chain}:
            chain.append(c)
    for c in ok:                                           # one long take: its sightings a second or more apart
        if len(chain) >= n:
            break
        if c not in chain and all(q["pic"] != c["pic"] or abs(q["t"] - c["t"]) >= 1.0 for q in chain):
            chain.append(c)
    if len(chain) < n:
        return {"skip": f"only {len(chain)} round sightings stand apart", "log": {"frames_read": looked, "round": len(best)}}
    if not want:
        chain = chain[1:] + chain[:1]                      # the strongest shape is the one the chain lands on
    mono = ask.get("mono")
    mono = (n >= 3) if mono is None else bool(mono)
    keep, gone, was = sense.carve(l1, a, z)
    inherit = {k: v for k, v in (was[0] if was else {}).items() if k in ("look", "grade", "shutter")}
    items, radius, log = [], [], []
    for i, (s, e, c) in enumerate(zip(starts, ends, chain)):
        m = e - s
        path = sense.path_of(l1, work, c["pic"])
        play = m / fps
        if c["video"]:
            # a sighting near its clip's end plays what is left of the clip across the shot, slowed: the cut still starts on it
            play = min(play, max(sense.probe(path)["dur"] - c["t"] - 1.0 / fps, 1.0 / fps))
            tr, kept = track(path, c["t"], m, m / play, c["c"], c["pw"], c["ph"])
            frozen = kept < max(2, m // 2)
            if frozen:
                tr = [c["c"]] * m
        else:
            tr, kept, frozen = [c["c"]] * m, m, True
        crops = []
        for j in range(m):
            u = (s + j - a) / max(z - a - 1, 1)
            R = R0 * grow ** (u ** 1.6)                    # the shape gathers speed into its last picture
            crop, _ = crop_for(tr[j], seat, R, c["pw"], c["ph"], W, H)
            dx, dy = min(max(-crop[0], 0.0), 1.0), min(max(-crop[1], 0.0), 1.0)
            crop = [crop[0] + dx, crop[1] + dy, crop[2] + dx, crop[3] + dy]
            ox, oy = max(crop[2] - 1.0, 0.0), max(crop[3] - 1.0, 0.0)
            crops.append([round(crop[0] - ox, 6), round(crop[1] - oy, 6), round(crop[2] - ox, 6), round(crop[3] - oy, 6)])
            radius.append(round(R, 5))
        it = dict(inherit, id=f"{ask['id']}.r{i}", k="shot", f=[s, e], pic=c["pic"], crop=crops)
        if c["video"]:
            it["src"] = [c["t"], c["t"]] if frozen else [c["t"], round(c["t"] + play, 5)]
        if mono:
            it["treat"] = {"mode": "mono"}
        items.append(it)
        log.append({"pic": c["pic"], "t": c["t"], "support": round(c["s"], 3), "tracked": kept, "of": m, "held": bool(c["video"] and frozen)})
    return {"items": keep + items, "drop": gone, "marks": [{"f": s, "what": "rhyme cut"} for s in starts[1:]],
            "seat": {"x": seat[0], "y": seat[1], "r": radius, "f": [a, z]},
            "log": {"frames_read": looked, "round": len(best), "fit": len(ok), "r": R0, "grow": grow, "seat": list(seat), "chain": log, "mono": mono}}
