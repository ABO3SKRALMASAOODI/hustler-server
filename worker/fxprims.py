"""FX primitives: the few filtergraph shapes every EDL.fx device compiles to.

Track 0B (ROADMAP Wave 0, INTERFACES.md §4.4). Device compilers (fxgraph and,
from Wave 1, accents / subject_fx / compositor / finish ...) never invent
graph plumbing of their own: they hand these functions a chain and get back
an output label. That is what keeps three properties true for every device:

* FRAME EXACT. Windows are half-open PROGRAM FRAME spans [f0, f1), gated on
  the frame index ``n`` — never on ``t`` — so 23.976 / 29.97 / 59.94 programs
  never drift a frame at a window edge. ``frame_at`` is the one conversion
  (round half up on the 3-decimal render rate the graph runs at).
* LOCAL COST. A window costs only its own frames: a timeline-capable chain
  takes ``enable=`` (zero work outside), and any other chain runs on a
  trimmed branch that is re-padded to the program clock (see ``window``).
  Trimming WITHOUT the re-pad makes ``overlay`` buffer every frame before the
  window (measured: 4.7 GB RSS for a window at 56 s of 1080p25, against
  288 MB re-padded, identical frames).
* NO PER-PIXEL EXPRESSIONS. Shapes come from precomputed gray masks or maps
  (alpha composites / ``maskedmerge``), never ``geq``/``xfade=custom``
  (ROADMAP §2 benchmarks).
* EXACT OUTSIDE. Pixels outside a device's region and window are the
  picture's own, bit for bit: masks become ALPHA on a processed copy laid
  over the untouched stream, so no format or colour-range conversion is ever
  negotiated onto the main picture.

Everything here returns strings into ``parts`` (the renderer's filter list)
and never touches the filesystem; precomputation (mask PNGs, maps) belongs to
a compiler's ``prepare`` step.
"""

import math
import re

# The graph runs at the render rate written with three decimals
# (``fps=29.970``), so frame k sits at k / fps3 on every program stream.
FPS_DECIMALS = 3


def fps3(fps):
    return round(float(fps or 30.0), FPS_DECIMALS)


def frame_at(t, fps):
    """The program frame of program second ``t`` (round half up).

    INTERFACES.md §4.1: ``frame = round(t * fps)`` with ``end`` exclusive.
    Centisecond EDL times identify frames exactly up to 100 fps, so a device
    that writes ``start = round(k / fps, 2)`` reads frame k back here."""
    return int(math.floor(float(t) * fps3(fps) + 0.5 + 1e-9))


def frame_span(start, end, fps):
    """[f0, f1) frames of a program window; at least one frame."""
    f0 = max(0, frame_at(start, fps))
    f1 = max(f0 + 1, frame_at(end, fps))
    return f0, f1


def frame_time(k, fps):
    """Program second of frame k (exact on the graph's frame grid)."""
    return k / fps3(fps)


def merge_frame_spans(spans):
    """Sorted, merged half-open frame spans (touching spans join)."""
    out = []
    for a, b in sorted((int(a), int(b)) for a, b in spans if int(b) > int(a)):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def enable_expr(spans):
    """The ``enable`` expression for half-open frame spans: a union of
    ``between(n,f0,f1-1)`` terms (``between`` is inclusive at both ends).
    One filter with one union expression keeps the graph flat however many
    windows a device places (ROADMAP 1D "graph size")."""
    spans = merge_frame_spans(spans)
    if not spans:
        return "0"
    return "+".join(f"between(n,{a},{b - 1})" for a, b in spans)


def split_chain(chain):
    """Split a filter chain on its top-level commas: commas inside quotes,
    parentheses or after a backslash belong to an option value."""
    if isinstance(chain, (list, tuple)):
        return [str(c).strip() for c in chain if str(c).strip()]
    out, buf, depth, quote, esc = [], [], 0, None, False
    for ch in str(chain):
        if esc:
            buf.append(ch)
            esc = False
            continue
        if ch == "\\":
            buf.append(ch)
            esc = True
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            piece = "".join(buf).strip()
            if piece:
                out.append(piece)
            buf = []
            continue
        buf.append(ch)
    piece = "".join(buf).strip()
    if piece:
        out.append(piece)
    return out


_FILTER_NAME = re.compile(r"^([A-Za-z0-9_]+)")


