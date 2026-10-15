"""Speed ramps: an eased time map as contiguous speed spans (track 2D).

ROADMAP 2D "Ramps": piecewise setpts with an integrated eased time map.
The renderer already plays SOURCE-time ``speed`` spans as constant-rate
pieces (trim -> setpts/f -> concat, audio atempo pitch-preserved), so a
ramp is a plan, not a renderer change: the eased curve is quantised to at
most MAX_STEPS constant steps whose boundaries sit where the curve has
changed by equal amounts of OUTPUT time density (1/f), and each step's
factor is the exact mean of the curve over the step in the time-map sense

    factor_k = len_k / integral(ds / f(s))   over step k

so the ramp's total output length equals the continuous curve's. Steps
that round to 1.0 are left as plain footage (a span of factor 1.0 is
refused by the EDL), which is the same thing.

Monotonic by construction (every factor > 0, spans contiguous and sorted).
Frame duplication: a source at ``fps_src`` slowed to ``f`` shows a fresh
source frame every output frame only while ``f * fps_src >= fps_out``;
``floor(...)`` reports it and the planner clamps to it unless told
otherwise (interpolation is Wave 4A).
"""

import math

MAX_STEPS = 12
MIN_STEP_S = 0.2
CURVES = ("ease_in_out", "ease_in", "ease_out", "linear", "hold")
N_SAMPLES = 2000


def _ease(u, kind):
    u = min(1.0, max(0.0, u))
    if kind == "linear":
        return u
    if kind == "ease_in":
        return u * u * u
    if kind == "ease_out":
        return 1.0 - (1.0 - u) ** 3
    if kind == "hold":
        return 1.0 if u > 0 else 0.0
    # smootherstep: C2 ease in-out (no velocity jump at either end)
    return u * u * u * (u * (6.0 * u - 15.0) + 10.0)


def curve_fn(keys, kind="ease_in_out"):
    """f(u) over u in [0, 1] from keys [(u, factor)] (u ascending), eased
    between keys."""
    ks = sorted((float(u), float(f)) for u, f in keys)
    if not ks:
        raise ValueError("a ramp curve needs keys")

    def f(u):
        if u <= ks[0][0]:
            return ks[0][1]
        for (u0, f0), (u1, f1) in zip(ks, ks[1:]):
            if u <= u1:
                w = (u - u0) / max(1e-9, u1 - u0)
                return f0 + (f1 - f0) * _ease(w, kind)
        return ks[-1][1]
    return f


def swing(low=0.5, hold=0.3, kind="ease_in_out"):
    """The slow-motion swing: 1.0 -> low -> (held) -> 1.0. ``hold`` is the
    share of the window spent at the low."""
    h = min(0.8, max(0.0, float(hold)))
    a = (1.0 - h) / 2.0
    return curve_fn([(0.0, 1.0), (a, low), (a + h, low), (1.0, 1.0)], kind)


def floor(fps_src, fps_out):
    """The slowest factor that still shows a fresh source frame on every
    output frame (no duplicates)."""
    if not fps_src or not fps_out:
        return 1.0
    return min(1.0, float(fps_out) / float(fps_src))


