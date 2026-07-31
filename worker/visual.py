"""Time-sampled visual perception: what is on screen, and when it changes.

ROUND 69. THE SAMPLING UNIT WAS THE SHOT, AND HALF OF ALL VIDEOS ARE ONE SHOT.
Until now the index pulled exactly one mid-frame per detected shot and
captioned it. Measured across 177 real production indexes:

  * 81 of them (46%) are a SINGLE shot — every locked-off talking head,
    screen recording and timelapse — so the agent's entire visual description
    of the video was ONE 320x180 thumbnail;
  * the worst case was a 19.3-minute video whose whole visual description
    came from a single frame at 9:38;
  * median footage per captioned frame was 12.1s, mean 35.5s.

None of that is a model-quality problem. The model was answering questions
about footage it had been shown one frame of.

WHY SHOTS WERE NOT SIMPLY SUBDIVIDED
A shot boundary MEANS something downstream: timeline.transition_junctions
uses "these two sides came from different shots" to decide where a transition
may land, which is the whole of round 48's fix for the customer who got 45
whip pans through one continuous take. Splitting long shots to manufacture
more frames would have manufactured scene changes with them, and every
transition in the product would have gone back to landing on jump cuts.

So this is a SECOND, independent track. Shots stay shots. MOMENTS are sampled
on a clock and say what is on screen at a time. Shot captions are then DERIVED
from the moment nearest each shot's midpoint, so every existing reader of
`shot.caption` — the burned-caption detector, the taste audit, get_shots, the
turn prompt — is unchanged and simply has better company.

THE COST CEILING DOES NOT MOVE
MAX_VISION_SHEETS (12) x PER_SHEET (25) = 300 tiles per index has bounded this
since round 22. A single-shot video spent 1 of that 300. Nothing here raises
the cap; it only stops leaving it unspent. The median prod video (79s) samples
to ~20 tiles = ONE contact sheet = one vision call, exactly what it costs
today.

READING IT BACK IS RUN-LENGTH ENCODED
150 moments of a static talking head are 150 near-identical sentences, which
would be a wall of tokens that buries the two moments where something actually
happened. `timeline_lines` collapses consecutive near-identical descriptions
into one span, so what the agent reads is a list of CHANGES — which is what an
editor wants from a timeline in the first place.
"""

import bisect
import re

# Two consecutive descriptions sharing this fraction of their words are the
# same picture described twice. Deliberately high: merging two genuinely
# different moments HIDES A CHANGE from the editor, while failing to merge
# only costs a line.
#
# Measured on TOKENS, not characters. Character similarity was the first
# implementation and it is wrong here for a specific reason: these strings
# share a long boilerplate prefix (setting | people) and differ only in the
# tail that carries the news, so "room | one man | scene 0" and
# "room | one man | scene 1" come out 96% similar and merge — which collapsed
# a whole video into one line. Token overlap prices the difference where it
# actually is, and as a bonus is not fooled by a reordered clause.
SAME_TOKEN_RATIO = 0.8
# A grid point this close to a shot midpoint is that midpoint; sampling both
# spends two tiles of a hard budget on one picture.
MIN_GAP_FRAC = 0.5
# A grid point this close to a shot midpoint is that midpoint; sampling both
# spends two tiles of a hard budget on one picture.
def sample_points(shots, duration, step_s, budget):
    """Where to look. Returns ([{t, shot, shot_mid}], capped: bool).

    Shot midpoints come first and are never dropped while they fit, because
    shot.caption is derived from them. Only when the shots ALONE exceed the
    budget are they thinned evenly — which is exactly what the old sheet
    sampling did, so a 400-shot video degrades the way it always has.
    """
    budget = max(1, int(budget or 1))
    step_s = max(0.25, float(step_s or 0.25))
    duration = max(0.0, float(duration or 0.0))

    mids = sorted((round((float(s.start) + float(s.end)) / 2.0, 2), int(s.id))
                  for s in shots)
    capped = False
    if len(mids) > budget:
        stride = len(mids) / float(budget)
        keep = sorted({min(len(mids) - 1, int(i * stride))
                       for i in range(budget)})
        mids = [mids[i] for i in keep]
        capped = True
        grid = []
    else:
        room = budget - len(mids)
        grid = []
        if room > 0 and duration > 0:
            # The grid stretches rather than the budget: a 3-hour video
            # samples every 36s instead of firing 2700 vision tiles.
            grid_step = max(step_s, duration / float(room))
            min_gap = grid_step * MIN_GAP_FRAC
            mid_ts = [m[0] for m in mids]
            t = grid_step / 2.0
            while t < duration and len(grid) < room:
                if not _near(mid_ts, t, min_gap):
                    grid.append(round(t, 2))
                t += grid_step

    bounds = sorted((float(s.start), float(s.end), int(s.id)) for s in shots)
    points = [{"t": t, "shot": sid, "shot_mid": True} for t, sid in mids]
    points += [{"t": t, "shot": _shot_at(bounds, t), "shot_mid": False}
               for t in grid]
    points.sort(key=lambda p: p["t"])
    return points, capped


