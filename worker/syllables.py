"""Syllable onsets inside spoken words (Wave 2 track 2C).

ROADMAP 2C: "nuclei from speech-band envelope peaks inside each word span,
plus pyphen hyphenation; wav2vec2 only if AT-2C-3 fails". For each word:

1. the expected syllables and their spellings come from pyphen (Liang's
   hyphenation patterns; a vowel-group count when pyphen or the language is
   missing) — "try-ing", "civ-i-li-za-tion";
2. a 3 ms-hop speech-band (300-3400 Hz) loudness envelope over the word's
   span (plus a margin) is smoothed and its peaks are the vowel nuclei;
3. the strongest nuclei, as many as there are syllables and at least
   MIN_SYL_S apart, are kept in order; each syllable's onset is the
   envelope's valley before its nucleus (the first syllable's onset is the
   word's own onset, refined to where its energy rises).

Pure numpy; times are on the clock of the samples handed in (source
seconds when the caller passes source audio and source word times).
"""

import math
import re

import numpy as np

SR = 16000
NFFT = 512
HOP = 48                         # 3 ms
MIN_SYL_S = 0.06
BAND = (300.0, 3400.0)
_VOWELS = re.compile(r"[aeiouy]+", re.I)
_DIC = {}


def _hyphenator(lang="en_US"):
    if lang in _DIC:
        return _DIC[lang]
    try:
        import pyphen
        _DIC[lang] = pyphen.Pyphen(lang=lang)
    except Exception:  # noqa: BLE001 — vowel groups instead
        _DIC[lang] = None
    return _DIC[lang]


def split_word(word, lang="en_US"):
    """The word's syllables as printed pieces ("try", "ing")."""
    w = str(word or "")
    core = re.sub(r"[^\w'’-]", "", w)
    if not core:
        return [w] if w else []
    h = _hyphenator(lang)
    parts = []
    if h is not None:
        try:
            parts = h.inserted(core, hyphen="·").split("·")
        except Exception:  # noqa: BLE001
            parts = []
    groups = len(_VOWELS.findall(core.lower().rstrip("e")
                                 if len(core) > 3 else core.lower())) or 1
    if not parts or (len(parts) == 1 and groups > 1):
        parts = _vowel_split(core, groups)
    # pyphen keeps a minimum of 2 letters at each end ("ok-ay" -> "okay"):
    # trust the vowel groups when they find more syllables
    if len(parts) < groups and len(core) >= 4:
        alt = _vowel_split(core, groups)
        if len(alt) > len(parts):
            parts = alt
    return _tidy(parts)


_DIGRAPHS = ("th", "ch", "sh", "ph", "wh", "ck", "ng", "gh")
_SIB = ("s", "z", "x", "ch", "sh", "ge", "ce", "se", "ze")


def _tidy(parts):
    """Spoken pieces from typographic ones: a cut never splits a digraph
    ("a-nyt-hing" -> "a-ny-thing"), and a silent final -es / -ed joins
    its syllable ("de-ca-des" -> "de-cades"; "gen-er-at-ed" keeps -ed
    after t/d)."""
    parts = [p for p in parts if p]
    for k in range(len(parts) - 1):
        a, b = parts[k], parts[k + 1]
        if a and b and (a[-1:] + b[:1]).lower() in _DIGRAPHS and len(a) > 1:
            parts[k], parts[k + 1] = a[:-1], a[-1] + b
    if len(parts) >= 2:
        last, prev = parts[-1].lower(), parts[-2].lower()
        stem = prev + last[:-2]
        if re.fullmatch(r"[^aeiouy]+es", last) and not stem.endswith(_SIB):
            parts[-2:] = [parts[-2] + parts[-1]]
        elif re.fullmatch(r"[^aeiouy]+ed", last) and not \
                stem.endswith(("t", "d")):
            parts[-2:] = [parts[-2] + parts[-1]]
    return [p for p in parts if p]


