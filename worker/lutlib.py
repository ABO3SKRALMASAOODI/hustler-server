"""Colour math for finishing (track 1E): LUTs, hue keys, shot match.

Pure numpy, no worker imports, so every lane (the browserless agent and MCP
lanes included) can validate a LUT, solve a hue or match two shots, and the
tests can check the exact numbers the render will apply.

LUTs
----
A 3D LUT here is a float32 array ``t[b, g, r, 3]`` (blue slowest, red
fastest: the order a ``.cube`` lists its rows) with values in 0..1.

    parse_cube(text) -> Lut            .cube 1D or 3D, <= 65^3, validated
    cube_text(lut) -> str              canonical .cube text (6 decimals)
    identity(n), apply(lut, rgb), mix(lut, strength), resample(lut, n)

Hue keys (the ``hue:<rrggbb>`` mask and ``grade_window.isolate``)
-----------------------------------------------------------------
The key is computed in the CHROMA plane of the picture (BT.709 Cb/Cr, 8-bit
video range), which is where the renderer reads it: a per-pixel table over
(U, V) at chroma resolution (ffmpeg ``lut2``), a quarter of the work of an
RGB key and exactly the same numbers in numpy (``hue_weight``) and in the
graph (``hue_expr``). Two gates:

* hue: within ``tolerance`` x 180 deg of the target, falling to 0 over a
  further ``softness`` x 90 deg;
* saturation: chroma below ~35 % of the target's is not "that colour" (skin
  next to a red jacket, a grey wall under coloured light), with a soft ramp.

Shot match
----------
``match_lut(src_rgb, ref_rgb, method)``: Reinhard (per-channel mean/std in
CIELAB) or MKL (the Monge-Kantorovich linear map between the two colour
distributions, in CIELAB), baked into a 33^3 cube. ``delta_e2000`` measures
the result (AT-1E-6).
"""

import math
import re

import numpy as np

CUBE_MAX = 65          # largest .cube edge accepted (INTERFACES_W1 §3.5)
CUBE_MIN = 2
MATCH_SIZE = 33        # shot-match cubes
CUBE_MAX_BYTES = 16 * 1024 * 1024


class LutError(ValueError):
    """A LUT that cannot be used, with a reason fit for the agent."""


class Lut:
    """A validated 3D LUT: ``table`` [n, n, n, 3] float32 (b, g, r order),
    ``size`` n, ``title``, and ``source_kind`` ('3d' or '1d' when a 1D
    curve set was expanded)."""

    def __init__(self, table, title="", source_kind="3d"):
        t = np.asarray(table, dtype=np.float32)
        if t.ndim != 4 or t.shape[0] != t.shape[1] or \
                t.shape[1] != t.shape[2] or t.shape[3] != 3:
            raise LutError("a 3D LUT is an n x n x n x 3 table")
        self.table = np.clip(t, 0.0, 1.0)
        self.size = int(t.shape[0])
        self.title = str(title or "")[:120]
        self.source_kind = source_kind


# ── .cube I/O ────────────────────────────────────────────────────────────

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?(?:nan|inf)"
_ROW = re.compile(rf"^\s*({_NUM})\s+({_NUM})\s+({_NUM})\s*$", re.I)


