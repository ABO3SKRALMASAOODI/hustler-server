"""Layout families: where each printed word goes (ROADMAP 2A).

A family turns the item's words (``Tok``: text, role, ladder rung, face)
into absolute placements inside one region of the free-space map, at one
hero cap height ``C`` (design px). The solver (solver.py) searches C, the
region and the family's options, and keeps the best layout that passes
every constraint. Families never measure by guessing: every width and ink
box comes from typeset.shaping on the page's own font files.

Families (the roadmap's list):
  stack        a lockup of kicker and hero lines (left, centre, right)
  magazine     hero lines filling the width behind the head (depth)
  staircase    each line steps right, built top-down in onset order
  stack_up     a stack built bottom-up
  justified    force-justified block: equal line widths by TRACKING
  side_column  a narrow force-justified column beside the subject
  slots        baselines on an even slot grid, alternating sides
  flank        two edge-justified columns either side of the subject
  constellation  words scattered in the free space around the subject
  spread       one letter-spaced line spanning the width
  tilt         a rotated stack bleeding off one side
  quote_window one word at a time, centred, sized by its role
  arc          the payload on an arc round the head (path text)
"""

import math

from . import shaping
from . import voices as V

FAMILIES = ("stack", "magazine", "staircase", "stack_up", "justified",
            "side_column", "slots", "flank", "constellation", "spread",
            "tilt", "quote_window", "arc")
MAX_LINES = 6
WORD_GAP = 0.27            # x the larger neighbour's font size
LINE_GAP = {"tight": 0.16, "normal": 0.32, "airy": 0.55}   # x the cap
MAX_LINE_SCALE = 2.2       # a fitted hero line grows at most this much
TRACK_RANGE = (-0.04, 0.9)  # em, force-justification by tracking


class Tok:
    """One printed word."""

    __slots__ = ("i", "raw", "text", "role", "rung", "spec", "track",
                 "case", "onset", "dur", "ratio", "style", "floor")

    def __init__(self, i, raw, role, voice, onset=None, dur=None):
        self.i, self.raw, self.role = i, raw, role
        self.rung = V.rung_of(role)
        rs = V.rung_spec(voice, role)
        self.style = rs
        self.case = rs.get("case", "keep")
        self.text = V.cased(raw, self.case)
        self.spec = shaping.FontSpec(rs["family"], rs.get("weight", 400),
                                     rs.get("style", "normal"),
                                     rs.get("stretch", 100.0),
                                     rs.get("axes"))
        self.track = float(rs.get("tracking", 0.0))
        self.ratio = V.ladder_ratio(voice, role)
        self.onset, self.dur = onset, dur
        self.floor = 0.0                # legibility floor (cap px)

    def size_at(self, C, scale=1.0):
        """Font size (px) giving this rung's cap height at hero cap C. A
        small rung never drops below the legibility floor: the ladder
        compresses instead (a designer's whisper still reads)."""
        cap = max(C * self.ratio * scale, self.floor)
        return shaping.size_for_cap(self.spec, cap)

    def shaped(self, size):
        return shaping.shape(self.text, self.spec, size)


class Placed:
    __slots__ = ("tok", "size", "x", "y", "track_px", "ink", "rot", "line",
                 "cx", "cy", "glyphs")

    def __init__(self, tok, size, x, y, track_px, ink, line, rot=0.0):
        self.tok, self.size, self.x, self.y = tok, size, x, y
        self.track_px, self.ink, self.line, self.rot = track_px, ink, line, rot
        self.cx = self.cy = None        # rotation centre (px)
        self.glyphs = None              # [(char, x, y, rot)] for path text


class Layout:
    def __init__(self, family, placed, opts=None):
        self.family = family
        self.placed = placed
        self.opts = dict(opts or {})

    def boxes(self):
        return [p.ink for p in self.placed]

    def bbox(self):
        bs = self.boxes()
        return (min(b[0] for b in bs), min(b[1] for b in bs),
                max(b[2] for b in bs), max(b[3] for b in bs))


# ── lines ────────────────────────────────────────────────────────────────

