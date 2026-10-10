"""Audio-safe keep edges: every cut a keep-writing tool makes lands in the
quietest moment near where it was asked for, never inside a word.

WHY (judges, Oct 2026 round 7)

The transcript's word times are not the sound. Whisper marks the voiced
core of a word: its onset can sit 50-80 ms after the real one and its end
before the release of the last consonant. Cutting on those times — or a
few hundredths off them — clipped words the viewer hears:

  * Elon's hook kept 130.80 for 'ever' (sound from ~130.75): "somebody
    was... 'ver good"; its first span ended 30 ms inside 'was';
  * the Jobs cut at 9.25 s left the tail of 'fonts,' at -13 dBFS and
    chopped it into room tone;
  * Thiel's edit log carried eleven "boundary lands inside the word"
    warnings that nobody acted on.

So the keep tools no longer only warn: each edge of a real cut they write
(not the source's own start or end, not a split of one continuous run) is
moved, before the write, to the quietest point within REACH_S of where it
was written — measured on the source's sound over a window a little wider
than the crossfade the renderer joins with (renderer.JOIN_XFADE_S), on the
hundredths grid the EDL stores. The transcript bounds the search, so a cut
never reaches into the word on its far side:

  * an edge the transcript puts inside a word, where the sound is loud, is
    a mid-word cut: it moves OUT of the word (a start before its onset, an
    end past its release), to the quietest point there;
  * an edge the transcript puts inside a word where the sound is already
    quiet is the transcript being early or late: it is left where it is;
  * any other edge moves only when a point within reach is at least
    MIN_GAIN_DB quieter (the gap between two words, a breath's end);
  * an edge still in sound after that (a trailing 's', a breath) reaches
    up to TAIL_REACH_S into the removed side for the first quiet point, and
    a start may sit ONSET_SLACK_S past an early transcript onset;
  * an END is judged a frame either side too: on the renderer's block
    clock a span's sound is a whole number of frames long, so it really
    cuts up to a frame from the keep's end (a start is exact).

Without the source's sound (a test context, a lane that cannot read it) an
edge inside a word moves to the word's edge with a short pad, and nothing
else moves. Every move is reported, with the level before and after.

Pure functions over (keep, words, a level reader); ``source_levels`` is the
one reader that touches media (a single ffmpeg call over short windows).
"""
from __future__ import annotations

import bisect
import math
import os
import shutil
import subprocess
import tempfile

REACH_S = 0.08          # how far an edge may move (the judges' ±80 ms)
CROSSFADE_S = 0.012     # what renderer.JOIN_XFADE_S mixes at a cut
# The level AT a cut is read over a little more than the crossfade: a
# 12 ms window finds one-cycle dips inside a decaying word (Elon's
# 'Actually,' read -41 dB at 129.26 between -30 dB neighbours), which are
# not pauses.
LEVEL_WIN_S = 0.024
GRID_S = 0.01           # EDL keep times are hundredths (schemas._r)
MIN_GAIN_DB = 3.0       # a move must buy at least this much quiet
QUIET_DB = 8.0          # within this of the local floor = already quiet
QUIET_ABS_DB = -42.0    # ...or this quiet outright (room tone, a pause)
LOUD_ABS_DB = -30.0     # this loud is sound at a cut whatever its floor
TAIL_REACH_S = 0.25     # an edge still in sound looks this far into the cut
ONSET_SLACK_S = 0.03    # a start may sit this far past the transcript onset
FAR_MARGIN_S = 0.03     # ...stopping this short of the removed side's word
FLOOR_REACH_S = 0.30    # the local floor: the quietest point this close
DIST_DB_PER_S = 20.0    # 0.2 dB per 10 ms: the nearer of two equal minima
WORD_EPS = 0.011        # word containment, as audit.word_at_boundary
# Without the sound: an edge inside a word moves to its edge, padded (the
# transcript's onset runs late and its end early).
PAD_START_S = 0.04
PAD_END_S = 0.08
SR = 16000
FETCH_PAD_S = FLOOR_REACH_S + 0.1
MAX_INPUTS = 24         # windows per ffmpeg call
SILENT_DB = -90.0


# ── which edges are cuts ────────────────────────────────────────────────

