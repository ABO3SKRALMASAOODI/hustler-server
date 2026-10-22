"""The cover headline (motion/templates/cover_headline.html), measured.

The owner's ask (Oct 2026): "make some of the shorts start with a headline
like this picture" — a short whose OPENING FRAME is one huge centred title
in tall condensed white caps ('THE RUTHLESS FOCUS OF / MARK ZUCKERBERG' over
the speaker, three lines at about 70% of the frame width, a soft shadow, no
band), held a few seconds, so the frame a feed shows before anyone presses
play is a thumbnail that sells the story.

The template lays it out in the browser; this module mirrors those rules on
the same font files so the lanes without a browser (the agent, MCP and
shorts writes) know the box it draws: the keep-out moves it off the face
(keepout.nominal_ink) and the captions are planned around it. Pillow's
advances carry no pair kerning, so a line measured here runs a hair WIDER
than the page's: the estimate errs a touch small, never large.

The rules (keep in step with the template):
- every line at ONE size; the widest fills ``width`` of the frame;
- a line at most LINE_MAX of the frame height, the block at most BLOCK_MAX;
- the FEWEST lines (2-4; one word: one line; or the editor's forced ' / '
  breaks) whose caps reach TARGET_CAP of the frame height (a cover is huge:
  the owner's reference set three lines at ~6%) without breaking a
  *starred* span, and among those (or among all, when none reaches it) the
  best score: the type size, less RAGGED x
  how ragged the lines are (1 - narrowest / widest), LINE_COST per line
  past two, WEAK_END per line that ends on a little word ('SLEEPS AT / THE
  FACTORY'), SPLIT_ACCENT per break inside a *starred* span and SPLIT_NAME
  per break inside a name (two capitalised words, not the title's first:
  'STEVE / JOBS');
- centred on (x, y), kept inside the 9:16 safe band (y 0.08-0.80).
"""

import functools
import re

# the faces the template offers -> (CSS family, tracking em, line height)
FACES = {
    "Anton": ("Anton", 0.004, 1.0),
    "League Gothic": ("League Gothic", 0.012, 0.98),
    "Bebas Neue": ("Bebas Neue", 0.01, 0.98),
}
TARGET_CAP = 0.055
LINE_MAX = 0.115
BLOCK_MAX = 0.36
RAGGED = 0.5
LINE_COST = 0.93
WEAK_END = 0.75
SPLIT_ACCENT = 0.85
SPLIT_NAME = 0.75
MAX_LINES = 4
# a line ending on one of these reads as broken mid-phrase
WEAK_WORDS = frozenset("""a an the of to at in on for and or but with by from as
    is are was his her my your our their its this that""".split())
SAFE_TOP, SAFE_BOTTOM = 0.08, 0.80
MIN_PX = 24.0
# the gap between words the page sets (.mg-w margin-right: .2em)
WORD_GAP_EM = 0.2

# The opening it is: it starts by COVER_START_S (frame 0 is the point) and
# holds COVER_MIN_S-COVER_MAX_S (long enough to read and to be the cover,
# short enough that the talk is not held back).
COVER_START_S = 0.5
COVER_MIN_S = 2.0
COVER_MAX_S = 3.5
TEMPLATE = "cover_headline"

_FORCED = re.compile(r"\s+/\s*|\s*/\s+")


def is_cover(item):
    return isinstance(item, dict) and item.get("template") == TEMPLATE


def _words(text):
    """[(WORD, accent, capitalised)] — MG.starWords, upper-cased as the page
    sets it; ``capitalised`` as typed (a name's words)."""
    import band_type
    return [(t.upper(), bool(acc), t[:1].isupper())
            for t, acc in band_type.star_words(text)]


def rows_of(text):
    """The editor's forced lines (' / '), each a list of (WORD, accent)."""
    rows = [_words(r) for r in _FORCED.split(str(text or "").strip())]
    return [r for r in rows if r]


@functools.lru_cache(maxsize=8)
def _path(family):
    import band_type
    return band_type.font_path(family, 400)


def _line_em(family, track, words):
    """A line's width in em at the page's spacing (advances + tracking after
    every character + the word gap)."""
    import band_type
    words = [w[0] for w in words]
    path = _path(family)
    if not path:
        return 0.45 * sum(len(w) for w in words) + WORD_GAP_EM * max(0, len(words) - 1)
    em = sum(band_type.text_width(path, w, 1.0, track) for w in words)
    return em + WORD_GAP_EM * max(0, len(words) - 1)


def _splits(ws, n):
    if n == 1:
        return [[ws]]
    out = []
    for a in range(1, len(ws) - n + 2):
        for rest in _splits(ws[a:], n - 1):
            out.append([ws[:a]] + rest)
    return out


