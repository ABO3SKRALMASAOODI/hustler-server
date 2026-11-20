"""callback: the opening comes back. At the payoff the film's first pictures return (a fast recap, in the order they
were first seen, or a grid of them), and a loop returns the last frames to the first picture the opening held (a
bookend before the brand's end card; a seamless loop only when nothing follows the film).

The opening is read off the scene itself (its first shots); which moment is the payoff is the caller's.

ask {"dev": "callback", "at": [a, z], "from": [a0, z0] (the opening, default its first 4 s), "mode": "burst"|"grid",
     "frames": 4 (a recap picture's length)}
ask {"dev": "loop", "frames": 15, "end_card": true}
"""
from ..alien import sense
from . import layout
from . import picks as K


def opening(l1, work, a0, z0, take=False):
    """The opening's pictures in the order first seen: [(shot, Pic, src start, first crop)], one per picture."""
    out, seen = [], set()
    for sh in K.shots(l1):
        if sh["f"][0] >= z0 or sh["f"][1] <= a0 or sh.get("pic") == "plate":
            continue
        if (K.is_take(sh.get("pic")) and not take) or sh["pic"] in seen:
            continue
        pic = K.pic_of_asset(l1, work, sh["pic"])
        if not pic:
            continue
        seen.add(sh["pic"])
        f = max(sh["f"][0], a0)
        t = sense.src_time(sh, f, float(l1.get("fps") or 30)) if sh.get("src") else 0.0
        out.append((sh, pic, t, K.crops(l1, work, sh)[f - sh["f"][0]]))
    return out


