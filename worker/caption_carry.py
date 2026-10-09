"""Word-level caption muting: a graphic hides only the spoken words it shows.

Showcase judging (Oct 2026): a graphic muted the captions for its WHOLE
window, so spoken words it did not show vanished for sound-off viewers ("the
1960s technology meant" under a paraphrased hook, "green revolution
agriculture" under a list of other items, "we're solving the problems of"
before a phrase build's first row), while a counter that did not mute showed
"140" in the graphic and again in the caption.

The contract (MotionItem.mute_captions):

- unset (the default): WORD-LEVEL. The words spoken in the graphic's window
  that it actually shows — its visible text params matched against the
  transcript, tolerant of case, punctuation, plurals, numerals vs number
  words and *starred* accents — are dropped from the captions (one
  connector word or two between shown words go with them, and so does a
  quoted phrase that starts up to CARRY_LEAD_S before the graphic). Every
  other spoken word keeps its caption. While the graphic is up those
  captions sit in a band clear of its drawn box (``MotionItem.drawn``, the
  write-time probe) and of the speaker's face (the spatial index), found
  the way the motion captions already place by band. When no band is clear,
  a template that replaces speech (spec ``mutes_captions``) mutes them — the
  write reply's NOTE names the words — and any other template keeps the
  caption where it was.
- true: the graphic replaces the captions for its whole window (an explicit
  choice; the behaviour every graphic used to have).
- false: the captions keep running beside it; only a number or a hero word
  it shows (a *starred* word, a counter's value) is not repeated in the
  caption at the same moment. Placement as unset; never muted.

Everything here is a pure function of the EDL, the index and the timeline:
the libass captions, the motion captions, the write-time lint and the
sound-off audit all compute the same plan. An item without a drawn box
(written before the probe stored one, or measured at another frame shape
than today's: ``drawn_ar``) is treated as old behaviour (a speech-replacing
template mutes what it does not carry); the renderer and audit_captions
fill the box in by probing before they build captions
(motion_layer.fill_drawn). A composition with no ink (a light leak) has no
box and never moves or mutes a caption.
"""

import difflib
import re

MODE_ALL, MODE_WORDS, MODE_HERO = "all", "words", "hero"

# A quoted phrase may start this long before its graphic (the kicker "it's
# not quite been" under a slam on "enough"): those words are handed to the
# graphic instead of being read twice.
CARRY_LEAD_S = 0.5
# Connector words between shown words go with them (one or two of "and",
# "the", "of" left alone as a flashing card are noise, not speech).
ABSORB_MAX = 2
# Caption geometry (frame fractions): a two-line block around its anchor,
# the smallest band the motion caption template lays a block into, the
# column a centred caption occupies, and the clearances kept from graphics
# and faces.
CAP_HALF_H = 0.055
MIN_BAND_H = 0.085
ZONE_HALF_H = 0.11
COLUMN = (0.15, 0.85)
GRAPHIC_PAD = 0.015
FACE_PAD = 0.01
# Where a face is assumed when none was measured near the moment: a talking
# head's face in a 9:16 or 16:9 frame. Haar misses profiles, so "no face
# found" is not evidence of no face; assuming one only ever blocks a move.
FACE_PRIOR = (0.2, 0.12, 0.8, 0.5)
# A detector's face box runs from the brows to the chin; the head (hair,
# forehead) rises about this share of its height above it.
HEAD_ABOVE = 0.45
FACE_NEAR_S = 1.5
FACE_FAR_S = 6.0
# A caption that would start this little before an occupying graphic leaves
# waits for it instead of touching it (the 'computers' under the hook title).
START_WAIT_S = 0.3
# Sound-off coverage: spoken spans longer than this with nothing on screen
# saying them are reported.
SOUND_OFF_GAP_S = 0.6

# ── tokens ────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_STAR_RE = re.compile(r"\*([^*]+)\*")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")
# Function words do not decide whether a graphic carries what was said.
FUNCTION_WORDS = frozenset((
    "a an the and or but so to of in on at for with from by as is are was were be been "
    "being am i i'm you he she it it's its we they me him her us them my your his our "
    "their this that these those do does did not no yes just very really than then there "
    "here what which who when where why how if into out up down over about like um uh oh "
    "okay ok yeah well also too can could would should will might must have has had all "
    "some any you're we're they're that's there's i've i'd you've percent per cent").split())
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"thousand": 1000, "million": 10 ** 6, "billion": 10 ** 9}
# Row fields that position or style a row rather than print words.
NON_TEXT_FIELDS = frozenset(("at", "side", "role", "size", "accent", "highlight", "font"))


