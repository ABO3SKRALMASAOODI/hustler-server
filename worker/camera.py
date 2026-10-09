"""The camera: eased zoom curves and the sub-pixel geometry filter.

WHY THIS MODULE EXISTS

The shared camera used to be ffmpeg's `zoompan`. zoompan crops on INTEGER
pixel offsets (rounded down to even on yuv420p) with an integer window size
and then rescales, so a slow push cannot move smoothly: the crop's origin
steps 2 px while its size steps 1 px. Measured on a 0.10 push over 4 s at
1080x1920 (render-primitives audit, exp5): a tracked edge moved BACKWARDS on
50 of 119 frames and froze on 21 more — the shimmer viewers read as cheap.

`perspective` (sense=source, interpolation=cubic) resamples from a projective
map at 1/256-px precision: the same push measures 0 backward steps and ~12x
lower jerk, at zoompan's exact sharpness (cubic). The map is a general quad,
so ROLL and SHAKE are the same filter (no second geometry stage), and a frame
at zoom 1 is an exact copy (cubic tap weights at sub-pixel 0 are [0,1,0,0]).

THE CURVES

Every camera move is `strength * envelope(t)` added to zoom 1, the envelope
being one of:

  punch     fast expo-out attack (default 0.15 s), hold, hard step out at the
            window end (a cut back to the wide — the "second camera" grammar).
            `overshoot` swaps the attack for a back-out that passes the target
            and settles (a slam). ramp_s=0 is the old instant step.
  ease      smootherstep (C2) ramps in and out inside the window.
  push_in   slow continuous push, a soft (C1) start then constant speed.
  pull_out  the mirror: starts pushed in, glides out and lands softly.
  landing   starts pushed in by `strength` and settles to 1.0 with a quartic
            ease-out over ~0.35 s — momentum through a cut.
  pulse     1.0 -> 1+strength -> 1.0 over ~0.3 s (cubic attack, smooth
            release) — a beat/word thump.
  shake     smooth value noise (2 octaves, seeded per item — never a sine)
            with a fast attack and exponential decay. `shake` > 0 adds the
            same noise to any other mode (a punch that lands as an impact).

`rotate` (degrees, + = clockwise on screen) rides the same envelope as the
zoom. Roll and shake need picture outside the frame, so the camera zooms in
JUST enough to cover them (never more), and the window centre is clamped so
an edge is never smeared.

ONE IMPLEMENTATION, TWO EVALUATORS

The curve code is written once against `X`, a tiny symbolic type: run on an
X time variable it produces the ffmpeg expression text; run on a float it IS
the python mirror (zoom_state_at, stitch's proof clipping, the static-hold
planner below, the tests). They cannot drift: there is one copy of the
arithmetic.

COST

perspective costs real CPU where zoompan was nearly free: ~3 ms/frame to
rebuild its sampling map (single-threaded, only when the expression is
evaluated per frame) plus the slice-threaded cubic resample, plus parsing
the eight corner expressions every frame. So the camera never runs where
nothing moves: shots are clustered by time and each cluster is its own
filter, bypassed (timeline `enable`) outside its frames; inside a cluster,
frames where the camera HOLDS (a punch after its snap, an ease's plateau)
get a constant-corner instance whose map is built once (eval=init), and only
the frames that actually move pay for the per-frame map.
"""

import functools
import logging
import math
import os
import zlib

_log = logging.getLogger(__name__)

MODES = ("punch", "ease", "push_in", "pull_out", "landing", "pulse", "shake")

# Default move durations, seconds. Measured on the owner's reference reels:
# snap zooms land in 100-300 ms (expo-out), landings settle in ~350 ms, beat
# pulses are ~250-300 ms, impacts shake for a few hundred ms and decay.
PUNCH_RAMP_S = 0.15
PUNCH_OVERSHOOT_RAMP_S = 0.30
EASE_RAMP_MIN_S, EASE_RAMP_MAX_S = 0.2, 0.5
PUSH_SOFT_S = 0.5
LANDING_S = 0.35
PULSE_S = 0.30
PULSE_ATTACK = 0.35            # fraction of the pulse spent rising
RAMP_MAX_S = 3.0

SHAKE_DEFAULT = 0.5            # mode 'shake' intensity when none is given
SHAKE_HZ = 9.0
SHAKE_DECAY = 6.0              # 1/s: amplitude halves every ~0.12 s
SHAKE_ATTACK_S = 0.03
SHAKE_RELEASE_S = 0.12
# Intensity 1.0 = 3% of the frame's short side of travel (~32 px at 1080)
# plus 0.8 degrees of roll: a hard impact. 0.2-0.3 reads as handheld.
SHAKE_TRAVEL = 0.03
SHAKE_ROLL_DEG = 0.8
# Value noise peaks at |n| <= 1; 1.05 keeps the cover zoom honest at the peak.
_SHAKE_MARGIN = 2.0 * 1.05

ROTATE_MAX_DEG = 15.0
OVERSHOOT_MAX = 0.5
# A held framing shorter than this stays on the per-frame filter.
HOLD_MIN_FRAMES = 3
# Every `perspective` instance allocates its sampling map at graph init:
# two int32 per pixel, 16.6 MB at 1080x1920, 66 MB at 3840x2160. The chain
# is therefore a FIXED small number of instances, not one per move: at most
# MAX_GROUPS per-frame instances (consecutive clusters share one) plus
# constant-corner instances for the longest holds, all inside LUT_BUDGET.
LUT_BUDGET_BYTES = 128 * 1024 * 1024
MAX_GROUPS = 4
MAX_INSTANCES = 8
# Characters of camera filter text one graph may carry. The renderer hands
# graphs over 96 KiB to ffmpeg through a file (argv caps one argument at
# 128 KiB), so this bounds the per-frame parse work and graph size, not the
# exec: a plain eased zoom costs ~1.3 KB, a shake ~6 KB, and ~200 eased
# zooms (a 10-minute programme punched every 3 s) still fit.
TEXT_BUDGET = 300000
ZOOM_MAX = 10.0                # zoompan's own clamp, kept for parity

