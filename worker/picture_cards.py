"""Footage-only rounded cards, before captions/graphics and native branding.

One cached antialiased plate per design. Opening/closing uses cheap native
overlay/fade filters, not per-frame image generation or rasterized typography.
The card is a window of the original program, never a prepared child master.

Designed backdrops (PictureCard.background_style / grain / vignette) replace
the flat colour around the card:

* vertical_gradient / radial_gradient are STATIC: the gradient and vignette
  are baked (dithered, so 8-bit steps never band) into the same one-input
  plate the flat card uses. The plate's hole carries the clean fill, which is
  split off as the layer under the opening footage, so a fading card
  materializes out of its own backdrop instead of out of a flat slab.
* blur is DYNAMIC: a blurred, darkened copy of the card's own footage fills
  the canvas. It is computed at 1/8 resolution (blur + grade there cost next
  to nothing) and scaled up. Because the backdrop moves, the rounded window
  that clips the footage cannot be baked into an opaque plate; the backdrop
  is laid back over everything outside the window through a static mask
  instead, and a decor plate (shadow, vignette, hairline) goes on top.

Film grain is temporal luma noise with a fixed seed (deterministic renders)
applied to the backdrop only — the footage itself is never re-noised, except
a small face enlarged past SOURCE_UPSCALE_CAP (face_cap) on a grained card,
which wears a little of the same grain so its softness reads as film. A card
with none of these options renders through the historical graph, unchanged.

SOURCE-FED CARDS AND STACKED LAYOUTS

A legacy card is a window of the composed program: on a 9:16 crop of a 4:3
480p talk, the card cropped the already-cropped frame and enlarged it ~3.7x
(judges, Oct 2026). A card with ``source`` (or ``panels``) takes its footage
from the main SOURCE frame instead. The renderer composes every main-footage
block inside the card's window with layout_filter — each source rect scaled
ONCE straight onto its box of the canvas — so the camera, grade, stylize and
transitions downstream still act on the picture the card shows; the card
branch then crops its box(es) back out, exactly where they already are. The
blocks are split at card edges that fall inside a kept segment (the program
picture changes composition there). Every composed frame carries a layout
tag in its metadata, and the card is built from exactly the tagged frames
(_append_source_fed), so it can never disagree with the blocks about which
frames were composed for it. A spliced insert inside the window plays
full-frame: the card steps aside for it.

LAYOUT CHANGES NEVER SHOW THE BARE CANVAS (judges, Oct 2026)

A card's entrance used to fade its footage in over its own backdrop, which
the card drew at full strength from its first frame: the Elon stack opened
on a frame of empty dark canvas (mean luma 66.6 -> 27.4 in one frame,
mid-sentence) and closed the same way before a hard cut to a bright shot —
every judge read it as a dropped frame. Now an animated entrance or exit
(fade, lift, reveal) DISSOLVES the whole card — backdrop, plate and footage
as one picture — with the full-frame shot under it:

* a program card is drawn over the program itself, so fading the finished
  card in over it is the dissolve;
* a source-fed card's blocks are re-composed, so over its entrance (and
  exit) the renderer composes the block as the full-frame shot with the
  panels dissolving in inside their boxes (layout_filter ``under``), and the
  card fades in over that at the same rate: inside the boxes the picture is
  exactly the dissolving footage, outside it the backdrop dissolves over the
  shot. Its footage never slides (a lift is a dissolve here): a moving tile
  would double the picture under it.

A source-fed card that opens or closes ON A CUT (a camera cut, a jump cut,
an insert's edge, another card's layout) hard-cuts instead, with its panels
already populated: a dissolve right after a cut is mush, and a cut is
already the change. ``entrance``/``exit`` 'none' always hard-cut.
"""
import hashlib
import json
import math
import os

DESIGNED_STYLES = ("vertical_gradient", "radial_gradient", "blur")
BLUR_DIM_DEFAULT = .45
GRAIN_SEED = 4271

# Enlargement past this smears footage under razor-sharp type: the archival
# Jobs talk at 3.7x read as "very soft" to every judge.
SOURCE_UPSCALE_CAP = 2.0
# A source whose short side is below this is low-resolution / archival: its
# card shows the whole frame (contain) on a dark canvas, never a blur of
# itself.
LOWRES_SHORT_SIDE = 720
# Analog-era masters carry blanking and chroma bleed at the frame edges (the
# Jobs master: ~10 green columns on the left, a black one on the right, dark
# head-switching rows at the bottom). A contained card trims this much.
ARCHIVAL_OVERSCAN = (.02, .015)
# Speaker crops: at least this much of the card's height clear above the
# head, aiming for the target; the face fills about FACE_SHARE of the card
# height (a medium close-up). Detector boxes start at the brow, so the crown
# sits HAIR_ABOVE_FACE face-heights above the box.
HEADROOM_MIN = .08
HEADROOM_TARGET = .10
FACE_SHARE = .30
HAIR_ABOVE_FACE = .40
# A small face reads as a figure, not a person: the judged Jobs card (480p
# archival, enlarged at most 2x) showed a waist-up wide whose face was ~6%
# of the frame. A face framing whose face would sit under FACE_MIN_FRAME of
# the FRAME height takes a larger share of its card (up to FACE_SHARE_MAX:
# a medium close-up) and may be enlarged past SOURCE_UPSCALE_CAP for it —
# never past FACE_UPSCALE_CAP (lanczos, light sharpening and, on a grained
# card, the backdrop's grain over the footage keep it from reading soft).
FACE_MIN_FRAME = .15
FACE_SHARE_MAX = .40
FACE_UPSCALE_CAP = 3.0


def source_fed(spec):
    """True when the card's footage comes from the main source frame."""
    return bool(spec.get("source") or spec.get("panels"))


# Entrances/exits that animate (the card dissolves with the shot under it);
# 'none' is a cut.
ANIMATED = ("fade", "lift", "reveal")


def edge_s(spec):
    """The card's entrance/exit length (seconds, program clock)."""
    start, end = float(spec["start"]), float(spec["end"])
    full = float(spec.get("full_duration_s") or (end - start))
    return min(float(spec.get("duration_s", .45)), full * .3)


def animation_windows(spec):
    """(entrance, exit) PROGRAM windows [a, b] the card dissolves over —
    the whole animation on the card's own clock (``phase_s``: a stitched
    fragment opened inside the card has its entrance behind it), or None
    for a cut or an animation outside this piece of the card."""
    start, end = float(spec["start"]), float(spec["end"])
    phase = float(spec.get("phase_s") or 0.0)
    full = float(spec.get("full_duration_s") or (end - start))
    edge = edge_s(spec)
    origin = start - phase
    out = []
    for key, default, a in (("entrance", "lift", origin),
                            ("exit", "fade", origin + full - edge)):
        b = a + edge
        if spec.get(key, default) in ANIMATED and edge > 1e-3 \
                and b > start + 1e-3 and a < end - 1e-3:
            out.append((a, b))
        else:
            out.append(None)
    return tuple(out)


def _fade(kind, st, d):
    """An alpha fade ('in'/'out') from ``st`` over ``d`` seconds of the
    stream's clock; ``st`` may be negative (the stream opens inside the
    fade): fade cannot start before zero, so the clock is shifted forward
    that much and straight back."""
    shift = max(0.0, -float(st))
    chain = f",fade=t={kind}:st={st + shift:.6f}:d={d:.6f}:alpha=1"
    if shift:
        chain = f",setpts=PTS+{shift:.6f}/TB{chain},setpts=PTS-{shift:.6f}/TB"
    return chain


def source_at(spec, src_t=None):
    """The source rect a single-rect card shows at SOURCE second ``src_t``:
    the ``source_track`` span holding it (the card re-aims shot by shot),
    else ``source`` — and where a ``follow`` span holds ``src_t``, that rect
    centred on the follow path (the card follows the speaker inside the
    shot). None for a program card or a stack."""
    if not spec.get("source") or spec.get("panels"):
        return None
    rect = list(spec["source"])
    if src_t is not None:
        for span in spec.get("source_track") or []:
            try:
                if float(span["t0"]) - 1e-6 <= float(src_t) <= float(span["t1"]) + 1e-6:
                    rect = list(span["source"])
                    break
            except (KeyError, TypeError, ValueError):
                continue
        if spec.get("follow"):
            import follow
            span = follow.span_at(spec["follow"], src_t)
            c = follow.centre_at(span, src_t) if span else None
            if c is not None:
                rect = recentre(rect, c)
        k = step_scale_at(spec, src_t)
        if k is not None:
            rect = step_rect(rect, k)
    return rect


def step_scale_at(spec, src_t):
    """The cut step (PictureCard.cut_steps, written only by the optional
    conceal_jump_cuts) holding SOURCE second ``src_t``: its scale, or None."""
    if src_t is None:
        return None
    for st in spec.get("cut_steps") or []:
        try:
            if float(st["t0"]) - 1e-6 <= float(src_t) <= float(st["t1"]) + 1e-6:
                k = float(st["scale"])
                return k if abs(k - 1.0) > 1e-6 else None
        except (KeyError, TypeError, ValueError):
            continue
    return None


def step_rect(rect, k):
    """``rect`` scaled by 1/k around its centre (k > 1 tighter, < 1 wider),
    kept inside the source frame: a wide step near an edge slides inward
    rather than showing past it."""
    x0, y0, x1, y1 = (float(v) for v in rect)
    w = min(1.0, (x1 - x0) / k)
    h = min(1.0, (y1 - y0) / k)
    return recentre([0.0, 0.0, w, h], ((x0 + x1) / 2.0, (y0 + y1) / 2.0))


def recentre(rect, centre):
    """``rect`` moved (size kept) to centre on ``centre``, inside 0..1."""
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    x0 = _clamp(float(centre[0]) - w / 2.0, 0.0, max(0.0, 1.0 - w))
    y0 = _clamp(float(centre[1]) - h / 2.0, 0.0, max(0.0, 1.0 - h))
    return [x0, y0, x0 + w, y0 + h]


def card_panels(spec, src_t=None):
    """[(box, source_rect)] of a source-fed card, [] for a program card.
    ``src_t`` (a SOURCE second) picks a re-aimed card's framing there."""
    if spec.get("panels"):
        return [(list(p["box"]), panel_source_at(p, src_t))
                for p in spec["panels"]]
    if spec.get("source"):
        return [(list(spec["box"]), source_at(spec, src_t))]
    return []


def panel_source_at(panel, src_t=None):
    """A stack panel's source rect at SOURCE second ``src_t``: its rect,
    centred on its follow path where a follow span holds ``src_t``."""
    rect = list(panel["source"])
    if src_t is not None and panel.get("follow"):
        import follow
        span = follow.span_at(panel["follow"], src_t)
        c = follow.centre_at(span, src_t) if span else None
        if c is not None:
            rect = recentre(rect, c)
    return rect


def card_boxes(spec):
    """The rounded windows a card draws: one per panel, else its box."""
    if spec.get("panels"):
        return [list(p["box"]) for p in spec["panels"]]
    return [list(spec["box"])]