def _base(t):
    return t.casefold().replace("’", "'")


def norm_token(t):
    """One comparison token: case-folded, possessive and plural folded, a
    single number word as its numeral."""
    t = _base(t)
    if t in FUNCTION_WORDS:
        return t                       # "this", "does" are not plurals
    if t.endswith("'s"):
        t = t[:-2]
    if t in _UNITS:
        return str(_UNITS[t])
    if t in _TENS:
        return str(_TENS[t])
    if t == "hundred":
        return "100"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]                     # "characters" carries "character"
    return t


def _number_at(raw, i):
    """(end, value) of a spelled number starting at raw[i] ("one hundred
    and forty" -> 140), or (i, None)."""
    total, cur, k, last, n = 0, 0, i, None, len(raw)
    while k < n:
        t = raw[k]
        nxt = raw[k + 1] if k + 1 < n else None
        if t == "a" and k == i and nxt in ("hundred",) + tuple(_SCALES):
            cur, last = 1, "unit"
        elif t in _UNITS:
            if last in ("unit", "teen") or (last == "tens" and _UNITS[t] >= 10):
                break
            cur += _UNITS[t]
            last = "teen" if _UNITS[t] >= 10 else "unit"
        elif t in _TENS:
            if last in ("unit", "teen", "tens"):
                break
            cur += _TENS[t]
            last = "tens"
        elif t == "hundred":
            if last == "hundred":
                break
            cur = max(cur, 1) * 100
            last = "hundred"
        elif t in _SCALES:
            if last is None:
                break
            total += max(cur, 1) * _SCALES[t]
            cur, last = 0, "scale"
        elif t == "and" and last in ("hundred", "scale") and (nxt in _UNITS or nxt in _TENS):
            k += 1
            continue
        else:
            break
        k += 1
    if last is None or (k - i == 1 and raw[i] == "a"):
        return i, None
    return k, total + cur


def _token_rows(texts):
    """Per input text, its comparison tokens; spelled numbers spanning
    several texts ("one" "hundred" "forty") become one numeral on each."""
    raws = [[_base(t) for t in _TOKEN_RE.findall(_THOUSANDS_RE.sub("", str(x or "")))]
            for x in texts]
    flat = [(r, j) for r, row in enumerate(raws) for j in range(len(row))]
    seq = [raws[r][j] for r, j in flat]
    out = [[] for _ in raws]
    i = 0
    while i < len(seq):
        end, val = _number_at(seq, i)
        if val is not None and end - i > 1:
            for r in sorted({flat[x][0] for x in range(i, end)}):
                out[r].append(str(val))
            i = end
            continue
        out[flat[i][0]].append(norm_token(seq[i]))
        i += 1
    return out


def tokens(text):
    """Comparison tokens of one text (graphic line or transcript phrase)."""
    return _token_rows([text])[0]


def is_content(tok):
    return tok not in FUNCTION_WORDS


# ── what a graphic shows ─────────────────────────────────────────────────

def _spec(item):
    import motion_templates
    try:
        return motion_templates.spec(item.get("template"))
    except (ValueError, KeyError, TypeError):
        return {}


def mode(item):
    """MODE_ALL (mute_captions=true), MODE_HERO (false) or MODE_WORDS
    (unset: word-level)."""
    m = item.get("mute_captions")
    if m is True:
        return MODE_ALL
    if m is False:
        return MODE_HERO
    return MODE_WORDS


def replaces_speech(item):
    """Does the template exist to say the spoken words (spec mutes_captions)?
    Decides the fallback when no caption band is clear of it."""
    return bool(_spec(item).get("mutes_captions"))


_SERIES_RE = re.compile(r"\s*[-+]?\d[\d,]*(?:\.\d+)?\s*")


