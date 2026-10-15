"""Gaps, drops and stutters (track 2C).

gaps    stretches where the track goes quiet: at least GAP_MIN_S below
        -40 dBFS, or a dip GAP_DIP_DB below the surrounding second (a
        pre-drop "suck-out" is rarely digital silence), measured on a 5 ms
        RMS so edges land within +/-10 ms
drops   a bar line where the low end comes back hard (+DROP_RISE_DB over the
        bar before, kick present after) — after a gap, a filtered build or
        a bass-less intro — snapped to the nearest downbeat
stutters  short windows whose 5 ms envelope repeats at a 1/8 or 1/16-beat
        period (slices of the same audio)
"""

import numpy as np

from . import dsp

WIN_S = 0.005
# 5 ms at 22050 Hz is 110.25 samples: windows are 110 samples long and
# every time below is computed with their EXACT length (a nominal 5 ms
# drifts 52 ms by the 21st second)
STEP_S = round(WIN_S * dsp.SR) / float(dsp.SR)
GAP_MIN_S = 0.05
GAP_DIP_DB = 18.0
GAP_ABS_DB = -40.0
DROP_RISE_DB = 9.0


def short_rms_db(y, sr=dsp.SR, win_s=WIN_S):
    n = max(1, int(round(win_s * sr)))
    m = len(y) // n
    if m == 0:
        return np.zeros(0)
    x = np.asarray(y[:m * n], np.float64).reshape(m, n)
    return 20.0 * np.log10(np.sqrt((x * x).mean(axis=1)) + 1e-9)