def filter_names(chain):
    """The ffmpeg filter names a chain uses (for availability checks)."""
    names = []
    for f in split_chain(chain):
        m = _FILTER_NAME.match(f)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def with_enable(chain, expr):
    """``chain`` with ``enable='expr'`` on every filter (each must support
    the timeline; the caller decides — ``window(timeline=...)``)."""
    out = []
    for f in split_chain(chain):
        sep = ":" if "=" in f else "="
        out.append(f"{f}{sep}enable='{expr}'")
    return ",".join(out)


# How a composite lays its branch back over the program (Wave 0
# integration, measured on the v5 Thiel showcase). ffmpeg negotiates pixel
# formats per GROUP of links joined by format-transparent filters: overlay's
# default (yuv420) fixes its links to yuv420p, while ``format=auto`` fixes
# them to an ALPHA format (yuva420p, rgba, ...). The motion graphics
# composite with format=auto, so from the first one on the program runs in
# an alpha group; before them (world, screen, junctions) it is plain yuv420p.
# A composite must JOIN the group it lands in, never re-format it:
#   - a yuv420 overlay on an alpha chain converted the WHOLE program at that
#     point (every frame, inside the window or not: region_locality broken,
#     the end card changed);
#   - an alpha overlay (or an alpha-forced branch) on a plain chain pulled
#     the group downstream into an alpha format, which moved the watermark
#     robot by a pixel (its overlay rounds to the 4:2:0 grid in yuva420p).
OVERLAY = "overlay=eof_action=pass"
OVERLAY_ALPHA = "overlay=eof_action=pass:format=auto"
# Filters that keep their input's format group (negotiation passes through).
_FORMAT_TRANSPARENT = frozenset((
    "split", "trim", "setpts", "null", "copy", "fifo", "settb", "setsar",
    "select", "tpad", "fade", "negate", "subtitles", "ass"))
_PART = re.compile(r"^((?:\[[^\]]+\])*)(.*?)((?:\[[^\]]+\])*)$", re.S)
_LABEL = re.compile(r"\[([^\]]+)\]")


def _producer(parts, label):
    """(input labels, filter chain) of the part that outputs ``label``."""
    for p in reversed(parts):
        m = _PART.match(p.strip())
        if not m:
            continue
        if label in _LABEL.findall(m.group(3)):
            return _LABEL.findall(m.group(1)), m.group(2)
    return None, None


def alpha_chain(parts, label, _depth=0):
    """Does ``label`` carry the program in an ALPHA format group — fed by an
    ``overlay=...:format=auto`` through format-transparent filters only?"""
    if _depth > 400:
        return False
    ins, chain = _producer(parts, label)
    if chain is None:
        return False
    for f in reversed(split_chain(chain)):
        m = _FILTER_NAME.match(f)
        name = m.group(1) if m else ""
        if name == "overlay":
            return "format=auto" in f
        if name not in _FORMAT_TRANSPARENT:
            return False
    return bool(ins) and alpha_chain(parts, ins[0], _depth + 1)


def composite_overlay(parts, main_label):
    """The overlay a composite lays its branch over ``main_label`` with,
    without re-formatting the program: it joins an alpha group and keeps a
    plain one plain. Position and ``enable`` options follow it."""
    return OVERLAY_ALPHA if alpha_chain(parts, main_label) else OVERLAY


# How far ahead of its program frame a re-padded window frame is stamped,
# in microseconds. A concatenated program's stamps carry the rounding of
# each block's start (measured +1 us on later blocks at 29.97), so a branch
# stamped exactly would sometimes sit 1 us AFTER its program frame and the
# overlay would show the previous branch frame there. Two microseconds early
# can never reach the previous frame (>= 8 ms away at 120 fps).
EARLY_US = 2


def restamp(fps):
    """``settb`` + ``setpts`` that stamp frame N exactly on the program grid
    (N / fps3) on the microsecond time base the concatenated program runs on.
    ROUNDED: setpts truncates, and N / 25.000 / TB on a 1/25 link evaluates
    to 56.999... for N = 57, which stacked two frames on one stamp (Wave 1
    integration; every track had found it on its own)."""
    return f"settb=AVTB,setpts=round(N/{fps3(fps):.3f}/TB)"


