"""Frame punctuation: the hits an editor lays on single frames and short spans, as plain flash, shot and card items.

    neg         1-2 inverted frames on a cut (a polarity flip), or an accelerating run of them into a drop
    neg_hold    a held negative over a span: erasure, loss, the turn
    neg_split   half the frame or a band of it negative (split polarity)
    neg_world   the world around the speaker goes negative while he stays (the take's matte)
    white       one white frame with a fast decay, full frame or clipped to the card on screen
    black       a dip to black on a phrase boundary
    tint        the shot before a cut repeats in a duotone (an echo), cyan or magenta
    variant     the picture re-rendered (thermal, threshold, halftone, xray, blueprint, ...) for a few frames or flips
    spotlight   a vignette that pushes in round the face or the centre over a shot
    blur_in     a picture that opens defocused and is sharp by its fourth frame
    word_blend  the display words of a span cut through the picture (difference) or printed on it (multiply)

Strobes are built to stay under three full-frame light changes a second: a run's flips are at least ten frames apart
and a single flip moves to the nearest frame with room (``Sense.spaced``); with no room it lands on a band of the
frame instead. Nothing here makes a sound: every landing is an event the sound workflow may answer.
"""
import os

from ..alien import sense as AS
from ..plan import MODES, PICTURE_ONLY, TREATS

DUO = {"cyan": "#22E4FF", "magenta": "#FF2D95", "yellow": "#FFD21F", "red": "#FF3B2F", "blue": "#2F6BFF", "green": "#38F28A"}
VARIANTS = ("thermal", "threshold", "halftone", "xray", "blueprint", "posterise", "solarise", "night_vision", "line",
            "mosaic", "recolour", "kaleidoscope", "pixel_stretch", "burn", "complement", "mono")
GAP = 10                   # frames between two full-frame light changes: under three a second
SEEN_F = 12                # frames a device that needs the speaker must cover on him to be seen at all (0.4 s)
FULL = 0.8                 # a card clipped flash that covers this share of the frame is a full-frame flash


def r4(x):
    return round(float(x), 4)


def span(ask, l1):
    from . import span as S
    return S(ask, l1)


def colour(c, default="cyan"):
    c = str(c or default)
    return DUO.get(c.lower(), c if c.startswith("#") and len(c) == 7 else DUO[default])


def flash(i, a, b, mode, alpha, c=None, rect=None, **kw):
    it = {"id": i, "k": "flash", "f": [int(a), int(b)], "mode": mode, "a": alpha}
    if c:
        it["c"] = c
    if rect:
        it["rect"] = [r4(v) for v in rect]
    it.update(kw)
    return it


def band_rect(part, y=0.5, h=0.16):
    return {"left": [0.0, 0.0, 0.5, 1.0], "right": [0.5, 0.0, 1.0, 1.0], "top": [0.0, 0.0, 1.0, 0.5],
            "bottom": [0.0, 0.5, 1.0, 1.0]}.get(part) or [0.0, max(y - h / 2, 0.0), 1.0, min(y + h / 2, 1.0)]


def card_rect(card):
    x, y, w, h = float(card.get("x", 0.5)), float(card.get("y", 0.5)), float(card.get("w", 0.5)), float(card.get("h", 0.5))
    s = card.get("s", 1)
    s = float(s[-1] if isinstance(s, list) else s)
    x = float(x[-1] if isinstance(x, list) else x)
    y = float(y[-1] if isinstance(y, list) else y)
    return [max(x - w * s / 2, 0.0), max(y - h * s / 2, 0.0), min(x + w * s / 2, 1.0), min(y + h * s / 2, 1.0)]


# --------------------------------------------------------------------- negative

