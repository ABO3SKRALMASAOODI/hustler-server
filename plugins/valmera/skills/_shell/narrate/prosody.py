"""Prosody and voice features: one function measures the reference narrators (demucs vocal stems,
voice_ref.json) and every narration or take the same way (13 Nov voice study, lane VO; moved into the
tool by lane VO2 so takes are chosen by these signals).

measure(x, sr, words) -> dict of features
  pitch     f0_med_hz, range_st (p5..p95), std_st, vel_st_s (median |df0/dt| inside voiced runs),
            swing_st (median per-phrase p5..p95), decl_st_s (median per-phrase slope),
            final_st (last fifth of a phrase against its median: the cadence)
  rate      wpm, wpm_artic, phrase_wpm_cv (rate change between phrases)
  pauses    pauses_pm, pause_med_s, pause_p90_s
  emphasis  emph_db / emph_st (per phrase: loudest / highest word above the phrase median),
            prom_per_10s (words +3 dB and +2 st above their phrase), word_db_std
  timbre    tilt_db_oct (LTAS slope 250 Hz..8 kHz), alpha_db (1-5 kHz vs 50 Hz-1 kHz),
            presence_db (2-5 kHz vs 200 Hz-2 kHz), mud_db (200-500 Hz vs 50 Hz-5 kHz), centroid_hz,
            sib_db (5-10 kHz in the sibilant frames against the voice)
  room      decay_ms (time for the voice to fall 15 dB from its last peak at a phrase end), floor_db (pause core vs speech)
  dynamics  crest_db, st_loud_std_db (400 ms loudness spread), frame_dyn_db (20 ms p95-p10), lra
  loudness  lufs
No listening model: signal processing and the given word timings only.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

NARRATE = Path(__file__).resolve().parent
if str(NARRATE) not in sys.path:
    sys.path.insert(0, str(NARRATE))
import measure  # noqa: E402

PAUSE = 0.30


def _db(p):
    return 10 * np.log10(np.maximum(p, 1e-14))


def frames_db(x, sr, win=0.01):
    n = int(win * sr)
    k = len(x) // n
    f = x[: k * n].reshape(k, n).astype(np.float64)
    return _db((f ** 2).mean(1))


_F0_LAST = {}


def pitch_track(x, sr):
    """f0 every 10 ms (pyin, 60-520 Hz). The last track is kept: a take is measured three ways (its prosody, the
    writer's stressed words, its register) and pyin is the slow part."""
    key = (x.__array_interface__["data"][0] if hasattr(x, "__array_interface__") else id(x), len(x), sr,
           float(np.asarray(x[::997], np.float64).sum()))
    if key in _F0_LAST:
        return _F0_LAST[key]
    _F0_LAST.clear()
    _F0_LAST[key] = f0 = _pitch_track(x, sr)
    return f0


def _pitch_track(x, sr):
    import librosa
    y = measure.resample(x, sr, 16000) if sr != 16000 else x
    f0, v, _ = librosa.pyin(y, fmin=60, fmax=520, sr=16000, frame_length=1024, hop_length=160, fill_na=np.nan)
    f0 = np.where(v & np.isfinite(f0), f0, np.nan)
    return f0  # 10 ms hop


def phrases_of(words):
    ph, cur = [], []
    for w in words:
        if cur and w["start"] - cur[-1]["end"] >= PAUSE:
            ph.append(cur)
            cur = []
        cur.append(w)
    if cur:
        ph.append(cur)
    return ph


def measure_voice(x, sr, words):
    x = np.asarray(x, np.float32)
    words = [w for w in words if w["end"] > w["start"]]
    out = {}
    # ------------------------------------------------ pitch
    f0 = pitch_track(x, sr)
    ok = np.isfinite(f0)
    med = float(np.nanmedian(f0)) if ok.sum() > 50 else None
    if med:
        st = 12 * np.log2(f0 / med)
        st[np.abs(st) > 12] = np.nan
        v = st[np.isfinite(st)]
        out["f0_med_hz"] = round(med, 1)
        out["range_st"] = round(float(np.percentile(v, 95) - np.percentile(v, 5)), 2)
        out["std_st"] = round(float(np.std(v)), 2)
        # velocity inside voiced runs of 50 ms or more, after a 5-frame median
        from scipy.ndimage import median_filter
        vel = []
        i = 0
        fin = np.isfinite(st)
        while i < len(st):
            if fin[i]:
                j = i
                while j < len(st) and fin[j]:
                    j += 1
                if j - i >= 9:
                    from scipy.signal import savgol_filter
                    s = savgol_filter(median_filter(st[i:j], size=5, mode="nearest"), 9, 2)
                    vel += list(np.abs(np.diff(s)) / 0.01)
                i = j
            else:
                i += 1
        out["vel_st_s"] = round(float(np.median(vel)), 2) if vel else None
        sw, dc, fn = [], [], []
        for p in phrases_of(words):
            a, b = int(p[0]["start"] / 0.01), int(p[-1]["end"] / 0.01)
            seg = st[a:b]
            t = np.arange(len(seg)) * 0.01
            m = np.isfinite(seg)
            if m.sum() < 30:
                continue
            sv = seg[m]
            sw.append(np.percentile(sv, 95) - np.percentile(sv, 5))
            dc.append(np.polyfit(t[m], sv, 1)[0])
            last = seg[int(len(seg) * 0.8):]
            if np.isfinite(last).sum() >= 5:
                fn.append(np.nanmedian(last) - np.median(sv))
        out["swing_st"] = round(float(np.median(sw)), 2) if sw else None
        out["decl_st_s"] = round(float(np.median(dc)), 2) if dc else None
        out["final_st"] = round(float(np.median(fn)), 2) if fn else None
    # ------------------------------------------------ rate and pauses
    pc = measure.pace(words)
    out["wpm"], out["wpm_artic"], out["pauses_pm"], out["pause_med_s"] = pc["wpm"], pc["wpm_artic"], pc["pauses_pm"], pc["pause_med_s"]
    gaps = [b["start"] - a["end"] for a, b in zip(words, words[1:]) if b["start"] - a["end"] >= PAUSE]
    out["pause_p90_s"] = round(float(np.percentile(gaps, 90)), 2) if gaps else 0.0
    rates = [60 * len(p) / (p[-1]["end"] - p[0]["start"]) for p in phrases_of(words)
             if len(p) >= 4 and p[-1]["end"] - p[0]["start"] > 0.5]
    out["phrase_wpm_cv"] = round(float(np.std(rates) / np.mean(rates)), 3) if len(rates) >= 3 else None
    # ------------------------------------------------ emphasis
    edb, est, wstd, prom = [], [], [], 0
    for p in phrases_of(words):
        if len(p) < 4:
            continue
        lv, pk = [], []
        for w in p:
            a, b = int(w["start"] * sr), int(w["end"] * sr)
            seg = x[a:b].astype(np.float64)
            lv.append(float(_db((seg ** 2).mean())) if len(seg) > sr * 0.03 else np.nan)
            if med:
                s = st[int(w["start"] / 0.01): int(w["end"] / 0.01)]
                pk.append(float(np.nanmax(s)) if np.isfinite(s).sum() >= 3 else np.nan)
        lv = np.array(lv)
        if np.isfinite(lv).sum() >= 4:
            m = np.nanmedian(lv)
            edb.append(np.nanmax(lv) - m)
            wstd.append(np.nanstd(lv))
            if pk:
                pk = np.array(pk)
                if np.isfinite(pk).sum() >= 4:
                    pm = np.nanmedian(pk)
                    est.append(np.nanmax(pk) - pm)
                    prom += int(np.sum((lv >= m + 3) & (pk >= pm + 2)))
    span = (words[-1]["end"] - words[0]["start"]) if words else 1
    out["emph_db"] = round(float(np.median(edb)), 2) if edb else None
    out["emph_st"] = round(float(np.median(est)), 2) if est else None
    out["word_db_std"] = round(float(np.median(wstd)), 2) if wstd else None
    out["prom_per_10s"] = round(10 * prom / max(span, 1), 2)
    # ------------------------------------------------ timbre: long-term spectrum of the speech frames
    import librosa
    nfft = 2048 if sr <= 24000 else 4096
    S = np.abs(librosa.stft(x, n_fft=nfft, hop_length=nfft // 4, center=True)) ** 2
    fr = librosa.fft_frequencies(sr=sr, n_fft=nfft)
    fe = _db(S.sum(0))
    sp = fe > np.percentile(fe, 95) - 25
    L = S[:, sp].mean(1)

    def band(lo, hi):
        m = (fr >= lo) & (fr < min(hi, sr / 2))
        return L[m].sum()
    cents = np.geomspace(250, 8000, 16)
    lev = [10 * np.log10(np.mean(L[(fr >= c / 2 ** (1 / 6)) & (fr < c * 2 ** (1 / 6))]) + 1e-20) for c in cents]
    out["tilt_db_oct"] = round(float(np.polyfit(np.log2(cents), lev, 1)[0]), 2)
    out["alpha_db"] = round(float(_db(band(1000, 5000)) - _db(band(50, 1000))), 2)
    out["presence_db"] = round(float(_db(band(2000, 5000)) - _db(band(200, 2000))), 2)
    out["mud_db"] = round(float(_db(band(200, 500)) - _db(band(50, 5000))), 2)
    cen = (fr[:, None] * S[:, sp]).sum(0) / (S[:, sp].sum(0) + 1e-20)
    out["centroid_hz"] = round(float(np.median(cen)), 0)
    hf = S[(fr >= 5000) & (fr < min(10000, sr / 2)), :][:, sp].sum(0)
    tot = S[:, sp].sum(0)
    ratio = hf / (tot + 1e-20)
    top = ratio >= np.percentile(ratio, 95)
    out["sib_db"] = round(float(_db(hf[top].mean()) - _db(np.median(tot))), 2)
    out["ltas_db"] = [round(float(v), 2) for v in lev]
    # ------------------------------------------------ room: the fall at phrase ends, and the pause floor
    db = frames_db(x, sr, 0.01)
    lvl = np.percentile(db, 95)
    dec, floors = [], []
    i = 10
    while i < len(db) - 30:
        if db[i - 1] > lvl - 18 and np.all(db[i:i + 20] < lvl - 22):
            pre = float(np.max(db[i - 10:i]))
            j = i - 10 + int(np.argmax(db[i - 10:i]))
            k = j
            while k < min(len(db), j + 60) and db[k] > pre - 15:
                k += 1
            dec.append((k - j) * 10)
            q = i
            while q < len(db) and db[q] < lvl - 22:
                q += 1
            core = db[i + 5:q - 5]
            if len(core) >= 10:
                floors.append(np.percentile(core, 20) - lvl)
            i = q
        else:
            i += 1
    out["decay_ms"] = round(float(np.median(dec)), 1) if dec else None
    out["floor_db"] = round(float(np.median(floors)), 1) if floors else None
    # ------------------------------------------------ dynamics
    n1 = sr
    crest = []
    for a in range(0, len(x) - n1, n1 // 2):
        seg = x[a:a + n1].astype(np.float64)
        r = math.sqrt((seg ** 2).mean())
        if r > 10 ** ((lvl - 20) / 20):
            crest.append(20 * math.log10(np.abs(seg).max() / r))
    out["crest_db"] = round(float(np.median(crest)), 2) if crest else None
    bp = measure._block_power(x, sr, 0.4, 0.1)
    sl = -0.691 + 10 * np.log10(bp + 1e-12)
    integ = measure.lufs(x, sr)
    sl = sl[sl > integ - 20]
    out["st_loud_std_db"] = round(float(np.std(sl)), 2) if len(sl) > 3 else None
    d20 = frames_db(x, sr, 0.02)
    d20 = d20[d20 > np.percentile(d20, 95) - 40]
    out["frame_dyn_db"] = round(float(np.percentile(d20, 95) - np.percentile(d20, 10)), 2)
    out["lra"] = round(measure.lra(x, sr), 2)
    out["lufs"] = round(integ, 2)
    out["words"] = len(words)
    out["dur_s"] = round(len(x) / sr, 1)
    return out


# features that score a voice against the references (f0 median, centroid and loudness are a
# choice of voice or a mix level, not a quality, and stay out)
SCORED = {
    "pitch": ["range_st", "std_st", "vel_st_s", "swing_st", "decl_st_s", "final_st"],
    "rate": ["wpm_artic", "phrase_wpm_cv"],
    "pauses": ["pauses_pm", "pause_med_s"],
    "emphasis": ["emph_db", "emph_st", "word_db_std", "prom_per_10s"],
    "timbre": ["tilt_db_oct", "alpha_db", "presence_db", "mud_db", "sib_db"],
    "room": ["decay_ms"],
    "dynamics": ["crest_db", "st_loud_std_db", "frame_dyn_db"],
}


def band_of(rows):
    out = {}
    keys = {k for g in SCORED.values() for k in g} | {"f0_med_hz", "centroid_hz", "lufs", "lra", "wpm", "pause_p90_s", "floor_db"}
    for k in sorted(keys):
        v = [r[k] for r in rows if r.get(k) is not None]
        if len(v) >= 5:
            out[k] = {"p10": round(float(np.percentile(v, 10)), 3), "p25": round(float(np.percentile(v, 25)), 3),
                      "median": round(float(np.median(v)), 3), "p75": round(float(np.percentile(v, 75)), 3),
                      "p90": round(float(np.percentile(v, 90)), 3), "n": len(v)}
    return out


def score(feat, band):
    """0..100 per feature: exp(-z^2/2), z = distance from the reference median in robust sigmas
    ((p90-p10)/2.563); per group the mean, overall the mean of groups. Also in-band counts."""
    per, groups, inb, tot = {}, {}, 0, 0
    for g, ks in SCORED.items():
        ss = []
        for k in ks:
            if feat.get(k) is None or k not in band:
                continue
            b = band[k]
            sig = max((b["p90"] - b["p10"]) / 2.563, 1e-6)
            z = (feat[k] - b["median"]) / sig
            s = 100 * math.exp(-0.5 * z * z)
            per[k] = {"value": feat[k], "median": b["median"], "band": [b["p10"], b["p90"]], "z": round(z, 2),
                      "score": round(s, 1), "in_band": b["p10"] <= feat[k] <= b["p90"]}
            inb += per[k]["in_band"]
            tot += 1
            ss.append(s)
        if ss:
            groups[g] = round(float(np.mean(ss)), 1)
    return {"overall": round(float(np.mean(list(groups.values()))), 1), "groups": groups,
            "in_band": f"{inb}/{tot}", "features": per}
