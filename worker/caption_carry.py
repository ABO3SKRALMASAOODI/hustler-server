"""Caption arbitration and placement: graphics and captions share one stage.

Round 6 (Oct 2026, after round-4 judging): EVERY HEARD WORD REACHES THE
SCREEN ONCE. Lockups had grown into 6-line piles of micro "bridge" rows,
words were heard but never shown ("to take our civilization to" under a
slam, "and scored" between two stat slams, the speech under a versus split
muted for its whole window), and captions sat on the seam between two
stacked panels and carried their place over a layout change onto a chin.

The contract (MotionItem.mute_captions):

- unset (the default) and true: a graphic takes the words it SHOWS — its
  visible text params matched against the transcript, tolerant of case,
  punctuation, plurals, numerals vs number words and *starred* accents,
  read in display order (a kicker before its line) — and the one or two
  connectors between them (and a quoted phrase that starts up to
  CARRY_LEAD_S before it). Every other heard word stays captioned — the
  rest of the phrase it shows included (``owned``: reported as
  ``captioned``; a lockup no longer sets them itself, its ``reading`` has
  no bridges). No graphic mutes a whole window any more: true behaves as
  unset, except that with no free band its unshown words are muted.
- false: the captions keep running beside it; only a number or a hero word
  it shows (a *starred* word, a counter's value) is not repeated.

Placement (``plan``, with worker/caption_place.py): a caption keeps its
usual place unless that place, on the frames it is up, touches a graphic's
stored box (MotionItem.footprint), a card or panel edge or seam, a stack's
content panel, a face with its chin (the write-time zones and the index's
evidence together), a prop the index knows or the watermark's corner; then
it takes the best free band (the largest, at least ~1.4 lines tall) — and,
with none on the canvas, the inside of a stack's content panel rather than
lose a heard word. Where no band is free of a graphic that replaces speech
(spec ``mutes_captions``) or was asked to (true), its words are muted and
named (the write NOTE, ``heard_unshown``, the sound-off audit); under any
other graphic they stay where they were. Placement is re-solved at every
layout change — a graphic's or card's edges, a cut inside a card — and a
page never holds its place across one (``Plan.hold_limit``); a word said
within two frames before the change appears ON it; one or two words a
change would strand take their line's place when it is clear for them.

Everything here is a pure function of the EDL, the index and the timeline,
and it is the ONE caption placement pass: the libass captions, the motion
caption track, the write-time notes, audit_captions and the sound-off audit
all compute the same plan. The graphic's box is its stored footprint
(worker/keepout.py writes it on every add/set_motion_graphic: the probe's
COVER box where a browser runs, the template's estimated box where none
does). An item without a footprint (or with one measured at another frame
shape: ``footprint.ar``) is treated as old behaviour (a speech-replacing
template mutes what it does not carry); the renderer and audit_captions
measure it — and any estimated box — before they build captions
(motion_layer.fill_footprints). A composition with no ink (a light leak)
has no box and never moves or mutes a caption.
"""

import difflib
import math
import re

MODE_ALL, MODE_WORDS, MODE_HERO = "all", "words", "hero"

# A quoted phrase may start this long before its graphic (the kicker "it's
# not quite been" under a slam on "enough"): those words are handed to the
# graphic instead of being read twice.
CARRY_LEAD_S = 0.5
# Connector words between shown words go with them (one or two of "and",
# "the", "of" left alone as a flashing card are noise, not speech).
ABSORB_MAX = 2
# A printed word this long may stand for a longer spoken word it starts
# ("tech" for "technology") — inside a run of the graphic's words only.
ABBREV_MIN = 4
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
# A face zone (keepout.face_zone) runs from the forehead to the chin; the
# hair rises about this share of its height above it. A caption moved above
# the head clears the hair too when a band that tall fits.
HAIR_UP = 0.3
# A face measured this far from a moment still speaks for it (any take)
# when nothing nearer was measured.
FACE_FAR_S = 6.0
# A caption that would start this little before an occupying graphic leaves
# waits for it instead of touching it (the 'computers' under the hook title)
# — never more than two frames: a word is never revealed later than that
# to line a page flip up with anything (judged: 'has' 0.2 s late).
START_WAIT_S = 0.075
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


def persistent(item):
    """A persistent template (the headline band, motion_templates.persistent):
    it carries no spoken words and is placed around like any graphic."""
    return bool(_spec(item).get("persistent"))


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
    # in reading order: a kicker is read (and said) before the line it sits
    # over ("and scored / 26% better overall" matched against the speech)
    out.sort(key=lambda kv: 0 if kv[0] == "kicker" else 1)
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
    # inside a run, a printed word may be the spoken word cut short
    # ("information tech" over "information technology"): a run is never
    # built of a clipped word alone, and only runs read it that way
    clipped = sorted((t for t in content if len(t) >= ABBREV_MIN and t.isalpha()),
                     key=len, reverse=True)

    def run_token(t):
        if t in content:
            return t
        return next((c for c in clipped if len(t) >= len(c) + 2 and t.startswith(c)), t)
    if seq and flat:
        sm = difflib.SequenceMatcher(None, seq, [run_token(t) for t, _i in flat],
                                     autojunk=False)
        for blk in sm.get_matching_blocks():
            # a run of connectors alone ("of the" in "THE END OF THE WORLD"
            # over "one of the best years") is not the graphic's words
            if blk.size >= 2 and any(is_content(seq[blk.a + k]) and
                                     flat[blk.b + k][0] == seq[blk.a + k]
                                     for k in range(blk.size)):
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

def frame_wh(edl, index, canvas=None):
    """The output frame (W, H): ``canvas`` when the caller knows it (the
    renderer), else the EDL's canvas, else the frame fitted to the source."""
    if canvas:
        return float(canvas[0]), float(canvas[1])
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


def safe_range(W, H):
    """Vertical platform-safe text area of a W x H frame, as the motion
    caption template lays it out (worker/motion/templates/caption_motion.html
    SAFE)."""
    ar = float(H) / max(float(W), 1e-6)
    if ar >= 1.6:
        return 0.08, 0.80
    if ar > 1.15:
        return 0.06, 0.90
    if ar >= 0.95:
        return 0.06, 0.92
    return 0.07, 0.92


# ── the stored measurement (MotionItem.footprint) ─────────────────────────
# One measurement per motion item, written by add/set_motion_graphic
# (worker/keepout.py) and filled in by the renderer for items without one
# (motion_layer.fill_footprints):
#   box        the box the graphic draws that captions keep clear of (the
#              probe's COVER box, motion_engine.COVER_ALPHA), frame fractions;
#   ar         the frame aspect W/H it was measured at — a template lays out
#              against its canvas, so after set_frame the box is stale and
#              is measured again;
#   faces      the face zones the write-time keep-out measured over the
#              item's window (exact frames, through crop, zooms and cards);
#   estimated  true on a lane without a browser: the template's nominal box
#              (keepout.nominal_ink). The plan trusts it; the renderer, which
#              has a browser, measures it before it burns captions.
AR_TOL = 0.02


