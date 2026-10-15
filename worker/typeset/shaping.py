"""Measured type geometry on every lane (ROADMAP A2; INTERFACES_W2.md §3).

The typesetter solves absolute boxes in Python and the page only draws
them, so the geometry here must be what Chromium will draw. Both sides use
HarfBuzz on the SAME font files (motion/fonts, fonts/): uharfbuzz when it
is installed, with the font's variations applied, kerning and ligatures on
(Chromium's defaults). Without uharfbuzz a FreeType fallback (Pillow) keeps
every lane working at slightly lower fidelity (kerning from the 'kern'
table only); ``ENGINE`` says which one measured.

Units: every measurement is in pixels at the requested size. y grows DOWN;
a run's origin is its baseline-left, so ``ink`` = [x0, y0, x1, y1] with y0
negative (above the baseline).
"""

import functools
import json
import math
import os
import threading

try:                                    # the shaping engine Chromium uses
    import uharfbuzz as _hb
    ENGINE = "harfbuzz"
except Exception:                       # noqa: BLE001 — FreeType fallback
    _hb = None
    ENGINE = "freetype"

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
FONTS_DIR = os.path.join(WORKER, "fonts")
MOTION_FONTS_DIR = os.path.join(WORKER, "motion", "fonts")
MANIFEST = os.path.join(MOTION_FONTS_DIR, "manifest.json")

_LOCK = threading.Lock()


# ── the face registry (the same list the pages load) ─────────────────────

def _weight_range(w):
    if isinstance(w, (int, float)):
        return (float(w), float(w))
    a, b = str(w).split()
    return (float(a), float(b))


def _stretch_range(s):
    if not s:
        return (100.0, 100.0)
    a, b = str(s).replace("%", "").split()
    return (float(a), float(b))


@functools.lru_cache(maxsize=1)
def faces():
    """[{family, path, url, weight: (lo, hi), style, stretch: (lo, hi),
    axes: {tag: (min, default, max)}}] for every installed face, in the
    order motion_engine registers them."""
    out = []
    try:
        import motion_engine as me
        for fam, fn, weight, style in me.FONT_FACES:
            p = os.path.join(FONTS_DIR, fn)
            if os.path.exists(p):
                out.append({"family": fam, "path": p, "url": f"fonts/{fn}",
                            "weight": _weight_range(weight), "style": style,
                            "stretch": (100.0, 100.0)})
        for fam, fn, weight, style, stretch in me.MOTION_FONT_FACES:
            p = os.path.join(MOTION_FONTS_DIR, fn)
            if os.path.exists(p):
                out.append({"family": fam, "path": p, "url": f"mfonts/{fn}",
                            "weight": _weight_range(weight), "style": style,
                            "stretch": _stretch_range(stretch)})
    except Exception:  # noqa: BLE001 — a lane without the engine module
        pass
    for f in out:
        f["axes"] = _axes(f["path"])
    return out


@functools.lru_cache(maxsize=None)
def _axes(path):
    """{tag: (min, default, max)} of a variable face's axes. HarfBuzz reads
    them itself (the engine every lane has); fontTools when it has not.
    An empty answer for a variable face would measure its default instance
    while the page sets the voice's weight and width."""
    if _hb is not None:
        try:
            face = _hb.Face(_hb.Blob.from_file_path(path))
            return {a.tag: (float(a.min_value), float(a.default_value),
                            float(a.max_value)) for a in face.axis_infos}
        except Exception:  # noqa: BLE001 — fontTools below
            pass
    try:
        from fontTools.ttLib import TTFont
        t = TTFont(path, lazy=True)
        if "fvar" not in t:
            return {}
        return {a.axisTag: (a.minValue, a.defaultValue, a.maxValue)
                for a in t["fvar"].axes}
    except Exception:  # noqa: BLE001
        return {}


@functools.lru_cache(maxsize=1)
def family_classes():
    """{family: [class tags]} from the manifest (condensed, serif, script,
    blackletter, ...)."""
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            return dict(json.load(f).get("families") or {})
    except (OSError, ValueError):
        return {}


