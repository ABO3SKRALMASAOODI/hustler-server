"""Burned-in screen insets and picture-in-picture boxes, measured from the
pixels — no vision model.

WHY (judges, Oct 2026)

A podcast that shows a browser screenshot burns it into the programme as a
box over the studio shot (the Joe Rogan layout: the guest's article in the
bottom-right quadrant while the host reads it). A 9:16 crop knows nothing of
it: the Elon showcase spent 7 s on a crop of the neon wall above a
screenshot cut off at the left ('o were current video game players
made...'), with no face while Rogan spoke, and the next shot kept a
half-cropped sliver of the browser down its right edge. Every judge flagged
it. A human editor either shows the screen legibly (the whole box, fitted)
or keeps it out of the face crop.

WHAT IT FINDS

A box burned over the footage is an axis-aligned rectangle with straight,
hard edges drawn on all four sides that sits in the SAME place across the
frames it is in. Measured
on a downscaled grey frame: long vertical and horizontal runs of a hard luma
step (the same edge test as subject.hard_edge_lines), paired into
rectangles whose sides meet. Scenery rarely closes a rectangle with four
straight edges — curtains, people, signs and microphones never do; a door
or a picture frame on the wall can, which is why every consumer of these
boxes ADVISES (naming the box and a fitted layout to show it) and only ever
acts by keeping a crop clear of a box it would otherwise cut through. On the
Elon, Thiel and Jobs showcase sources it finds the browser box (and nothing
else) on every frame it is fully on screen.

Pure measurement: returns rectangles in fractions of the frame, never
writes an EDL.
"""
import math
import os
import subprocess

WORK_W = 640               # detection width (positions are fractions)
STEP = 18                  # luma step across an edge (0-255, 2 px apart)
STEP_SOFT = 12             # ...across a closing side (and its fragments
SOFT_SIDE = 0.08           # this long count)
MIN_SIDE = 0.12            # a side spans at least this share of the frame
MIN_W = 0.15               # a box at least this wide and tall (fractions)
MIN_AREA = 0.03            # ...and covering this much of the frame
MAX_AREA = 0.70            # past this it is the shot, not a box in it
EDGE_TOL_PX = 4            # sides meet within this many work pixels
FLUSH = 0.012              # a side this close to the frame edge lies on it
SAME_IOU = 0.85            # the same box in two frames
FLAT_SHARE = 0.35          # screen content: this much of it is flat...
SHARP_SHARE = 0.02         # ...and this much of it is sharp detail
SAMPLE_FPS = 1.0           # write-time sampling over kept footage
MAX_FRAMES = 48            # per measurement
BUDGET_S = 30.0
SEEK_PAST_S = 60.0         # longer footage is sampled by seeks, not decoded


def _segments(edge, min_len):
    """Vertical runs of True in ``edge`` (rows x cols): [(col, r0, r1)]."""
    import numpy as np
    h, w = edge.shape
    out = []
    # run starts and ends per column, vectorised by padding
    pad = np.zeros((h + 2, w), bool)
    pad[1:-1] = edge
    d = np.diff(pad.astype(np.int8), axis=0)
    for c in range(w):
        starts = np.nonzero(d[:, c] == 1)[0]
        ends = np.nonzero(d[:, c] == -1)[0]
        for s, e in zip(starts, ends):
            if e - s >= min_len:
                out.append((c, int(s), int(e)))
    return out


def _lines(gray, axis, step=STEP, min_side=MIN_SIDE):
    """Long straight hard edges of one grey frame: [(pos, a, b)] in work
    pixels — for axis 'x' vertical edges (pos a column, a..b rows), for
    'y' horizontal ones (pos a row, a..b columns)."""
    import numpy as np
    g = gray.astype(np.float32)
    if axis == "y":
        g = g.T
    h, w = g.shape
    d = np.zeros_like(g)
    d[:, 1:-1] = g[:, 2:] - g[:, :-2]
    raw = np.abs(d) > step
    edge = raw.copy()
    edge[:, 1:] |= raw[:, :-1]
    edge[:, :-1] |= raw[:, 1:]
    edge[1:-1, :] |= edge[:-2, :] & edge[2:, :]
    segs = sorted(_segments(edge, int(min_side * h)))
    out = []
    for c, a, b in segs:
        if out and c - out[-1][0] <= 2 and \
                min(b, out[-1][2]) - max(a, out[-1][1]) > .5 * (b - a):
            if b - a > out[-1][2] - out[-1][1]:
                out[-1] = (c, a, b)
            continue
        out.append((c, a, b))
    return out