def frame_ar(W, H):
    """The aspect (W/H) a footprint is stamped with."""
    return round(float(W) / max(float(H), 1.0), 4)


def make_footprint(box, W, H, faces=(), estimated=False, geo=None):
    fp = {"box": [round(float(v), 4) for v in box], "ar": frame_ar(W, H),
          "faces": [[round(float(v), 4) for v in f[:4]] for f in list(faces)[:8]]}
    if estimated:
        fp["estimated"] = True
    if faces and geo:
        fp["geo"] = str(geo)
    return fp


def _canon(v):
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in v.items() if x is not None}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, float):
        return round(v, 4)
    return v


def face_geometry(edl):
    """A short stamp of everything that decides where the speaker's face sits
    on the program canvas: the program map (kept spans, contiguous pieces
    merged; speed; inserts and full-frame overlays, which hide it), the
    frame (crop, aim, focus track, picture region), the zooms and the
    picture cards. Face zones stored with a footprint (``geo``) are evidence
    only while the stamp still matches."""
    import hashlib
    import json
    keep = []
    for span in edl.get("keep") or []:
        try:
            s, e = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        if keep and abs(keep[-1][1] - s) < 1e-3:
            keep[-1][1] = e
        else:
            keep.append([s, e])
    fx = edl.get("effects") if isinstance(edl.get("effects"), dict) else {}
    inserts = [[i.get("at_output_s"), i.get("duration_s"), i.get("fit"), i.get("kind")]
               for i in edl.get("inserts") or [] if isinstance(i, dict)]
    covers = [[o.get("start"), o.get("duration_s"), o.get("fit"), bool(o.get("screen"))]
              for o in edl.get("overlays") or [] if isinstance(o, dict)
              and (o.get("fit") == "cover" or o.get("screen"))]
    cards = [{k: c.get(k) for k in ("start", "end", "box", "fit", "source", "panels",
                                    "source_track", "follow")}
             for c in (fx or {}).get("picture_cards") or [] if isinstance(c, dict)]
    frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
    frame = {k: (frame or {}).get(k) for k in ("ratio", "mode", "focus_x", "focus_y",
                                               "focus_track", "follow", "picture")}
    blob = json.dumps(_canon({"keep": keep, "speed": edl.get("speed") or [],
                              "inserts": inserts, "covers": covers, "frame": frame,
                              "zooms": (fx or {}).get("zooms") or [], "cards": cards,
                              "canvas": edl.get("canvas")}),
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def footprint_fresh(item, ar):
    """Was the item's footprint measured at this frame aspect (W/H)?"""
    fp = item.get("footprint")
    if not isinstance(fp, dict):
        return False
    try:
        return abs(float(fp["ar"]) - float(ar)) <= AR_TOL
    except (KeyError, TypeError, ValueError):
        return False


def footprint_box(item, ar):
    """The item's stored box (x0, y0, x1, y1) or None (never measured, or
    measured at another frame aspect than ``ar``)."""
    if not footprint_fresh(item, ar):
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in item["footprint"]["box"])
    except (KeyError, TypeError, ValueError):
        return None
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def footprint_faces(item, ar, geo=None):
    """The face zones the keep-out stored with a fresh footprint ([]). With
    ``geo`` (face_geometry of the EDL now), zones stamped with another
    picture geometry are stale: []."""
    if not footprint_fresh(item, ar):
        return []
    stamp = item["footprint"].get("geo")
    if geo and stamp and stamp != geo:
        return []
    out = []
    for f in item["footprint"].get("faces") or []:
        try:
            x0, y0, x1, y1 = (float(v) for v in f[:4])
        except (TypeError, ValueError):
            continue
        if x1 > x0 and y1 > y0:
            out.append((x0, y0, x1, y1))
    return out


def estimated(item):
    fp = item.get("footprint")
    return bool(isinstance(fp, dict) and fp.get("estimated"))


# ── faces ────────────────────────────────────────────────────────────────

def _in_column(box):
    return box[2] > COLUMN[0] and box[0] < COLUMN[1]


def faces_over(edl, index, tl, a, b, W, H, live=()):
    """Face ZONES (worker/keepout.face_zone: brow to chin, with a forehead
    and chin pad; output fractions) over program [a, b] — never empty, an
    unmeasured face blocks a move:

    1. the zones the write-time keep-out stored with the live graphics'
       footprints (exact source frames, mapped through the crop, the zoom at
       each second and any picture card) — only while the picture they were
       measured on is still the picture (``footprint.geo``, face_geometry):
       a zoom, re-cut, reframe or card added after the graphic (the usual
       order: graphics, then camera) moves the face without re-measuring
       them, and stale zones would park a caption on the face — together
       with
    2. the keep-out's face track over [a, b] from the index's spatial
       samples (the same mapping, on the EDL as it is now): either alone
       can miss a profile;
    3. with no track, the nearest measured face within FACE_FAR_S;
    4. the talking-head prior (Haar misses profiles: "no face found" is no
       evidence of no face)."""
    import keepout
    ar = frame_ar(W, H)
    geo = face_geometry(edl)
    stored = [tuple(z) for m in live for z in footprint_faces(m, ar, geo)]
    try:
        zones = [tuple(z) for z in keepout.zones_of(keepout.face_track(edl, index, W, H, a, b))]
    except Exception:  # noqa: BLE001 — unmappable index: the fallbacks answer
        zones = []
    if not zones:
        zones = _faces_far(edl, index, tl, a, b, W, H)
    # the write-time frames win for a face they measured (exact frames: a
    # push-in moved it), but a face the index has and they do not count too
    # — Haar misses profiles, and the write-time zones alone parked 'and
    # scored' on Rogan's turned cheek
    out = stored + [z for z in zones if not any(_same_face(z, s) for s in stored)]
    return out or [FACE_PRIOR]


