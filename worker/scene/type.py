"""T2: the type engine's layout half. HarfBuzz shapes; the runtime never does layout, so every
tier draws the same glyphs at the same places.

A voice names a font file (``font``) or a family from the typeset registry (``family`` with
``weight``, ``style``, ``stretch``), plus ``px`` in output-frame pixels, ``track`` in percent of px
and variable axes in ``var``. Kerning is on; ligatures are off when tracked, as CSS does.

``lay_line`` places runs of several voices on one baseline and returns ``word`` items, with the
reflow rules of DESIGN 4.6: pre_laid, reflow_centre, ghost, justify_on_fill.
"""
import math
import os

import numpy as np

try:
    import uharfbuzz as hb
except Exception:      # noqa: BLE001 - reported by scene.eligible, never at import
    hb = None

WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FALLBACK = ("InterDisplay-Bold.ttf", "Montserrat-Bold.ttf", "Inter-Variable.ttf", "NotoSerifDisplay-Variable.ttf",
            "NotoSansArabic-Bold.ttf", "NotoSansArabic-Regular.ttf", "NotoNaskhArabic-Bold.ttf", "NotoSans-Bold.ttf")
# Type tiers as a share of frame height (ATLAS 1.4, 8.4): vertical and landscape captions differ by two.
TIERS = {"vertical": {"micro": 0.014, "caption": 0.030, "display": 0.060},
         "landscape": {"micro": 0.022, "caption": 0.058, "display": 0.115}}


def font_dirs():
    return [d for d in (os.environ.get("VALMERA_FONTS") or os.path.join(WORKER, "fonts"),
                        os.path.join(WORKER, "motion", "fonts"),
                        os.environ.get("VALMERA_SCRIPT_FONTS") or "/usr/share/fonts/truetype/noto") if d]


def find_font(name):
    if name and os.path.isabs(name) and os.path.exists(name):
        return name
    for d in font_dirs():
        p = os.path.join(d, name or "")
        if name and os.path.exists(p):
            return p
    raise FileNotFoundError(name)


def voice_font(v):
    """(path, variations) of a voice. A family goes through the typeset registry, the one face list
    the pages and the typesetter already share."""
    if v.get("font"):
        return find_font(v["font"]), v.get("var")
    if v.get("family"):
        from typeset import shaping
        spec = shaping.match(v["family"], int(v.get("weight", 400)), v.get("style", "normal"), float(v.get("stretch", 100.0)))
        if spec is None:
            raise FileNotFoundError(v["family"])
        var = dict(getattr(spec, "variations", None) or {})
        var.update(v.get("var") or {})
        return spec.path, (var or None)
    raise FileNotFoundError("voice names no font")


def tier_px(tier, H, W=None):
    shape = "landscape" if W and W > H else "vertical"
    return TIERS[shape][tier] * H


class Pen:
    """Collects an outline from HarfBuzz, flattened to lines at a given pixel scale."""

    def __init__(self, scale):
        self.scale, self.out, self.start, self.p = scale, [], None, None

    def moveTo(self, p):
        self.closePath()
        self.start = self.p = p

    def lineTo(self, p):
        self.out.append((*self.p, *p))
        self.p = p

    def qCurveTo(self, c, p):
        a = self.p
        dev = math.hypot(a[0] - 2 * c[0] + p[0], a[1] - 2 * c[1] + p[1]) * self.scale
        n = max(2, min(40, int(math.ceil(math.sqrt(dev) * 1.6))))
        q = a
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            r = (u * u * a[0] + 2 * u * t * c[0] + t * t * p[0], u * u * a[1] + 2 * u * t * c[1] + t * t * p[1])
            self.out.append((*q, *r))
            q = r
        self.p = p

    def curveTo(self, c1, c2, p):
        a = self.p
        dev = max(math.hypot(a[0] - 2 * c1[0] + c2[0], a[1] - 2 * c1[1] + c2[1]),
                  math.hypot(c1[0] - 2 * c2[0] + p[0], c1[1] - 2 * c2[1] + p[1])) * self.scale
        n = max(3, min(48, int(math.ceil(math.sqrt(dev) * 2.2))))
        q = a
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            r = tuple(u * u * u * a[k] + 3 * u * u * t * c1[k] + 3 * u * t * t * c2[k] + t * t * t * p[k] for k in (0, 1))
            self.out.append((*q, *r))
            q = r
        self.p = p

    def closePath(self):
        if self.p is not None and self.start is not None and self.p != self.start:
            self.out.append((*self.p, *self.start))
        self.p = self.start = None

    def endPath(self):
        self.closePath()