def gaps(y, sr=dsp.SR):
    r = short_rms_db(y, sr)
    WIN_S = round(globals()["WIN_S"] * sr) / float(sr)   # the exact step
    if not r.size:
        return []
    ctx = int(round(1.0 / WIN_S))
    pad = np.pad(r, (ctx, ctx), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(pad, 2 * ctx + 1)[:len(r)]
    local = np.percentile(view[:, ::4], 70, axis=1)
    quiet = (r < GAP_ABS_DB) | (r < local - GAP_DIP_DB)
    out, i, n = [], 0, len(r)
    min_n = int(round(GAP_MIN_S / WIN_S))
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        if j - i >= min_n:
            # the edges where the level crosses halfway (in dB) between the
            # music around the gap and its floor: a fade into a gap starts
            # where it is heard to, not where a fixed threshold says
            floor = float(r[i:j].min())
            span = int(round(0.3 / WIN_S))
            around = np.concatenate([r[max(0, i - span):i], r[j:j + span]])
            sur = float(np.median(around)) if around.size else floor + 20.0
            thr = (floor + sur) / 2.0
            a, b = i, j
            while a > 0 and r[a - 1] < thr and i - a < span:
                a -= 1
            while a < b and r[a] >= thr:
                a += 1
            while b < n and r[b] < thr and b - j < span:
                b += 1
            while b > a and r[b - 1] >= thr:
                b -= 1
            if b - a >= min_n:
                out.append({"t0": round(a * WIN_S, 3),
                            "t1": round(b * WIN_S, 3),
                            "floor_db": round(floor, 1)})
        i = j
    # the first/last stretch of digital silence of a file is no musical gap
    dur = n * WIN_S
    return [g for g in out if g["t0"] > 0.05 and g["t1"] < dur - 0.05]


def drops(feat, downbeats, sections, gap_list=(), bpm=None):
    """[t]: downbeats where the low end comes back hard."""
    return [t for t, _r in drops_with_rise(feat, downbeats, sections,
                                           gap_list, bpm)]


def drops_with_rise(feat, downbeats, sections, gap_list=(), bpm=None):
    """[(t, rise_db)]: downbeats where the low end comes back hard."""
    if len(downbeats) < 4:
        return []
    low = 10.0 * np.log10(np.maximum(
        np.asarray(feat["bands"]["kick"], np.float64) +
        np.asarray(feat["bands"]["sub"], np.float64), 1e-12))
    bar = float(np.median(np.diff(downbeats)))
    out = []
    for t in downbeats[1:]:
        i = int(round(t * dsp.FPS))
        w = int(round(bar * dsp.FPS))
        before = low[max(0, i - w):i]
        after = low[i:i + w]
        if len(before) < w // 2 or len(after) < w // 2:
            continue
        rise = float(np.percentile(after, 75) - np.percentile(before, 75))
        if rise >= DROP_RISE_DB:
            out.append((t, rise))
    # silence, then the drop: the music's return after a held gap IS the
    # drop moment (02: 12.15-13.67 then the hit), on the grid or not — the
    # return's first strong onset, preferred over a grid line nearby
    full = dsp.superflux(feat["logmel"])
    for g in gap_list or ():
        if g["t1"] - g["t0"] < 0.12:
            continue
        i = int(round(g["t1"] * dsp.FPS))
        w = int(round(bar * dsp.FPS))
        j = i + int(np.argmax(full[i:i + int(0.12 * dsp.FPS) + 1])) \
            if i < len(full) else i
        t = dsp.frame_time(j)
        after = low[j:j + w]
        if len(after) < 4:
            continue
        rise = float(np.percentile(after, 75) -
                     np.percentile(low[max(0, i - w):max(1, i)], 75))
        # a grid drop INSIDE the gap is no drop; one just after the gap's
        # end (a pickup, then the downbeat: 21.90 -> 22.19) is the drop
        beat = bar / 4.0
        out = [o for o in out if not (g["t0"] <= o[0] < g["t1"] - 0.02)]
        if any(g["t1"] - 0.05 <= o[0] <= g["t1"] + 1.1 * beat for o in out):
            continue
        if rise >= DROP_RISE_DB:
            out.append((t, rise))
    out.sort()
    # one drop per section start at most (the biggest rise)
    keep = []
    for t, rise in out:
        if keep and t - keep[-1][0] < 4 * bar:
            if rise > keep[-1][1]:
                keep[-1] = (t, rise)
            continue
        keep.append((t, rise))
    return [(round(t, 3), round(r, 1)) for t, r in keep]


def stutters(y, beats, sr=dsp.SR):
    """[{t0, t1, period}] where the envelope repeats at 1/8 or 1/16 beat."""
    if len(beats) < 4:
        return []
    r = short_rms_db(y, sr)
    WIN_S = round(globals()["WIN_S"] * sr) / float(sr)   # the exact step
    period = float(np.median(np.diff(beats)))
    out = []
    for a, b in zip(beats, beats[1:]):
        i, j = int(a / WIN_S), int(b / WIN_S)
        seg = r[i:j]
        if len(seg) < 16:
            continue
        seg = seg - seg.mean()
        if seg.std() < 3.0:
            continue
        ac = np.correlate(seg, seg, "full")[len(seg) - 1:]
        ac = ac / (ac[0] or 1.0)
        for div in (8, 16):
            lag = int(round(period / (div / 4) / WIN_S))
            if 2 <= lag < len(ac) and ac[lag] > 0.75 and \
                    (2 * lag >= len(ac) or ac[2 * lag] > 0.6):
                out.append({"t0": round(a, 3), "t1": round(b, 3),
                            "period": round(lag * WIN_S, 4)})
                break
    merged = []
    for s in out:
        if merged and abs(s["t0"] - merged[-1]["t1"]) < 1e-3 and \
                s["period"] == merged[-1]["period"]:
            merged[-1]["t1"] = s["t1"]
        else:
            merged.append(dict(s))
    return merged


def openings(feat, downbeats, rise_db=10.0, beats=None):
    """[t]: downbeats where the top end opens (a low-pass intro opening on
    the drop: 15@6.7, 18@3.64): the 2-11 kHz band rises by ``rise_db`` over
    the bar before."""
    if len(downbeats) < 2:
        return []
    hi = 10.0 * np.log10(np.maximum(
        np.asarray(feat["bands"]["noise"], np.float64) +
        np.asarray(feat["bands"]["hat"], np.float64), 1e-12))
    bar = float(np.median(np.diff(downbeats)))
    w = int(round(bar * dsp.FPS))
    cands = []
    for t in (beats or downbeats)[1:]:
        i = int(round(t * dsp.FPS))
        if i - w < 0 or i + w // 2 > len(hi):
            continue
        rise = float(np.percentile(hi[i:i + w // 2], 75) -
                     np.percentile(hi[i - w:i], 75))
        if rise >= rise_db:
            cands.append((rise, t))
    out = []
    for rise, t in sorted(cands, reverse=True):     # the biggest per 2 bars
        if all(abs(t - u) >= 2 * bar for u in out):
            out.append(t)
    return sorted(round(t, 3) for t in out)
