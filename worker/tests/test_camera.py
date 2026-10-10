"""The sub-pixel camera (worker/camera.py).

What is pinned here, and how:

  * ONE ARITHMETIC. The emitted ffmpeg expressions are evaluated by a small
    evaluator below and compared, frame by frame, with the python mirror
    (camera.corners over float Terms) for every mode and knob. If the text
    and the mirror ever drift, zoom_state_at, stitch's proof clipping and the
    static-hold planner would all be lying about what renders.
  * THE CURVES are what they claim: exact endpoints, the overshoot the caller
    asked for, a landing that starts pushed in and ends at 1, a pulse that
    peaks at its strength, a push that starts without a velocity kink.
  * THE PLAN. camera_chain enables exactly one filter on every frame that is
    not identity and none on identity frames; held frames get constant
    corners equal to the mirror's.
  * NO EDGE SMEAR: roll and shake never put the sampled window outside the
    source frame, whatever the target.
  * RENDERED PIXELS (ffmpeg): a slow push moves a tracked edge monotonically
    (zoompan moved it backwards on 50 of 119 frames), a held punch frames the
    crop it claims, idle frames are bit-exact copies, and +rotate is
    clockwise on screen.
  * The schema, the add_zoom tool and stitch's clipping speak the new modes.

Run:  python -m pytest tests/test_camera.py -q     (from worker/)
"""
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402

import camera                                                 # noqa: E402
import renderer                                               # noqa: E402
import stitch                                                 # noqa: E402
from schemas import EDLValidationError, validate_edl          # noqa: E402
from timeline import Timeline                                 # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not present")


# ------------------------------------------------------------------ #
#  A tiny evaluator for the ffmpeg expression subset camera emits     #
# ------------------------------------------------------------------ #

_TOKEN = re.compile(r"\s*(?:(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)"
                    r"|([A-Za-z_][A-Za-z_0-9]*)|(\S))")


def _tokens(text):
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1):
            out.append(("num", float(m.group(1))))
        elif m.group(2):
            out.append(("id", m.group(2)))
        elif m.group(3):
            out.append(("op", m.group(3)))
    return out


class _Parser:
    def __init__(self, text):
        self.t = _tokens(text)
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, val=None):
        tok = self.peek()
        if val is not None:
            assert tok == ("op", val), (tok, val, self.i)
        self.i += 1
        return tok

    def expr(self):
        node = self.sub()
        while self.peek() == ("op", ";"):
            self.take()
            node = ("seq", node, self.sub())
        return node

    def sub(self):
        node = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return ("neg", self.unary())
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.primary()

    def primary(self):
        kind, val = self.take()
        if kind == "num":
            return ("num", val)
        if kind == "id":
            if self.peek() == ("op", "("):
                self.take("(")
                args = [self.expr()]
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.expr())
                self.take(")")
                return ("call", val, args)
            return ("var", val)
        assert (kind, val) == ("op", "("), (kind, val)
        node = self.expr()
        self.take(")")
        return node


def _div(a, b):
    if b == 0:
        return math.copysign(math.inf, a) if a else math.nan
    return a / b


def ff_eval(text, env):
    """Evaluate an ffmpeg expression the way libavutil/eval.c does for the
    functions camera uses (lazy if/ifnot, st/ld registers, floored mod)."""
    regs = [0.0] * 10
    tree = _Parser(text).expr()

    def ev(n):
        k = n[0]
        if k == "num":
            return n[1]
        if k == "var":
            return {"PI": math.pi, **env}[n[1]]
        if k == "neg":
            return -ev(n[1])
        if k == "seq":
            ev(n[1])
            return ev(n[2])
        if k == "bin":
            a, b = ev(n[2]), ev(n[3])
            return {"+": lambda: a + b, "-": lambda: a - b,
                    "*": lambda: a * b, "/": lambda: _div(a, b)}[n[1]]()
        name, args = n[1], n[2]
        if name == "if":
            return ev(args[1]) if ev(args[0]) else (
                ev(args[2]) if len(args) > 2 else 0.0)
        if name == "ifnot":
            if not ev(args[0]):
                return ev(args[1])
            return ev(args[2]) if len(args) > 2 else 0.0
        if name == "st":
            v = ev(args[1])
            regs[int(ev(args[0]))] = v
            return v
        if name == "ld":
            return regs[int(ev(args[0]))]
        v = [ev(a) for a in args]
        if name == "clip":
            return min(max(v[0], v[1]), v[2])
        if name == "between":
            return 1.0 if v[1] <= v[0] <= v[2] else 0.0
        if name == "lt":
            return 1.0 if v[0] < v[1] else 0.0
        if name == "gt":
            return 1.0 if v[0] > v[1] else 0.0
        if name == "gte":
            return 1.0 if v[0] >= v[1] else 0.0
        if name == "lte":
            return 1.0 if v[0] <= v[1] else 0.0
        if name == "mod":
            return v[0] - math.floor(v[0] / v[1]) * v[1]
        fns = {"min": min, "max": max, "abs": abs, "sin": math.sin,
               "cos": math.cos, "exp": math.exp, "floor": math.floor,
               "pow": math.pow, "sqrt": math.sqrt}
        return float(fns[name](*v))
    return ev(tree)


# ------------------------------------------------------------------ #
#  1. One arithmetic: emitted text == python mirror                   #
# ------------------------------------------------------------------ #

W, H, FPS = 540, 960, 30.0

ZOOM_CASES = [
    {"id": "p0", "start": 1.0, "end": 2.5, "strength": 0.3},
    {"id": "p1", "start": 1.0, "end": 2.5, "strength": 0.25, "cx": 0.7,
     "cy": 0.3, "overshoot": 0.12},
    {"id": "p2", "start": 1.0, "end": 2.5, "strength": 0.2, "ramp_s": 0.0},
    {"id": "e0", "start": 1.0, "end": 3.0, "strength": 0.4, "mode": "ease"},
    {"id": "e1", "start": 1.0, "end": 1.3, "strength": 0.2, "mode": "ease",
     "overshoot": 0.1, "cx": 0.2, "cy": 0.9},
    {"id": "u0", "start": 0.5, "end": 4.0, "strength": 0.1,
     "mode": "push_in"},
    {"id": "o0", "start": 0.5, "end": 4.0, "strength": 0.1,
     "mode": "pull_out", "rotate": -2.0},
    {"id": "l0", "start": 1.0, "end": 1.6, "strength": 0.16,
     "mode": "landing", "cx": 0.55, "cy": 0.35},
    {"id": "q0", "start": 1.0, "end": 1.5, "strength": 0.07,
     "mode": "pulse"},
    {"id": "s0", "start": 1.0, "end": 2.2, "mode": "shake"},
    {"id": "x0", "start": 1.0, "end": 2.5, "strength": 0.2, "rotate": 3.0,
     "shake": 0.6, "shake_hz": 12, "shake_decay": 3, "cx": 1.0, "cy": 0.0},
]


def _frames(z, extra=0.2):
    n0 = int(max(0.0, z["start"] - extra) * FPS)
    n1 = int((z["end"] + extra) * FPS)
    return range(n0, n1 + 1)


@pytest.mark.parametrize("z", ZOOM_CASES, ids=lambda z: z["id"])
def test_emitted_corners_equal_the_python_mirror(z):
    T = camera.time_var(FPS)
    a, b = z["start"], z["end"]
    exprs = camera.corner_exprs(W, H, [camera.zoom_terms(z, T, a, b)],
                                fps=FPS)
    for n in _frames(z):
        want = camera.corners(W, H, [camera.zoom_terms(z, n / FPS, a, b)])
        got = [ff_eval(e, {"on": n + 1}) for e in exprs]
        assert all(math.isfinite(g) for g in got), (n, got)
        assert max(abs(g - w) for g, w in zip(got, want)) < 1e-6, (n, got,
                                                                    want)