def repad(f0, fps, alpha=False):
    """The tail of a window branch: re-pad its frames to the program clock
    and RE-STAMP them on the program's own grid (frame n at n / fps3).

    ``tpad`` stamps its padding by accumulating a ROUNDED frame duration in
    the link's time base, so a window frame could arrive before or after the
    program frame it belongs to and overlay's frame sync matched it to the
    wrong one. The branch is re-stamped on the microsecond time base,
    rounded (``restamp``), ``EARLY_US`` ahead of its program frame. Measured
    with a null window over every frame (Wave 1 integration): the truncating
    ``setpts=N/fps/TB`` it replaces showed the previous frame on 8 of 300
    frames at 25 and 50 fps and 2 at 30 fps on a 1/fps link; this stamp is
    frame-exact at 23.976, 25, 29.97, 30, 50 and 59.94, concatenated or not.
    One trailing pad frame keeps the branch alive past the window so its
    last frame is never taken for end-of-stream."""
    color = "black@0" if alpha else "black"
    lead = f"start={int(f0)}:" if f0 > 0 else ""
    return (f",tpad={lead}stop=1:start_mode=add:stop_mode=add:color={color},"
            f"settb=AVTB,setpts='round(N/{fps3(fps):.3f}/TB)"
            f"-{EARLY_US}*gt(N,0)'")


def window(parts, in_label, out_label, spans, op, *, uid, timeline=True,
           fps=None):
    """Apply ``op`` (a filter chain) to the frames in ``spans`` only.

    timeline=True: every filter of ``op`` supports ``enable`` — one chain,
    gated by the frame-index union, no work outside the window.

    timeline=False: split -> trim to the hull of the spans -> op -> re-pad to
    the program clock -> overlay gated by the union. The branch costs only
    the hull's frames, and the re-pad (``tpad`` start frames) keeps the two
    overlay inputs in step so nothing is buffered. ``op`` must keep the
    frame size and stay opaque. The composite joins the pixel-format group
    the program is in (``composite_overlay``): on a plain program the branch
    is forced to opaque yuv420p, on an alpha one (after the motion graphics)
    it keeps the group's format — either way a pixel-exact replacement
    inside the window and an untouched pass-through outside it.

    Returns the label holding the result (``in_label`` when the spans are
    empty: no window, no filter)."""
    spans = merge_frame_spans(spans)
    if not spans:
        return in_label
    expr = enable_expr(spans)
    if timeline:
        parts.append(f"[{in_label}]{with_enable(op, expr)}[{out_label}]")
        return out_label
    if fps is None:
        raise ValueError("a generic window needs the program rate (fps)")
    f0, f1 = spans[0][0], spans[-1][1]
    ov = composite_overlay(parts, in_label)
    flat = ",format=yuv420p" if ov == OVERLAY else ""
    parts.append(f"[{in_label}]split[{uid}a][{uid}b]")
    parts.append(f"[{uid}b]trim=start_frame={f0}:end_frame={f1},"
                 f"setpts=PTS-STARTPTS,{op}{flat}{repad(f0, fps)}"
                 f"[{uid}w]")
    parts.append(f"[{uid}a][{uid}w]{ov}:"
                 f"enable='{expr}'[{out_label}]")
    return out_label


