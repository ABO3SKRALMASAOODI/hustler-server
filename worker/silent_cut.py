"""A short silent clip cut like an editor would (Q6, 30 Oct 2026).

QB's camera (short_clip.py) gave a short silent clip a subject crop that
follows it, and the gym clip rose 5.75 -> 7.25. The snow walk stayed at 6.1
and both judges, every run, still asked for the same three things:

- jump cuts: "cut the 17 s take into 4-6 tighter beats so something changes
  every 2-3 s", "cut the routine into its poses, trimming the transitional
  fumbling", "trim the moments where the phone arm blocks the frame";
- a striking first frame: "start tighter: the subject is tiny in the 0-2 s
  wide";
- a designed end beat: "end on a held close hero frame, or a hard exit with
  a hit or a flash", "rather than letting the take just stop".

So the clip is cut into 4-6 beats, each one a strong, clean moment of the
action, in order. One trial-model look at 8-14 frames (about $0.001) boxes
the subject on each and says which frames are clean (nothing in front of the
lens, the subject whole) and which are peaks (a pose held, the key step,
the subject at its most visible). Each beat has its own framing on the
subject (medium and close alternate, so every cut reads as a new camera);
the first beat eases in under the title and one middle beat pushes in
slowly (one or two eased push-ins, never a zoom on every cut); the last beat
is the end beat: a snap in to the hero framing with one white flash on it,
then live picture to the end.

WHO GETS IT: the First cut's engine accounts (trial and new subscribers),
for a silent vertical clip of SILENT_CUT_MIN_S-20 s (shorter clips keep
QB's whole-take camera). No lane, no cut: the whole take stays, as before.
"""
import base64
import json
import re
import subprocess

SILENT_CUT_MIN_S = 8.0      # a shorter clip keeps its whole take
BEATS_MIN, BEATS_MAX = 4, 6
BEAT_S = 2.6                # one beat per this much source
PROG_SHARE = 0.62           # the cut keeps about this share of the take
PROG_MIN_S, PROG_MAX_S = 7.0, 11.5
BEAT_MIN_S, BEAT_MAX_S = 1.1, 1.3   # judges: "something every 1-2 s"
OPEN_MIN_S = 1.3            # the first beat carries the title's first read
END_MIN_S = 1.6             # the end beat: the snap and live picture after it
GAP_MIN_S = 0.8             # a jump skips at least this much of the take
DIRTY_PAD_S = 0.3           # a beat stays this far from an unclean frame
END_HIT_S = 0.75            # the snap lands this long before the end
END_AFTER_S = 0.12          # the end beat closes this soon after its clean look
EDGE = 0.04                 # the end beat's subject is this far inside the sides
FLASH_S = 0.1               # one white flash, three frames, on the snap
LOOK_EVERY_S = 1.1
LOOKS_MIN, LOOKS_MAX = 8, 14
Z_FLOOR = 1.12              # every beat is a crop on the subject
FILLS = (0.5, 0.8)          # subject height share of the frame: medium, close
OPEN_FILL = 0.62
END_FILL = 0.9              # the hero framing after the snap
PUSH = 0.14                 # an eased push-in adds this much zoom
ALTERNATE = 0.3             # medium and close differ by at least this much zoom
MEDIUM_RATIO = 0.8          # ... and the medium is at most this share of the close
                            # (final run: 2.1x after 2.4x read as one long shot)
GROUND = 0.08               # a subject that fills the window: at most this share below it
TITLE_HOLD_S = 3.0          # the hook title's time on screen (first_cut.HOOK_S)
TITLE_BIAS = 0.12           # every beat under the title keeps this much more room above
                            # the subject (QB2 loop 5: with head room the head walked into
                            # the title at y 0.26, "MIRROR FLEX over his forehead", 3 of 8)
HEAD_ROOM = 0.25            # a subject that cannot fill it sits with this much above
FILL_TOP = 0.62             # ... "fills" = at least this share of the window height
                            # (QB2, 30 Oct: a small subject pinned to the base left the
                            # head at y 0.72 under a band of empty wall, 8 of 8 judgements)
READ_FILL = 0.28            # the subject reads at this share of the frame height
OPEN_SKIP_MAX = 0.4         # the cut never skips more than this share of the take