def neg(l1, work, ask, sense):
    a, b = span(ask, l1)
    n = max(1, min(int(ask.get("n") or 1), 2))
    run = max(1, min(int(ask.get("run") or ask.get("count") or 1), 8))
    i = ask["id"]
    items, lum, ev = [], [], []
    if run == 1:
        f = sense.on_cut(a)
        g = sense.spaced(f, GAP)
        if g is None:                       # no room for a full-frame flip here: a band of the frame flips instead
            items.append(flash(f"{i}n0", f, f + n, "neg", 1.0, rect=band_rect("band", 0.5, 0.34)))
            ev.append({"f": f, "kind": "neg_band", "strength": 0.5})
        else:
            items.append(flash(f"{i}n0", g, g + n, "neg", 1.0))
            lum.append(g)
            ev.append({"f": g, "kind": "neg", "strength": 0.8})
    else:
        # an accelerating run into the drop at b (or the ask's end): gaps shrink from 24 frames to the floor
        end = sense.on_cut(b if ask.get("f") and isinstance(ask.get("f"), list) else a + 24 * run)
        gaps = [max(GAP, int(round(24 - (24 - GAP) * k / max(run - 1, 1)))) for k in range(run - 1)]
        fs = [end]
        for gp in reversed(gaps):
            fs.insert(0, fs[0] - gp)
        fs = [f for f in fs if 0 < f < int(l1["dur_f"]) - n]
        for k, f in enumerate(fs):
            g = sense.spaced(f, GAP, reach=3)
            if g is None:
                continue
            items.append(flash(f"{i}n{k}", g, g + n, "neg", 1.0))
            lum.append(g)
            sense.lum.append(g)
            ev.append({"f": g, "kind": "neg", "strength": round(0.5 + 0.5 * (k + 1) / len(fs), 2)})
        sense.lum[:] = [x for x in sense.lum if x not in lum]
    if not items:
        return {"skip": "no frame with room for a flip"}
    return {"items": items, "lum": lum, "events": ev, "log": {"frames": [it["f"][0] for it in items]}}


def neg_hold(l1, work, ask, sense):
    a, b = span(ask, l1)
    b = max(b, a + 6)
    b = min(b, int(l1["dur_f"]))
    pol = sense.negative(a, b)
    if pol and sum(pol) >= 0.5 * len(pol):
        # the frames are already negative (a section printed negative): a second negative would print them positive
        # and the hold would vanish into the section. Its visible counterpart: a band of the picture punches through
        # positive over the hold
        rect = band_rect("band", float(ask.get("y", 0.5)), float(ask.get("h", 0.34)))
        return {"items": [flash(f"{ask['id']}nh", a, b, "neg", 1.0, rect=rect)],
                "events": [{"f": a, "kind": "neg_hold_punch", "strength": 0.6}],
                "log": {"f": [a, b], "counterpart": "positive band through a negative section", "negative_share": round(sum(pol) / len(pol), 2)}}
    a2 = sense.spaced(sense.on_cut(a), GAP)
    if a2 is None:
        return neg_split(l1, work, dict(ask, part="band"), sense)
    b = min(b + (a2 - a), int(l1["dur_f"]))
    return {"items": [flash(f"{ask['id']}nh", a2, b, "neg", 1.0)], "lum": [a2, b],
            "events": [{"f": a2, "kind": "neg_hold", "strength": 0.7}], "log": {"f": [a2, b]}}


