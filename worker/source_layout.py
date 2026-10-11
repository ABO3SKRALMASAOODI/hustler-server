"""What the main footage's FRAME is made of — measured once per source and
read by every short cut from it.

WHY (the Diamandis run, Oct 10 2026: 0 of 9 shorts shipped)

Peter Diamandis's EP #91 is a stage feed. Elon Musk joins by PHONE: for most
of its 30 minutes the programme is a portrait FaceTime window (a status bar,
call buttons, Peter's own self-view in a corner) composited over a black
graphic carrying two burned-in logos, cut with stage shots. Every editor met
the same frame, solved it alone, and every tool misread it:

  * auto_reframe found a face plus detail all over the graphic, decided a
    crop would lose content and FITTED the whole 16:9 frame (a tiny
    letterboxed call) — or found no steady face and fitted it anyway;
  * set_picture_card(source='auto') framed a medium close-up of the face in
    the WHOLE frame: a rect wider than the call window, so the card showed
    the call on its backdrop — a wide shot of a phone;
  * the children arrived with no frame at all (one render came out 16:9, a
    stock search defaulted to landscape), and each editor measured the call
    window, the self-view and the buttons again by eye.

WHAT IT MEASURES (pixels only, no vision model: ~10 s on a 30 min proxy)

  * CALL WINDOWS — a portrait box with four straight hard sides that holds
    the speaking face, in the same place across the programme (a phone or
    video call, or a vertical video, composited over a broadcast frame).
    Found on the frames where all four sides show, then recognised on every
    other frame by its sides or by the unchanging backdrop around it (a dark
    shirt on a black backdrop hides a side; the graphic around the window
    does not change);
  * inside a call window, its CHROME and the host's SELF-VIEW: what stays
    put while the call's picture changes — the status bar and the call
    buttons (rows a picture must crop off) and the picture-in-picture box
    (a face: kept whole inside the picture, or erased — never cut);
  * per shot: its kind (call / camera / other) and the faces in it.

WHAT IT RECOMMENDS (on demand, for any canvas)

  * the largest card that shows a call at <= 2x enlargement with its chrome
    cropped off and the guest's head inside, with and without a headline
    band; whether a full-bleed crop OF the call window stays under 2x; the
    plain crop centred on the call;
  * a frame for every short (never none): per-shot crop aims that keep each
    face wholly in or wholly out of the crop, fitted where no aim can.

Inheritance: the sidecar lives in the source's INDEX row and every short
shares its parent's original by storage key, so every child reads the same
measurement; make_shorts seeds each child's frame from it. An ordinary
camera podcast measures no call window and every tool behaves as before.

Pure measurement and planning: never writes an EDL."""
import math
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import insets

LAYOUT_VERSION = 1

WORK_W = 640               # analysis width (positions are fractions)
MAX_SAMPLES = 240
SAMPLE_EVERY_S = 2.0
MIN_SHOT_S = 1.0           # shorter shots are not sampled
GRAB_THREADS = 4
BUDGET_S = 90.0
# Call windows: portrait (pixel width/height at most this), tall, with a
# face inside at least this share of the window's width.
CALL_MAX_ASPECT = 0.85
CALL_MIN_H = 0.40
CALL_FACE_MIN = 0.15
CALL_MIN_HITS = 3          # four-sided sightings that found it
CALL_FACE_HITS = 2         # ...of which this many held the face
WINDOW_IOU = 0.85
SIDE_TOL_PX = 4
SIDE_SCORE = 2.2           # sides' coverage (0-4) recognising a known window
SIDE_MIN = 0.5             # ...with both long sides at least this covered
BACKDROP_STEP = 25         # luma a structured backdrop pixel may differ
BACKDROP_MATCH = 0.85      # share of those that must match
BACKDROP_MIN_PX = 0.004    # share of the frame the structured backdrop needs
INSIDE_STD_MIN = 12.0      # a window showing a picture, not a flat fill
OUTSIDE_FACE_W = 0.025     # a face this wide outside the window: a wide shot
WIDE_SHARE = 0.3           # of a window's faced sightings -> 'call_wide'
# Chrome / self-view: pixels that hold still while the call picture moves.
STATIC_MAD = 6.0
STATIC_GRAD = 40.0
MIN_STATIC_SAMPLES = 4
TOP_CHROME_MAX = 0.16      # window-height share the status bar may take
BOTTOM_CHROME_MIN = 0.76   # ...and where the call buttons may start
CHROME_EDGE = 0.05         # a chrome band starts this close to the edge
PIP_NEAR = 0.04            # a control this close to the self-view is its own
CHROME_MIN_PX = 20         # (work pixels) smaller steady specks are scenery
PIP_MIN = (0.10, 0.07)     # self-view box size bounds (window shares)
PIP_MAX = (0.60, 0.50)
BORDER_INSET = 0.004       # source share trimmed inside a window's border
# Cards on a 9:16 canvas (worker/plugins looks.md "Card geometry"): under the
# free-tier mark's zone, above the band the platform covers.
UPSCALE_CAP = 2.0          # picture_cards.SOURCE_UPSCALE_CAP
CARD_TOP = 0.13
CARD_TOP_BAND = 0.24
CARD_BOTTOM = 0.795
PICTURE_FLOOR = 0.54       # the Looks' picture-area floor
HAIR_ABOVE = 0.40          # face-heights of hair above a detector box
CHIN_BELOW = 0.12
SIDE_PAD = 0.15
HEADROOM = 0.06            # of a card's source height above the hair
CALL_LAYOUT_SHARE = 0.5    # kept footage share that makes a call layout
FACED_SHARE = 0.25         # samples with a face that make a camera shot
NEAR_S = 30.0              # unsampled footage borrows shots this close
CALL_CARD_SHARE = 0.6      # card window share that frames the call itself
# agent_tools' resolution-aware window (FRAME_WINDOW_*): past this crop
# enlargement a short starts fitted, unless the fit is a thin strip
WINDOW_UPSCALE = 2.5
WINDOW_MIN_SHARE = 0.4
STRIP_UPSCALE = 3.5
LOWRES_SHORT_SIDE = 720    # picture_cards.LOWRES_SHORT_SIDE: dark bars below


class LayoutError(RuntimeError):
    pass


