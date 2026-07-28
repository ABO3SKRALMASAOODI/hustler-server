"""Where the screen is in the shot, measured from the pixels — no vision model.

Round 55. `add_screen_takeover` pushes the camera into a device screen in the
footage and lets what is playing on it become the whole video. That move lives
or dies on ONE number set: the four corners of the glass. A corner wrong by 2%
of the frame is a 2% mismatch that GROWS by the push factor — an 8x push turns
it into a sixth of the frame sliding as the content arrives, which reads
exactly like the content is not attached to the screen at all.

So the corners are measured, not estimated. Asking a vision model for a
coordinate is the thing that has burned this codebase before (see subject.py):
it is the fallback here, never the route. A lit screen inside a room is one of
the easiest things in computer vision — it is a bright, high-contrast, mostly
convex quadrilateral with a hard edge against a darker bezel — and OpenCV,
already in the image for inpaint.py / cursor.py / subject.py, finds it in
milliseconds on a downscaled frame.

Two candidate generators run and their results compete on one score:

  * CONTOUR — Canny edges, closed with a dilate, every 4-point convex
    approximation of a contour. This is what finds an angled laptop screen,
    because it does not assume the quad is axis-aligned.
  * BRIGHT — Otsu threshold of the value channel, largest bright blob, its
    minimum-area rectangle. This is what finds a screen whose bezel is the
    same colour as the desk behind it (no usable edge), and a screen showing
    dark content still separates because the surrounding room is darker.

The score is deliberately not "biggest" — a whole-frame contour is usually the
frame itself. It rewards internal DETAIL (a screen has content on it; a wall
does not), the brightness step across the boundary (glass is lit, the bezel is
not) and squareness of the corners, and it punishes a quad that reaches the
frame edge (that is the shot, not a screen in the shot).

Pure measurement: returns corners, never writes an EDL, never touches storage.
"""

from schemas import quad_is_sane

# Corners are returned in ffmpeg's `perspective` order everywhere in this
# module: top-left, top-right, BOTTOM-LEFT, bottom-right. It is not clockwise
# and that is on purpose — schemas.ScreenLock stores the same order, so no
# code between the detector and the filtergraph ever re-orders a quad.

# Work resolution. Detection is scale-free (everything is returned as
# fractions), and a screen is a large object, so there is nothing to gain from
# running Canny on 4K pixels.
_WORK_W = 960
# A candidate outside these bounds is not a screen in a shot: too small to push
# into, or so large it IS the shot.
_MIN_AREA_FRAC = 0.012
_MAX_AREA_FRAC = 0.80
# Touching the frame edge this closely means the "screen" runs out of picture.
_EDGE_TOUCH = 0.005
# Below this the corners are not trustworthy enough to pin content to.
MIN_CONFIDENCE = 0.30


def _cv2():
    try:
        import cv2
        return cv2
    except Exception:
        return None


def _order_corners(pts):
    """4 arbitrary points -> (TL, TR, BL, BR).

    Sum and difference, not angle-sorting: for a quad this shallow the
    x+y / x-y extremes are stable even when the shape is strongly sheared,
    whereas sorting by angle about the centroid flips two corners as soon as
    one of them crosses the centroid's axis.
    """
    pts = [(float(x), float(y)) for x, y in pts]
    s = [x + y for x, y in pts]
    d = [x - y for x, y in pts]
    tl = pts[s.index(min(s))]
    br = pts[s.index(max(s))]
    tr = pts[d.index(max(d))]
    bl = pts[d.index(min(d))]
    return [tl, tr, bl, br]


