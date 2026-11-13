"""Pause shaping for the narration: every silence in the read set from the script and the reference band.

The references (28 metromedia stories in 03, atlas whisper-large-v3 timings): a sentence gap has median
0.22 s (p90 0.42), a comma 0.16 s (p90 0.30), and inside a phrase there is no gap at all. Synthetic voices
pause 25-37 times a minute against the references' 5. So, per silence in the signal:
  segment end      the writer's pause_after (its direction), exact
  sentence end     at most CAP["sentence"]
  comma            at most CAP["comma"]
  other            at most CAP["word"]
Silences are found in the signal (10 ms frames under the read's floor), each is given to the one
script-word boundary (punctuation first, the listener's word timings for distance), and only
shortened, except at a segment end. Edits happen inside the quiet core of the silence (narrate.set_pause), so no word is touched.
"""
import numpy as np

CAP = {"03": {"sentence": 0.24, "comma": 0.16, "word": 0.10},
       "04": {"sentence": 0.50, "comma": 0.18, "word": 0.10}}


PAUSES_PM = {"03": 3.0, "04": 6.0}
KEPT = {"03": (0.32, 0.55), "04": (0.40, 0.90)}


def silences(x, sr, floor_db, N, min_s=0.10):
    db, n = N._frames(x, sr)
    q = db < floor_db
    out, i = [], 0
    while i < len(q):
        if q[i]:
            j = i
            while j < len(q) and q[j]:
                j += 1
            if (j - i) * n / sr >= min_s and i > 0 and j < len(q):
                out.append((i * n / sr, j * n / sr))
            i = j
        else:
            i += 1
    return out


def boundaries(words, segments, band):
    cap = CAP.get(band, CAP["03"])
    seg_last, k = {}, 0
    for si, s in enumerate(segments):
        k += len(s["text"].split())
        seg_last[k - 1] = si
    out = []
    for i, (a, b) in enumerate(zip(words, words[1:])):
        mid = (a["end"] + b["start"]) / 2
        if i in seg_last:
            out.append({"t": mid, "want": max(float(segments[seg_last[i]].get("pause_after") or 0.0), 0.06),
                        "exact": True, "kind": "segment", "after": a["w"]})
            continue
        p = a["w"].rstrip("\"'”’)")[-1:]
        kind = "sentence" if p in ".!?" else "comma" if p in ",;:—–-" else "word"
        out.append({"t": mid, "want": cap[kind], "exact": False, "kind": kind, "after": a["w"]})
    return out


def shape(x, sr, text_segments, words, N, band="03"):
    """Return (shaped signal, edits). words: every script word with start/end (narrate.map_words);
    N: the narrate module (set_pause, _frames)."""
    db, _ = N._frames(x, sr)
    floor_db = max(np.percentile(db, 90) - 38, np.percentile(db, 3) + 6)
    sil = silences(x, sr, floor_db, N)
    bnd = boundaries(words, text_segments, band)
    # The writer's pauses are its ranking of the beats. The references pause 4.0 (03) and 8.2 (04) times
    # a minute by the listener's word gaps, 1 (03) by silences of 0.3 s in their voice stems; a script asking
    # for more keeps its biggest beats as pauses (KEPT, seconds) and reads the rest as sentence gaps.
    seg = [b for b in bnd if b["exact"]]
    keep = max(1, round(PAUSES_PM.get(band, 4.0) * len(x) / sr / 60))
    for r, b in enumerate(sorted(seg, key=lambda b: -b["want"])):
        b["asked"] = b["want"]
        lo, hi = KEPT.get(band, KEPT["03"])
        b["want"] = min(max(b["want"], lo), hi) if (r < keep and b["want"] >= 0.3) else min(b["want"], CAP.get(band, CAP["03"])["sentence"])
    # Each silence goes to one boundary. The listener's word timings drift into a silence (a word
    # stretched over the pause), so punctuation wins: a silence with a segment, sentence or comma
    # boundary within 0.6 s belongs to the nearest of those; only a silence with none near is a
    # pause inside a phrase (nearest word boundary within 0.35 s). A boundary takes one silence.
    pairs = []
    for si, (a, b) in enumerate(sil):
        c = (a + b) / 2
        near = [(abs(bd["t"] - c), k) for k, bd in enumerate(bnd)]
        punct = [(d, k) for d, k in near if bnd[k]["kind"] != "word" and d <= 0.6]
        pick = min(punct) if punct else min([(d, k) for d, k in near if d <= 0.35] or [(None, None)])
        if pick[1] is not None:
            pairs.append((0 if punct else 1, pick[0], si, pick[1]))
    taken_s, taken_b, plan = set(), set(), []
    for _, d, si, bi in sorted(pairs):
        if si in taken_s or bi in taken_b:
            continue
        taken_s.add(si)
        taken_b.add(bi)
        plan.append((sil[si], bnd[bi]))
    # a silence that lost its punctuation to a nearer one is a pause inside the phrase
    for si, (a, b) in enumerate(sil):
        if si in taken_s:
            continue
        c = (a + b) / 2
        free = [(abs(bd["t"] - c), k) for k, bd in enumerate(bnd) if k not in taken_b and bd["kind"] == "word"
                and abs(bd["t"] - c) <= 0.35]
        if free:
            k = min(free)[1]
            taken_b.add(k)
            plan.append(((a, b), bnd[k]))
    edits = []
    for (a, b), bd in sorted(plan, key=lambda r: -r[0][0]):
        have = b - a
        want = bd["want"]
        if not bd["exact"] and have <= want + 0.02:
            continue
        if bd["exact"] and abs(have - want) <= 0.02:
            continue
        x, dlt, had = N.set_pause(x, sr, (a + b) / 2, want, floor_db)
        edits.append({"t": round(a, 3), "kind": bd["kind"], "after": bd["after"], "had": round(had, 3), "want": round(want, 3),
                      "asked": bd.get("asked"), "shift_s": round(dlt / sr, 3)})
    # a segment end with no silence at all (two segments read together): reported
    for k, bd in enumerate(bnd):
        if bd["exact"] and k not in taken_b and bd["want"] >= 0.12:
            edits.append({"t": round(bd["t"], 3), "kind": "segment", "after": bd["after"], "had": 0.0, "want": bd["want"],
                          "skipped": "no silence in the signal"})
    return x, sorted(edits, key=lambda e: e["t"])


def remap(t, edits):
    """A time in the read before shaping, in the read after it."""
    return t + sum(e.get("shift_s", 0) for e in edits if e["t"] < t)


def remap_words(words, edits):
    return [dict(w, start=remap(w["start"], edits), end=remap(w["end"], edits)) for w in words]


def stress_spans(words, segments, edits, N, gain_db=0.0):
    """(start, end, gain) of every word the writer marked for emphasis (its segment's `emphasis`), in the
    shaped read's time. words: the script words with times before shaping (narrate.map_words)."""
    out, k = [], 0
    for s in segments:
        toks = s["text"].split()
        span = words[k:k + len(toks)]
        for e in s.get("emphasis") or []:
            en = N.measure.norm_words(str(e))
            for w in span:
                wn = N.measure.norm_words(w["w"])
                if en and (wn == en or (len(en) == 1 and en[0] in wn)):
                    out.append((remap(w["start"], edits), remap(w["end"], edits), gain_db))
                    break
        k += len(toks)
    return out
