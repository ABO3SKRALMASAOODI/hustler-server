"""arc: a look that is the story's calendar and its stakes, on every frame of the film. Each sentence of a
story speaks from a year and from a height (how things stand for its hero). The film's finish follows the
year: spoken into 1927 the picture ages on the word, colour drains to silver, the gate weaves and the lamp
flickers, whatever the footage is and whenever it was shot; back in 2006 it clears. Its light follows the
height: as things sink the room darkens round the faces, colour thins and the frame itself closes in; on
the turn it opens in two frames. An editor grades three sections by hand. This binds every shot of a film
to the sentence it stands under, and footage from five decades and ten sources becomes one telling.

Who reads the year and the height is the caller (a model reads the script; nothing here reads a word).
This module turns those marks into numbers per shot and per frame, with the engine's own look.

ask  {"dev": "arc", "marks": [{"f": frame, "year": 1927, "stakes": -0.4}, ...]   (a mark holds until the next;
              stakes -1..1 is read as a line between marks; "year" may be absent: the film's own time)
      "now": 2026, "bars": 0.085 (of the frame's height, each, at the lowest point), "develop": 6 (frames a
      new era takes to come up, on its word), "weave": true, "flicker": true}
"""
import math
import random

from . import sense
from .. import looks

# What a year looks like once it has been through its own film stock, a telecine and a phone: numbers of the
# engine's look. Between two rows a year is read on a line.
#        year   sat   con   black  white  shadow hue, amount   highlight hue, amount   weave  flicker
ERAS = ((1905, 0.00, 1.34, 0.050, 0.900, "#161A22", 0.030, "#FFF1D8", 0.030, 1.00, 1.00),
        (1930, 0.00, 1.30, 0.040, 0.920, "#181C24", 0.026, "#FFF3DC", 0.024, 0.85, 0.80),
        (1948, 0.00, 1.24, 0.026, 0.945, "#1A1E26", 0.020, "#FFF6E6", 0.014, 0.45, 0.35),
        (1962, 0.42, 1.14, 0.036, 0.940, "#2A2014", 0.040, "#FFE9BE", 0.036, 0.30, 0.15),
        (1975, 0.72, 1.08, 0.040, 0.945, "#3A2410", 0.046, "#FFE2B0", 0.040, 0.20, 0.06),
        (1990, 0.86, 1.10, 0.024, 0.960, "#141C30", 0.026, "#F4F0FF", 0.012, 0.05, 0.00),
        (2008, 1.00, 1.00, 0.000, 1.000, None, 0.0, None, 0.0, 0.0, 0.0))
OWN = 2008            # from here on a year is the film's own time: its look as solved, untouched


def _hex(c):
    c = c.lstrip("#")
    return [int(c[i:i + 2], 16) for i in (0, 2, 4)]


def _mix_hex(a, b, t):
    if a is None or b is None:
        return a if t < 0.5 else b
    x, y = _hex(a), _hex(b)
    return "#" + "".join(f"{int(round(x[i] + (y[i] - x[i]) * t)):02X}" for i in range(3))


def era(year):
    """The finish of a year: {"sat", "con", "black", "white", "sh", "sh_a", "hi", "hi_a", "weave", "flicker"}, or
    None when the year is the film's own time."""
    if year is None or year >= OWN:
        return None
    y = max(float(year), ERAS[0][0])
    for lo, hi in zip(ERAS, ERAS[1:]):
        if y <= hi[0]:
            t = (y - lo[0]) / (hi[0] - lo[0])
            n = lambda i: lo[i] + (hi[i] - lo[i]) * t      # noqa: E731
            return {"sat": n(1), "con": n(2), "black": n(3), "white": n(4), "sh": _mix_hex(lo[5], hi[5], t) or lo[5], "sh_a": n(6),
                    "hi": _mix_hex(lo[7], hi[7], t) or lo[7], "hi_a": n(8), "weave": n(9), "flicker": n(10)}
    return None


def base_look(l1, shot):
    lk = shot.get("look") if "look" in shot else (l1.get("film") or {}).get("look")
    if isinstance(lk, str):
        lk = looks.LOOKS.get(lk)
    return looks.clean(lk or {})