def even_box(bbox, W, H):
    """(x, y, w, h) clamped to the frame with even origin and size (4:2:0
    chroma never straddles a crop edge), or None when nothing is left."""
    if bbox is None:
        return None
    x, y, w, h = (float(v) for v in bbox)
    x0 = max(0, int(math.floor(x / 2.0)) * 2)
    y0 = max(0, int(math.floor(y / 2.0)) * 2)
    x1 = min(int(W) // 2 * 2, int(math.ceil((x + w) / 2.0)) * 2)
    y1 = min(int(H) // 2 * 2, int(math.ceil((y + h) / 2.0)) * 2)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, y0, x1 - x0, y1 - y0


def rect_box(rect, W, H):
    """Fractional [x0, y0, x1, y1] -> even pixel (x, y, w, h)."""
    x0, y0, x1, y1 = (float(v) for v in rect)
    return even_box((x0 * W, y0 * H, (x1 - x0) * W, (y1 - y0) * H), W, H)


def region_fx(parts, in_label, out_label, mask_label, op, spans, *, uid,
              fps, bbox=None):
    """``op`` applied where ``mask_label`` is white, inside ``spans``.

    ``mask_label`` is a gray program mask (INTERFACES.md §4.5: W x H, program
    rate, the full program length; black is inert). The picture and the mask
    are trimmed to the window hull (and cropped to ``bbox`` when the region's
    box is known), the processed copy takes the mask as its ALPHA and is laid
    back over the untouched picture. Gray levels feather:
    out = base + (op(base) - base) * mask / 255, with chroma weighted by the
    2x2 mean of the mask. Outside the mask (alpha 0) and outside the window
    the picture is bit-exact — no pixel format or range conversion ever
    touches the main stream (a range-converting mask chain negotiated
    through ``maskedmerge`` and ``split`` re-ranged the whole program in an
    early draft; region_locality must be 0).

    The mask is consumed whatever happens; returns the result label."""
    spans = merge_frame_spans(spans)
    if not spans:
        parts.append(f"[{mask_label}]nullsink")
        return in_label
    f0, f1 = spans[0][0], spans[-1][1]
    expr = enable_expr(spans)
    trim = f"trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS"
    crop = (f",crop={bbox[2]}:{bbox[3]}:{bbox[0]}:{bbox[1]}"
            if bbox else "")
    ov = composite_overlay(parts, in_label)
    # alphamerge needs an alpha format: yuva420p on a plain program, the
    # group's own on an alpha one (composite_overlay)
    alpha_fmt = ",format=yuva420p" if ov == OVERLAY else ""
    parts.append(f"[{in_label}]split[{uid}a][{uid}b]")
    parts.append(f"[{uid}b]{trim}{crop},{op}{alpha_fmt}[{uid}p]")
    parts.append(f"[{mask_label}]{trim}{crop},format=gray[{uid}m]")
    parts.append(f"[{uid}p][{uid}m]alphamerge{repad(f0, fps, alpha=True)}"
                 f"[{uid}w]")
    xy = f"x={bbox[0]}:y={bbox[1]}:" if bbox else ""
    parts.append(f"[{uid}a][{uid}w]{ov.replace('overlay=', 'overlay=' + xy)}:"
                 f"enable='{expr}'[{out_label}]")
    return out_label


BLEND_MODES = ("normal", "screen", "addition", "multiply", "overlay",
               "softlight", "difference", "exclusion", "lighten", "darken",
               "hardlight", "average", "subtract")


def blend(parts, base_label, layer_label, out_label, spans, *, uid, mode,
          fps, opacity=1.0, bbox=None, mask_label=None):
    """Blend ``layer_label`` onto ``base_label`` inside ``spans``.

    bbox-local and in ``gbrp`` (ROADMAP 0B): both streams are trimmed to the
    window hull and cropped to ``bbox`` before the RGB conversion, so the
    expensive part runs on the box only. Semantics are ffmpeg ``blend`` with
    the BASE as the first input: out = base + (MODE(base, layer) - base) *
    opacity. An optional gray ``mask_label`` (full program, like every
    program mask) confines the result: it becomes the blended box's alpha,
    so outside the mask the picture is bit-exact.

    ``layer_label`` is a full-frame program-clock stream like the base (a
    split of the picture, a still looped to the program, ...). Both inputs
    and the mask are consumed. Returns the result label."""
    if mode not in BLEND_MODES:
        raise ValueError(f"blend mode {mode!r}; one of {BLEND_MODES}")
    spans = merge_frame_spans(spans)
    if not spans:
        parts.append(f"[{layer_label}]nullsink")
        if mask_label:
            parts.append(f"[{mask_label}]nullsink")
        return base_label
    f0, f1 = spans[0][0], spans[-1][1]
    expr = enable_expr(spans)
    trim = f"trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS"
    crop = (f",crop={bbox[2]}:{bbox[3]}:{bbox[0]}:{bbox[1]}"
            if bbox else "")
    op = max(0.0, min(1.0, float(opacity)))
    ov = composite_overlay(parts, base_label)
    plain = ov == OVERLAY
    parts.append(f"[{base_label}]split[{uid}a][{uid}b]")
    parts.append(f"[{uid}b]{trim}{crop},format=gbrp[{uid}bg]")
    parts.append(f"[{layer_label}]{trim}{crop},format=gbrp[{uid}l]")
    # back to the program's group: forced on a plain program, negotiated on
    # an alpha one (composite_overlay)
    if mask_label:
        parts.append(f"[{uid}bg][{uid}l]blend=all_mode={mode}"
                     f":all_opacity={op:.4f},"
                     f"format={'yuva420p' if plain else 'gbrap'}[{uid}x]")
        parts.append(f"[{mask_label}]{trim}{crop},format=gray[{uid}m]")
        parts.append(f"[{uid}x][{uid}m]alphamerge"
                     f"{repad(f0, fps, alpha=True)}[{uid}w]")
    else:
        parts.append(f"[{uid}bg][{uid}l]blend=all_mode={mode}"
                     f":all_opacity={op:.4f}"
                     f"{',format=yuv420p' if plain else ''}"
                     f"{repad(f0, fps)}[{uid}w]")
    xy = f"x={bbox[0]}:y={bbox[1]}:" if bbox else ""
    parts.append(f"[{uid}a][{uid}w]{ov.replace('overlay=', 'overlay=' + xy)}:"
                 f"enable='{expr}'[{out_label}]")
    return out_label


def track_matte(parts, fill_label, matte_label, out_label, *, uid,
                invert=False, matte_has_alpha=False):
    """``fill_label`` cut out by a matte: an alpha-carrying stream to lay
    over the picture (footage inside letters, letters as windows).

    matte_has_alpha: the matte is an RGBA clip (a glyph clip) whose ALPHA is
    the shape (``alphaextract``); otherwise it is a gray mask. invert gives
    the inverse (the fill everywhere except the shape). Returns the label of
    a yuva420p stream."""
    src = matte_label
    if matte_has_alpha:
        parts.append(f"[{matte_label}]alphaextract[{uid}ma]")
        src = f"{uid}ma"
    chain = "format=gray" + (",negate" if invert else "")
    parts.append(f"[{src}]{chain}[{uid}m]")
    parts.append(f"[{fill_label}]format=yuva420p[{uid}f]")
    parts.append(f"[{uid}f][{uid}m]alphamerge[{out_label}]")
    return out_label


def mask_upsample(parts, mask_label, out_label, *, W, H, uid, preview,
                  guide_label=None, radius=8, eps=0.0001):
    """A low-resolution mask brought to W x H.

    Finals with a guide (the program picture at full resolution): a joint
    ``guided`` filter on the guide's luma, so the edge snaps to hair and
    shoulders the 540p measurement could not see. Previews (and finals
    without a guide) scale bilinearly — a soft edge is the honest preview.
    The guide, when given, is consumed either way."""
    parts.append(f"[{mask_label}]scale={int(W)}:{int(H)}:flags=bilinear,"
                 f"format=gray[{uid}s]")
    if preview or not guide_label:
        if guide_label:
            parts.append(f"[{guide_label}]nullsink")
        parts.append(f"[{uid}s]null[{out_label}]")
        return out_label
    parts.append(f"[{guide_label}]scale={int(W)}:{int(H)},"
                 f"extractplanes=y[{uid}g]")
    parts.append(f"[{uid}s][{uid}g]guided=radius={int(radius)}"
                 f":eps={float(eps):.6f}:guidance=on[{out_label}]")
    return out_label


def mask_ops(parts, in_label, out_label, *, uid, invert=False, grow_px=0,
             feather_px=0.0):
    """A gray program mask reshaped: grown (``grow_px`` > 0, 3x3 dilation
    passes) or shrunk (< 0, erosion), feathered (``feather_px``: gaussian
    sigma = px / 2) and/or inverted. Wave 1 (INTERFACES_W1.md §1.3):
    inversion and morphology are consumer parameters, never mask labels.
    ``in_label`` is consumed; returns ``out_label`` (a straight copy when
    nothing is asked)."""
    chain = ["format=gray"]
    grow = int(round(float(grow_px or 0)))
    if grow:
        chain += ["dilation" if grow > 0 else "erosion"] * min(abs(grow), 32)
    if feather_px and float(feather_px) > 0:
        chain.append(f"gblur=sigma={float(feather_px) / 2.0:.3f}")
    if invert:
        chain.append("negate")
    parts.append(f"[{in_label}]{','.join(chain)}[{out_label}]")
    return out_label


# Filters mask_ops may emit (a compiler that calls it lists them in FILTERS).
MASK_OP_FILTERS = ("format", "dilation", "erosion", "gblur", "negate")


# Filters the primitives themselves emit (fxgraph checks the lane has them
# before any device that uses a primitive is compiled).
PRIMITIVE_FILTERS = ("split", "trim", "setpts", "settb", "tpad", "overlay",
                     "format",
                     "crop", "scale", "blend", "alphamerge", "alphaextract",
                     "guided", "extractplanes", "nullsink", "null", "negate")
