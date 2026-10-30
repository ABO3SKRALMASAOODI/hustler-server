"""The trial agent's own turn on top of the First cut (Q3, 30 Oct 2026).

Every trial account gets an automatic First cut; the typed request then runs
one agent turn on the Claude trial lane. Measured on the C2 harness (Opus
judge, two judgements each) that turn mostly KEPT or LOWERED the First cut:
"nothing was changed" for a captions request (a tie), a clean-up that
restored the whole take and lost the hook and captions (vlog 3.75 against
6.5), a highlight re-cut into long shots (game 5.75 against 7.25), 3-5
renders and 170-290 s of wall clock for no gain.

This module gives the turn a deterministic head start before the model's
first call, for the generic first requests the funnel actually sees (make it
a short / captions / a hook / the best moment / a highlight / clean it up /
cut the boring parts). It never runs for a specific instruction ("make the
captions red", "cut the part at 0:12"), which the agent handles as before.

  * repair: a full-frame card with nothing on it (its slam was refused) is
    removed (the judges' "empty navy frame at 15.2 s").
  * lift: on a talking head, every stretch of more than LIFT_GAP_S with no
    visible event gets one: a punch to a second, closer framing for one
    phrase (a hard cut in and back out, the references' two-camera look),
    or, where the frame has no zoom room left, the stressed word slammed in
    big type. Sparse by rule (owner: zooms read childish when overused).
  * clean-up of the WHOLE take: when the user asked to clean up their footage
    and the First cut kept only one moment of it, the First cut's own chain
    (clean ends, pauses, fillers, framing, the hook, slams, captions) is run
    on the whole source instead of the model restoring and re-cutting it by
    hand (C1: 5-6 renders and ~300 s, opening on the mic check).

The model then gets one short note saying exactly what was done, renders
once and replies. Only the trial lane calls prepare() (agent_loop gates it
on ctx.claude_trial): grandfathered subscribers never reach this code.
"""
import json
import re
import time

import config

LIFT_GAP_S = 4.0           # a talking head never holds one look longer
LIFT_MIN_EDGE_S = 1.8      # an added event stays this clear of its neighbours
PUNCH_STRENGTH = 0.2       # 100% -> 120%: a second framing the eye reads
PUNCH_MIN_ROOM = 0.15      # below this a punch is invisible: slam instead
VISIBLE_ZOOM = 0.15        # the judges read no change in a smaller step (C2
                           # phone: a 0.12 cut step inside "11 s of one look")
PUNCH_HOLD_S = (1.4, 2.6)  # one phrase, then a hard cut back out
WHOLE_KEPT_SHARE = 0.6     # a First cut keeping less than this of the take
                           # is a "moment", not the user's whole footage

# ── which requests get the head start ─────────────────────────────────────

_CLEANUP = re.compile(
    r"\b(clean(?:ing)?[\s-]*(?:it\s+)?up|clean\s+(?:my|the|this)\s+(?:footage|video|clip|vlog)"
    r"|tidy(?:\s+up)?|tighten\s+(?:it|my|the|this)"
    r"|remove\s+(?:the\s+)?(?:pauses|silences?|dead\s+air|filler(?:\s+words)?|ums?)"
    r"|cut\s+(?:out\s+)?(?:the\s+)?(?:pauses|silences?|dead\s+air|ums?))\b", re.I)
_BORING = re.compile(r"\b(boring|dull|slow)\s+(parts?|bits?|sections?|stuff)\b"
                     r"|\bcut\s+the\s+fluff\b", re.I)
_GENERIC = re.compile(
    r"\b(short|reel|reels|tiktok|shorts|vertical|9\s*:\s*16|caption(?:s|ed)?|"
    r"subtitles?|hook|best\s+(?:moment|part|bit|clip|bits|parts)|highlights?|"
    r"montage|edit\s+(?:it|this|my)|make\s+(?:it|this)\s+(?:good|better|pop|"
    r"engaging|viral)|polish|post(?:able)?|instagram|youtube)\b", re.I)