def _same_face(a, b):
    """Do two face zones speak for the same face (side by side in x, of a
    like size — one measured where the other was before a move)?"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    wa, wb = a[2] - a[0], b[2] - b[0]
    if w <= 0 or w < 0.5 * min(wa, wb):
        return False
    area_a = max(1e-9, wa * (a[3] - a[1]))
    area_b = max(1e-9, wb * (b[3] - b[1]))
    return 0.4 <= area_a / area_b <= 2.5


def _faces_far(edl, index, tl, a, b, W, H):
    """Zones of the index faces measured within FACE_FAR_S of the source
    span under program [a, b] (any take), mapped at the span's middle."""
    import keepout
    video = (index or {}).get("video") or {}
    if not video.get("width") or not video.get("height") or edl.get("canvas"):
        return []
    lo, hi = _src_near(tl, a), _src_near(tl, b)
    lo, hi = min(lo, hi), max(lo, hi)
    mid = (a + b) / 2.0
    src_mid = _src_near(tl, mid)
    try:
        geo = keepout.Geometry(edl, video, W, H, float(tl.out_duration),
                               zooms=keepout.camera_zooms(edl, index, tl))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for s in (((index or {}).get("spatial") or {}).get("samples") or []):
        try:
            t = float(s["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if not lo - FACE_FAR_S <= t <= hi + FACE_FAR_S:
            continue
        for f in s.get("faces") or []:
            try:
                ms = geo.to_outputs(mid, src_mid, [float(v) for v in f[:4]])
            except Exception:  # noqa: BLE001 — unmappable sample
                ms = []
            out += [tuple(keepout.face_zone(m)) for m in ms]
    return out


def _head(zone):
    """A face zone with the hair above it (HAIR_UP of its height)."""
    return (zone[0], zone[1] - HAIR_UP * (zone[3] - zone[1]), zone[2], zone[3])


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


def _free_band(boxes, faces, safe, normal_y):
    ivs = [tuple(safe)]
    for b in boxes:
        if _in_column(b):
            ivs = _subtract(ivs, b[1] - GRAPHIC_PAD, b[3] + GRAPHIC_PAD)
    for f in faces:
        if _in_column(f):
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
    return best


def clear_band(boxes, faces, safe, normal_y):
    """{y, b, z, position} of the clear band nearest ``normal_y`` that
    misses every graphic box and face zone (``faces``) reaching the caption
    column, or None when none is tall enough. A band that also clears the
    hair above each face (HAIR_UP) wins when one fits; a frame-filling head
    leaves the band over the hair, which is still used."""
    best = _free_band(boxes, [_head(f) for f in faces], safe, normal_y) or \
        _free_band(boxes, faces, safe, normal_y)
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

    ``hidden[i] = (item id, "carried"|"room"|"unmeasured")`` ("carried":
    the graphic shows the word, or it is a connector joint between words it
    shows; "room": no band clear of its box, the face and the layout, and
    the graphic replaces speech or was asked to (mute_captions=true);
    "unmeasured": no box measured at this frame shape, so it is assumed to
    sit on the captions); ``placed[i]`` = the band dict for a word moved
    clear of a graphic or of a card layout's edges; ``shown_at[i]`` = the
    program second a word snapped forward onto a layout change appears
    (never more than caption_place.SNAP_FRAMES late); ``clamp_spans`` are
    program windows a caption from before may not hold into (a graphic
    occupies the caption band there, or the placement changes);
    ``yield_spans`` and ``wait_spans`` are kept for older callers and are
    empty (no graphic owns a phrase or mutes a whole window any more: every
    heard word reaches the screen once); ``report`` is per item for notes
    and audits."""

    def __init__(self, words):
        self.words = words
        self.hidden = {}
        self.placed = {}
        self.shown_at = {}
        self.clamp_spans = []
        self.wait_spans = []
        self.yield_spans = []
        self.segments = []
        self._seg_places = None
        self.report = {}

    def hold_limit(self, start, place=None):
        """The program second a page shown from ``start`` at ``place`` (its
        band dict, None = its usual place) must clear by: the start of the
        first later segment where the captions sit elsewhere (another band,
        their usual place after a band, or muted) — a page never carries its
        place across a layout change onto the new layout. A segment where a
        word is still shown at ``place`` (one or two words a change would
        strand keep their line's place: _smooth_flips) is not such a change:
        a page reaching into it is not cut off before its own last words.
        inf when none."""
        for k, (a, _b, st) in enumerate(self.segments):
            if a > start + 1e-3 and st != place and place not in self._places_in(k):
                return a
        return float("inf")

    def _places_in(self, k):
        """The places (band dicts, None = usual) of the caption words shown
        from inside segment ``k``."""
        if self._seg_places is None:
            import bisect
            starts = [a for a, _b, _st in self.segments]
            self._seg_places = [[] for _ in self.segments]
            for i, w in enumerate(self.words):
                if i in self.hidden:
                    continue
                t = max(float(w["t0"]), self.shown_at.get(i, float(w["t0"])))
                j = bisect.bisect_right(starts, t + 1e-6) - 1
                if 0 <= j < len(self._seg_places):
                    pl = self.placed.get(i)
                    if pl not in self._seg_places[j]:
                        self._seg_places[j].append(pl)
        return self._seg_places[k] if k < len(self._seg_places) else []

    def caption_words(self):
        """The words the captions show: hidden ones dropped, the first word
        after a hidden run or a placement change breaking the card, moved
        words carrying their band as ``place``, a word snapped onto a layout
        change starting there (``t0_said`` keeps its spoken onset)."""
        if not self.hidden and not self.placed and not self.shown_at:
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
            if i in self.shown_at and self.shown_at[i] > float(w["t0"]):
                word["t0_said"] = float(w["t0"])
                word["t0"] = round(min(self.shown_at[i], float(w["t1"]) - 0.01), 4)
            out.append(word)
            prev_hidden, prev_place = False, place
        return out


def said_key(w):
    """A caption word's identity across the plan: its spoken onset (before
    any snap onto a layout change) and its text."""
    return (round(float(w.get("t0_said", w["t0"])), 3), str(w.get("w")))


def _segment_bounds(edl, tl, items, cards):
    """Program seconds where what a caption must keep clear of can change:
    every graphic's and card's edges, the program cuts inside a card (each
    shot frames the speaker anew) and the placement track's span edges."""
    import captions as caplib
    end = float(tl.out_duration)
    bounds = {0.0, end}
    for m in items:
        bounds.update((m["start"], m["end"]))
    for s, e in cards:
        bounds.update((s, e))
        bounds.update(c for c in caplib.program_cuts(tl) if s < c < e)
    for span in ((edl.get("captions") or {}).get("placement_track") or []):
        for key in ("t0", "t1"):
            try:
                o = tl.src_to_out(float(span[key]))
            except Exception:  # noqa: BLE001
                o = None
            if o is not None and any(s < o < e for s, e in cards):
                bounds.add(float(o))
    return sorted(b for b in bounds if 0.0 <= b <= end)


# How long after a card leaves the captions' usual place is checked against
# the full shot's face (see plan).
AFTER_CARD_S = 0.25


def _inside(a, b):
    """The end of a segment's face query: a hair before its last instant,
    which belongs to the next layout (a card's end frame is the full shot)."""
    return b - 0.02 if b - a > 0.06 else b


def _off_content(faces, screens):
    """Face zones less those centred inside a stack's content panel
    (caption_place.content_zones: the panel was told apart BY having no face
    in its source rect). A face there is a far take's face mapped through
    the panel (_faces_far: any take within FACE_FAR_S), not one the viewer
    sees — it would block the panel's clear space for nothing."""
    if not screens:
        return faces
    out = [f for f in faces if not any(
        z[0] <= (f[0] + f[2]) / 2.0 <= z[2] and z[1] <= (f[1] + f[3]) / 2.0 <= z[3]
        for z in screens)]
    return out or [FACE_PRIOR]


def _in_windows(zones, rects):
    """Zones on a card layout clipped to the card windows they show through
    (picture_cards.card_boxes): a card shows only its source rect, so a face
    zone mapped past a window's edge — padded by keepout.face_zone, a chin
    pad, or a false detection outside what the card frames — is not on
    screen there and never blocks the canvas around the card (Jobs: a zone
    mapped to y 0.47-0.99, off the bottom of his card, took the band below
    it, so every caption climbed into the card over his hair). A zone that
    meets no window is dropped. With no window (a full-frame stretch) the
    zones are returned as they are."""
    if not rects:
        return list(zones)
    out = []
    for z in zones:
        for x0, y0, x1, y1 in rects:
            c = (max(z[0], x0), max(z[1], y0), min(z[2], x1), min(z[3], y1))
            if c[2] - c[0] > 1e-3 and c[3] - c[1] > 1e-3:
                out.append(c)
    return out


def _card_faces(edl, index, tl, a, b, W, H, live, screens, rects):
    """The face zones a caption keeps clear of over a card segment: the
    faces over [a, b] less any on a stack's content panel, clipped to the
    card windows (_in_windows), else the talking-head prior in each window."""
    faces = _off_content(faces_over(edl, index, tl, a, _inside(a, b), W, H, live), screens)
    if rects:
        faces = [f for f in faces if f != FACE_PRIOR]
        faces = _in_windows(faces, rects) or _prior_in_cards(rects)
    return faces


def _chins(faces, rects):
    """caption_place.chin of each face, kept inside the windows it shows in."""
    import caption_place
    return _in_windows([caption_place.chin(f) for f in faces], rects)


def _prior_in_cards(rects):
    """The talking-head prior (FACE_PRIOR) inside each card window: under a
    card the face is in a panel, never on the canvas around it."""
    if not rects:
        return [FACE_PRIOR]
    out = []
    fx0, fy0, fx1, fy1 = FACE_PRIOR
    for x0, y0, x1, y1 in rects:
        w, h = x1 - x0, y1 - y0
        out.append((x0 + fx0 * w, y0 + fy0 * h, x0 + fx1 * w, y0 + fy1 * h))
    return out


# At a placement change inside a spoken line, the page splits around it;
# when that strands this many words or fewer on one side ("I'd" before a
# lower third arrives), they take the other side's place — when it is clear
# for them where they are said — instead of flashing as an orphan page.
SMOOTH_MAX_WORDS = 2


def _line_break(prev, nxt, cuts):
    """A break the captions take anyway between two consecutive words: a
    breath (0.62 s), a sentence end, an insert or a program cut."""
    try:
        gap = float(nxt["t0"]) - float(prev["t1"])
    except (KeyError, TypeError, ValueError):
        return True
    end = str(prev.get("w") or "").rstrip("\"'”’ )")
    if gap >= 0.62 or end[-1:] in ".!?…" or nxt.get("brk"):
        return True
    return any(float(prev["t1"]) - 1e-3 <= c <= float(nxt["t0"]) + 1e-3 for c in cuts)


def _smooth_flips(p, edl, index, tl, W, H, col, bounds, seg_of, state, info):
    """Move the one or two words a placement change strands (see
    SMOOTH_MAX_WORDS) to the place of the rest of their line, when that
    place is clear at the moments they are said."""
    import caption_place
    import captions as caplib
    words = p.words
    cuts = caplib.program_cuts(tl)
    vis = [i for i in range(len(words)) if i not in p.hidden]
    faces_cache = {}

    def zones(k):
        if info[k] is not None:
            return info[k]["zones"]
        if k not in faces_cache:
            # a stretch the plan did not solve (no graphic, no card): the
            # faces there, less any the usual place already sits on (a
            # moved word is held to no stricter a standard than the rest)
            a, b = bounds[k], bounds[k + 1]
            i = next((i for i in vis if seg_of[i] == k), None)
            src = float(words[i].get("src_t0", words[i]["t0"])) if i is not None \
                else _src_near(tl, (a + b) / 2.0)
            ny, _band = normal_place(edl, src)
            chins = [caption_place.chin(f) for f in
                     faces_over(edl, index, tl, a, _inside(a, b), W, H)]
            faces_cache[k] = [z for z in chins if not caption_place.hits(
                ny - CAP_HALF_H, ny + CAP_HALF_H, [z], col)]
        return faces_cache[k]

    def fits(place, idx):
        for i in idx:
            k = seg_of[i]
            if k >= len(state):
                return False
            if place is None:
                if state[k] is not None:
                    return False       # its usual place is taken there
                continue
            if state[k] == "mute":
                return False
            lo, hi = place["y"] - CAP_HALF_H, place["y"] + CAP_HALF_H
            if caption_place.hits(lo, hi, zones(k), col):
                return False
        return True
    j = 1
    while j < len(vis):
        i0, i1 = vis[j - 1], vis[j]
        pl0, pl1 = p.placed.get(i0), p.placed.get(i1)
        if pl0 == pl1 or i1 != i0 + 1 or _line_break(words[i0], words[i1], cuts):
            j += 1
            continue
        tail = [i0]
        while len(tail) <= SMOOTH_MAX_WORDS:
            q = tail[0] - 1
            if q < 0 or q in p.hidden or p.placed.get(q) != pl0 or \
                    _line_break(words[q], words[tail[0]], cuts):
                break
            tail.insert(0, q)
        head = [i1]
        while len(head) <= SMOOTH_MAX_WORDS:
            q = head[-1] + 1
            if q >= len(words) or q in p.hidden or p.placed.get(q) != pl1 or \
                    _line_break(words[head[-1]], words[q], cuts):
                break
            head.append(q)
        if len(tail) <= SMOOTH_MAX_WORDS and len(head) > len(tail) and fits(pl1, tail):
            for i in tail:
                if pl1 is None:
                    p.placed.pop(i, None)
                else:
                    p.placed[i] = pl1
        elif len(head) <= SMOOTH_MAX_WORDS and len(tail) > len(head) and fits(pl0, head):
            for i in head:
                if pl0 is None:
                    p.placed.pop(i, None)
                else:
                    p.placed[i] = pl0
        j += 1


def plan(edl, index, tl, words, canvas=None):
    """Plan word-level caption muting and placement for ``words`` (program
    caption words after the explicit caption mutes; see
    captions.caption_words).

    This is the ONE caption placement pass: the libass captions, the motion
    caption track, the write-time notes, audit_captions and the sound-off
    review all read it. ``canvas`` is the output (W, H) when the caller
    knows it (the renderer); otherwise it is derived from the EDL.

    Every heard word reaches the screen once: a graphic takes the words it
    shows (and the one or two connectors between them); every other word is
    captioned — moved to a free band (worker/caption_place.py) wherever its
    usual place is taken by a graphic, a card or panel edge, a face in a
    panel, a prop or the watermark. Only where no band is free of a graphic
    that replaces speech (or was asked to, mute_captions=true) are its words
    muted, and the sound-off audit names them. Placement is re-solved at
    every layout change (a graphic or card starting or ending, a cut inside
    a card): a page never carries its place across one where it would land
    on the new layout, and a word whose onset is within two frames of the
    change appears with the new layout."""
    import caption_place
    p = Plan(words)
    caps = edl.get("captions")
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript"):
        return p
    items = program_items(edl, tl)
    cards = caption_place.card_windows(edl)
    if not words or not (items or cards):
        return p
    word_toks = _token_rows([w.get("w") for w in words])
    mids = [_mid(w) for w in words]
    pids = phrase_ids(words)
    W, H = frame_wh(edl, index, canvas)
    safe = safe_range(W, H)
    ar = frame_ar(W, H)
    for m in items:
        s, e = m["start"], m["end"]
        carried = carried_by(m, words, word_toks, mids)
        for i in sorted(carried):
            p.hidden.setdefault(i, (m["id"], "carried"))
        rep = p.report[m["id"]] = {
            "id": m["id"], "start": s, "end": e, "mode": mode(m),
            "box": footprint_box(m, ar), "estimated": estimated(m),
            "carried": [words[i] for i in sorted(carried)],
            "placed": None, "muted": [], "kept": [],
            "owns_from": None, "joined": [], "yielded": [], "beside": [],
            "captioned": []}
        # (the standing headline shows no spoken words: captions run beside it)
        own = owned(m, words, word_toks, mids, pids, carried) \
            if mode(m) != MODE_HERO and not persistent(m) else None
        if own is None:
            continue
        rep["owns_from"] = own["from"]
        for i in own["absorbed"]:
            p.hidden.setdefault(i, (m["id"], "carried"))
        # the phrase's words it does not show stay captioned (every heard
        # word reaches the screen once; a lockup no longer sets them itself)
        rep["captioned"] = [[words[i] for i in run] for run in own["rest"]]
    # Layout segments: spans with one set of live graphics and cards.
    bounds = _segment_bounds(edl, tl, items, cards)
    snap = caption_place.snap_s(((index or {}).get("video") or {}).get("fps") or 30.0)
    import bisect
    seg_of = [max(0, bisect.bisect_right(bounds, float(w["t0"]) + snap) - 1) for w in words]
    col = caption_place.column(W, H, COLUMN)
    min_h = caption_place.min_band(edl, W, H, MIN_BAND_H)
    nseg = max(0, len(bounds) - 1)
    state = [None] * nseg          # per segment: None (usual place), a band, "mute"
    info = [None] * nseg           # per solved segment: its usual y and hard zones
    prev_pick = None               # the band the last placed page used
    for k, (a, b) in enumerate(zip(bounds, bounds[1:])):
        if b - a < 1e-3:
            continue
        live = [m for m in items if m["start"] <= a + 1e-6 and m["end"] >= b - 1e-6]
        lcards = caption_place.live_cards(edl, a + 1e-4, b - 1e-4)
        seg = [i for i, sk in enumerate(seg_of) if sk == k]
        visible = [i for i in seg if i not in p.hidden]
        # the stretch right after a card leaves is re-solved too: a page or
        # a placement span written for the card must not land on the full
        # shot's face (Elon: 'believe that for sure' on Rogan's chin)
        after = not lcards and any(ce - 1e-3 <= a < ce + AFTER_CARD_S for _cs, ce in cards)
        if not (live or lcards or after):
            prev_pick = None
            continue
        # the usual place most of its words have (a placement span may end
        # a hair inside the stretch); with none said here, the place a page
        # from before would hold into
        normals = [normal_place(edl, float(words[i].get("src_t0", mids[i]))) for i in visible] \
            or [normal_place(edl, _src_near(tl, (a + b) / 2.0))]
        normal_y, _band = max(normals, key=normals.count)
        known = [bx for bx in (footprint_box(m, ar) for m in live) if bx]
        gzones = [(bx[0], bx[1] - GRAPHIC_PAD, bx[2], bx[3] + GRAPHIC_PAD) for bx in known]
        # An unmeasured box is old behaviour: a word-level graphic that
        # replaces speech (or was asked to) is assumed to sit on the
        # captions (no band can be proven clear of it).
        speech = [m for m in live if not persistent(m) and
                  (mode(m) == MODE_ALL or (mode(m) == MODE_WORDS and replaces_speech(m)))]
        assume = [m for m in speech if not footprint_box(m, ar)]
        rects = caption_place.card_rects(lcards)
        edges = caption_place.edge_zones(rects, col)
        wm = caption_place.watermark_box(W, H)
        props = caption_place.prop_zones(edl, index, tl, W, H, a, b)
        soft = caption_place.text_boxes(edl, index, tl, W, H, a, b)
        screens = caption_place.content_zones(lcards, index, tl, a, b)
        hard = gzones + edges + ([wm] if wm else []) + props + screens
        faces = None
        lo, hi = max(safe[0], normal_y - CAP_HALF_H), min(safe[1], normal_y + CAP_HALF_H)
        blocked = bool(assume) or collides(known, normal_y) or \
            caption_place.hits(lo, hi, props, col)
        if (lcards or after) and not blocked:
            # a card layout: its edges and seams, the faces in its panels
            # and the watermark are no place for the usual anchor either
            faces = _card_faces(edl, index, tl, a, b, W, H, live, screens, rects)
            blocked = caption_place.hits(lo, hi, edges + screens + ([wm] if wm else []) +
                                         _chins(faces, rects), col)
        if faces is not None:
            info[k] = {"y": normal_y, "zones": hard + _chins(faces, rects)}
        if not blocked:
            prev_pick = None
            continue
        place = None
        if not assume:
            if faces is None:
                faces = _card_faces(edl, index, tl, a, b, W, H, live, screens, rects)
            chins = _chins(faces, rects)
            info[k] = {"y": normal_y, "zones": hard + chins}
            one_line = max(MIN_BAND_H * 0.75, caption_place.line_height(edl, W, H) * 1.15)
            pick = None
            # a band that clears the hair too wins when one fits; then the
            # face and chin alone; then (a frame-filling head, a crowded
            # stack) any band one line still fits
            for fz, need in (([_head(f) for f in chins], min_h), (chins, min_h),
                             (chins, one_line)):
                pick = caption_place.choose(caption_place.free_bands(hard + fz, safe, col),
                                            normal_y, CAP_HALF_H, need, soft, rects, col,
                                            prev=prev_pick)
                if pick:
                    break
            if not pick and screens:
                # the last resort before muting heard words (or leaving them
                # on a seam): a stack's content panel, inside its edges and
                # priced like source text under the caption — every heard
                # word reaches the screen once (Elon: 'and scored' between
                # two stat slams, the speaker's face filling his panel)
                loose = gzones + edges + ([wm] if wm else []) + props + chins
                pick = caption_place.choose(caption_place.free_bands(loose, safe, col),
                                            normal_y, CAP_HALF_H, one_line,
                                            list(soft) + list(screens), rects, col,
                                            prev=prev_pick)
                if pick:
                    info[k] = {"y": normal_y, "zones": loose}
            if pick:
                y, ba, bb, _score = pick
                z0, z1 = max(ba, y - ZONE_HALF_H), min(bb, y + ZONE_HALF_H)
                letter = band_letter(y)
                place = {"y": round(y, 4), "b": letter, "z": [round(z0, 4), round(z1, 4)],
                         "position": _POSITION[letter]}
                prev_pick = (y, ba, bb)
        mute = place is None and bool(speech)
        if place is None and not mute:
            # nowhere clear and nothing that replaces speech: the captions
            # stay where they always were (named in the notes)
            for i in visible:
                for m in live:
                    p.report[m["id"]]["kept"].append(words[i])
            prev_pick = None
            continue
        state[k] = place if place else "mute"
        for i in visible:
            if mute:
                owner = next((m for m in speech if m in assume), speech[0])
                p.hidden[i] = (owner["id"], "unmeasured" if owner in assume else "room")
                p.report[owner["id"]]["muted"].append(words[i])
            else:
                p.placed[i] = place
        if place:
            for m in live:
                p.report[m["id"]]["placed"] = p.report[m["id"]]["placed"] or \
                    dict(place, normal_y=round(normal_y, 4))
    _smooth_flips(p, edl, index, tl, W, H, col, bounds, seg_of, state, info)
    p.segments = [(bounds[k], bounds[k + 1], state[k]) for k in range(nseg)]
    for a, b, st in p.segments:
        if st is not None:
            p.clamp_spans.append([a, b])
    # a word said within two frames before a layout change it is placed
    # for appears ON the change (the page never flips early)
    prev = None
    for i, w in enumerate(words):
        if i in p.hidden:
            continue
        place = p.placed.get(i)
        k = seg_of[i]
        if prev is not None and place != prev[1] and k < nseg and \
                float(w["t0"]) < bounds[k] and k > 0:
            p.shown_at[i] = bounds[k]
        prev = (i, place)
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


# ── what a graphic takes beyond its own words ─────────────────────────────
# Round 3 made a graphic that shows a phrase OWN it: the captions yielded
# from its first shown word to its exit, a lockup set the phrase's other
# words as micro "bridge" rows inside itself, any other graphic left them to
# the sound. Round 4 judging: the lockups became 6-line piles with 1.3%-of-
# frame type, and words were heard but never shown ("to take our
# civilization to" under ENOUGH, "and scored" between two stat slams).
# The contract now (the captions side of it, pinned in
# tests/test_caption_coverage.py): EVERY HEARD WORD REACHES THE SCREEN ONCE.
# A graphic takes the words it shows and the one or two connectors between
# them; every other word — the rest of its phrase included — is captioned,
# in a band clear of it (plan). A lockup's ``reading`` times its rows to the
# speech and carries no bridges.

# A source jump this long (or a program pause) ends a phrase even without
# punctuation; sentence and clause marks always do.
PHRASE_GAP_S = 1.5
PHRASE_PAUSE_S = 1.0
_PHRASE_END = ".!?…;:"
READING_VERSION = 1
# Judged Oct 2026 (round 4): the lockups grew piles of micro bridge type
# (Thiel's list to 6 lines in 5 sizes, 'the Green Revolution / agriculture'
# at ~1.3% of the frame height; Jobs' payoff likewise). A lockup no longer
# sets the phrase's other words: they stay captioned beside it (owned():
# "rest"; readings() writes no bridges), and the lockup is only its designed
# rows. An OLD stored reading with bridges still parses; while this switch
# is off its bridge lines are never drawn or timed (lockup_reveals) — the
# render recomputes the reading anyway (attach_readings).
LOCKUP_SETS_BRIDGES = False
# A word-timed reveal within this much after its item's start shows AT the
# start: the write puts a lockup's window on its first visible word, or on a
# cut up to this far before it (motion_tools: one event, not a cut and then
# a word two frames later) — never further off its word than this.
READING_SNAP_S = 0.15


def reads_phrase(item):
    """Is the template a lockup that reads a phrase row by row (its rows
    are timed to the speech: ``reading``)?"""
    return bool(_spec(item).get("reads_phrase"))


def reads_onsets(item):
    """Does the template reveal its printed words on their spoken onsets
    (spec ``reads_onsets``: marker_text's lines; every ``reads_phrase``
    lockup too)? Such an item gets a ``reading`` (MotionItem.reading)."""
    sp = _spec(item)
    return bool(sp.get("reads_phrase") or sp.get("reads_onsets"))


def phrase_ids(words):
    """Per caption word, the index of its phrase (sentence or clause): a
    new phrase after sentence/clause punctuation, an insert break, a source
    jump of PHRASE_GAP_S or a program pause of PHRASE_PAUSE_S. Commas do
    not end one (a list is one phrase)."""
    out, k = [], 0
    for i, w in enumerate(words):
        if i:
            prev = words[i - 1]
            raw = str(prev.get("w") or "").rstrip("\"'”’) ")
            try:
                gap = float(w["t0"]) - float(prev["t1"])
                jump = float(w.get("src_t0", w["t0"])) - float(prev.get("src_t1", prev["t1"]))
            except (KeyError, TypeError, ValueError):
                gap = jump = 0.0
            if (raw and raw[-1] in _PHRASE_END) or raw in ("-", "—", "–") or w.get("brk") \
                    or gap >= PHRASE_PAUSE_S or abs(jump) >= PHRASE_GAP_S:
                k += 1
        out.append(k)
    return out


def carried_by(m, words, word_toks, mids, hero_only=None):
    """Indices of the caption words graphic ``m`` shows (see the module
    docstring; MODE_HERO keeps only its hero words unless ``hero_only`` is
    False)."""
    if persistent(m):
        # the standing headline is a claim, not the spoken line: it never
        # takes a word out of the captions it runs beside
        return set()
    s, e = float(m["start"]), float(m["end"])
    seq, hero, solo = shown_tokens(m)
    # a word still being said as the graphic leaves counts: the graphic
    # shows it while it is heard ("140" spoken across a slam's exit)
    idx = [i for i, t in enumerate(mids) if s - CARRY_LEAD_S <= t and
           (t <= e or float(words[i]["t0"]) < e - 0.04)]
    lead_n = sum(1 for i in idx if mids[i] < s)
    local = carried_indices([word_toks[i] for i in idx], seq, lead_n, solo)
    carried = {idx[k] for k in local}
    if hero_only is None:
        hero_only = mode(m) == MODE_HERO
    if hero_only:
        return {i for i in carried if hero & set(word_toks[i])}
    inside = [i for i in idx if mids[i] >= s]
    return carried | _absorbed(inside, carried, word_toks, words)


def _runs(idx):
    runs = []
    for i in sorted(idx):
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def owned(m, words, word_toks, mids, pids, carried):
    """What graphic ``m`` takes from the captions beyond the words it
    shows, or None when it shows no content word of what is said.
    {"from": program second its phrase is first shown (its first shown
    word, or its start), "absorbed": [] (connector joints between shown
    words go with them in carried_by), "rest": runs of the
    phrase's other words said while it is up — they stay captioned beside
    it (every heard word reaches the screen once: no graphic leaves a word
    to the sound, and a lockup no longer sets them in small type itself),
    "joined"/"yielded"/"beside": [] (older callers)}."""
    if not any(is_content(t) for i in carried for t in word_toks[i]):
        return None
    s, e = float(m["start"]), float(m["end"])
    first = min(carried, key=lambda i: float(words[i]["t0"]))
    start = max(s, float(words[first]["t0"]))
    phrases = {pids[i] for i in carried}
    span = [i for i in range(len(words)) if pids[i] in phrases and i not in carried
            and float(words[i]["t0"]) >= start - 1e-3 and mids[i] < e]
    out = {"from": round(start, 3), "absorbed": [], "rest": [],
           "joined": [], "yielded": [], "beside": []}
    # every word of it the graphic does not show stays captioned, list
    # joints included ("and the Green Revolution agriculture" reads on as
    # the caption beside a list that shows the other items)
    out["rest"] = _runs(span)
    return out


_LINE_BREAK_RE = re.compile(r"\s+/\s*|\s*/\s+")
ONSET_TEXT_MAX_LINES = 3


def display_rows(item):
    """The words each row of a lockup prints, split like the page splits
    them (MG.starWords: whitespace, *accent* stars dropped). A template that
    reveals one text on its onsets (``reads_onsets``: marker_text) is ONE
    row of its words in reading order, its ' / ' breaks dropped like the
    page drops them (at most ONSET_TEXT_MAX_LINES lines)."""
    params = item.get("params") or {}
    if not params.get("rows") and _spec(item).get("reads_onsets"):
        text = _LINE_BREAK_RE.sub("\n", str(params.get("text") or "")).strip()
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        words = [t.replace("*", "") for ln in lines[:ONSET_TEXT_MAX_LINES]
                 for t in ln.split() if t.replace("*", "")]
        return [words] if words else []
    out = []
    for row in params.get("rows") or []:
        text = row.get("text") if isinstance(row, dict) else None
        out.append([t.replace("*", "") for t in str(text or "").split()
                    if t.replace("*", "")])
    return out


def _reading_of(m, words, word_toks, mids, carried):
    """The ``reading`` of lockup ``m``: per row, per printed word, the
    composition second its carried spoken word starts (None: not said,
    e.g. a headline row). ``bridges`` is always [] (see readings)."""
    rows = display_rows(m)
    s = float(m["start"])
    flat_d = [(r, j) for r, ws in enumerate(rows) for j in range(len(ws))]
    toks_d = _token_rows([rows[r][j] for r, j in flat_d])
    seq = [(t, k) for k, ts in enumerate(toks_d) for t in ts]
    near = sorted(i for i in carried)
    flat_w = [(t, i) for i in near for t in word_toks[i]]
    at = [[None] * len(ws) for ws in rows]
    if seq and flat_w:
        sm = difflib.SequenceMatcher(None, [t for t, _k in seq], [t for t, _i in flat_w],
                                     autojunk=False)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                r, j = flat_d[seq[blk.a + k][1]]
                i = flat_w[blk.b + k][1]
                t = round(max(0.0, float(words[i]["t0"]) - s), 3)
                if t <= READING_SNAP_S:
                    t = 0.0             # on the window's first frame (a cut)
                if at[r][j] is None or t < at[r][j]:
                    at[r][j] = t
    if not any(v is not None for row in at for v in row):
        return None
    return {"v": READING_VERSION, "rows": at, "bridges": []}


def readings(edl, index, tl):
    """{item id: reading} for the word-timed items (``reads_onsets``:
    phrase_build lockups, marker_text) of a program: their printed words
    timed to the spoken onsets (a reveal within READING_SNAP_S of the
    item's start shows at the start). ``bridges`` is always empty now — the
    words of the phrase a lockup's rows leave out stay CAPTIONED beside it
    (plan / owned: every heard word reaches the screen once), instead of
    being set as micro bridge rows inside it (the judged 6-line piles with
    1.3%-of-frame type). A windowed item (a stitched piece,
    ``full_duration_s``) keeps the reading its full program gave it."""
    import captions as caplib
    items = [m for m in program_items(edl, tl)
             if reads_onsets(m) and not m.get("full_duration_s")]
    if not items or not (index or {}).get("words"):
        return {}
    words = caplib.transcript_words(edl, index, tl)
    if not words:
        return {}
    word_toks = _token_rows([w.get("w") for w in words])
    mids = [_mid(w) for w in words]
    out = {}
    for m in items:
        carried = carried_by(m, words, word_toks, mids, hero_only=False)
        rd = _reading_of(m, words, word_toks, mids, carried)
        if rd:
            out[m["id"]] = rd
    return out


# A bridge line (one reading path) adds about this much frame height per
# wrapped line to a lockup; a line wraps at about this many characters.
BRIDGE_LINE_H = 0.034
BRIDGE_LINE_CHARS = 34


def bridged_box(box, item):
    """A lockup's ESTIMATED box grown by the bridge lines its reading sets
    (the block stays centred on its y). Used wherever the box is a nominal
    estimate rather than a measured footprint: the browserless keep-out and
    the headline band's yield."""
    if not box:
        return box
    lines = 0
    for b in ((item or {}).get("reading") or {}).get("bridges") or []:
        chars = len(" ".join(str(w.get("t") or "") for w in b.get("words") or []))
        lines += max(1, -(-chars // BRIDGE_LINE_CHARS))
    if not lines:
        return box
    half = BRIDGE_LINE_H * lines / 2.0
    return (box[0], max(0.0, box[1] - half), box[2], min(1.0, box[3] + half))


# How motion/templates/phrase_build.html times its reveals, mirrored for the
# engine's own questions (when a lockup first draws: the headline band's
# yield; when it visibly lands: a sound's visual partner).
LOCKUP_RISE_LEAD_S = 0.06       # a 'rise' word starts this early (readable on it)
LOCKUP_ROW_GAP_S = 0.25         # an unspoken blank-'at' row follows the last word
_JS_FLOAT = re.compile(r"^\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _js_float(v, default):
    """JavaScript parseFloat(v) (a leading number, else ``default``)."""
    m = _JS_FLOAT.match(str(v)) if v is not None else None
    if not m:
        return default
    try:
        n = float(m.group(0))
    except ValueError:
        return default
    return n if math.isfinite(n) else default


def lockup_reveals(item, fps=30.0):
    """Composition seconds at which a phrase_build lockup reveals each of its
    rows and bridge lines, in reading order — exactly as the page times them
    from its ``reading``: a spoken row (and a bridge line) from its first
    spoken word's onset, a row nobody says on its ``at`` (blank: right after
    the previous row), never before a word above it; a 'rise' entrance
    starts LOCKUP_RISE_LEAD_S early. The first value is when the lockup
    first draws a word. [] for any other template."""
    if not isinstance(item, dict) or item.get("template") != "phrase_build":
        return []
    params = item.get("params") or {}
    rd = item.get("reading") if isinstance(item.get("reading"), dict) else {}
    rd_rows = rd.get("rows") if isinstance(rd.get("rows"), list) else []
    # the page sets only its rows (round 4): a stored reading's bridge lines
    # are not drawn, so they reveal nothing
    bridges = (rd.get("bridges") if isinstance(rd.get("bridges"), list) else []) \
        if LOCKUP_SETS_BRIDGES else []
    lead = LOCKUP_RISE_LEAD_S if params.get("entrance") == "rise" else 0.0

    def onset(v):
        n = None if v is None else _js_float(v, None)
        return None if n is None else max(0.0, n - lead)

    rows = []

    def bridge_rows(after):
        for b in bridges:
            if not isinstance(b, dict):
                continue
            try:
                if int(b.get("after", -1)) != after:
                    continue
            except (TypeError, ValueError):
                continue
            ws = [w for w in b.get("words") or []
                  if isinstance(w, dict) and str(w.get("t") or "").strip()]
            if ws:
                rows.append({"known": [onset(w.get("s")) for w in ws], "at": ""})

    bridge_rows(-1)
    shown = display_rows(item)
    for i, row in enumerate((params.get("rows") or [])[:4]):
        words = shown[i] if i < len(shown) else []
        if words:
            k = rd_rows[i] if i < len(rd_rows) and isinstance(rd_rows[i], list) else []
            at = row.get("at") if isinstance(row, dict) else None
            rows.append({"known": [onset(k[j]) if j < len(k) else None
                                   for j in range(len(words))],
                         "at": "" if at is None else str(at).strip()})
        bridge_rows(i)
    stagger = max(0.0, _js_float(params.get("word_stagger", 0.14), 0.0) or 0.0)

    def start_of(r):
        k = [v for v in r["known"] if v is not None]
        if k:
            return min(k)
        return max(0.0, _js_float(r["at"], 0.0)) if r["at"] != "" else None

    out, prev_last, prev_end = [], 0.0, -LOCKUP_ROW_GAP_S
    for idx, r in enumerate(rows):
        n = len(r["known"])
        if any(v is not None for v in r["known"]):
            cur = next(v for v in r["known"] if v is not None)
            times = []
            for v in r["known"]:
                cur = v if v is not None else cur
                times.append(cur)
        else:
            t0 = (max(0.0, _js_float(r["at"], 0.0)) if r["at"] != ""
                  else prev_end + LOCKUP_ROW_GAP_S)
            nxt = start_of(rows[idx + 1]) if idx + 1 < len(rows) else None
            st = min(stagger, (nxt - t0) / n) if (nxt is not None and nxt > t0) else stagger
            if st < stagger and st < 2.0 / float(fps or 30.0):
                st = 0.0
            times = [t0 + j * st for j in range(n)]
        clamped = []
        for t in times:
            prev_last = max(t, prev_last)
            clamped.append(prev_last)
        out.append(round(clamped[0], 4))
        prev_end = clamped[-1]
    return out


# A word of an onset-timed line starts its short rise this early.
ONSET_RISE_LEAD_S = 0.06


def onset_reveals(item):
    """Composition seconds at which a ``reads_onsets`` template that is not
    a lockup (marker_text) reveals each printed word, mirroring the page:
    a word ONSET_RISE_LEAD_S before its spoken onset, a word nobody says
    with the word before it (leading ones with the first said word), never
    before a word before it. [] when fewer than half its printed words are
    said (the page keeps its own authored 0.3 s build) or it is a lockup
    (lockup_reveals)."""
    if not isinstance(item, dict) or reads_phrase(item) or not reads_onsets(item):
        return []
    rd = item.get("reading") if isinstance(item.get("reading"), dict) else {}
    rows = rd.get("rows") if isinstance(rd.get("rows"), list) else []
    n = sum(len(r) for r in display_rows(item))
    flat = [v for row in rows if isinstance(row, list) for v in row][:n]
    known = [None if v is None else _js_float(v, None) for v in flat]
    known = [None if v is None else max(0.0, v - ONSET_RISE_LEAD_S) for v in known]
    if not n or 2 * sum(1 for v in known if v is not None) < n:
        return []
    known += [None] * (n - len(known))
    cur = next(v for v in known if v is not None)
    out = []
    for v in known:
        cur = max(cur, v) if v is not None else cur
        out.append(round(cur, 4))
    return out


def first_reveal(item):
    """When a word-timed item first draws a word (composition seconds), or
    None when its reveals are not word-timed."""
    r = lockup_reveals(item) or onset_reveals(item)
    return r[0] if r else None


def attach_readings(edl, index, tl):
    """Write each whole lockup's ``reading`` (readings) onto its motion item
    in place — and drop a stale one — before anything measures or renders
    it. Never raises: a lockup without a reading reveals on its authored
    ``at`` times, in reading order."""
    try:
        got = readings(edl, index, tl)
    except Exception as e:  # noqa: BLE001
        print(f"[motion] lockup reading skipped: {str(e)[:160]}", flush=True)
        return edl
    for m in edl.get("motion") or []:
        if not isinstance(m, dict) or m.get("full_duration_s") or not reads_onsets(m):
            continue
        if m.get("id") in got:
            m["reading"] = got[m["id"]]
        else:
            m.pop("reading", None)
    return edl


# ── sound-off coverage ───────────────────────────────────────────────────

def _on_screen(edl, tl):
    """[(start, end, token set, label)] of every text element on screen:
    motion graphics and designed texts."""
    out = []
    for m in program_items(edl, tl):
        if persistent(m):
            continue                    # a standing claim is not the spoken line
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
    key = said_key
    shown = {key(w) for w in p.caption_words()}
    # carried: on the graphic (a word it shows, or a joint between two)
    carried = {key(after_mutes[i]) for i, (_o, why) in p.hidden.items()
               if why == "carried"}
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
                    float(spoken[i]["t0"]) - float(spoken[run[-1]]["t1"]) >
                    max(min_gap, SOUND_OFF_GAP_S)):
            flush()
            run = []
        if st != "covered":
            run.append(i)
    flush()
    return gaps


def heard_unshown(edl, index, tl):
    """Every heard content word no caption and no graphic or text shows,
    however short the run (the owner's rule: every heard word reaches the
    screen once) — sound_off_gaps with no minimum length."""
    return sound_off_gaps(edl, index, tl, min_gap=0.0)


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
                cause = (f"motion graphic '{owner}' leaves no caption band clear of it, "
                         "the face and the layout")
                fix = (f"move '{owner}' off the caption band (its y param) or carry "
                       "those words on it")
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
