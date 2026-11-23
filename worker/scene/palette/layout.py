"""layout: more than one picture on the frame at once. Grids that fill a tile per beat and collapse, split screens and
panels (the speaker beside what he names), a scrapbook pile on paper, photo-in-photo, the exit by shrinking, and the
dissolve or double exposure.

All of it is cards over a base shot: plain scene items, no new op. Which moment takes a layout, and which pictures, is
the caller's; the geometry here (tile seats, crops round the faces, arrival curves) is measured, never read.
"""
import math

from ..alien import sense
from . import picks as K

INK = "#0B0B0B"
PAPER = {"kind": "paper", "c0": "#E9E3D6", "c1": "#D9D1C0", "grain": 0.05}
DARK = {"kind": "paper", "c0": "#141414", "c1": "#0A0A0A", "grain": 0.03}
GUTTER = 0.014             # between tiles, frame widths


def _ground(arg):
    if isinstance(arg, dict):
        return dict(arg)
    return dict(PAPER) if str(arg or "dark") == "paper" else dict(DARK)


def _cover_for(pic, aspect, focus=None):
    """The crop of a picture that fills a box of this aspect (w/h in pixels), centred on its biggest face or ``focus``."""
    pw, ph = float(pic.w), float(pic.h)
    pa = pw / ph
    if pa > aspect:
        cw, ch = aspect / pa, 1.0
    else:
        cw, ch = 1.0, pa / aspect
    if focus is None and isinstance(pic.meta.get("x"), (int, float)) and not pic.faces:
        focus = (float(pic.meta["x"]), 0.45)
    if focus is None:
        if pic.faces:
            f = max(pic.faces, key=lambda q: q[2] * q[3])
            focus = (f[0] + f[2] / 2, (f[4] if len(f) > 4 else f[1] + f[3] * 0.42))
        else:
            focus = (0.5, 0.45)
    x0 = min(max(focus[0] - cw / 2, 0.0), 1.0 - cw)
    y0 = min(max(focus[1] - ch * 0.42, 0.0), 1.0 - ch)
    return [round(x0, 5), round(y0, 5), round(x0 + cw, 5), round(y0 + ch, 5)]


def _arrive(n, frames=5, s0=1.08):
    """A card's scale on each of its frames: it lands a little large and settles in ``frames``."""
    k = K.decel(max(frames, 2), 0.1)
    return [round(s0 - (s0 - 1.0) * k[j], 5) if j < frames else 1.0 for j in range(n)]


def verified_lo(l1, work, aid):
    """The earliest source second of an asset a viewer may see: its verified range's start (0 for the take, whose every
    frame is the speaker's own)."""
    if K.is_take(aid):
        return 0.0
    pic = K.pic_of_asset(l1, work, aid)
    return pic.span()[0] if pic is not None else 0.0


def preroll(l1, work, q, n):
    """(hold, start): an incoming shot ``q`` seen n frames before its cut, at real speed. Its clock runs back from its own
    src start as far as its verified range allows; the frames before that hold its first verified frame. hold frames
    hold on ``start``; then it runs from ``start`` into the cut unbroken."""
    fps = K.geometry(l1)[2]
    s0 = float(q["src"][0])
    lo = verified_lo(l1, work, q.get("pic"))
    run = int(min(n, max(int((s0 - lo) * fps + 1e-6), 0)))
    return n - run, round(s0 - run / fps, 5)


def split_at(card, hold, tail_id):
    """A card cut in two at ``hold`` frames in (picks.split_frames): (head, tail)."""
    return K.split_frames(card, hold, tail_id)


def _n_chosen(args):
    """How many pictures the caller chose (``pics``, or picture ids in ``pool``): a layout's count when no beat sets it."""
    return len(list(args.get("pics") or []) + [p for p in (args.get("pool") or []) if isinstance(p, str)])


def _steps(a, z, k, args, frac=0.7):
    """Frames each of k elements arrives on: the caller's (word frames), else even steps over the first ``frac``."""
    st = sorted(int(f) for f in (args.get("steps") or []) if isinstance(f, (int, float)) and a <= int(f) < z)
    if len(st) >= k:
        return st[:k]
    span_ = max(int((z - a) * frac), k)
    return [a + int(round(span_ * i / k)) for i in range(k)]


def grid_shape(n, W, H):
    """(rows, cols) for n tiles on a frame of this shape: portrait frames stack more rows."""
    if n <= 2:
        return (2, 1) if H > W else (1, 2)
    if n <= 4:
        return 2, 2
    if n <= 6:
        return (3, 2) if H > W else (2, 3)
    if n <= 9:
        return 3, 3
    return (4, 3) if H > W else (3, 4)


