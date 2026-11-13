"""Signal measurements for narration: pace, pitch, loudness and chunk joins.

One module measures both sides of the VOICE gate, the reference narrators and
our own renders, so the band and the check are computed the same way. Needs
numpy, scipy, soundfile and librosa; pyloudnorm when present (an exact
BS.1770 fallback is built in).
"""
from __future__ import annotations

import math
import re

import numpy as np

FLOOR_DB = -80.0        # noise-floor comparisons are clamped here (dBFS, 50 ms frames)
PAUSE_S = 0.30          # a gap between words at least this long is a pause, not articulation


# ---------------------------------------------------------------- audio io

def load_mono(path, sr=None):
    import soundfile as sf
    x, s = sf.read(str(path), dtype="float32", always_2d=True)
    x = x.mean(1)
    if sr and s != sr:
        x = resample(x, s, sr)
        s = sr
    return x, s


def resample(x, sr, target):
    from scipy.signal import resample_poly
    g = math.gcd(int(sr), int(target))
    return resample_poly(x, int(target) // g, int(sr) // g).astype(np.float32)


# ---------------------------------------------------------------- loudness

def _k_weight(x, sr):
    from scipy.signal import lfilter
    # BS.1770 pre-filter and RLB high-pass, bilinear designs valid at any rate
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh, Vb = 10 ** (G / 20), 10 ** (G / 20) ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a0 = 1 + K / Q + K * K
    b2 = [1, -2, 1]
    a2 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    return lfilter(b2, a2, lfilter(b1, a1, x))


def _block_power(x, sr, win, hop):
    y = _k_weight(np.asarray(x, np.float64), sr) ** 2
    n, h = int(win * sr), int(hop * sr)
    if len(y) < n:
        return np.array([y.mean()]) if len(y) else np.array([1e-12])
    c = np.concatenate([[0.0], np.cumsum(y)])
    starts = np.arange(0, len(y) - n + 1, h)
    return (c[starts + n] - c[starts]) / n


def lufs(x, sr):
    """Integrated loudness, BS.1770-4 gating."""
    p = _block_power(x, sr, 0.4, 0.1)
    l = -0.691 + 10 * np.log10(p + 1e-12)
    p = p[l > -70]
    if not len(p):
        return -70.0
    rel = -0.691 + 10 * np.log10(p.mean()) - 10
    l = -0.691 + 10 * np.log10(p + 1e-12)
    p = p[l > rel]
    return float(-0.691 + 10 * np.log10(p.mean() + 1e-12)) if len(p) else -70.0


def lra(x, sr):
    """Loudness range (EBU 3342): short-term 3 s blocks, p10..p95 after gating."""
    p = _block_power(x, sr, 3.0, 1.0)
    l = -0.691 + 10 * np.log10(p + 1e-12)
    l = l[l > -70]
    if len(l) < 2:
        return 0.0
    rel = -0.691 + 10 * np.log10((10 ** ((l + 0.691) / 10)).mean()) - 20
    l = l[l > rel]
    return float(np.percentile(l, 95) - np.percentile(l, 10)) if len(l) > 1 else 0.0


# ---------------------------------------------------------------- pitch

def f0_voiced(x, sr):
    """Voiced f0 frames (Hz, 10 ms hop), pyin's own voicing decision."""
    import librosa
    y = resample(x, sr, 16000) if sr != 16000 else x
    f0, vflag, vprob = librosa.pyin(y, fmin=60, fmax=520, sr=16000, frame_length=1024,
                                    hop_length=160, fill_na=np.nan)
    # pyin's own voicing decision (its Viterbi path); a per-frame probability
    # cut keeps only a few percent of frames on real speech and biases f0 high
    ok = vflag & np.isfinite(f0)
    return f0[ok]


def pitch(x, sr, hz=None):
    """f0 of the voice: median Hz, range p5..p95 in semitones, spread (std, st)
    and a phrase-level melody measure: the mean within-phrase f0 swing.
    `hz` = voiced frames already measured (f0_voiced), e.g. several takes joined."""
    hz = f0_voiced(x, sr) if hz is None else np.asarray(hz)
    if len(hz) < 50:
        return {"f0_med_hz": None, "range_st": None, "std_st": None, "voiced_s": round(len(hz) * 0.01, 1)}
    med = float(np.median(hz))
    st = 12 * np.log2(hz / med)
    # octave errors: drop frames more than 12 st from the median
    st = st[np.abs(st) < 12]
    return {"f0_med_hz": round(med, 1),
            "range_st": round(float(np.percentile(st, 95) - np.percentile(st, 5)), 2),
            "std_st": round(float(np.std(st)), 2),
            "voiced_s": round(len(hz) * 0.01, 1)}


# ---------------------------------------------------------------- pace

def pace(words):
    """words: [{w,start,end}]. Words per minute overall and while articulating
    (gaps of PAUSE_S or more excluded), pauses per minute and median pause."""
    words = [w for w in words if w.get("end", 0) > w.get("start", 0) - 1e-6]
    if len(words) < 5:
        return {"wpm": None, "wpm_artic": None, "pauses_pm": None, "pause_med_s": None, "words": len(words)}
    span = words[-1]["end"] - words[0]["start"]
    gaps = [b["start"] - a["end"] for a, b in zip(words, words[1:])]
    pauses = [g for g in gaps if g >= PAUSE_S]
    artic = span - sum(pauses)
    return {"wpm": round(60 * len(words) / max(span, 1e-3), 1),
            "wpm_artic": round(60 * len(words) / max(artic, 1e-3), 1),
            "pauses_pm": round(60 * len(pauses) / max(span, 1e-3), 1),
            "pause_med_s": round(float(np.median(pauses)), 2) if pauses else 0.0,
            "words": len(words), "span_s": round(span, 1)}


# ---------------------------------------------------------------- word error

_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
          "fifteen sixteen seventeen eighteen nineteen").split()


