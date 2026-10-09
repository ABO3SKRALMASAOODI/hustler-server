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
  shake     smooth value noise (seeded per item and axis — never a sine)
            on x, y and roll, with a fast attack and exponential decay.
            `shake` > 0 adds the same to any other mode (a punch that lands
            as an impact).

A zoom edge within CUT_HOLD_FRAMES (4) of a program cut is moved onto the
cut and holds through it (hold_through_cuts): an ease ending on a cut stays
pushed in to the cut's last frame instead of releasing over it, and a punch
or ease starting on a cut is already in on the cut's first frame — the cut
changes the framing, once.

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

COST (measured; this is the price of the sub-pixel camera)

perspective is a generic per-pixel remap and costs real CPU where zoompan
was nearly free. Per 1080x1920 frame, ONE thread:

                         review box     Apple M (ffmpeg 9)
  zoompan                  ~2.5 ms         ~0.6 ms
  cubic, eval=frame        ~31 ms          ~14.6 ms
  cubic, held (eval=init)  ~27 ms          ~12.6 ms
  linear, eval=frame       ~13 ms          ~6.5 ms

plus ~32 us per KB of corner text re-parsed on every eval=frame frame.
Whole graphs (review box): a 60 s short with 15 zooms, filters only, went
11.6 -> 37.1 s on 1 thread and 5.3 -> 10.1 s on 4, max RSS 247 -> 409 MB;
a 35 s short with 9 zooms plus x264 veryfast 3.67 -> 5.23 s wall on 4
threads (+43%). So the camera only runs where something moves: timeline
`enable` passes every identity frame through untouched (zero cost), the
longest HOLDS (a punch after its snap, an ease's plateau) get build-once
constant-corner instances (only ~12% cheaper — the resample dominates —
so they are reserved for holds long enough to matter), and the moving
frames are served by a few TIME-LOCAL per-frame instances, each carrying
only the moves near its own frames. Draft previews (270x480) resample
linearly, at ~1/16 of these costs; approval previews and finals stay cubic.
Each instance also holds a W*H*8-byte map (16.6 MB at 1080x1920), which is
what caps their number. See camera_chain.

ffmpeg's expression parser also has hard limits that shape the text: ~100
operators in one flat chain, ~100 nesting levels. Sums are balanced trees;
the deepest expression here nests ~20.
"""

import functools
import logging
import math
import os
import zlib

_log = logging.getLogger(__name__)

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
# A landing or pulse IS its ramp (it is zero once the ramp is over), so it
# can never be shorter than this: ramp_s=0 would place a move that renders
# nothing. ~2 frames at 30 fps.
SETTLE_MIN_S = 0.06
# A held framing at least this long gets its own build-once instance when
# instance slots remain; shorter holds ride a per-frame instance (a held
# frame is only ~12% cheaper, not worth a 16.6 MB map for a few frames).
HOLD_OWN_MIN_FRAMES = 12
# Every `perspective` instance allocates its sampling map at graph init:
# two int32 per pixel, 16.6 MB at 1080x1920, 66 MB at 3840x2160. The chain
# is therefore a FIXED small number of instances, not one per move: at most
# MAX_GROUPS per-frame instances (each serving one stretch of the
# programme) plus constant-corner instances for the longest holds, all
# inside LUT_BUDGET.
LUT_BUDGET_BYTES = 128 * 1024 * 1024
MAX_GROUPS = 4
MAX_INSTANCES = 8
# Characters of corner-expression text ONE per-frame instance may carry.
# perspective re-parses all eight corners on every frame it runs (~32 us
# per KB, measured: 128 KiB is ~4 ms on a frame whose resample costs
# 13-31 ms). Instances are time-local — a frame's instance carries only the
# moves near it — so this bounds LOCAL density and the total scales with
# the programme: a plain eased zoom costs ~1.3 KB, a shake ~6 KB, so ~90
# eased zooms per instance, ~360 over a long programme's four. Past it the
# planner sheds texture (camera_chain). Graphs over 96 KiB reach ffmpeg
# through a file (renderer._graph_args), so this is about time, not argv.
INSTANCE_TEXT_BUDGET = 128 * 1024
# Below this much corner text a second per-frame instance saves less parse
# time than its sampling map costs in memory.
GROUP_TEXT_MIN = 16 * 1024
ZOOM_MAX = 10.0                # zoompan's own clamp, kept for parity

# cubic matches zoompan's bicubic sharpness (measured gradient energy 6.69
# vs 6.69 on a 1.2x face crop); linear is ~5% softer and ~2.2x cheaper. The
# renderer picks linear for draft previews (camera_chain's `interpolation`);
# CAMERA_INTERPOLATION=linear forces it everywhere for an overloaded lane.
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
    if mode in ("landing", "pulse"):
        # The move is the ramp: never shorter than SETTLE_MIN_S (the schema
        # refuses less), never longer than the window.
        r = (LANDING_S if mode == "landing" else PULSE_S) if r is None \
            else max(r, SETTLE_MIN_S)
        return round(min(r, span), 3)
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
    Zero outside [a, b) for every mode. A zoom edge hold_through_cuts put on
    a program cut (`_hold_in` / `_hold_out`) has no ramp at that edge: the
    move is already complete on the cut's first frame, or still held on the
    last frame before it."""
    mode = z.get("mode") or "punch"
    a, b = round(float(a), 3), round(float(b), 3)
    r = ramp_seconds(z, a, b)
    c1 = back_c1(_f(z.get("overshoot"), 0.0) or 0.0)
    gate = window_gate(t, a, b)
    # An overshoot slam keeps its attack on a cut: the impact IS the move.
    hold_in = bool(z.get("_hold_in")) and c1 <= 0
    if mode == "punch":
        if r <= 1e-3 or hold_in:
            return gate
        curve = (lambda u: back_out(u, c1)) if c1 > 0 else expo_out
        return let(_R_U, clip((t - a) / r, 0.0, 1.0), curve) * gate
    if mode == "ease":
        if r <= 1e-3:
            return gate
        curve = (lambda u: back_out(u, c1)) if c1 > 0 else smootherstep
        rise = gte(t, a) if hold_in else \
            let(_R_U, clip((t - a) / r, 0.0, 1.0), curve)
        fall = lt(t, b) if z.get("_hold_out") else \
            let(_R_U, clip((b - t) / r, 0.0, 1.0), smootherstep)
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
    fps = round(float(fps), 3)
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


def _filter(in_label, out_label, W, H, exprs, eval_frame, frames, fps,
            interpolation=None):
    names = ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3")
    opts = ":".join(f"{n}='{e}'" for n, e in zip(names, exprs))
    en = ""
    if frames is not None:
        en = ":enable='" + _balanced_sum([
            f"between(t,{(n0 - 0.25) / fps:.4f},{(n1 + 0.25) / fps:.4f})"
            for n0, n1 in frames]) + "'"
    ev = ":eval=frame" if eval_frame else ""
    return (f"[{in_label}]perspective={opts}:sense=source{ev}"
            f":interpolation={interpolation or INTERPOLATION}{en}"
            f"[{out_label}]")


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


def _same(p, q, tol):
    return max(abs(a - b) for a, b in zip(p, q)) < tol


def _runs(frames):
    out = []
    for n in frames:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


class _Plan:
    """instances: [(exprs, eval_frame, frame runs)] in chain order.
    groups: [(text length, shot indices)], one per per-frame instance."""
    __slots__ = ("instances", "groups")

    def __init__(self):
        self.instances, self.groups = [], []


def _sweep(W, H, fps, shots, targeted):
    """Every non-identity frame as (n, corners | None, active shot indices).

    A frame needs only the shots whose (one-frame padded) window contains
    it, so the sweep evaluates exactly those mirrors — O(frames x local
    density), never O(frames x moves). corners is None when an active shot
    has no mirror (its frames must run per frame)."""
    pad = 1.0 / fps
    ident = _identity(W, H)
    spans = sorted((max(0, int(math.floor((s.a - pad) * fps))),
                    int(math.ceil((s.b + pad) * fps)), k)
                   for k, s in enumerate(shots))
    out, active, i, n = [], [], 0, 0
    while i < len(spans) or active:
        if not active:
            n = max(n, spans[i][0])
        while i < len(spans) and spans[i][0] <= n:
            active.append((spans[i][1], spans[i][2]))
            i += 1
        ks = tuple(sorted(k for _n1, k in active))
        if all(shots[k].mirror is not None for k in ks):
            cs = corners(W, H, [shots[k].mirror(n / fps) for k in ks],
                         targeted)
            if not _same(cs, ident, 1e-9):
                out.append((n, cs, ks))
        else:
            out.append((n, None, ks))
        n += 1
        active = [(n1, k) for n1, k in active if n1 >= n]
    return out


def _plan_chain(W, H, fps, shots, targeted, max_groups, max_instances):
    """Which `perspective` instance serves which frame.

    Each non-identity frame is served by EXACTLY ONE instance whose text
    holds every move active on that frame, so the frame sees the summed
    single-filter maths. Identity frames get no filter at all. The longest
    constant-corner holds get build-once instances; every other frame goes
    to one of up to `max_groups` per-frame instances that split the
    programme into consecutive stretches of roughly equal move text — a
    stretch carries only its own moves (plus any long move spanning it), so
    per-frame parse cost follows local density, not programme length."""
    plan = _Plan()
    frames = _sweep(W, H, fps, shots, targeted)
    if not frames:
        return plan
    max_inst = max(1, min(max_instances,
                          LUT_BUDGET_BYTES // max(1, int(W) * int(H) * 8)))
    runs = []                           # [n0, n1, corners]
    for n, cs, _ks in frames:
        if runs and runs[-1][1] == n - 1 and cs is not None and \
                runs[-1][2] is not None and _same(cs, runs[-1][2], 1e-7):
            runs[-1][1] = n
        else:
            runs.append([n, n, cs])
    weight = {}
    for _n, _cs, ks in frames:
        for k in ks:
            if k not in weight:
                weight[k] = 8 * _term_text(shots[k].terms) + 64
    n_groups = max(1, min(max_groups, max_inst, int(math.ceil(
        sum(weight.values()) / float(GROUP_TEXT_MIN)))))
    holds = sorted((r for r in runs if r[2] is not None
                    and r[1] - r[0] + 1 >= HOLD_OWN_MIN_FRAMES),
                   key=lambda r: (r[0] - r[1], r[0]))
    own = holds[:max(0, max_inst - n_groups)]
    owned = set()
    for r in own:
        owned.update(range(r[0], r[1] + 1))
    per_frame = [f for f in frames if f[0] not in owned]
    if per_frame:
        # Consecutive stretches of about equal text, counting each move
        # once, where it first appears.
        seen, cum, marks = set(), 0, []
        for _n, _cs, ks in per_frame:
            for k in ks:
                if k not in seen:
                    seen.add(k)
                    cum += weight[k]
            marks.append(cum)
        stretches = [[]]
        for f, mark in zip(per_frame, marks):
            if stretches[-1] and len(stretches) < n_groups and \
                    mark > cum * len(stretches) / n_groups:
                stretches.append([])
            stretches[-1].append(f)
        for stretch in stretches:
            ks = sorted({k for _n, _cs, kk in stretch for k in kk})
            exprs = corner_exprs(W, H, [shots[k].terms for k in ks],
                                 targeted, fps)
            plan.instances.append(
                (exprs, True, _runs([f[0] for f in stretch])))
            plan.groups.append((sum(len(e) for e in exprs), ks))
    for r in own:
        # Ten decimals: at six, perspective's 1/256-px sample rounding could
        # land a few pixels one grey level off the per-frame evaluation.
        # At ten a hold renders bit-identically to the single filter.
        plan.instances.append(([f"{v:.10f}" for v in r[2]], False,
                               [[r[0], r[1]]]))
    return plan


def _emit(plan, in_label, out_label, W, H, fps, interpolation):
    if not plan.instances:
        return [f"[{in_label}]null[{out_label}]"]
    out, cur = [], in_label
    for k, (exprs, ev, runs) in enumerate(plan.instances):
        nxt = out_label if k == len(plan.instances) - 1 \
            else f"{out_label}_c{k}"
        out.append(_filter(cur, nxt, W, H, exprs, ev, runs, fps,
                           interpolation))
        cur = nxt
    return out


def _sheddable(shot, key):
    if not (shot.rebuild and shot.src):
        return False
    if key == "shake":
        return shake_amount(shot.src) > 0
    if key == "curve":
        # landing and pulse ARE their curve (and their text is short):
        # never shed. A punch/ease/push already stepped has nothing left.
        return (shot.src.get("mode") or "punch") in (
            "punch", "ease", "push_in", "pull_out") \
            and _f(shot.src.get("ramp_s")) != 0.0
    return bool(shot.src.get(key))


def _shed(z, keys):
    """z without the texture `keys` names — never a different framing."""
    z = {key: v for key, v in z.items() if key not in keys}
    if keys[0] == "shake" and z.get("mode") == "shake":
        z["mode"], z["strength"], z["ramp_s"] = "punch", 0.0, 0.0
    if keys[0] == "curve":
        # punch / ease: a hard step to the same held framing. push_in /
        # pull_out: the same push, linear (no soft start) — still from 1.0
        # to its strength over its window.
        z.pop("overshoot", None)
        z["ramp_s"] = 0.0
    return z


_SHED_STAGES = (("shake", "shake_hz", "shake_decay"), ("rotate",),
                ("curve",))


def camera_chain(in_label, out_label, W, H, fps, shots, targeted=True,
                 max_groups=MAX_GROUPS, max_instances=MAX_INSTANCES,
                 interpolation=None):
    """The shared camera as a chain of `perspective` filters.

    Every non-identity frame is served by exactly one filter whose text
    holds every move active on it (overlapping moves sum — the
    single-filter maths) and every filter is enabled only on its own frames
    (see _plan_chain). Frames covered only by shots with mirrors are
    classified by their python-computed corners: identity frames get no
    filter, the longest holds get build-once constant-corner instances, and
    the rest are evaluated per frame. Shots without a mirror (takeovers,
    travelling paths, aspect-shift pushes) run per frame over their window.

    `max_groups` / `max_instances` bound the per-frame and total instance
    counts (each instance allocates a W*H*8-byte map); `interpolation`
    overrides INTERPOLATION ('linear' for draft previews).

    Every corner expression is re-parsed per frame, so one instance's text
    is bounded (INSTANCE_TEXT_BUDGET): past it the longest shakes in the
    over-budget stretch are shed first (the zoom itself stays), then rolls,
    then eased punch/ease curves become hard steps at the same framing and
    pushes lose their soft start — and the renderer's log says so. A render
    that loses a shake beats a render that cannot start. Returns the filter
    strings, ending on out_label.
    """
    # The graph's fps filters run at fps rounded to 3 decimals (29.970 for
    # NTSC) and the expressions read (on-1)/29.970: the planner's frame
    # clock must be that same one.
    fps = round(float(fps), 3)
    if not shots:
        return [f"[{in_label}]null[{out_label}]"]
    shots = list(shots)

    def plan_():
        return _plan_chain(W, H, fps, shots, targeted, max_groups,
                           max_instances)
    plan = plan_()
    # Shed texture, never framing. Shots are shed in batches sized to the
    # overflow (each costs ~8x its term text, once per corner), only from
    # the stretches that are over, and the chain re-planned once per batch.
    for keys in _SHED_STAGES:
        for _round in range(8):
            over = [(size - INSTANCE_TEXT_BUDGET, ks)
                    for size, ks in plan.groups
                    if size > INSTANCE_TEXT_BUDGET]
            if not over:
                break
            pool = sorted({k for _o, ks in over for k in ks
                           if _sheddable(shots[k], keys[0])},
                          key=lambda k: (-_term_text(shots[k].terms), k))
            if not pool:
                break
            need, saved = 1.1 * sum(o for o, _ks in over), 0
            for k in pool:
                s = shots[k]
                _log.warning("camera text over budget: dropped %s on zoom "
                             "%s", keys[0], s.src.get("id"))
                shots[k] = s.rebuild(_shed(s.src, keys))
                saved += 8 * max(0, _term_text(s.terms)
                                 - _term_text(shots[k].terms))
                if saved >= need:
                    break
            plan = plan_()
    if any(size > INSTANCE_TEXT_BUDGET for size, _ks in plan.groups):
        _log.warning("camera text still over budget after shedding "
                     "(%d chars in one instance): rendering it anyway",
                     max(size for size, _ks in plan.groups))
    return _emit(plan, in_label, out_label, W, H, fps, interpolation)


def _term_text(tm):
    return sum(len(lit(v)) for v in (tm.z, tm.rot, tm.dx, tm.dy, tm.amp,
                                     tm.cx, tm.cy)
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


# --------------------------------------------------------------------------
# Cut hygiene: zoom edges that sit on a program cut
# --------------------------------------------------------------------------

# A zoom edge within this many frames of a program cut is moved ONTO the cut
# and holds its strength through it (hold_through_cuts). Judged on the Oct
# 2026 showcase shorts: an 'ease' ending on a cut released over its last
# frames, so the wide flashed a frame before the new shot; an 'ease' starting
# on a jump cut began at 1.0x and left the jump bare; and an edge a frame or
# two off its cut changed the framing twice, once at the edge and again at
# the cut.
CUT_HOLD_FRAMES = 4
# Modes whose START moves onto a nearby cut (they are visibly pushed in from
# their first frame, or punch in there) and modes whose END does (still
# pushed in on their last frame). pulse and shake are beat devices — a cut is
# not their clock — and follow/path travel on authored waypoints.
_CUT_START_MODES = ("punch", "ease", "pull_out", "landing")
_CUT_END_MODES = ("punch", "ease", "push_in")
# A snap never leaves a zoom shorter than this.
_CUT_SNAP_MIN_SPAN_S = 0.2


def cut_reach_s(fps):
    """How far (seconds) a zoom edge may sit from a cut and still snap."""
    fps = float(fps or 30.0)
    return CUT_HOLD_FRAMES / max(fps, 1.0) + 1e-6


def hold_through_cuts(zooms, cuts, fps, out_duration=None):
    """The zooms as the camera renders them around program cuts.

    A zoom whose start or end lies within CUT_HOLD_FRAMES of a cut is moved
    onto that cut and HOLDS there instead of ramping across it: an eased
    release that would complete in the last frames before the cut stays at
    full strength until the cut (`_hold_out`), and a punch or ease that
    starts on a cut is already at full strength on the cut's first frame
    (`_hold_in`) — the cut itself changes the framing, once. An overshoot
    slam keeps its attack (envelope). `out_duration`, when given, counts as
    a cut for zoom ENDS: the programme's last frame cuts to the end card (or
    back to the start of a loop), and a zoom overhanging it ends there.

    `cuts` are program seconds (renderer.camera_cuts). Returns a new list;
    a zoom nothing touches is the SAME dict object, so an edit with no zoom
    near a cut renders byte-identically to the camera before this rule, and
    `changed()` can tell. Moved zooms are copies — the EDL is never edited.
    """
    zooms = list(zooms or [])
    pts = sorted({round(float(c), 4) for c in cuts or []})
    reach = cut_reach_s(fps)
    end_pts = list(pts)
    if out_duration is not None:
        end_pts = sorted(set(end_pts) | {round(float(out_duration), 4)})
    if not zooms or not end_pts:
        return zooms

    def nearest(t, among):
        best = None
        for c in among:
            d = abs(c - t)
            if d <= reach and (best is None or d < abs(best - t)):
                best = c
        return best

    out = []
    for z in zooms:
        mode = z.get("mode") or "punch"
        try:
            a, b = float(z["start"]), float(z["end"])
        except (KeyError, TypeError, ValueError):
            out.append(z)
            continue
        na, nb, held_in, held_out = a, b, False, False
        if mode in _CUT_START_MODES:
            c = nearest(a, pts)
            if c is not None and nb - c >= _CUT_SNAP_MIN_SPAN_S:
                na, held_in = c, True
        if mode in _CUT_END_MODES:
            c = nearest(b, end_pts)
            if out_duration is not None and b >= float(out_duration):
                # Overhanging the programme: the renderer clamps the window
                # to its end already, so only an ease's release changes.
                c = round(float(out_duration), 4) if mode == "ease" else None
            if c is not None and c - na >= _CUT_SNAP_MIN_SPAN_S:
                nb, held_out = c, True
        # Flag only a hold that changes the curve: a stepped punch/ease
        # (ramp_s 0) and an overshoot slam render the same either way, so
        # they keep their dict (and an old render of them stays current).
        flags = {}
        ramped = ramp_seconds(z, na, nb) > 1e-3
        if held_in and ramped and mode in ("punch", "ease") and \
                back_c1(_f(z.get("overshoot"), 0.0) or 0.0) <= 0:
            flags["_hold_in"] = True
        if held_out and ramped and mode == "ease":
            flags["_hold_out"] = True
        if not flags and abs(na - a) < 1e-9 and abs(nb - b) < 1e-9:
            out.append(z)
            continue
        out.append(dict(z, start=round(na, 4), end=round(nb, 4), **flags))
    return out


def changed(before, after):
    """Ids (or positions) of the zooms hold_through_cuts moved or held."""
    return [z.get("id") or i for i, (z, n) in enumerate(zip(before, after))
            if z is not n]
