"""Selective colour: one hue kept in a grey world (Schindler's coat), and colour blooming back out of mono from
that hue. The kept hue is measured on the picture (its strongest colour) unless the ask names one."""
from ._util import arg, base_at, ease, frames, uid

LAYER = "surface"


def _key(l1, ask, sense, f):
    c = arg(ask, "c")
    if c:
        return c
    sh = base_at(l1, f)
    if sh and sh.get("pic") not in (None, "plate"):
        return sense.hue(sh["pic"], float((sh.get("src") or [0, 0])[0]))
    return "#D2302A"


def keep_hue(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=45, min_len=6)
    key = _key(l1, ask, sense, (a + b) // 2)
    it = {"id": uid(l1, "pd_kh"), "k": "flash", "f": [a, b], "mode": "keep_hue", "c": key, "th": float(arg(ask, "width", 0.07)), "z": 20}
    return {"items": [it], "log": {"key": key}, "events": [{"f": a, "kind": "colour", "strength": 0.4}]}


def bloom(l1, work, ask, sense):
    """Grey with one hue kept, then the band of kept hues widens until the whole picture is in colour."""
    a, b = frames(l1, ask, default_len=30, min_len=8)
    key = _key(l1, ask, sense, a)
    n = b - a
    hold = max(int(n * float(arg(ask, "hold", 0.35))), 1)
    th = [0.06] * hold + [round(v, 4) for v in ease(n - hold, 0.06, 0.62)]
    it = {"id": uid(l1, "pd_kb"), "k": "flash", "f": [a, b], "mode": "keep_hue", "c": key, "th": th, "z": 20}
    return {"items": [it], "log": {"key": key}, "events": [{"f": a + hold, "kind": "colour", "strength": 0.5}]}


DEVICES = {
    "keep_hue": {"plan": keep_hue, "needs": (), "fallback": (),
                 "card": "f c? width. The world in grey, one hue kept (measured: the picture's strongest colour). For the one "
                         "thing that matters in the frame: the red jersey, the brand, the money"},
    "colour_bloom": {"plan": bloom, "needs": (), "fallback": ("keep_hue",),
                     "card": "f c? hold. Grey with one hue, then colour blooms back out of it. For a turn to life, success arriving, "
                             "the present after the past"},
}