# ----------------------------------------------------------------- grid

def grid(l1, work, ask, sense_=None, pool=None):
    """A grid that fills one tile per beat (each cut or word), holds, and collapses tile by tile; or, with ``resolve``, one
    picture that resolves through its own tiles. ask: at/until, pics, rows, cols, steps [frames], ground (dark|paper),
    collapse (default true), resolve (a picture id)."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    a, z = K.span(l1, args, default_s=2.4, least=12)
    rng = K.rng_of(l1, args)
    given = sorted(int(f) for f in (args.get("steps") or []) if isinstance(f, (int, float)) and a <= int(f) < z)
    if args.get("resolve"):
        pics = K.pictures(l1, work, dict(args, pics=[args["resolve"]], only=True), pool, sense_=sense_)
    else:
        want = int(args.get("tiles") or len(given) or (int(args.get("rows") or 0) * int(args.get("cols") or 0)) or _n_chosen(args) or 6)
        pics = K.pictures(l1, work, args, pool, sense_=sense_, need=want, length_s=(z - a) / fps * 0.5, distinct=True)
    if not pics:
        return {"skip": "no pictures for a grid"}
    n_want = int(args.get("tiles") or (len(given) if given else 0) or (len(pics) if not args.get("resolve") else 6))
    n_want = min(max(n_want, 2), 12)
    rows, cols = int(args.get("rows") or 0), int(args.get("cols") or 0)
    if not rows or not cols:
        rows, cols = grid_shape(n_want, W, H)
    n = min(rows * cols, n_want) if not (args.get("rows") and args.get("cols")) else rows * cols
    y_top, y_bot = 0.06, 0.94
    gw = GUTTER
    gh = GUTTER * W / H
    tw = (1.0 - gw * (cols + 1)) / cols
    th = ((y_bot - y_top) - gh * (rows - 1)) / rows
    seats = [(gw + c * (tw + gw) + tw / 2, y_top + r * (th + gh) + th / 2) for r in range(rows) for c in range(cols)]
    order = list(range(n))
    rng.shuffle(order) if args.get("scatter") else None
    steps = _steps(a, z, n, args)
    collapse = args.get("collapse", True)
    out_f = 5 if collapse else 0
    keep, gone, was = K.carve(l1, a, z)
    base = dict(id=f"{args.get('id', 'grid')}.ground", k="shot", f=[a, z], pic="plate", plate=_ground(args.get("ground")))
    items, marks = [base], []
    asp = tw * W / (th * H)
    for i, k in enumerate(order):
        f0 = steps[i]
        n_f = z - f0
        if n_f < 2:
            continue
        cx, cy = seats[k]
        if args.get("resolve"):
            pic = pics[0]
            full = _cover_for(pic, W * (1.0 - 2 * gw) / (H * (y_bot - y_top)))
            fx0, fy0 = (cx - tw / 2 - gw) / (1.0 - 2 * gw), (cy - th / 2 - y_top) / (y_bot - y_top)
            fx1, fy1 = (cx + tw / 2 - gw) / (1.0 - 2 * gw), (cy + th / 2 - y_top) / (y_bot - y_top)
            cw, ch = full[2] - full[0], full[3] - full[1]
            crop = [round(full[0] + fx0 * cw, 5), round(full[1] + fy0 * ch, 5), round(full[0] + fx1 * cw, 5), round(full[1] + fy1 * ch, 5)]
        else:
            pic = pics[i % len(pics)]
            crop = _cover_for(pic, asp)
        s = _arrive(n_f)
        if collapse:
            # the tiles leave in the order they came, each shrinking to its seat inside the last frames
            lead = min(int(i * out_f / max(n, 1)), out_f - 2)
            j0 = max(n_f - out_f + lead, 0)
            for j in range(j0, n_f):
                u = (j - j0 + 1) / max(n_f - j0, 1)
                s[j] = round(max(s[j] * (1.0 - u ** 1.6), 0.02), 5)
        it = dict(id=f"{args.get('id', 'grid')}.t{i}", k="card", f=[f0, z], pic=pic.aid, x=round(cx, 5), y=round(cy, 5),
                  w=round(tw, 5), h=round(th, 5), s=s, rot=0, op=1, z=2 + i, crop=crop, radius=0.006, shutter=0.2)
        items += K.clocked(it, pic, fps, rng, i)          # its own clock: real speed, or run then held
        marks.append({"f": f0, "what": "tile lands", "kind": "tile", "strength": 0.4})
    return {"items": keep + items, "drop": gone, "assets": K.new_assets(pics[:n] if not args.get("resolve") else pics[:1]),
            "events": marks, "log": {"rows": rows, "cols": cols, "tiles": len(marks), "resolve": bool(args.get("resolve"))}}


# ----------------------------------------------------------------- split screen and panels

def split(l1, work, ask, sense_=None, pool=None):
    """The frame cut into panels: the speaker in one (on his own clock, so the voice stays in sync) and what he names in
    the other; or two or three pictures side by side. Panels slide in from their own edges and settle.
    ask: at/until, pics, dir ("h": stacked, "v": columns), take (keep the speaker as a panel, default when he is on screen)."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    a, z = K.span(l1, args, default_s=2.0, least=10)
    rng = K.rng_of(l1, args)
    under = K.shot_at(l1, a)
    take_on = under is not None and K.is_take(under.get("pic"))
    with_take = bool(args.get("take", take_on)) and take_on
    pics = K.pictures(l1, work, args, pool, sense_=sense_, least=2 if not with_take else 1)
    given = len([p for p in pics if p.chosen is not None])
    k = int(args.get("panels") or ((given + (1 if with_take else 0)) if given else (2 if with_take else min(max(len(pics), 2), 3))))
    k = min(max(k, 2), 3)
    need = k - (1 if with_take else 0)
    if len(pics) < need:
        if not pics:
            return {"skip": "no pictures for the panels"}
        need = len(pics)
        k = need + (1 if with_take else 0)
        if k < 2:
            return {"skip": "a split needs two panels"}
    stacked = str(args.get("dir", "h" if H > W else "v")) == "h"
    keep, gone, was = K.carve(l1, a, z)
    items, marks = [], []
    if with_take:
        # the speaker, dimmed and out of focus, is the ground; his panel is the same take on the same clock
        bed = sense.slice_shot(under, a, min(z, under["f"][1]), f"{args.get('id', 'split')}.bed")
        bed = dict(bed, blur=26, grade={"lift": 0.0, "gamma": 1.0, "gain": 0.4, "sat": 0.7, "contrast": 1.0, "temp": 0.0, "tint": 0.0})
        bed.pop("crop", None)
        if bed["f"][1] < z:
            items.append(dict(id=f"{args.get('id', 'split')}.bedz", k="shot", f=[bed["f"][1], z], pic="plate", plate=dict(DARK)))
        items.append(bed)
    else:
        items.append(dict(id=f"{args.get('id', 'split')}.ground", k="shot", f=[a, z], pic="plate", plate=dict(DARK)))
    g = 0.012
    slots = []
    for i in range(k):
        if stacked:
            h_ = (1.0 - g * (k + 1) * W / H) / k
            slots.append((0.5, g * W / H + h_ / 2 + i * (h_ + g * W / H), 1.0 - 2 * g, h_))
        else:
            w_ = (1.0 - g * (k + 1)) / k
            slots.append((g + w_ / 2 + i * (w_ + g), 0.5, w_, 1.0 - 2 * g * W / H))
    stagger = max(int(0.12 * fps), 2)
    for i, (cx, cy, w_, h_) in enumerate(slots):
        clip = None
        f0 = min(a + i * stagger, z - 4)
        n = z - f0
        asp = w_ * W / (h_ * H)
        if with_take and i == 0:
            src_t = sense.src_time(under, f0, fps)
            pic_id = under["pic"]
            tc = K.crops(l1, work, under)[min(f0, under["f"][1] - 1) - under["f"][0]]
            tp = K.pic_of_asset(l1, work, pic_id)
            if tp:
                pw, ph = tp.w, tp.h
                fx, fy = (tc[0] + tc[2]) / 2, tc[1] + 0.40 * (tc[3] - tc[1])
                crop = _cover_for(K.Pic(pic_id, tp.path, "video", pw, ph, tp.dur), asp, (fx, fy))
            else:
                crop = [0.0, 0.0, 1.0, 1.0]
            it = dict(id=f"{args.get('id', 'split')}.p{i}", k="card", f=[f0, z], pic=pic_id, src=[round(src_t, 5), round(src_t + n / fps, 5)], crop=crop)
        else:
            pic = pics[(i - (1 if with_take else 0)) % len(pics)]
            it = dict(id=f"{args.get('id', 'split')}.p{i}", k="card", f=[f0, z], pic=pic.aid, crop=_cover_for(pic, asp))
            clip = pic
        # in from its own edge: the first panel from the top (or left), the next from the bottom (or right)
        sgn = -1 if i % 2 == 0 else 1
        m = min(8, n)
        kk = K.decel(m, 0.12)
        if stacked:
            ys = [round(cy + sgn * (1.0 - kk[j]) * 0.6 * h_, 5) if j < m else round(cy, 5) for j in range(n)]      # already entering on its first frame
            it.update(x=round(cx, 5), y=ys)
        else:
            xs = [round(cx + sgn * (1.0 - kk[j]) * 0.6 * w_, 5) if j < m else round(cx, 5) for j in range(n)]
            it.update(x=xs, y=round(cy, 5))
        it.update(w=round(w_, 5), h=round(h_, 5), s=1, rot=0, op=1, z=3 + i, radius=0.008, shadow={"a": 0.5, "blur": 0.02, "dx": 0, "dy": 0.008})
        items += K.clocked(it, clip, fps, rng, i) if clip is not None else [it]
        marks.append({"f": f0, "what": "panel lands", "kind": "panel", "strength": 0.45})
    used = [p for p in pics[:need]]
    return {"items": keep + items, "drop": gone, "assets": K.new_assets(used), "events": marks,
            "log": {"panels": k, "take": with_take, "stacked": stacked}}


