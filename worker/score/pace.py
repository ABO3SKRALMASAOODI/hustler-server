"""The judge's pace on found pictures (lane K round six, 14 Nov 2026).

The rated round-five films cut 6.1 times in 10 s with a median shot of 1.06 s; the judge (@metromedia.house,
judge/metromedia/TEARDOWN.md rows 1, 5 and 11) cuts 16.1 times with a median of 0.49 s, moves the camera on most
pictures (4.3 moves in 10 s, 13.9% a second, fast in and easing out) and opens 28% of its shots on a soft frame.
A found picture arrived here as one still, held a second or more, most of them small on a dark page.

Three things, each on a picture the Bin already checked, none of them a new device and none a model call:

  * `parts`: a found picture held past SPLIT_F frames is cut inside itself, the way an editor cuts a photograph or a
    clip he has only one of: a still to a second and third framing of itself (closer, on its subject), a clip to a
    later second of itself. The first part keeps the shot's id, so every line that answers the shot still finds it.
  * `still_path`: every found still is a camera move for its whole length on the judge's curve (speed peaks in the
    first third): a push toward its subject, a pull back from it, or a travel across a wide photograph. A wide
    photograph fills the frame and is travelled, where round five set it small on a page.
  * `soft_in`: the first picture after the person opens on a soft frame and is sharp by its fourth.

What it never does: cut a head a picture shows (the framing holds every head whole, with hair and chin), go past
the pixels a still has, or touch the take (score.move eases the take). VALMERA_PACE=0 turns all of it off.
"""
import math
import os

SPLIT_F = 26               # a found picture held this many frames or more is cut inside itself (0.87 s at 30 fps)
PART_F = 16                # about this long each part: the judge's median shot is 0.49 s
PARTS_MOST = 4
STILL_ZOOM = (1.24, 1.5)   # a second framing of a still is this much closer than the first
CLIP_JUMP_S = 0.7          # a clip cut inside itself jumps at least this far on
PUSH = (0.07, 0.12)        # one move on a still: the judge zooms 8.8% inside one shot
TRAVEL = 0.34              # a travel crosses at most this share of the window's own width
SOFT_F = 3                 # frames a soft start takes to come sharp
EDGE = 0.01                # a head keeps this much of the source between itself and the frame's edge


def on():
    return os.environ.get("VALMERA_PACE", "1") != "0"


def punch_on():
    return os.environ.get("VALMERA_PUNCH", "1") != "0"


def _curve(n, kind="out"):
    """n positions 0..1 of one move: fast in and slow to rest, the judge's top curve."""
    if n <= 1:
        return [1.0] * max(n, 0)
    out = []
    for i in range(n):
        t = i / (n - 1)
        out.append(1 - (1 - t) ** 2.4 if kind == "out" else t * t * (3 - 2 * t))
    return out


def _clamp(x, a, b):
    return max(a, min(b, x))


def faces_of(ctx, key):
    """Head boxes a Bin still holds, in source fractions: each face grown to its hair and chin."""
    row = ctx.bin.get(str(key).split(":", 1)[-1]) or {}
    out = []
    for s in row.get("seen") or []:
        for f in s.get("faces") or []:
            if len(f) >= 4 and (f[2] - f[0]) * (f[3] - f[1]) > 0.004:
                w, h = f[2] - f[0], f[3] - f[1]
                out.append([_clamp(f[0] - 0.18 * w, 0, 1), _clamp(f[1] - 0.42 * h, 0, 1), _clamp(f[2] + 0.18 * w, 0, 1), _clamp(f[3] + 0.22 * h, 0, 1)])
    return out


def designed(ctx, key):
    """The picture is one Valmera drew from the film's own words (a number, a quoted phrase): type, not a photograph.
    It is shown whole. Round seven: framed "on its subject" and cut inside itself like a found still, two of p5555's
    were pushed past both edges of the frame with their words cut off."""
    row = ctx.bin.get(str(key).split(":", 1)[-1]) or {}
    return bool(row.get("design")) or row.get("provider") == "designed"


