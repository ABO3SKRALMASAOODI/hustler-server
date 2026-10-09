"""Dialogue leveling for mastered mixes: the program's own speech brought to
one steady level BEFORE music, voiceover and sound effects are mixed onto it.

Two measured failures made this necessary. Library sound effects carry
absolute gains (sound_library manifest gain_db, chosen for speech at a normal
level), so on a quiet lecture recorded at -30 LUFS they played 5-11 dB OVER
the voice, and the final loudnorm then raised the whole mix, sfx included.
And a two-person clip jumped 4.7 LU at the speaker change (one mic hot, one
quiet), which no single master gain can fix.

How: the renderer measures the dialogue bed — the concatenated program audio
[ac], WITHOUT the user's volume automation — once with ebur128 (momentary
loudness every 100 ms on the PROGRAM clock), and this module turns that into
a slow gain curve:

  * speech only: blocks under an absolute -50 LUFS gate, or 20 LU under the
    program's own gated level, are pauses and room tone and never steer the
    gain. Across a pause the gain simply holds, so breaths and room tone are
    not pumped up;
  * a 3 s centred window (the EBU short-term length) measures the local
    speech level;
  * the gain moves the program's overall speech level fully to TARGET_LUFS,
    plus STRENGTH of each window's deviation from it — a speaker sitting
    5 LU under the other ends about 1 LU under, while an exclamation inside
    one phrase keeps its shape;
  * clamped, slew-limited both ways so it never moves faster than SLEW_DB_S,
    and decimated to knots for one ffmpeg volume expression.

Measured on the automation-free bed and applied on top of it, so a user's
"quieter here" or a muted range stays exactly that relative to the leveled
speech instead of being undone by the leveler. Deterministic: preview and
final measure the same program and apply the same curve.
"""

import math
import re

# The speech level the mix is built on (the master then adds ~6 dB). Every
# absolute gain downstream was tuned against unleveled speech: the music
# beds on a -23 LUFS voice, with the owner's podcast sources at a median of
# about -21, and the library sfx by ear. -20 keeps those relations where
# they were tuned. Measured on the three showcase shorts with the library's
# suggested gains, -18 left the sfx 6-10.5 dB under the speech peaks on two
# of them (target 3-8) and every bed ~3 dB further under a median voice;
# -20 lands all but one cue about 3-8.5 dB under.
TARGET_LUFS = -20.0
STEP_S = 0.1              # ebur128 metadata=1 emits one 100 ms block each
WINDOW_S = 3.0            # local level window, centred
ABS_GATE_LUFS = -50.0     # under this a block is silence/room tone
REL_GATE_LU = 20.0        # ...or this far under the program's speech level
STRENGTH = 0.8            # share of a window's deviation that is corrected
MIN_GAIN_DB = -12.0
MAX_GAIN_DB = 24.0
SLEW_DB_S = 4.0           # a 5 dB speaker change rides over ~1.25 s
KNOT_S = 0.5              # knot cadence before decimation
KNOT_TOL_DB = 0.25        # a knot is dropped when interpolation stays this close
MAX_KNOTS = 3000          # bounds the expression on very long programs
MIN_SPEECH_S = 1.0        # less measured speech than this: no curve at all
_MIN_WINDOW_BLOCKS = 3
_MAX_SPAN_KNOTS = 60      # a kept knot at least every 30 s bounds the scan

_PTS = re.compile(r"pts_time:\s*(-?[0-9.]+)")
_MOMENTARY = re.compile(r"lavfi\.r128\.M=(\S+)")


def probe_chain(path):
    """Filters appended to the dialogue bed for the measurement pass. The
    metadata file is quoted the way the renderer quotes subtitle paths."""
    return ("aresample=48000,ebur128=metadata=1,"
            f"ametadata=mode=print:key=lavfi.r128.M:file='{path}'")


def parse_probe(text, step=STEP_S):
    """[(window_centre_s, momentary_lufs)] from the probe's ametadata log.

    Each 100 ms block reports the 400 ms momentary window ENDING with it, so
    the window's centre is 0.2 s before the block's end. Silence (-inf/nan,
    or ebur128's -120.7 floor) becomes -120."""
    out, start = [], None
    for line in (text or "").splitlines():
        m = _PTS.search(line)
        if m:
            start = float(m.group(1))
            continue
        m = _MOMENTARY.search(line)
        if not m:
            continue
        try:
            v = float(m.group(1))
        except ValueError:
            v = -120.0
        if not math.isfinite(v):
            v = -120.0
        t0 = start if start is not None else len(out) * step
        out.append((round(t0 + step - 0.2, 3), max(-120.0, v)))
        start = None
    return out


def _lufs(values):
    return 10.0 * math.log10(sum(10.0 ** (v / 10.0) for v in values)
                             / len(values))


