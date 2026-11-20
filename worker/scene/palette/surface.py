"""Surfaces by section, beyond mono, gate and CRT: the whole frame re-printed for a stretch of the film. A tape
rewinding through the past, a projector's gate, a board of lamps, a photocopy, a page, a thermal scope. Each is a
stack of frame-level flashes (the engine's surfaces: vhs, projector, led, xerox, paper, thermal) and, for the tape
and the scope, a few words of on-screen display. The type of the film stays over them, whole.

The display's words stay out of the watermark's corner (``_util.KEEPOUT``) and never reach the end card: they
live inside the asked section only."""
import numpy as np

from ._util import KEEPOUT, arg, colour, frames, num, uid

LAYER = "surface"
OSD = {"osd": {"font": "BebasNeue-Regular.ttf", "px": 46, "track": 70, "shadow": {"a": 0.55, "blur": 0.004}}}


def _flash(l1, a, b, mode, z=10, **kw):
    return dict({"id": uid(l1, f"pd_{mode[:3]}"), "k": "flash", "f": [a, b], "mode": mode, "z": z}, **kw)


def _word(l1, items, a, b, t, x, y, ink="#F2F2F2", **kw):
    if KEEPOUT[0] <= x <= KEEPOUT[2] and KEEPOUT[1] <= y <= KEEPOUT[3]:
        x = 0.075                                       # the watermark's corner is never written on: the word moves left
    return dict({"id": uid(dict(l1, items=(l1.get("items") or []) + items), "pd_osd"), "k": "word", "f": [a, b], "t": t, "v": "osd",
                 "x": x, "y": y, "ink": ink, "z": 60}, **kw)


def _blink(n, on=16, off=10):
    """A display's blink: on, off, on, never faster than twice a second."""
    return [1.0 if (i % (on + off)) < on else 0.0 for i in range(n)]


def rew(l1, work, ask, sense):
    """The past played back on tape: a tracking band rolling down, line jitter, bleeding chroma, and the deck's
    own display (REW, then PLAY when the section settles)."""
    a, b = frames(l1, ask, default_len=60, min_len=12)
    n = b - a
    word = str(arg(ask, "label", "REW <<"))
    items = [_flash(l1, a, b, "vhs", track=num(ask, "track", 0.15), roll=num(ask, "roll", 0.45), amount=1.0)]
    items.append(_word(l1, items, a, b, word, 0.075, 0.085, op=_blink(n)))
    stamp = str(arg(ask, "stamp", "")).strip()
    if stamp:
        items.append(_word(l1, items, a, b, stamp, 0.075, 0.9))
    return {"items": items, "voices": OSD, "log": {"f": [a, b]}, "events": [{"f": a, "kind": "surface", "strength": 0.5}]}


def vhs(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=60, min_len=8)
    items = [_flash(l1, a, b, "vhs", track=num(ask, "track", 0.75), roll=num(ask, "roll", 0.25), amount=1.0)]
    if arg(ask, "label", "PLAY >"):
        items.append(_word(l1, items, a, b, str(arg(ask, "label", "PLAY >")), 0.075, 0.085))
    return {"items": items, "voices": OSD, "log": {"f": [a, b]}, "events": [{"f": a, "kind": "surface", "strength": 0.4}]}


def projector(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=60, min_len=8)
    items = [_flash(l1, a, b, "projector", flicker=num(ask, "flicker", 0.05), weave=num(ask, "weave", 1.6), rate=2)]
    return {"items": items, "log": {"f": [a, b]}, "events": [{"f": a, "kind": "surface", "strength": 0.3}]}


def led(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=45, min_len=6)
    items = [_flash(l1, a, b, "led", cell=num(ask, "cell", 16), gap=num(ask, "gap", 0.18), levels=int(num(ask, "levels", 7)))]
    return {"items": items, "log": {"f": [a, b]}, "events": [{"f": a, "kind": "surface", "strength": 0.4}]}


def _middle_light(l1, sense, a, b):
    """The light in the middle of the section's pictures (their median), where a hard tone should split them."""
    from ..plan import AR
    from ._util import frac_crop, over
    W, H = (l1.get("frame") or [1080, 1920])[:2]
    vals = []
    for sh in over(l1, a, b):
        asset = (l1.get("assets") or {}).get(sh.get("pic"))
        if not asset:
            continue
        f = (max(a, sh["f"][0]) + min(b, sh["f"][1])) // 2
        crop = AR(sh.get("crop"), f, sh["f"][0]) or (frac_crop(asset["w"], asset["h"], W, H) if asset.get("w") else None)
        src = sh.get("src") or [0, 0]
        vals.append(sense.luma(sh["pic"], float(src[0]), crop))
    return float(np.median(vals)) if vals else 0.45