def focus_of(ctx, key, asset):
    """Where a still's subject is, (x, y) in source fractions: its largest head, or the centre of its detail (where
    the picture has edges and colour, read on a thumbnail), or the centre. Cached on the compile."""
    cache = ctx.__dict__.setdefault("_pace_focus", {})
    if key in cache:
        return cache[key]
    heads = faces_of(ctx, key)
    if heads:
        f = max(heads, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        cache[key] = ((f[0] + f[2]) / 2, (f[1] + f[3]) / 2)
        return cache[key]
    at = (0.5, 0.5)
    try:
        import numpy as np
        from PIL import Image
        with Image.open(os.path.join(ctx.work or "", asset["path"])) as im:
            im = im.convert("RGB").resize((96, max(8, int(96 * im.size[1] / max(im.size[0], 1)))))
        a = np.asarray(im, np.float32)
        g = a.mean(axis=2)
        e = np.abs(np.diff(g, axis=1))[:-1, :] + np.abs(np.diff(g, axis=0))[:, :-1]
        e = e + 0.5 * (a.max(axis=2) - a.min(axis=2))[:-1, :-1]
        e = np.clip(e - np.percentile(e, 60), 0, None)
        tot = float(e.sum())
        if tot > 1e-3:
            ys, xs = np.mgrid[0:e.shape[0], 0:e.shape[1]]
            at = (_clamp(float((e * xs).sum() / tot) / e.shape[1], 0.2, 0.8), _clamp(float((e * ys).sum() / tot) / e.shape[0], 0.25, 0.75))
    except Exception:  # noqa: BLE001 - a picture unread is framed on its centre
        at = (0.5, 0.5)
    cache[key] = at
    return at


def cover(ctx, key, asset, W, H, zoom=1.0, at=None):
    """The window of a still that fills the frame, on its subject: source fractions. Every head stays whole when the
    window can hold them; closer than the pixels allow is never asked for (the caller limits the upscale)."""
    sw, sh = float(asset.get("w") or W), float(asset.get("h") or H)
    target = (W / H) * (sh / sw)                  # window width over height, in source fractions
    w, h = (1.0, 1.0 / target) if target >= 1.0 else (target, 1.0)
    if h > 1.0:
        w, h = w / h, 1.0
    w, h = w / zoom, h / zoom
    fx, fy = at or focus_of(ctx, key, asset)
    heads = faces_of(ctx, key)
    if heads:                                     # every head inside, or at least the largest
        x0, y0 = min(r[0] for r in heads), min(r[1] for r in heads)
        x1, y1 = max(r[2] for r in heads), max(r[3] for r in heads)
        if x1 - x0 > w - 2 * EDGE or y1 - y0 > h - 2 * EDGE:
            f = max(heads, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
            x0, y0, x1, y1 = f
        if x1 - x0 <= w - 2 * EDGE:
            fx = _clamp(fx, x1 + EDGE - w / 2, x0 - EDGE + w / 2)
        if y1 - y0 <= h - 2 * EDGE:
            fy = _clamp(fy, y1 + EDGE - h / 2, y0 - EDGE + h / 2)
    cx, cy = _clamp(fx, w / 2, 1 - w / 2), _clamp(fy, h / 2, 1 - h / 2)
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def zoom_room(ctx, key, asset, W, H):
    """How much closer than its cover window a still may go: the pixels it has (2.2x upscale at most), and its heads
    (the largest stays whole with air round it)."""
    base = cover(ctx, key, asset, W, H)
    px = (base[2] - base[0]) * float(asset.get("w") or W)
    room = max(1.0, px / (W / 2.2))
    for f in faces_of(ctx, key):
        room = min(room, max(1.0, min((base[2] - base[0]) / max(f[2] - f[0] + 2 * EDGE, 1e-3), (base[3] - base[1]) / max(f[3] - f[1] + 2 * EDGE, 1e-3))))
    return room


def still_path(ctx, key, asset, W, H, n, rng, part=0, written=None):
    """Per-frame windows of one still shot: a camera move for its whole length. `written` is a rect the Score gave
    (a crop): the move stays inside it. part: which framing of this still this is (0 the first)."""
    room = zoom_room(ctx, key, asset, W, H)
    if designed(ctx, key) and written is None:
        # type drawn for this frame: whole, on its centre, breathing by a hundredth and a half so it is never a still frame
        base = cover(ctx, key, asset, W, H, 1.0, (0.5, 0.5))
        if n <= 2:
            return base
        w, h = base[2] - base[0], base[3] - base[1]
        cx, cy = (base[0] + base[2]) / 2, (base[1] + base[3]) / 2
        return [[cx - w / z / 2, cy - h / z / 2, cx + w / z / 2, cy + h / z / 2] for z in (1.0 + 0.015 * (1.0 - p) for p in _curve(n))]
    if written is not None:
        base, room = list(written), 1.0 + min(0.1, max(0.0, room - 1.0))
    else:
        z0 = 1.0
        if part:
            lo, hi = STILL_ZOOM
            z0 = min(room / (1 + PUSH[0]), rng.uniform(lo, hi) * (1.0 if part == 1 else 0.9))
            z0 = max(z0, 1.0)
        at = None
        if part >= 2 and not faces_of(ctx, key):          # a third framing looks somewhere else in the picture
            fx, fy = focus_of(ctx, key, asset)
            at = (_clamp(fx + rng.choice((-1, 1)) * 0.16, 0.1, 0.9), _clamp(fy + rng.uniform(-0.08, 0.08), 0.1, 0.9))
        base = cover(ctx, key, asset, W, H, z0, at)
        room = room / z0
    if n <= 2:
        return base
    w, h = base[2] - base[0], base[3] - base[1]
    cx, cy = (base[0] + base[2]) / 2, (base[1] + base[3]) / 2
    amt = min(rng.uniform(*PUSH), max(0.0, room - 1.0))
    slack_l, slack_r = base[0], 1.0 - base[2]
    wide = written is None and part == 0 and (slack_l + slack_r) > 0.6 * w and not faces_of(ctx, key)
    kinds = ["in", "out"] if amt >= 0.03 else []
    if wide or (amt < 0.03 and slack_l + slack_r > 0.12 * w):
        kinds = ["travel"]
    if not kinds:
        return base
    kind = kinds[(part + rng.randrange(2)) % len(kinds)]
    cur = _curve(n)
    out = []
    if kind == "travel":
        d = min(TRAVEL * w, slack_l + slack_r)
        sign = rng.choice((-1, 1))
        a = _clamp(cx - sign * d / 2, w / 2, 1 - w / 2)
        b = _clamp(cx + sign * d / 2, w / 2, 1 - w / 2)
        zin = 1.0 + min(amt, 0.05)
        for p in cur:
            x = a + (b - a) * p
            z = 1.0 + (zin - 1.0) * p
            out.append([x - w / z / 2, cy - h / z / 2, x + w / z / 2, cy + h / z / 2])
        return out
    for p in cur:
        z = 1.0 + amt * (p if kind == "in" else 1.0 - p)
        out.append([cx - w / z / 2, cy - h / z / 2, cx + w / z / 2, cy + h / z / 2])
    return out


PUNCH_SPLIT_F = 24         # a stretch of the take nobody speaks over, held 0.8 s or more, is cut inside itself
PUNCH_F = 18               # about this long each framing (0.6 s): a product teaser's own pace
PUNCH_MOST = 5
PUNCH_MIN_F = 9
# Under a voice (round seven, 14 Nov: the rated films held one framing of a talking take 14.5 s, and 3.5 s twice in the
# best of them; the judge passes 2 s on one shot in forty). A stretch of the take somebody speaks over is cut inside
# itself where a phrase starts: the word after a breath, or a word the voice stresses. Never mid-word, never two cuts
# closer than a framing can be read, and never back to the framing just left (score.move sets the framings).
TALK_MAX_F = 62            # a framing of a talking take holds this long at most (2.07 s)
TALK_F = 46                # and about this long (1.5 s): the take is the slowest picture a film has, the face needs its line
TALK_MIN_F = 26            # never two cuts closer than this (0.87 s)
TALK_TAIL_F = 14           # what a cut leaves after itself in its shot, or the cut waits for the next shot


def spoken(ctx, a, z):
    """A word of the upload is on the film between programme frames a and z."""
    ends = getattr(ctx, "wfe_any", None) or {}
    for w, f in (getattr(ctx, "wf_any", None) or {}).items():
        if f < z and ends.get(w, f + 1) > a:
            return True
    return False


def _counts(b):
    """The beat runs a counter: one gesture that stands in one place over the take for the whole count."""
    do = (getattr(b, "raw", None) or {}).get("do")
    do = do if isinstance(do, list) else [do] if do else []
    return any((g.get("g") if isinstance(g, dict) else g) == "counter" for g in do)


def take_parts(ctx, b, sid, ref, d, at, rng):
    """A stretch of the take with no word on it as the framings it is cut into: [(id, ref, frames)], each part
    marked `_punch` (0 the first). The upload's own moment, re-framed: the cut falls on the upload's own sound where
    it has an onset there, and score.move sets each part's framing (closer on what the frame must keep whole, never
    back to the framing just left). Under a voice the take is never punched: it is eased (score.move)."""
    one = [(sid, ref, d)]
    z = ref.get("zoom")
    plain = not (ref.get("asset") != "main" or ref.get("_view") is not None or ref.get("crop") is not None or ref.get("cam")
                 or ref.get("_cam") or ref.get("rate") or ref.get("step") or ref.get("k") is not None or ref.get("treat")
                 or (isinstance(z, (int, float)) and not isinstance(z, bool) and z > 1) or _counts(b))
    if plain and ref.get("sync") and spoken(ctx, at, at + d) and talk_on():
        return talk_parts(ctx, sid, ref, d, at, rng)
    ctx.__dict__.pop("_talk", None)              # anything else on screen ends a talking stretch
    if d < PUNCH_SPLIT_F or not plain or spoken(ctx, at, at + d):
        return one
    from . import compiler
    n = int(min(PUNCH_MOST, max(2, round(d / PUNCH_F))))
    cuts = []
    try:
        from . import move
        ons = sorted({f - at for f, _s in move.stress_frames(ctx) if PUNCH_MIN_F <= f - at <= d - PUNCH_MIN_F})
    except Exception:  # noqa: BLE001 - no sound read: an even pulse
        ons = []
    for f in ons:                                  # the upload's own onsets first, never two closer than a framing can be read
        if all(abs(f - c) >= PUNCH_MIN_F for c in cuts) and len(cuts) < n - 1:
            cuts.append(f)
    if len(cuts) < n - 1:                          # the rest on an even pulse, in the longest stretch left
        for _ in range(n - 1 - len(cuts)):
            edges = [0] + sorted(cuts) + [d]
            a, b_ = max(zip(edges, edges[1:]), key=lambda e: e[1] - e[0])
            if b_ - a < 2 * PUNCH_MIN_F + 4:
                break
            cuts.append(int(a + (b_ - a) * rng.uniform(0.42, 0.58)))
    if not cuts:
        return one
    edges = [0] + sorted(cuts) + [d]
    lens = [y - x for x, y in zip(edges, edges[1:])]
    return [(sid if k == 0 else f"{sid}x{k}", dict(ref, _punch=k), ln) for k, ln in enumerate(lens)]


def talk_on():
    return os.environ.get("VALMERA_TALK_CUTS", "1") != "0"


def phrase_starts(ctx, a, z):
    """Programme frames in (a, z) where a phrase starts: a word that follows a breath of three frames or more, or one
    the voice stresses (the measured stress that prints the * marks). The strength of each, 0..1."""
    ends = getattr(ctx, "wfe_any", None) or {}
    rows = sorted((int(f), w) for w, f in (getattr(ctx, "wf_any", None) or {}).items())
    stress = getattr(ctx, "stress", None) or {}
    out, prev_end = [], None
    for f, w in rows:
        try:
            st = float(stress.get(w, stress.get(str(w), 0.0)) or 0.0)
        except (TypeError, ValueError):
            st = 0.0
        gap = f - prev_end if prev_end is not None else 0
        if a < f < z and (gap >= 3 or st >= 0.6):
            out.append((f, min(1.0, 0.25 * min(gap, 12) / 3.0 * 0.6 + st / 2.5)))
        prev_end = max(prev_end or 0, int(ends.get(w, f + 1)))
    return out


def talk_parts(ctx, sid, ref, d, at, rng):
    """A stretch of the take somebody speaks over as the framings it is cut into: [(id, ref, frames)]. The stretch is
    read across the film's beats: a take that runs straight on from the shot before carries how long its framing has
    already held, so a run of short sentences is cut as one stretch, and a cut may fall on the shot's own first frame
    (a sentence starts there). A part marked `_punch` is a framing of its own; `_talk` says the cut is the voice's.
    A stretch that never passes TALK_MAX_F stays one shot, eased by score.move as before."""
    one = [(sid, ref, d)]
    st = ctx.__dict__.get("_talk")
    cont = bool(st) and st.get("end") == at
    since = int(st["since"]) if cont else 0
    k = int(st["k"]) if cont else 0
    marked = bool(st.get("marked")) if cont else False
    cuts = []
    if since + d > TALK_MAX_F:
        cand = phrase_starts(ctx, at, at + d)
        if cont and since >= TALK_MIN_F:
            cand = [(at, 0.6)] + cand                  # the shot's own first frame: a sentence starts on it
        pos = at - since                               # where the framing on screen began
        while True:
            lo = max(pos + TALK_MIN_F, at)             # a framing that has already held past its time is cut at the first phrase from here
            hi = max(pos + TALK_MAX_F, lo + TALK_MIN_F)
            pool = [(f, s) for f, s in cand if lo <= f <= hi and (at + d - f) >= TALK_TAIL_F and (f == at or f - at >= 2)]
            if not pool:
                if at + d - pos <= TALK_MAX_F or hi >= at + d:
                    break
                # no phrase starts in reach: the nearest word start to the pulse, or the stretch runs on to its next shot
                words = sorted(int(f) for f in (getattr(ctx, "wf_any", None) or {}).values() if lo <= f <= hi and at < f and at + d - f >= TALK_TAIL_F)
                if not words:
                    break
                want = pos + TALK_F
                pool = [(min(words, key=lambda f: abs(f - want)), 0.1)]
            want = pos + TALK_F + int(rng.uniform(-6, 6))
            f = max(pool, key=lambda c: (c[1] - abs(c[0] - want) / float(TALK_MAX_F), -c[0]))[0]
            cuts.append(f - at)
            pos = f
    if not cuts:
        ctx._talk = {"end": at + d, "since": since + d, "k": k, "marked": marked}
        return [(sid, dict(ref, _punch=k, _talk=True), d)] if marked else one
    out, edges = [], sorted(set(cuts))
    bounds = [0] + [c for c in edges if c > 0] + [d]
    if edges[0] == 0:
        k += 1                                         # the cut falls on this shot's first frame
    for j, (x, y) in enumerate(zip(bounds, bounds[1:])):
        if j:
            k += 1
        out.append((sid if j == 0 else f"{sid}x{j}", dict(ref, _punch=k, _talk=True), y - x))
    ctx._talk = {"end": at + d, "since": d - bounds[-2], "k": k, "marked": True}
    return out


def parts(ctx, b, sid, ref, d, rng, at=None):
    """One planned shot of a found picture as the shots it is cut into: [(id, ref, frames)]. The first keeps the
    shot's id. A shot too short to cut, a plate, a picture the Score framed or moved itself, and a clip with no
    later second to jump to come back as they are. The take is cut only where nobody speaks (take_parts)."""
    one = [(sid, ref, d)]
    if on() and at is not None and ref.get("kind") == "main" and punch_on():
        return take_parts(ctx, b, sid, ref, d, at, rng)
    ctx.__dict__.pop("_talk", None)
    if not on() or d < SPLIT_F or ref.get("kind") != "asset" or ref.get("crop") is not None or ref.get("cam") or ref.get("rate"):
        return one
    from . import compiler
    asset = compiler.asset_for(ctx, ref["asset"])
    if not asset:
        return one
    n = int(min(PARTS_MOST, max(2, round(d / PART_F))))
    if asset.get("kind") == "video":
        need = d / ctx.fps
        dur = float(asset.get("dur") or 0.0)
        start = float(ref["t0"]) if ref.get("t0") is not None else ctx.cursor.get(ref["asset"])
        if start is None:
            start = 0.0
        while n > 1 and start + need + CLIP_JUMP_S * (n - 1) > dur:
            n -= 1
        if n <= 1:
            if ref.get("t0") is None and dur >= need + CLIP_JUMP_S:        # room for one jump when the clip starts at its head
                n, start = 2, 0.0
            else:
                return one
        lens = compiler.jitter_split(d, n, rng)
        jump = min(1.6, (dur - start - need) / (n - 1))
        out, t = [], start
        for k, ln in enumerate(lens):
            out.append((sid if k == 0 else f"{sid}x{k}", dict(ref, t0=round(t, 3), _part=k), ln))
            t += ln / ctx.fps + jump
        return out
    if designed(ctx, ref["asset"]):
        return one                                 # type is read whole, once: never cut inside itself
    if zoom_room(ctx, ref["asset"], asset, ctx.W, ctx.H) < 1.0 + PUSH[0] + 0.1:
        n = min(n, 2) if (asset.get("w") or 0) / max(asset.get("h") or 1, 1) > 1.2 * ctx.W / ctx.H else 1      # a small still: a travel has two halves, a push has one
    if n <= 1:
        return one
    lens = compiler.jitter_split(d, n, rng)
    return [(sid if k == 0 else f"{sid}x{k}", dict(ref, _part=k), ln) for k, ln in enumerate(lens)]


def parent(sid):
    """The id of the shot a part belongs to: `b03.s1x2` is a part of `b03.s1`."""
    head, _, tail = str(sid).rpartition("x")
    return head if head and tail.isdigit() and "." in head else str(sid)


def soft_in(ctx):
    """Marks the shots that open soft: a found still that follows the person (the cut from the take to a picture), one
    in three of them at most, never two in a row. The mark is `soft` frames on the L1 shot; the lowering draws it."""
    if not on():
        return 0
    shots = sorted((s for s in ctx.shots if s.get("k") == "shot"), key=lambda s: s["f"][0])
    marked, last = 0, -9
    for i, s in enumerate(shots):
        if i == 0 or s["f"][1] - s["f"][0] < 3 * SOFT_F:
            continue
        a = ctx.assets.get(s.get("pic")) or {}
        prev = shots[i - 1]
        if a.get("kind") == "image" and str(s.get("pic")).startswith("bin:") and prev.get("pic") == "main" and i - last >= 3:
            s["soft"] = SOFT_F
            marked, last = marked + 1, i
    return marked


def report(ctx):
    shots = [s for s in ctx.shots if s.get("k") == "shot"]
    cut = sum(1 for s in shots if parent(s["id"]) != s["id"] and s.get("punch") is None)
    punched = sum(1 for s in shots if s.get("punch"))
    moving = sum(1 for s in shots if isinstance(s.get("crop"), list) and s["crop"] and isinstance(s["crop"][0], list) and (ctx.assets.get(s.get("pic")) or {}).get("kind") == "image")
    return {"parts_added": cut, "take_punches": punched, "stills_moving": moving, "soft_starts": sum(1 for s in shots if s.get("soft"))}


def _unused(*_):       # keeps math imported for callers that extend the curves
    return math.pi