def cut_edges(keep, duration=None, prev_keep=None):
    """[(span index, 'start'|'end', time)] for every keep edge that is a
    real cut this write introduces: not the source's start or end, not an
    edge where two spans touch (one continuous run, split for a camera or a
    speed change), and not an edge the previous keep already had."""
    old = set()
    for s, e in prev_keep or []:
        old.add(("start", round(float(s), 2)))
        old.add(("end", round(float(e), 2)))
    out = []
    spans = [(float(s), float(e)) for s, e in keep or []]
    for i, (s, e) in enumerate(spans):
        if s > 0.005 and not (i and abs(spans[i - 1][1] - s) < 0.011) \
                and ("start", round(s, 2)) not in old:
            out.append((i, "start", s))
        last = duration is not None and e >= float(duration) - 0.005
        if not last and not (i + 1 < len(spans)
                             and abs(spans[i + 1][0] - e) < 0.011) \
                and ("end", round(e, 2)) not in old:
            out.append((i, "end", e))
    return out


# ── the transcript's bounds ─────────────────────────────────────────────

class _Words:
    def __init__(self, words):
        rows = []
        for w in words or []:
            try:
                t0 = float(w["t0"] if isinstance(w, dict) else w.t0)
                t1 = float(w["t1"] if isinstance(w, dict) else w.t1)
                text = (w.get("w") if isinstance(w, dict) else w.w) or ""
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if t1 > t0:
                rows.append((t0, t1, str(text)))
        rows.sort()
        self.rows = rows
        self.starts = [r[0] for r in rows]
        self.ends = sorted(r[1] for r in rows)

    def inside(self, b):
        """The word whose interior strictly holds b, or None."""
        i = bisect.bisect_right(self.starts, b)
        for t0, t1, text in self.rows[max(0, i - 3):i]:
            if t0 + WORD_EPS < b < t1 - WORD_EPS:
                return (t0, t1, text)
        return None

    def end_before(self, t):
        """The latest word end at or before t (+eps), or None."""
        i = bisect.bisect_right(self.ends, t + 1e-6)
        return self.ends[i - 1] if i else None

    def start_after(self, t):
        """The earliest word start at or after t (-eps), or None."""
        i = bisect.bisect_left(self.starts, t - 1e-6)
        return self.starts[i] if i < len(self.starts) else None

    def word_starting(self, t):
        i = bisect.bisect_left(self.starts, t - 1e-6)
        return self.rows[i] if i < len(self.rows) else None

    def word_ending(self, t):
        best = None
        for t0, t1, text in self.rows:
            if t1 <= t + 1e-6 and (best is None or t1 > best[1]):
                best = (t0, t1, text)
        return best


def _grid(lo, hi):
    a = int(math.ceil(lo / GRID_S - 1e-6))
    b = int(math.floor(hi / GRID_S + 1e-6))
    return [round(k * GRID_S, 2) for k in range(a, b + 1)]


def _quietest(level, lo, hi, b):
    """(t, dB) — the quietest hundredth in [lo, hi], nearer ones preferred
    among near-equals; None when there is none or no sound to read."""
    best = None
    for t in _grid(lo, hi):
        db = level(t)
        if db is None:
            return None
        cost = db + DIST_DB_PER_S * abs(t - b)
        if best is None or cost < best[2] - 1e-9:
            best = (t, db, cost)
    return None if best is None else (best[0], best[1])