def _series_value(v):
    """One point of a data series: a number, or a number written as a string
    (templates take series values 'as strings')."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    return isinstance(v, str) and bool(_SERIES_RE.fullmatch(v))


def graphic_lines(item):
    """[(param, text)] the item prints: its text params (list items and row
    text fields included) and, for authored HTML, the markup's text."""
    pspec = _spec(item).get("params") or {}
    out = []
    for key, val in (item.get("params") or {}).items():
        kind = (pspec.get(key) or {}).get("type", "str")
        if kind not in ("str", "text", "list", "rows"):
            continue
        vals = val if isinstance(val, list) else [val]
        if kind == "list" and vals and all(_series_value(v) for v in vals):
            # a data series (line_chart values, a stat_card spark): drawn as
            # a line, never printed — "12" said over it is still captioned
            continue
        for v in vals:
            if isinstance(v, dict):
                out += [(key, str(x)) for f, x in v.items()
                        if f not in NON_TEXT_FIELDS and isinstance(x, str)]
            elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
                out.append((key, str(v)))
    if item.get("template") == "html" and item.get("html"):
        body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", item["html"])
        out.append(("html", re.sub(r"<[^>]+>", " ", body)))
    return [(k, v) for k, v in out if tokens(v)]


def shown_tokens(item):
    """(token sequence, hero token set, solo token set) of everything the
    item prints. Hero tokens are numbers, *starred* words and a counter/stat
    ``value``; solo tokens are hero tokens plus the content word of every
    one-word line (a label, a list row, a slammed word)."""
    seq, hero, solo = [], set(), set()
    for key, text in graphic_lines(item):
        toks = tokens(text)
        seq += toks
        hero.update(t for t in toks if any(c.isdigit() for c in t))
        if key == "value":
            hero.update(t for t in toks if is_content(t))
        for starred in _STAR_RE.findall(text):
            hero.update(t for t in tokens(starred) if is_content(t))
        for line in re.split(r"\s*(?:/|\n)\s*", text):
            content = [t for t in tokens(line) if is_content(t)]
            if len(content) == 1:
                solo.add(content[0])
    return seq, hero, solo | hero


def carried_indices(word_toks, seq, lead_n=0, solo=frozenset()):
    """Indices of the words (``word_toks`` = per-word token lists) that a
    graphic printing ``seq`` shows.

    Words inside a run of two or more tokens printed in the spoken order are
    shown (function words included). Outside such a run a content word is
    shown when its token is printed AND it stands on its own on the graphic
    (``solo``: a number, a starred word, a one-word line) or a spoken
    neighbour is shown too — a paraphrased line that happens to share one
    word with the sentence ("computer fonts were GARBAGE" over "every
    computer to date") does not punch a hole in the caption. The first
    ``lead_n`` words are spoken before the graphic starts and count only as
    part of a run that continues into its window."""
    content = {t for t in seq if is_content(t)}
    flat = [(t, i) for i, ts in enumerate(word_toks) for t in ts]
    in_run, lead_runs = set(), []
    if seq and flat:
        sm = difflib.SequenceMatcher(None, seq, [t for t, _i in flat], autojunk=False)
        for blk in sm.get_matching_blocks():
            # a run of connectors alone ("of the" in "THE END OF THE WORLD"
            # over "one of the best years") is not the graphic's words
            if blk.size >= 2 and any(is_content(seq[blk.a + k]) for k in range(blk.size)):
                idx = {flat[blk.b + k][1] for k in range(blk.size)}
                if max(idx) >= lead_n:
                    in_run |= idx
                elif max(idx) == lead_n - 1:
                    lead_runs.append(idx)       # ends right where the window starts
    member = {i for i, ts in enumerate(word_toks)
              if i >= lead_n and any(t in content for t in ts if is_content(t))}
    carried = in_run | {i for i in member if set(word_toks[i]) & solo}

    def beside_shown(i):
        # the nearest spoken content word either side, past at most
        # ABSORB_MAX connectors ("rockets and supersonic")
        for step in (-1, 1):
            j, hops = i + step, 0
            while 0 <= j < len(word_toks) and hops <= ABSORB_MAX:
                if j in carried:
                    return True
                if any(is_content(t) for t in word_toks[j]):
                    break
                j, hops = j + step, hops + 1
        return False
    grew = True
    while grew:
        grew = False
        for i in sorted(member - carried):
            if beside_shown(i):
                carried.add(i)
                grew = True
    # a quoted run said just before the graphic goes with it only when the
    # graphic's own words carry straight on from it
    for idx in lead_runs:
        if lead_n in carried:
            carried |= idx
    return carried


# ── geometry ─────────────────────────────────────────────────────────────