def _decimate(knots, tol):
    """Drop knots that linear interpolation between their kept neighbours
    reproduces within `tol` dB. Endpoints always survive."""
    if len(knots) <= 2:
        return list(knots)
    kept = [knots[0]]
    a, j = 0, 2
    while j < len(knots):
        (ta, va), (tj, vj) = knots[a], knots[j]
        ok = all(abs(knots[m][1] - (va + (vj - va) * (knots[m][0] - ta)
                                    / max(tj - ta, 1e-9))) <= tol
                 for m in range(a + 1, j))
        if ok and j - a < _MAX_SPAN_KNOTS:
            j += 1
            continue
        kept.append(knots[j - 1])
        a, j = j - 1, j + 1
    kept.append(knots[-1])
    return kept


def gain_knots(blocks, step=STEP_S):
    """[(program_s, gain_db)] knots that level the measured dialogue, or None
    when there is too little speech to measure (a silent or music-free bed
    is left alone). `blocks` is parse_probe's output."""
    if not blocks:
        return None
    ts = [t for t, _m in blocks]
    lv = [m for _t, m in blocks]
    audible = [m for m in lv if m > ABS_GATE_LUFS]
    if len(audible) * step < MIN_SPEECH_S:
        return None
    gate = max(ABS_GATE_LUFS, _lufs(audible) - REL_GATE_LU)
    speech = [m > gate for m in lv]
    if sum(speech) * step < MIN_SPEECH_S:
        return None
    prog = _lufs([m for m, s in zip(lv, speech) if s])
    base = TARGET_LUFS - prog
    n = len(lv)
    # Prefix sums over speech blocks make every centred window O(1).
    energy, count = [0.0], [0]
    for m, s in zip(lv, speech):
        energy.append(energy[-1] + (10.0 ** (m / 10.0) if s else 0.0))
        count.append(count[-1] + (1 if s else 0))
    half = max(1, int(round(WINDOW_S / 2.0 / step)))
    gains = [None] * n
    for i in range(n):
        a, b = max(0, i - half), min(n, i + half + 1)
        c = count[b] - count[a]
        if c < _MIN_WINDOW_BLOCKS:
            continue
        local = 10.0 * math.log10((energy[b] - energy[a]) / c)
        g = base + STRENGTH * (prog - local)
        gains[i] = min(MAX_GAIN_DB, max(MIN_GAIN_DB, g))
    first = next((g for g in gains if g is not None), None)
    if first is None:
        return None
    held = first
    for i in range(n):              # pauses hold the last speech gain
        if gains[i] is None:
            gains[i] = held
        else:
            held = gains[i]
    d = SLEW_DB_S * step
    for i in range(1, n):
        gains[i] = min(max(gains[i], gains[i - 1] - d), gains[i - 1] + d)
    for i in range(n - 2, -1, -1):
        gains[i] = min(max(gains[i], gains[i + 1] - d), gains[i + 1] + d)
    every = max(1, int(round(KNOT_S / step)))
    idx = list(range(0, n, every))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    knots = [(round(ts[i], 3), round(gains[i], 2)) for i in idx]
    tol = KNOT_TOL_DB
    out = _decimate(knots, tol)
    while len(out) > MAX_KNOTS:
        tol *= 2.0
        out = _decimate(knots, tol)
    return out


def gain_expr(knots, tvar="t"):
    """ffmpeg expression for the gain in dB at time `tvar`: piecewise linear
    between knots, held flat outside them. A balanced if() tree, so a long
    program's thousand knots cost ten comparisons per audio frame and never
    nest deeper than the expression parser is comfortable with."""
    if len(knots) == 1:
        return f"{knots[0][1]:.2f}"

    def seg(i):
        (t0, v0), (t1, v1) = knots[i], knots[i + 1]
        if abs(v1 - v0) < 0.005:
            return f"{v0:.2f}"
        return (f"({v0:.2f}+{v1 - v0:.3f}*({tvar}-{t0:.3f})/"
                f"{max(t1 - t0, 0.001):.3f})")

    def tree(lo, hi):
        if hi - lo == 1:
            return seg(lo)
        mid = (lo + hi) // 2
        return (f"if(lt({tvar},{knots[mid][0]:.3f}),{tree(lo, mid)},"
                f"{tree(mid, hi)})")

    return (f"if(lt({tvar},{knots[0][0]:.3f}),{knots[0][1]:.2f},"
            f"if(gte({tvar},{knots[-1][0]:.3f}),{knots[-1][1]:.2f},"
            f"{tree(0, len(knots) - 1)}))")


def volume_filter(knots):
    """The leveling filter for the dialogue bed. Float samples first: a +20 dB
    gain on an s16 bed (a PCM source on the cheap graph) would clip before
    the master's limiter ever saw it."""
    return ("aformat=sample_fmts=fltp,"
            f"volume=volume='pow(10,({gain_expr(knots)})/20)':eval=frame")
