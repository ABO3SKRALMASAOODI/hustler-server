"""Face and safe-area keep-out for motion graphics and the captions near them.

Showcase judging (Oct 2026): the '140' counter sat across Peter Thiel's mouth
for the whole payoff (and the push-in drew his face further into it), the
'Elon Musk' lower third ran across his mouth and chin, and slams reached
92-97% of the frame width, into Instagram's right button rail. The human
references never cover a face. Placement was a fixed y the agent typed from
memory; nothing compared the drawing with where the face actually is.

What is compared, at write time (add_motion_graphic / set_motion_graphic):

- The graphic: the write-time probe's INK box (motion_engine.INK_ALPHA —
  type, strokes and plates, not soft scrims or blooms), settled (an entrance
  overshoot in the first ENTRANCE_S is left out when later samples exist).
- The face: boxes measured on the SOURCE frames under the item's window (a
  high-recall cascade pass on exact frames when the tool can decode them,
  else the index's spatial samples), then mapped into OUTPUT coordinates at
  every TRACK_STEP_S of the window through what the render does to the
  picture: the frame crop and focus track (renderer.picture_mapping), the
  shared camera's zoom at that program second (renderer.zoom_state_at) and an
  active picture card. A push-in that grows the face into a graphic is seen.
- The 9:16 platform safe area: >= 6% side margins, the right ~12% clear
  between y 0.5 and 0.85 (the button rail), nothing below y 0.80.

When the graphic covers a face (or leaves the safe area) the solver moves it
to the nearest clear zone with the template's own knobs — ``y``; ``x``,
``align`` or ``side`` where it has them; a size/width step (at most 25%) when
that is cheaper than a long move or the only way to fit the safe width —
predicting each candidate's box, then rendering the cheapest few in one
browser session and keeping the first whose REAL ink is clear (a second
round searches over what the first measured). The tool says what moved; when
nothing fits the graphic stays and the tool returns a NOTE.
``allow_face_overlap`` keeps a deliberate design; ``behind_subject`` graphics
are behind the speaker by construction.

A lane with no browser (the agent, MCP and shorts lanes ship no Chromium, so
this is what production writes run) compares the template's NOMINAL ink
instead and says the box was estimated: the example's probed box moved by
the item's y/x/size, the lower third sized by its own copy and side. A move
there must clear the face by a full CLEARANCE (the estimate can be a wrapped
line short), and only templates whose width is their own knob
(COLUMN_TEMPLATES) are also moved out of the margins and the button rail.

Captions: the item stores ONE measurement, ``footprint`` (the box captions
keep clear of — the probe's COVER box, motion_engine.COVER_ALPHA, at the
final placement, or the estimated box on a browserless lane, flagged
``estimated`` — the frame aspect it was measured at and the face zones of
the window). The caption plan (worker/caption_carry.py, the one placement
pass for the libass and the motion captions alike) moves the captions a
graphic does not show into the nearest band clear of that box AND those
face zones (and the hair above them when there is room), so moving the
graphic never pushes a caption onto the mouth. The solver itself only
prices a spot on the caption band (CAPTION_PENALTY): word-level muting
keeps captions running beside most graphics.
"""

import json
import math
import os
import threading

import caption_carry

# ── the 9:16 platform safe area (output-frame fractions) ──────────────────
SAFE_X0, SAFE_X1 = 0.06, 0.94
SAFE_Y0, SAFE_Y1 = 0.06, 0.80
RAIL_X1, RAIL_Y0, RAIL_Y1 = 0.88, 0.50, 0.85
PORTRAIT_MIN = 1.6          # H/W at which the vertical-feed safe area applies
EDGE_TOL = 0.006            # measurement slack on a safe-area edge

# ── the face ──────────────────────────────────────────────────────────────
# A detected face box spans roughly brow/hairline to chin; the zone adds the
# forehead and chin a box can clip. The mouth band is its lower middle.
FACE_PAD = (0.06, 0.15, 0.06, 0.03)       # left, top, right, bottom x box size
MOUTH = (0.22, 0.58, 0.22, 0.08)          # insets: left, top, right, bottom
# A cascade box's lower edge lands anywhere from the lower lip to the neck,
# so a graphic must cover a real share of the zone (about the lower 15% of
# the face across its width) or of the mouth band before it counts as on the
# face: type tucked under the chin is the chest band, not a collision.
FACE_HIT = 0.10             # share of the face zone a graphic may not cover
MOUTH_HIT = 0.10            # share of the mouth band
HIT_SHARE = 0.15            # share of the faced moments that must collide
CLEARANCE = 0.025           # a moved graphic keeps this gap from the zone
CLEAR_PENALTY = 0.15        # solver cost of a spot that only grazes a zone
SHRINK_COST = 0.8           # solver cost per unit of size given up
SHRINK_STEPS = (0.95, 0.9, 0.85, 0.8, 0.75)   # a size/width step never goes below 75%
NEAR_S = 2.0                # an index sample this close speaks for a moment
FILL_S = 4.0                # a measured moment speaks for nearby track times
MOMENT_EVERY_S = 0.8        # evidence moments per window (exact frames)
MAX_MOMENTS = 6
TRACK_STEP_S = 0.1          # geometry is evaluated this often
MAX_TRACK = 90
ENTRANCE_S = 0.25           # probe samples before this are entrance motion

# Templates that never move: full-frame transitions and callouts whose job
# is to point AT something (they may target the face on purpose).
EXEMPT_CATEGORIES = frozenset(("transition", "texture"))
EXEMPT_TEMPLATES = frozenset(("arrow_callout", "circle_highlight",
                              "focus_spotlight", "caption_motion"))
HORIZONTAL_KEYS = ("align", "side")

# ── captions ──────────────────────────────────────────────────────────────
CAPTION_PENALTY = 0.2       # solver cost of a spot on the caption band


