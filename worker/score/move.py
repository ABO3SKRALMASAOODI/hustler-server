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
WIDE_LINE = 0.4               # printed text this wide in the frame is a caption or a title of the upload's own, never a sign behind someone


PUNCH_MAX = 1.26              # the closest framing of a punched stretch against the framing the film gave it: a tenth of the
                              # frame leaves at each edge, so a subject no detector found still has its top and its sides
PUNCH_MAX_PERSON = 1.2        # with someone in the frame: one step, the person and what they are doing both stay in it
PUNCH_STEP = 0.12             # a framing differs from the last by at least this much, or it is not a cut the eye reads
TALK_MAX = 1.3                # a talking take cut inside itself (score.pace.talk_parts): the face is the subject, so the closest
                              # framing goes this far, never past what keeps the head whole or the governor's face height
TALK_PUSH = 0.02              # and each framing drifts in this much a second, so no frame of it repeats the one before


CUT_SHOWN = 0.6               # a printed line the window already cuts still binds the camera while this share of it shows
TALK_STEP = 0.10              # under a voice a framing differs from the last by a tenth: the step the film already takes where
                              # words were cut out (taste.EDIT_STEP), so every cut inside a talking take reads as that one cut


def punch_levels(zp, step=PUNCH_STEP):
    """The framings a stretch cut inside itself goes through, as scales: wide, closer, closest, then wide again. Three
    when the picture has the room. With room for one step there are two, and the stretch takes that step once and
    stays (round seven: two framings in turn are wide, closer, wide, closer, each cut back to the framing just left,
    which is what p5582's jar did). None when the picture has no room."""
    if zp >= (1.0 + step) ** 2:
        return [1.0, math.sqrt(zp), zp]
    if zp >= 1.0 + step:
        return [1.0, zp]
    return []


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
            # The frame already cuts a line the viewer is reading: this run does not move. A mark that is mostly out of
            # the window already (it touches the edge, or under three fifths of it shows) is not read as a line, and a
            # closer frame only shows less of it (p5574: the film's own window cut the label under the speaker in half,
            # and 14.5 s of the take were held as "no room"; a step closer leaves the label out).
            ow, oh = min(m[2], c[2]) - max(m[0], c[0]), min(m[3], c[3]) - max(m[1], c[1])
            area = max((m[2] - m[0]) * (m[3] - m[1]), 1e-9)
            if ow > 0 and oh > 0 and ow * oh / area >= CUT_SHOWN:
                return 1.0
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
        if take and cur and cur[-1]["f"][1] == s["f"][0] and abs(s["src"][0] - cur[-1]["src"][1]) < 0.12 \
                and (s.get("punch") is None) == (cur[-1].get("punch") is None):       # a punched stretch is a run of its own
            cur.append(s)
            continue
        if cur:
            runs.append(cur)
        cur = [s] if take else []
    if cur:
        runs.append(cur)
    return runs


def _unsure(ctx, s0, s1):
    """Someone is in these seconds of the upload and the faces read there do not say where their head is on every
    sample (turned away, arms up, eyes shut): a closer framing set by the heads that were found could cut the one
    that was not (p5575, 14 Nov: a punch left a pyjama top with no head)."""
    for smp in (ctx.perc or {}).get("samples") or []:
        try:
            t = float(smp.get("t"))
        except (TypeError, ValueError):
            continue
        if s0 - 0.6 <= t <= s1 + 0.6 and (smp.get("persons") or smp.get("body")) and not smp.get("face"):
            return True
    return False


def _unpunch(ctx, run):
    """The parts of a punched stretch that cannot be framed apart become the one shot they were: the first part takes
    the others' frames and source, and the others leave the film (a cut nobody can see is not a cut)."""
    from . import pace
    heads = {}
    for s in run:
        if s.get("punch") is None:
            continue
        key = pace.parent(s["id"])
        first = heads.get(key)
        s.pop("talk", None)
        if first is None:
            heads[key] = s
            s.pop("punch", None)
            continue
        first["f"] = [first["f"][0], s["f"][1]]
        if first.get("src") and s.get("src"):
            first["src"] = [first["src"][0], s["src"][1]]
        if isinstance(first.get("crop"), list) and first["crop"] and isinstance(first["crop"][0], (list, tuple)):
            first["crop"] = list(first["crop"][0])
        s["_gone"] = True
    if any(s.get("_gone") for s in run):
        ctx.shots[:] = [s for s in ctx.shots if not s.get("_gone")]
        for b in getattr(ctx, "beats", None) or []:
            if getattr(b, "shots", None):
                b.shots[:] = [s for s in b.shots if not s.get("_gone")]