def parse_cube(text, max_size=CUBE_MAX):
    """A ``.cube`` file (Adobe / Resolve) as a Lut, or LutError.

    Accepts ``TITLE``, comments, ``DOMAIN_MIN`` / ``DOMAIN_MAX`` (rescaled
    to 0..1), ``LUT_3D_SIZE`` 2..``max_size`` and ``LUT_1D_SIZE`` (a per-
    channel curve set, expanded to a 33^3 cube). Refuses anything else: a
    wrong row count, NaN or infinite values, a size over the cap, two
    size lines, values far outside the domain."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            text = text.decode("latin-1")
    if len(text) > CUBE_MAX_BYTES:
        raise LutError("the .cube file is larger than 16 MB")
    size3 = size1 = None
    dmin, dmax = [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]
    title = ""
    rows = []
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        head = line.split(None, 1)[0].upper()
        if head == "TITLE":
            title = line[5:].strip().strip('"')
            continue
        if head in ("LUT_3D_SIZE", "LUT_1D_SIZE"):
            if size3 is not None or size1 is not None:
                raise LutError(f"line {ln}: a .cube declares one size")
            try:
                n = int(line.split()[1])
            except (IndexError, ValueError):
                raise LutError(f"line {ln}: {head} needs an integer")
            if head == "LUT_3D_SIZE":
                if not CUBE_MIN <= n <= max_size:
                    raise LutError(f"LUT_3D_SIZE {n} is outside "
                                   f"{CUBE_MIN}..{max_size} (cubes up to "
                                   f"{max_size}^3 are accepted)")
                size3 = n
            else:
                if not 2 <= n <= 65536:
                    raise LutError(f"LUT_1D_SIZE {n} is outside 2..65536")
                size1 = n
            continue
        if head in ("DOMAIN_MIN", "DOMAIN_MAX", "LUT_3D_INPUT_RANGE",
                    "LUT_1D_INPUT_RANGE"):
            vals = line.split()[1:]
            try:
                nums = [float(v) for v in vals]
            except ValueError:
                raise LutError(f"line {ln}: {head} needs numbers")
            if head.endswith("INPUT_RANGE"):
                if len(nums) != 2:
                    raise LutError(f"line {ln}: {head} takes min max")
                dmin, dmax = [nums[0]] * 3, [nums[1]] * 3
            else:
                if len(nums) != 3:
                    raise LutError(f"line {ln}: {head} takes three numbers")
                (dmin if head == "DOMAIN_MIN" else dmax)[:] = nums
            continue
        m = _ROW.match(line)
        if not m:
            if re.match(r"^[A-Z_][A-Z0-9_]*\b", line):
                continue                     # unknown keyword: ignore
            raise LutError(f"line {ln}: not a row of three numbers")
        rows.append((float(m.group(1)), float(m.group(2)),
                     float(m.group(3))))
    if size3 is None and size1 is None:
        raise LutError("no LUT_3D_SIZE or LUT_1D_SIZE line")
    data = np.asarray(rows, dtype=np.float64)
    if not np.all(np.isfinite(data)):
        raise LutError("the LUT holds NaN or infinite values")
    lo, hi = np.asarray(dmin, float), np.asarray(dmax, float)
    if np.any(hi <= lo):
        raise LutError("DOMAIN_MAX must exceed DOMAIN_MIN")
    if size3 is not None:
        if data.shape[0] != size3 ** 3:
            raise LutError(f"LUT_3D_SIZE {size3} needs {size3 ** 3} rows, "
                           f"found {data.shape[0]}")
        out = data
        if np.any(out < -0.5) or np.any(out > 1.5 * max(1.0, hi.max())):
            raise LutError("LUT values are far outside 0..1")
        table = out.reshape(size3, size3, size3, 3)
        return Lut(np.clip(table, 0, 1), title=title, source_kind="3d")
    if data.shape[0] != size1:
        raise LutError(f"LUT_1D_SIZE {size1} needs {size1} rows, found "
                       f"{data.shape[0]}")
    curves = np.clip(data, 0, 1)
    n = MATCH_SIZE
    x = np.linspace(0, 1, n)
    src = np.linspace(0, 1, size1)
    cr = np.interp(x, src, curves[:, 0])
    cg = np.interp(x, src, curves[:, 1])
    cb = np.interp(x, src, curves[:, 2])
    b, g, r = np.meshgrid(cb, cg, cr, indexing="ij")
    return Lut(np.stack([r, g, b], axis=-1), title=title, source_kind="1d")


def cube_text(lut, title=None):
    """Canonical ``.cube`` text: title, size, rows with 6 decimals, red
    fastest. Stable bytes for the same table (a content-addressed asset)."""
    t = lut.table if isinstance(lut, Lut) else np.asarray(lut, np.float32)
    n = int(t.shape[0])
    out = [f'TITLE "{(title if title is not None else getattr(lut, "title", "")) or "Valmera"}"',
           f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0",
           "DOMAIN_MAX 1.0 1.0 1.0"]
    flat = np.clip(t.reshape(-1, 3), 0, 1)
    out.extend(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat)
    return "\n".join(out) + "\n"


def identity(n=MATCH_SIZE):
    x = np.linspace(0.0, 1.0, int(n), dtype=np.float32)
    b, g, r = np.meshgrid(x, x, x, indexing="ij")
    return Lut(np.stack([r, g, b], axis=-1), title="identity")


def apply(lut, rgb):
    """Trilinear lookup of ``rgb`` (float 0..1, any leading shape) through
    a Lut -> float array of the same shape."""
    t = lut.table if isinstance(lut, Lut) else np.asarray(lut, np.float32)
    n = t.shape[0]
    a = np.clip(np.asarray(rgb, np.float64), 0.0, 1.0) * (n - 1)
    i0 = np.minimum(np.floor(a).astype(np.int64), n - 2)
    f = a - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]

    def at(db, dg, dr):
        return t[b0 + db, g0 + dg, r0 + dr].astype(np.float64)
    c00 = at(0, 0, 0) * (1 - fr) + at(0, 0, 1) * fr
    c01 = at(0, 1, 0) * (1 - fr) + at(0, 1, 1) * fr
    c10 = at(1, 0, 0) * (1 - fr) + at(1, 0, 1) * fr
    c11 = at(1, 1, 0) * (1 - fr) + at(1, 1, 1) * fr
    c0 = c00 * (1 - fg) + c01 * fg
    c1 = c10 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def resample(lut, n):
    """The same transform on an n^3 lattice."""
    if lut.size == n:
        return lut
    grid = identity(n).table
    return Lut(apply(lut, grid).astype(np.float32), title=lut.title)


def mix(lut, strength):
    """identity + strength x (lut - identity): a LUT at partial strength,
    baked into the table (no extra composite at render)."""
    s = float(max(0.0, min(1.0, strength)))
    if s >= 0.9999:
        return lut
    ident = identity(lut.size).table
    return Lut(ident + (lut.table - ident) * s, title=lut.title)


def compose(first, then):
    """A Lut that applies ``first`` and then ``then`` (on ``first``'s
    lattice)."""
    return Lut(apply(then, first.table).astype(np.float32),
               title=first.title)


# ── colour spaces ────────────────────────────────────────────────────────

_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]])
_M_XYZ2RGB = np.linalg.inv(_M_RGB2XYZ)
_D65 = np.array([0.95047, 1.0, 1.08883])


def srgb_to_linear(c):
    c = np.asarray(c, np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(np.asarray(c, np.float64), 0, None)
    return np.where(c <= 0.0031308, 12.92 * c,
                    1.055 * np.power(c, 1 / 2.4) - 0.055)


def rgb_to_lab(rgb):
    """sRGB 0..1 -> CIELAB (D65)."""
    xyz = srgb_to_linear(rgb) @ _M_RGB2XYZ.T / _D65
    eps, k = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (k * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_rgb(lab):
    lab = np.asarray(lab, np.float64)
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    eps, k = 216 / 24389, 24389 / 27

    def finv(f):
        f3 = f ** 3
        return np.where(f3 > eps, f3, (116 * f - 16) / k)
    xyz = np.stack([finv(fx), np.where(lab[..., 0] > k * eps, fy ** 3,
                                       lab[..., 0] / k), finv(fz)],
                   axis=-1) * _D65
    return np.clip(linear_to_srgb(xyz @ _M_XYZ2RGB.T), 0, 1)


def delta_e2000(lab1, lab2):
    """CIEDE2000 colour difference, element-wise."""
    L1, a1, b1 = (np.asarray(lab1, np.float64)[..., i] for i in range(3))
    L2, a2, b2 = (np.asarray(lab2, np.float64)[..., i] for i in range(3))
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cb ** 7 / (Cb ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(C1p * C2p == 0, 0, dh)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh) / 2)
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    hsum = h1p + h2p
    hbp = np.where(np.abs(h1p - h2p) > 180,
                   np.where(hsum < 360, (hsum + 360) / 2, (hsum - 360) / 2),
                   hsum / 2)
    hbp = np.where(C1p * C2p == 0, hsum, hbp)
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30))
         + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6))
         - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dtheta = 30 * np.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / np.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2 * dtheta)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                   + Rt * (dCp / Sc) * (dHp / Sh))


# ── video-range YCbCr (BT.709) ───────────────────────────────────────────

KR, KB = 0.2126, 0.0722
KG = 1.0 - KR - KB


def rgb_to_ycc8(rgb):
    """sRGB 0..1 -> 8-bit video-range (Y, Cb, Cr) floats, BT.709."""
    rgb = np.asarray(rgb, np.float64)
    y = KR * rgb[..., 0] + KG * rgb[..., 1] + KB * rgb[..., 2]
    cb = (rgb[..., 2] - y) / (2 * (1 - KB))
    cr = (rgb[..., 0] - y) / (2 * (1 - KR))
    return np.stack([16 + 219 * y, 128 + 224 * cb, 128 + 224 * cr],
                    axis=-1)


def ycc8_to_rgb(ycc):
    ycc = np.asarray(ycc, np.float64)
    y = (ycc[..., 0] - 16) / 219
    cb = (ycc[..., 1] - 128) / 224
    cr = (ycc[..., 2] - 128) / 224
    r = y + 2 * (1 - KR) * cr
    b = y + 2 * (1 - KB) * cb
    g = (y - KR * r - KB * b) / KG
    return np.clip(np.stack([r, g, b], axis=-1), 0, 1)


def hex_rgb(h):
    h = str(h).strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"not a #rrggbb colour: {h!r}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb):
    r, g, b = (int(round(max(0, min(255, v)))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── the hue key ──────────────────────────────────────────────────────────

DEFAULT_TOLERANCE = 0.12      # x 180 deg half-width (pinned field default)
DEFAULT_SOFTNESS = 0.1        # x 90 deg feather


# relative saturation = chroma radius / max(luma - 16, REL_FLOOR): the
# floor keeps near-black noise from reading as saturated
REL_FLOOR = 12.0


class HueKey:
    """Solved numbers of one hue key (see the module doc)."""

    def __init__(self, colour, tolerance=DEFAULT_TOLERANCE,
                 softness=DEFAULT_SOFTNESS):
        r, g, b = hex_rgb(colour)
        _y, cb, cr = rgb_to_ycc8(np.array([r, g, b]) / 255.0)
        self.colour = rgb_hex((r, g, b))
        self.angle = math.atan2(cr - 128.0, cb - 128.0)
        self.radius = math.hypot(cb - 128.0, cr - 128.0)
        self.half = max(0.01, float(tolerance)) * math.pi
        self.soft = max(0.02, float(softness) * math.pi / 2)
        r_t = max(self.radius, 8.0)
        self.sat_lo = max(4.0, 0.33 * r_t)
        self.sat_hi = max(self.sat_lo + 4.0, 0.55 * r_t)
        # with luma, saturation is RELATIVE (chroma over brightness, as
        # HSV's S): the shadowed fold of a red coat has a third of the
        # coat's chroma but the same relative saturation
        rel_t = max(self.radius, 4.0) / max(_y - 16.0, REL_FLOOR)
        self.rel_lo = max(0.06, 0.35 * rel_t)
        self.rel_hi = max(self.rel_lo + 0.05, 0.6 * rel_t)
        # Skin never counts as a kept colour unless the colour IS skin:
        # ruddy skin sits 20-30 degrees from red, inside a red key. (The
        # render gates skin by luma too, so a dark red is never "skin".)
        self.exclude_skin = float(skin_weight(cb, cr, _y)) < 0.5
        # ... but never at the kept colour itself: a deep pinkish red can
        # sit on the skin box's edge; within this chroma distance of the
        # target the skin gate stands down
        self.near = (cb, cr, max(6.0, 0.15 * r_t), max(12.0, 0.35 * r_t))

    def as_dict(self):
        return {"colour": self.colour, "angle": round(self.angle, 6),
                "radius": round(self.radius, 3),
                "half": round(self.half, 6), "soft": round(self.soft, 6),
                "sat_lo": round(self.sat_lo, 3),
                "sat_hi": round(self.sat_hi, 3),
                "rel_lo": round(self.rel_lo, 4),
                "rel_hi": round(self.rel_hi, 4),
                "exclude_skin": self.exclude_skin}


def _smooth(lo, hi, x):
    t = np.clip((x - lo) / max(1e-9, hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def hue_weight(key, cb, cr, y=None):
    """0..1 membership of chroma (cb, cr) (8-bit, any shape) in the key.

    With luma ``y`` (what the render uses): the saturation gate is
    relative to brightness and skin is excluded as the key asks. Without
    it: the chroma-only weight (``hue_expr``, the brand-hue protection)."""
    du, dv = np.asarray(cb, np.float64) - 128.0, \
        np.asarray(cr, np.float64) - 128.0
    ang = np.arctan2(dv, du)
    d = np.abs((ang - key.angle + 3 * np.pi) % (2 * np.pi) - np.pi)
    wh = 1.0 - _smooth(key.half, key.half + key.soft, d)
    if y is None:
        return wh * _smooth(key.sat_lo, key.sat_hi, np.hypot(du, dv))
    yv = np.asarray(y, np.float64)
    # the radius as the render carries it (x2, clipped to 8 bits; ffmpeg's
    # lut2 truncates to whole levels)
    rad2 = np.floor(np.minimum(255.0, 2 * np.hypot(du, dv)))
    rel = (rad2 / 2.0) / np.maximum(yv - 16.0, REL_FLOOR)
    ws = _smooth(key.rel_lo, key.rel_hi, rel)
    if key.exclude_skin:
        ws = ws * (1.0 - skin_weight(cb, cr, y, near=key.near))
    return wh * ws


def _sm_expr(lo, hi, v):
    t = f"clip(({v}-({lo:.6f}))/{max(1e-9, hi - lo):.6f},0,1)"
    return f"({t}*{t}*(3-2*{t}))"


def hue_expr(key, scale=255):
    """The chroma-only weight as an ffmpeg ``lut2`` expression over x = Cb
    and y = Cr (8-bit), times ``scale``. smoothstep is written out so
    ffmpeg and numpy evaluate one formula."""
    d = (f"abs(mod(atan2(y-128,x-128)-({key.angle:.6f})+3*PI,2*PI)-PI)")
    wh = f"(1-{_sm_expr(key.half, key.half + key.soft, d)})"
    ws = _sm_expr(key.sat_lo, key.sat_hi, "hypot(x-128,y-128)")
    return f"{scale}*{wh}*{ws}"


def hue_angle_expr(key, scale=255):
    """lut2 over (Cb, Cr): the hue gate alone."""
    d = (f"abs(mod(atan2(y-128,x-128)-({key.angle:.6f})+3*PI,2*PI)-PI)")
    return f"{scale}*(1-{_sm_expr(key.half, key.half + key.soft, d)})"


# lut2 over (Cb, Cr): the chroma radius x2 (8-bit), for the relative gate
RADIUS2_EXPR = "min(255,2*hypot(x-128,y-128))"


def rel_gate_expr(key, scale=255):
    """lut2 over x = luma (at chroma size), y = RADIUS2_EXPR: the
    relative-saturation gate of the key."""
    rel = f"((y/2)/max(x-16,{REL_FLOOR:.1f}))"
    return f"{scale}*{_sm_expr(key.rel_lo, key.rel_hi, rel)}"


# Skin: the classic Cb/Cr cluster (Chai & Ngan), softened by 6 levels.
SKIN_CB = (77.0, 127.0)
SKIN_CR = (133.0, 173.0)
SKIN_SOFT = 6.0
# ... and chroma: a grey is not skin, however close to the box
SKIN_MIN_RADIUS = (6.0, 12.0)


# Skin is never dark: a deep red curtain shares skin's chroma but not its
# brightness (8-bit video luma).
SKIN_MIN_LUMA = (48.0, 72.0)


def skin_weight(cb, cr, y=None, near=None):
    """0..1 skin membership from chroma (the Cb/Cr box), times the luma
    gate when ``y`` is given (dark reds are not skin). ``near`` (cb_t,
    cr_t, d0, d1): no skin within d0 of that chroma, full beyond d1 (a
    key's own colour is never excluded as skin)."""
    cb = np.asarray(cb, np.float64)
    cr = np.asarray(cr, np.float64)
    if near is not None:
        c0, r0, d0, d1 = near
        far = _smooth(d0, d1, np.hypot(cb - c0, cr - r0))
        return skin_weight(cb, cr, y) * far
    if y is not None:
        return skin_weight(cb, cr) * _smooth(SKIN_MIN_LUMA[0],
                                             SKIN_MIN_LUMA[1],
                                             np.asarray(y, np.float64))
    w = (_smooth(SKIN_CB[0] - SKIN_SOFT, SKIN_CB[0], cb)
         * (1 - _smooth(SKIN_CB[1], SKIN_CB[1] + SKIN_SOFT, cb))
         * _smooth(SKIN_CR[0] - SKIN_SOFT, SKIN_CR[0], cr)
         * (1 - _smooth(SKIN_CR[1], SKIN_CR[1] + SKIN_SOFT, cr))
         * _smooth(SKIN_MIN_RADIUS[0], SKIN_MIN_RADIUS[1],
                   np.hypot(cb - 128.0, cr - 128.0)))
    return w


def skin_expr(scale=255, near=None):
    """lut2 over (Cb, Cr): the chroma skin weight (see skin_weight; luma
    gating is skin_luma_expr, applied after)."""
    def sm(lo, hi, v):
        t = f"clip(({v}-({lo:.3f}))/{hi - lo:.3f},0,1)"
        return f"({t}*{t}*(3-2*{t}))"
    s = SKIN_SOFT
    far = ""
    if near is not None:
        c0, r0, d0, d1 = near
        far = "*" + _sm_expr(d0, d1, f"hypot(x-({c0:.4f}),y-({r0:.4f}))")
    return (f"{scale}*{sm(SKIN_CB[0] - s, SKIN_CB[0], 'x')}"
            f"*(1-{sm(SKIN_CB[1], SKIN_CB[1] + s, 'x')})"
            f"*{sm(SKIN_CR[0] - s, SKIN_CR[0], 'y')}"
            f"*(1-{sm(SKIN_CR[1], SKIN_CR[1] + s, 'y')})"
            f"*{sm(SKIN_MIN_RADIUS[0], SKIN_MIN_RADIUS[1], 'hypot(x-128,y-128)')}"
            f"{far}")


def skin_luma_expr():
    """lut2 over x = luma (8-bit, at chroma size), y = a chroma-only skin
    weight: the weight gated by brightness."""
    lo, hi = SKIN_MIN_LUMA
    t = f"clip((x-({lo:.3f}))/{hi - lo:.3f},0,1)"
    return f"y*({t}*{t}*(3-2*{t}))"


def is_skin_hue(colour):
    r, g, b = hex_rgb(colour)
    y, cb, cr = rgb_to_ycc8(np.array([r, g, b]) / 255.0)
    return float(skin_weight(cb, cr, y)) > 0.5


# ── dominant hue (auto) ──────────────────────────────────────────────────

def dominant_hue(rgb, mask=None, min_radius=18.0, exclude_skin=True,
                 min_share=0.004):
    """(hex, share) of the most present saturated hue in ``rgb`` (uint8 or
    float [..., 3]) within ``mask`` (bool/0..1), or (None, 0.0).

    Pixels with chroma below ``min_radius`` (8-bit Cb/Cr units) and, by
    default, skin are ignored: "the subject's colour" is the jacket, not
    the face. The hue histogram (5 deg bins, weighted by chroma) is
    smoothed; the peak's representative colour is the median RGB of the
    pixels within +-12 deg of it, pushed to their 80th-percentile
    saturation (the colour the key should hold, not its shadows)."""
    a = np.asarray(rgb)
    f = a.astype(np.float64) / (255.0 if a.dtype == np.uint8 else 1.0)
    ycc = rgb_to_ycc8(f)
    du, dv = ycc[..., 1] - 128.0, ycc[..., 2] - 128.0
    rad = np.hypot(du, dv)
    # "saturated" relative to brightness: a deep red curtain (luma 30,
    # chroma 11) is as coloured as a neon sign
    floor = np.maximum(min_radius * np.clip((ycc[..., 0] - 16) / 120.0,
                                            0.3, 1.0), 6.0)
    sel = rad >= floor
    if exclude_skin:
        sel &= skin_weight(ycc[..., 1], ycc[..., 2], ycc[..., 0]) < 0.5
    if mask is not None:
        sel &= np.asarray(mask).astype(bool)
    total = float(np.asarray(mask).astype(bool).sum()) if mask is not None \
        else float(sel.size)
    if not sel.any() or total <= 0:
        return None, 0.0
    ang = np.degrees(np.arctan2(dv[sel], du[sel])) % 360
    hist, _ = np.histogram(ang, bins=72, range=(0, 360), weights=rad[sel])
    k = np.array([1, 2, 3, 2, 1], float)
    sm = np.convolve(np.concatenate([hist[-2:], hist, hist[:2]]), k,
                     "valid")
    peak = (int(np.argmax(sm)) + 0.5) * 5.0
    near = np.abs((ang - peak + 180) % 360 - 180) <= 12
    share = float(near.sum()) / total
    if share < min_share:
        return None, share
    pix = f[sel][near]
    rr = rad[sel][near]
    top = pix[rr >= np.percentile(rr, 60)] if len(rr) > 10 else pix
    rgb_med = np.median(top, axis=0) * 255.0
    return rgb_hex(rgb_med), share


# ── shot match ───────────────────────────────────────────────────────────

def _stats(lab):
    x = np.asarray(lab, np.float64).reshape(-1, 3)
    return x.mean(axis=0), np.cov(x, rowvar=False) + np.eye(3) * 1e-6


def _sqrtm(m):
    w, v = np.linalg.eigh((m + m.T) / 2)
    w = np.clip(w, 1e-12, None)
    return (v * np.sqrt(w)) @ v.T


def mkl_matrix(cov_src, cov_ref):
    """The Monge-Kantorovich linear map T with T cov_src T' = cov_ref
    (Pitie & Kokaram 2007)."""
    a_half = _sqrtm(cov_src)
    a_ihalf = np.linalg.inv(a_half)
    c = _sqrtm(a_half @ cov_ref @ a_half)
    return a_ihalf @ c @ a_ihalf


def _sample(rgb, max_px=200_000, seed=7):
    x = np.asarray(rgb)
    x = (x.astype(np.float64) / (255.0 if x.dtype == np.uint8 else 1.0)) \
        .reshape(-1, 3)
    # drop clipped pixels: they carry no colour information
    keep = (x.max(axis=1) < 0.995) & (x.max(axis=1) > 0.01)
    x = x[keep] if keep.sum() > 1000 else x
    if len(x) > max_px:
        idx = np.random.default_rng(seed).choice(len(x), max_px,
                                                 replace=False)
        x = x[idx]
    return x


def shared_content(ls, lr, bins=(6, 6, 6), floor=0.002, least=0.2):
    """Masks over two CIELAB pixel sets keeping only the colours BOTH shots
    contain (3D histogram bins holding >= ``floor`` of each set): a black
    T-shirt in one shot and a white shirt in the other are content, not a
    colour cast, and must not drive the match. Falls back to everything
    when less than ``least`` of either set is shared."""
    lo = np.array([0.0, -60.0, -60.0])
    hi = np.array([100.0, 60.0, 60.0])
    nb = np.array(bins)

    def idx(lab):
        q = np.clip(((lab - lo) / (hi - lo) * nb).astype(int), 0, nb - 1)
        return (q[:, 0] * nb[1] + q[:, 1]) * nb[2] + q[:, 2]
    ia, ib = idx(ls), idx(lr)
    n = int(np.prod(nb))
    ha = np.bincount(ia, minlength=n) / max(1, len(ia))
    hb = np.bincount(ib, minlength=n) / max(1, len(ib))
    ok = (ha >= floor) & (hb >= floor)
    ma, mb = ok[ia], ok[ib]
    if ma.mean() < least or mb.mean() < least:
        return np.ones(len(ls), bool), np.ones(len(lr), bool)
    return ma, mb


def _fit(ls, lr, method):
    ms, cs = _stats(ls)
    mr, cr = _stats(lr)
    if method == "reinhard":
        k = np.sqrt(np.diag(cr) / np.diag(cs))
        return lambda lab: (np.asarray(lab) - ms) * k + mr
    T = mkl_matrix(cs, cr)
    return lambda lab: (np.asarray(lab) - ms) @ T.T + mr


def _match_lab(src_rgb, ref_rgb, shared=True):
    ls = rgb_to_lab(_sample(src_rgb))
    lr = rgb_to_lab(_sample(ref_rgb))
    if not shared:
        return ls, lr
    ma, mb = shared_content(ls, lr)
    return ls[ma], lr[mb]


def match_transform(src_rgb, ref_rgb, method="mkl", shared=True):
    """A function lab -> lab mapping the colour distribution of ``src_rgb``
    (the shot to correct) onto ``ref_rgb`` (the hero), in CIELAB.
    ``method``: 'reinhard' (per-channel mean/std) or 'mkl' (full
    covariance). ``shared`` fits on the colours both shots contain
    (``shared_content``: coarse bins, so a cast of a few dE keeps its
    colours shared), so different wardrobe never reads as a cast."""
    return _fit(*_match_lab(src_rgb, ref_rgb, shared), method)


# The fit is a linear map learnt on the colours the shots share (walls,
# skin, neutrals). Beyond the chroma it was learnt on, it is
# extrapolation: a saturated neon sign or a brand colour the shared
# content never sampled would be pushed by a correction meant for
# lit surfaces. The correction fades out over this many C* units past the
# fitted content's 99th-percentile chroma.
MATCH_CHROMA_FADE = 25.0


def match_lut(src_rgb, ref_rgb, method="mkl", size=MATCH_SIZE,
              strength=1.0, limit=None, shared=True):
    """A Lut that makes ``src_rgb``'s shot look like ``ref_rgb``'s.

    ``limit`` (Lab units, default None) caps how far any lattice colour may
    move, so a match on unlucky frames can never wreck a shot. Colours
    more saturated than anything the fit was learnt on keep their own
    colour (MATCH_CHROMA_FADE). Neutrals stay neutral within the
    reference's own cast."""
    ls, lr = _match_lab(src_rgb, ref_rgb, shared)
    fn = _fit(ls, lr, method)
    c_fit = float(np.percentile(np.hypot(ls[:, 1], ls[:, 2]), 99)) \
        if len(ls) else 0.0
    grid = identity(size).table.astype(np.float64)
    lab = rgb_to_lab(grid)
    out = fn(lab)
    keep = _smooth(c_fit, c_fit + MATCH_CHROMA_FADE,
                   np.hypot(lab[..., 1], lab[..., 2]))[..., None]
    out = out + (lab - out) * keep
    if limit:
        d = out - lab
        n = np.linalg.norm(d, axis=-1, keepdims=True)
        out = lab + d * np.minimum(1.0, float(limit) / np.maximum(n, 1e-9))
    lut = Lut(lab_to_rgb(out).astype(np.float32), title=f"match {method}")
    return mix(lut, strength)


def mean_delta_e(rgb_a, rgb_b):
    """Mean CIEDE2000 between two equally shaped RGB arrays."""
    a = np.asarray(rgb_a)
    b = np.asarray(rgb_b)
    fa = a.astype(np.float64) / (255.0 if a.dtype == np.uint8 else 1.0)
    fb = b.astype(np.float64) / (255.0 if b.dtype == np.uint8 else 1.0)
    return float(np.mean(delta_e2000(rgb_to_lab(fa), rgb_to_lab(fb))))