def break_lines(toks, family="stack", max_lines=MAX_LINES, mode="kicker"):
    """Kicker / hero lines: a hero opens its own line; the small words
    before it form the kicker line above it; adjacent heroes share one.
    ``mode="pack"`` sets the small words INLINE instead (before the next
    hero, after the last one): fewer, fuller lines — the magazine
    masthead."""
    if family == "quote_window":
        return [[t] for t in toks]
    if mode.startswith("bal"):
        return balanced(toks, int(mode[3:] or 2))
    if mode == "pack" and any(t.rung == "hero" for t in toks):
        lines, pend = [], []
        for t in toks:
            if t.rung == "hero":
                if lines and lines[-1][-1].rung == "hero" and not pend:
                    lines[-1].append(t)
                else:
                    lines.append(pend + [t])
                pend = []
            else:
                pend.append(t)
        if pend:
            lines[-1] += pend
        while len(lines) > max_lines:
            k = min(range(len(lines) - 1),
                    key=lambda i: len(lines[i]) + len(lines[i + 1]))
            lines[k:k + 2] = [lines[k] + lines[k + 1]]
        return lines
    lines, cur = [], []
    for t in toks:
        if t.rung == "hero":
            if cur and cur[-1].rung != "hero":
                lines.append(cur)
                cur = []
            cur.append(t)
        else:
            if cur and cur[-1].rung == "hero":
                lines.append(cur)
                cur = []
            cur.append(t)
    if cur:
        lines.append(cur)
    # a lone one-letter/short connector line merges into the next line
    merged = []
    for ln in lines:
        if merged and len(merged[-1]) == 1 and merged[-1][0].rung == \
                "whisper" and len(merged[-1][0].raw) <= 3 and \
                ln[0].rung != "whisper":
            merged[-1] = merged[-1] + ln
        else:
            merged.append(ln)
    lines = merged
    # long small-word lines split in two
    out = []
    for ln in lines:
        if ln[0].rung != "hero" and len(ln) > 5:
            h = (len(ln) + 1) // 2
            out += [ln[:h], ln[h:]]
        else:
            out.append(ln)
    while len(out) > max_lines:
        # merge the two adjacent small lines with the fewest words
        best, bi = None, None
        for k in range(len(out) - 1):
            if out[k][0].rung == "hero" and out[k + 1][0].rung == "hero":
                continue
            n = len(out[k]) + len(out[k + 1])
            if best is None or n < best:
                best, bi = n, k
        if bi is None:
            bi = len(out) - 2
        out[bi:bi + 2] = [out[bi] + out[bi + 1]]
    return out


def balanced(toks, n):
    """``toks`` broken into ``n`` lines of natural widths as even as
    possible (minimum raggedness over all break sets, at a unit hero cap),
    never ending a line on a connector when another break exists."""
    k = len(toks)
    if n <= 1 or k <= 1:
        return [list(toks)]
    n = min(n, k)
    widths = [measure_line([t], 100.0)["w"] for t in toks]
    gap = WORD_GAP * 100.0

    def wspan(a, b):
        return sum(widths[a:b]) + gap * (b - a - 1)
    target = wspan(0, k) / n
    INF = float("inf")
    best = [[(INF, None)] * (k + 1) for _ in range(n + 1)]
    best[0][0] = (0.0, None)
    for j in range(1, n + 1):
        for b in range(1, k + 1):
            for a in range(j - 1, b):
                prev = best[j - 1][a][0]
                if prev == INF:
                    continue
                cost = (wspan(a, b) - target) ** 2
                if b < k and toks[b - 1].rung == "whisper":
                    cost += (0.6 * target) ** 2      # a dangling connector
                if prev + cost < best[j][b][0]:
                    best[j][b] = (prev + cost, a)
    if best[n][k][1] is None and n > 1:
        return [list(toks)]
    cuts, b = [], k
    for j in range(n, 0, -1):
        a = best[j][b][1]
        cuts.append((a, b))
        b = a
    return [list(toks[a:b]) for a, b in reversed(cuts)]