# ----------------------------------------------------------------- collage

def collage(l1, work, ask, sense_=None, pool=None):
    """A scrapbook pile on paper: prints land one per beat, each turned a few degrees, dropping in and settling, with a
    white border and a shadow, on top of the ones before; the whole pile drifts closer. ask: at/until, pics, steps,
    ground (paper|dark), border."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    a, z = K.span(l1, args, default_s=2.4, least=12)
    rng = K.rng_of(l1, args)
    want = int(args.get("prints") or len([f for f in args.get("steps") or [] if isinstance(f, (int, float))]) or _n_chosen(args) or 6)
    pics = K.pictures(l1, work, args, pool, sense_=sense_, need=min(want, 9), length_s=(z - a) / fps * 0.5, distinct=True)
    if not pics:
        return {"skip": "no pictures for a pile"}
    k = min(want, len(pics), 9)
    k = max(k, min(3, len(pics)))
    pics = pics[:k]
    steps = _steps(a, z, k, args, frac=0.8)
    keep, gone, was = K.carve(l1, a, z)
    items = [dict(id=f"{args.get('id', 'pile')}.ground", k="shot", f=[a, z], pic="plate", plate=_ground(args.get("ground", "paper")))]
    marks = []
    for i, (pic, f0) in enumerate(zip(pics, steps)):
        n = z - f0
        if n < 2:
            continue
        aspect = pic.w / max(pic.h, 1)
        w_ = float(rng.uniform(0.68, 0.84))
        h_ = w_ * W / H / aspect
        if h_ > 0.46:
            h_ = 0.46
            w_ = h_ * H / W * aspect
        cx = 0.5 + rng.uniform(-0.08, 0.08)
        cy = 0.40 + rng.uniform(-0.07, 0.07)
        rot = rng.choice((-1, 1)) * rng.uniform(2.0, 7.5)
        drop = _arrive(n, frames=6, s0=1.22)
        push = [1.0 + 0.04 * (f0 - a + j) / max(z - a, 1) for j in range(n)]
        it = dict(id=f"{args.get('id', 'pile')}.c{i}", k="card", f=[f0, z], pic=pic.aid, x=round(cx, 5), y=round(cy, 5), w=round(w_, 5),
                  h=round(h_, 5), s=[round(d * p, 5) for d, p in zip(drop, push)], rot=round(rot, 3),
                  op=[min(1.0, 0.35 + 0.65 * j / 2) for j in range(min(3, n))] + [1.0] * max(n - 3, 0),
                  z=2 + i, crop=_cover_for(pic, w_ * W / (h_ * H)),
                  border={"w": float(args.get("border", 0.012)), "c": "#F7F4EC"},
                  shadow={"a": 0.55, "blur": 0.02, "dx": 0.004, "dy": 0.012})
        items += K.clocked(it, pic, fps, rng, i)
        marks.append({"f": f0, "what": "print lands", "kind": "drop", "strength": 0.5})
    return {"items": keep + items, "drop": gone, "assets": K.new_assets(pics), "events": marks, "log": {"prints": len(marks)}}


# ----------------------------------------------------------------- photo-in-photo, shrink exit

def _cut_near(l1, f, reach=20):
    cs = K.cuts(l1)
    near = [c for c in cs if abs(c - f) <= reach]
    return min(near, key=lambda c: abs(c - f)) if near else None


def _full_card(sh, l1, work, f0, f1, cid):
    """A base shot redrawn as a full-frame card over [f0, f1): its picture, its crop on those frames and its clock."""
    W, H, fps = K.geometry(l1)
    lo = sh["f"][0]
    cs = K.crops(l1, work, sh)
    n = f1 - f0
    crop = [cs[min(max(f - lo, 0), len(cs) - 1)] for f in range(f0, f1)]
    it = dict(id=cid, k="card", f=[f0, f1], pic=sh["pic"], x=0.5, y=0.5, w=1.0, h=1.0, crop=crop, rot=0, op=1, z=4)
    if sh.get("src"):
        t0 = sense.src_time(sh, f0, fps)
        t1 = sense.src_time(sh, f1, fps)
        it["src"] = [round(max(t0, 0.0), 5), round(max(t1, t0), 5)]
    for k in ("look", "grade", "treat"):
        if sh.get(k):
            it[k] = sh[k]
    return it, n


def _early(q, f0, l1, work):
    """The incoming shot started early, on [f0, its end), at its own speed: its clock runs back as far as its verified
    range allows and the frames before that hold its first verified frame (preroll). Returns its items (the held head,
    when there is one, then the shot under its own id)."""
    lo, hi = q["f"]
    cs = K.crops(l1, work, q)
    it = dict(q, f=[f0, hi], crop=[cs[0]] * (lo - f0) + cs)
    if not q.get("src"):
        return [it]
    hold, start = preroll(l1, work, q, lo - f0)
    it["src"] = [start, q["src"][1]]
    if hold <= 0:
        return [it]
    head, tail = split_at(dict(it, id=f"{q['id']}~h"), hold, q["id"])
    head["src"] = [start, start]
    return [x for x in (head, tail) if x is not None]


def shrink(l1, work, ask, sense_=None, pool=None):
    """The exit by shrinking: at a join the outgoing picture shrinks into a small window and leaves while the next one
    already runs full frame under it. ask: at (near the join), frames (default 14), to ([x, y] where it shrinks to)."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    f, _ = K.span(l1, args, default_s=0.1, least=1)
    c = _cut_near(l1, f)
    if c is None:
        return {"skip": "no join near to exit through"}
    p, q = K.shot_at(l1, c - 1), K.shot_at(l1, c)
    n = min(int(args.get("frames", 14)), c - K.run_start(l1, c) - 2)
    if p is None or q is None or n < 6 or c - p["f"][0] < n + 2 or K.is_take(q.get("pic")) and not q.get("src"):
        return {"skip": "the outgoing shot is too short to shrink away"}
    f0 = c - n
    k = K.decel(n, 0.15)
    destination = args.get("to")
    tx, ty = destination if isinstance(destination, (list, tuple)) and len(destination) == 2 else (0.72, 0.30)
    size = min(1.0, max(0.03, destination)) if isinstance(destination, (int, float)) else 0.30
    cards = [it for it in l1.get("items", []) if it["k"] == "card" and it.get("pic") not in (None, "plate")
             and it["f"][0] < c and it["f"][1] > f0]
    if cards:
        # The outgoing picture may already be a card. Animate the visible
        # card in place, with every existing tilt, crop and source clock intact.
        from ..plan import A
        items = []
        for it in cards:
            lo, hi = it["f"]
            out = dict(it)
            for key, default, target in (("s", 1., size), ("x", .5, tx), ("y", .5, ty)):
                values = []
                for frame in range(lo, hi):
                    start = A(it.get(key, default), frame, lo)
                    t = k[frame - f0] if f0 <= frame < c else 0.
                    end = start * target if key == "s" else target
                    values.append(round(start + (end - start) * t, 5))
                out[key] = values
            items.append(out)
        return {"items": items, "events": [{"f": f0, "what": "shrink exit", "kind": "move", "strength": .4}],
                "log": {"join": c, "frames": n, "cards": len(cards)}}
    if p.get("pic") == "plate":
        return {"skip": "the outgoing shot has no picture to shrink"}
    card, _ = _full_card(p, l1, work, f0, c, f"{args.get('id', 'shrink')}.out")
    card["s"] = [round(1.0 - (1.0 - size) * k[j], 5) for j in range(n)]
    card["x"] = [round(0.5 + (tx - 0.5) * k[j], 5) for j in range(n)]
    card["y"] = [round(0.5 + (ty - 0.5) * k[j], 5) for j in range(n)]
    card["rot"] = [round(-3.5 * k[j], 4) for j in range(n)]
    card["shadow"] = {"a": 0.6, "blur": 0.025, "dx": 0.0, "dy": 0.012}
    card["radius"] = 0.01
    rest = sense.slice_shot(p, p["f"][0], f0, p["id"])
    new_q = _early(q, f0, l1, work)
    return {"items": [rest] + new_q + [card], "drop": [], "assets": {}, "events": [{"f": f0, "what": "shrink exit", "kind": "move", "strength": 0.4}],
            "log": {"join": c, "frames": n, "held": new_q[0]["f"][1] - new_q[0]["f"][0] if new_q[0]["id"] != q["id"] else 0}}


