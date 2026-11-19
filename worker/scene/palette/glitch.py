"""Glitch: the picture breaks. RGB split, slice tear and the smeared frame at a cut (the references: MM's RGB split
on 0.71 shots in 10 s, Ackman's smeared frame, ref 37's slice glitches). Frame-level flashes, so they break
whatever is on the frame: the take, a card, a still. No device changes the frame's light: a glitch is a picture
change, never a white flash, so a run of them stays far under three luminance flashes a second.

Signal places them: the strength follows the voice's level over the moment when the film has a voice
(``follow: "voice"``), else lands hard and decays."""
from ._util import arg, base_at, frames, hit, shots, uid

LAYER = "surface"


def _amounts(sense, a, b, follow, peak):
    n = b - a
    if follow == "voice":
        lev = sense.level()[a:b]
        if len(lev) == n and float(lev.max()) > 0.05:
            m = float(lev.max())
            return [round(max(0.25, min(1.6, float(v) / m)) * peak, 4) for v in lev]
    if follow == "flat":
        return [peak] * n
    return [round(v, 4) for v in hit(n, peak, 0.2)]


def rgb_split(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=6)
    px = float(arg(ask, "px", 18.0))
    amt = _amounts(sense, a, b, arg(ask, "follow", "hit"), 1.0)
    it = {"id": uid(l1, "pd_rgb"), "k": "flash", "f": [a, b], "mode": "rgb_split", "px": px, "angle": float(arg(ask, "angle", 0.0)),
          "amount": amt, "z": 40}
    return {"items": [it], "log": {"f": [a, b], "px": px},
            "events": [{"f": a, "kind": "glitch", "strength": round(min(px / 30.0, 1.0), 2)}]}


def slice_(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=4)
    shift = float(arg(ask, "shift", 110.0))
    items = [{"id": uid(l1, "pd_slc"), "k": "flash", "f": [a, b], "mode": "slice", "shift": shift, "band": float(arg(ask, "band", 52.0)),
              "density": float(arg(ask, "density", 0.38)), "rate": int(arg(ask, "rate", 1)), "amount": hit(b - a, 1.0, 0.35, 2.0), "z": 41}]
    if arg(ask, "split", True):
        items.append({"id": uid(l1, "pd_slr"), "k": "flash", "f": [a, b], "mode": "rgb_split", "px": shift * 0.12, "angle": 0.0,
                      "amount": hit(b - a, 1.0, 0.3), "z": 42})
    return {"items": items, "log": {"f": [a, b], "shift": shift},
            "events": [{"f": a, "kind": "glitch", "strength": round(min(shift / 160.0, 1.0), 2)}]}


def smear(l1, work, ask, sense):
    """One smeared frame carrying one picture into the next: the cut nearest the asked frame gets a torn,
    split frame on each side of it."""
    a, _ = frames(l1, ask, default_len=2)
    cuts = [s["f"][0] for s in shots(l1) if s["f"][0] > 0]
    if not cuts:
        return {"skip": "the film has no cut to smear"}
    c = min(cuts, key=lambda x: abs(x - a))
    lo, hi = max(c - 1, 0), min(c + 1, int(l1["dur_f"]))
    items = [{"id": uid(l1, "pd_smr"), "k": "flash", "f": [lo, hi], "mode": "slice", "shift": 220.0, "band": 26.0, "density": 0.72,
              "rate": 1, "z": 43},
             {"id": uid(l1, "pd_sms"), "k": "flash", "f": [lo, hi], "mode": "rgb_split", "px": 30.0, "angle": 0.0, "z": 44}]
    return {"items": items, "log": {"cut": c}, "events": [{"f": c, "kind": "smear", "strength": 0.8}]}


