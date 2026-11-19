"""What every family of the palette shares: frames of an ask, the base pictures under it, cutting a shot, curves."""
import math
import os

from ..plan import ANIM, ANIM_RECT

KEEPOUT = (0.5594, 0.115, 0.9146, 0.1923)      # the watermark's corner (score.compiler.KEEPOUT): no device type there


def arg(ask, k, default=None):
    """An argument of the ask: on the ask itself or under ``args``."""
    if k in ask and ask[k] is not None:
        return ask[k]
    return (ask.get("args") or {}).get(k, default)


def frames(l1, ask, default_len=12, min_len=1):
    """(a, b): the programme frames an ask covers, clamped to the film. ``f``, ``at``/``until``/``len`` in frames,
    or ``t``/``t_end``/``dur`` in seconds; a span too short is lengthened to ``min_len`` (the moment, repaired)."""
    dur = int(l1["dur_f"])
    fps = float(l1.get("fps") or 30)
    if isinstance(ask.get("f"), (list, tuple)) and len(ask["f"]) == 2:
        a, b = int(ask["f"][0]), int(ask["f"][1])
    else:
        if ask.get("at") is not None and not isinstance(ask.get("at"), dict):
            a = int(ask["at"])
        elif ask.get("t") is not None:
            a = int(round(float(ask["t"]) * fps))
        else:
            a = 0
        if ask.get("until") is not None and not isinstance(ask.get("until"), dict):
            b = int(ask["until"])
        elif ask.get("t_end") is not None:
            b = int(round(float(ask["t_end"]) * fps))
        elif ask.get("dur") is not None:
            b = a + int(round(float(ask["dur"]) * fps))
        else:
            b = a + int(arg(ask, "len", default_len))
    a = min(max(a, 0), dur - 1)
    b = min(max(b, a + max(min_len, 1)), dur)
    if b - a < min_len:
        a = max(b - min_len, 0)
    return a, b


def shots(l1):
    return sorted((i for i in l1.get("items") or [] if i.get("k") == "shot"), key=lambda i: i["f"][0])


def base_at(l1, f):
    for s in shots(l1):
        if s["f"][0] <= f < s["f"][1]:
            return s
    return None


def over(l1, a, b):
    """The base shots that overlap [a, b)."""
    return [s for s in shots(l1) if s["f"][0] < b and s["f"][1] > a]


def take_id(l1):
    """The take's asset: ``main``, else the asset the spine runs on."""
    assets = l1.get("assets") or {}
    if "main" in assets:
        return "main"
    for sp in l1.get("spine") or []:
        if sp.get("pic") in assets:
            return sp["pic"]
    return None


def is_take(l1, shot):
    """Does this shot show the take (the asset itself, or another name for the same file)?"""
    t = take_id(l1)
    if not t or not shot or not shot.get("src"):
        return False
    assets = l1.get("assets") or {}
    pic = shot.get("pic")
    if pic == t or str(pic).startswith("own:cut"):
        return True
    a, b = assets.get(pic) or {}, assets.get(t) or {}
    return bool(a.get("path")) and os.path.normpath(str(a.get("path"))) == os.path.normpath(str(b.get("path")))


def pictures(l1, ask=None, kinds=("image", "video")):
    """The pictures an ask may use: its own ``pics`` first, then the film's other pictures in order of first use."""
    assets = l1.get("assets") or {}
    t = take_id(l1)
    take_path = os.path.normpath(str((assets.get(t) or {}).get("path"))) if t else None

    def ok(k):
        a = assets.get(k) or {}
        return (k not in ("matte", t) and a.get("kind") in kinds and not str(k).startswith("own:")
                and os.path.normpath(str(a.get("path"))) != take_path and not str(k).startswith("pal:"))
    out = [p for p in (arg(ask or {}, "pics") or []) if ok(p)]
    for it in sorted(l1.get("items") or [], key=lambda i: i["f"][0]):
        p = it.get("pic")
        if p and ok(p) and p not in out:
            out.append(p)
    for k in sorted(assets):
        if ok(k) and k not in out:
            out.append(k)
    return out