def _page_inset(l1, page, words, frames, xy, ident):
    """A designed page arrives on its spoken cut, with its lettering inside its frame."""
    from ..plan import A
    from .kit import item_box
    start, end = page["f"]
    n = min(frames, end-start)
    scale = [.46+.54*u for u in K.decel(n, .2)]
    dx = [(xy[0]-.5)*(1-v)/.54 for v in scale]
    dy = [(xy[1]-.5)*(1-v)/.54 for v in scale]
    tile = dict(id=f"{ident}.in", k="card", pic="plate", plate=page["plate"],
                f=[start,start+n], w=1., h=1., s=scale, x=[.5+x for x in dx], y=[.5+y for y in dy],
                z=4, border={"w":.008,"c":"#F7F4EC"})
    items = [tile]
    for word in words:
        placed = dict(word)
        values = {key:[] for key in ("x","y","s")}
        for f in range(*word["f"]):
            i = f-start
            x,y,size = (A(word.get(k,default), f, start) for k,default in (("x",0),("y",0),("s",1)))
            if i < n:
                left,top,right,bottom = item_box(word,l1["voices"],*l1["frame"],f)
                x += dx[i]+(.5-(left+right)/2)*(1-scale[i])
                y += dy[i]+(.5-(top+bottom)/2)*(1-scale[i])
                size *= scale[i]
            for key, value in zip(values,(x,y,size)):
                values[key].append(round(value,6))
        items.append(dict(placed,**values))
    return dict(items=items, drop=[w["id"] for w in words], assets={},
                events=[dict(f=start, what="printed page opens",kind="cut",strength=.4)],
                log=dict(join=start,frames=n,subject="authored page"))


