"""Picture finishes and live variants: the ``finish`` fx compiler (track 1E).

``fx_schemas/finish.py`` ``FinishFx`` items compile here (``finish:FINISH``)
at the world, screen, pre_captions or over_type stage. Every kind is a small
ffmpeg graph built from fast primitives (ROADMAP §2): per-value tables
(``lut``, ``lut2``, ``lutrgb``), precomputed maps multiplied in once
(subpixel stripes, phosphor masks, LED dots, halftone screens, blueprint
grids — PIL stills, never per-pixel expressions), separable blurs, and
``lenscorrection`` / ``rgbashift`` / ``edgedetect`` / ``pseudocolor``-style
colour maps.

  lcd            monitor re-photography: the picture on a subpixel grid
                 (slightly rotated, so it beats into a real moiré), lifted
                 blacks and a cool cast, bloom, barrel and edge falloff
  crt            scanlines and an aperture grille, convergence error, phosphor
                 persistence (lagfun), bloom, stronger barrel, rounded bezel
  led            LED dot matrix: one colour per dot, glowing dots, RGB split
  halftone       AM halftone: a 45-degree dot screen, anti-aliased threshold,
                 ink on paper (palette newsprint | red | blue | black)
  xerox          photocopy: contrast, toner noise, thickened darks, paper
  band_tear      horizontal bands torn sideways with RGB split (seeded)
  film           gate weave, film curve, halation, midtone-weighted grain
  thermal        an iron palette over luma; ``matte_weighted`` makes the
                 person hot (top 20 % of the palette) through 1A's matte
  xray           negative + local contrast + cyan + glow, clipped to the
                 person when ``matte_weighted``
  blueprint      Canny lines, white on blueprint blue, with a grid
  line_art       XDoG pencil lines on paper (registered to the footage)
  solarise       Sabattier tone reversal of the highlights
  posterise      flat colour levels
  kaleidoscope   four-way mirror with the picture kept in a centre diamond
  pixel_stretch  one column (or row) smeared across the frame
  night_vision   phosphor green, lifted shadows, grain, binocular vignette

Exactness: a finish replaces only the frames of its window; outside it (and
outside its region) the program is bit-exact (``window_composite``, the
fxprims pattern: a processed copy laid back over the untouched stream).
Every kind is deterministic (seeded noise, expressions on the frame index).

Variant STILLS (``variants.make_variant_set``) run these very graphs on one
frame, so a strobe of variants and a live finish look the same.
"""

import hashlib
import math
import os

import faultinject
import fxgraph
import fxprims
import regions

KINDS = ("lcd", "crt", "led", "halftone", "xerox", "band_tear", "film",
         "thermal", "xray", "blueprint", "line_art", "solarise", "posterise",
         "kaleidoscope", "pixel_stretch", "night_vision")
MATTE_KINDS = ("thermal", "xray")
PALETTES = {
    "thermal": ("iron", "rainbow", "white_hot", "black_hot"),
    "halftone": ("newsprint", "red", "blue", "black"),
    "night_vision": ("phosphor", "goggles"),
    "blueprint": ("blue", "dark"),
    "line_art": ("paper", "ink"),
}
DIRECTIONS = ("left", "right", "up", "down")

# Filters every finish graph may use (fxgraph checks them per lane before
# the device is compiled; a missing one omits the device with the reason).
FILTERS = ("split", "trim", "setpts", "tpad", "overlay", "format", "scale",
           "alphamerge", "lut", "lut2", "lutrgb", "gblur", "avgblur",
           "blend", "loop", "maskedmerge", "mergeplanes",
           "nullsink", "null", "extractplanes", "lenscorrection", "vignette",
           "rgbashift", "lagfun", "edgedetect", "noise", "crop", "pad",
           "fillborders", "hflip", "vflip", "xstack", "hstack", "eq",
           "erosion", "unsharp", "swaprect", "color")
# composites through fxprims-style overlays only: frames keep their tags
KEEPS_FRAME_TAGS = True

# Measured CPU ms per output frame: (fixed, per megapixel), fitted on
# 270x480 and 1080x1920 renders (ffmpeg 9.0, Apple M5 Pro, 4 threads,
# loaded host). The device cards carry the per-lane figures at the preview
# size; this is the compiler's own, size-aware estimate for the report.
MS_PER_FRAME = {
    "lcd": (5.2, 19.8),
    "crt": (6.5, 16.2),
    "led": (6.4, 19.8),
    "halftone": (2.7, 7.1),
    "xerox": (4.6, 12.6),
    "band_tear": (3.2, 7.5),
    "film": (3.8, 18.6),
    "thermal": (2.2, 5.9),
    "xray": (3.1, 10.0),
    "blueprint": (7.0, 10.5),
    "line_art": (7.4, 5.9),
    "solarise": (1.4, 4.8),
    "posterise": (1.7, 6.9),
    "kaleidoscope": (1.7, 6.9),
    "pixel_stretch": (1.4, 8.0),
    "night_vision": (4.3, 17.9),
}


# ── the window composite (shared with gradewin) ──────────────────────────

def ramp_weights(n, ramp_in=0, ramp_out=0):
    """{branch frame: weight} for the frames of an n-frame window that ramp.

    A ramp of N frames eases (smoothstep) through N steps strictly between
    0 and 1: ramp-in frame k (0-based) weighs s((k+1)/(N+1)), so the frame
    after the ramp is the first at full strength; ramp-out frame k of N
    weighs s((N-k)/(N+1)), so the frame after the window (``end``) is the
    first without the effect. A 3-frame ramp-out on a grade that ends on a
    drop is therefore fully off exactly ON the drop frame."""
    def s(x):
        return x * x * (3 - 2 * x)
    w = {}
    n = int(n)
    ri, ro = max(0, int(ramp_in or 0)), max(0, int(ramp_out or 0))
    for k in range(min(ri, n)):
        w[k] = s((k + 1) / (ri + 1))
    for k in range(min(ro, n)):
        f = n - ro + k
        if f < 0:
            continue
        v = s((ro - k) / (ro + 1))
        w[f] = min(w.get(f, 1.0), v)
    return w


def window_clock(fps):
    """The stamp every stream of a window carries: frame k at k / fps on
    the microsecond time base (the program's after a concat), computed by
    one formula so synced filters see EQUAL timestamps (window_composite)."""
    return f"settb=AVTB,setpts=N/{fxprims.fps3(fps):.3f}/TB"