def uid(l1, stem):
    ids = {i["id"] for i in l1.get("items") or []}
    n = 0
    while f"{stem}{n}" in ids:
        n += 1
    return f"{stem}{n}"


def _slice(v, lo, hi, a0):
    """A per-frame list of an item that starts at a0, cut to [lo, hi)."""
    if not isinstance(v, list) or not v:
        return v
    return v[lo - a0:hi - a0] or v[-1:]


def cut(it, lo, hi):
    """The part of an item that falls in [lo, hi): its per-frame lists cut to match, its source clock with it."""
    a0, b0 = it["f"]
    lo, hi = max(lo, a0), min(hi, b0)
    o = dict(it, f=[lo, hi])
    for k in ANIM.get(it["k"], ()):
        if isinstance(o.get(k), list):
            o[k] = _slice(o[k], lo, hi, a0)
    for k in ANIM_RECT.get(it["k"], ()):
        if isinstance(o.get(k), list) and o[k] and isinstance(o[k][0], list):
            o[k] = _slice(o[k], lo, hi, a0)
    if it.get("src"):
        s0, s1 = it["src"]
        n = max(b0 - a0, 1)
        o["src"] = [s0 + (s1 - s0) * (lo - a0) / n, s0 + (s1 - s0) * (hi - a0) / n]
    return o


def make_room(l1, a, b, stem):
    """Base shots cut so that [a, b) is free for a new base picture: (items to drop, items to add). Each shot that
    overlapped keeps what lies outside the window, under its own id where it can."""
    drop, add = [], []
    for s in over(l1, a, b):
        drop.append(s["id"])
        if s["f"][0] < a:
            add.append(dict(cut(s, s["f"][0], a), id=s["id"]))
        if s["f"][1] > b:
            add.append(dict(cut(s, b, s["f"][1]), id=s["id"] if s["f"][0] >= a else uid(l1, stem + "r")))
    return drop, add


def frac_crop(iw, ih, W, H, cx=0.5, cy=0.5, zoom=1.0):
    """A crop of the picture (0..1) that covers a W x H frame, centred on (cx, cy), ``zoom`` times tighter."""
    ta, ra = iw / ih, W / H
    cw, ch = (ra / ta, 1.0) if ta > ra else (1.0, ta / ra)
    cw, ch = cw / zoom, ch / zoom
    x0 = min(max(cx - cw / 2, 0.0), 1.0 - cw)
    y0 = min(max(cy - ch / 2, 0.0), 1.0 - ch)
    return [x0, y0, x0 + cw, y0 + ch]


def decel(n, a, b, k=4.0):
    """n values from a to b that move fast first and settle (exponential out): the references' push, peaking early."""
    if n <= 1:
        return [float(b)]
    d = 1.0 - math.exp(-k)
    return [a + (b - a) * (1.0 - math.exp(-k * i / (n - 1))) / d for i in range(n)]


def ease(n, a, b):
    if n <= 1:
        return [float(b)]
    return [a + (b - a) * (0.5 - 0.5 * math.cos(math.pi * i / (n - 1))) for i in range(n)]


def envelope(n, attack=3, release=6, peak=1.0, floor=0.0):
    """Up in ``attack`` frames, held, down over the last ``release``."""
    out = []
    for i in range(n):
        up = min(1.0, (i + 1) / max(attack, 1))
        down = min(1.0, (n - i) / max(release, 1))
        out.append(floor + (peak - floor) * min(up, down))
    return out


def hit(n, peak=1.0, tail=0.15, k=3.0):
    """Strong on its first frame, dying away: a glitch that lands and decays."""
    return [tail + (peak - tail) * math.exp(-k * i / max(n - 1, 1)) for i in range(n)]