def _covered(segs, pos, lo, hi, tol):
    """Share of [lo, hi] covered by the segments lying at ``pos`` ± tol."""
    spans = sorted((max(a, lo), min(b, hi)) for p, a, b in segs
                   if abs(p - pos) <= tol and min(b, hi) > max(a, lo))
    got, cur = 0, lo
    for a, b in spans:
        if b <= cur:
            continue
        got += b - max(a, cur)
        cur = b
    return got / float(max(1, hi - lo))


def _pairs(A, B, size_a, size_b, tol, flush):
    """Boxes from pairs of parallel edges in A (pos, a, b) — two sides of
    one box, level with each other (one may be shortened where something
    in front of the box hides part of it) — closed at both ends by edges in
    B: each end covered at least 60% of the way between the pair, the two
    ends 75% on average (a side breaks where the box's own content or a
    speaker in front of it matches its colour). [(p0, p1, a, b)] in work
    pixels along (A's axis, B's axis)."""
    out = []
    A = [s for s in A if flush * size_a < s[0] < size_a - 1 - flush * size_a]
    for i, s1 in enumerate(A):
        for s2 in A[i + 1:]:
            p0, p1 = sorted((s1[0], s2[0]))
            if p1 - p0 < MIN_W * size_a or p1 - p0 > size_a - 2:
                continue
            l1, l2 = s1[2] - s1[1], s2[2] - s2[1]
            over = min(s1[2], s2[2]) - max(s1[1], s2[1])
            if over < .85 * min(l1, l2) or min(l1, l2) < .5 * max(l1, l2):
                continue                  # not level: not one box
            best = None
            for a in {s1[1], s2[1]}:
                for b in {s1[2], s2[2]}:
                    if b - a < MIN_W * size_b:
                        continue
                    if a <= flush * size_b or b >= size_b - 1 - flush * size_b:
                        continue          # a side on the frame's own edge
                    ca = _covered(B, a, p0, p1, tol)
                    cb = _covered(B, b, p0, p1, tol)
                    if min(ca, cb) >= .6 and ca + cb >= 1.5 and \
                            (best is None or ca + cb > best[0]):
                        best = (ca + cb, a, b)
            if best:
                out.append((p0, p1, best[1], best[2]))
    return out


def screen_like(gray, x0, y0, x1, y1):
    """Does the box at work pixels (x0, y0)-(x1, y1) hold SCREEN content
    over the scene, rather than scenery that happens to be rectangular?

    * its sides hold their polarity: along at least three of the four, the
      box is consistently darker (or lighter) than what lies outside — a
      textured wall or a shirt's pattern flips sign along an edge;
    * its inside is a screen: mostly flat (UI backgrounds, slides, page
      colour: at least FLAT_SHARE of it barely changes pixel to pixel) yet
      carrying sharp detail (type, icons: SHARP_SHARE of it steps hard) —
      a light panel or a window is flat and empty, a painting or a crowd
      is all texture.
    Measured on the real sources (798 frames of 67): every screenshot,
    slide and in-car screen passes; walls, shelves, windows, light panels
    and set dressing do not."""
    import numpy as np
    g = gray.astype(np.float32)
    H, W = g.shape
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    if x1 - x0 < 12 or y1 - y0 < 12:
        return False
    inner = g[y0 + 3:y1 - 2, x0 + 3:x1 - 2]
    gx = np.abs(np.diff(inner, axis=1))[:-1, :]
    gy = np.abs(np.diff(inner, axis=0))[:, :-1]
    grad = np.maximum(gx, gy)
    if float((grad < 2.0).mean()) < FLAT_SHARE or \
            float((grad > 40.0).mean()) < SHARP_SHARE:
        return False

    def polarity(inside, outside):
        d = inside - outside
        d = d[np.abs(d) > STEP_SOFT]
        if len(d) < 5:
            return 0.0
        return abs(float(np.sign(d).mean()))
    sides = [
        polarity(g[y0:y1, min(W - 1, x0 + 1)], g[y0:y1, max(0, x0 - 2)]),
        polarity(g[y0:y1, max(0, x1 - 1)], g[y0:y1, min(W - 1, x1 + 2)]),
        polarity(g[min(H - 1, y0 + 1), x0:x1], g[max(0, y0 - 2), x0:x1]),
        polarity(g[max(0, y1 - 2), x0:x1], g[min(H - 1, y1 + 1), x0:x1])]
    return sum(1 for v in sides if v >= .75) >= 3


