"""Lyrics on the singer's timing (Wave 2 track 2C).

ROADMAP 2C: "Demucs vocals -> faster-whisper word times -> DP alignment to
the user's lyrics text". The recogniser's words on a sung vocal are noisy
(misheard, merged, dropped), so the user's lyrics are aligned to them with
a Needleman-Wunsch dynamic programme over normalised tokens (a fuzzy match
scores by edit similarity; gaps are cheap), and every lyric word that no
recognised word matched takes its time by interpolation between its matched
neighbours, flagged ``estimated``. The output is the lyric text with
per-word onsets/offsets the typesetter and the syllable splitter read.

Pure functions; the vocals and the recognition are the caller's (the
existing stems and transcription jobs).
"""

import difflib
import re

_TOK = re.compile(r"[^\w']+")
GAP = -0.6
MATCH_MIN = 0.55


def norm(w):
    return _TOK.sub("", str(w or "").lower().replace("’", "'"))


def tokens(text):
    """The lyric words in order (line breaks kept as ``brk`` flags)."""
    out = []
    for li, line in enumerate(str(text or "").splitlines()):
        ws = [w for w in line.split() if norm(w)]
        for k, w in enumerate(ws):
            out.append({"w": w, "line": li, "brk": k == 0 and li > 0})
    return out


def _sim(a, b):
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def align(lyrics_text, asr_words):
    """[{w, t0, t1, conf, line, estimated}] for every lyric word.
    ``asr_words``: [{w, t0, t1, p?}] (the recogniser's words on the vocal
    stem, on the song's clock)."""
    lyr = tokens(lyrics_text)
    asr = [w for w in asr_words or [] if norm(w.get("w"))]
    n, m = len(lyr), len(asr)
    if not n:
        return []
    la = [norm(x["w"]) for x in lyr]
    aa = [norm(x.get("w")) for x in asr]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0], back[i][0] = score[i - 1][0] + GAP, 1
    for j in range(1, m + 1):
        score[0][j], back[0][j] = score[0][j - 1] + GAP * 0.5, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = _sim(la[i - 1], aa[j - 1])
            diag = score[i - 1][j - 1] + (2 * s - 1 if s >= MATCH_MIN else -1)
            up = score[i - 1][j] + GAP
            left = score[i][j - 1] + GAP * 0.5        # extra recognised word
            best = max(diag, up, left)
            score[i][j] = best
            back[i][j] = 0 if best == diag else 1 if best == up else 2
    pairs = {}
    i, j = n, m
    while i > 0 and j > 0:
        b = back[i][j]
        if b == 0:
            if _sim(la[i - 1], aa[j - 1]) >= MATCH_MIN:
                pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif b == 1:
            i -= 1
        else:
            j -= 1
    out = []
    for k, t in enumerate(lyr):
        if k in pairs:
            a = asr[pairs[k]]
            out.append({"w": t["w"], "t0": round(float(a["t0"]), 3),
                        "t1": round(float(a["t1"]), 3),
                        "conf": round(_sim(la[k], aa[pairs[k]]) *
                                      float(a.get("p") or 1.0), 3),
                        "line": t["line"], "estimated": False})
        else:
            out.append({"w": t["w"], "t0": None, "t1": None, "conf": 0.0,
                        "line": t["line"], "estimated": True})
    _interpolate(out)
    return out


def _interpolate(rows):
    known = [(k, r) for k, r in enumerate(rows) if r["t0"] is not None]
    if not known:
        return
    for k, r in enumerate(rows):
        if r["t0"] is not None:
            continue
        prev = max(((i, x) for i, x in known if i < k), default=None,
                   key=lambda p: p[0])
        nxt = min(((i, x) for i, x in known if i > k), default=None,
                  key=lambda p: p[0])
        if prev and nxt:
            span = nxt[1]["t0"] - prev[1]["t1"]
            gaps = nxt[0] - prev[0]
            t0 = prev[1]["t1"] + span * (k - prev[0] - 1) / max(1, gaps - 1) \
                if gaps > 1 else prev[1]["t1"]
            r["t0"] = round(t0, 3)
            r["t1"] = round(t0 + max(0.08, span / max(1, gaps)), 3)
        elif prev:
            r["t0"] = round(prev[1]["t1"] + 0.25 * (k - prev[0] - 1), 3)
            r["t1"] = round(r["t0"] + 0.25, 3)
        else:
            r["t1"] = round(nxt[1]["t0"] - 0.25 * (nxt[0] - k - 1), 3)
            r["t0"] = round(max(0.0, r["t1"] - 0.25), 3)


def median_error(aligned, truth):
    """Median |onset error| (s) of aligned lyric words against true onsets
    (a list of the same length, None = unknown)."""
    errs = sorted(abs(a["t0"] - t) for a, t in zip(aligned, truth)
                  if t is not None and a["t0"] is not None)
    if not errs:
        return None
    return errs[len(errs) // 2]