def pip(l1, work, ask, sense_=None, pool=None):
    """Photo-in-photo: the next picture arrives as a framed window inside the current one, holds, then grows to take the
    frame on the join. With no join near, a picture of the pool comes in as an inset and leaves. ask: at, frames
    (default 24), pics, at_xy ([x, y] of the window)."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    f, _ = K.span(l1, args, default_s=0.1, least=1)
    n = int(args.get("frames", 24))
    wx, wy = args.get("at_xy") or (0.5, 0.36)
    c = _cut_near(l1, f + n // 2)
    p = K.shot_at(l1, c - 1) if c is not None else None
    q = K.shot_at(l1, c) if c is not None else None
    if c is not None and p is not None and q is not None and c - p["f"][0] >= n + 2:
        f0 = c - n
        if q.get("plate"):
            words = [it for it in l1["items"] if it["k"]=="word" and it.get("role")=="graphic"
                     and it["f"]==q["f"] and it.get("t") and it.get("v") in l1["voices"]]
            if words:
                return _page_inset(l1, q, words, n, (wx,wy), args.get("id","pip"))
        card, _ = _full_card(q, l1, work, c, c + 1, "tmp")
        # A documentary window sits over a cropped background of the same source.
        # Reveal the evidence itself, including its checked lettering and destination seat.
        window = next((item for item in l1["items"] if item.get("k")=="card" and (item.get("source_ink") or item.get("source_frame"))
                       and (item.get("pic")==q.get("pic") or q.get("plate")) and item["f"][0]==c), None)
        cs = K.crops(l1, work, q)
        if window:
            q = window
            card.update(pic=window["pic"], w=window["w"], h=window["h"])
            if window.get("source_frame"):
                card["source_frame"] = True
            cs = [window["crop"][0] if isinstance(window["crop"][0],list) else window["crop"]]
        tx,ty = (window["x"],window["y"]) if window else (.5,.5)
        card = dict(card, id=f"{args.get('id', 'pip')}.in", f=[f0, c], crop=[cs[0]] * n)
        still = 0
        if q.get("src"):
            still, start = preroll(l1, work, q, n)       # real speed into its own cut; the head holds its first verified frame
            card["src"] = [start, round(float(q["src"][0]), 5)]
        hold = int(n * 0.6)
        kk = K.decel(n - hold, 0.2)
        s = [0.42] * hold + [round(0.42 + 0.58 * kk[j], 5) for j in range(n - hold)]
        card["s"] = [round(s_ * (1.06 - 0.06 * min(j / 4, 1)) if j < 5 else s_, 5) for j, s_ in enumerate(s)]
        card["x"] = [round(wx + (tx - wx) * (0 if j < hold else kk[j - hold]), 5) for j in range(n)]
        card["y"] = [round(wy + (ty - wy) * (0 if j < hold else kk[j - hold]), 5) for j in range(n)]
        card.update(inset_seat=True, border={"w": 0.008, "c": "#F7F4EC"}, shadow={"a": 0.6, "blur": 0.03, "dx": 0.0, "dy": 0.014}, z=4)
        head, tail = split_at(card, still, f"{card['id']}~run")
        if head is not None:
            head["src"] = [card["src"][0], card["src"][0]]
        return {"items": [x for x in (head, tail) if x is not None], "drop": [], "assets": {},
                "events": [{"f": f0, "what": "window opens", "kind": "pop", "strength": 0.45},
                           {"f": c, "what": "window takes the frame", "kind": "cut", "strength": 0.4}],
                "log": {"join": c, "frames": n, "held": still}}
    pics = K.pictures(l1, work, args, pool, sense_=sense_)
    if not pics:
        return {"skip": "no join and no picture for a window"}
    pic = pics[0]
    a, z = K.span(l1, args, default_s=1.2, least=8)
    # A request may cross the end of a checked source. The pool then offers
    # the synchronous take. Land the inset on the existing documentary card
    # instead of covering its subject and detaching its identifying super.
    if K.is_take(pic.aid):
        checked = {row.get("pic") for row in l1.get("documentary_cuts", []) if row["f"][0] <= a < row["f"][1]}
        window = next((it for it in l1["items"] if it["k"] == "card" and it.get("pic") in checked
                       and it.get("source_ink") and it["f"][0] <= a < it["f"][1]), None)
        if window:
            from ..plan import A
            lo, hi = window["f"]
            arrival = K.decel(min(8, z-a, hi-a))
            scale = [A(window.get("s", 1), f, lo) for f in range(lo, hi)]
            for i, progress in enumerate(arrival, a-lo):
                scale[i] *= .9 + .1*progress
            return {"items": [dict(window, s=scale)],
                    "events": [{"f": a, "what": "source window settles", "kind": "pop", "strength": .45}],
                    "log": {"inset": window["pic"]}}
    m = z - a
    rng = K.rng_of(l1, args)
    w_ = 0.5
    h_ = w_ * W / H / (pic.w / pic.h)
    s = _arrive(m, 5, 1.12)
    for j in range(max(m - 5, 0), m):
        s[j] = round(s[j] * (1 - ((j - (m - 5)) / 5) ** 1.5), 5)
    card = dict(id=f"{args.get('id', 'pip')}.in", k="card", f=[a, z], pic=pic.aid, x=wx, y=wy, w=w_, h=round(min(h_, 0.4), 5), s=s, rot=0, op=1,
                inset_seat=True, z=4, crop=_cover_for(pic, w_ * W / (min(h_, 0.4) * H)), border={"w": 0.008, "c": "#F7F4EC"},
                shadow={"a": 0.6, "blur": 0.03, "dx": 0.0, "dy": 0.014})
    return {"items": K.clocked(card, pic, fps, rng), "drop": [], "assets": K.new_assets([pic]), "events": [{"f": a, "what": "inset", "kind": "pop", "strength": 0.45}],
            "log": {"inset": pic.aid}}


# ----------------------------------------------------------------- dissolve, double exposure

def dissolve(l1, work, ask, sense_=None, pool=None):
    """A dissolve at a join (the incoming picture fades up over the outgoing one), or, with ``double``, a double exposure:
    a picture of the pool held faint over the moment. Rare by design. ask: at, frames (default 10), double, op."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    f, _ = K.span(l1, args, default_s=0.1, least=1)
    if args.get("double"):
        a, z = K.span(l1, args, default_s=1.5, least=8)
        pics = K.pictures(l1, work, args, pool, sense_=sense_)
        if not pics:
            return {"skip": "no picture to lay over"}
        pic = pics[0]
        m = z - a
        op = float(args.get("op", 0.42))
        ramp = [round(op * min(j / 6, 1.0, (m - 1 - j) / 6), 4) for j in range(m)]
        card = dict(id=f"{args.get('id', 'dx')}.over", k="card", f=[a, z], pic=pic.aid, x=0.5, y=0.5, w=1.0, h=1.0, s=1, rot=0, op=ramp, z=1,
                    crop=_cover_for(pic, W / H))
        return {"items": K.clocked(card, pic, fps, K.rng_of(l1, args)), "drop": [], "assets": K.new_assets([pic]),
                "events": [{"f": a, "what": "double exposure", "kind": "soft", "strength": 0.2}],
                "log": {"double": pic.aid}}
    c = _cut_near(l1, f)
    if c is None:
        return {"skip": "no join near to dissolve"}
    p, q = K.shot_at(l1, c - 1), K.shot_at(l1, c)
    n = int(args.get("frames", 10))
    room = c - K.run_start(l1, c) - 1                     # the outgoing picture's frames before the join
    n = min(n, room)
    if p is None or q is None or n < 4:
        return {"skip": "the outgoing picture is too short to dissolve"}
    f0 = c - n
    cs = K.crops(l1, work, q)
    card = dict(id=f"{args.get('id', 'dx')}.in", k="card", f=[f0, c], pic=q["pic"], x=0.5, y=0.5, w=1.0, h=1.0, s=1, rot=0,
                op=[round(((j + 1) / (n + 1)) ** 1.2, 4) for j in range(n)], z=1, crop=[cs[0]] * n)
    still = 0
    if q.get("src"):
        still, start = preroll(l1, work, q, n)           # real speed into its own cut; the head holds its first verified frame
        card["src"] = [start, round(float(q["src"][0]), 5)]
    for k in ("look", "grade", "treat"):
        if q.get(k):
            card[k] = q[k]
    head, tail = split_at(card, still, f"{card['id']}~run")
    if head is not None:
        head["src"] = [card["src"][0], card["src"][0]]
    return {"items": [x for x in (head, tail) if x is not None], "drop": [], "assets": {},
            "events": [{"f": f0, "what": "dissolve", "kind": "soft", "strength": 0.2}], "log": {"join": c, "frames": n, "held": still}}


