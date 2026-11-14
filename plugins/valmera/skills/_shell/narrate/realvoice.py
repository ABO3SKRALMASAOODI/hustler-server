"""Real voices cut into a finished narration (lane VO2, 14 Nov 2026).

The footage engine (worker/footage/voice.py) finds the person a film is about saying, in their own voice, a sentence that
belongs after one of the narrator's lines, and cuts its sound and picture from the same seconds. This puts the sound in:
the narrator finishes the line, a breath, the person speaks, a breath, the narrator goes on. The references do this in
all four critic reels (judge TEARDOWN W2), up to 22% of a film.

  splice(narr_dir, voices, foot_dir) -> placements
narr_dir holds narration.wav and words.json (narrate.py render). The narrator-only files are kept as
narration_narrator.wav and words_narrator.json; narration.wav and words.json are rewritten with the person's words in
them (each word carries "voice": the person, and the segment of the line it follows, so a film's clock gives that line
the extra seconds); voices.json lists where each cut-in sits for the film to pin its picture.

Level: the person's speech is set to the narrator's speech level (the loud half of 50 ms frames, RMS) minus 1 dB, under
-1 dBFS peak, high-passed at 80 Hz; 12 ms fades. Nothing else is done to it: it should sound like the room it was
recorded in.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

PAUSE_S = 0.30
GAP_BEFORE, GAP_AFTER = 0.32, 0.42
FADE_S = 0.012


def _speech_rms(x, sr):
    n = int(0.05 * sr)
    k = len(x) // n
    if k < 4:
        return float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    e = np.sqrt((x[: k * n].reshape(k, n).astype(np.float64) ** 2).mean(1))
    return float(np.mean(e[e >= np.median(e)]) + 1e-9)


def _hp(y, sr, f=80.0):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(2, f / (sr / 2), btype="high", output="sos"), y).astype(np.float32)


def _load(path, sr):
    import soundfile as sf
    y, ysr = sf.read(str(path), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if ysr != sr:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(int(sr), int(ysr))
        y = resample_poly(y, sr // g, ysr // g).astype(np.float32)
    return y


def phrases_of(words):
    out, cur = [], [words[0]]
    for a, z in zip(words, words[1:]):
        if z["start"] - a["end"] >= PAUSE_S or a.get("seg") != z.get("seg") or a.get("voice") != z.get("voice"):
            out.append(cur)
            cur = []
        cur.append(z)
    out.append(cur)
    return [{"text": " ".join(w["w"] for w in p), "start": p[0]["start"], "end": p[-1]["end"], "seg": p[0].get("seg"),
             **({"voice": p[0]["voice"]} if p[0].get("voice") else {})} for p in out]


def splice(narr_dir, voices, foot_dir):
    import soundfile as sf
    nd, fd = Path(narr_dir), Path(foot_dir)
    src_wav = nd / "narration_narrator.wav"
    src_words = nd / "words_narrator.json"
    if not src_wav.exists() and any(w.get("voice") for w in json.load(open(nd / "words.json"))["words"]):
        return []                                 # a narration that already carries a cut-in, without its narrator-only copy
    if not src_wav.exists():
        shutil.copy2(nd / "narration.wav", src_wav)
        shutil.copy2(nd / "words.json", src_words)
    x, sr = sf.read(str(src_wav), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    wj = json.load(open(src_words))
    words = [dict(w) for w in wj["words"]]
    level = _speech_rms(x, sr) * 10 ** (-1 / 20)
    nseg = max((int(w.get("seg", 0)) for w in words), default=0) + 1
    placed = []
    for v in sorted(voices, key=lambda v: int(v["after"]), reverse=True):     # the latest first: earlier times stay put
        after = int(v["after"])
        if not 0 <= after < nseg - 1:
            continue                              # a cut-in sits between two of the narrator's lines
        mine = [w for w in words if int(w.get("seg", -1)) == after and not w.get("voice")]
        nxt = [w for w in words if int(w.get("seg", -1)) > after and not w.get("voice")]
        if not mine or not nxt:
            continue
        te, tn = float(mine[-1]["end"]), float(nxt[0]["start"])
        m = (te + tn) / 2
        y = _load(fd / v["wav"], sr)
        y = _hp(y, sr)
        y *= level / _speech_rms(y, sr)
        pk = float(np.max(np.abs(y))) if len(y) else 0.0
        if pk > 0.89:
            y *= 0.89 / pk
        f = int(FADE_S * sr)
        if len(y) > 4 * f:
            y[:f] *= np.linspace(0, 1, f, dtype=np.float32)
            y[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
        pa = max(GAP_BEFORE - (m - te), 0.0)
        pb = max(GAP_AFTER - (tn - m), 0.0)
        ins = np.concatenate([np.zeros(int(pa * sr), np.float32), y, np.zeros(int(pb * sr), np.float32)])
        c = int(round(m * sr))
        x = np.concatenate([x[:c], ins, x[c:]])
        shift = len(ins) / sr
        for w in words:
            if float(w["start"]) >= m:
                w["start"] = round(float(w["start"]) + shift, 3)
                w["end"] = round(float(w["end"]) + shift, 3)
        t0 = m + pa
        new = [dict(w=w["w"], start=round(t0 + float(w["start"]), 3), end=round(t0 + float(w["end"]), 3), seg=after, voice=v["person"])
               for w in v.get("words") or []]
        k = next((i for i, w in enumerate(words) if float(w["start"]) >= m), len(words))
        words[k:k] = new
        for p in placed:                          # cut-ins already placed later in the film move with this one
            p["start"] = round(p["start"] + shift, 3)
            p["end"] = round(p["end"] + shift, 3)
        placed.append(dict(after=after, person=v["person"], start=round(t0, 3), end=round(t0 + len(y) / sr, 3), said=v.get("said"),
                           file=str(fd / v["file"]) if v.get("file") else None, wav=str(fd / v["wav"]), source=v.get("source"),
                           time_range=v.get("time_range")))
    if not placed:
        return []
    sf.write(str(nd / "narration.wav"), x, sr, subtype="PCM_24")
    cuts = [round((a["end"] + z["start"]) / 2, 3) for a, z in zip(words, words[1:]) if z["start"] - a["end"] >= PAUSE_S]
    out = dict(wj, duration=round(len(x) / sr, 2), words=words, phrases=phrases_of(words), cut_points=cuts,
               voices=sorted(placed, key=lambda p: p["start"]))
    json.dump(out, open(nd / "words.json", "w"), indent=1)
    json.dump(sorted(placed, key=lambda p: p["start"]), open(nd / "voices.json", "w"), indent=1)
    return sorted(placed, key=lambda p: p["start"])
