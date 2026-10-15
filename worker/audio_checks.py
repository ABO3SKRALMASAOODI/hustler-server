"""Deterministic listening: what the sound says, measured (Wave 2, 2C).

Run report defects 25-27 (JRE #2054):

25  review_audio was unreliable — it echoed the question ("I mean" heard
    where the timings said "other than that" at 0.999), returned bare
    transcriptions, shuffled clips, invented transcripts. ``listen`` answers
    the questions an editor actually asks of the sound from MEASUREMENTS:
    which transcript words are audible at what level, what sound no word
    covers (an untranscribed "um", a breath, a fragment of a cut word),
    clipping and clicks. It never claims to hear a word it is not shown.
26  "audio-safe" cuts left fragments and missed fillers the transcript
    lacks. ``fragments`` finds untranscribed sound on the KEPT side of a cut
    edge — the tail of a word cut away, an "um" the transcript dropped — and
    ``trim_fragments`` moves the edge past it (never into a transcribed
    word), reporting every move.
27  make_shorts opened mid-question. ``sentence_start`` / ``sentence_end``
    snap a clip's range to the transcript's sentence boundaries: a start
    inside a sentence moves back to the sentence's first word (a question
    is never entered mid-way), an end moves on to its last word.

Pure functions over samples / a level reader / transcript words.
"""

import math

import numpy as np

SR = 16000
WIN_S = 0.02
VOICED_DB = 12.0          # above the local floor = sound
ABS_FLOOR_DB = -42.0      # and above this outright
FRAG_MIN_S = 0.04
FRAG_MAX_S = 0.65         # longer than this is speech, not a fragment
EDGE_SCAN_S = 0.45        # how far past a cut edge fragments are looked for
WORD_SLACK_S = 0.03
SENTENCE_BACK_S = 8.0     # a start moves back at most this far
SENTENCE_FWD_S = 5.0      # an end moves on at most this far
_END = (".", "?", "!", "…")


def levels(y, sr=SR, win_s=WIN_S):
    """(times, dBFS RMS) on a ``win_s`` hop (exact window length)."""
    n = max(1, int(round(win_s * sr)))
    m = len(y) // n
    if m <= 0:
        return np.zeros(0), np.zeros(0)
    x = np.asarray(y[:m * n], np.float64).reshape(m, n)
    db = 20.0 * np.log10(np.sqrt((x * x).mean(axis=1)) + 1e-9)
    t = (np.arange(m) + 0.5) * n / float(sr)
    return t, db


def _floor(db):
    return float(np.percentile(db, 10)) if db.size else -90.0


def sound_segments(t, db, min_s=FRAG_MIN_S):
    """[(t0, t1, peak_db)] stretches of sound (VOICED_DB over the floor and
    over ABS_FLOOR_DB)."""
    if not db.size:
        return []
    thr = max(_floor(db) + VOICED_DB, ABS_FLOOR_DB)
    on = db > thr
    out, i = [], 0
    step = (t[1] - t[0]) if len(t) > 1 else WIN_S
    while i < len(on):
        if not on[i]:
            i += 1
            continue
        j = i
        while j < len(on) and on[j]:
            j += 1
        a, b = t[i] - step / 2, t[j - 1] + step / 2
        if b - a >= min_s:
            out.append((round(float(a), 3), round(float(b), 3),
                        round(float(db[i:j].max()), 1)))
        i = j
    return out


def _covered(a, b, words, slack=WORD_SLACK_S):
    """Share of [a, b] the transcript's words cover."""
    if b <= a:
        return 1.0
    cov = 0.0
    for w in words:
        try:
            w0, w1 = float(w["t0"]) - slack, float(w["t1"]) + slack
        except (KeyError, TypeError, ValueError):
            continue
        cov += max(0.0, min(b, w1) - max(a, w0))
    return min(1.0, cov / (b - a))


def untranscribed(t, db, words, t0=None, t1=None):
    """[(a, b, peak_db)] of sound no transcript word covers (fillers,
    breaths, fragments) inside [t0, t1]."""
    out = []
    for a, b, pk in sound_segments(t, db):
        if t0 is not None and b < t0:
            continue
        if t1 is not None and a > t1:
            continue
        if _covered(a, b, words) < 0.25 and b - a <= FRAG_MAX_S * 2:
            out.append((a, b, pk))
    return out