# --------------------------------------------------------------- sampling
def sample_times(duration, shots=None, cap=MAX_SAMPLES):
    """SOURCE seconds to look at: the middle of every shot of at least
    MIN_SHOT_S first (evenly thinned past ``cap``), then more inside the
    long shots while the cap allows; evenly over the video when there is no
    shot list. Never a shot's first frame."""
    if duration:
        # one look per SAMPLE_EVERY_S of footage at most: a one-minute clip
        # needs a handful, not the cap
        cap = int(min(cap, max(8, float(duration) / SAMPLE_EVERY_S)))
    spans = []
    for s in shots or []:
        try:
            a, b = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b - a >= MIN_SHOT_S:
            spans.append((a, b))
    if not spans:
        if not duration:
            return []
        n = int(min(cap, max(4, float(duration) // 5)))
        return [round(float(duration) * (i + .5) / n, 3) for i in range(n)]
    spans.sort()
    if len(spans) > cap:
        step = len(spans) / float(cap)
        spans = [spans[int(i * step)] for i in range(cap)]
    room = cap - len(spans)
    long_s = sum(max(0.0, b - a - 8.0) for a, b in spans) or 1.0
    ts = []
    for a, b in spans:
        d = b - a
        extra = int(room * max(0.0, d - 8.0) / long_s) if room > 0 else 0
        n = 1 + extra
        ts += [round(a + d * (k + .5) / n, 3) for k in range(n)]
    return sorted(set(ts))[:cap]


def _probe(path, timeout=20):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of",
                        "csv=p=0", path], capture_output=True, text=True,
                       timeout=timeout)
    try:
        w, h = (int(v) for v in r.stdout.strip().split(",")[:2])
    except ValueError:
        raise LayoutError("cannot read the video's size")
    return w, h


def _grab(path, t, width, height, timeout=30):
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-threads", "1",
           "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
           "-an", "-sn", "-dn", "-map", "0:v:0",
           "-vf", f"scale={width}:{height}:flags=area,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return None
    if r.returncode != 0 or len(r.stdout) < width * height:
        return None
    return np.frombuffer(r.stdout[:width * height],
                         np.uint8).reshape(height, width)


def _frames(path, times, budget_s=BUDGET_S):
    sw, sh = _probe(path)
    height = max(2, int(round(WORK_W * sh / float(sw) / 2.0)) * 2)
    deadline = time.monotonic() + budget_s

    def one(t):
        if time.monotonic() > deadline:
            return None
        return _grab(path, t, WORK_W, height)
    # strided order: a budget that runs out leaves an even subset of the
    # programme, never only its first minutes
    n = len(times)
    order = sorted(range(n), key=lambda i: (i * 7919) % max(1, n))
    picked = [times[i] for i in order]
    with ThreadPoolExecutor(GRAB_THREADS) as ex:
        grays = list(ex.map(one, picked))
    return sorted(((t, g) for t, g in zip(picked, grays) if g is not None),
                  key=lambda tg: tg[0])


# --------------------------------------------------------------- per frame
def _cv():
    try:
        import cv2
        return cv2
    except Exception:
        return None


def _faces(cv2, cascades, gray):
    """[[x0, y0, x1, y1]] fractions, biggest first."""
    if cv2 is None or not cascades:
        return []
    import subject
    eq = cv2.equalizeHist(gray)
    hits = subject._faces_in(cv2, cascades[:1], eq)
    if not hits and len(cascades) > 1:
        hits = subject._faces_in(cv2, cascades[1:], eq)
    H, W = gray.shape
    return [[round(x / W, 4), round(y / H, 4), round((x + w) / W, 4),
             round((y + h) / H, 4)]
            for x, y, w, h in subject._distinct(hits)[:4]]


def _polarity(g, x0, y0, x1, y1):
    """Per side (left, right, top, bottom): (consistency of the luma step's
    sign across the side, share of the side that steps at all)."""
    import numpy as np
    H, W = g.shape

    def pol(inside, outside):
        d = inside.astype(np.float32) - outside.astype(np.float32)
        d = d[np.abs(d) > insets.STEP_SOFT]
        if len(d) < 5:
            return 0.0, 0.0
        return abs(float(np.sign(d).mean())), len(d) / float(len(inside))
    return [pol(g[y0:y1, min(W - 1, x0 + 1)], g[y0:y1, max(0, x0 - 2)]),
            pol(g[y0:y1, max(0, x1 - 1)], g[y0:y1, min(W - 1, x1 + 2)]),
            pol(g[min(H - 1, y0 + 1), x0:x1], g[max(0, y0 - 2), x0:x1]),
            pol(g[max(0, y1 - 2), x0:x1], g[min(H - 1, y1 + 1), x0:x1])]


def _lines(gray):
    return {"V": insets._lines(gray, "x"), "H": insets._lines(gray, "y"),
            "Vw": insets._lines(gray, "x", insets.STEP_SOFT, insets.SOFT_SIDE),
            "Hw": insets._lines(gray, "y", insets.STEP_SOFT, insets.SOFT_SIDE)}


def _window_cands(gray, lines):
    """Portrait boxes drawn with four hard sides of one polarity each:
    [[x0, y0, x1, y1]] fractions (insets.py's edge pairing, without its
    screen-content test — a call holds a camera picture, not a page)."""
    H, W = gray.shape
    found = [(x0, y0, x1, y1) for x0, x1, y0, y1 in insets._pairs(
        lines["V"], lines["Hw"], W, H, SIDE_TOL_PX, insets.FLUSH)]
    found += [(x0, y0, x1, y1) for y0, y1, x0, x1 in insets._pairs(
        lines["H"], lines["Vw"], H, W, SIDE_TOL_PX, insets.FLUSH)]
    out = []
    for x0, y0, x1, y1 in found:
        w, h = x1 - x0, y1 - y0
        if h < CALL_MIN_H * H or w > CALL_MAX_ASPECT * h:
            continue
        sides = _polarity(gray, x0, y0, x1, y1)
        if sum(1 for p, n in sides if p >= .7 and n >= .5) < 4:
            continue
        r = [round(x0 / W, 4), round(y0 / H, 4), round((x1 + 1) / W, 4),
             round(y1 / H, 4)]
        if all(insets.iou(r, o) < WINDOW_IOU for o in out):
            out.append(r)
    return out


def _side_score(lines, rect, W, H):
    """How much of ``rect``'s four sides this frame draws (0-4), and the
    lesser of its two long sides."""
    x0, y0, x1, y1 = rect[0] * W, rect[1] * H, rect[2] * W, rect[3] * H
    segV = lines["V"] + lines["Vw"]
    segH = lines["H"] + lines["Hw"]
    left = insets._covered(segV, x0, y0, y1, SIDE_TOL_PX)
    right = insets._covered(segV, x1 - 1, y0, y1, SIDE_TOL_PX)
    top = insets._covered(segH, y0, x0, x1, SIDE_TOL_PX)
    bottom = insets._covered(segH, y1, x0, x1, SIDE_TOL_PX)
    return left + right + top + bottom, min(left, right)


def _inside(f, r, slack=.01):
    return (f[0] >= r[0] - slack and f[1] >= r[1] - slack
            and f[2] <= r[2] + slack and f[3] <= r[3] + slack)


def _overlap(a, b):
    return (max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            * max(0.0, min(a[3], b[3]) - max(a[1], b[1])))


def _median(vals):
    vals = sorted(vals)
    return vals[len(vals) // 2] if vals else None


def _pct(vals, q):
    vals = sorted(vals)
    if not vals:
        return None
    return vals[min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))]


# --------------------------------------------------------------- windows
def _px(rect, W, H):
    return (int(round(rect[0] * W)), int(round(rect[1] * H)),
            int(round(rect[2] * W)), int(round(rect[3] * H)))


