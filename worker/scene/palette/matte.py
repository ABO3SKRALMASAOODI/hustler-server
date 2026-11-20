"""Matte graphics: the take itself becomes the picture. The person is cut from his own shot by his matte (RVM, made
now when the film has none) and the world behind him changes while he stays: a background swap, a strobe of plates
behind a locked figure, a flat silhouette or a two-colour poster on a field of colour, a halo, ghost clones that
slide out of him, an outline sticker, and selective colour on the take (the room in grey, the person in colour).

Everything compiles to the engine's depth sandwich: opaque cards ``behind: true`` drawn over the take, then the
person drawn again through his matte, as himself or as a pass of ``shot.keep`` (plan.KEEP_MODES).

A moment asked where the take is not on screen is moved to the nearest stretch of the take (logged): the device
lands on the person nearest the moment, never nowhere. A film with no take runs the fallback chain."""
from ._util import arg, colour, cut, decel, envelope, frames, is_take, num, pictures, shots, take_id, uid

LAYER = "surface"
FIELDS = ("#E8341C", "#1F4BFF", "#FFD21F", "#0E0E0E", "#F2EFE6", "#13A35A")


def _windows(l1, a, b, min_len=6):
    """[(shot, lo, hi)] of the take inside [a, b); when the take is not there, the nearest stretch of it."""
    out = [(s, max(a, s["f"][0]), min(b, s["f"][1])) for s in shots(l1) if is_take(l1, s) and s["f"][0] < b and s["f"][1] > a]
    out = [w for w in out if w[2] - w[1] >= 2]
    moved = False
    if not out:
        takes = [s for s in shots(l1) if is_take(l1, s) and s["f"][1] - s["f"][0] >= min_len]
        if not takes:
            return [], False
        s = min(takes, key=lambda s: min(abs(s["f"][0] - a), abs(s["f"][1] - b)))
        n = max(b - a, min_len)
        lo = min(max(a, s["f"][0]), max(s["f"][1] - n, s["f"][0]))
        out, moved = [(s, lo, min(lo + n, s["f"][1]))], True
    return out, moved


def _ready(l1, sense, ask, default_len=30, min_len=6):
    """(windows, patch base) or (None, skip patch): the take's windows, the matte made, the shots named as the take."""
    if not take_id(l1):
        return None, {"skip": "the film has no take to cut the person from"}
    a, b = frames(l1, ask, default_len=default_len, min_len=min_len)
    wins, moved = _windows(l1, a, b, min_len)
    if not wins:
        return None, {"skip": "the take is never long enough on screen"}
    got = sense.matte()
    if not got or "matte" not in got:
        return None, {"skip": "no matte could be made for the take"}
    base = {"items": [], "assets": {"matte": got["matte"]}, "log": {"f": [a, b], "moved": moved, "windows": [[w[1], w[2]] for w in wins]}}
    t = take_id(l1)
    if t != "main" and "main" not in l1["assets"]:
        base["assets"]["main"] = l1["assets"][t]
    return wins, base


def _shot_with(l1, shot, passes):
    """The take shot named as the take for the engine's sandwich, with ``passes`` added to its keep list."""
    o = dict(shot)
    if o.get("pic") != "main" and not str(o.get("pic")).startswith("own:cut"):
        o["pic"] = "main"
    o["keep"] = list(o.get("keep") or []) + passes
    return o


def _behind(l1, items, lo, hi, **kw):
    it = dict({"id": uid(dict(l1, items=(l1.get("items") or []) + items), "pd_bh"), "k": "card", "f": [lo, hi], "x": 0.5, "y": 0.5,
               "w": 1.0, "h": 1.0, "s": 1.0, "op": 1.0, "behind": True, "z": 3}, **kw)
    return it


def _picture_card(l1, items, pic, lo, hi, push=0.06):
    a = l1["assets"][pic]
    kw = {"pic": pic, "s": [round(v, 5) for v in decel(hi - lo, 1.0 + push, 1.0, 3.0)]}
    if a.get("kind") == "video":
        kw["src"] = [0.0, (hi - lo) / float(l1.get("fps") or 30)]
    return _behind(l1, items, lo, hi, **kw)


def _plate(l1, items, lo, hi, c):
    return _behind(l1, items, lo, hi, pic="plate", plate={"c0": c, "c1": c, "grain": 0.03})


def _field(l1, ask, i=0):
    c = colour(ask, "c") or colour(ask, "field")
    if c:
        return c
    import zlib
    return FIELDS[(zlib.crc32(f"{l1.get('seed')}:{ask.get('id')}".encode()) + i) % len(FIELDS)]