class Face:
    """One font file: shaping and outlines. Pure, cached."""
    _all = {}

    @classmethod
    def get(cls, font, var=None):
        path = find_font(font)
        k = (path, tuple(sorted((var or {}).items())))
        if k not in cls._all:
            cls._all[k] = cls(path, var)
        return cls._all[k]

    @classmethod
    def of(cls, voice):
        path, var = voice_font(voice)
        return cls.get(path, var)

    def __init__(self, path, var):
        if hb is None:
            raise RuntimeError("uharfbuzz is not installed")
        self.path = path
        self.face = hb.Face(hb.Blob.from_file_path(path))
        self.font = hb.Font(self.face)
        if var:
            self.font.set_variations(var)
        self.upem = float(self.face.upem)
        self.glyphs = {}

    def shape(self, text, px, track=0.0):
        """Glyph ids and pen positions, in font units, left to right on the page. track is percent
        of px per character. A right-to-left run comes back in visual order, so x always rises."""
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        feats = {"kern": True, "liga": track == 0, "clig": track == 0}
        hb.shape(self.font, buf, feats)
        out, x = [], 0.0
        tr = track / 100.0 * self.upem
        infos, poss = buf.glyph_infos, buf.glyph_positions
        for i, (g, p) in enumerate(zip(infos, poss)):
            out.append((g.codepoint, x + p.x_offset, p.y_offset))
            x += p.x_advance + (tr if i + 1 < len(infos) else 0.0)
        return out, x

    def lines(self, gid, px):
        k = (gid, round(px, 2))
        if k not in self.glyphs:
            s = px / self.upem
            pen = Pen(s)
            self.font.draw_glyph_with_pen(gid, pen)
            pen.closePath()
            self.glyphs[k] = np.array(pen.out, np.float64).reshape(-1, 4) * s
        return self.glyphs[k]

    def missing(self, text):
        """True when shaping this text lands on .notdef."""
        return any(g[0] == 0 for g in self.shape(text, 100)[0])

    def measure(self, text, px, track=0.0):
        """Advance width in pixels at this size."""
        return self.shape(text, px, track)[1] * px / self.upem

    def metrics(self, px):
        """(ascender, descender, cap height) in pixels, y up positive for the first and third."""
        e = self.font.get_font_extents("ltr")
        s = px / self.upem
        cap = 0.0
        g = self.font.get_nominal_glyph(ord("H"))
        if g:
            ln = self.lines(g, px)
            if len(ln):
                cap = float(ln[:, [1, 3]].max())
        return e.ascender * s, e.descender * s, cap

    def outline(self, text, px, track=0.0):
        """Every edge of the shaped text as Nx4 lines in pixels, y down, origin at the baseline's
        left end, plus the advance."""
        glyphs, adv = self.shape(text, px, track)
        s = px / self.upem
        parts = []
        for gid, x, y in glyphs:
            ln = self.lines(gid, px)
            if len(ln):
                ln = ln.copy()
                ln[:, [0, 2]] += x * s
                ln[:, [1, 3]] = -(ln[:, [1, 3]] + y * s)
                parts.append(ln)
        return (np.vstack(parts) if parts else np.zeros((0, 4))), adv * s

    def quads(self, text, px, track=0.0):
        """Glyph quads for a tier that draws glyph by glyph: (gid, x0, y0, x1, y1) in pixels, y down,
        from the baseline's left end. Layout happens here and nowhere else."""
        glyphs, _ = self.shape(text, px, track)
        s = px / self.upem
        out = []
        for gid, x, y in glyphs:
            ln = self.lines(gid, px)
            if not len(ln):
                continue
            out.append((gid, float(ln[:, [0, 2]].min() + x * s), float(-(ln[:, [1, 3]].max() + y * s)),
                        float(ln[:, [0, 2]].max() + x * s), float(-(ln[:, [1, 3]].min() + y * s))))
        return out


def is_joined_script(text):
    return any(0x0590 <= ord(c) <= 0x08FF or 0xFB1D <= ord(c) <= 0xFDFF or 0xFE70 <= ord(c) <= 0xFEFF for c in text)


_measure = {}


def compiler_advance(text, v):
    """The width the Score compiler placed this word by: unkerned advances from Pillow at a whole
    pixel size. The engine draws it shaped and kerned, so it sits in the middle of that slot. Without
    this, a display line the compiler centred lands left by half its kerning. A joined script is
    shaped there too, so there is nothing to make up. A voice with ``shaped`` true was placed by
    this module's own measure and needs no correction."""
    px = float(v.get("px", 58))
    if v.get("shaped") or is_joined_script(text) or not v.get("font"):
        return Face.of(v).measure(text, px, float(v.get("track", 0)) if v.get("shaped") else 0.0)
    from PIL import ImageFont
    key = (v.get("font"), int(round(px)))
    if key not in _measure:
        _measure[key] = ImageFont.truetype(find_font(key[0]), key[1])
    f = _measure[key]
    tr = float(v.get("track", 0)) * px / 100.0
    return sum(f.getlength(c) + tr for c in text) - tr