def test_overlapping_shots_sum_like_the_single_filter():
    T = camera.time_var(FPS)
    zs = [ZOOM_CASES[1], ZOOM_CASES[3], ZOOM_CASES[10]]
    exprs = camera.corner_exprs(
        W, H, [camera.zoom_terms(z, T, z["start"], z["end"]) for z in zs],
        fps=FPS)
    for n in range(20, 100, 3):
        t = n / FPS
        want = camera.corners(W, H, [camera.zoom_terms(
            z, t, z["start"], z["end"]) for z in zs])
        got = [ff_eval(e, {"on": n + 1}) for e in exprs]
        assert max(abs(g - w) for g, w in zip(got, want)) < 1e-6, n


def test_expressions_stay_finite_far_outside_their_window():
    """Every branch of every product is evaluated by ffmpeg, so an exp() or
    a division that blows up outside the window turns 0*inf into NaN."""
    T = camera.time_var(FPS)
    for z in ZOOM_CASES:
        exprs = camera.corner_exprs(W, H, [camera.zoom_terms(
            z, T, z["start"], z["end"])], fps=FPS)
        for n in (0, 1, int(600 * FPS), int(3600 * FPS)):
            got = [ff_eval(e, {"on": n + 1}) for e in exprs]
            assert all(math.isfinite(g) for g in got), (z["id"], n)


def test_no_semicolons_reach_the_filtergraph():
    """';' is also the filtergraph chain separator: renderer's audio-only
    graph pruner splits on it. Sequencing goes through ifnot()."""
    T = camera.time_var(FPS)
    for z in ZOOM_CASES:
        for e in camera.corner_exprs(W, H, [camera.zoom_terms(
                z, T, z["start"], z["end"])], fps=FPS):
            assert ";" not in e and "'" not in e and ":" not in e


# ------------------------------------------------------------------ #
#  2. The curves                                                      #
# ------------------------------------------------------------------ #

def _z(z, t):
    return 1.0 + camera.zoom_terms(z, t, z["start"], z["end"]).z


def test_punch_snaps_in_holds_exactly_and_cuts_out():
    z = {"id": "p", "start": 1.0, "end": 2.0, "strength": 0.3}
    assert _z(z, 1.0) == 1.0
    # expo-out: most of the move in the first frames, then a soft landing
    assert _z(z, 1.0 + 1 / 30) - 1.0 > 0.6 * 0.3
    assert _z(z, 1.2) == 1.3 and _z(z, 1.99) == 1.3     # exact hold
    # hard cut out — and the frame AT `end` is already out (half-open), so
    # a punch ending on a cut never holds the next shot's first frame
    assert _z(z, 2.0) == 1.0
    vals = [_z(z, 1.0 + k / 300) for k in range(40)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))


def test_punch_overshoot_passes_the_target_by_what_was_asked():
    for o in (0.05, 0.1, 0.2):
        z = {"id": "p", "start": 0.0, "end": 2.0, "strength": 0.5,
             "overshoot": o}
        peak = max(_z(z, k / 1000.0) for k in range(0, 400))
        assert abs((peak - 1.0) / 0.5 - (1.0 + o)) < 2e-3, (o, peak)
        assert _z(z, 0.5) == 1.5                         # settled


def test_ramp_zero_is_the_legacy_instant_step():
    z = {"id": "p", "start": 1.0, "end": 2.0, "strength": 0.3, "ramp_s": 0}
    assert _z(z, 1.0) == 1.3 and _z(z, 0.99) == 1.0


def test_ease_is_smooth_at_both_ends_and_holds_between():
    z = {"id": "e", "start": 0.0, "end": 2.0, "strength": 0.2,
         "mode": "ease"}
    assert camera.ramp_seconds(z, 0.0, 2.0) == 0.5
    assert _z(z, 0.0) == 1.0 and _z(z, 1.0) == 1.2 and _z(z, 2.0) == 1.0
    # zero velocity at the ramp ends (smootherstep)
    d = 1e-4
    assert abs(_z(z, d) - 1.0) / d < 1e-3
    assert abs(_z(z, 0.5) - _z(z, 0.5 - d)) / d < 1e-3


def test_landing_starts_pushed_in_and_settles_to_the_wide():
    z = {"id": "l", "start": 3.0, "end": 3.5, "strength": 0.15,
         "mode": "landing"}
    assert _z(z, 3.0) == pytest.approx(1.15)
    assert _z(z, 3.0 + 0.35) == 1.0 and _z(z, 3.45) == 1.0
    vals = [_z(z, 3.0 + k / 100) for k in range(36)]
    assert all(b <= a for a, b in zip(vals, vals[1:]))
    assert _z(z, 2.99) == 1.0


def test_pulse_peaks_at_its_strength_and_returns():
    z = {"id": "q", "start": 1.0, "end": 1.3, "strength": 0.07,
         "mode": "pulse"}
    vals = [_z(z, 1.0 + k / 1000) for k in range(0, 301)]
    assert vals[0] == 1.0 and abs(vals[-1] - 1.0) < 1e-12
    assert max(vals) == pytest.approx(1.07, abs=1e-6)
    assert vals.index(max(vals)) == pytest.approx(0.35 * 300, abs=2)


def test_push_in_starts_without_a_velocity_kink():
    z = {"id": "u", "start": 0.0, "end": 4.0, "strength": 0.1,
         "mode": "push_in"}
    v = [(_z(z, (k + 1) / 30) - _z(z, k / 30)) for k in range(0, 60)]
    assert v[0] < v[20] / 5.0                 # starts slow...
    assert all(b >= a - 1e-12 for a, b in zip(v, v[1:]))    # ...accelerates
    assert abs(v[30] - v[50]) < 1e-9          # then constant speed
    assert _z(z, 4.0 - 1e-9) == pytest.approx(1.1)


def test_shake_is_noise_that_decays_not_a_sine():
    z = {"id": "s", "start": 0.0, "end": 3.0, "mode": "shake",
         "shake_decay": 0.0}
    xs = np.array([camera.zoom_terms(z, k / 60.0, 0.0, 3.0).dx
                   for k in range(30, 150)])
    assert np.abs(xs).max() > 0.004
    # a sine's autocorrelation returns to ~1 at its period; noise does not
    xs = xs - xs.mean()
    ac = [float(np.dot(xs[:-lag], xs[lag:]) / np.dot(xs, xs))
          for lag in range(4, 60)]
    assert max(ac) < 0.85
    decaying = dict(z, shake_decay=6.0)
    early = max(abs(camera.zoom_terms(decaying, t, 0, 3).dx)
                for t in np.linspace(0.03, 0.3, 40))
    late = max(abs(camera.zoom_terms(decaying, t, 0, 3).dx)
               for t in np.linspace(1.0, 1.3, 40))
    assert late < early * 0.05


def test_rolled_and_shaken_windows_never_leave_the_frame():
    """perspective clamps samples outside the source to the EDGE pixel — a
    smear. Cover zoom + centre clamp must keep every corner inside."""
    for z in (ZOOM_CASES[6], ZOOM_CASES[9], ZOOM_CASES[10],
              {"id": "hard", "start": 0.0, "end": 2.0, "strength": 0.05,
               "rotate": 15.0, "shake": 1.0, "cx": 0.0, "cy": 1.0}):
        for wh in ((540, 960), (960, 540), (720, 720)):
            w_, h_ = wh
            for n in range(0, 140):
                cs = camera.corners(w_, h_, [camera.zoom_terms(
                    z, n / FPS, z["start"], z["end"])])
                tl, tr, bl = (cs[0], cs[1]), (cs[2], cs[3]), (cs[4], cs[5])
                # perspective samples output pixel (i, j) at
                # TL + i/W (TR-TL) + j/H (BL-TL): the extreme samples are
                # the four corner PIXELS, i, j in {0, W-1} x {0, H-1}.
                for i in (0, w_ - 1):
                    for j in (0, h_ - 1):
                        x = tl[0] + i / w_ * (tr[0] - tl[0]) \
                            + j / h_ * (bl[0] - tl[0])
                        y = tl[1] + i / w_ * (tr[1] - tl[1]) \
                            + j / h_ * (bl[1] - tl[1])
                        assert -0.5 - 1e-6 <= x <= w_ - 0.5 + 1e-6, (
                            z["id"], wh, n, x)
                        assert -0.5 - 1e-6 <= y <= h_ - 0.5 + 1e-6, (
                            z["id"], wh, n, y)