def punch_takes(ctx):
    """The wordless stretches score.pace cut inside themselves get their framings, before anything is set on them or
    sounded on their cuts; a stretch that cannot be framed apart is one shot again. Returns what it did."""
    try:
        return ease_takes(ctx, punch=True)
    except Exception as e:  # noqa: BLE001 - no punch at all: every stretch is the shot it was
        try:
            _unpunch(ctx, [s for s in ctx.shots if s.get("punch") is not None])
        except Exception:  # noqa: BLE001
            pass
        return {"fault": f"{type(e).__name__}: {str(e)[:160]}"}


def ease_takes(ctx, punch=False):
    """Every run of the take as one camera track. Returns what it did, for the meter. punch: only the stretches
    score.pace cut inside themselves (punch_takes); without it those are left as that pass framed them."""
    rep = {"runs": 0, "moved": 0, "moves": 0, "held": 0, "frames_moving": 0, "frames": 0, "why_held": {}}
    if not on() or not ctx.shots:
        if punch and ctx.shots:
            _unpunch(ctx, [s for s in ctx.shots if s.get("punch") is not None])
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
    talking = len(ctx.words) / max(float(ctx.src_dur or 0), 1e-3) >= 1.0
    try:
        own_caps = taste.upload_captions(ctx) >= 0.3
    except Exception:  # noqa: BLE001 - unread: the words are held, the careful side
        own_caps = True

    at = {"run": None}

    def held(why):
        rep["held"] += 1
        rep["why_held"][why] = rep["why_held"].get(why, 0) + 1
        if punch and at["run"] is not None:
            _unpunch(ctx, at["run"])

    for run in _runs(ctx, S):
        if punch != any(s.get("punch") is not None for s in run):
            continue
        at["run"] = run
        a, b = run[0]["f"][0], run[-1]["f"][1]
        n = b - a
        rep["runs"] += 1
        rep["frames"] += n
        rows = []
        for s in run:
            rows += _rects(s["crop"], s["f"][1] - s["f"][0])
        # A camera the Score wrote is the Score's. A window that only follows the film's subject (dna.take "face") is
        # still one framing: the camera closes on it inside that window, frame by frame (round seven: a followed take
        # was neither eased nor cut, and p5574 held it 14.5 s).
        if any(len(set(map(tuple, _rects(s["crop"], s["f"][1] - s["f"][0])))) > 1 and not s.get("_follow") for s in run):
            held("the Score already moves this framing")
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
        # prints stays whole, and an upload that carries its own captions keeps them whole in any shape. A small sign
        # in the background of a same-shape take is not held: a punch-in that leaves it out is a choice (main_marks).
        marks = [list(x["box"]) for x in (ctx.perc.get("text") or []) if x.get("box") and x["t"][0] - 0.5 <= s1 and x["t"][1] + 0.5 >= s0]
        # A picture with nobody in it that prints words (an animated explainer, a diagram, a screen) is its words: every
        # one of them stays whole (round seven: p5555's label "ARCTIC DEEP" was clipped by a closer frame).
        drawn = not heads and face is None and bool(marks)
        if not (other_shape or own_caps or drawn):
            # a line printed across the frame is the upload's own caption or title, whoever speaks or does not (p5572, 13
            # Nov: an eased step cut "POV: You have back pain" at both edges, and the last look took the film back)
            marks = [m for m in marks if m[2] - m[0] >= WIDE_LINE * w]
        if not talking:
            # A clip nobody talks over is where a caption or a title is burned in ("POV: ...", a game's HUD), and the
            # index sees such a line on samples where perception kept no region. Behind a person talking, the same
            # boxes are a poster on the wall, and a closer frame leaving part of it out is a choice.
            marks += [m for m in S.wide_print(ctx, s0, s1, w) if m not in marks]
        # The anchor: the point of the frame that stays put while the frame closes. It sits inside the span of
        # everything that must stay whole (heads, and the lines the upload prints), where the room either side of that
        # span is shared out: a head held against one edge (a selfie) grows away from that edge, a caption that
        # starts at the left edge keeps its first letter, a line along the bottom stays on the bottom. Then the
        # captions: the head never grows onto the caption seat, so the anchor is no higher than the chin when the
        # captions sit under the face, and no lower than the brow when they sit over it.
        ax, ay, z_face = 0.5, 0.5, 9.0
        whole = [m for m in list(heads) + list(marks) if c[0] - 1e-3 <= m[0] and c[1] - 1e-3 <= m[1] and m[2] <= c[2] + 1e-3 and m[3] <= c[3] + 1e-3]
        if whole:
            l, r = min(m[0] for m in whole), max(m[2] for m in whole)
            t, b_ = min(m[1] for m in whole), max(m[3] for m in whole)
            fl, fr_ = (l - c[0]) / w, (r - c[0]) / w
            ft, fb = (t - c[1]) / h, (b_ - c[1]) / h
            rl, rr = max(fl - EDGE, 0.0), max(1 - EDGE - fr_, 0.0)
            ax = fl + (fr_ - fl) * (rl / (rl + rr) if rl + rr > 1e-6 else 0.5)
            rt, rb = max(ft - EDGE, 0.0), max(1 - EDGE - fb, 0.0)
            ay = ft + (fb - ft) * (rt / (rt + rb) if rt + rb > 1e-6 else 0.5)
        if face and c[0] <= (face[0] + face[2]) / 2 <= c[2] and c[1] <= (face[1] + face[3]) / 2 <= c[3]:
            if cap_y is not None:
                chin, brow = (face[3] - c[1]) / h, (face[1] - c[1]) / h
                ay = max(ay, min(chin, 0.9)) if cap_y >= (chin + brow) / 2 else min(ay, max(brow, 0.1))
            z_face = taste.FACE_H_MAX / max((face[3] - face[1]) / h, 1e-6)
        up = ctx.W / max(1.0, w * aw)
        z_up = max(1.0, min(TOTAL_MAX, taste.UPSCALE_MAX / max(up, 1e-6)))
        room = min(z_face, _cap(c, marks, ax, ay), _cap(c, heads, ax, ay))
        zcap = min(TOTAL_MAX, z_up, room)
        if punch:
            # a stretch nobody speaks over, cut inside itself (score.pace.take_parts): each part is a framing of its
            # own, a step closer on what the frame keeps whole, then wide again, never the framing just left; inside
            # a part the slow push goes on. Hard, on the cut: there is no word here for an eased move to land on.
            if _unsure(ctx, s0, s1):
                held("someone is in the frame whose head is not found on every sample")
                continue
            said = any(s.get("talk") for s in run)       # the voice's own cuts (score.pace.talk_parts): the face is the subject
            zp = min(PUNCH_MAX if not heads else TALK_MAX if said else PUNCH_MAX_PERSON, room, max(1.0, taste.UPSCALE_MAX / max(up, 1e-6)))
            levels = punch_levels(zp, TALK_STEP if said else PUNCH_STEP)
            if not levels:
                held("no room for a second framing: a head, a printed word or the source's own pixels")
                continue
            # The part a run opens on says which framing it opens on: a stretch whose first shots were left as shot
            # opens its first cut on the second framing, never on the wide one the eye is already looking at.
            first = int(run[0].get("punch") or 0)
            push = TALK_PUSH if said else PUSH_RATE
            # With room for one step only, a talking stretch is cut in and drifts back out: every cut closes on the
            # speaker where a phrase starts, the frame eases wide again while they talk, and no cut ever goes back to
            # the framing just left. (Two framings in turn would be wide, closer, wide, closer.)
            loop = said and len(levels) == 2
            plen, kk, lp = {}, first - 1, object()
            for s in run:
                if s.get("punch") != lp:
                    kk, lp = kk + 1, s.get("punch")
                plen[kk] = plen.get(kk, 0) + s["f"][1] - s["f"][0]
            i, k_part, last_p = 0, first - 1, object()
            li_prev, since, cuts_seen = None, 0, 0
            for s in run:
                m = s["f"][1] - s["f"][0]
                new = s.get("punch") != last_p
                if new:
                    k_part, last_p = k_part + 1, s.get("punch")
                # three framings turn (wide, closer, closest, wide); two are one step, taken once: never the framing just left
                li = k_part % len(levels) if len(levels) >= 3 else min(k_part, len(levels) - 1)
                cut_here = new and ((k_part >= 1) if loop else (li != li_prev if li_prev is not None else li != 0))
                if cut_here or li_prev is None:
                    since = 0
                lv = levels[li]
                if loop and k_part >= 1:
                    span = max(plen.get(k_part, m) - 1, 1)
                    rest = 1.0 + (zp - 1.0) * 0.12              # where the drift ends: nearly wide, still moving
                    zs = [zp * (rest / zp) ** min(1.0, (since + j) / span) for j in range(m)]
                else:
                    zs = [min(zp, lv * math.exp(push * (since + j) / fps)) for j in range(m)]
                s["crop"] = [[round(v, 5) for v in _zoom(rows[i + j], zs[j], ax, ay)] for j in range(m)]
                s["_eased"] = True
                # on the L1 a part says only whether the eye sees a cut where it starts: the sound pass and the meter read that
                s["punch"] = max(1, k_part) if cut_here else 0
                cuts_seen += 1 if cut_here else 0
                li_prev, since = li, since + m
                i += m
            rep["moved"] += 1
            rep["punched"] = rep.get("punched", 0) + cuts_seen
            if said:
                rep["talk_cuts"] = rep.get("talk_cuts", 0) + cuts_seen
            rep["frames_moving"] += n
            continue
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
    if punch:
        return rep
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