# ------------------------------------------------------------------ layout

def lay_line(runs, voices, frame, y, x=0.5, align="centre", gap_em=0.28, rule="pre_laid", f=(0, 1), ons=None,
             ghost=0.26, ink="#FFFFFF", fill=None, id_="line", role="cap", z=None, extra=None):
    """One line of type from runs of words, each run in its own voice, all on one baseline.

    runs: [(voice key, [word, ...]), ...] in reading order. frame: (W, H). y: baseline, frame
    fraction. x: the line's anchor (its centre, left or right edge by ``align``). ons: the programme
    frame each word lands, in reading order; absent means every word is there from f[0].

    rule:
      pre_laid         every word holds the place it has in the finished line, from the start
      reflow_centre    the line is re-centred each time a word lands, so it grows from its middle
      ghost            pre_laid, and unspoken words wait at ``ghost`` opacity
      justify_on_fill  pre_laid until the last word, then the gaps stretch so the line fills ``fill``
                       (a frame-width fraction)
    Returns ``word`` items (voices must carry ``shaped: true`` so the runtime trusts these places).
    A right-to-left line is placed right to left: the first word spoken sits at the right.
    """
    W, H = frame
    a, b = f
    words = [(vk, w) for vk, ws in runs for w in ws]
    if not words:
        return []
    faces = {vk: Face.of(voices[vk]) for vk, _ in words}
    wid = [faces[vk].measure(w, float(voices[vk].get("px", 58)), float(voices[vk].get("track", 0))) for vk, w in words]
    gap = gap_em * max(float(voices[vk].get("px", 58)) for vk, _ in words)
    rtl = is_joined_script("".join(w for _, w in words))
    ons = list(ons) if ons is not None else [a] * len(words)

    def places(n, gap_):
        """Left edges of the first n words (reading order), for a line holding exactly those."""
        total = sum(wid[:n]) + gap_ * max(n - 1, 0)
        left = x * W - (total / 2 if align == "centre" else total if align == "right" else 0.0)
        out, pen = [], (left + total if rtl else left)
        for i in range(n):
            if rtl:
                pen -= wid[i]
                out.append(pen)
                pen -= gap_
            else:
                out.append(pen)
                pen += wid[i] + gap_
        return out

    full = places(len(words), gap)
    last_on = max(ons)
    items = []
    for i, (vk, w) in enumerate(words):
        start = a if rule in ("ghost",) else ons[i]
        if start >= b:
            continue
        n = b - start
        it = {"id": f"{id_}.w{i}", "k": "word", "f": [start, b], "t": w, "v": vk, "y": y, "ink": ink, "role": role, "on": ons[i]}
        if z is not None:
            it["z"] = z
        if rule == "reflow_centre":
            xs = []
            for fr in range(start, b):
                landed = sum(1 for o in ons if o <= fr)
                xs.append(places(max(landed, i + 1), gap)[i] / W)
            it["x"] = xs if len(set(xs)) > 1 else xs[0]
        elif rule == "justify_on_fill" and fill and len(words) > 1 and last_on < b:
            g2 = (fill * W - sum(wid)) / (len(words) - 1)
            wide = places(len(words), max(g2, gap))
            xs = [(full[i] if fr < last_on else wide[i]) / W for fr in range(start, b)]
            it["x"] = xs if len(set(xs)) > 1 else xs[0]
        else:
            it["x"] = full[i] / W
        if rule == "ghost":
            op = [ghost if fr < ons[i] else 1.0 for fr in range(start, b)]
            it["op"] = op if len(set(op)) > 1 else op[0]
        if extra:
            it.update(extra)
        assert n > 0
        items.append(it)
    return items


def carets(items, voices, frame):
    """The right edge (left, for a right-to-left word) of each laid word, in pixels at the frame: where
    a reading seam rests once that word is spoken."""
    W = frame[0]
    out = []
    for it in items:
        v = voices[it["v"]]
        w = Face.of(v).measure(it["t"], float(v.get("px", 58)), float(v.get("track", 0)))
        x = it["x"][-1] if isinstance(it["x"], list) else it["x"]
        out.append(x * W if is_joined_script(it["t"]) else x * W + w)
    return out