def window_composite(ctx, label, spans, build, *, mask=None, opacity=1.0,
                     ramp_in=0, ramp_out=0, uid=None):
    """Replace the frames ``spans`` of ``label`` with ``build``'s picture.

    ``build(parts, in_label, uid) -> out_label`` gets the window's frames
    (trimmed to the hull, timestamps from 0, yuv420p, W x H) and returns
    its processed picture on the same clock, same size, any format.
    ``mask`` (a gray PROGRAM mask pad, consumed) weights the result per
    pixel, ``opacity`` (0..1) and the ramps per frame (``ramp_weights``).
    Outside the window, the mask and the alpha the result is bit-exact: the
    processed copy is laid over the untouched stream, joining the pixel-
    format group the program is in (fxprims.composite_overlay)."""
    parts, fps = ctx.parts, ctx.fps
    spans = fxprims.merge_frame_spans(spans)
    if not spans:
        if mask:
            parts.append(f"[{mask}]nullsink")
        return label
    f0, f1 = spans[0][0], spans[-1][1]
    n = f1 - f0
    u = uid or ctx.uid("fw")
    ov = fxprims.composite_overlay(parts, label)
    expr = fxprims.enable_expr(spans)
    # every stream of the window (the frames, a mask, the maps) is stamped
    # by ONE formula on ONE time base, so the filters that sync them pair
    # frame k with frame k: the program arrives in microseconds after a
    # concat, a looped still in 1/framerate, and 1/30 s against
    # microseconds disagrees by a rounding microsecond every third frame —
    # enough for framesync to emit both, doubling the window's frames
    # (measured: an LCD window played at half speed, test_w1e_render)
    trim = (f"trim=start_frame={f0}:end_frame={f1},"
            f"{window_clock(fps)}")
    parts.append(f"[{label}]split[{u}a][{u}b]")
    # ``scale`` (identity size) breaks the format group: the branch is
    # yuv420p whatever the program runs in, and the program keeps its own
    parts.append(f"[{u}b]{trim},scale,format=yuv420p[{u}i]")
    out = build(parts, f"{u}i", u)
    weights = ramp_weights(n, ramp_in, ramp_out)
    op = max(0.0, min(1.0, float(opacity if opacity is not None else 1.0)))
    if mask is None and op >= 0.9999 and not weights:
        parts.append(f"[{out}]format=yuv420p{fxprims.repad(f0, fps)}"
                     f"[{u}w]")
    else:
        if mask is not None:
            parts.append(f"[{mask}]{trim},format=gray[{u}m]")
            parts.append(f"[{out}]format=yuva420p[{u}c]")
            parts.append(f"[{u}c][{u}m]alphamerge[{u}x]")
        else:
            parts.append(f"[{out}]format=yuva420p[{u}x]")
        chain = []
        if op < 0.9999:
            chain.append(f"lut=c3='val*{op:.4f}'")
        for k in sorted(weights):
            chain.append(f"lut=c3='val*{weights[k]:.4f}':enable='eq(n,{k})'")
        parts.append(f"[{u}x]{','.join(chain) or 'null'}"
                     f"{fxprims.repad(f0, fps, alpha=True)}[{u}w]")
    parts.append(f"[{u}a][{u}w]{ov}:enable='{expr}'[{u}o]")
    return f"{u}o"


def region_pad(ctx, item):
    """A fresh gray program mask for ``item['region']``, or None.

    Static shapes read the still regions.prepare registered DIRECTLY, once
    per graph build: ``regions.pad`` keeps its one-pad-per-key record on the
    plan, and the renderer builds the graph twice when it measures dialogue
    loudness first (``_dialogue_gain``), so its second build got None and
    every static-region device was omitted (found by
    tests/test_w1e_render.py; reported to the round lead). Anchored shapes
    (glyph, face, card, keyed) go through regions.pad, whose masks are fresh
    on every build."""
    region = item.get("region")
    if not region:
        return None
    if regions.is_static(region):
        label = ctx.input_label(f"region:{item.get('id')}:region")
        if label is None:
            return None
        out = ctx.uid("rg")
        ctx.parts.append(f"[{label}]format=gray[{out}]")
        return out
    return regions.pad(ctx, item, region)


# ── graph builder ────────────────────────────────────────────────────────

class Graph:
    """Small helper the kind builders write through: fresh labels, chains,
    splits and the precomputed maps (``map(name)`` -> a label of W x H
    frames on the branch clock, one consumer each)."""

    def __init__(self, parts, uid, W, H, fps, n, maps=None, matte=None,
                 seed=0):
        self.parts, self.u = parts, uid
        self.W, self.H = int(W), int(H)
        self.fps, self.n = float(fps), int(n)
        self._maps = maps or {}
        self.matte = matte
        self.seed = int(seed)
        self.s = min(self.W, self.H) / 1080.0
        self._k = 0

    def t(self):
        self._k += 1
        return f"{self.u}k{self._k}"

    def add(self, ins, chain, out=None):
        out = out or self.t()
        if isinstance(ins, str):
            ins = [ins]
        self.parts.append("".join(f"[{i}]" for i in ins) + chain
                          + f"[{out}]")
        return out

    def split(self, label, n=2):
        outs = [self.t() for _ in range(n)]
        self.parts.append(f"[{label}]split={n}"
                          + "".join(f"[{o}]" for o in outs))
        return outs

    def map(self, name, yuv_mask=False):
        """The map's frames for the window. ``yuv_mask``: a gray map as a
        yuv420p stream whose THREE planes carry the weight (a maskedmerge
        mask that weights chroma too)."""
        get = self._maps.get(name)
        if get is None:
            raise ValueError(f"map {name} was never prepared")
        return get(self.W, self.H) if yuv_mask else get()


def table_expr(table):
    """A per-value lookup table (256 ints) as a ``lut`` expression."""
    import gradelut
    return gradelut._tree_expr([int(max(0, min(255, round(v))))
                                for v in table])


def _tbl(fn):
    return [fn(v / 255.0) * 255.0 for v in range(256)]


def _palette_tables(stops):
    """(r, g, b) 256-tables interpolating ``stops`` [(x, (r, g, b))]."""
    xs = [x for x, _ in stops]
    out = []
    for c in range(3):
        ys = [col[c] for _, col in stops]
        out.append([_interp(v / 255.0, xs, ys) for v in range(256)])
    return out