def callback(l1, work, ask, sense_=None, pool=None):
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    a, z = K.span(l1, args, default_s=1.4, least=8)
    fr = args.get("from") or [0, int(4 * fps)]
    a0, z0 = int(fr[0]), int(fr[1])
    if z0 > a:
        z0 = a
    op = opening(l1, work, a0, z0) or opening(l1, work, a0, z0, take=True)
    if not op:
        return {"skip": "the opening holds no picture to return"}
    if str(args.get("mode", "burst")) == "grid" and len(op) >= 2:
        patch = layout.grid(l1, work, dict(args, pics=[p.aid for _, p, _, _ in op], only=True, at=[a, z]), sense_, pool)
        if not patch.get("skip"):
            patch["events"] = [dict(m, what="callback tile") for m in patch.get("events") or []]
            patch.setdefault("log", {})["callback"] = [p.aid for _, p, _, _ in op]
            return patch
    per = int(args.get("frames", 4))
    span_ = z - a
    land_min = 6
    if per * len(op) + land_min > span_:                 # a short payoff: quicker returns, then fewer of them
        per = max((span_ - land_min) // max(len(op), 1), 2)
        op = op[:max((span_ - land_min) // per, 1)]
    elif span_ - per * len(op) > span_ // 2 and len(op) > 1:
        per = max((span_ - span_ // 2) // len(op), 2)
    land = span_ - per * len(op)
    lens = [per] * len(op) + [land]
    seq = [(p, t, c) for _, p, t, c in op] + [(op[0][1], op[0][2], op[0][3])]
    lum = [K.luminance(p, t + 0.02, c) for p, t, c in seq]
    _, gains = K.flash_safe(lum, lens, fps, order=list(range(len(seq))))
    keep, gone, was = K.carve(l1, a, z)
    look = K.inherit(was)
    items, marks, pos = [], [], a
    for i, ((p, t, c), n) in enumerate(zip(seq, lens)):
        it = dict(look, id=f"{args.get('id', 'cb')}.r{i}", k="shot", f=[pos, pos + n], pic=p.aid,
                  crop=[K.r5(K.scale_crop(c, 1.0 / (1.0 + (0.06 if i == len(seq) - 1 else 0.03) * j / max(n - 1, 1)))) for j in range(n)])
        if p.video:
            it["src"] = [round(t, 4), round(t + n / fps, 4)]
        if abs(gains[i] - 1.0) > 0.01:
            it["grade"] = {"lift": 0.0, "gamma": 1.0, "gain": round(gains[i], 3), "sat": 1.0, "contrast": 1.0, "temp": 0.0, "tint": 0.0}
        items.append(it)
        marks.append({"f": pos, "what": "callback", "kind": "cut", "strength": 0.6 if i in (0, len(seq) - 1) else 0.3})
        pos += n
    return {"items": keep + items, "drop": gone, "assets": {}, "events": marks,
            "log": {"returned": [p.aid for p, _, _ in seq[:-1]], "lands_on": seq[-1][0].aid}}


HELD_F = 8                 # a shot this long or longer is a picture the viewer held, not a sliver of a strobe


def held_opening(l1, a0=0, z0=None, least=HELD_F):
    """The first picture of the opening a viewer held: the first shot inside [a0, z0) of ``least`` frames or more that
    shows a picture (a strobe's slivers and plates are passed over), else the opening's longest shot."""
    fps = float(l1.get("fps") or 30)
    z0 = int(z0 if z0 is not None else 4 * fps)
    cand = [s for s in K.shots(l1) if s["f"][0] < z0 and s["f"][1] > a0 and s.get("pic") != "plate"]
    if not cand:
        return None
    held = [s for s in cand if s["f"][1] - s["f"][0] >= least]
    return held[0] if held else max(cand, key=lambda s: s["f"][1] - s["f"][0])


def loop(l1, work, ask, sense_=None, pool=None):
    """The last frames return to the first picture the opening held, in the framing the viewer first saw it. Workflow
    films are followed by the brand's end card (joined after the programme, never drawn here), so by default this is a
    bookend: the held picture comes back on the moment it opened with and the end card follows it. With
    ``end_card: false`` (a film with nothing after it) the frames are the ones just before the opening's own, so a replay
    runs on unbroken. ask: frames (default 15, never over the words: after the last caption word), end_card."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    dur = int(l1["dur_f"])
    first = held_opening(l1)
    if first is None:
        return {"skip": "the film opens on no picture"}
    words_end = max((w["f"][1] for w in l1.get("items") or [] if w.get("k") == "word" and w.get("role") == "cap"), default=0)
    n = int(args.get("frames", 15))
    room = dur - words_end if words_end else n
    n = min(n, room) if room >= 6 else min(n, 8)
    if n < 4 or dur - n <= first["f"][1]:
        return {"skip": "no room after the last word for a loop"}
    end_card = bool(args.get("end_card", l1.get("end_card", True)))
    c0 = K.crops(l1, work, first)[0]
    it = dict(id=f"{args.get('id', 'loop')}.end", k="shot", f=[dur - n, dur], pic=first["pic"], crop=[c0] * n)
    for k in ("look", "grade", "treat"):
        if first.get(k):
            it[k] = first[k]
    held = False
    if first.get("src"):
        t0 = float(first["src"][0])
        if end_card:
            # the moment it opened with, at its own speed, pushing in a little so the end card lands on a move
            t1 = min(t0 + n / fps, float(first["src"][1]) if float(first["src"][1]) > t0 else t0 + n / fps)
            it["src"] = [round(t0, 5), round(t1, 5)]
            it["crop"] = [K.r5(K.scale_crop(c0, 1.0 / (1.0 + 0.04 * j / max(n - 1, 1)))) for j in range(n)]
            held = t1 - t0 < n / fps - 1e-3
        else:
            lo = 0.0 if K.is_take(first.get("pic")) else (K.placed_range(l1, first["pic"]) or [0.0])[0]
            t_a = t0 - n / fps
            held = t_a < lo
            it["src"] = [round(t_a, 5), round(t0, 5)] if not held else [round(t0, 5), round(t0, 5)]
            if held:                             # nothing verified before its first frame: held, pulled to its first framing
                it["crop"] = [K.r5(K.scale_crop(c0, 1.0 / (1.0 + 0.05 * (1 - j / max(n - 1, 1))))) for j in range(n)]
    keep, gone, _ = K.carve(l1, dur - n, dur)
    return {"items": keep + [it], "drop": gone, "assets": {}, "events": [{"f": dur - n, "what": "loop", "kind": "cut", "strength": 0.4}],
            "log": {"frames": n, "pic": first["pic"], "opening_shot": first["id"], "opening_f": list(first["f"]),
                    "end_card_follows": end_card, "held": held}}


ALIASES = {"bookend": "callback", "recap": "callback"}

DEVICES = {
    "callback": {"plan": callback, "layer": "rhythm", "needs": ("opening",), "fallback": ("burst",), "span": True, "words": ("from",),
                 "card": "callback at until from[a0 z0] mode:burst|grid frames. The opening's pictures return at the payoff, as a fast recap or a grid, landing on the first one"},
    "loop": {"plan": loop, "layer": "rhythm", "needs": ("opening",), "fallback": (),
             "card": "loop frames end_card. The last frames return to the first picture the opening held, in its first framing: a bookend before the end card (a seamless loop only when nothing follows the film)"},
}