def families():
    seen = []
    for f in faces():
        if f["family"] not in seen:
            seen.append(f["family"])
    return seen


class FontSpec:
    """One family at one weight, style and width: what a run asks for. The
    face is resolved like CSS font matching does (style first, then the
    closest weight range, then width), and ``css`` is exactly what the page
    writes so Chromium selects the same face with the same variations."""

    __slots__ = ("family", "weight", "style", "stretch", "extra", "face",
                 "variations")

    def __init__(self, family, weight=400, style="normal", stretch=100.0,
                 extra=None):
        self.family = family
        self.weight = float(weight)
        self.style = style
        self.stretch = float(stretch)
        self.extra = dict(extra or {})
        self.face = match(family, self.weight, style, self.stretch)
        if self.face is None:
            raise KeyError(f"no installed face for {family!r}")
        lo, hi = self.face["weight"]
        # a static face keeps its own weight (Chromium would synthesize a
        # bolder one past it: never)
        self.weight = min(max(self.weight, lo), hi)
        slo, shi = self.face["stretch"]
        self.stretch = min(max(self.stretch, slo), shi)
        var = {}
        axes = self.face["axes"]
        if "wght" in axes:
            var["wght"] = _clampax(self.weight, axes["wght"])
        if "wdth" in axes:
            var["wdth"] = _clampax(self.stretch, axes["wdth"])
        for tag, v in self.extra.items():
            if tag in axes:
                var[tag] = _clampax(float(v), axes[tag])
        self.variations = var

    def key(self):
        return (self.face["path"], tuple(sorted(self.variations.items())))

    def css(self):
        """The page's font properties for this spec (dict of CSS props)."""
        out = {"font-family": f"'{self.family}'",
               "font-weight": f"{self.weight:g}",
               "font-style": self.style}
        if self.face["stretch"] != (100.0, 100.0):
            out["font-stretch"] = f"{self.stretch:g}%"
        if self.variations:
            out["font-variation-settings"] = ", ".join(
                f"'{k}' {v:g}" for k, v in sorted(self.variations.items()))
        return out

    def as_dict(self):
        return {"family": self.family, "file": os.path.basename(
            self.face["path"]), "axes": {k: round(v, 2) for k, v in
                                         self.variations.items()},
                "style": self.style, "weight": round(self.weight, 1),
                "stretch": round(self.stretch, 1)}


def _clampax(v, ax):
    lo, _d, hi = ax
    return float(min(max(v, lo), hi))


def match(family, weight=400, style="normal", stretch=100.0):
    cands = [f for f in faces() if f["family"] == family]
    if not cands:
        return None
    same = [f for f in cands if f["style"] == style] or cands

    def dist(f):
        lo, hi = f["weight"]
        dw = 0.0 if lo <= weight <= hi else min(abs(weight - lo),
                                               abs(weight - hi))
        slo, shi = f["stretch"]
        ds = 0.0 if slo <= stretch <= shi else min(abs(stretch - slo),
                                                  abs(stretch - shi))
        return (dw, ds)
    return min(same, key=dist)


# ── shaping ──────────────────────────────────────────────────────────────

class Shaped:
    """One shaped string at one size: per-glyph positions and the ink."""

    __slots__ = ("text", "size", "advance", "glyphs", "ink", "ascender",
                 "descender", "cap", "xh", "chars")

    def __init__(self, text, size, advance, glyphs, ink, ascender,
                 descender, cap, xh, chars):
        self.text = text
        self.size = size
        self.advance = advance          # pen advance, tracking excluded
        self.glyphs = glyphs            # [(gid, cluster, x, x_adv)]
        self.ink = ink                  # [x0, y0, x1, y1] (y down)
        self.ascender = ascender
        self.descender = descender      # positive below the baseline
        self.cap = cap
        self.xh = xh
        self.chars = chars              # per CHARACTER: (x, adv) pen offsets

    def width(self, tracking_px=0.0):
        """Ink-to-ink width with tracking between characters (the page's
        letter-spacing adds after every character; the last one's is
        trailing space, never ink)."""
        n = max(0, len(self.text) - 1)
        return (self.ink[2] - self.ink[0]) + tracking_px * n


