"""The grade and finish stage: looks, as numbers, and the solver that fits one to a film's own pictures.

A look is what the picture shader's ``look()`` reads (glsl/picture.glsl) plus what the film layer adds:

    con, pivot      the tone curve: an S about ``pivot`` whose slope there is ``con``
    black, white    its floor and ceiling (a lifted black, a held white)
    sat             colour kept (1 is all of it)
    sh, sh_a        the hue the shadows take and how much; hi, hi_a the same for the highlights
    skin            how much of skin is kept out of all that, 0..1: skin takes no hue, keeps its own
                    colour (saturation held within 8%) and half the contrast
    drop            the level of the room against the face's (1 is the same): the references hold a face above
                    the room (skin L* 43.5 over a median of 39.5 in the stories), so a flat-lit picture has its
                    room taken down and its face left where it is. The face is a place, not a colour: a shot's
                    ``window`` ([cx, cy, rx, ry] in its picture, ``window_of(face)``) is the soft ellipse that
                    stays; a shot with no window goes down whole
    grain, vignette, halation, bloom, shutter     the film layer and the shutter, as ``film`` carries them

``TARGET`` is where the reference films sit, measured (week-1112 VIS lane, tools/measure.py: CIELAB
percentiles of the picture without its bars and plates, chroma, per workflow). ``LOOKS`` are the designs;
``solve`` moves a design's contrast, exposure and colour until this film's own pictures land in the
target's band, and never past what skin takes. Nothing here reads a meaning: frames in, numbers out.

    film = looks.film("story_low", pictures=[arr, ...])     # a film dict: look, grain, vignette, halation, shutter
    arr2 = looks.apply(arr, film["look"])                   # the numpy twin of the shader, for meters and tests
"""
import math

import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722])
# The skin key (glsl skin_key): the direction of skin's chroma in ((R - Y) / Y, (Y - B) / Y). Skin of every depth
# lies along one line there. Read off 12,480 face samples of the reference films (cheeks under YuNet boxes): the
# axis sits at 30.5 degrees at the median, quartiles 23 to 37, and ungraded skin from pale to deep at 37 to 51.
# Centre 35, 15 either side, feathered over 10 more.
SKIN_AXIS, SKIN_HALF, SKIN_FEATHER = 35.0, 15.0, 10.0
IDENTITY = {"con": 1.0, "pivot": 0.45, "black": 0.0, "white": 1.0, "sat": 1.0, "skin": 1.0, "sh": None, "sh_a": 0.0, "hi": None, "hi_a": 0.0, "drop": 1.0}

# Where the references sit (CIELAB L* percentiles of the picture, mean chroma C*ab), by family. Numbers from
# VIS.md table "tone"; each is the median film of its family and the band is the families' quartiles.
TARGET = {
    "story": {"L5": 3.0, "L50": 39.5, "L95": 91.0, "C": 10.3, "skin_L": 43.5},     # WF3 topic and founder stories (26 films)
    "podcast": {"L5": 6.0, "L50": 35.5, "L95": 71.5, "C": 8.9, "skin_L": 33.0},    # WF1 clips: low key, highlights held under 75
    "promo": {"L5": 4.0, "L50": 33.0, "L95": 93.0, "C": 7.4, "skin_L": 39.7},      # WF2 marketing (15): whites near the top
    "long": {"L5": 2.0, "L50": 28.0, "L95": 67.0, "C": 8.9, "skin_L": 42.0},       # WF4 long-form (7 films, 14 windows)
    "owner": {"L5": 2.0, "L50": 23.0, "L95": 84.0, "C": 8.5, "skin_L": 23.1},      # the owner's 41 picks: the darkest set
}