# cubic matches zoompan's bicubic sharpness (measured gradient energy 6.69
# vs 6.69 on a 1.2x face crop); linear is ~5% softer and cheaper. Kept
# switchable for an overloaded lane, never per edit.
INTERPOLATION = ("linear" if os.getenv("CAMERA_INTERPOLATION", "").strip()
                 .lower() == "linear" else "cubic")

# st()/ld() slots. The filter itself uses 0-3 (zoom, roll, centre x/y);
# curve temporaries use 7-8 and the noise 4-6. Every binding is used before
# anything that could rebind it runs (bodies complete before siblings).
_R_ZOOM, _R_ROT, _R_CX, _R_CY = 0, 1, 2, 3
_R_NX, _R_NI, _R_NW = 4, 5, 6
_R_U, _R_V = 7, 8
# Slot 9 holds program time for the whole expression (set first): the time
# variable is referenced dozens of times per corner, and a corner expression
# is re-parsed every frame.
_R_T = 9


# --------------------------------------------------------------------------
# X: symbolic ffmpeg expressions that evaluate like floats
# --------------------------------------------------------------------------

class X:
    """An ffmpeg expression. Arithmetic with X builds expression text;
    arithmetic on plain floats stays float — so a curve written once is both
    the emitted filter and its python mirror.

    `p` is the text's binding: 1 = a sum/difference, 2 = a product/quotient,
    3 = an atom (number, call, parenthesised group). Operands are wrapped in
    parentheses only where the parser would otherwise regroup them; ffmpeg's
    parse_expr re-reads the shorter text as the same tree (+,- and *,/ are
    left-associative), and expressions are re-parsed for EVERY frame by
    `perspective`, so the bytes are real time."""
    __slots__ = ("s", "p")

    def __init__(self, s, p=3):
        self.s = str(s)
        self.p = p

    def __str__(self):
        return self.s

    def __repr__(self):
        return f"X({self.s!r})"

    def __add__(self, o):
        return _op("+", self, o)

    def __radd__(self, o):
        return _op("+", o, self)

    def __sub__(self, o):
        return _op("-", self, o)

    def __rsub__(self, o):
        return _op("-", o, self)

    def __mul__(self, o):
        return _op("*", self, o)

    def __rmul__(self, o):
        return _op("*", o, self)

    def __truediv__(self, o):
        return _op("/", self, o)

    def __rtruediv__(self, o):
        return _op("/", o, self)

    def __neg__(self):
        return _op("-", 0.0, self)


def lit(v):
    """Expression text for a value. Negative literals are parenthesised:
    ffmpeg's parser rejects the '+-(' a bare unary minus can produce."""
    if isinstance(v, X):
        return v.s
    if isinstance(v, str):
        return v
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"non-finite camera constant {v!r}")
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    if s in ("", "-0"):
        s = "0"
    return f"({s})" if s.startswith("-") else s


def _is_x(*vals):
    return any(isinstance(v, X) for v in vals)


def _op(op, a, b):
    if not _is_x(a, b):
        a, b = float(a), float(b)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        return a / b
    # Identities that keep the text short WITHOUT changing a finite value.
    if op == "*":
        if not isinstance(a, X) and float(a) == 1.0:
            return b
        if not isinstance(b, X) and float(b) == 1.0:
            return a
        if (not isinstance(a, X) and float(a) == 0.0) or \
                (not isinstance(b, X) and float(b) == 0.0):
            return 0.0
    if op in ("+", "-") and not isinstance(b, X) and float(b) == 0.0:
        return a
    if op == "+" and not isinstance(a, X) and float(a) == 0.0:
        return b
    if op == "/" and not isinstance(b, X) and float(b) == 1.0:
        return a
    pa = a.p if isinstance(a, X) else 3
    pb = b.p if isinstance(b, X) else 3
    if op in ("+", "-"):
        ta = lit(a)
        tb = f"({lit(b)})" if (op == "-" and pb <= 1) else lit(b)
        return X(f"{ta}{op}{tb}", 1)
    ta = f"({lit(a)})" if pa < 2 else lit(a)
    tb = f"({lit(b)})" if (pb < 2 or (op == "/" and pb < 3)) else lit(b)
    return X(f"{ta}{op}{tb}", 2)


def _fn(name, *args):
    return X(f"{name}(" + ",".join(lit(a) for a in args) + ")")


def seq(stmts, final):
    """Evaluate each st() statement in order, then `final`.

    ffmpeg evaluates the operands of + and * in an unspecified order, and
    its ';' sequence operator is also the filtergraph's chain separator — a
    graph splitter (renderer._prune_graph_to_audio) would cut the filter in
    half at it. ifnot(x, y) evaluates x first and then y (a C ternary), and
    st(...)*0 is 0 for every finite value, so this is the sequence point."""
    out = lit(final)
    for st_ in reversed(list(stmts)):
        out = f"ifnot({st_}*0,{out})"
    return out


