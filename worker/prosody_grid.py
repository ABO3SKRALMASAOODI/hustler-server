"""The speech prosody grid: a RhythmMap for a voice (track 0B).

A talking head has no music track, so its clock is the speaker: where words
start, which ones land (stress), where phrases end and where the speaker
breathes. Devices bind to those moments (``bind.on`` = word / stress /
syllable / phrase_end, ``rhythm`` = "speech") the way a music video binds to
beats — "flash on the three strongest stresses", "cut on the phrase end".

Built entirely from data the product already has, so it runs on every lane
(no audio decode, no model):

  * the index transcript's words (``w``, ``t0``, ``t1`` on the SOURCE clock,
    optional ``p`` confidence);
  * the perception sidecar's speech-band envelope, through
    ``perception.word_stress`` (or scores the caller already has).

Output (``build``) is a ``rhythm_types`` map, clock ``source``:

  onsets   word (every word, strength = its acoustic stress), syllable
           (vowel-group estimate inside multi-syllable words), stress (the
           prominent words, see ``stress_peaks``), phrase_end (each phrase's
           last word END), pause_end (the first word after a pause)
  sections one ``phrase`` per intonation phrase, energy = mean stress
  gaps     every pause >= PAUSE_S
  beats / downbeats / bpm / drops: empty — speech has no meter and this
           producer never invents one (INTERFACES.md §4.6)

Stress peaks are the words a listener hears land: per intonation phrase,
the content word with the highest prominence (acoustic stress, plus
syllable-normalised lengthening) and any other content word that is a
local maximum clearly above its phrase. Function words are not candidates
except the ones English accents for meaning (negations, yes/no, only, all,
least ...). AT-0B-6 measures it against stress labels on V01 149.44-187.0.
"""

import math
import re

import rhythm_types

VERSION = 1
PRODUCER = f"prosody_grid@{VERSION}"

# A silence this long between words is a gap (and its next word a
# pause_end onset).
PAUSE_S = 0.25
# A silence this long, or clause punctuation, ends an intonation phrase.
PHRASE_BREAK_S = 0.18
# A pick needs this much prominence over its phrase's mean candidate
# (in standard deviations of the whole window's candidates).
STRESS_MARGIN_Z = 0.35
# The phrase-final content word carries the nuclear accent in English
# declaratives more often than not: a small prior, never a pick on its own.
NUCLEAR_BONUS_Z = 0.3
# Weight of syllable-normalised lengthening against acoustic stress.
LENGTH_WEIGHT = 0.4
# Two picks closer than this are one emphasis (the stronger wins).
MIN_PEAK_GAP_S = 0.3
# The strongest word of a phrase is a pick only when it reaches this raw
# acoustic stress (a mumbled aside has no peak).
MIN_PEAK_STRESS = 0.25

FUNCTION_WORDS = frozenset("""
a an the and or nor but if then so because as than that this these those
to of in on at by for with from into onto over under about after before
it its it's is was were be been being am are has have had do does did done
will would can could should shall may might must i i'm i've i'd i'll you
you're he he's she she's we we're they they're me him her us them my your
his our their there here which who whom whose what's there's that's
um uh oh ah er hmm like just also very really kind sort
""".split())
# Function-class words English accents for meaning.
ACCENTABLE = frozenset("""
not no never nobody nothing none nowhere neither yes yeah only all every
each least most more less much many why how what when where whole own
""".split())


def token(word):
    w = word.get("w") if isinstance(word, dict) else getattr(word, "w", "")
    return re.sub(r"[^a-z0-9']", "", str(w or "").lower())


def syllable_count(tok):
    """Vowel-group estimate (a heuristic: 2C replaces it with a real
    syllabifier); digits count as their spoken length roughly."""
    if not tok:
        return 1
    if tok.isdigit():
        return max(1, len(tok))
    groups = re.findall(r"[aeiouy]+", tok)
    n = len(groups)
    if tok.endswith("e") and n > 1 and not tok.endswith(("le", "ee", "ye")):
        n -= 1
    return max(1, n)


def _attr(w, key):
    return float(w[key] if isinstance(w, dict) else getattr(w, key))


def is_candidate(tok):
    return bool(tok) and (tok not in FUNCTION_WORDS or tok in ACCENTABLE)


