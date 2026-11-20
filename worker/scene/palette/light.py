"""Light on the frame, drawn by the engine, no asset: a leak round the gate, a film burning through, a prismatic
flare off the brightest light. Screened over the frame under the type. A burn whitens the frame once, as a cut
to white would: one luminance event, never a strobe."""
from ..plan import AR
from ._util import arg, base_at, envelope, frac_crop, frames, uid

LAYER = "surface"


def leak(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=36, min_len=10)
    n = b - a
    amt = float(arg(ask, "amount", 0.85))
    it = {"id": uid(l1, "pd_lk"), "k": "flash", "f": [a, b], "mode": "leak", "speed": float(arg(ask, "speed", 1.0)),
          "amount": [round(v, 4) for v in envelope(n, max(n // 5, 3), max(n // 3, 4), amt)], "z": 30}
    if arg(ask, "c"):
        it["c"] = arg(ask, "c")
    return {"items": [it], "log": {"f": [a, b]}, "events": [{"f": a + n // 5, "kind": "light", "strength": round(amt * 0.6, 2)}]}


def filmburn(l1, work, ask, sense):
    """The frame burns through from a corner over the moment, ending white-hot: an exit, a close, a memory lost."""
    a, b = frames(l1, ask, default_len=14, min_len=6)
    n = b - a
    o = arg(ask, "origin") or [0.12, 0.88]
    prog = [round(0.05 + 1.05 * (i / max(n - 1, 1)) ** 1.6, 4) for i in range(n)]
    it = {"id": uid(l1, "pd_fb"), "k": "flash", "f": [a, b], "mode": "filmburn", "origin": [float(o[0]), float(o[1])], "amount": prog, "z": 31}
    return {"items": [it], "log": {"f": [a, b]}, "events": [{"f": b - 1, "kind": "burn", "strength": 0.9}]}


def prism(l1, work, ask, sense):
    """An anamorphic streak and a rainbow ghost off the picture's brightest light, swelling and gone."""
    a, b = frames(l1, ask, default_len=24, min_len=8)
    n = b - a
    at = arg(ask, "origin")
    if not (isinstance(at, (list, tuple)) and len(at) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in at)):
        at = None
    if not at:
        sh = base_at(l1, a + n // 2)
        at = [0.72, 0.3]
        if sh and sh.get("pic") in (l1.get("assets") or {}):
            asset = l1["assets"][sh["pic"]]
            W, H = (l1.get("frame") or [1080, 1920])[:2]
            crop = AR(sh.get("crop"), a + n // 2, sh["f"][0]) or (frac_crop(asset["w"], asset["h"], W, H) if asset.get("w") else None)
            at = list(sense.bright(sh["pic"], float((sh.get("src") or [0, 0])[0]), crop))
    it = {"id": uid(l1, "pd_pr"), "k": "flash", "f": [a, b], "mode": "prism", "at": [round(float(at[0]), 4), round(float(at[1]), 4)],
          "amount": [round(v, 4) for v in envelope(n, max(n // 4, 2), max(n // 2, 3), float(arg(ask, "amount", 0.9)))], "z": 32}
    return {"items": [it], "log": {"at": it["at"]}, "events": [{"f": a + n // 4, "kind": "light", "strength": 0.5}]}


DEVICES = {
    "leak": {"plan": leak, "needs": (), "fallback": (),
             "card": "f|at..until c amount. Warm light leaking in round the gate, drifting. For memory, nostalgia, a warm turn, the past"},
    "filmburn": {"plan": filmburn, "needs": (), "fallback": ("leak",),
                 "card": "f(6-20 frames, ends on a cut) origin[x,y]. The frame burns through to white-hot. For an ending, a loss, "
                         "'gone', a chapter that closes"},
    "prism": {"plan": prism, "needs": (), "fallback": ("leak",),
              "card": "f origin[x,y]?. A lens flare streak and rainbow ghost off the brightest light. For a reveal, fame, a stage, a win"},
}
