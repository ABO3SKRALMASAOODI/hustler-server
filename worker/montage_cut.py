"""A highlight montage at the references' rhythm, ending on a hit (Q6, 30 Oct 2026).

The game fixture (74 s of a rocket launch, silent) scored 8.0-8.5 with C2's
montage, and every judgement asked for the same three things:

- the references' rhythm: "tighten the cuts toward 0.3-0.5 s per shot", "drop
  the repeated Earth-window frames that slow 4.7-5.1 s" (C2 cut every shot to
  0.45-0.5 s, opened on 0.8 s of a static tower and could take only one
  moment from a long shot, so the landing shot gave the descent and never the
  splashdown);
- an ending on a hit: "run the landing shot through the splashdown flash or
  explosion and cut on that hit, instead of holding about 1 s on the soft
  descent" (C2 stretched the payoff shot to a 1.0 s hold);
- framing that does not depend on a face (QI's 067cb854 made it always crop;
  the crop was the frame's centre).

So, for the First cut's engine accounts only (trial and new subscribers):
the shots run 0.3-0.48 s (the most moving ones shortest), a long shot may
give two moments at least SECOND_GAP_S apart (a jump cut inside the shot),
the reel opens on a strong moving moment, the payoff shot is cut so it ENDS
just after its biggest motion peak, and one white flash lands on the last
frames. The 9:16 crop follows the action in each shot
(first_cut_visual.action_follow, the explainer's crop).
"""

SCOUT_SYSTEM = (
    "The numbered frames are the shots of a highlight reel cut from one "
    "video, in order. 1) TITLE: the poster title a top editor would put "
    "over its opening: 1 to 3 words naming what is visibly happening "
    "(\"LAUNCH *DAY*\", \"Game *winner*\"), nothing you cannot see, no "
    "brand or name unless legible on screen, exactly one word in *stars*. "
    "2) END: the number of the shot the reel should END on: its payoff, "
    "where the subject is visible at the peak of its action (the landing, "
    "the goal, the reveal), late in the order; never an empty aftermath "
    "(only smoke, sky, water or a crowd without the subject); later shots "
    "are dropped. 3) DROP: the numbers of shots that are broken (heavy "
    "pixelation, blur, black), off-topic, or show no subject. 4) BOXES: for "
    "every shot, the box (pixel coordinates of THAT image: x0 y0 x1 y1) "
    "around its main subject (the vehicle, the player, the ball, the "
    "person), tight even when it is small. Answer with JSON only: "
    "{\"title\": \"<1-3 words>\", \"end\": <n>, \"drop\": [<n>, ...], "
    "\"boxes\": [{\"n\": <n>, \"box\": [x0, y0, x1, y1]}]}.")
TARGET_S = 12.0             # references: 7-15 s
MIN_TOTAL_S = 6.0           # a reel shorter than this has its shots lengthened
MAX_SPAN_S = 0.45          # ... never past this (judges: 0.25-0.45, 2-4 cuts/s)
MAX_SHOTS = 30
SHOT_FAST, SHOT_MID, SHOT_SLOW = 0.3, 0.38, 0.45  # by motion rank (refs 0.15-0.5)
OPEN_S = 0.8                # the first shot carries the title's first read
SECOND_GAP_S = 1.4          # two moments of one shot are this far apart (a jump)
PIECE_S = 2.0
CAP_EVERY_S = 2.2
HIT_BEFORE_S = 0.48         # the last shot runs this long up to its peak
HIT_AFTER_S = 0.14          # ... and ends this soon after it (the hit)
HIT_REACH_S = 0.6           # the peak is looked for this close around the payoff
                            # window (the scout saw THAT moment; further on, the
                            # landing shot was smoke over the sea)
FLASH_S = 0.1               # one white flash on the last frames
CUT_SKIP_S = 0.26           # a frame difference this close after a cut is the cut