def aged(base, e):
    """The film's own look taken all the way to an era's finish."""
    if not e:
        return dict(base)
    o = dict(base)
    o["sat"] = base["sat"] * e["sat"]
    o["con"] = base["con"] * e["con"]
    o["black"] = max(base["black"], e["black"])
    o["white"] = min(base["white"], e["white"])
    o["sh"], o["sh_a"], o["hi"], o["hi_a"] = e["sh"], e["sh_a"], e["hi"], e["hi_a"]
    if e["sat"] < 0.05:
        o["skin"] = 0.0                                    # a silver print keeps no skin
    return o


def mix_look(a, b, k):
    """Between two whole looks: numbers on a line, hues on a line in RGB."""
    o = {}
    for key in looks.LOOK_KEYS:
        x, y = a.get(key), b.get(key)
        if key in ("sh", "hi"):
            o[key] = _mix_hex(x, y, k) if (x and y) else (y if k >= 0.5 else x)
        else:
            o[key] = float(x) + (float(y) - float(x)) * k
    for key, amt in (("sh", "sh_a"), ("hi", "hi_a")):      # a hue that only one side has comes up with its amount
        if not a.get(key) and b.get(key):
            o[key], o[amt] = b[key], float(b[amt]) * k
        elif a.get(key) and not b.get(key):
            o[key], o[amt] = a[key], float(a[amt]) * (1 - k)
    return o


def lit(look, stakes):
    """A look under the story's stakes: as things sink the room comes down round the face, colour thins and
    the top of the range closes; on the way up colour comes back a little past where it was."""
    o = dict(look)
    low, high = max(-stakes, 0.0), max(stakes, 0.0)
    o["sat"] = o["sat"] * (1.0 - 0.42 * low + 0.07 * high)
    o["con"] = o["con"] * (1.0 + 0.05 * low)
    o["drop"] = max(min(float(o.get("drop", 1.0)), 1.0) - 0.20 * low, looks.DROP_MIN)
    o["white"] = o["white"] * (1.0 - 0.10 * low)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in looks.clean(o).items()}


def stakes_at(marks, f):
    pts = [(m["f"], float(m["stakes"])) for m in marks if m.get("stakes") is not None]
    if not pts:
        return 0.0
    if f <= pts[0][0]:
        return pts[0][1]
    for (f0, s0), (f1, s1) in zip(pts, pts[1:]):
        if f <= f1:
            t = (f - f0) / max(f1 - f0, 1)
            t = t * t * (3 - 2 * t)
            return s0 + (s1 - s0) * t
    return pts[-1][1]


def year_at(marks, f):
    y = None
    for m in marks:
        if m["f"] <= f and "year" in m:
            y = m["year"]
    return y


