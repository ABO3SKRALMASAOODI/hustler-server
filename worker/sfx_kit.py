"""Valmera's built-in sound-design kit: deterministic, licence-free one-shots.

Motion design without sound reads as a slideshow, and the online SFX search
is a network dependency that has failed in production (provider outages,
authentication). These sounds are SYNTHESIZED here from noise and
oscillators with fixed seeds, so they are always available, identical on
every machine, and carry no third-party licence.

Craft notes: whooshes are pink noise through a moving spectral band (STFT
shaping) with a pan sweep and a short room; pops are pitch-dropping sines
with a transient; bells are inharmonic partial stacks; impacts are pitch-
dropping sub sines plus a filtered noise crack and tail. Every kind is peak-
normalized and then trimmed to a per-kind gain so a pop never shouts over
dialogue.

``render(kind, path)`` writes a 48 kHz stereo 16-bit WAV. ``KINDS`` lists the
catalog with durations and descriptions for tools and templates.
"""

import hashlib
import math
import os
import wave

import numpy as np

SR = 48000
KIT_VERSION = "kit-1"

KINDS = {
    "whoosh_soft": (0.70, "airy pass-by for a title or card entering"),
    "whoosh_hard": (0.50, "fast, bright pass-by for a punchy transition or slam"),
    "swoosh_up": (0.55, "rising sweep into a reveal"),
    "swish_short": (0.24, "tiny high swish for small UI moves and word flicks"),
    "swipe": (0.32, "mid swipe for a panel or card sliding across"),
    "pop_soft": (0.16, "rounded pop for a word, icon or bubble appearing"),
    "pop_bright": (0.14, "brighter bubbly pop for emoji/icon pops"),
    "click_ui": (0.07, "crisp UI click for buttons, toggles, list items"),
    "tick": (0.05, "small tick for counters, typewriters, list bullets"),
    "kick": (0.45, "punchy low hit for a word slam or hard cut"),
    "ding": (1.30, "clean bell for a reveal, correct answer or result"),
    "chime": (1.60, "two-note shimmer for a positive payoff"),
    "notification": (0.55, "two-tone phone notification blip"),
    "coin": (0.80, "bright coin/cash ping for money and numbers"),
    "riser_short": (1.20, "tension build that lands on a cut or reveal"),
    "riser_long": (2.60, "long tension build into a big moment"),
    "impact_soft": (1.00, "soft cinematic thud under a reveal"),
    "impact_hard": (1.50, "heavy cinematic boom for the biggest beat"),
    "sub_drop": (1.60, "sub-bass drop after a riser or for gravity"),
    "glitch": (0.40, "digital glitch burst for a glitch transition/text"),
    "shutter": (0.28, "camera shutter for a photo/freeze moment"),
    "typing": (1.10, "keyboard typing for typewriter text or terminals"),
}

# Per-kind output peak in dBFS after normalization (mix staging).
_PEAK_DB = {
    "whoosh_soft": -9, "whoosh_hard": -7, "swoosh_up": -9, "swish_short": -12,
    "swipe": -11, "pop_soft": -10, "pop_bright": -11, "click_ui": -12,
    "tick": -14, "kick": -6, "ding": -12, "chime": -12, "notification": -12,
    "coin": -12, "riser_short": -9, "riser_long": -9, "impact_soft": -6,
    "impact_hard": -4, "sub_drop": -6, "glitch": -12, "shutter": -12,
    "typing": -15,
}


