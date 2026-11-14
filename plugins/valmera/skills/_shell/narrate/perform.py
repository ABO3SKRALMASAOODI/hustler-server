"""Line-by-line performance: several takes of every script line, the kept take chosen by signal.

Every take is heard (the take listener) and measured with prosody.py on the same signals the
reference narrators were measured on (voice_ref.json: 48 narrated metromedia and long-form films).
A take is kept only when every word is heard; among those, the take whose pitch movement, pace
and stress sit nearest the references wins, with a penalty for a register or timbre that drifts
from the lines already kept (one narrator, one voice). The writer's stressed words are measured
where they fall: how much louder and higher than the rest of the line. No listening model.
"""
from __future__ import annotations

import math
import re

import numpy as np

import measure
import prosody

LINE_FEATS = {"pitch": ["range_st", "std_st", "vel_st_s", "swing_st", "final_st"],
              "rate": ["wpm_artic"],
              "emphasis": ["emph_db", "emph_st", "word_db_std"]}
DRIFT_ST = 1.0        # register drift allowed against the kept lines before it costs (semitones)
DRIFT_CENT = 0.12     # spectral centroid drift allowed (fraction)


def _tok(w):
    return re.sub(r"[^a-z0-9']", "", str(w).lower())


def stress_hit(x, sr, words, stress):
    """dB and semitones the writer's stressed words stand above the rest of the line (median word)."""
    if not stress or len(words) < 4:
        return None
    f0 = prosody.pitch_track(x, sr)
    ok = np.isfinite(f0)
    if ok.sum() < 30:
        return None
    med = float(np.nanmedian(f0))
    st = 12 * np.log2(f0 / med)
    lv, pk, mark = [], [], []
    want = {_tok(s) for w in stress for s in str(w).split()}
    for w in words:
        a, b = int(w["start"] * sr), int(w["end"] * sr)
        seg = x[a:b].astype(np.float64)
        lv.append(10 * math.log10(max((seg ** 2).mean(), 1e-14)) if len(seg) > sr * 0.03 else np.nan)
        s = st[int(w["start"] / 0.01): int(w["end"] / 0.01)]
        pk.append(float(np.nanmax(s)) if np.isfinite(s).sum() >= 3 else np.nan)
        mark.append(_tok(w["w"]) in want)
    lv, pk, mark = np.array(lv), np.array(pk), np.array(mark)
    if not mark.any() or np.isfinite(lv[~mark]).sum() < 3:
        return None
    db = float(np.nanmax(lv[mark]) - np.nanmedian(lv[~mark]))
    sv = float(np.nanmax(pk[mark]) - np.nanmedian(pk[~mark])) if np.isfinite(pk[mark]).any() and np.isfinite(pk[~mark]).sum() >= 3 else None
    return {"db": round(db, 2), "st": None if sv is None else round(sv, 2)}


def _s(v, b, center=None):
    sig = max((b["p90"] - b["p10"]) / 2.563, 1e-6)
    z = (v - (b["median"] if center is None else center)) / sig
    return 100 * math.exp(-0.5 * z * z)


def line_score(feat, band, want_wpm=None, hit=None):
    """0..100: the line's prosody against the reference band (pace against the asked pace)."""
    groups = {}
    for g, ks in LINE_FEATS.items():
        ss = []
        for k in ks:
            if feat.get(k) is None or k not in band:
                continue
            ss.append(_s(feat[k], band[k], want_wpm if (k == "wpm_artic" and want_wpm) else None))
        if ss:
            groups[g] = float(np.mean(ss))
    if hit and "emph_db" in band:
        hs = [_s(hit["db"], band["emph_db"])]
        if hit.get("st") is not None and "emph_st" in band:
            hs.append(_s(hit["st"], band["emph_st"]))
        groups["stress"] = float(np.mean(hs))
    return round(float(np.mean(list(groups.values()))), 1) if groups else 0.0, {k: round(v, 1) for k, v in groups.items()}


def voice_of(x, sr, words):
    """register and brightness of a take: f0 median (Hz) and spectral centroid of its speech frames."""
    f0 = prosody.pitch_track(x, sr)
    f0m = float(np.nanmedian(f0)) if np.isfinite(f0).sum() > 20 else None
    import librosa
    c = librosa.feature.spectral_centroid(y=x.astype(np.float32), sr=sr, n_fft=2048, hop_length=512)[0]
    e = librosa.feature.rms(y=x.astype(np.float32), frame_length=2048, hop_length=512)[0]
    sp = e > np.percentile(e, 60)
    return f0m, float(np.median(c[sp])) if sp.any() else None


def drift_penalty(v, kept):
    """points lost for a register or brightness away from the median of the lines already kept"""
    if not kept or v[0] is None:
        return 0.0
    f0s = [k[0] for k in kept if k[0]]
    cs = [k[1] for k in kept if k[1]]
    p = 0.0
    if f0s:
        d = abs(12 * math.log2(v[0] / float(np.median(f0s))))
        p += 25 * max(0.0, d - DRIFT_ST)
    if cs and v[1]:
        d = abs(v[1] / float(np.median(cs)) - 1)
        p += 150 * max(0.0, d - DRIFT_CENT)
    return round(p, 1)


def judge(x, sr, words, seg, band, want_wpm=None):
    """signals of one take of one line"""
    feat = prosody.measure_voice(x, sr, words) if len(words) >= 3 else {}
    hit = stress_hit(x, sr, words, seg.get("emphasis"))
    sc, groups = line_score(feat, band, want_wpm, hit)
    return {"score": sc, "groups": groups, "stress": hit,
            "feat": {k: feat.get(k) for ks in LINE_FEATS.values() for k in ks}, "voice": voice_of(x, sr, words)}


def choose(takes, kept_voices):
    """takes: [{"errs", "score", "voice", ...}]; the kept take: every word heard first, then signal minus drift."""
    for t in takes:
        t["drift"] = drift_penalty(t["voice"], kept_voices)
        t["net"] = round(t["score"] - t["drift"], 1)
    return min(takes, key=lambda t: (t["errs"], -t["net"]))


def tempo(x, sr, f):
    """x played f times faster, pitch kept (ffmpeg atempo, WSOLA): a slow take brought toward its asked pace. Small
    factors only (the references' pace is ~190-210 words a minute while speaking; several engines read ~150-165)."""
    import subprocess
    import tempfile

    import soundfile as sf

    import narrate as N
    if abs(f - 1.0) < 0.01:
        return x
    with tempfile.TemporaryDirectory() as td:
        a, b = f"{td}/in.wav", f"{td}/out.wav"
        sf.write(a, x, sr, subtype="FLOAT")
        subprocess.run([N.FFMPEG, "-v", "error", "-y", "-i", a, "-af", f"atempo={f:.4f}", "-c:a", "pcm_f32le", b], check=True)
        y, _ = sf.read(b, dtype="float32")
    return y if y.ndim == 1 else y.mean(axis=1)