# A specific instruction: the agent works it as before, nothing is added.
_SPECIFIC = re.compile(
    r"\b(red|blue|green|yellow|white|black|orange|purple|pink|gold|font|"
    r"colou?r|music|song|soundtrack|sfx|sound\s+effects?|logo|watermark|"
    r"speed|slow[\s-]?mo|faster|slower|zoom|transition|b-?roll|"
    r"remove\s+(?:the\s+)?(?:part|bit|section|word|sentence|intro|outro|"
    r"beginning|end(?:ing)?)|at\s+\d|\d+\s*:\s*\d\d|\d+(?:\.\d+)?\s*s(?:ec)?\b|"
    r"seconds?|minutes?|landscape|16\s*:\s*9|square|1\s*:\s*1|4\s*:\s*5|"
    r"translate|language|spanish|arabic|french|voice\s*over|narrat)\b", re.I)
_URL = re.compile(r"https?://|www\.", re.I)


def intent(text):
    """'cleanup', 'generic' or None (a specific instruction or a brief)."""
    t = " ".join(str(text or "").split())
    if not t or _URL.search(t) or len(t.split()) > 18:
        return None
    if _SPECIFIC.search(t):
        return None
    if _CLEANUP.search(t) or _BORING.search(t):
        # QI (30 Oct): "cut the boring parts" asks for the user's footage
        # minus its dead weight, not one 36 s moment of a 10-min talk (the
        # matrix: "not what the user asked", a hard fault, twice). It takes
        # the whole-take clean-up when the First cut kept one moment.
        return "cleanup"
    if _GENERIC.search(t):
        return "generic"
    return None


# ── the First cut this turn builds on ─────────────────────────────────────

def first_cut_state(conn, session_id):
    """The First cut's meta when it is still the project's current version
    (nobody edited since), else None."""
    with conn.cursor() as cur:
        cur.execute("""SELECT meta FROM chat_messages
                       WHERE session_id = %s AND role = 'assistant'
                         AND meta->>'kind' = 'first_cut'
                       ORDER BY id DESC LIMIT 1""", (session_id,))
        row = cur.fetchone()
    if not row:
        return None
    meta = row["meta"] if isinstance(row, dict) else row[0]
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            return None
    return meta or None


# ── visible events on the program ─────────────────────────────────────────