def let(reg, value, body):
    """body(value) with value evaluated ONCE: an st()/ld() slot in ffmpeg,
    a plain call in python. A binding is only ever read inside its own body
    and never rebound there before its last read (siblings complete one
    after the other, whatever order ffmpeg picks)."""
    if not isinstance(value, X):
        return body(value)
    out = body(X(f"ld({reg})"))
    return X(seq([f"st({reg},{value.s})"], out))


def clip(x, lo, hi):
    if _is_x(x, lo, hi):
        return _fn("clip", x, lo, hi)
    return min(max(float(x), float(lo)), float(hi))


def between(x, lo, hi):
    if _is_x(x, lo, hi):
        return _fn("between", x, lo, hi)
    return 1.0 if float(lo) <= float(x) <= float(hi) else 0.0


def lt(x, y):
    if _is_x(x, y):
        return _fn("lt", x, y)
    return 1.0 if float(x) < float(y) else 0.0


def gte(x, y):
    if _is_x(x, y):
        return _fn("gte", x, y)
    return 1.0 if float(x) >= float(y) else 0.0


def fmin(x, y):
    if _is_x(x, y):
        return _fn("min", x, y)
    return min(float(x), float(y))


def fabs(x):
    return _fn("abs", x) if _is_x(x) else abs(float(x))


def sin(x):
    return _fn("sin", x) if _is_x(x) else math.sin(x)


def cos(x):
    return _fn("cos", x) if _is_x(x) else math.cos(x)


def exp(x):
    return _fn("exp", x) if _is_x(x) else math.exp(x)


def floor(x):
    return _fn("floor", x) if _is_x(x) else float(math.floor(x))


def fmod1(x):
    """x - floor(x), ffmpeg's floored mod(x, 1) — in [0, 1) for any x."""
    if _is_x(x):
        return _fn("mod", x, 1.0)
    return x - math.floor(x)


def pow2(x):
    """2**x."""
    return _fn("pow", 2.0, x) if _is_x(x) else math.pow(2.0, x)


def time_var(fps):
    """Program seconds inside the camera's corner expressions: a read of
    slot 9, which every corner expression sets first to (on-1)/fps — see
    time_statement(). `perspective`'s `on` counter is 1-BASED (zoompan's
    started at 0), so (on-1)/fps addresses the same frames the old
    zoompan's on/fps did, including the screen-takeover handoff tuned
    against them. Terms built on this variable are only valid inside
    camera_chain / corner_exprs, which is the only place they go."""
    return X(f"ld({_R_T})")


def time_statement(fps):
    return f"st({_R_T},(on-1)/{float(fps):.3f})"


# --------------------------------------------------------------------------
# Curves. `u` is already clipped to [0, 1] and cheap (a slot or a float).
# --------------------------------------------------------------------------

# 1 - 2^-10 = 1023/1024: exact in binary AND in ten decimals, so expo_out(1)
# is exactly 1 in ffmpeg and in python alike (a held punch is exactly its
# strength, not 2e-7 off it).
_EXPO_DEN = 1.0 - 2.0 ** -10


def smootherstep(u):
    """6u^5 - 15u^4 + 10u^3: zero velocity AND acceleration at both ends."""
    return u * u * u * (u * (u * 6.0 - 15.0) + 10.0)


def expo_out(u):
    """The snap: ~80% of the move in the first third, a long soft landing."""
    return (1.0 - pow2(u * -10.0)) / _EXPO_DEN


def quart_out(u):
    return let(_R_V, 1.0 - u, lambda v: 1.0 - v * v * v * v)


def cubic_out(u):
    return let(_R_V, 1.0 - u, lambda v: 1.0 - v * v * v)


def back_out(u, c1):
    """easeOutBack: passes the target by 4c^3/(27(c+1)^2) and settles with
    zero velocity at u=1."""
    return let(_R_V, u - 1.0,
               lambda v: 1.0 + (c1 + 1.0) * v * v * v + c1 * v * v)


@functools.lru_cache(maxsize=64)
def back_c1(overshoot):
    """The back-out constant whose peak passes the target by `overshoot`
    (0.1 = 10% past). Rounded so the emitted text and the mirror agree."""
    o = min(max(float(overshoot), 0.0), OVERSHOOT_MAX)
    if o <= 1e-4:
        return 0.0
    lo, hi = 0.0, 50.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if 4.0 * mid ** 3 / (27.0 * (mid + 1.0) ** 2) < o:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 4)


def soft_start(u, k):
    """Constant speed with a C1 quadratic run-up over the first k of the
    move — a slow push that starts without a velocity kink."""
    k = round(min(max(float(k), 0.0), 0.5), 4)
    if k <= 1e-4:
        return u
    return (lt(u, k) * (u * u / (k * (2.0 - k)))
            + gte(u, k) * ((u * 2.0 - k) / (2.0 - k)))


def pulse_curve(u, k=PULSE_ATTACK):
    rise = cubic_out(clip(u / k, 0.0, 1.0))
    fall = 1.0 - let(_R_V, clip((u - k) / (1.0 - k), 0.0, 1.0), smootherstep)
    return lt(u, k) * rise + gte(u, k) * fall


# --------------------------------------------------------------------------
# Smooth noise for shake
# --------------------------------------------------------------------------

def _hash(i, seed):
    """Deterministic lattice value in [-1, 1) — the classic sin-hash."""
    return fmod1(sin(i * 12.9898 + seed) * 43758.5453) * 2.0 - 1.0


