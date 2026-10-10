"""The caption placement solver: where a caption page may sit.

Showcase judging (Oct 2026, round 4): in Elon's speaker + screen stack the
captions sat in the ~55 px gap between the two panels (an explicit 'middle'
placement at y 0.585), their glyphs crossing the speaker panel's bottom edge
and their descenders hitting the screen panel, while the top 28% of the
frame was empty; and when the stack ended, the page carried on at the same
height over the host's chin and microphone. The old placement only ever
checked a caption against the boxes graphics draw.

This module is the geometry the caption plan (worker/caption_carry.py — the
ONE placement pass every caption path reads) solves with. Over a program
window it collects:

HARD no-go zones (a caption block never touches one):
  - card and panel EDGES and the SEAMS between panels (picture_cards
    .card_boxes of every live card: a thin band along each horizontal edge,
    and a whole panel when one of its vertical edges runs through the
    caption column — a side-by-side layout);
  - the speaker's FACE, chin included (CHIN_PAD below the zone; the caller
    hands the zones, hair first where a band that tall fits);
  - PROPS where the index knows them (spatial samples' ``props`` boxes: a
    microphone, a laptop), mapped onto the canvas like the faces;
  - the FOOTPRINTS of the live graphics (the caller's boxes);
  - the free-tier WATERMARK's reserved box (keepout.watermark_zone at the
    normal corner — reserving it for text is always allowed, and a caption
    there would sit under the mark on a free-tier final);
  - the platform SIDE CROP (SIDE_CROP_PORTRAIT of a 9:16 frame each side,
    the margin feeds crop): the caption column never reaches into it.

SOFT costs (a band may hold them, at a price): source text under the band
(a screen share's UI, a sign), and sitting over a panel's picture instead of
the canvas around it.

A free band shorter than MIN_BAND_LINES caption lines (or the template's own
MIN_BAND_H) is no band. Among the rest the LARGEST wins, less a small price
for moving away from the caption's usual anchor; a band the previous page
used that is still free wins outright (no needless jumps). Pure functions of
the EDL, the index and the timeline; nothing here touches the browser.
"""

import math

# The caption column a 9:16 reel may use: feeds crop about this share of the
# frame on each side (Instagram's grid and Reels' side rail).
SIDE_CROP_PORTRAIT = 0.09
# A caption block keeps this far from a card or panel edge (frame height).
EDGE_PAD = 0.012
# A face zone (keepout.face_zone) ends at the chin box Haar gives; the jaw
# and chin run on below it by about this share of its height.
CHIN_PAD = 0.08
# Clearance kept from a prop box (frame height).
PROP_PAD = 0.01
# A band shorter than this many caption lines is no band (a two-line page
# squeezed into a 55 px seam is the defect this module exists for).
MIN_BAND_LINES = 1.4
# Band choice: the free height that counts (a taller band is no better),
# what moving the caption away from its usual anchor costs per unit of
# frame height, what source text under the band costs at full coverage,
# and what sitting over a panel's picture (not the canvas) costs.
SIZE_CAP = 0.24
MOVE_COST = 0.25
TEXT_COST = 0.6
PANEL_COST = 0.03
# A word whose onset falls this close before a layout change belongs to the
# layout after it: its page appears ON the change (the judged caption that
# jumped to its new place two frames before the stack arrived). Never more
# than two frames — a word is never revealed later than that for a cut.
SNAP_FRAMES = 2

# Motion caption looks (worker/motion/templates/caption_motion.html CFG):
# base font px on a 1080x1920 design canvas at size 1, and the line height.
LOOK_LINE = {"clean": (70, 1.16), "editorial": (72, 1.08), "lockup": (44, 1.6),
             "pop": (94, 1.0), "box": (62, 1.3), "serif": (66, 1.14),
             "glow": (74, 1.1), "stack": (48, 1.7), "mono": (46, 1.4)}
_SIZE_OF = {"s": 0.82, "m": 1.0, "l": 1.2, "xl": 1.45}


def snap_s(fps=30.0):
    """SNAP_FRAMES in seconds (a hair over, so a rounded millisecond
    onset exactly two frames early still snaps)."""
    return SNAP_FRAMES / max(1.0, float(fps or 30.0)) + 0.008


# ── the caption's size ───────────────────────────────────────────────────