def _vowel_split(word, n):
    """Split ``word`` into ``n`` pieces at consonant boundaries between
    vowel groups."""
    if n <= 1:
        return [word]
    spans = [m.span() for m in _VOWELS.finditer(word)]
    if len(spans) < 2:
        return [word]
    cuts = []
    for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        gap = b0 - a1
        cuts.append(a1 + (gap // 2 if gap > 1 else 0))
    cuts = cuts[:n - 1]
    out, prev = [], 0
    for c in cuts:
        if c > prev:
            out.append(word[prev:c])
            prev = c
    out.append(word[prev:])
    return [p for p in out if p]


def count(word, lang="en_US"):
    return len(split_word(word, lang))


def envelope(y, sr=SR, band=BAND):
    """(times, band loudness in dB) at a 3 ms hop."""
    y = np.asarray(y, np.float32)
    if len(y) < NFFT:
        y = np.pad(y, (0, NFFT - len(y)))
    pad = NFFT // 2
    y = np.pad(y, (pad, pad))
    f = np.fft.rfftfreq(NFFT, 1.0 / sr)
    m = (f >= band[0]) & (f <= band[1])
    view = np.lib.stride_tricks.sliding_window_view(y, NFFT)[::HOP]
    win = np.hanning(NFFT).astype(np.float32)
    spec = np.abs(np.fft.rfft(view * win, axis=1))
    e = 10.0 * np.log10((spec[:, m] ** 2).sum(axis=1) + 1e-10)
    k = 5
    e = np.convolve(e, np.ones(k) / k, mode="same")
    t = np.arange(len(e)) * HOP / float(sr)
    return t, e


def _prominent_peaks(e, min_gap):
    """[(index, prominence)] of local maxima: prominence is the drop to the
    higher of the two valleys separating it from a higher peak (or the
    edges). A diphthong's two humps have little prominence between them;
    a syllable's nucleus stands out of the dip before and after it."""
    idx = [i for i in range(1, len(e) - 1) if e[i] >= e[i - 1] and
           e[i] > e[i + 1]]
    out = []
    for i in idx:
        l = i
        while l > 0 and e[l - 1] <= e[i]:
            l -= 1
        r = i
        while r < len(e) - 1 and e[r + 1] <= e[i]:
            r += 1
        lv = float(e[l:i + 1].min())
        rv = float(e[i:r + 1].min())
        out.append((i, float(e[i]) - max(lv, rv)))
    keep = []
    for i, p in sorted(out, key=lambda x: -x[1]):
        if all(abs(i - j) >= min_gap for j, _p in keep):
            keep.append((i, p))
    return keep


NUCLEUS_BAND = (250.0, 1500.0)    # vowels carry their energy here
RISE_FRAC = 0.3                   # onset: 30% of the way up from the dip


def _rise_point(t, e, lo, hi, frac=RISE_FRAC):
    """Where ``e`` first climbs ``frac`` of the way from its minimum in
    [lo, hi] back up to e[hi] (a release burst, a frication or vowel
    onset: measured, AT-2C-3, against the steepest-rise and the valley)."""
    lo, hi = max(0, lo), min(len(e) - 1, hi)
    if hi <= lo:
        return float(t[max(0, min(len(t) - 1, lo))])
    seg = e[lo:hi + 1]
    v = int(np.argmin(seg))
    thr = seg[v] + frac * (e[hi] - seg[v])
    for j in range(v, len(seg)):
        if seg[j] >= thr:
            return float(t[lo + j])
    return float(t[hi])


def word_syllables(y, sr, t0, t1, word, lang="en_US", margin=0.08):
    """[{k, t0, t1, text}] for one word spanning [t0, t1] (seconds on the
    samples' clock).

    Nuclei: the most PROMINENT vowel-band (250-1500 Hz) peaks, one per
    syllable, at least MIN_SYL_S apart. Onsets: where the broadband
    (300-7500 Hz) level has climbed RISE_FRAC of the way from the dip before
    the nucleus back up to it (a stop's release burst, the start of
    frication or of the vowel: the labelling definition). The first onset
    is searched from 50 ms before the word's transcript onset (transcript
    word starts run early or late by tens of milliseconds)."""
    parts = split_word(word, lang)
    n = len(parts)
    a = max(0, int((t0 - margin) * sr))
    b = min(len(y), int((t1 + margin) * sr))
    seg = y[a:b]
    if len(seg) < NFFT or t1 - t0 < 0.05:
        return _even(parts, t0, t1)
    tt, e = envelope(seg, sr, NUCLEUS_BAND)
    _tf, ef = envelope(seg, sr, (300.0, 7500.0))
    tt = tt + a / float(sr)
    lo = int(np.searchsorted(tt, t0 - 0.03))
    hi = int(np.searchsorted(tt, t1 + 0.02))
    if hi - lo < 8:
        return _even(parts, t0, t1)
    ei = e[lo:hi]
    gap = max(2, int(round(MIN_SYL_S * sr / HOP)))
    pk = [(i + lo, p) for i, p in _prominent_peaks(ei, gap)
          if ei[i] > float(np.percentile(ei, 25)) + 3.0]
    if len(pk) < n or not pk:
        return _even(parts, t0, t1)
    nuclei = sorted(i for i, _p in sorted(pk, key=lambda x: -x[1])[:n])
    first_lo = int(np.searchsorted(tt, t0 - 0.05))
    onsets = [_rise_point(tt, ef, first_lo, nuclei[0])]
    for p, q in zip(nuclei, nuclei[1:]):
        v = p + int(np.argmin(e[p:q + 1]))
        onsets.append(_rise_point(tt, ef, v, q))
    ends = onsets[1:] + [max(t1, onsets[-1] + 0.02)]
    return [{"k": k, "t0": round(s, 3), "t1": round(e_, 3), "text": parts[k]}
            for k, (s, e_) in enumerate(zip(onsets, ends))]


def _even(parts, t0, t1, anchors=()):
    n = len(parts)
    w = [max(1, len(_VOWELS.findall(p)) + len(p) * 0.2) for p in parts]
    tot = sum(w)
    out, s = [], t0
    for k, p in enumerate(parts):
        e = t0 + (t1 - t0) * sum(w[:k + 1]) / tot
        out.append({"k": k, "t0": round(s, 3), "t1": round(e, 3), "text": p,
                    "estimated": True})
        s = e
    return out


def split(words, y, sr=SR, lang="en_US"):
    """Syllables of every word: [{i, w, syllables: [...]}] (``words``: dicts
    with w/t0/t1 on the samples' clock)."""
    out = []
    for i, w in enumerate(words):
        try:
            t0, t1 = float(w["t0"]), float(w["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"i": i, "w": w.get("w"),
                    "syllables": word_syllables(y, sr, t0, t1, w.get("w"),
                                                lang)})
    return out


def onset_times(words, y, sr=SR, lang="en_US"):
    """Every syllable onset (seconds), the RhythmMap ``syllable`` class."""
    return [s["t0"] for row in split(words, y, sr, lang)
            for s in row["syllables"]]