def _frame_wh(edl, index):
    canvas = edl.get("canvas")
    if canvas:
        return float(canvas["width"]), float(canvas["height"])
    video = (index or {}).get("video") or {}
    ratio = (edl.get("frame") or {}).get("ratio") or "source"
    sw, sh = video.get("width"), video.get("height")
    if sw and sh:
        import renderer
        return tuple(float(v) for v in renderer.frame_dims(float(sw), float(sh), ratio))
    if ratio != "source":
        try:
            a, b = (float(x) for x in ratio.split(":"))
            return a, b
        except ValueError:
            pass
    return 16.0, 9.0


def frame_ar(edl, index):
    """The output frame's aspect as H/W (what a drawn box is stamped with)."""
    w, h = _frame_wh(edl, index)
    return h / max(w, 1e-6)


def safe_range(edl, index):
    """Vertical platform-safe text area, as the motion caption template
    lays it out (worker/motion/templates/caption_motion.html SAFE)."""
    w, h = _frame_wh(edl, index)
    ar = h / max(w, 1e-6)
    if ar >= 1.6:
        return 0.08, 0.80
    if ar > 1.15:
        return 0.06, 0.90
    if ar >= 0.95:
        return 0.06, 0.92
    return 0.07, 0.92


# A drawn box measured at another frame shape (stamped ``drawn_ar`` = H/W)
# says nothing about this one: a template lays out against its canvas, so a
# box measured at 16:9 is the wrong box after set_frame 9:16.
DRAWN_AR_TOL = 0.02


def drawn_fresh(item, ar=None):
    """Was the item's drawn box measured at this frame aspect (H/W)? An
    unstamped box is trusted (no shape to compare)."""
    if ar is None or item.get("drawn_ar") is None:
        return True
    try:
        return abs(float(item["drawn_ar"]) - float(ar)) <= DRAWN_AR_TOL
    except (TypeError, ValueError):
        return False


def drawn_box(item, ar=None):
    """The item's drawn box [x0, y0, x1, y1] (frame fractions) or None
    (never measured, or measured at another frame aspect than ``ar``)."""
    b = item.get("drawn")
    try:
        x0, y0, x1, y1 = (float(v) for v in b)
    except (TypeError, ValueError):
        return None
    if not drawn_fresh(item, ar):
        return None
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _in_column(box):
    return box[2] > COLUMN[0] and box[0] < COLUMN[1]


def _frame_at(edl, t):
    frame = edl.get("frame") or {}
    mode_, focus = frame.get("mode") or "crop", (frame.get("focus_x"), frame.get("focus_y"))
    for span in frame.get("focus_track") or []:
        try:
            if float(span.get("t0")) <= t <= float(span.get("t1")):
                mode_ = span.get("mode") or mode_
                focus = (span.get("x") if span.get("x") is not None else focus[0],
                         span.get("y") if span.get("y") is not None else focus[1])
                break
        except (TypeError, ValueError):
            continue
    return mode_, focus


def _face_to_output(edl, index, t, box):
    """A source-frame face box mapped into the output frame, or None."""
    video = (index or {}).get("video") or {}
    try:
        sw, sh = float(video["width"]), float(video["height"])
    except (KeyError, TypeError, ValueError):
        return None
    import renderer
    frame = edl.get("frame") or {}
    W, H = renderer.frame_dims(sw, sh, frame.get("ratio") or "source")
    mode_, focus = _frame_at(edl, t)
    src, dest = renderer.picture_mapping(sw, sh, W, H, mode_, focus, frame.get("picture"))
    fx0, fy0, fx1, fy1 = src
    dx0, dy0, dx1, dy1 = dest
    x0, y0, x1, y1 = (float(v) for v in box)
    x0, y0, x1, y1 = max(x0, fx0), max(y0, fy0), min(x1, fx1), min(y1, fy1)
    if x1 <= x0 or y1 <= y0:
        return None
    return (dx0 + (x0 - fx0) / (fx1 - fx0) * (dx1 - dx0),
            dy0 + (y0 - fy0) / (fy1 - fy0) * (dy1 - dy0),
            dx0 + (x1 - fx0) / (fx1 - fx0) * (dx1 - dx0),
            dy0 + (y1 - fy0) / (fy1 - fy0) * (dy1 - dy0))


