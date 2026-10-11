"""Measured type for the headline band above a card or letterboxed picture.

Production finding (Diamandis run, Oct 2026, 7 of 9 shorts killed): the
standing `headline` claim rendered SMALLER than the captions under it. The
band above the card was thin, the kicker and its gap took a third of it, and
headline.html silently shrank the claim (down to 28 px) to fit what was left;
nothing at write time said so. This module measures, without a browser, what
headline.html will draw, so the write can refuse a claim smaller than the
captions and say what makes it legible:

* ``headline_layout`` is headline.html's layout pass in Python: the kicker
  fit (MG.fitSecondary: one line from 46 px x size down to the 2.2% cap
  floor, else wrapped at the floor), the claim's descending 0.97 search for
  the largest size whose lines fit the band, the column and the line limit,
  what caps it (size, band or column) and where its type draws (the lines
  balanced as text-wrap: balance sets them). Advances, cap heights and
  vertical metrics come from the SAME font files the motion engine serves
  Chromium (worker/fonts, worker/motion/fonts), through Pillow's basic
  layout (no shaping engine needed, identical on every lane). Chromium also
  applies kerning, so a line measured here runs a little WIDER than the
  browser's (0-2% on real claims): the estimate wraps no later than the
  page, and with WRAP_SAFETY / HEIGHT_SAFETY_PX a borderline fit is taken
  one step down, so it errs small (tests/test_headline_type.py holds it to
  the page within a few percent). Only a run of positively kerned pairs
  ('WWWWW') measures narrower than the page.
* ``caption_cap`` is the cap height the short's captions are drawn at
  (motion look, premium libass preset or the plain style), as a share of
  the frame height; libass sizes a face by its OS/2 Windows ascent +
  descent (``ass_line_em``).
* ``needed_band`` / ``max_claim_chars`` / ``largest_card`` turn a miss into
  the concrete fix: the band height (so the card top) at which the claim
  reaches the captions, the claim length the present band holds at that
  size, and the largest card box left under that band with its area against
  a picture floor.
* ``mark_strip`` is the free strip BESIDE the free-tier mark: the mark
  covers the top-left only (keepout.watermark_zone, x 0.09-0.50), so a
  short kicker can sit right of it, above the band, and leave the whole band
  to the claim.

Everything here is read-only geometry; it never changes a render by itself.
"""
import functools
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REF_PX = 1000                 # measuring size: metrics in 1/1000 em

# headline.html's styles (keep in step with the template's STYLE table)
STYLES = {
    "sans": {"family": "Inter Display", "weight": 800, "track": -0.025, "lh": 1.04, "upper": False},
    "serif": {"family": "Instrument Serif", "weight": 400, "track": -0.012, "lh": 1.0, "upper": False},
    "condensed": {"family": "Anton", "weight": 400, "track": 0.005, "lh": 0.98, "upper": True},
}
KICKER = {"family": "Inter Display", "weight": 700, "track": -0.01, "lh": 1.05}
ACC_SERIF = {"family": "Instrument Serif", "weight": 400, "italic": True, "track": -0.01, "scale": 1.12}
CLAIM_MAX_PX = 84.0           # x size: the template's largest claim size
CLAIM_MIN_PX = 28.0           # the template's floor
KICKER_MAX_PX = 46.0          # x size
KICKER_START_PX = 40.0
SIZE_MAX = 1.4                # the template's size knob
MIN_CAP = 0.022               # MG.MIN_CAP: secondary type's cap-height floor
FIT_STEP = 0.97
LONG_CLAIM = 50               # more characters than this may take 3 lines
# A claim with no captions to beat is held to the cap height a clean
# caption track would have (motion look 'clean', size m: 70 px Inter).
NO_CAPTION_CAP = 0.026
# Cards end here in the 9:16 layouts (the platform covers the bottom 20%).
CARD_BOTTOM = 0.794
# Clear space between the mark's reserved zone and a kicker set beside it.
STRIP_GAP = 0.012
# The page's tests of a fit, held a hair tighter here: a line wraps
# WRAP_SAFETY of the column early, and where the block's height rests on
# font metrics (a stacked kicker's size comes from the browser's own cap
# measure; a serif accent line's box from its ascent and descent) it must
# fit HEIGHT_SAFETY_PX inside the band. A borderline layout (the run's 3447
# claim beside its kicker had 1.6 px of column to spare), where another
# platform's glyph advances or line metrics could tip the page one 0.97
# step down, is measured at that smaller step: the estimate errs small,
# never large. (Line boxes alone are arithmetic, the same on every lane.)
WRAP_SAFETY = 0.004
HEIGHT_SAFETY_PX = 1.0