def bg_swap(l1, work, ask, sense):
    wins, p = _ready(l1, sense, ask)
    if wins is None:
        return p
    pics = pictures(l1, ask)
    for i, (sh, lo, hi) in enumerate(wins):
        if pics:
            p["items"].append(_picture_card(l1, p["items"], pics[i % len(pics)], lo, hi))
        else:
            p["items"].append(_plate(l1, p["items"], lo, hi, _field(l1, ask, i)))
        p["items"].append(_shot_with(l1, sh, []))
    p["events"] = [{"f": wins[0][1], "kind": "swap", "strength": 0.8}]
    return p


def plate_strobe(l1, work, ask, sense):
    """The world behind a locked figure changes every few frames: pictures, or plates of one light (so it is a
    strobe of pictures, never of light)."""
    wins, p = _ready(l1, sense, ask, default_len=24, min_len=8)
    if wins is None:
        return p
    hold = max(int(num(ask, "hold", 3)), 2)
    pics = pictures(l1, ask)
    plates = [c for c in ("#E8341C", "#1F4BFF", "#13A35A", "#B0249A") if c != colour(ask, "skip")]
    evs, k = [], 0
    for sh, lo, hi in wins:
        f = lo
        while f < hi:
            g = min(f + hold, hi)
            if pics:
                p["items"].append(_picture_card(l1, p["items"], pics[k % len(pics)], f, g, push=0.0))
            else:
                p["items"].append(_plate(l1, p["items"], f, g, plates[k % len(plates)]))
            evs.append({"f": f, "kind": "strobe", "strength": 0.4})
            k += 1
            f = g
        p["items"].append(_shot_with(l1, sh, []))
    p["events"] = evs
    p["log"]["plates"] = k
    return p


def silhouette(l1, work, ask, sense):
    wins, p = _ready(l1, sense, ask)
    if wins is None:
        return p
    field, ink = _field(l1, ask), colour(ask, "ink", "#0B0B0B")
    if field.upper() in ("#0E0E0E", "#0B0B0B"):
        ink = "#F2EFE6"
    for sh, lo, hi in wins:
        p["items"].append(_plate(l1, p["items"], lo, hi, field))
        passes = [{"mode": "fill", "c": ink, "f": [lo, hi]}]
        if arg(ask, "glow"):                        # a glow asked as true is the warm halo
            passes.insert(0, {"mode": "glow", "c": colour(ask, "glow", "#FFD27A"), "w": 34, "f": [lo, hi]})
        p["items"].append(_shot_with(l1, sh, passes))
    p["events"] = [{"f": wins[0][1], "kind": "silhouette", "strength": 0.9}]
    return p


def poster(l1, work, ask, sense):
    """The person in two colours of ink on a field, with a paper-white outline: a screen-printed poster."""
    wins, p = _ready(l1, sense, ask)
    if wins is None:
        return p
    field = _field(l1, ask)
    dark, light = colour(ask, "ink", "#121212"), colour(ask, "light", "#F2EFE6")
    for sh, lo, hi in wins:
        p["items"].append(_plate(l1, p["items"], lo, hi, field))
        p["items"].append(_shot_with(l1, sh, [{"mode": "outline", "c": light, "w": 12, "f": [lo, hi]},
                                              {"mode": "duo", "c": dark, "c2": light, "f": [lo, hi]}]))
    p["events"] = [{"f": wins[0][1], "kind": "poster", "strength": 0.8}]
    return p