def _interp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            t = (x - xs[i - 1]) / max(1e-9, xs[i] - xs[i - 1])
            return ys[i - 1] + (ys[i] - ys[i - 1]) * t
    return ys[-1]


def _lutrgb(tables):
    r, g, b = (table_expr(t) for t in tables)
    return f"format=gbrp,lutrgb=r='{r}':g='{g}':b='{b}'"


def _gray_palette(g, label, stops):
    """A gray (luma, full range) label coloured through ``stops``. The
    three planes are copied with mergeplanes, never converted by swscale,
    so no range or matrix guess can touch the values."""
    a, b, c = g.split(label, 3)
    rgb = g.add([a, b, c], "mergeplanes=0x001020:gbrp")
    r, gg, bb = (table_expr(t) for t in _palette_tables(stops))
    return g.add(rgb, f"lutrgb=r='{r}':g='{gg}':b='{bb}'")


def _tv_to_full(x):
    """8-bit video-range luma -> 0..1."""
    return max(0.0, min(1.0, (x - 16.0) / 219.0))


def _luma(g, src):
    """Full-range gray luma of a yuv420p label."""
    y = g.add(src, "extractplanes=y")
    return g.add(y, "lut=c0='" + table_expr(
        [_tv_to_full(v) * 255 for v in range(256)]) + "'")


def _gain_curve(gain, lift=(0, 0, 0), cast=(1.0, 1.0, 1.0)):
    """Per-channel tables: x*gain through a soft shoulder, then a black
    lift and a per-channel cast (screens photographed: lifted, cool)."""
    tabs = []
    for c in range(3):
        def f(x, c=c):
            y = math.tanh(x * gain) / math.tanh(gain) if gain > 0 else x
            y = lift[c] / 255.0 + (1 - lift[c] / 255.0) * y * cast[c]
            return max(0.0, min(1.0, y))
        tabs.append(_tbl(f))
    return tabs