def faces_near(edl, index, src_lo, src_hi):
    """Output-frame face boxes measured around a source span: samples inside
    it (± FACE_NEAR_S), else the nearest measured within FACE_FAR_S, else
    the talking-head prior. Never empty: an unmeasured face blocks a move."""
    samples = []
    for s in (((index or {}).get("spatial") or {}).get("samples") or []):
        try:
            samples.append((float(s["t"]), s.get("faces") or []))
        except (KeyError, TypeError, ValueError):
            continue
    for reach in (FACE_NEAR_S, FACE_FAR_S):
        out = []
        for t, faces in samples:
            if src_lo - reach <= t <= src_hi + reach:
                for f in faces:
                    try:
                        m = _face_to_output(edl, index, t, f)
                    except Exception:  # noqa: BLE001 — unmappable sample
                        m = None
                    if m:
                        h = m[3] - m[1]
                        out.append((m[0], max(0.0, m[1] - HEAD_ABOVE * h), m[2], m[3]))
        if out:
            return out
    return [FACE_PRIOR]


def _subtract(ivs, lo, hi):
    out = []
    for a, b in ivs:
        if hi <= a or lo >= b:
            out.append((a, b))
            continue
        if lo > a:
            out.append((a, lo))
        if hi < b:
            out.append((hi, b))
    return out


def band_letter(y):
    return "t" if y < 0.36 else "m" if y < 0.62 else "b"


_POSITION = {"t": "top", "m": "middle", "b": "bottom"}


def collides(boxes, y):
    """Does a caption block anchored at ``y`` touch any graphic box?"""
    lo, hi = y - CAP_HALF_H, y + CAP_HALF_H
    return any(_in_column(b) and b[1] - GRAPHIC_PAD < hi and b[3] + GRAPHIC_PAD > lo
               for b in boxes)


def clear_band(boxes, faces, safe, normal_y):
    """{y, b, z, position} of the clear band nearest ``normal_y`` that
    misses every graphic box and face, or None when none is tall enough."""
    ivs = [tuple(safe)]
    for b in boxes:
        if _in_column(b):
            ivs = _subtract(ivs, b[1] - GRAPHIC_PAD, b[3] + GRAPHIC_PAD)
    for f in faces:
        ivs = _subtract(ivs, f[1] - FACE_PAD, f[3] + FACE_PAD)
    best = None
    for a, b in ivs:
        if b - a < MIN_BAND_H - 1e-9:
            continue
        half = min(CAP_HALF_H, (b - a) / 2.0)
        y = min(max(normal_y, a + half), b - half)
        key = abs(y - normal_y)
        if best is None or key < best[0]:
            best = (key, y, a, b)
    if best is None:
        return None
    _k, y, a, b = best
    z0, z1 = max(a, y - ZONE_HALF_H), min(b, y + ZONE_HALF_H)
    letter = band_letter(y)
    return {"y": round(y, 4), "b": letter, "z": [round(z0, 4), round(z1, 4)],
            "position": _POSITION[letter]}


def normal_place(edl, src_mid):
    """(anchor_y, band) the captions use at this source moment without any
    graphic (the measured placement track, else the style)."""
    import motion_captions
    caps = edl.get("captions") or {}
    style = dict(caps.get("style") or {})
    track = caps.get("placement_track")
    if not motion_captions.look_of(edl) and style.get("anchor_y") is None:
        import captions as caplib
        for span in track or []:
            if float(span.get("t0", 0)) <= src_mid <= float(span.get("t1", 0)):
                pos = span.get("position") or "bottom"
                y = span.get("anchor_y")
                y = float(y) if y is not None else caplib.PREMIUM_ANCHOR_Y.get(pos, 0.8)
                return y, _BAND.get(pos, "b")
        st = caplib._norm_style(style)
        p = caplib._preset_of(st) or {}
        pos = (st.get("position") if st.get("_pos_set") else
               p.get("position") or st.get("position") or "bottom")
        return caplib._premium_anchor(p, pos, st), _BAND.get(pos, "b")
    return motion_captions._placement_for(style, track, src_mid)


_BAND = {"top": "t", "middle": "m", "bottom": "b"}


# ── the plan ─────────────────────────────────────────────────────────────

