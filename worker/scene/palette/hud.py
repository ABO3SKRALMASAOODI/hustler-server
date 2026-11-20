"""The HUD: a box that follows a face (or the thing the picture is about) with a mono label, fine print in a corner
typed like a stage note, and data set on a close-up (a ring of dots that fills to a share, bars that grow to their
values). Thin flat rectangles and words: nothing the engine does not already draw.

The face is YuNet's on the frames the viewer sees, followed and smoothed along the shot; with nobody in the frame
the box follows the picture's busiest region (its gradient energy). The words, the values and the labels are the
caller's.
"""
import math

import numpy as np

from . import kit as K
from ..alien import sense as asense


def _base(l1, work, ask, sense):
    o = K.opts(ask)
    W, H, fps, dur = K.size(l1)
    K.scaled(l1)
    return o, W, H, fps, dur, K.Sense(l1, work, sense), K.palette_of(l1, o)


def _smooth(rows, k=5):
    a = np.asarray(rows, np.float64)
    if len(a) < 3:
        return a
    k = min(k, len(a) // 2 * 2 - 1) if len(a) > 2 else 1
    if k < 3:
        return a
    pad = np.pad(a, ((k // 2, k // 2), (0, 0)), mode="edge")
    w = np.hanning(k + 2)[1:-1]
    w /= w.sum()
    return np.stack([np.convolve(pad[:, c], w, mode="valid") for c in range(a.shape[1])], 1)


def busy_box(rgb):
    """The picture's busiest region (its gradient energy, blurred), as [x0, y0, x1, y1] fractions, or None."""
    import cv2
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    e = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    e = cv2.GaussianBlur(e, (0, 0), 0.04 * g.shape[0])
    th = np.percentile(e, 88)
    m = (e >= th).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = st[i, 0], st[i, 1], st[i, 2], st[i, 3]
    H, W = g.shape
    return [x / W, y / H, (x + w) / W, (y + h) / H]


def follow(l1, work, a, z, step=2):
    """Per frame of [a, z): the box to follow, [x0, y0, x1, y1] fractions (the face, else the busiest region).
    Returns (boxes, what) or (None, why)."""
    fr = asense.shot_frames(l1, work, a, z, size=(270, 480))
    if not fr:
        return None, "no picture to follow"
    n = z - a
    rows, seen = [None] * n, 0
    prev = None
    for i in range(0, n, step):
        f = a + i
        im = fr.get(f)
        if im is None:
            continue
        fs = K.detect_faces(im)
        if fs:
            if prev is not None:
                fs.sort(key=lambda b: abs((b[0] + b[2]) / 2 - (prev[0] + prev[2]) / 2) + abs((b[1] + b[3]) / 2 - (prev[1] + prev[3]) / 2))
            rows[i] = fs[0]
            prev = fs[0]
            seen += 1
    what = "face"
    if seen < max(2, (n // step) * 0.3):
        rows, seen = [None] * n, 0
        for i in range(0, n, max(step, 4)):
            im = fr.get(a + i)
            bb = busy_box(im) if im is not None else None
            if bb:
                rows[i] = bb
                seen += 1
        what = "region"
        if not seen:
            return None, "nothing in the frame to follow"
    idx = [i for i in range(n) if rows[i] is not None]
    out = np.zeros((n, 4))
    for c in range(4):
        out[:, c] = np.interp(np.arange(n), idx, [rows[i][c] for i in idx])
    cuts = sorted({sh["f"][0] for sh in l1.get("items") or [] if sh.get("k") == "shot" and a < sh["f"][0] < z})
    edges = [0] + [c - a for c in cuts] + [n]
    for p, q in zip(edges, edges[1:]):                  # smoothed along each shot, never across a cut
        if q - p >= 3:
            out[p:q] = _smooth(out[p:q], 7)
    return out.tolist(), what


def track(l1, work, ask, sense=None):
    """A thin box that follows the face (or the picture's subject) with corner ticks and a mono label typed on."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 40)), text=o.get("t") if isinstance(o.get("t"), str) else None)
    boxes, what = follow(l1, work, a, z)
    if boxes is None:
        return {"skip": f"track: {what}"}
    col = o.get("c") or ("#FFFFFF" if o.get("white", True) else pal["accent"])
    style = o.get("style") or K.rng_of(l1, ask).choice(["corners", "box"])
    tx, ty = 0.0028, 0.0016                              # 3 px lines at 1080 x 1920
    pad = 0.12
    B = []
    for b in boxes:
        w, h = b[2] - b[0], b[3] - b[1]
        x0, y0, x1, y1 = b[0] - pad * w, b[1] - pad * h, b[2] + pad * w, b[3] + pad * h
        x0, y0 = max(x0, 0.02), max(y0, K.MARK[3] + 0.01 if x1 > K.MARK[0] else 0.11)
        x1, y1 = min(x1, 0.98), min(y1, 0.97)
        B.append((x0, y0, x1, y1))
    n = z - a
    lines = []
    if style == "box":
        lines = [lambda q: [q[0], q[1], q[2], q[1] + ty], lambda q: [q[0], q[3] - ty, q[2], q[3]],
                 lambda q: [q[0], q[1], q[0] + tx, q[3]], lambda q: [q[2] - tx, q[1], q[2], q[3]]]
    else:
        L = 0.24

        def tick(cx, cy, hx, vy):
            return [lambda q, cx=cx, cy=cy, hx=hx: [q[0] + (q[2] - q[0]) * (cx if hx > 0 else cx - L), q[1] + (q[3] - q[1]) * cy - (0 if cy == 0 else ty),
                                                    q[0] + (q[2] - q[0]) * (cx + L if hx > 0 else cx), q[1] + (q[3] - q[1]) * cy + (ty if cy == 0 else 0)],
                    lambda q, cx=cx, cy=cy, vy=vy: [q[0] + (q[2] - q[0]) * cx - (0 if cx == 0 else tx), q[1] + (q[3] - q[1]) * (cy if vy > 0 else cy - L),
                                                    q[0] + (q[2] - q[0]) * cx + (tx if cx == 0 else 0), q[1] + (q[3] - q[1]) * (cy + L if vy > 0 else cy)]]
        for cx, cy, hx, vy in ((0, 0, 1, 1), (1, 0, -1, 1), (0, 1, 1, -1), (1, 1, -1, -1)):
            lines += tick(cx, cy, hx, vy)
    # the box draws itself in: its lines grow from their corners over the first five frames
    grow = K.ease_out(n, 0.0, 1.0, frames=5)
    items = []
    for j, fn in enumerate(lines):
        rr = []
        for q in range(n):
            r = fn(B[q])
            cxm, cym = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            g = grow[q]
            rr.append([round(cxm - (cxm - r[0]) * g, 5), round(cym - (cym - r[1]) * g, 5), round(cxm + (r[2] - cxm) * g, 5), round(cym + (r[3] - cym) * g, 5)])
        items.append(K.rect(f"{ask['id']}.ln{j}", (a, z), rr, col, a=0.95, z=4))
    label = str(o.get("label") or o.get("t") or "").upper().strip()
    voices = {}
    stand = K.others_t(l1, a, z) + K.caption_boxes(l1, a, z)
    cap = K.caption_top(l1, a, z)
    taken = []                                           # (box, f0, f1) of the label, for the note to clear
    log_seat = {}
    if label:
        vl = K.mono(o.get("px", 36))
        vn = f"pal.{ask['id']}.lab"
        voices[vn] = vl
        f0, f1, xs, ys, side = seat_label(vl, label, B, a + 2, z, a, W, H, stand, cap, LABEL_SEATS)
        log_seat["label"] = side
        k = min(len(label), 12)
        for q in range(k):
            m = max(1, round(len(label) * (q + 1) / k))
            g0, g1 = f0 + q, (f0 + 1 + q if q < k - 1 else f1)
            if g1 <= g0 or g0 >= f1:
                continue
            items.append(K.word(f"{ask['id']}.lab{q:02d}", (g0, g1), label[:m], vn, xs[g0 - a:g1 - a], ys[g0 - a:g1 - a], col, z=8, role="graphic", on=a,
                                op=0.95))
        taken += [(K.drawn(vl, label, xs[i], ys[i], W, H), a + i, a + i + 1) for i in range(f0 - a, f1 - a, 4)]
    note = str(o.get("note") or "").upper().strip()
    if note:
        vnn = f"pal.{ask['id']}.note"
        voices[vnn] = K.mono(30.0)
        f0, f1, xs, ys, side = seat_label(voices[vnn], note, B, min(a + 6, z - 1), z, a, W, H, stand + taken, cap, NOTE_SEATS)
        log_seat["note"] = side
        if f1 > f0:
            items.append(K.word(f"{ask['id']}.note", (f0, f1), note, vnn, xs[f0 - a:f1 - a], ys[f0 - a:f1 - a], col, z=8, role="graphic", on=a, op=0.8))
    return {"items": items, "voices": voices, "marks": [{"f": a, "what": "track locks", "kind": "hud", "strength": 0.4}],
            "log": {"follows": what, "style": style, "frames": n, "seats": log_seat}}


# where a label can sit on the tracked box: (name, preference) and its pen from the box (x0, y0, x1, y1) and the line's
# ink box (bx): above the top edge, inside it, under the bottom edge, right-aligned over the top, beside the box on
# either side at its top, or stacked over a label set above it
_SIDE = 0.015
LABEL_SEATS = (("above", 0.0, lambda q, bx, W, H: (q[0] - bx[0] / W, q[1] - 0.010 - bx[3] / H)),
               ("inside", 0.05, lambda q, bx, W, H: (q[0] + 0.012 - bx[0] / W, q[1] + 0.012 - bx[1] / H)),
               ("right", 0.06, lambda q, bx, W, H: (q[2] - bx[2] / W, q[1] - 0.010 - bx[3] / H)),
               ("beside", 0.07, lambda q, bx, W, H: (q[2] + _SIDE - bx[0] / W, q[1] - bx[1] / H)),
               ("before", 0.07, lambda q, bx, W, H: (q[0] - _SIDE - bx[2] / W, q[1] - bx[1] / H)),
               ("below", 0.08, lambda q, bx, W, H: (q[0] - bx[0] / W, q[3] + 0.012 - bx[1] / H)))
NOTE_SEATS = (("below", 0.0, lambda q, bx, W, H: (q[0] - bx[0] / W, q[3] + 0.014 - bx[1] / H)),
              ("inside", 0.05, lambda q, bx, W, H: (q[0] + 0.012 - bx[0] / W, q[3] - 0.012 - bx[3] / H)),
              ("right", 0.06, lambda q, bx, W, H: (q[2] - bx[2] / W, q[3] + 0.014 - bx[1] / H)),
              ("beside", 0.07, lambda q, bx, W, H: (q[2] + _SIDE - bx[0] / W, q[3] - bx[3] / H)),
              ("before", 0.07, lambda q, bx, W, H: (q[0] - _SIDE - bx[2] / W, q[3] - bx[3] / H)),
              ("beside_top", 0.08, lambda q, bx, W, H: (q[2] + _SIDE - bx[0] / W, q[1] + 0.034 - bx[1] / H)),
              ("above", 0.08, lambda q, bx, W, H: (q[0] - bx[0] / W, q[1] - 0.010 - bx[3] / H)),
              ("stacked", 0.09, lambda q, bx, W, H: (q[0] - bx[0] / W, q[1] - 0.044 - bx[3] / H)))


def seat_label(v, text, B, f0, f1, a, W, H, stand, cap, seats):
    """(first frame, last frame, xs, ys, seat) for a mono line riding a tracked box over [f0, f1): the seat whose
    line stays off the watermark, off the type laid before it (``stand``: (box, from, to)) and inside the frame,
    measured on every fourth frame as drawn. The line is kept inside the frame and off the watermark on every frame;
    when type laid before it still stands where the best seat is, its hold is shortened to the frames that are
    clear. xs and ys run from frame a."""
    _, bx = K.ink_box(v, text)
    lw = (bx[2] - bx[0]) / W
    g = 0.012                                            # a breath between two pieces of type, never touching
    stand = [([b[0] - g, b[1] - g, b[2] + g, b[3] + g], f0_, f1_) for b, f0_, f1_ in stand]
    best = None
    for name, pref, pen in seats:
        xs, ys, cost, hits = [], [], pref, []
        for i, q in enumerate(B):
            x, y = pen(q, bx, W, H)
            x = min(max(x, 0.03 - bx[0] / W), 0.97 - lw - bx[0] / W)
            blk = [x + bx[0] / W, y + bx[1] / H, x + bx[2] / W, y + bx[3] / H]
            if K.overlap(blk, K.MARK) > 0:                 # off the watermark: the line drops below the keep-out
                y += K.MARK[3] + 0.006 - blk[1]
                blk = [blk[0], K.MARK[3] + 0.006, blk[2], K.MARK[3] + 0.006 + (bx[3] - bx[1]) / H]
                cost += 0.004
            if blk[1] < 0.105:
                y += 0.105 - blk[1]
            if blk[3] > 0.97:
                y -= blk[3] - 0.97
                cost += 0.01
            xs.append(round(x, 6))
            ys.append(round(y, 6))
            f = a + i
            if f0 <= f < f1 and i % 2 == 0:
                area = max((blk[2] - blk[0]) * (blk[3] - blk[1]), 1e-6)
                hit = sum(K.overlap(blk, ob) for ob, g0, g1 in stand if g0 <= f < g1) / area
                if blk[3] > cap - 0.01:
                    cost += 0.02
                if hit > 0.02:
                    hits.append(f)
                cost += 0.5 * hit
        if best is None or cost < best[0]:
            best = (cost, name, xs, ys, hits)
    _, name, xs, ys, hits = best
    g0, g1 = f0, f1
    if hits:                                             # the type laid first keeps its place: the line yields its frames
        first, last = min(hits), max(hits)
        if first - f0 >= 10:
            g1 = first
        elif f1 - (last + 2) >= 10:
            g0 = last + 2
        else:                                            # it stands there the whole time: the line gives it the frame
            g1 = g0
            name += " (yields)"
    return g0, max(g1, g0), xs, ys, name


CORNERS = ("tl", "tr", "bl")


def fineprint(l1, work, ask, sense=None):
    """Fine print in a corner, typed on like a stage note: a date and place, a source, a timecode."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    raw = o.get("lines") or str(o.get("t") or "").split("|")
    lines = [str(x).upper().strip() for x in raw if str(x).strip()]
    if not lines:
        return {"skip": "fineprint needs words"}
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 60)))
    asked = o.get("corner") if o.get("corner") in CORNERS else K.rng_of(l1, ask).choice(CORNERS)
    v = K.mono(o.get("px", 36))
    lh = 1.35 * v["px"] / H
    wmax = max(K.ink_box(v, ln)[1][2] for ln in lines) / W
    stand = K.others(l1, a, z)
    faces = S.faces_over(a, z)
    cap = K.caption_top(l1, a, z)

    def at(c):
        if c == "tl":
            x, y = 0.06, 0.135
            if x + wmax > K.MARK[0]:                    # a line long enough to reach the watermark starts under it
                y = K.MARK[3] + 0.03
        elif c == "tr":
            x, y = 0.94 - wmax, K.MARK[3] + 0.03
        else:
            x, y = 0.06, cap - 0.04 - lh * len(lines)
        return max(x, 0.03), y

    def cost(c):                                       # the asked corner first; another when the type laid before is there
        x, y = at(c)
        blk = [x, y, x + wmax, y + lh * len(lines)]
        area = max((blk[2] - blk[0]) * (blk[3] - blk[1]), 1e-6)
        return (0.0 if c == asked else 0.05) + sum(K.overlap(blk, b) for b in stand) / area + 40 * K.overlap(blk, K.MARK) / area \
            + 0.5 * sum(K.overlap(blk, f) for f in faces) / area
    corner = min((asked,) + tuple(c for c in CORNERS if c != asked), key=cost)
    x0, y0 = at(corner)
    tone, _ = S.ground([x0, y0, x0 + wmax, y0 + lh * len(lines)], a, z)
    ink = K.DARK if tone > 0.62 else ("#FFFFFF" if o.get("white", True) else pal["accent"])
    vn = f"pal.{ask['id']}.fp"
    items = []
    f = a
    for li, ln in enumerate(lines):
        y = round(y0 + lh * (li + 1) - 0.3 * v["px"] / H, 6)
        k = max(1, min(len(ln), 16))
        for q in range(k):
            m = max(1, round(len(ln) * (q + 1) / k))
            f1 = f + 1 if q < k - 1 else z
            if f >= z:
                break
            items.append(K.word(f"{ask['id']}.l{li}.{q:02d}", (f, max(f1, f + 1)), ln[:m], vn, round(x0, 6), y, ink, z=8, role="graphic", on=a, op=0.88))
            f += 1
    if o.get("dot"):
        items.append(K.rect(f"{ask['id']}.dot", (a, z), [round(x0 - 0.026, 5), round(y0 + 0.4 * lh, 5), round(x0 - 0.012, 5), round(y0 + 0.4 * lh + 0.008, 5)], "#FF3B30"))
    return {"items": items, "voices": {vn: v}, "marks": [], "log": {"corner": corner, "asked": asked, "lines": lines}}


def _share(o):
    v = float(o.get("value", o.get("to", 0.5)))
    return v / 100.0 if v > 1.0 else max(v, 0.0)


def donut(l1, work, ask, sense=None):
    """A ring of dots beside a close-up that fills to a share as the share is said, the figure counting in its middle."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    if o.get("value") is None and o.get("to") is None:
        return {"skip": "donut needs a value"}
    share = min(_share(o), 1.0)
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 45)), text=o.get("t") if isinstance(o.get("t"), str) else None)
    land = hit[0][1] if hit else a + 4
    faces = S.faces_over(a, z)
    r = float(o.get("r", 0.13))
    rpx = r * W
    cap = K.caption_top(l1, a, z)
    stand = K.others(l1, a, z)
    lab_h = 0.06 if o.get("label") else 0.0
    cands = []
    if faces:
        f = faces[0]
        left, right = f[0], 1 - f[2]
        sides = [(f[0] - r - 0.05), (f[2] + r + 0.05)]
        if right >= left:
            sides.reverse()
        for k, cx in enumerate(sides):
            for dy in (0.0, -0.12, 0.12):
                cands.append((cx, (f[1] + f[3]) / 2 + dy, 0.05 * k + 0.1 * abs(dy)))
    else:
        cands = [(0.5, 0.40, 0.0), (0.5, 0.30, 0.05), (0.5, 0.52, 0.05)]
    best = None
    for cx, cy, pref in cands:                         # beside the face, off the type laid before it and the watermark
        cx = min(max(cx, r + 0.05), 0.95 - r)
        cy = min(max(cy, K.MARK[3] + 0.03 + rpx / H), cap - 0.04 - rpx / H - lab_h)
        blk = [cx - r, cy - rpx / H, cx + r, cy + rpx / H + lab_h]
        area = (blk[2] - blk[0]) * (blk[3] - blk[1])
        c = pref + sum(K.overlap(blk, b) for b in stand) / area + 40 * K.overlap(blk, K.MARK) / area \
            + 0.6 * sum(K.overlap(blk, fb) for fb in faces[:1]) / area
        if best is None or c < best[0]:
            best = (c, cx, cy)
    _, cx, cy = best
    N = int(o.get("dots", 40))
    dv = {"font": "InterDisplay-Bold.ttf", "px": K.px(34.0), "track": 0}
    _, db = K.ink_box(dv, "•")
    acc = o.get("c") or pal["accent"]
    items = []
    n_fill = int(round(share * N))
    fill_frames = max(min(land - a, 18), 8)
    voices = {f"pal.{ask['id']}.dot": dv}
    for i in range(N):
        th = -math.pi / 2 + 2 * math.pi * i / N
        px, py = cx + r * math.cos(th), cy + rpx * math.sin(th) / H
        x = round(px - (db[0] + db[2]) / 2 / W, 6)
        y = round(py - (db[1] + db[3]) / 2 / H, 6)
        lit = a + int(fill_frames * i / max(n_fill, 1)) if i < n_fill else None
        if lit is None or lit > a:
            items.append(K.word(f"{ask['id']}.d{i:02d}", (a, lit if lit is not None else z), "•", f"pal.{ask['id']}.dot", x, y, "#FFFFFF", z=7, role="graphic", on=a, op=0.30))
        if lit is not None:
            items.append(K.word(f"{ask['id']}.D{i:02d}", (lit, z), "•", f"pal.{ask['id']}.dot", x, y, acc, z=7, role="graphic", on=lit))
    from .type import counter
    pct = round(share * 100)
    sub = dict(K.opts(ask), **{"from": 0, "to": pct, "fmt": o.get("fmt") or "{n}%", "unit": None, "underline": False, "label": None,
                               "seat": [cx, cy + 0.025], "width": r * 1.25, "cap": 0.06, "lands": a + fill_frames, "at": a, "until": None, "hold": z - a - fill_frames})
    for k in ("args", "id", "dev", "t"):
        sub.pop(k, None)
    got = counter(l1, work, dict(sub, id=f"{ask['id']}.n", dev="counter"), sense)
    if not got.get("skip"):
        items += got["items"]
        voices.update(got.get("voices") or {})
    label = str(o.get("label") or "").upper().strip()
    if label:
        vl = K.mono(32.0)
        lx, ly = K.place(vl, label, cx, cy + rpx / H + 0.045, W, H)
        voices[f"pal.{ask['id']}.lab"] = vl
        items.append(K.word(f"{ask['id']}.lab", (a, z), label, f"pal.{ask['id']}.lab", lx, ly, "#FFFFFF", z=7, role="graphic", on=a, op=0.9))
    return {"items": items, "voices": voices, "marks": [{"f": a + fill_frames, "what": "donut full", "kind": "hud", "strength": 0.6}],
            "seat": {"x": cx, "y": cy, "f": [a, z]}, "log": {"share": share, "dots": N, "lit": n_fill}}


def bar(l1, work, ask, sense=None):
    """Bars that grow to their values over a close-up, each with its label and its number counting up."""
    o, W, H, fps, dur, S, pal = _base(l1, work, ask, sense)
    bars = o.get("bars") or ([{"label": o.get("label") or "", "value": o.get("value", o.get("to"))}] if (o.get("value") is not None or o.get("to") is not None) else [])
    bars = [b for b in bars if isinstance(b, dict) and b.get("value") is not None]
    if not bars:
        return {"skip": "bar needs values"}
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 45)), text=o.get("t") if isinstance(o.get("t"), str) else None)
    vals = [float(b["value"]) for b in bars]
    top_v = max(max(vals), 1e-6)
    rel = all(v <= 1.0 for v in vals)
    width = 0.62
    gap = 0.055
    bh = gap * len(bars)
    x0, base = K.seat(l1, S, a, z, width + 0.2, bh, prefer=float(o.get("prefer", 0.66)), align="l", x=0.08)
    y0 = base - bh
    vl = K.mono(32.0)
    vn = f"pal.{ask['id']}.lab"
    items, voices = [], {vn: vl}
    acc = o.get("c") or pal["accent"]
    for i, b in enumerate(bars):
        y = y0 + gap * i + 0.03
        share = vals[i] if rel else vals[i] / top_v
        f0 = min(a + 3 * i, z - 2)
        n = z - f0
        g = K.ease_out(n, 0.0, share, frames=10)
        items.append(K.rect(f"{ask['id']}.t{i}", (f0, z), [round(x0, 5), round(y, 5), round(x0 + width, 5), round(y + 0.011, 5)], "#FFFFFF", a=0.16, z=3))
        items.append(K.rect(f"{ask['id']}.b{i}", (f0, z), [[round(x0, 5), round(y, 5), round(x0 + width * q, 5), round(y + 0.011, 5)] for q in g],
                            b.get("c") or acc, a=1.0, z=4))
        lab = str(b.get("label") or "").upper()
        if lab:
            items.append(K.word(f"{ask['id']}.l{i}", (f0, z), lab, vn, round(x0, 6), round(y - 0.008, 6), "#FFFFFF", z=8, role="graphic", on=f0, op=0.9))
        shown = b.get("text") or (f"{round(vals[i] * 100)}%" if rel else f"{vals[i]:g}")
        items.append(K.word(f"{ask['id']}.v{i}", (min(f0 + 8, z - 1), z), str(shown), vn, [round(x0 + width * q + 0.012, 6) for q in g[min(8, n - 1):]],
                            round(y + 0.011, 6), acc, z=8, role="graphic", on=f0))
    return {"items": items, "voices": voices, "marks": [{"f": a + 10, "what": "bars grown", "kind": "hud", "strength": 0.5}],
            "seat": {"x": x0, "y": base, "f": [a, z]}, "log": {"bars": len(bars)}}


DEVICES = {
    "track": {"plan": track, "layer": 4, "needs": ("faces",), "fallback": ("fineprint",),
              "card": 'track at until label note style(corners|box) c. A thin box locks onto the face (or the picture\'s subject) and follows it, a mono label typed on',
              "amplifies": "a person singled out, a suspect, a target, 'this guy', surveillance, a reveal of who"},
    "fineprint": {"plan": fineprint, "layer": 4, "needs": (), "fallback": ("typewriter", "verb"),
                  "card": 'fineprint at until lines[] corner(tl|tr|bl) dot. Fine print typed into a corner like a stage note: date, place, source, timecode',
                  "amplifies": "a date and place, a source, a document, an archive, a time jump"},
    "donut": {"plan": donut, "layer": 4, "needs": (), "fallback": ("bar", "counter", "verb"),
              "card": 'donut at value(0-1 or %) label fmt. A ring of dots beside a close-up fills to the share as it is said, the figure counting inside',
              "amplifies": "a percentage, a share, odds, a part of a whole"},
    "bar": {"plan": bar, "layer": 4, "needs": (), "fallback": ("counter", "verb"),
            "card": 'bar at bars[{label value text c}]. Bars grow to their values over the picture, labels in mono, numbers riding the ends',
            "amplifies": "a comparison of two or three amounts, before and after, us against them"},
}
