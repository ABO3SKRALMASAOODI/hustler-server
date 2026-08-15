"""Runtime audio perception: beat grid, energy envelope, per-word vocal stress.

This is the runtime promotion of the measurement stack that tagged the music
library (worker/tools/analyze.py) — the same spectral-flux onset envelope,
autocorrelation tempo estimate and phase-locked beat grid, made safe to run
on customer media inside an agent turn on a ~1 vCPU / low-RAM box:

  * the decode is STREAMED (ffmpeg stdout in small chunks) and the STFT is
    computed incrementally, so memory stays ~tens of MB regardless of the
    file's length — never a full spectrogram in RAM (a 20-min spectrogram is
    ~400 MB in float64, on a worker that has OOM-crashed before);
  * everything is numpy + ffmpeg, no new dependencies.

The output feeds DECISIONS, never renders. Tools read it to place cuts,
zooms and sfx on real beats / stress peaks and then write CONCRETE
timestamps into the EDL — the renderer never consults perception, so a
render stays reproducible from (EDL version, source sha, index words) alone.

Cached per content: the main video's analysis lives under the index row's
json["perception"] (lazy, written the first time a tool asks; PIPELINE_VERSION
untouched — this is a sidecar, not indexer output, so no re-index is ever
triggered by shipping or changing it); music assets cache in asset meta.
PERCEPTION_VERSION invalidates stale sidecars when this algorithm changes.
"""

import collections
import json
import math
import subprocess
import threading

import numpy as np

# 2 (Jul 26 2026): _onset_env now clips outliers, so every sidecar computed
# before it — including the ones that recorded "beats: none detected" on real
# music — must be recomputed rather than read back.
# 3: compact timbre/mid-band/bass descriptors let the music workbench compare
# actual candidate audio instead of selecting from titles alone.
PERCEPTION_VERSION = 3

SR = 22050
N_FFT = 2048
HOP = 512
FPS = SR / HOP                  # envelope frame rate ~43.07 Hz
# The streamed STFT indexes frames by WINDOW START, so an onset surfaces in
# the first window that contains it — up to n_fft samples before its true
# time. Report frame k as the window CENTER (librosa's center=True
# convention): measured on a sample-accurate click track this took the beat
# grid from ~70ms early to inside sync tolerance.
FRAME_CENTER_S = (N_FFT / 2) / SR
# Keep the FFT working set genuinely small. 2048 frames looked harmless as
# "47 seconds of mono audio", but the old advanced-indexing matrix plus the
# float/complex spectral intermediates peaked around 130 MiB in production.
# That single lazy sidecar pass took the 512-MiB Render dispatcher from
# 310 MiB to 442 MiB RSS immediately before an OOM. 512 frames is still a
# large vectorised batch (~12 s of audio) while cutting the transient arrays
# to one quarter of their former size.
CHUNK_FRAMES = 512
CHUNK_SAMPLES = HOP * CHUNK_FRAMES
MAX_ANALYZE_S = 3600.0          # hard stop: an hour of audio is enough signal
ENERGY_BIN_S = 0.5              # energy envelope resolution
VB_STORE_FPS = 8.0              # stored speech-envelope rate (peak-pooled)
SPEECH_BAND = (300.0, 3400.0)

# The frequency bin masks, computed once.
_FREQS = np.fft.rfftfreq(N_FFT, 1.0 / SR)
_VB = (_FREQS >= SPEECH_BAND[0]) & (_FREQS <= SPEECH_BAND[1])
_BASS = (_FREQS >= 35.0) & (_FREQS <= 250.0)


class PerceptionError(RuntimeError):
    """Analysis failed in a way the caller should surface, not hide."""