def program_items(edl, tl=None):
    """Motion items with a live window on the program clock."""
    end = float(tl.out_duration) if tl is not None else float("inf")
    out = []
    for m in edl.get("motion") or []:
        if not isinstance(m, dict) or m.get("_synthetic"):
            continue
        try:
            s, e = float(m["start"]), min(float(m["end"]), end)
        except (KeyError, TypeError, ValueError):
            continue
        if e - s >= 0.05:
            out.append(dict(m, start=s, end=e))
    return out


def _mid(w):
    return (float(w["t0"]) + float(w["t1"])) / 2.0


def _said(words):
    """Display form of a run of caption words."""
    return " ".join(str(w["w"]).strip().strip("\"“”").rstrip(".,;:…") for w in words)


class Plan:
    """The caption decision for one EDL's words (see module docstring).

    ``hidden[i] = (item id, "carried"|"room"|"unmeasured")`` ("room": no
    band clear of its drawn box; "unmeasured": no box measured at this frame
    shape, so it is assumed to sit on the captions); ``placed[i]`` = the band
    dict for a word moved clear of a graphic; ``clamp_spans`` are program
    windows no normally placed caption may hold into (a graphic occupies the
    caption band there); ``wait_spans`` add the whole-window mutes of
    motion graphics, which a normally placed caption must not start inside
    of near their end; ``report`` is per item for notes and audits."""

    def __init__(self, words):
        self.words = words
        self.hidden = {}
        self.placed = {}
        self.clamp_spans = []
        self.wait_spans = []
        self.report = {}

    def caption_words(self):
        """The words the captions show: hidden ones dropped, the first word
        after a hidden run or a placement change breaking the card, moved
        words carrying their band as ``place``."""
        if not self.hidden and not self.placed:
            return self.words
        out, prev_hidden, prev_place = [], False, None
        for i, w in enumerate(self.words):
            if i in self.hidden:
                prev_hidden = True
                continue
            place = self.placed.get(i)
            word = dict(w)
            if out and (prev_hidden or place != prev_place):
                word["brk"] = True
            if place:
                word["place"] = dict(place)
            out.append(word)
            prev_hidden, prev_place = False, place
        return out


def plan(edl, index, tl, words):
    """Plan word-level caption muting and placement for ``words`` (program
    caption words after the whole-window mutes; see captions.caption_words)."""
    p = Plan(words)
    caps = edl.get("captions")
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript"):
        return p
    items_all = program_items(edl, tl)
    p.wait_spans = [[m["start"], m["end"]] for m in items_all if mode(m) == MODE_ALL]
    items = [m for m in items_all if mode(m) != MODE_ALL]
    if not items or not words:
        return p
    word_toks = _token_rows([w.get("w") for w in words])
    mids = [_mid(w) for w in words]
    safe = safe_range(edl, index)
    ar = frame_ar(edl, index)
    for m in items:
        s, e = m["start"], m["end"]
        seq, hero, solo = shown_tokens(m)
        # a word still being said as the graphic leaves counts: the graphic
        # shows it while it is heard ("140" spoken across a slam's exit)
        idx = [i for i, t in enumerate(mids) if s - CARRY_LEAD_S <= t and
               (t <= e or float(words[i]["t0"]) < e - 0.04)]
        lead_n = sum(1 for i in idx if mids[i] < s)
        local = carried_indices([word_toks[i] for i in idx], seq, lead_n, solo)
        carried = {idx[k] for k in local}
        if mode(m) == MODE_HERO:
            carried = {i for i in carried if hero & set(word_toks[i])}
        else:
            inside = [i for i in idx if mids[i] >= s]
            carried |= _absorbed(inside, carried, word_toks, words)
        for i in sorted(carried):
            p.hidden.setdefault(i, (m["id"], "carried"))
        p.report[m["id"]] = {"id": m["id"], "start": s, "end": e, "mode": mode(m),
                             "box": drawn_box(m, ar),
                             "carried": [words[i] for i in sorted(carried)],
                             "placed": None, "muted": [], "kept": []}
    # Segments: maximal program spans with one set of live graphics.
    cuts = sorted({x for m in items for x in (m["start"], m["end"])})
    for a, b in zip(cuts, cuts[1:]):
        live = [m for m in items if m["start"] <= a + 1e-6 and m["end"] >= b - 1e-6]
        if not live or b - a < 1e-3:
            continue
        seg = [i for i, t in enumerate(mids) if a <= t < b or (b == cuts[-1] and t == b)]
        visible = [i for i in seg if i not in p.hidden]
        src_at = (float(words[visible[0]].get("src_t0", mids[visible[0]])) if visible
                  else _src_near(tl, (a + b) / 2.0))
        normal_y, _band = normal_place(edl, src_at)
        known = [bx for bx in (drawn_box(m, ar) for m in live) if bx]
        # An unmeasured box is old behaviour: a speech-replacing word-level
        # graphic is assumed to sit on the captions (no band can be proven
        # clear of it), anything else is assumed to sit elsewhere.
        assume = [m for m in live if not drawn_box(m, ar) and mode(m) == MODE_WORDS
                  and replaces_speech(m)]
        if not (collides(known, normal_y) or assume):
            continue
        place = None
        if not assume:
            src = [float(words[i].get("src_t0", mids[i])) for i in visible] or [src_at]
            place = clear_band(known, faces_near(edl, index, min(src), max(src)),
                               safe, normal_y)
        mute = place is None and any(mode(m) == MODE_WORDS and replaces_speech(m)
                                     for m in live)
        if place is None and not mute and visible:
            # nowhere clear and not a graphic that replaces speech: the
            # captions stay where they always were
            for i in visible:
                for m in live:
                    p.report[m["id"]]["kept"].append(words[i])
            continue
        p.clamp_spans.append([a, b])
        for i in visible:
            if mute:
                owner = next(m for m in live if mode(m) == MODE_WORDS and replaces_speech(m))
                p.hidden[i] = (owner["id"], "unmeasured" if owner in assume else "room")
                p.report[owner["id"]]["muted"].append(words[i])
            else:
                p.placed[i] = place
        if place:
            for m in live:
                p.report[m["id"]]["placed"] = p.report[m["id"]]["placed"] or \
                    dict(place, normal_y=round(normal_y, 4))
    p.clamp_spans = _merge(p.clamp_spans)
    return p