def phrases(words):
    """[[word index, ...], ...] intonation phrases: break on a silence of
    PHRASE_BREAK_S or on clause punctuation after a word."""
    out, cur = [], []
    for i, w in enumerate(words):
        if cur:
            prev = words[i - 1]
            gap = _attr(w, "t0") - _attr(prev, "t1")
            text = str(prev.get("w") if isinstance(prev, dict)
                       else getattr(prev, "w", ""))
            if gap >= PHRASE_BREAK_S or re.search(r"[.?!,;:]$", text):
                out.append(cur)
                cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return out


def _z(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return [0.0] * len(values)
    m = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) or 1.0
    return [0.0 if v is None else (v - m) / sd for v in values]


def prominence(words, scores):
    """Per-word prominence z-scores (None for non-candidates): acoustic
    stress plus LENGTH_WEIGHT x syllable-normalised lengthening, z-scored
    over the window's candidates."""
    toks = [token(w) for w in words]
    cand = [is_candidate(t) for t in toks]
    dur = [(_attr(w, "t1") - _attr(w, "t0")) / syllable_count(t)
           for w, t in zip(words, toks)]
    za = _z([s if c else None for s, c in zip(scores, cand)])
    zd = _z([d if c else None for d, c in zip(dur, cand)])
    return [(za[i] + LENGTH_WEIGHT * zd[i]) if cand[i] else None
            for i in range(len(words))]


def stress_peaks(words, scores):
    """Indexes of the words heard as stressed (sorted). ``scores`` are the
    per-word acoustic stress values 0..1 aligned with ``words``."""
    prom = prominence(words, scores)
    picks = []
    for ph in phrases(words):
        cands = [i for i in ph if prom[i] is not None]
        if not cands:
            continue
        adj = {i: prom[i] for i in cands}
        adj[cands[-1]] += NUCLEAR_BONUS_Z
        best = max(cands, key=lambda i: adj[i])
        mean = sum(adj.values()) / len(adj)
        for k, i in enumerate(cands):
            nbs = [cands[j] for j in (k - 1, k + 1) if 0 <= j < len(cands)]
            local_max = all(adj[i] >= adj[j] for j in nbs)
            if (i == best and scores[i] >= MIN_PEAK_STRESS) or (
                    local_max and adj[i] >= mean + STRESS_MARGIN_Z
                    and scores[i] >= MIN_PEAK_STRESS):
                picks.append(i)
    picks = sorted(set(picks))
    # one emphasis per MIN_PEAK_GAP_S: keep the stronger of a close pair
    kept = []
    for i in picks:
        if kept and _attr(words[i], "t0") - _attr(words[kept[-1]], "t0") \
                < MIN_PEAK_GAP_S:
            if (prom[i] or 0) > (prom[kept[-1]] or 0):
                kept[-1] = i
            continue
        kept.append(i)
    return kept


def envelope_scores(perception, words):
    """Per-word acoustic stress 0..1 from the perception sidecar's speech-band
    envelope (``vb_env``, peak-pooled to ~8.6 Hz).

    The envelope's bins are ~116 ms wide, so a bin straddling a word edge
    carries its loud neighbour: ``perception.word_stress`` (max over every
    bin the word touches) scores "our" in "take OUR whole" as high as the
    stressed words around it. Here a word reads only the bins whose CENTRE
    lies inside it (its middle bin when it is shorter than one), against a
    local ceiling (the 90th percentile of the surrounding ~10 s), so the
    measure follows the word itself. Words past the analysed audio score
    0.0 (never a fabricated value)."""
    env = [float(v) for v in (perception or {}).get("vb_env") or []]
    fps = float((perception or {}).get("vb_env_fps") or 0.0)
    if not env or fps <= 0:
        return [0.0] * len(words)
    half = int(round(5.0 * fps))
    out = []
    for w in words:
        a, b = _attr(w, "t0") * fps, _attr(w, "t1") * fps
        if a >= len(env) - 1:
            out.append(0.0)
            continue
        ks = [k for k in range(int(math.floor(a)), int(math.ceil(b)) + 1)
              if a <= k + 0.5 <= b and 0 <= k < len(env)]
        if not ks:
            ks = [min(len(env) - 1, max(0, int((a + b) / 2.0)))]
        level = max(env[k] for k in ks)
        lo, hi = max(0, ks[0] - half), min(len(env), ks[-1] + half + 1)
        window = sorted(env[lo:hi])
        ceiling = window[min(len(window) - 1,
                             int(0.9 * (len(window) - 1)))] or 1e-9
        out.append(max(0.0, min(1.0, level / ceiling)))
    return out


