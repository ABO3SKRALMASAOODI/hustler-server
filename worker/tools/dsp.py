"""Minimal DSP toolkit for synthesizing the SFX pack. numpy + stdlib only.

Everything here exists because the alternative — shipping third-party sound
files — carries a licence obligation we cannot discharge inside a customer's
exported MP4. Synthesized sounds are ours outright.
"""
import math
import numpy as np

SR = 48000


# ----------------------------------------------------------------- envelopes
def t(n):
    return np.arange(n) / SR


def exp_decay(n, tau):
    return np.exp(-t(n) / max(1e-5, tau))


def ar(n, attack, release, curve=2.0):
    """Attack/release envelope. Attack is linear-ish, release exponential."""
    e = np.ones(n)
    a = max(1, int(attack * SR))
    if a < n:
        e[:a] = np.linspace(0, 1, a) ** (1 / curve)
    r = max(1, int(release * SR))
    if r < n:
        e[n - r:] *= np.exp(-np.linspace(0, curve * 3, r))
    return e


def bell(n, peak=0.5, sharp=2.0):
    """Asymmetric bell — the amplitude shape of a whoosh passing the mic."""
    x = np.linspace(0, 1, n)
    p = min(max(peak, 0.02), 0.98)
    left = (x / p) ** sharp
    right = ((1 - x) / (1 - p)) ** sharp
    return np.where(x < p, left, right)


def sweep(n, f0, f1, curve="exp"):
    """Instantaneous-phase sweep. Integrating frequency (not lerping phase)
    is what keeps a pitch drop from clicking at the seams."""
    x = np.linspace(0, 1, n)
    if curve == "exp":
        f = f0 * (max(f1, 1e-3) / max(f0, 1e-3)) ** x
    else:
        f = f0 + (f1 - f0) * x
    return f, np.cumsum(2 * np.pi * f / SR)


# ------------------------------------------------------------------- sources
def noise(n, rng, kind="white"):
    w = rng.standard_normal(n)
    if kind == "pink":
        # one-pole cascade approximation; good enough and cheap
        b = np.zeros(n)
        acc = 0.0
        for i in range(n):
            acc = 0.99 * acc + w[i] * 0.1
            b[i] = acc
        return w * 0.3 + b * 3.0
    return w


def sine(phase):
    return np.sin(phase)


def saw(phase):
    return 2.0 * ((phase / (2 * np.pi)) % 1.0) - 1.0


# ------------------------------------------------------------------- filters
def _biquad_coeffs(kind, f0, q):
    f0 = min(max(f0, 20.0), SR * 0.45)
    w0 = 2 * math.pi * f0 / SR
    c, s = math.cos(w0), math.sin(w0)
    alpha = s / (2 * max(0.05, q))
    if kind == "lp":
        b0, b1, b2 = (1 - c) / 2, 1 - c, (1 - c) / 2
    elif kind == "hp":
        b0, b1, b2 = (1 + c) / 2, -(1 + c), (1 + c) / 2
    else:                                    # bp (constant peak gain)
        b0, b1, b2 = alpha, 0.0, -alpha
    a0, a1, a2 = 1 + alpha, -2 * c, 1 - alpha
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def biquad(x, kind, f0, q=0.707):
    b0, b1, b2, a1, a2 = _biquad_coeffs(kind, f0, q)
    y = np.empty_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(len(x)):
        xi = x[i]
        yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        y[i] = yi
        x2, x1 = x1, xi
        y2, y1 = y1, yi
    return y