def line_height(edl, W, H):
    """One caption line's height as a share of the frame height, for the
    caption style the EDL burns (a motion look, else the libass preset)."""
    caps = (edl or {}).get("captions") or {}
    style = caps.get("style") if isinstance(caps.get("style"), dict) else {}
    try:
        import motion_captions
        look = motion_captions.look_of(edl)
    except Exception:  # noqa: BLE001 — an unreadable style: the editorial size
        look = None
    W, H = float(W), float(H)
    if look:
        fs, lh = LOOK_LINE.get(look, LOOK_LINE["editorial"])
        try:
            size = float(style.get("size_scale") or _SIZE_OF.get(style.get("size") or "m", 1.0))
        except (TypeError, ValueError):
            size = 1.0
        dh = 1080.0 * H / max(W, 1.0)                  # the design canvas
        k = min(1.0, math.sqrt(dh / 1920.0))
        return fs * size * k * lh / dh
    try:
        import captions as caplib
        st = caplib._norm_style(style)
        p = caplib._preset_of(st)
        if p:
            px = caplib._premium_font_px(p, st, (int(W), int(H)))
            return px * float(caplib._leading(st, p)) / H
        px = caplib.FONT_SIZES.get(st.get("size") or "m", 40) * H / caplib.BASE_PLAY_RES[1]
        return px * 1.2 / H
    except Exception:  # noqa: BLE001
        return 0.042


def min_band(edl, W, H, floor=0.0):
    """The shortest free band a caption page may be laid into."""
    return max(float(floor), MIN_BAND_LINES * line_height(edl, W, H))


def column(W, H, base=(0.15, 0.85)):
    """(x0, x1) of the caption column: ``base`` (where a centred caption
    block lies), never inside a 9:16 reel's side crop."""
    if float(H) / max(float(W), 1.0) >= 1.6:
        return (max(base[0], SIDE_CROP_PORTRAIT), min(base[1], 1.0 - SIDE_CROP_PORTRAIT))
    return tuple(base)


# ── layout: cards and panels ─────────────────────────────────────────────

def _cards(edl):
    fx = (edl or {}).get("effects")
    cards = fx.get("picture_cards") if isinstance(fx, dict) else None
    return [c for c in cards or [] if isinstance(c, dict)]


def live_cards(edl, a, b):
    """The picture cards on screen over program [a, b] (any overlap)."""
    out = []
    for c in _cards(edl):
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if s < b - 1e-6 and e > a + 1e-6:
            out.append(c)
    return out


def card_windows(edl):
    """[(start, end)] of every picture card (program seconds)."""
    out = []
    for c in _cards(edl):
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e > s:
            out.append((s, e))
    return out


def card_rects(cards):
    """The rounded windows the live cards draw (picture_cards.card_boxes —
    one per panel, else the card's box), as (x0, y0, x1, y1)."""
    import picture_cards
    out = []
    for c in cards:
        try:
            boxes = picture_cards.card_boxes(c)
        except (KeyError, TypeError, ValueError):
            continue
        for bx in boxes:
            try:
                x0, y0, x1, y1 = (float(v) for v in bx[:4])
            except (TypeError, ValueError):
                continue
            if x1 > x0 and y1 > y0:
                out.append((x0, y0, x1, y1))
    return out


def edge_zones(rects, col):
    """Hard zones along every card/panel edge a caption could cross: a thin
    band (EDGE_PAD) over each horizontal edge, and the whole window when a
    vertical edge runs through the caption column (a caption cannot sit
    across two side-by-side panels)."""
    out = []
    for x0, y0, x1, y1 in rects:
        if x1 <= col[0] or x0 >= col[1]:
            continue
        for y in (y0, y1):
            out.append((x0, y - EDGE_PAD, x1, y + EDGE_PAD))
        if col[0] + 0.02 < x0 < col[1] - 0.02 or col[0] + 0.02 < x1 < col[1] - 0.02:
            out.append((x0, y0 - EDGE_PAD, x1, y1 + EDGE_PAD))
    return out


# ── the watermark ────────────────────────────────────────────────────────

_WM = {}


def watermark_box(W, H):
    """The free-tier mark's reserved box (keepout.watermark_zone at the
    normal corner), or None when it cannot be worked out."""
    key = (int(W), int(H))
    if key not in _WM:
        try:
            import keepout
            _WM[key] = tuple(float(v) for v in keepout.watermark_zone(int(W), int(H)))
        except Exception:  # noqa: BLE001 — no box: nothing reserved
            _WM[key] = None
    return _WM[key]


# ── evidence from the index: source text and props ───────────────────────