def _near(sorted_ts, t, gap):
    if not sorted_ts:
        return False
    i = bisect.bisect_left(sorted_ts, t)
    for j in (i - 1, i):
        if 0 <= j < len(sorted_ts) and abs(sorted_ts[j] - t) < gap:
            return True
    return False


def _shot_at(bounds, t):
    """The shot containing t. Falls back to the nearest one rather than 0 —
    a moment always belongs to some shot, and a sentinel id would look like
    a real shot to every reader."""
    if not bounds:
        return 1
    for start, end, sid in bounds:
        if start <= t < end:
            return sid
    return bounds[-1][2] if t >= bounds[-1][0] else bounds[0][2]


def derive_shot_captions(shots, moments):
    """Give every shot the caption of the moment nearest its midpoint.

    This is what keeps `shot.caption` — read by the burned-caption warning,
    taste.critique, get_shots and the turn prompt — working untouched while
    the sampling underneath it changes completely.
    """
    by_shot = {}
    for m in moments:
        if m.caption is None:
            continue
        by_shot.setdefault(int(m.shot), []).append(m)
    for s in shots:
        cands = by_shot.get(int(s.id))
        if not cands:
            continue
        mid = (float(s.start) + float(s.end)) / 2.0
        s.caption = min(cands, key=lambda m: abs(float(m.t) - mid)).caption
    return shots


def _describe(caption):
    if not caption:
        return ""
    if not isinstance(caption, dict):
        caption = caption.model_dump()
    parts = [caption.get("setting"), caption.get("people"),
             caption.get("action")]
    return " | ".join(p for p in parts if p)


def _extras(caption):
    if not caption:
        return ""
    if not isinstance(caption, dict):
        caption = caption.model_dump()
    out = ""
    ost = (caption.get("on_screen_text") or "").strip()
    if ost:
        out += f'  on-screen text: "{ost}"'
    if caption.get("subtitles"):
        out += "  [burned-in captions visible]"
    return out


def _fmt(t):
    m, s = divmod(int(float(t)), 60)
    return f"{m}:{s:02d}"


def collapse(moments, start=None, end=None):
    """Consecutive near-identical moments -> spans. Returns
    [{t0, t1, n, caption}] in time order.

    On-screen text is compared too: a slide deck whose framing never changes
    is a different moment every time the words on it change, and that is
    precisely the change an editor is looking for.
    """
    rows = []
    for m in moments:
        t = float(m["t"] if isinstance(m, dict) else m.t)
        if start is not None and t < float(start):
            continue
        if end is not None and t > float(end):
            continue
        cap = m["caption"] if isinstance(m, dict) else m.caption
        rows.append((t, cap))
    rows.sort(key=lambda r: r[0])

    spans = []
    for t, cap in rows:
        if spans and _same(spans[-1]["caption"], cap):
            spans[-1]["t1"] = t
            spans[-1]["n"] += 1
            continue
        spans.append({"t0": t, "t1": t, "n": 1, "caption": cap})
    return spans


def _as_dict(caption):
    if not caption:
        return {}
    return caption if isinstance(caption, dict) else caption.model_dump()


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _same(a, b):
    """Do these two captions describe the same picture?

    Two fields are CATEGORICAL, compared exactly rather than fuzzily, because
    they are facts rather than descriptions: the words on screen either
    changed or they did not (a slide deck's framing never moves — the text on
    it IS the edit), and burned-in captions appearing is exactly the kind of
    change this timeline exists to surface. Everything else is prose and is
    compared by word overlap.
    """
    a, b = _as_dict(a), _as_dict(b)
    if _norm_text(a.get("on_screen_text")) != _norm_text(b.get("on_screen_text")):
        return False
    if bool(a.get("subtitles")) != bool(b.get("subtitles")):
        return False
    ta, tb = _tokens(_describe(a)), _tokens(_describe(b))
    if not ta and not tb:
        return True
    if not ta or not tb:
        return False
    return len(ta & tb) / float(len(ta | tb)) >= SAME_TOKEN_RATIO


def _norm_text(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def timeline_lines(moments, start=None, end=None, max_lines=60, indent="  "):
    """The visual timeline as the agent reads it: one line per CHANGE.

    Over-long timelines are thinned by dropping the SHORTEST spans first —
    a two-second insert card matters less than the two minutes either side of
    it, and dropping from the tail would hide the end of the video.
    """
    spans = collapse(moments, start, end)
    if not spans:
        return []
    dropped = 0
    if len(spans) > max_lines:
        order = sorted(range(len(spans)),
                       key=lambda i: (spans[i]["t1"] - spans[i]["t0"],
                                      -spans[i]["n"]), reverse=True)
        keep = sorted(order[:max_lines])
        dropped = len(spans) - len(keep)
        spans = [spans[i] for i in keep]
    lines = []
    for s in spans:
        desc = _describe(s["caption"]) or "(no visual description)"
        when = (_fmt(s["t0"]) if s["t1"] - s["t0"] < 0.01
                else f"{_fmt(s['t0'])}-{_fmt(s['t1'])}")
        lines.append(f"{indent}{when} {desc}{_extras(s['caption'])}")
    if dropped:
        lines.append(f"{indent}... {dropped} shorter moment(s) not listed — "
                     "call get_shots(start, end) on a narrower range for them")
    return lines
