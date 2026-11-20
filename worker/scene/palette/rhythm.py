"""rhythm: when the picture changes and how hard it lands. Bursts, the pace contour, glimpses, early-peaking settles,
snap zooms, whips and streaks, shakes, speed ramps and freezes, each laid on the moment the caller chose.

Every device gives back plain scene items (shots with crop and src, cards with x/y): no new op, no renderer change.
Which moment earns a device is the caller's (the Director's asks); the signal here decides only the frame-exact
geometry: the cut frames on the voice's stress, the curves, the luminance path of a strobe.

    patch = rhythm.burst(l1, work, {"dev": "burst", "id": "b1", "at": [120, 165], "pics": ["v03", "p01"]})

A strobe never flashes: a burst of pictures is laid as a path of light (or, when the words fix the order, its outliers
are graded toward their neighbours), so the frame's luminance stays under three flashes a second (picks.flash_safe).
"""
import math

from ..alien import sense
from . import picks as K

# Shots of 1-2 frames are the punctuation devices' (white, black, negative frames, glimpses): 8.6% of the judge's shots.
# A strobe or a burst cuts no shorter than the judge's p10 shot (0.13 s, 4 frames) and a burst's own strobe lasts about a
# second, then lands and holds (20 Nov rating: bursts asked over 3.5 s strobed 34 three-frame shots and strobe sections
# cut 2-frame framings, so 21% of one film's shots were 1-2 frames and it read over-strobed).
STROBE_F = (4, 8)          # a strobe section's shots, in frames
BRISK_F = (10, 24)         # a brisk section: a new picture every 0.33-0.8 s (MM 16.1 a 10 s at the median)
BURST_F = (3, 6)           # a list burst: 3-6 frames a picture
BURST_RUN_S = 1.2          # the burst's strobe, at most; the picture it lands on holds the rest of its span
TASTE_UP = 3.0             # the tightest any framing is drawn: three output pixels a source pixel (TASTE 4.2)


def _patch(keep, gone, items, pics, marks, log):
    return {"items": keep + items, "drop": gone, "assets": K.new_assets(pics), "events": marks, "log": log}


def _lengths(n, lo, hi, rng, stress=None, a=0):
    """Shot lengths in frames that add up to n, each in [lo, hi]; with stress peaks, cuts are pulled onto them."""
    out, t = [], 0
    peaks = sorted(f - a for f, s in (stress or []) if s >= 0.5 and a <= f < a + n)
    while n - t > hi:
        want = rng.randint(lo, hi)
        near = [p for p in peaks if t + lo <= p <= t + hi]
        if near:
            want = min(near, key=lambda p: abs(p - (t + want))) - t
        out.append(want)
        t += want
    rest = n - t
    if rest < lo and out:
        out[-1] += rest
    elif rest > 0:
        out.append(rest)
    return out


def _tight_ok(pic, crop, W):
    return W / max((crop[2] - crop[0]) * pic.w, 1e-6) <= TASTE_UP


def _framing(pic, W, H, k, rng):
    """The k-th framing of a picture for a strobe: its framings in turn, never the same twice running."""
    st = K.still_of(pic, size=(160, 160))
    menu = K.framings(pic, W, H, st)
    name, crop = menu[k % len(menu)]
    return name, crop


def _clip_shots(seq, a, lens, fps, look, mono, ask_id, tag):
    """Items for a run of (pic, t0, crop, gain) laid end to end from frame a with the given lengths."""
    items, marks, pos = [], [], a
    for i, ((pic, t0, crop, gain), n) in enumerate(zip(seq, lens)):
        it = dict(look, id=f"{ask_id}.{tag}{i}", k="shot", f=[pos, pos + n], pic=pic.aid)
        # a picture in a strobe still moves: a 3% push across its few frames, so the run never reads as stills
        # (a picture that holds, a burst's landing, pushes the way the references push a held picture: about 8% a second)
        push = 0.03 if n <= 8 else min(0.04 + 0.06 * n / fps, 0.14)
        it["crop"] = [K.r5(K.scale_crop(crop, 1.0 / (1.0 + push * j / max(n - 1, 1)))) for j in range(n)]
        if pic.video:
            it["src"] = [round(t0, 4), round(t0 + n / fps, 4)]
        if abs(gain - 1.0) > 0.01:
            it["grade"] = {"lift": 0.0, "gamma": 1.0, "gain": round(gain, 3), "sat": 1.0, "contrast": 1.0, "temp": 0.0, "tint": 0.0}
        if mono:
            it["treat"] = {"mode": "mono"}
        items.append(it)
        marks.append({"f": pos, "what": f"{tag} cut", "kind": "cut", "strength": 0.8 if i == 0 else 0.35})
        pos += n
    return items, marks


