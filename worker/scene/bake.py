"""E5: curves and drivers, baked to one number per frame. Pure functions: same call, same list.

Every animatable in a scene is a number, a list with one entry per frame of its item, or a curve
spec ``{"curve": name, ...}`` that ``resolve`` turns into that list at compile. The runtime only ever
indexes arrays. The numbers are the measured ones (ATLAS 8.3, 8.5, 8.6); nothing here reads meaning.
"""
import math

# ATLAS 8.5, "Hold moves": a landed card settles from its first frame on one curve in all 3 films.
SETTLE = {"s_inf": 0.84, "tau": 6.9}
SETTLE_FITS = {
    "DekHmZOR_gL": {"fps": 25, "s_inf": 0.84, "tau": 6.9, "fit_error": 0.0025},
    "DfFErGgsMxT": {"fps": 30, "s_inf": 0.75, "tau": 6.2, "fit_error": 0.0023},
    "DfFsUfUx0dZ": {"fps": 24, "s_inf": 0.865, "tau": 6.9, "fit_error": 0.0025},
}
FLICKER = (0.5, 0.9, 0.25, 0.85, 0.3, 1.0)            # ATLAS 8.3, DfFsUfUx0dZ: per-frame opacities
BURN = (0.90, 0.76, 0.61, 0.50, 0.30, 0.14, 0.06, 0.02)   # ATLAS 8.6: near-white share, frame 0 then 7 of decay
RELAY = ((0, 0), (0, 1), (0, 2), (1, 2), (2, 2), (2, 1), (2, 0), (1, 0), (1, 1))   # ATLAS 8.6: (col, row) of a 3x3, 2 frames a cell
BOUNCE = {"blur": 10.0, "rise": 0.03, "over": -0.005, "n": 9}   # ATLAS 3 M4: blur 10 to 0, rise 0.03 h, overshoot -0.005, 8-10 f
UI_ZOOM_PEAK = 0.22                                   # DESIGN 2.6: velocity peaks near 22% of the move


def settle(n, s_inf=None, tau=None, norm=False):
    """s(t) = s_inf + (1 - s_inf) e^(-t/tau), no overshoot. norm: rest at 1 and start at 1/s_inf,
    which is how a picture that must end at its solved size is drawn."""
    s_inf = SETTLE["s_inf"] if s_inf is None else float(s_inf)
    tau = SETTLE["tau"] if tau is None else float(tau)
    out = [s_inf + (1.0 - s_inf) * math.exp(-i / tau) for i in range(int(n))]
    return [v / s_inf for v in out] if norm else out


def flicker(n, pattern=None, rest=1.0):
    """The measured opacities, then held."""
    p = list(pattern or FLICKER)
    return (p + [float(rest)] * int(n))[:int(n)]


def burn(n, peak=None, decay=None):
    """Burn amount per frame: the near-white share, falling over 7 frames, then gone. As a flash this
    is the mix amount; as a threshold, ``burn_th`` gives the level that leaves that share white."""
    d = list(decay or BURN)
    if peak is not None:
        d = [v * float(peak) / d[0] for v in d]
    return (d + [0.0] * int(n))[:int(n)]


def burn_th(n, lo=0.2, hi=0.85):
    """Threshold for flash mode ``burn`` so the white floor recedes as the measured share does:
    a low threshold burns more."""
    return [hi - (hi - lo) * a / BURN[0] for a in burn(n)]


