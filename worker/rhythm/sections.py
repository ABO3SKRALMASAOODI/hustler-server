"""Sections from bar-synchronous self-similarity (track 2C).

Each bar (downbeat to downbeat) is summarised by its mean log-mel, chroma
and loudness; a checkerboard kernel over the bar self-similarity matrix
gives a novelty curve whose peaks are section boundaries (on bar lines, at
least MIN_BARS apart). Labels come from energy, low end and onset density:
the loudest full-bass sections are ``drop``; the section just before the
first drop that rises is ``build``; a quiet opening is ``intro``; a dip
after a drop is ``break``; a quiet close is ``outro``; the rest ``verse``.
"""

import numpy as np

from . import dsp

MIN_BARS = 4
KERNEL_BARS = 4


def bar_features(feat, downbeats, duration):
    edges = list(downbeats) + [duration]
    rows, spans = [], []
    lm, ch, rms = feat["logmel"], feat["chroma"], feat["rms_db"]
    low = np.log1p(1e4 * np.asarray(feat["bands"]["bass"], np.float64))
    for a, b in zip(edges, edges[1:]):
        i, j = int(round(a * dsp.FPS)), int(round(b * dsp.FPS))
        if j <= i + 2:
            continue
        rows.append(np.concatenate([lm[i:j].mean(axis=0) / 4.0,
                                    ch[i:j].mean(axis=0) * 2.0,
                                    [rms[i:j].mean() / 20.0,
                                     low[i:j].mean() / 4.0]]))
        spans.append((a, b, float(rms[i:j].mean()), float(low[i:j].mean())))
    return np.array(rows), spans


def novelty(X, k=KERNEL_BARS):
    n = len(X)
    if n < 2 * k + 1:
        return np.zeros(n)
    Xn = X - X.mean(axis=0)
    Xn = Xn / (np.linalg.norm(Xn, axis=1, keepdims=True) + 1e-9)
    S = Xn @ Xn.T
    g = np.arange(-k, k)
    kern = np.sign(g[:, None] + 0.5) * np.sign(g[None, :] + 0.5)
    w = np.exp(-0.5 * ((g[:, None] + 0.5) ** 2 + (g[None, :] + 0.5) ** 2)
               / (k * k))
    kern = kern * w
    nov = np.zeros(n)
    for i in range(k, n - k + 1):
        nov[i] = float((S[i - k:i + k, i - k:i + k] * kern).sum())
    nov = np.maximum(nov, 0.0)
    return nov / (nov.max() or 1.0)


def boundaries(nov, min_bars=MIN_BARS, thresh=0.25):
    cand = [i for i in range(1, len(nov) - 1)
            if nov[i] >= thresh and nov[i] >= nov[i - 1]
            and nov[i] >= nov[i + 1]]
    out = []
    for i in sorted(cand, key=lambda i: -nov[i]):
        if all(abs(i - j) >= min_bars for j in out) and i >= min_bars // 2:
            out.append(i)
    return sorted(out)


def sections(feat, downbeats, duration, onsets=()):
    """[{t0, t1, label, energy}] covering [0, duration)."""
    if len(downbeats) < 2 * MIN_BARS:
        return [{"t0": 0.0, "t1": round(duration, 3), "label": "verse",
                 "energy": 0.5}]
    X, spans = bar_features(feat, downbeats, duration)
    if not len(X):
        return []
    nov = novelty(X)
    cuts = boundaries(nov)
    starts = [0] + cuts
    ends = cuts + [len(spans)]
    secs = []
    for a, b in zip(starts, ends):
        if b <= a:
            continue
        t0 = 0.0 if a == 0 else spans[a][0]
        t1 = spans[b - 1][1] if b - 1 < len(spans) else duration
        e = float(np.mean([s[2] for s in spans[a:b]]))
        lo = float(np.mean([s[3] for s in spans[a:b]]))
        dens = sum(1 for o in onsets if t0 <= o["t"] < t1) / max(1.0,
                                                                t1 - t0)
        secs.append({"t0": round(t0, 3), "t1": round(t1, 3), "rms": e,
                     "low": lo, "dens": dens})
    if secs:
        secs[-1]["t1"] = round(duration, 3)
    _label(secs)
    for s in secs:
        for k in ("rms", "low", "dens"):
            s.pop(k, None)
    return secs


def _label(secs):
    rms = np.array([s["rms"] for s in secs])
    low = np.array([s["low"] for s in secs])
    z = lambda v: (v - v.mean()) / (v.std() + 1e-9)  # noqa: E731
    energy = 0.6 * z(rms) + 0.4 * z(low)
    e01 = (energy - energy.min()) / ((energy.max() - energy.min()) or 1.0)
    for s, e in zip(secs, e01):
        s["energy"] = round(float(e), 3)
        s["label"] = "verse"
    hi = float(np.percentile(e01, 60)) if len(e01) > 2 else 0.5
    for s, e in zip(secs, e01):
        if e >= max(hi, 0.55):
            s["label"] = "drop"
    first_drop = next((i for i, s in enumerate(secs)
                       if s["label"] == "drop"), None)
    if secs and secs[0]["label"] != "drop" and secs[0]["energy"] < 0.45:
        secs[0]["label"] = "intro"
    if first_drop and first_drop >= 1 and \
            secs[first_drop - 1]["label"] in ("verse", "intro") and \
            first_drop - 1 > 0:
        secs[first_drop - 1]["label"] = "build"
    seen_drop = False
    for s in secs:
        if s["label"] == "drop":
            seen_drop = True
        elif seen_drop and s["label"] == "verse" and s["energy"] < 0.35:
            s["label"] = "break"
    if len(secs) > 2 and secs[-1]["label"] != "drop" and \
            secs[-1]["energy"] < 0.45:
        secs[-1]["label"] = "outro"
