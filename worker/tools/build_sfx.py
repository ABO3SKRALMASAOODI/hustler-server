"""Synthesize the built-in SFX pack.

Why synthesis and not sourcing: these files ship inside our Docker image AND
inside customers' exported (often monetized) videos. That needs CC0 with no
attribution obligation, and the verifiably-CC0 sound pools are overwhelmingly
retro game-UI blips — wrong for modern short-form video. Whooshes, risers,
impacts and sub-drops are *made* from filtered noise sweeps and pitch-dropping
sines in the first place, so synthesizing them is not a compromise; it is how
they are produced. Everything here is original work we own outright.
"""
import numpy as np
from dsp import (SR, ar, bell, biquad, biquad_sweep, bp_cascade, dc_block,
                 declick, exp_decay, fit, grit, lufs_momentary_max, noise,
                 reverb, saw, sine, stereo, sweep, sweep_hold, write_wav)

R = lambda seed: np.random.default_rng(seed)


def _n(sec):
    return int(sec * SR)


# --------------------------------------------------------------- UI / accents
def click():
    n, g = _n(0.055), R(11)
    tr = biquad(noise(n, g), "hp", 2400) * exp_decay(n, 0.005)
    _, ph = sweep(n, 3400, 2200)
    ping = sine(ph) * exp_decay(n, 0.008) * 0.5
    return stereo(tr * 1.0 + ping, width=0.2)


def tick():
    n, g = _n(0.042), R(12)
    x = biquad(noise(n, g), "bp", 3800, q=2.2) * exp_decay(n, 0.0035)
    return stereo(x, width=0.15)


def pop():
    n, g = _n(0.14), R(13)
    _, ph = sweep_hold(n, _n(0.06), 1150, 170)
    body = sine(ph) * exp_decay(n, 0.032)
    tr = biquad(noise(n, g), "hp", 3000) * exp_decay(n, 0.0035) * 0.6
    return stereo(body + tr, width=0.25)


def shutter():
    """The weakest item in the pack, and the one category that genuinely wants
    a recording: a real shutter is mirror slap -> curtain travel -> aperture ->
    spring return, and its realism lives almost entirely in the micro-timing
    between those four events. Four staggered sub-events at irregular offsets
    is as close as synthesis gets."""
    n, g = _n(0.24), R(14)

    def at(off, sig):
        o = _n(off)
        return np.concatenate([np.zeros(o), sig])[:n]

    mirror = biquad(noise(n, g), "bp", 3200, q=1.5) * exp_decay(n, 0.010)
    curtain = at(0.021, biquad(noise(n, g), "bp", 1900, q=1.1)
                 * exp_decay(n, 0.020) * 0.75)
    aperture = at(0.068, biquad(noise(n, g), "bp", 1350, q=1.4)
                  * exp_decay(n, 0.026) * 0.85)
    spring = at(0.094, biquad(noise(n, g), "bp", 2600, q=2.0)
                * exp_decay(n, 0.045) * 0.35)
    thunk = biquad(noise(n, g), "lp", 320) * exp_decay(n, 0.05) * 0.7
    return stereo(mirror + curtain + aperture + spring + thunk, width=0.3)


# ------------------------------------------------------------------ transitions
def _whoosh(dur, f_lo, f_peak, f_end, q, peak, sharp, seed, lp=None):
    n, g = _n(dur), R(seed)
    half = n // 2
    f = np.concatenate([np.geomspace(f_lo, f_peak, half),
                        np.geomspace(f_peak, f_end, n - half)])
    env = bell(n, peak=peak, sharp=sharp)
    chans = []
    for ch in range(2):                       # decorrelated noise = real width
        x = bp_cascade(noise(n, R(seed + 100 + ch)), f, q=q)
        if lp:
            x = biquad(x, "lp", lp)
        chans.append(x * env)
    return np.stack(chans, axis=1)


def whoosh():
    return _whoosh(0.72, 380, 6200, 850, 1.1, 0.55, 2.2, 21)


def whoosh_deep():
    return _whoosh(1.05, 110, 3000, 240, 0.9, 0.6, 2.0, 22)


def swipe():
    return _whoosh(0.3, 900, 7200, 1100, 1.2, 0.42, 2.6, 23)


def whoosh_reverse():
    """Amplitude ramps UP to a hard stop — the 'suck in' before a cut."""
    n, g = _n(0.55), R(24)
    f = np.geomspace(5200, 900, n)
    env = np.linspace(0, 1, n) ** 2.4
    chans = [bp_cascade(noise(n, R(24 + 100 + c)), f, q=1.0) * env
             for c in range(2)]
    return np.stack(chans, axis=1)


