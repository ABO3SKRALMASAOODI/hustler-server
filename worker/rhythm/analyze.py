"""The rhythm engine: a user track -> RhythmMap (Wave 2 track 2C).

Pipeline (ROADMAP 2C, the model-free fallback path):
  1. features      dsp.features (log-mel, drum bands, chroma, level)
  2. onsets        drums.onsets: kick / snare / hat / transient + strength;
                   drums.notes_808 from the bass band
  3. tempo         autocorrelation of a percussive onset envelope with a comb
                   over 2x and 4x the lag and a broad prior
  4. beats         dynamic-programming beat tracker (Ellis) on that tempo
  5. downbeats     downbeats.phase (kick + harmony change - backbeat snare)
  6. sections      sections.sections (bar self-similarity novelty)
  7. gaps, drops, stutters   drops.*
  8. confidence    tempo salience x beat support x bar-phase margin

The user's track is the clock; nothing here ever chooses or adds music.
Pure numpy + ffmpeg; ~5-15 s for a 3-minute track on one vCPU.
"""

import math
import time

import numpy as np

from . import downbeats as DB
from . import drops as DR
from . import drums as DM
from . import dsp
from . import sections as SEC

VERSION = 1
PRODUCER = f"rhythm.dsp@{VERSION}"
BPM_RANGE = (55.0, 215.0)
TIGHTNESS = 300.0


def onset_envelope(feat):
    full = dsp.detrend(dsp.superflux(feat["logmel"]))
    b = feat["bands"]
    kick = dsp.detrend(dsp.band_flux(b["kick"], lag=2))
    snare = dsp.detrend(dsp.band_flux(b["noise"], lag=2))

    def n(x):
        s = float(np.percentile(x, 99)) if len(x) else 0.0
        return x / s if s > 0 else x
    return (n(full) + 0.6 * n(kick) + 0.4 * n(snare)).astype(np.float64)


def _acf(env):
    e = env - env.mean()
    n = len(e)
    nfft = 1 << int(math.ceil(math.log2(2 * n)))
    f = np.fft.rfft(e, nfft)
    ac = np.fft.irfft(f * np.conj(f), nfft)[:n]
    return ac / (ac[0] or 1.0)