def _strobe_seq(pics, k, lens, W, H, fps, rng, order=None, land=None):
    """k (pic, t0, crop, gain): every picture in turn, a clip at a new moment each time it returns, a still at a new
    framing; laid on a path of light unless the order is the caller's. ``land``: the picture the run lands on and holds
    (the caller's last chosen one); the others run down then up in light into it, so it stays last and the strobe keeps
    to one turn of light."""
    uses = {}
    raw = []
    for i in range(k):
        pic = land if (land is not None and i == k - 1) else pics[i % len(pics)]
        j = uses.get(pic.aid, 0)
        uses[pic.aid] = j + 1
        raw.append((pic, j))
    per = {}
    for pic, j in raw:
        per.setdefault(pic.aid, (pic, uses[pic.aid]))
    when = {aid: K.moments(pic, n, max(lens) / fps + 0.05, rng) for aid, (pic, n) in per.items()}
    seq = []
    for pic, j in raw:
        t0 = when[pic.aid][j][0]
        if pic.video:
            crop = K.cover_on(pic, W, H)
            if pic.faces and j % 2 == 1:
                f = max(pic.faces, key=lambda q: q[2] * q[3])
                c2 = K.cam_of(pic, W, H, TASTE_UP).on_face(f[:4], 0.34, eyes=f[4] if len(f) > 4 else None)
                crop = c2 if _tight_ok(pic, c2, W) else crop
            elif j % 2 == 1:
                c2 = K.scale_crop(crop, 0.8)
                crop = c2 if _tight_ok(pic, c2, W) else crop
        else:
            crop = _framing(pic, W, H, j, rng)[1]
        seq.append([pic, t0, list(crop), 1.0])
    lum = [K.luminance(p, t0 + 0.02, c) for p, t0, c, _ in seq]
    if order is None and land is not None and len(seq) > 2:
        # the brighter ones falling, the darker ones rising into the landing: one turn of light, the landing last
        L = lum[-1]
        hi_ = sorted((i for i in range(len(seq) - 1) if lum[i] > L), key=lambda i: -lum[i])
        lo_ = sorted((i for i in range(len(seq) - 1) if lum[i] <= L), key=lambda i: lum[i])
        idx = hi_ + lo_ + [len(seq) - 1]
        seq = [seq[i] for i in idx]
        lum = [lum[i] for i in idx]
        for i in range(1, len(seq) - 2):             # a picture never follows itself when another of like light can
            if seq[i][0].aid == seq[i - 1][0].aid:
                for j in range(i + 1, min(i + 4, len(seq) - 1)):
                    if seq[j][0].aid != seq[i - 1][0].aid and abs(lum[j] - lum[i]) < K.FLASH_STEP:
                        seq[i], seq[j] = seq[j], seq[i]
                        lum[i], lum[j] = lum[j], lum[i]
                        break
        _, gains = K.flash_safe(lum, lens, fps, order=list(range(len(seq))))
        if [i for i in _] != list(range(len(seq))):    # no grade brings them together: the light path stands ungraded
            gains = [1.0] * len(seq)
    elif order is None:
        idx, gains = K.flash_safe(lum, lens, fps)
        seq = [seq[i] for i in idx]
        lum = [lum[i] for i in idx]
        for i in range(1, len(seq) - 1):            # a picture never follows itself when another of like light can
            if seq[i][0].aid == seq[i - 1][0].aid:
                for j in range(i + 1, min(i + 4, len(seq))):
                    if seq[j][0].aid != seq[i - 1][0].aid and abs(lum[j] - lum[i]) < K.FLASH_STEP:
                        seq[i], seq[j] = seq[j], seq[i]
                        lum[i], lum[j] = lum[j], lum[i]
                        break
    else:
        idx, gains = K.flash_safe(lum, lens, fps, order=list(range(len(seq))))
    for i, g in enumerate(gains):
        seq[i][3] = g
    return [tuple(s) for s in seq], lum


# ----------------------------------------------------------------- burst