def measure_line(line, C, scale=1.0, extra_track=0.0):
    """Shape a line at hero cap C. Returns {w, top, bottom, items: [(tok,
    size, pen_x, track_px, ink_rel)]} with pen_x from the line's ink
    left and ink_rel the word's ink relative to its own origin."""
    items, x = [], 0.0
    first = True
    prev_size = None
    for t in line:
        size = t.size_at(C, scale)
        sh = t.shaped(size)
        tp = (t.track + extra_track) * size
        if not first:
            x += WORD_GAP * max(size, prev_size)
        pen = x - (sh.ink[0] if first else 0.0)
        ink = (sh.ink[0], sh.ink[1], sh.ink[2] + tp * max(0, len(t.text) - 1),
               sh.ink[3])
        items.append((t, size, pen, tp, ink))
        x = pen + sh.advance + tp * len(t.text)
        first = False
        prev_size = size
    if not items:
        return {"w": 0.0, "top": 0.0, "bottom": 0.0, "items": []}
    last = items[-1]
    w = last[2] + last[4][2]
    top = min(it[4][1] for it in items)
    bottom = max(it[4][3] for it in items)
    return {"w": w, "top": top, "bottom": bottom, "items": items}


def _place_line(m, x, base, line_no, out, rot=0.0):
    for t, size, pen, tp, ink in m["items"]:
        ox = x + pen
        out.append(Placed(t, size, ox, base, tp,
                          (ox + ink[0], base + ink[1], ox + ink[2],
                           base + ink[3]), line_no, rot))


def _fit_scale(m, width):
    if m["w"] <= 0:
        return 1.0
    return width / m["w"]


def _justify_track(line, C, scale, width):
    """Extra tracking (em) that makes the line ``width`` wide, or None."""
    base = measure_line(line, C, scale)
    chars = sum(max(0, len(t.text) - 1) for t in line)
    sizes = [t.size_at(C, scale) for t in line]
    if chars <= 0:
        return None
    em = sum(s * max(0, len(t.text) - 1) for s, t in zip(sizes, line))
    need = (width - base["w"]) / max(1e-6, em)
    lo, hi = TRACK_RANGE
    if need < lo or need > hi:
        return None
    return need


# ── families ─────────────────────────────────────────────────────────────

def _stack(lines, C, region, opts, order=1):
    """The workhorse: kicker and hero lines stacked in ``region``.
    opts: align left|center|right, fit width|none|tracking, gap tight|
    normal|airy, valign top|center|bottom, step (staircase px per line)."""
    x0, y0, x1, y1 = region
    W = x1 - x0
    fit = opts.get("fit", "none")
    gap = LINE_GAP[opts.get("gap", "normal")] * C
    ms = []
    for ln in lines:
        hero = any(t.rung == "hero" for t in ln)
        scale = 1.0
        extra = 0.0
        if fit == "width" and hero:
            m0 = measure_line(ln, C)
            scale = min(MAX_LINE_SCALE, _fit_scale(m0, W * opts.get(
                "fill", 1.0)))
            if scale < 0.45:
                return None
        m = measure_line(ln, C, scale)
        if fit == "tracking" and len(lines) > 1:
            tr = _justify_track(ln, C, scale, W * opts.get("fill", 1.0))
            if tr is not None:
                extra = tr
                m = measure_line(ln, C, scale, extra)
        if m["w"] > W + 0.5:
            return None
        ms.append(m)
    step = float(opts.get("step") or 0.0)
    heights = [m["bottom"] - m["top"] for m in ms]
    total = sum(heights) + gap * (len(ms) - 1)
    if total > (y1 - y0) + 0.5:
        return None
    if step:
        span = max(m["w"] for m in ms) + step * (len(ms) - 1)
        if span > W + 0.5:
            return None
    va = opts.get("valign", "center")
    if va == "top":
        y = y0
    elif va == "bottom":
        y = y1 - total
    else:
        y = y0 + ((y1 - y0) - total) / 2
    ys = []
    for m, h in zip(ms, heights):
        ys.append(y - m["top"])
        y += h + gap
    if order < 0:                      # built bottom-up: first line lowest
        ys = list(reversed(ys))
        ms_order = list(range(len(ms)))
    else:
        ms_order = list(range(len(ms)))
    placed = []
    align = opts.get("align", "left")
    for k in ms_order:
        m = ms[k]
        base = ys[k] if order > 0 else ys[k]
        if step:
            bx = x0 + step * k
        elif align == "center":
            bx = x0 + (W - m["w"]) / 2
        elif align == "right":
            bx = x1 - m["w"]
        else:
            bx = x0
        _place_line(m, bx, base, k, placed)
    return placed