SYSTEM = (
    "You edit a short silent clip into jump cuts. Each numbered image is one "
    "frame of it, in time order. For EACH frame answer: box: [x0, y0, x1, "
    "y1], pixel coordinates of THAT image, around the clip's main subject "
    "(the person, animal or object the clip is about), whole, with a little "
    "room, tight on it even when it is small; clean: false when the subject "
    "is blocked by something close to the lens (an arm, a hand, the phone, "
    "a passer-by), smeared by a fast whip, cut off by the frame edge or out "
    "of the frame, else true; peak: true when the frame is a strong moment "
    "worth keeping (a pose held, a key step of the action, the subject at "
    "its most visible or expressive, the payoff). Answer with JSON only: "
    "{\"frames\": [{\"n\": <frame number>, \"box\": [x0, y0, x1, y1], "
    "\"clean\": true, \"peak\": false}]}.")


def parse_looks(text, sizes, times):
    """[(t, box, clean, peak)] in time order from the model's answer; a
    frame with no usable box is kept as unclean with box None."""
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        pick = json.loads(match.group(0)) if match else {}
    except ValueError:
        return []
    out = {}
    for item in pick.get("frames") or []:
        try:
            n = int(item.get("n"))
        except (TypeError, ValueError, AttributeError):
            continue
        if n not in sizes or n not in times:
            continue
        box = None
        try:
            w, h = sizes[n]
            x0, y0, x1, y1 = [float(v) for v in item.get("box")]
            b = [x0 / w, y0 / h, x1 / w, y1 / h]
            if -0.02 <= b[0] < b[2] <= 1.02 and -0.02 <= b[1] < b[3] <= 1.02 \
                    and (b[2] - b[0]) * (b[3] - b[1]) <= 0.85:
                box = tuple(min(max(v, 0.0), 1.0) for v in b)
        except (TypeError, ValueError):
            box = None
        clean = bool(item.get("clean", True)) and box is not None
        out[n] = (float(times[n]), box, clean, bool(item.get("peak")) and clean)
    return [out[n] for n in sorted(out)]


