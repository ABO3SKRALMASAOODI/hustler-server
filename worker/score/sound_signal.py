"""A sound read as a signal (sound engine lane, 13 Nov): one description for a reference's sound, a library
recording and an audition candidate, so the three can be laid on one ruler.

    describe(x, sr, bg=None, at=None) -> {dur_s, attack_s, decay_s, peak_s, hit_s, bands, flat, low, crest, sweep, kind, k200}
    vector(d) -> np.ndarray          the description as a point (time shape, band shape, noise, sweep)
    distance(a, b, scale=None)       between two descriptions
    kind_of(d)                       click | hit | impact | whoosh | riser | tone, from the shape alone

numpy only. Nothing here knows a file name, a title or a tag: a recording is what its samples say it is.
The reference side of the ruler is score/sound_profiles.py, measured by the lane's study on Demucs stems.
"""
import math

import numpy as np

SR = 48000
NFFT = 1024
HOP = 240                                   # 5 ms
BANDS = ((40, 120), (120, 300), (300, 800), (800, 2000), (2000, 4000), (4000, 8000), (8000, 14000))
BODY_DB = 20.0                              # a sound's body: within 20 dB of its peak
FLOOR_UP_DB = 6.0                           # and at least 6 dB over what lies around it


def _kweight(hz, sr=SR):
    """BS.1770 K-weighting as a power gain per frequency (the two biquads' magnitude response at 48 kHz)."""
    w = 2 * math.pi * np.asarray(hz, np.float64) / 48000.0
    z = np.exp(-1j * w)

    def resp(b, a):
        return (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    shelf = resp((1.53512485958697, -2.69169618940638, 1.19839281085285), (1.0, -1.69065929318241, 0.73248077421585))
    hp = resp((1.0, -2.0, 1.0), (1.0, -1.99004745483398, 0.99007225036621))
    return (np.abs(shelf * hp) ** 2).astype(np.float32)


_HZ = np.fft.rfftfreq(NFFT, 1.0 / SR)
_KW = _kweight(_HZ)
_WIN = np.hanning(NFFT).astype(np.float32)
_WIN_POW = float((_WIN ** 2).sum())
_BAND_MASK = [(_HZ >= a) & (_HZ < b) for a, b in BANDS]
_ALL = (_HZ >= 40) & (_HZ < 14000)
_NOISE = (_HZ >= 300) & (_HZ < 12000)
_LOW = (_HZ >= 30) & (_HZ < 150)


def power(x):
    """Power spectrogram (frames x bins) of mono samples at SR, frame k centred on k * HOP. A frame's bins sum to
    its mean square, so levels read in dBFS."""
    x = np.asarray(x, np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    pad = NFFT // 2
    x = np.concatenate([np.zeros(pad, np.float32), x, np.zeros(pad, np.float32)])
    n = 1 + max(0, len(x) - NFFT) // HOP
    out = np.empty((n, NFFT // 2 + 1), np.float32)
    for s in range(0, n, 2048):
        idx = np.arange(s, min(n, s + 2048))
        fr = np.stack([x[i * HOP:i * HOP + NFFT] for i in idx]) * _WIN
        out[idx] = (np.abs(np.fft.rfft(fr, axis=1)) ** 2 * (2.0 / (_WIN_POW * NFFT))).astype(np.float32)
    return out


def _db(p):
    return 10.0 * np.log10(np.maximum(p, 1e-12))


def k_level(P):
    """K-weighted level per frame, dB (LUFS-like: a full-scale 1 kHz sine reads -3)."""
    return _db((P * _KW).sum(axis=1)) - 0.691


def describe(x, sr=SR, bg=None, at=None, search_s=0.2):
    """The shape of the one sound in x (mono, at SR). bg: samples of what lay under it (a stem's bed just before
    it); its mean spectrum is taken off, so a sound read out of a film is read without the music it sat on.
    at: where its peak is expected (s); the loudest frame within search_s of it is the peak, else the loudest."""
    if sr != SR:
        raise ValueError("describe wants 48 kHz")
    P = power(x)
    if len(P) < 4:
        return None
    if bg is not None and len(bg) >= NFFT:
        Pb = power(bg)
        P = np.maximum(P - np.median(Pb, axis=0) * 1.5, 0.0)
    e = (P[:, _ALL]).sum(axis=1)
    k = min(3, len(e))
    es = np.convolve(e, np.ones(k, np.float32) / k, mode="same")
    edb = _db(es)
    if at is not None:
        a, b = max(0, int((at - search_s) * SR / HOP)), min(len(edb), int((at + search_s) * SR / HOP) + 1)
        pk = a + int(np.argmax(edb[a:b])) if b > a else int(np.argmax(edb))
    else:
        pk = int(np.argmax(edb))
    top = float(edb[pk])
    if top < -95:
        return None
    floor = float(np.percentile(edb, 10))
    thr = max(top - BODY_DB, min(floor + FLOOR_UP_DB, top - 6.0))
    gap = 4                                                     # 20 ms under the line does not end a sound
    l = pk
    miss = 0
    for i in range(pk - 1, -1, -1):
        if edb[i] > thr:
            l, miss = i, 0
        else:
            miss += 1
            if miss > gap:
                break
    r = pk
    miss = 0
    for i in range(pk + 1, len(edb)):
        if edb[i] > thr:
            r, miss = i, 0
        else:
            miss += 1
            if miss > gap:
                break
    hit = l
    for i in range(l, pk + 1):
        if edb[i] >= top - 6.0:
            hit = i
            break
    body = P[l:r + 1]
    tot = float(body[:, _ALL].sum()) + 1e-12
    bands = [round(float(_db(body[:, m].sum() / tot)), 1) for m in _BAND_MASK]
    spec = body[:, _NOISE].mean(axis=0) + 1e-12
    flat = float(np.exp(np.mean(np.log(spec))) / spec.mean())
    crest = float(_db(spec.max() / spec.mean()))
    low = float(body[:, _LOW].sum() / tot)

    def cen(seg):
        s = seg[:, _ALL].sum(axis=0) + 1e-12
        return float((s * _HZ[_ALL]).sum() / s.sum())
    n = r - l + 1
    third = max(1, n // 3)
    c0, c1 = cen(body[:third]), cen(body[-third:])
    sweep = math.log2(max(c1, 20.0) / max(c0, 20.0)) if n >= 6 else 0.0
    kl = k_level(P)
    w = 40                                                      # the loudest 200 ms, K-weighted
    if len(kl) > w:
        run = np.convolve(10 ** (kl / 10), np.ones(w) / w, mode="valid")
        a0, a1 = max(0, pk - w), min(len(run), pk + 1)
        k200 = float(_db(run[a0:a1].max())) if a1 > a0 else float(_db(run.max()))
    else:
        k200 = float(_db((10 ** (kl / 10)).mean()))
    d = {"dur_s": round(n * HOP / SR, 3), "attack_s": round((pk - l) * HOP / SR, 3), "decay_s": round((r - pk) * HOP / SR, 3),
         "onset_s": round(l * HOP / SR, 3), "peak_s": round(pk * HOP / SR, 3), "hit_s": round(hit * HOP / SR, 3),
         "bands": bands, "flat": round(flat, 3), "crest": round(crest, 1), "low": round(low, 3),
         "centroid_hz": int(cen(body)), "sweep": round(sweep, 2), "k200": round(k200, 1)}
    d["kind"] = kind_of(d)
    return d


def kind_of(d):
    """The class a shape belongs to. Thresholds are the atlas's (8.5): a click is under 90 ms, a riser climbs for
    most of its length, an impact carries its weight under 150 Hz and lands (its decay outlasts its attack), a
    whoosh is noise that swells, a tone rings."""
    dur, att, dec = d["dur_s"], d["attack_s"], d["decay_s"]
    if att >= 0.45 and att >= 2.0 * dec:
        return "riser"
    if d["low"] >= 0.3 and dur >= 0.1 and dec >= 1.5 * att:
        return "impact"                            # weight that lands and rings; weight that swells is a deep whoosh
    if dur <= 0.09:
        return "click"
    if d["crest"] >= 20.0 and d["flat"] < 0.05 and dec >= 0.2 and att <= 0.04:
        return "tone"                              # struck, then it rings
    if (d["flat"] >= 0.08 and dur >= 0.15) or att >= 0.06:
        return "whoosh"
    return "hit"


def vector(d):
    """A description as a point: time shape (log), band shape (dB, floored at -36), noise, sweep."""
    b = [max(-36.0, min(0.0, v)) / 6.0 for v in d["bands"]]
    return np.array([math.log2(max(d["dur_s"], 0.02) / 0.2), math.log2((d["attack_s"] + 0.01) / 0.05),
                     math.log2((d["decay_s"] + 0.01) / 0.1), *b, min(d["flat"], 0.6) * 5.0, max(-2.0, min(2.0, d["sweep"]))],
                    np.float32)


# time shape counts most (a click is not a whoosh), then where the energy sits, then noise against tone
WEIGHT = np.array([1.5, 1.2, 1.0, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 1.0, 0.5], np.float32)


def distance(a, b, scale=None):
    va = vector(a) if isinstance(a, dict) else np.asarray(a, np.float32)
    vb = vector(b) if isinstance(b, dict) else np.asarray(b, np.float32)
    s = np.asarray(scale, np.float32) if scale is not None else 1.0
    return float(np.sqrt(np.mean((WEIGHT * (va - vb) / s) ** 2)))