def stack(lines, C, region, space, opts):
    p = _stack(lines, C, region, opts)
    return Layout("stack", p, opts) if p else None


def magazine(lines, C, region, space, opts):
    o = dict(opts, fit="width", gap="tight", align=opts.get("align", "left"))
    p = _stack(lines, C, region, o)
    return Layout("magazine", p, o) if p else None


def staircase(lines, C, region, space, opts):
    n = len(lines)
    if n < 2:
        return None
    W = region[2] - region[0]
    ms = [measure_line(ln, C) for ln in lines]
    widest = max(m["w"] for m in ms)
    step = (W - widest) / (n - 1)
    if step < 0.04 * W:
        return None
    step = min(step, 0.22 * W)
    o = dict(opts, step=step, fit="none", gap=opts.get("gap", "tight"))
    p = _stack(lines, C, region, o)
    return Layout("staircase", p, o) if p else None


def stack_up(lines, C, region, space, opts):
    o = dict(opts, valign="bottom")
    p = _stack(lines, C, region, o, order=-1)
    return Layout("stack_up", p, o) if p else None


def justified(lines, C, region, space, opts):
    if len(lines) < 2:
        return None
    o = dict(opts, fit="tracking", align="left", gap=opts.get("gap",
                                                              "normal"))
    p = _stack(lines, C, region, o)
    return Layout("justified", p, o) if p else None


def side_column(lines, C, region, space, opts):
    """Re-break the words into a narrow column (greedy by width at their
    own sizes) and force-justify it by tracking."""
    W = region[2] - region[0]
    if W > 0.48 * space.W:
        cw = 0.42 * space.W
        if opts.get("side", "auto") == "right" or (
                opts.get("side", "auto") == "auto" and
                region[0] + region[2] > space.W):
            region = (region[2] - cw, region[1], region[2], region[3])
        else:
            region = (region[0], region[1], region[0] + cw, region[3])
        W = cw
    toks = [t for ln in lines for t in ln]
    cols, cur = [], []
    for t in toks:
        trial = cur + [t]
        if cur and (measure_line(trial, C)["w"] > W or t.rung == "hero"
                    or cur[-1].rung == "hero"):
            cols.append(cur)
            cur = [t]
        else:
            cur = trial
    if cur:
        cols.append(cur)
    if len(cols) > 9:
        return None
    o = dict(opts, fit="tracking", align="left", gap="normal")
    p = _stack(cols, C, region, o)
    return Layout("side_column", p, o) if p else None


def slots(lines, C, region, space, opts):
    """Baselines on an even grid (one line per slot), alternating left and
    right: the 20@3.0 slot lockup."""
    n = len(lines)
    if n < 2 or n > 5:
        return None
    x0, y0, x1, y1 = region
    W, Hh = x1 - x0, y1 - y0
    ms = [measure_line(ln, C) for ln in lines]
    if max(m["w"] for m in ms) > W:
        return None
    pitch = Hh / n
    placed = []
    for k, m in enumerate(ms):
        base = y0 + pitch * (k + 1) - pitch * 0.18
        if base + m["top"] < y0 + pitch * k - 0.5 or base + m["bottom"] > \
                y0 + pitch * (k + 1) + 0.5:
            return None
        bx = x0 if (k % 2 == 0) == (opts.get("start", "left") == "left") \
            else x1 - m["w"]
        _place_line(m, bx, base, k, placed)
    return Layout("slots", placed, dict(opts, pitch=pitch))