def _pieces(shots, duration):
    spans = []
    for k, sh in enumerate(shots or []):
        try:
            a, b = float(sh["start"]), float(sh["end"])
        except (KeyError, TypeError, ValueError):
            continue
        while b - a > PIECE_S + 0.2:
            spans.append((a, a + PIECE_S, k))
            a += PIECE_S
        spans.append((a, b, k))
    if not spans:
        t = 0.0
        while t < duration - 0.5:
            spans.append((t, min(duration, t + PIECE_S), int(t // PIECE_S)))
            t += PIECE_S
    return spans


def per_shot_cap(length):
    """How many moments one shot may give: one, plus one per CAP_EVERY_S of
    it, at most five."""
    return int(min(5, 1 + max(0.0, float(length)) // CAP_EVERY_S))


def windows(curve, shots, duration, target=TARGET_S):
    """[[start, end]] source windows, in order, or None (fewer than four
    moving moments): each moving piece's peak moment, the strongest first
    until ``target`` seconds, a long shot giving up to per_shot_cap moments SECOND_GAP_S apart,
    shorter the more it moves; the reel opens on a moment that moves at
    least as much as the median pick."""
    import numpy as np
    times, diff, luma = curve
    cands = []
    pieces = _pieces(shots, duration)
    shot_len = {}
    for a, b, shot in pieces:
        shot_len[shot] = shot_len.get(shot, 0.0) + (b - a)
    for a, b, shot in pieces:
        if b - a < 0.4:
            continue
        # the first sample after a shot starts measures the cut itself, not
        # motion inside the shot
        sel = (times >= a + CUT_SKIP_S) & (times < b - 0.1)
        if sel.sum() < 1:
            continue
        if float(np.median(luma[sel])) < 14.0:
            continue                        # black or nearly black
        d = diff[sel]
        score = float(np.percentile(d, 75))
        if score < 0.6:
            continue                        # frozen or static
        peak = float(times[sel][int(np.argmax(d))])
        cands.append({"score": score, "peak": peak, "piece": (a, b),
                      "shot": shot, "cap": per_shot_cap(shot_len.get(shot, 0.0))})
    if len(cands) < 4:
        return None
    ranked = sorted(cands, key=lambda c: -c["score"])
    n = len(ranked)
    for r, c in enumerate(ranked):
        c["span"] = SHOT_FAST if r < n / 3 else (SHOT_MID if r < 2 * n / 3
                                                  else SHOT_SLOW)
    chosen, total, per_shot = [], 0.0, {}
    for c in ranked:
        if total >= target or len(chosen) >= MAX_SHOTS:
            break
        mine = per_shot.get(c["shot"], [])
        if len(mine) >= c["cap"] or any(
                abs(c["peak"] - m) < SECOND_GAP_S for m in mine):
            continue
        per_shot[c["shot"]] = mine + [c["peak"]]
        chosen.append(c)
        total += c["span"]
    chosen.sort(key=lambda c: c["peak"])
    med = float(np.median([c["score"] for c in chosen]))
    while len(chosen) > 4 and chosen[0]["score"] < med:
        chosen.pop(0)                       # never open on a weak moment
    out = []
    for k, c in enumerate(chosen):
        a, b = c["piece"]
        span = OPEN_S if k == 0 else c["span"]
        w0 = min(max(c["peak"] - 0.45 * span, a), max(a, b - span))
        w1 = min(b, w0 + span)
        if out and w0 <= out[-1][1] + 0.05:
            w0 = out[-1][1] + 0.05
        if w1 - w0 >= 0.25:
            out.append([round(w0, 2), round(w1, 2)])
    return out if len(out) >= 4 else None


def _shot_of(shots, t, duration):
    for sh in shots or []:
        try:
            a, b = float(sh["start"]), float(sh["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if a - 1e-3 <= t < b:
            return a, b
    return max(0.0, t - PIECE_S), min(duration, t + PIECE_S)


def hit_end(curve, wins, shots, duration):
    """``wins`` with the last (payoff) window cut to END on the biggest
    motion peak around it (HIT_REACH_S, inside its shot): the hit, not a
    hold."""
    import numpy as np
    if not wins or len(wins) < 2:
        return wins
    times, diff, _luma = curve
    out = [list(w) for w in wins]
    a, b = out[-1]
    s0, s1 = _shot_of(shots, (a + b) / 2.0, duration)
    lo = max(s0 + CUT_SKIP_S, a - HIT_REACH_S)
    hi = min(s1 - 0.1, b + HIT_REACH_S, duration - 0.05)
    sel = (times >= lo + HIT_BEFORE_S * 0.5) & (times <= hi)
    if sel.sum() < 1:
        return out
    peak = float(times[sel][int(np.argmax(diff[sel]))])
    w0 = max(lo, peak - HIT_BEFORE_S)
    w1 = min(hi, peak + HIT_AFTER_S)
    prev = out[-2]
    if w0 < prev[1] + 0.05:
        if s0 <= (prev[0] + prev[1]) / 2.0 < s1 and len(out) > 4:
            out.pop(-2)                 # a moment of the same shot gives way
        else:
            w0 = prev[1] + 0.05
    if w1 - w0 >= 0.35:
        out[-1] = [round(w0, 2), round(w1, 2)]
    return out


def fit_length(wins, shots, duration, least=MIN_TOTAL_S, most_span=MAX_SPAN_S):
    """``wins`` lengthened evenly (each about its own middle, inside its
    shot, never into a neighbour, never past ``most_span``) until the reel
    runs ``least`` seconds: after the scout drops broken and off-topic shots
    a 74 s launch kept 15 moments, 5.5 s (references: 7-13 s). The payoff
    (last) window keeps its hit."""
    if not wins or len(wins) < 3:
        return wins
    out = [list(w) for w in wins]
    total = sum(b - a for a, b in out)
    if total >= least:
        return out
    body = out[:-1]
    grow = (least - total) / max(1, len(body))
    for k, (a, b) in enumerate(body):
        s0, s1 = _shot_of(shots, (a + b) / 2.0, duration)
        want = min(most_span, (b - a) + grow)
        extra = max(0.0, want - (b - a))
        lo = max(s0 + 0.05, out[k - 1][1] + 0.05 if k else 0.0)
        hi = min(s1 - 0.05, out[k + 1][0] - 0.05)
        a2 = max(lo, a - extra / 2.0)
        b2 = min(hi, a2 + (b - a) + extra)
        a2 = max(lo, min(a2, b2 - (b - a)))
        out[k] = [round(a2, 2), round(max(b, b2), 2)]
    return out


def end_flash(ctx, steps, started, run):
    """One white flash on the last frames of the program (R01 ends on a white
    flash). Returns the add_stylize result or None."""
    import schemas
    try:
        prog = float(schemas.program_duration(ctx.latest_edl()["json"]))
    except Exception:
        return None
    if prog < 2.0:
        return None
    return run(ctx, steps, "add_stylize", {
        "kind": "flash", "start": round(prog - FLASH_S, 3),
        "end": round(prog, 3), "intensity": 1.0}, started)


def parse_boxes(items, sizes):
    """{n: (x0, y0, x1, y1) fractions} from the scout's pixel boxes; boxes
    that are not a box, outside the image or the whole image are dropped."""
    out = {}
    for item in items or []:
        try:
            n = int(item.get("n"))
            x0, y0, x1, y1 = [float(v) for v in item.get("box")]
        except (TypeError, ValueError, AttributeError):
            continue
        if n not in sizes:
            continue
        w, h = sizes[n]
        b = [x0 / w, y0 / h, x1 / w, y1 / h]
        if not (-0.02 <= b[0] < b[2] <= 1.02 and -0.02 <= b[1] < b[3] <= 1.02):
            continue
        if (b[2] - b[0]) * (b[3] - b[1]) > 0.85:
            continue
        out[n] = tuple(min(max(v, 0.0), 1.0) for v in b)
    return out


def crop_x(box, ww):
    """The 9:16 crop's centre (fraction of the width) on a subject box, kept
    inside the frame; the frame's centre without a box."""
    x = 0.5 if not box else (box[0] + box[2]) / 2.0
    return round(min(max(x, ww / 2.0), 1.0 - ww / 2.0), 4)


def _box_for(pairs, a, b):
    """The scout's box for the program window [a, b] (source seconds): the
    picked window it overlaps most (windows move a little: the payoff's
    hit, keep_segments' own snapping)."""
    best, best_ov = None, 0.0
    for (w0, w1), box in pairs or []:
        ov = min(b, w1 + 0.6) - max(a, w0 - 0.6)
        if ov > best_ov:
            best, best_ov = box, ov
    return best


def follow(ctx, steps, started, run):
    """Crop the montage to 9:16 with each window's crop centred on its
    subject as the scout boxed it (the frame's centre without a box), still
    inside a window: 0.3-0.5 s is too short for a move. (A colour/motion/
    detail window, first_cut_visual.action_boxes, found the blue sky beside
    a grey rocket.) True when the crop was written."""
    import schemas
    res = run(ctx, steps, "auto_reframe", {"ratio": "9:16", "mode": "crop",
                                           "follow": False}, started)
    if res is None or str(res).lstrip().upper().startswith(
            ("REJECTED", "FAILED", "CORRECTION")):
        return False
    pairs = getattr(ctx, "first_cut_montage_boxes", None) or []
    try:
        edl = dict(ctx.latest_edl()["json"])
        video = (ctx.index or {}).get("video") or {}
        ww = (9 / 16.0) / (float(video["width"]) / float(video["height"]))
    except Exception:
        return True
    if not pairs or not 0.1 < ww < 0.95:
        return True
    spans = []
    for a, b in [tuple(map(float, k)) for k in edl.get("keep") or []]:
        if b - a < 0.1:
            continue
        box = _box_for(pairs, a, b)
        if box is None:
            continue
        spans.append({"t0": round(a, 3), "t1": round(b, 3),
                      "k": [[round((a + b) / 2.0, 3), crop_x(box, ww), 0.5]]})
    if not spans:
        return True
    frame = dict(edl.get("frame") or {})
    frame["follow"] = spans[:schemas.FOLLOW_MAX_SPANS]
    edl["frame"] = frame
    try:
        out = ctx.write_edl(edl, "the 9:16 crop sits on the subject in each "
                                 "shot of the highlight")
        steps.append({"tool": "montage_follow", "ok": str(out).startswith(
            ("EDL v", "NO CHANGE")), "note": str(out)[:160]})
    except Exception as exc:  # noqa: BLE001 — the centre crop stands
        steps.append({"tool": "montage_follow", "ok": False,
                      "note": f"{type(exc).__name__}: {str(exc)[:140]}"})
    return True