def glitch(l1, work, ask, sense):
    """The full break: a tear for its first frames, the channels apart for all of it, both following the voice."""
    a, b = frames(l1, ask, default_len=8)
    t = min(a + max((b - a) // 2, 2), b)
    p1 = slice_(l1, work, dict(ask, f=[a, t], split=False), sense)
    p2 = rgb_split(l1, work, dict(ask, f=[a, b], follow=arg(ask, "follow", "voice"), px=arg(ask, "px", 24.0)), sense)
    items = p1["items"] + [dict(p2["items"][0], id=uid(l1, "pd_glr"))]
    return {"items": items, "log": {"f": [a, b]}, "events": [{"f": a, "kind": "glitch", "strength": 1.0}]}


def tear(l1, work, ask, sense):
    """A band tear across a stretch: every half second or so a few frames of torn bands, the rest clean."""
    a, b = frames(l1, ask, default_len=45, min_len=8)
    every = max(int(arg(ask, "every", 14)), 6)
    items, evs = [], []
    import random
    r = random.Random(f"{l1.get('seed')}:{ask.get('id')}")
    f = a
    while f < b:
        n = min(r.choice((2, 3, 3, 4)), b - f)
        items.append({"id": uid(dict(l1, items=(l1.get("items") or []) + items), "pd_tr"), "k": "flash", "f": [f, f + n], "mode": "slice",
                      "shift": float(r.choice((70, 120, 180))), "band": float(r.choice((24, 40, 70))), "density": 0.3 + 0.3 * r.random(),
                      "rate": 1, "z": 41})
        evs.append({"f": f, "kind": "glitch", "strength": 0.5})
        f += every + r.randint(-3, 4)
    return {"items": items, "log": {"f": [a, b], "tears": len(items)}, "events": evs}


def on_picture(l1, work, ask, sense):
    """The split drawn into one picture only (its own treat), so the type and the cards over it stay whole."""
    a, b = frames(l1, ask, default_len=8)
    sh = base_at(l1, a)
    if sh is None or sh.get("treat"):
        return {"skip": "no untreated base picture there"}
    lo, hi = sh["f"]
    n = hi - lo
    amt = [0.0] * n
    for i, v in enumerate(hit(b - a, 1.0, 0.25)):
        if 0 <= a - lo + i < n:
            amt[a - lo + i] = round(v, 4)
    it = dict(sh, treat={"mode": "rgb_split", "px": float(arg(ask, "px", 18.0)), "angle": float(arg(ask, "angle", 0.0))}, amount=amt)
    return {"items": [it], "log": {"shot": sh["id"]}, "events": [{"f": a, "kind": "glitch", "strength": 0.6}]}


DEVICES = {
    "rgb_split": {"plan": rgb_split, "needs": (), "fallback": (),
                  "card": "f|at px(8-40) angle follow(hit|voice|flat). The channels pulled apart over the frame, landing hard and "
                          "decaying or riding the voice. For tech, screens, a hack, 'broke', 'error', a hook that hits"},
    "slice": {"plan": slice_, "needs": (), "fallback": ("rgb_split",),
              "card": "f|at shift(px) band(px) density split. Bands of the frame torn sideways for 2-6 frames. For a crash, a break, "
                      "a lie exposed, a section joint"},
    "smear": {"plan": smear, "needs": (), "fallback": ("slice",),
              "card": "at. The cut nearest the moment gets one torn, split frame each side. For a jump in time or place, energy rising"},
    "glitch": {"plan": glitch, "needs": (), "fallback": ("rgb_split",),
               "card": "f|at px follow. Tear then split, following the voice: the full break. For 'hacked', 'collapsed', 'it all broke'"},
    "tear": {"plan": tear, "needs": (), "fallback": ("slice",),
             "card": "f(a section) every(frames). A band tear every half second through a stretch. For a tense, unstable passage"},
    "split_picture": {"plan": on_picture, "needs": (), "fallback": ("rgb_split",),
                      "card": "at. The split inside one picture only, the type over it whole. For a screen or a photo that glitches"},
}