# The designs. The hues are the references' own (VIS.md "split": mean a*, b* by tone band). Stories lean warm-red in
# the shadows (a* +5, b* +2 at L* 20-40) and stay near neutral on top; the podcast clips run their shadows toward
# violet (a* +9, b* -6); the long films hold neutral shadows under warm highlights (b* +9 over L* 60). The film
# layer follows what survives delivery: next to no grain (under 1 code value of texture on flat areas in every
# family), no red fringe and no colour fringe, a glow round highlights of 4 to 37 code values, dark edges.
LOOKS = {
    "story_low": {"family": "story", "con": 1.22, "pivot": 0.40, "black": 0.010, "white": 0.975, "sat": 0.90, "skin": 0.9,
                  "sh": "#3A1C10", "sh_a": 0.028, "hi": "#FFE9C8", "hi_a": 0.016,
                  "grain": 0.014, "vignette": 0.45, "halation": 0.03, "bloom": 0.12, "shutter": 0.6},
    "story_cold": {"family": "story", "con": 1.20, "pivot": 0.40, "black": 0.010, "white": 0.975, "sat": 0.80, "skin": 0.9,
                   "sh": "#0E2C46", "sh_a": 0.034, "hi": "#F2EEE4", "hi_a": 0.012,
                   "grain": 0.014, "vignette": 0.45, "halation": 0.0, "bloom": 0.10, "shutter": 0.6},
    "owner_night": {"family": "owner", "con": 1.30, "pivot": 0.36, "black": 0.006, "white": 0.95, "sat": 0.84, "skin": 0.9,
                    "sh": "#2A1410", "sh_a": 0.030, "hi": "#FFE6C0", "hi_a": 0.020,
                    "grain": 0.014, "vignette": 0.5, "halation": 0.04, "bloom": 0.16, "shutter": 0.6},
    "podcast_low": {"family": "podcast", "con": 1.16, "pivot": 0.38, "black": 0.016, "white": 0.93, "sat": 0.92, "skin": 1.0,
                    "sh": "#1C1438", "sh_a": 0.028, "hi": "#FFEBD2", "hi_a": 0.012,
                    "grain": 0.012, "vignette": 0.34, "halation": 0.03, "bloom": 0.08, "shutter": 0.5},
    "promo_pop": {"family": "promo", "con": 1.16, "pivot": 0.44, "black": 0.0, "white": 1.0, "sat": 1.04, "skin": 0.9,
                  "sh": "#201410", "sh_a": 0.016, "hi": "#FFF4E0", "hi_a": 0.010,
                  "grain": 0.008, "vignette": 0.2, "halation": 0.0, "bloom": 0.10, "shutter": 0.6},
    "long_doc": {"family": "long", "con": 1.16, "pivot": 0.40, "black": 0.008, "white": 0.92, "sat": 0.90, "skin": 0.95,
                 "sh": "#101820", "sh_a": 0.016, "hi": "#FFE2B0", "hi_a": 0.026,
                 "grain": 0.012, "vignette": 0.36, "halation": 0.03, "bloom": 0.06, "shutter": 0.5},
    "mono_silver": {"family": "story", "con": 1.28, "pivot": 0.42, "black": 0.008, "white": 0.97, "sat": 0.0, "skin": 0.0,
                    "sh": "#1A1E28", "sh_a": 0.020, "hi": "#FFF6E6", "hi_a": 0.012,
                    "grain": 0.02, "vignette": 0.45, "halation": 0.0, "bloom": 0.10, "shutter": 0.6},
}
FILM_KEYS = ("grain", "vignette", "halation", "bloom", "shutter")
LOOK_KEYS = ("con", "pivot", "black", "white", "sat", "skin", "sh", "sh_a", "hi", "hi_a", "drop")
# what the solver may do: contrast between these, colour between these, exposure within these stops
CON, SAT, EV_MAX = (1.0, 1.45), (0.55, 1.2), 0.45
DROP_MIN = 0.7                # the room is never taken under 70% of its level against the face: past that the key's edge shows
SKIN_L_MIN = 31.0             # no look takes a lit face under this L*: the dimmest family's own faces (WF1, 31.6)