def test_zoom_state_at_includes_the_cover_zoom_and_mirrors_new_modes():
    land = {"id": "l", "start": 2.0, "end": 2.5, "strength": 0.15,
            "mode": "landing", "cx": 0.6, "cy": 0.4}
    z, cx, cy = renderer.zoom_state_at([land], 2.0, 10.0)
    assert z == pytest.approx(1.15) and cx == pytest.approx(0.6)
    assert renderer.zoom_state_at([land], 2.4, 10.0)[0] == 1.0
    rolled = {"id": "r", "start": 0.0, "end": 2.0, "strength": 0.01,
              "rotate": 4.0}
    z = renderer.zoom_state_at([rolled], 1.0, 10.0, size=(1080, 1920))[0]
    # 4 degrees of roll in 9:16 needs cos + (16/9) sin of zoom to stay
    # covered — far more than the 1% the zoom asked for.
    k = 1920 / 1080
    r = math.radians(4.0)
    assert z == pytest.approx(math.cos(r) + k * math.sin(r), rel=1e-6)


# ------------------------------------------------------------------ #
#  3. The plan: which filter runs on which frame                      #
# ------------------------------------------------------------------ #

def _enabled(filter_text, t):
    m = re.search(r"enable='([^']*)'", filter_text)
    if not m:
        return True
    return any(float(a) <= t <= float(b) for a, b in re.findall(
        r"between\(t,(-?[\d.]+),(-?[\d.]+)\)", m.group(1)))


def _corner_values(filter_text, n):
    vals = []
    for name in ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3"):
        m = re.search(rf"{name}='([^']*)'", filter_text)
        vals.append(ff_eval(m.group(1), {"on": n + 1}))
    return vals


def test_chain_runs_exactly_one_correct_filter_per_moving_frame():
    zooms = [ZOOM_CASES[0], dict(ZOOM_CASES[3], start=3.0, end=5.0),
             dict(ZOOM_CASES[7], start=5.6, end=6.2),
             dict(ZOOM_CASES[10], start=7.0, end=8.5),
             dict(ZOOM_CASES[1], start=7.5, end=9.0)]
    shots = [camera.zoom_shot(z, z["start"], z["end"], FPS) for z in zooms]
    chain = camera.camera_chain("in", "out", W, H, FPS, shots)
    assert chain[0].startswith("[in]") and chain[-1].endswith("[out]")
    held = [f for f in chain if ":eval=frame" not in f]
    assert held, "a punch's hold must get a constant-corner instance"
    for n in range(0, int(9.5 * FPS)):
        t = n / FPS
        want = camera.corners(W, H, [s.mirror(t) for s in shots])
        identity = want == [0.0, 0.0, W, 0.0, 0.0, H, W, H]
        on = [f for f in chain if _enabled(f, t)]
        if identity:
            for f in on:                     # padding frames: identity only
                assert max(abs(a - b) for a, b in zip(
                    _corner_values(f, n), want)) < 1e-6
            continue
        assert len(on) == 1, (t, len(on))
        got = _corner_values(on[0], n)
        assert max(abs(a - b) for a, b in zip(got, want)) < 1e-5, t


def test_shots_without_a_mirror_run_per_frame_over_their_window():
    T = camera.time_var(FPS)
    shot = camera.Shot(1.0, 2.0, camera.Terms(
        f"0.2*between({T},1,2)"))
    chain = camera.camera_chain("a", "b", W, H, FPS, [shot])
    assert len(chain) == 1 and ":eval=frame" in chain[0]
    assert _enabled(chain[0], 1.5) and not _enabled(chain[0], 2.5)


@pytest.mark.parametrize("shifts", [
    [{"at": 2.0, "ratio": "1:1", "duration_s": 0.8}],
    [{"at": 2.0, "ratio": "1:1", "duration_s": 0.8},
     {"at": 5.0, "ratio": "source", "duration_s": 0.5}],
    [{"at": 1.0, "ratio": "4:5", "duration_s": 0.6},
     {"at": 3.0, "ratio": "source", "duration_s": 0.6},
     {"at": 6.0, "ratio": "1:1", "duration_s": 1.0}],
    [{"at": 0.0, "ratio": "1:1", "duration_s": 0.8}],
], ids=["hold-to-end", "there-and-back", "back-then-again", "from-zero"])
def test_an_aspect_shift_push_runs_only_where_it_is_nonzero(shifts):
    """The shift's push used to force ONE always-on per-frame filter: cubic
    perspective on every programme frame. It now runs over its own support
    only, and that support provably contains every non-zero frame."""
    import screenframe
    import travel
    prog = 9.0
    _w, _h, zpts = screenframe.shift_tracks(shifts, 1080, 1920, prog)
    T = camera.time_var(FPS)
    zexp = travel.path_value_expr(zpts, "v", str(T), 0.0, prog, default=0.0,
                                  ease="cubic_in_out")
    a, b = renderer._shift_push_window(zpts, prog)
    text = camera.seq([camera.time_statement(FPS)], zexp)
    nonzero = []
    for n in range(int(prog * FPS) + 1):
        v = ff_eval(text, {"on": n + 1})
        if abs(v) > 1e-9:
            nonzero.append(n / FPS)
            assert a <= n / FPS <= b, (n / FPS, v, a, b)
    assert nonzero
    # tight: it starts on the first moving frame, not at 0
    assert a >= nonzero[0] - 0.05
    assert b <= nonzero[-1] + 0.05 or b == prog
    shot = camera.Shot(a, b, camera.Terms(f"({zexp})"))
    chain = camera.camera_chain("a", "b", 1080, 1920, FPS, [shot])
    assert all("enable=" in f for f in chain)
    if nonzero[0] > 0.5:
        assert not any(_enabled(f, nonzero[0] - 0.2) for f in chain)


def test_an_aspect_shift_in_the_graph_leaves_its_lead_in_untouched():
    edl = validate_edl({"keep": [[0.0, 8.0]], "effects": {
        "frame_shifts": [{"id": "s", "at": 4.0, "ratio": "1:1"}]}},
        8.0).model_dump()
    g = renderer.build_filtergraph(
        edl, 8.0, True, Timeline(edl["keep"], []), None, [],
        {"video": {"duration": 8.0}, "words": [], "sentences": []},
        preview=False, W=W, H=H, fps=FPS)
    cams = [f for f in g.split(";") if "perspective=" in f]
    assert cams and all("enable=" in f for f in cams)
    assert not any(_enabled(f, 2.0) for f in cams)
    assert any(_enabled(f, 6.0) for f in cams)


def test_graph_without_zooms_has_no_camera():
    edl = validate_edl({"keep": [[0.0, 5.0]]}, 5.0).model_dump()
    g = renderer.build_filtergraph(
        edl, 5.0, True, Timeline(edl["keep"], []), None, [],
        {"video": {"duration": 5.0}, "words": [], "sentences": []},
        preview=False, W=W, H=H, fps=FPS)
    assert "perspective" not in g and "zoompan" not in g


# ------------------------------------------------------------------ #
#  4. Rendered pixels                                                 #
# ------------------------------------------------------------------ #

RW, RH = 360, 640


