"""The take is never still (lane K round five, 13 Nov 2026).

The owner, 13 Nov: "a picture or framing that holds for a long time is a defect"; the cringe of the week before was
bad execution, not speed. The films that went out since held one framing of a selfie 12 s at a time. In the
references a take is never punched with a hard cut (0 hard punches in 67 s of podcast clips and 910 s of long
form); it is eased: 4.2 moves per 10 s of 4.3% each over about 14 frames, fast in and slow to rest, and the frame
is moving three frames in four (week-1112/notes/VIS.md, sections 2 and 2.1). The judge's reels carry 4.3 camera
moves per 10 s (judge/metromedia/TEARDOWN.md, row 5).

This pass gives every stretch of the upload that pace, after the governor has settled the film and before the L1
is written. It writes no cut and no device: it turns each run of the take into one continuous camera track.

  * On the words the voice stresses most (the same measured stress that prints the * marks), the camera eases a
    step in, on the references' own curve (scene.bake.MOVE "out": speed peaks in the first third). Up to three
    steps in a row or as far as the picture allows, then it eases back out further than one step.
  * Between moves it keeps a slow push, so no frame repeats the one before.
  * Where the take comes back from a cutaway it does not come back to the framing it left.
  * With no voice the moves fall on the onsets of the upload's own sound, or on an even pulse.

What it never does: cut a head (every head seen in the run stays whole, with the hair and the chin), cut through a
word the upload prints, pass the upscale the source can hold, let a face fill more of the frame than the governor
allows, or push a head onto the caption seat (the zoom is anchored on the side of the head that faces the
captions, so the head grows away from them). A run that cannot move cleanly stays as shot.

It reads no meaning and calls no model. VALMERA_EASE=0 turns it off.
"""
import math
import os
import random

MOVE_IN = (0.045, 0.075)      # one eased step in: the references' eased moves on a take run 4 to 9%
MOVE_FRAMES = (14, 18)        # frames at 30 fps: in on the "out" curve, back on the "io" curve (VIS 2.1)
HOLD_S = (0.9, 2.6)           # a framing holds at least and at most this long (VIS: the podcast band)
PUSH_RATE = 0.012             # the slow push between moves, scale per second
RUN_MIN_S = 0.8               # a run shorter than this is a glimpse: it keeps one framing, pushed
TOTAL_MAX = 1.18              # the tightest a run ever gets against the framing the film gave it: three steps in, no more
RETURN_IN = 1.07              # a take that comes back from a cutaway comes back this much closer, or wide again
STRESS_MIN = 0.6              # measured stress (z) a word needs to lead a move
EDGE = 0.012                  # a head keeps this much of the frame between itself and the edge


def on():
    return os.environ.get("VALMERA_EASE", "1") != "0"


def _curve(kind, n):
    """n positions 0..1 of one eased move. The measured camera curves when the engine is in this build."""
    try:
        from scene import bake
        return bake.move(n, 0.0, 1.0, kind=kind, frames=n)
    except Exception:  # noqa: BLE001 - no engine here: the same shapes by formula
        out = []
        for i in range(n):
            t = i / max(n - 1, 1)
            out.append(1 - (1 - t) ** 2.4 if kind == "out" else t * t * (3 - 2 * t))
        return out


def _rects(crop, n):
    if isinstance(crop[0], (list, tuple)):
        rows = [list(c) for c in crop]
        return (rows + [rows[-1]] * n)[:n]
    return [list(crop)] * n


def _zoom(c, z, ax, ay):
    """Rect c seen z times closer, the point at frame fraction (ax, ay) staying where it is."""
    w, h = c[2] - c[0], c[3] - c[1]
    px, py = c[0] + ax * w, c[1] + ay * h
    w2, h2 = w / z, h / z
    x0, y0 = px - ax * w2, py - ay * h2
    return [x0, y0, x0 + w2, y0 + h2]


def _cap(c, marks, ax, ay):
    """How far rect c can close on anchor (ax, ay) before a mark inside it touches the frame's edge."""
    w, h = c[2] - c[0], c[3] - c[1]
    z = 9.0
    for m in marks:
        inside = c[0] - 1e-3 <= m[0] and c[1] - 1e-3 <= m[1] and m[2] <= c[2] + 1e-3 and m[3] <= c[3] + 1e-3
        if not inside:
            if m[0] < c[2] and m[2] > c[0] and m[1] < c[3] and m[3] > c[1]:
                return 1.0                    # the frame already cuts it: this run does not move
            continue
        l, r = (m[0] - c[0]) / w, (m[2] - c[0]) / w
        t, b = (m[1] - c[1]) / h, (m[3] - c[1]) / h
        for lo, hi, a in ((l, r, ax), (t, b, ay)):
            if lo < a - 1e-6:
                z = min(z, (a - EDGE) / max(a - lo, 1e-6))
            if hi > a + 1e-6:
                z = min(z, (1 - EDGE - a) / max(hi - a, 1e-6))
    return max(1.0, z)