def _quad_area(c):
    """Shoelace area of a quad in storage order, walked round its boundary."""
    p = [(c[0], c[1]), (c[2], c[3]), (c[6], c[7]), (c[4], c[5])]
    a = 0.0
    for i in range(4):
        x1, y1 = p[i]
        x2, y2 = p[(i + 1) % 4]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _squareness(c):
    """1.0 when every corner is a right angle, falling off with the worst one.

    A screen photographed from any angle stays projectively a rectangle, so its
    corners stay near-right unless the shot is extreme; a random bright blob's
    min-area rect can be right-angled too, which is why this is one term of the
    score and not a gate.
    """
    p = [(c[0], c[1]), (c[2], c[3]), (c[6], c[7]), (c[4], c[5])]
    worst = 1.0
    for i in range(4):
        ax, ay = p[(i - 1) % 4]
        bx, by = p[i]
        cx, cy = p[(i + 1) % 4]
        v1 = (ax - bx, ay - by)
        v2 = (cx - bx, cy - by)
        n1 = (v1[0] ** 2 + v1[1] ** 2) ** 0.5
        n2 = (v2[0] ** 2 + v2[1] ** 2) ** 0.5
        if n1 < 1e-6 or n2 < 1e-6:
            return 0.0
        cos = abs((v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2))
        worst = min(worst, 1.0 - cos)
    return max(0.0, worst)


def _score(cv2, gray, corners, w, h):
    """How much this quad looks like a lit screen. 0-1, or None to discard."""
    import numpy as np

    ok, _why = quad_is_sane(corners)
    if not ok:
        return None
    area = _quad_area(corners)
    if not (_MIN_AREA_FRAC <= area <= _MAX_AREA_FRAC):
        return None
    xs, ys = corners[0::2], corners[1::2]
    if (min(xs) < _EDGE_TOUCH and max(xs) > 1.0 - _EDGE_TOUCH) or \
       (min(ys) < _EDGE_TOUCH and max(ys) > 1.0 - _EDGE_TOUCH):
        return None                     # spans the whole frame: that's the shot

    poly = np.array([[corners[0] * w, corners[1] * h],
                     [corners[2] * w, corners[3] * h],
                     [corners[6] * w, corners[7] * h],
                     [corners[4] * w, corners[5] * h]], dtype=np.int32)
    inside = np.zeros(gray.shape, dtype=np.uint8)
    cv2.fillConvexPoly(inside, poly, 255)
    # The ring just outside the quad — what the screen is sitting against.
    ring = cv2.dilate(inside, np.ones((9, 9), np.uint8), iterations=3)
    ring = cv2.bitwise_and(ring, cv2.bitwise_not(inside))
    if inside.sum() < 255 * 200 or ring.sum() < 255 * 200:
        return None

    g = gray.astype(np.float32)
    in_mask = inside > 0
    out_mask = ring > 0
    in_mean = float(g[in_mask].mean())
    out_mean = float(g[out_mask].mean())
    # Brightness step. Signed on purpose: a screen is brighter than its bezel,
    # and a bright wall behind a dark object must NOT score as a screen.
    step = max(0.0, (in_mean - out_mean) / 90.0)

    # Detail: a screen carries content, a door or a whiteboard does not. Mean
    # gradient magnitude inside, normalised to a level real UI easily clears.
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    detail = min(1.0, float(mag[in_mask].mean()) / 26.0)

    # Size, gently: bigger is a better push target, but a 0.7-of-frame quad is
    # not 7x better than a 0.1 one, and rewarding size linearly is exactly how
    # the frame itself wins.
    size = min(1.0, (area / 0.28) ** 0.5)

    return round(0.34 * detail + 0.26 * min(1.0, step) + 0.22 * size
                 + 0.18 * _squareness(corners), 4)


def _contour_candidates(cv2, gray, w, h):
    import numpy as np
    out = []
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    med = float(np.median(blur))
    lo = int(max(0, 0.66 * med))
    hi = int(min(255, 1.33 * med))
    edges = cv2.Canny(blur, lo, hi)
    # A bezel's edge is broken by glare and by cables crossing it; closing the
    # gaps is what turns four arcs into one contour that approxPolyDP can call
    # a quadrilateral.
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    edges = cv2.erode(edges, np.ones((3, 3), np.uint8), iterations=1)
    found, _ = cv2.findContours(edges, cv2.RETR_LIST,
                                cv2.CHAIN_APPROX_SIMPLE)[-2:]
    for cnt in found:
        if cv2.contourArea(cnt) < _MIN_AREA_FRAC * w * h * 0.5:
            continue
        peri = cv2.arcLength(cnt, True)
        for eps in (0.02, 0.035, 0.05):
            ap = cv2.approxPolyDP(cnt, eps * peri, True)
            if len(ap) == 4 and cv2.isContourConvex(ap):
                pts = _order_corners([(p[0][0], p[0][1]) for p in ap])
                out.append([round(v, 4) for p in pts
                            for v in (p[0] / w, p[1] / h)])
                break
    return out