def _backdrop(rows, idxs, rect):
    """(template, structured-stable-outside mask) of a window's backdrop
    from the frames ``idxs`` that drew it, or None when it has no steady
    structure to recognise it by."""
    import numpy as np
    if len(idxs) < 3:
        return None
    stack = np.stack([rows[i]["g"] for i in idxs[:40]]).astype(np.float32)
    T = np.median(stack, 0)
    mad = np.median(np.abs(stack - T), 0)
    H, W = T.shape
    gx = np.zeros_like(T)
    gy = np.zeros_like(T)
    gx[:, 1:-1] = np.abs(T[:, 2:] - T[:, :-2])
    gy[1:-1, :] = np.abs(T[2:, :] - T[:-2, :])
    mask = (mad < 8) & (np.maximum(gx, gy) > 30)
    x0, y0, x1, y1 = _px(rect, W, H)
    mask[max(0, y0 - 4):y1 + 4, max(0, x0 - 4):x1 + 4] = False
    if mask.mean() < BACKDROP_MIN_PX:
        return None
    return T, mask


def _backdrop_match(gray, bd, rect):
    import numpy as np
    T, mask = bd
    d = np.abs(gray.astype(np.float32) - T)[mask]
    if not len(d) or float((d < BACKDROP_STEP).mean()) < BACKDROP_MATCH:
        return False
    H, W = gray.shape
    x0, y0, x1, y1 = _px(rect, W, H)
    inner = gray[y0 + 2:y1 - 2, x0 + 2:x1 - 2]
    return inner.size > 0 and float(inner.std()) >= INSIDE_STD_MIN