@pytest.fixture(scope="module")
def workdir():
    d = tempfile.mkdtemp(prefix="camera_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _card(kind):
    img = np.full((RH, RW), 20, np.uint8)
    if kind == "lines":
        for x in range(30, RW, 80):
            img[:, x:x + 2] = 235
    elif kind == "hline":
        img[RH // 2 - 1:RH // 2 + 1, :] = 235
    else:                                    # textured, for exactness
        rng = np.random.default_rng(7)
        img = rng.integers(0, 255, (RH, RW), dtype=np.uint8)
        img = (img // 2 + 60).astype(np.uint8)
    return img


def _clip_path(workdir, kind, dur):
    path = os.path.join(workdir, f"{kind}.mkv")
    if os.path.exists(path):
        return path
    frame = np.repeat(_card(kind)[:, :, None], 3, axis=2)
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt",
         "rgb24", "-s", f"{RW}x{RH}", "-r", str(int(FPS)), "-i", "pipe:0",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={dur}",
         "-c:v", "ffv1", "-pix_fmt", "yuv444p", "-c:a", "pcm_s16le",
         "-shortest", path], stdin=subprocess.PIPE,
        stderr=subprocess.PIPE)
    for _ in range(int(dur * FPS)):
        p.stdin.write(frame.tobytes())
    p.stdin.close()
    assert p.wait() == 0, p.stderr.read().decode()
    return path


def _render_gray(src, edl_dict, dur):
    """All frames of build_filtergraph's output as a gray uint8 array."""
    edl = validate_edl(dict(edl_dict), dur).model_dump()
    tl = Timeline(edl["keep"], [])
    g = renderer.build_filtergraph(
        edl, dur, True, tl, None, [],
        {"video": {"duration": dur}, "words": [], "sentences": []},
        preview=False, W=RW, H=RH, fps=FPS)
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src, "-filter_complex", g,
         "-map", "[vout]", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
         "-map", "[aout]", "-f", "null", "-"],
        capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-2000:]
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, RH, RW), g