# ── boxes ─────────────────────────────────────────────────────────────────

def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter(a, b):
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def shift(b, dx, dy):
    return (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)


def scale_about(b, f):
    cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    hw, hh = (b[2] - b[0]) * f / 2.0, (b[3] - b[1]) * f / 2.0
    return (cx - hw, cy - hh, cx + hw, cy + hh)


def grow(b, m):
    return (b[0] - m, b[1] - m, b[2] + m, b[3] + m)


def union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def rounded(b, n=3):
    return [round(float(v), n) for v in b]


def _clip(z, f):
    """A zone clipped to the picture the face is seen in (a face entry may
    carry that rect as its last four numbers: a card or a letterboxed
    picture shows no face outside it)."""
    if len(f) < 8:
        return z
    c = f[4:8]
    return (max(z[0], c[0]), max(z[1], c[1]), min(z[2], c[2]), min(z[3], c[3]))


def face_zone(f):
    w, h = f[2] - f[0], f[3] - f[1]
    return _clip((f[0] - FACE_PAD[0] * w, f[1] - FACE_PAD[1] * h,
                  f[2] + FACE_PAD[2] * w, f[3] + FACE_PAD[3] * h), f)


def mouth_zone(f):
    w, h = f[2] - f[0], f[3] - f[1]
    return _clip((f[0] + MOUTH[0] * w, f[1] + MOUTH[1] * h,
                  f[2] - MOUTH[2] * w, f[3] - MOUTH[3] * h), f)


def portrait(W, H):
    return float(H) / max(1.0, float(W)) >= PORTRAIT_MIN


def safe_issues(box, W, H):
    """What of the vertical-feed safe area a box leaves ([] = inside, and
    always [] off a portrait frame)."""
    if not box or not portrait(W, H):
        return []
    x0, y0, x1, y1 = box
    out = []
    if x0 < SAFE_X0 - EDGE_TOL or x1 > SAFE_X1 + EDGE_TOL:
        out.append("side")
    if x1 > RAIL_X1 + EDGE_TOL and y1 > RAIL_Y0 and y0 < RAIL_Y1:
        out.append("rail")
    if y1 > SAFE_Y1 + EDGE_TOL:
        out.append("bottom")
    if y0 < SAFE_Y0 - EDGE_TOL:
        out.append("top")
    return out


ISSUE_TEXT = {
    "side": "reached into the 6% side margins",
    "rail": "ran into the platform's right button rail (x > 0.88 between y 0.5 and 0.85)",
    "bottom": "sat in the bottom UI band (below y 0.80)",
    "top": "sat in the top UI band (above y 0.06)",
}


# ── faces on a source frame ───────────────────────────────────────────────

def _cv():
    try:
        import cv2
        return cv2
    except Exception:
        return None


# One cascade set per thread (as spatial.py keeps them): an OpenCV
# CascadeClassifier is not safe to share between threads, and agent lanes
# run tool calls on several.
_CASCADES = threading.local()
_RANK = {"alt2": 0, "front": 1, "profile": 2}


def _cascades(cv2):
    cached = getattr(_CASCADES, "c", None)
    if cached is None:
        out = []
        try:
            base = cv2.data.haarcascades
        except Exception:
            base = None
        for kind, name in (("front", "haarcascade_frontalface_default.xml"),
                           ("alt2", "haarcascade_frontalface_alt2.xml"),
                           ("profile", "haarcascade_profileface.xml")):
            path = os.path.join(base, name) if base else ""
            try:
                c = cv2.CascadeClassifier(path) if os.path.exists(path) else None
            except Exception:
                c = None
            if c is not None and not c.empty():
                out.append((kind, c))
        _CASCADES.c = cached = out
    return cached


def _iou(a, b):
    i = inter(a, b)
    return i / max(1e-9, area(a) + area(b) - i)


def filter_faces(found):
    """[(box, support)] -> the speaker-candidate boxes, largest first.

    A cascade fires on hands, collars and background patches. What survives:
    boxes at least 40% of the largest face's width (a background face is not
    the one a graphic must avoid), backed by two detectors unless it is the
    largest or the highest box, and not stacked under a kept face (a chest or
    a hand below the chin is not a second head — however big it is). Two
    faces at most."""
    rows = sorted(((tuple(float(v) for v in b), int(s)) for b, s in found
                   if b and b[2] > b[0] and b[3] > b[1]),
                  key=lambda r: -area(r[0]))
    if not rows:
        return []
    wmax = rows[0][0][2] - rows[0][0][0]
    top = min(r[0][1] for r in rows)
    cands = [b for i, (b, s) in enumerate(rows)
             if b[2] - b[0] >= 0.4 * wmax and (i == 0 or s >= 2 or b[1] == top)]
    kept = []
    for b in sorted(cands, key=lambda b: b[1]):          # top first
        w = b[2] - b[0]
        if any(min(b[2], k[2]) - max(b[0], k[0]) >= 0.5 * min(w, k[2] - k[0])
               and b[1] >= k[1] + 0.5 * (k[3] - k[1]) for k in kept):
            continue
        kept.append(b)
    return sorted(kept, key=lambda b: -area(b))[:2]