def value_noise(x, seed):
    """1-D value noise: lattice hashes blended with a smoothstep. C1, no
    period, the same numbers in ffmpeg and in python."""
    def at(xr):
        def with_i(i):
            def with_w(w):
                return _hash(i, seed) * (1.0 - w) + _hash(i + 1.0, seed) * w
            return let(_R_NW, fmod1(xr), lambda f: let(
                _R_NW, f * f * (3.0 - f * 2.0), with_w))
        return let(_R_NI, floor(xr), with_i)
    return let(_R_NX, x, at)


def _seed(z, axis):
    key = f"{z.get('id') or ''}:{axis}".encode("utf-8")
    return round((zlib.crc32(key) % 100000) / 1000.0, 3)


# --------------------------------------------------------------------------
# Per-zoom terms
# --------------------------------------------------------------------------

def _f(v, default=None):
    try:
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


def ramp_seconds(z, a, b):
    """How long this zoom's camera MOVE takes, by mode — the one knob
    ramp_s overrides. Always fits inside the window."""
    mode = z.get("mode") or "punch"
    span = max(b - a, 1e-3)
    r = _f(z.get("ramp_s"))
    if r is not None:
        r = min(max(r, 0.0), RAMP_MAX_S)
    if mode == "punch":
        if r is None:
            r = PUNCH_OVERSHOOT_RAMP_S if _f(z.get("overshoot"), 0.0) > 1e-4 \
                else PUNCH_RAMP_S
        return round(min(r, span), 3)
    if mode == "ease":
        if r is None:
            r = min(max(span / 4.0, EASE_RAMP_MIN_S), EASE_RAMP_MAX_S)
        return round(max(min(r, span / 2.0), 0.0), 3)
    if mode in ("push_in", "pull_out"):
        return round(min(PUSH_SOFT_S if r is None else r, span / 2.0), 3)
    if mode == "landing":
        return round(min(LANDING_S if r is None else r, span), 3)
    if mode == "pulse":
        return round(min(PULSE_S if r is None else r, span), 3)
    return 0.0


def window_gate(t, a, b):
    """1 on [a, b), 0 elsewhere. HALF-open on purpose: the frame AT `end` is
    whatever follows the move — usually the first frame of the next shot
    when a zoom is cut to end on a cut — and an inclusive end held that
    shot's first frame at the old framing (and summed it with a landing
    that starts there). zoompan's between() was inclusive."""
    return gte(t, a) * lt(t, b)


def envelope(z, t, a, b):
    """0..1 (more with overshoot): how far this zoom's move has got at t.
    Zero outside [a, b) for every mode."""
    mode = z.get("mode") or "punch"
    a, b = round(float(a), 3), round(float(b), 3)
    r = ramp_seconds(z, a, b)
    c1 = back_c1(_f(z.get("overshoot"), 0.0) or 0.0)
    gate = window_gate(t, a, b)
    if mode == "punch":
        if r <= 1e-3:
            return gate
        curve = (lambda u: back_out(u, c1)) if c1 > 0 else expo_out
        return let(_R_U, clip((t - a) / r, 0.0, 1.0), curve) * gate
    if mode == "ease":
        if r <= 1e-3:
            return gate
        curve = (lambda u: back_out(u, c1)) if c1 > 0 else smootherstep
        rise = let(_R_U, clip((t - a) / r, 0.0, 1.0), curve)
        fall = let(_R_U, clip((b - t) / r, 0.0, 1.0), smootherstep)
        return rise * fall
    if mode in ("push_in", "pull_out"):
        span = b - a
        k = r / span if span > 1e-3 else 0.0
        u = clip((t - a) / span, 0.0, 1.0)
        if mode == "pull_out":
            u = 1.0 - u
        return let(_R_U, u, lambda uu: soft_start(uu, k)) * gate
    if mode == "landing":
        if r <= 1e-3:
            return 0.0
        return (1.0 - quart_out(clip((t - a) / r, 0.0, 1.0))) * gate
    if mode == "pulse":
        if r <= 1e-3:
            return 0.0
        return let(_R_U, clip((t - a) / r, 0.0, 1.0), pulse_curve) * gate
    return 0.0                          # shake: no zoom of its own


def shake_amount(z):
    """Shake intensity 0-1 for this zoom (mode 'shake' defaults to 0.5)."""
    s = _f(z.get("shake"))
    if s is None:
        s = SHAKE_DEFAULT if (z.get("mode") == "shake") else 0.0
    return min(max(s, 0.0), 1.0)


def shake_envelope(z, t, a, b):
    a, b = round(float(a), 3), round(float(b), 3)
    decay = _f(z.get("shake_decay"), SHAKE_DECAY)
    decay = min(max(decay, 0.0), 30.0)
    el = clip(t - a, 0.0, 600.0)
    env = clip(el / SHAKE_ATTACK_S, 0.0, 1.0) * window_gate(t, a, b) \
        * clip((b - t) / SHAKE_RELEASE_S, 0.0, 1.0)
    if decay > 1e-3:
        env = env * exp(el * -round(decay, 3))
    return env


class Terms:
    """One shot's contribution to the shared camera. z: zoom amount added to
    1. cx/cy: target offsets from the frame centre (None = untargeted).
    rot: radians (+ = counter-clockwise on screen, ffmpeg's sense). dx/dy:
    shake travel as fractions of the frame's SHORT side. amp: the shake's
    travel envelope (same units) the cover zoom must leave room for."""
    __slots__ = ("z", "cx", "cy", "rot", "dx", "dy", "amp")

    def __init__(self, z=0.0, cx=None, cy=None):
        self.z, self.cx, self.cy = z, cx, cy
        self.rot = self.dx = self.dy = self.amp = 0.0


def _nonzero(v):
    if isinstance(v, (X, str)):
        return bool(str(v).strip())
    return v is not None and abs(float(v)) > 0.0