@needs_ffmpeg
def test_a_slow_push_moves_monotonically_sub_pixel(workdir):
    dur = 4.0
    src = _clip_path(workdir, "lines", dur)
    frames, g = _render_gray(src, {"keep": [[0.0, dur]], "effects": {
        "zooms": [{"id": "z", "start": 0.0, "end": dur, "strength": 0.1,
                   "mode": "push_in", "ramp_s": 0}]}}, dur)
    assert "perspective" in g and "zoompan" not in g
    xs, prev = [], 110.0                 # the line at x=110 drifts left
    for f in frames[:int(dur * FPS) - 1].astype(np.float64):
        row = f[RH // 2 - 100:RH // 2 + 100].mean(axis=0)
        lo = int(prev) - 12
        seg = np.clip(row[lo:lo + 24] - 60, 0, None)
        prev = lo + float((seg * np.arange(24)).sum() / max(seg.sum(), 1e-9))
        xs.append(prev)
    v = np.diff(np.array(xs))
    assert (v > 0.02).sum() == 0, "the edge stepped backwards"
    assert (np.abs(v) < 0.005).sum() <= 2, "the edge froze"
    assert v.std() < 0.08, v.std()


@needs_ffmpeg
def test_a_held_punch_frames_exactly_the_window_it_claims(workdir):
    import cv2
    dur = 3.0
    src = _clip_path(workdir, "tex", dur)
    z = {"id": "z", "start": 0.5, "end": 2.5, "strength": 0.6, "cx": 0.7,
         "cy": 0.25}
    frames, _g = _render_gray(src, {"keep": [[0.0, dur]],
                                    "effects": {"zooms": [z]}}, dur)
    got = frames[int(1.5 * FPS)].astype(np.float64)
    zoom, cx, cy = renderer.zoom_state_at([z], 1.5, dur)
    assert zoom == pytest.approx(1.6)
    # Centre-aligned map: out pixel centre (i+.5)/zoom + crop origin.
    x0, y0 = (RW - RW / zoom) * cx, (RH - RH / zoom) * cy
    M = np.float32([[1 / zoom, 0, x0 + 0.5 / zoom - 0.5],
                    [0, 1 / zoom, y0 + 0.5 / zoom - 0.5]])
    want = cv2.warpAffine(_card("tex").astype(np.float32), M, (RW, RH),
                          flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP)
    inner = (slice(8, RH - 8), slice(8, RW - 8))
    # Measured 1.3 grey levels on this random texture; without the
    # pixel-centre term (perspective's corner-anchored map) it is ~11.
    assert np.abs(got[inner] - want[inner]).mean() < 3.0
    corner_anchored = cv2.warpAffine(
        _card("tex").astype(np.float32),
        np.float32([[1 / zoom, 0, x0], [0, 1 / zoom, y0]]), (RW, RH),
        flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP)
    assert np.abs(got[inner] - corner_anchored[inner]).mean() > 6.0


@needs_ffmpeg
def test_idle_frames_are_exact_copies(workdir):
    dur = 3.0
    src = _clip_path(workdir, "tex", dur)
    frames, g = _render_gray(src, {"keep": [[0.0, dur]], "effects": {
        "zooms": [{"id": "z", "start": 1.0, "end": 2.0, "strength": 0.3,
                   "mode": "ease"}]}}, dur)
    plain, _ = _render_gray(src, {"keep": [[0.0, dur]]}, dur)
    for n in (0, 10, 29, 30, 61, 75):          # outside, edges, after
        assert np.array_equal(frames[n], plain[n]), n
    assert not np.array_equal(frames[45], plain[45])


@needs_ffmpeg
def test_positive_rotate_turns_the_picture_clockwise(workdir):
    dur = 2.0
    src = _clip_path(workdir, "hline", dur)
    frames, _g = _render_gray(src, {"keep": [[0.0, dur]], "effects": {
        "zooms": [{"id": "z", "start": 0.2, "end": 1.8, "strength": 0.3,
                   "rotate": 6.0, "ramp_s": 0}]}}, dur)
    f = frames[int(1.0 * FPS)].astype(np.float64)

    def line_y(col):
        c = np.clip(f[:, col] - 60, 0, None)
        return float((c * np.arange(RH)).sum() / max(c.sum(), 1e-9))
    # y grows downwards: clockwise = the right end of the line sits lower.
    assert line_y(RW - 40) - line_y(40) > 10


# ------------------------------------------------------------------ #
#  5. Schema, tool, stitch                                            #
# ------------------------------------------------------------------ #

def _validate(z, dur=10.0):
    return validate_edl({"keep": [[0.0, dur]], "effects": {"zooms": [z]}},
                        dur).model_dump()["effects"]["zooms"][0]


def test_schema_accepts_the_new_modes_and_clamps_the_knobs():
    z = _validate({"id": "z", "start": 1.0, "end": 2.0, "strength": 0.2,
                   "overshoot": 0.9, "rotate": 40, "ramp_s": 9,
                   "shake": 2, "shake_hz": 100, "shake_decay": -3})
    assert (z["overshoot"], z["rotate"], z["ramp_s"]) == (0.5, 15.0, 3.0)
    assert (z["shake"], z["shake_hz"], z["shake_decay"]) == (1.0, 30.0, 0.0)
    for mode in ("landing", "pulse", "shake"):
        assert _validate({"id": "z", "start": 1.0, "end": 1.5,
                          "mode": mode})["mode"] == mode


@pytest.mark.parametrize("bad", [
    {"mode": "landing", "overshoot": 0.1},
    {"mode": "shake", "rotate": 2},
    {"mode": "shake", "ramp_s": 0.2},
    {"mode": "shake", "shake": 0},
    {"shake_hz": 9},
    {"mode": "follow", "ramp_s": 0.3,
     "path": [{"f": 0, "cx": 0.2, "cy": 0.2}, {"f": 1, "cx": 0.8, "cy": 0.8}]},
])
def test_schema_refuses_knobs_the_mode_would_ignore(bad):
    with pytest.raises(EDLValidationError):
        _validate({"id": "z", "start": 1.0, "end": 2.0, **bad})


def test_old_zooms_keep_their_exact_dump():
    """New fields are None-by-default, and _sig_canon drops None keys, so no
    stored EDL changes signature (or loses its cached render)."""
    import json
    from schemas import edl_signature
    zoom = {"id": "z", "start": 1.0, "end": 2.0, "strength": 0.2, "cx": 0.3,
            "cy": 0.4, "mode": "ease"}
    a = validate_edl({"keep": [[0.0, 10.0]], "effects": {"zooms": [zoom]}},
                     10.0).model_dump()
    z = a["effects"]["zooms"][0]
    assert all(z[k] is None for k in camera_fields())
    canon = json.loads(edl_signature(a))["effects"]["zooms"][0]
    assert not any(k in canon for k in camera_fields())


def camera_fields():
    return ("ramp_s", "overshoot", "rotate", "shake", "shake_hz",
            "shake_decay")


class _Ctx:
    has_main_video = True
    user_message = ""
    edit_plan = {}

    def __init__(self, edl):
        self.edl = edl
        self.index = {"words": [], "sentences": [],
                      "video": {"duration": 20.0}}

    def latest_edl(self):
        return {"json": self.edl}

    def write_edl(self, edl, desc):
        validate_edl(dict(edl), 20.0)
        self.edl = edl
        self.desc = desc
        return f"EDL v1 -> v2: {desc}"


def test_add_zoom_writes_the_camera_knobs():
    import agent_tools
    ctx = _Ctx({"keep": [[0.0, 8.0], [10.0, 20.0]]})
    res = agent_tools.add_zoom(ctx, 2.0, 4.0, mode="punch", cx=0.5, cy=0.4,
                               overshoot=0.1, rotate=2, shake=0.4)
    z = ctx.edl["effects"]["zooms"][-1]
    assert (z.get("mode"), z["overshoot"], z["rotate"], z["shake"]) == (
        None, 0.1, 2.0, 0.4)
    assert "overshoot" in res and "roll" in res and "shake" in res
    res = agent_tools.add_zoom(ctx, 8.0, 8.4, mode="landing", cx=0.5,
                               cy=0.4)
    assert "QUALITY ADVISORY: a landing" not in res     # 8.0 is the cut
    res = agent_tools.add_zoom(ctx, 5.0, 5.4, mode="landing", cx=0.5,
                               cy=0.4)
    assert "QUALITY ADVISORY: a landing" in res and "8" in res
    agent_tools.add_zoom(ctx, 12.0, 12.3, mode="pulse", cx=0.5, cy=0.4)
    assert ctx.edl["effects"]["zooms"][-1]["strength"] == 0.07
    assert agent_tools.add_zoom(ctx, 13.0, 14.0, mode="shake",
                                rotate=3).startswith("REJECTED")
    assert agent_tools.add_zoom(ctx, 13.0, 14.0, mode="ease",
                                shake_hz=9).startswith("REJECTED")


def test_a_landing_or_pulse_cannot_be_given_a_ramp_that_renders_nothing():
    """A landing/pulse IS its ramp: ramp_s=0 used to validate, report
    'landing zoom 15%' and render nothing at all."""
    import agent_tools
    for mode in ("landing", "pulse"):
        with pytest.raises(EDLValidationError, match="at least"):
            _validate({"id": "z", "start": 1.0, "end": 1.5, "mode": mode,
                       "ramp_s": 0.0})
        assert _validate({"id": "z", "start": 1.0, "end": 1.5,
                          "mode": mode, "ramp_s": 0.06})["ramp_s"] == 0.06
        ctx = _Ctx({"keep": [[0.0, 8.0], [10.0, 20.0]]})
        res = agent_tools.add_zoom(ctx, 8.0, 8.4, mode=mode, ramp_s=0)
        assert res.startswith("REJECTED") and "0.06" in res
        # a hand-made dict that bypassed both still renders its move
        z = {"id": "z", "start": 1.0, "end": 1.5, "strength": 0.15,
             "mode": mode, "ramp_s": 0.0}
        assert max(_z(z, 1.0 + k / 300) for k in range(30)) > 1.05


def test_punch_in_on_emphasis_holds_to_the_cut_or_the_phrase(monkeypatch):
    """Each punch snaps in 60 ms before its word and holds to the nearest
    natural boundary: the next cut, the sentence's end (+0.1 s), at most
    2.6 s, never into the next punch; a sentence too short to hold falls
    back to 0.9 s. The strongest word and spoken numbers slam (overshoot)."""
    import agent_tools

    class Ctx(_Ctx):
        def __init__(self, edl, words, sentences):
            super().__init__(edl)
            self.index = {"words": words, "sentences": sentences,
                          "video": {"duration": 30.0}}

        def write_edl(self, edl, desc):
            validate_edl(dict(edl), 30.0)
            self.edl = edl
            return "EDL v1 -> v2: " + desc

    words = [{"w": w, "t0": t, "t1": t + 0.3} for w, t in (
        ("growth", 2.0), ("business", 8.5), ("customers", 20.0),
        ("2024", 25.0))]
    sentences = [{"t0": 1.8, "t1": 3.5}, {"t0": 8.0, "t1": 14.0},
                 {"t0": 19.5, "t1": 24.4}, {"t0": 24.9, "t1": 25.3}]
    monkeypatch.setattr(agent_tools, "_get_perception", lambda _c: {})
    monkeypatch.setattr(agent_tools.perception, "word_stress",
                        lambda _p, _w: [0.9, 1.0, 0.8, 0.7])
    monkeypatch.setattr(agent_tools, "_face_at_source_moments",
                        lambda *_a: {})
    ctx = Ctx({"keep": [[0.0, 10.0], [12.0, 30.0]]}, words, sentences)
    res = agent_tools.punch_in_on_emphasis(ctx, count=4, strength=0.15)
    assert res.startswith("EDL v1 -> v2"), res
    zs = ctx.edl["effects"]["zooms"]
    assert [z["start"] for z in zs] == [1.94, 8.44, 17.94, 22.94]
    # 3.5+0.1 sentence end; the cut at program 10 (the sentence runs on
    # past it); the 2.6 s cap; a sentence ending 0.36 s in -> 0.9 s hold
    assert [z["end"] for z in zs] == [3.6, 10.0, 20.54, 23.84]
    for z in zs:
        assert z["end"] > z["start"] + 0.2
        assert z["end"] - z["start"] <= agent_tools.PUNCH_HOLD_MAX_S + 1e-9
    assert all(a["end"] <= b["start"] for a, b in zip(zs, zs[1:]))
    # 'business' scored highest; '2024' is a spoken number
    assert [bool(z.get("overshoot")) for z in zs] == [False, True, False,
                                                      True]
    assert all(z.get("mode") is None for z in zs)     # punches
    # every punch re-checks the graphics on screen under it against the
    # face as it frames it (as add_zoom does); what moved is in the reply
    import motion_tools
    seen = []

    def recheck(c, edl, a, b):
        seen.append((a, b))
        return ["KEEP-OUT under the camera move: graphic 'g'."] if a < 5 else []
    monkeypatch.setattr(motion_tools, "keep_out_under_camera", recheck)
    ctx = Ctx({"keep": [[0.0, 10.0], [12.0, 30.0]]}, words, sentences)
    res = agent_tools.punch_in_on_emphasis(ctx, count=4, strength=0.15)
    assert seen == [(z["start"], z["end"]) for z in ctx.edl["effects"]["zooms"]]
    assert "KEEP-OUT under the camera move: graphic 'g'." in res


def test_add_zoom_schema_offers_the_new_modes_and_knobs():
    import agent_tools
    props = agent_tools.TOOLS["add_zoom"][2]
    assert {"landing", "pulse", "shake"} <= set(props["mode"]["enum"])
    for k in camera_fields():
        assert k in props, k
    compact = {t["function"]["name"]: t["function"]["description"]
               for t in agent_tools.openai_tools(compact=True)}
    assert "landing" in compact["add_zoom"] and "punch" in compact["add_zoom"]


def test_stitch_samples_the_camera_curve_when_a_proof_clips_a_zoom():
    land = {"id": "l", "start": 2.0, "end": 2.6, "strength": 0.15,
            "mode": "landing", "cx": 0.6, "cy": 0.4, "rotate": 1.0}
    (clipped,) = stitch._clip_program_zooms([land], 2.1, 10.0)
    assert clipped["mode"] == "path"
    assert not any(k in clipped for k in camera_fields())
    # the strength at the proof's first frame is the landing's, not a reset
    assert clipped["path"][0]["s"] == pytest.approx(
        round(camera.zoom_terms(land, 2.1, 2.0, 2.6).z, 3), abs=1e-3)
    validate_edl({"keep": [[0.0, 7.9]],
                  "effects": {"zooms": [dict(clipped, id="x")]}}, 7.9)
    punch = {"id": "p", "start": 2.0, "end": 4.0, "strength": 0.3}
    (late,) = stitch._clip_program_zooms([punch], 3.0, 10.0)
    assert late["ramp_s"] == 0.0 and late.get("mode") is None
    assert stitch._zoom_strength_at(punch, 3.0) == pytest.approx(0.3)


def _per_frame_sizes(chain):
    return [sum(len(e) for e in re.findall(r"[xy][0-3]='([^']*)'", f))
            for f in chain if ":eval=frame" in f]


def test_the_camera_text_budget_sheds_shakes_never_zooms(caplog,
                                                         monkeypatch):
    """Corner expressions are re-parsed every frame, so one instance's text
    is bounded. Over budget, a reel that shakes on every beat sheds its
    longest shakes first — never a zoom's framing."""
    monkeypatch.setattr(camera, "INSTANCE_TEXT_BUDGET", 15000)
    zooms = []
    for k in range(40):
        z = {"id": f"z{k}", "start": 1.5 * k, "end": 1.5 * k + 1.2,
             "strength": 0.15, "cx": 0.6, "cy": 0.3}
        if k % 2:
            z.update(shake=0.6)
        zooms.append(z)
    shots = [camera.zoom_shot(z, z["start"], z["end"], FPS) for z in zooms]
    with caplog.at_level("WARNING"):
        chain = camera.camera_chain("in", "out", 1080, 1920, FPS, shots)
    assert max(_per_frame_sizes(chain)) <= camera.INSTANCE_TEXT_BUDGET
    assert "dropped shake" in caplog.text
    assert "dropped curve" not in caplog.text
    # a bounded number of instances (each allocates a W*H*8-byte map) ...
    assert len(chain) <= camera.MAX_INSTANCES
    # ... and every zoom still frames its hold: on a held frame exactly one
    # filter is enabled and its corners are the zoom's.
    for z in zooms:
        n = int((z["start"] + 0.8) * FPS)
        on = [f for f in chain if _enabled(f, n / FPS)]
        assert len(on) == 1, z["id"]
        want = camera.corners(1080, 1920, [camera.zoom_terms(
            dict(z, shake=None), n / FPS, z["start"], z["end"])])
        got = _corner_values(on[0], n)
        assert max(abs(a - b) for a, b in zip(got, want)) < 1e-4, z["id"]


@needs_ffmpeg
def test_a_dense_premium_camera_pass_renders(workdir):
    """Forty moves (punches, landings, pulses, pushes, shakes) through the
    real renderer on a 60 s programme: the graph parses and runs."""
    dur = 60.0
    path = os.path.join(workdir, "tex60.mkv")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc2=s={RW}x{RH}:r=30:d={dur}", "-f", "lavfi",
                    "-i", f"anullsrc=r=48000:cl=stereo:d={dur}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", path], check=True)
    modes = ["punch", "landing", "pulse", "ease", "push_in", "shake"]
    zooms = []
    for k in range(40):
        z = {"id": f"z{k}", "start": round(1.4 * k + 0.5, 2),
             "end": round(1.4 * k + 1.7, 2), "strength": 0.15,
             "mode": modes[k % len(modes)], "cx": 0.55, "cy": 0.35}
        if z["mode"] == "punch" and k % 4 == 0:
            z.update(overshoot=0.1, rotate=1.5, shake=0.4)
        zooms.append(z)
    edl = validate_edl({"keep": [[0.0, dur]], "effects": {"zooms": zooms}},
                       dur).model_dump()
    g = renderer.build_filtergraph(
        edl, dur, True, Timeline(edl["keep"], []), None, [],
        {"video": {"duration": dur}, "words": [], "sentences": []},
        preview=False, W=RW, H=RH, fps=FPS)
    assert g.count("perspective=") <= camera.MAX_INSTANCES
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         *renderer._graph_args(g, workdir), "-map", "[vout]",
         "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
         "-map", "[aout]", "-f", "null", "-"], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-2000:]
    assert len(r.stdout) == int(dur * FPS) * RW * RH


