"""Glyph outlines for TypeFX glyph morphs (Wave 2 track 2B).

ROADMAP 2B: outlines are precomputed in Python and the page only lerps
them (opentype.js and flubber never enter a page: the 200 KB cap). Each
glyph contour is flattened and resampled to the same point count, so a
morph is a per-point linear interpolation in ``typeset.html``.

``morph_doc(spec, text, size, x, y, track_px)`` -> {"from": [contour],
"to": [contour]} where a contour is [[x, y], ...] in design px: ``to`` is
the word's real outlines at its solved place, ``from`` the same points
gathered into a small seed disc at each glyph's centre (the letters bloom
out of a dot), deterministic and at most MAX_CONTOURS contours.
"""

import math

POINTS = 24
MAX_CONTOURS = 40
_STEPS = 6


class _FlatPen:
    """Contours as polylines (curves subdivided), y up (font units)."""

    def __init__(self):
        self.contours, self.cur, self.last = [], [], (0.0, 0.0)

    def moveTo(self, p):
        if self.cur:
            self.contours.append(self.cur)
        self.cur = [tuple(p)]
        self.last = tuple(p)

    def lineTo(self, p):
        self.cur.append(tuple(p))
        self.last = tuple(p)

    def qCurveTo(self, *pts):
        p0 = self.last
        for i in range(0, len(pts) - 1, 2):
            c, p1 = pts[i], pts[i + 1]
            for k in range(1, _STEPS + 1):
                t = k / _STEPS
                x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p1[0]
                y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p1[1]
                self.cur.append((x, y))
            p0 = tuple(p1)
        self.last = p0

    def curveTo(self, a, b, c):
        p0 = self.last
        for k in range(1, _STEPS + 1):
            t = k / _STEPS
            mt = 1 - t
            x = (mt ** 3 * p0[0] + 3 * mt * mt * t * a[0] + 3 * mt * t * t * b[0]
                 + t ** 3 * c[0])
            y = (mt ** 3 * p0[1] + 3 * mt * mt * t * a[1] + 3 * mt * t * t * b[1]
                 + t ** 3 * c[1])
            self.cur.append((x, y))
        self.last = tuple(c)

    def closePath(self):
        if self.cur:
            self.contours.append(self.cur)
        self.cur = []

    def endPath(self):
        self.closePath()


def resample(poly, n=POINTS):
    """``poly`` (closed) resampled to ``n`` points evenly by arc length."""
    if len(poly) < 2:
        return [list(poly[0])] * n if poly else []
    pts = list(poly) + [poly[0]]
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg) or 1.0
    out, acc, i = [], 0.0, 0
    for k in range(n):
        target = total * k / n
        while i < len(seg) - 1 and acc + seg[i] < target:
            acc += seg[i]
            i += 1
        u = 0.0 if seg[i] == 0 else (target - acc) / seg[i]
        a, b = pts[i], pts[i + 1]
        out.append([a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u])
    return out


def outlines(spec, text, size, x, y, track_px=0.0):
    """Per-glyph contours of ``text`` set at baseline-left (x, y), design
    px (y down): [[contour, ...] per glyph]."""
    from typeset import shaping
    if shaping.ENGINE != "harfbuzz":
        return []
    sh = shaping.shape(text, spec, size)
    f = shaping._font(spec)
    s = float(size) / float(f.upem)
    out = []
    for k, (gid, cluster, gx, _adv) in enumerate(sh.glyphs):
        pen = _FlatPen()
        try:
            f.font.draw_glyph_with_pen(gid, pen)
        except Exception:  # noqa: BLE001
            continue
        pen.closePath()
        ox = x + gx + track_px * cluster
        out.append([[(ox + px * s, y - py * s) for px, py in c]
                    for c in pen.contours if len(c) >= 3])
    return out


def morph_doc(spec, text, size, x, y, track_px=0.0, n=POINTS):
    glyphs = outlines(spec, text, size, x, y, track_px)
    to, frm = [], []
    for g in glyphs:
        if not g:
            continue
        xs = [p[0] for c in g for p in c]
        ys = [p[1] for c in g for p in c]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        r = 0.06 * size
        for c in g:
            if len(to) >= MAX_CONTOURS:
                break
            pts = resample(c, n)
            to.append([[round(p[0], 1), round(p[1], 1)] for p in pts])
            frm.append([[round(cx + r * math.cos(2 * math.pi * i / n), 1),
                         round(cy + r * math.sin(2 * math.pi * i / n), 1)]
                        for i in range(n)])
    return {"from": frm, "to": to} if to else None