def voice_features(samples, sr, words, offset=0.0):
    """Per-word acoustic stress 0..1 from the voice itself: F0 peak
    (semitones above the speaker's median, YIN at 10 ms) and intensity
    peak, z-scored over the words and squashed. The richer producer for the
    ``rhythm.speech`` sidecar: it needs the audio (``samples``: mono float
    PCM at ``sr``, starting at source second ``offset``), so it runs where a
    sidecar job runs, never on a browserless agent turn. ~0.2 s per minute
    of audio."""
    import numpy as np
    x = np.asarray(samples, dtype=np.float64)
    hop, win = int(sr * 0.01), int(sr * 0.04)
    if len(x) < win + hop or not words:
        return [0.0] * len(words)
    tmax, tmin = int(sr / 65.0), int(sr / 320.0)
    w_int = win - tmax
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx]
    nfft = 1 << int(math.ceil(math.log2(2 * win)))
    corr = np.fft.irfft(np.conj(np.fft.rfft(fr[:, :w_int], nfft))
                        * np.fft.rfft(fr, nfft), nfft)[:, :tmax + 1]
    sq = np.cumsum(np.pad(fr ** 2, ((0, 0), (1, 0))), axis=1)
    lags = np.arange(tmax + 1)
    d = sq[:, w_int][:, None] + (sq[:, lags + w_int] - sq[:, lags]) - 2 * corr
    d[:, 0] = 0.0
    cmnd = np.ones_like(d)
    cmnd[:, 1:] = d[:, 1:] * lags[None, 1:] / np.maximum(
        np.cumsum(d[:, 1:], axis=1), 1e-12)
    below = cmnd[:, tmin:] < 0.2
    first = np.where(below.any(axis=1), below.argmax(axis=1) + tmin, -1)
    f0 = np.zeros(n)
    for i in np.nonzero(first >= 0)[0]:
        t = int(first[i])
        while t + 1 <= tmax and cmnd[i, t + 1] < cmnd[i, t]:
            t += 1
        f0[i] = sr / t
    db = 20 * np.log10(np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12))
    voiced = (f0 > 0) & (db > np.percentile(db, 30))
    if not voiced.any():
        return [0.0] * len(words)
    semis = np.where(voiced, 12 * np.log2(np.maximum(f0, 1.0)
                                          / np.median(f0[voiced])), np.nan)
    pitch, loud = [], []
    for w in words:
        a = int((_attr(w, "t0") - offset) / 0.01)
        b = max(a + 1, int((_attr(w, "t1") - offset) / 0.01))
        a, b = max(0, min(a, n - 1)), max(1, min(b, n))
        seg = semis[a:b]
        pitch.append(float(np.nanmax(seg)) if np.any(~np.isnan(seg))
                     else None)
        loud.append(float(np.max(db[a:b])))
    zp, zl = _z(pitch), _z(loud)
    out = []
    for p_, a_, b_ in zip(pitch, zp, zl):
        zz = ((a_ if p_ is not None else -1.0) + b_) / 2.0
        out.append(1.0 / (1.0 + math.exp(-2.0 * zz)))
    return out


def _duration_scores(words):
    """Acoustic stand-in when no perception sidecar exists: syllable-
    normalised duration, scaled to 0..1 (a weak cue; confidence reflects
    it)."""
    d = [(_attr(w, "t1") - _attr(w, "t0")) / syllable_count(token(w))
         for w in words]
    if not d:
        return []
    lo, hi = min(d), max(d)
    return [0.5 if hi <= lo else (v - lo) / (hi - lo) for v in d]