# ------------------------------------------------------------------ decode
def _stream_frames(path, max_s=MAX_ANALYZE_S):
    """Yield (frames, magnitudes) incrementally: STFT magnitude blocks of the
    file's mono audio at SR, never holding more than one chunk. Raises
    PerceptionError when the file has no decodable audio.

    stderr is drained by a thread from the start: a corrupt file makes
    ffmpeg emit one '-v error' line per bad packet, and once those fill the
    ~64KB pipe ffmpeg blocks on write, stdout goes silent, and the read
    below waits forever — the classic two-pipe deadlock that pins an agent
    slot (the round-28 net_fetch failure class). The tail of stderr is kept
    (bounded) for the honest error message."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0",
           "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1",
           "-ar", str(SR), "-t", f"{max_s:.0f}", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    err_tail = collections.deque(maxlen=32)

    def _drain():
        try:
            for line in proc.stderr:
                err_tail.append(line)
        except Exception:
            pass

    drainer = threading.Thread(target=_drain, daemon=True)
    drainer.start()
    window = np.hanning(N_FFT).astype(np.float32)
    tail = np.zeros(0, dtype=np.float32)
    got_any = False
    try:
        while True:
            raw = proc.stdout.read(CHUNK_SAMPLES * 4)
            if not raw:
                break
            x = np.frombuffer(raw, dtype="<f4")
            buf = np.concatenate([tail, x])
            if len(buf) < N_FFT:
                tail = buf
                continue
            n = 1 + (len(buf) - N_FFT) // HOP
            # A strided view avoids materialising the old n x N_FFT int64
            # advanced-index matrix (about 32 MiB at the former chunk size).
            # Multiplication intentionally makes the one contiguous float32
            # frame block the FFT needs.
            frame_view = np.lib.stride_tricks.sliding_window_view(
                buf, N_FFT)[::HOP][:n]
            frames = frame_view * window[None, :]
            mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
            got_any = True
            yield mag
            tail = buf[n * HOP:]
    finally:
        proc.stdout.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()          # never leave a wedged decoder holding a slot
            proc.wait()
        drainer.join(timeout=5)
        try:
            proc.stderr.close()
        except Exception:
            pass
    if not got_any:
        err = b"".join(err_tail).decode("utf-8", "replace").strip()
        raise PerceptionError(
            "no decodable audio in this file"
            + (f" ({err[:200]})" if err else ""))


# ------------------------------------------------------------------ tempo
def _onset_env(flux):
    """Trend-removed onset strength (rhythm without the loudness arc).

    Then CLIPPED against a robust high percentile, because one outlier frame
    otherwise decides the tempo. _acf divides by the envelope's variance, and
    a single click / DC pop / clipped transient can carry more variance than
    every real beat put together — the periodic component is then a rounding
    error and the confidence collapses to ~0. A user's uploaded song did
    exactly this on Jul 26 2026: one burst 67 dB above the music left
    "87.7 BPM, confidence 0.01, beats: none detected" on a track with an
    obvious pulse, and the agent had to tell him it could not cut to it.
    Beats are 1-2% of frames, so p99.5 sits AT beat level; clipping at 3x
    that leaves every real onset untouched and only squashes the freak."""
    w = max(3, int(round(FPS * 0.5)) | 1)
    kern = np.ones(w, dtype=np.float64) / w
    trend = np.convolve(flux, kern, mode="same")
    env = np.maximum(flux - trend, 0.0)
    if env.size:
        ceiling = 3.0 * float(np.percentile(env, 99.5))
        if ceiling > 0:
            env = np.minimum(env, ceiling)
    return env


def _acf(env):
    e = env - env.mean()
    n = len(e)
    nfft = 1 << int(math.ceil(math.log2(2 * n)))
    F = np.fft.rfft(e, nfft)
    ac = np.fft.irfft(F * np.conj(F), nfft)[:n]
    if ac[0] <= 0:
        return None
    return (ac / ac[0]) * (n / np.maximum(n - np.arange(n), 1))


def _acf_at_bpm(ac, bpm):
    """ACF support at a tempo, as the MAX over the lag's neighboring bins.
    A non-integer true lag splits its peak across two bins — reading one
    bin under-reports the very periodicities the octave logic must judge
    (measured: an exact 120 BPM click aliased to 60 because lag 21.5's
    energy read as ~0.55 from bin 21 alone while bin 43 read whole)."""
    if ac is None:
        return 0.0
    lag = 60.0 * FPS / bpm
    lo = max(1, int(math.floor(lag)) - 1)
    hi = min(len(ac) - 1, int(math.ceil(lag)) + 1)
    if hi < lo:
        return 0.0
    return float(np.max(ac[lo:hi + 1]))


def _tempo(env):
    """(bpm, confidence 0-1) — the analyze.py estimator, condensed. Returns
    (None, 0.0) when the audio has no usable pulse."""
    if env.size < int(FPS * 8) or env.std() == 0:
        return None, 0.0
    ac = _acf(env)
    if ac is None:
        return None, 0.0
    lag_min = int(round(60.0 * FPS / 200.0))
    lag_max = min(len(ac) - 1, int(round(60.0 * FPS / 50.0)))
    if lag_max <= lag_min + 2:
        return None, 0.0
    lags = np.arange(lag_min, lag_max + 1)
    vals = ac[lags]
    bpms = 60.0 * FPS / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 110.0)) ** 2)
    k = int(np.argmax(vals * prior))
    i = lags[k]
    if lag_min < i < lag_max:
        y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
        denom = (y0 - 2 * y1 + y2)
        shift = float(np.clip(0.5 * (y0 - y2) / denom, -0.5, 0.5)) \
            if denom != 0 else 0.0
    else:
        shift = 0.0
    bpm = 60.0 * FPS / (i + shift)

    # fold double-time down into the tapping range when the half lag is a
    # real peak of its own (same rule as the library analyzer)
    halved = 0
    while bpm > 140.0 and _acf_at_bpm(ac, bpm / 2.0) > 0.5 * _acf_at_bpm(ac, bpm):
        bpm /= 2.0
        halved += 1
        if halved >= 2:
            break
    # ...and fold UP when the double tempo carries almost the same ACF
    # support. The library analyzer never up-folds (mis-tagging a slow track
    # "fast" is the costlier SEARCH error), but for a BEAT GRID the cost
    # flips: an octave-slow grid halves the cut opportunities, while
    # up-folding is only taken when onsets genuinely occur at the faster
    # period (measured: a non-integer true lag splits its ACF energy across
    # two bins while its integer double lands whole, aliasing an exact
    # 120 BPM click track to 60 without this).
    if bpm <= 100.0 and bpm * 2.0 <= 200.0 \
            and _acf_at_bpm(ac, bpm * 2.0) >= 0.75 * _acf_at_bpm(ac, bpm):
        bpm *= 2.0

    med = float(np.median(vals))
    spread = float(np.percentile(vals, 90) - med)
    sal = (float(vals[k]) - med) / spread if spread > 1e-12 else 0.0
    acf_here = _acf_at_bpm(ac, bpm)
    # stability across quarters of the track
    wins = []
    wlen = len(env) // 4
    if wlen > int(FPS * 8):
        for q in range(4):
            b, _ = _tempo_window(env[q * wlen:(q + 1) * wlen])
            wins.append(b)
    exact = sum(1 for b in wins if b and abs(b - bpm) / bpm <= 0.04)
    stability = exact / 4.0 if wins else 0.5
    conf = (0.4 * float(np.clip(sal / 3.0, 0, 1)) + 0.3 * stability
            + 0.3 * float(np.clip(acf_here / 0.6, 0, 1)))
    conf = min(conf, float(np.clip(acf_here / 0.35, 0, 1)))
    return float(bpm), float(np.clip(conf, 0.0, 1.0))


def _tempo_window(env):
    """Cheap single-window estimate used for the stability check."""
    if env.size < int(FPS * 8) or env.std() == 0:
        return None, 0.0
    ac = _acf(env)
    if ac is None:
        return None, 0.0
    lag_min = int(round(60.0 * FPS / 200.0))
    lag_max = min(len(ac) - 1, int(round(60.0 * FPS / 50.0)))
    if lag_max <= lag_min + 2:
        return None, 0.0
    lags = np.arange(lag_min, lag_max + 1)
    bpms = 60.0 * FPS / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 110.0)) ** 2)
    k = int(np.argmax(ac[lags] * prior))
    return float(60.0 * FPS / lags[k]), float(ac[lags[k]])


def _beat_grid(env, bpm):
    """Phase-locked beat timestamps at `bpm`, each refined to the local
    envelope peak within a small window (a rigid grid drifts on human-played
    music; a pure peak-picker loses the meter — this does neither)."""
    lag = 60.0 * FPS / bpm
    if lag < 2 or len(env) < 2 * lag:
        return []
    n_beats = int((len(env) - 1) / lag)
    # 64 phase candidates ≈ sub-frame granularity at any musical tempo — 24
    # left beats up to ~40ms off a synthetic click, at the edge of what
    # audio/visual sync tolerates.
    best_phase, best_sum = 0.0, -1.0
    for ph in np.linspace(0, lag, 64, endpoint=False):
        idx = np.round(ph + lag * np.arange(n_beats)).astype(int)
        idx = idx[idx < len(env)]
        s = env[idx].sum()
        if s > best_sum:
            best_sum, best_phase = s, ph
    half_w = max(1, int(round(lag * 0.15)))
    beats = []
    for k in range(n_beats):
        g = best_phase + lag * k
        i = min(len(env) - 1, int(round(g)))
        lo, hi = max(0, i - half_w), min(len(env), i + half_w + 1)
        if hi <= lo:
            continue
        j = lo + int(np.argmax(env[lo:hi]))
        # weight toward the grid: only accept the local peak if it clearly
        # beats the grid point, else keep the meter's time
        t = (j if env[j] > env[i] * 1.05 else g) / FPS + FRAME_CENTER_S
        beats.append(round(float(t), 3))
    return beats


# ------------------------------------------------------------------ analyze
def analyze_audio(path, max_s=MAX_ANALYZE_S):
    """Full analysis of a media file's first audio stream.

    Returns {"v", "bpm", "bpm_conf", "beats", "energy", "energy_db_range",
    "vb_env_fps", "vb_env"} — vb_env is the speech-band amplitude envelope
    (float, FPS Hz, quantized) kept so word stress can be scored against any
    word list without re-decoding the audio."""
    flux_parts, energy_parts, vb_parts = [], [], []
    centroid_parts, mid_ratio_parts, bass_ratio_parts = [], [], []
    prev_sqrt = None
    for mag in _stream_frames(path, max_s=max_s):
        c = np.sqrt(mag, dtype=np.float32)
        if prev_sqrt is not None:
            block = np.concatenate([prev_sqrt[None, :], c])
        else:
            block = c
        d = np.diff(block, axis=0)
        flux = np.sum(np.maximum(d, 0.0), axis=1, dtype=np.float64)
        if prev_sqrt is None:
            flux = np.concatenate([[0.0], flux[:len(c) - 1]]) \
                if len(c) > 1 else np.zeros(len(c))
        prev_sqrt = c[-1]
        flux_parts.append(flux)
        p = mag.astype(np.float64) ** 2
        total = p.sum(axis=1)
        safe_total = np.maximum(total, 1e-18)
        energy_parts.append(total)
        vb_parts.append(np.sqrt(p[:, _VB].sum(axis=1)))
        centroid_parts.append((p * _FREQS[None, :]).sum(axis=1) / safe_total)
        mid_ratio_parts.append(p[:, _VB].sum(axis=1) / safe_total)
        bass_ratio_parts.append(p[:, _BASS].sum(axis=1) / safe_total)

    flux = np.concatenate(flux_parts) if flux_parts else np.zeros(0)
    frame_e = np.concatenate(energy_parts) if energy_parts else np.zeros(0)
    vb_env = np.concatenate(vb_parts) if vb_parts else np.zeros(0)
    centroids = (np.concatenate(centroid_parts)
                 if centroid_parts else np.zeros(0))
    mid_ratios = (np.concatenate(mid_ratio_parts)
                  if mid_ratio_parts else np.zeros(0))
    bass_ratios = (np.concatenate(bass_ratio_parts)
                   if bass_ratio_parts else np.zeros(0))
    if flux.size < int(FPS * 2):
        raise PerceptionError("audio too short to analyze (need ~2s)")

    env = _onset_env(flux)
    bpm, conf = _tempo(env)
    beats = _beat_grid(env, bpm) if bpm and conf >= 0.3 else []

    # energy envelope: mean frame power per bin, in dB relative to the peak
    per_bin = max(1, int(round(ENERGY_BIN_S * FPS)))
    n_bins = int(math.ceil(len(frame_e) / per_bin))
    pad = n_bins * per_bin - len(frame_e)
    binned = np.pad(frame_e, (0, pad)).reshape(n_bins, per_bin).mean(axis=1)
    peak = float(binned.max()) or 1e-12
    energy_db = 10.0 * np.log10(np.maximum(binned / peak, 1e-8))
    energy = [round(float(v), 1) for v in energy_db]
    active = frame_e > max(float(np.percentile(frame_e, 20)), 1e-18)

    def _active_median(values):
        rows = values[active] if len(values) == len(active) else values
        return float(np.median(rows)) if rows.size else 0.0

    # speech-band envelope, normalized, then MAX-pooled down to VB_STORE_FPS
    # for compact storage (word stress reads peaks over ≥100ms word spans, so
    # peak-preserving 8 Hz loses nothing that matters; 43 Hz would put ~350 KB
    # of JSON on the index row of a 20-min video).
    vb_peak = float(np.percentile(vb_env, 99.5)) or 1e-12
    vb_q = np.clip(vb_env / vb_peak, 0.0, 1.0)
    pool = max(1, int(round(FPS / VB_STORE_FPS)))
    n_pool = int(math.ceil(len(vb_q) / pool))
    vb_pooled = np.pad(vb_q, (0, n_pool * pool - len(vb_q))
                       ).reshape(n_pool, pool).max(axis=1)
    return {
        "v": PERCEPTION_VERSION,
        "bpm": round(bpm, 1) if bpm else None,
        "bpm_conf": round(conf, 2),
        "beats": beats,
        "energy_bin_s": ENERGY_BIN_S,
        "energy": energy,
        "duration_s": round(len(frame_e) / FPS, 2),
        "dynamic_range_db": round(float(np.percentile(energy_db, 90)
                                         - np.percentile(energy_db, 10)), 1),
        "spectral_centroid_hz": round(_active_median(centroids)),
        # "midband", not "vocals": instruments share 300-3400 Hz. This is
        # still useful evidence for whether a bed may crowd spoken dialogue.
        "midband_ratio": round(_active_median(mid_ratios), 3),
        "bass_ratio": round(_active_median(bass_ratios), 3),
        "vb_env_fps": round(FPS / pool, 3),
        "vb_env": [round(float(v), 3) for v in vb_pooled],
    }


def analyze_transient(path, max_s=60.0):
    """Measure a short sound effect without pretending to hear it.

    Music analysis intentionally refuses clips shorter than roughly two
    seconds because tempo needs history.  Editorial one-shots are often
    80–900 ms, so they need a different measurement surface: attack, peak
    position, audible tail, crest, spectral balance and event density.  These
    facts distinguish a clean click, impact, whoosh and riser far better than
    a catalog title while remaining honest about semantic hearing.
    """
    energy_parts, centroid_parts = [], []
    bass_parts, mid_parts = [], []
    for mag in _stream_frames(path, max_s=max_s):
        power = mag.astype(np.float64) ** 2
        total = power.sum(axis=1)
        safe = np.maximum(total, 1e-18)
        energy_parts.append(total)
        centroid_parts.append(
            (power * _FREQS[None, :]).sum(axis=1) / safe)
        bass_parts.append(power[:, _BASS].sum(axis=1) / safe)
        mid_parts.append(power[:, _VB].sum(axis=1) / safe)
    energy = np.concatenate(energy_parts) if energy_parts else np.zeros(0)
    if not energy.size or float(energy.max()) <= 1e-18:
        raise PerceptionError("sound effect has no measurable audio")
    centroid = np.concatenate(centroid_parts)
    bass = np.concatenate(bass_parts)
    mid = np.concatenate(mid_parts)
    peak = float(energy.max())
    db = 10.0 * np.log10(np.maximum(energy / peak, 1e-8))
    active = np.flatnonzero(db >= -35.0)
    if not active.size:
        raise PerceptionError("sound effect has no measurable active event")
    peak_i = int(np.argmax(energy))
    first_i, last_i = int(active[0]), int(active[-1])
    duration = len(energy) / FPS
    active_duration = max(1.0 / FPS, (last_i - first_i + 1) / FPS)

    # Count separate strong local maxima. A clean one-shot generally has one;
    # a loop/pack/ambience result often has several. Suppress neighboring
    # frames so one transient's rounded top is not counted repeatedly.
    strong = np.flatnonzero(db >= -9.0)
    peaks = []
    guard = max(1, int(round(FPS * 0.12)))
    for i in strong:
        lo, hi = max(0, i - guard), min(len(energy), i + guard + 1)
        if energy[i] >= float(energy[lo:hi].max()) \
                and (not peaks or i - peaks[-1] > guard):
            peaks.append(int(i))

    mask = db >= -35.0
    mean_power = float(energy[mask].mean()) if mask.any() else 1e-18
    return {
        "v": PERCEPTION_VERSION,
        "duration_s": round(duration, 3),
        "active_duration_s": round(active_duration, 3),
        "leading_silence_s": round(first_i / FPS, 3),
        "attack_s": round(max(0.0, (peak_i - first_i) / FPS), 3),
        "peak_time_s": round(peak_i / FPS, 3),
        "peak_position": round(peak_i / max(1, len(energy) - 1), 3),
        "tail_s": round(max(0.0, (last_i - peak_i) / FPS), 3),
        "crest_db": round(10.0 * math.log10(peak / max(mean_power, 1e-18)),
                           2),
        "spectral_centroid_hz": round(float(np.median(centroid[mask]))),
        "bass_ratio": round(float(np.median(bass[mask])), 3),
        "midband_ratio": round(float(np.median(mid[mask])), 3),
        "strong_event_count": len(peaks),
    }


# ------------------------------------------------------------------ stress
def word_stress(perception, words):
    """Per-word vocal stress 0-1 from the stored speech-band envelope.

    A word's stress is its envelope peak relative to the local (rolling
    ~10 s) envelope ceiling — local, because a speaker who warms up over a
    video would otherwise have every late word scored 'stressed'. Returns a
    list aligned with `words` (dicts or objects with t0/t1)."""
    env = np.asarray(perception.get("vb_env") or [], dtype=np.float64)
    fps = float(perception.get("vb_env_fps") or FPS)
    if env.size == 0 or not words:
        return [0.0] * len(list(words))
    win = max(3, int(round(fps * 10.0)))
    # rolling 95th percentile ceiling, cheap via strided max-of-means
    kern = np.ones(win) / win
    local_ref = np.convolve(env, kern, mode="same")
    ceiling = np.maximum(local_ref * 2.0, np.percentile(env, 60) or 1e-9)
    cov_s = env.size / fps           # analysis stops at MAX_ANALYZE_S
    out = []
    for w in words:
        t0 = float(w["t0"] if isinstance(w, dict) else w.t0)
        t1 = float(w["t1"] if isinstance(w, dict) else w.t1)
        if t0 >= cov_s - (1.0 / fps):
            # The analysis never heard this word (past the 1h cap) — 0.0,
            # never a value clamped out of the final frame: a fabricated
            # score would rank words and place punch-ins on audio nobody
            # measured. 0.0 never ranks in top_stressed_words.
            out.append(0.0)
            continue
        a, b = int(t0 * fps), max(int(t0 * fps) + 1, int(math.ceil(t1 * fps)))
        a, b = max(0, min(a, env.size - 1)), max(1, min(b, env.size))
        seg = env[a:b]
        ref = float(np.max(ceiling[a:b])) or 1e-9
        out.append(round(float(np.clip(seg.max() / ref, 0.0, 1.5)) / 1.5, 3))
    return out


def stress_coverage_s(perception):
    """Seconds of audio the stored speech envelope actually covers — words
    past this point carry sentinel 0.0 stress, not a measurement."""
    env_n = len(perception.get("vb_env") or [])
    fps = float(perception.get("vb_env_fps") or FPS)
    return env_n / fps if fps > 0 else 0.0


def top_stressed_words(perception, words, count=8, min_gap_s=2.0,
                       min_len=3):
    """The strongest emphasis moments: indexes into `words`, spaced at least
    min_gap_s apart, skipping tiny function words. Deterministic."""
    scores = word_stress(perception, words)
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    picked = []
    for i in order:
        w = words[i]
        token = (w["w"] if isinstance(w, dict) else w.w) or ""
        t0 = float(w["t0"] if isinstance(w, dict) else w.t0)
        if len(token.strip("\"'.,!?;:")) < min_len:
            continue
        if any(abs(t0 - (words[j]["t0"] if isinstance(words[j], dict)
                         else words[j].t0)) < min_gap_s for j in picked):
            continue
        picked.append(i)
        if len(picked) >= count:
            break
    return sorted(picked)


# ------------------------------------------------------------------ cache
def get_or_compute_for_index(worker_db, dbx, index_row, media_path):
    """The main video's perception sidecar: read from the index row, or
    compute from `media_path` and persist. Never raises on a persist failure
    (the analysis is still returned; it just recomputes next time).

    The persist is a targeted single-key merge, NOT an upsert of the whole
    row: analyze_audio runs for seconds-to-minutes on the 1-vCPU box, and a
    read-modify-write across that window would clobber a concurrent
    transcript edit or a self-heal re-index wholesale. It is also guarded on
    the pipeline_version we READ — upsert_index would re-stamp an old index
    as pipeline-current and silently cancel the backend's self-heal."""
    idx_json = index_row.get("json") or {}
    p = idx_json.get("perception")
    if isinstance(p, dict) and p.get("v") == PERCEPTION_VERSION:
        return p
    p = analyze_audio(media_path)
    try:
        worker_db.run(dbx.set_index_perception, index_row["video_sha256"],
                      p, index_row.get("pipeline_version"))
    except Exception as e:
        print(f"[perception] sidecar persist failed (non-fatal): {e}",
              flush=True)
    return p


def get_or_compute_for_asset(worker_db, dbx, asset, media_path):
    """Perception for a music/audio asset, cached in the asset's meta."""
    meta = asset.get("meta") or {}
    p = meta.get("perception")
    if isinstance(p, dict) and p.get("v") == PERCEPTION_VERSION:
        return p
    p = analyze_audio(media_path)
    # asset meta rows are small — drop the bulky envelope for assets; beat
    # placement against a music track needs beats/bpm/energy only.
    slim = {k: v for k, v in p.items() if k not in ("vb_env", "vb_env_fps")}
    try:
        worker_db.run(dbx.update_asset_meta, asset["id"],
                      {"perception": slim})
    except Exception as e:
        print(f"[perception] asset meta persist failed (non-fatal): {e}",
              flush=True)
    return slim