def zoom_terms(z, t, a, b, targeted=True):
    """Terms for one non-travelling zoom over its clamped window [a, b]."""
    out = Terms()
    st = round(_f(z.get("strength"), 0.25), 3)
    mode = z.get("mode") or "punch"
    e = envelope(z, t, a, b)
    if mode != "shake":
        out.z = st * e
    rot = _f(z.get("rotate"), 0.0) or 0.0
    rot = min(max(rot, -ROTATE_MAX_DEG), ROTATE_MAX_DEG)
    if abs(rot) > 1e-4 and mode != "shake":
        # + is CLOCKWISE on screen for the editor; the source quad turns the
        # other way, so the radians carry the opposite sign.
        out.rot = round(-math.radians(rot), 6) * e
    add_shake(out, z, t, a, b)
    if targeted:
        gate = window_gate(t, round(a, 3), round(b, 3))
        cx, cy = _f(z.get("cx")), _f(z.get("cy"))
        if cx is not None and abs(cx - 0.5) > 1e-6:
            out.cx = round(cx - 0.5, 3) * gate
        if cy is not None and abs(cy - 0.5) > 1e-6:
            out.cy = round(cy - 0.5, 3) * gate
    return out


def add_shake(out, z, t, a, b):
    """Add z's shake (if any) to `out` — shared with travelling zooms."""
    s = shake_amount(z)
    if s <= 1e-4:
        return out
    a, b = round(float(a), 3), round(float(b), 3)
    hz = min(max(_f(z.get("shake_hz"), SHAKE_HZ), 0.5), 30.0)
    env = shake_envelope(z, t, a, b)
    travel = round(s * SHAKE_TRAVEL, 5)
    x = clip(t - a, 0.0, 600.0) * round(hz, 3)
    # One octave per axis: a smooth random walk between fresh lattice
    # targets every 1/hz s. (A second octave doubled the expression text —
    # every corner re-parses it every frame — for detail a 30 fps frame
    # cannot show at impact speeds.)
    out.dx = out.dx + travel * env * value_noise(x, _seed(z, "x"))
    out.dy = out.dy + travel * env * value_noise(x, _seed(z, "y"))
    roll = round(math.radians(s * SHAKE_ROLL_DEG), 6)
    out.rot = out.rot + roll * env * value_noise(x * 0.8, _seed(z, "r"))
    out.amp = out.amp + travel * env
    return out


def cover_zoom(rot, amp, aspect):
    """The least zoom at which a window rolled by `rot` and shaken by `amp`
    (fraction of the short side) still lies inside the source frame."""
    margin = 1.0 / (1.0 - fmin(amp * _SHAKE_MARGIN, 0.5))
    return (cos(rot) + aspect * fabs(sin(rot))) * margin


def aspect_k(W, H):
    W, H = float(W or 1), float(H or 1)
    return round(max(W / H, H / W), 6)


# --------------------------------------------------------------------------
# Shots -> the filter chain
# --------------------------------------------------------------------------

class Shot:
    """One thing that moves the camera over program window [a, b]: its
    expression Terms (X / str) and, when it has one, `mirror(t)` -> float
    Terms. Shots without a mirror (takeovers, travelling paths, shifts) are
    rendered correctly but never get the static-hold shortcut. `rebuild`
    (zoom shots) re-makes the shot from an edited zoom dict — the text
    budget uses it to shed a shake."""
    __slots__ = ("a", "b", "terms", "mirror", "rebuild", "src")

    def __init__(self, a, b, terms, mirror=None, rebuild=None, src=None):
        self.a, self.b, self.terms, self.mirror = a, b, terms, mirror
        self.rebuild, self.src = rebuild, src


def zoom_shot(z, a, b, fps, targeted=True):
    T = time_var(fps)
    return Shot(a, b, zoom_terms(z, T, a, b, targeted),
                lambda t, _z=z, _a=a, _b=b: zoom_terms(_z, t, _a, _b,
                                                       targeted),
                rebuild=lambda nz, _a=a, _b=b: zoom_shot(nz, _a, _b, fps,
                                                         targeted),
                src=z)


def insert_motion_shot(motion, start, dur, fps):
    """An insert's Ken Burns move as a Shot on the PROGRAM clock: 1 -> 1.25
    (zoom_in), 1.25 -> 1 (zoom_out), or a 1.15x pan across the frame,
    constant speed over the insert's own frames (the move starts and ends on
    its cuts). It rides the shared camera chain instead of a filter per
    insert: every perspective instance costs a W*H*8-byte map, and a photo
    montage can carry dozens of inserts."""
    start = round(float(start), 3)
    nframes = max(1, int(round(float(dur) * fps)))
    span = round(nframes / float(fps), 6)
    end = round(start + float(dur), 3)

    def terms(t):
        gate = window_gate(t, start, end)
        prog = clip((t - start) / span, 0.0, 1.0)
        cx = None
        if motion == "zoom_in":
            z = prog * 0.25 * gate
        elif motion == "zoom_out":
            z = (0.25 - prog * 0.25) * gate
        elif motion == "pan_left":
            z, cx = 0.15 * gate, (0.5 - prog) * gate
        else:                           # pan_right
            z, cx = 0.15 * gate, (prog - 0.5) * gate
        return Terms(z, cx)
    return Shot(start, end, terms(time_var(fps)), terms)


# ffmpeg's expression parser spends one unit of a ~100-deep recursion budget
# per operator in a flat chain AND per nesting level: 'val+0+0...' fails to
# parse past ~100 terms (measured). A sum of many moves is therefore emitted
# as a balanced tree of short chains — depth grows with log(N), never N.
_CHAIN = 8