def build(words, perception=None, *, src_range=None, ref=None,
          scores=None, voice=None):
    """The speech RhythmMap for ``words`` (index transcript, SOURCE clock)
    inside ``src_range`` = (t0, t1) when given.

    Acoustic stress per word, best source first: ``scores`` (0..1, aligned
    with the selected words, from a caller that already has them);
    ``voice`` = (samples, sr, offset) — F0 + intensity (``voice_features``,
    the sidecar producer); the perception sidecar's envelope
    (``envelope_scores``, every lane); else a duration-only stand-in at
    lower confidence. Never raises for odd words — they are skipped."""
    sel = []
    for w in words or []:
        try:
            t0, t1 = _attr(w, "t0"), _attr(w, "t1")
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if not (math.isfinite(t0) and math.isfinite(t1)) or t1 < t0:
            continue
        if src_range and not (src_range[0] - 1e-6 <= t0 < src_range[1]):
            continue
        sel.append(w)
    rmap = rhythm_types.empty("speech", clock="source", ref=ref,
                              producer=PRODUCER)
    if not sel:
        return rmap
    acoustic = bool(scores is not None or voice is not None
                    or (perception and perception.get("vb_env")))
    producer = PRODUCER
    if scores is None:
        if voice is not None:
            samples, sr, offset = voice
            scores = voice_features(samples, sr, sel, offset)
            producer = f"{PRODUCER}+voice"
        elif acoustic:
            scores = envelope_scores(perception, sel)
        else:
            scores = _duration_scores(sel)
    rmap["producer"] = producer
    scores = [max(0.0, min(1.0, float(s))) for s in scores]
    toks = [token(w) for w in sel]
    onsets = []
    for w, s, tok in zip(sel, scores, toks):
        t0, t1 = _attr(w, "t0"), _attr(w, "t1")
        onsets.append({"t": t0, "cls": "word", "strength": s})
        n = syllable_count(tok)
        if n > 1 and t1 - t0 > 0.12:
            step = (t1 - t0) / n
            for k in range(1, n):
                onsets.append({"t": t0 + k * step, "cls": "syllable",
                               "strength": round(0.3 * s, 3)})
    for i in stress_peaks(sel, scores):
        onsets.append({"t": _attr(sel[i], "t0"), "cls": "stress",
                       "strength": scores[i]})
    phs = phrases(sel)
    sections, gaps = [], []
    for ph in phs:
        a, b = _attr(sel[ph[0]], "t0"), _attr(sel[ph[-1]], "t1")
        last = str(sel[ph[-1]].get("w") if isinstance(sel[ph[-1]], dict)
                   else getattr(sel[ph[-1]], "w", ""))
        onsets.append({"t": b, "cls": "phrase_end",
                       "strength": 1.0 if re.search(r"[.?!]$", last)
                       else 0.6})
        if b > a:
            sections.append({"t0": a, "t1": b, "label": "phrase",
                             "energy": sum(scores[i] for i in ph) / len(ph)})
    for i in range(1, len(sel)):
        gap = _attr(sel[i], "t0") - _attr(sel[i - 1], "t1")
        if gap >= PAUSE_S:
            gaps.append({"t0": _attr(sel[i - 1], "t1"),
                         "t1": _attr(sel[i], "t0")})
            onsets.append({"t": _attr(sel[i], "t0"), "cls": "pause_end",
                           "strength": min(1.0, gap)})
    order = {"word": 0, "syllable": 1, "stress": 2, "pause_end": 3,
             "phrase_end": 4}
    onsets.sort(key=lambda o: (o["t"], order[o["cls"]]))
    rmap["onsets"] = onsets
    rmap["sections"] = sections
    rmap["gaps"] = gaps
    probs = [float(w.get("p")) for w in sel
             if isinstance(w, dict) and w.get("p") is not None]
    word_q = (sum(probs) / len(probs)) if probs else 0.9
    rmap["confidence"] = round((0.85 if acoustic else 0.45) * word_q, 3)
    return rhythm_types.compact(rmap)


def stress_times(rmap):
    """The stress onset times of a speech map (sorted)."""
    return [o["t"] for o in rmap.get("onsets") or [] if o["cls"] == "stress"]


def f1_against(predicted, labeled, tol_s=0.08):
    """(precision, recall, f1) of predicted vs labelled stress times,
    one-to-one matching within ``tol_s`` (word onsets)."""
    pred, lab = sorted(predicted), sorted(labeled)
    used, tp = set(), 0
    for t in pred:
        best = None
        for j, u in enumerate(lab):
            if j in used or abs(u - t) > tol_s:
                continue
            if best is None or abs(u - t) < abs(lab[best] - t):
                best = j
        if best is not None:
            used.add(best)
            tp += 1
    p = tp / len(pred) if pred else 0.0
    r = tp / len(lab) if lab else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f