def _bright_candidates(cv2, gray, w, h):
    """The fallback generator: the largest bright blob's minimum-area rect.

    Canny finds nothing when the bezel and the desk are the same value, which
    is the ordinary case for a dark laptop on a dark desk. Otsu does not care
    about edges at all.
    """
    import numpy as np
    out = []
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    _t, mask = cv2.threshold(blur, 0, 255,
                             cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            np.ones((9, 9), np.uint8), iterations=2)
    found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                cv2.CHAIN_APPROX_SIMPLE)[-2:]
    found = sorted(found, key=cv2.contourArea, reverse=True)[:4]
    for cnt in found:
        if cv2.contourArea(cnt) < _MIN_AREA_FRAC * w * h * 0.5:
            continue
        box = cv2.boxPoints(cv2.minAreaRect(cnt))
        pts = _order_corners([(p[0], p[1]) for p in box])
        out.append([round(v, 4) for p in pts
                    for v in (p[0] / w, p[1] / h)])
    return out


def find_screen(frame_paths):
    """Find the device screen across one or more frames of the same shot.

    Returns {corners, confidence, agreement, n_frames, method} or
    {error: "..."} — never raises, because every caller of this is inside an
    agent turn the user is paying for.

    Several frames are voted rather than one trusted: a single frame can catch
    a glare flash or a hand crossing the bezel, and the AGREEMENT between
    frames is itself the honest confidence signal — corners that land in the
    same place three times are corners worth pinning content to.
    """
    cv2 = _cv2()
    if cv2 is None:
        return {"error": "OpenCV is unavailable in this worker."}
    try:
        # Probed once here rather than inside the per-frame helpers, so a
        # worker built without it says so instead of failing four times.
        __import__("numpy")
    except Exception:
        return {"error": "numpy is unavailable in this worker."}

    picks = []
    for path in frame_paths:
        try:
            img = cv2.imread(path)
            if img is None:
                continue
            h0, w0 = img.shape[:2]
            if w0 > _WORK_W:
                img = cv2.resize(img, (_WORK_W, int(h0 * _WORK_W / w0)),
                                 interpolation=cv2.INTER_AREA)
            h, w = img.shape[:2]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            best, best_s, best_m = None, 0.0, None
            for method, cands in (("contour", _contour_candidates(cv2, gray,
                                                                  w, h)),
                                  ("bright", _bright_candidates(cv2, gray,
                                                                w, h))):
                for c in cands:
                    s = _score(cv2, gray, c, w, h)
                    if s is not None and s > best_s:
                        best, best_s, best_m = c, s, method
            if best is not None:
                picks.append((best, best_s, best_m))
        except Exception:
            continue

    if not picks:
        return {"error": "no screen-shaped region found"}

    # Vote: keep the frames whose corners agree with the best-scoring one, and
    # average them. A frame that disagrees is dropped rather than averaged in —
    # averaging a glare-flash outlier moves every corner toward a place none of
    # the frames actually saw a screen.
    picks.sort(key=lambda p: -p[1])
    lead = picks[0][0]
    agree = [p for p in picks
             if max(abs(a - b) for a, b in zip(p[0], lead)) < 0.06]
    corners = [round(sum(p[0][i] for p in agree) / len(agree), 4)
               for i in range(8)]
    ok, _why = quad_is_sane(corners)
    if not ok:                       # the average of sane quads can still fold
        corners = lead
        agree = [picks[0]]
    conf = round(min(1.0, picks[0][1] * (0.55 + 0.45 * len(agree)
                                         / max(1, len(frame_paths)))), 3)
    return {"corners": corners, "confidence": conf,
            "agreement": len(agree), "n_frames": len(frame_paths),
            "method": picks[0][2]}