def _heads(ctx, S, c, s0, s1):
    """What a closer frame must keep whole of the people in a run: each head with its hair and chin (compiler
    head_of) when the frame holds it whole now; when the frame already cuts the hair or an ear, as a selfie held
    close does, the face itself with a tenth of its size around it. None when a face is already cut by the frame:
    that run does not move."""
    out = []
    for r in ctx.faces or []:
        if not s0 - 0.75 <= r["t"] <= s1 + 0.75:
            continue
        for f in r["boxes"]:
            if (f[2] - f[0]) * (f[3] - f[1]) <= 0.0004:
                continue
            hd = S.head_of(f)
            if c[0] <= hd[0] and c[1] <= hd[1] and hd[2] <= c[2] and hd[3] <= c[3]:
                out.append(hd)
                continue
            if not (f[0] < c[2] and f[2] > c[0] and f[1] < c[3] and f[3] > c[1]):
                continue                                   # a face outside this frame binds nothing
            if not (c[0] <= f[0] and c[1] <= f[1] and f[2] <= c[2] and f[3] <= c[3]):
                return None
            mw, mh = 0.1 * (f[2] - f[0]), 0.1 * (f[3] - f[1])
            out.append([max(c[0], f[0] - mw), max(c[1], f[1] - mh), min(c[2], f[2] + mw), min(c[3], f[3] + mh)])
    return out


def stress_frames(ctx):
    """[(programme frame, strength 0..1)]: where a move may land. The voice's stressed words; with no voice, the
    onsets of the upload's own sound through the spine."""
    out = []
    for w, f in (ctx.wf_any or {}).items():
        z = ctx.stress.get(w, ctx.stress.get(str(w), 0.0)) if ctx.stress else 0.0
        try:
            z = float(z)
        except (TypeError, ValueError):
            z = 0.0
        if z >= STRESS_MIN:
            out.append((int(f), min(1.0, z / 2.5)))
    if out:
        return sorted(out)
    fps = ctx.fps
    for t in ((ctx.perc or {}).get("audio") or {}).get("onsets") or []:
        try:
            t = float(t if not isinstance(t, (list, tuple)) else t[0])
        except (TypeError, ValueError):
            continue
        for s in ctx.spine:
            if s["src"][0] <= t < s["src"][1]:
                out.append((int(round(s["f"][0] + (t - s["src"][0]) * fps)), 0.5))
                break
    return sorted(out)


def _splits(a, b, stress, lo, hi, rng):
    """Frames inside (a, b) where a move lands: the strongest stress in each window of [lo, hi] frames after the
    last one, else a point the seed picks in it."""
    pts, t = [], a
    while b - t > lo + 6:
        cand = [(s, -f) for f, s in stress if t + lo <= f <= t + hi and b - f >= 8]
        if cand:
            f = -max(cand)[1]
        else:
            f = t + int(lo + (hi - lo) * (0.35 + 0.4 * rng.random()))
            if b - f < 8:
                break
        pts.append(int(f))
        t = f
    return pts


def _runs(ctx, S):
    """Stretches of the take that play on without a cut: [[shot, ...]]. A jump of the source (a closed pause, a beat
    out of order) or anything else on screen between two shots ends a run."""
    shots = sorted(ctx.shots, key=lambda s: s["f"][0])
    runs, cur = [], []
    for s in shots:
        take = s.get("pic") == "main" and s.get("src") and s.get("crop") and s.get("view") is None and not s.get("treat") and not s.get("framed")
        if take and cur and cur[-1]["f"][1] == s["f"][0] and abs(s["src"][0] - cur[-1]["src"][1]) < 0.12:
            cur.append(s)
            continue
        if cur:
            runs.append(cur)
        cur = [s] if take else []
    if cur:
        runs.append(cur)
    return runs