def burst(l1, work, ask, sense_=None, pool=None):
    """A list burst: 8-45 related pictures at 2-5 frames each over [a, z), the last one landing and held.
    ask: at/until, pics, frames (one length for all), land (frames the last holds, default 6), mono, order ("given")."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    a, z = K.span(l1, args, default_s=1.2, least=6)
    rng = K.rng_of(l1, args)
    land = min(int(args.get("land", 6)), max((z - a) // 3, 2))
    land = max(land, z - a - int(round(BURST_RUN_S * fps)))       # the strobe runs about a second, the landing holds the rest
    fixed = args.get("frames")
    lo, hi = (max(int(fixed), BURST_F[0]), max(int(fixed), BURST_F[0])) if fixed else BURST_F
    lens = _lengths(z - a - land, lo, hi, rng) + [land]
    pics = K.pictures(l1, work, args, pool, sense_=sense_, need=len(lens), length_s=hi / fps + 0.05)
    if not pics:
        return {"skip": "no pictures to burst"}
    picked = [p for p in pics if p.chosen is not None]
    landing = max(picked, key=lambda p: p.chosen) if picked else None      # the caller's last chosen picture lands and holds
    seq, lum = _strobe_seq(pics, len(lens), lens, W, H, fps, rng, order=args.get("order"), land=landing)
    keep, gone, was = K.carve(l1, a, z)
    items, marks = _clip_shots(seq, a, lens, fps, K.inherit(was), bool(args.get("mono")), args.get("id", "burst"), "b")
    used = list({p.aid: p for p, *_ in seq}.values())
    return _patch(keep, gone, items, used, marks,
                  {"shots": len(items), "pictures": len(used), "lengths": lens, "flashes": K.flashes(lum, lens, fps),
                   "chosen": len(picked), "pool_added": len(pics) - len(picked),
                   "lands_on": seq[-1][0].aid})


# ----------------------------------------------------------------- contour

def contour(l1, work, ask, sense_=None, pool=None):
    """The pace contour: each section re-cut at its energy. strobe: 2-8 frame shots of the moment's pictures (the pool's
    and the section's own, other moments of the same clips); brisk: a new picture every 0.33-0.8 s, the take's
    stretches alternating with pictures and with its own tighter framing; hold: no cut is added, and pieces of one
    continuous take that the base cut apart are joined again.
    ask: sections [{"at": [a, z] | "f": [a, z], "energy": "strobe|brisk|hold"}], or at/until + energy; stress [(f, s)]."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    secs = args.get("sections") or [{"f": args.get("f"), "at": args.get("at"), "until": args.get("until"), "energy": args.get("energy", "brisk")}]
    cur = l1
    out_items, out_drop, out_assets, marks, log = {}, set(), {}, [], []
    rng = K.rng_of(l1, args)
    spans = [(K.span(l1, dict(s_), default_s=2.0, least=4), str(s_.get("energy") or "brisk")) for s_ in secs]
    stress = [(int(q[0]), float(q[1])) for q in args.get("stress") or [] if isinstance(q, (list, tuple)) and len(q) >= 2]
    stress += [(int(q), 1.0) for q in args.get("stress") or [] if isinstance(q, (int, float)) and not isinstance(q, bool)]
    heard = "ask"
    if not stress and spans:
        # the cuts land on the voice's stress: read off the take's sound (else the words' timing) when the ask gave none
        lo_, hi_ = min(a for (a, z), _ in spans), max(z for (a, z), _ in spans)
        stress = K.voice_stress(l1, work, lo_, hi_)
        heard = "voice" if stress else "none"
    need = sum((z - a) // 4 if en == "strobe" else (z - a) // (2 * BRISK_F[0]) for (a, z), en in spans if en != "hold")
    pics_all = K.pictures(l1, work, args, pool, sense_=sense_, need=need, length_s=STROBE_F[1] / fps + 0.05)
    turn = 0
    for s_ in secs:
        a, z = K.span(cur, dict(s_), default_s=2.0, least=4)
        energy = str(s_.get("energy") or "brisk")
        if energy == "hold":
            merged, gone = _join_take(cur, a, z)
            if merged:
                cur = _merge(cur, merged, gone)
                for it in merged:
                    out_items[it["id"]] = it
                out_drop |= set(gone)
            log.append({"f": [a, z], "energy": energy, "joined": len(gone)})
            continue
        lo, hi = STROBE_F if energy == "strobe" else BRISK_F
        lens = _lengths(z - a, lo, hi, rng, stress, a)
        base = [sh for sh in K.shots(cur) if sh["f"][0] < z and sh["f"][1] > a]
        own = [K.pic_of_asset(cur, work, sh["pic"]) for sh in base if not K.is_take(sh.get("pic"))]
        own = [p for p in own if p]
        # the caller's chosen pictures first, then the pictures the base already placed on these words, then the rest
        chosen = [p for p in pics_all if p.chosen is not None]
        pics = list({p.aid: p for p in chosen + own + [p for p in pics_all if p.chosen is None]}.values())
        keep, gone, was = K.carve(cur, a, z)
        items, pos, used = [], a, []
        look = K.inherit(was)
        if energy == "strobe" and pics:
            seq, lum = _strobe_seq(pics[turn:] + pics[:turn], len(lens), lens, W, H, fps, rng)
            turn = (turn + len(lens)) % max(len(pics), 1)
            items, mk = _clip_shots(seq, a, lens, fps, look, bool(s_.get("mono")), f"{args.get('id', 'ct')}.s{len(log)}", "c")
            marks += mk
            used = [p for p, *_ in seq]
        else:
            # brisk (or a strobe with nothing but the take): the base where it is, a picture or a tighter framing between
            k_pic = 0
            for i, n in enumerate(lens):
                sh = next((q for q in was if q["f"][0] <= pos < q["f"][1]), None)
                take = sh is not None and K.is_take(sh.get("pic"))
                pid = f"{args.get('id', 'ct')}.s{len(log)}.{i}"
                clip = None
                if i % 2 == 1 and pics and (take or energy == "strobe" or rng.random() < 0.5):
                    pic = pics[(turn + k_pic) % len(pics)]
                    k_pic += 1
                    name, crop = _framing(pic, W, H, k_pic, rng) if not pic.video else ("wide", K.cover_on(pic, W, H))
                    it = dict(look, id=pid, k="shot", f=[pos, pos + n], pic=pic.aid,
                              crop=[K.r5(K.scale_crop(crop, 1.0 / (1.0 + 0.04 * j / max(n - 1, 1)))) for j in range(n)])
                    clip = pic
                    used.append(pic)
                elif sh is not None:
                    # the base where it is: every base shot this stretch crosses keeps its own piece (a stretch that
                    # crosses a base cut is never cut short at it)
                    under = [q for q in was if q["f"][0] < pos + n and q["f"][1] > pos]
                    parts = []
                    for j, q in enumerate(under):
                        a_, b_ = max(pos, q["f"][0]), min(pos + n, q["f"][1])
                        piece = sense.slice_shot(q, a_, b_, pid if j == 0 else f"{pid}.{j}")
                        if take and i % 2 == 1 and K.is_take(q.get("pic")):
                            c = K.crops(cur, work, q)[a_ - q["f"][0]:b_ - q["f"][0]]
                            piece["crop"] = [K.r5(K.scale_crop(c_, 0.78, (c_[0] + c_[2]) / 2, c_[1] + 0.36 * (c_[3] - c_[1]))) for c_ in c]
                        parts.append(piece)
                    it = parts
                else:
                    pos += n
                    continue
                items += (K.clocked(it, clip, fps, rng, k_pic) if clip is not None else [it]) if isinstance(it, dict) else it
                marks.append({"f": pos, "what": f"{energy} cut", "kind": "cut", "strength": 0.4})
                pos += n
            turn = (turn + k_pic) % max(len(pics), 1)
        cur = _merge(cur, keep + items, gone)
        for it in keep + items:
            out_items[it["id"]] = it
        out_drop |= set(gone)
        out_assets.update(K.new_assets(used))
        log.append({"f": [a, z], "energy": energy, "shots": len(items), "cuts_per_3s": round(len(items) / max((z - a) / fps, 1e-6) * 3, 2)})
    final_ids = {i["id"] for i in cur.get("items") or []}
    items = [it for i, it in out_items.items() if i in final_ids]
    drop = [g for g in out_drop if g not in final_ids and g in {i["id"] for i in l1.get("items") or []}]
    if not items and not drop:
        return {"skip": "the contour changed nothing"}
    return {"items": items, "drop": drop, "assets": out_assets, "events": marks,
            "log": {"sections": log, "stress": heard, "stress_peaks": len([q for q in stress if q[1] >= 0.5])}}


def _merge(l1, items, gone):
    out = dict(l1)
    ids = {i["id"] for i in items}
    gone = set(gone)
    out["items"] = [i for i in l1.get("items") or [] if i["id"] not in gone and i["id"] not in ids] + list(items)
    return out


def _join_take(l1, a, z):
    """Neighbouring pieces of one continuous take inside [a, z) as one shot again: (new items, ids gone)."""
    seq = [s for s in K.shots(l1) if s["f"][0] >= a and s["f"][1] <= z]
    out, gone = [], []
    run = []

    def flush():
        if len(run) > 1:
            first, last = run[0], run[-1]
            it = dict(first, f=[first["f"][0], last["f"][1]])
            if first.get("src"):
                it["src"] = [first["src"][0], last["src"][1]]
            for k in ("crop", "blur"):
                vals = [v for r in run for v in (r.get(k) if isinstance(r.get(k), list) and len(r.get(k)) == r["f"][1] - r["f"][0] else
                                                  [r.get(k)] * (r["f"][1] - r["f"][0]))]
                if any(v is not None for v in vals):
                    it[k] = vals if all(v is not None for v in vals) else first.get(k)
            out.append(it)
            gone.extend(r["id"] for r in run[1:])
    for s in seq:
        if run and s.get("pic") == run[-1].get("pic") and s.get("src") and run[-1].get("src") and s["f"][0] == run[-1]["f"][1] and \
                abs(float(s["src"][0]) - float(run[-1]["src"][1])) < 1.5 / float(l1.get("fps") or 30) and not s.get("treat") and not run[-1].get("treat"):
            run.append(s)
        else:
            flush()
            run = [s]
    flush()
    return out, gone


# ----------------------------------------------------------------- glimpse

def _cut_near(l1, f, reach=15):
    cs = K.cuts(l1)
    near = [c for c in cs if abs(c - f) <= reach]
    return min(near, key=lambda c: abs(c - f)) if near else None


def glimpse(l1, work, ask, sense_=None, pool=None):
    """1-2 frames of the incoming picture shown a few frames before the cut that brings it (a promise, then the cut).
    ask: at (a frame near the cut), frames (1-2), lead (frames between the glimpse and the cut, default 5)."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    a, _ = K.span(l1, args, default_s=0.2, least=1)
    g = min(max(int(args.get("frames", 2)), 1), 3)
    lead = min(max(int(args.get("lead", 5)), 2), 12)
    c = _cut_near(l1, a)
    if c is None:
        return {"skip": "no cut near to glimpse"}
    p = K.shot_at(l1, c - 1)
    q = K.shot_at(l1, c)
    if p is None or q is None or c - p["f"][0] < lead + g + 3:
        return {"skip": "the outgoing shot is too short to hold a glimpse"}
    s0 = c - lead - g
    pieces = [sense.slice_shot(p, p["f"][0], s0, p["id"]), sense.slice_shot(p, s0 + g, c, f"{p['id']}~g")]
    ins = dict(q, id=f"{args.get('id', 'gl')}.g", f=[s0, s0 + g])
    if q.get("src"):
        ins["src"] = [q["src"][0], round(float(q["src"][0]) + g / fps, 4)]
    if isinstance(q.get("crop"), list) and q["crop"] and isinstance(q["crop"][0], list):
        ins["crop"] = q["crop"][:g]
    for k in ("blur",):
        if isinstance(q.get(k), list):
            ins[k] = q[k][:g]
    return {"items": pieces + [ins], "drop": [p["id"]], "assets": {}, "events": [{"f": s0, "what": "glimpse", "kind": "glimpse", "strength": 0.3}],
            "log": {"cut": c, "at": s0, "frames": g}}


# ----------------------------------------------------------------- settle, snap

def _subject_point(c):
    return (c[0] + c[2]) / 2, c[1] + 0.40 * (c[3] - c[1])


def settle(l1, work, ask, sense_=None, pool=None):
    """A fast rigid push in (or pull out) whose speed peaks early, 9-35% of the way, and dies away: MM's moves on stills and
    archive, 5-12% in (pull-outs 20-24% on event footage). Laid on every base shot that starts inside [a, z).
    ask: at/until, dir ("in"|"out"), size, peak, frames (the move's length, default the shot's, at most 1 s)."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    a, z = K.span(l1, args, default_s=0.8, least=2)
    out_dir = str(args.get("dir", "in")) == "out"
    size = float(args.get("size", 0.22 if out_dir else 0.09))
    peak = float(args.get("peak", 0.18))
    items, log = [], []
    targets = [s for s in K.shots(l1) if a <= s["f"][0] < z] or [s for s in K.shots(l1) if s["f"][0] <= a < s["f"][1]]
    for sh in targets:
        if sh.get("pic") == "plate":
            continue
        pic = K.pic_of_asset(l1, work, sh.get("pic"))
        n = sh["f"][1] - sh["f"][0]
        cs = K.crops(l1, work, sh)
        start = max(a - sh["f"][0], 0) if sh["f"][0] < a else 0
        m = min(int(args.get("frames") or min(n - start, int(fps))), n - start)
        if m < 3:
            continue
        sz = size
        if pic:                                        # the tightest frame stays inside the taste ceiling
            w_min = min(c[2] - c[0] for c in cs)
            room = (w_min * pic.w) / (W / TASTE_UP)
            sz = min(sz, max(room - 1.0, 0.0))
        if sz < 0.02:
            continue
        k = K.decel(m, peak)
        new = []
        for i, c in enumerate(cs):
            j = i - start
            if j < 0:
                new.append(c)
                continue
            u = k[min(j, m - 1)]
            g = (1.0 + sz * (1.0 - u)) if out_dir else (1.0 + sz * u)
            new.append(K.r5(K.scale_crop(c, 1.0 / g, *_subject_point(c))))
        items.append(dict(sh, crop=new))
        log.append({"shot": sh["id"], "size": round(sz, 3), "frames": m})
    if not items:
        return {"skip": "no picture here has the pixels for a move"}
    return {"items": items, "drop": [], "assets": {}, "events": [{"f": it["f"][0], "what": "settle", "kind": "move", "strength": 0.25} for it in items],
            "log": {"moves": log, "peak": peak, "dir": "out" if out_dir else "in"}}


def snap(l1, work, ask, sense_=None, pool=None):
    """A snap zoom: on the stressed word the framing jumps 1.2-1.5x tighter on its subject, lands a hair past and settles in
    four frames; it holds (and, with back, cuts back out). ask: at (the word's frame), size (1.2-1.5), hold (frames), back."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    f, _ = K.span(l1, args, default_s=0.1, least=1)
    sh = K.shot_at(l1, f)
    if sh is None or sh.get("pic") == "plate":
        return {"skip": "no picture under the word"}
    lo, hi = sh["f"]
    if f - lo < 3:
        f = lo
    size = float(args.get("size", 1.3))
    pic = K.pic_of_asset(l1, work, sh.get("pic"))
    cs = K.crops(l1, work, sh)
    if pic:
        w_min = min(c[2] - c[0] for c in cs[f - lo:])
        size = min(size, (w_min * pic.w) / (W / TASTE_UP))
    if size < 1.12:
        return {"skip": "the picture has no pixels for a punch"}
    hold = int(args.get("hold") or (hi - f))
    e = min(f + max(hold, 4), hi)
    back = bool(args.get("back")) and e < hi
    pieces = []
    if f > lo:
        pieces.append(sense.slice_shot(sh, lo, f, sh["id"]))
    punch = sense.slice_shot(sh, f, e, sh["id"] if f == lo else f"{sh['id']}~s")
    over = [1.0 + 0.045 * math.exp(-j / 1.6) for j in range(e - f)]
    punch["crop"] = [K.r5(K.scale_crop(c, 1.0 / (size * over[j]), *_subject_point(c))) for j, c in enumerate(cs[f - lo:e - lo])]
    pieces.append(punch)
    if e < hi:
        rest = sense.slice_shot(sh, e, hi, f"{sh['id']}~t")
        if not back:
            rest["crop"] = [K.r5(K.scale_crop(c, 1.0 / size, *_subject_point(c))) for c in cs[e - lo:]]
        pieces.append(rest)
    return {"items": pieces, "drop": [sh["id"]], "assets": {}, "events": [{"f": f, "what": "snap zoom", "kind": "punch", "strength": 0.7}],
            "log": {"shot": sh["id"], "f": f, "size": round(size, 3), "until": e, "back": back}}


# ----------------------------------------------------------------- whip, streak

_ANG = {"right": 0.0, "left": math.pi, "down": math.pi / 2, "up": -math.pi / 2}


def whip(l1, work, ask, sense_=None, pool=None):
    """A whip pan carrying one shot into the next at a chosen join: the outgoing picture runs off along a line gathering
    speed, the incoming one arrives along it and brakes, both smeared by the shutter. With no room to travel in either
    picture, the whip is a zoom (the outgoing pushes in, the incoming arrives from closer). ask: at (near the join),
    frames (3-5; a streak is 2), travel (frame widths), ang (radians or left/right/up/down), kind ("pan"|"zoom")."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    f, _ = K.span(l1, args, default_s=0.1, least=1)
    c = _cut_near(l1, f)
    if c is None:
        return {"skip": "no join near to whip through"}
    p, q = K.shot_at(l1, c - 1), K.shot_at(l1, c)
    fr = int(args.get("frames", 4))
    if p is None or q is None or min(c - p["f"][0], q["f"][1] - c) < fr + 1:
        return {"skip": "a shot either side is shorter than the whip"}
    tr = float(args.get("travel", 0.27))
    cp, cq = K.crops(l1, work, p), K.crops(l1, work, q)
    ang = args.get("ang")
    ang = _ANG.get(ang, ang) if isinstance(ang, str) else ang
    kind = str(args.get("kind", "pan"))

    def room(cs, at):
        x0, y0, x1, y1 = cs[at]
        w, h = x1 - x0, y1 - y0
        return {0.0: (1 - x1) / w, math.pi: x0 / w, math.pi / 2: (1 - y1) / h, -math.pi / 2: y0 / h}
    if kind == "pan":
        rp, rq = room(cp, -1), room(cq, 0)
        opts = {k_: min(rp[k_], {0.0: rq[math.pi], math.pi: rq[0.0], math.pi / 2: rq[-math.pi / 2], -math.pi / 2: rq[math.pi / 2]}[k_]) for k_ in rp}
        if ang is None:
            ang = max(opts, key=lambda k_: opts[k_])
        best = opts.get(float(ang), max(opts.values()))
        if best < 0.10:
            kind = "zoom"
        else:
            tr = min(tr, best)
    np_, nq = list(cp), list(cq)
    for j in range(fr):
        out_k = ((j + 1) / fr) ** 2                   # leaves gathering speed, at full travel on its last frame
        in_k = (1 - j / fr) ** 2                      # arrives from full travel on its first frame, braking
        i_p = len(np_) - fr + j
        if kind == "zoom":
            np_[i_p] = K.r5(K.scale_crop(np_[i_p], 1.0 / (1.0 + 0.45 * out_k)))
            nq[j] = K.r5(K.scale_crop(nq[j], 1.0 / (1.0 + 0.45 * in_k)))
        else:
            dx, dy = math.cos(ang) * tr, math.sin(ang) * tr
            np_[i_p] = K.r5(K.shift_crop(np_[i_p], dx * out_k, dy * out_k))
            nq[j] = K.r5(K.shift_crop(nq[j], -dx * in_k, -dy * in_k))
    pi = dict(p, crop=np_, shutter=0.85)
    qi = dict(q, crop=nq, shutter=0.85)
    return {"items": [pi, qi], "drop": [], "assets": {}, "events": [{"f": c, "what": "whip", "kind": "whip", "strength": 0.6}],
            "log": {"join": c, "kind": kind, "frames": fr, "travel": round(tr, 3), "ang": None if kind == "zoom" else round(float(ang), 3)}}


def streak(l1, work, ask, sense_=None, pool=None):
    """One streaked frame either side of a join: a two-frame whip that travels far."""
    a = dict(ask)
    a.setdefault("frames", 2)
    a.setdefault("travel", 0.42)
    return whip(l1, work, a, sense_, pool)


# ----------------------------------------------------------------- shake

def shake(l1, work, ask, sense_=None, pool=None):
    """A jolt of the whole picture on an impact: 2-6 frames of crop jitter that dies inside them (and every card in front
    jolts with it). ask: at, frames (2-6), amp (frame widths, 0.012-0.03)."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    f, _ = K.span(l1, args, default_s=0.1, least=1)
    n = min(max(int(args.get("frames", 5)), 2), 6)
    amp = float(args.get("amp", 0.022))
    rng = K.rng_of(l1, args)
    dirs = []
    for j in range(n):
        ang = rng.uniform(0, 2 * math.pi) if j == 0 else dirs[-1][2] + math.pi + rng.uniform(-0.9, 0.9)
        k = math.exp(-j / 1.4)
        dirs.append((math.cos(ang) * amp * k, math.sin(ang) * amp * k * W / H, ang))
    dirs.append((0.0, 0.0, 0.0))
    items = []
    for sh in K.shots(l1):
        lo, hi = sh["f"]
        if hi <= f or lo >= f + n or sh.get("pic") == "plate":
            continue
        cs = K.crops(l1, work, sh)
        new = list(cs)
        for j in range(n):
            g = f + j
            if lo <= g < hi:
                c = cs[g - lo]
                c = K.scale_crop(c, 1.0 / (1.0 + 2.2 * amp))       # a little room to move in, for the jolt only
                new[g - lo] = K.r5(K.shift_crop(c, dirs[j][0], dirs[j][1]))      # one crop width is one frame width
        items.append(dict(sh, crop=new))
    for cd in (i for i in l1.get("items") or [] if i.get("k") == "card"):
        lo, hi = cd["f"]
        if hi <= f or lo >= f + n:
            continue
        m = hi - lo
        xs = list(cd["x"]) if isinstance(cd.get("x"), list) else [float(cd.get("x", 0.5))] * m
        ys = list(cd["y"]) if isinstance(cd.get("y"), list) else [float(cd.get("y", 0.5))] * m
        for j in range(n):
            g = f + j
            if lo <= g < hi:
                xs[g - lo] = round(xs[g - lo] - dirs[j][0], 5)
                ys[g - lo] = round(ys[g - lo] - dirs[j][1], 5)
        items.append(dict(cd, x=xs, y=ys))
    if not items:
        return {"skip": "nothing on screen to shake"}
    return {"items": items, "drop": [], "assets": {}, "events": [{"f": f, "what": "shake", "kind": "impact", "strength": 0.9}],
            "log": {"f": f, "frames": n, "amp": amp, "items": len(items)}}


# ----------------------------------------------------------------- speed ramp, freeze

def ramp_rates(n, low=0.33, shape="dip"):
    """A rate for each of n frames: 1.0 easing down to ``low`` and back up (dip), or down and staying (into)."""
    out = []
    for i in range(n):
        u = i / max(n - 1, 1)
        if shape == "into":
            w = 0.5 - 0.5 * math.cos(math.pi * min(u / 0.45, 1.0))
        else:
            w = math.sin(math.pi * u) ** 2 if u < 1 else 0.0
        out.append(1.0 - (1.0 - low) * w)
    return out


def ramp(l1, work, ask, sense_=None, pool=None):
    """A speed ramp on a clip: real speed easing into slow motion (0.25-0.4) on the moment and back, as sub-shots whose
    clocks each run at their piece of the curve (the plan's own linear src mapping, piece by piece). The take is never
    ramped (its picture keeps the voice's sync). ask: at/until, low, shape ("dip"|"into"), piece (frames a piece, 3)."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    a, z = K.span(l1, args, default_s=1.2, least=6)
    sh = next((s for s in K.shots(l1) if s["f"][0] < z and s["f"][1] > a and s.get("src") and not K.is_take(s.get("pic"))
               and (l1.get("assets") or {}).get(s.get("pic"), {}).get("kind") == "video"), None)
    if sh is None:
        return {"skip": "no clip under the moment to ramp"}
    lo, hi = sh["f"]
    a, z = max(a, lo), min(z, hi)
    if z - a < 6:
        return {"skip": "the clip's stretch is too short to ramp"}
    low = min(max(float(args.get("low", 0.33)), 0.2), 0.6)
    piece = min(max(int(args.get("piece", 3)), 2), 6)
    rates = ramp_rates(z - a, low, str(args.get("shape", "dip")))
    s0, s1 = float(sh["src"][0]), float(sh["src"][1])
    rate0 = (s1 - s0) / max((hi - lo) / fps, 1e-6)          # the clip's own speed in this shot (1.0 is real time)
    pic = K.pic_of_asset(l1, work, sh["pic"])
    end_s = (pic.dur - 1.0 / fps) if pic and pic.dur else s1
    pieces = []
    if a > lo:
        pieces.append(sense.slice_shot(sh, lo, a, sh["id"]))
    t = s0 + rate0 * (a - lo) / fps
    cs = K.crops(l1, work, sh)
    k = 0
    for p0 in range(a, z, piece):
        p1 = min(p0 + piece, z)
        r = sum(rates[p0 - a:p1 - a]) / (p1 - p0) * rate0
        t1 = min(t + r * (p1 - p0) / fps, end_s)
        it = dict(sense.slice_shot(sh, p0, p1, sh["id"] if (p0 == lo) else f"{sh['id']}~r{k}"), src=[round(t, 5), round(max(t1, t), 5)])
        it["crop"] = cs[p0 - lo:p1 - lo]
        pieces.append(it)
        t, k = max(t1, t), k + 1
    if z < hi:
        rest = sense.slice_shot(sh, z, hi, f"{sh['id']}~rz")
        t1 = min(t + rate0 * (hi - z) / fps, end_s)
        rest["src"] = [round(t, 5), round(max(t1, t), 5)]
        pieces.append(rest)
    return {"items": pieces, "drop": [sh["id"]], "assets": {}, "events": [{"f": a, "what": "speed ramp", "kind": "slow", "strength": 0.5}],
            "log": {"shot": sh["id"], "f": [a, z], "low": low, "pieces": k}}


def freeze(l1, work, ask, sense_=None, pool=None):
    """A freeze frame: the picture stops on the moment and the camera keeps pushing (4%); then it runs again on its own
    clock (the take keeps the voice's sync). ask: at, frames (default 18), push."""
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    f, _ = K.span(l1, args, default_s=0.1, least=1)
    sh = K.shot_at(l1, f)
    if sh is None or not sh.get("src"):
        return {"skip": "no moving picture under the moment to freeze"}
    lo, hi = sh["f"]
    n = min(int(args.get("frames", 18)), hi - f)
    if n < 4:
        return {"skip": "the shot ends before a freeze could hold"}
    push = float(args.get("push", 0.04))
    cs = K.crops(l1, work, sh)
    t = sense.src_time(sh, f, fps)
    pieces = []
    if f > lo:
        pieces.append(sense.slice_shot(sh, lo, f, sh["id"]))
    fr = sense.slice_shot(sh, f, f + n, sh["id"] if f == lo else f"{sh['id']}~f")
    fr["src"] = [round(t, 5), round(t, 5)]
    k = K.decel(n, 0.12)
    fr["crop"] = [K.r5(K.scale_crop(cs[f - lo], 1.0 / (1.0 + push * k[j]), *_subject_point(cs[f - lo]))) for j in range(n)]
    pieces.append(fr)
    if f + n < hi:
        pieces.append(sense.slice_shot(sh, f + n, hi, f"{sh['id']}~fz"))
    return {"items": pieces, "drop": [sh["id"]], "assets": {}, "events": [{"f": f, "what": "freeze", "kind": "freeze", "strength": 0.6}],
            "log": {"shot": sh["id"], "f": f, "frames": n}}


ALIASES = {"list_burst": "burst", "strobe": "burst", "montage": "burst", "strobe_montage": "burst", "pace": "contour",
           "pace_contour": "contour", "punch": "snap", "punch_in": "snap", "snap_zoom": "snap", "push": "settle", "pull": "settle",
           "push_in": "settle", "pull_out": "settle", "whip_pan": "whip", "smear": "streak", "slowmo": "ramp", "slow_motion": "ramp",
           "speed_ramp": "ramp", "freeze_frame": "freeze", "screen_shake": "shake", "jolt": "shake", "glimpse_cut": "glimpse"}

DEVICES = {
    "burst": {"plan": burst, "layer": "rhythm", "needs": ("pictures",), "fallback": ("contour", "snap"), "span": True,
              "card": "burst at until pics[] frames land mono order. A list burst: 8-45 related pictures at 2-5 frames each, the last one held; for figures, enumerations, the hook"},
    "contour": {"plan": contour, "layer": "rhythm", "needs": (), "fallback": ("snap",), "span": True,
                "words": ("sections[].at", "sections[].f", "sections[].until", "stress[]"),
                "card": "contour sections[{at:[first last] energy:strobe|brisk|hold}] stress[]. The pace contour: strobe stretches of 2-8 frame shots against brisk ones and holds; lists and the hook strobe, the turn and payoff hold"},
    "glimpse": {"plan": glimpse, "layer": "surface", "needs": ("cut",), "fallback": ("whip",),
                "card": "glimpse at frames lead. 1-2 frames of the incoming picture shown just before its cut: a promise, then the cut"},
    "settle": {"plan": settle, "layer": "surface", "needs": ("picture",), "fallback": (), "span": True,
               "card": "settle at until dir:in|out size peak. A fast rigid push in (5-12%) or pull out (20%) whose speed peaks early and dies; almost every still and archive picture"},
    "snap": {"plan": snap, "layer": "surface", "needs": ("picture",), "fallback": ("settle",),
             "card": "snap at size hold back. A snap zoom: the framing jumps 1.2-1.5x tighter on the stressed word, lands past and settles; the hook, the drop"},
    "whip": {"plan": whip, "layer": "surface", "needs": ("cut",), "fallback": ("glimpse", "snap"),
             "card": "whip at frames travel ang kind:pan|zoom. A whip pan through a join, both sides smeared; jumps in place or time, section joins, energy rises"},
    "streak": {"plan": streak, "layer": "surface", "needs": ("cut",), "fallback": ("whip",),
               "card": "streak at. One streaked frame either side of a join"},
    "shake": {"plan": shake, "layer": "look", "needs": (), "fallback": ("snap",),
              "card": "shake at frames amp. A 2-6 frame jolt of the whole picture on an impact word or a hit; rare"},
    "ramp": {"plan": ramp, "layer": "surface", "needs": ("clip",), "fallback": ("freeze", "settle"), "span": True,
             "card": "ramp at until low shape:dip|into. A speed ramp on a clip: real speed easing into slow motion on the moment and back; the payoff, an athletic or emotional peak"},
    "freeze": {"plan": freeze, "layer": "surface", "needs": ("clip",), "fallback": ("snap",),
               "card": "freeze at frames push. The picture stops on the moment while the camera keeps pushing, then runs again"},
}
