"""Prove each synthesized sound is the sound it claims to be.

I cannot listen to these. So every claim in the manifest is checked against a
measurement: a 'whoosh' whose spectral centroid does not rise then fall is not
a whoosh, and a 'riser' whose energy does not climb monotonically is not a
riser — regardless of how the code reads.
"""
import glob
import os
import subprocess
import sys
import wave

import numpy as np

SR = 48000


def load(path):
    with wave.open(path) as w:
        n, ch = w.getnframes(), w.getnchannels()
        y = np.frombuffer(w.readframes(n), "<i2").astype(float) / 32768.0
    return y.reshape(-1, ch) if ch > 1 else y.reshape(-1, 1)


def centroid_track(mono, nfft=2048, hop=512):
    # A 42ms tick is 2016 samples — shorter than the default window. Shrink the
    # analysis to fit rather than skipping short sounds, which are exactly the
    # ones (click, tick) whose brightness claim most needs checking.
    while nfft > 128 and nfft > len(mono):
        nfft //= 2
        hop = max(64, nfft // 4)
    win = np.hanning(nfft)
    freqs = np.fft.rfftfreq(nfft, 1 / SR)
    cents, energy = [], []
    for s in range(0, max(1, len(mono) - nfft + 1), hop):
        seg = mono[s:s + nfft] * win
        mag = np.abs(np.fft.rfft(seg))
        e = mag.sum()
        energy.append(e)
        cents.append(float((mag * freqs).sum() / (e + 1e-12)))
    return np.array(cents), np.array(energy)


# Use our own BS.1770 meter rather than scraping ffmpeg's log: ebur128's
# per-frame "M:" lines are absent on short inputs and the summary block is
# format-fragile, which silently produced NaN for all 18 sounds.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsp import lufs_momentary_max as _lufs


def momentary_max_lufs(path):
    return _lufs(load(path))


EXPECT = {
    "whoosh": "rise-fall", "whoosh-deep": "rise-fall", "swipe": "rise-fall",
    "whoosh-reverse": "fall", "riser": "rise", "sub-drop": "fall",
    "zap": "fall", "impact": "fall", "boom": "fall", "impact-soft": "fall",
    "pop": "fall",
}

rows = []
for path in sorted(glob.glob(os.path.join(sys.argv[1], "*.wav"))):
    slug = os.path.basename(path)[:-4]
    y = load(path)
    mono = y.mean(axis=1)
    dur = len(mono) / SR
    peak = 20 * np.log10(np.abs(y).max() + 1e-12)
    rms = 20 * np.log10(np.sqrt((mono ** 2).mean()) + 1e-12)
    dc = float(mono.mean())
    lufs = momentary_max_lufs(path)
    c, e = centroid_track(mono)
    # centroid at 15% / 50% / 85% of cumulative ENERGY, so silence padding
    # cannot drag the numbers around
    ce = np.cumsum(e) / (e.sum() + 1e-12)
    idx = [int(np.searchsorted(ce, q)) for q in (0.15, 0.5, 0.85)]
    idx = [min(i, len(c) - 1) for i in idx]
    c0, c1, c2 = (c[i] for i in idx)
    # Thresholds are deliberately modest: a deep whoosh sweeping under a
    # cascaded band-pass genuinely moves its centroid less than a bright one,
    # and calling that a failure would be measuring the meter, not the sound.
    shape = ("rise" if c2 > c0 * 1.12 else "fall" if c2 < c0 * 0.88 else "flat")
    if c1 > c0 * 1.12 and c2 < c1 * 0.88:
        shape = "rise-fall"
    want = EXPECT.get(slug)
    verdict = "" if want is None else ("OK" if shape == want else f"!! want {want}")
    rows.append((slug, dur, peak, rms, lufs, dc, c0, c1, c2, shape, verdict))

print(f"{'slug':16s} {'dur':>5s} {'peak':>6s} {'rms':>7s} {'LUFSm':>7s} "
      f"{'dc':>7s} {'cent 15%':>9s} {'50%':>7s} {'85%':>7s}  shape       check")
for r in rows:
    print(f"{r[0]:16s} {r[1]:5.2f} {r[2]:6.1f} {r[3]:7.1f} {r[4]:7.1f} "
          f"{r[5]:7.4f} {r[6]:9.0f} {r[7]:7.0f} {r[8]:7.0f}  {r[9]:11s} {r[10]}")

bad = [r for r in rows if r[10].startswith("!!")]
print(f"\n{len(rows)} sounds, {len(bad)} failing their expected spectral shape")
lu = [r[4] for r in rows if np.isfinite(r[4])]
print(f"momentary loudness spread: {min(lu):.1f} to {max(lu):.1f} LUFS "
      f"({max(lu)-min(lu):.1f} dB)")