def _floor(level, b):
    vals = [level(t) for t in _grid(b - FLOOR_REACH_S, b + FLOOR_REACH_S)]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def place_edge(side, b, words, level=None, lo_limit=None, hi_limit=None):
    """Where one cut edge should sit: a dict {t, why, word, db_was, db,
    loud} (t == b when it stays). ``side`` is 'start' (sound kept AFTER b)
    or 'end' (sound kept BEFORE b). ``level(t)`` is the source's level
    (dBFS) over the crossfade centred on t, or None without sound.

    The search never crosses into the word on the REMOVED side (for a
    start, the word before; for an end, the word after) and, when the
    transcript puts b inside a word, never moves further into it: a
    mid-word edge only moves out of its word."""
    ws = words if isinstance(words, _Words) else _Words(words)
    lo_limit = 0.0 if lo_limit is None else float(lo_limit)
    hi_limit = math.inf if hi_limit is None else float(hi_limit)
    hit = ws.inside(b)
    db_b = level(b) if level else None
    out = {"t": b, "why": None, "word": hit[2] if hit else None,
           "db_was": db_b, "db": db_b, "loud": False}
    if db_b is None:
        if not hit:
            return out
        t0, t1, _text = hit
        if side == "start":
            prv = ws.end_before(t0)
            t = t0 - PAD_START_S
            if prv is not None:
                t = max(t, prv + 0.01)
            t = math.floor(min(t, t0) / GRID_S + 1e-6) * GRID_S
        else:
            nxt = ws.start_after(t1)
            t = t1 + PAD_END_S
            if nxt is not None:
                t = min(t, nxt - 0.01)
            t = math.ceil(max(t, t1) / GRID_S - 1e-6) * GRID_S
        t = round(min(max(t, lo_limit), hi_limit), 2)
        if abs(t - b) >= 0.005:
            out.update(t=t, why="word")
        return out
    floor = _floor(level, b)
    loud = _is_loud(db_b, floor)
    if hit and not loud:
        # The transcript holds a word here but the sound is quiet: its
        # timing is off (Whisper's onsets run late, its ends early).
        out["quiet_already"] = True
        return out
    onset = None
    if side == "start":
        far = ws.end_before(hit[0] if hit else b)
        lo = b - REACH_S if far is None else max(b - REACH_S, far)
        if hit:
            hi = b
        else:
            onset = ws.start_after(b)
            # Whisper's onset can also run EARLY (Elon's 'if': 129.28 in the
            # transcript, sound from 129.30): the pause may sit just past it.
            hi = b + REACH_S if onset is None \
                else min(b + REACH_S, onset + ONSET_SLACK_S)
    else:
        far = ws.start_after(hit[1] if hit else b)
        hi = b + REACH_S if far is None else min(b + REACH_S, far)
        if hit:
            lo = b
        else:
            prv = ws.end_before(b)
            lo = b - REACH_S if prv is None else max(b - REACH_S, prv)
    lo, hi = max(lo, lo_limit), min(hi, hi_limit)
    q = _quietest(level, min(lo, b), max(hi, b), b) \
        if hi >= lo - 1e-9 else None
    if q is not None and onset is not None and q[0] > onset + 1e-6 and \
            _is_loud(q[1], floor):
        # past the transcript's onset only into a real pause
        q = _quietest(level, min(lo, b), max(min(hi, onset), b), b)
    if q is None:
        out["loud"] = loud
        return out
    t, db = q
    if abs(t - b) >= 0.005 and db_b - db >= MIN_GAIN_DB:
        out.update(t=t, db=db, why="word" if hit else "quiet")
        loud = _is_loud(db, floor)
    if loud:
        # Still in sound: a word's release (a trailing 's', a breath) runs
        # past Whisper's end and an onset starts before its start. Reach
        # further into the REMOVED side — keeping more of the release or
        # the lead-in, never the far word — for the nearest quiet point.
        if side == "end":
            far = ws.start_after(hit[1] if hit else b)
            hi = b + TAIL_REACH_S if far is None \
                else min(b + TAIL_REACH_S, far - FAR_MARGIN_S)
            grid = _grid(out["t"], min(hi, hi_limit))
        else:
            far = ws.end_before(hit[0] if hit else b)
            lo = b - TAIL_REACH_S if far is None \
                else max(b - TAIL_REACH_S, far + FAR_MARGIN_S)
            grid = list(reversed(_grid(max(lo, lo_limit), out["t"])))
        for k, x in enumerate(grid):
            dx = level(x)
            if dx is None:
                break
            if not _is_loud(dx, floor):
                # settle on the local minimum just past the first quiet point
                best = (x, dx)
                for y in grid[k + 1:k + 4]:
                    dy = level(y)
                    if dy is None or dy >= best[1]:
                        break
                    best = (y, dy)
                out.update(t=best[0], db=best[1],
                           why="word" if hit else "quiet")
                loud = False
                break
    out["loud"] = loud
    return out