def flank(lines, C, region, space, opts):
    """Two edge-justified columns either side of the face."""
    reg = space.named(opts.get("depth", "front"))
    left, right = reg.get("left"), reg.get("right")
    if not left or not right or len(lines) < 2:
        return None
    h = (len(lines) + 1) // 2
    a = _stack(lines[:h], C, left, dict(opts, align="left", fit="none"))
    b = _stack(lines[h:], C, right, dict(opts, align="right", fit="none"))
    if not a or not b:
        return None
    for p in b:
        p.line += h
    return Layout("flank", a + b, opts)


def constellation(lines, C, region, space, opts):
    """Each line to its own free region around the subject, the hero lines
    to the largest, in reading order (top-left first), with a slight
    seeded tilt on the small lines."""
    regs = space.regions(opts.get("depth", "front"), min_frac=0.02, top=10)
    if len(regs) < 2 or len(lines) < 2:
        return None
    order = sorted(range(len(lines)), key=lambda k: (
        0 if any(t.rung == "hero" for t in lines[k]) else 1, k))
    used, assign = [], {}
    for k in order:
        m = measure_line(lines[k], C)
        hgt = m["bottom"] - m["top"]
        best = None
        for r in regs:
            if r[2] - r[0] < m["w"] or r[3] - r[1] < hgt:
                continue
            # the slot inside r: aligned toward the subject's side
            cx = (r[0] + r[2]) / 2
            fx = (space.faces_px[0][0] + space.faces_px[0][2]) / 2 \
                if space.faces_px else space.W / 2
            bx = r[2] - m["w"] if cx < fx else r[0]
            by = r[1] + ((r[3] - r[1]) - hgt) * (0.3 if k == 0 else 0.5)
            box = (bx, by, bx + m["w"], by + hgt)
            if any(_overlap(box, u, 0.02 * space.W) for u in used):
                continue
            score = (r[2] - r[0]) * (r[3] - r[1])
            if best is None or score > best[0]:
                best = (score, box, m)
        if best is None:
            return None
        used.append(best[1])
        assign[k] = best
    # reading order: sort the boxes top-to-bottom, then left-to-right, and
    # give them to the lines in spoken order
    boxes = sorted((assign[k][1] for k in assign),
                   key=lambda b: (round(b[1] / (0.08 * space.H)), b[0]))
    placed = []
    for k in range(len(lines)):
        m = measure_line(lines[k], C)
        b = boxes[k]
        if b[2] - b[0] < m["w"] - 0.5 or b[3] - b[1] < (m["bottom"] -
                                                         m["top"]) - 0.5:
            # a smaller line took a bigger box: fine; a bigger line that
            # landed in a smaller box re-centres inside it
            pass
        bx = b[0] + max(0.0, ((b[2] - b[0]) - m["w"]) / 2)
        base = b[1] - m["top"]
        _place_line(m, bx, base, k, placed)
    return Layout("constellation", placed, opts)


def spread(lines, C, region, space, opts):
    """One line, letter-spaced to the region's width."""
    toks = [t for ln in lines for t in ln]
    if len(toks) > 4:
        return None
    W = region[2] - region[0]
    tr = _justify_track(toks, C, 1.0, W)
    if tr is None:
        return None
    m = measure_line(toks, C, 1.0, tr)
    h = m["bottom"] - m["top"]
    if h > region[3] - region[1]:
        return None
    y = region[1] + ((region[3] - region[1]) - h) / 2 - m["top"]
    placed = []
    _place_line(m, region[0], y, 0, placed)
    return Layout("spread", placed, dict(opts, track=tr))


def tilt(lines, C, region, space, opts):
    """A stack rotated about its centre (axis-aligned boxes of the rotated
    words are what the constraints see: conservative)."""
    ang = float(opts.get("angle", -7.0))
    p = _stack(lines, C, region, dict(opts, fit="none"))
    if not p:
        return None
    lay = Layout("tilt", p, dict(opts, angle=ang))
    bx = lay.bbox()
    cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
    a = math.radians(ang)
    for q in p:
        q.rot, q.cx, q.cy = ang, cx, cy
        xs, ys = [], []
        for (x, y) in ((q.ink[0], q.ink[1]), (q.ink[2], q.ink[1]),
                       (q.ink[0], q.ink[3]), (q.ink[2], q.ink[3])):
            dx, dy = x - cx, y - cy
            xs.append(cx + dx * math.cos(a) - dy * math.sin(a))
            ys.append(cy + dx * math.sin(a) + dy * math.cos(a))
        q.ink = (min(xs), min(ys), max(xs), max(ys))
    return lay