def _find_windows(rows):
    """Confirmed call windows over the sampled ``rows`` (each with "g",
    "faces", "lines", "cands"); marks every row's "win"."""
    clusters = []
    for i, row in enumerate(rows):
        for r in row["cands"]:
            for c in clusters:
                if insets.iou(c["ref"], r) >= WINDOW_IOU:
                    c["hits"].append((i, r))
                    break
            else:
                clusters.append({"ref": r, "hits": [(i, r)]})
    wins = []
    for c in clusters:
        idxs = sorted({i for i, _ in c["hits"]})
        if len(idxs) < min(CALL_MIN_HITS, max(2, len(rows) // 4)):
            continue
        rect = [round(_median([r[k] for _, r in c["hits"]]), 4)
                for k in range(4)]
        held = 0
        for i in idxs:
            faces = rows[i]["faces"]
            if faces and _inside(faces[0], rect) and \
                    faces[0][2] - faces[0][0] >= CALL_FACE_MIN * (rect[2] - rect[0]):
                held += 1
        if held < CALL_FACE_HITS:
            continue
        wins.append({"rect": rect, "hits": idxs})
    # nested finds of one window (a double border): keep the most seen
    wins.sort(key=lambda w: -len(w["hits"]))
    out = []
    for w in wins:
        if all(insets.iou(w["rect"], o["rect"]) < .6 and
               not insets._within(w["rect"], o["rect"], .02) for o in out):
            out.append(w)
    for k, w in enumerate(out):
        w["id"] = f"call{k + 1}"
        w["bd"] = _backdrop(rows, w["hits"], w["rect"])
    for i, row in enumerate(rows):
        row["win"] = None
        H, W = row["g"].shape
        for w in out:
            if i in w["hits"]:
                row["win"] = w
                break
            score, longer = _side_score(row["lines"], w["rect"], W, H)
            if (score >= SIDE_SCORE and longer >= SIDE_MIN) or (
                    w["bd"] is not None and
                    _backdrop_match(row["g"], w["bd"], w["rect"])):
                row["win"] = w
                break
    return out


def _chrome(rows, win):
    """Measure a window's chrome and self-view from the frames showing it:
    sets win["chrome_top"], win["chrome_bottom"] (SOURCE y fractions, or
    None) and win["pip"] (SOURCE rect or None)."""
    import numpy as np
    win.update(chrome_top=None, chrome_bottom=None, pip=None,
               measured=0)
    cv2 = _cv()
    idxs = [i for i, r in enumerate(rows) if r.get("win") is win]
    if len(idxs) < MIN_STATIC_SAMPLES or cv2 is None:
        return
    H, W = rows[idxs[0]]["g"].shape
    x0, y0, x1, y1 = _px(win["rect"], W, H)
    x0, y0, x1, y1 = x0 + 2, y0 + 2, x1 - 2, y1 - 2
    if x1 - x0 < 16 or y1 - y0 < 16:
        return
    stack = np.stack([rows[i]["g"][y0:y1, x0:x1] for i in idxs[:80]]
                     ).astype(np.float32)
    M = np.median(stack, 0)
    mad = np.median(np.abs(stack - M), 0)
    gx = np.abs(cv2.Sobel(M, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(M, cv2.CV_32F, 0, 1, ksize=3))
    static = ((mad < STATIC_MAD) & (np.maximum(gx, gy) > STATIC_GRAD)
              ).astype(np.uint8)
    static = cv2.dilate(static, np.ones((3, 3), np.uint8))
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(static, 8)
    h, w = static.shape
    comps, pips = [], []
    for k in range(1, n):
        bx, by, bw, bh, area = (int(v) for v in stats[k])
        if area < 6:
            continue
        fx0, fy0 = bx / float(w), by / float(h)
        fx1, fy1 = (bx + bw) / float(w), (by + bh) / float(h)
        cx = (fx0 + fx1) / 2.0
        if PIP_MIN[0] <= fx1 - fx0 <= PIP_MAX[0] and \
                PIP_MIN[1] <= fy1 - fy0 <= PIP_MAX[1] and \
                (cx < .4 or cx > .6) and area <= .6 * bw * bh:
            # an outline round a picture that moves: a self-view
            ix0, ix1 = bx + bw // 4, bx + bw - bw // 4
            iy0, iy1 = by + bh // 4, by + bh - bh // 4
            inner = mad[iy0:iy1, ix0:ix1]
            if inner.size and float(inner.mean()) >= 2 * STATIC_MAD:
                pips.append([bw * bh, fx0, fy0, fx1, fy1])
                continue
        if area >= CHROME_MIN_PX:         # smaller: a speck of steady room
            comps.append([fx0, fy0, fx1, fy1])
    pip = max(pips)[1:] if pips else None
    if pip:
        # the self-view's own controls (a collapse chevron above it, a
        # camera-flip icon on it) go with it: one box to keep or erase
        grown = True
        while grown:
            grown = False
            for c in comps:
                if c[1] > CHROME_EDGE and c[3] < 1.0 - 2 * CHROME_EDGE \
                        and c[0] < pip[2] and c[2] > pip[0] and \
                        c[1] < pip[3] + PIP_NEAR and c[3] > pip[1] - PIP_NEAR \
                        and c[2] - c[0] <= 1.2 * (pip[2] - pip[0]):
                    pip = [min(pip[0], c[0]), min(pip[1], c[1]),
                           max(pip[2], c[2]), max(pip[3], c[3])]
                    comps.remove(c)
                    grown = True
                    break
    top, bottom = 0.0, 1.0
    for fx0, fy0, fx1, fy1 in comps:
        if fy0 <= CHROME_EDGE and fy1 <= TOP_CHROME_MAX:
            top = max(top, fy1)           # the status bar
        elif fy1 >= 1.0 - 2 * CHROME_EDGE and fy0 >= BOTTOM_CHROME_MIN:
            bottom = min(bottom, fy0)     # the call buttons, home bar
    rx0, ry0, rx1, ry1 = win["rect"]
    rw, rh = rx1 - rx0, ry1 - ry0
    # the crop's pixel offset (2 px each side) back to the window
    sx, sy = 2.0 / W, 2.0 / H
    iw, ih = rw - 2 * sx, rh - 2 * sy
    if top > 0:
        win["chrome_top"] = round(ry0 + sy + top * ih + .005, 4)
    if bottom < 1:
        win["chrome_bottom"] = round(ry0 + sy + bottom * ih - .005, 4)
    if pip:
        px0, py0, px1, py1 = pip
        win["pip"] = [round(rx0 + sx + px0 * iw - .004, 4),
                      round(ry0 + sy + py0 * ih - .004, 4),
                      round(rx0 + sx + px1 * iw + .004, 4),
                      round(ry0 + sy + py1 * ih + .004, 4)]
    win["measured"] = len(idxs)


def _head(rows, win):
    """The guest's head envelope inside a window: the faces it held (10th-
    90th percentile of their edges) grown by hair, chin and sides."""
    faces = []
    for r in rows:
        if r.get("win") is not win:
            continue
        for f in r["faces"]:
            if _inside(f, win["rect"]) and \
                    f[2] - f[0] >= CALL_FACE_MIN * (win["rect"][2] - win["rect"][0]):
                if not win.get("pip") or _overlap(f, win["pip"]) < .5 * (
                        (f[2] - f[0]) * (f[3] - f[1])):
                    faces.append(f)
                break
    if not faces:
        return None
    # the guest is the face the window holds most often: a detector's
    # steady false find (a lamp, a panel of the room) sits elsewhere
    mw = _median([f[2] - f[0] for f in faces])
    best = max(faces, key=lambda c: sum(
        1 for f in faces if abs((f[0] + f[2]) - (c[0] + c[2])) / 2.0 <= mw
        and abs((f[1] + f[3]) - (c[1] + c[3])) / 2.0 <= mw))
    faces = [f for f in faces
             if abs((f[0] + f[2]) - (best[0] + best[2])) / 2.0 <= mw
             and abs((f[1] + f[3]) - (best[1] + best[3])) / 2.0 <= mw]
    fw = _median([f[2] - f[0] for f in faces])
    fh = _median([f[3] - f[1] for f in faces])
    face = [_pct([f[0] for f in faces], .1), _pct([f[1] for f in faces], .1),
            _pct([f[2] for f in faces], .9), _pct([f[3] for f in faces], .9)]
    r = usable(win)

    def clip(b):
        return [round(max(r[0], b[0]), 4), round(max(r[1], b[1]), 4),
                round(min(r[2], b[2]), 4), round(min(r[3], b[3]), 4)]
    win["face"] = clip(face)
    return clip([face[0] - SIDE_PAD * fw, face[1] - HAIR_ABOVE * fh,
                 face[2] + SIDE_PAD * fw, face[3] + CHIN_BELOW * fh])


# --------------------------------------------------------------- analyze
def analyze(path, duration, shots=None, src_w=None, src_h=None,
            spatial=None, max_samples=MAX_SAMPLES, budget_s=BUDGET_S):
    """The source-layout sidecar for the video at ``path`` (the proxy):
    {"v", "w", "h", "samples", "windows", "spans", "summary"} —
    "w"/"h" the ORIGINAL's size (``src_w``/``src_h``, else the file's):
    enlargement is judged against the pixels a final renders from.
    Raises LayoutError when no frame can be read."""
    if not path or not os.path.exists(path):
        raise LayoutError("no local video to measure")
    pw, ph = _probe(path)
    try:
        sw, sh = int(float(src_w)), int(float(src_h))
    except (TypeError, ValueError):
        sw, sh = pw, ph
    if sw <= 0 or sh <= 0:
        sw, sh = pw, ph
    times = sample_times(duration, shots, max_samples)
    frames = _frames(path, times, budget_s)
    if not frames:
        raise LayoutError("no frame could be decoded")
    cv2 = _cv()
    cascades = []
    if cv2 is not None:
        import subject
        cascades = subject._cascades(cv2)
    rows = []
    for t, g in frames:
        lines = _lines(g)
        rows.append({"t": t, "g": g, "lines": lines,
                     "faces": _faces(cv2, cascades, g),
                     "cands": _window_cands(g, lines)})
    wins = _find_windows(rows)
    for w in wins:
        _chrome(rows, w)
        w["head"] = _head(rows, w)
        faced = [r for r in rows if r.get("win") is w and r["faces"]]
        wide = sum(1 for r in faced if any(
            not _inside(f, w["rect"], .02) and f[2] - f[0] >= OUTSIDE_FACE_W
            for f in r["faces"]))
        w["kind"] = ("call_wide" if faced and wide >= WIDE_SHARE * len(faced)
                     else "call")
        w["seen"] = sum(1 for r in rows if r.get("win") is w)
    spans = add_spatial(_spans(rows, shots, duration), spatial)
    for w in wins:
        w["secs"] = round(sum(s["t1"] - s["t0"] for s in spans
                              if s.get("win") == w["id"]), 1)
    layout = {
        "v": LAYOUT_VERSION, "w": int(sw), "h": int(sh),
        "samples": len(rows),
        "windows": [{k: w.get(k) for k in (
            "id", "rect", "kind", "seen", "secs", "chrome_top",
            "chrome_bottom", "pip", "face", "head", "measured")}
            for w in wins],
        "spans": spans,
    }
    layout["summary"] = summary(layout)
    return layout


def _spans(rows, shots, duration):
    """Per sampled shot: {"t0", "t1", "kind", "win", "faces"}; consecutive
    shots of one call window merge into one span."""
    rows = sorted(rows, key=lambda r: r["t"])
    edges = []
    for s in shots or []:
        try:
            edges.append((float(s["start"]), float(s["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not edges:
        edges = [(0.0, float(duration or (rows[-1]["t"] + 1.0)))]
    edges.sort()
    out = []
    k = 0
    for a, b in edges:
        mine = []
        while k < len(rows) and rows[k]["t"] < b:
            if rows[k]["t"] >= a:
                mine.append(rows[k])
            k += 1
        if not mine:
            continue
        votes = {}
        for r in mine:
            w = r.get("win")
            if w:
                votes[(w["kind"], w["id"])] = votes.get((w["kind"], w["id"]), 0) + 1
        faced = sum(1 for r in mine if r["faces"] and not r.get("win"))
        if votes and max(votes.values()) * 2 > len(mine):
            kind, wid = max(votes.items(), key=lambda kv: kv[1])[0]
        else:
            # a detector misses faces far more often than it invents them:
            # a shot with a face in a quarter of its samples is a camera shot
            kind, wid = ("camera" if faced >= FACED_SHARE * len(mine)
                         else "other"), None
        faces = []
        for r in mine:
            for f in r["faces"]:
                if all(insets.iou(f, o) < .5 for o in faces):
                    faces.append(f)
        span = {"t0": round(a, 3), "t1": round(b, 3), "kind": kind,
                "win": wid, "faces": faces[:6]}
        prev = out[-1] if out else None
        if prev and wid and prev["win"] == wid and prev["kind"] == kind and \
                a - prev["t1"] < 1.5:
            prev["t1"] = span["t1"]
            for f in faces:
                if len(prev["faces"]) < 6 and all(
                        insets.iou(f, o) < .5 for o in prev["faces"]):
                    prev["faces"].append(f)
            continue
        out.append(span)
    return out


def add_spatial(spans, spatial):
    """Fold the index's own face track (spatial.py samples) into the spans:
    another detector pass at other times — a shot that measured no face
    here but one there is a camera shot."""
    samples = [s for s in (spatial or {}).get("samples") or []
               if isinstance(s, dict)]
    if not samples or not spans:
        return spans
    for span in spans:
        mine = [s for s in samples
                if span["t0"] <= float(s.get("t", -1)) < span["t1"]]
        faced = [s for s in mine if s.get("faces")]
        for s in faced:
            for f in s["faces"][:2]:
                f = [round(float(v), 4) for v in f]
                if len(span["faces"]) < 6 and all(
                        insets.iou(f, o) < .5 for o in span["faces"]):
                    span["faces"].append(f)
        if span["kind"] == "other" and mine and \
                len(faced) >= FACED_SHARE * len(mine):
            span["kind"] = "camera"
    return spans


def get_or_compute_for_index(worker_db, dbx, index_row, media_path,
                             workdir=None):
    """The index row's sidecar, computed and persisted when it is missing
    or stale (old indexes: no fleet-wide re-index)."""
    idx = index_row.get("json") or {}
    layout = idx.get("source_layout")
    if valid(layout):
        return layout
    video = idx.get("video") or {}
    layout = analyze(media_path, video.get("duration"),
                     shots=idx.get("shots") or [],
                     src_w=video.get("width"), src_h=video.get("height"),
                     spatial=idx.get("spatial"))
    try:
        worker_db.run(dbx.set_index_source_layout, index_row["video_sha256"],
                      layout, index_row.get("pipeline_version"))
    except Exception as exc:
        print(f"[source_layout] sidecar persist failed (non-fatal): {exc}",
              flush=True)
    return layout


def valid(layout):
    return isinstance(layout, dict) and layout.get("v") == LAYOUT_VERSION


# --------------------------------------------------------------- reading
def window(layout, wid):
    for w in (layout or {}).get("windows") or []:
        if w.get("id") == wid:
            return w
    return None


def _merge(windows):
    out = []
    for a, b in sorted((float(a), float(b)) for a, b in windows or []
                       if float(b) > float(a)):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def spans_over(layout, windows):
    """[(span, seconds of ``windows`` it covers)] for the spans that
    overlap SOURCE ``windows``, in time order."""
    wins = _merge(windows)
    out = []
    for s in (layout or {}).get("spans") or []:
        got = sum(max(0.0, min(b, s["t1"]) - max(a, s["t0"])) for a, b in wins)
        if got > 1e-3:
            out.append((s, got))
    return out


def call_share(layout, windows):
    """(dominant call window or None, its share of ``windows``' seconds,
    {kind: seconds}) — unmeasured seconds count against the share."""
    total = sum(b - a for a, b in _merge(windows))
    kinds, per = {}, {}
    for s, got in spans_over(layout, windows):
        kinds[s["kind"]] = kinds.get(s["kind"], 0.0) + got
        if s.get("win") and s["kind"] == "call":
            per[s["win"]] = per.get(s["win"], 0.0) + got
    if not per or total <= 0:
        return None, 0.0, kinds
    wid, got = max(per.items(), key=lambda kv: kv[1])
    return window(layout, wid), got / total, kinds


def usable(win):
    """The call's picture: the window inside its border, below its status
    bar and above its call buttons (SOURCE fractions)."""
    x0, y0, x1, y1 = win["rect"]
    top = win.get("chrome_top") or (y0 + BORDER_INSET)
    bottom = win.get("chrome_bottom") or (y1 - BORDER_INSET)
    return [x0 + BORDER_INSET, max(y0 + BORDER_INSET, top),
            x1 - BORDER_INSET, min(y1 - BORDER_INSET, bottom)]


def _place(lo, hi, size, want_lo, want_hi):
    """Start of a span ``size`` long inside [lo, hi] holding [want_lo,
    want_hi] (centred on it), or the nearest it can get."""
    start = (want_lo + want_hi) / 2.0 - size / 2.0
    start = min(max(start, want_hi - size), want_lo)
    return min(max(start, lo), hi - size)


def _source_in(win, sw, sh, aspect, max_w=None):
    """The largest SOURCE rect of pixel ``aspect`` (w/h) inside the call's
    picture, at most ``max_w`` source px wide: the whole head with HEADROOM
    above the hair where it fits, else the face whole and as much hair as
    fits (a close-up trims the crown, never the face). (rect, face_ok,
    head_whole) — None, None when no face was measured."""
    U = usable(win)
    uw, uh = (U[2] - U[0]) * sw, (U[3] - U[1]) * sh
    rw = min(uw, uh * aspect)
    if max_w:
        rw = min(rw, max_w)
    rh = rw / aspect
    fw, fh = rw / sw, rh / sh
    head = win.get("head")
    face = win.get("face") or head
    if not head:
        x0 = (U[0] + U[2]) / 2.0 - fw / 2.0
        rect = [x0, U[1], x0 + fw, U[1] + fh]
        return [round(v, 4) for v in rect], None, None
    lo, hi = (head[0], head[2]) if head[2] - head[0] <= fw else \
        (face[0], face[2])
    x0 = _place(U[0], U[2], fw, lo, hi)
    if fh >= (head[3] - head[1]) + HEADROOM * fh:
        y0 = head[1] - HEADROOM * fh
    else:
        # the chin's margin at the bottom, the crown trimmed at the top
        y0 = head[3] - fh
    y0 = min(max(y0, U[1]), U[3] - fh)
    rect = [round(v, 4) for v in (x0, y0, x0 + fw, y0 + fh)]
    return rect, _inside(face, rect, .002), _inside(head, rect, .002)


def _pip_state(win, rect):
    pip = win.get("pip")
    if not pip:
        return None
    if _inside(pip, rect, .002):
        return "inside"
    if _overlap(pip, rect) <= 1e-6:
        return "clear"
    return "cut"


def card_plan(win, sw, sh, W=1080, H=1920, top=CARD_TOP, bottom=CARD_BOTTOM,
              cap=UPSCALE_CAP):
    """The largest card showing the call's picture at <= ``cap`` on a W x H
    canvas between ``top`` and ``bottom`` (full width): {"box", "source",
    "k", "area", "head_ok", "head_whole", "pip"}. The enlargement is the cap
    unless the measured face would not fit the box whole; the head decides
    where the source rect sits."""
    U = usable(win)
    uw, uh = (U[2] - U[0]) * sw, (U[3] - U[1]) * sh
    cw, ch = float(W), (bottom - top) * H
    k = cap
    face = win.get("face") or win.get("head")
    if face:
        # a face taller (wider) than the box allows at the cap: less enlarged
        k = min(k, ch / max(1.0, (face[3] - face[1]) * sh * 1.1),
                cw / max(1.0, (face[2] - face[0]) * sw * 1.1))
    rw, rh = min(uw, cw / k), min(uh, ch / k)
    rect, ok, whole = _source_in(win, sw, sh, rw / rh, max_w=rw)
    bw, bh = (rect[2] - rect[0]) * sw * k, (rect[3] - rect[1]) * sh * k
    x0 = (1.0 - bw / W) / 2.0
    y0 = top + (ch - bh) / H / 2.0
    box = [round(x0, 4), round(y0, 4), round(x0 + bw / W, 4),
           round(y0 + bh / H, 4)]
    return {"box": box, "source": rect, "k": round(k, 2),
            "area": round((bw / W) * (bh / H), 3), "head_ok": ok,
            "head_whole": whole, "pip": _pip_state(win, rect)}


def card_for_box(win, sw, sh, box, W=1080, H=1920, cap=UPSCALE_CAP):
    """An editor's card ``box`` filled with the call's picture: the SOURCE
    rect at the box's aspect inside the picture, holding the face (the head
    where it fits); the box narrows (centred) when filling it would enlarge
    past ``cap``. (box, rect, k, face_ok, pip)."""
    bw, bh = (box[2] - box[0]) * W, (box[3] - box[1]) * H
    rect, ok, _whole = _source_in(win, sw, sh, bw / bh)
    face, head = win.get("face") or win.get("head"), win.get("head")
    if ok is False and face and head:
        # A box wider than the call's picture can fill at the face's height
        # (a 1:1 or landscape card on a portrait call) would cut the face: a
        # face never leaves a card, so the box narrows (centred) to the
        # aspect that holds it — forehead to chin margin, the crown trimmed.
        U = usable(win)
        need = min(U[3] - U[1], (head[3] - face[1]) * 1.02) * sh
        aspect = min(bw / bh, (U[2] - U[0]) * sw / max(1.0, need))
        rect, ok, _whole = _source_in(win, sw, sh, aspect)
        nw = bh * aspect / W
        cx = (box[0] + box[2]) / 2.0
        box = [round(cx - nw / 2.0, 4), box[1], round(cx + nw / 2.0, 4),
               box[3]]
        bw = nw * W
    rw = (rect[2] - rect[0]) * sw
    k = bw / max(1.0, rw)
    if k > cap + 1e-6:
        nw, nh = rw * cap / W, (rect[3] - rect[1]) * sh * cap / H
        cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        box = [round(cx - nw / 2.0, 4), round(cy - nh / 2.0, 4),
               round(cx + nw / 2.0, 4), round(cy + nh / 2.0, 4)]
        k = cap
    return list(box), rect, round(k, 2), ok, _pip_state(win, rect)


def full_bleed(win, sw, sh, W=1080, H=1920, cap=UPSCALE_CAP):
    """A crop OF the call's picture that fills the canvas: {"viable", "k",
    "source"}."""
    rect, ok, _whole = _source_in(win, sw, sh, W / float(H))
    k = W / max(1.0, (rect[2] - rect[0]) * sw)
    return {"viable": bool(k <= cap + 1e-6 and ok is not False),
            "k": round(k, 2), "source": rect, "head_ok": ok}


def crop_width(sw, sh, W=1080, H=1920):
    """The SOURCE width share a full-height crop to W:H takes."""
    return min(1.0, sh * (W / float(H)) / float(sw))


def plain_crop(win, sw, sh, W=1080, H=1920):
    """The crop of the whole frame centred on the call: {"x", "k",
    "whole", "margin"} (margin: backdrop share of the crop each side)."""
    cw = crop_width(sw, sh, W, H)
    r = win["rect"]
    ww = r[2] - r[0]
    if ww <= cw:
        x = (r[0] + r[2]) / 2.0
        margin = (cw - ww) / 2.0 / cw
        whole = True
    else:
        head = win.get("head") or r
        x = min(max((head[0] + head[2]) / 2.0, r[0] + cw / 2.0), r[2] - cw / 2.0)
        margin, whole = 0.0, False
    x = min(max(x, cw / 2.0), 1.0 - cw / 2.0)
    return {"x": round(x, 3), "k": round(W / (cw * sw), 2), "whole": whole,
            "margin": round(margin, 3)}


# --------------------------------------------------------------- frames
def safe_aim(faces, cw, prefer, must=None):
    """The crop centre x (crop ``cw`` wide) nearest ``prefer`` that holds
    ``must`` [x0, x1] (if given) and keeps every face wholly inside or
    wholly outside, or None."""
    lo_c, hi_c = cw / 2.0, 1.0 - cw / 2.0
    best = None
    steps = 400
    for i in range(steps + 1):
        c = lo_c + (hi_c - lo_c) * i / steps
        x0, x1 = c - cw / 2.0, c + cw / 2.0
        if must and not (x0 <= must[0] + 1e-6 and must[1] <= x1 + 1e-6):
            continue
        if any(not (f[2] <= x0 + 1e-6 or f[0] >= x1 - 1e-6 or
                    (f[0] >= x0 - 1e-6 and f[2] <= x1 + 1e-6)) for f in faces):
            continue
        if best is None or abs(c - prefer) < abs(best - prefer):
            best = c
    return None if best is None else round(best, 3)


def _span_aim(layout, span, cw):
    """(x or None, mode) for one span's crop in a CALL layout: the call
    window centred and whole (every face it holds whole); any other shot
    FITTED whole. Those are stage wides and cutaways where a host walks the
    stage at a size no face detector sees (the Diamandis wide: Peter's face
    measured nowhere, cut in half by a crop aimed on the screen's Elon) —
    the editor frames or cuts them; the report names them."""
    faces = [f for f in span.get("faces") or [] if f[2] - f[0] >= .02]
    win = window(layout, span.get("win"))
    if win is not None and span["kind"] in ("call", "call_wide"):
        r = win["rect"]
        if r[2] - r[0] <= cw:
            x = safe_aim(faces, cw, (r[0] + r[2]) / 2.0, must=(r[0], r[2]))
        else:
            head = win.get("head") or r
            x = safe_aim(faces, cw, (head[0] + head[2]) / 2.0,
                         must=(head[0], head[2]))
        return (x, "crop") if x is not None else (None, "pad_blur")
    return None, "pad_blur"


def _unmeasured_rows(track, keep):
    """Fitted rows for the kept seconds no sampled shot covers in a call
    layout's track (a long source samples about every other shot): left to
    the frame's base aim they would be cropped on the call window, and a
    stage wide there can cut a host in half. Only a gap with the call
    cropped on BOTH sides keeps that aim (most likely more of the call)."""
    rows = sorted(track, key=lambda r: r["t0"])
    out = []
    for a, b in _merge(keep):
        cur = a
        for r in rows + [None]:
            g1 = b if r is None else min(b, r["t0"])
            if g1 - cur > .05:
                left = next((p for p in reversed(rows)
                             if abs(p["t1"] - cur) <= .05), None)
                right = r if r is not None and abs(r["t0"] - g1) <= .05 \
                    else None
                if not (left and right and left["mode"] == "crop"
                        and right["mode"] == "crop"):
                    out.append({"t0": round(max(0.0, cur), 3),
                                "t1": round(g1, 3), "mode": "pad_blur"})
            if r is None or r["t0"] >= b:
                break
            cur = max(cur, r["t1"])
    return out


def frame_for(layout, keep, ratio="9:16", sw=None, sh=None):
    """The frame a short over SOURCE ``keep`` starts with (a Frame dict,
    never None), and why: (frame, note).

    A call layout (call spans >= CALL_LAYOUT_SHARE of the kept seconds)
    gets one crop per call span — centred on the call window, every face
    wholly in or wholly out — and every other shot fitted whole, with the
    call's centre as the base aim. Anything
    else gets ONE aim on the dominant face when it cuts no face anywhere in
    the kept footage, else the whole frame fitted (pad_blur): the editor's
    auto_reframe measures it properly, exactly as before."""
    ratio = str(ratio or "9:16")
    try:
        rw, rh = (float(v) for v in ratio.split(":"))
    except ValueError:
        rw, rh = 9.0, 16.0
    sw = float(sw or (layout or {}).get("w") or 1920)
    sh = float(sh or (layout or {}).get("h") or 1080)
    cw = min(1.0, sh * (rw / rh) / sw)
    keep = [(float(a), float(b)) for a, b in keep or []]
    fitted = "pad" if min(sw, sh) < LOWRES_SHORT_SIDE else "pad_blur"
    W, H = _canvas(rw, rh)
    crop_up = W / max(1.0, cw * sw)
    fit_up = min(W / sw, H / sh)
    if crop_up > WINDOW_UPSCALE and not (
            (sw * fit_up / W) * (sh * fit_up / H) < WINDOW_MIN_SHARE
            and crop_up <= STRIP_UPSCALE):
        # auto_reframe's resolution-aware window: a crop would smear it
        return ({"ratio": ratio, "mode": fitted},
                f"a crop would enlarge this {int(sw)}x{int(sh)} source "
                f"{crop_up:.1f}x: the whole frame is shown as a window")
    if not valid(layout):
        return ({"ratio": ratio, "mode": fitted},
                "source layout unmeasured: the whole frame is fitted until "
                "auto_reframe measures it")
    win, share, _kinds = call_share(layout, keep)
    if win is not None and share >= CALL_LAYOUT_SHARE:
        lo = min(a for a, _b in keep) if keep else 0.0
        hi = max(b for _a, b in keep) if keep else 0.0
        track = []
        for s, _got in spans_over(layout, keep):
            x, mode = _span_aim(layout, s, cw)
            t0, t1 = max(s["t0"], lo - .5), min(s["t1"], hi + .5)
            if t1 - t0 <= .01:
                continue
            row = {"t0": round(max(0.0, t0), 3), "t1": round(t1, 3),
                   "mode": mode}
            if x is not None:
                row.update(x=x, y=0.5)
            track.append(row)
        track += _unmeasured_rows(track, keep)
        track.sort(key=lambda r: r["t0"])
        base = plain_crop(win, sw, sh, *_canvas(rw, rh))
        frame = {"ratio": ratio, "mode": "crop", "focus_x": base["x"],
                 "focus_y": 0.5, "focus_track": track or None}
        fitted = [r for r in track if r["mode"] != "crop"]
        return frame, (f"call layout ({share * 100:.0f}% of the kept footage "
                       f"is the call window {fmt(win['rect'])}): the crop is "
                       "centred on the call"
                       + (f"; {len(fitted)} other shot"
                          f"{'s are' if len(fitted) != 1 else ' is'} fitted "
                          "whole until framed or cut" if fitted else ""))
    near = spans_over(layout, keep)
    if not near and keep:
        # no shot of this footage was sampled (a long source thins its
        # samples): the shots either side speak for it
        near = spans_over(layout, [(max(0.0, a - NEAR_S), b + NEAR_S)
                                   for a, b in keep])
    faces = [f for s, _got in near
             for f in s.get("faces") or [] if f[2] - f[0] >= .02]
    if not faces:
        return ({"ratio": ratio, "mode": "pad_blur"},
                "no face measured in this footage: the whole frame is fitted")
    main = []
    for s, got in near:
        fs = [f for f in s.get("faces") or [] if f[2] - f[0] >= .02]
        if fs:
            m = max(fs, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))
            main.append(((m[0] + m[2]) / 2.0, (m[1] + m[3]) / 2.0, got, m))
    main.sort(key=lambda row: row[:3])
    half, acc, cx, cy = sum(r[2] for r in main) / 2.0, 0.0, .5, .5
    for x, y, g, _m in main:
        acc += g
        if acc >= half:
            cx, cy = x, y
            break
    x = safe_aim(faces, cw, cx)
    if x is None or abs(x - cx) > cw / 2.0:
        return ({"ratio": ratio, "mode": "pad_blur"},
                "no single crop keeps every face whole: the whole frame is "
                "fitted until auto_reframe aims it shot by shot")
    # One still aim must also hold each shot's own speaker: a two-camera
    # podcast frames host and guest at different places in their close-ups,
    # and an aim on one leaves the other's shots with nobody in the crop
    # (1 in 5 single-aim seeds on 21 real podcast sources, Oct 2026).
    if any(g >= MIN_SHOT_S and not (m[0] >= x - cw / 2.0 - 1e-3 and
                                    m[2] <= x + cw / 2.0 + 1e-3)
           for _x, _y, g, m in main):
        return ({"ratio": ratio, "mode": "pad_blur"},
                "the speakers sit at different places across these shots, "
                "so no one still crop holds each shot's speaker: the whole "
                "frame is fitted until auto_reframe aims it shot by shot")
    return ({"ratio": ratio, "mode": "crop", "focus_x": x,
             "focus_y": round(min(max(cy, 0.0), 1.0), 3)},
            "one crop aim on the speaker that cuts no face")


def _canvas(rw, rh):
    """A delivery canvas for the ratio (HD short side)."""
    if rh >= rw:
        return 1080, int(round(1080 * rh / rw / 2.0)) * 2
    return int(round(1080 * rw / rh / 2.0)) * 2, 1080


# --------------------------------------------------------------- report
def fmt(rect, n=3):
    return "[" + ", ".join(f"{float(v):.{n}f}" for v in rect) + "]"


def erase_call(win, t0=None, t1=None):
    """The erase_region call that repaints the self-view (fill 'box') over
    SOURCE [t0, t1] — never the whole source, a repaint of every frame —
    or None when the window has no self-view."""
    p = win.get("pip")
    if not p:
        return None
    region = (f"{{'x': {p[0]:.3f}, 'y': {p[1]:.3f}, 'w': {p[2] - p[0]:.3f}, "
              f"'h': {p[3] - p[1]:.3f}, 'fill': 'box'}}")
    span = (f", start={t0:.2f}, end={t1:.2f}" if t0 is not None and
            t1 is not None and t1 > t0 else "")
    return f"erase_region(regions=[{region}]{span})"


def _pct_s(v):
    return f"{v * 100:.0f}%"


def window_lines(win, sw, sh, W=1080, H=1920, windows=None, layout=None):
    """What one call window offers a short: chrome, self-view, the largest
    cards, full-bleed and the plain crop — one line each."""
    lines = []
    U = usable(win)
    bits = []
    if win.get("chrome_top"):
        bits.append(f"status bar down to y {win['chrome_top']:.2f}")
    if win.get("chrome_bottom"):
        bits.append(f"call buttons from y {win['chrome_bottom']:.2f}")
    if bits:
        lines.append("Inside the call: " + " and ".join(bits) +
                     " (crop them off: the picture is "
                     f"{fmt(U)}).")
    elif not win.get("measured"):
        lines.append("Inside the call: too few frames to measure its "
                     "status bar and buttons — look_at it.")
    if win.get("pip"):
        got = []
        if layout is not None and windows:
            got = [(max(s["t0"], a), min(s["t1"], b))
                   for s, _g in spans_over(layout, windows)
                   if s.get("win") == win["id"]
                   for a, b in _merge(windows)
                   if min(s["t1"], b) - max(s["t0"], a) > .05]
        how = (erase_call(win, min(a for a, _b in got),
                          max(b for _a, b in got)) if got else
               "erase_region(fill 'box') over it, with start/end on the "
               "short's call footage")
        lines.append(
            f"The host's self-view (picture-in-picture) at {fmt(win['pip'])} "
            "is a FACE inside the call: keep it whole inside the picture or "
            f"erase it — {how} — never cut it with a card or crop edge.")
    nb = card_plan(win, sw, sh, W, H, CARD_TOP)
    band = card_plan(win, sw, sh, W, H, CARD_TOP_BAND)

    def card_txt(c):
        return (f"box {fmt(c['box'])} (area {c['area']:.2f}"
                + ("" if c["area"] >= PICTURE_FLOOR - 1e-3 else
                   f", BELOW the {PICTURE_FLOOR:g} floor")
                + f"), source {fmt(c['source'])} at {c['k']:.2f}x"
                + (", the face does not fit whole" if c["head_ok"] is False
                   else ", the crown of the head trimmed"
                   if c.get("head_whole") is False else "")
                + (", the self-view whole inside" if c["pip"] == "inside" else
                   ", the self-view CUT — erase it" if c["pip"] == "cut" else ""))
    lines.append(f"Largest card at <= {UPSCALE_CAP:g}x, no headline band: "
                 + card_txt(nb) + f"; with a headline band from y "
                 f"{CARD_TOP_BAND:g}: " + card_txt(band) + ". "
                 "set_picture_card(source='auto') frames the call this way "
                 "in any box (never past 2x).")
    fb = full_bleed(win, sw, sh, W, H)
    pc = plain_crop(win, sw, sh, W, H)
    lines.append(
        "Full-bleed crop OF the call: "
        + (f"viable at {fb['k']:.2f}x (source {fmt(fb['source'])})"
           if fb["viable"] else
           f"not viable (it needs {fb['k']:.2f}x > {UPSCALE_CAP:g}x)")
        + f". The plain crop centred on the call (focus_x {pc['x']:.2f}, "
        f"{pc['k']:.2f}x) "
        + (f"shows the whole window with {_pct_s(pc['margin'])} of backdrop "
           "each side." if pc["whole"] else "cuts the window's sides."))
    return lines


def summary(layout, W=1080, H=1920):
    """The whole source's layout in a few lines (stored in the sidecar for
    surfaces that cannot import this module)."""
    return report(layout, W=W, H=H)


def report(layout, keep=None, W=1080, H=1920, to_program=None):
    """SOURCE LAYOUT text for the whole source, or for the kept SOURCE
    windows ``keep`` of a short (``to_program`` maps a source second to the
    programme clock for the ranges it names)."""
    if not valid(layout):
        return ""
    sw, sh = float(layout["w"]), float(layout["h"])
    span_list = layout.get("spans") or []
    if keep:
        windows = _merge(keep)
    else:
        lo = min((s["t0"] for s in span_list), default=0.0)
        hi = max((s["t1"] for s in span_list), default=0.0)
        windows = [[lo, hi]] if hi > lo else []
    total = sum(b - a for a, b in windows) or 1.0
    win, share, kinds = call_share(layout, windows)
    head = ("SOURCE LAYOUT (measured once on the source pixels; every short "
            "cut from it reads the same): ")
    parts = []
    names = {"call": "a portrait CALL window (a phone/video call composited "
                     "over the broadcast frame)",
             "call_wide": "a call window inside a wide shot",
             "camera": "camera shots", "other": "no face (graphics, b-roll, ads)"}
    for kind in ("call", "call_wide", "camera", "other"):
        if kinds.get(kind, 0.0) >= .02 * total:
            parts.append(f"{_pct_s(kinds[kind] / total)} {names[kind]}")
    lines = []
    if win is None or share < .2:
        if not (layout.get("windows") or []):
            return (head + "no call window, self-view or picture-in-picture "
                    "— an ordinary camera source: auto_reframe and "
                    "set_picture_card(source='auto') frame it as usual.")
        return (head + "this footage shows no call window (the source's "
                "call is elsewhere) — frame it as ordinary camera footage.")
    if not parts:
        return ""
    else:
        lines.append(head + "; ".join(parts) + f". The call window: "
                     f"{fmt(win['rect'])} of the source frame.")
        lines += ["- " + ln for ln in window_lines(
            win, sw, sh, W, H, windows=windows if keep else None,
            layout=layout)]
        if keep:
            off = [(max(s["t0"], a), min(s["t1"], b), s["kind"])
                   for s, _g in spans_over(layout, windows)
                   if s["kind"] != "call" or s.get("win") != win["id"]
                   for a, b in windows
                   if min(s["t1"], b) - max(s["t0"], a) > .3]
            if off:
                def rng(a, b):
                    if to_program is not None:
                        pa, pb = to_program(a + 1e-3), to_program(b - 1e-3)
                        if pa is not None and pb is not None:
                            return f"{pa:.1f}-{pb:.1f}s"
                    return f"source {a:.1f}-{b:.1f}s"
                lines.append("- Not the call: " + ", ".join(
                    f"{rng(a, b)} ({kind})" for a, b, kind in off[:6])
                    + (f" and {len(off) - 6} more" if len(off) > 6 else "")
                    + " — a card on the call's picture shows the wrong place "
                    "there: end the card at the cut, or give those shots "
                    "their own framing.")
    return "\n".join(lines)


def child_line(layout, keep, W=1080, H=1920):
    """One line for a board card: the short's layout and its best card."""
    if not valid(layout) or not keep:
        return ""
    win, share, kinds = call_share(layout, keep)
    if win is None or share < .2:
        return ""
    c = card_plan(win, float(layout["w"]), float(layout["h"]), W, H)
    return (f"{share * 100:.0f}% call window {fmt(win['rect'], 2)}; largest "
            f"card {fmt(c['box'], 2)} at {c['k']:.1f}x (area {c['area']:.2f})"
            + ("; erase the self-view" if win.get("pip") else ""))