def xerox(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=45, min_len=6)
    th = arg(ask, "th")
    th = float(th) if th is not None else min(max(_middle_light(l1, sense, a, b) * 0.8, 0.1), 0.7)
    items = [_flash(l1, a, b, "xerox", th=round(th, 4), toner=1.0, rate=2, **({"c": colour(ask, "c")} if colour(ask, "c") else {}))]
    return {"items": items, "log": {"f": [a, b]}, "events": [{"f": a, "kind": "surface", "strength": 0.4}]}


def paper(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=45, min_len=6)
    items = [_flash(l1, a, b, "paper", ink=num(ask, "ink", 0.9), **({"c": colour(ask, "c")} if colour(ask, "c") else {}))]
    return {"items": items, "log": {"f": [a, b]}, "events": [{"f": a, "kind": "surface", "strength": 0.3}]}


def thermal_hud(l1, work, ask, sense):
    """A thermal scope: the frame in the iron palette, corner brackets, a reticle and the scope's read-outs."""
    a, b = frames(l1, ask, default_len=45, min_len=8)
    n = b - a
    items = [_flash(l1, a, b, "thermal")]
    W, H = (l1.get("frame") or [1080, 1920])[:2]
    thin, arm = 0.004, 0.06
    for i, (x, y) in enumerate(((0.12, 0.3), (0.88, 0.3), (0.12, 0.72), (0.88, 0.72))):      # brackets: two thin plates a corner
        sx = 1 if x < 0.5 else -1
        sy = 1 if y < 0.5 else -1
        for j, (w, h, dx, dy) in enumerate(((arm, thin * W / H, arm / 2, 0), (thin, arm * W / H, 0, arm * W / H / 2))):
            items.append({"id": uid(dict(l1, items=(l1.get("items") or []) + items), "pd_hb"), "k": "card", "f": [a, b], "pic": "plate",
                          "plate": {"c0": "#F4F4F4", "c1": "#F4F4F4", "grain": 0}, "x": x + sx * dx, "y": y + sy * dy, "w": w, "h": h, "z": 55})
    items.append(_word(l1, items, a, b, str(arg(ask, "label", "TGT 01")), 0.075, 0.24, ink="#F4F4F4", op=_blink(n, 20, 8)))
    items.append(_word(l1, items, a, b, str(arg(ask, "read", "+36.6 C")), 0.075, 0.82, ink="#F4F4F4"))
    return {"items": items, "voices": OSD, "log": {"f": [a, b]}, "events": [{"f": a, "kind": "surface", "strength": 0.5}]}


DEVICES = {
    "rew": {"plan": rew, "needs": (), "fallback": ("vhs",),
            "card": "f(a section) label stamp. Played back on tape, rewinding: tracking band, line jitter, chroma bleed, a REW display. "
                    "For going back in time, 'back in 1998', a memory replayed"},
    "vhs": {"plan": vhs, "needs": (), "fallback": ("projector",),
            "card": "f label. A tape playing: home video, the 80s and 90s, an archive clip, a confession"},
    "projector": {"plan": projector, "needs": (), "fallback": (),
                  "card": "f flicker weave. A projector's gate: the lamp breathes, the frame weaves, dust. For old film, history, origins"},
    "led": {"plan": led, "needs": (), "fallback": ("projector",),
            "card": "f cell gap. The frame on a board of lamps. For a stadium, a scoreboard, fame, a billboard, Times Square"},
    "xerox": {"plan": xerox, "needs": (), "fallback": ("paper",),
              "card": "f th?(0-1, measured when absent) c?(#hex toner). A photocopy: hard tone, toner noise. For a document, a leak, a dossier, punk energy, a list"},
    "paper": {"plan": paper, "needs": (), "fallback": ("projector",),
              "card": "f ink(0-1 how much becomes ink) c?(#hex paper). Printed on a page. For a newspaper, a book, a letter, a quote in print"},
    "thermal_hud": {"plan": thermal_hud, "needs": (), "fallback": ("projector",),
                    "card": "f label read. A thermal scope with brackets and read-outs. For military, surveillance, a target, a hunt"},
}