class _HbFont:
    def __init__(self, path, variations):
        blob = _hb.Blob.from_file_path(path)
        self.face = _hb.Face(blob)
        self.upem = self.face.upem
        self.font = _hb.Font(self.face)
        if variations:
            self.font.set_variations(dict(variations))
        ext = self.font.get_font_extents("ltr")
        self.asc = ext.ascender
        self.desc = -ext.descender
        self.cap = self._height("H") or self.asc * 0.7
        self.xh = self._height("x") or self.cap * 0.7

    def _height(self, ch):
        gid = self.font.get_nominal_glyph(ord(ch))
        if not gid:
            return None
        e = self.font.get_glyph_extents(gid)
        return float(e.y_bearing) if e else None

    def shape(self, text):
        buf = _hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        _hb.shape(self.font, buf, {"kern": True, "liga": True})
        out, x = [], 0.0
        x0 = y0 = math.inf
        x1 = y1 = -math.inf
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            gx = x + pos.x_offset
            e = self.font.get_glyph_extents(info.codepoint)
            if e and e.width:
                ex0 = gx + e.x_bearing
                ex1 = ex0 + e.width
                ey0 = -(pos.y_offset + e.y_bearing)      # y down
                ey1 = ey0 - e.height
                x0, x1 = min(x0, ex0, ex1), max(x1, ex0, ex1)
                y0, y1 = min(y0, ey0, ey1), max(y1, ey0, ey1)
            out.append((info.codepoint, info.cluster, gx, pos.x_advance))
            x += pos.x_advance
        if x0 is math.inf:
            x0, y0, x1, y1 = 0.0, 0.0, 0.0, 0.0
        return out, x, [x0, y0, x1, y1]


class _FtFont:
    """FreeType fallback (Pillow): variations, per-char advances with the
    'kern' table, ink from the rendered mask."""

    SIZE = 1000

    def __init__(self, path, variations):
        from PIL import ImageFont
        self.font = ImageFont.truetype(path, self.SIZE)
        if variations:
            try:
                axes = self.font.get_variation_axes()
                vals = []
                for ax in axes:
                    tag = {"Weight": "wght", "Width": "wdth"}.get(
                        ax.get("name"), ax.get("name"))
                    vals.append(variations.get(tag, ax.get("default")))
                self.font.set_variation_by_axes(vals)
            except Exception:  # noqa: BLE001
                pass
        self.upem = self.SIZE
        asc, desc = self.font.getmetrics()
        self.asc, self.desc = float(asc), float(desc)
        bb = self.font.getbbox("H", anchor="ls")
        self.cap = float(-bb[1]) if bb else self.asc * 0.7
        bb = self.font.getbbox("x", anchor="ls")
        self.xh = float(-bb[1]) if bb else self.cap * 0.7

    def shape(self, text):
        out, x = [], 0.0
        for i, ch in enumerate(text):
            adv = float(self.font.getlength(ch))
            if i + 1 < len(text):
                pair = float(self.font.getlength(ch + text[i + 1]))
                nxt = float(self.font.getlength(text[i + 1]))
                adv = pair - nxt
            out.append((ord(ch), i, x, adv))
            x += adv
        bb = self.font.getbbox(text, anchor="ls") if text.strip() else None
        ink = [float(v) for v in bb] if bb else [0.0, 0.0, 0.0, 0.0]
        return out, x, ink


_FONTS = {}


def _font(spec):
    key = spec.key()
    with _LOCK:
        f = _FONTS.get(key)
        if f is None:
            cls = _HbFont if _hb is not None else _FtFont
            f = cls(spec.face["path"], spec.variations)
            if len(_FONTS) > 256:
                _FONTS.clear()
            _FONTS[key] = f
    return f