# ── The layout's geometry, for caption placement (judges, Oct 2026) ──────
# Captions anchored on the 0.03 seam between two stacked panels crossed both
# panels' edges. A caption band must be at least this tall (frame
# fractions); the panels' edges are lines a caption never sits on.
CAPTION_BAND_MIN = .06


def layout_rects(edl, t):
    """[[x0, y0, x1, y1], ...] — the rounded windows (each panel of a stack,
    else the card's box) of every picture card on screen at PROGRAM second
    ``t``, in frame fractions: what caption placement keeps its lines off
    (a window's edge is a no-go line). [] on a full-frame picture."""
    out = []
    for card in ((edl or {}).get("effects") or {}).get("picture_cards") or []:
        if not isinstance(card, dict):
            continue
        try:
            a, b = float(card["start"]), float(card["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if a - 1e-6 <= float(t) < b:
            out += [[float(v) for v in box] for box in card_boxes(card)]
    return out


def layout_edges(edl):
    """Sorted PROGRAM seconds where the picture's layout changes (a card's
    start or end): caption placement re-solves on these frames."""
    out = set()
    for card in ((edl or {}).get("effects") or {}).get("picture_cards") or []:
        if not isinstance(card, dict):
            continue
        for key in ("start", "end"):
            try:
                out.add(round(float(card[key]), 4))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(out)


def free_bands(boxes, top=0.0, bottom=1.0, min_h=CAPTION_BAND_MIN):
    """[(y0, y1)] full-width bands of the frame between ``top`` and
    ``bottom`` that no box in ``boxes`` covers, at least ``min_h`` tall —
    where a caption may sit (layout_rects gives the boxes)."""
    spans = sorted((max(top, float(b[1])), min(bottom, float(b[3])))
                   for b in boxes or [] if float(b[3]) > top and float(b[1]) < bottom)
    out, y = [], float(top)
    for y0, y1 in spans:
        if y0 - y >= min_h - 1e-9:
            out.append((round(y, 4), round(y0, 4)))
        y = max(y, y1)
    if bottom - y >= min_h - 1e-9:
        out.append((round(y, 4), round(float(bottom), 4)))
    return out


def is_lowres(src_w, src_h):
    try:
        return 0 < min(float(src_w), float(src_h)) < LOWRES_SHORT_SIDE
    except (TypeError, ValueError):
        return False


def archival_rect():
    ox, oy = ARCHIVAL_OVERSCAN
    return [ox, oy, 1.0 - ox, 1.0 - oy]


def match_rect(rect, box, src_w, src_h, W, H):
    """``rect`` trimmed (centred) to the box's pixel aspect, so one uniform
    scale maps it onto the box. Tools resolve this at write time; the render
    repeats it only to absorb rounding."""
    x0, y0, x1, y1 = (float(v) for v in rect)
    a = ((box[2] - box[0]) * W) / max(1e-9, (box[3] - box[1]) * H)
    rw, rh = (x1 - x0) * src_w, (y1 - y0) * src_h
    if rw / rh > a * 1.002:
        d = (rw - rh * a) / src_w / 2.0
        x0, x1 = x0 + d, x1 - d
    elif rw / rh < a / 1.002:
        d = (rh - rw / a) / src_h / 2.0
        y0, y1 = y0 + d, y1 - d
    return [x0, y0, x1, y1]


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _centred_box(box, w, h):
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    return [cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0]


def fit_panel(src_w, src_h, W, H, box, rect, fit="crop",
              cap=SOURCE_UPSCALE_CAP):
    """Resolve one source rect against its box on a W x H canvas.

    fit 'pad' keeps the whole rect and shrinks the box (centred) to its
    aspect — the window hugs the footage. fit 'crop' keeps the box and trims
    the rect to the box's aspect: centred across, from the BOTTOM down (the
    top is where a speaker's headroom is). Either way the footage is never
    enlarged past ``cap``: a pad box shrinks, a crop rect grows around its
    centre while the source has room, and the box shrinks when it has not.
    Returns (box, rect, upscale) — fractions, and canvas px per source px."""
    sw, sh = float(src_w), float(src_h)
    x0, y0, x1, y1 = (float(v) for v in rect)
    bw, bh = (box[2] - box[0]) * W, (box[3] - box[1]) * H
    rw, rh = (x1 - x0) * sw, (y1 - y0) * sh
    if fit == "pad":
        k = min(bw / rw, bh / rh, cap)
        box = _centred_box(box, rw * k / W, rh * k / H)
        return [round(v, 4) for v in box], [x0, y0, x1, y1], k
    a = bw / bh
    if rw / rh > a:
        cut = (rw - rh * a) / sw / 2.0
        x0, x1 = x0 + cut, x1 - cut
    else:
        y1 = y0 + (rw / a) / sh
    rw, rh = (x1 - x0) * sw, (y1 - y0) * sh
    k = bw / rw
    if k > cap:
        # Grow the rect around its centre (inside the source) ...
        nw = min(bw / cap, sw, sh * a)
        nh = nw / a
        cx, cy = (x0 + x1) / 2.0 * sw, (y0 + y1) / 2.0 * sh
        nx0 = _clamp(cx - nw / 2.0, 0.0, sw - nw)
        ny0 = _clamp(cy - nh / 2.0, 0.0, sh - nh)
        x0, y0, x1, y1 = nx0 / sw, ny0 / sh, (nx0 + nw) / sw, (ny0 + nh) / sh
        k = bw / nw
        if k > cap + 1e-6:
            # ... and when the whole source is too small, shrink the box.
            box = _centred_box(box, nw * cap / W, nh * cap / H)
            k = cap
    return [round(v, 4) for v in box], [x0, y0, x1, y1], k


def median_face(faces):
    """The median [x0, y0, x1, y1] of face boxes (fractions), or None."""
    faces = [f for f in faces or [] if f and len(f) == 4
             and f[2] > f[0] and f[3] > f[1]]
    if not faces:
        return None
    out = []
    for i in range(4):
        vals = sorted(float(f[i]) for f in faces)
        out.append(vals[len(vals) // 2])
    return out


def face_share(box_h, H):
    """The share of a card's height (``box_h`` canvas px of an H-tall
    frame) its speaker's face takes: FACE_SHARE, more in a short card so
    the face reaches FACE_MIN_FRAME of the frame (at most FACE_SHARE_MAX)."""
    need = FACE_MIN_FRAME * float(H) / max(1.0, float(box_h))
    return min(FACE_SHARE_MAX, max(FACE_SHARE, need))


def face_cap(face_h, box_h, H, cap=SOURCE_UPSCALE_CAP):
    """The enlargement a face framing may use: ``cap``, raised as far as a
    small face (``face_h`` source px) needs to fill face_share of a
    ``box_h``-px card — never past FACE_UPSCALE_CAP."""
    try:
        want = face_share(box_h, H) * float(box_h) / float(face_h)
    except (TypeError, ValueError, ZeroDivisionError):
        return cap
    return max(cap, min(FACE_UPSCALE_CAP, want))


def speaker_rect(src_w, src_h, W, H, box, face=None, focus=None,
                 cap=SOURCE_UPSCALE_CAP):
    """A source rect at the box's aspect framing a speaker: the face about
    face_share of the card's height, HEADROOM_TARGET of it clear above the
    crown (the source's top edge permitting), never enlarged past ``cap``
    (past it only as far as face_cap lets a small face be read).
    With no face it is the largest such rect centred on ``focus``.
    Returns (rect, headroom) — headroom as a share of the rect's height, or
    None when no face was measured."""
    sw, sh = float(src_w), float(src_h)
    bw, bh = (box[2] - box[0]) * W, (box[3] - box[1]) * H
    a = bw / bh
    big = min(sw, sh * a)
    if face is None:
        rw = big
        rh = rw / a
        fx, fy = (focus or (.5, .5))
        fx = .5 if fx is None else float(fx)
        fy = .5 if fy is None else float(fy)
        x0 = _clamp(fx * sw - rw / 2.0, 0.0, sw - rw)
        y0 = _clamp(fy * sh - rh / 2.0, 0.0, sh - rh)
        return [x0 / sw, y0 / sh, (x0 + rw) / sw, (y0 + rh) / sh], None
    fh = (face[3] - face[1]) * sh
    share = face_share(bh, H)
    kcap = face_cap(fh, bh, H, cap)
    rh = max(fh / share, bh / kcap)
    rw = max(rh * a, bw / kcap)
    rw = min(rw, big)
    rh = rw / a
    cx = (face[0] + face[2]) / 2.0 * sw
    crown = (face[1] - HAIR_ABOVE_FACE * (face[3] - face[1])) * sh
    x0 = _clamp(cx - rw / 2.0, 0.0, sw - rw)
    y0 = _clamp(crown - HEADROOM_TARGET * rh, 0.0, sh - rh)
    rect = [x0 / sw, y0 / sh, (x0 + rw) / sw, (y0 + rh) / sh]
    return rect, (crown - y0) / rh


# The head around a detector box (which runs brow to chin): the hair above
# it (HAIR_ABOVE_FACE), the ears and hair either side, the chin and collar
# below. A card framing keeps every measured head of its shot inside.
HEAD_SIDE = .15
HEAD_BELOW = .35
# A detection less than half or more than twice the median face of its
# stretch is another face (a poster, a passer-by), not the speaker moving.
FACE_OUTLIER = 2.0
# Shots whose speaker sits alike share one framing: joining them may cost
# at most this much of either one's own framing (a wider rect covering both
# positions), and must leave each shot's speaker within SHARE_CENTRE of the
# card's width from its centre (a speaker pushed to the card's edge is a
# composition to re-aim, even when a wide rect still holds the head). Past
# either the card re-aims on the cut between them.
SHARE_GROW = 1.15
SHARE_CENTRE = .18


def head_box(face):
    """The head (hair, ears, chin) around one detector face box."""
    w, h = face[2] - face[0], face[3] - face[1]
    return [face[0] - HEAD_SIDE * w, face[1] - HAIR_ABOVE_FACE * h,
            face[2] + HEAD_SIDE * w, face[3] + HEAD_BELOW * h]


def steady_faces(faces):
    """``faces`` without detections far off the median size."""
    med = median_face(faces)
    if med is None:
        return []
    mh = max(med[3] - med[1], 1e-6)
    return [f for f in faces if f and len(f) == 4 and f[2] > f[0] and f[3] > f[1]
            and 1.0 / FACE_OUTLIER <= (f[3] - f[1]) / mh <= FACE_OUTLIER]


def framing_rect(src_w, src_h, W, H, box, faces, focus=None,
                 cap=SOURCE_UPSCALE_CAP):
    """A source rect at the box's aspect that keeps EVERY measured position
    of the speaker's head over a stretch inside the card (judges, Oct 2026:
    a card framed on the median face let the head drift out of it when the
    speaker leaned or stepped). Sized like speaker_rect on the median face,
    then grown (aspect kept, inside the source) until the union of the head
    boxes fits under HEADROOM_TARGET of headroom, centred on that union.
    Returns (rect, headroom) like speaker_rect."""
    faces = steady_faces(faces)
    face = median_face(faces)
    rect, headroom = speaker_rect(src_w, src_h, W, H, box, face, focus, cap)
    if face is None:
        return rect, headroom
    sw, sh = float(src_w), float(src_h)
    bw, bh = (box[2] - box[0]) * W, (box[3] - box[1]) * H
    a = bw / bh
    big = min(sw, sh * a)
    heads = [head_box(f) for f in faces]
    need = [max(0.0, min(h[0] for h in heads)), max(0.0, min(h[1] for h in heads)),
            min(1.0, max(h[2] for h in heads)), min(1.0, max(h[3] for h in heads))]
    rw, rh = (rect[2] - rect[0]) * sw, (rect[3] - rect[1]) * sh
    nw, nh = (need[2] - need[0]) * sw, (need[3] - need[1]) * sh
    rh2 = max(rh, nh / (1.0 - HEADROOM_TARGET), nw * 1.04 / a)
    rw2 = min(rh2 * a, big)
    rh2 = rw2 / a
    if rw2 <= rw + 1e-6 and rect[0] * sw <= need[0] * sw + 1e-6 \
            and rect[2] * sw >= need[2] * sw - 1e-6 \
            and rect[1] * sh <= need[1] * sh + 1e-6 \
            and rect[3] * sh >= need[3] * sh - 1e-6:
        return rect, headroom          # the median framing already holds them
    cx = (need[0] + need[2]) / 2.0 * sw
    x0 = _clamp(cx - rw2 / 2.0, 0.0, sw - rw2)
    y0 = _clamp(need[1] * sh - HEADROOM_TARGET * rh2, 0.0, sh - rh2)
    out = [x0 / sw, y0 / sh, (x0 + rw2) / sw, (y0 + rh2) / sh]
    crown = need[1] * sh
    return out, (crown - y0) / rh2


def shot_framings(src_w, src_h, W, H, box, shots, focus_of=None,
                  cap=SOURCE_UPSCALE_CAP):
    """One framing per run of alike shots: [(rect, [shot, ...])] in order.

    ``shots`` = [(shot, faces)] (a shot is any hashable span; faces its
    measured face boxes, source fractions). Consecutive shots share a rect
    while the framing of both together is at most SHARE_GROW of either's
    own (the speaker sits alike): a steady speaker gets ONE framing for the
    whole card, one who moves between takes gets the card re-aimed on the
    cut where the move happened, never mid-shot. A shot with no face joins
    the run before it (or after, at the start)."""
    focus_of = focus_of or (lambda _shot: None)
    runs = []                           # [rect, [shots], faces, own height]

    def centred(rect, faces):
        face = median_face(steady_faces(faces))
        if face is None:
            return True
        w = max(rect[2] - rect[0], 1e-6)
        return abs((face[0] + face[2]) / 2.0 - (rect[0] + rect[2]) / 2.0) <= SHARE_CENTRE * w

    for shot, faces in shots:
        faces = list(faces or [])
        if not faces:
            if runs:
                runs[-1][1].append(shot)
            else:
                rect, _h = framing_rect(src_w, src_h, W, H, box, [], focus_of(shot), cap)
                runs.append([rect, [shot], [], None])
            continue
        own, _h = framing_rect(src_w, src_h, W, H, box, faces, focus_of(shot), cap)
        if runs:
            prev = runs[-1]
            if not prev[2]:
                prev[0], prev[2], prev[3] = own, faces, own[3] - own[1]
                prev[1].append(shot)
                continue
            both, _h = framing_rect(src_w, src_h, W, H, box, prev[2] + faces,
                                    focus_of(shot), cap)
            if both[3] - both[1] <= SHARE_GROW * max(own[3] - own[1], prev[3]) + 1e-9 \
                    and centred(both, faces) and centred(both, prev[2]):
                prev[0], prev[2] = both, prev[2] + faces
                prev[3] = max(prev[3], own[3] - own[1])
                prev[1].append(shot)
                continue
        runs.append([own, [shot], faces, own[3] - own[1]])
    return [(rect, shots_) for rect, shots_, _f, _h in runs]


# ── Face-safe panel framing (judges, Oct 2026) ───────────────────────────
# Owner rule: a card or panel never lets part of the speaker's face leave
# it. The judged Elon stack chose its speaker rect to end above the source's
# burned-in browser box: the panel's bottom edge ran through his mouth and
# chin with the top 45% empty curtain, and Rogan's nose pressed the panel's
# right edge with a sliver of the browser in the corner. A panel's speaker
# rect is SOLVED from the measured face track instead:
#
# * what it must hold (keep): every measured detector box of the window
#   (Haar boxes run brow or hairline to the chin, or under it) with PANEL_HAIR of its
#   height above it, PANEL_CHIN below and PANEL_SIDE either side, at least
#   PANEL_MARGIN of the rect inside every edge;
# * its size: the keep region about PANEL_FILL of the rect's height (a
#   close framing — no empty curtain), never enlarged past face_cap;
# * its place: the crown HEADROOM_TARGET below the top, and LEAD_ROOM of
#   the rect's width more room on the side the speaker looks to;
# * burned-in screen/PIP boxes (insets.py) stay out: of the rects that hold
#   the face, the one showing the least of them wins — none when one can.
#   Where the box touches the face itself (the Rogan layout: the browser's
#   corner sits at the speaker's chin) no rect can leave it out; the face
#   wins and the result says how much shows.
PANEL_HAIR = .22
PANEL_CHIN = .05
PANEL_SIDE = .06
PANEL_MARGIN = .03
PANEL_FILL = .86
PANEL_FILL_MAX = .94
LEAD_ROOM = .10
# A face track that saw the speaker over less than PANEL_SEEN_MIN of the
# window (a profile the detectors lose) frames with PANEL_UNSEEN_PAD of the
# face's size more room all round: the unseen stretch may sit a little lower
# or nearer the edge (the judged Rogan window was measured over 35% of it).
PANEL_SEEN_MIN = .6
PANEL_UNSEEN_PAD = .12


def panel_keep(faces, pad=0.0):
    """The region (source fractions) a panel must hold for ``faces``: the
    union of each box grown by PANEL_HAIR / PANEL_CHIN / PANEL_SIDE (and
    ``pad`` of its size more all round: room for where it was not seen)."""
    faces = steady_faces(faces)
    if not faces:
        return None
    keeps = []
    for f in faces:
        w, h = f[2] - f[0], f[3] - f[1]
        keeps.append([f[0] - (PANEL_SIDE + pad) * w, f[1] - (PANEL_HAIR + pad) * h,
                      f[2] + (PANEL_SIDE + pad) * w, f[3] + (PANEL_CHIN + pad) * h])
    return [max(0.0, min(k[0] for k in keeps)), max(0.0, min(k[1] for k in keeps)),
            min(1.0, max(k[2] for k in keeps)), min(1.0, max(k[3] for k in keeps))]


def _overlap(a, b):
    """Area of rects a and b's intersection (same units)."""
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * \
        max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def holds(rect, keep, margin=0.0):
    """True when ``keep`` lies inside ``rect`` with ``margin`` of the
    rect's size clear on every side (fractions; a hair of slack)."""
    mx, my = margin * (rect[2] - rect[0]), margin * (rect[3] - rect[1])
    return (keep[0] >= rect[0] + mx - 1e-4 and keep[2] <= rect[2] - mx + 1e-4
            and keep[1] >= rect[1] + my - 1e-4 and keep[3] <= rect[3] - my + 1e-4)


def panel_framing(src_w, src_h, W, H, box, faces, looks=(), avoid=(),
                  cap=SOURCE_UPSCALE_CAP, prefer=None, pad=0.0):
    """(rect, info) — a source rect at the box's aspect that holds the
    speaker's face (panel_keep of ``faces``, source fractions) for a panel
    or card, or (None, info) when no rect of this box's aspect can (the
    head is taller or wider than the source allows at it). ``looks``: the
    samples' gaze (-1 screen-left .. +1 right); ``avoid``: burned-in boxes
    (source fractions) to keep out; ``prefer``: a rect the editor gave —
    the answer is the nearest framing to it that holds the face (moved and
    grown only as far as it must).

    info: keep (the held region), lead (-1/0/1), share (the face's share of
    the rect's height), k (canvas px per source px), inset (the share of
    the rect an avoided box still covers), moved (prefer was changed).
    ``pad``: extra room round the head (a share of the face) where the
    track saw only part of the window."""
    sw, sh = float(src_w), float(src_h)
    bw, bh = (box[2] - box[0]) * W, (box[3] - box[1]) * H
    a = bw / bh
    big = min(sw, sh * a)                     # the widest rect of this aspect
    keep = panel_keep(faces, pad)
    info = {"keep": keep, "lead": 0, "share": None, "k": None, "inset": 0.0,
            "moved": False}
    if keep is None:
        return None, info
    med = median_face(steady_faces(faces))
    fh = (med[3] - med[1]) * sh
    vals = [float(v) for v in looks or () if v is not None]
    mean = sum(vals) / len(vals) if vals else 0.0
    lead = -1 if mean <= -.5 else 1 if mean >= .5 else 0
    info["lead"] = lead
    kx0, ky0, kx1, ky1 = keep[0] * sw, keep[1] * sh, keep[2] * sw, keep[3] * sh
    kw, kh = kx1 - kx0, ky1 - ky0
    m = PANEL_MARGIN
    # the smallest rect that holds the keep region with its margins (the
    # source's size permitting; the keep itself it must always hold)
    rh_min = max(min(sh, max(kh / (1.0 - 2 * m), kh / PANEL_FILL_MAX)),
                 min(big, kw / (1.0 - 2 * m)) / a, kh, kw / a)
    kcap = face_cap(fh, bh, H, cap)
    rh_pref = max(kh / PANEL_FILL, bh / kcap, rh_min)
    if prefer is not None:
        ph = (prefer[3] - prefer[1]) * sh
        rh_pref = max(rh_min, ph)
    if rh_min * a > big + 1e-6:
        return None, info                    # the head does not fit this aspect
    avoid = [[float(v) for v in r] for r in avoid or () if r and len(r) == 4]
    best = None
    sizes = sorted({min(big / a, rh_pref)} |
                   {min(big / a, rh_min + (rh_pref - rh_min) * f)
                    for f in (0.0, .25, .5, .75)}, reverse=True)
    # The margin may give way (to the keep region's own edge) where that is
    # what keeps a burned-in box out, or where the source has no room for
    # it; a framing with it always wins otherwise.
    margins = (m, 0.0)
    # a side where the head reaches the source's own edge has no margin to
    # keep (the source edge permitting, as speaker_rect's headroom)
    edge = (keep[0] <= 1e-3, keep[1] <= 1e-3, keep[2] >= 1 - 1e-3,
            keep[3] >= 1 - 1e-3)
    for rh, mm in [(rh, mm) for rh in sizes for mm in margins]:
        rw = rh * a
        # positions that hold the keep region with its margins
        xa = max(0.0, kx1 + (0.0 if edge[2] else mm) * rw - rw)
        xb = min(sw - rw, kx0 - (0.0 if edge[0] else mm) * rw)
        ya = max(0.0, ky1 + (0.0 if edge[3] else mm) * rh - rh)
        yb = min(sh - rh, ky0 - (0.0 if edge[1] else mm) * rh)
        if xa > xb + 1e-6 or ya > yb + 1e-6:
            continue
        if prefer is not None:
            px = prefer[0] * sw + ((prefer[2] - prefer[0]) * sw - rw) / 2.0
            py = prefer[1] * sh + ((prefer[3] - prefer[1]) * sh - rh) / 2.0
        else:
            px = (kx0 + kx1) / 2.0 + lead * LEAD_ROOM * rw - rw / 2.0
            py = ky0 - HEADROOM_TARGET * rh
        px, py = _clamp(px, xa, xb), _clamp(py, ya, yb)
        xs = sorted({xa, xb, px} | {xa + (xb - xa) * i / 12.0 for i in range(13)})
        ys = sorted({ya, yb, py} | {ya + (yb - ya) * i / 12.0 for i in range(13)})
        for x0 in xs:
            for y0 in ys:
                r = [round(x0 / sw, 4), round(y0 / sh, 4),
                     round((x0 + rw) / sw, 4), round((y0 + rh) / sh, 4)]
                cover = sum(_overlap(r, b) for b in avoid) / max(
                    1e-9, (r[2] - r[0]) * (r[3] - r[1]))
                # the inset first (a sliver counts), then the composition:
                # distance from the preferred place, then size from the
                # preferred size
                score = (0.0 if cover < 1e-5 else 1.0 + round(cover, 2),
                         0 if mm == m else 1,
                         abs(x0 - px) / max(1.0, rw) + abs(y0 - py) / max(1.0, rh)
                         + abs(rh - rh_pref) / max(1.0, rh_pref))
                if best is None or score < best[0]:
                    best = (score, r, rh)
    if best is None:
        return None, info
    rect = [round(v, 4) for v in best[1]]
    rect = [max(0.0, rect[0]), max(0.0, rect[1]), min(1.0, rect[2]), min(1.0, rect[3])]
    info.update(share=fh / best[2], k=bw / (best[2] * a), inset=best[0][0],
                moved=prefer is not None and any(
                    abs(u - v) > .004 for u, v in zip(rect, prefer)))
    return rect, info


# The designed canvas a card gets when none is chosen: the footage's own
# hue, desaturated and taken down to a dark tone, glowing out to near-black
# — a deliberate canvas, not a blurred smear of the picture (judges, Oct
# 2026, on the blur-fill under a 480p card) and not a flat void either.
CANVAS_VALUE = .14
CANVAS_EDGE_VALUE = .04
CANVAS_MAX_SATURATION = .45
CANVAS_FALLBACK = ("#1A1A1D", "#08080A")


def canvas_colours(mean_rgb):
    """(centre, edge) #RRGGBB of a dark canvas sampled from the footage's
    mean colour (0-255 RGB), or the neutral pair when there is none."""
    import colorsys
    try:
        r, g, b = (min(max(float(c), 0.0), 255.0) / 255.0 for c in mean_rgb)
    except (TypeError, ValueError):
        return CANVAS_FALLBACK
    h, s, _v = colorsys.rgb_to_hsv(r, g, b)
    s = min(s, CANVAS_MAX_SATURATION)

    def hexed(value):
        return "#" + "".join(f"{round(c * 255):02X}"
                             for c in colorsys.hsv_to_rgb(h, s, value))
    return hexed(CANVAS_VALUE), hexed(CANVAS_EDGE_VALUE)


def _even(v):
    return int(round(v / 2.0)) * 2


def _crop_expr(f):
    """crop= args for fractions of the incoming frame (even, like the insert
    crop) — fractions survive a proxy, a rotation or a non-square SAR."""
    x0, y0, x1, y1 = f
    return (f"crop=trunc(iw*{x1 - x0:.6f}/2)*2:trunc(ih*{y1 - y0:.6f}/2)*2"
            f":trunc(iw*{x0:.6f}/2)*2:trunc(ih*{y0:.6f}/2)*2")


def _enlarge(k):
    """The resampler + pre-sharpen frame_fit_filter uses for an enlargement."""
    import renderer
    flags = (f":flags={renderer.UPSCALE_SCALER}"
             if k > 1.0 + 1e-6 and renderer.UPSCALE_SCALER != "bicubic" else "")
    sharpen = (f",unsharp=3:3:{renderer.UPSCALE_SHARPEN:.2f}:3:3:0"
               if renderer.UPSCALE_SHARPEN > 0
               and k >= renderer.UPSCALE_SHARPEN_MIN_FACTOR else "")
    return flags, sharpen


def _footage_grain(k, grain):
    """Film grain over enlarged FOOTAGE (',' + noise, or ''): only a face
    framing enlarged past SOURCE_UPSCALE_CAP (face_cap) on a card that
    grains its backdrop — the same texture over picture and canvas, so the
    softness of a 3x archival enlargement reads as film, not as blur. A
    little lighter than the backdrop's (_grain)."""
    try:
        g = float(grain or 0.0)
    except (TypeError, ValueError):
        g = 0.0
    if g <= 0 or k <= SOURCE_UPSCALE_CAP + 1e-3:
        return ""
    return f",noise=c0s={max(1, round(16 * g))}:c0f=t:c0_seed={GRAIN_SEED}"


def _single_panel(W, H, box, rect, src_size, grain=None):
    """One rect onto its box, with the rest of the canvas showing the source
    around it at the same scale (black past the source's edges): a punch-in
    the camera aims inside the card then reveals real neighbouring picture,
    never a black edge."""
    x, y, w, h = pixels(W, H, box)
    if not src_size or not src_size[0] or not src_size[1]:
        return (f"{_crop_expr(rect)},scale={w}:{h},setsar=1,"
                f"pad={W}:{H}:{x}:{y}:color=black")
    sw, sh = float(src_size[0]), float(src_size[1])
    rect = match_rect(rect, box, sw, sh, W, H)
    kx = w / ((rect[2] - rect[0]) * sw)
    ky = h / ((rect[3] - rect[1]) * sh)
    # The source fractions the whole canvas covers at this scale.
    cover = [rect[0] - x / (kx * sw), rect[1] - y / (ky * sh),
             rect[0] + (W - x) / (kx * sw), rect[1] + (H - y) / (ky * sh)]
    vis = [max(0.0, cover[0]), max(0.0, cover[1]),
           min(1.0, cover[2]), min(1.0, cover[3])]
    left = max(0, _even(x - (rect[0] - vis[0]) * sw * kx))
    top = max(0, _even(y - (rect[1] - vis[1]) * sh * ky))
    right = min(W, _even(x + w + (vis[2] - rect[2]) * sw * kx))
    bottom = min(H, _even(y + h + (vis[3] - rect[3]) * sh * ky))
    flags, sharpen = _enlarge(max(kx, ky))
    return (f"{_crop_expr(vis)}{sharpen},scale={right - left}:{bottom - top}"
            f"{flags}{_footage_grain(max(kx, ky), grain)},setsar=1,"
            f"pad={W}:{H}:{left}:{top}:color=black")


def _panel(W, H, box, rect, src_size, grain=None):
    """One rect scaled exactly onto its box (w x h), unplaced."""
    _x, _y, w, h = pixels(W, H, box)
    k = 1.0
    if src_size and src_size[0] and src_size[1]:
        rect = match_rect(rect, box, float(src_size[0]), float(src_size[1]),
                          W, H)
        k = w / ((rect[2] - rect[0]) * float(src_size[0]))
    flags, sharpen = _enlarge(k)
    return (f"{_crop_expr(rect)}{sharpen},scale={w}:{h}{flags}"
            f"{_footage_grain(k, grain)},setsar=1")


def layout_filter(parts, in_label, out_label, W, H, fps, panels, uid,
                  src_size=None, seg_dur=None, grade=None, tag=None,
                  frames=None, follow_block=None, bounded=False, under=None,
                  dissolve=None, panel_follow=None, grain=None,
                  panel_conceal=None):
    """A main-footage block composed for a source-fed card: every panel's
    source rect scaled once onto its box of a W x H canvas, then the block
    tail _normalize_video uses (CFR, exact length, sar 1, yuv420p). The grade
    runs first, on the source pixels, as the block grade does elsewhere.
    tag marks every frame of the block (LAYOUT_TAG_KEY) for the card branch
    that cuts it back out. ``frames`` is the block's exact frame count on
    the programme's block clock (renderer.block_clock), bounded exactly as
    every other block is (renderer.block_tail).

    follow_block (a single card whose rect FOLLOWS the speaker, worker/
    follow.py): (time_map, key_span, key times on the block clock, rect
    centres x, y, interpolation) — the canvas is the same source-at-scale
    picture _single_panel draws, translated every frame so the moving rect
    lands on the box.

    bounded: the block's rect is a cut step (step_rect) — its follow path was
    planned for the unstepped rect, so the moving rect is held inside the
    source frame (a wider step near an edge slides inward).

    under + dissolve (the card's entrance or exit, module docstring): the
    label of the SAME frames composed as the full-frame program would show
    them, and [(kind 'in'/'out', start, seconds)] on the block's clock — the
    block is that full-frame picture with each panel's box dissolving in (or
    out) over it, tagged like any composed block, so the card cut from it
    shows exactly the dissolving footage inside its boxes.

    panel_follow (a stack): one follow_block (or None) per panel — a panel
    whose rect FOLLOWS its speaker inside the shot is drawn by follow.
    window_chain at its box's size.

    grain: the card's grain — laid over footage enlarged past
    SOURCE_UPSCALE_CAP (_footage_grain).

    panel_conceal (a stack): one list of burned-in boxes (or None) per
    panel, softened where the panel shows them (_conceal_chain; a still
    panel only)."""
    import renderer
    if under and dissolve:
        mid = f"lyd{uid}"
        layout_filter(parts, in_label, mid, W, H, fps, panels, uid,
                      src_size=src_size, seg_dur=seg_dur, grade=grade,
                      frames=frames, follow_block=follow_block,
                      bounded=bounded, panel_follow=panel_follow, grain=grain,
                      panel_conceal=panel_conceal)
        wins = [pixels(W, H, box) for box, _rect in panels]
        labels = [mid] if len(wins) == 1 else \
            [f"{mid}b{k}" for k in range(len(wins))]
        if len(wins) > 1:
            parts.append(f"[{mid}]split={len(wins)}"
                         + "".join(f"[{lb}]" for lb in labels))
        fades = "".join(_fade(kind, st, d) for kind, st, d in dissolve)
        base = under
        for k, (x, y, w, h) in enumerate(wins):
            parts.append(f"[{labels[k]}]crop={w}:{h}:{x}:{y},format=yuva420p"
                         f"{fades}[{mid}t{k}]")
            parts.append(f"[{base}][{mid}t{k}]overlay={x}:{y}:format=auto"
                         f"[{mid}u{k}]")
            base = f"{mid}u{k}"
        mark = (f",metadata=mode=add:key={LAYOUT_TAG_KEY}:value={tag}"
                if tag else "")
        parts.append(f"[{base}]format=yuv420p{mark}[{out_label}]")
        return
    tail = renderer.block_tail(fps, seg_dur, frames)
    if tag:
        tail += f",metadata=mode=add:key={LAYOUT_TAG_KEY}:value={tag}"
    head = f"format=yuv420p,{grade}," if grade else ""
    if len(panels) == 1 and follow_block and src_size and src_size[0] \
            and src_size[1]:
        import follow
        tmap, kspan, ts, cxs, cys, interp = follow_block
        box, rect = panels[0]
        sw, sh = float(src_size[0]), float(src_size[1])
        x, y, w, _h = pixels(W, H, box)
        rect = match_rect(rect, box, sw, sh, W, H)
        rw, rh = (rect[2] - rect[0]) * sw, (rect[3] - rect[1]) * sh
        k = w / rw
        if bounded:
            hx, hy = rw / 2.0 / sw, rh / 2.0 / sh
            cxs = [min(max(c, hx), 1.0 - hx) if hx < 0.5 else 0.5 for c in cxs]
            cys = [min(max(c, hy), 1.0 - hy) if hy < 0.5 else 0.5 for c in cys]
        # the canvas's top-left on the source, for each rect centre
        ox = [cx * sw - rw / 2.0 - x / k for cx in cxs]
        oy = [cy * sh - rh / 2.0 - y / k for cy in cys]
        flags, sharpen = _enlarge(k)
        follow.window_chain(
            parts, in_label, out_label, f"c{uid}", src_size=(sw, sh),
            out_size=(W, H), k=k, ts=ts, ox=ox, oy=oy, length=seg_dur or 0.0,
            fps=fps, tail=tail + _footage_grain(k, grain), grade=grade,
            interpolation=interp, sharpen=sharpen, flags=flags,
            time_map=tmap, key_span=kspan)
        return
    if len(panels) == 1:
        box, rect = panels[0]
        parts.append(f"[{in_label}]{head}{_single_panel(W, H, box, rect, src_size, grain)},"
                     f"{tail}[{out_label}]")
        return
    n = len(panels)
    parts.append(f"[{in_label}]{head}split={n}"
                 + "".join(f"[ly{uid}_{k}]" for k in range(n)))
    for k, (box, rect) in enumerate(panels):
        x, y, _w, _h = pixels(W, H, box)
        pf = panel_follow[k] if panel_follow and k < len(panel_follow) else None
        if pf and src_size and src_size[0] and src_size[1]:
            _follow_panel(parts, f"ly{uid}_{k}", f"lyf{uid}_{k}", W, H, fps,
                          box, rect, src_size, pf, seg_dur, frames,
                          f"{uid}p{k}")
            chain, src_label = "null", f"lyf{uid}_{k}"
        else:
            chain, src_label = _panel(W, H, box, rect, src_size, grain), f"ly{uid}_{k}"
            hide = panel_conceal[k] if panel_conceal and k < len(panel_conceal) else None
            if hide and src_size and src_size[0] and src_size[1]:
                _x, _y, w, h = pixels(W, H, box)
                shown = match_rect(rect, box, float(src_size[0]),
                                   float(src_size[1]), W, H)
                parts.append(f"[ly{uid}_{k}]{chain}[lyq{uid}_{k}]")
                _conceal_chain(parts, f"lyq{uid}_{k}", f"lyh{uid}_{k}", shown,
                               hide, w, h, f"lyk{uid}_{k}")
                chain, src_label = "null", f"lyh{uid}_{k}"
        if k == 0:
            parts.append(f"[{src_label}]{chain},pad={W}:{H}:{x}:{y}:color=black"
                         f"[lyc{uid}_0]")
            continue
        parts.append(f"[{src_label}]{chain}[lyp{uid}_{k}]")
        parts.append(f"[lyc{uid}_{k - 1}][lyp{uid}_{k}]overlay={x}:{y}"
                     f":shortest=1[lyc{uid}_{k}]")
    parts.append(f"[lyc{uid}_{n - 1}]{tail}[{out_label}]")


# A concealed box (CardPanel.conceal) is blurred this strongly (a share of
# its own short side), darkened to this share of its level and feathered
# over this share of the panel's short side into the picture around it.
CONCEAL_BLUR = .25
CONCEAL_DIM = .45
CONCEAL_FEATHER = .12


def conceal_boxes(rect, regions, w, h):
    """[(x0, y0, x1, y1, interior sides)] — the parts of ``regions`` (SOURCE
    fractions) a panel showing ``rect`` at w x h draws, in panel pixels,
    each grown by the feather on the sides that face the picture (never
    past the panel), interior = (left, top, right, bottom) feathered."""
    out = []
    rx0, ry0, rx1, ry1 = (float(v) for v in rect)
    f = max(4, int(round(CONCEAL_FEATHER * min(w, h))))
    for c in regions or []:
        ix0, iy0 = max(float(c[0]), rx0), max(float(c[1]), ry0)
        ix1, iy1 = min(float(c[2]), rx1), min(float(c[3]), ry1)
        if ix1 - ix0 < 1e-4 or iy1 - iy0 < 1e-4:
            continue
        px0 = (ix0 - rx0) / (rx1 - rx0) * w
        py0 = (iy0 - ry0) / (ry1 - ry0) * h
        px1 = (ix1 - rx0) / (rx1 - rx0) * w
        py1 = (iy1 - ry0) / (ry1 - ry0) * h
        inner = (px0 > 1, py0 > 1, px1 < w - 1, py1 < h - 1)
        x0 = _even(max(0, px0 - f)) if inner[0] else 0
        y0 = _even(max(0, py0 - f)) if inner[1] else 0
        x1 = min(w, _even(px1 + f)) if inner[2] else w
        y1 = min(h, _even(py1 + f)) if inner[3] else h
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            out.append((x0, y0, x1, y1, inner))
    return out


def _conceal_chain(parts, in_label, out_label, rect, regions, w, h, uid):
    """[in_label] (a panel picture, w x h) -> [out_label] with the parts of
    burned-in boxes it shows softened: blurred, darkened and feathered into
    the picture — what of the box a face-holding framing could not leave
    out reads as shadow, never as a second screen."""
    boxes = conceal_boxes(rect, regions, w, h)
    if not boxes:
        parts.append(f"[{in_label}]null[{out_label}]")
        return
    f = max(4, int(round(CONCEAL_FEATHER * min(w, h))))
    parts.append(f"[{in_label}]split={len(boxes) + 1}[{uid}m]"
                 + "".join(f"[{uid}c{k}]" for k in range(len(boxes))))
    base = f"{uid}m"
    for k, (x0, y0, x1, y1, inner) in enumerate(boxes):
        cw, ch = x1 - x0, y1 - y0
        r = max(2, min(int(CONCEAL_BLUR * min(cw, ch)), (min(cw, ch) // 2) - 1))
        rc = max(1, min(r // 2, (min(cw, ch) // 4) - 1))
        # an eased ramp over the feather on each side facing the picture:
        # the softened box fades in like a shadow, never a hard-edged block
        ramps = []
        if inner[0]:
            ramps.append(f"X/{f}")
        if inner[1]:
            ramps.append(f"Y/{f}")
        if inner[2]:
            ramps.append(f"(W-1-X)/{f}")
        if inner[3]:
            ramps.append(f"(H-1-Y)/{f}")
        t = "1"
        for ramp in ramps:
            t = f"min({t},{ramp})"
        t = f"clip({t},0,1)"
        alpha = f"255*{t}*{t}*(3-2*{t})"
        parts.append(
            f"[{uid}c{k}]crop={cw}:{ch}:{x0}:{y0},"
            f"boxblur=luma_radius={r}:luma_power=2:chroma_radius={rc}:chroma_power=2,"
            f"lutyuv=y='16+(val-16)*{CONCEAL_DIM:.2f}':u='128+(val-128)*.5'"
            f":v='128+(val-128)*.5',format=yuva420p,"
            f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='clip({alpha},0,255)'"
            f"[{uid}s{k}]")
        nxt = out_label if k == len(boxes) - 1 else f"{uid}o{k}"
        parts.append(f"[{base}][{uid}s{k}]overlay={x0}:{y0}:format=auto,"
                     f"format=yuv420p[{nxt}]")
        base = nxt


def _follow_panel(parts, in_label, out_label, W, H, fps, box, rect, src_size,
                  follow_block, seg_dur, frames, uid):
    """One stack panel whose rect FOLLOWS its speaker (follow.window_chain
    at the box's size): the rect's centre moves along the panel's follow
    path, held inside the source frame (a panel never shows past it)."""
    import follow
    import renderer
    tmap, kspan, ts, cxs, cys, interp = follow_block
    sw, sh = float(src_size[0]), float(src_size[1])
    _x, _y, w, h = pixels(W, H, box)
    rect = match_rect(rect, box, sw, sh, W, H)
    rw, rh = (rect[2] - rect[0]) * sw, (rect[3] - rect[1]) * sh
    k = w / rw
    hx, hy = rw / 2.0 / sw, rh / 2.0 / sh
    cxs = [min(max(c, hx), 1.0 - hx) if hx < 0.5 else 0.5 for c in cxs]
    cys = [min(max(c, hy), 1.0 - hy) if hy < 0.5 else 0.5 for c in cys]
    ox = [cx * sw - rw / 2.0 for cx in cxs]
    oy = [cy * sh - rh / 2.0 for cy in cys]
    flags, sharpen = _enlarge(k)
    follow.window_chain(
        parts, in_label, out_label, f"c{uid}", src_size=(sw, sh),
        out_size=(w, h), k=k, ts=ts, ox=ox, oy=oy, length=seg_dur or 0.0,
        fps=fps, tail=renderer.block_tail(fps, seg_dur, frames), grade=None,
        interpolation=interp, sharpen=sharpen, flags=flags, time_map=tmap,
        key_span=kspan)


def overlaps_source_card(edl, spans):
    """True when any program span in ``spans`` meets a source-fed card's
    window — footage there is re-composed, so a subject mask measured in the
    frame's own framing no longer lines up."""
    for card in ((edl or {}).get("effects") or {}).get("picture_cards") or []:
        if not source_fed(card):
            continue
        a, b = float(card["start"]), float(card["end"])
        if any(min(b, float(e)) - max(a, float(s)) > 1e-3 for s, e in spans):
            return True
    return False


def pixels(W, H, rect):
    def even(value):
        return int(round(value / 2)) * 2
    x, y = even(W * rect[0]), even(H * rect[1])
    right, bottom = min(W, even(W * rect[2])), min(H, even(H * rect[3]))
    return x, y, max(2, right - x), max(2, bottom - y)


def _windows(W, H, spec):
    """(x, y, w, h, corner radius) in px for every rounded window."""
    out = []
    for box in card_boxes(spec):
        x, y, w, h = pixels(W, H, box)
        out.append((x, y, w, h, float(spec.get("radius", .045)) * min(w, h)))
    return out


def designed(spec):
    """True when the card draws a designed backdrop (not the flat colour)."""
    return bool(spec.get("background_style") in DESIGNED_STYLES
                or spec.get("grain") or spec.get("vignette"))


def plate_path(workdir, W, H, spec, kind="plate"):
    keys = ("box", "radius", "border", "border_color", "background", "shadow")
    if designed(spec):
        keys += ("background_style", "background_color2", "vignette")
    style = {k: spec.get(k) for k in keys}
    if spec.get("panels"):
        # Only stacked cards carry it, so every older digest is unchanged.
        style["windows"] = card_boxes(spec)
    digest = hashlib.sha256(json.dumps([W, H, style], sort_keys=True).encode()).hexdigest()[:20]
    suffix = "" if kind == "plate" else f"-{kind}"
    return os.path.join(workdir, f"picture-card-{digest}{suffix}.png")


def _rgb(color):
    import numpy as np
    c = (color or "#101012").lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def _smooth(t):
    """Smoothstep, IN PLACE on a float32 array (no full-canvas copies)."""
    import numpy as np
    sq = np.square(t)
    t *= -2.0
    t += 3.0
    t *= sq
    return t


def _axes(W, H, cx=.5, cy=.5):
    """Pixel-centre offsets from (cx, cy) as broadcastable float32 axes,
    (1 x W) and (H x 1): the radial maths below never materializes an
    int64 mgrid of the whole canvas (a 2160x3840 plate peaked at ~1 GB)."""
    import numpy as np
    xx = ((np.arange(W, dtype=np.float32) + np.float32(.5)) / np.float32(W)
          - np.float32(cx))[None, :]
    yy = ((np.arange(H, dtype=np.float32) + np.float32(.5)) / np.float32(H)
          - np.float32(cy))[:, None]
    return xx, yy


def _vignette(W, H, strength):
    """Multiplicative darkening, 1.0 in the middle, falling off toward the
    corners in the canvas's own aspect (an ellipse on a portrait frame).
    H x W float32."""
    import numpy as np
    xx, yy = _axes(W, H)
    r = np.hypot(xx, yy)
    r *= np.float32(1 / .7071)
    r -= np.float32(.32)
    r *= np.float32(1 / .68)
    fall = _smooth(np.clip(r, 0, 1, out=r))
    fall *= np.float32(-.78 * float(strength))
    fall += np.float32(1.0)
    return fall


def _fill(W, H, spec):
    """H x W x 3 float32 backdrop for the static styles (before shadow)."""
    import numpy as np
    style = spec.get("background_style")
    c1 = _rgb(spec.get("background"))
    c2 = (_rgb(spec["background_color2"]) if spec.get("background_color2")
          else c1 * np.float32(.3))
    img = np.empty((H, W, 3), np.float32)
    if style == "vertical_gradient":
        t = _smooth(np.linspace(0, 1, H, dtype=np.float32))[:, None, None]
        img[:] = c1 * (1 - t) + c2 * t
    elif style == "radial_gradient":
        x0, y0, x1, y1 = spec["box"]
        cx, cy = (x0 + x1) / 2 * W, (y0 + y1) / 2 * H
        far = max(np.hypot(px - cx, py - cy) for px in (0, W) for py in (0, H))
        xx, yy = _axes(W, H, cx / W, cy / H)
        d = np.hypot(xx * np.float32(W), yy * np.float32(H))
        d *= np.float32(1 / (far * .92))
        t = _smooth(np.clip(d, 0, 1, out=d))
        for ch in range(3):                   # one H x W plane at a time
            np.multiply(t, c2[ch] - c1[ch], out=img[..., ch])
            img[..., ch] += c1[ch]
    else:
        img[:] = c1
    if spec.get("vignette"):
        img *= _vignette(W, H, spec["vignette"])[..., None]
    return img


def _dither(img):
    """Quantize with +-0.5 LSB of fixed-seed noise: smooth gradients never
    band at 8 bits, and the same design always yields the same pixels.
    Consumes ``img`` (dithered in place)."""
    import numpy as np
    rng = np.random.default_rng(GRAIN_SEED)
    noise = rng.random(img.shape[:2], dtype=np.float32)
    noise -= np.float32(.5)
    img += noise[..., None]
    return np.clip(img, 0, 255, out=img).astype(np.uint8)


def _geometry_masks(W, H, spec):
    """(hole alpha, border ring, shadow) as PIL L images at W x H, drawn
    exactly as build_plate draws them."""
    from PIL import Image, ImageDraw, ImageFilter
    wins = _windows(W, H, spec)
    shadow = float(spec.get("shadow", .35))
    shade = Image.new("L", (W, H))
    if shadow:
        blur = max(2, round(min(W, H) * .018))
        d = ImageDraw.Draw(shade)
        for x, y, w, h, radius in wins:
            d.rounded_rectangle(
                (x, y + blur*.6, x+w-1, y+h-1+blur*.6),
                radius=radius, fill=round(shadow*190))
        shade = shade.filter(ImageFilter.GaussianBlur(blur))
    ss = 3
    border = float(spec.get("border", .001)) * min(W, H)
    alpha = Image.new("L", (W*ss, H*ss), 255)
    d = ImageDraw.Draw(alpha)
    for x, y, w, h, radius in wins:
        d.rounded_rectangle(
            (x*ss, y*ss, (x+w)*ss-1, (y+h)*ss-1), radius=radius*ss, fill=0)
    ring = Image.new("L", (W, H))
    if border:
        edge = Image.new("L", (W*ss, H*ss))
        d = ImageDraw.Draw(edge)
        for x, y, w, h, radius in wins:
            d.rounded_rectangle(((x-border)*ss, (y-border)*ss,
                                 (x+w+border)*ss-1, (y+h+border)*ss-1),
                                radius=(radius+border)*ss, fill=255)
        for x, y, w, h, radius in wins:
            d.rounded_rectangle((x*ss, y*ss, (x+w)*ss-1, (y+h)*ss-1),
                                radius=radius*ss, fill=0)
        ring = edge.resize((W, H), Image.Resampling.LANCZOS)
    return alpha.resize((W, H), Image.Resampling.LANCZOS), ring, shade


def build_designed_plate(path, W, H, spec):
    """Static designed backdrop: gradient/solid + vignette + shadow + border,
    opaque outside the card. The hole keeps the CLEAN fill in its RGB (alpha
    0), which the graph splits off as the layer under the opening footage."""
    import numpy as np
    from PIL import Image
    hole, ring, shade = _geometry_masks(W, H, spec)
    img = _fill(W, H, spec)
    k = np.asarray(shade, np.float32)
    k *= np.asarray(hole, np.float32)
    k *= np.float32(-1 / 255.0 ** 2)
    k += np.float32(1.0)
    img *= k[..., None]                      # shadow only where the plate shows
    b = np.asarray(ring, np.float32)
    b *= np.float32(1 / 255.0)
    border = _rgb(spec.get("border_color", "#444444"))
    for ch in range(3):                      # img * (1 - b) + border * b
        plane = img[..., ch]
        plane -= border[ch]
        plane *= 1.0 - b
        plane += border[ch]
    del k, b
    rgba = np.dstack([_dither(img), np.asarray(hole, np.uint8)])
    # Fast deflate: the dither makes this a large PNG, decoded once a render.
    Image.fromarray(rgba, "RGBA").save(path, compress_level=1)
    return path


def build_decor(path, mask_path, W, H, spec):
    """Dynamic (blur) backdrop: a decor plate — shadow, vignette, hairline as
    straight RGBA over a transparent canvas — and the static mask (white
    outside the card) through which the backdrop is laid back over the
    footage's overhang. Both exclude the card's own window."""
    import numpy as np
    from PIL import Image
    hole, ring, shade = _geometry_masks(W, H, spec)
    # In place on H x W float32 planes (a 4K-class canvas stays ~100 MB):
    # dark = 1 - (1 - shadow) * (1 - vignette)   black: shadow over vignette
    # alpha = ring + dark * (1 - ring)            hairline on top
    alpha = np.asarray(shade, np.float32)
    alpha *= np.float32(-1 / 255.0)
    alpha += np.float32(1.0)
    if spec.get("vignette"):
        alpha *= _vignette(W, H, spec["vignette"])
    b = np.asarray(ring, np.float32)
    b *= np.float32(1 / 255.0)
    alpha *= b - np.float32(1.0)               # -(1-dark)(1-b) ...
    alpha += np.float32(1.0)                   # ... = b + dark * (1 - b)
    share = b / np.maximum(alpha, np.float32(1e-6))
    del b
    rgba = np.empty((H, W, 4), np.uint8)
    for ch, c in enumerate(_rgb(spec.get("border_color", "#444444"))):
        rgba[..., ch] = np.clip(share * c + .5, 0, 255)
    del share
    alpha *= np.asarray(hole, np.float32)      # x outside (hole / 255) x 255
    alpha += np.float32(.5)
    rgba[..., 3] = np.clip(alpha, 0, 255, out=alpha)
    Image.fromarray(rgba, "RGBA").save(path)
    hole.save(mask_path)
    return path, mask_path


def build_plate(path, W, H, spec):
    from PIL import Image, ImageDraw, ImageFilter
    wins = _windows(W, H, spec)
    bg = spec.get("background", "#101012")
    image = Image.new("RGB", (W, H), bg)
    shadow = float(spec.get("shadow", .35))
    if shadow:
        mask = Image.new("L", (W, H))
        blur = max(2, round(min(W, H) * .018))
        d = ImageDraw.Draw(mask)
        for x, y, w, h, radius in wins:
            d.rounded_rectangle(
                (x, y + blur*.6, x+w-1, y+h-1+blur*.6),
                radius=radius, fill=round(shadow*190))
        image = Image.composite(Image.new("RGB", (W,H)), image,
                                mask.filter(ImageFilter.GaussianBlur(blur)))
    # Supersample both the border and the transparent hole. Width is relative
    # to the canvas short side, so it survives both proof and HD delivery.
    ss = 3
    border = float(spec.get("border", .001)) * min(W,H)
    alpha = Image.new("L", (W*ss,H*ss), 255)
    d = ImageDraw.Draw(alpha)
    for x, y, w, h, radius in wins:
        d.rounded_rectangle(
            (x*ss,y*ss,(x+w)*ss-1,(y+h)*ss-1), radius=radius*ss, fill=0)
    if border:
        edge = Image.new("L", (W*ss,H*ss))
        d = ImageDraw.Draw(edge)
        for x, y, w, h, radius in wins:
            d.rounded_rectangle(((x-border)*ss,(y-border)*ss,
                                 (x+w+border)*ss-1,(y+h+border)*ss-1),
                                radius=(radius+border)*ss, fill=255)
        for x, y, w, h, radius in wins:
            d.rounded_rectangle((x*ss,y*ss,(x+w)*ss-1,(y+h)*ss-1),
                                radius=radius*ss, fill=0)
        image = Image.composite(Image.new("RGB", (W,H), spec.get("border_color", "#444444")),
                                image, edge.resize((W,H), Image.Resampling.LANCZOS))
    image = image.convert("RGBA")
    image.putalpha(alpha.resize((W,H), Image.Resampling.LANCZOS))
    image.save(path)
    return path


def prepare_inputs(edl, workdir, W, H, fps, args, next_idx):
    inputs = []
    for spec in (edl.get("effects") or {}).get("picture_cards") or []:
        if designed(spec):
            # Designed plates are ONE decoded frame each, repeated in the
            # graph (_still): `-loop 1` would re-decode a full-canvas PNG
            # and re-convert it every frame, which measured as most of the
            # designed backdrop's cost.
            if spec.get("background_style") == "blur":
                path = plate_path(workdir, W, H, spec, "decor")
                mask = plate_path(workdir, W, H, spec, "mask")
                if not (os.path.exists(path) and os.path.exists(mask)):
                    build_decor(path, mask, W, H, spec)
                args.extend(["-framerate", str(fps), "-i", path,
                             "-framerate", str(fps), "-i", mask])
                inputs.append((next_idx, dict(spec, _mask_input=next_idx + 1)))
                next_idx += 2
                continue
            path = plate_path(workdir, W, H, spec)
            if not os.path.exists(path):
                build_designed_plate(path, W, H, spec)
            args.extend(["-framerate", str(fps), "-i", path])
            inputs.append((next_idx, spec))
            next_idx += 1
            continue
        path = plate_path(workdir, W, H, spec)
        if not os.path.exists(path):
            build_plate(path, W, H, spec)
        # The plate is ONE decoded still that overlay holds for the whole
        # card (append_graph). It used to be a `-loop 1 -t window -r fps`
        # input that re-decoded the 1080x1920 RGBA PNG every frame; ffmpeg 8
        # decodes such inputs ahead of the graph and parks the 8 MB frames
        # in RAM — 12 cards held ~3.3 GB of identical plates on a 40 s final.
        args.extend(["-i", path])
        inputs.append((next_idx, spec))
        next_idx += 1
    return inputs, next_idx


def _still(length, fps):
    """Repeat a single decoded (and already converted) frame for the card's
    window plus a frame of slack, at the program rate."""
    return (f"loop=loop=-1:size=1:start=0,"
            f"trim=end_frame={int(math.ceil((length + .1) * float(fps)))}")


def _grain(spec):
    """Temporal luma grain for a backdrop branch ('' when off). Strength 20
    is Gaussian sigma ~11.5/255; .25 lands on the references' sigma ~3.

    Measured at the final encode (x264 veryfast CRF 20, 1080x1920, dark
    radial backdrop): the encoder's dead zone erases grain completely below
    ~.2 (c0s <= 4) and keeps it from .25 (c0s 5), where the export grows
    ~3-4x. Coarser (half-resolution) or 2-frame-held grain is NOT cheaper:
    the first is erased outright, the second doubles the size (mbtree spends
    more on the reused frames). Previews (smaller, CRF 27) never show it."""
    g = float(spec.get("grain") or 0)
    if g <= 0:
        return ""
    return f"noise=c0s={max(1, round(20 * g))}:c0f=t:c0_seed={GRAIN_SEED},"


def _blur_chain(W, H, spec):
    """Cover-scale the footage to 1/8 of the canvas, blur and darken there,
    and scale back up: a full-canvas backdrop for the cost of a thumbnail."""
    bw, bh = max(16, int(round(W / 16)) * 2), max(16, int(round(H / 16)) * 2)
    k = 1.0 - float(spec.get("background_dim") if spec.get("background_dim")
                    is not None else BLUR_DIM_DEFAULT)
    # Proportional to the canvas, so a 360p preview and a 1080p final show
    # the same softness.
    sigma = max(1.0, min(bw, bh) / 32.0)
    return (f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"setsar=1,format=yuv420p,gblur=sigma={sigma:.2f}:steps=2,"
            f"lutyuv=y='16+(val-16)*{k:.3f}':u='128+(val-128)*.85':v='128+(val-128)*.85',"
            f"scale={W}:{H}:flags=bicubic,setsar=1")


def _program_card_fades(spec, phase, full, edge):
    """A program card's entrance/exit: the WHOLE finished card (backdrop,
    plate and footage) fading over the program under it — a dissolve from
    and to the full-frame shot, never its footage fading in over its own
    already-drawn backdrop (the bare canvas; module docstring). On the
    card piece's own clock (0 at its start; ``phase`` into the card).
    '' when neither end animates (the historical graph)."""
    out = ""
    if spec.get("entrance", "lift") in ANIMATED:
        out += _fade("in", -phase, edge)
    if spec.get("exit", "fade") in ANIMATED:
        out += _fade("out", full - edge - phase, edge)
    return ",format=yuva420p" + out if out else ""


def _append_designed(parts, vlabel, p, idx, spec, W, H, fps, source_rect):
    """The designed-backdrop variant of append_graph's per-card branch."""
    sx, sy, sw, sh = pixels(W, H, source_rect or [0, 0, 1, 1])
    start, end = float(spec["start"]), float(spec["end"])
    length = end-start
    phase = float(spec.get("phase_s") or 0)
    full = float(spec.get("full_duration_s") or length)
    edge = min(float(spec.get("duration_s", .45)), full*.3)
    x, y, w, h = pixels(W, H, spec["box"])
    blur = spec.get("background_style") == "blur"
    parts.append(f"[{vlabel}]split[{p}pass][{p}src]")
    # Transparent padding: a fit=pad card shows its backdrop around the
    # footage rather than a flat bar.
    fit = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=rgba"
           if spec.get("fit", "crop") == "crop" else
           f"scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1,format=rgba,"
           f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0")
    head = f"trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS+{phase:.6f}/TB,crop={sw}:{sh}:{sx}:{sy}"
    tile = fit
    ent, ext = spec.get("entrance", "lift"), spec.get("exit", "fade")
    tile += f",setpts=PTS-{phase:.6f}/TB"
    fades = _program_card_fades(spec, phase, full, edge)
    if blur:
        parts.append(f"[{p}src]{head},split[{p}tsrc][{p}bsrc]")
        parts.append(f"[{p}tsrc]{tile}[{p}tile]")
        parts.append(f"[{p}bsrc]{_blur_chain(W, H, spec)},{_grain(spec)}"
                     f"setpts=PTS-{phase:.6f}/TB,split[{p}bg][{p}bgtop]")
    else:
        parts.append(f"[{p}src]{head},{tile}[{p}tile]")
        parts.append(f"[{idx}:v]format=rgba,split[{p}plate0][{p}under0]")
        # The plate's hole carries the clean fill; dropping alpha makes it
        # the layer the opening footage fades in over.
        parts.append(f"[{p}under0]format=yuv420p,{_still(length, fps)}[{p}bg]")
        grain = _grain(spec)
        parts.append(f"[{p}plate0]" + (f"format=yuva444p,{_still(length, fps)},{grain.rstrip(',')}"
                                       if grain else _still(length, fps)) + f"[{p}plate]")
    ye = str(y)
    if ent in ("lift", "reveal"):
        distance = h if ent == "reveal" else H*.022
        ye += f"+{distance:.3f}*pow(max(0,1-(t+{phase:.6f})/{edge:.6f}),3)"
    if ext in ("lift", "reveal"):
        distance = h if ext == "reveal" else H*.022
        ye += f"+{distance:.3f}*pow(max(0,(t+{phase:.6f}-{full-edge:.6f})/{edge:.6f}),3)"
    parts.append(f"[{p}bg][{p}tile]overlay=x={x}:y='{ye}':shortest=1:format=auto[{p}placed]")
    if blur:
        # The moving backdrop goes back over the footage's overhang outside
        # the rounded window (the lift/reveal slide), then the decor plate.
        parts.append(f"[{spec['_mask_input']}:v]format=gray,{_still(length, fps)}[{p}mask]")
        parts.append(f"[{p}bgtop][{p}mask]alphamerge[{p}clip]")
        parts.append(f"[{p}placed][{p}clip]overlay=0:0:shortest=1:format=auto[{p}framed]")
        parts.append(f"[{idx}:v]format=rgba,{_still(length, fps)}[{p}plate]")
        parts.append(f"[{p}framed][{p}plate]overlay=0:0:shortest=1:format=auto{fades},setpts=PTS+{start:.6f}/TB[{p}card]")
    else:
        parts.append(f"[{p}placed][{p}plate]overlay=0:0:shortest=1:format=auto{fades},setpts=PTS+{start:.6f}/TB[{p}card]")
    parts.append(f"[{p}pass][{p}card]overlay=0:0:eof_action=repeat:repeatlast=1:enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[{p}out]")
    return f"{p}out"


# Render blocks composed for a source-fed card carry this frame-metadata key
# (layout_filter), its value naming the RUN: the contiguous stretch of
# blocks one card branch draws over.
LAYOUT_TAG_KEY = "valmera_card"
# How far a card branch looks past its run's Timeline span for the run's
# frames. The blocks land on the program clock up to a frame or two away
# from the Timeline's exact one; the tag, not the clock, decides. It also
# bounds how many program frames ever wait on the branch (MEMORY in
# _append_source_fed).
SOURCE_CARD_REACH_S = .25


def _append_source_fed(parts, vlabel, p, idx, spec, W, H, fps, runs,
                       anim=None):
    """A source-fed card (one panel or a stack). layout_filter already put
    every panel's source rect on its box of the program picture; this cuts
    each box back out of the finished program (camera and grade included)
    and opens it inside its rounded window over the card's backdrop.

    The card is built FROM the very program frames that were composed for
    it — selected by their layout tag, one branch per run — and keeps their
    timestamps, so it covers exactly those frames: the first composed frame
    and the last one, wherever the blocks really landed on the frame grid,
    and never a frame of an ordinary block (a cut's neighbour, or a spliced
    insert inside the window, which plays full-frame). After its run the
    branch ends and the program passes through (eof_action=pass).

    runs: [(tag, program start, program end)] from build_filtergraph.
    anim: (entrance, exit) program windows the card dissolves over (the
    renderer composed those blocks as the full-frame shot with the panels
    dissolving in: layout_filter ``under``), None each for a cut. The
    WHOLE card fades over them — never its footage over its own backdrop,
    which showed the bare canvas (module docstring).

    MEMORY: overlay's framesync releases no program frame until it knows
    the card branch's NEXT timestamp, and a branch cut from the program
    cannot produce its first frame until the program reaches the run. Two
    lead frames placed just before the run's trim window (tpad, re-timed)
    answer that at once, so the program flows freely up to the run and at
    most ~SOURCE_CARD_REACH_S of frames ever wait. With the lead frames at
    zero instead, every frame before the run queued in RAM: a card at 30 s
    of a 1080x1920 final peaked at 5.1 GB against 0.6 GB for a program
    card. Grained plates loop per run for the same reason — a split of one
    moving plate would park every frame one run consumes in the next run's
    queue."""
    start, end = float(spec["start"]), float(spec["end"])
    wins = [pixels(W, H, box) for box in card_boxes(spec)]
    n = len(wins)
    blur = spec.get("background_style") == "blur"
    styled = designed(spec)
    grain = _grain(spec)
    # The whole card's dissolve, on the program clock (a fragment that
    # opens inside a fade has it start before zero: _fade shifts).
    fades = ""
    win_in, win_out = anim or (None, None)
    if win_in:
        fades += _fade("in", win_in[0], win_in[1] - win_in[0])
    if win_out:
        fades += _fade("out", win_out[0], win_out[1] - win_out[0])
    # Each run's trim window on the program clock: its blocks' Timeline
    # span, reached a little either side (the tag, not the clock, decides
    # which frames belong to it).
    # A bare tag (no span) reaches over the whole authored window.
    runs = [(run, start, end) if isinstance(run, str) else tuple(run)
            for run in runs]
    spans = [(max(0.0, float(t0) - SOURCE_CARD_REACH_S),
              float(t1) + SOURCE_CARD_REACH_S) for _tag, t0, t1 in runs]
    nruns = len(runs)
    # The card's stills, once per run: the plate (and the static backdrop's
    # clean fill, or the blur's decor mask). A single still is held over
    # every frame (eof repeat); a grained plate is a run-long loop placed
    # on the program clock so its noise moves.
    def stills(label, chain):
        outs = [f"{p}{label}{r}" for r in range(nruns)]
        parts.append(f"{chain}" + (f",split={nruns}" if nruns > 1 else "")
                     + "".join(f"[{o}]" for o in outs))
        return outs
    if blur:
        plates = stills("plate", f"[{idx}:v]format=rgba,setpts=PTS-STARTPTS")
        masks = stills("mask", f"[{spec['_mask_input']}:v]format=gray,setpts=PTS-STARTPTS")
    elif styled:
        parts.append(f"[{idx}:v]format=rgba,split[{p}plate0][{p}under0]")
        unders = stills("under", f"[{p}under0]format=yuv420p,setpts=PTS-STARTPTS")
        if grain:
            # ONE decoded frame split (cheap), then each run loops its own.
            plates = stills("still", f"[{p}plate0]format=yuva444p")
            for r, (a, b) in enumerate(spans):
                parts.append(f"[{plates[r]}]{_still(b - a, fps)},{grain}"
                             f"setpts=PTS+{a:.6f}/TB[{p}plate{r}g]")
            plates = [f"{p}plate{r}g" for r in range(nruns)]
        else:
            plates = stills("plate", f"[{p}plate0]setpts=PTS-STARTPTS")
    else:
        plates = stills("plate", f"[{idx}:v]setpts=PTS-STARTPTS,format=rgba")
    color = spec.get("background", "#101012").replace("#", "0x")
    for r, run in enumerate(runs):
        tag = run[0]
        a, b = spans[r]
        q = f"{p}r{r}"
        outs = [f"{q}s{k}" for k in range(n)] + [f"{q}b"]
        parts.append(f"[{vlabel}]split[{q}pass][{q}src]")
        parts.append(f"[{q}src]trim=start={a:.6f}:end={b:.6f},"
                     f"metadata=mode=select:key={LAYOUT_TAG_KEY}:value={tag}"
                     f":function=same_str,split={len(outs)}"
                     + "".join(f"[{o}]" for o in outs))
        for k, (x, y, w, h) in enumerate(wins):
            # opaque: the footage is exactly the program's inside its box
            parts.append(f"[{q}s{k}]crop={w}:{h}:{x}:{y},setsar=1[{q}tile{k}]")
        if blur:
            x, y, w, h = wins[0]       # the first panel (the speaker)
            parts.append(f"[{q}b]crop={w}:{h}:{x}:{y},{_blur_chain(W, H, spec)},"
                         f"{grain}split[{q}bg][{q}bgtop]")
        elif styled:
            parts.append(f"[{q}b]format=yuv420p[{q}bf]")
            parts.append(f"[{q}bf][{unders[r]}]overlay=0:0:eof_action=repeat"
                         f":format=auto[{q}bg]")
        else:
            parts.append(f"[{q}b]format=yuv420p,drawbox=x=0:y=0:w=iw:h=ih"
                         f":color={color}:t=fill[{q}bg]")
        base = f"{q}bg"
        for k, (x, y, w, h) in enumerate(wins):
            out = f"{q}placed" if k == n - 1 else f"{q}pl{k}"
            parts.append(f"[{base}][{q}tile{k}]overlay=x={x}:y={y}"
                         f":format=auto[{out}]")
            base = out
        if blur:
            parts.append(f"[{q}bgtop][{masks[r]}]alphamerge[{q}clip]")
            parts.append(f"[{base}][{q}clip]overlay=0:0:format=auto[{q}framed]")
            base = f"{q}framed"
        # tpad's two transparent lead frames are re-timed to the two frame
        # slots just before the trim window (MEMORY above); the run's own
        # frames get their exact timestamps back.
        lead = (f"if(lt(N,2),({a:.6f}-(2-N)/{float(fps):.6f})/TB,"
                f"PTS-2*round(1/(FRAME_RATE*TB)))")
        # The run's frames alone decide the card's length: a grained plate
        # loops a little past the run, and overlay would otherwise go on
        # holding the run's last frame over every remaining plate frame
        # (the frozen card over the insert or past the cut). A single still
        # ends at once and is held (eof_action=repeat).
        parts.append(f"[{base}][{plates[r]}]overlay=0:0:eof_action=repeat"
                     + (":shortest=1" if styled and grain and not blur
                        else "") +
                     f":format=auto,format=yuva420p{fades},"
                     f"tpad=start=2:color=black@0,"
                     f"setpts='{lead}'[{q}card]")
        parts.append(f"[{q}pass][{q}card]overlay=0:0:eof_action=pass"
                     f":format=auto[{q}out]")
        vlabel = f"{q}out"
    return vlabel


def append_graph(parts, vlabel, inputs, W, H, fps, source_rect=None,
                 runs=None, anim=None):
    """One branch per card over the composed program ``vlabel``.

    source_rect is frame.picture: the region of the program a program card
    shows. runs = {card id: [(layout tag, program start, program end), ...]}
    names the runs of render blocks renderer.build_filtergraph composed for
    each source-fed card (_append_source_fed). A source-fed card with no run (its window holds
    only spliced media) draws nothing. anim = {card id: (entrance window,
    exit window)} — the dissolves build_filtergraph composed the blocks
    for (a source-fed card absent from it cuts in and out).
    """
    if not inputs:
        return vlabel
    runs = runs or {}
    sx, sy, sw, sh = pixels(W,H,source_rect or [0,0,1,1])
    for j, (idx, spec) in enumerate(inputs or []):
        p = f"pc{j}"
        if source_fed(spec):
            if runs.get(spec.get("id")):
                vlabel = _append_source_fed(parts, vlabel, p, idx, spec, W, H,
                                            fps, runs[spec["id"]],
                                            (anim or {}).get(spec.get("id")))
            continue
        if designed(spec):
            vlabel = _append_designed(parts, vlabel, p, idx, spec, W, H, fps,
                                      source_rect)
            continue
        start, end = float(spec["start"]), float(spec["end"])
        length = end-start
        phase = float(spec.get("phase_s") or 0)
        full = float(spec.get("full_duration_s") or length)
        edge = min(float(spec.get("duration_s",.45)), full*.3)
        x,y,w,h = pixels(W,H,spec["box"])
        color = spec.get("background", "#101012").replace("#", "0x")
        parts.append(f"[{vlabel}]split[{p}pass][{p}src]")
        fit = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
               if spec.get("fit", "crop") == "crop" else
               f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={color}")
        chain = f"trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS+{phase:.6f}/TB,crop={sw}:{sh}:{sx}:{sy},{fit},setsar=1,format=rgba"
        ent, ext = spec.get("entrance","lift"), spec.get("exit","fade")
        chain += f",setpts=PTS-{phase:.6f}/TB"
        fades = _program_card_fades(spec, phase, full, edge)
        parts.append(f"[{p}src]{chain}[{p}tile]")
        parts.append(f"color=c={color}:s={W}x{H}:r={fps}:d={length:.6f}[{p}bg]")
        ye = str(y)
        if ent in ("lift","reveal"):
            distance = h if ent=="reveal" else H*.022
            ye += f"+{distance:.3f}*pow(max(0,1-(t+{phase:.6f})/{edge:.6f}),3)"
        if ext in ("lift","reveal"):
            distance = h if ext=="reveal" else H*.022
            ye += f"+{distance:.3f}*pow(max(0,(t+{phase:.6f}-{full-edge:.6f})/{edge:.6f}),3)"
        parts.append(f"[{p}bg][{p}tile]overlay=x={x}:y='{ye}':shortest=1:format=auto[{p}placed]")
        parts.append(f"[{idx}:v]setpts=PTS-STARTPTS,format=rgba[{p}plate]")
        # MEMORY: the card is a window of the program itself, so its first
        # frame cannot exist until the program reaches `start`, and overlay's
        # framesync releases no main frame until it has seen the secondary's
        # first timestamp. Every full-resolution program frame before `start`
        # therefore queued in RAM: 6 cards on a 40 s 1080x1920 program peaked
        # at 6.4 GB, and finals were OOM-killed. Two lead frames that tpad
        # emits on demand before any input arrives tell framesync "nothing
        # until start-2/fps", so the main stream flows and at most a frame or
        # two waits. They sit just before `start`, where the enable window
        # keeps the overlay off, so they are never drawn. tpad keeps the
        # card's own 1/fps timebase and shifts it by exactly those two ticks,
        # which `PTS-2` removes: every card frame keeps the timestamp the
        # direct `PTS+start/TB` gave it, so the composite is bit-identical.
        # shortest=0 + eof_action=repeat: the single plate frame is held over
        # every card frame, and [placed] alone decides the card's length —
        # exactly what the window-length looped plate (always the longer
        # input under shortest=1) produced before.
        parts.append(f"[{p}placed][{p}plate]overlay=0:0:shortest=0:eof_action=repeat:format=auto{fades},"
                     f"tpad=start=2,setpts=PTS-2+{start:.6f}/TB[{p}card]")
        # trim/setpts and framesync quantize fractional cut clocks differently.
        # The card branch can reach EOF one or two frames before the program
        # clock reaches end. Hold its last composed frame until that exact
        # boundary; passing through at EOF briefly exposes square footage.
        # The explicit enable interval still releases it at the authored end.
        parts.append(f"[{p}pass][{p}card]overlay=0:0:eof_action=repeat:repeatlast=1:enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[{p}out]")
        vlabel = f"{p}out"
    return vlabel
