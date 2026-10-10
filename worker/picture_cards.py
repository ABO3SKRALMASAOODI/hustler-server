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
applied to the backdrop only — the footage itself is never re-noised. A card
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


def source_fed(spec):
    """True when the card's footage comes from the main source frame."""
    return bool(spec.get("source") or spec.get("panels"))


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
    return rect


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
        return [(list(p["box"]), list(p["source"])) for p in spec["panels"]]
    if spec.get("source"):
        return [(list(spec["box"]), source_at(spec, src_t))]
    return []


def card_boxes(spec):
    """The rounded windows a card draws: one per panel, else its box."""
    if spec.get("panels"):
        return [list(p["box"]) for p in spec["panels"]]
    return [list(spec["box"])]


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


def speaker_rect(src_w, src_h, W, H, box, face=None, focus=None,
                 cap=SOURCE_UPSCALE_CAP):
    """A source rect at the box's aspect framing a speaker: the face about
    FACE_SHARE of the card's height, HEADROOM_TARGET of it clear above the
    crown (the source's top edge permitting), never enlarged past ``cap``.
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
    rh = max(fh / FACE_SHARE, bh / cap)
    rw = max(rh * a, bw / cap)
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


def _single_panel(W, H, box, rect, src_size):
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
            f"{flags},setsar=1,pad={W}:{H}:{left}:{top}:color=black")


def _panel(W, H, box, rect, src_size):
    """One rect scaled exactly onto its box (w x h), unplaced."""
    _x, _y, w, h = pixels(W, H, box)
    k = 1.0
    if src_size and src_size[0] and src_size[1]:
        rect = match_rect(rect, box, float(src_size[0]), float(src_size[1]),
                          W, H)
        k = w / ((rect[2] - rect[0]) * float(src_size[0]))
    flags, sharpen = _enlarge(k)
    return f"{_crop_expr(rect)}{sharpen},scale={w}:{h}{flags},setsar=1"


def layout_filter(parts, in_label, out_label, W, H, fps, panels, uid,
                  src_size=None, seg_dur=None, grade=None, tag=None,
                  frames=None, follow_block=None):
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
    lands on the box."""
    import renderer
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
        # the canvas's top-left on the source, for each rect centre
        ox = [cx * sw - rw / 2.0 - x / k for cx in cxs]
        oy = [cy * sh - rh / 2.0 - y / k for cy in cys]
        flags, sharpen = _enlarge(k)
        follow.window_chain(
            parts, in_label, out_label, f"c{uid}", src_size=(sw, sh),
            out_size=(W, H), k=k, ts=ts, ox=ox, oy=oy, length=seg_dur or 0.0,
            fps=fps, tail=tail, grade=grade, interpolation=interp,
            sharpen=sharpen, flags=flags, time_map=tmap, key_span=kspan)
        return
    if len(panels) == 1:
        box, rect = panels[0]
        parts.append(f"[{in_label}]{head}{_single_panel(W, H, box, rect, src_size)},"
                     f"{tail}[{out_label}]")
        return
    n = len(panels)
    parts.append(f"[{in_label}]{head}split={n}"
                 + "".join(f"[ly{uid}_{k}]" for k in range(n)))
    for k, (box, rect) in enumerate(panels):
        x, y, _w, _h = pixels(W, H, box)
        chain = _panel(W, H, box, rect, src_size)
        if k == 0:
            parts.append(f"[ly{uid}_0]{chain},pad={W}:{H}:{x}:{y}:color=black"
                         f"[lyc{uid}_0]")
            continue
        parts.append(f"[ly{uid}_{k}]{chain}[lyp{uid}_{k}]")
        parts.append(f"[lyc{uid}_{k - 1}][lyp{uid}_{k}]overlay={x}:{y}"
                     f":shortest=1[lyc{uid}_{k}]")
    parts.append(f"[lyc{uid}_{n - 1}]{tail}[{out_label}]")


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
    if ent in ("fade", "lift"):
        tile += f",fade=t=in:st=0:d={edge:.6f}:alpha=1"
    if ext in ("fade", "lift"):
        tile += f",fade=t=out:st={full-edge:.6f}:d={edge:.6f}:alpha=1"
    tile += f",setpts=PTS-{phase:.6f}/TB"
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
        parts.append(f"[{p}framed][{p}plate]overlay=0:0:shortest=1:format=auto,setpts=PTS+{start:.6f}/TB[{p}card]")
    else:
        parts.append(f"[{p}placed][{p}plate]overlay=0:0:shortest=1:format=auto,setpts=PTS+{start:.6f}/TB[{p}card]")
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