def _balanced_sum(texts):
    if len(texts) <= _CHAIN:
        return "+".join(texts)
    mid = len(texts) // 2
    return (f"({_balanced_sum(texts[:mid])})"
            f"+({_balanced_sum(texts[mid:])})")


def _sum(terms):
    terms = [t for t in terms if t is not None and _nonzero(t)]
    if not terms:
        return None
    return _balanced_sum([lit(t) for t in terms])


def _filter(in_label, out_label, W, H, exprs, eval_frame, frames, fps):
    names = ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3")
    opts = ":".join(f"{n}='{e}'" for n, e in zip(names, exprs))
    en = ""
    if frames is not None:
        en = ":enable='" + _balanced_sum([
            f"between(t,{(n0 - 0.25) / fps:.4f},{(n1 + 0.25) / fps:.4f})"
            for n0, n1 in frames]) + "'"
    ev = ":eval=frame" if eval_frame else ""
    return (f"[{in_label}]perspective={opts}:sense=source{ev}"
            f":interpolation={INTERPOLATION}{en}[{out_label}]")


def corner_exprs(W, H, terms_list, targeted=True, fps=None):
    """The eight corner expressions of the camera over summed Terms.

    zoom   = clip(1 + sum(z), 1, 10), raised to the cover zoom when the
             window rolls or shakes;
    window = W/zoom x H/zoom at ((W - W/zoom) * clip(0.5 + sum(cx), 0, 1),
             ...) — zoompan's exact geometry, so every aimed zoom and every
             takeover keeps its framing — then shaken by dx/dy, rolled by rot
             about its centre, and clamped inside the frame.

    Corners are SOURCE pixel indices (sense=source), shifted by the
    half-pixel term perspective's corner-anchored mapping needs to sample
    pixel CENTRES the way every scaler (and zoompan) does — zero at zoom 1,
    so an idle frame is an exact copy. Each expression recomputes the shared
    values into st() slots: perspective has no variables shared between its
    eight expressions. corners() is the same arithmetic in python.
    """
    W, H = int(W), int(H)
    hw, hh = W / 2.0, H / 2.0
    S = float(min(W, H))
    zsum = _sum([tm.z for tm in terms_list])
    rot = _sum([tm.rot for tm in terms_list])
    amp = _sum([tm.amp for tm in terms_list])
    dx = _sum([tm.dx for tm in terms_list])
    dy = _sum([tm.dy for tm in terms_list])
    cx = _sum([tm.cx for tm in terms_list]) if targeted else None
    cy = _sum([tm.cy for tm in terms_list]) if targeted else None
    zoom = f"clip(1+{zsum},1,{ZOOM_MAX:g})" if zsum else "1"
    cxe = f"clip(0.5+{cx},0,1)" if cx else "0.5"
    cye = f"clip(0.5+{cy},0,1)" if cy else "0.5"
    moving = bool(rot or amp or dx or dy)
    pre = []
    if fps is not None:
        pre.append(time_statement(fps))
    if rot:
        pre.append(f"st({_R_ROT},{rot})")
    if moving:
        cov = (f"(cos(ld(1))+{lit(aspect_k(W, H))}*abs(sin(ld(1))))"
               if rot else "1")
        if amp:
            cov += f"/(1-min(({amp})*{lit(_SHAKE_MARGIN)},0.5))"
        zoom = f"max({zoom},{cov})"
    pre.append(f"st({_R_ZOOM},{zoom})")
    c_ = "cos(ld(1))" if rot else "1"
    s_ = "sin(ld(1))" if rot else "0"
    sa = "abs(sin(ld(1)))" if rot else "0"
    # Window centre (continuous coordinates): zoompan's crop origin plus half
    # the window, then the shake travel, then clamped so the rolled window's
    # bounding box stays inside the frame.
    cxp = f"({W}-{W}/ld(0))*{cxe}+{lit(hw)}/ld(0)"
    cyp = f"({H}-{H}/ld(0))*{cye}+{lit(hh)}/ld(0)"
    if moving:
        if dx:
            cxp += f"+({dx})*{lit(S)}"
        if dy:
            cyp += f"+({dy})*{lit(S)}"
        bx = (f"({lit(hw)}/ld(0)*{c_}+{lit(hh)}/ld(0)*{sa})" if rot
              else f"{lit(hw)}/ld(0)")
        by = (f"({lit(hw)}/ld(0)*{sa}+{lit(hh)}/ld(0)*{c_})" if rot
              else f"{lit(hh)}/ld(0)")
        cxp = f"clip({cxp},{bx},{W}-{bx})"
        cyp = f"clip({cyp},{by},{H}-{by})"
    # Pixel-centre alignment: index = C + Rot.(i + 0.5 - W/2, ...)/z - 0.5,
    # i.e. the centre moves by Rot.(0.5, 0.5)/z - 0.5.
    if rot:
        cxp += f"+0.5*({c_}-{s_})/ld(0)-0.5"
        cyp += f"+0.5*({s_}+{c_})/ld(0)-0.5"
    else:
        cxp += "+0.5/ld(0)-0.5"
        cyp += "+0.5/ld(0)-0.5"
    stx, sty = f"st({_R_CX},{cxp})", f"st({_R_CY},{cyp})"
    ux = f"{lit(hw)}/ld(0)*{c_}" if rot else f"{lit(hw)}/ld(0)"
    vy = f"{lit(hh)}/ld(0)*{c_}" if rot else f"{lit(hh)}/ld(0)"
    uy, vx = f"{lit(hw)}/ld(0)*{s_}", f"{lit(hh)}/ld(0)*{s_}"
    # TL, TR, BL, BR = C -u -v, C +u -v, C -u +v, C +u +v with
    # u = (hw cos, hw sin)/z and v = (-hh sin, hh cos)/z.
    exprs = []
    for su, sv in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        xe = (f"ld(2){'+' if su > 0 else '-'}{ux}"
              + (f"{'-' if sv > 0 else '+'}{vx}" if rot else ""))
        ye = ("ld(3)" + (f"{'+' if su > 0 else '-'}{uy}" if rot else "")
              + f"{'+' if sv > 0 else '-'}{vy}")
        exprs.append(seq(pre + [stx], xe))
        exprs.append(seq(pre + [sty], ye))
    return exprs