def listen(y, sr, words, t0, t1, *, clock_offset=0.0):
    """The measured answer to "what does the sound say in [t0, t1]?":
    {words: [{w, t0, t1, db, audible}], unexplained: [{t0, t1, db, guess}],
    clipped_s, clicks: [t], floor_db}. ``y`` covers [clock_offset, ...)
    seconds of the clock ``words`` and t0/t1 are on."""
    t, db = levels(y, sr)
    t = t + clock_offset
    sel = (t >= t0) & (t <= t1)
    floor = _floor(db[sel]) if sel.any() else _floor(db)
    rows = []
    span_words = []
    for w in words:
        try:
            a, b = float(w["t0"]), float(w["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        if b < t0 or a > t1:
            continue
        span_words.append(w)
        m = (t >= a) & (t <= b)
        lv = float(db[m].max()) if m.any() else None
        rows.append({"w": w.get("w"), "t0": round(a, 3), "t1": round(b, 3),
                     "db": None if lv is None else round(lv, 1),
                     "audible": bool(lv is not None and lv > max(
                         floor + VOICED_DB / 2, ABS_FLOOR_DB))})
    unexplained = []
    for a, b, pk in untranscribed(t[sel], db[sel], span_words, t0, t1):
        d = b - a
        guess = ("filler (um/uh)" if 0.12 <= d <= 0.65 and pk > floor + 18
                 else "breath or noise" if pk < floor + 18
                 else "fragment" if d < 0.12 else "untranscribed speech")
        unexplained.append({"t0": a, "t1": b, "db": pk, "guess": guess})
    x = np.asarray(y, np.float64)
    a = max(0, int((t0 - clock_offset) * sr))
    b = min(len(x), int((t1 - clock_offset) * sr))
    seg = x[a:b]
    clipped = float((np.abs(seg) >= 0.999).sum()) / sr if seg.size else 0.0
    clicks = []
    if seg.size > 8:
        d = np.abs(np.diff(seg))
        lim = max(0.3, 12.0 * float(np.median(d)) + 1e-6)
        idx = np.nonzero(d > lim)[0]
        last = -1e9
        for i in idx[:200]:
            tt = clock_offset + (a + i) / float(sr)
            if tt - last > 0.05:
                clicks.append(round(tt, 3))
                last = tt
    return {"words": rows, "unexplained": unexplained,
            "clipped_s": round(clipped, 4), "clicks": clicks[:20],
            "floor_db": round(floor, 1)}


# ── cut edges (defect 26) ───────────────────────────────────────────────

def fragments(keep, words, level, step=0.01):
    """[{span, side, edge, t0, t1, db}] untranscribed sound on the kept side
    of each cut edge, between the edge and the first transcribed word
    inside the span (``level``: cut_audio's reader, t -> dBFS or None)."""
    out = []
    ws = sorted((float(w["t0"]), float(w["t1"])) for w in words or []
                if "t0" in w and "t1" in w)
    for i, (s, e) in enumerate(keep or []):
        for side, edge in (("start", float(s)), ("end", float(e))):
            if side == "start":
                first = next((a for a, _b in ws if a >= edge - 1e-3), None)
                lim = min(edge + EDGE_SCAN_S, (first - WORD_SLACK_S)
                          if first is not None else edge + EDGE_SCAN_S,
                          float(e))
                grid = np.arange(edge, lim, step)
            else:
                last = next((b for _a, b in reversed(ws) if b <= edge + 1e-3),
                            None)
                lim = max(edge - EDGE_SCAN_S, (last + WORD_SLACK_S)
                          if last is not None else edge - EDGE_SCAN_S,
                          float(s))
                grid = np.arange(lim, edge, step)
            if len(grid) < 3:
                continue
            vals = [level(float(t)) for t in grid]
            if any(v is None for v in vals):
                continue
            v = np.array(vals)
            floor = min(float(np.percentile(v, 10)), -45.0)
            loud = v > max(floor + VOICED_DB, ABS_FLOOR_DB)
            if not loud.any():
                continue
            # the run of sound that touches the edge (what the cut leaves)
            idx = np.nonzero(loud)[0]
            if side == "start":
                if idx[0] * step > 0.15:
                    continue
                j = idx[0]
                while j + 1 < len(loud) and loud[j + 1]:
                    j += 1
                a, b = float(grid[idx[0]]), float(grid[j]) + step
            else:
                if (len(loud) - 1 - idx[-1]) * step > 0.15:
                    continue
                j = idx[-1]
                while j - 1 >= 0 and loud[j - 1]:
                    j -= 1
                a, b = float(grid[j]), float(grid[idx[-1]]) + step
            if b - a < FRAG_MIN_S or _covered(a, b, words) > 0.25:
                continue
            out.append({"span": i, "side": side, "edge": round(edge, 3),
                        "t0": round(a, 3), "t1": round(b, 3),
                        "db": round(float(v[idx].max()), 1)})
    return out


def trim_fragments(keep, words, level, step=0.01):
    """(new keep, moves): every cut edge with an untranscribed fragment on
    its kept side moves past it, to the quietest point between the fragment
    and the next transcribed word — never into a word, never leaving a span
    shorter than 0.05 s."""
    spans = [[float(a), float(b)] for a, b in keep or []]
    moves = []
    ws = sorted((float(w["t0"]), float(w["t1"])) for w in words or []
                if "t0" in w and "t1" in w)
    for f in fragments(spans, words, level, step):
        i, side = f["span"], f["side"]
        s, e = spans[i]
        if side == "start":
            nxt = next((a for a, _b in ws if a >= f["t1"] - 1e-3), e)
            grid = np.arange(f["t1"], min(nxt, e - 0.05), step)
        else:
            prv = next((b for _a, b in reversed(ws) if b <= f["t0"] + 1e-3),
                       s)
            grid = np.arange(max(prv, s + 0.05), f["t0"], step)
        if not len(grid):
            continue
        vals = [level(float(t)) for t in grid]
        if any(v is None for v in vals):
            continue
        new = round(float(grid[int(np.argmin(vals))]), 2)
        if side == "start" and new < e - 0.05:
            spans[i][0] = new
        elif side == "end" and new > s + 0.05:
            spans[i][1] = new
        else:
            continue
        moves.append({"side": side, "old": f["edge"], "new": new,
                      "fragment": [f["t0"], f["t1"]], "db": f["db"]})
    return spans, moves


def fragment_report(moves):
    if not moves:
        return []
    return ["AUDIO-SAFE (fragments): " + "; ".join(
        f"{m['side']} {m['old']:.2f}s -> {m['new']:.2f}s cut away "
        f"untranscribed sound {m['fragment'][0]:.2f}-{m['fragment'][1]:.2f}s "
        f"({m['db']:.0f} dBFS: a filler or the tail of a cut word)"
        for m in moves[:6])]


# ── sentence boundaries (defect 27) ─────────────────────────────────────

def _sentences(index):
    out = []
    for s in (index or {}).get("sentences") or []:
        try:
            out.append((float(s["t0"]), float(s["t1"]),
                        str(s.get("text") or "")))
        except (KeyError, TypeError, ValueError):
            continue
    if out:
        return sorted(out)
    # no sentence index: build sentences from word punctuation
    words = (index or {}).get("words") or []
    cur, out = [], []
    for w in words:
        try:
            a, b = float(w["t0"]), float(w["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        if cur and a - cur[-1][1] > 1.2:
            out.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
            cur = []
        cur.append((a, b, str(w.get("w") or "")))
        if str(w.get("w") or "").rstrip("\"'”’)").endswith(_END):
            out.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
            cur = []
    if cur:
        out.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
    return out


def sentence_start(index, t, back=SENTENCE_BACK_S):
    """(start, why): ``t`` moved back to the first word of the sentence it
    falls inside (a question is never entered mid-way), or ``t`` unchanged
    (why None) when it already starts one or sits in a pause."""
    sents = _sentences(index)
    for a, b, text in sents:
        if a + 0.15 < t <= b:
            if t - a <= back:
                q = "question" if text.strip().endswith("?") else "sentence"
                return round(a, 2), f"moved back to the start of the {q}"
            return t, None
        if a - 0.15 <= t <= a + 0.15:
            return t, None
    return t, None


def sentence_end(index, t, fwd=SENTENCE_FWD_S):
    """(end, why): ``t`` moved on to the last word of the sentence it falls
    inside, or back to the previous sentence's end when that one is too
    long to finish."""
    sents = _sentences(index)
    for k, (a, b, _text) in enumerate(sents):
        if a <= t < b - 0.15:
            if b - t <= fwd:
                return round(b, 2), "moved on to the end of the sentence"
            prev = sents[k - 1][1] if k else None
            if prev is not None and t - prev <= fwd * 2:
                return round(prev, 2), "moved back to the previous sentence end"
            return t, None
    return t, None