def detect_faces(path, width=480):
    """High-recall face boxes (fractions of the frame) on one image.

    The index's sidecar runs one frontal cascade at 360 px (the profile
    cascade only when that finds nothing) and misses most three-quarter
    views: on the Thiel showcase it found a face in 8 of 67 frames. A
    keep-out that misses a face is the failure it exists to prevent, so
    this pass runs the frontal, alt2 and profile cascades and the profile
    MIRRORED (it only knows one direction) at 480 px, clusters the hits
    and filters body false positives (filter_faces): 67 of 67 there.

    None (not []) when it cannot measure — no OpenCV, no cascades, an
    unreadable frame — so the caller asks the index instead of reading the
    failure as "no face here"."""
    cv2 = _cv()
    if cv2 is None or not _cascades(cv2):
        return None
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    if w != width:
        img = cv2.resize(img, (width, max(1, int(round(h * width / float(w))))))
        h, w = img.shape[:2]
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    flipped = cv2.flip(gray, 1)
    ms = (max(24, w // 20), max(24, w // 20))
    hits = []                        # (box, detector rank: 0 = tightest)
    for kind, c in _cascades(cv2):
        passes = [(gray, False)] + ([(flipped, True)] if kind == "profile" else [])
        for g, mirror in passes:
            try:
                found = c.detectMultiScale(g, scaleFactor=1.1, minNeighbors=4, minSize=ms)
            except Exception:
                continue
            for x, y, bw, bh in found:
                x0, x1 = (w - x - bw, w - x) if mirror else (x, x + bw)
                hits.append(((x0 / w, y / h, x1 / w, (y + bh) / h), _RANK[kind]))
    clusters = []                    # [first (largest) box, members]
    for b, rank in sorted(hits, key=lambda r: -area(r[0])):
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        home = next((c for c in clusters if _iou(c[0], b) >= 0.3 or
                     (c[0][0] <= cx <= c[0][2] and c[0][1] <= cy <= c[0][3])), None)
        if home is None:
            clusters.append([b, [(b, rank)]])
        else:
            home[1].append((b, rank))
    # A cluster's box comes from its TIGHTEST detector (brow to chin): the
    # default frontal box of a close-up reaches the neck and a profile box
    # the hair (measured on the showcase close-ups: +0.05-0.07 of the frame
    # below the chin), and a union or mean of them drags the zone down
    # across the chest band where a graphic should go.
    found = []
    for _b, ms in clusters:
        best = min(r for _m, r in ms)
        tight = [m for m, r in ms if r == best]
        found.append((tuple(sum(m[i] for m in tight) / len(tight) for i in range(4)), len(ms)))
    return [rounded(b, 4) for b in filter_faces(found)]


# ── source -> output geometry ─────────────────────────────────────────────

class Geometry:
    """Where a SOURCE-frame box lands on the OUTPUT canvas at a program
    second: the frame fit (crop/pad, focus and focus track, picture), the
    shared camera's zoom, then any active picture card. Mirrors what the
    render does (plate.Probe uses the same three stages for luma)."""

    def __init__(self, edl, video, W, H, out_duration, zooms=None):
        self.sw, self.sh = float(video["width"]), float(video["height"])
        frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
        self.mode = (frame or {}).get("mode") or "crop"
        self.focus = ((frame or {}).get("focus_x"), (frame or {}).get("focus_y"))
        self.track = (frame or {}).get("focus_track") or []
        self.picture = (frame or {}).get("picture")
        self.W, self.H = int(W), int(H)
        fx = edl.get("effects") or {}
        # the camera as it renders (renderer.camera_zooms: edges near a cut
        # held through it) when the caller has it, else the authored zooms
        self.zooms = list(zooms) if zooms is not None else (fx.get("zooms") or [])
        self.cards = fx.get("picture_cards") or []
        self.out_duration = float(out_duration)

    def _frame_at(self, src_t):
        for sp in self.track:
            try:
                if float(sp.get("t0", 0)) <= src_t <= float(sp.get("t1", 0)):
                    fx, fy = sp.get("x"), sp.get("y")
                    mode = sp.get("mode") or self.mode
                    if fx is None and fy is None:
                        return self.focus, mode
                    return ((fx if fx is not None else self.focus[0],
                             fy if fy is not None else self.focus[1]), mode)
            except (TypeError, ValueError, AttributeError):
                continue
        return self.focus, self.mode

    def to_output(self, prog_t, src_t, box):
        """The face box on the canvas (unclipped, so its zone keeps the
        face's own proportions) followed by the rect of the canvas where
        that picture is visible — (x0, y0, x1, y1, cx0, cy0, cx1, cy1) —
        or None when none of the face is visible. (A stacked card can show
        one face in several panels: to_outputs lists them all.)"""
        outs = self.to_outputs(prog_t, src_t, box)
        return outs[0] if outs else None

    def to_outputs(self, prog_t, src_t, box):
        """Every place the face is visible on the canvas, as to_output
        entries: one, none, or one per panel of a stacked source card."""
        card = self._source_card_at(prog_t)
        if card is not None:
            return self._source_card_outputs(card, prog_t, box)
        out = self._program_output(prog_t, src_t, box)
        return [out] if out else []

    def _source_card_at(self, prog_t):
        import picture_cards
        for card in self.cards:
            try:
                if float(card["start"]) <= prog_t < float(card["end"]) \
                        and picture_cards.source_fed(card):
                    return card
            except (KeyError, TypeError, ValueError):
                continue
        return None

    def _source_card_outputs(self, card, prog_t, box):
        """A source-fed card (picture_cards.layout_filter) draws its SOURCE
        rect straight onto its box — no frame crop, no program card fit —
        and the camera then moves the composed canvas (a stacked card's
        windows are cut out of every zoom); the card shows its box of it."""
        import picture_cards
        import renderer   # lazy: the renderer imports the caption track
        panels = picture_cards.card_panels(card)
        z, cx, cy = 1.0, 0.5, 0.5
        if len(panels) == 1:
            z, cx, cy = renderer.zoom_state_at(self.zooms, prog_t, self.out_duration,
                                               size=(self.W, self.H))
        vx, vy = (1.0 - 1.0 / z) * cx, (1.0 - 1.0 / z) * cy

        def zm(r):
            if z <= 1.0001:
                return tuple(r)
            return ((r[0] - vx) * z, (r[1] - vy) * z, (r[2] - vx) * z, (r[3] - vy) * z)
        out = []
        for pbox, rect in panels:
            try:
                rect = picture_cards.match_rect(rect, pbox, self.sw, self.sh, self.W, self.H)
                kx = (pbox[2] - pbox[0]) / (rect[2] - rect[0])
                ky = (pbox[3] - pbox[1]) / (rect[3] - rect[1])
            except (TypeError, ValueError, ZeroDivisionError):
                continue

            def place(r):
                return (pbox[0] + (r[0] - rect[0]) * kx, pbox[1] + (r[1] - rect[1]) * ky,
                        pbox[0] + (r[2] - rect[0]) * kx, pbox[1] + (r[3] - rect[1]) * ky)
            b = zm(place(box))
            # a single card shows the source around its rect at the same
            # scale (a zoom reveals it); a stack panel shows its rect alone
            seen = zm(place((0.0, 0.0, 1.0, 1.0))) if len(panels) == 1 else tuple(pbox)
            clip = (max(seen[0], pbox[0], 0.0), max(seen[1], pbox[1], 0.0),
                    min(seen[2], pbox[2], 1.0), min(seen[3], pbox[3], 1.0))
            if min(b[2], clip[2]) <= max(b[0], clip[0]) or min(b[3], clip[3]) <= max(b[1], clip[1]):
                continue
            out.append(tuple(b) + tuple(clip))
        return out

    def _program_output(self, prog_t, src_t, box):
        import renderer   # lazy: the renderer imports the caption track
        focus, mode = self._frame_at(src_t)
        src, dest = renderer.picture_mapping(self.sw, self.sh, self.W, self.H,
                                             mode, focus, self.picture)
        sx = (dest[2] - dest[0]) / (src[2] - src[0])
        sy = (dest[3] - dest[1]) / (src[3] - src[1])
        b = (dest[0] + (box[0] - src[0]) * sx, dest[1] + (box[1] - src[1]) * sy,
             dest[0] + (box[2] - src[0]) * sx, dest[1] + (box[3] - src[1]) * sy)
        clip = tuple(dest)
        z, cx, cy = renderer.zoom_state_at(self.zooms, prog_t, self.out_duration,
                                           size=(self.W, self.H))
        if z > 1.0001:
            vx, vy = (1.0 - 1.0 / z) * cx, (1.0 - 1.0 / z) * cy

            def zm(r):
                return ((r[0] - vx) * z, (r[1] - vy) * z, (r[2] - vx) * z, (r[3] - vy) * z)
            b, clip = zm(b), zm(clip)
        for card in self.cards:
            try:
                if not float(card["start"]) <= prog_t < float(card["end"]):
                    continue
                if (card.get("source") or card.get("panels")):
                    continue          # source cards: _source_card_outputs
                fit = self._card_fit(card)
            except (KeyError, TypeError, ValueError):
                continue
            if fit is None:
                return None
            b, clip = fit(b), fit(clip)
            bx = card["box"]
            clip = (max(clip[0], bx[0]), max(clip[1], bx[1]), min(clip[2], bx[2]), min(clip[3], bx[3]))
        clip = (max(0.0, clip[0]), max(0.0, clip[1]), min(1.0, clip[2]), min(1.0, clip[3]))
        if min(b[2], clip[2]) <= max(b[0], clip[0]) or min(b[3], clip[3]) <= max(b[1], clip[1]):
            return None
        return tuple(b) + tuple(clip)

    def _card_fit(self, card):
        """The affine map of the program into a picture card's box (cover
        crop by default, contain for fit='pad'), or None for a degenerate
        card. The caller clips to the card."""
        sr = self.picture or (0.0, 0.0, 1.0, 1.0)
        bx = card["box"]
        W, H = float(self.W), float(self.H)
        sw, sh = (sr[2] - sr[0]) * W, (sr[3] - sr[1]) * H
        bw, bh = (bx[2] - bx[0]) * W, (bx[3] - bx[1]) * H
        if sw <= 0 or sh <= 0 or bw <= 0 or bh <= 0:
            return None
        k = (max if (card.get("fit") or "crop") == "crop" else min)(bw / sw, bh / sh)
        ox, oy = (sw * k - bw) / 2.0, (sh * k - bh) / 2.0

        def fit(r):
            return ((bx[0] * W + (r[0] * W - sr[0] * W) * k - ox) / W,
                    (bx[1] * H + (r[1] * H - sr[1] * H) * k - oy) / H,
                    (bx[0] * W + (r[2] * W - sr[0] * W) * k - ox) / W,
                    (bx[1] * H + (r[3] * H - sr[1] * H) * k - oy) / H)
        return fit


# ── the face over a program window ────────────────────────────────────────

def _segment_of(tl, src_t):
    for s, e in tl.segs:
        if s - 1e-6 <= src_t <= e + 1e-6:
            return (s, e)
    return None


def _shot_at(shots, t):
    for sh in shots or []:
        try:
            if float(sh["start"]) - 1e-6 <= t < float(sh["end"]) + 1e-6:
                return sh.get("id", (sh["start"], sh["end"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return None


def _index_faces(index, src_t, seg):
    """Faces of the nearest index spatial sample in the same kept segment
    and source shot within NEAR_S, filtered like a measured frame; None when
    no sample speaks for the moment."""
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    shots = (index or {}).get("shots") or []
    here = _shot_at(shots, src_t)
    best = None
    for s in samples:
        try:
            t = float(s["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(t - src_t) > NEAR_S or (seg and not seg[0] - 1e-6 <= t <= seg[1] + 1e-6):
            continue
        if shots and _shot_at(shots, t) != here:
            continue
        if best is None or abs(t - src_t) < abs(best[0] - src_t):
            best = (t, s)
    if best is None:
        return None
    return filter_faces([(b, 1) for b in best[1].get("faces") or []])


def _agrees(a, b):
    cx, cy = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    return _iou(a, b) >= 0.2 or (b[0] <= cx <= b[2] and b[1] <= cy <= b[3])


def _consistent(moments):
    """Drop a box no other moment of the same take agrees with while those
    moments do see a face (a lone hand or collar where the cascade missed
    the head, e.g. Thiel at 860.3 s), and a box stacked under a face the
    other moments agree on."""
    out = []
    for i, (t, key, faces) in enumerate(moments):
        peers = [b for j, (_t, k, fs) in enumerate(moments) if j != i and k == key for b in fs]
        if not peers or not faces:
            out.append((t, key, faces))
            continue
        kept = []
        for b in faces:
            if not any(_agrees(b, p) for p in peers):
                continue
            w = b[2] - b[0]
            if any(min(b[2], p[2]) - max(b[0], p[0]) >= 0.5 * min(w, p[2] - p[0])
                   and b[1] >= p[1] + 0.5 * (p[3] - p[1]) for p in peers
                   if not _agrees(p, b)):
                continue
            kept.append(b)
        out.append((t, key, kept))
    return out


def _cover_windows(edl):
    """Program windows where an opaque overlay replaces the picture (a b-roll
    cutaway with fit 'cover' or 'picture'): the speaker is not on screen."""
    out = []
    for o in edl.get("overlays") or []:
        try:
            if o.get("fit") not in ("cover", "picture") or o.get("screen"):
                continue
            op = o.get("opacity")
            if isinstance(op, (int, float)) and not isinstance(op, bool) and op < 0.9:
                continue
            a = float(o.get("start") or 0.0)
            out.append((a, a + float(o.get("duration_s") or 0.0)))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def camera_zooms(edl, index, tl):
    """The zooms as the render plays them (a zoom edge near a program cut
    moved onto it and held through it, renderer.camera_zooms), or the
    authored list when that cannot be worked out."""
    try:
        import renderer
        return renderer.camera_zooms(edl, index, tl)
    except Exception:  # noqa: BLE001 — the authored zooms are a fine guess
        return ((edl.get("effects") or {}).get("zooms") or [])


def face_track(edl, index, W, H, start, end, measure=None):
    """[(program second, [face boxes in output fractions])] across
    [start, end], every TRACK_STEP_S.

    Evidence: a few moments per window (MOMENT_EVERY_S, at most MAX_MOMENTS)
    measured on the exact source frame by ``measure(src_t)`` (None when it
    cannot), else the index's nearest spatial sample. Every track second
    borrows the nearest moment with a face in its own kept segment within
    FILL_S (a cascade that misses one frame of a talking head is a miss,
    not an empty shot) and is mapped through the geometry AT that second,
    so zooms and cards move the face as the viewer sees it. A second under
    an inserted clip or an opaque b-roll overlay that fills the picture
    (fit cover/picture) shows no face."""
    from timeline import Timeline
    video = (index or {}).get("video") or {}
    if not edl.get("keep") or not video.get("width") or not video.get("height") \
            or edl.get("canvas"):
        return []
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    start, end = float(start), min(float(end), tl.out_duration)
    if end - start < 0.05:
        return []
    geo = Geometry(edl, video, W, H, tl.out_duration, zooms=camera_zooms(edl, index, tl))
    span = end - start
    n = int(min(MAX_MOMENTS, max(2, math.ceil(span / MOMENT_EVERY_S) + 1)))
    moments = []
    for i in range(n):
        t = start + span * (i + 0.5) / n
        src = tl.out_to_src(t)
        if src is None:
            continue
        seg = _segment_of(tl, src)
        faces = measure(src) if measure else None
        if faces is None:
            faces = _index_faces(index, src, seg)
        moments.append((t, (seg, _shot_at((index or {}).get("shots"), src)), faces or []))
    moments = _consistent(moments)
    covers = _cover_windows(edl)
    steps = int(min(MAX_TRACK, max(2, math.ceil(span / TRACK_STEP_S) + 1)))
    out = []
    for i in range(steps):
        t = start + span * i / (steps - 1)
        src = tl.out_to_src(t)
        if src is None or any(a <= t < b for a, b in covers):
            out.append((t, []))
            continue
        seg = (_segment_of(tl, src), _shot_at((index or {}).get("shots"), src))
        near = [m for m in moments if m[1] == seg and m[2] and abs(m[0] - t) <= FILL_S]
        if not near:
            out.append((t, []))
            continue
        faces = min(near, key=lambda m: abs(m[0] - t))[2]
        mapped = [m for f in faces for m in geo.to_outputs(t, src, f)]
        out.append((t, mapped))
    return out


def zones_of(track, kind=None):
    """Distinct face zones over a track (near-identical boxes merged), for
    the solver, reports and the stored footprint; kind=mouth_zone gives the
    mouth bands the same way."""
    kind = kind or face_zone
    out = []
    for _t, faces in track:
        for f in faces:
            z = kind(f)
            hit = next((o for o in out if _iou(o, z) >= 0.5), None)
            if hit is None:
                out.append(z)
            else:
                out[out.index(hit)] = union([hit, z])
    return out


def on_face(box, zones, mouths=()):
    """The solver's fast version of assess() over merged zones: does a box
    cover a real share of a face zone or a mouth band?"""
    return any(inter(box, z) >= FACE_HIT * area(z) for z in zones) or \
        any(inter(box, m) >= MOUTH_HIT * area(m) for m in mouths)


def assess(box, track):
    """How a graphic box meets the faces: {'hit': bool, 'mouth': bool,
    'when': (t0, t1) of the colliding seconds, 'face': union of the zones
    it hits} — hit when it covers FACE_HIT of a face zone or MOUTH_HIT of
    the mouth band in HIT_SHARE of the seconds that show a face."""
    faced = hits = 0
    mouth = False
    when, zs = [], []
    for t, faces in track:
        if not faces:
            continue
        faced += 1
        hit = False
        for f in faces:
            fz, mz = face_zone(f), mouth_zone(f)
            a = inter(box, fz) / max(1e-9, area(fz))
            m = inter(box, mz) / max(1e-9, area(mz))
            if a >= FACE_HIT or m >= MOUTH_HIT:
                hit = True
                mouth = mouth or m >= MOUTH_HIT
                zs.append(fz)
        if hit:
            hits += 1
            when.append(t)
    if faced > 2:
        collide = hits >= max(2, HIT_SHARE * faced)
    else:
        collide = hits > 0
    return {"hit": bool(collide), "mouth": bool(collide and mouth),
            "when": (min(when), max(when)) if when else None,
            "face": union(zs) if zs else None}


def clear_of_faces(box, zones):
    g = grow(box, CLEARANCE)
    return all(inter(g, z) <= 0.01 * area(z) for z in zones)


# ── a lane without a browser ──────────────────────────────────────────────
# The agent/MCP/shorts lanes ship no Chromium, so the write-time probe cannot
# run there. The keep-out still checks the face against the template's
# NOMINAL ink: its example measured by the probe at y = 0.5 (x0, top offset
# from y, x1, bottom offset), scaled by the item's size and moved by its
# x/y. It is an estimate — the reply says so. Widths depend on the copy, so
# the safe area is acted on only for COLUMN_TEMPLATES (width = their knob).
NOMINAL_INK = {
    "bar_compare": (0.056, -0.208, 0.944, 0.208),
    "chapter_title": (0.115, -0.133, 0.93, 0.133),
    "chat_bubbles": (0.085, -0.3, 0.904, 0.015),
    "checklist": (0.237, -0.096, 0.756, 0.102),
    "comment_cta": (0.067, -0.156, 0.933, 0.162),
    "counter": (0.081, -0.075, 0.933, 0.088),
    "emoji_pop": (0.548, -0.058, 0.848, 0.081),
    "follow_cta": (0.063, -0.056, 0.937, 0.054),
    "glow_title": (0.085, -0.119, 0.919, 0.121),
    "hook_title": (0.07, -0.056, 0.937, 0.069),
    "image_card": (0.104, -0.179, 0.907, 0.171),
    "line_chart": (0.056, -0.229, 0.944, 0.229),
    "lower_third": (0.067, -0.044, 0.722, 0.044),
    "marker_text": (0.07, -0.052, 0.937, 0.06),
    "notification": (0.056, -0.173, 0.944, 0.069),
    "photo_stack": (0.059, -0.16, 0.941, 0.229),
    "phrase_build": (0.093, -0.125, 0.907, 0.127),
    "post_card": (0.081, -0.154, 0.926, 0.154),
    "progress_ring": (0.215, -0.162, 0.789, 0.194),
    "quote_card": (0.141, -0.175, 0.856, 0.15),
    "save_cta": (0.204, -0.048, 0.796, 0.046),
    "search_bar": (0.085, -0.04, 0.919, 0.281),
    "stat_card": (0.078, -0.158, 0.922, 0.156),
    "text_scramble": (0.085, -0.023, 0.911, 0.027),
    "timeline_steps": (0.096, -0.11, 0.889, 0.113),
    "typewriter": (0.178, -0.056, 0.83, 0.056),
    "versus_split": (0.126, -0.096, 0.852, 0.096),
    "word_slam": (0.081, -0.077, 0.922, 0.079),
}


def nominal_ink(template, spec, params, frame=None):
    """The estimated ink box of a library template at these params, or None.
    ``frame`` = (W, H) lets the box follow a template's own clamp into the
    9:16 safe band (the counter and the lower third keep y 0.08-0.80)."""
    row = NOMINAL_INK.get(template)
    pspec = (spec or {}).get("params") or {}
    if not row or (pspec.get("y") or {}).get("type") != "float":
        return None
    example = (spec or {}).get("example") or {}
    k = 1.0
    for key in ("size", "scale", "width"):
        sp = pspec.get(key) or {}
        if sp.get("type") == "float":
            ref = num(example, key, sp.get("default", sp.get("max", 1.0)))
            k = num(params, key, ref) / max(ref, 1e-6)
            break
    xs = pspec.get("x") or {}
    dx = (num(params, "x", 0.5) - num(example, "x", xs.get("default", 0.5))) \
        if xs.get("type") == "float" else 0.0
    y = num(params, "y", pspec["y"].get("default", 0.5))
    if template in ("lower_third", "counter"):
        box = (_lower_third_ink if template == "lower_third" else _counter_ink)(params or {}, y, k)
        if frame and portrait(*frame) and box[3] - box[1] <= CLAMP_BAND[1] - CLAMP_BAND[0]:
            dy = max(0.0, CLAMP_BAND[0] - box[1]) - max(0.0, box[3] - CLAMP_BAND[1])
            box = shift(box, 0.0, dy)
        return box
    x0, top, x1, bottom = row
    return (0.5 + dx - (0.5 - x0) * k, y + top * k, 0.5 + dx + (x1 - 0.5) * k, y + bottom * k)


# The counter's box is set by its copy too: a short figure is drawn huge
# (capped by height), a long one fitted to the width, and a label adds a
# line under (or over) it — larger under a short figure. Probed at y 0.5,
# size 1 (heights): '3x' 0.162, '100%' 0.146, '$1.2B' 0.15, '62,000+' 0.113,
# '1,000,000 users' 0.075; with a label '3x' 0.256, '100%' 0.235, '$1.2B'
# 0.25, '62,000+' 0.163 (the example), a label that wraps under '40' 0.289.
# The example's 0.163 put the Jobs '40 / fonts on the screen' (0.256) clear
# of a face it covered. Both tags stay inside y 0.08-0.80 on a 9:16 frame.
CLAMP_BAND = (0.08, 0.80)
COUNTER_W = 0.852            # the safe width the figure is fitted to


def _counter_ink(params, y, k):
    n = len(str(params.get("value") or "").strip())
    label = str(params.get("label") or "").replace("*", "").strip()
    h = 0.162 if n <= 3 else 0.15 if n <= 5 else 0.115 if n <= 7 else 0.08
    w = min(COUNTER_W, 0.12 + 0.19 * n)
    if label:
        h += (0.09 if n <= 5 else 0.05) + (0.035 if len(label) > 30 else 0.0)
        w = max(w, min(0.75, 0.02 + 0.035 * len(label)))
    w, half = min(COUNTER_W, w * k), h * k / 2.0
    if params.get("align") == "left":
        return (0.056, y - half, 0.056 + w, y + half)
    return (0.5 - w / 2.0, y - half, 0.5 + w / 2.0, y + half)


# The lower third is the graphic placed beside a face by design, and its box
# is set by its copy: it hangs off the left (or right) margin, a name alone
# is ONE line about as wide as the name, and a long role wraps to a second
# line. Probed at y 0.5: 'Joe Rogan' alone 0.04 tall x 0.37 wide; 'Peter
# Thiel / Co-founder, PayPal' 0.088 x 0.655; 'Elon Musk / on The Joe Rogan
# Experience' 0.12 x 0.61; a 46-character role 0.154. The example's box for
# every one of them moved name-only tags that sat beside the face and left
# wrapped ones on the chin.
LT_MARGIN, LT_WIDTH = 0.067, 0.655


def _lower_third_ink(params, y, k):
    name = str(params.get("name") or "")
    role = str(params.get("role") or "").strip()
    w = LT_WIDTH
    if not role:
        half, w = 0.021, min(w, 0.03 + 0.038 * len(name))
    elif len(role) > 44:
        half = 0.078
    elif len(role) > 22:
        half = 0.062
    else:
        half = 0.044
    w, half = min(1.0 - 2 * LT_MARGIN, w * k), half * k
    if params.get("side") == "right":
        return (1.0 - LT_MARGIN - w, y - half, 1.0 - LT_MARGIN, y + half)
    return (LT_MARGIN, y - half, LT_MARGIN + w, y + half)


# Templates whose widest line FILLS their own width knob (word_slam fits its
# type to ``width``, phrase_build its widest row to the column): their
# estimated width is the drawn width whatever the copy, so a lane without a
# browser can keep them out of the side margins and the button rail as well.
# Every other template sizes to its copy, and its safe area waits for a probe.
COLUMN_TEMPLATES = frozenset(("word_slam", "phrase_build"))


# ── captions as obstacles ─────────────────────────────────────────────────

def caption_band(y):
    """The block a caption anchored at ``y`` occupies (caption_carry's
    geometry: the one caption placement pass)."""
    return (caption_carry.COLUMN[0], y - caption_carry.CAP_HALF_H,
            caption_carry.COLUMN[1], y + caption_carry.CAP_HALF_H)


# ── the solver ────────────────────────────────────────────────────────────

def settled_box(rep, times, field="ink"):
    """A settled box from a probe report: the union of its per-time ``field``
    boxes ("ink": what hides a face, INK_ALPHA; "cover": what a caption must
    clear, COVER_ALPHA), leaving out samples inside the entrance
    (ENTRANCE_S) when at least two later ones exist. None when the field
    holds no box (nothing that dense: a soft glow has no ink). A report
    without the field (an older probe) falls back to "ink", then to the
    visible boxes."""
    for key in ((field, "ink") if field != "ink" else ("ink",)):
        if key not in rep:
            continue
        rows = rep.get(key)
        if not isinstance(rows, list):
            break
        if rows and len(rows) == len(times):
            rows = [(t, b) for t, b in zip(times, rows) if b]
            late = [b for t, b in rows if t >= ENTRANCE_S]
            return union(late if len(late) >= 2 else [b for _t, b in rows])
        return union([b for b in rows if b])
    return union(rep.get("bboxes") or [])


def settled_ink(rep, times):
    """The graphic's settled INK box (what the face keep-out compares)."""
    return settled_box(rep, times, "ink")


def cover_box(rep, times):
    """The settled COVER box: what the item stores for the captions."""
    return settled_box(rep, times, "cover")


def applicable(template, spec, layer):
    if layer == "behind_subject" or template in EXEMPT_TEMPLATES:
        return False
    return (spec or {}).get("category") not in EXEMPT_CATEGORIES


def num(p, key, default):
    try:
        return float(p.get(key, default))
    except (TypeError, ValueError):
        return default


def patch_cost(patch, params, spec):
    """The horizontal part of a placement's cost: x travel, a size step,
    another align/side (y travel is added by the search)."""
    pspec = (spec or {}).get("params") or {}
    c = 0.0
    for k, v in patch.items():
        sp = pspec.get(k) or {}
        if k == "x":
            c += 0.5 * abs(float(v) - num(params, "x", sp.get("default", 0.5)))
        elif k in ("width", "size", "scale"):
            cur = num(params, k, sp.get("default", sp.get("max", 1.0)))
            c += SHRINK_COST * max(0.0, 1.0 - float(v) / max(cur, 1e-6))
        elif k in HORIZONTAL_KEYS:
            c += 0.08
    return c


def candidates(template, spec, params, box, variants, zones, W, H,
               captions=(), predict=True, mouths=(), clear_penalty=CLEAR_PENALTY,
               require_clear=False):
    """Ranked placements [(cost, patch, predicted box)] off every face (no real
    share of a zone or mouth band, on_face; grazing a zone's CLEARANCE costs
    CLEAR_PENALTY) and inside the safe area. ``variants`` are measured alternatives at the
    current y: [(patch, box)] (another align/side value, or a placement a
    first verification round rendered). Moves use the template's own ``y``
    (a box moves 1:1 with it), ``x`` and those enums, plus a size/width step
    when the graphic is too wide for the safe area. predict=False searches
    only y over the measured variants (no predicted x/size boxes). A move
    OFF a face pays ``clear_penalty`` for a spot that grazes a zone; a
    safe-area fix passes 0 (the design already sat that close).
    ``require_clear`` keeps only spots a full CLEARANCE off every zone (an
    ESTIMATED box, whose real ink can run a wrapped line taller than the
    template's example: a graze on the estimate is a collision on screen)."""
    pspec = (spec or {}).get("params") or {}
    port = portrait(W, H)
    y_lo, y_hi = (SAFE_Y0, SAFE_Y1) if port else (0.02, 0.98)
    horiz = ([({}, tuple(box), 0.0)] if box else []) + \
        [(dict(p), tuple(b), patch_cost(p, params, spec)) for p, b in variants if b]
    sizer = next((k for k in ("width", "size", "scale")
                  if (pspec.get(k) or {}).get("type") == "float"), None)
    if sizer and predict:
        # an unset size knob reads as its maximum (word_slam's width: 0.85)
        sp = pspec[sizer]
        cur = num(params, sizer, sp.get("default", sp.get("max", 1.0)))
        lo = float(sp.get("min", 0.0))
        for p, b, c in list(horiz):
            cx = (b[0] + b[2]) / 2.0
            fits = []
            if port and {"side", "rail"} & set(safe_issues((b[0], 0.6, b[2], 0.7), W, H)):
                # the exact steps that bring the width inside the margins / rail
                fits = [min((RAIL_X1 - cx) / max(1e-6, b[2] - cx), (cx - SAFE_X0) / max(1e-6, cx - b[0])),
                        min((SAFE_X1 - cx) / max(1e-6, b[2] - cx), (cx - SAFE_X0) / max(1e-6, cx - b[0]))]
            for f in sorted({round(v - 0.005, 3) for v in fits} | set(SHRINK_STEPS)):
                if f >= 1.0 or f < 0.75 or cur * f < lo:
                    continue
                horiz.append((dict(p, **{sizer: round(cur * f, 3)}), scale_about(b, f),
                              c + SHRINK_COST * (1 - f)))
    xs = pspec.get("x") if (pspec.get("x") or {}).get("type") == "float" else None
    if xs and predict:
        xcur = num(params, "x", xs.get("default", 0.5))
        steps = sorted({round(0.01 * k, 2) for k in range(-10, 11)} |
                       {round(0.02 * k, 2) for k in range(-20, 21)})
        for p, b, c in list(horiz):
            for d in steps:
                nx = round(xcur + d, 3)
                if not d or not float(xs.get("min", 0)) <= nx <= float(xs.get("max", 1)):
                    continue
                nb = shift(b, d, 0.0)
                if nb[0] < -EDGE_TOL or nb[2] > 1 + EDGE_TOL:
                    continue
                horiz.append((dict(p, x=nx), nb, c + 0.5 * abs(d)))
    ys = pspec.get("y") if (pspec.get("y") or {}).get("type") == "float" else None
    ycur = num(params, "y", (ys or {}).get("default", 0.5)) if ys else None
    if ys:
        lo, hi = float(ys.get("min", 0.0)), float(ys.get("max", 1.0))
        heights = [round(lo + 0.005 * i, 3) for i in range(int(round((hi - lo) / 0.005)) + 1)]
    else:
        heights = [None]
    out = []
    for p, b, c in horiz:
        for ny in heights:
            dy = (ny - ycur) if ny is not None else 0.0
            nb = shift(b, 0.0, dy)
            if nb[1] < y_lo - EDGE_TOL or nb[3] > y_hi + EDGE_TOL or nb[0] < -EDGE_TOL \
                    or nb[2] > 1 + EDGE_TOL:
                continue
            if safe_issues(nb, W, H) or on_face(nb, zones, mouths):
                continue
            cost = c + abs(dy)
            if (clear_penalty or require_clear) and not clear_of_faces(nb, zones):
                if require_clear:
                    continue
                cost += clear_penalty
            if any(inter(nb, cb) > 0 for cb in captions):
                cost += CAPTION_PENALTY
            patch = dict(p)
            if ny is not None and abs(dy) > 1e-9:
                patch["y"] = ny
            out.append((round(cost, 4), patch, nb))
    out.sort(key=lambda r: (r[0], json.dumps(r[1], sort_keys=True)))
    return out


def distinct(cands, k=6):
    """The k cheapest candidates that differ in their knobs (another value of
    x/size/align/side) or land more than 0.03 apart in y — verification
    samples alternatives, not neighbours."""
    def knobs(c):
        return {key: v for key, v in c[1].items() if key != "y"}

    def same(a, b):
        ka, kb = knobs(a), knobs(b)
        if set(ka) != set(kb):
            return False
        for key in ka:
            va, vb = ka[key], kb[key]
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                if abs(float(va) - float(vb)) >= 0.015:
                    return False
            elif va != vb:
                return False
        return abs(a[2][1] - b[2][1]) < 0.03
    picked = []
    for c in cands:
        if any(same(c, p) for p in picked):
            continue
        picked.append(c)
        if len(picked) == k:
            break
    return picked


def where_label(box, zones):
    """'above the head' / 'below the chin' / 'beside the face' / ''."""
    if not zones:
        return ""
    top = min(z[1] for z in zones)
    bottom = max(z[3] for z in zones)
    # A clear spot may still graze the zones' outer fifth (a push-in grows
    # the chin zone for part of the window; on_face tolerates a sliver), and
    # that is still the band under the chin, not a spot beside the face.
    edge = max(0.01, 0.2 * (bottom - top))
    if box[3] <= top + edge:
        return "above the head"
    if box[1] >= bottom - edge:
        return "below the chin"
    return "beside the face"