def visible_events(edl, prog):
    """Program seconds where the picture visibly changes: graphics and cards
    landing or leaving, inserts, overlays, a zoom of strength >= VISIBLE_ZOOM
    starting or ending (a framing that covers the whole program, the band
    crop, is not an event; nor is a shallower push or cut step)."""
    ev = [0.0, float(prog)]
    for m in edl.get("motion") or []:
        for f in ("start", "end"):
            if m.get(f) is not None:
                ev.append(float(m[f]))
    for key in ("vectors", "texts"):
        for it in edl.get(key) or []:
            for f in ("start", "end"):
                if it.get(f) is not None:
                    ev.append(float(it[f]))
    for it in edl.get("overlays") or []:
        try:
            s = float(it["start"])
            ev += [s, s + float(it.get("duration_s") or 0)]
        except (KeyError, TypeError, ValueError):
            continue
    for it in edl.get("inserts") or []:
        try:
            s = float(it.get("at_output_s"))
            ev += [s, s + float(it.get("duration_s") or 0)]
        except (TypeError, ValueError):
            continue
    fx = edl.get("effects") or {}
    for z in fx.get("zooms") or []:
        try:
            s, e = float(z["start"]), float(z["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if s <= 0.05 and e >= prog - 0.05:
            continue                      # a framing, not an event
        if float(z.get("strength") or 0) >= VISIBLE_ZOOM:
            ev += [s, e]
    return sorted(set(round(t, 2) for t in ev if 0.0 <= t <= prog + 1e-6))


def static_gaps(edl, prog, gap_s=LIFT_GAP_S):
    ev = visible_events(edl, prog)
    return [(a, b) for a, b in zip(ev, ev[1:]) if b - a > gap_s]


# ── the steps ─────────────────────────────────────────────────────────────

class Steps:
    """Runs shipped tools through agent_tools.execute, records each like an
    agent step (activity row, write_calls) through `post`, and keeps a
    plain-words list of what landed for the model's note and the reply."""

    def __init__(self, ctx, post=None):
        self.ctx, self.post = ctx, post
        self.rows, self.applied = [], []

    def run(self, name, args):
        import agent_tools
        t0 = time.monotonic()
        try:
            result = agent_tools.execute(self.ctx, name, args)
        except Exception as exc:  # noqa: BLE001 — one step never sinks the turn
            result = f"FAILED: {type(exc).__name__}: {str(exc)[:200]}"
        ok = isinstance(result, str) and result.startswith("EDL v")
        self.rows.append({"tool": name, "ok": ok,
                          "s": round(time.monotonic() - t0, 2),
                          "note": str(result or "")[:160]})
        if ok and name in agent_tools.WRITE_TOOLS:
            self.ctx.write_calls.append(name)
        if self.post is not None:
            try:
                self.post(name, args, result)
            except Exception:  # noqa: BLE001 — the feed row is cosmetic
                pass
        return result if ok else None

    def record(self, rows):
        """Steps another chain already ran (first_cut.video_chain)."""
        import agent_tools
        for row in rows or []:
            if not row.get("tool") or row.get("skipped"):
                continue
            self.rows.append(row)
            if row.get("ok") and row["tool"] in agent_tools.WRITE_TOOLS:
                self.ctx.write_calls.append(row["tool"])
            if self.post is not None:
                try:
                    self.post(row["tool"], {}, row.get("note") or "")
                except Exception:  # noqa: BLE001
                    pass


def _talking(ctx):
    import first_cut
    faces = first_cut._faces(ctx.index)
    return bool(first_cut._speech(ctx.index) and faces
                and faces["share"] >= 0.5)


def repair_empty_cards(ctx, steps):
    """Remove a full-frame card with no graphic on it (C2's vlog First cut:
    the slam was refused after its card was placed, leaving 1.4 s of flat
    navy). Returns how many were removed."""
    edl = ctx.latest_edl()["json"]
    motion = edl.get("motion") or []
    removed = 0
    for v in list(edl.get("vectors") or []):
        try:
            full = (float(v.get("width") or 0) >= 0.95
                    and float(v.get("height") or 0) >= 0.95
                    and float(v.get("opacity") or 1) >= 0.9)
            s, e = float(v["start"]), float(v["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not full:
            continue
        covered = sum(max(0.0, min(e, float(m.get("end") or 0))
                          - max(s, float(m.get("start") or 0)))
                      for m in motion)
        if covered < 0.5 * (e - s) and steps.run(
                "remove_vector_graphic", {"id": v.get("id")}):
            removed += 1
    if removed:
        steps.applied.append("an empty card removed")
    return removed


_STOP = frozenset("""a an the and or but so to of in on at for with from by is
are was were be been being it its it's this that these those there their they
them we our you your i me my he she his her him not no yes just like really
very about what when where which who how than then also too can could would
should will shall do does did have has had going gonna kind sort thing things
because actually basically literally right okay ok well um uh""".split())


def _word_pick(ctx, tl, a, b, edge=LIFT_MIN_EDGE_S):
    """The most stressed content word whose start falls inside (a+edge,
    b-edge) of the program: (program_t0, source_word, index)."""
    words = [w for w in (ctx.index or {}).get("words") or []
             if w.get("w") is not None and w.get("t0") is not None]
    cands = []
    for i, w in enumerate(words):
        tok = re.sub(r"[^\w'’-]", "", str(w["w"])).casefold()
        if len(tok) < 4 or tok in _STOP:
            continue
        pt = tl.src_to_out(float(w["t0"]))
        if pt is None or not (a + edge <= pt <= b - edge):
            continue
        cands.append((i, w, float(pt)))
    if not cands:
        return None
    try:
        import agent_tools
        import perception
        p = agent_tools._get_perception(ctx)
        stress = perception.word_stress(p, [c[1] for c in cands])
    except Exception:  # noqa: BLE001 — length and centrality decide instead
        stress = [0.0] * len(cands)
    mid = (a + b) / 2.0
    best = max(range(len(cands)), key=lambda k: (
        float(stress[k]) + 0.03 * min(len(str(cands[k][1]["w"])), 10)
        - 0.05 * abs(cands[k][2] - mid)))
    i, w, pt = cands[best]
    return pt, w, i


def _phrase_end(ctx, tl, word, pt, limit):
    """Where a punch on `word` cuts back out: the end of its sentence or the
    next cut, held PUNCH_HOLD_S, never past `limit`."""
    lo, hi = PUNCH_HOLD_S
    end = pt + hi
    for s in (ctx.index or {}).get("sentences") or []:
        try:
            if float(s["t0"]) - 1e-6 <= float(word["t0"]) <= float(s["t1"]):
                so = tl.src_to_out(float(s["t1"]))
                if so is not None and so > pt + lo:
                    end = min(end, so)
                break
        except (KeyError, TypeError, ValueError):
            continue
    seg = tl.seg_program_range(float(word["t0"]))
    if seg and seg[1] > pt + lo:
        end = min(end, seg[1])
    return round(min(end, limit), 2)


def lift_static(ctx, steps, most=None):
    """One visible event inside each stretch of a talking head that holds
    one look for more than LIFT_GAP_S. Returns how many landed."""
    import agent_tools
    import schemas
    from timeline import Timeline
    edl = ctx.latest_edl()["json"]
    prog = float(schemas.program_duration(edl))
    try:
        tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    except Exception:  # noqa: BLE001
        return 0
    hook_end = max([float(m.get("end") or 0) for m in edl.get("motion") or []
                    if m.get("template") == "hook_title"] or [0.0])
    gaps = [(max(a, hook_end), b) for a, b in static_gaps(edl, prog)
            if b > hook_end + LIFT_GAP_S and a < prog - 1.5]
    gaps = [(a, min(b, prog - 0.6)) for a, b in gaps if min(b, prog - 0.6) - a > LIFT_GAP_S]
    if not gaps:
        return 0
    most = most if most is not None else max(1, int(prog // 7))
    slam_y = next((float((m.get("params") or {}).get("y"))
                   for m in edl.get("motion") or []
                   if m.get("template") == "word_slam"
                   and (m.get("params") or {}).get("y") is not None
                   and float((m.get("params") or {}).get("y")) != 0.5), None)
    if slam_y is None:
        # no slam of the First cut's to align with: the chest band its
        # captions use, never the template's default over the face (Q3:
        # a slam over the mouth capped talk10best at 5)
        style = ((edl.get("captions") or {}).get("style") or {}) \
            if isinstance(edl.get("captions"), dict) else {}
        if style.get("anchor_y") is not None:
            slam_y = float(style["anchor_y"])
        else:
            import first_cut
            faces = first_cut._faces(ctx.index)
            slam_y = first_cut.chest_y(faces) if faces else None
    zoom_target = next(((z.get("cx"), z.get("cy"))
                        for z in (edl.get("effects") or {}).get("zooms") or []
                        if z.get("cx") is not None and z.get("cy") is not None),
                       (None, None))
    placed = punched = slammed = 0
    for a, b in gaps:
        # a long stretch gets one event per ~LIFT_GAP_S of it
        # (disjoint windows: two added events are at least 2 x
        # LIFT_MIN_EDGE_S apart)
        n = max(1, int((b - a) // (LIFT_GAP_S + 0.4)))
        cuts = [a + (b - a) * k / n for k in range(n + 1)]
        for k in range(n):
            if placed >= most:
                break
            lo, hi = cuts[k], cuts[k + 1]
            pick = _word_pick(ctx, tl, lo, hi)
            if not pick:
                continue
            pt, word, _i = pick
            start = round(max(0.0, pt - 0.06), 2)
            end = _phrase_end(ctx, tl, word, pt, b - 0.4)
            cur = ctx.latest_edl()["json"]
            room, _base, _stacked = agent_tools._zoom_room(ctx, cur, start, end)
            # never on top of another move: stacked zooms render one-frame
            # pops the picture check flags (and the editor then removed)
            overlap = any(
                float(z.get("start") or 0) < end + 0.1
                and float(z.get("end") or 0) > start - 0.1
                and not (float(z.get("start") or 0) <= 0.05
                         and float(z.get("end") or 0) >= prog - 0.05)
                for z in (cur.get("effects") or {}).get("zooms") or [])
            # alternate the two events (a punch, then a word in big type)
            # so a long talk never repeats one device
            want_punch = (punched <= slammed)
            if want_punch and not overlap and end - start >= PUNCH_HOLD_S[0] \
                    and (room is None or room >= PUNCH_MIN_ROOM):
                args = {"start": start, "end": end, "mode": "punch",
                        "strength": PUNCH_STRENGTH,
                        "purpose": "a second, closer framing for one phrase "
                                   "on a stretch that held one look (the "
                                   "references: a visual event every 2-3 s)"}
                if zoom_target[0] is not None:
                    args.update({"cx": zoom_target[0], "cy": zoom_target[1]})
                if steps.run("add_zoom", args):
                    placed += 1
                    punched += 1
                    continue
            text = re.sub(r"[^\w'’%$-]", "", str(word["w"]))
            if not text or slam_y is None:
                continue
            params = {"text": f"*{text}*", "tier": "display", "entrance": "pop",
                      "exit": "cut", "accent": "#FFD23F", "width": 0.82,
                      "role": "condensed"}
            if slam_y is not None:
                params["y"] = slam_y
            s_end = round(min(start + 1.4, b - 0.3), 2)
            if s_end - start >= 0.8 and steps.run("add_motion_graphic", {
                    "template": "word_slam", "start": start, "end": s_end,
                    "params": params,
                    "purpose": "the stressed word slammed as it is said, on "
                               "a stretch that held one look"}):
                placed += 1
                slammed += 1
    if punched:
        steps.applied.append(
            f"{punched} punch-in{'s' if punched > 1 else ''} to a closer "
            "framing where the picture held still")
    if slammed:
        steps.applied.append(
            f"{slammed} more key word{'s' if slammed > 1 else ''} in big type")
    return placed


SCREEN_WINDOW_S = 4.0      # the reading zoom re-aims about this often
SCREEN_MAX_STRENGTH = 0.8  # at most 1.8x on a screen capture
SCREEN_MIN_STRENGTH = 0.2  # a smaller push does not make text readable


def _text_column(boxes):
    """(x0, x1) of the column the text mass sits in: boxes around the
    area-weighted median centre (a docs page's left navigation is another
    column and is left out), its 10th-90th percentile edges."""
    rows = []
    for b in boxes:
        try:
            bx0, by0, bx1, by1 = (float(v) for v in b[:4])
        except (TypeError, ValueError):
            continue
        area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
        if area > 0:
            rows.append(((bx0 + bx1) / 2.0, area, bx0, bx1))
    if len(rows) < 8:
        return None
    rows.sort()
    half, acc, mid = sum(r[1] for r in rows) / 2.0, 0.0, rows[0][0]
    for c, a, _l, _r in rows:
        acc += a
        if acc >= half:
            mid = c
            break
    main = [r for r in rows if abs(r[0] - mid) <= 0.3]
    if len(main) < 5:
        return None
    lefts = sorted(r[2] for r in main)
    rights = sorted(r[3] for r in main)
    x0 = lefts[int(0.15 * len(lefts))]
    x1 = rights[max(0, int(0.85 * len(rights)) - 1)]
    x0, x1 = max(0.0, x0 - 0.02), min(1.0, x1 + 0.02)
    return (x0, x1) if x1 - x0 >= 0.2 else None


def _reading_rect(boxes, col, aspect):
    """[x0, y0, x1, y1] over the text column `col` whose height matches the
    output aspect and holds the most text area of this sample's boxes."""
    x0, x1 = col
    h = min(1.0, (x1 - x0) / max(aspect, 1e-6))
    inside = [b for b in boxes if float(b[2]) > x0 and float(b[0]) < x1]
    if not inside or h >= 0.98:
        return None
    best, best_area = None, 0.0
    for b in inside:
        top = min(max(float(b[1]) - 0.03, 0.0), 1.0 - h)
        area = sum((min(float(c[2]), x1) - max(float(c[0]), x0))
                   * max(0.0, min(float(c[3]), top + h) - max(float(c[1]), top))
                   for c in inside)
        if area > best_area:
            best, best_area = top, area
    if best is None:
        return None
    return [round(x0, 3), round(best, 3), round(x1, 3), round(best + h, 3)]


def lift_screen(ctx, steps):
    """A screen recording kept in its own shape gets a reading zoom: one
    travelling zoom that frames the densest block of text every
    SCREEN_WINDOW_S (the index's text boxes per sample), so the words read
    on a phone (the judges, every vfr run: "unreadable docs text ... push in
    1.3-1.8x on each section as it is named"). Only when the program has no
    framing of its own (no frame change, no zoom across the whole program).
    True when it landed."""
    import first_cut
    import schemas
    from timeline import Timeline
    edl = ctx.latest_edl()["json"]
    if not first_cut._screen_like(ctx.index) or edl.get("frame"):
        return False
    prog = float(schemas.program_duration(edl))
    zooms = (edl.get("effects") or {}).get("zooms") or []
    if prog < 6.0 or any(float(z.get("end") or 0) - float(z.get("start") or 0)
                         > 0.5 * prog for z in zooms):
        return False
    samples = [smp for smp in ((ctx.index or {}).get("spatial") or {}).get("samples") or []
               if smp.get("t") is not None and smp.get("text")]
    boxes = [b for smp in samples for b in smp["text"] if len(b) >= 4]
    if len(boxes) < 8:
        return False
    col = _text_column(boxes)
    if col is None:
        return False
    x0, x1 = col
    video = (ctx.index or {}).get("video") or {}
    try:
        aspect = float(video["width"]) / float(video["height"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
    try:
        tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    except Exception:  # noqa: BLE001
        return False
    hook_end = max([float(m.get("end") or 0) for m in edl.get("motion") or []
                    if m.get("template") == "hook_title"] or [0.0])
    t, keys = max(0.6, min(hook_end, 2.0)), []
    entry_t = round(t - 0.6, 2)
    while t < prog - 0.8:
        src = tl.out_to_src(min(t + SCREEN_WINDOW_S / 2.0, prog - 0.1))
        if src is None:
            t += SCREEN_WINDOW_S
            continue
        smp = min(samples, key=lambda x: abs(float(x["t"]) - src))
        rect = _reading_rect(smp["text"], (x0, x1), aspect)
        if rect:
            keys.append({"t": round(t, 2), "rect": rect})
            keys.append({"t": round(min(t + SCREEN_WINDOW_S - 0.7, prog), 2),
                         "rect": rect})
        t += SCREEN_WINDOW_S
    if len(keys) < 2:
        return False
    # the entry eases in from the untouched frame, already aimed at the
    # first block (a strength-0 keyframe still needs an aim)
    keys.insert(0, {"t": entry_t, "rect": keys[0]["rect"], "strength": 0.0})
    if keys[-1]["t"] < prog - 0.01:
        # held to the last frame: no step back out before the end card
        last = dict(keys[-1])
        last["t"] = round(prog, 2)
        keys.append(last)
    # the solver fits each rect; hold it inside the screen's resolution
    import agent_tools
    for k in keys:
        if k.get("rect") and k.get("strength") != 0.0:
            err, fit, _cx, _cy, _r = agent_tools._solve_zoom_rect(k["rect"])
            if err:
                return False
            # the column fills the width (the solver's own fit keeps a
            # margin that left a 1.2x push: not readable)
            r0, _r1, r2, _r3 = k["rect"]
            fill = 0.94 / max(0.05, float(r2) - float(r0)) - 1.0
            k["strength"] = round(min(max(float(fit or 0.0), fill),
                                      SCREEN_MAX_STRENGTH), 3)
    if max(float(k.get("strength") or 0) for k in keys) < SCREEN_MIN_STRENGTH:
        return False             # the text already fills the frame
    if steps.run("add_zoom_path", {
            "keyframes": keys, "ease": "cubic_in_out",
            "purpose": "a reading zoom that frames the text being shown, so "
                       "the screen reads on a phone"}):
        steps.applied.append("a reading zoom that follows the text on screen "
                             "so it reads on a phone")
        return True
    return False


def montage_end_beat(ctx, steps):
    """A highlight montage ends on a designed beat: a slow push across its
    payoff shot (the judges: "end on a designed beat ... don't let the last
    shot sit near-static"). True when it landed."""
    import schemas
    edl = ctx.latest_edl()["json"]
    prog = float(schemas.program_duration(edl))
    if prog < 4.0:
        return False
    start = round(max(0.0, prog - 1.0), 2)
    for z in (edl.get("effects") or {}).get("zooms") or []:
        try:
            if float(z["end"]) > start and not (float(z["start"]) <= 0.05
                                                and float(z["end"]) >= prog - 0.05):
                return False          # the ending already moves
        except (KeyError, TypeError, ValueError):
            continue
    if steps.run("add_zoom", {"start": start, "end": round(prog, 2),
                              "mode": "push_in", "strength": 0.1,
                              "purpose": "the montage's payoff shot ends on "
                                         "a designed push, never near-static"}):
        steps.applied.append("a push across the payoff shot so the "
                             "highlight ends on a beat")
        return True
    return False


def whole_cleanup(ctx, worker_db, steps):
    """The First cut's chain over the WHOLE take (clean ends, pauses, fillers,
    framing, hook, slams, captions): reset to the source, then
    first_cut.video_chain(whole=True). True when it landed."""
    import first_cut
    if not steps.run("reset_edit", {}):
        return False
    trial = getattr(ctx, "claude_trial", None)
    if trial is not None and not getattr(ctx, "first_cut_model", None):
        # the trim scout names the hook and the beats, metered on the
        # trial's own budget like every call of this turn
        ctx.first_cut_model = trial.model
    spend = {"usd": 0.0}
    chain_steps, applied, _picked = first_cut.video_chain(
        ctx, worker_db, time.monotonic(), spend, whole=True)
    steps.record(chain_steps)
    steps.applied.append("the whole take cleaned up: " + ", ".join(
        a for a in applied if a) if applied else "the whole take cleaned up")
    return True


def shape_words(ctx):
    """The delivered frame in words, from the EDL's frame or the source's
    own shape (C2/Q3: a reply called a 1280x800 screen recording "an
    18-second vertical cut" and the judges marked it down for the claim)."""
    try:
        frame = ctx.latest_edl()["json"].get("frame") or {}
    except Exception:  # noqa: BLE001
        frame = {}
    ratio = str(frame.get("ratio") or "").replace(" ", "")
    if ratio:
        return {"9:16": "vertical 9:16", "1:1": "square 1:1", "4:5": "4:5 portrait",
                "16:9": "landscape 16:9"}.get(ratio, ratio)
    video = (ctx.index or {}).get("video") or {}
    try:
        w, h = float(video["width"]), float(video["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if h / w >= 1.6:
        return "vertical 9:16"
    if abs(h / w - 1.0) < 0.05:
        return "square"
    return f"landscape, the source's own {int(w)}x{int(h)} shape (not vertical)"


def prepare(ctx, worker_db, session_id, text, post=None):
    """The deterministic head start for a generic first request on top of an
    untouched First cut. Returns {"intent", "applied", "note", "steps"} or
    None (nothing to do: the agent works the request as before)."""
    kind = intent(text)
    if kind is None or not getattr(ctx, "has_main_video", False):
        return None
    try:
        meta = worker_db.run(first_cut_state, session_id)
    except Exception:  # noqa: BLE001 — no head start, the turn runs as before
        return None
    if not meta or str(meta.get("edl_version")) != str(ctx.latest_edl()["version"]):
        return None
    fc = meta.get("first_cut") or {}
    picked = fc.get("picked") or {}
    if picked.get("how") in ("finished_piece", "motion"):
        # a finished piece over its song is kept whole; a highlight montage
        # is already cut to its strongest shots (re-cutting it lost 1.5)
        kind = "kept"
    steps = Steps(ctx, post)
    t0 = time.monotonic()
    whole = False
    if kind == "cleanup":
        import first_cut
        dur = float(ctx.duration or 0)
        try:
            kept = (float(picked["end_s"]) - float(picked["start_s"])
                    if picked.get("end_s") is not None else dur)
        except (TypeError, ValueError, KeyError):
            kept = dur
        if dur > 0 and kept < WHOLE_KEPT_SHARE * dur \
                and first_cut._speech(ctx.index):
            whole = whole_cleanup(ctx, worker_db, steps)
    if kind != "kept":
        repair_empty_cards(ctx, steps)
        if _talking(ctx):
            # measured OFF by default (Q3, 30 Oct, three Haiku rounds, two
            # Opus judgements each): added punches and slams on a talking
            # head never raised the judged score over its First cut (phone
            # -0.75..0, phonehook -1.0..-0.25, talk10best -1.25..-0.75,
            # talk10 0..+0.25); the judges ask for reframes and B-roll, which
            # this pass cannot add. CLAUDE_TRIAL_LIFT_TALKING=1 turns it on.
            if config.CLAUDE_TRIAL_LIFT_TALKING:
                lift_static(ctx, steps)
        else:
            lift_screen(ctx, steps)
    elif picked.get("how") == "motion":
        montage_end_beat(ctx, steps)
    elapsed = round(time.monotonic() - t0, 2)
    applied = [a for a in steps.applied if a]
    print(f"[trial_turn] {kind}: {applied or 'nothing'} in {elapsed}s",
          flush=True)
    if kind == "kept":
        done = ("The First cut already answers this request. Keep its cut, "
                "hook and framing exactly; do not re-cut, reset or extend it."
                + (" Added in code: " + "; ".join(applied) + "."
                   if applied else ""))
    elif applied:
        done = ("Applied in code before you started, on top of the First "
                "cut: " + "; ".join(applied) + ".")
    else:
        done = ("The First cut already answers this request; nothing needed "
                "adding.")
    shape = shape_words(ctx)
    note = (
        "\n[system: TRIAL TURN PREPARED. " + done
        + (f" The video's frame is {shape}: describe it as that." if shape else "")
        + (" The whole take is now cut with the First cut's craft (clean "
           "ends, pauses, fillers, framing, hook title, slams, captions)."
           if whole else "")
        + " Your job now: render_preview once, look at the result, fix only "
        "a MAJOR defect you can see (a face cut off, a caption over the "
        "face, a fragment at the start or end), then reply in two or three "
        "sentences saying what the video now is. Do not move, resize or "
        "restyle the hook title, the slams or the captions, and do not "
        "change the framing: the render's automatic notes about them (a "
        "title near the face, a caption near a slam, a framing step, the "
        "head near the top of a full-height crop, a held last frame) are "
        "known and kept, so do not act on them. The reply says what the "
        "video is, never a list of open items or review notes. Do not "
        "render again unless you changed something. Do not ask a "
        "question.]")
    return {"intent": kind, "whole": whole, "applied": applied, "note": note,
            "steps": steps.rows, "s": elapsed}