def neg_split(l1, work, ask, sense):
    a, b = span(ask, l1)
    b = max(b, a + 4)
    part = ask.get("part") or ("band" if (a // 7) % 2 else "left")
    rect = ask.get("rect") or band_rect(part, float(ask.get("y", 0.5)), float(ask.get("h", 0.16)))
    return {"items": [flash(f"{ask['id']}ns", a, b, "neg", 1.0, rect=rect)],
            "events": [{"f": a, "kind": "neg_split", "strength": 0.5}], "log": {"rect": rect}}


def neg_world(l1, work, ask, sense):
    """The world inverts round the speaker, who stays as he is: over the take, the take negative is drawn behind him and
    he is drawn again through his matte (the runtime's depth sandwich); over any other picture of the span the whole
    picture is the inverted world."""
    a, b = span(ask, l1)
    b = max(b, a + 8)

    def take(sh):
        return sense.is_take(sh) and sh.get("src") and (str(sh.get("pic")) == "main" or str(sh.get("pic")).startswith("own:cut"))
    shots = [sh for sh in sense.shots() if sh["f"][0] < b and sh["f"][1] > a]
    on_him = sum(min(b, sh["f"][1]) - max(a, sh["f"][0]) for sh in shots if take(sh))
    if on_him < SEEN_F:
        # a world negative round a speaker seen for a few frames is not seen at all: the fallback lands instead, and the
        # log says how many frames he was on screen
        return {"skip": f"the speaker is on screen {on_him} of the span's {b - a} frames (needs {SEEN_F})"}
    got = sense.matte_windows([(a,b)]) if hasattr(sense,"matte_windows") else sense.matte()
    if not got:
        return {"skip": "no matte of the take"}
    items = []
    for k, sh in enumerate(shots):
        lo, hi = max(a, sh["f"][0]), min(b, sh["f"][1])
        if hi - lo < 1 or not take(sh):
            continue                        # the device is the world round him: drawn where he is on screen
        piece = AS.slice_shot(sh, lo, hi)
        card = {"id": f"{ask['id']}nw{k}", "k": "card", "f": [lo, hi], "pic": sh["pic"], "src": piece["src"], "x": 0.5, "y": 0.5,
                "w": 1.0, "h": 1.0, "s": 1, "rot": 0, "op": 1, "z": 1, "behind": True, "treat": {"mode": "neg"}}
        if piece.get("crop") is not None:
            card["crop"] = piece["crop"]
        for key in ("look", "grade"):
            if sh.get(key) is not None:
                card[key] = sh[key]
        items.append(card)
    first = min(it["f"][0] for it in items)
    return {"items": items, "assets": {} if "matte" in (l1.get("assets") or {}) else got, "lum": [first],
            "events": [{"f": first, "kind": "neg_world", "strength": 0.7}],
            "log": {"f": [a, b], "pieces": len(items), "frames": on_him}}


# --------------------------------------------------------------------- light

def white(l1, work, ask, sense):
    a, _ = span(ask, l1)
    f = sense.on_cut(a)
    n = max(1, min(int(ask.get("n") or 3), 5))
    alpha = [1.0, 0.55, 0.22, 0.08, 0.0][:n]
    i = ask["id"]
    if ask.get("card") or ask.get("clip"):
        # the pictures' cards only (not the palette's overlays): a card as large as the frame is a full-frame white,
        # spaced and recorded like one
        cards = [c for c in sense.cards_at(f) if c.get("pic") not in (None, "plate")]
        if cards:
            big = max(cards, key=lambda c: float(c.get("w", 0)) * float(c.get("h", 0)))
            r = card_rect(big)
            if (r[2] - r[0]) * (r[3] - r[1]) < FULL:
                return {"items": [flash(f"{i}wc", f, f + n, "fill", alpha, c="#FFFFFF", rect=r)],
                        "events": [{"f": f, "kind": "white_card", "strength": 0.6}], "log": {"card": big["id"]}}
    g = sense.spaced(f, GAP)
    if g is None:
        return {"items": [flash(f"{i}wb", f, f + n, "fill", alpha, c="#FFFFFF", rect=band_rect("band", 0.5, 0.3))],
                "events": [{"f": f, "kind": "white_band", "strength": 0.5}], "log": {"band": True}}
    return {"items": [flash(f"{i}w", g, g + n, "fill", alpha, c="#FFFFFF")], "lum": [g],
            "events": [{"f": g, "kind": "white", "strength": 0.9}], "log": {"f": g}}


def black(l1, work, ask, sense):
    a, _ = span(ask, l1)
    n = max(1, min(int(ask.get("n") or 2), 4))
    f = sense.on_cut(a, reach=3)
    g = sense.spaced(f, GAP)
    if g is None:                           # no room for a full-frame dip: the frame closes to a letterbox for it instead
        return {"items": [flash(f"{ask['id']}k{q}", f, min(f + n + 2, int(l1["dur_f"])), "fill", 1.0, c="#000000", rect=r)
                          for q, r in enumerate(([0.0, 0.0, 1.0, 0.3], [0.0, 0.7, 1.0, 1.0]))],
                "events": [{"f": f, "kind": "black_bars", "strength": 0.4}], "log": {"f": f, "bars": True}}
    # In continuous dialogue a dip can mark the turn with the face still in view.
    # A pause or a transition to another picture can carry the full blackout.
    alpha = 1.0
    if l1.get("workflow_text_layout") and sense.is_take(sense.shot_at(g)) and sense.is_take(sense.shot_at(g-1)):
        said = l1.get("said") or []
        margin = round(.10 * float(l1.get("fps", 30)))
        speaking = any(row[1] - margin <= g < row[2] + margin for row in said)
        if speaking and not (sense.cards_at(g) or sense.cards_at(g-1)):
            alpha = .35
    return {"items": [flash(f"{ask['id']}k", g, min(g + n, int(l1["dur_f"])), "fill", alpha, c="#000000")], "lum": [g],
            "events": [{"f": g, "kind": "black", "strength": 0.6}], "log": {"f": g}}


# --------------------------------------------------------------------- repeat and re-render

def tint(l1, work, ask, sense):
    """The shot before a cut plays its last frames again in a duotone (the editor's echo on an emphasised word)."""
    a, b = span(ask, l1)
    n = max(4, min(int(ask.get("n") or (b - a if b - a > 3 else 8)), 14))
    c = colour(ask.get("c") or ask.get("colour") or ("magenta" if (a // 11) % 2 else "cyan"))
    cut = sense.on_cut(a, reach=8)
    prev = sense.shot_at(cut - 1)
    nxt = sense.shot_at(cut)
    if prev is None or nxt is None or prev is nxt or cut + n >= int(l1["dur_f"]):
        # no cut near: the picture on screen turns duotone for the span
        return {"items": [flash(f"{ask['id']}t", a, a + n, "tint", 1.0, c=c)],
                "events": [{"f": a, "kind": "tint", "strength": 0.5}], "log": {"mode": "flash"}}
    lo = max(prev["f"][0], cut - n)
    echo = AS.slice_shot(prev, lo, cut, new_id=f"{ask['id']}te")
    k = cut - lo
    echo["f"] = [cut, cut + k]
    echo["treat"] = {"mode": "tint", "c": c}
    keep, gone, _ = AS.carve(l1, cut, cut + k)
    return {"items": keep + [echo], "drop": gone, "events": [{"f": cut, "kind": "tint_echo", "strength": 0.6}],
            "log": {"echo_of": prev["id"], "f": echo["f"], "c": c}}


def variant(l1, work, ask, sense):
    a, b = span(ask, l1)
    mode = str(ask.get("mode") or ask.get("as") or "thermal").lower().replace("-", "_").replace(" ", "_")
    mode = SPELLED.get(mode, mode)
    if mode not in MODES:
        mode = "thermal"
    b = max(b, a + 3)
    i = ask["id"]
    if mode in PICTURE_ONLY:
        keep, gone, was = AS.carve(l1, a, b)
        inside = [dict(AS.slice_shot(sh, max(a, sh["f"][0]), min(b, sh["f"][1]), new_id=f"{i}v{k}"), treat={"mode": mode})
                  for k, sh in enumerate(was)]
        return {"items": keep + inside, "drop": gone, "events": [{"f": a, "kind": "variant", "strength": 0.5}],
                "log": {"mode": mode, "as": "treat"}}
    flips = max(1, min(int(ask.get("flips") or 1), 6))
    rect = ask.get("rect")
    items = []
    lv = None
    if mode == "threshold":                 # split each picture at its own middle light (readable, never blobs)
        from .surface import own_light
        lv = own_light(l1, sense, a, b, lo=0.12, hi=0.88)

    def th(f0, f1):
        if lv is None:
            return {}
        return {"th": lv if not isinstance(lv, list) else lv[min(max(f0 - a, 0), len(lv) - 1):max(min(f1 - a, len(lv)), 1)]}
    if flips == 1:
        items.append(flash(f"{i}v", a, b, mode, 1.0, rect=rect, **({"cell": 22} if mode == "mosaic" else {}), **th(a, b)))
    else:                                  # the picture and its re-render alternate, two frames each way
        f = a
        for k in range(flips):
            if f + 2 > int(l1["dur_f"]):
                break
            items.append(flash(f"{i}v{k}", f, f + 2, mode, 1.0, rect=rect, **th(f, f + 2)))
            f += GAP if k % 2 else 4
    return {"items": items, "events": [{"f": a, "kind": "variant", "strength": 0.5}], "log": {"mode": mode, "flips": len(items)}}


# --------------------------------------------------------------------- the picture's own focus and light

def _vignette_png(work, H=1920, W=1080):
    """A dark ring with a clear centre, 1.6 frames wide, drawn once into the work: the spotlight's card."""
    p = os.path.join(work, "palette", "spotlight.png")
    if os.path.exists(p):
        return p
    import numpy as np
    from PIL import Image
    os.makedirs(os.path.dirname(p), exist_ok=True)
    w, h = int(W * 0.8), int(H * 0.8)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    al = np.clip((r - 0.28) / 0.55, 0, 1) ** 1.6 * 235
    Image.fromarray(np.dstack([np.zeros((h, w, 3), np.uint8), al.astype(np.uint8)]), "RGBA").save(p)
    return p


def spotlight(l1, work, ask, sense):
    a, b = span(ask, l1)
    sh = sense.shot_at(a)
    if sh:
        b = min(max(b, a + 12), sh["f"][1]) if b - a < 12 else b
    n = b - a
    cx, cy = 0.5, 0.42
    if sh and sh.get("pic") not in (None, "plate"):
        t = AS.src_time(sh, a, sense.fps) if sh.get("src") else 0.0
        fs = sense.faces(sh["pic"], t)
        if fs:
            pw, ph = (l1["assets"].get(sh["pic"]) or {}).get("w") or 0, (l1["assets"].get(sh["pic"]) or {}).get("h") or 0
            big = max(fs, key=lambda q: (q[2] - q[0]) * (q[3] - q[1]))
            fx, fy = (big[0] + big[2]) / 2, (big[1] + big[3]) / 2
            try:
                info = AS.probe(sense.path_of(sh["pic"]))
                pw, ph = pw or info["w"], ph or info["h"]
                W, H = sense.size
                crop = AS.crop_at(sh, a, pw, ph, W, H)
                cx = (fx - crop[0]) / max(crop[2] - crop[0], 1e-6)
                cy = (fy - crop[1]) / max(crop[3] - crop[1], 1e-6)
                cx, cy = min(max(cx, 0.2), 0.8), min(max(cy, 0.2), 0.75)
            except Exception:      # noqa: BLE001 - the centre of the frame
                cx, cy = 0.5, 0.42
    png = _vignette_png(work)
    aid = "palette_spotlight"
    amt = min(max(float(ask.get("amount", 0.9)), 0.3), 1.0)
    from .finish import ease
    op = [r4(amt * ease(min(q / max(n * 0.35, 1), 1.0))) for q in range(n)]
    s = [r4(1.45 - 0.45 * ease(min(q / max(n * 0.6, 1), 1.0))) for q in range(n)]
    card = {"id": f"{ask['id']}sp", "k": "card", "f": [a, b], "pic": aid, "x": r4(cx), "y": r4(cy), "w": 1.6, "h": 1.6,
            "s": s, "rot": 0, "op": op, "z": 3}
    return {"items": [card], "assets": {aid: {"kind": "image", "path": png}},
            "events": [{"f": a, "kind": "spotlight", "strength": 0.3}], "log": {"centre": [r4(cx), r4(cy)]}}


def blur_in(l1, work, ask, sense):
    a, _ = span(ask, l1)
    c = sense.on_cut(a, reach=12)
    sh = sense.shot_at(c)
    if not sh or sh["f"][0] != c or sh.get("pic") in (None, "plate"):
        sh = next((s for s in sense.shots() if s["f"][0] >= a and s.get("pic") not in (None, "plate")), None)
    if not sh:
        return {"skip": "no picture starts here"}
    n = sh["f"][1] - sh["f"][0]
    if n < 5:
        return {"skip": "the shot is too short to open soft"}
    curve = [14.0, 8.0, 3.5, 1.0][:n]
    old = sh.get("blur")
    if isinstance(old, list) and len(old) == n:
        blur = [max(float(x), y) for x, y in zip(old, curve + [0.0] * (n - len(curve)))]
    else:
        blur = curve + [float(old or 0.0)] * (n - len(curve))
    return {"items": [dict(sh, blur=blur)], "events": [{"f": sh["f"][0], "kind": "blur_in", "strength": 0.25}],
            "log": {"shot": sh["id"]}}


def word_blend(l1, work, ask, sense):
    a, b = span(ask, l1)
    mode = next((m for m in (ask.get("blend"), ask.get("as")) if m in ("difference", "multiply")), "difference")
    ws = [w for w in sense.words_in(a, b) if w.get("role") in ("big", "stat") and not w.get("behind")]
    if not ws:
        from .kit import accent_cue
        cue = accent_cue(l1, a, b, ask.get("t"))
        if cue is None:
            return {"skip": "no matching lettering in the span"}
        return {"items": [cue], "events": [{"f": max(a, cue["f"][0]), "kind": "word_blend", "strength": .4}],
                "log": {"cue": cue["id"], "treatment": "lift"}}
    out = [dict({k: v for k, v in w.items() if not (mode == "difference" and k == "stroke")}, blend=mode,
                ink="#FFFFFF" if mode == "difference" else w.get("ink")) for w in ws]
    return {"items": out, "events": [{"f": out[0]["f"][0], "kind": "word_blend", "strength": 0.4}],
            "log": {"words": [w["id"] for w in out], "blend": mode}}


SPELLED = {"x_ray": "xray", "posterize": "posterise", "solarize": "solarise", "pixelate": "mosaic", "pixel": "mosaic",
           "pixelated": "mosaic", "nightvision": "night_vision", "night": "night_vision", "schematic": "blueprint",
           "recolor": "recolour", "glitch": "pixel_stretch", "datamosh": "pixel_stretch", "smear": "pixel_stretch",
           "kaleido": "kaleidoscope", "mirror": "kaleidoscope", "sketch": "line", "lines": "line", "outline": "line",
           "edges": "line", "infrared": "thermal", "heat": "thermal", "heatmap": "thermal", "dots": "halftone",
           "print": "halftone", "poster": "posterise", "invert_colour": "complement", "complementary": "complement"}

DEVICES = {
    "neg": {"plan": neg, "layer": "look", "fallback": ("neg_split",), "ranges": {"n": (1, 2, 1, "frames a flip"), "run": (1, 8, 1, "flips")},
            "card": "neg at n(1-2) run(1-8). A polarity flip: 1-2 inverted frames on the cut; run>1 accelerates into the drop at the ask's end. Hook, before a name, a drop, the close"},
    "neg_hold": {"plan": neg_hold, "layer": "surface", "fallback": ("neg_split",),
                 "card": "neg_hold f[a,b]. The picture held in negative over a span. Erasure, loss, 'as if it never existed', the turn"},
    "neg_split": {"plan": neg_split, "layer": "look", "fallback": ("white",),
                  "ranges": {"y": (0.05, 0.95, 0.5, "the band's centre, of the frame's height"), "h": (0.05, 0.6, 0.16, "the band's height, of the frame's")},
                  "card": "neg_split f[a,b] part(left|right|top|bottom|band) y h. Half the frame, or a band of it, negative. Two sides, a split, a tear"},
    "neg_world": {"plan": neg_world, "layer": "look", "needs": ("take", "matte"), "fallback": ("neg_split",),
                  "card": "neg_world f[a,b]. The world round the speaker inverts while he stays (his matte). Isolation, 'everyone else', a verdict on the world. Needs the speaker on screen for at least half a second of the span"},
    "white": {"plan": white, "layer": "look", "fallback": ("neg_split",), "ranges": {"n": (1, 5, 3, "frames")},
              "card": "white at n(1-5) card(bool). One white frame with a fast decay, full frame or clipped to the card on screen. Payoff join, a name reveal, a drop"},
    "black": {"plan": black, "layer": "look", "fallback": ("white",), "ranges": {"n": (1, 4, 2, "frames")},
              "card": "black at n(1-4). A dip to black on a phrase boundary. Before the payoff, between two thoughts, the close"},
    "tint": {"plan": tint, "layer": "surface", "fallback": ("variant",), "ranges": {"n": (4, 14, 8, "frames of the echo")},
             "card": "tint at n c(cyan|magenta|#hex). The shot before the cut plays its last frames again in a duotone. Emphasis on a repeated word, a chapter"},
    "variant": {"plan": variant, "layer": "surface", "fallback": ("tint", "white"), "ranges": {"flips": (1, 6, 1, "flips")},
                "card": "variant f[a,b] mode(thermal|threshold|halftone|xray|blueprint|posterise|solarise|night_vision|line|mosaic|recolour|kaleidoscope|pixel_stretch) flips rect. The picture re-rendered for a few frames or flips. Military, tech, science, surveillance, the hook strobe"},
    "spotlight": {"plan": spotlight, "layer": "surface", "needs": ("faces",), "fallback": ("blur_in",),
                  "ranges": {"amount": (0.3, 1.0, 0.9, "how dark the ring falls")},
                  "card": "spotlight f[a,b] amount. A vignette pushes in round the face over the shot. Confession, focus, a single person against everything"},
    "blur_in": {"plan": blur_in, "layer": "surface", "fallback": ("white",),
                "card": "blur_in at. The new picture opens defocused and is sharp by its fourth frame. Section openers, after a flash, 'focus', 'realised'"},
    "word_blend": {"plan": word_blend, "layer": "type", "needs": ("words",), "fallback": ("settle",),
                   "card": "word_blend f[a,b] blend(difference|multiply). The display words cut through the picture (difference) or print on it (multiply). A word that must feel part of the picture"},
}
ALIASES = {"negative": "neg", "polarity": "neg", "invert": "neg", "flip": "neg", "inverse": "neg", "strobe": "neg",
           "flash": "white", "white_flash": "white", "flash_frame": "white", "black_dip": "black", "dip": "black",
           "cut_to_black": "black", "blackout": "black", "duotone": "tint", "tint_echo": "tint", "echo": "tint",
           "freeze": "tint", "freeze_frame": "tint", "repeat": "tint", "vignette": "spotlight", "blur": "blur_in",
           "defocus": "blur_in", "rack_focus": "blur_in", "whip": "blur_in", "swish": "blur_in", "shake": "blur_in",
           "difference": "word_blend", "multiply": "word_blend", "split_polarity": "neg_split", "band": "neg_split",
           "negative_hold": "neg_hold", "hold_negative": "neg_hold", "world_negative": "neg_world", "punct": "white",
           # layouts and moves a model may name as a device: the nearest device that draws something like them
           "split": "neg_split", "split_screen": "neg_split", "window": "spotlight", "bleed": "spotlight",
           "zoom": "spotlight", "zoom_in": "spotlight", "punch_in": "spotlight", "push_in": "spotlight",
           "crash_zoom": "spotlight", "push": "spotlight", "focus": "spotlight", "type": "word_blend", "text": "word_blend",
           "word": "word_blend", "title": "word_blend", "kinetic_type": "word_blend", "collage": "answer", "grid": "answer",
           "prints": "answer", "montage": "answer", "burst": "answer", "list": "answer", "match_cut": "rhyme",
           "shape_match": "rhyme"}
# every re-render the variant card names (the engine's own modes), in every spelling a model writes, is the variant
# device with the mode carried in "as"
ALIASES.update({m: "variant" for m in tuple(MODES) + VARIANTS + tuple(SPELLED)
                if m not in ("neg", "tint", "fill", "pool", "mono") and m not in ALIASES})
_ = TREATS
