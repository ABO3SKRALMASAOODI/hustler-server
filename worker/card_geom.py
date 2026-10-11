"""Where the main footage's SOURCE pixels land in the OUTPUT frame at one
program second: the frame's crop, the camera's zoom and the picture card
that holds them — as axis-aligned windows (Oct 2026 review tools).

WHY

A picture card enlarges a window of the source ~1.8-2x onto a designed
backdrop. Read on the rendered frames, Haar boxes drift on that soft,
upscaled face, and on a blur backdrop they lock onto the giant blurred copy
of the same face behind the card: the Oct 10 podcast run's PICTURE CHECK
called a face "cut 16-30% past the card edge" on every short whose face sat
inside its card. The source frame has the face at its own resolution and
nothing behind it, so the picture check reads faces THERE and places them
through these windows; look_at's assembled view composes cards with them.

A window is ``(box, src)``: ``box`` the output rectangle (fractions of the
frame) that shows main footage, ``src`` the source rectangle (fractions of
the source frame) that lands exactly on it — a source point s maps to
``box0 + (s - src0) / (src1 - src0) * (box1 - box0)`` on each axis. ``src``
may run past 0-1 (a contained frame's bars, a zoom out past the source).

Mirrors, never re-implements, the render: picture_cards (source rects,
match_rect, the program card's cover fit of frame.picture), renderer
(picture_mapping, zoom_state_at's viewport x0=(1-1/z)*cx). A card's
lift/reveal slide and roll/shake are not part of a window. Pure.
"""


def frame_focus_at(edl, src_t):
    """The main crop's (focus_x, focus_y) at SOURCE second ``src_t``: a
    follow path's centre where one holds it, else its focus_track span,
    else the frame's static focus (renderer _frame_for, agent_tools
    _frame_focus_at_source)."""
    frame = (edl or {}).get("frame") or {}
    if src_t is not None:
        try:
            import follow
            moving = follow.frame_focus_at(edl, src_t)
        except Exception:  # noqa: BLE001
            moving = None
        if moving is not None and frame_mode_at(edl, src_t) == "crop":
            return moving
    base = (frame.get("focus_x"), frame.get("focus_y"))
    for span in frame.get("focus_track") or [] if src_t is not None else []:
        try:
            if float(span.get("t0")) <= src_t <= float(span.get("t1")):
                return (span.get("x") if span.get("x") is not None else base[0],
                        span.get("y") if span.get("y") is not None else base[1])
        except (AttributeError, TypeError, ValueError):
            continue
    return base


def frame_mode_at(edl, src_t):
    frame = (edl or {}).get("frame") or {}
    base = frame.get("mode") or "crop"
    for span in frame.get("focus_track") or [] if src_t is not None else []:
        try:
            if float(span.get("t0")) <= src_t <= float(span.get("t1")):
                return span.get("mode") or base
        except (AttributeError, TypeError, ValueError):
            continue
    return base


def card_at(edl, t):
    """The picture card on screen at PROGRAM second ``t`` (the last one
    listed wins, as the renderer draws them in order), or None."""
    hit = None
    for card in ((edl or {}).get("effects") or {}).get("picture_cards") or []:
        try:
            if float(card["start"]) - 1e-6 <= float(t) < float(card["end"]):
                hit = card
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return hit


def _lin(v, a0, a1, b0, b1):
    return b0 + (v - a0) / (a1 - a0) * (b1 - b0) if abs(a1 - a0) > 1e-12 \
        else b0


def map_rect(rect, a, b):
    """``rect`` taken from rect space ``a`` to ``b`` (both [x0, y0, x1, y1]:
    a maps onto b corner to corner)."""
    return [_lin(rect[0], a[0], a[2], b[0], b[2]),
            _lin(rect[1], a[1], a[3], b[1], b[3]),
            _lin(rect[2], a[0], a[2], b[0], b[2]),
            _lin(rect[3], a[1], a[3], b[1], b[3])]