def quote_window(lines, C, region, space, opts):
    """One word at a time in the same window, each sized by its rung and
    fitted to the window's width (sentence case)."""
    x0, y0, x1, y1 = region
    W, Hh = x1 - x0, y1 - y0
    placed = []
    for k, ln in enumerate(lines):
        m = measure_line(ln, C)
        scale = min(1.0, W / max(1.0, m["w"]))
        if scale < 1.0:
            m = measure_line(ln, C, scale)
        h = m["bottom"] - m["top"]
        if h > Hh or m["w"] > W + 0.5:
            return None
        cy = y0 + Hh * 0.5
        base = cy + (m["bottom"] - m["top"]) / 2 - m["bottom"]
        bx = x0 + (W - m["w"]) / 2
        _place_line(m, bx, base, k, placed)
    return Layout("quote_window", placed, opts)


def arc(lines, C, region, space, opts):
    """The words on an arc concentric with the head, above it (38@15.0):
    per-glyph positions and rotations, solved here so the page only
    draws them. Clearance from the head ellipse 3-6% of the frame
    height."""
    if not space.faces_px:
        return None
    f = space.faces_px[0]
    cx = (f[0] + f[2]) / 2
    hw, hh = (f[2] - f[0]) / 2 * 1.25, (f[3] - f[1]) / 2 * 1.45
    cy = (f[1] + f[3]) / 2 - 0.05 * (f[3] - f[1])
    clear = float(opts.get("clear", 0.045)) * space.H
    toks = [t for ln in lines for t in ln]
    m = measure_line(toks, C)
    capmax = max(it[4][3] - it[4][1] for it in m["items"])
    rx, ry = hw + clear + capmax * 0.15, hh + clear + capmax * 0.15
    # arc length available above the head: up to 200 degrees
    per = math.pi * (rx + ry) / 2            # half-ellipse length (approx)
    length = m["w"]
    if length > per * 1.05:
        return None
    sweep = length / ((rx + ry) / 2)
    a0 = -math.pi / 2 - sweep / 2
    placed = []
    for t, size, pen, tp, ink in m["items"]:
        sh = t.shaped(size)
        glyphs = []
        boxes = []
        for ci, ch in enumerate(t.text):
            gx, gadv = sh.chars[ci]
            s = pen + gx + tp * ci + gadv / 2
            a = a0 + s / ((rx + ry) / 2)
            px, py = cx + rx * math.cos(a), cy + ry * math.sin(a)
            rot = math.degrees(a + math.pi / 2)
            glyphs.append((ch, round(px, 2), round(py, 2), round(rot, 2),
                           round(gadv, 2)))
            r = max(gadv, size * 0.7)
            boxes.append((px - r / 2, py - size * 0.8, px + r / 2,
                          py + size * 0.2))
        p = Placed(t, size, glyphs[0][1] if glyphs else cx, cy, tp,
                   (min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes)), 0)
        p.glyphs = glyphs
        placed.append(p)
    lay = Layout("arc", placed, dict(opts, cx=cx, cy=cy, rx=rx, ry=ry,
                                     a0=a0, a1=a0 + sweep))
    return lay


def _overlap(a, b, pad=0.0):
    return (a[0] - pad < b[2] and b[0] - pad < a[2] and a[1] - pad < b[3]
            and b[1] - pad < a[3])


BUILDERS = {"stack": stack, "magazine": magazine, "staircase": staircase,
            "stack_up": stack_up, "justified": justified,
            "side_column": side_column, "slots": slots, "flank": flank,
            "constellation": constellation, "spread": spread, "tilt": tilt,
            "quote_window": quote_window, "arc": arc}