def _src_near(tl, out_t):
    try:
        v = tl.out_to_src(out_t)
    except Exception:  # noqa: BLE001
        v = None
    return float(v) if v is not None else float(out_t)


def _merge(spans):
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1] + 1e-3:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def _absorbed(inside, carried, word_toks, words):
    """Connector words that go with the shown words around them: a run of at
    most ABSORB_MAX function words inside the window touching a shown word,
    whose other side is a shown word too, or a pause or the end of a
    sentence (a lone "the" before a shown "next level" is no caption)."""
    out = set()
    inside_set = set(inside)

    def connector(i):
        return i in inside_set and i not in carried and bool(word_toks[i]) and \
            not any(is_content(t) for t in word_toks[i])

    def open_between(i, j):
        """A pause or a sentence/clause end between words i < j."""
        return float(words[j]["t0"]) - float(words[i]["t1"]) >= 0.3 or \
            str(words[i].get("w") or "").rstrip("\"'”’ ")[-1:] in ".!?…,;:"
    k = 0
    while k < len(inside):
        if not connector(inside[k]):
            k += 1
            continue
        j = k
        while j < len(inside) and connector(inside[j]):
            j += 1
        run = inside[k:j]
        before, after = run[0] - 1, run[-1] + 1
        left_shown = before in carried
        right_shown = after in carried
        open_left = before < 0 or open_between(before, run[0])
        open_right = after >= len(words) or open_between(run[-1], after)
        if len(run) <= ABSORB_MAX and ((left_shown and (right_shown or open_right))
                                       or (right_shown and open_left)):
            out.update(run)
        k = j
    return out


# ── sound-off coverage ───────────────────────────────────────────────────