@needs_ffmpeg
@pytest.mark.parametrize("under_push", [False, True])
def test_the_planned_chain_renders_exactly_the_single_filter(workdir,
                                                            under_push):
    """End to end: identity frames skipped, holds on build-once instances,
    moves split across consecutive stretches — and every output frame is
    bit-identical to ONE per-frame filter carrying every move."""
    dur, w, h = 6.0, 160, 284
    path = os.path.join(workdir, "tex_small.mkv")
    if not os.path.exists(path):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"testsrc2=s={w}x{h}:r=30:d={dur}", "-c:v", "ffv1",
                        "-pix_fmt", "yuv420p", path], check=True)
    modes = ["punch", "landing", "pulse", "ease", "shake"]
    zooms = [{"id": "push", "start": 0.0, "end": dur, "strength": 0.05,
              "mode": "push_in"}] if under_push else []
    for k in range(5):
        a = round(0.3 + 1.1 * k, 2)
        z = {"id": f"p{k}", "start": a, "end": round(a + 0.8, 2),
             "strength": 0.14, "cx": 0.55, "cy": 0.35, "mode": modes[k]}
        if z["mode"] == "shake":
            z = {"id": z["id"], "start": a, "end": z["end"], "mode": "shake"}
        if z["mode"] == "punch":
            z.update(overshoot=0.1, rotate=1.5)
        zooms += [z, {"id": f"l{k}", "start": round(a + 0.8, 2),
                      "end": round(a + 1.1, 2), "strength": 0.1,
                      "mode": "landing"}]
    shots = [camera.zoom_shot(z, z["start"], z["end"], FPS) for z in zooms]
    import unittest.mock
    with unittest.mock.patch.object(camera, "GROUP_TEXT_MIN", 1500):
        chain = camera.camera_chain("sv", "out", w, h, FPS, shots)
    assert len([f for f in chain if ":eval=frame" in f]) > 1
    assert under_push or any(":eval=frame" not in f for f in chain)
    single = camera._filter(
        "sv", "out", w, h, camera.corner_exprs(
            w, h, [s.terms for s in shots], True, FPS), True, None, FPS)

    def run(graph):
        g = "[0:v]format=yuv420p[sv];" + ";".join(graph)
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", path,
                            "-filter_complex", g, "-map", "[out]", "-f",
                            "rawvideo", "-pix_fmt", "gray", "pipe:1"],
                           capture_output=True)
        assert r.returncode == 0, r.stderr.decode()[-800:]
        return np.frombuffer(r.stdout, np.uint8).reshape(-1, h, w)
    a, b = run(chain), run([single])
    assert len(a) == int(dur * FPS)
    assert np.array_equal(a, b)