def glow(l1, work, ask, sense):
    """A halo round the person that swells on the moment and settles: he lights up, the room stays."""
    wins, p = _ready(l1, sense, ask, default_len=24)
    if wins is None:
        return p
    c = colour(ask, "c", "#FFD27A")
    for sh, lo, hi in wins:
        s0 = sh["f"][0]
        n = sh["f"][1] - s0
        env = [0.0] * n
        for i, v in enumerate(envelope(hi - lo, 4, max((hi - lo) // 2, 4), num(ask, "amount", 0.9))):
            env[lo - s0 + i] = round(v, 4)
        p["items"].append(_shot_with(l1, sh, [{"mode": "glow", "c": c, "w": min(max(num(ask, "w", 36), 14.0), 90.0), "a": env, "f": [lo, hi]},
                                              {"mode": "person", "f": [lo, hi]}]))
    p["events"] = [{"f": wins[0][1], "kind": "glow", "strength": 0.6}]
    return p


def clones(l1, work, ask, sense):
    """Ghost copies of the person slide out of him to either side and hold: many of him, one voice."""
    wins, p = _ready(l1, sense, ask, default_len=30)
    if wins is None:
        return p
    spread = num(ask, "spread", 0.26)
    count = max(1, min(int(num(ask, "count", 2)), 3))
    for sh, lo, hi in wins:
        s0 = sh["f"][0]
        n = sh["f"][1] - s0
        passes = []
        if colour(ask, "field"):
            p["items"].append(_plate(l1, p["items"], lo, hi, colour(ask, "field")))
        for j in range(1, count + 1):
            for side in (-1, 1):
                dx = [0.0] * n
                for i, v in enumerate(decel(min(hi - lo, 9), 0.0, side * spread * j, 3.5)):
                    dx[lo - s0 + i] = round(v, 5)
                for i in range(lo - s0 + min(hi - lo, 9), n):
                    dx[i] = side * spread * j
                passes.append({"mode": "person", "dx": dx, "a": round(0.62 / j, 3), "f": [lo, hi]})
        passes.append({"mode": "person", "f": [lo, hi]})
        p["items"].append(_shot_with(l1, sh, passes))
    p["events"] = [{"f": wins[0][1], "kind": "clones", "strength": 0.7}]
    return p


def sticker(l1, work, ask, sense):
    """The person cut out like a sticker, a white border round him, on a picture or a plate."""
    wins, p = _ready(l1, sense, ask)
    if wins is None:
        return p
    pics = pictures(l1, ask)
    for i, (sh, lo, hi) in enumerate(wins):
        if pics and not colour(ask, "field"):
            p["items"].append(_picture_card(l1, p["items"], pics[i % len(pics)], lo, hi))
        else:
            p["items"].append(_plate(l1, p["items"], lo, hi, _field(l1, ask, i)))
        p["items"].append(_shot_with(l1, sh, [{"mode": "outline", "c": colour(ask, "c", "#FFFFFF"), "w": min(max(num(ask, "w", 16), 6.0), 30.0), "f": [lo, hi]},
                                              {"mode": "person", "f": [lo, hi]}]))
    p["events"] = [{"f": wins[0][1], "kind": "sticker", "strength": 0.7}]
    return p


def keep_colour(l1, work, ask, sense):
    """The room in grey, the person in colour: a grey copy of the take on its own clock behind him."""
    wins, p = _ready(l1, sense, ask)
    if wins is None:
        return p
    for sh, lo, hi in wins:
        part = cut(sh, lo, hi)
        card = _behind(l1, p["items"], lo, hi, pic="main", src=part["src"], treat={"mode": "mono"})
        if isinstance(part.get("crop"), list) and part["crop"]:
            card["crop"] = part["crop"]
        for k in ("grade", "look", "window"):
            if k in sh:
                card[k] = sh[k]
        p["items"].append(card)
        p["items"].append(_shot_with(l1, sh, []))
    p["events"] = [{"f": wins[0][1], "kind": "colour", "strength": 0.5}]
    return p


TAKE = ("keep_hue",)
DEVICES = {
    "bg_swap": {"plan": bg_swap, "needs": ("take", "matte"), "fallback": ("sticker", "keep_hue"),
                "card": "f pics[]. The speaker stays, the world behind him becomes the picture he names. For a hook, 'imagine', "
                        "being transported, the place he talks about"},
    "plate_strobe": {"plan": plate_strobe, "needs": ("take", "matte"), "fallback": ("bg_swap",),
                     "card": "f pics[] hold(2-4). The world behind a locked speaker changes every few frames. For a list, a drop, "
                             "'everything changed', energy rising"},
    "silhouette": {"plan": silhouette, "needs": ("take", "matte"), "fallback": ("keep_hue",),
                   "card": "f c(field) ink glow?. The speaker as a flat silhouette on a field of colour. For a hook, mystery, 'who', "
                           "an anonymous figure, a drop"},
    "poster": {"plan": poster, "needs": ("take", "matte"), "fallback": ("silhouette",),
               "card": "f c(field) ink light. The speaker in two inks with a paper outline on a field: a screen print. For a name, "
                       "a legend, the verdict on a person"},
    "glow": {"plan": glow, "needs": ("take", "matte"), "fallback": ("leak",),
             "card": "f c w(px 14-90) amount(0-1). A halo swells round the speaker and settles. For a realisation, pride, the payoff landing on him"},
    "clones": {"plan": clones, "needs": ("take", "matte"), "fallback": ("glow",),
               "card": "f count(1-3) spread(0.1-0.4 of the frame) field?. Ghost copies slide out of him. For 'all of us', doubt, many versions, scale"},
    "sticker": {"plan": sticker, "needs": ("take", "matte"), "fallback": ("bg_swap",),
                "card": "f pics[]|field c w(px 6-30). The speaker cut out like a sticker, white border, on a picture or colour. Playful, a "
                        "meme beat, a punchline"},
    "keep_colour": {"plan": keep_colour, "needs": ("take", "matte"), "fallback": TAKE,
                    "card": "f. The room drained to grey, the speaker in colour. For focus on him, the one who mattered, isolation"},
}