def corners(W, H, terms_list, targeted=True):
    """corner_exprs() evaluated in python over float Terms: the eight
    perspective corners [x0, y0, x1, y1, x2, y2, x3, y3] for one frame."""
    W, H = int(W), int(H)
    hw, hh = W / 2.0, H / 2.0
    S = float(min(W, H))
    zs = sum(float(tm.z) for tm in terms_list)
    rot = sum(float(tm.rot) for tm in terms_list)
    amp = sum(float(tm.amp) for tm in terms_list)
    dx = sum(float(tm.dx) for tm in terms_list)
    dy = sum(float(tm.dy) for tm in terms_list)
    cx = 0.5 + sum(float(tm.cx) for tm in terms_list
                   if targeted and tm.cx is not None)
    cy = 0.5 + sum(float(tm.cy) for tm in terms_list
                   if targeted and tm.cy is not None)
    moving = bool(rot or amp or dx or dy)
    z = min(max(1.0 + zs, 1.0), ZOOM_MAX)
    if moving:
        z = max(z, cover_zoom(rot, amp, aspect_k(W, H)))
    c, s = math.cos(rot), math.sin(rot)
    cxv = (W - W / z) * clip(cx, 0.0, 1.0) + hw / z
    cyv = (H - H / z) * clip(cy, 0.0, 1.0) + hh / z
    if moving:
        cxv += dx * S
        cyv += dy * S
        bx = hw / z * c + hh / z * abs(s)
        by = hw / z * abs(s) + hh / z * c
        cxv = clip(cxv, bx, W - bx)
        cyv = clip(cyv, by, H - by)
    cxv += 0.5 * (c - s) / z - 0.5
    cyv += 0.5 * (s + c) / z - 0.5
    ux, uy = hw / z * c, hw / z * s
    vx, vy = hh / z * s, hh / z * c
    out = []
    for su, sv in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        out.append(cxv + su * ux - sv * vx)
        out.append(cyv + su * uy + sv * vy)
    return out


def _identity(W, H):
    return [0.0, 0.0, float(W), 0.0, 0.0, float(H), float(W), float(H)]


def _clusters(shots, pad):
    out = []
    for sh in sorted(shots, key=lambda s: s.a):
        if out and sh.a - pad <= out[-1][1] + pad:
            out[-1][1] = max(out[-1][1], sh.b)
            out[-1][2].append(sh)
        else:
            out.append([sh.a, sh.b, [sh]])
    return out