def tempo(env, prior_bpm=115.0):
    """(bpm, salience 0-1, candidates [(bpm, score)])."""
    if len(env) < dsp.FPS * 6 or env.std() == 0:
        return None, 0.0, []
    ac = _acf(env)
    lo = int(math.floor(60.0 * dsp.FPS / BPM_RANGE[1]))
    hi = min(len(ac) // 4 - 1, int(math.ceil(60.0 * dsp.FPS / BPM_RANGE[0])))
    if hi <= lo + 2:
        return None, 0.0, []

    def at(lag):
        i = int(math.floor(lag))
        f = lag - i
        if i + 1 >= len(ac):
            return 0.0
        return float(ac[i] * (1 - f) + ac[i + 1] * f)
    cands = []
    for lag in np.arange(lo, hi + 0.001, 0.25):
        comb = at(lag) + 0.5 * at(2 * lag) + 0.25 * at(4 * lag) + \
            0.25 * at(3 * lag)
        bpm = 60.0 * dsp.FPS / lag
        prior = math.exp(-0.5 * (math.log2(bpm / prior_bpm) / 1.0) ** 2)
        cands.append((bpm, comb * (0.6 + 0.4 * prior), comb))
    best = max(cands, key=lambda c: c[1])
    vals = np.array([c[2] for c in cands])
    sal = float(np.clip((best[2] - np.median(vals)) /
                        ((np.percentile(vals, 95) - np.median(vals)) or 1.0)
                        / 2.0, 0.0, 1.0))
    return best[0], sal, sorted(cands, key=lambda c: -c[1])[:6]


def beat_track(env, bpm, tightness=TIGHTNESS):
    """Beat frames by dynamic programming (Ellis 2007): every frame's best
    score is its onset strength plus the best predecessor 0.5-2 periods
    back, penalised by the log-deviation from the period."""
    period = 60.0 * dsp.FPS / bpm
    n = len(env)
    e = env / (env.std() or 1.0)
    score = e.copy()
    back = np.full(n, -1, dtype=np.int64)
    lo_off, hi_off = int(round(2 * period)), int(round(period / 2))
    offs = np.arange(hi_off, lo_off + 1)
    pen = -tightness * np.log(offs / period) ** 2
    for i in range(hi_off, n):
        idx = i - offs
        ok = idx >= 0
        if not ok.any():
            continue
        cand = score[idx[ok]] + pen[ok]
        k = int(np.argmax(cand))
        if cand[k] > 0:
            score[i] = e[i] + cand[k]
            back[i] = idx[ok][k]
    # the end: the best score within the last period
    tail = max(1, int(round(period)))
    i = n - tail + int(np.argmax(score[n - tail:]))
    beats = []
    while i >= 0:
        beats.append(i)
        i = int(back[i])
    beats = beats[::-1]
    # trim leading/trailing beats in silence
    thr = 0.05 * float(np.percentile(env, 95))
    while len(beats) > 2 and env[beats[0]] < thr and \
            env[max(0, beats[0] - 2):beats[0] + 3].max() < thr:
        beats.pop(0)
    return beats


def _refine(env, frames, r=2):
    out = []
    for i in frames:
        lo, hi = max(1, i - r), min(len(env) - 1, i + r + 1)
        j = lo + int(np.argmax(env[lo:hi]))
        if 0 < j < len(env) - 1:
            a, b, c = env[j - 1], env[j], env[j + 1]
            den = a - 2 * b + c
            off = 0.5 * (a - c) / den if den else 0.0
            off = float(np.clip(off, -0.5, 0.5))
        else:
            off = 0.0
        out.append((j + off))
    return out


def _octave(env, bpm, cands):
    """Between bpm, 2x and 0.5x: the tempo whose beat track lands on more
    onset energy per beat with the most regular spacing (tapping range
    preferred)."""
    best = None
    for b in {bpm, bpm * 2.0, bpm / 2.0}:
        if not BPM_RANGE[0] <= b <= BPM_RANGE[1]:
            continue
        fr = beat_track(env, b)
        if len(fr) < 4:
            continue
        on = float(np.mean([env[i] for i in fr]))
        off = float(np.mean([env[(i + int(round(30 * dsp.FPS / b))) %
                                 len(env)] for i in fr]))
        contrast = on / (off + 1e-9)
        pref = math.exp(-0.5 * (math.log2(b / 115.0) / 0.9) ** 2)
        s = contrast * (0.7 + 0.3 * pref)
        if best is None or s > best[0]:
            best = (s, b, fr)
    return best


def analyze_samples(y, *, ref=None, sr=dsp.SR):
    """RhythmMap of mono samples (see the module doc)."""
    t0 = time.monotonic()
    feat = dsp.features(y)
    dur = len(y) / float(sr)
    env = onset_envelope(feat)
    bpm, sal, cands = tempo(env)
    ons = DM.onsets(feat)
    rmap = {"version": 1, "source": "music", "clock": "media",
            "ref": ref or {"storage_key": None, "sha256": None,
                           "music_id": None},
            "producer": PRODUCER, "bpm": None, "beats": [], "downbeats": [],
            "onsets": ons, "sections": [], "drops": [], "gaps": [],
            "confidence": 0.0}
    gap_list = DR.gaps(y, sr)
    rmap["gaps"] = [{"t0": g["t0"], "t1": g["t1"]} for g in gap_list]
    if not bpm:
        rmap["timing_ms"] = round((time.monotonic() - t0) * 1000.0)
        return rmap
    pick = _octave(env, bpm, cands)
    if pick:
        _s, bpm, frames = pick
    else:
        frames = beat_track(env, bpm)
    refined = _refine(env, frames)
    beats = [round(f * dsp.HOP / dsp.SR, 3) for f in refined]
    # the tempo the beats actually keep (robust slope through the beats)
    if len(beats) > 8:
        k = np.arange(len(beats))
        slope = float(np.polyfit(k, np.array(beats), 1)[0])
        if slope > 0:
            bpm = 60.0 / slope
    ph, pconf, _per = DB.phase(feat, beats, ons)
    downs = DB.downbeats(beats, ph)
    secs = SEC.sections(feat, downs, dur, ons)
    rises = DR.drops_with_rise(feat, downs, secs, gap_list, bpm)
    # a drop is a STRUCTURAL moment: it opens a drop section, comes straight
    # after a gap, or brings the low end back from near silence at a section
    # line (a final drop under an outro label); a rise inside a section is a
    # fill
    bar = 240.0 / bpm
    drops = [t for t, rise in rises
             if any(s["label"] == "drop" and abs(s["t0"] - t) <= bar * 0.6
                    for s in secs)
             or any(t - bar <= g["t1"] <= t + 0.1 for g in gap_list)
             or (rise >= 12.0 and any(abs(s["t0"] - t) <= bar * 0.6
                                      for s in secs[1:]))]
    # beat support: share of beats on a local onset peak
    support = float(np.mean([1.0 if env[int(round(f))] >=
                             0.5 * env[max(0, int(f) - 3):int(f) + 4].max()
                             else 0.0 for f in refined])) if refined else 0.0
    conf = float(np.clip(0.45 * sal + 0.35 * support + 0.2 * pconf, 0, 1))
    rmap.update(bpm=round(float(bpm), 3), beats=beats, downbeats=downs,
                sections=secs, drops=drops, confidence=round(conf, 3))
    rmap["openings"] = DR.openings(feat, downs, beats=beats)
    rmap["808"] = DM.notes_808(feat)
    rmap["stutters"] = DR.stutters(y, beats, sr)
    rmap["phrase_ends"] = [s["t1"] for s in secs[:-1]]
    rmap["downbeat_confidence"] = round(pconf, 3)
    rmap["timing_ms"] = round((time.monotonic() - t0) * 1000.0)
    return rmap


def analyze(path, *, ref=None, max_s=None):
    """RhythmMap of a media file's first audio stream."""
    if max_s is None:
        try:
            import config
            max_s = float(config.RHYTHM_MAX_S)
        except Exception:  # noqa: BLE001
            max_s = 900.0
    y = dsp.decode(path, max_s=max_s)
    return analyze_samples(y, ref=ref)