def plan(src0, src1, curve, *, fps_src=30.0, fps_out=30.0, max_steps=None,
         min_step_s=MIN_STEP_S, min_factor=0.25, max_factor=4.0,
         prefix="rp", allow_duplicates=False):
    """Contiguous speed spans over SOURCE [src0, src1] following ``curve``
    (a callable f(u) or keys [(u, factor)]). Returns (spans, report):
    spans [{id, start, end, factor}], report {out_s, steps, dup_frames,
    floor, clamped}."""
    src0, src1 = float(src0), float(src1)
    if src1 - src0 < 2 * min_step_s:
        raise ValueError(f"a ramp needs at least {2 * min_step_s:.1f}s of "
                         "source")
    if not callable(curve):
        curve = curve_fn(curve)
    n_max = max(1, min(MAX_STEPS, int(max_steps or MAX_STEPS),
                       int((src1 - src0) / min_step_s)))
    lo_ok = floor(fps_src, fps_out)
    clamped = False

    def fac(u):
        nonlocal clamped
        v = min(max_factor, max(min_factor, float(curve(u))))
        if not allow_duplicates and v < lo_ok - 1e-9:
            clamped = True
            v = lo_ok
        return v
    # dense samples of the output density 1/f
    us = [k / N_SAMPLES for k in range(N_SAMPLES + 1)]
    dens = [1.0 / fac(u) for u in us]
    L = src1 - src0
    # cumulative variation of the density: equal-change boundaries
    var = [0.0]
    for a, b in zip(dens, dens[1:]):
        var.append(var[-1] + abs(b - a))
    total_var = var[-1]
    if total_var < 1e-9:
        cuts = [0.0, 1.0]
    else:
        cuts = [0.0]
        for k in range(1, n_max):
            target = total_var * k / n_max
            # first sample reaching the target
            i = next(i for i, v in enumerate(var) if v >= target - 1e-12)
            cuts.append(us[i])
        cuts.append(1.0)
    # merge steps shorter than min_step_s into their neighbour
    cuts = sorted(set(round(c, 6) for c in cuts))
    merged = [cuts[0]]
    for c in cuts[1:]:
        if (c - merged[-1]) * L < min_step_s - 1e-9:
            continue
        merged.append(c)
    if (1.0 - merged[-1]) * L < 1e-9:
        pass
    elif (1.0 - merged[-1]) * L < min_step_s:
        merged[-1] = 1.0
    if merged[-1] != 1.0:
        merged.append(1.0)
    # each step's exact time-map mean (trapezoid on the dense density)
    steps = []
    for a, b in zip(merged, merged[1:]):
        ia, ib = int(round(a * N_SAMPLES)), int(round(b * N_SAMPLES))
        integ = 0.0
        for i in range(ia, ib):
            integ += 0.5 * (dens[i] + dens[i + 1]) * (us[i + 1] - us[i])
        length = (b - a) * L
        out = integ * L
        steps.append((src0 + a * L, src0 + b * L, length / max(1e-9, out)))
    # snap boundaries to the EDL's 0.01 s source grid (contiguous)
    spans = []
    out_s = 0.0
    dup = 0.0
    for k, (a, b, f) in enumerate(steps):
        a, b = round(a, 2), round(b, 2)
        if b - a < min_step_s - 1e-9:
            continue
        f = round(f, 3)
        out_s += (b - a) / f
        if f * fps_src < fps_out - 1e-6:
            dup += (b - a) / f * fps_out - (b - a) * fps_src
        if abs(f - 1.0) < 0.01:
            continue                # plain footage: no span
        spans.append({"id": f"{prefix}{len(spans) + 1}", "start": a,
                      "end": b, "factor": f})
    rep = {"out_s": round(out_s, 3), "steps": len(spans),
           "dup_frames": int(round(dup)), "floor": round(lo_ok, 3),
           "clamped": clamped,
           "factors": [s["factor"] for s in spans]}
    return spans, rep


def time_map(spans, s, origin):
    """Output seconds from SOURCE ``origin`` to SOURCE ``s`` through the
    spans (identity outside them): the integrated time map."""
    out = float(s) - float(origin)
    for sp in spans:
        a = max(float(origin), float(sp["start"]))
        b = min(float(s), float(sp["end"]))
        if b > a:
            out += (b - a) * (1.0 / float(sp["factor"]) - 1.0)
    return out


def fits(edl_speed, spans):
    """Spans that overlap existing speed spans of another id (a ramp never
    silently replaces speed the user set)."""
    ids = {s["id"] for s in spans}
    clash = []
    for sp in edl_speed or []:
        if sp.get("id") in ids:
            continue
        for s in spans:
            if s["start"] < float(sp["end"]) - 1e-6 and \
                    float(sp["start"]) < s["end"] - 1e-6:
                clash.append(sp.get("id"))
                break
    return clash