_STAR_TOKEN_END = re.compile(r"\*([.,!?:;\"')’”]*)$")


# ── fonts ─────────────────────────────────────────────────────────────────

def _faces():
    import motion_engine
    out = []
    for fam, fn, weight, style in motion_engine.FONT_FACES:
        out.append((fam, os.path.join(motion_engine.FONTS_DIR, fn), weight, weight, style))
    for fam, fn, weight, style, _st in motion_engine.MOTION_FONT_FACES:
        lo, hi = ((int(weight.split()[0]), int(weight.split()[1])) if isinstance(weight, str)
                  else (weight, weight))
        out.append((fam, os.path.join(motion_engine.MOTION_FONTS_DIR, fn), lo, hi, style))
    return out


@functools.lru_cache(maxsize=64)
def font_path(family, weight=400, italic=False):
    """The bundled file the motion engine serves for a CSS family/weight/style
    (the nearest weight, as the browser's font matching picks it), or None."""
    style = "italic" if italic else "normal"
    best, dist = None, None
    for fam, path, lo, hi, st in _faces():
        if fam != family or st != style:
            continue
        d = 0 if lo <= weight <= hi else min(abs(weight - lo), abs(weight - hi))
        if dist is None or d < dist:
            best, dist = path, d
    if best is None and italic:
        return font_path(family, weight, False)
    return best if best and os.path.isfile(best) else None


@functools.lru_cache(maxsize=64)
def _font(path):
    from PIL import ImageFont
    return ImageFont.truetype(path, REF_PX, layout_engine=ImageFont.Layout.BASIC)


@functools.lru_cache(maxsize=64)
def metrics(path):
    """(cap height, ascent, descent) of a font file in em."""
    f = _font(path)
    asc, desc = f.getmetrics()
    try:
        cap = -float(f.getbbox("H", anchor="ls")[1])
    except Exception:  # noqa: BLE001 — a face with no H: the usual ratio
        cap = 0.72 * REF_PX
    return cap / REF_PX, asc / REF_PX, desc / REF_PX