def _rng(kind):
    seed = int(hashlib.sha256((KIT_VERSION + kind).encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed)


def _t(dur):
    return np.arange(int(dur * SR)) / SR


def _pink(n, rng):
    white = rng.standard_normal(n)
    f = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    f[1:] /= np.sqrt(freqs[1:])
    f[0] = 0
    x = np.fft.irfft(f, n)
    return x / (np.max(np.abs(x)) + 1e-9)


def _band_sweep(x, f_of_t, width_oct=1.0, n_fft=1024, hop=256):
    """Shape noise through a moving log-gaussian band (center f_of_t(sec))."""
    n = len(x)
    win = np.hanning(n_fft)
    pad = np.concatenate([np.zeros(n_fft), x, np.zeros(n_fft)])
    out = np.zeros_like(pad)
    norm = np.zeros_like(pad)
    freqs = np.fft.rfftfreq(n_fft, 1 / SR)
    logf = np.log2(np.maximum(freqs, 20.0))
    for start in range(0, len(pad) - n_fft, hop):
        tc = (start + n_fft / 2 - n_fft) / SR
        fc = max(40.0, float(f_of_t(max(0.0, tc))))
        g = np.exp(-0.5 * ((logf - math.log2(fc)) / (width_oct / 2.355)) ** 2)
        seg = pad[start:start + n_fft] * win
        y = np.fft.irfft(np.fft.rfft(seg) * g, n_fft) * win
        out[start:start + n_fft] += y
        norm[start:start + n_fft] += win ** 2
    out = out / np.maximum(norm, 1e-6)
    return out[n_fft:n_fft + n]


def _lowpass(x, fc):
    a = math.exp(-2 * math.pi * fc / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def _fft_filter(x, lo=None, hi=None):
    f = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    g = np.ones_like(freqs)
    if lo:
        g *= 1 / np.sqrt(1 + (lo / np.maximum(freqs, 1)) ** 4)
    if hi:
        g *= 1 / np.sqrt(1 + (freqs / hi) ** 4)
    return np.fft.irfft(f * g, len(x))


def _sine_sweep(t, f0, f1, curve=3.0):
    p = t / max(t[-1], 1e-9)
    f = f1 + (f0 - f1) * np.exp(-curve * p) if curve else f0 + (f1 - f0) * p
    phase = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(phase)


def _exp_env(t, decay):
    return np.exp(-t / max(decay, 1e-4))


def _bell_env(t, peak_frac=0.55, sharp=2.0):
    p = t / max(t[-1], 1e-9)
    up = np.clip(p / peak_frac, 0, 1) ** sharp
    down = np.clip((1 - p) / (1 - peak_frac), 0, 1) ** (sharp * 0.8)
    return np.where(p < peak_frac, up, down)


def _reverb(x, rng, size=0.6, wet=0.25, bright=6000):
    n = int(size * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal(n) * np.exp(-t / (size / 5))
    ir = _fft_filter(ir, hi=bright)
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    wet_sig = np.convolve(x, ir)
    dry = np.concatenate([x, np.zeros(len(wet_sig) - len(x))])
    return dry * (1 - wet * 0.5) + wet_sig * wet


def _stereo(mono, pan=None, width=0.0, rng=None):
    """pan: array or scalar in [-1, 1]; width decorrelates with a micro-delay."""
    if pan is None:
        pan = 0.0
    pan = np.broadcast_to(np.asarray(pan, dtype=float), mono.shape)
    left = mono * np.cos((pan + 1) * np.pi / 4)
    right = mono * np.sin((pan + 1) * np.pi / 4)
    if width:
        d = int(0.0007 * SR)
        right = (1 - width) * right + width * np.concatenate([np.zeros(d), right[:-d]])
    return np.stack([left, right], axis=1) * math.sqrt(2)


def _synth(kind):
    rng = _rng(kind)
    dur = KINDS[kind][0]
    t = _t(dur)
    n = len(t)
    if kind in ("whoosh_soft", "whoosh_hard", "swoosh_up", "swish_short", "swipe"):
        cfg = {
            "whoosh_soft": (260, 2600, 620, 1.2, 0.55, 0.30),
            "whoosh_hard": (420, 5200, 900, 1.0, 0.45, 0.22),
            "swoosh_up": (380, 7000, 7000, 1.1, 0.82, 0.18),
            "swish_short": (1800, 9000, 4000, 0.9, 0.5, 0.10),
            "swipe": (700, 3800, 1400, 1.0, 0.4, 0.15),
        }[kind]
        f0, fpk, f1, width, peak, room = cfg
        def fc(sec, f0=f0, fpk=fpk, f1=f1, peak=peak):
            p = sec / dur
            if p < peak:
                q = p / peak
                return f0 * (fpk / f0) ** (q ** 1.4)
            q = (p - peak) / max(1 - peak, 1e-6)
            return fpk * (f1 / fpk) ** (q ** 0.8)
        x = _band_sweep(_pink(n, rng), fc, width_oct=width * 1.6)
        env = _bell_env(t, peak, 2.2 if kind != "swoosh_up" else 2.8)
        if kind == "swoosh_up":
            env = np.where(t / dur > 0.92, env * np.clip((1 - t / dur) / 0.08, 0, 1), env)
        x = x * env
        if kind == "whoosh_hard":
            thump = _sine_sweep(t, 140, 55, 6) * _exp_env(np.maximum(t - dur * peak, 0), 0.07) \
                * (t >= dur * peak)
            x = x + 0.35 * thump
        pan = np.interp(t, [0, dur], [-0.7, 0.7]) if kind != "swish_short" else 0.2
        st = _stereo(x, pan, width=0.3)
        tail = _reverb(st[:, 0], rng, 0.5, room), _reverb(st[:, 1], rng, 0.5, room)
        return np.stack(tail, axis=1)[: n + int(0.15 * SR)]
    if kind in ("pop_soft", "pop_bright"):
        f0, f1, dec = (950, 260, 0.055) if kind == "pop_soft" else (1700, 650, 0.045)
        body = _sine_sweep(t, f0, f1, 9) * _exp_env(t, dec)
        if kind == "pop_bright":
            body += 0.3 * _sine_sweep(t, f0 * 2.1, f1 * 2.0, 9) * _exp_env(t, dec * 0.6)
        click = _fft_filter(rng.standard_normal(n), lo=2500, hi=9000) * _exp_env(t, 0.003)
        x = body + 0.25 * click
        x = x * np.clip(t / 0.002, 0, 1)
        return _stereo(x, 0.0, 0.15)
    if kind in ("click_ui", "tick"):
        f = 2100 if kind == "click_ui" else 3300
        dec = 0.012 if kind == "click_ui" else 0.006
        tone = np.sin(2 * np.pi * f * t) * _exp_env(t, dec)
        noise = _fft_filter(rng.standard_normal(n), lo=3000, hi=11000) * _exp_env(t, 0.0025)
        x = 0.7 * tone + 0.6 * noise
        return _stereo(x, 0.0, 0.1)
    if kind == "kick":
        body = _sine_sweep(t, 160, 48, 14) * _exp_env(t, 0.16)
        click = _fft_filter(rng.standard_normal(n), lo=1500, hi=6000) * _exp_env(t, 0.004)
        x = np.tanh(1.8 * (body + 0.2 * click))
        return _stereo(x, 0.0, 0.0)
    if kind in ("ding", "chime", "coin", "notification"):
        def bell(f0, start, dec, gain=1.0, partials=((1, 1), (2.76, .45), (5.40, .25), (8.93, .12))):
            out = np.zeros(n)
            m = t >= start
            tt = t[m] - start
            for ratio, g in partials:
                out[m] += g * np.sin(2 * np.pi * f0 * ratio * tt + rng.uniform(0, 6.28)) \
                    * _exp_env(tt, dec / (ratio ** 0.6))
            out[m] *= np.clip(tt / 0.003, 0, 1)
            return out * gain
        if kind == "ding":
            x = bell(1318.5, 0.0, 0.55) + 0.25 * bell(1318.5 * 1.003, 0.0, 0.5)
        elif kind == "chime":
            x = bell(1046.5, 0.0, 0.6) + 0.9 * bell(1568.0, 0.11, 0.7)
        elif kind == "coin":
            soft = ((1, 1), (2.0, .3), (3.0, .15))
            x = bell(1975.5, 0.0, 0.18, 0.8, soft) + bell(2637.0, 0.07, 0.45, 1.0, soft)
        else:
            soft = ((1, 1), (2.0, .2))
            x = bell(880.0, 0.0, 0.09, 1.0, soft) + bell(1318.5, 0.12, 0.16, 1.0, soft)
        st = _stereo(x, 0.0, 0.35)
        return np.stack([_reverb(st[:, 0], rng, 0.8, 0.2, 9000)[:n],
                         _reverb(st[:, 1], rng, 0.8, 0.2, 9000)[:n]], axis=1)
    if kind in ("riser_short", "riser_long"):
        noise = _band_sweep(_pink(n, rng), lambda s: 300 * (9000 / 300) ** ((s / dur) ** 1.7), 1.4)
        tone = _sine_sweep(t, 180, 1100, 0) * 0.5 + _sine_sweep(t, 181.5, 1104, 0) * 0.5
        env = (t / dur) ** 2.4
        x = (0.75 * noise + 0.35 * tone) * env
        x *= np.clip((dur - t) / 0.012, 0, 1)
        st = _stereo(x, np.sin(2 * np.pi * 0.6 * t) * 0.3, 0.4)
        return st
    if kind in ("impact_soft", "impact_hard", "sub_drop"):
        if kind == "sub_drop":
            x = _sine_sweep(t, 120, 30, 2.2) * _exp_env(t, 0.55) * np.clip(t / 0.01, 0, 1)
            return _stereo(np.tanh(1.4 * x), 0.0, 0.0)
        f0, f1, dec, crack, room = (95, 42, 0.32, 0.35, 0.22) if kind == "impact_soft" \
            else (80, 32, 0.55, 0.6, 0.35)
        body = _sine_sweep(t, f0, f1, 5) * _exp_env(t, dec)
        nz = _fft_filter(rng.standard_normal(n), lo=60, hi=3500 if kind == "impact_soft" else 6000)
        x = np.tanh(2.0 * body) + crack * nz * _exp_env(t, 0.05)
        x *= np.clip(t / 0.002, 0, 1)
        st = _stereo(x, 0.0, 0.3)
        return np.stack([_reverb(st[:, 0], rng, 1.2, room, 4000)[:n],
                         _reverb(st[:, 1], rng, 1.2, room, 4000)[:n]], axis=1)
    if kind == "glitch":
        x = np.zeros(n)
        pos = 0
        while pos < n - 400:
            ln = int(rng.uniform(0.012, 0.05) * SR)
            gap = int(rng.uniform(0.0, 0.025) * SR)
            seg_t = np.arange(ln) / SR
            if rng.uniform() < 0.5:
                f = rng.uniform(200, 2400)
                seg = np.sign(np.sin(2 * np.pi * f * seg_t))
            else:
                seg = rng.standard_normal(ln)
            levels = rng.integers(3, 9)
            seg = np.round(seg * levels) / levels
            x[pos:pos + ln] = seg[: max(0, min(ln, n - pos))] * rng.uniform(0.4, 1.0)
            pos += ln + gap
        x = _fft_filter(x, lo=120, hi=9000)
        return _stereo(x, np.sign(np.sin(2 * np.pi * 9 * t)) * 0.4, 0.2)
    if kind == "shutter":
        x = np.zeros(n)
        for start, g in ((0.0, 1.0), (0.075, 0.7)):
            m = t >= start
            tt = t[m] - start
            x[m] += g * (_fft_filter(rng.standard_normal(m.sum()), lo=1800, hi=9000) * _exp_env(tt, 0.012)
                         + 0.5 * np.sin(2 * np.pi * 1200 * tt) * _exp_env(tt, 0.006))
        return _stereo(x, 0.0, 0.2)
    if kind == "typing":
        x = np.zeros(n)
        pos = 0.02
        while pos < dur - 0.06:
            m = t >= pos
            tt = t[m] - pos
            f = rng.uniform(1800, 3200)
            x[m] += rng.uniform(0.5, 1.0) * (
                _fft_filter(rng.standard_normal(m.sum()), lo=1500, hi=8000) * _exp_env(tt, 0.004)
                + 0.4 * np.sin(2 * np.pi * f * tt) * _exp_env(tt, 0.008))
            pos += rng.uniform(0.055, 0.14)
        return _stereo(x, 0.0, 0.25)
    raise KeyError(kind)


def samples(kind):
    """Stereo float array (n, 2) at SR, normalized to the kind's peak."""
    if kind not in KINDS:
        raise KeyError(f"unknown kit sound '{kind}'. Available: {', '.join(sorted(KINDS))}")
    x = _synth(kind).astype(np.float64)
    x = x - np.mean(x, axis=0, keepdims=True)
    # 4 ms fade-out guard so no kind ends on a click
    fade = min(len(x), int(0.004 * SR))
    x[-fade:] *= np.linspace(1, 0, fade)[:, None]
    peak = np.max(np.abs(x)) + 1e-9
    return x / peak * (10 ** (_PEAK_DB.get(kind, -10) / 20))


def render(kind, path):
    x = samples(kind)
    pcm = np.clip(x * 32767, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def fingerprint(kind):
    return hashlib.sha256(f"{KIT_VERSION}:{kind}".encode()).hexdigest()[:10]


def catalog():
    return [{"kind": k, "duration_s": d, "use": desc} for k, (d, desc) in KINDS.items()]


if __name__ == "__main__":  # render the whole kit for listening/review
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "sfx_kit_out"
    os.makedirs(out, exist_ok=True)
    for k in KINDS:
        render(k, os.path.join(out, f"{k}.wav"))
        print(k)