def _ye_abs(y, h, H, ent, ext, origin, edge, full):
    """_append_graph's lift/reveal offset on the PROGRAM clock: t - origin
    is the time since the full card's start (clipped: a run can open or
    close a frame either side of the authored window)."""
    x = f"(t-{origin:.6f})"
    ye = str(y)
    if ent in ("lift", "reveal"):
        distance = h if ent == "reveal" else H*.022
        ye += f"+{distance:.3f}*pow(clip(1-{x}/{edge:.6f},0,1),3)"
    if ext in ("lift", "reveal"):
        distance = h if ext == "reveal" else H*.022
        ye += f"+{distance:.3f}*pow(clip(({x}-{full-edge:.6f})/{edge:.6f},0,1),3)"
    return ye


def _append_source_fed(parts, vlabel, p, idx, spec, W, H, fps, runs):
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
    length = end-start
    phase = float(spec.get("phase_s") or 0)
    full = float(spec.get("full_duration_s") or length)
    edge = min(float(spec.get("duration_s", .45)), full*.3)
    origin = start - phase             # the full card's start, program clock
    wins = [pixels(W, H, box) for box in card_boxes(spec)]
    n = len(wins)
    blur = spec.get("background_style") == "blur"
    styled = designed(spec)
    grain = _grain(spec)
    ent, ext = spec.get("entrance", "lift"), spec.get("exit", "fade")
    fades = ""
    # fade's start cannot be negative: a proof fragment that opens inside
    # the card has its card clock begin before zero. Its fades then run on
    # a clock shifted forward by that much and shifted straight back (each
    # frame lands on its own timestamp again).
    shift = max(0.0, -origin)
    if ent in ("fade", "lift"):
        fades += f",fade=t=in:st={origin + shift:.6f}:d={edge:.6f}:alpha=1"
    if ext in ("fade", "lift"):
        fades += (f",fade=t=out:st={origin + shift + full - edge:.6f}"
                  f":d={edge:.6f}:alpha=1")
    if fades and shift:
        fades = (f",setpts=PTS+{shift:.6f}/TB{fades}"
                 f",setpts=PTS-{shift:.6f}/TB")
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
            parts.append(f"[{q}s{k}]crop={w}:{h}:{x}:{y},setsar=1,format=rgba"
                         f"{fades}[{q}tile{k}]")
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
            parts.append(f"[{base}][{q}tile{k}]overlay=x={x}:"
                         f"y='{_ye_abs(y, h, H, ent, ext, origin, edge, full)}'"
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
                     f":format=auto,format=yuva420p,"
                     f"tpad=start=2:color=black@0,"
                     f"setpts='{lead}'[{q}card]")
        parts.append(f"[{q}pass][{q}card]overlay=0:0:eof_action=pass"
                     f":format=auto[{q}out]")
        vlabel = f"{q}out"
    return vlabel


def append_graph(parts, vlabel, inputs, W, H, fps, source_rect=None,
                 runs=None):
    """One branch per card over the composed program ``vlabel``.

    source_rect is frame.picture: the region of the program a program card
    shows. runs = {card id: [(layout tag, program start, program end), ...]}
    names the runs of render blocks renderer.build_filtergraph composed for
    each source-fed card (_append_source_fed). A source-fed card with no run (its window holds
    only spliced media) draws nothing.
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
                                            fps, runs[spec["id"]])
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
        if ent in ("fade","lift"):
            chain += f",fade=t=in:st=0:d={edge:.6f}:alpha=1"
        if ext in ("fade","lift"):
            chain += f",fade=t=out:st={full-edge:.6f}:d={edge:.6f}:alpha=1"
        chain += f",setpts=PTS-{phase:.6f}/TB"
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
        parts.append(f"[{p}placed][{p}plate]overlay=0:0:shortest=0:eof_action=repeat:format=auto,"
                     f"tpad=start=2,setpts=PTS-2+{start:.6f}/TB[{p}card]")
        # trim/setpts and framesync quantize fractional cut clocks differently.
        # The card branch can reach EOF one or two frames before the program
        # clock reaches end. Hold its last composed frame until that exact
        # boundary; passing through at EOF briefly exposes square footage.
        # The explicit enable interval still releases it at the authored end.
        parts.append(f"[{p}pass][{p}card]overlay=0:0:eof_action=repeat:repeatlast=1:enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[{p}out]")
        vlabel = f"{p}out"
    return vlabel