def glitch():
    """Chopped, gated, part-reversed noise+tone — digital stutter."""
    n, g = _n(0.45), R(25)
    _, ph = sweep(n, 900, 640, curve="lin")
    base = sine(ph) * 0.6 + biquad(noise(n, g), "bp", 2600, q=1.1) * 0.8
    out = np.zeros(n)
    i = 0
    while i < n:
        blk = int(g.integers(_n(0.012), _n(0.05)))
        e = min(i + blk, n)
        seg = base[i:e]
        r = g.random()
        if r < 0.22:
            seg = np.zeros_like(seg)                      # dropout
        elif r < 0.45:
            seg = seg[::-1]                               # reversed block
        elif r < 0.62:
            seg = seg * 2.0                               # stab
        out[i:e] = seg
        i = e
    out *= ar(n, 0.002, 0.06)
    return stereo(out, width=0.5)


# ---------------------------------------------------------------- impacts
def impact():
    n, g = _n(1.3), R(31)
    _, ph = sweep_hold(n, _n(0.16), 130, 42)
    sub = sine(ph) * exp_decay(n, 0.22)
    tr = biquad(noise(n, g), "hp", 1200) * exp_decay(n, 0.045) * 0.55
    body = biquad(noise(n, g), "lp", 420) * exp_decay(n, 0.13) * 0.7
    x = reverb(sub + tr + body + grit(n, g, 1100, 2500, 0.10) * 0.5,
               g, tail=0.5, mix=0.22)
    return stereo(x, width=0.35)


def impact_soft():
    n, g = _n(0.85), R(32)
    _, ph = sweep_hold(n, _n(0.13), 110, 48)
    sub = sine(ph) * exp_decay(n, 0.16)
    body = biquad(noise(n, g), "lp", 500) * exp_decay(n, 0.08) * 0.45
    return stereo(reverb(sub + body + grit(n, g, 500, 2200, 0.06) * 0.25,
                         g, tail=0.35, mix=0.15), width=0.3)


def boom():
    n, g = _n(2.4), R(33)
    _, ph = sweep_hold(n, _n(0.28), 82, 28)
    sub = sine(ph) * exp_decay(n, 0.55)
    body = biquad(noise(n, g), "lp", 700) * exp_decay(n, 0.2) * 0.5
    tr = biquad(noise(n, g), "hp", 900) * exp_decay(n, 0.03) * 0.3
    return stereo(reverb(sub + body + tr + grit(n, g, 1600, 2000, 0.22) * 0.6,
                         g, tail=1.2, mix=0.4), width=0.45)


def sub_drop():
    n = _n(1.3)
    _, ph = sweep(n, 200, 28)
    x = sine(ph) * ar(n, 0.008, 0.5) * exp_decay(n, 0.45)
    return stereo(x, width=0.0)               # bass stays centred


def zap():
    """The 'shock'. Ring-modulated saw with a hard pitch collapse."""
    n, g = _n(0.32), R(34)
    f, ph = sweep(n, 2400, 180)
    _, mod = sweep(n, 58, 44)
    body = saw(ph) * (0.55 + 0.45 * sine(mod))
    body = biquad_sweep(body, "lp", f * 3.0, q=1.4)
    crackle = biquad(noise(n, g), "hp", 3500) * exp_decay(n, 0.02) * 0.35
    return stereo((body * ar(n, 0.001, 0.12) + crackle), width=0.4)


# ---------------------------------------------------------------- risers/alerts
def riser():
    n, g = _n(2.6), R(41)
    f = np.geomspace(300, 9000, n)
    nz = bp_cascade(noise(n, g), f, q=1.1)
    _, ph = sweep(n, 220, 1800)
    tone = sine(ph) * 0.35
    env = np.linspace(0, 1, n) ** 2.2
    return stereo((nz + tone) * env, width=0.6)


def _bell_tone(n, f0, decay, amps=(1.0, 0.55, 0.33, 0.2),
               ratios=(1.0, 2.76, 5.40, 8.93), decays=(1.0, 0.55, 0.35, 0.24)):
    out = np.zeros(n)
    for a, r, d in zip(amps, ratios, decays):
        _, ph = sweep(n, f0 * r, f0 * r)
        out += a * sine(ph) * exp_decay(n, decay * d)
    return out * ar(n, 0.002, 0.02)


def ding():
    n = _n(1.3)
    return stereo(_bell_tone(n, 1046.5, 0.34), width=0.3)