@needs_ffmpeg
def test_image_insert_ken_burns_is_sub_pixel_and_stays_on_its_frames(
        workdir):
    """A still is where zoompan's whole-pixel crop shimmered worst. The
    insert's push now rides the shared camera on the insert's own program
    window: monotonic on its frames, untouched footage either side."""
    from PIL import Image
    dur = 2.0
    src = _clip_path(workdir, "tex", dur)
    img = os.path.join(workdir, "lines.png")
    Image.fromarray(_card("lines")).convert("RGB").save(img)
    ins = {"id": "i1", "kind": "image", "asset_key": "lines.png",
           "at_output_s": 1.0, "duration_s": 3.0, "motion": "zoom_in",
           "fit": "crop"}
    edl = validate_edl({"keep": [[0.0, 1.0], [1.0, dur]], "inserts": [ins]},
                       dur).model_dump()
    tl = Timeline(edl["keep"], edl["inserts"])
    g = renderer.build_filtergraph(
        edl, dur, True, tl, None, [],
        {"video": {"duration": dur}, "words": [], "sentences": []},
        preview=False, W=RW, H=RH, fps=FPS,
        insert_inputs=[(1, edl["inserts"][0], False)], silence_idx=2)
    assert "zoompan" not in g and g.count("perspective=") == 1
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src,
         "-loop", "1", "-t", "3.000", "-r", f"{FPS:.3f}", "-i", img,
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-filter_complex", g, "-map", "[vout]", "-f", "rawvideo",
         "-pix_fmt", "gray", "pipe:1", "-map", "[aout]", "-f", "null", "-"],
        capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-2000:]
    frames = np.frombuffer(r.stdout, np.uint8).reshape(-1, RH, RW)
    plain = _render_gray(src, {"keep": [[0.0, dur]]}, dur)[0]
    # footage before (0-1 s) and after (4-5 s) the insert is untouched
    assert np.array_equal(frames[10], plain[10])
    assert np.array_equal(frames[int(4.5 * FPS)], plain[int(1.5 * FPS)])
    xs, prev = [], 110.0
    for f in frames[int(1.0 * FPS):int(4.0 * FPS) - 1].astype(np.float64):
        row = f[RH // 2 - 100:RH // 2 + 100].mean(axis=0)
        lo = int(prev) - 12
        seg = np.clip(row[lo:lo + 24] - 60, 0, None)
        prev = lo + float((seg * np.arange(24)).sum() / max(seg.sum(), 1e-9))
        xs.append(prev)
    v = np.diff(np.array(xs))
    assert xs[-1] < xs[0] - 5, "the still never pushed in"
    assert (v > 0.02).sum() == 0 and v.std() < 0.08, (v.min(), v.std())


def _render_insert(workdir, src, img, dur, ins, effects=None, preview=False):
    edl = validate_edl({"keep": [[0.0, 1.0], [1.0, dur]], "inserts": [ins],
                        "effects": effects or {}}, dur).model_dump()
    tl = Timeline(edl["keep"], edl["inserts"])
    g = renderer.build_filtergraph(
        edl, dur, True, tl, None, [],
        {"video": {"duration": dur}, "words": [], "sentences": []},
        preview=preview, W=RW, H=RH, fps=FPS,
        insert_inputs=[(1, edl["inserts"][0], False)], silence_idx=2)
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src,
         "-loop", "1", "-t", f"{ins['duration_s']:.3f}", "-r", f"{FPS:.3f}",
         "-i", img, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-filter_complex", g, "-map", "[vout]", "-f", "rawvideo",
         "-pix_fmt", "gray", "pipe:1", "-map", "[aout]", "-f", "null", "-"],
        capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-2000:]
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, RH, RW), g


@needs_ffmpeg
@pytest.mark.parametrize("motion", ["pan_left", "zoom_in"])
def test_insert_motion_moves_the_photo_not_the_look(workdir, motion):
    """The insert's Ken Burns runs where the per-block zoompan used to: on
    the spliced programme BEFORE the grade/stylize/custom-filter stage and
    the shared camera. On the shared camera a vignette slid across the
    frame with the pan and snapped back at the cut (measured edge means
    67.5/50.1 -> 49.7/67.9 on flat grey; constant 43.2/43.6 before)."""
    from PIL import Image
    dur = 2.0
    src = _clip_path(workdir, "tex", dur)
    img = os.path.join(workdir, "grey.png")
    Image.fromarray(np.full((RH, RW), 128, np.uint8)).convert("RGB").save(img)
    ins = {"id": "i1", "kind": "image", "asset_key": "grey.png",
           "at_output_s": 1.0, "duration_s": 3.0, "fit": "crop"}
    look = {"stylize": [{"id": "v", "kind": "vignette", "intensity": 0.8}]}
    still, _g = _render_insert(workdir, src, img, dur, ins, look)
    moving, g = _render_insert(workdir, src, img, dur,
                               dict(ins, motion=motion), look)
    # the insert's chain sits on the concat output, before the look
    assert g.index("[vc]perspective=") < g.index("vignette")
    assert "[vcins]" in g or "[vcins_c0]" in g
    for n in range(int(1.0 * FPS) + 2, int(4.0 * FPS) - 2, 7):
        a, b = moving[n].astype(np.float64), still[n].astype(np.float64)
        for edge in (np.s_[:, :24], np.s_[:, -24:], np.s_[:24, :],
                     np.s_[-24:, :]):
            assert abs(a[edge].mean() - b[edge].mean()) < 0.6, (n, edge)


def test_a_user_zoom_over_an_insert_multiplies_with_its_push():
    """Two chains in series compose: the shared camera magnifies what the
    insert chain already pushed (1.25 x 1.15), not 1 + 0.25 + 0.15."""
    edl = validate_edl({
        "keep": [[0.0, 1.0], [1.0, 2.0]],
        "inserts": [{"id": "i1", "kind": "image", "asset_key": "a.png",
                     "at_output_s": 1.0, "duration_s": 3.0,
                     "motion": "zoom_out"}],
        "effects": {"zooms": [{"id": "z", "start": 1.0, "end": 3.0,
                               "strength": 0.15, "ramp_s": 0}]}},
        2.0).model_dump()
    tl = Timeline(edl["keep"], edl["inserts"])
    g = renderer.build_filtergraph(
        edl, 2.0, True, tl, None, [],
        {"video": {"duration": 2.0}, "words": [], "sentences": []},
        preview=False, W=W, H=H, fps=FPS,
        insert_inputs=[(1, edl["inserts"][0], False)], silence_idx=2)
    cams = [f for f in g.split(";") if "perspective=" in f]
    ins = [f for f in cams if f.startswith("[vc]")]
    shared = [f for f in cams if f.endswith("[vzoom]")]
    assert len(ins) == 1 and len(shared) == 1
    n = int(1.0 * FPS)                      # the insert's first frame
    zi = W / (_corner_values(ins[0], n)[2] - _corner_values(ins[0], n)[0])
    zs = W / (_corner_values(shared[0], n)[2]
              - _corner_values(shared[0], n)[0])
    assert zi == pytest.approx(1.25, abs=1e-6)
    assert zs == pytest.approx(1.15, abs=1e-6)


def test_draft_previews_resample_linearly_finals_stay_cubic():
    edl = validate_edl({"keep": [[0.0, 4.0]], "effects": {"zooms": [
        {"id": "z", "start": 1.0, "end": 2.0, "strength": 0.2,
         "mode": "ease"}]}}, 4.0).model_dump()

    def graph(preview):
        return renderer.build_filtergraph(
            edl, 4.0, True, Timeline(edl["keep"], []), None, [],
            {"video": {"duration": 4.0}, "words": [], "sentences": []},
            preview=preview, W=W, H=H, fps=FPS)
    assert "interpolation=linear" in graph(True)
    assert "interpolation=cubic" in graph(False)
    token = renderer._PREVIEW_QUALITY.set("approval")
    try:
        assert "interpolation=cubic" in graph(True)
    finally:
        renderer._PREVIEW_QUALITY.reset(token)