ALIASES = {"tiles": "grid", "tile_grid": "grid", "grid_reveal": "grid", "split_screen": "split", "panels": "split",
           "scrapbook": "collage", "pile": "collage", "photo_in_photo": "pip", "inset": "pip", "exit": "shrink",
           "shrink_exit": "shrink", "double_exposure": "dissolve", "crossfade": "dissolve"}

DEVICES = {
    "grid": {"plan": grid, "layer": "rhythm", "needs": ("pictures",), "fallback": ("collage", "burst"), "span": True, "words": ("steps[]",),
             "card": "grid at until pics[] rows cols steps[] ground collapse resolve. A grid that fills one tile per beat and collapses; or one picture resolving through its tiles; the hook, 'seven founders', who's who"},
    "split": {"plan": split, "layer": "rhythm", "needs": ("pictures",), "fallback": ("pip",), "span": True,
              "card": "split at until pics[] dir:h|v take. Panels: the speaker on his own clock beside what he names, or two or three pictures side by side; contrasts, comparisons"},
    "collage": {"plan": collage, "layer": "rhythm", "needs": ("pictures",), "fallback": ("grid", "burst"), "span": True, "words": ("steps[]",),
                "card": "collage at until pics[] steps[] ground. A scrapbook pile on paper: prints land one per beat, turned, bordered, dropping in; archive, a life in pictures"},
    "pip": {"plan": pip, "layer": "surface", "needs": (), "fallback": ("dissolve",),
            "card": "pip at frames pics[] at_xy. Photo-in-photo: the next picture arrives as a window inside this one and grows to take the frame; name intros, archive"},
    "shrink": {"plan": shrink, "layer": "surface", "needs": ("cut",), "fallback": ("whip",),
               "card": "shrink at frames to. The exit by shrinking: the picture shrinks into a small window and leaves while the next runs under it; chapter exits"},
    "dissolve": {"plan": dissolve, "layer": "surface", "needs": (), "fallback": (),
                 "card": "dissolve at frames double op pics[]. A dissolve at a join, or a picture held faint over the moment (double exposure); rare"},
}