def layout(params, W=1080, H=1920):
    """{"lines": [[words]], "fs": px, "cap": cap height share of H,
    "block_h": share of H, "width": widest line share of W, "box": [x0, y0,
    x1, y1] the ink (frame fractions)} for these params on a W x H frame."""
    p = params or {}
    family, track, lh = FACES.get(str(p.get("font") or "Anton"), FACES["Anton"])
    try:
        width = float(p.get("width") or 0.72)
    except (TypeError, ValueError):
        width = 0.72
    max_w = float(W) * width
    rows = rows_of(p.get("text"))
    if not rows:
        rows = [[("-", False, False)]]
    memo = {}

    def em(ws):
        key = tuple(ws)
        if key not in memo:
            memo[key] = _line_em(family, track, ws)
        return memo[key]

    first = rows[0][0] if rows and rows[0] else None
    import band_type
    path = _path(family)
    cap_em, asc, desc = band_type.metrics(path) if path else (0.86, 1.18, 0.33)

    def size(c):
        ems = [em(ws) for ws in c]
        widest = max(max(ems), 1e-3)
        fs = min(max_w / widest, LINE_MAX * H, BLOCK_MAX * H / (len(c) * lh))
        ragged = 1.0 - min(ems) / widest if len(c) > 1 else 0.0
        weak = sum(1 for ws in c[:-1] if ws[-1][0].lower() in WEAK_WORDS)
        split = sum(1 for a, b in zip(c, c[1:]) if a[-1][1] and b[0][1])
        name = sum(1 for a, b in zip(c, c[1:])
                   if a[-1][2] and b[0][2] and a[-1] is not first)
        score = (fs * (1.0 - RAGGED * ragged) * LINE_COST ** max(0, len(c) - 2)
                 * WEAK_END ** weak * SPLIT_ACCENT ** split * SPLIT_NAME ** name)
        return score, fs, c, split
    if len(rows) > 1:
        _s, fs, pick, _x = size(rows)
    else:
        words = rows[0]
        lo = 1 if len(words) == 1 else 2
        cands = [size(c) for n in range(lo, min(MAX_LINES, len(words)) + 1)
                 for c in _splits(words, n)]
        # a *starred* span is kept on one line when any break allows it
        whole = [r for r in cands if not r[3]] or cands
        big = [r for r in whole if r[1] * cap_em >= TARGET_CAP * H - 0.5]
        if big:
            _s, fs, pick, _x = sorted(big, key=lambda r: (len(r[2]), -r[0]))[0]
        else:
            _s, fs, pick, _x = sorted(whole, key=lambda r: (-r[0], len(r[2])))[0]
    fs = max(MIN_PX, fs)
    cap = cap_em
    widest_px = max(em(ws) for ws in pick) * fs
    block_px = len(pick) * fs * lh
    try:
        x = float(p.get("x") or 0.5)
        y = float(p.get("y") or 0.5)
    except (TypeError, ValueError):
        x, y = 0.5, 0.5
    top = min(max(y * H - block_px / 2.0, SAFE_TOP * H), SAFE_BOTTOM * H - block_px)
    # the ink: from the first line's cap top to the last baseline (caps sit
    # on it), plus the contact shade under it; a line box's baseline sits
    # half the leading (line height - ascent - descent) plus the ascent down
    base = ((lh - (asc + desc)) / 2.0 + asc) * fs
    ink_top = top + base - cap * fs
    ink_bottom = top + (len(pick) - 1) * lh * fs + base + 0.06 * fs
    x0 = x * W - widest_px / 2.0
    box = [max(0.0, x0 / W), max(0.0, ink_top / H),
           min(1.0, (x0 + widest_px) / W), min(1.0, ink_bottom / H)]
    return {"lines": [[w[0] for w in ws] for ws in pick], "fs": fs, "cap": cap * fs / H, "block_h": block_px / H,
            "width": widest_px / W, "box": [round(v, 4) for v in box]}


def nominal_ink(params, frame=None):
    """keepout.nominal_ink's estimate for a cover headline (frame fractions)."""
    W, H = frame or (1080, 1920)
    return tuple(layout(params, W, H)["box"])


def notes(item, others, prog=None):
    """Advisory lines for a cover headline write: it is the opening, one per
    short, held 2-3.5 s; and a hook title that also opens the short."""
    out = []
    s, e = float(item.get("start") or 0.0), float(item.get("end") or 0.0)
    if s > COVER_START_S + 1e-6:
        out.append(f"NOTE (cover): it starts at {s:g}s; a cover is the short's OPENING frame "
                   "(the thumbnail), so start it at 0. Later in the short use a headline "
                   "or a display word_slam instead.")
    twin = [m.get("id") for m in others or [] if is_cover(m)]
    if twin:
        out.append(f"NOTE (cover): '{twin[0]}' is already this short's cover headline; "
                   "a short has ONE cover. Remove one of them.")
    hook = [m.get("id") for m in others or []
            if m.get("template") == "word_slam"
            and str((m.get("params") or {}).get("tier") or "") == "hook"
            and float(m.get("start") or 0.0) < e - 0.05]
    if hook:
        out.append(f"NOTE (cover): '{hook[0]}' is a hook-tier title over the cover's window; "
                   "the cover IS the hook here. Remove the hook title or start it after "
                   f"the cover ends ({e:g}s).")
    held = e - s
    if held < COVER_MIN_S - 1e-6 or held > COVER_MAX_S + 1e-6:
        out.append(f"NOTE (cover): it holds {held:.1f}s; a cover reads and lands as the "
                   f"thumbnail in {COVER_MIN_S:g}-{COVER_MAX_S:g}s (the captions wait until it "
                   "exits, so every second past that is speech nobody reads).")
    if s <= COVER_START_S + 1e-6:
        out.append("COVER: complete on frame 0 (the thumbnail; no entrance), the captions "
                   "wait until it exits (a standing headline band yields to it), then it "
                   f"fades into the talk. Leave the opening's first {held:.1f}s to it: no "
                   "other graphic, zoom or sound on top.")
    return out