def _is_loud(db, floor):
    """Sound (not a pause) at a cut: past the room-tone level and either
    plainly loud or well over the local floor."""
    return db > QUIET_ABS_DB and (db > LOUD_ABS_DB or floor is None
                                  or db > floor + QUIET_DB)


def _robust(level, slack):
    """level(t) as the loudest of t and t +- slack: where the renderer may
    really cut. On the block clock a kept span's sound is exactly as long as
    its block — a whole number of frames — so its END lands up to a frame
    either side of the keep's end (renderer.block_clock); a span's start is
    exact."""
    if level is None or not slack:
        return level

    def lv(t):
        vals = [level(round(t + d, 4)) for d in (-slack, 0.0, slack)]
        return None if any(v is None for v in vals) else max(vals)
    return lv


def refine_keep(keep, words, duration=None, prev_keep=None, level=None,
                end_slack=0.0):
    """(new keep, moves, checked): every cut edge this write introduces
    (cut_edges) placed by place_edge. moves: [{side, old, new, why, word,
    db_was, db}]; checked: how many edges were read against the sound.
    ``end_slack`` (seconds, a frame): an END is placed where the sound is
    quiet that far either side too (_robust). Spans stay sorted and never
    cross their neighbours; a span the moves would leave shorter than
    0.05 s keeps its edges."""
    spans = [[float(s), float(e)] for s, e in keep or []]
    if not spans:
        return spans, [], 0
    ws = _Words(words)
    end_level = _robust(level, end_slack)
    moves, checked = [], 0
    for i, side, b in cut_edges(spans, duration, prev_keep):
        lo_limit = spans[i - 1][1] + 0.02 if side == "start" and i else 0.0
        hi_limit = (spans[i + 1][0] - 0.02 if side == "end"
                    and i + 1 < len(spans) else
                    (float(duration) if duration is not None else None))
        if side == "start":
            hi_limit = spans[i][1] - 0.05
        else:
            lo_limit = spans[i][0] + 0.05
        got = place_edge(side, b, ws, end_level if side == "end" else level,
                         lo_limit, hi_limit)
        if got.get("db_was") is not None:
            checked += 1
        if got["why"] is None:
            if got.get("quiet_already"):
                moves.append({"side": side, "old": b, "new": b,
                              "why": "quiet_word", "word": got["word"],
                              "db_was": got["db_was"], "db": got["db"]})
            elif got.get("loud"):
                moves.append({"side": side, "old": b, "new": b,
                              "why": "loud", "word": got["word"],
                              "db_was": got["db_was"], "db": got["db"]})
            continue
        new = round(got["t"], 2)
        spans[i][0 if side == "start" else 1] = new
        moves.append({"side": side, "old": b, "new": new, "why": got["why"],
                      "word": got["word"], "db_was": got["db_was"],
                      "db": got["db"], "loud": bool(got.get("loud"))})
    spans = [s for s in spans if s[1] - s[0] >= 0.05]
    spans.sort(key=lambda x: x[0])
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged, moves, checked


def report(moves, checked):
    """The lines a keep tool appends to its result ([] when nothing to say)."""
    real = [m for m in moves if m["why"] in ("quiet", "word")]
    kept = [m for m in moves if m["why"] == "quiet_word"]
    loud = [m for m in moves if m["why"] == "loud"
            or (m["why"] in ("quiet", "word") and m.get("loud"))]
    if not real and not kept and not loud:
        return []
    bits = []
    for m in real:
        lvl = ""
        if m.get("db_was") is not None and m.get("db") is not None:
            lvl = f", {m['db_was']:.0f} -> {m['db']:.0f} dB"
        what = (f"out of '{m['word']}'" if m["why"] == "word" and m["word"]
                else "to the quiet point")
        bits.append(f"{m['side']} {m['old']:g}->{m['new']:g} ({what}{lvl})")
    lines = []
    if real:
        basis = ("measured on the source sound" if checked
                 else "no source sound to measure: moved to the word's edge")
        lines.append(
            f"AUDIO-SAFE CUTS: moved {len(real)} keep edge"
            f"{'s' if len(real) != 1 else ''} so no cut clips a word ({basis}): "
            + "; ".join(bits) + ". Pass snap_to_words=false to keep exact "
            "times.")
    if kept:
        lines.append(
            "AUDIO-SAFE CUTS: " + ", ".join(
                f"{m['old']:g}" for m in kept)
            + " sit in quiet sound although the transcript places a word "
            "there (its timing is off): left as written.")
    if loud:
        lines.append(
            "AUDIO-SAFE CUTS: no pause within 80 ms of "
            + ", ".join(f"{m['side']} {m['new']:g} ({m['db']:.0f} dB)"
                        for m in loud)
            + ": the cut joins running speech or sound (a crossfade "
            "softens it, it cannot hide it). Keep it only if it sounds "
            "clean; else cut at a breath or sentence end.")
    return lines