def look(ctx, duration, spend):
    """One trial-model look at frames across the whole clip (and one at its
    very end); [] without the lane or a usable answer."""
    import claude_lane
    import first_cut_visual as fcv
    import llm
    model = getattr(ctx, "first_cut_model", None)
    if not model or not claude_lane.available():
        return []
    try:
        path = ctx.proxy_path()
    except Exception:
        return []
    n = int(min(LOOKS_MAX, max(LOOKS_MIN, round(duration / LOOK_EVERY_S))))
    marks = [duration * (k + 0.5) / n for k in range(n)]
    marks.append(max(0.0, duration - 0.3))
    parts, sizes, times = [], {}, {}
    for k, t in enumerate(marks):
        try:
            jpg = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i",
                 path, "-frames:v", "1", "-vf", "scale=384:-2", "-f", "image2",
                 "-c:v", "mjpeg", "-q:v", "5", "-"],
                capture_output=True, timeout=30).stdout
        except Exception:
            jpg = b""
        size = fcv._jpeg_size(jpg) if jpg else None
        if not size:
            continue
        sizes[k + 1], times[k + 1] = size, t
        parts.append({"type": "text", "text":
                      f"Frame {k + 1} ({size[0]}x{size[1]} px) at {t:.1f} s"})
        parts.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    if len(sizes) < 5:
        return []
    try:
        resp = claude_lane.create(
            model, [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": parts}],
            max_tokens=3000, effort="low")
        text = resp.choices[0].message.content or ""
        spend["usd"] += float(getattr(resp.usage, "provider_cost_usd", 0) or 0)
        llm.record("first_cut_silent_cut", {"model": model, "frames": len(sizes)},
                   {"content": text}, resp.usage)
        print(f"[first_cut] silent cut look: {text.strip()[:300]}", flush=True)
    except Exception as exc:  # noqa: BLE001 - QB's whole-take camera is the fallback
        print(f"[first_cut] silent cut look failed: {type(exc).__name__}: "
              f"{str(exc)[:160]}", flush=True)
        return []
    looks = parse_looks(text, sizes, times)
    print("[first_cut] silent cut looks: " + "; ".join(
        f"{t:.1f}{'' if ok else ' blocked'}{' peak' if pk else ''} "
        f"{[round(v, 2) for v in b] if b else None}"
        for t, b, ok, pk in looks), flush=True)
    return looks


def _clamp(v, a, b):
    return max(a, min(b, v))


def opening_at(looks, duration, z_max=2.4):
    """The source second the cut may open at: the first clean frame where
    the subject reads at the deepest crop (READ_FILL of the frame height),
    so the first frame is never a speck in a wide (judges, the snow walk:
    "the first ~5 s show a tiny figure among the trees"); 0 when the subject
    reads from the start, or never within the first OPEN_SKIP_MAX of it."""
    for t, box, ok, _p in sorted(looks or [], key=lambda lk: lk[0]):
        if not ok or not box:
            continue
        if t > OPEN_SKIP_MAX * duration:
            return 0.0
        if (box[3] - box[1]) * z_max >= READ_FILL:
            return max(0.0, t - 0.6) if t > 1.5 else 0.0
    return 0.0


def plan(looks, duration, z_max=2.4):
    """[[start, end]] source seconds: the clip's beats, in order, or [] when
    it should stay whole (too short, too few clean frames). The take (from
    where its subject first reads) is cut into zones; each zone gives the
    beat around its best clean frame (a peak first); the last zone's beat is
    the end beat (its latest clean peak); no beat runs into an unclean
    frame; a jump always skips at least GAP_MIN_S."""
    looks = sorted(looks or [], key=lambda lk: lk[0])
    clean = [lk for lk in looks if lk[2]]
    if duration < SILENT_CUT_MIN_S or len(clean) < 4:
        return []
    t0 = opening_at(looks, duration, z_max)
    span = duration - t0
    m = int(_clamp(round(span / BEAT_S), BEATS_MIN, BEATS_MAX))
    prog = _clamp(PROG_SHARE * span, PROG_MIN_S, PROG_MAX_S)
    beat = _clamp(prog / m, BEAT_MIN_S, BEAT_MAX_S)
    dirty = [lk[0] for lk in looks if not lk[2]]
    zone = span / m
    # the end beat first (the latest strong moment of the last zone), then
    # one beat per earlier zone near its centre, clear of the end beat
    # the end beat's frame must show the subject whole, clear of the
    # frame's sides (a man walking out of the frame was boxed "clean" at
    # its edge, and the hero framing sliced him)
    last_zone = [lk for lk in clean if lk[0] >= t0 + (m - 1) * zone
                 and lk[1][0] >= EDGE and lk[1][2] <= 1.0 - EDGE]
    if not last_zone:
        return []
    end_pick = ([lk for lk in last_zone if lk[3]] or last_zone)[-1][0]
    room = end_pick - (beat + END_MIN_S) / 2.0 - GAP_MIN_S
    picks = []
    for z in range(m - 1):
        a, b = t0 + z * zone, t0 + (z + 1) * zone
        floor = picks[-1][1] + beat + GAP_MIN_S if picks else t0
        cands = [lk for lk in clean if a <= lk[0] < b and floor <= lk[0] <= room]
        if not cands:
            continue
        peaks = [lk for lk in cands if lk[3]]
        pool = peaks or cands
        if z == 0:
            pick = pool[0]
        else:
            mid = (a + b) / 2.0
            pick = min(pool, key=lambda lk: abs(lk[0] - mid))
        picks.append((z, pick[0]))
    picks.append((m - 1, end_pick))
    beat = _clamp(prog / len(picks), BEAT_MIN_S, BEAT_MAX_S)
    if len(picks) < 3:
        return []
    segs = []
    for k, (z, c) in enumerate(picks):
        last = k == len(picks) - 1
        want = max(beat, OPEN_MIN_S if k == 0 else 0.0, END_MIN_S if last else 0.0)
        a, b = c - 0.45 * want, c + 0.55 * want
        if last:
            # the end beat closes just after the frame the model saw clean:
            # a subject walking out of the frame was sliced at its edge
            # (candidate q6c1: he left the hero framing in its last 0.3 s)
            b = c + END_AFTER_S
            a = b - want
        lo, hi = t0, duration - 0.05
        for d in dirty:
            if d <= c:
                lo = max(lo, d + DIRTY_PAD_S)
            else:
                hi = min(hi, d - DIRTY_PAD_S)
        if b > hi:
            a, b = a - (b - hi), hi
        if a < lo:
            if last and b - lo >= 0.7:
                # the end beat only ever shortens: sliding it later put its
                # last second on a frame the model had not seen clean (the
                # snow walk's man was out of the frame, "a jacket edge", 4
                # of 4 judgements)
                a = lo
            else:
                b, a = min(hi, b + (lo - a)), lo
        a, b = max(a, lo), min(b, hi)
        if b - a >= 0.7:
            segs.append([round(a, 2), round(b, 2)])
    out = []
    for k, (a, b) in enumerate(segs):
        if out and a < out[-1][1] + GAP_MIN_S:
            # a jump must skip enough of the take to read as one: the later
            # beat starts later, or (too short then) gives way; the end beat
            # never gives way, the beat before it shortens instead
            a2 = out[-1][1] + GAP_MIN_S
            if b - a2 >= BEAT_MIN_S * 0.8:
                a = a2
            elif k == len(segs) - 1:
                cut = a - GAP_MIN_S
                if cut - out[-1][0] >= BEAT_MIN_S * 0.8:
                    out[-1][1] = round(cut, 2)
                else:
                    out.pop()
            else:
                continue
        out.append([round(a, 2), round(b, 2)])
    if len(out) < 3:
        return []
    total = sum(b - a for a, b in out)
    if total < 5.0 or total > span - 0.6:
        return []
    return out


def _box_at(looks, t):
    """The subject box at source second ``t``, interpolated between clean
    looks (held before the first and after the last)."""
    pts = [(lk[0], lk[1]) for lk in sorted(looks, key=lambda lk: lk[0]) if lk[2] and lk[1]]
    if not pts:
        return None
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, b0), (t1, b1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            f = (t - t0) / max(1e-6, t1 - t0)
            return tuple(a + (b - a) * f for a, b in zip(b0, b1))
    return pts[-1][1]


def fill_z(box, fill, z_max):
    """The zoom that shows the subject at ``fill`` of the frame height (and
    never wider than the frame), between Z_FLOOR and ``z_max``."""
    bh = max(0.04, box[3] - box[1])
    bw = max(0.04, box[2] - box[0])
    return round(_clamp(min(fill / bh, 0.92 / bw), Z_FLOOR, z_max), 3)


def frame_at(box, z, bias=0.0):
    """The crop window ([x0, y0, x1, y1], fractions, the frame's own aspect)
    at zoom ``z`` on the subject. A subject that fills the window
    (FILL_TOP or more of its height) is centred, but never with more than
    GROUND of the window below its base (the gym clip: "the lower third is
    sink"). One that cannot fill it (a small subject at the deepest crop)
    sits with HEAD_ROOM of the window above its top, the rule of thirds: the
    old base pin left the head at y 0.72 under empty wall (QB2: the gym
    clip's last beat, the snow walk's whole middle, every judgement).
    ``bias`` moves the subject down the frame by that share of the window
    (room above it for a title)."""
    half = 0.5 / max(1.0, z)
    win = 2 * half
    cx = (box[0] + box[2]) / 2.0
    if box[3] - box[1] < FILL_TOP * win:
        y0 = _clamp(box[1] - (HEAD_ROOM + bias) * win, 0.0, 1.0 - win)
    else:
        cy = (box[1] + box[3]) / 2.0 - bias * win
        cy = min(cy, box[3] + GROUND * win - half)
        y0 = _clamp(cy - half, 0.0, 1.0 - win)
    x0 = _clamp(cx - half, 0.0, 1.0 - win)
    return [round(x0, 4), round(y0, 4), round(x0 + win, 4),
            round(y0 + win, 4)]


def _key(t, box, z, bias=0.0):
    return {"t": round(t, 3), "rect": frame_at(box, z, bias),
            "strength": round(z - 1.0, 3)}


def _upper(box, share=0.55):
    return (box[0], box[1], box[2], box[1] + share * (box[3] - box[1]))


def z_max_for(width):
    """The deepest crop: about 3x enlargement of the source on a 1080-wide
    output at most (a 720-wide phone clip stops at 2.1x, a 1080-wide one at
    2.4x)."""
    try:
        w = float(width)
    except (TypeError, ValueError):
        w = 1080.0
    return round(_clamp(3.15 * w / 1080.0, 1.6, 2.4), 3)


def beat_z(box, i, z_max):
    """A middle beat's zoom: close on odd beats, medium on even ones, and
    always at least ALTERNATE apart (a small subject caps both at z_max, and
    two cuts at the same framing read as a glitch, not a new camera)."""
    close = fill_z(box, FILLS[1], z_max)
    medium = max(Z_FLOOR, min(fill_z(box, FILLS[0], z_max), close - ALTERNATE,
                              close * MEDIUM_RATIO))
    return close if i % 2 else medium


def keyframes(looks, segs, z_max=1.9):
    """(keyframes, snap, first_window) for add_zoom_path over the cut
    program: one framing per beat (medium and close alternate, so each cut
    reads as a new camera), the first beat easing in under the title, the
    longest middle beat pushing in slowly, the last beat snapping in to the
    hero framing (the subject's upper part) END_HIT_S before the end and
    drifting on (live). ``snap`` is the program second of the snap (the
    flash lands on it); ``first_window`` frames the first beat (the title
    keeps clear of the subject inside it)."""
    if not segs:
        return [], None, None
    keys, t_out = [], 0.0
    lengths = [b - a for a, b in segs]
    middles = list(range(1, len(segs) - 1))
    push_at = max(middles, key=lambda i: lengths[i]) if middles else None
    snap, first_win = None, None
    for i, (a, b) in enumerate(segs):
        o0, o1 = t_out, t_out + (b - a)
        ba, bb = _box_at(looks, a), _box_at(looks, b)
        if ba is None or bb is None:
            return [], None, None
        if i == 0:
            # tight from the first frame (judges, the gym clip: "start
            # already punched in on the torso, not wide on the sink"), then
            # an eased push under the title
            z1 = fill_z(bb, OPEN_FILL, z_max)
            z0 = max(Z_FLOOR, z1 - PUSH)
            first_win = frame_at(ba, z0, 0.12)
            keys += [_key(o0, ba, z0, 0.12), _key(o1 - 0.02, bb, z1, 0.12)]
        elif i == len(segs) - 1:
            snap = round(max(o0 + 0.5, o1 - END_HIT_S), 3)
            bs = _box_at(looks, a + (snap - o0))
            zh = max(fill_z(_upper(bs), END_FILL, z_max), 1.0)
            zs = fill_z(bs, FILLS[i % 2], z_max)
            if zh - zs < 0.3:
                zs = max(Z_FLOOR, zh - 0.3)
                zh = max(zh, zs + 0.3)
            # the hero framing travels from where the subject was at the
            # snap to where the end beat's own clean look saw him, which the
            # beat ends just after (a subject walking out of the frame:
            # held still, the window lost him in its last 0.3 s, q6c3)
            keys += [_key(o0, ba, zs), _key(snap - 0.03, bs, zs),
                     _key(snap + 0.06, _upper(bs), zh),
                     _key(o1, _upper(bb), min(z_max + 0.1, zh + 0.05))]
        else:
            z0 = beat_z(ba, i, z_max)
            z1 = z0 if i != push_at else min(z_max, z0 + PUSH)
            under = TITLE_BIAS if o0 < TITLE_HOLD_S else 0.0
            keys += [_key(o0, ba, z0, under), _key(o1 - 0.02, bb, z1, under)]
        t_out = o1
    return keys, snap, first_win


def title_y(looks, first_win, seg):
    """Where the hook title sits on the first beat so it never crosses the
    subject (as framed by the first beat's window): above it (the
    references' place) or below, else None (the title's own default)."""
    import short_clip
    if not first_win or not seg:
        return None
    x0, y0, x1, y1 = first_win
    h = max(1e-3, y1 - y0)
    boxes = []
    for t in (seg[0], (seg[0] + seg[1]) / 2.0, seg[1]):
        b = _box_at(looks, t)
        if b:
            boxes.append((t - seg[0], (b[0], (b[1] - y0) / h, b[2], (b[3] - y0) / h)))
    return short_clip.title_y(boxes, until=seg[1] - seg[0] + 1.0)


def _z_max(ctx):
    video = (ctx.index or {}).get("video") or {}
    try:
        w, h = float(video.get("width") or 0), float(video.get("height") or 0)
    except (TypeError, ValueError):
        w = h = 0.0
    return z_max_for(min(w, h) or 1080)


def plan_for(ctx, looks, duration):
    """plan() at this clip's deepest crop."""
    return plan(looks, duration, _z_max(ctx))


def title_for(ctx, looks, segs):
    """The hook title's y for the cut (see title_y)."""
    _k, _s, first_win = keyframes(looks, segs, _z_max(ctx))
    return title_y(looks, first_win, segs[0] if segs else None)


def apply(ctx, steps, started, run, looks, segs):
    """Cut the clip to its beats (keep_segments). True when the cut landed."""
    if not segs:
        return False
    res = run(ctx, steps, "keep_segments", {"segments": segs,
                                            "snap_to_words": False}, started)
    return res is not None and not str(res).lstrip().upper().startswith(
        ("REJECTED", "FAILED", "CORRECTION"))


def camera(ctx, steps, started, run, looks, segs):
    """The beats' framings (one add_zoom_path) and the end beat's flash.
    Returns the add_zoom_path result or None."""
    keys, snap, _first = keyframes(looks, segs, _z_max(ctx))
    if len(keys) < 4:
        return None
    res = run(ctx, steps, "add_zoom_path", {
        "keyframes": keys, "ease": "cubic_in_out",
        "purpose": "jump cuts on a silent clip: each beat framed on the "
                   "subject, an eased push-in under the title, the end beat "
                   "snaps in to the hero framing"}, started)
    if res is not None and snap is not None and not str(res).lstrip().upper(
            ).startswith(("REJECTED", "FAILED", "CORRECTION")):
        run(ctx, steps, "add_stylize", {
            "kind": "flash", "start": round(max(0.0, snap - 0.02), 3),
            "end": round(snap - 0.02 + FLASH_S, 3), "intensity": 1.0},
            started)
    return res