def chime_up():
    n = _n(1.0)
    a = _bell_tone(n, 880.0, 0.26)
    b2 = np.concatenate([np.zeros(_n(0.11)), _bell_tone(n, 1318.5, 0.3)])[:n]
    return stereo(a * 0.8 + b2, width=0.35)


def buzz():
    n, g = _n(0.5), R(42)
    _, ph = sweep(n, 110, 104)
    sq = np.tanh(saw(ph) * 4.0)
    gate = np.zeros(n)
    for st in (0.0, 0.26):
        s, e = _n(st), _n(st + 0.18)
        gate[s:e] = ar(e - s, 0.004, 0.05)
    return stereo(biquad(sq * gate, "lp", 2200), width=0.2)


BANK = [
    # slug,              fn,              category,     title,                 tags
    ("click",         click,         "ui",         "Click",              "click tap ui select"),
    ("tick",          tick,          "ui",         "Tick",               "tick typewriter counter"),
    ("pop",           pop,           "ui",         "Pop",                "pop bubble text-appear"),
    ("shutter",       shutter,       "ui",         "Camera Shutter",     "shutter photo snapshot"),
    ("whoosh",        whoosh,        "transition", "Whoosh",             "whoosh swipe transition cut"),
    ("whoosh-deep",   whoosh_deep,   "transition", "Deep Whoosh",        "whoosh heavy transition cinematic"),
    ("swipe",         swipe,         "transition", "Swipe",              "swipe fast short transition"),
    ("whoosh-reverse", whoosh_reverse, "transition", "Reverse Whoosh",   "reverse suck pre-cut build"),
    ("glitch",        glitch,        "transition", "Glitch",             "glitch stutter digital datamosh"),
    ("impact",        impact,        "impact",     "Impact",             "impact hit shock emphasis"),
    ("impact-soft",   impact_soft,   "impact",     "Soft Impact",        "impact subtle thud gentle"),
    ("boom",          boom,          "impact",     "Cinematic Boom",     "boom braam trailer huge shock"),
    ("sub-drop",      sub_drop,      "impact",     "Sub Drop",           "bass drop sub emphasis"),
    ("zap",           zap,           "impact",     "Zap",                "zap electric shock zing"),
    ("riser",         riser,         "riser",      "Riser",              "riser build tension reveal"),
    ("ding",          ding,          "alert",      "Ding",               "ding bell notification correct"),
    ("chime-up",      chime_up,      "alert",      "Chime Up",           "chime success positive unlock"),
    ("buzz",          buzz,          "alert",      "Buzz",               "buzz error wrong negative"),
]

if __name__ == "__main__":
    import os
    # worker/sfx — one level up from this tools/ directory.
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "sfx")
    os.makedirs(out, exist_ok=True)
    TARGET_LUFS, CEILING_DB = -16.0, -1.0
    manifest, capped = [], []
    for slug, fn, cat, title, tags in BANK:
        y = np.asarray(fn(), dtype=float)
        for ch in range(y.shape[1]):
            y[:, ch] = dc_block(y[:, ch])
        y = declick(y)
        # Loudness first, THEN a peak ceiling. Peak-normalizing alone left a
        # 17.8 dB spread in perceived loudness across the pack, which makes a
        # single default gain_db meaningless — the exact failure the music
        # library hit before two-pass loudnorm.
        y *= 10 ** ((TARGET_LUFS - lufs_momentary_max(y)) / 20.0)
        peak = np.abs(y).max()
        ceil = 10 ** (CEILING_DB / 20.0)
        if peak > ceil:
            y *= ceil / peak
            capped.append(slug)
        lufs = lufs_momentary_max(y)
        write_wav(os.path.join(out, slug + ".wav"), y)
        manifest.append({"slug": slug, "title": title, "category": cat,
                         "duration_s": round(len(y) / SR, 3),
                         "file": slug + ".wav", "tags": tags,
                         "license": "CC0-1.0",
                         "author": "Valmera (synthesized)",
                         "source_url": "worker/tools/build_sfx.py"})
        print(f"  {slug:16s} {cat:11s} {len(y)/SR:5.2f}s  "
              f"{lufs:6.1f} LUFS  peak {20*np.log10(np.abs(y).max()):5.1f} dBFS")
    import json
    json.dump(manifest, open(os.path.join(out, "manifest.json"), "w"), indent=2)
    tot = sum(os.path.getsize(os.path.join(out, m["file"])) for m in manifest)
    print(f"\n{len(BANK)} sounds, {tot/1e6:.2f} MB -> {out}")
    if capped:
        print(f"peak-limited below target (expected for transients): "
              f"{', '.join(capped)}")