def _blur(g, label, sigma, out_fmt=None, gray=False):
    """A gaussian-like blur of ``sigma`` output pixels, at the cost the
    device budget allows (measured on 1080x1920, ms per frame):

      sigma < 2.5   two 3x3 / 5x5 box passes (avgblur, ~3 ms; gblur's
                    recursive passes cost 17 ms even at sigma 0.9)
      sigma >= 2.5  gblur at 1/2 or 1/4 resolution in YUV or gray, back up
                    bilinear (~5 ms; planar-RGB gblur at sigma 7: 53 ms):
                    a blur keeps no detail finer than itself."""
    k = 4 if sigma >= 6 else 2 if sigma >= 2.5 else 1
    fmt = "gray" if gray else "yuv420p"
    if k == 1:
        r = 1 if sigma < 1.4 else 2
        chain = f"format={fmt},avgblur=sizeX={r},avgblur=sizeX={r}"
    else:
        w = max(2, g.W // k // 2 * 2)
        h = max(2, g.H // k // 2 * 2)
        chain = (f"format={fmt},scale={w}:{h}:flags=area,"
                 f"gblur=sigma={sigma / k:.2f},"
                 f"scale={g.W}:{g.H}:flags=bilinear")
    if out_fmt:
        chain += f",format={out_fmt}"
    return g.add(label, chain)


def _bloom(g, label, sigma, opacity):
    """Light spilling from the highlights: a screen of the blurred luma
    over the picture's luma, chroma untouched (in YUV: 6 ms a frame where
    a planar-RGB screen costs 25). Returns yuv420p."""
    a, b = g.split(g.add(label, "format=yuv420p"))
    b = _blur(g, b, sigma)
    return g.add([a, b], f"blend=c0_mode=screen:c0_opacity={opacity:.3f}"
                         ":c1_mode=normal:c2_mode=normal")


def _darken(g, label, mapname):
    """The picture multiplied by a gray map (bezels, binocular masks): a
    maskedmerge toward black with the map's weight on all three planes."""
    x = g.add(label, "format=yuv420p")
    blk = g.add([], f"color=c=black:s={g.W}x{g.H}:r={g.fps:.3f},"
                    f"format=yuv420p,trim=end_frame={g.n},"
                    f"{window_clock(g.fps)}")
    m = g.map(mapname, yuv_mask=True)
    return g.add([blk, x, m], "maskedmerge")


def _barrel(g, label, k1, k2):
    # in YUV: lenscorrection on planar RGB costs twice as much (measured)
    return g.add(label, f"format=yuv420p,lenscorrection=k1={k1:.4f}"
                        f":k2={k2:.4f}:i=bilinear:fc=black")


def _vignette(g, label, angle):
    return g.add(label, f"format=yuv420p,vignette=angle={angle}")


def _multiply(g, label, mapname):
    m = g.map(mapname)
    a = g.add(label, "format=gbrp")
    m = g.add(m, "format=gbrp")
    return g.add([a, m], "blend=all_mode=multiply")


def _noise_expr_seed(g, salt):
    return (g.seed * 2654435761 + salt * 40503) % 2147483647


# ── kinds ────────────────────────────────────────────────────────────────

def _scale(it):
    return float(it.get("scale") or 1.0)


def _lcd(g, src, it):
    p = max(3.0, 4.6 * g.s * _scale(it))
    x = _blur(g, src, 0.22 * p)
    x = _multiply(g, x, "lcd_grid")
    # the grid passes a third of the light: the gain brings a natural
    # shot back to its own mean brightness (measured on V03: 86 -> 90; at
    # 3.1 it sat at 73 and a dark UI's whites at 161 of 251)
    x = g.add(x, "lutrgb=r='{}':g='{}':b='{}'".format(*(
        table_expr(t) for t in _gain_curve(4.5, lift=(6, 6, 10),
                                           cast=(0.965, 0.94, 1.0)))))
    x = _bloom(g, x, 7.0 * g.s, 0.22)
    x = g.add(x, "eq=saturation=1.18")
    # the camera's edge falloff: the frame's rim is a touch out of focus
    # (the grid map carries the lens vignette)
    a, b = g.split(x)
    b = _blur(g, b, 2.6 * g.s)
    m = g.map("edge_falloff", yuv_mask=True)
    x = g.add([a, b, m], "maskedmerge")
    return _barrel(g, x, 0.028, 0.010)


def _crt(g, src, it):
    x = g.add(src, "avgblur=sizeX=1:sizeY=1")
    sh = max(1, int(round(2.0 * g.s)))
    x = g.add(x, f"format=gbrp,rgbashift=rh=-{sh}:bh={sh}:edge=smear")
    x = _multiply(g, x, "crt_mask")
    x = g.add(x, "lutrgb=r='{}':g='{}':b='{}'".format(*(
        table_expr(t) for t in _gain_curve(2.0, lift=(4, 6, 5),
                                           cast=(1.0, 0.98, 0.96)))))
    x = _bloom(g, x, 10.0 * g.s, 0.32)
    x = g.add(x, "lagfun=decay=0.55")
    x = _darken(g, x, "crt_bezel")
    return _barrel(g, x, 0.055, 0.022)


def _led(g, src, it):
    p = max(4, int(round(7.0 * g.s * _scale(it))))
    cols = -(-g.W // p)
    rows = -(-g.H // p)
    sh = max(1, int(round(2.5 * g.s)))
    # convergence error on the picture's own edges (ref 14's red/blue
    # fringes), then one colour per LED, then the dots
    x = g.add(src, f"format=gbrp,rgbashift=rh=-{sh}:bh={sh}:edge=smear,"
                   f"scale={cols}:{rows}:flags=area,"
                   f"scale={cols * p}:{rows * p}:flags=neighbor,"
                   f"crop={g.W}:{g.H}:0:0")
    x = _multiply(g, x, "led_dots")
    x = g.add(x, "lutrgb=r='{}':g='{}':b='{}'".format(*(
        table_expr(t) for t in _gain_curve(3.3))))
    x = _bloom(g, x, 1.1 * p, 0.3)
    x = g.add(x, "eq=saturation=1.3")
    return _barrel(g, x, 0.03, 0.01)


_HALFTONE_INK = {"newsprint": ((26, 26, 30), (240, 234, 220)),
                 "black": ((10, 10, 12), (250, 250, 250)),
                 "red": ((196, 28, 40), (246, 238, 226)),
                 "blue": ((22, 58, 150), (240, 236, 226))}


def _halftone(g, src, it):
    c = max(5.0, 9.0 * g.s * _scale(it))
    y = _luma(g, src)
    y = _blur(g, y, 0.22 * c, gray=True)
    y = g.add(y, "lut=c0='" + table_expr(
        _tbl(lambda v: 0.06 + 0.9 * (v ** 1.1))) + "'")
    scr = g.add(g.map("halftone_screen"), "format=gray")
    k = 7.0
    t = g.add([y, scr], f"lut2=c0='clip(128+(x-y)*{k:.1f},0,255)'")
    ink, paper = _HALFTONE_INK.get(it.get("palette") or "newsprint",
                                   _HALFTONE_INK["newsprint"])
    return _gray_palette(g, t, [(0.0, ink), (1.0, paper)])


def _xerox(g, src, it):
    y = _luma(g, src)
    y = g.add(y, "lut=c0='" + table_expr(_tbl(
        lambda v: 1 / (1 + math.exp(-9.0 * (v - 0.52))))) + "'")
    a, b = g.split(y)
    hw, hh = max(2, g.W // 2), max(2, g.H // 2)
    # toner grain: half-resolution noise (a toner speck is two pixels)
    nz = g.add(b, f"scale={hw}:{hh}:flags=area,lut=c0=128,"
                  f"noise=c0s=34:c0f=t+u:all_seed={_noise_expr_seed(g, 3)},"
                  f"scale={g.W}:{g.H}:flags=bilinear")
    y = g.add([a, nz], "lut2=c0='clip(x+(y-128)*0.9,0,255)'")
    y = g.add(y, "erosion")
    y = _blur(g, y, 0.7 * g.s, gray=True)
    y = g.add(y, "lut=c0='" + table_expr(
        _tbl(lambda v: max(0.0, min(1.0, (v - 0.43) * 3.4 + 0.5)))) + "'")
    return _gray_palette(g, y, [(0.0, (20, 20, 22)), (0.55, (128, 126, 122)),
                                (1.0, (238, 235, 228))])


def _band_tear(g, src, it):
    W, H = g.W, g.H
    nb = 6
    rnd = _rng(g.seed, "bands")
    edges = sorted(rnd.random() for _ in range(nb * 2))
    bands = []
    for i in range(nb):
        y0 = int(edges[2 * i] * H) // 2 * 2
        y1 = max(y0 + 4, int(edges[2 * i + 1] * H) // 2 * 2)
        bands.append((y0, min(H, y1)))
    amp = 0.18 * W * float(it.get("scale") or 1.0)
    sh = max(2, int(round(4 * g.s)))
    outs = g.split(src, nb + 1)
    cur = outs[0]
    for i, (y0, y1) in enumerate(bands):
        h = y1 - y0
        if h < 2:
            g.parts.append(f"[{outs[i + 1]}]nullsink")
            continue
        b1, b2 = g.split(g.add(outs[i + 1], f"crop={W}:{h}:0:{y0}"))
        band = g.add([b1, b2], "hstack=inputs=2")
        if i % 2:
            band = g.add(band, f"format=gbrp,rgbashift=rh={sh}:bh=-{sh}"
                               ":edge=wrap")
        # the tear jumps every 2-3 frames; some frames hold still
        ph = 12.9898 * (i + 1) + 0.37 * g.seed % 97
        off = (f"{amp:.1f}*sin(floor(n/{2 + i % 2})*{ph:.4f}+{i * 78.233:.3f})"
               f"*gt(sin(floor(n/{2 + i % 2})*{ph * 1.7:.4f}+1.3),-0.35)")
        cur = g.add([cur, band], f"overlay=x='mod({off},{W})-{W}'"
                                 f":y={y0}:eval=frame:eof_action=pass")
    bw, bh = W // 4 // 2 * 2, max(4, H // 22 // 2 * 2)
    return g.add(cur, f"swaprect=w={bw}:h={bh}"
                      f":x1='mod(n*{37 + g.seed % 11},{W - bw})'"
                      f":y1='mod(n*{53 + g.seed % 7},{H - bh})'"
                      f":x2='mod(n*{71 + g.seed % 13}+{W // 3},{W - bw})'"
                      f":y2='mod(n*{29 + g.seed % 5}+{H // 2},{H - bh})'")


def _film(g, src, it):
    W, H = g.W, g.H
    amp = 1.5 * g.s
    pd = 8
    x = g.add(src, f"pad={W + 2 * pd}:{H + 2 * pd}:{pd}:{pd},"
                   f"fillborders=left={pd}:right={pd}:top={pd}:bottom={pd}"
                   f":mode=mirror,crop={W}:{H}"
                   f":x='{pd}+{amp:.2f}*(0.6*sin(n*0.61)+0.4*sin(n*1.37+1.2))'"
                   f":y='{pd}+{amp * 0.7:.2f}*(0.6*sin(n*0.83+0.4)"
                   f"+0.4*sin(n*1.91))'")

    def curve(c):
        def f(v):
            toe = 0.035 + (1 - 0.035 - 0.03) * (
                1 / (1 + math.exp(-6.2 * (v - 0.5))) - 0.0431) / 0.9138
            warm = (0.012 * (v ** 2), 0.0, -0.01 * (v ** 2))[c] + \
                (0.0, 0.0, 0.02 * (1 - v) ** 3)[c]
            return max(0.0, min(1.0, toe + warm))
        return _tbl(f)
    x = g.add(x, "format=gbrp,lutrgb=r='{}':g='{}':b='{}'".format(
        *(table_expr(curve(c)) for c in range(3))))
    a, b = g.split(x)
    b = g.add(b, "lutrgb=r='{}':g='{}':b='{}'".format(
        table_expr(_tbl(lambda v: max(0.0, v - 0.72) * 3.0)),
        table_expr(_tbl(lambda v: max(0.0, v - 0.72) * 0.9)),
        table_expr(_tbl(lambda v: max(0.0, v - 0.72) * 0.25))))
    b = _blur(g, b, 8.0 * g.s, out_fmt="gbrp")
    x = g.add([a, b], "blend=all_mode=screen:all_opacity=0.45")
    # (strength is the finish's opacity; the grain itself is one stock)
    gr = g.add([], f"color=c=black:s={W}x{H}:r={g.fps:.3f},"
               "format=yuv420p,lutyuv=y=128:u=128:v=128,"
               f"noise=c0s=24:c0f=t"
               f":all_seed={_noise_expr_seed(g, 7)},trim=end_frame={g.n},"
               f"{window_clock(g.fps)}")
    x = g.add(x, "format=yuv420p")
    # softlight on luma only (a 0.5-grey plate is neutral); "normal" keeps
    # the first input's chroma
    x = g.add([x, gr], "blend=c0_mode=softlight:c1_mode=normal:"
                       "c2_mode=normal")
    return _vignette(g, x, "PI/5.5")


_THERMAL = {
    "iron": [(0.0, (6, 2, 24)), (0.18, (40, 6, 110)), (0.38, (140, 10, 140)),
             (0.56, (222, 40, 60)), (0.72, (255, 120, 10)),
             (0.88, (255, 214, 40)), (1.0, (255, 255, 228))],
    "rainbow": [(0.0, (0, 0, 40)), (0.2, (0, 40, 220)), (0.4, (0, 210, 210)),
                (0.6, (40, 230, 40)), (0.78, (250, 230, 0)),
                (0.9, (255, 80, 0)), (1.0, (255, 255, 255))],
    "white_hot": [(0.0, (8, 8, 8)), (1.0, (250, 250, 250))],
    "black_hot": [(0.0, (250, 250, 250)), (1.0, (8, 8, 8))],
}


def _thermal(g, src, it):
    y = _luma(g, src)
    if g.matte is not None:
        m = g.add(g.matte, "format=gray")
        # background up to 62 % of the palette, the person 80-100 %: hot
        y = g.add([y, m], "lut2=c0='clip((1-y/255)*x*0.62+(y/255)*"
                          "(204+x*0.2),0,255)'")
    else:
        y = g.add(y, "lut=c0='" + table_expr(_tbl(
            lambda v: min(1.0, max(0.0, (v - 0.04) / 0.92) ** 0.85)))
            + "'")
    y = _blur(g, y, 1.1 * g.s, gray=True)
    pal = _THERMAL.get(it.get("palette") or "iron", _THERMAL["iron"])
    return _gray_palette(g, y, pal)


def _xray(g, src, it):
    y = _luma(g, src)
    y = g.add(y, "lut=c0='" + table_expr(_tbl(
        lambda v: (1.0 - v) ** 1.35)) + "'")
    a, b = g.split(y)
    b = _blur(g, b, 5.0 * g.s, gray=True)
    # local contrast: the bone-and-edge look of a radiograph
    y = g.add([a, b], "lut2=c0='clip(x+(x-y)*1.6,0,255)'")
    if g.matte is not None:
        m = g.add(g.matte, "format=gray")
        y = g.add([y, m], "lut2=c0='x*(0.30+0.70*y/255)'")
    x = _gray_palette(g, y, [(0.0, (2, 6, 14)), (0.45, (40, 120, 170)),
                             (0.8, (150, 225, 245)), (1.0, (240, 252, 255))])
    return _bloom(g, x, 6.0 * g.s, 0.42)


def _chroma_edges(g, src):
    """Gray W x H edges of the CHROMA planes (Canny on Cb and Cr at chroma
    resolution): the outline of a red jacket against a grey wall of the
    same brightness, which a luma-only drawing misses."""
    cu, cv = g.t(), g.t()
    g.parts.append(f"[{src}]format=yuv420p,extractplanes=u+v[{cu}][{cv}]")
    eu = g.add(cu, "gblur=sigma=0.8,edgedetect=low=0.05:high=0.14:"
                   "mode=wires")
    ev = g.add(cv, "gblur=sigma=0.8,edgedetect=low=0.05:high=0.14:"
                   "mode=wires")
    e = g.add([eu, ev], "blend=all_mode=lighten")
    return g.add(e, f"scale={g.W}:{g.H}:flags=bilinear,format=gray")


def _blueprint(g, src, it):
    hw, hh = max(2, g.W // 2), max(2, g.H // 2)
    a, b = g.split(src)
    y = _luma(g, a)
    # Canny at half resolution (a quarter of the cost, and the 2-px line a
    # drafting pen draws at full size), thickened once, eased back up;
    # chroma edges join it so colour-only outlines are drawn too
    e = g.add(y, f"scale={hw}:{hh}:flags=area,gblur=sigma=1.5,"
                 "edgedetect=low=0.035:high=0.11:mode=wires,dilation,"
                 f"scale={g.W}:{g.H}:flags=bicubic,format=gray")
    e = g.add([e, _chroma_edges(g, b)], "blend=all_mode=lighten")
    grid = g.add(g.map("blueprint_grid"), "format=gray")
    e = g.add([e, grid], "blend=all_mode=lighten")
    if (it.get("palette") or "blue") == "dark":
        stops = [(0.0, (8, 14, 26)), (1.0, (120, 220, 255))]
    else:
        stops = [(0.0, (18, 54, 124)), (0.18, (40, 86, 160)),
                 (0.6, (200, 222, 250)), (1.0, (246, 250, 255))]
    e = g.add(e, "vignette=angle=PI/5")
    return _gray_palette(g, e, stops)


def _line_art(g, src, it):
    a0, b0 = g.split(src)
    y = _luma(g, a0)
    hw, hh = max(2, g.W // 2 // 2 * 2), max(2, g.H // 2 // 2 * 2)
    lo, sh = g.split(y)
    a, b = g.split(g.add(lo, f"scale={hw}:{hh}:flags=area"))
    # XDoG at half resolution: proper gaussians at a quarter of the cost,
    # and a 2-px pen line once eased back up
    a = g.add(a, f"gblur=sigma={0.55 * g.s:.2f}")
    b = g.add(b, f"gblur=sigma={1.25 * g.s:.2f}")
    d = g.add([b, a], "blend=all_mode=subtract")
    lines = g.add(d, "lut=c0='" + table_expr(
        _tbl(lambda v: 1.0 - math.tanh(max(0.0, v * 255 - 0.8) * 0.42)))
        + f"',scale={g.W}:{g.H}:flags=bicubic")
    # colour-only outlines (same brightness, different hue) are inked too
    lines = g.add([lines, _chroma_edges(g, b0)],
                  "lut2=c0='min(x,255-y*0.85)'")
    # a whisper of the original tone under the lines (pencil shading)
    x = g.add([lines, sh], "lut2=c0='x*(0.80+0.20*y/255)'")
    if (it.get("palette") or "paper") == "ink":
        stops = [(0.0, (240, 240, 236)), (1.0, (12, 12, 16))]
    else:
        stops = [(0.0, (22, 22, 30)), (0.6, (150, 146, 140)),
                 (1.0, (245, 241, 233))]
    return _gray_palette(g, x, stops)


def _solarise(g, src, it):
    def f(v):
        return math.sin(math.pi * min(1.0, v ** 0.85)) ** 0.9
    t = table_expr(_tbl(f))
    x = g.add(src, f"format=gbrp,lutrgb=r='{t}':g='{t}':b='{t}'")
    return g.add(x, "format=yuv420p,eq=saturation=1.35:contrast=1.08")


def _posterise(g, src, it):
    levels = max(2, min(12, int(round(4 * _scale(it)))))
    t = table_expr(_tbl(lambda v: round(v * (levels - 1)) / (levels - 1)))
    x = _blur(g, src, 1.3 * g.s, out_fmt="gbrp")
    x = g.add(x, f"lutrgb=r='{t}':g='{t}':b='{t}'")
    return g.add(x, "format=yuv420p,eq=saturation=1.2")


def _kaleidoscope(g, src, it):
    W, H = g.W, g.H
    tw, th = max(2, W // 2), max(2, H // 2)
    z = max(0.5, min(2.0, _scale(it)))
    cw, ch = max(2, int(W / (2 * z))), max(2, int(H / (2 * z)))
    keep, tile = g.split(g.add(src, "format=gbrp"))
    t = g.add(tile, f"crop={min(W, cw)}:{min(H, ch)}:"
                    f"{max(0, (W - cw) // 2)}:{max(0, (H - ch) // 2)},"
                    f"scale={tw}:{th}")
    t1, t2, t3, t4 = g.split(t, 4)
    t2 = g.add(t2, "hflip")
    t3 = g.add(t3, "vflip")
    t4 = g.add(t4, "hflip,vflip")
    k = g.add([t1, t2, t3, t4], f"xstack=inputs=4:layout=0_0|{tw}_0|0_{th}"
                                f"|{tw}_{th},scale={W}:{H},format=gbrp")
    m = g.add(g.map("kaleido_diamond"), "format=gray")
    keep = g.add(keep, "format=gbrap")
    keep = g.add([keep, m], "alphamerge")
    return g.add([k, keep], "overlay=eof_action=pass:format=gbrp")


def _pixel_stretch(g, src, it):
    W, H = g.W, g.H
    o = it.get("origin")
    o = 0.5 if o is None else max(0.02, min(0.98, float(o)))
    d = it.get("direction") or "left"
    base, c = g.split(g.add(src, "format=gbrp"))
    if d in ("left", "right"):
        x0 = max(1, min(W - 1, int(round(o * W))))
        span = x0 if d == "left" else W - x0
        s = g.add(c, f"crop=1:{H}:{x0}:0,scale={span}:{H}:flags=neighbor")
        pos = "0:0" if d == "left" else f"{x0}:0"
    else:
        y0 = max(1, min(H - 1, int(round(o * H))))
        span = y0 if d == "up" else H - y0
        s = g.add(c, f"crop={W}:1:0:{y0},scale={W}:{span}:flags=neighbor")
        pos = "0:0" if d == "up" else f"0:{y0}"
    return g.add([base, s], f"overlay={pos}:eof_action=pass:format=gbrp")


def _night_vision(g, src, it):
    y = _luma(g, src)
    y = g.add(y, "lut=c0='" + table_expr(_tbl(
        lambda v: min(1.0, 1.15 * (v ** 0.62)))) + "'")
    y = g.add(y, f"noise=c0s=22:c0f=t:all_seed={_noise_expr_seed(g, 11)}")
    y = _blur(g, y, 0.9 * g.s, gray=True)
    if (it.get("palette") or "phosphor") != "goggles":
        y = g.add(y, "vignette=angle=PI/3.2")
    x = _gray_palette(g, y, [(0.0, (2, 10, 4)), (0.5, (40, 170, 60)),
                             (0.85, (150, 250, 140)), (1.0, (225, 255, 220))])
    x = _bloom(g, x, 5.0 * g.s, 0.3)
    if (it.get("palette") or "phosphor") == "goggles":
        return _darken(g, x, "goggles")
    return x


BUILDERS = {"lcd": _lcd, "crt": _crt, "led": _led, "halftone": _halftone,
            "xerox": _xerox, "band_tear": _band_tear, "film": _film,
            "thermal": _thermal, "xray": _xray, "blueprint": _blueprint,
            "line_art": _line_art, "solarise": _solarise,
            "posterise": _posterise, "kaleidoscope": _kaleidoscope,
            "pixel_stretch": _pixel_stretch, "night_vision": _night_vision}

# Precomputed maps each kind multiplies in (built once per size in prepare).
KIND_MAPS = {"lcd": ("lcd_grid", "edge_falloff"),
             "crt": ("crt_mask", "crt_bezel"), "led": ("led_dots",),
             "halftone": ("halftone_screen",),
             "blueprint": ("blueprint_grid",),
             "kaleidoscope": ("kaleido_diamond",),
             "night_vision": ("goggles",)}


def maps_for(item):
    names = KIND_MAPS.get(item.get("kind"), ())
    if item.get("kind") == "night_vision" and \
            (item.get("palette") or "phosphor") != "goggles":
        return ()
    return names


# ── maps (PIL / numpy stills) ────────────────────────────────────────────

def _rng(seed, salt):
    import random
    h = hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def build_map(name, W, H, scale=1.0):
    """A PIL image for map ``name`` at W x H (deterministic)."""
    import numpy as np
    from PIL import Image
    W, H = int(W), int(H)
    s = min(W, H) / 1080.0
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    if name == "lcd_grid":
        p = max(3.0, 4.6 * s * scale)
        ss = 3
        hy, hx = np.mgrid[0:H * ss, 0:W * ss].astype(np.float64) / ss
        a = math.radians(0.7)
        u = hx * math.cos(a) + hy * math.sin(a)
        v = -hx * math.sin(a) + hy * math.cos(a)
        fu = (u / p) % 1.0
        fv = (v / p) % 1.0
        sub = np.floor(fu * 3).astype(int)
        inner = ((fu * 3) % 1.0 > 0.12) & ((fu * 3) % 1.0 < 0.9) & \
            (fv < 0.84)
        # each subpixel leaks a little into the other two channels; the
        # leaks balance (every channel sums to 365) so white stays white
        # and the cast is the gain curve's choice, not the grid's
        lit = np.array([[255, 52, 58], [58, 255, 52], [52, 58, 255]],
                       float)
        img = lit[sub] * inner[..., None] + 10.0 * (~inner)[..., None]
        img = img.reshape(H, ss, W, ss, 3).mean(axis=(1, 3))
        # the lens vignette of the camera that photographed the screen
        r = np.hypot((xx / W - 0.5) / 0.5, (yy / H - 0.5) / 0.5) / \
            math.sqrt(2)
        img = img * (1.0 - 0.42 * np.clip(r, 0, 1) ** 2.2)[..., None]
        return Image.fromarray(np.clip(img, 0, 255).astype("uint8"), "RGB")
    if name == "edge_falloff":
        r = np.hypot((xx / W - 0.5) / 0.5, (yy / H - 0.5) / 0.5) / math.sqrt(2)
        m = np.clip((r - 0.55) / 0.45, 0, 1) ** 1.6
        return Image.fromarray((m * 255).astype("uint8"), "L")
    if name == "crt_mask":
        scan = max(2.0, 4.0 * s * scale)
        line = 0.30 + 0.70 * np.cos(np.pi * ((yy % scan) / scan - 0.5)) ** 2
        trip = np.floor(xx % 3).astype(int)
        col = np.array([[255, 150, 140], [140, 255, 150], [150, 150, 255]],
                       float)
        img = col[trip] * line[..., None]
        return Image.fromarray(np.clip(img, 0, 255).astype("uint8"), "RGB")
    if name == "crt_bezel":
        nx, ny = (xx / W - 0.5) * 2, (yy / H - 0.5) * 2
        k = 8.0
        d = (np.abs(nx) ** k + np.abs(ny) ** k) ** (1 / k)
        m = np.clip((1.0 - d) / 0.05, 0, 1) * (1 - 0.35 * (nx ** 2 + ny ** 2)
                                               / 2)
        return Image.fromarray((m * 255).astype("uint8"), "L")
    if name == "led_dots":
        p = max(4, int(round(7.0 * s * scale)))
        fx = (xx % p) / p - 0.5
        fy = (yy % p) / p - 0.5
        rr = np.hypot(fx, fy)
        dot = np.clip((0.40 - rr) / 0.07, 0, 1)
        m = 0.05 + 0.95 * dot
        img = np.repeat((m * 255)[..., None], 3, axis=2)
        return Image.fromarray(img.astype("uint8"), "RGB")
    if name == "halftone_screen":
        c = max(5.0, 9.0 * s * scale)
        a = math.radians(45)
        u = (xx * math.cos(a) + yy * math.sin(a)) / c
        v = (-xx * math.sin(a) + yy * math.cos(a)) / c
        scr = 0.5 + 0.25 * (np.cos(2 * np.pi * u) + np.cos(2 * np.pi * v))
        return Image.fromarray((np.clip(scr, 0, 1) * 255).astype("uint8"),
                               "L")
    if name == "blueprint_grid":
        step = max(8.0, 36.0 * s * scale)
        fine = ((xx % step) < 1.0) | ((yy % step) < 1.0)
        bold = ((xx % (step * 5)) < 1.6) | ((yy % (step * 5)) < 1.6)
        m = np.where(bold, 46, np.where(fine, 24, 0))
        return Image.fromarray(m.astype("uint8"), "L")
    if name == "kaleido_diamond":
        nx = np.abs(xx / W - 0.5) / 0.5
        ny = np.abs(yy / H - 0.5) / 0.5
        d = nx + ny
        edge = 0.92 * min(1.0, max(0.4, scale))
        m = np.clip((edge - d) / 0.012, 0, 1)
        return Image.fromarray((m * 255).astype("uint8"), "L")
    if name == "goggles":
        r = 0.30 * min(W, H) / max(W, H)
        c1 = np.hypot((xx - 0.32 * W) / max(W, H), (yy - 0.5 * H) / max(W, H))
        c2 = np.hypot((xx - 0.68 * W) / max(W, H), (yy - 0.5 * H) / max(W, H))
        d = np.minimum(c1, c2) / max(r, 1e-6)
        m = np.clip((1.0 - d) / 0.08, 0, 1)
        return Image.fromarray((m * 255).astype("uint8"), "L")
    raise ValueError(f"unknown map {name}")


def map_path(workdir, name, W, H, scale=1.0):
    """Write (once) and return the PNG of a map for this size."""
    key = hashlib.sha256(f"{name}|{W}|{H}|{scale:.4f}|v5".encode()) \
        .hexdigest()[:16]
    path = os.path.join(workdir, f"fxmap_{name}_{key}.png")
    if not os.path.exists(path):
        tmp = path + ".tmp.png"
        build_map(name, W, H, scale).save(tmp)
        os.replace(tmp, path)
    return path


# ── the compiler ─────────────────────────────────────────────────────────

def _seed(item):
    if item.get("seed") is not None:
        return int(item["seed"])
    return int(hashlib.sha256(str(item.get("id")).encode()).hexdigest()[:8],
               16) % 1_000_003


def _state(plan):
    return plan.__dict__.setdefault("_finish_state", {})


def _map_input_key(item, name):
    return f"finish:{item.get('id')}:{name}"


class _Finish:
    FILTERS = FILTERS
    KEEPS_FRAME_TAGS = KEEPS_FRAME_TAGS

    def prepare(self, plan, item):
        kind = item.get("kind")
        if kind not in BUILDERS:
            return ("omit", f"unknown finish kind {kind!r}")
        if not plan.W or not plan.H:
            return ("omit", "no geometry for the finish")
        st = {"rung": "full", "reason": "ok"}
        if item.get("region"):
            got = regions.prepare(plan, item, item["region"])
            if got:
                return got
        names = maps_for(item)
        if names:
            if faultinject.fault("oom:fx_precompute"):
                raise MemoryError("the finish's map precompute was "
                                  "OOM-killed")
            if not plan.workdir:
                return ("omit", "no workdir for the finish's maps")
            for name in names:
                path = map_path(plan.workdir, name, plan.W, plan.H,
                                _scale(item))
                plan.add_input(_map_input_key(item, name), ["-i", path])
        if item.get("matte_weighted") and kind in MATTE_KINDS:
            info = fxgraph.request_matte(plan, item, "person",
                                         _space(item))
            if info.get("status") == "absent":
                st = {"rung": "reduced",
                      "reason": f"{kind} unweighted: "
                                + (info.get("reason") or "no person matte")}
            st["matte"] = info.get("status") != "absent"
        _state(plan)[item.get("id")] = st
        if st["rung"] != "full":
            return (st["rung"], st["reason"])
        return None

    def cost_ms(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        fix, per = MS_PER_FRAME.get(item.get("kind"), (3.0, 15.0))
        return 150.0 + (f1 - f0) * (fix + mpx * per)

    def stage(self, ctx, items):
        label = ctx.label
        for it in items:
            label = self._emit(ctx, label, it)
        return label

    def _emit(self, ctx, label, it):
        st = _state(ctx.plan).get(it.get("id"), {})
        f0, f1 = ctx.frames(it)
        n = f1 - f0
        u = ctx.uid("fi")
        names = maps_for(it)
        maps = {}
        for name in names:
            src = ctx.input_label(_map_input_key(it, name))
            if src is None:
                raise ValueError(f"map {name} was never bound")
            maps[name] = _map_getter(ctx.parts, src, n, ctx.fps,
                                     f"{u}{name[:3]}")
        matte = None
        if it.get("matte_weighted") and it.get("kind") in MATTE_KINDS \
                and st.get("matte"):
            raw = ctx.mask("person")
            if raw is not None:
                matte = f"{u}mt"
                ctx.parts.append(f"[{raw}]trim=start_frame={f0}:end_frame="
                                 f"{f1},{window_clock(ctx.fps)},format=gray"
                                 f"[{matte}]")
            else:
                ctx.note(it, "reduced", f"{it.get('kind')} unweighted: the "
                         "person matte was not built on this render")
        mask = None
        if it.get("region"):
            mask = region_pad(ctx, it)
            if mask is None:
                raise ValueError("the finish's region could not be built")
        builder = BUILDERS[it["kind"]]

        def build(parts, src, uid):
            g = Graph(parts, uid, ctx.W, ctx.H, ctx.fps, n, maps=maps,
                      matte=matte, seed=_seed(it))
            return builder(g, src, it)
        return window_composite(
            ctx, label, [(f0, f1)], build, mask=mask,
            opacity=float(it.get("strength") if it.get("strength")
                          is not None else 1.0),
            ramp_in=it.get("ramp_in_f") or 0,
            ramp_out=it.get("ramp_out_f") or 0, uid=u)


def _space(item):
    return "world" if item.get("stage") == "world" else "screen"


def _map_getter(parts, src, n, fps, tag):
    """A one-shot getter for a map input: the still repeated for the
    window's n frames on the branch clock (``loop`` repeats the processed
    frame; the PNG is decoded and prepared once)."""
    tail = (f"loop=loop=-1:size=1:start=0,trim=end_frame={n},"
            f"{window_clock(fps)}")

    def get(W=None, H=None):
        out = f"{tag}m"
        if W is None:
            parts.append(f"[{src}]{tail}[{out}]")
            return out
        cw, ch = (int(W) + 1) // 2, (int(H) + 1) // 2
        parts.append(f"[{src}]format=gray,split=3[{tag}a][{tag}b][{tag}c]")
        parts.append(f"[{tag}b]scale={cw}:{ch}:flags=area[{tag}b2]")
        parts.append(f"[{tag}c]scale={cw}:{ch}:flags=area[{tag}c2]")
        parts.append(f"[{tag}a][{tag}b2][{tag}c2]mergeplanes=0x001020:"
                     f"yuv420p,{tail}[{out}]")
        return out
    return get


FINISH = _Finish()


# ── stills: the same graphs on one frame (the Variant Forge) ─────────────

def still_graph(kind, W, H, item=None, workdir=None):
    """(filter_complex, map_paths) running finish ``kind`` on input 0 (one
    W x H frame) -> [out]. Map stills are inputs 1..n in ``map_paths``
    order. Used by variants.make_variant_set on the agent lane."""
    item = dict(item or {}, kind=kind)
    parts = []
    names = maps_for(item)
    paths = [map_path(workdir, nm, W, H, _scale(item)) for nm in names]
    maps = {nm: _map_getter(parts, f"{i + 1}:v", 1, 25.0, f"s{i}")
            for i, nm in enumerate(names)}
    parts.append(f"[0:v]scale={W}:{H},format=yuv420p,setsar=1[src]")
    g = Graph(parts, "sv", W, H, 25.0, 1, maps=maps, matte=None,
              seed=_seed(item))
    out = BUILDERS[kind](g, "src", item)
    parts.append(f"[{out}]format=rgb24[out]")
    return ";".join(parts), paths