def unsafe_edges(keep, words, duration=None, prev_keep=None, level=None):
    """Advisory form for writers that must keep their exact times (an edit
    batch): [(side, old, suggested, why)] for each edge refine_keep would
    move."""
    _new, moves, _checked = refine_keep(keep, words, duration, prev_keep,
                                        level)
    return [(m["side"], m["old"], m["new"], m["why"]) for m in moves
            if m["why"] in ("quiet", "word", "loud")]


# ── reading the sound ───────────────────────────────────────────────────

def _windows(times, duration=None):
    """Merged fetch windows around edge times: [(a, b)]."""
    spans = []
    for t in sorted(float(x) for x in times):
        a = max(0.0, t - FETCH_PAD_S)
        b = t + FETCH_PAD_S
        if duration is not None:
            b = min(float(duration), b)
        if spans and a <= spans[-1][1] + 0.5:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [(a, b) for a, b in spans if b - a > 0.05]


def fetch(source, windows, timeout=30.0, ffmpeg="ffmpeg"):
    """{(a, b): mono float32 samples at SR} for each window of ``source``
    (a local path or a URL ffmpeg can range-read). One ffmpeg process per
    MAX_INPUTS windows; a window that fails to decode is left out."""
    out = {}
    windows = list(windows)
    if not source or not windows:
        return out
    import numpy as np
    tmp = tempfile.mkdtemp(prefix="cutaudio_")
    try:
        for k in range(0, len(windows), MAX_INPUTS):
            chunk = windows[k:k + MAX_INPUTS]
            cmd = [ffmpeg, "-v", "error", "-nostdin", "-y"]
            for a, b in chunk:
                cmd += ["-ss", f"{a:.4f}", "-t", f"{b - a:.4f}", "-i", source]
            paths = []
            for j, _w in enumerate(chunk):
                p = os.path.join(tmp, f"w{k + j}.f32")
                paths.append(p)
                cmd += ["-map", f"{j}:a:0", "-ac", "1", "-ar", str(SR),
                        "-f", "f32le", p]
            try:
                subprocess.run(cmd, capture_output=True, timeout=timeout,
                               check=False)
            except Exception:
                continue
            for w, p in zip(chunk, paths):
                try:
                    x = np.fromfile(p, dtype=np.float32)
                except Exception:
                    continue
                if len(x) > SR * 0.05:
                    out[w] = x
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def level_reader(samples_by_window):
    """level(t) -> dBFS RMS over LEVEL_WIN_S centred on t, or None outside
    every fetched window."""
    import numpy as np
    rows = sorted(samples_by_window.items())
    half = int(round(LEVEL_WIN_S * SR / 2.0))

    def level(t):
        for (a, b), x in rows:
            if a + LEVEL_WIN_S / 2 <= t <= b - LEVEL_WIN_S / 2:
                c = int(round((t - a) * SR))
                seg = x[max(0, c - half):c + half]
                if len(seg) < half:
                    return None
                r = float(np.sqrt(np.mean(np.square(seg, dtype=np.float64))))
                return max(SILENT_DB, 20.0 * math.log10(r + 1e-12))
        return None
    return level


def source_levels(source, times, duration=None, timeout=30.0):
    """A level reader over windows around ``times`` of ``source``, or None
    when nothing could be read."""
    try:
        got = fetch(source, _windows(times, duration), timeout=timeout)
    except Exception:
        return None
    return level_reader(got) if got else None