def _hex(c):
    c = (c or "#808080").lstrip("#")
    return np.array([int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


def _hue(c):
    """A hue as an rgb offset that adds no luma, its largest component 1."""
    if not c:
        return np.zeros(3)
    v = _hex(c)
    v = v - float(v @ LUMA)
    m = float(np.abs(v).max())
    return v / m if m > 1e-6 else np.zeros(3)


def clean(lk):
    """A look with every number present and in range: what a plan carries."""
    o = dict(IDENTITY)
    for k in LOOK_KEYS:
        if lk.get(k) is not None:
            o[k] = lk[k]
    o["con"] = min(max(float(o["con"]), 0.5), 2.5)
    o["pivot"] = min(max(float(o["pivot"]), 0.1), 0.9)
    o["black"] = min(max(float(o["black"]), 0.0), 0.3)
    o["white"] = min(max(float(o["white"]), 0.6), 1.0)
    for k in ("sat", "skin", "sh_a", "hi_a", "drop"):
        o[k] = float(o[k])
    o["drop"] = min(max(o["drop"], 0.4), 1.0)
    o["skin"] = min(max(o["skin"], 0.0), 1.0)
    return o


def uniforms(lk):
    """A look as the shader's uniforms."""
    o = clean(lk)
    a, h, f = math.radians(SKIN_AXIS), math.radians(SKIN_HALF), math.radians(SKIN_HALF + SKIN_FEATHER)
    return {"lk0": (o["con"], o["pivot"], o["black"], o["white"]), "lk1": (o["sat"], o["skin"], o["sh_a"], o["hi_a"]),
            "sh": tuple(float(x) for x in _hue(o["sh"])), "hi": tuple(float(x) for x in _hue(o["hi"])),
            "sk": (math.cos(a), math.sin(a), math.cos(h), math.cos(f)), "drop": o["drop"]}


def _ss(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def skin_key(rgb):
    """The shader's skin key on an array of rgb in 0..1: 1 on skin, 0 off it."""
    y = rgb @ LUMA
    yy = np.maximum(y, 0.05)
    u, w = (rgb[..., 0] - y) / yy, (y - rgb[..., 2]) / yy
    m = np.hypot(u, w)
    a, h, f = math.radians(SKIN_AXIS), math.radians(SKIN_HALF), math.radians(SKIN_HALF + SKIN_FEATHER)
    along = (u * math.cos(a) + w * math.sin(a)) / np.maximum(m, 1e-4)
    return _ss(math.cos(f), math.cos(h), along) * _ss(0.07, 0.15, m) * (1 - _ss(0.95, 1.25, m)) * _ss(0.06, 0.16, y)


def window_of(face, pad=1.0):
    """The window a face gets, in its picture's fractions: [centre x, centre y, radius x, radius y]. The look's
    ``drop`` leaves everything inside it at its level and takes the rest of the picture down, softly."""
    x, y, w, h = face[:4]
    return [round(x + w / 2, 5), round(y + h * 0.5, 5), round(w * 0.75 * pad, 5), round(h * 0.8 * pad, 5)]


def apply(rgb, lk, win=None):
    """The numpy twin of glsl ``look()``. rgb: float array, 0..1, last axis 3. ``win``: how far inside the face's
    window each pixel is (0..1, a number or an array); None reads skin itself as the inside of the window,
    which is what the solver wants: it has pixels, not places."""
    o = clean(lk)
    c = np.clip(np.asarray(rgb, np.float64), 0.0, 1.0)
    y = c @ LUMA
    keep = skin_key(c) * o["skin"]
    inside = skin_key(c) if win is None else np.broadcast_to(np.asarray(win, np.float64), y.shape)
    c = c * (o["drop"] + (1.0 - o["drop"]) * inside)[..., None]
    y = c @ LUMA
    p = o["pivot"]
    k = o["con"] + (1.0 + (o["con"] - 1.0) * 0.5 - o["con"]) * keep
    t = np.where(y < p, p * np.power(np.maximum(y, 0) / p, k), 1.0 - (1.0 - p) * np.power(np.maximum(1.0 - y, 0) / (1.0 - p), k))
    c = c * np.minimum(t / np.maximum(y, 1e-3), 4.0)[..., None]
    s = o["sat"] + (min(max(o["sat"], 0.92), 1.08) - o["sat"]) * keep
    c = t[..., None] + (c - t[..., None]) * s[..., None]
    c = o["black"] + (o["white"] - o["black"]) * c
    free = 1.0 - keep
    c = c + _hue(o["sh"]) * (o["sh_a"] * (1 - _ss(0.0, 0.5, t)) * free)[..., None] + _hue(o["hi"]) * (o["hi_a"] * _ss(0.5, 1.0, t) * (1 - 0.5 * keep))[..., None]
    return c


def _lab(rgb):
    """sRGB 0..1 to CIELAB (D65). Small and exact enough for a meter."""
    c = np.clip(rgb, 0, 1)
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def numbers(px, skin=None):
    """The meter: L* percentiles of the picture (near-black left out, as bars and plates are), mean chroma, and skin's
    L*. ``skin``: the pixels to read as skin (a mask), so a graded picture is read on the skin its source had."""
    lab = _lab(px)
    L = lab[..., 0]
    m = L > 1.5
    Lm = L[m] if m.sum() > 50 else L
    sk = (skin_key(px) > 0.5) if skin is None else skin
    return {"L5": float(np.percentile(Lm, 5)), "L50": float(np.percentile(Lm, 50)), "L95": float(np.percentile(Lm, 95)),
            "C": float(np.hypot(lab[..., 1], lab[..., 2])[m].mean()) if m.sum() > 50 else 0.0,
            "skin_L": float(np.median(L[sk])) if sk.sum() > 40 else None, "skin_share": float(sk.mean())}


def pixels(pictures, n=6000, seed=7):
    """A fixed sample of pixels from the film's pictures (arrays HxWx3 uint8 or float): what the solver reads."""
    rng = np.random.RandomState(seed)
    out = []
    per = max(n // max(len(pictures), 1), 200)
    for a in pictures:
        a = np.asarray(a)
        a = a[..., :3].reshape(-1, 3)
        a = a.astype(np.float64) / (255.0 if a.dtype == np.uint8 else 1.0)
        out.append(a[rng.randint(0, len(a), size=min(per, len(a)))])
    return np.concatenate(out) if out else np.zeros((0, 3))


def solve(name_or_look, px, target=None):
    """Fit a design to a film's own pixels. Returns (look, grade, report): the look's numbers, the balance to draw
    before it (the engine's ``grade``: exposure as gain), and what was measured before and after.

    Exposure first, by at most EV_MAX stops: with people in the film, skin goes to the references' skin level and
    never under SKIN_L_MIN; with none, the median goes to the target's. Then contrast until the spread
    (L95 - L5) reaches the target's, then colour until the chroma does.
    Each is a bisection on one number, in that order, so the same pixels give the same look."""
    base = dict(LOOKS[name_or_look]) if isinstance(name_or_look, str) else dict(name_or_look)
    tgt = dict(target or TARGET.get(base.get("family") or "story") or TARGET["story"])
    lk = clean(base)
    if len(px) < 200:
        return lk, {}, {"before": None, "after": None, "target": tgt}
    sk0 = skin_key(px) > 0.5                          # the source's own skin: every reading of skin is taken on these pixels
    before = numbers(px, sk0)

    def run(gain, con, sat, drop=1.0):
        return numbers(apply(np.clip(px * gain, 0, 1), dict(lk, con=con, sat=sat, drop=drop)), sk0)

    def bisect(lo, hi, f, want, n=12):
        """f rises with its argument: the argument where it meets ``want``, kept inside [lo, hi]."""
        if f(lo) >= want:
            return lo
        if f(hi) <= want:
            return hi
        for _ in range(n):
            mid = (lo + hi) / 2
            if f(mid) < want:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    g_lo, g_hi = 2.0 ** -EV_MAX, 2.0 ** EV_MAX
    if before["skin_L"] is not None and before["skin_share"] >= 0.02 and tgt.get("skin_L"):
        # a film with people in it is exposed for them: skin goes to where the references' skin sits, and never
        # under the floor (or under the source's own level, when the source never cleared it)
        want = max(tgt["skin_L"], min(SKIN_L_MIN, before["skin_L"]))
        gain = bisect(g_lo, g_hi, lambda g: (run(g, lk["con"], lk["sat"])["skin_L"] or 100.0), want)
    else:
        gain = bisect(g_lo, g_hi, lambda g: run(g, lk["con"], lk["sat"])["L50"], tgt["L50"])
    drop = 1.0
    if before["skin_L"] is not None and before["skin_share"] >= 0.02 and lk["skin"] > 0:
        # the room comes down to the references' median while the face stays: never under DROP_MIN of its level
        drop = bisect(DROP_MIN, 1.0, lambda d: run(gain, lk["con"], lk["sat"], d)["L50"], tgt["L50"])
    con = bisect(CON[0], CON[1], lambda c: (lambda m: m["L95"] - m["L5"])(run(gain, c, lk["sat"], drop)), tgt["L95"] - tgt["L5"])
    con = (con + lk["con"]) / 2                       # half way from the design to what the numbers ask: the design still shows
    sat = lk["sat"] if lk["sat"] == 0 else bisect(SAT[0], SAT[1], lambda s: run(gain, con, s, drop)["C"], tgt["C"])
    sat = sat if lk["sat"] == 0 else (sat + lk["sat"]) / 2
    if before["skin_L"] is not None and before["skin_share"] >= 0.02 and tgt.get("skin_L"):
        # the contrast moved the face: bring it back to its level, with the look as solved
        want = max(tgt["skin_L"], min(SKIN_L_MIN, before["skin_L"]))
        gain = bisect(g_lo, g_hi, lambda g: (run(g, con, sat, drop)["skin_L"] or 100.0), want)
    out = dict(lk, con=round(con, 4), sat=round(sat, 4), drop=round(drop, 4))
    after = numbers(apply(np.clip(px * gain, 0, 1), out), sk0)
    return out, {"gain": round(gain, 4)}, {"before": before, "after": after, "target": tgt}


def film(name, pictures=None, target=None, base=None):
    """A ``film`` dict for a scene: the look (solved on ``pictures`` when given), the film layer's amounts and the
    shutter. ``base`` is a film dict to build on (its own keys win only where this look names none)."""
    d = LOOKS[name]
    if pictures is not None:
        lk, gr, rep = solve(name, pixels(pictures), target)
    else:
        lk, gr, rep = clean(d), {}, None
    out = dict(base or {})
    out["look"] = {k: lk[k] for k in LOOK_KEYS}
    for k in FILM_KEYS:
        out[k] = d[k]
    if gr.get("gain"):
        g = dict({"lift": 0.0, "gamma": 1.0, "gain": 1.0, "sat": 1.0, "contrast": 1.0, "temp": 0.0, "tint": 0.0}, **(out.get("grade") or {}))
        g["gain"] = round(g["gain"] * gr["gain"], 4)
        out["grade"] = g
    out["look_name"] = name
    if rep:
        out["look_report"] = rep
    return out