def ease_takes(ctx):
    """Every run of the take as one camera track. Returns what it did, for the meter."""
    rep = {"runs": 0, "moved": 0, "moves": 0, "held": 0, "frames_moving": 0, "frames": 0, "why_held": {}}
    if not on() or not ctx.shots:
        return rep
    from . import compiler as S
    from . import taste
    fps = float(ctx.fps)
    asset = ctx.assets.get("main") or {}
    aw, ah = float(asset.get("w") or 0), float(asset.get("h") or 0)
    if aw <= 0 or ah <= 0:
        return rep
    rng = random.Random((int(ctx.seed or 0) * 2654435761) ^ 0x5EA5ED)
    stress = stress_frames(ctx)
    lo, hi = int(round(HOLD_S[0] * fps)), int(round(HOLD_S[1] * fps))
    cap_y = getattr(ctx, "cap_y", None)
    last_end_z, k_run = 1.0, 0
    other_shape = abs((ctx.W / ctx.H) / max(aw / ah, 1e-6) - 1.0) > 0.03
    try:
        own_caps = taste.upload_captions(ctx) >= 0.3
    except Exception:  # noqa: BLE001 - unread: the words are held, the careful side
        own_caps = True

    def held(why):
        rep["held"] += 1
        rep["why_held"][why] = rep["why_held"].get(why, 0) + 1

    for run in _runs(ctx, S):
        a, b = run[0]["f"][0], run[-1]["f"][1]
        n = b - a
        rep["runs"] += 1
        rep["frames"] += n
        rows = []
        for s in run:
            rows += _rects(s["crop"], s["f"][1] - s["f"][0])
        if any(len(set(map(tuple, _rects(s["crop"], s["f"][1] - s["f"][0])))) > 1 for s in run):
            held("the Score already moves this framing")       # a followed subject, a written camera: theirs
            last_end_z = 1.0
            continue
        s0, s1 = run[0]["src"][0], run[-1]["src"][1]
        c = rows[0]
        w, h = c[2] - c[0], c[3] - c[1]
        if w <= 0 or h <= 0 or w > 1.0 + 1e-6 or h > 1.0 + 1e-6:
            held("the upload is held whole in a frame of another shape")
            last_end_z = 1.0
            continue
        face = S.face_at(ctx, (s0 + s1) / 2)
        heads = _heads(ctx, S, c, s0, s1)
        if heads is None:
            held("a face already sits on the frame's edge")
            last_end_z = 1.0
            continue
        # Printed words bind as the compiler binds them: where the upload's shape is not the frame's every word it
        # prints stays whole, and an upload that carries its own captions keeps them whole in any shape. A sign in
        # the background of a same-shape take is not held: a punch-in that leaves it out is a choice (main_marks).
        marks = [list(x["box"]) for x in (ctx.perc.get("text") or []) if x.get("box") and x["t"][0] - 0.5 <= s1 and x["t"][1] + 0.5 >= s0] if (other_shape or own_caps) else []
        # The anchor: the point of the frame that stays put while the frame closes. Across, it sits in the heads'
        # span where the room either side of them is shared out, so a head held against one edge (a selfie) grows
        # away from that edge and is never cut by it. Down, it is the chin when the captions sit under the face and
        # the brow when they sit over it, so the head never grows onto the caption seat.
        ax, ay, z_face = 0.5, 0.5, 9.0
        if heads:
            l, r = min(m[0] for m in heads), max(m[2] for m in heads)
            t, b_ = min(m[1] for m in heads), max(m[3] for m in heads)
            fl, fr_ = (l - c[0]) / w, (r - c[0]) / w
            ft, fb = (t - c[1]) / h, (b_ - c[1]) / h
            rl, rr = max(fl - EDGE, 0.0), max(1 - EDGE - fr_, 0.0)
            ax = fl + (fr_ - fl) * (rl / (rl + rr) if rl + rr > 1e-6 else 0.5)
            rt, rb = max(ft - EDGE, 0.0), max(1 - EDGE - fb, 0.0)
            ay = ft + (fb - ft) * (rt / (rt + rb) if rt + rb > 1e-6 else 0.5)
        if face and c[0] <= (face[0] + face[2]) / 2 <= c[2] and c[1] <= (face[1] + face[3]) / 2 <= c[3]:
            if cap_y is not None:
                below = cap_y >= ((face[1] + face[3]) / 2 - c[1]) / h
                ay = min(max(((face[3] if below else face[1]) - c[1]) / h, 0.1), 0.9)
            z_face = taste.FACE_H_MAX / max((face[3] - face[1]) / h, 1e-6)
        up = ctx.W / max(1.0, w * aw)
        z_up = max(1.0, min(TOTAL_MAX, taste.UPSCALE_MAX / max(up, 1e-6)))
        zcap = min(TOTAL_MAX, z_face, z_up, _cap(c, marks, ax, ay), _cap(c, heads, ax, ay))
        if zcap < 1.0 + MOVE_IN[0] * 0.6:
            held("no room: a head, a printed word or the source's own pixels")
            last_end_z = 1.0
            continue
        # where the run starts: not the framing the take left before the cutaway
        k_run += 1
        z0 = 1.0
        if k_run > 1 and last_end_z < 1.035 and zcap >= RETURN_IN:
            z0 = RETURN_IN
        dirn = 1.0
        z = z0
        path = []
        moves = []
        if n >= int(RUN_MIN_S * fps):
            run_in = 1 if z0 > 1.0 else 0
            cur = z0
            for f in _splits(a, b, stress, lo, hi, rng):
                size = MOVE_IN[0] + (MOVE_IN[1] - MOVE_IN[0]) * rng.random()
                go_in = run_in < 3 and cur * (1 + size) <= zcap * 1.002 and (run_in == 0 or rng.random() < 0.7)
                if go_in:
                    tgt, kind, fr = cur * (1 + size), "out", MOVE_FRAMES[0]
                    run_in += 1
                elif cur > 1.0 + 1e-3:
                    tgt, kind, fr = max(1.0, cur / (1 + max(run_in, 1) * 0.05 + size)), "io", MOVE_FRAMES[1]
                    run_in = 0
                else:
                    continue
                fr = max(6, int(round(fr * fps / 30.0 * (0.8 + 0.4 * rng.random()))))
                start = max(f - a - max(fr // 5, 1), 0)         # the curve's speed peaks a fifth of the way in: that lands on the word
                if moves and start < moves[-1][0] + moves[-1][1] + 4:
                    continue
                if start + fr > n - 2:
                    continue
                moves.append((start, fr, kind, tgt))
                cur = tgt
        mi, active = 0, None
        for i in range(n):
            if active is None and mi < len(moves) and i >= moves[mi][0]:
                st, fr, kind, tgt = moves[mi]
                active = (st, fr, _curve(kind, fr), z, tgt)
                mi += 1
            if active is not None:
                st, fr, cv, zs, tgt = active
                j = i - st
                if j < fr:
                    z = math.exp(math.log(zs) + (math.log(tgt) - math.log(zs)) * cv[j])
                else:
                    z, active = tgt, None
                    dirn = 1.0 if tgt < zcap - 0.02 else -1.0
            if active is None:
                z2 = z * math.exp(dirn * PUSH_RATE / fps)
                if 1.0 <= z2 <= zcap:
                    z = z2
            path.append(min(max(z, 1.0), zcap))
        if max(path) - min(path) < 0.004:
            held("too short to move")
            last_end_z = path[-1]
            continue
        i = 0
        for s in run:
            m = s["f"][1] - s["f"][0]
            s["crop"] = [[round(v, 5) for v in _zoom(rows[i + j], path[i + j], ax, ay)] for j in range(m)]
            s["_eased"] = True
            i += m
        rep["moved"] += 1
        rep["moves"] += len(moves)
        rep["frames_moving"] += n
        last_end_z = path[-1]
    rep["moves10"] = round(rep["moves"] / max(ctx.dur_f / fps, 1e-3) * 10, 2)
    rep["stills"] = push_stills(ctx)
    return rep


STILL_RATE = 0.045            # a still set whole on the plate pushes this much a second (the references' slow pushes run 5 to 15%)
STILL_MAX = 1.08              # and never grows past this, nor past the frame


def push_stills(ctx):
    """A photograph set whole on the film's plate settles in and then stood still for as long as it was held. It
    keeps moving: a slow push from where its settle leaves it, one speed, as the references push a still. Returns
    how many it moved."""
    fps = float(ctx.fps)
    n_moved = 0
    for c in ctx.items:
        if c.get("k") != "card" or not c.get("framed"):
            continue
        n = c["f"][1] - c["f"][0]
        if n < 8:
            continue
        s = c.get("s", 1.0)
        rows = [float(v) for v in s] if isinstance(s, list) else [float(s or 1.0)] * n
        rows = (rows + [rows[-1]] * n)[:n]
        top = max(rows)                    # never larger than the size it arrived at: that is inside the frame's margins
        out = [round(min(v * min(STILL_MAX, math.exp(STILL_RATE * i / fps)), top), 5) for i, v in enumerate(rows)]
        if max(abs(a - b) for a, b in zip(out, rows)) < 0.004:
            continue
        c["s"] = out
        n_moved += 1
    return n_moved
