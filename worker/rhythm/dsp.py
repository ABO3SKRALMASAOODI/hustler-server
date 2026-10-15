"""Signal front end for the rhythm engine (Wave 2 track 2C).

numpy only (no librosa, no scipy: every lane runs it), streamed in chunks so
a 15-minute track never holds a full spectrogram in memory. One pass over
the decoded audio yields, per hop (HOP / SR = 11.6 ms):

  logmel    64 log-mel bands (onset envelope, timbre, sections)
  bands     energy in the drum bands: kick 30-120 Hz, snare body 150-400 Hz,
            snare/clap noise 2-6 kHz, hats 6-11 kHz, sub 30-60 Hz, bass
            40-250 Hz (the 808's home), speech 300-3400 Hz
  chroma    12 pitch classes (harmonic change, sections)
  rms_db    frame level in dBFS (gaps, drops)
  bass_hz   the dominant low-frequency peak (808 pitch)

``FRAME_S`` and ``frame_time(k)`` place a frame at its window CENTRE.
"""

import collections
import subprocess
import threading

import numpy as np

SR = 22050
NFFT = 2048
HOP = 256
FPS = SR / HOP
FRAME_S = 1.0 / FPS
CENTRE_S = (NFFT / 2) / SR
N_MELS = 64
CHUNK = 2048                       # frames per FFT block

_F = np.fft.rfftfreq(NFFT, 1.0 / SR)
BANDS = {"kick": (30.0, 120.0), "sub": (30.0, 60.0), "bass": (40.0, 250.0),
         "snare": (150.0, 400.0), "noise": (2000.0, 6000.0),
         "hat": (6000.0, 11000.0), "speech": (300.0, 3400.0)}
_BAND_MASK = {k: (_F >= a) & (_F < b) for k, (a, b) in BANDS.items()}
_LOW = (_F >= 30.0) & (_F <= 150.0)


def _mel(n_mels=N_MELS, fmin=30.0, fmax=None):
    fmax = fmax or SR / 2.0

    def hz2mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1.0)
    pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
    fb = np.zeros((n_mels, len(_F)), dtype=np.float32)
    for i in range(n_mels):
        a, c, b = pts[i], pts[i + 1], pts[i + 2]
        up = (_F - a) / max(1e-6, c - a)
        down = (b - _F) / max(1e-6, b - c)
        fb[i] = np.maximum(0.0, np.minimum(up, down))
    return fb


_MEL = _mel()


def _chroma_fb():
    fb = np.zeros((12, len(_F)), dtype=np.float32)
    ok = (_F >= 60.0) & (_F <= 5000.0)
    midi = np.zeros_like(_F)
    midi[ok] = 69.0 + 12.0 * np.log2(_F[ok] / 440.0)
    for k in np.nonzero(ok)[0]:
        pc = int(np.round(midi[k])) % 12
        fb[pc, k] = 1.0
    return fb


_CHROMA = _chroma_fb()


class AudioError(RuntimeError):
    """No decodable audio."""


