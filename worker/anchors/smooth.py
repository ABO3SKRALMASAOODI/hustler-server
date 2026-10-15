"""Smoothing for anchor tracks: the One-Euro filter (Casiez et al. 2012).

A graphic riding a fingertip must not jitter while the hand is still and
must not lag while it moves: the One-Euro filter's cutoff rises with speed.
``smooth`` 0..1 (the anchor's knob) maps to the minimum cutoff: 0 = raw
landmarks, 1 = very steady. Pure numpy; runs on every lane.
"""

import math

import numpy as np


def _alpha(cutoff, dt):
    tau = 1.0 / (2.0 * math.pi * max(1e-6, cutoff))
    return 1.0 / (1.0 + tau / max(1e-6, dt))


def params_for(smooth):
    """(min_cutoff Hz, beta) for the anchor's ``smooth`` knob."""
    s = min(1.0, max(0.0, float(smooth)))
    # 0 -> 8 Hz (barely filtered), 0.5 -> 1.6 Hz, 1 -> 0.45 Hz
    min_cutoff = 8.0 * (0.056 ** s)
    beta = 0.6 * (1.0 - 0.6 * s) + 0.05
    return min_cutoff, beta


def one_euro(times, values, smooth=0.5, d_cutoff=1.0, scale=1.0,
             zero_phase=True):
    """Filter a (N, D) series sampled at ``times`` (seconds, increasing).
    ``scale`` is the series' natural unit (a hand's span in the same units
    as the values), so the speed term is unit-free. NaN rows pass through
    and restart the filter after them.

    ``zero_phase`` (the default): renders are offline and know the future,
    so the filter runs forward AND backward in time and the two passes are
    averaged — the causal filter's lag (a card trailing a fast finger)
    cancels, the jitter suppression stays. A live preview of a causal
    tracker could never do this."""
    if zero_phase:
        f = _one_euro(times, values, smooth, d_cutoff, scale)
        t = np.asarray(times, np.float64)
        b = _one_euro((t[-1] - t)[::-1] if len(t) else t,
                      np.asarray(values, np.float64)[::-1], smooth,
                      d_cutoff, scale)[::-1]
        return 0.5 * (f + b)
    return _one_euro(times, values, smooth, d_cutoff, scale)


def _one_euro(times, values, smooth=0.5, d_cutoff=1.0, scale=1.0):
    t = np.asarray(times, np.float64)
    x = np.asarray(values, np.float64)
    if x.ndim == 1:
        x = x[:, None]
    out = x.copy()
    if len(t) < 2:
        return out
    min_cutoff, beta = params_for(smooth)
    prev = dprev = None
    tprev = None
    for i in range(len(t)):
        xi = x[i]
        if not np.all(np.isfinite(xi)):
            prev = dprev = tprev = None
            continue
        if prev is None:
            prev, dprev, tprev = xi.copy(), np.zeros_like(xi), t[i]
            out[i] = xi
            continue
        dt = max(1e-4, t[i] - tprev)
        dx = (xi - prev) / dt
        a_d = _alpha(d_cutoff, dt)
        dhat = a_d * dx + (1.0 - a_d) * dprev
        speed = float(np.linalg.norm(dhat)) / max(1e-9, float(scale))
        cutoff = min_cutoff + beta * speed * 10.0
        a = _alpha(cutoff, dt)
        cur = a * xi + (1.0 - a) * prev
        out[i] = cur
        prev, dprev, tprev = cur, dhat, t[i]
    return out


def bridge(times, values, max_gap_s=0.35):
    """Fill NaN gaps no longer than ``max_gap_s`` by linear interpolation
    (a blink of a lost landmark); longer gaps stay NaN."""
    t = np.asarray(times, np.float64)
    x = np.asarray(values, np.float64).copy()
    if x.ndim == 1:
        x = x[:, None]
    ok = np.all(np.isfinite(x), axis=1)
    i = 0
    n = len(t)
    while i < n:
        if ok[i]:
            i += 1
            continue
        j = i
        while j < n and not ok[j]:
            j += 1
        if i > 0 and j < n and t[j] - t[i - 1] <= max_gap_s + 1e-9:
            for k in range(i, j):
                u = (t[k] - t[i - 1]) / max(1e-9, t[j] - t[i - 1])
                x[k] = x[i - 1] + (x[j] - x[i - 1]) * u
        i = j
    return x


def sample(times, values, at):
    """Linear interpolation of a (N, D) series at times ``at`` (NaN outside
    the samples or across a NaN)."""
    t = np.asarray(times, np.float64)
    x = np.asarray(values, np.float64)
    if x.ndim == 1:
        x = x[:, None]
    at = np.atleast_1d(np.asarray(at, np.float64))
    out = np.full((len(at), x.shape[1]), np.nan)
    if len(t) == 0:
        return out
    idx = np.searchsorted(t, at)
    for k, (a, i) in enumerate(zip(at, idx)):
        if i < len(t) and abs(t[i] - a) < 1e-6:
            out[k] = x[i]
        elif 0 < i < len(t):
            u = (a - t[i - 1]) / max(1e-9, t[i] - t[i - 1])
            out[k] = x[i - 1] + (x[i] - x[i - 1]) * u
        elif i == 0 and abs(t[0] - a) < 1e-6:
            out[k] = x[0]
    return out