def rects_in(gray):
    """Boxes burned over one grey frame: [[x0, y0, x1, y1]] fractions.

    Two parallel edges of a box (equal length, level) closed at both ends
    by perpendicular edges, searched both ways round so a side broken
    where the box's content touches it is still found."""
    import numpy as np
    h0, w0 = gray.shape[:2]
    if w0 != WORK_W:
        import cv2
        H = max(2, int(round(h0 * WORK_W / float(w0))))
        gray = cv2.resize(gray, (WORK_W, H), interpolation=cv2.INTER_AREA)
    gray = np.asarray(gray)
    H, W = gray.shape[:2]
    V = _lines(gray, "x")                 # (col, row0, row1)
    Hs = _lines(gray, "y")                # (row, col0, col1)
    # The closing sides are judged on SOFT edges: a dark box over a dark
    # shirt steps far less than its other sides do.
    Vw = _lines(gray, "x", STEP_SOFT, SOFT_SIDE)
    Hw = _lines(gray, "y", STEP_SOFT, SOFT_SIDE)
    tol = EDGE_TOL_PX
    found = []
    # Four drawn sides, never the frame's own edge: a box flush to a
    # corner is far more often a window, a dark wall or a panel the frame
    # cuts (on 798 frames of 67 real sources every flush find was scenery)
    # than a burned-in screen.
    for x0, x1, y0, y1 in _pairs(V, Hw, W, H, tol, FLUSH):
        found.append((x0, y0, x1, y1))
    for y0, y1, x0, x1 in _pairs(Hs, Vw, H, W, tol, FLUSH):
        found.append((x0, y0, x1, y1))
    rects = []
    for x0, y0, x1, y1 in found:
        r = [x0 / W, y0 / H, (x1 + 1) / W, y1 / H]
        area = (r[2] - r[0]) * (r[3] - r[1])
        ar = (r[2] - r[0]) * W / max(1e-6, (r[3] - r[1]) * H)
        if not MIN_AREA <= area <= MAX_AREA or not .5 <= ar <= 4.0:
            continue
        if not screen_like(gray, x0, y0, x1, y1):
            continue
        rects.append([round(v, 4) for v in r])
    # one box per inset: a double border (or its shadow) finds the box
    # twice, both searches find it, and a panel of the screen's own layout
    # is a box inside it — keep the outermost
    rects.sort(key=lambda r: -(r[2] - r[0]) * (r[3] - r[1]))
    out = []
    for r in rects:
        if all(iou(r, o) < SAME_IOU and not _within(r, o) for o in out):
            out.append(r)
    return out


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _within(r, o, slack=.01):
    """``r`` lies (mostly) inside ``o``: a border's inner edge, a panel of
    the box's own content, or a box that reaches past it on one side only
    by joining one of its panels to the scene."""
    if (r[0] >= o[0] - slack and r[1] >= o[1] - slack
            and r[2] <= o[2] + slack and r[3] <= o[3] + slack):
        return True
    ix = max(0.0, min(r[2], o[2]) - max(r[0], o[0]))
    iy = max(0.0, min(r[3], o[3]) - max(r[1], o[1]))
    area = (r[2] - r[0]) * (r[3] - r[1])
    return area > 0 and ix * iy >= .7 * area