def decode(path, sr=SR, max_s=900.0, start=0.0):
    """Mono float32 samples of the file's first audio stream."""
    cmd = ["ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", f"{float(start):.3f}"]
    cmd += ["-i", path, "-map", "0:a:0", "-f", "f32le", "-acodec",
            "pcm_f32le", "-ac", "1", "-ar", str(sr), "-t",
            f"{float(max_s):.3f}", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    err = collections.deque(maxlen=16)

    def drain():
        try:
            for line in proc.stderr:
                err.append(line)
        except Exception:  # noqa: BLE001
            pass
    th = threading.Thread(target=drain, daemon=True)
    th.start()
    raw = proc.stdout.read()
    proc.wait()
    th.join(timeout=5)
    y = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    if not y.size:
        raise AudioError("no decodable audio" + (
            f" ({b''.join(err).decode('utf-8', 'replace')[:160]})"
            if err else ""))
    return y


def features(y):
    """Streamed per-frame features of mono samples ``y`` at SR (dict of
    arrays, one row per HOP)."""
    y = np.asarray(y, dtype=np.float32)
    pad = np.zeros(NFFT // 2, dtype=np.float32)
    y = np.concatenate([pad, y, pad])
    n = max(0, 1 + (len(y) - NFFT) // HOP)
    win = np.hanning(NFFT).astype(np.float32)
    logmel = np.zeros((n, N_MELS), np.float32)
    chroma = np.zeros((n, 12), np.float32)
    bands = {k: np.zeros(n, np.float32) for k in BANDS}
    rms = np.zeros(n, np.float32)
    bass_hz = np.zeros(n, np.float32)
    lowf = _F[_LOW]
    view = np.lib.stride_tricks.sliding_window_view(y, NFFT)[::HOP]
    for a in range(0, n, CHUNK):
        b = min(n, a + CHUNK)
        frames = view[a:b] * win[None, :]
        spec = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
        power = spec * spec
        logmel[a:b] = np.log1p(1000.0 * (power @ _MEL.T) / NFFT)
        c = power @ _CHROMA.T
        chroma[a:b] = c / (c.sum(axis=1, keepdims=True) + 1e-9)
        for k, m in _BAND_MASK.items():
            bands[k][a:b] = power[:, m].sum(axis=1) / NFFT
        rms[a:b] = np.sqrt((frames ** 2).mean(axis=1) * (1.0 / 0.375))
        low = spec[:, _LOW]
        bass_hz[a:b] = lowf[np.argmax(low, axis=1)]
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-7))
    return {"logmel": logmel, "chroma": chroma, "bands": bands,
            "rms_db": rms_db, "bass_hz": bass_hz, "n": n,
            "duration": (len(y) - NFFT) / SR}


def frame_time(k):
    """Seconds of frame ``k``'s window centre (frames are padded by NFFT/2,
    so frame k is centred on sample k * HOP)."""
    return float(k) * HOP / SR


def superflux(logmel, lag=2, max_size=3):
    """Onset strength: positive log-mel difference against a frequency-max-
    filtered spectrum ``lag`` frames back (SuperFlux: vibrato-robust)."""
    if len(logmel) <= lag:
        return np.zeros(len(logmel), np.float32)
    ref = logmel.copy()
    if max_size > 1:
        r = max_size // 2
        padded = np.pad(logmel, ((0, 0), (r, r)), mode="edge")
        ref = np.max(np.stack([padded[:, i:i + logmel.shape[1]]
                               for i in range(max_size)]), axis=0)
    d = np.zeros(len(logmel), np.float32)
    d[lag:] = np.maximum(logmel[lag:] - ref[:-lag], 0.0).sum(axis=1)
    return d


def band_flux(energy, lag=1):
    """Positive log-energy difference of one band (its onsets)."""
    e = np.log1p(1e4 * np.asarray(energy, np.float64))
    d = np.zeros(len(e), np.float32)
    d[lag:] = np.maximum(e[lag:] - e[:-lag], 0.0)
    return d


def detrend(env, win_s=0.5):
    w = max(3, int(round(FPS * win_s)) | 1)
    kern = np.ones(w) / w
    trend = np.convolve(env, kern, mode="same")
    out = np.maximum(env - trend, 0.0)
    if out.size:
        ceil = 3.0 * float(np.percentile(out, 99.5))
        if ceil > 0:
            out = np.minimum(out, ceil)
    return out.astype(np.float32)


def onset_peaks(env, thresh, min_gap_frames=3, window=3, replace=2.0):
    """Like ``peaks`` but ONSET-first: inside a cluster the earliest peak
    is kept unless a later one is ``replace`` times stronger (a hit's
    attack, not its decaying tail or the bass note that swells after it)."""
    cand = peaks(env, thresh, 1, window)
    out = []
    for i in cand:
        if out and i - out[-1] < min_gap_frames:
            if env[i] > replace * env[out[-1]]:
                out[-1] = i
            continue
        out.append(i)
    return out


def peaks(env, thresh, min_gap_frames=3, window=3):
    """Local maxima of ``env`` above ``thresh`` (array or scalar), at least
    ``min_gap_frames`` apart (the stronger wins). Vectorised: a sliding max
    finds the candidates, a strongest-first sweep over a sorted list keeps
    the spacing."""
    import bisect
    env = np.asarray(env, np.float64)
    n = len(env)
    if n < 3:
        return []
    th = np.broadcast_to(np.asarray(thresh, np.float64), env.shape)
    pad = np.pad(env, (window, window), mode="constant",
                 constant_values=-np.inf)
    mx = np.lib.stride_tricks.sliding_window_view(pad, 2 * window + 1).max(
        axis=1)
    cand = np.nonzero((env >= th) & (env == mx) & (env > 0))[0]
    cand = cand[(cand > 0) & (cand < n - 1)]
    out = []
    for i in cand[np.argsort(-env[cand], kind="stable")]:
        k = bisect.bisect_left(out, i)
        if (k < len(out) and out[k] - i < min_gap_frames) or \
                (k > 0 and i - out[k - 1] < min_gap_frames):
            continue
        out.insert(k, int(i))
    return out


def adaptive_threshold(env, win_s=1.0, k=1.4, floor_q=60):
    """A moving median + k x moving MAD, floored at a global percentile."""
    w = max(5, int(round(FPS * win_s)) | 1)
    n = len(env)
    if n == 0:
        return np.zeros(0)
    pad = w // 2
    e = np.pad(np.asarray(env, np.float64), (pad, pad), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(e, w)[:n]
    med = np.median(view, axis=1)
    mad = np.median(np.abs(view - med[:, None]), axis=1)
    floor = np.percentile(env, floor_q) if n else 0.0
    return np.maximum(med + k * mad, floor)