def test_old_camera_renders_are_never_spliced_into_new_ones():
    moved = {"keep": [[0, 5]], "effects": {"zooms": [
        {"id": "z", "start": 1, "end": 2, "strength": 0.2}]}}
    plain = {"keep": [[0, 5]], "effects": {}}
    assert renderer.camera_current({}, plain)
    assert not renderer.camera_current({}, moved)
    assert not renderer.camera_current({"cam_v": 0}, moved)
    assert renderer.camera_current(
        {"cam_v": renderer.config.CAMERA_VERSION}, moved)
    kb = {"keep": [[0, 5]], "inserts": [{"id": "i", "motion": "zoom_in"}]}
    assert not renderer.camera_current({}, kb)
    assert renderer.camera_current({}, {"keep": [[0, 5]], "inserts": [
        {"id": "i"}]})
    shift = {"keep": [[0, 5]], "effects": {"frame_shifts": [
        {"id": "s", "at": 1, "ratio": "1:1", "zoom": True}]}}
    assert not renderer.camera_current({}, shift)
    shift["effects"]["frame_shifts"][0]["zoom"] = False
    assert renderer.camera_current({}, shift)
    take = {"keep": [[0, 5]],
            "overlays": [{"id": "o", "screen": {"x": 0.1}}]}
    assert not renderer.camera_current({}, take)


def test_the_render_stamps_its_camera_version_and_the_splice_checks_it():
    import inspect
    src = inspect.getsource(renderer)
    assert '"cam_v": config.CAMERA_VERSION' in src
    assert "camera_current(pm, prev_row[\"json\"], index)" in src


def _chain_zoom(chain, n, w=1080):
    """The magnification the chain applies at frame n (1.0 = no filter)."""
    on = [f for f in chain if _enabled(f, n / FPS)]
    assert len(on) <= 1, n
    if not on:
        return 1.0
    c = _corner_values(on[0], n)
    return w / math.hypot(c[2] - c[0], c[3] - c[1])


def test_an_absurdly_dense_pass_degrades_to_steps_not_to_a_failed_render(
        caplog, monkeypatch):
    """Shedding a curve keeps each move's framing: an ease or punch becomes
    a hard step to the SAME held zoom, a push keeps running from 1.0 to its
    strength (linear, no soft start), and landings and pulses — which ARE
    their curve — are never turned into held crops."""
    monkeypatch.setattr(camera, "INSTANCE_TEXT_BUDGET", 30000)
    modes = ("ease", "push_in", "landing", "punch", "pulse")
    zooms = [{"id": f"z{k}", "start": round(0.7 * k, 2),
              "end": round(0.7 * k + 0.6, 2),
              "strength": 0.15, "mode": modes[k % len(modes)],
              "cx": 0.6, "cy": 0.3} for k in range(160)]
    for z in zooms:
        if z["mode"] == "push_in":
            z["end"] = round(z["start"] + 0.6, 2)
        if z["mode"] == "punch":
            z["overshoot"] = 0.1
    shots = [camera.zoom_shot(z, z["start"], z["end"], FPS) for z in zooms]
    with caplog.at_level("WARNING"):
        chain = camera.camera_chain("in", "out", 1080, 1920, FPS, shots)
    assert "dropped curve" in caplog.text
    assert len(chain) <= camera.MAX_INSTANCES
    shed = {line.split()[-1] for line in caplog.text.splitlines()
            if "dropped curve" in line}
    by_id = {z["id"]: z for z in zooms}
    assert not any(by_id[i]["mode"] in ("landing", "pulse") for i in shed)
    pushes = [i for i in shed if by_id[i]["mode"] == "push_in"]
    assert pushes, "the dense pass should shed some push soft-starts"
    for i in pushes:
        z = by_id[i]
        n0 = int(round(z["start"] * FPS))
        n1 = int(round(z["end"] * FPS)) - 1
        assert _chain_zoom(chain, n0) < 1.01, i            # starts wide
        assert _chain_zoom(chain, n1) == pytest.approx(    # ends pushed in
            1.0 + 0.15 * ((n1 / FPS - z["start"]) / 0.6), abs=2e-3)
    for z in zooms:
        mid = int(round((z["start"] + 0.3) * FPS))
        got = _chain_zoom(chain, mid)
        if z["mode"] in ("ease", "punch"):
            assert got == pytest.approx(1.15, abs=2e-3), z["id"]
        if z["mode"] in ("landing", "pulse"):        # back at the wide
            assert got == pytest.approx(1.0, abs=1e-3), z["id"]


def test_touching_moves_plan_fast_and_carry_only_their_own_text():
    """The new grammar butts moves together (a punch held to the next cut,
    a landing starting on it) over a long push. Each frame must evaluate
    only nearby mirrors, and each per-frame instance carry only its own
    stretch's moves — not every move of the programme."""
    import time
    zooms = [{"id": "push", "start": 0.0, "end": 300.0, "strength": 0.06,
              "mode": "push_in"}]
    for k in range(150):
        a = 2.0 * k
        zooms.append({"id": f"p{k}", "start": a, "end": a + 1.6,
                      "strength": 0.12, "cx": 0.55, "cy": 0.35})
        zooms.append({"id": f"l{k}", "start": a + 1.6, "end": a + 2.0,
                      "strength": 0.1, "mode": "landing"})
    shots = [camera.zoom_shot(z, z["start"], z["end"], FPS) for z in zooms]
    t0 = time.monotonic()
    chain = camera.camera_chain("in", "out", 1080, 1920, FPS, shots)
    assert time.monotonic() - t0 < 30.0
    sizes = _per_frame_sizes(chain)
    assert len(sizes) == camera.MAX_GROUPS
    one = camera.corner_exprs(1080, 1920, [s.terms for s in shots], True,
                              FPS)
    assert max(sizes) < 0.4 * sum(len(e) for e in one)
    for n in range(0, int(300 * FPS), 97):
        t = n / FPS
        want = camera.corners(1080, 1920, [s.mirror(t) for s in shots])
        on = [f for f in chain if _enabled(f, t)]
        assert len(on) <= 1, t
        got = _corner_values(on[0], n) if on else \
            [0.0, 0.0, 1080.0, 0.0, 0.0, 1920.0, 1080.0, 1920.0]
        assert max(abs(a - b) for a, b in zip(got, want)) < 1e-4, t


@needs_ffmpeg
def test_a_graph_past_the_argv_limit_goes_through_a_file(workdir):
    """Linux refuses an argv string over 128 KiB before ffmpeg even starts;
    a long, densely zoomed programme must still render."""
    small = renderer._graph_args("[0:v]null[vout]", workdir)
    assert small == ["-filter_complex", "[0:v]null[vout]"]
    # a long but parseable expression (balanced: ffmpeg's parser refuses
    # flat operator chains past ~100)
    big = "[0:v]drawbox=w=2:h=2:enable='" \
        + camera._balanced_sum(["0"] * 40000) + "'[vout]"
    assert len(big) > renderer.GRAPH_ARG_MAX_BYTES
    flag, path = renderer._graph_args(big, workdir)
    assert flag in ("-/filter_complex", "-filter_complex_script")
    assert open(path).read() == big
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=64x64:d=0.1", flag, path, "-map",
                        "[vout]", "-f", "null", "-"], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-500:]


@needs_ffmpeg
def test_hundreds_of_moves_still_parse(workdir):
    """ffmpeg's expression parser gives up on a flat chain of ~100 '+' terms
    (measured), and a per-frame group carries every move it serves: sums are
    balanced trees. A 10-minute programme punched every 2 s must parse."""
    zooms = [{"id": f"z{k}", "start": 2.0 * k, "end": 2.0 * k + 1.5,
              "strength": 0.15, "mode": ("ease", "punch", "landing")[k % 3],
              "cx": 0.55, "cy": 0.3} for k in range(300)]
    shots = [camera.zoom_shot(z, z["start"], z["end"], FPS) for z in zooms]
    chain = camera.camera_chain("0:v", "vout", 64, 112, FPS, shots)
    path = os.path.join(workdir, "many.txt")
    with open(path, "w") as fh:
        fh.write(";".join(chain))
    flag = renderer._graph_file_flag()
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=64x112:r=30:d=0.5", flag, path,
                        "-map", "[vout]", "-f", "null", "-"],
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-800:]