def _decode_gray(path, a, b, fps, width, timeout):
    """Grey frames of SOURCE [a, b] at ``fps``, ``width`` wide."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-threads", "2",
           "-ss", f"{max(0.0, a):.3f}", "-i", path, "-t", f"{max(0.05, b - a):.3f}",
           "-an", "-sn", "-dn", "-map", "0:v:0",
           "-vf", f"fps={fps:.4f},scale={width}:-2:flags=area,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    probe = ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path]
    r = subprocess.run(probe, capture_output=True, text=True, timeout=timeout)
    try:
        sw, sh = (int(v) for v in r.stdout.strip().split(",")[:2])
    except ValueError:
        return []
    height = max(2, int(round(width * sh / float(sw) / 2.0)) * 2)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        return []
    size = width * height
    n = len(r.stdout) // size
    buf = np.frombuffer(r.stdout[:n * size], np.uint8)
    return [buf[i * size:(i + 1) * size].reshape(height, width) for i in range(n)]


def measure(path, windows, fps=SAMPLE_FPS, max_frames=MAX_FRAMES,
            budget_s=BUDGET_S, timeout=60):
    """[(t, [rect, ...])] the boxes found at ``fps`` (fewer past
    ``max_frames``) over SOURCE ``windows`` [(a, b)] of the video at
    ``path``. [] when nothing can be decoded or the budget runs out."""
    import time
    windows = [(float(a), float(b)) for a, b in windows or []
               if float(b) - float(a) > .05]
    if not windows or not path or not os.path.exists(path):
        return []
    total = sum(b - a for a, b in windows)
    fps = min(float(fps), max_frames / max(total, 1e-6))
    deadline = time.monotonic() + float(budget_s)
    out = []
    if total > SEEK_PAST_S:
        # Long footage: one seek per sample (a decode of one frame each)
        # instead of decoding every frame of minutes of footage.
        step = 1.0 / fps
        ts, acc = [], 0.0
        for a, b in windows:
            t = a + max(step / 2.0 - acc, 0.0)
            while t < b:
                ts.append(t)
                t += step
            acc = (acc + (b - a)) % step
        for t in ts[:max_frames]:
            if time.monotonic() > deadline:
                return []
            try:
                grays = _decode_gray(path, t, t + .05, 25, WORK_W,
                                     min(float(timeout), deadline - time.monotonic()))
            except Exception:
                grays = []
            if grays:
                try:
                    out.append((round(t, 3), rects_in(grays[0])))
                except Exception:
                    out.append((round(t, 3), []))
        return out
    for a, b in windows:
        left = deadline - time.monotonic()
        if left <= 0:
            return []
        # sample the middle of each step, never the window's first frame
        # (a cut's first frame is often a blend)
        off = min(.5 / fps, (b - a) / 2.0)
        try:
            grays = _decode_gray(path, a + off, b, fps, WORK_W,
                                 min(float(timeout), left))
        except subprocess.TimeoutExpired:
            return []
        except Exception:
            grays = []
        for i, g in enumerate(grays):
            t = a + off + i / fps
            if t > b + 1e-6:
                break
            try:
                out.append((round(t, 3), rects_in(g)))
            except Exception:
                out.append((round(t, 3), []))
    return out


def boxes(frames, step=None, probe=None):
    """The persistent boxes in ``frames`` (measure's output): [{"rect",
    "spans": [(t0, t1)], "seen"}] — a box found in at least two samples
    (or in the only one), its rect the median of its sightings, its spans
    the runs of samples it was found in (one missed sample inside a run is
    bridged), each widened by half a sampling step. ``probe(t, rect)`` ->
    bool (one decoded frame, e.g. frame_has) refines each span edge that
    lies between two consecutive samples to ~1/8 of the step: a box that
    slides in or out mid-step starts and ends where it does."""
    rows = sorted(frames or [], key=lambda f: f[0])
    if not rows:
        return []
    if step is None:
        gaps = sorted(b[0] - a[0] for a, b in zip(rows, rows[1:]) if b[0] > a[0])
        step = gaps[len(gaps) // 2] if gaps else 1.0
    clusters = []
    for i, (t, rects) in enumerate(rows):
        for r in rects:
            for c in clusters:
                if iou(c["ref"], r) >= SAME_IOU:
                    c["hits"].append((i, t, r))
                    break
            else:
                clusters.append({"ref": r, "hits": [(i, t, r)]})
    out = []
    for c in clusters:
        if len(c["hits"]) < min(2, len(rows)):
            continue
        rect = [sorted(h[2][k] for h in c["hits"])[len(c["hits"]) // 2]
                for k in range(4)]
        idx = sorted(h[0] for h in c["hits"])
        runs = [[idx[0], idx[0]]]
        for k in idx[1:]:
            if k - runs[-1][1] <= 2:
                runs[-1][1] = k
            else:
                runs.append([k, k])
        spans = []
        for a, b in runs:
            t0, t1 = rows[a][0] - step / 2.0, rows[b][0] + step / 2.0
            if probe is not None:
                if a > 0 and rows[a][0] - rows[a - 1][0] <= 1.5 * step:
                    t0 = _edge(probe, rect, rows[a - 1][0], rows[a][0])
                if b + 1 < len(rows) and rows[b + 1][0] - rows[b][0] <= 1.5 * step:
                    t1 = _edge(probe, rect, rows[b + 1][0], rows[b][0])
            spans.append((round(t0, 3), round(t1, 3)))
        out.append({"rect": [round(v, 4) for v in rect], "spans": spans,
                    "seen": len(c["hits"])})
    out.sort(key=lambda b: -b["seen"])
    return out


def _edge(probe, rect, absent, shown, rounds=3):
    """Where the box appears between a sample without it (``absent``) and
    one with it (``shown``): a bisection on single decoded frames."""
    for _ in range(rounds):
        mid = (absent + shown) / 2.0
        try:
            hit = bool(probe(mid, rect))
        except Exception:
            break
        if hit:
            shown = mid
        else:
            absent = mid
    return (absent + shown) / 2.0


def frame_has(path, t, rect, timeout=20):
    """True when the frame at SOURCE second t shows a box like ``rect``."""
    grays = _decode_gray(path, t, t + .05, 25, WORK_W, timeout)
    return bool(grays) and any(iou(r, rect) >= SAME_IOU or _within(r, rect, .02)
                               and iou(r, rect) >= .6 for r in rects_in(grays[0]))


def present(box, t0, t1):
    """Seconds of SOURCE [t0, t1] the box is on screen."""
    return sum(max(0.0, min(b, t1) - max(a, t0)) for a, b in box.get("spans") or [])


def cuts_through(rect, window, min_share=.04):
    """True when the crop ``window`` [x0, y0, x1, y1] (source fractions)
    shows part of ``rect`` but not all of it: a box cropped through. A
    sliver thinner than ``min_share`` of the window is still a cut box."""
    ix0, iy0 = max(rect[0], window[0]), max(rect[1], window[1])
    ix1, iy1 = min(rect[2], window[2]), min(rect[3], window[3])
    if ix1 - ix0 <= 1e-4 or iy1 - iy0 <= 1e-4:
        return False
    whole = (rect[0] >= window[0] - 1e-4 and rect[2] <= window[2] + 1e-4
             and rect[1] >= window[1] - 1e-4 and rect[3] <= window[3] + 1e-4)
    return not whole


def overlap_share(rect, window):
    """The share of the window's width the box covers where they meet."""
    ix0, ix1 = max(rect[0], window[0]), min(rect[2], window[2])
    iy0, iy1 = max(rect[1], window[1]), min(rect[3], window[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) / max(1e-6, window[2] - window[0])


def clear_aim(rect, window, keep, bounds=(0.0, 1.0)):
    """The window centre (x) nearest the current one that keeps the box
    wholly out of the window and ``keep`` [k0, k1] (source x fractions)
    inside it, or None. ``window`` [x0, y0, x1, y1]."""
    w = window[2] - window[0]
    c = (window[0] + window[2]) / 2.0
    cands = []
    # the window left of the box, or right of it
    right_edge = rect[0] - .004
    left_edge = rect[2] + .004
    for x0 in (right_edge - w, left_edge):
        x1 = x0 + w
        if x0 < bounds[0] - 1e-6 or x1 > bounds[1] + 1e-6:
            continue
        if keep is not None and not (x0 <= keep[0] and keep[1] <= x1):
            continue
        cands.append(x0 + w / 2.0)
    if not cands:
        return None
    return min(cands, key=lambda v: abs(v - c))


def fit_box(rect, src_w, src_h, W, H, width=.92, y_centre=.5, max_h=.5):
    """A card box [x0, y0, x1, y1] (canvas fractions) that shows ``rect``
    whole at its own aspect: ``width`` of the canvas wide (narrower when
    taller than ``max_h``), centred on ``y_centre``."""
    rw = (rect[2] - rect[0]) * float(src_w)
    rh = (rect[3] - rect[1]) * float(src_h)
    if rw <= 0 or rh <= 0:
        return None
    bw = width * W
    bh = bw * rh / rw
    if bh > max_h * H:
        bh = max_h * H
        bw = bh * rw / rh
    x0 = (W - bw) / 2.0 / W
    y0 = y_centre - bh / 2.0 / H
    y0 = min(max(y0, 0.02), 0.98 - bh / H)
    return [round(x0, 4), round(y0, 4), round(x0 + bw / W, 4),
            round(y0 + bh / H, 4)]


def upscale(rect, src_w, src_h, box, W):
    """How far the box's pixels are enlarged in ``box`` (canvas fractions)."""
    rw = (rect[2] - rect[0]) * float(src_w)
    return (box[2] - box[0]) * W / max(1e-6, rw)