@functools.lru_cache(maxsize=64)
def ass_line_em(path):
    """The line height libass sizes this font by, in em: OS/2 usWinAscent +
    usWinDescent when the table sets them (libass set_font_metrics, GDI's
    rule), else the face's ascent + descent."""
    import struct
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        tables = {}
        for k in range(struct.unpack(">H", data[4:6])[0]):
            tag, _sum, off, _len = struct.unpack(">4sIII", data[12 + 16 * k:28 + 16 * k])
            tables[tag] = off
        upm = struct.unpack(">H", data[tables[b"head"] + 18:tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        win_a, win_d = struct.unpack(">hh", data[o + 74:o + 78])
        if win_a + win_d and upm:
            return (win_a + win_d) / float(upm)
    except Exception:  # noqa: BLE001 — no OS/2 table: the face's metrics
        pass
    _cap, a, d = metrics(path)
    return a + d


@functools.lru_cache(maxsize=4096)
def _advance(path, text):
    return float(_font(path).getlength(text)) / REF_PX


def text_width(path, text, px, track_em=0.0):
    """Width in px of ``text`` at ``px``: advances plus the letter-spacing
    Chromium adds after every character."""
    return (_advance(path, text) + track_em * len(text)) * px


def cap_ratio(family, weight=400, italic=False):
    path = font_path(family, weight, italic)
    return metrics(path)[0] if path else 0.72


# ── headline.html, measured ───────────────────────────────────────────────

def star_words(text):
    """MG.starWords: whitespace tokens with their accent flag."""
    out, run = [], False
    for tok in str(text or "").split():
        t, acc = tok, run
        if t.startswith("*"):
            acc, run, t = True, True, t[1:]
        if _STAR_TOKEN_END.search(t):
            acc, run = True, False
            t = _STAR_TOKEN_END.sub(r"\1", t)
        t = t.replace("*", "")
        if t:
            out.append((t, acc))
    return out


def _num(params, key, default):
    try:
        v = params.get(key)
        return float(default if v is None else v)
    except (TypeError, ValueError, AttributeError):
        return float(default)


def design_height(W, H):
    """The template's CSS height (1080 wide, the frame's aspect)."""
    return 1080.0 * float(H) / max(1.0, float(W))


def _kicker_fit(text, col_w, size, Hd):
    """MG.fitSecondary on the kicker: (font px, lines, its box's width px)."""
    path = font_path(KICKER["family"], KICKER["weight"])
    floor = MIN_CAP * Hd / metrics(path)[0]
    hi = max(KICKER_MAX_PX * size, floor)
    width = lambda px: text_width(path, text, px, KICKER["track"])  # noqa: E731
    lo_, hi_, best = floor, hi, floor
    for _ in range(18):                       # MG.fit's bisection
        mid = (lo_ + hi_) / 2.0
        if width(mid) <= col_w + 0.5:
            best, lo_ = mid, mid
        else:
            hi_ = mid
    if width(best) <= col_w + 1:
        return best, 1, width(best)
    fs = floor
    words = text.split()
    while fs > 12 and max(width_w for width_w in
                          (text_width(path, w, fs, KICKER["track"]) for w in words)) > col_w + 1:
        fs *= 0.95
    lines = _greedy([text_width(path, w, fs, KICKER["track"]) for w in words],
                    text_width(path, " ", fs, KICKER["track"]), col_w)
    # wrapped, the kicker's box is the whole column (max-width: the column),
    # and a kicker set aside starts its lines at the column's LEFT edge
    return fs, len(lines), (col_w if len(lines) > 1 else max(lines))


def _greedy(widths, space, col_w):
    """Greedy line widths (Chromium's first pass; text-wrap: balance keeps
    the line count)."""
    lines = []
    for w in widths:
        if lines and lines[-1] + space + w <= col_w + 0.01:
            lines[-1] += space + w
        else:
            lines.append(w)
    return lines or [0.0]


def _balanced_width(claim, fs, col_w):
    """The widest line of the claim under text-wrap: balance — the
    narrowest width that keeps the greedy line count (Chromium narrows the
    line box until another line would be needed)."""
    widths = [em * fs for em, _s in claim.words]
    space = claim.space_em * fs
    n = len(_greedy(widths, space, col_w))
    lo_, hi_ = max(widths or [0.0]), col_w
    for _ in range(24):
        mid = (lo_ + hi_) / 2.0
        if len(_greedy(widths, space, mid)) <= n:
            hi_ = mid
        else:
            lo_ = mid
    return max(_greedy(widths, space, hi_))


def _box_above_below(path, px, lh):
    """(above, below) the baseline of an inline box: ascent/descent plus
    half the leading of line-height ``lh`` (CSS inline layout)."""
    _cap, a, d = metrics(path)
    half = (lh - (a + d)) / 2.0
    return px * (a + half), px * (d + half)


class _Claim:
    """The claim's words, measured once in em (every size is a multiple)."""

    def __init__(self, params):
        st = STYLES.get(str(params.get("style") or "sans"), STYLES["sans"])
        self.st = st
        self.path = font_path(st["family"], st["weight"])
        serif_acc = (str(params.get("accent_style") or "color") == "serif"
                     and str(params.get("style") or "sans") != "serif")
        self.acc_path = font_path(ACC_SERIF["family"], ACC_SERIF["weight"], True) if serif_acc else None
        self.words = []                       # (em width, is serif accent)
        for t, acc in star_words(params.get("text")):
            t = t.upper() if st["upper"] else t
            if acc and self.acc_path:
                k = ACC_SERIF["scale"]
                em = (_advance(self.acc_path, t) + ACC_SERIF["track"] * len(t)) * k
                self.words.append((em, True))
            else:
                self.words.append((_advance(self.path, t) + st["track"] * len(t), False))
        self.space_em = _advance(self.path, " ") + st["track"]
        self.chars = len(str(params.get("text") or "").replace("*", ""))
        self.max_lines = 2 if self.chars <= LONG_CLAIM else 3
        self.cap = metrics(self.path)[0]

    def layout(self, fs, col_w):
        """(height px, lines by height, widest word px, line count)."""
        lh = self.st["lh"]
        lines = _greedy([em * fs for em, _s in self.words], self.space_em * fs, col_w)
        # which words sit on which line, for the taller accent lines
        rows, cur, w_cur = [[]], 0.0, None
        for em, serif in self.words:
            w = em * fs
            if w_cur is None:
                w_cur = w
            elif w_cur + self.space_em * fs + w <= col_w + 0.01:
                w_cur += self.space_em * fs + w
            else:
                rows.append([])
                w_cur = w
            rows[-1].append(serif)
        base_a, base_d = _box_above_below(self.path, fs, lh)
        height = 0.0
        for row in rows:
            a, d = base_a, base_d
            if self.acc_path and any(row):
                k = ACC_SERIF["scale"]
                sa, sd = _box_above_below(self.acc_path, fs * k, lh)
                a, d = max(a, sa), max(d, sd)
            height += a + d
        widest = max([em * fs for em, _s in self.words] or [0.0])
        self.line_widths = lines
        return height, int(round(height / (fs * lh))) if fs > 0 else 0, widest, len(lines)


def headline_layout(params, W=1080, H=1920, kicker_beside=None):
    """What headline.html draws for ``params`` on a W x H frame, as frame
    fractions (cap heights, block height) and design px (font sizes).

    ``kicker_beside``: True sets the kicker out of the stack (beside the
    mark, ``kicker_y``), False keeps it above the claim; None follows the
    params."""
    p = params or {}
    Hd = design_height(W, H)
    size = _num(p, "size", 1.0)
    col_w = 1080.0 * _num(p, "width", 0.84)
    band_h = Hd * _num(p, "height", 0.15)
    kicker = " ".join(str(p.get("kicker") or "").split())
    # the template sets a kicker aside only on a centred block
    beside = ((_num(p, "kicker_y", 0.0) > 0 and str(p.get("align") or "center") != "left")
              if kicker_beside is None else bool(kicker_beside))
    out = {"kicker_px": 0.0, "kicker_lines": 0, "kicker_w": 0.0, "kicker_cap": 0.0,
           "kicker_beside": bool(kicker) and beside}
    kh = gap = 0.0
    if kicker:
        kfs, kl, kw = _kicker_fit(kicker, col_w, size, Hd)
        out.update(kicker_px=kfs, kicker_lines=kl, kicker_w=kw / 1080.0,
                   kicker_h=kl * kfs * KICKER["lh"] / Hd,
                   kicker_cap=kfs * cap_ratio(KICKER["family"], KICKER["weight"]) / Hd)
        if not out["kicker_beside"]:
            kh = kl * kfs * KICKER["lh"]
            gap = max(6.0, 0.012 * Hd * size)
    claim = _Claim(p)
    room = max(40.0, band_h - kh - gap)
    hi = min(CLAIM_MAX_PX * size, room)
    lo = min(CLAIM_MIN_PX, hi)
    col_fit = col_w * (1.0 - WRAP_SAFETY)
    metric = kh > 0 or any(serif for _em, serif in claim.words)
    room_fit = room + 0.5 - (HEIGHT_SAFETY_PX if metric else 0.0)
    fs = hi
    fits = False
    while fs > lo:
        h, lines, widest, _n = claim.layout(fs, col_fit)
        if h <= room_fit and widest <= col_fit + 1 and lines <= claim.max_lines:
            fits = True
            break
        fs *= FIT_STEP
    fs = max(fs, lo)
    h, lines, widest, n = claim.layout(fs, col_fit)
    line_w = _balanced_width(claim, fs, col_fit) / 1080.0
    if not fits:
        fits = h <= room_fit and widest <= col_fit + 1 and lines <= claim.max_lines
    # what caps it: the size knob, the band's height, or the column (its
    # words in the line limit, however tall the band)
    limit = "size"
    if fs < CLAIM_MAX_PX * size - 1e-6:
        limit = "band"
        f = CLAIM_MAX_PX * size
        while f > lo:
            _h, li, wi, _n = claim.layout(f, col_fit)
            if wi <= col_fit + 1 and li <= claim.max_lines:
                break
            f *= FIT_STEP
        if f <= fs * 1.0001:
            limit = "width"
    out.update(claim_px=fs, claim_cap=fs * claim.cap / Hd, claim_lines=max(1, n),
               claim_h=h / Hd, room=room / Hd, block_h=(kh + gap + h) / Hd,
               fits=fits, limit=limit, max_lines=claim.max_lines, chars=claim.chars,
               floor=fs <= lo + 1e-6)
    # where it draws (frame fractions; line boxes, balanced as the page
    # balances them): the block centred on y, the
    # claim's widest line and a stacked kicker centred (left-aligned: from
    # the column's left), a kicker set aside in its own box
    x, y = _num(p, "x", 0.5), _num(p, "y", 0.15)
    left = str(p.get("align") or "center") == "left"
    top = y - out["block_h"] / 2.0
    rows = [(line_w, top + (kh + gap) / Hd)]
    if kicker and not out["kicker_beside"]:
        rows.append((out["kicker_w"], top))
    xs = [(x - 0.5 * col_w / 1080.0, x - 0.5 * col_w / 1080.0 + w) if left else
          (x - w / 2.0, x + w / 2.0) for w, _t in rows]
    ink = [min(a for a, _b in xs), min(t for _w, t in rows),
           max(b for _a, b in xs), top + out["block_h"]]
    if out["kicker_beside"]:
        right = x + _num(p, "width", 0.84) / 2.0
        ky, khh = _num(p, "kicker_y", 0.0), out["kicker_h"] / 2.0
        ink = [min(ink[0], right - out["kicker_w"]), min(ink[1], ky - khh),
               max(ink[2], right), max(ink[3], ky + khh)]
    out["ink"] = [round(v, 4) for v in ink]
    return out


# ── captions ──────────────────────────────────────────────────────────────

# caption_motion.html: base px on 9:16 at size 1 and the running row's font.
# lockup/stack set connectors small under a giant hero word; a phrase with
# no hero reads as one MID row (CFG.mid x base) — their running caption size.
MOTION_LOOK_TYPE = {
    "clean": (70, "Inter", 600), "editorial": (72, "Inter Display", 700),
    "lockup": (44 * 1.6, "Inter Display", 700), "pop": (94, "Inter Display", 900),
    "box": (62, "Inter Display", 800), "serif": (66, "Inter Display", 700),
    "glow": (74, "Inter Display", 800), "stack": (48 * 1.7, "Inter Display", 800),
    "mono": (46, "JetBrains Mono", 700),
}
# libass fonts by ASS family name -> bundled file
_ASS_FILES = {
    "Inter Display Black": "InterDisplay-Black.ttf",
    "Inter Display ExtraBold": "InterDisplay-ExtraBold.ttf",
    "Inter Display Bold": "InterDisplay-Bold.ttf",
    "Inter Display": "InterDisplay-Bold.ttf",
    "Anton": "Anton-Regular.ttf", "Bebas Neue": "BebasNeue-Regular.ttf",
    "Archivo Black": "ArchivoBlack-Regular.ttf", "Poppins Black": "Poppins-Black.ttf",
    "Syne ExtraBold": "Syne-ExtraBold.ttf",
    "Playfair Display Black": "PlayfairDisplay-Black.ttf",
    "Instrument Serif": "InstrumentSerif-Regular.ttf",
    "DM Serif Display": "DMSerifDisplay-Regular.ttf", "Montserrat": "Montserrat-Bold.ttf",
    "Plus Jakarta Sans": "PlusJakartaSans-ExtraBold.ttf",
    "Plus Jakarta Sans ExtraBold": "PlusJakartaSans-ExtraBold.ttf",
}
# an unbundled ASS family (DejaVu Sans, the plain default): its cap height
# over its Windows ascent + descent (libass sizes a font by that line height)
_ASS_DEFAULT_CAP = 0.626


def caption_cap(edl, W=1080, H=1920):
    """(cap height as a share of the frame height, a short label) the
    transcript captions are drawn at, or None when the program has no
    transcript captions. The nominal size of the running caption text —
    hero words of the lockup/stack looks are larger, a long cue may shrink."""
    caps = (edl or {}).get("captions")
    if not isinstance(caps, dict) or caps.get("mode") != "from_transcript":
        return None
    import motion_captions
    st = caps.get("style") or {}
    st = st if isinstance(st, dict) else {}
    look = motion_captions.look_of(edl)
    if look:
        px, fam, weight = MOTION_LOOK_TYPE.get(look, MOTION_LOOK_TYPE["clean"])
        sp = motion_captions.style_params(edl)
        if sp.get("font") and look != "mono":
            fam, weight = sp["font"], sp.get("font_weight") or weight
        Hd = design_height(W, H)
        k = min(1.0, math.sqrt(Hd / 1920.0))
        px = px * float(sp.get("size") or 1.0) * k
        return px * cap_ratio(fam, int(weight)) / Hd, f"the '{look}' captions"
    import captions as caplib
    s = caplib._norm_style(st)
    p = caplib._preset_of(s)
    play = (int(W), int(H))
    if p:
        px = caplib._premium_font_px(p, s, play)
        fam = caplib._font_of(p, s)
        label = f"the '{s.get('preset')}' captions"
    else:
        f = max(play[0] / caplib.BASE_PLAY_RES[0], play[1] / caplib.BASE_PLAY_RES[1])
        px = max(10, round(caplib.FONT_SIZES.get(s.get("size"), 40) * f * caplib._size_scale(s)))
        fam = s.get("font") or "DejaVu Sans"
        label = "the captions"
    return px * _ass_cap_ratio(fam) / float(H), label


def _ass_cap_ratio(family):
    """Cap height over the libass font size for an ASS family name."""
    fn = _ASS_FILES.get(" ".join(str(family).split()))
    if fn and os.path.isfile(os.path.join(HERE, "fonts", fn)):
        path = os.path.join(HERE, "fonts", fn)
        return metrics(path)[0] / max(1e-6, ass_line_em(path))
    return _ASS_DEFAULT_CAP


# ── the editorial headline (set_editorial_graphic kind='headline') ────────
# Its rows are texts-layer lines in Inter Display Bold at font_size x the
# frame's short edge, drawn by libass (graphics._compile_item): measured on a
# render, font_size 0.052 (its default) draws a cap height of 0.0177 of a
# 9:16 frame — two thirds of a clean caption track's 0.0265 — and even its
# largest font_size, 0.085, stays under 'l' captions.
EDITORIAL_FONT = "Inter Display Bold"


def ass_text_cap(font_size, W=1080, H=1920, family=EDITORIAL_FONT):
    """Cap height (share of the frame height) of a texts-layer line set at
    ``font_size`` (a share of the frame's short edge) in ASS ``family``."""
    px = max(6, round(float(font_size) * min(int(W), int(H))))
    return px * _ass_cap_ratio(family) / float(H)


def editorial_headline_note(edl, font_size, W=1080, H=1920, max_size=0.085, owns=1.2):
    """The editorial headline's measured cap height against the captions,
    with the font_size that reaches them (or that none up to ``max_size``
    does); '' with no transcript captions or nothing to measure."""
    try:
        ref = caption_cap(edl, W, H)
        if not ref or font_size is None:
            return ""
        target, who = ref
        cap = ass_text_cap(font_size, W, H)
    except Exception:  # noqa: BLE001 — a measurement is advice, never a block
        return ""
    ratio = cap / max(1e-6, target)
    line = (f"\nHEADLINE TYPE (measured from the font files): the headline's cap height is "
            f"{cap:.3f} of the frame height (font_size {float(font_size):g}) vs {who} "
            f"{target:.3f}: {ratio:.2f}x.")
    if ratio >= owns - 1e-6:
        return line
    goal = target * (owns if ratio >= 1.0 - 1e-6 else 1.0)
    short = min(int(W), int(H))
    need = math.ceil(math.ceil(goal * H / _ass_cap_ratio(EDITORIAL_FONT) - 1e-9)
                     / short * 1000.0) / 1000.0
    them = "them" if goal <= target else f"{owns:g}x"
    reach = (f"font_size {need:g} reaches {them} (a shorter claim or a taller box keeps it "
             "in three lines)" if need <= max_size + 1e-9 else
             f"no font_size up to {max_size:g} reaches {them}")
    alt = ("; over a card or letterbox the persistent headline template "
           "(add_motion_graphic template='headline') sizes its claim to the band and "
           "measures it at write")
    if ratio >= 1.0 - 1e-6:
        return line + f" Under {owns:g}x it reads as the captions' size, not the hook: {reach}{alt}."
    return (line + " NOTE (headline type): SMALLER than the captions — the hook must not be "
            f"the smallest type on screen: {reach}{alt}.")


# ── the fix: band, length, card ───────────────────────────────────────────

def needed_band(params, W, H, target_cap, kicker_beside=None, hi=0.45):
    """The smallest band height (frame share) at which the claim measures at
    least ``target_cap``, or None when no band does at this size."""
    p = dict(params or {})
    if headline_layout(dict(p, height=hi), W, H, kicker_beside)["claim_cap"] < target_cap - 1e-6:
        return None
    lo_, hi_ = 0.02, hi
    for _ in range(22):
        mid = (lo_ + hi_) / 2.0
        if headline_layout(dict(p, height=mid), W, H, kicker_beside)["claim_cap"] >= target_cap - 1e-6:
            hi_ = mid
        else:
            lo_ = mid
    return math.ceil(hi_ * 1000.0) / 1000.0


def needed_size(params, W, H, target_cap):
    """The ``size`` whose largest claim reaches ``target_cap`` (None past the
    template's SIZE_MAX)."""
    cap = STYLES.get(str((params or {}).get("style") or "sans"), STYLES["sans"])
    ratio = metrics(font_path(cap["family"], cap["weight"]))[0]
    s = target_cap * design_height(W, H) / (CLAIM_MAX_PX * ratio)
    s = math.ceil(s * 100.0) / 100.0
    return s if s <= SIZE_MAX + 1e-9 else None


def max_claim_chars(params, W, H, target_cap, kicker_beside=None):
    """About how many characters of this claim the present band holds at
    ``target_cap`` (0 when not one line fits)."""
    p = dict(params or {})
    lay = headline_layout(p, W, H, kicker_beside)
    Hd = design_height(W, H)
    claim = _Claim(p)
    fs = target_cap * Hd / max(1e-6, claim.cap)
    lines = int((lay["room"] * Hd + 0.5) // (fs * claim.st["lh"]))
    if lines < 1:
        return 0
    col_w = 1080.0 * _num(p, "width", 0.84)
    ems = sum(em for em, _s in claim.words) + claim.space_em * max(0, len(claim.words) - 1)
    per_char = ems * fs / max(1, claim.chars)
    per_line = col_w / max(1e-6, per_char)
    # a line breaks at a word: about half a word short of full on average
    n = int(min(lines, 3) * per_line * 0.88)
    return min(n, LONG_CLAIM) if lines < 3 else n


def mark_strip(W, H, top=None):
    """[x0, y0, x1, y1] (frame fractions) of the free strip beside the
    free-tier mark, above the band (the mark sits top-left only), or None.
    ``top`` is the highest a band graphic may reach (the feed header)."""
    import keepout
    try:
        mark = keepout.watermark_zone(int(W), int(H))
    except Exception:  # noqa: BLE001
        return None
    y0 = float(top if top is not None else keepout.SAFE_Y0)
    y1 = float(mark[3])
    x0, x1 = float(mark[2]) + STRIP_GAP, keepout.SAFE_X1
    if y1 - y0 < 0.02 or x1 - x0 < 0.15:
        return None
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def kicker_beside_fits(params, W, H, strip):
    """(fits, kicker_y): the kicker set beside the mark, right-aligned to the
    column, inside ``strip`` at the size it takes in the stack."""
    p = params or {}
    kicker = " ".join(str(p.get("kicker") or "").split())
    if not kicker or not strip or str(p.get("align") or "center") != "center":
        return False, 0.0
    lay = headline_layout(p, W, H, kicker_beside=True)
    right = _num(p, "x", 0.5) + _num(p, "width", 0.84) / 2.0
    if right > strip[2] + 1e-6:
        return False, 0.0
    if lay["kicker_lines"] != 1 or right - lay["kicker_w"] < strip[0]:
        return False, 0.0
    kh = lay["kicker_h"]
    if kh > strip[3] - strip[1]:
        return False, 0.0
    return True, round((strip[1] + strip[3]) / 2.0, 4)


def kicker_box(params, W, H, lay=None):
    """[x0, y0, x1, y1] (frame fractions) of a kicker set aside
    (``kicker_y`` > 0, a centred block): right-aligned to the column, its
    line centred on kicker_y. None when the kicker is in the stack."""
    p = params or {}
    lay = lay or headline_layout(p, W, H)
    if not lay["kicker_beside"]:
        return None
    right = _num(p, "x", 0.5) + _num(p, "width", 0.84) / 2.0
    ky, kh = _num(p, "kicker_y", 0.0), lay["kicker_h"]
    return [round(right - lay["kicker_w"], 4), round(ky - kh / 2.0, 4), round(right, 4),
            round(ky + kh / 2.0, 4)]


def kicker_chars_beside(params, W, H, strip):
    """About how many characters of kicker fit on one line beside the mark
    (in ``strip``) at the size this kicker is set at; 0 when none do."""
    p = params or {}
    kicker = " ".join(str(p.get("kicker") or "").split())
    if not kicker or not strip:
        return 0
    right = min(_num(p, "x", 0.5) + _num(p, "width", 0.84) / 2.0, strip[2])
    # a shorter kicker is set at the largest kicker size (a long one was
    # fitted down to its column): its characters at that size
    path = font_path(KICKER["family"], KICKER["weight"])
    top_px = max(KICKER_MAX_PX * _num(p, "size", 1.0),
                 MIN_CAP * design_height(W, H) / metrics(path)[0])
    per = text_width(path, kicker, top_px, KICKER["track"]) / 1080.0 / len(kicker)
    return int((right - strip[0]) / per) if per > 0 else 0


def largest_card(top, W=1080, H=1920, floor=None, bottom=CARD_BOTTOM, src_px=None,
                 upscale=2.0):
    """The largest picture card under a band: its ``box`` (full width, or
    narrower where ``src_px`` — the source window in pixels — would be
    enlarged past ``upscale``), its area, and whether it meets ``floor``
    (the Look's picture-area floor). With a floor, ``floor_top`` is the
    lowest card top that still meets it at that width (None when none does)."""
    top = float(top)
    h = max(0.0, float(bottom) - top)
    w = 1.0
    if src_px:
        w = min(w, upscale * float(src_px[0]) / float(W))
        h = min(h, upscale * float(src_px[1]) / float(H))
    x0 = round((1.0 - w) / 2.0, 3)
    box = [x0, round(top, 3), round(1.0 - x0, 3), round(top + h, 3)]
    area = round((box[2] - box[0]) * (box[3] - box[1]), 3)
    out = {"box": box, "area": area}
    if floor is not None:
        out["meets_floor"] = area >= float(floor) - 1e-6
        tall = float(floor) / max(1e-6, box[2] - box[0])
        lim = float(bottom) - tall
        if src_px and tall > upscale * float(src_px[1]) / float(H) + 1e-9:
            lim = None
        out["floor_top"] = round(lim, 3) if lim is not None and lim > 0 else None
    return out