def plan(l1, work, ask):
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    D = int(l1["dur_f"])
    marks = sorted(({**m, "f": int(m["f"])} for m in (ask.get("marks") or []) if m.get("f") is not None), key=lambda m: m["f"])
    if not marks:
        return {"skip": "arc needs marks"}
    develop = int(ask.get("develop") if ask.get("develop") is not None else 6)
    rng = random.Random(int(l1.get("seed") or 0) ^ 0xA2C)
    # where the finish changes: each change lands on its frame, and the new finish comes up over ``develop`` frames
    changes, last = [], "start"
    for m in marks:
        if "year" in m:
            key = None if era(m.get("year")) is None else round(float(m["year"]))
            if key != last and last != "start":                    # a film begins in its first year: nothing changes there
                changes.append(m["f"])
            last = key
    edges = sorted({c + j for c in changes for j in range(0, develop + 1, 2)})
    shots = sorted((i for i in l1.get("items") or [] if i.get("k") == "shot"), key=lambda i: i["f"][0])
    pieces = []
    for sh in shots:
        bounds = [sh["f"][0]] + [e for e in edges if sh["f"][0] < e < sh["f"][1]] + [sh["f"][1]]
        for j, (p0, p1) in enumerate(zip(bounds, bounds[1:])):
            pieces.append(sense.slice_shot(sh, p0, p1, sh["id"] if j == 0 else f"{sh['id']}~e{j}"))
    out, log_eras, old = [], [], []
    for sh in pieces:
        if sh.get("pic") == "plate":
            out.append(sh)
            continue
        f0, f1 = sh["f"]
        base = base_look(l1, sh)
        c = max([x for x in changes if x <= f0], default=None)
        e_to = era(year_at(marks, f0))
        k, e_from = 1.0, None
        if c is not None and develop and f0 - c < develop:
            k = min((f0 - c + 2) / float(develop + 2), 1.0)          # this piece's place on the ramp
            e_from = era(year_at(marks, c - 1))
        look = mix_look(aged(base, e_from), aged(base, e_to), k) if k < 1.0 else aged(base, e_to)
        s = stakes_at(marks, (f0 + f1) // 2)
        if e_to or e_from or abs(s) > 1e-3:
            sh["look"] = lit(look, s)
        age = e_to
        if age and ask.get("weave", True) and age["weave"] > 0.02 and f1 - f0 >= 2:
            p = sense.path_of(l1, work, sh.get("pic"))
            try:
                info = sense.probe(p) if p else None
            except Exception:      # noqa: BLE001
                info = None
            if info and info["w"]:
                # the gate: the picture wanders a pixel or two up and down, with a rare jump, as a worn print does
                amp = 0.0016 * age["weave"]
                ph, crops = rng.random() * 6.28, []
                for f in range(f0, f1):
                    cr = sense.crop_at(sh, f, info["w"], info["h"], W, H)
                    ch = cr[3] - cr[1]
                    dy = amp * ch * (math.sin(ph + f * 0.9) + 0.6 * math.sin(ph * 1.7 + f * 2.3)) + (amp * 2.2 * ch if rng.random() < 0.035 else 0.0)
                    dy = min(max(dy, -cr[1]), 1.0 - cr[3])
                    crops.append([round(cr[0], 6), round(cr[1] + dy, 6), round(cr[2], 6), round(cr[3] + dy, 6)])
                sh["crop"] = crops
        if age:
            old.append((f0, f1, age["flicker"] * k))
            log_eras.append({"f": [f0, f1], "year": year_at(marks, f0), "k": round(k, 2)})
        out.append(sh)
    items = list(out)
    if ask.get("flicker", True) and old:
        a_ = [0.0] * D
        for f0, f1, amt in old:
            for f in range(f0, min(f1, D)):
                r = rng.random()
                a_[f] = round(amt * (0.035 * r + (0.07 if r > 0.93 else 0.0)), 4)   # the lamp: a breath every frame, a dip now and then
        lo, hi = min(f0 for f0, _, _ in old), min(max(f1 for _, f1, _ in old), D)
        if max(a_[lo:hi]) > 0:
            items.append({"id": f"{ask['id']}.lampf", "k": "flash", "f": [lo, hi], "mode": "fill", "c": "#000000", "a": a_[lo:hi], "z": -5})
    bars = float(ask.get("bars") if ask.get("bars") is not None else 0.085)
    hgt = [round(bars * max(-stakes_at(marks, f), 0.0) ** 1.2, 5) for f in range(D)]
    if bars > 0 and max(hgt) > 0.004:
        for j in range(1, D):                                        # closing is slow; opening is as fast as the marks say
            if hgt[j] > hgt[j - 1] + 0.0016:
                hgt[j] = round(hgt[j - 1] + 0.0016, 5)
        lo = next(i for i, v in enumerate(hgt) if v > 0.0005)
        hi = D - next(i for i, v in enumerate(reversed(hgt)) if v > 0.0005)
        if hi - lo >= 2:
            items.append({"id": f"{ask['id']}.bar0", "k": "flash", "f": [lo, hi], "mode": "fill", "c": "#000000", "a": 1.0,
                          "rect": [[0.0, 0.0, 1.0, max(v, 1e-5)] for v in hgt[lo:hi]], "z": -4})
            items.append({"id": f"{ask['id']}.bar1", "k": "flash", "f": [lo, hi], "mode": "fill", "c": "#000000", "a": 1.0,
                          "rect": [[0.0, 1.0 - max(v, 1e-5), 1.0, 1.0] for v in hgt[lo:hi]], "z": -4})
    return {"items": items, "drop": [s["id"] for s in shots],
            "marks": [{"f": c, "what": "era"} for c in changes],
            "log": {"eras": log_eras[:40], "changes": changes, "bars_max": max(hgt) if hgt else 0.0,
                    "stakes": [round(stakes_at(marks, f), 3) for f in range(0, D, max(D // 24, 1))]}}