def _runs(frames):
    out = []
    for n in frames:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def _plan_chain(in_label, out_label, W, H, fps, shots, targeted):
    pad = 1.0 / fps
    ident = _identity(W, H)
    max_inst = max(2, min(MAX_INSTANCES,
                          LUT_BUDGET_BYTES // max(1, int(W) * int(H) * 8)))
    clusters = []          # (members, moving frame list, holds)
    for a, b, members in _clusters(shots, pad):
        n0 = max(0, int(math.floor((a - pad) * fps)))
        n1 = int(math.ceil((b + pad) * fps))
        if not all(s.mirror is not None for s in members):
            clusters.append((members, list(range(n0, n1 + 1)), []))
            continue
        # Runs of frames with identical corners. Identity frames need no
        # filter; a long enough run is a HOLD whose map can be built once.
        runs = []
        for n in range(n0, n1 + 1):
            cs = corners(W, H, [s.mirror(n / fps) for s in members],
                         targeted)
            if max(abs(p - q) for p, q in zip(cs, ident)) < 1e-9:
                continue
            if runs and runs[-1][1] == n - 1 and \
                    max(abs(p - q) for p, q in zip(cs, runs[-1][2])) < 1e-7:
                runs[-1][1] = n
            else:
                runs.append([n, n, cs])
        moving, holds = [], []
        for r in runs:
            if r[1] - r[0] + 1 >= HOLD_MIN_FRAMES:
                holds.append(r)
            else:
                moving.extend(range(r[0], r[1] + 1))
        clusters.append((members, moving, holds))
    clusters = [c for c in clusters if c[1] or c[2]]
    if not clusters:
        return [f"[{in_label}]null[{out_label}]"]
    n_groups = min(len(clusters), MAX_GROUPS, max_inst)
    # The longest holds get their own build-once instance; every other held
    # frame is served by its group's per-frame instance (same corners).
    all_holds = sorted((h for c in clusters for h in c[2]),
                       key=lambda h: h[1] - h[0], reverse=True)
    own = {id(h) for h in all_holds[:max(0, max_inst - n_groups)]}
    # Consecutive clusters share a per-frame instance, balanced by how much
    # expression text each carries (that text is parsed every frame).
    weights = [sum(len(lit(s.terms.z)) + len(lit(s.terms.rot))
                   + len(lit(s.terms.dx)) + 1 for s in c[0])
               for c in clusters]
    target = sum(weights) / n_groups
    groups, acc = [[]], 0.0
    for c, w in zip(clusters, weights):
        if groups[-1] and acc + w / 2.0 > target * len(groups) and \
                len(groups) < n_groups:
            groups.append([])
        groups[-1].append(c)
        acc += w
    plan = []                           # (exprs, eval_frame, frame runs)
    for group in groups:
        frames = []
        for members, moving, holds in group:
            frames.extend(moving)
            for h in holds:
                if id(h) not in own:
                    frames.extend(range(h[0], h[1] + 1))
        if frames:
            exprs = corner_exprs(
                W, H, [s.terms for c in group for s in c[0]], targeted, fps)
            plan.append((exprs, True, _runs(sorted(frames))))
    for h in all_holds:
        if id(h) in own:
            plan.append(([f"{v:.6f}" for v in h[2]], False, [[h[0], h[1]]]))
    out, cur = [], in_label
    for k, (exprs, ev, runs) in enumerate(plan):
        nxt = out_label if k == len(plan) - 1 else f"{out_label}_c{k}"
        out.append(_filter(cur, nxt, W, H, exprs, ev, runs, fps))
        cur = nxt
    return out


def _sheddable(shot, key):
    if not (shot.rebuild and shot.src):
        return False
    if key == "shake":
        return shake_amount(shot.src) > 0
    if key == "curve":
        return not ((shot.src.get("mode") or "punch") == "punch"
                    and _f(shot.src.get("ramp_s")) == 0.0)
    return bool(shot.src.get(key))


def camera_chain(in_label, out_label, W, H, fps, shots, targeted=True,
                 always=False):
    """The shared camera as a chain of `perspective` filters.

    Shots are clustered by time (overlapping windows share one filter — their
    terms sum, exactly the single-filter maths), and every cluster's filter is
    enabled only on its own frames, so a frame pays for the expressions of
    the moves actually happening on it. Inside a cluster whose shots all
    have mirrors, the frames are classified by their python-computed
    corners: identity frames get no filter, held frames (constant corners
    over a run) get a constant-corner instance whose sampling map is built
    once, and only moving frames evaluate expressions per frame.

    always=True (an aspect shift's push, which can hold anywhere): one
    filter over everything, evaluated every frame — the old shape.

    Every corner expression is re-parsed per frame, so the text is bounded
    (TEXT_BUDGET): past it the longest shakes are shed first (the zoom itself
    stays), then rolls, then eased curves become hard steps at the same
    framing — and the renderer's log says so. A render that loses a shake
    beats a render that cannot start. Returns the filter strings, ending on
    out_label.
    """
    fps = float(fps)
    if not shots:
        return [f"[{in_label}]null[{out_label}]"]
    if always:
        exprs = corner_exprs(W, H, [s.terms for s in shots], targeted, fps)
        return [_filter(in_label, out_label, W, H, exprs, True, None, fps)]
    shots = list(shots)
    out = _plan_chain(in_label, out_label, W, H, fps, shots, targeted)
    # Shed texture, never framing: shakes first (the longest text by far),
    # then rolls, then — only for an absurdly dense pass — the eased curves
    # themselves (hard steps at the same framing). A zoom is never dropped.
    # Shots are shed in batches sized to the overflow (each costs ~8x its
    # term text, once per corner) and the chain re-planned once per batch.
    for keys in (("shake", "shake_hz", "shake_decay"), ("rotate",),
                 ("curve",)):
        for _round in range(8):
            over = sum(len(f) for f in out) - TEXT_BUDGET
            if over <= 0:
                break
            cand = sorted(((k, s) for k, s in enumerate(shots)
                           if _sheddable(s, keys[0])),
                          key=lambda ks: -_term_text(ks[1].terms))
            if not cand:
                break
            saved = 0
            for k, s in cand:
                z = {key: v for key, v in s.src.items() if key not in keys}
                if keys[0] == "shake" and z.get("mode") == "shake":
                    z["mode"], z["strength"], z["ramp_s"] = "punch", 0.0, 0.0
                if keys[0] == "curve":
                    z = {key: v for key, v in z.items()
                         if key not in ("overshoot", "mode")}
                    z["ramp_s"] = 0.0
                _log.warning("camera text over budget: dropped %s on zoom "
                             "%s", keys[0], s.src.get("id"))
                shots[k] = s.rebuild(z)
                saved += 8 * max(0, _term_text(s.terms)
                                 - _term_text(shots[k].terms))
                if saved >= over * 1.1:
                    break
            out = _plan_chain(in_label, out_label, W, H, fps, shots,
                              targeted)
    return out


def _term_text(tm):
    return sum(len(lit(v)) for v in (tm.z, tm.rot, tm.dx, tm.dy, tm.amp)
               if v is not None and _nonzero(v))


def describe(z):
    """A short human phrase for a zoom's camera move."""
    mode = z.get("mode") or "punch"
    bits = []
    r = _f(z.get("ramp_s"))
    if mode == "punch":
        bits.append("hard snap" if r is not None and r <= 1e-3
                    else "expo snap-in")
    if _f(z.get("overshoot"), 0.0) > 1e-4:
        bits.append(f"{int(round(_f(z.get('overshoot')) * 100))}% overshoot")
    if abs(_f(z.get("rotate"), 0.0) or 0.0) > 1e-4:
        bits.append(f"{_f(z.get('rotate')):g}° roll")
    if shake_amount(z) > 1e-4:
        bits.append(f"shake {shake_amount(z):g}")
    return ", ".join(bits)