def relay(n, hold=2, path=None, grid=3):
    """A one-cell walk on a fixed path: a rect per frame, ``hold`` frames a cell, held on the last."""
    p = list(path or RELAY)
    out = []
    for i in range(int(n)):
        c, r = p[min(i // max(int(hold), 1), len(p) - 1)]
        out.append([c / grid, r / grid, (c + 1) / grid, (r + 1) / grid])
    return out


def step(values, hold=2):
    """Animation on twos: hold each sample ``hold`` frames."""
    h = max(int(hold), 1)
    return [values[i // h * h] for i in range(len(values))]


def _ui(t, peak=UI_ZOOM_PEAK):
    """Position 0..1 of a move whose speed peaks at ``peak`` of its length and then coasts in: speed
    rises as a sine quarter to the peak, then falls as a cosine to zero. Smooth, no overshoot."""
    p = min(max(peak, 0.02), 0.98)
    area = p * 2 / math.pi + (1 - p) * 2 / math.pi
    if t <= p:
        return (p * 2 / math.pi) * (1 - math.cos(t / p * math.pi / 2)) / area
    u = (t - p) / (1 - p)
    return (p * 2 / math.pi + (1 - p) * 2 / math.pi * math.sin(u * math.pi / 2)) / area


def ui_zoom(n, a=1.0, b=1.2, peak=UI_ZOOM_PEAK):
    """From a to b over n frames, velocity peak near 22% of the move."""
    n = int(n)
    if n <= 1:
        return [float(b)] * n
    return [a + (b - a) * _ui(i / (n - 1), peak) for i in range(n)]


def bounce(n, rest=0.0, rise=None, over=None, frames=None):
    """A soft arrival: from rest+rise to rest, passing it by ``over`` once, in ``frames`` frames, then
    held. Units are the caller's (frame-height fractions for y)."""
    rise = BOUNCE["rise"] if rise is None else float(rise)
    over = BOUNCE["over"] if over is None else float(over)
    m = max(int(frames or BOUNCE["n"]), 2)
    out = []
    for i in range(int(n)):
        t = min(i / (m - 1), 1.0)
        # critically damped fall that crosses rest at t=.62 and returns: one overshoot, no ring
        fall = (1 - t) ** 3
        bump = math.sin(math.pi * min(max((t - 0.45) / 0.55, 0.0), 1.0)) if t > 0.45 else 0.0
        out.append(rest + rise * fall + over * bump)
    return out


def bounce_blur(n, blur=None, frames=None):
    """The blur that goes with a bounce: 10 to 0 over the same frames."""
    blur = BOUNCE["blur"] if blur is None else float(blur)
    m = max(int(frames or BOUNCE["n"]), 2)
    return [blur * max(1 - i / (m - 1), 0.0) ** 2 for i in range(int(n))]


def expo_out(n, a=0.0, b=1.0):
    n = int(n)
    if n <= 1:
        return [float(b)] * n
    return [a + (b - a) * (1 - 2 ** (-10 * i / (n - 1))) / (1 - 2 ** -10) for i in range(n)]


def ease(n, a=0.0, b=1.0):
    """Ease in and out (smootherstep)."""
    n = int(n)
    if n <= 1:
        return [float(b)] * n
    out = []
    for i in range(n):
        t = i / (n - 1)
        out.append(a + (b - a) * t * t * t * (t * (t * 6 - 15) + 10))
    return out


def linear(n, a=0.0, b=1.0):
    n = int(n)
    if n <= 1:
        return [float(b)] * n
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def grow(n, a=1.0, k=1.9):
    """Geometric growth, k times a frame: the punctuation cut's disc."""
    return [float(a) * float(k) ** i for i in range(int(n))]


def gravity(n, y0=0.0, y1=1.0, delay=0, frames=None):
    """y = g t^2 from y0, reaching y1 after ``frames`` frames, starting ``delay`` frames in."""
    m = max(int(frames or n), 1)
    out = []
    for i in range(int(n)):
        t = min(max(i - int(delay), 0) / m, 1.0)
        out.append(y0 + (y1 - y0) * t * t)
    return out


def keys(n, at, v, how="linear"):
    """Keyframes: frame offsets ``at`` and values ``v``. how: linear, hold, expo (expo-out per span)."""
    out = []
    for i in range(int(n)):
        if i <= at[0]:
            out.append(float(v[0]))
            continue
        if i >= at[-1]:
            out.append(float(v[-1]))
            continue
        j = max(k for k in range(len(at)) if at[k] <= i)
        if how == "hold":
            out.append(float(v[j]))
            continue
        t = (i - at[j]) / max(at[j + 1] - at[j], 1)
        if how == "expo":
            t = (1 - 2 ** (-10 * t)) / (1 - 2 ** -10)
        out.append(v[j] + (v[j + 1] - v[j]) * t)
    return out


# ---------------------------------------------------------------- camera curves, measured (week-1112 VIS lane)
# 13 Nov 2026: frame-to-frame camera motion on 91 reference films (the owner's 41 picks, 43 shorts and 7 long
# films across the four folders; week-1112/notes/VIS.md, lanes/VIS/data/numbers.json). The 84 shorts hold 745
# eased camera moves. An eased move is one of three shapes, by where its speed peaks; each is the median travel
# of its family at 17 evenly spaced times.
MOVE = {
    # speed peaks in the first third and coasts in: the move that lands (48% of eased moves; 14 frames, 8.3%)
    "out": (0.0, 0.070, 0.169, 0.278, 0.373, 0.461, 0.550, 0.629, 0.693, 0.760, 0.804, 0.856, 0.903, 0.941, 0.969, 0.992, 1.0),
    # eases both ways (19%; 11 frames, 4.2%)
    "io": (0.0, 0.011, 0.043, 0.090, 0.169, 0.248, 0.322, 0.429, 0.508, 0.599, 0.692, 0.773, 0.860, 0.924, 0.965, 0.995, 1.0),
    # gathers speed to its end: the move that runs into a cut (33%; 11 frames, 6.2%; a third of them end on one)
    "in": (0.0, 0.011, 0.032, 0.069, 0.110, 0.155, 0.216, 0.259, 0.313, 0.381, 0.460, 0.552, 0.639, 0.736, 0.830, 0.922, 1.0),
}
MOVE_FRAMES = 13          # an eased move's length at 30 fps: median 13, quartiles 8 to 19
MOVE_SIZE = (0.038, 0.063, 0.117)             # its size (scale change): quartiles; one in ten passes 18.6%
MOVE_OVER = 0.13          # where a move overshoots, it passes its end by this share of its travel (lower quartile of those)
MOVE_OVER_SHARE = 0.21    # and one move in five does
MOVE_BACK = 6             # frames to come back
CAM_SETTLE = {"size": 0.063, "tau": 5.7}      # a picture that lands off its size and settles: median size (quartiles 0.041 to 0.124), tau in frames at 30 fps
DRIFT_RATE = (0.051, 0.085, 0.149)            # a slow push: scale per second, quartiles; 57% go in; its middle is at 56% of the travel
PAN_RATE = (0.008, 0.019, 0.049)              # a slow pan: frame widths per second, quartiles
PUNCH_SIZE = (0.037, 0.046, 0.071)            # a hard punch (one frame, same picture): quartiles; one in ten passes 11%; 53% go in
WHIP = {"frames": 4, "peak_w": 0.082, "travel_w": 0.27}    # the fast pan either side of a whip cut: frames (3 to 5), peak in frame widths a frame, travel (0.18 to 0.42)


def _on(curve, t):
    """A 17-point curve read at t in 0..1."""
    x = min(max(t, 0.0), 1.0) * (len(curve) - 1)
    i = min(int(x), len(curve) - 2)
    return curve[i] + (curve[i + 1] - curve[i]) * (x - i)


def move(n, a=0.0, b=1.0, kind="out", frames=None, over=0.0, back=None, delay=0):
    """From a to b on a measured camera curve (MOVE), in ``frames`` frames (default: all n), then held. ``over``:
    the move passes b by that share of its travel and comes back over ``back`` frames, as a quarter of the
    references' moves do. ``delay`` holds a first."""
    n, delay = int(n), int(delay or 0)
    m = max(int(frames or (n - delay)), 1)
    bk = int(MOVE_BACK if back is None else back) if over else 0
    c = MOVE[kind]
    out = []
    for i in range(n):
        j = i - delay
        if j <= 0:
            out.append(float(a))
        elif j < m:
            out.append(a + (b - a) * (1.0 + over) * _on(c, j / m))
        elif bk and j < m + bk:
            u = (j - m) / bk
            out.append(b + (b - a) * over * (1.0 - _on(MOVE["out"], u)))
        else:
            out.append(float(b))
    return out


def cam_settle(n, size=None, tau=None, sign=1.0):
    """Log-scale offset per frame of a picture that lands ``size`` off and settles: size * e^(-t/tau), no overshoot."""
    size = CAM_SETTLE["size"] if size is None else float(size)
    tau = CAM_SETTLE["tau"] if tau is None else float(tau)
    return [sign * math.log1p(size) * math.exp(-i / tau) for i in range(int(n))]


CURVES = {"move": move, "cam_settle": cam_settle, "settle": settle, "flicker": flicker, "burn": burn, "burn_th": burn_th, "relay": relay, "ui_zoom": ui_zoom,
          "bounce": bounce, "bounce_blur": bounce_blur, "expo_out": expo_out, "ease": ease, "linear": linear,
          "grow": grow, "gravity": gravity, "keys": keys}


def is_curve(x):
    return isinstance(x, dict) and "curve" in x


def resolve(x, n):
    """An animatable to what the runtime reads: a number, or a list of exactly n entries. A curve spec
    may add ``step`` (hold each value that many frames), ``mul`` and ``add``, and ``delay`` (hold the
    first value that many frames first). Unknown curve names raise KeyError: a plan never guesses."""
    if not is_curve(x):
        return x
    spec = dict(x)
    name = spec.pop("curve")
    hold, mul, add, delay = spec.pop("step", None), spec.pop("mul", None), spec.pop("add", None), int(spec.pop("delay", 0) or 0)
    fn = CURVES[name]
    out = fn(max(n - delay, 0), **spec)
    if delay:
        out = [out[0] if out else 0.0] * min(delay, n) + out
    out = out[:n]
    if hold:
        out = step(out, hold)
    if mul is not None or add is not None:
        m, a = (1.0 if mul is None else float(mul)), (0.0 if add is None else float(add))
        out = [[c * m + a for c in v] if isinstance(v, list) else v * m + a for v in out]
    return out