def _on_screen(edl, tl):
    """[(start, end, token set, label)] of every text element on screen:
    motion graphics and designed texts."""
    out = []
    for m in program_items(edl, tl):
        seq, _hero, _solo = shown_tokens(m)
        out.append((m["start"], m["end"], set(seq), f"motion graphic '{m['id']}'"))
    for t in edl.get("texts") or []:
        try:
            s, e = float(t["start"]), float(t["end"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append((s, e, set(tokens(t.get("text") or "")), f"text '{t.get('id', '?')}'"))
    return out


def sound_off_gaps(edl, index, tl, min_gap=SOUND_OFF_GAP_S):
    """Spoken spans longer than ``min_gap`` that neither a caption nor an
    on-screen graphic/text shows. [{start, end, duration_s, said, cause,
    owner, fix}] in program order; [] when captions are not transcript
    captions (no caption track is a separate, louder finding)."""
    import captions as caplib
    caps = edl.get("captions")
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript"):
        return []
    if not (index or {}).get("words"):
        return []
    spoken = caplib.transcript_words(edl, index, tl)
    mutes = caplib.effective_caption_mutes(edl)
    after_mutes = caplib.transcript_words(edl, index, tl, mutes)
    p = plan(edl, index, tl, after_mutes)
    key = lambda w: (round(float(w["t0"]), 3), str(w.get("w")))  # noqa: E731
    shown = {key(w) for w in p.caption_words()}
    carried = {key(after_mutes[i]) for i, (_o, why) in p.hidden.items() if why == "carried"}
    room = {key(after_mutes[i]): (owner, why) for i, (owner, why) in p.hidden.items()
            if why in ("room", "unmeasured")}
    screen = _on_screen(edl, tl)
    toks = _token_rows([w.get("w") for w in spoken])
    state = []                         # covered / neutral / uncovered per word
    for w, ts in zip(spoken, toks):
        k = key(w)
        mid = _mid(w)
        on = [lab for s, e, tset, lab in screen if s <= mid <= e and tset & set(ts)]
        content = any(is_content(t) for t in ts)
        if k in shown or k in carried or (on and content):
            state.append("covered")
        elif not content:
            state.append("neutral")
        else:
            state.append("uncovered")
    gaps, run = [], []

    def flush():
        hard = [i for i in run if state[i] == "uncovered"]
        if hard:
            a, b = spoken[hard[0]], spoken[hard[-1]]
            dur = float(b["t1"]) - float(a["t0"])
            if dur > min_gap:
                gaps.append(_gap(edl, spoken, hard[0], hard[-1], dur, room, key))
    for i, st in enumerate(state):
        if run and (st == "covered" or
                    float(spoken[i]["t0"]) - float(spoken[run[-1]]["t1"]) > min_gap):
            flush()
            run = []
        if st != "covered":
            run.append(i)
    flush()
    return gaps


def _gap(edl, spoken, i0, i1, dur, room, key):
    a = spoken[i0]
    mid = _mid(a)
    cause, owner, fix = "hidden", None, "caption those words or show them on a graphic"
    for i in range(i0, i1 + 1):
        if key(spoken[i]) in room:
            owner, why = room[key(spoken[i])]
            if why == "unmeasured":
                cause = (f"motion graphic '{owner}' has no box measured at this frame "
                         "shape, so it is assumed to sit on the caption band (a render "
                         "measures it)")
                fix = (f"re-save '{owner}' (set_motion_graphic) so its box is measured, "
                       "keep it off the caption band, or carry those words on it")
            else:
                cause = (f"motion graphic '{owner}' leaves no caption band clear of it "
                         "and the face")
                fix = (f"move '{owner}' off the caption band (its y param) or carry "
                       "those words on it")
            break
    else:
        for m in program_items(edl):
            if mode(m) == MODE_ALL and m["start"] <= mid <= m["end"]:
                owner = m["id"]
                cause = f"motion graphic '{owner}' mutes the captions (mute_captions=true)"
                fix = (f"leave '{owner}' mute_captions unset so only the words it "
                       "shows are hidden")
                break
        else:
            for t in edl.get("texts") or []:
                try:
                    inside = float(t["start"]) <= mid <= float(t["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                if inside and t.get("mute_captions"):
                    owner = t.get("id")
                    cause = f"text '{owner}' mutes the captions"
                    fix = (f"set '{owner}' mute_captions=false, or put those words "
                           "on it")
                    break
            else:
                for m0, m1 in edl.get("caption_mutes") or []:
                    if float(m0) <= mid <= float(m1):
                        cause = f"set_caption_mutes window {float(m0):g}-{float(m1):g}s"
                        fix = "narrow or remove that caption mute"
                        break
                else:
                    cause = "no caption covers them"
    said = _said(spoken[i0:i1 + 1])
    if len(said) > 90:
        said = said[:87].rstrip() + "…"
    return {"start": round(float(a["t0"]), 2), "end": round(float(spoken[i1]["t1"]), 2),
            "duration_s": round(dur, 2), "said": said, "cause": cause, "owner": owner,
            "fix": fix}