def biquad_sweep(x, kind, f_curve, q=0.707, block=64):
    """Time-varying filter. Coefficients are recomputed per BLOCK, not per
    sample: per-sample recomputation is ~40x slower for no audible gain, and
    per-sound (one coefficient set for the whole file) is not a sweep at all."""
    y = np.empty_like(x)
    x1 = x2 = y1 = y2 = 0.0
    n = len(x)
    for s in range(0, n, block):
        e = min(s + block, n)
        b0, b1, b2, a1, a2 = _biquad_coeffs(kind, float(f_curve[(s + e) // 2]), q)
        for i in range(s, e):
            xi = x[i]
            yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            y[i] = yi
            x2, x1 = x1, xi
            y2, y1 = y1, yi
    return y


# -------------------------------------------------------------------- spatial
def reverb(x, rng, tail=0.6, mix=0.35, pre=0.01):
    """Decaying-noise convolution reverb via FFT. Direct convolution of a
    120k-sample hit with a 29k-sample tail is ~3.5e9 multiplies; FFT is
    milliseconds."""
    ln = int(tail * SR)
    ir = rng.standard_normal(ln) * np.exp(-np.linspace(0, 6, ln))
    ir[:int(pre * SR)] = 0.0
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    n = len(x) + ln - 1
    nfft = 1 << (n - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft), nfft)[:len(x)]
    return (1 - mix) * x + mix * wet


def stereo(left, right=None, width=1.0):
    if right is None:
        right = left
    m = (left + right) / 2
    s = (left - right) / 2 * width
    return np.stack([m + s, m - s], axis=1)


def fit(x, n):
    """Pad or truncate to exactly n samples."""
    if len(x) >= n:
        return x[:n]
    return np.concatenate([x, np.zeros(n - len(x))])


def declick(y, ms=3.0):
    """Force the very first and last samples to zero. A one-shot that starts
    or ends on a non-zero sample is an audible tick on top of the effect —
    and it survives every loudness check, because it IS the signal."""
    k = max(2, int(ms * SR / 1000))
    n = len(y)
    if n < 2 * k:
        k = max(1, n // 4)
    ramp = np.linspace(0, 1, k)
    y[:k] *= ramp[:, None] if y.ndim == 2 else ramp
    y[-k:] *= ramp[::-1, None] if y.ndim == 2 else ramp[::-1]
    return y


def write_wav(path, y, sr=SR):
    import wave
    y = np.clip(y, -1.0, 1.0)
    pcm = (y * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2 if y.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------- fixes found by measurement
def sweep_hold(n, n_sweep, f0, f1):
    """Sweep f0->f1 over n_sweep samples, then HOLD f1 for the rest of n.

    The obvious `fit(sine(sweep(...)), n)` zero-pads instead of holding, so the
    oscillator goes silent the moment the sweep ends while the amplitude
    envelope is still at 15%. Measured: 'pop' fell off a cliff to -180 dBFS at
    60ms — an abrupt truncation that reads as a CLICK, and 'impact' lost its
    entire sub-bass tail. Neither is visible in the code; both are obvious in
    a 10ms envelope dump.
    """
    n_sweep = max(1, min(int(n_sweep), n))
    x = np.linspace(0, 1, n_sweep)
    f = np.concatenate([f0 * (max(f1, 1e-3) / max(f0, 1e-3)) ** x,
                        np.full(n - n_sweep, f1)])
    return f, np.cumsum(2 * np.pi * f / SR)


def dc_block(x):
    """20 Hz high-pass. Lowpassed noise bursts leave a measurable DC offset
    (the camera shutter measured -0.0105), which eats headroom and can thump
    on a phone speaker."""
    return biquad(x, "hp", 20.0)


def bp_cascade(x, f_curve, q=0.9, stages=3):
    """Cascaded sweeping band-pass.

    A single 2-pole band-pass over white noise does NOT make a whoosh: its
    skirts fall only 6 dB/octave, so the untouched high end dominates and the
    measured spectral centroid barely moves (7004 -> 8339 -> 6965 Hz for a
    filter sweeping 380 -> 6200 -> 850). It sounds like tinted noise. Three
    cascaded stages give ~18 dB/octave and the sweep becomes the sound.
    """
    for _ in range(stages):
        x = biquad_sweep(x, "bp", f_curve, q=q)
    return x


def grit(n, rng, density=900.0, hp=2500.0, decay=0.09):
    """Sparse random impulses — the irregular, non-repeating high-frequency
    debris of a real slam. Filtered noise is statistically smooth and lands
    'clean' in a slightly video-gamey way; scattered transients do not."""
    x = np.zeros(n)
    k = max(1, int(density * n / SR))
    pos = rng.integers(0, n, k)
    x[pos] = rng.standard_normal(k) * rng.random(k) ** 2
    return biquad(x, "hp", hp) * exp_decay(n, decay)


# --------------------------------------------------------- ITU-R BS.1770 LUFS
_K1_B = np.array([1.53512485958697, -2.69169618940638, 1.19839281085285])
_K1_A = np.array([1.0, -1.69065929318241, 0.73248077421585])
_K2_B = np.array([1.0, -2.0, 1.0])
_K2_A = np.array([1.0, -1.99004745483398, 0.99007225036621])


def _lfilter(b, a, x):
    y = np.empty_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(len(x)):
        xi = x[i]
        yi = b[0] * xi + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
        y[i] = yi
        x2, x1 = x1, xi
        y2, y1 = y1, yi
    return y


def lufs_momentary_max(y):
    """Max momentary (400 ms) loudness, ITU-R BS.1770 K-weighting.

    This, not peak and not full-file RMS, is the right yardstick for one-shots:
    400 ms is the ear's loudness-integration window, so it scores a 40 ms tick
    and a 2.4 s boom on the same perceptual scale. Peak-normalizing instead
    left the pack with a 17.8 dB spread in actual loudness.
    """
    if y.ndim == 1:
        y = y[:, None]
    win = int(0.4 * SR)
    if len(y) < win:
        y = np.concatenate([y, np.zeros((win - len(y), y.shape[1]))])
    z = np.zeros(len(y))
    for ch in range(y.shape[1]):
        f = _lfilter(_K2_B, _K2_A, _lfilter(_K1_B, _K1_A, y[:, ch]))
        z += f ** 2
    c = np.concatenate([[0.0], np.cumsum(z)])
    ms = (c[win:] - c[:-win]) / win
    return -0.691 + 10 * np.log10(max(ms.max(), 1e-12))
