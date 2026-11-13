"""The narration's mastering chain, solved against the reference narrators by signal.

  y, report = master(x, sr, band="03")        # x: the raw voice, any rate; y: 48 kHz, -14 LUFS, <= -1 dBTP

Stages (each one measured, none guessed from words):
  1. high-pass      80 Hz, 24 dB/oct: rumble and plosive thumps out
  2. tonal EQ       the voice's long-term spectrum (250 Hz..8 kHz, third octaves) brought toward the
                    reference narrators' median shape, at most +-6 dB, linear phase
  3. compressor     fast (8 ms / 90 ms, 3:1, soft knee): the threshold is solved so the voice's crest
                    factor and 20 ms dynamics sit on the references' median
  4. leveler        slow (40 ms / 450 ms, 2:1): threshold solved for the references' 400 ms loudness spread
  4b. stress ride   the writer's emphasis words ridden 0..3 dB, solved after the compressors for the
                    references' emphasis (loudest word 4.9 dB over its phrase median, word spread 4.7 dB;
                    13 Nov: OpenAI voices read 2.6-3.3 dB, VoxCPM2 3.9)
  5. presence       a 3.2 kHz bell, -3..+4 dB, solved for the references' 2-5 kHz presence
  6. de-ess         split band at 4.5 kHz, 5 ms detector: threshold solved for the references' sibilance
  7. loudness       -14 LUFS integrated (BS.1770), true peak under -1 dBTP (4x oversampled gain ride)
The targets are the medians in voice_ref.json (htdemucs stems of the narrated reference films,
.valmera/week-1112/lanes/VOICE/tools/ref_study.py). Same chain for every engine.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REF = HERE / "voice_ref.json"
SR = 48000
LUFS = -14.0
RIDE_DB = 3.0           # the most the stress ride may add
CENTS = np.geomspace(250, 8000, 16)


def _db(p):
    return 10 * np.log10(np.maximum(p, 1e-14))


def targets(band="03"):
    b = json.load(open(REF))["band"].get(band) or json.load(open(REF))["band"]["03"]
    return b


def resample(x, sr, target):
    from scipy.signal import resample_poly
    g = math.gcd(int(sr), int(target))
    return resample_poly(x, int(target) // g, int(sr) // g).astype(np.float32)


# ------------------------------------------------------------------ measurements the chain steers by

def _stft_speech(x, sr):
    from scipy.signal import stft
    nfft = 4096 if sr > 24000 else 2048
    f, _, Z = stft(x, fs=sr, nperseg=nfft, noverlap=nfft * 3 // 4, boundary=None, padded=False)
    S = np.abs(Z) ** 2
    fe = _db(S.sum(0))
    sp = fe > np.percentile(fe, 95) - 25
    return f, S[:, sp]


def ltas(x, sr):
    f, S = _stft_speech(x, sr)
    L = S.mean(1)
    return np.array([10 * np.log10(np.mean(L[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))]) + 1e-20) for c in CENTS])


def presence_db(x, sr):
    f, S = _stft_speech(x, sr)
    L = S.mean(1)
    return float(_db(L[(f >= 2000) & (f < 5000)].sum()) - _db(L[(f >= 200) & (f < 2000)].sum()))


def sib_db(x, sr):
    f, S = _stft_speech(x, sr)
    hf = S[(f >= 5000) & (f < min(10000, sr / 2))].sum(0)
    tot = S.sum(0)
    r = hf / (tot + 1e-20)
    top = r >= np.percentile(r, 95)
    return float(_db(hf[top].mean()) - _db(np.median(tot)))


def _frames_db(x, sr, win):
    n = int(win * sr)
    k = len(x) // n
    return _db((x[: k * n].reshape(k, n).astype(np.float64) ** 2).mean(1))


def speech_level(x, sr):
    d = _frames_db(x, sr, 0.01)
    d = d[d > np.percentile(d, 95) - 25]
    return float(_db((10 ** (d / 10)).mean()))


def crest_db(x, sr):
    lvl = np.percentile(_frames_db(x, sr, 0.01), 95)
    out = []
    for a in range(0, len(x) - sr, sr // 2):
        s = x[a:a + sr].astype(np.float64)
        r = math.sqrt((s ** 2).mean())
        if r > 10 ** ((lvl - 20) / 20):
            out.append(20 * math.log10(np.abs(s).max() / r))
    return float(np.median(out)) if out else 0.0


def frame_dyn_db(x, sr):
    d = _frames_db(x, sr, 0.02)
    d = d[d > np.percentile(d, 95) - 40]
    return float(np.percentile(d, 95) - np.percentile(d, 10))


def _k_weight(x, sr):
    from scipy.signal import lfilter
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh, Vb = 10 ** (G / 20), 10 ** (G / 20) ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a0 = 1 + K / Q + K * K
    return lfilter([1, -2, 1], [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0], lfilter(b1, a1, x))


def _blocks(x, sr, win, hop):
    y = _k_weight(np.asarray(x, np.float64), sr) ** 2
    n, h = int(win * sr), int(hop * sr)
    if len(y) < n:
        return np.array([max(y.mean(), 1e-12)])
    c = np.concatenate([[0.0], np.cumsum(y)])
    s = np.arange(0, len(y) - n + 1, h)
    return (c[s + n] - c[s]) / n


def lufs(x, sr):
    p = _blocks(x, sr, 0.4, 0.1)
    l = -0.691 + 10 * np.log10(p + 1e-12)
    p = p[l > -70]
    if not len(p):
        return -70.0
    rel = -0.691 + 10 * np.log10(p.mean()) - 10
    l = -0.691 + 10 * np.log10(p + 1e-12)
    p = p[l > rel]
    return float(-0.691 + 10 * np.log10(p.mean() + 1e-12)) if len(p) else -70.0


def st_loud_std(x, sr):
    p = _blocks(x, sr, 0.4, 0.1)
    l = -0.691 + 10 * np.log10(p + 1e-12)
    l = l[l > lufs(x, sr) - 20]
    return float(np.std(l)) if len(l) > 3 else 0.0


# ------------------------------------------------------------------ stages

def highpass(x, sr, f=80.0):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, f, "hp", fs=sr, output="sos"), x).astype(np.float32)


def tonal_eq(x, sr, target_ltas, strength=0.85, max_db=6.0):
    """Linear-phase EQ bringing the long-term spectrum's SHAPE (level removed) toward the target."""
    from scipy.signal import fftconvolve, firwin2
    cur = ltas(x, sr)
    d = np.asarray(target_ltas) - cur
    d -= d.mean()
    d = np.convolve(np.pad(d, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    d = np.clip(d * strength, -max_db, max_db)
    freq = [0.0, 150.0] + list(CENTS) + [10000.0, 14000.0, sr / 2]
    gain_db = [0.0, 0.0] + list(d) + [d[-1] * 0.7, d[-1] * 0.3, 0.0]
    taps = 2047 if sr >= 44100 else 1023
    h = firwin2(taps, freq, 10 ** (np.array(gain_db) / 20), fs=sr)
    return fftconvolve(x, h, mode="same").astype(np.float32), [round(float(v), 2) for v in d]


def _gain_curve(lvl_db, thr, ratio, knee):
    over = lvl_db - thr
    gr = np.where(over <= -knee / 2, 0.0,
                  np.where(over >= knee / 2, over * (1 - 1 / ratio),
                           (1 - 1 / ratio) * (over + knee / 2) ** 2 / (2 * knee)))
    return gr


def _smooth(gr, att, rel):
    out = np.empty_like(gr)
    g = 0.0
    for i, v in enumerate(gr):
        g = att * g + (1 - att) * v if v > g else rel * g + (1 - rel) * v
        out[i] = g
    return out


def compress(x, sr, thr_db, ratio=3.0, attack_ms=8.0, release_ms=90.0, knee_db=6.0, det_ms=5.0, block_ms=1.0):
    """Feed-forward RMS compressor on 1 ms blocks; gain interpolated to samples. Returns (y, mean gain reduction dB)."""
    n = max(1, int(block_ms * sr / 1000))
    k = int(math.ceil(len(x) / n))
    xp = np.pad(x.astype(np.float64), (0, k * n - len(x)))
    p = (xp.reshape(k, n) ** 2).mean(1)
    a = math.exp(-block_ms / det_ms)
    from scipy.signal import lfilter
    p = lfilter([1 - a], [1, -a], p)
    gr = _gain_curve(_db(p), thr_db, ratio, knee_db)
    gr = _smooth(gr, math.exp(-block_ms / attack_ms), math.exp(-block_ms / release_ms))
    t = (np.arange(k) + 0.5) * n
    g = np.interp(np.arange(len(x)), t, gr)
    return (x * 10 ** (-g / 20)).astype(np.float32), float(gr[p > 10 ** ((thr_db - 30) / 10)].mean()) if (p > 0).any() else 0.0


def peaking(x, sr, f0, gain_db, q=0.9):
    from scipy.signal import lfilter
    A = 10 ** (gain_db / 40)
    w = 2 * math.pi * f0 / sr
    al = math.sin(w) / (2 * q)
    b = [1 + al * A, -2 * math.cos(w), 1 - al * A]
    a = [1 + al / A, -2 * math.cos(w), 1 - al / A]
    return lfilter(np.array(b) / a[0], np.array(a) / a[0], x).astype(np.float32)


def deess(x, sr, thr_db, split=4500.0, max_red=10.0, ratio=4.0):
    """Split band: the band above `split` is turned down while its own 5 ms level is over thr_db (dBFS)."""
    from scipy.signal import butter, sosfiltfilt
    hi = sosfiltfilt(butter(4, split, "hp", fs=sr, output="sos"), x)
    lo = x - hi
    n = int(0.001 * sr)
    k = int(math.ceil(len(x) / n))
    hp = np.pad(hi.astype(np.float64), (0, k * n - len(x)))
    p = (hp.reshape(k, n) ** 2).mean(1)
    from scipy.signal import lfilter
    a = math.exp(-1 / 5.0)
    p = lfilter([1 - a], [1, -a], p)
    gr = np.clip((_db(p) - thr_db) * (1 - 1 / ratio), 0, max_red)
    gr = _smooth(gr, math.exp(-1 / 1.0), math.exp(-1 / 40.0))
    g = np.interp(np.arange(len(x)), (np.arange(k) + 0.5) * n, gr)
    return (lo + hi * 10 ** (-g / 20)).astype(np.float32)


def loudness_set(x, sr, target=-14.0, tp_db=-1.0):
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    from scipy.signal import resample_poly
    y = x * 10 ** ((target - lufs(x, sr)) / 20)
    lim = 10 ** (tp_db / 20)
    for _ in range(3):
        over = np.abs(resample_poly(y, 4, 1)).reshape(-1, 4).max(1)[: len(y)]
        if over.max() <= lim:
            break
        need = np.minimum(1.0, lim / np.maximum(over, 1e-9))
        w = int(0.005 * sr)
        env = minimum_filter1d(need, size=2 * w + 1)
        env = uniform_filter1d(env, size=w)
        env = np.minimum(env, minimum_filter1d(need, size=3))
        y = y * env
        y = y * 10 ** ((target - lufs(y, sr)) / 20) if abs(lufs(y, sr) - target) > 0.3 else y
    return y.astype(np.float32)


# ------------------------------------------------------------------ the chain

def rides(x, sr, spans, ramp=0.03):
    """A fader ride: +gain dB over each (start, end, gain_db) span, 30 ms ramps."""
    if not spans:
        return x
    g = np.zeros(len(x), np.float32)
    r = int(ramp * sr)
    for a, b, db in spans:
        i, j = max(0, int(a * sr) - r), min(len(x), int(b * sr) + r)
        if j <= i:
            continue
        env = np.ones(j - i, np.float32)
        k = min(r, (j - i) // 2)
        if k:
            env[:k] = np.linspace(0, 1, k)
            env[-k:] = np.linspace(1, 0, k)
        g[i:j] = np.maximum(g[i:j], env * db)
    return (x * 10 ** (g / 20)).astype(np.float32)


def word_stats(x, sr, words, gap=0.30):
    """Emphasis as the references were measured: per phrase (4+ words, split at 0.3 s gaps) the loudest
    word over the phrase median (dB) and the spread of word levels; medians over phrases."""
    ph, cur = [], []
    for w in words:
        if cur and w["start"] - cur[-1]["end"] >= gap:
            ph.append(cur)
            cur = []
        cur.append(w)
    if cur:
        ph.append(cur)
    top, spread = [], []
    for p in ph:
        if len(p) < 4:
            continue
        lv = []
        for w in p:
            seg = x[int(w["start"] * sr):int(w["end"] * sr)].astype(np.float64)
            if len(seg) > 0.03 * sr:
                lv.append(float(_db((seg ** 2).mean())))
        if len(lv) >= 4:
            top.append(max(lv) - float(np.median(lv)))
            spread.append(float(np.std(lv)))
    return (float(np.median(top)) if top else None, float(np.median(spread)) if spread else None)


def master(x, sr, band="03", lufs_target=-14.0, ref=None, ride=None, words=None):
    """ride: [(start_s, end_s, _)] the writer's stressed words; the gain is solved after the compressors
    (0..3 dB) so the read's emphasis (loudest word over its phrase, word-level spread) sits nearest the
    references' medians. words: every script word's start/end in x's time (for that measurement)."""
    t = ref or targets(band)
    x = np.asarray(x, np.float32)
    if sr != SR:
        x, sr = resample(x, sr, SR), SR
    rep = {"band": band, "in": {"crest_db": round(crest_db(x, sr), 2), "frame_dyn_db": round(frame_dyn_db(x, sr), 2),
                                "st_loud_std_db": round(st_loud_std(x, sr), 2), "presence_db": round(presence_db(x, sr), 2),
                                "sib_db": round(sib_db(x, sr), 2)}}
    x = x / (np.abs(x).max() + 1e-9) * 0.5
    y = highpass(x, sr)
    if "ltas_db" in t:
        y, eq = tonal_eq(y, sr, t["ltas_db"]["median"])
        rep["tonal_eq_db"] = eq
    lvl = speech_level(y, sr)
    # compressor: threshold below the speech level, solved for crest and 20 ms dynamics
    want_c, want_f = t["crest_db"]["median"], t["frame_dyn_db"]["median"]
    sc_c = max((t["crest_db"]["p90"] - t["crest_db"]["p10"]) / 2.563, 0.3)
    sc_f = max((t["frame_dyn_db"]["p90"] - t["frame_dyn_db"]["p10"]) / 2.563, 0.5)
    best = None
    for off in (99, -3, -6, -9, -12, -15, -18, -21):
        z = y if off == 99 else compress(y, sr, lvl + off)[0]
        c, f = crest_db(z, sr), frame_dyn_db(z, sr)
        cost = ((c - want_c) / sc_c) ** 2 + ((f - want_f) / sc_f) ** 2
        if best is None or cost < best[0]:
            best = (cost, off, z, c, f)
    _, off, y, c, f = best
    rep["compressor"] = {"threshold_below_speech_db": None if off == 99 else -off, "ratio": 3.0, "crest_db": round(c, 2),
                         "frame_dyn_db": round(f, 2)}
    # leveler: the 400 ms loudness spread
    want_s = t["st_loud_std_db"]["median"]
    lvl = speech_level(y, sr)
    best = None
    for off in (99, -2, -4, -6, -8, -10):
        z = y if off == 99 else compress(y, sr, lvl + off, ratio=2.0, attack_ms=40, release_ms=450, det_ms=50, knee_db=8)[0]
        s = st_loud_std(z, sr)
        if best is None or abs(s - want_s) < best[0]:
            best = (abs(s - want_s), off, z, s)
    _, off, y, s = best
    rep["leveler"] = {"threshold_below_speech_db": None if off == 99 else -off, "ratio": 2.0, "st_loud_std_db": round(s, 2)}
    if ride and words:
        best = None
        for g in (0.0, RIDE_DB / 3, 2 * RIDE_DB / 3, RIDE_DB):
            z = rides(y, sr, [(a, b, g) for a, b, _ in ride]) if g else y
            e, sp = word_stats(z, sr, words)
            if e is None:
                break
            cost = sum(((v - t[k]["median"]) / max((t[k]["p90"] - t[k]["p10"]) / 2.563, 0.2)) ** 2
                       for v, k in ((e, "emph_db"), (sp, "word_db_std")) if k in t)
            if best is None or cost < best[0]:
                best = (cost, g, z, e, sp)
        if best:
            _, g, y, e, sp = best
            rep["ride"] = {"words": len(ride), "gain_db": g, "emph_db": round(e, 2), "word_db_std": round(sp, 2)}
    # presence: a 3.2 kHz bell for the references' 2-5 kHz balance
    want_p = t["presence_db"]["median"]
    best = None
    for g in (-3, -2, -1, 0, 1, 2, 3, 4):
        z = y if g == 0 else peaking(y, sr, 3200, g)
        p = presence_db(z, sr)
        if best is None or abs(p - want_p) < best[0]:
            best = (abs(p - want_p), g, z, p)
    _, g, y, p = best
    rep["presence"] = {"gain_db": g, "presence_db": round(p, 2)}
    # de-ess: threshold on the high band's own level, solved for the references' sibilance
    want_sib = t["sib_db"]["median"]
    s0 = sib_db(y, sr)
    rep["deess"] = {"sib_db_before": round(s0, 2), "threshold_db": None}
    if s0 > want_sib + 0.5:
        from scipy.signal import butter, sosfiltfilt
        hi = sosfiltfilt(butter(4, 4500.0, "hp", fs=sr, output="sos"), y)
        top = float(np.percentile(_frames_db(hi, sr, 0.005), 99))
        best = None
        for d in (3, 6, 9, 12, 15, 18):
            z = deess(y, sr, top - d)
            v = sib_db(z, sr)
            if best is None or abs(v - want_sib) < best[0]:
                best = (abs(v - want_sib), d, z, v)
            if v <= want_sib:
                break
        _, d, y, v = best
        rep["deess"].update(threshold_db=round(top - d, 1), sib_db=round(v, 2))
    y = loudness_set(y, sr, lufs_target)
    rep["out"] = {"lufs": round(lufs(y, sr), 2), "crest_db": round(crest_db(y, sr), 2), "presence_db": round(presence_db(y, sr), 2),
                  "sib_db": round(sib_db(y, sr), 2), "st_loud_std_db": round(st_loud_std(y, sr), 2)}
    return y, rep