def _reliable_text(sample):
    """Text boxes of one spatial sample that are really text: all of them
    on a dense UI frame, else only line- or title-shaped ones (MSER groups
    jacket seams and microphones into tall pseudo-text boxes)."""
    boxes = list(sample.get("text") or [])
    if sample.get("dense_ui"):
        return boxes
    out = []
    for box in boxes:
        try:
            w = float(box[2]) - float(box[0])
            h = float(box[3]) - float(box[1])
        except (TypeError, ValueError, IndexError):
            continue
        aspect = w / max(h, 1e-9)
        if w >= 0.10 and h >= 0.008 and ((h <= 0.13 and aspect >= 2.1)
                                         or (h <= 0.20 and aspect >= 3.0)):
            out.append(box)
    return out


def _near_sample(index, tl, a, b):
    """(source second at the window's middle, the index spatial sample
    nearest it within keepout.NEAR_S) or (None, None)."""
    import keepout
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    if not samples:
        return None, None
    try:
        src_mid = tl.out_to_src((a + b) / 2.0)
    except Exception:  # noqa: BLE001
        src_mid = None
    if src_mid is None:
        return None, None
    near = None
    for s in samples:
        try:
            t = float(s["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(t - src_mid) <= keepout.NEAR_S and (near is None or
                                                   abs(t - src_mid) < abs(near[0] - src_mid)):
            near = (t, s)
    return (float(src_mid), near[1]) if near else (float(src_mid), None)


def _mapped(edl, index, tl, W, H, a, b, field):
    """Boxes of the index's spatial sample nearest the source under program
    [a, b] (``field``: 'text' or 'props'), mapped onto the canvas at the
    window's middle the way faces are (keepout.Geometry: the crop, the
    zoom, any card or panel) and clipped to where they are visible."""
    import keepout
    video = (index or {}).get("video") or {}
    if not video.get("width") or not video.get("height") or (edl or {}).get("canvas"):
        return []
    src_mid, sample = _near_sample(index, tl, a, b)
    if sample is None:
        return []
    boxes = _reliable_text(sample) if field == "text" else list(sample.get(field) or [])
    if not boxes:
        return []
    mid = (a + b) / 2.0
    try:
        geo = keepout.Geometry(edl, video, W, H, float(tl.out_duration),
                               zooms=keepout.camera_zooms(edl, index, tl))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for bx in boxes:
        try:
            ms = geo.to_outputs(mid, src_mid, [float(v) for v in bx[:4]])
        except Exception:  # noqa: BLE001 — an unmappable box
            continue
        for m in ms:
            x0, y0, x1, y1 = (max(m[0], m[4]), max(m[1], m[5]), min(m[2], m[6]), min(m[3], m[7]))
            if x1 > x0 and y1 > y0:
                out.append((x0, y0, x1, y1))
    return out


def text_boxes(edl, index, tl, W, H, a, b):
    """Source text visible on the canvas over program [a, b] (soft)."""
    return _mapped(edl, index, tl, W, H, a, b, "text")


def prop_zones(edl, index, tl, W, H, a, b):
    """Props the index knows (spatial samples' ``props``: a microphone, a
    laptop) on the canvas over program [a, b], padded (hard)."""
    return [(x0 - PROP_PAD, y0 - PROP_PAD, x1 + PROP_PAD, y1 + PROP_PAD)
            for x0, y0, x1, y1 in _mapped(edl, index, tl, W, H, a, b, "props")]


# A face box this much inside a panel's source rect puts the speaker in it.
PERSON_SHARE = 0.5
# Face evidence for telling a stack's panels apart is looked for this far
# around the card's window (source seconds; profiles slip past Haar).
PANEL_FACE_S = 6.0


def content_zones(cards, index, tl, a, b):
    """The panels of the live stacked cards that show CONTENT — a screen,
    a page, a document: no face in their source rect while another panel
    of the same card has one — padded. A caption never sits on the evidence
    a stack shows (Elon's captions set over the article's text); it sits on
    the canvas around the panels, or in the speaker's panel clear of the
    face. A single card is the picture itself, and a stack with no face
    measured anywhere near it tells nothing apart: [] for both."""
    import picture_cards
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    if not samples:
        return []
    out = []
    for card in cards:
        try:
            s0 = tl.out_to_src(max(a, float(card["start"])))
            s1 = tl.out_to_src(min(b, float(card["end"])) - 1e-3)
        except Exception:  # noqa: BLE001
            continue
        if s0 is None or s1 is None:
            continue
        lo, hi = min(s0, s1) - PANEL_FACE_S, max(s0, s1) + PANEL_FACE_S
        panels = picture_cards.card_panels(card, (s0 + s1) / 2.0)
        if len(panels) < 2:
            continue
        faces = []
        for smp in samples:
            try:
                if lo <= float(smp["t"]) <= hi:
                    faces += [[float(v) for v in f[:4]] for f in smp.get("faces") or []]
            except (KeyError, TypeError, ValueError):
                continue
        if not faces:
            continue

        def person(rect):
            for f in faces:
                area = max(1e-9, (f[2] - f[0]) * (f[3] - f[1]))
                w = min(f[2], rect[2]) - max(f[0], rect[0])
                h = min(f[3], rect[3]) - max(f[1], rect[1])
                if w > 0 and h > 0 and w * h >= PERSON_SHARE * area:
                    return True
            return False
        kinds = [(box, person(rect)) for box, rect in panels]
        if not any(k for _b, k in kinds):
            continue
        for box, k in kinds:
            if not k:
                out.append((box[0], box[1] - EDGE_PAD, box[2], box[3] + EDGE_PAD))
    return out


def chin(zone):
    """A face zone grown down over the chin and jaw (CHIN_PAD)."""
    return (zone[0], zone[1], zone[2], zone[3] + CHIN_PAD * (zone[3] - zone[1]))


# ── bands ────────────────────────────────────────────────────────────────

def _in_col(box, col):
    return box[2] > col[0] and box[0] < col[1]


def hits(lo, hi, zones, col):
    """Does the block [lo, hi] (frame height) touch any zone reaching the
    caption column?"""
    return any(_in_col(z, col) and z[1] < hi and z[3] > lo for z in zones)


def free_bands(zones, safe, col):
    """Vertical intervals of the safe range no zone reaching the column
    touches."""
    ivs = [tuple(safe)]
    for z in zones:
        if not _in_col(z, col):
            continue
        lo, hi = z[1], z[3]
        nxt = []
        for a, b in ivs:
            if hi <= a or lo >= b:
                nxt.append((a, b))
                continue
            if lo > a:
                nxt.append((a, lo))
            if hi < b:
                nxt.append((hi, b))
        ivs = nxt
    return ivs


def _overlap_share(a, b, boxes, col):
    """Share of the band [a, b] x column that ``boxes`` cover (rough: the
    boxes' clipped areas summed, capped at 1)."""
    area = max(1e-9, (b - a) * (col[1] - col[0]))
    got = 0.0
    for x0, y0, x1, y1 in boxes:
        w = min(x1, col[1]) - max(x0, col[0])
        h = min(y1, b) - max(y0, a)
        if w > 0 and h > 0:
            got += w * h
    return min(1.0, got / area)


def _inside_panel(a, b, rects, col):
    return any(r[1] <= a + 1e-6 and r[3] >= b - 1e-6 and _in_col(r, col) for r in rects)


def choose(bands, normal_y, half, min_h, soft=(), panels=(), col=(0.15, 0.85), prev=None):
    """The band a page takes: (y, lo, hi, score) or None.

    ``bands`` are free intervals; one shorter than ``min_h`` is no band. A
    page centred on ``y`` with half-height ``half`` (shrunk to the band when
    it is shorter) sits as near ``normal_y`` as the band allows. ``prev`` =
    (y, lo, hi) of the band the previous page used: while it is still free
    the page stays there. Otherwise the largest band wins (SIZE_CAP), less
    MOVE_COST per unit moved, TEXT_COST x the share of source text under it
    and PANEL_COST for sitting on a panel's picture rather than the canvas."""
    cands = []
    for a, b in bands:
        if b - a < min_h - 1e-9:
            continue
        h = min(half, (b - a) / 2.0)
        y = min(max(normal_y, a + h), b - h)
        if prev is not None and a - 1e-6 <= prev[0] - h and prev[0] + h <= b + 1e-6:
            return (prev[0], a, b, float("inf"))
        score = min(b - a, SIZE_CAP) - MOVE_COST * abs(y - normal_y) \
            - TEXT_COST * _overlap_share(y - h, y + h, soft, col) \
            - (PANEL_COST if _inside_panel(y - h, y + h, panels, col) else 0.0)
        cands.append((score, -abs(y - normal_y), y, a, b))
    if not cands:
        return None
    score, _d, y, a, b = max(cands)
    return (y, a, b, score)