def to_output(rect_src, window):
    """A source rectangle placed in the output frame through ``window``."""
    box, src = window
    return map_rect(rect_src, src, box)


def to_source(rect_out, window):
    box, src = window
    return map_rect(rect_out, box, src)


def viewport(z, cx, cy):
    """The canvas rectangle a zoom of ``z`` aimed at (cx, cy) shows
    (renderer.zoom_state_at)."""
    z = max(1.0, float(z or 1.0))
    x0 = (1.0 - 1.0 / z) * float(cx)
    y0 = (1.0 - 1.0 / z) * float(cy)
    return [x0, y0, x0 + 1.0 / z, y0 + 1.0 / z]


def _zoomed(rect, view):
    """A canvas rectangle where the zoom puts it on screen."""
    return map_rect(rect, view, [0.0, 0.0, 1.0, 1.0])


def _cover(inner_wh, box, W, H, fit="crop"):
    """(shown, placed): a ``fit`` of a region of ``inner_wh`` canvas pixels
    into ``box`` — the centred share of the region it shows ([0-1] of the
    region) and the box rectangle it lands in."""
    pw, ph = max(1e-9, inner_wh[0]), max(1e-9, inner_wh[1])
    bw, bh = (box[2] - box[0]) * W, (box[3] - box[1]) * H
    if fit == "pad":
        k = min(bw / pw, bh / ph)
        w, h = pw * k / W, ph * k / H
        cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        return [0.0, 0.0, 1.0, 1.0], [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
    k = max(bw / pw, bh / ph)
    qw, qh = bw / k / pw, bh / k / ph
    return ([(1 - qw) / 2, (1 - qh) / 2, (1 + qw) / 2, (1 + qh) / 2],
            [float(v) for v in box])


def main_window(edl, src_t, W, H, src_w, src_h):
    """(canvas box, source rect) of the main footage's full-frame fit at
    SOURCE second ``src_t`` (renderer.picture_mapping), before the zoom."""
    import renderer
    frame = (edl or {}).get("frame") or {}
    mode = frame_mode_at(edl, src_t)
    focus = frame_focus_at(edl, src_t)
    src, dest = renderer.picture_mapping(src_w, src_h, W, H, mode, focus,
                                         frame.get("picture"))
    if src == [0.0, 0.0, 1.0, 1.0] and dest != src:
        return dest, src                       # contained: bars around it
    return dest, src


def windows(edl, src_t, W, H, src_w, src_h, zoom=None, t=None):
    """[(box, src)] for program second ``t`` (SOURCE second ``src_t`` of the
    main footage): one per picture-card window (each panel of a stack), else
    the whole frame. ``zoom`` is the camera's (z, cx, cy) there, or None.
    [] when the source size is unknown."""
    if not src_w or not src_h or not W or not H:
        return []
    import picture_cards
    W, H, sw, sh = float(W), float(H), float(src_w), float(src_h)
    view = viewport(*zoom) if zoom and float(zoom[0]) > 1.0005 else None
    card = card_at(edl, t) if t is not None else None
    out = []
    if card is not None and picture_cards.source_fed(card):
        # layout_filter: each rect scaled once onto its box of the canvas;
        # the camera then acts on the composed canvas and the card cuts its
        # box(es) back out of the zoomed picture, where they already are.
        for box, rect in picture_cards.card_panels(card, src_t):
            box = [float(v) for v in box]
            rect = picture_cards.match_rect(rect, box, sw, sh, W, H)
            if view:
                rect = map_rect(box, _zoomed(box, view), rect)
            out.append((box, [float(v) for v in rect]))
        return out
    dest, src = main_window(edl, src_t, int(W), int(H), sw, sh)
    if view:
        dest = _zoomed(dest, view)
    if card is not None:
        # a program card: the zoomed program's frame.picture region,
        # cover-fit (or contained) into the card's box
        frame = (edl or {}).get("frame") or {}
        region = [float(v) for v in (frame.get("picture") or [0, 0, 1, 1])]
        shown, placed = _cover(((region[2] - region[0]) * W,
                                (region[3] - region[1]) * H),
                               [float(v) for v in card["box"]], W, H,
                               card.get("fit", "crop"))
        q = map_rect(shown, [0, 0, 1, 1], region)       # canvas fractions
        src_q = map_rect(q, dest, src)
        return [(placed, src_q)]
    vis = [max(0.0, dest[0]), max(0.0, dest[1]),
           min(1.0, dest[2]), min(1.0, dest[3])]
    if vis[2] <= vis[0] or vis[3] <= vis[1]:
        return []
    return [(vis, map_rect(vis, dest, src))]


def window_of(windows_, rect_src, reach=0.5):
    """The window whose source rect (grown by ``reach`` of its size each
    side) holds the centre of ``rect_src``, nearest centre first; None."""
    cx = (rect_src[0] + rect_src[2]) / 2.0
    cy = (rect_src[1] + rect_src[3]) / 2.0
    best = None
    for w in windows_:
        s = w[1]
        gw, gh = (s[2] - s[0]) * reach, (s[3] - s[1]) * reach
        if s[0] - gw <= cx <= s[2] + gw and s[1] - gh <= cy <= s[3] + gh:
            d = abs(cx - (s[0] + s[2]) / 2.0) + abs(cy - (s[1] + s[3]) / 2.0)
            if best is None or d < best[0]:
                best = (d, w)
    return best[1] if best else None


def compose(raw, program, edl, t, src_t, src_w, src_h, zoom=None):
    """A picture card as it frames the footage at programme second ``t``:
    its backdrop (flat, gradient, or the blurred and dimmed program) and
    every window filled from ``raw`` (the SOURCE frame, PIL) through
    ``windows``, with the card's rounded corners. ``program`` (PIL) is the
    full-frame programme fit of the same moment, at the output's size.
    None when no card holds ``t``. Captions, graphics, grade, grain, the
    plate's shadow and the entrance's slide are not drawn."""
    card = card_at(edl, t)
    if card is None:
        return None
    from PIL import Image, ImageDraw, ImageFilter
    import numpy as np
    import picture_cards
    W, H = program.size
    style = card.get("background_style")
    if style == "blur":
        dim = 1.0 - float(card.get("background_dim")
                          if card.get("background_dim") is not None
                          else picture_cards.BLUR_DIM_DEFAULT)
        small = program.resize((max(16, W // 8), max(16, H // 8)), Image.BILINEAR)
        back = small.filter(ImageFilter.GaussianBlur(2)).resize((W, H), Image.BICUBIC)
        back = Image.fromarray((np.asarray(back, np.float32) * dim)
                               .clip(0, 255).astype(np.uint8))
    else:
        fill = picture_cards._fill(W, H, card)
        if fill.max() <= 1.0 + 1e-6:
            fill = fill * 255.0
        back = Image.fromarray(fill.clip(0, 255).astype(np.uint8))
    rw, rh = raw.size
    radius = float(card.get("radius", .045))
    for box, src in windows(edl, src_t, W, H, src_w, src_h, zoom, t):
        x0, y0 = int(round(box[0] * W)), int(round(box[1] * H))
        x1, y1 = int(round(box[2] * W)), int(round(box[3] * H))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        # the source rect may run past the source: black there, as rendered
        crop = raw.crop((int(round(src[0] * rw)), int(round(src[1] * rh)),
                         int(round(src[2] * rw)), int(round(src[3] * rh))))
        tile = crop.resize((x1 - x0, y1 - y0), Image.LANCZOS)
        mask = Image.new("L", tile.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, tile.size[0] - 1, tile.size[1] - 1],
            radius=max(0, int(radius * min(tile.size))), fill=255)
        back.paste(tile, (x0, y0), mask)
    return back