_SHAPE_CACHE = {}
_SHAPE_MAX = 8192


def shape(text, spec, size_px):
    """Shaped ``text`` in ``spec`` at ``size_px`` (CSS px = the em)."""
    key = (text, spec.key(), round(float(size_px), 3))
    hit = _SHAPE_CACHE.get(key)
    if hit is not None:
        return hit
    f = _font(spec)
    glyphs, adv, ink = f.shape(text)
    s = float(size_px) / float(f.upem)
    chars = _char_positions(text, glyphs, s)
    out = Shaped(text, float(size_px), adv * s,
                 [(g, c, x * s, a * s) for g, c, x, a in glyphs],
                 [v * s for v in ink], f.asc * s, f.desc * s, f.cap * s,
                 f.xh * s, chars)
    if len(_SHAPE_CACHE) >= _SHAPE_MAX:
        _SHAPE_CACHE.clear()
    _SHAPE_CACHE[key] = out
    return out


def _char_positions(text, glyphs, s):
    """Per character (x, advance) at scale ``s``: a ligature's characters
    share its advance evenly (the page positions characters, not glyphs)."""
    n = len(text)
    starts = {}
    for _g, c, x, a in glyphs:
        if c not in starts:
            starts[c] = [x, 0.0]
        starts[c][1] += a
    out, keys = [None] * n, sorted(starts)
    for k_i, c in enumerate(keys):
        end = keys[k_i + 1] if k_i + 1 < len(keys) else n
        x, a = starts[c]
        span = max(1, end - c)
        for j in range(c, min(end, n)):
            out[j] = ((x + a * (j - c) / span) * s, a / span * s)
    last = (0.0, 0.0)
    for j in range(n):
        if out[j] is None:
            out[j] = (last[0] + last[1], 0.0)
        last = out[j]
    return out


def metrics(spec, size_px):
    """{ascender, descender, cap, xh} in px for a spec at a size."""
    f = _font(spec)
    s = float(size_px) / float(f.upem)
    return {"ascender": f.asc * s, "descender": f.desc * s,
            "cap": f.cap * s, "xh": f.xh * s}


def size_for_cap(spec, cap_px):
    """The font size whose cap height is ``cap_px``."""
    f = _font(spec)
    return float(cap_px) * float(f.upem) / max(1.0, f.cap)


def glyph_path(spec, gid, size_px, dx=0.0, dy=0.0):
    """SVG path data of glyph ``gid`` at ``size_px`` with its origin at
    (dx, dy) on the baseline (y down). HarfBuzz only; '' without it."""
    if _hb is None:
        return ""
    f = _font(spec)
    s = float(size_px) / float(f.upem)
    pen = _SvgPen(s, dx, dy)
    try:
        f.font.draw_glyph_with_pen(gid, pen)
    except Exception:  # noqa: BLE001
        return ""
    return pen.d()


class _SvgPen:
    def __init__(self, s, dx, dy):
        self.s, self.dx, self.dy, self.parts = s, dx, dy, []

    def _p(self, x, y):
        return f"{self.dx + x * self.s:.1f} {self.dy - y * self.s:.1f}"

    def moveTo(self, p):
        self.parts.append("M" + self._p(*p))

    def lineTo(self, p):
        self.parts.append("L" + self._p(*p))

    def qCurveTo(self, *pts):
        for i in range(0, len(pts) - 1, 2):
            if i + 1 < len(pts):
                self.parts.append("Q" + self._p(*pts[i]) + " " +
                                  self._p(*pts[i + 1]))

    def curveTo(self, a, b, c):
        self.parts.append("C" + self._p(*a) + " " + self._p(*b) + " " +
                          self._p(*c))

    def closePath(self):
        self.parts.append("Z")

    def endPath(self):
        pass

    def d(self):
        return "".join(self.parts)