_INFLECT = []


def _num_words(tok, year=False):
    try:
        if not _INFLECT:
            import inflect
            _INFLECT.append(inflect.engine())
        e = _INFLECT[0]
        if year and len(tok) == 4 and 1100 <= int(tok) <= 2099 and int(tok) % 100:
            # a year is read in pairs: nineteen ninety four, twenty oh seven
            w = e.number_to_words(tok, group=2, zero="oh")
        else:
            w = e.number_to_words(tok, andword="")
        return w.replace("-", " ").replace(",", "")
    except Exception:
        return tok


def norm_words(text, year=False):
    """Normalise a transcript for word error: lower case, digits spelled out,
    punctuation dropped (an apostrophe inside a word is kept)."""
    t = text.lower().replace("’", "'").replace("%", " percent ")
    t = re.sub(r"(\d),(\d)", r"\1\2", t)
    t = re.sub(r"\$(\d+)", r"\1 dollars", t)
    t = re.sub(r"\d+(st|nd|rd|th)\b", lambda m: m.group(0), t)
    t = re.sub(r"\d+", lambda m: " " + _num_words(m.group(0), year) + " ", t)
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    t = re.sub(r"(^|\s)'|'(\s|$)", " ", t)
    return t.split()


def align(ref, hyp):
    """Levenshtein alignment of word lists. Returns (S, D, I, pairs) where pairs
    maps ref index -> hyp index for matched or substituted words."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), np.int32)
    d[:, 0] = np.arange(n + 1)
    d[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        ri = ref[i - 1]
        row, prev = d[i], d[i - 1]
        for j in range(1, m + 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (ri != hyp[j - 1]))
    i, j, S, D, I, pairs = n, m, 0, 0, 0, {}
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i, j] == d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            if ref[i - 1] != hyp[j - 1]:
                S += 1
            pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif i > 0 and d[i, j] == d[i - 1, j] + 1:
            D += 1
            i -= 1
        else:
            I += 1
            j -= 1
    return S, D, I, pairs


def _best_norm(ref_text, hyp_text):
    """A number written as digits can be read two ways (two thousand seven,
    twenty oh seven); score the reading that matches what was heard."""
    best = None
    for ry in (False, True):
        for hy in (False, True):
            r, h = norm_words(ref_text, ry), norm_words(hyp_text, hy)
            S, D, I, _ = align(r, h)
            if best is None or S + D + I < sum(best[2:]):
                best = (r, h, S, D, I)
    return best


def wer(ref_text, hyp_text):
    r, h, S, D, I = _best_norm(ref_text, hyp_text)
    return {"wer": round((S + D + I) / max(len(r), 1), 4), "S": S, "D": D, "I": I, "ref_words": len(r)}


def wer_detail(ref_text, hyp_text):
    """The word errors themselves, for the report."""
    r, h = _best_norm(ref_text, hyp_text)[:2]
    import difflib
    out = []
    for op, a, b, c, d in difflib.SequenceMatcher(a=r, b=h, autojunk=False).get_opcodes():
        if op != "equal":
            out.append({"op": op, "script": " ".join(r[a:b]), "heard": " ".join(h[c:d])})
    return out


# ---------------------------------------------------------------- joins

def _frames_db(x, sr, win=0.010):
    n = int(win * sr)
    k = len(x) // n
    f = x[: k * n].reshape(k, n)
    return 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-12), n


def _logmel(x, sr, win=0.025, hop=0.010):
    import librosa
    m = librosa.feature.melspectrogram(y=x, sr=sr, n_fft=int(2 ** math.ceil(math.log2(win * sr))),
                                       win_length=int(win * sr), hop_length=int(hop * sr), n_mels=64,
                                       center=True, power=2.0)
    return np.log10(m + 1e-10)


def gap_at(x, sr, t, reach=0.8, below=35.0):
    """The silence nearest time t: the run of 10 ms frames more than `below` dB
    under the file's speech level (p90), within `reach` s. Returns (start, end)
    in seconds, or None when t sits inside continuous speech."""
    db, n = _frames_db(x, sr)
    thr = np.percentile(db, 90) - below
    quiet = db < thr
    c = int(t / 0.010)
    lo, hi = max(0, c - int(reach / 0.010)), min(len(db), c + int(reach / 0.010))
    best = None
    i = lo
    while i < hi:
        if quiet[i]:
            j = i
            while j < len(db) and quiet[j]:
                j += 1
            k = i
            while k > 0 and quiet[k - 1]:
                k -= 1
            d = 0 if k <= c < j else min(abs(c - k), abs(c - j))
            if best is None or (d, -(j - k)) < (best[0], -(best[2] - best[1])):
                best = (d, k, j)
            i = j
        else:
            i += 1
    if best is None or best[2] - best[1] < 5:
        return None
    return best[1] * 0.010, best[2] * 0.010


def join_metrics(x, sr, boundaries, natural=None, ctx_s=1.5):
    """Measure every chunk boundary (seconds) in the stitched signal and set it
    against the file's own frame-to-frame statistics and against the natural
    sentence gaps inside chunks (`natural`, seconds), which are the same kind
    of moment without a join.

    Per boundary:
      energy_jump_db   |dB step| between the 10 ms frames either side
      spec_flux        log-mel distance between the frames either side
      *_pct            where that value sits among every frame step in the file (0..100)
      sample_step      |x[t]-x[t-1]| over the file's p99.9 sample step (a click is >> 1)
      floor_step_db    noise-floor change: quietest 50 ms in the 400 ms before vs after
      level_step_db    speech loudness of 1.5 s before vs after (prosody/timbre drift)
      f0_step_st       median pitch 1.5 s before vs after
    The same level/f0 steps are measured at the natural gaps for comparison."""
    db, n = _frames_db(x, sr)
    steps = np.abs(np.diff(db))
    lm = _logmel(x, sr)
    flux = np.sqrt(((np.diff(lm, axis=1)) ** 2).mean(0))
    ds = np.abs(np.diff(x))
    click_ref = float(np.percentile(ds, 99.9)) + 1e-9

    def at(t):
        g0 = gap_at(x, sr, t)
        if g0:   # a join and a sentence gap are both measured at the middle of their silence
            t = (g0[0] + g0[1]) / 2
        i = int(round(t * sr / n))
        j = int(round(t / 0.010))
        out = {"t": round(t, 3)}
        if 0 < i < len(db):
            v = abs(db[i] - db[i - 1])
            out["energy_jump_db"] = round(float(v), 2)
            out["energy_jump_pct"] = round(float((steps < v).mean() * 100), 1)
        if 0 < j < lm.shape[1]:
            v = float(np.sqrt(((lm[:, j] - lm[:, j - 1]) ** 2).mean()))
            out["spec_flux"] = round(v, 3)
            out["spec_flux_pct"] = round(float((flux < v).mean() * 100), 1)
        s = int(t * sr)
        w = x[max(0, s - 64): s + 64]
        out["sample_step"] = round(float(np.abs(np.diff(w)).max() / click_ref), 2) if len(w) > 2 else 0.0

        def floor(a, b):
            seg = x[max(0, int(a * sr)): max(0, int(b * sr))]
            if len(seg) < int(0.03 * sr):
                return None
            d, _ = _frames_db(seg, sr, 0.01)
            return float(np.percentile(d, 20))   # the floor, not the tail of the last word
        # the noise floor inside the pause itself: its first half against its second
        g = gap_at(x, sr, t)
        fa = fb = None
        if g and g[1] - g[0] >= 0.1:
            m = (g[0] + g[1]) / 2
            fa, fb = floor(g[0], m), floor(m, g[1])
        out["gap"] = [round(g[0], 3), round(g[1], 3)] if g else None
        if fa is not None and fb is not None:
            # below FLOOR_DB nothing is audible in a mix at -14 LUFS; a step from
            # digital silence to -90 dBFS is not a seam, -62 to -75 is
            fa, fb = max(fa, FLOOR_DB), max(fb, FLOOR_DB)
            out["floor_db"] = [round(fa, 1), round(fb, 1)]
            out["floor_step_db"] = round(abs(fa - fb), 2)
        else:
            out["floor_step_db"] = None
        # the voice on each side, measured from the edges of the silence: a
        # window centred in a long pause held a few voiced frames and gave pyin's
        # octave errors as 20+ st "steps" (13 Nov), at joins and natural gaps alike
        ea, eb = (g[0], g[1]) if g else (t, t)
        la = x[max(0, int((ea - ctx_s) * sr)): int(ea * sr)]
        lb = x[int(eb * sr): int((eb + ctx_s) * sr)]
        if len(la) > sr // 2 and len(lb) > sr // 2:
            out["level_step_db"] = round(abs(_speech_db(la, sr) - _speech_db(lb, sr)), 2)
            pa, pb = _f0_med(la, sr), _f0_med(lb, sr)
            out["f0_step_st"] = round(abs(12 * math.log2(pa / pb)), 2) if pa and pb else None
        return out

    bnd = [dict(t_asked=round(t, 3), **at(t)) for t in boundaries]
    nat = [dict(t_asked=round(t, 3), **at(t)) for t in (natural or [])]

    def summ(rows, k):
        v = [r[k] for r in rows if r.get(k) is not None]
        return {"max": round(max(v), 2), "median": round(float(np.median(v)), 2)} if v else None

    keys = ("energy_jump_db", "energy_jump_pct", "spec_flux_pct", "sample_step", "floor_step_db",
            "level_step_db", "f0_step_st")
    return {"boundaries": bnd, "natural": nat,
            "summary": {k: {"joins": summ(bnd, k), "natural": summ(nat, k)} for k in keys}}


def _speech_db(x, sr):
    d, _ = _frames_db(x, sr, 0.02)
    d = d[d > d.max() - 30]
    return float(10 * np.log10((10 ** (d / 10)).mean())) if len(d) else -120.0


def _f0_med(x, sr):
    import librosa
    y = resample(x, sr, 16000) if sr != 16000 else x
    f0, v, p = librosa.pyin(y, fmin=60, fmax=520, sr=16000, frame_length=1024, hop_length=160)
    ok = v & np.isfinite(f0)
    return float(np.median(f0[ok])) if ok.sum() > 10 else None


def voice_profile(x, sr, words):
    """Everything the VOICE gate compares against the reference band."""
    return {"pace": pace(words), "pitch": pitch(x, sr),
            "loudness": {"lufs": round(lufs(x, sr), 2), "lra": round(lra(x, sr), 2)}}
