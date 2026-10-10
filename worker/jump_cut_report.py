"""The JUMP-CUT REPORT: every same-shot keep join of a programme, how visible
its jump is, what already covers it, and the editor's options. ADVISORY
ONLY — it never writes an EDL, never adds a zoom and never adds a step.

WHY (judges, round 4, Oct 2026)

Pause removal on one camera leaves jump cuts inside a continuous shot, and
the judges flagged the bare ones (Thiel 7.22, 10.72, 19.18, 20.52; Elon 0.8
inside the hook and 18.88 on Rogan; Jobs 8.02, 12.49) and the 7.4% framing
steps on the others (too small to read as a cut: they stutter). Every round
they propose an automatic zoom ladder at every jump cut. The owner forbids
it (Oct 10 2026): zooms and sounds are optional, never rules — overused they
look childish — and a bare jump cut is the accepted grammar of a
talking-head short. So the engine REPORTS each join and leaves the choice to
the editor, who may leave it, move a graphic change onto it, re-cut it, or —
optional and rare — step the framing there with conceal_jump_cuts.

WHAT A ROW SAYS

* where: the programme second, the source join (e0 -> s1) and how much
  source the cut removes;
* how visible: the head's travel across the cut in face widths (exact
  frames through ``measure``, else the index's face samples; taste.face_jump)
  and, where the footage can be decoded, the picture change across the cut
  against the speaker's own motion (``pop``; cut_steps.pop_score) — visible,
  slight, steady or unmeasured;
* what covers it: a framing step across the cut (a zoom edge or a card's
  cut step) of at least STUTTER_STEP, the crop or a card re-aiming, a
  transition, a full-frame overlay, a graphic or picture card entering or
  leaving ON the cut frame — and, softer, a caption block changing there;
* flags: a jump cut inside the hook's first HOOK_S seconds, and a framing
  step under STUTTER_STEP (it reads as a stutter, not a cut);
* options: leave it; move a graphic change that sits within NEAR_S onto the
  cut; restore a short removed pause (one continuous take) or re-cut the
  join; and, optional and rare, a hard step of at least STEP_OPTION on that
  one cut (conceal_jump_cuts at=[t]).

WHERE AGENTS SEE IT

* conceal_jump_cuts(mode='report') returns the full report (read-only);
* taste.critique carries advisory_line() into render_preview's TASTE NOTES
  and the version's advisory record — only flagged, near-miss and visibly
  jumping joins are named;
* every EDL write that changes the cuts or the framing steps appends
  write_note(): new hook jump cuts and new stutter steps (index evidence
  only — no frame is decoded at write time).
"""

import json
import os
import tempfile

import taste

# A jump cut this early sits inside the hook: the opening stutters before
# the viewer has the shot.
HOOK_S = 1.5
# A framing step across a jump cut smaller than this reads as a stutter, not
# a cut (judged: Jobs' 7.4% card steps). From here it is a framing change.
STUTTER_STEP = 0.10
# The step the report offers when it offers one (cut_steps.STEP_DEFAULT).
STEP_OPTION = 0.12
# A step this small is no step at all (a following crop's drift).
_NO_STEP = 0.01
# A change within this many frames of the cut lands on its frame.
ON_CUT_FRAMES = 1.5
# A graphic edge this close to a jump cut could move onto it.
NEAR_S = 0.40
# A removed stretch this short can be restored: one continuous take.
RESTORE_MAX_S = 0.6
# Head travel (face widths): taste.JUMP_CUT_JAR_SHIFT is a visible jump;
# under SLIGHT_SHIFT the head is steady.
SLIGHT_SHIFT = 0.12
# Picture change across the cut against the speaker's own motion: from
# cut_steps.POP_RATIO it visibly pops; under SLIGHT_POP it reads as motion.
SLIGHT_POP = 1.6
# Joins measured on real frames per report, longest removed source first.
FACE_MAX = taste.JUMP_CUT_MEASURE_MAX
POP_MAX = 12
# Rows printed in full by the tool; the rest are summarised.
LIST_MAX = 24


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── the programme's events ────────────────────────────────────────────────

def _graphic_edges(edl, out_dur):
    """[(t, label, kind, item id, 'enters'|'leaves')] for every program-time
    graphic edge: motion graphics, texts, vectors, overlays and picture cards
    (a layout change). An edge at the programme's start or end is no cut
    cover and is left out."""
    rows = []

    def add(kind, item, a, b, label):
        for t, how in ((a, "enters"), (b, "leaves")):
            if t is None or t <= 0.02 or t >= out_dur - 0.02:
                continue
            rows.append((round(float(t), 3), label, kind,
                         str(item.get("id") or "?"), how))

    for m in edl.get("motion") or []:
        if isinstance(m, dict) and not m.get("_synthetic"):
            add("motion", m, _num(m.get("start"), None), _num(m.get("end"), None),
                f"graphic '{m.get('id')}' ({m.get('template') or 'motion'})")
    for t in edl.get("texts") or []:
        if isinstance(t, dict):
            add("text", t, _num(t.get("start"), None), _num(t.get("end"), None),
                f"text '{t.get('id')}'")
    for v in edl.get("vectors") or []:
        if isinstance(v, dict):
            add("vector", v, _num(v.get("start"), None), _num(v.get("end"), None),
                f"vector '{v.get('id')}'")
    for o in edl.get("overlays") or []:
        if isinstance(o, dict):
            a = _num(o.get("start"), None)
            b = None if a is None else a + _num(o.get("duration_s"))
            add("overlay", o, a, b, f"overlay '{o.get('id')}'")
    for cd in ((edl.get("effects") or {}).get("picture_cards") or []):
        if isinstance(cd, dict):
            add("card", cd, _num(cd.get("start"), None), _num(cd.get("end"), None),
                f"picture card '{cd.get('id')}'")
    return rows


def caption_edges(edl, index, tl, canvas=None):
    """Program seconds where a caption block starts or clears, or None when
    they cannot be worked out here (no transcript captions: [])."""
    caps = edl.get("captions")
    if not caps:
        return []
    try:
        import motion_captions
        if isinstance(caps, dict) and motion_captions.look_of(edl):
            cues = motion_captions.cues(edl, index, tl, canvas=canvas)
            return sorted({round(float(c[k]), 3) for c in cues for k in ("s", "e")})
        if isinstance(caps, list):
            return sorted({round(_num(c.get(k)), 3) for c in caps
                           for k in ("start", "end") if isinstance(c, dict)})
        import captions as caplib
        import stitch
        with tempfile.TemporaryDirectory(prefix="jcr_") as d:
            path = caplib.build_ass(edl, index, tl, os.path.join(d, "c.ass"))
            states = stitch.ass_events(path) if path else []
        return sorted({round(float(t), 3) for pair in states for t in pair})
    except Exception:  # noqa: BLE001 — the report goes on without captions
        return None


# ── the joins ─────────────────────────────────────────────────────────────

def _aim_fn(edl):
    """crop aim (x, y, mode) at a SOURCE second: a follow path, else the
    focus_track span holding it, else the frame's own focus."""
    import follow
    frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
    track = [sp for sp in (frame or {}).get("focus_track") or []
             if isinstance(sp, dict)]
    base = ((frame or {}).get("focus_x"), (frame or {}).get("focus_y"),
            (frame or {}).get("mode") or "crop")

    def static(t):
        for sp in track:
            if _num(sp.get("t0")) <= t <= _num(sp.get("t1")):
                return (sp["x"] if sp.get("x") is not None else base[0],
                        sp["y"] if sp.get("y") is not None else base[1],
                        sp.get("mode") or base[2])
        return base

    def aim(t):
        moving = follow.frame_focus_at(edl, t)
        s = static(t)
        if moving is not None and s[2] == "crop":
            return (moving[0], moving[1], "crop")
        return s
    return aim


def _card_change(edl, c, dt, e0, s1):
    """(scale step, shift, stacked, by) of the picture card live across the
    cut: its source rect's width ratio - 1, how far the rect's centre moves
    as a fraction of the rect (a re-aim — a following card drifts a little
    across any cut), whether it is a stacked card, and what made the step
    (its cut steps, else its own framing: a source_track span)."""
    import picture_cards
    for cd in ((edl.get("effects") or {}).get("picture_cards") or []):
        if not isinstance(cd, dict):
            continue
        if not (_num(cd.get("start")) <= c - dt and _num(cd.get("end")) >= c + dt):
            continue
        if cd.get("panels"):
            return 0.0, 0.0, True, None
        if not picture_cards.source_fed(cd):
            return 0.0, 0.0, False, None
        ra = picture_cards.source_at(cd, e0 - 1e-3)
        rb = picture_cards.source_at(cd, s1 + 1e-3)
        if not ra or not rb:
            return 0.0, 0.0, False, None
        step, shift = taste.card_reframe(ra, rb)
        stepped = (picture_cards.step_scale_at(cd, e0 - 1e-3) is not None
                   or picture_cards.step_scale_at(cd, s1 + 1e-3) is not None)
        return step, shift, False, ("the card's cut step" if stepped
                                    else "the card's framing")
    return 0.0, 0.0, False, None


def _vcentre(z, c):
    """The viewport centre (frame fraction) of a zoom z aimed at c."""
    return (1.0 - 1.0 / z) * _num(c, 0.5) + 0.5 / z


def _removed_words(index, e0, s1):
    out = []
    for w in (index or {}).get("words") or []:
        a = _num(w.get("start", w.get("t0")), None)
        b = _num(w.get("end", w.get("t1")), None)
        if a is None or b is None:
            continue
        if a < s1 - 0.02 and b > e0 + 0.02:
            out.append(str(w.get("word", w.get("w", ""))).strip())
    return [w for w in out if w]


def _visibility(row):
    face, pop = row.get("face"), row.get("pop")
    shift = face["shift"] if face else None
    ratio = pop["ratio"] if pop else None
    import cut_steps
    if (shift is not None and shift >= taste.JUMP_CUT_JAR_SHIFT - 1e-9) or \
            (pop and cut_steps.pops(pop)):
        return "visible"
    if (shift is not None and shift >= SLIGHT_SHIFT) or \
            (ratio is not None and ratio >= SLIGHT_POP and pop["cut"] >= 0.01):
        return "slight"
    if shift is None and ratio is None:
        return "unmeasured"
    return "steady"


def report(edl, index, tl=None, fps=None, measure=None, pop=None,
           captions=True, canvas=None, bare_only=False):
    """The report of ``edl``'s jump cuts: {"rows": [...], "camera_changes",
    "contiguous", "inserts", "program_s"}.

    measure(src_t) -> face boxes on that exact source frame, or None (the
    index's samples answer); pop(e0, s1, card) -> cut_steps.pop_score or None.
    Bare joins take the decoding budget first (longest removed source
    first); bare_only=True (the critic's note, which names no covered join's
    visibility) decodes nothing for a covered one — every render_preview
    pays for this, on a ~1 vCPU box.
    captions=False skips the caption blocks (write time)."""
    import renderer
    from timeline import Timeline, transition_junctions
    if tl is None:
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
    video = (index or {}).get("video") or {}
    try:
        fps = float(fps or video.get("fps") or 30.0)
    except (TypeError, ValueError):
        fps = 30.0
    out_dur = float(tl.out_duration)
    out = {"rows": [], "camera_changes": 0, "contiguous": 0, "inserts": 0,
           "program_s": round(out_dur, 2)}
    segs = list(getattr(tl, "segs", None) or [])
    if len(segs) < 2:
        return out
    dt = 0.5 / fps
    on_cut = ON_CUT_FRAMES / fps
    shots = (index or {}).get("shots") or []
    try:
        zooms = renderer.camera_zooms(edl, index, tl, fps)
    except Exception:  # noqa: BLE001
        zooms = []
    fx = edl.get("effects") or {}
    junctions = set()
    if fx.get("transition"):
        try:
            junctions = transition_junctions(edl, index)
        except Exception:  # noqa: BLE001
            junctions = set()
    covers = []
    for ov in edl.get("overlays") or []:
        if isinstance(ov, dict) and (ov.get("fit") == "cover" or ov.get("screen")):
            a = _num(ov.get("start"))
            covers.append((a, a + _num(ov.get("duration_s")), ov.get("id")))
    edges = _graphic_edges(edl, out_dur)
    cap_edges = caption_edges(edl, index, tl, canvas) if captions else None
    aim = _aim_fn(edl)
    # junction k sits between render blocks k and k+1 (inserts are blocks)
    block_of_seg, ins_j, pre = [], 0, 0.0
    ins_at = [at for at, _d in getattr(tl, "ins", []) or []]
    for i, L in enumerate(tl.seg_out_len):
        while ins_j < len(ins_at) and ins_at[ins_j] <= pre + 1e-6:
            ins_j += 1
        block_of_seg.append(i + ins_j)
        pre += L

    rows = []
    for i in range(len(segs) - 1):
        e0, s1 = float(segs[i][1]), float(segs[i + 1][0])
        end = tl.offsets[i] + tl.seg_out_len[i]
        c = float(tl.offsets[i + 1])
        if c - end > 1e-6:
            out["inserts"] += 1
            continue
        if abs(s1 - e0) <= 1e-3:
            out["contiguous"] += 1
            continue
        if shots:
            sa = taste._shot_id_at(shots, e0 - 0.04)
            sb = taste._shot_id_at(shots, s1 + 0.04)
            if sa is None or sb is None or sa != sb:
                out["camera_changes"] += 1
                continue
        row = {"t": round(c, 2), "src": (round(e0, 3), round(s1, 3)),
               "skip": round(s1 - e0, 2), "covers": [], "softens": [],
               "flags": [], "near": [], "step": 0.0, "face": None,
               "evidence": None, "pop": None, "_seg": (float(segs[i][0]),
                                                        float(segs[i + 1][1]))}
        # the framing across the cut: zoom edges (as rendered) and card steps
        zb = renderer.zoom_state_at(zooms, c - dt, out_dur)
        za = renderer.zoom_state_at(zooms, c + dt, out_dur)
        zstep = max(za[0], zb[0]) / max(1e-6, min(za[0], zb[0])) - 1.0
        cstep, card_shift, stacked, card_by = _card_change(edl, c, dt,
                                                           e0, s1)
        row["stacked"] = stacked
        step = max(zstep, cstep)
        row["step"] = round(step, 3)
        if step >= STUTTER_STEP - 1e-6:
            row["covers"].append(f"a {step * 100:.0f}% framing step")
        elif step >= _NO_STEP:
            row["flags"].append("stutter")
            ids = [z.get("id") for z in zooms if z.get("id") and (
                abs(_num(z.get("start")) - c) <= 2 * dt
                or abs(_num(z.get("end")) - c) <= 2 * dt)]
            row["step_of"] = (card_by if cstep >= zstep
                              else "zoom " + ", ".join(map(str, ids))
                              if ids else "a zoom")
        # a re-aim covers a cut when the viewer sees a new framing: the
        # picture moves taste.JUMP_CUT_MIN_SHIFT of what the card shows
        zshift = max(abs(_vcentre(za[0], za[1]) - _vcentre(zb[0], zb[1])),
                     abs(_vcentre(za[0], za[2]) - _vcentre(zb[0], zb[2])))
        if zshift >= taste.JUMP_CUT_MIN_SHIFT - 1e-6:
            row["covers"].append("the camera re-aims")
        if card_shift >= taste.JUMP_CUT_MIN_SHIFT - 1e-6:
            row["covers"].append("the picture card re-aims")
        elif card_shift >= 0.02:
            row["softens"].append(f"the card's framing drifts "
                                  f"{card_shift * 100:.0f}%")
        a0, a1 = aim(e0 - 1e-3), aim(s1 + 1e-3)
        if a0[2] != a1[2] or any(
                abs(_num(x, 0.5) - _num(y, 0.5)) >= taste.JUMP_CUT_MIN_AIM
                for x, y in zip(a0[:2], a1[:2])):
            row["covers"].append("the crop re-aims")
        if block_of_seg[i] in junctions:
            row["covers"].append("a transition")
        for a, b, oid in covers:
            if a <= c - dt and b >= c + dt:
                row["covers"].append(f"overlay '{oid}' fills the frame")
        for t, label, kind, iid, how in edges:
            d = t - c
            if abs(d) <= on_cut + 1e-6:
                row["covers"].append(f"{label} {how}")
            elif abs(d) <= NEAR_S and kind in ("motion", "text", "vector"):
                row["near"].append({"label": label, "kind": kind, "id": iid,
                                    "how": how, "at": t, "delta": round(d, 2)})
        if cap_edges and any(abs(t - c) <= on_cut + 1e-6 for t in cap_edges):
            row["softens"].append("a caption block changes on the cut")
        if c < HOOK_S:
            row["flags"].append("hook")
        row["covered"] = bool(row["covers"])
        row["removed_words"] = _removed_words(index, e0, s1)
        rows.append(row)

    # evidence: bare joins first, then the longest removed source first
    # (where a head moves); the decoding budget goes to what is uncovered
    order = sorted(range(len(rows)),
                   key=lambda k: (rows[k]["covered"], -rows[k]["skip"]))
    for n_, k in enumerate(order):
        r = rows[k]
        (a0, b1) = r["_seg"]
        e0, s1 = r["src"]
        decode = not (bare_only and r["covered"])
        fb = fa = None
        if measure is not None and decode and n_ < FACE_MAX:
            try:
                fb, fa = measure(e0 - dt), measure(s1 + dt)
            except Exception:  # noqa: BLE001 — a measurement never fails the report
                fb = fa = None
        framed = fb is not None and fa is not None
        if fb is None:
            fb = taste._sample_faces(index, max(a0, e0 - taste.JUMP_CUT_FACE_NEAR_S), e0, e0)
        if fa is None:
            fa = taste._sample_faces(index, s1, min(b1, s1 + taste.JUMP_CUT_FACE_NEAR_S), s1)
        r["face"] = taste.face_jump(fb, fa) if fb and fa else None
        r["evidence"] = (None if r["face"] is None else
                         "frames" if framed else "index")
        if pop is not None and decode and n_ < POP_MAX:
            try:
                r["pop"] = pop(e0, s1, r["t"])
            except Exception:  # noqa: BLE001
                r["pop"] = None
    for r in rows:
        r.pop("_seg", None)
        r["visibility"] = _visibility(r)
        r["options"] = _options(r)
    out["rows"] = rows
    return out


def _options(r):
    """The editor's options for one join, most restrained first."""
    t = r["t"]
    opts = []
    if "stutter" in r["flags"]:
        opts.append(f"the {r['step'] * 100:.0f}% step from {r.get('step_of')} "
                    "reads as a stutter: remove it (a bare cut is fine) or "
                    f"raise it to ≥{STEP_OPTION * 100:.0f}% "
                    f"(conceal_jump_cuts step={STEP_OPTION:g})")
    if r["covered"]:
        return opts
    opts.append("leave it (a bare jump cut is fine)")
    if r["visibility"] == "visible":
        opts.append("cover it with B-roll or a cutaway")
    for nb in sorted(r["near"], key=lambda q: abs(q["delta"])):
        which = "start" if nb["how"] == "enters" else "end"
        tool = ("set_motion_graphic" if nb["kind"] == "motion" else
                "its own tool")
        opts.append(f"move {nb['label']}'s {which} from {nb['at']:.2f}s onto "
                    f"the cut ({tool} {which}={t:.2f}): one event instead of two")
        break
    e0, s1 = r["src"]
    if r["skip"] <= RESTORE_MAX_S:
        said = r.get("removed_words") or []
        brings = (f" — it brings back '{' '.join(said[:4])}'" if said else "")
        opts.append(f"restore the {r['skip']:.2f}s removed between the takes "
                    f"(source {e0:.2f}-{s1:.2f}, restore_range): one continuous "
                    f"take, +{r['skip']:.2f}s{brings}")
    elif r["visibility"] == "visible" or "hook" in r["flags"]:
        opts.append("re-cut the join on a pause where the head sits still "
                    "(keep_segments)")
    if (r["visibility"] == "visible" or "hook" in r["flags"]) and \
            r["step"] < _NO_STEP and not r.get("stacked"):
        opts.append(f"optional and rare: a hard ≥{STEP_OPTION * 100:.0f}% "
                    f"framing step on this cut only (conceal_jump_cuts at=[{t:g}])")
    return opts


# ── words ─────────────────────────────────────────────────────────────────

def _evidence_text(r):
    bits = []
    f = r.get("face")
    if f:
        bits.append(f"head moves {f['shift']:.2f} face-widths"
                    + (" (index samples)" if r.get("evidence") == "index" else ""))
    p = r.get("pop")
    if p:
        bits.append(f"picture change {p['ratio']:.1f}x its motion")
    return ", ".join(bits) or "no face or frame evidence"


def _cover_text(r):
    if r["covers"]:
        return "covered: " + "; ".join(r["covers"])
    if r["softens"]:
        return "bare (" + "; ".join(r["softens"]) + ")"
    return "bare"


def format_report(rep):
    """The full report as the tool returns it."""
    rows = rep.get("rows") or []
    head = ("JUMP-CUT REPORT (advisory: it lists, it never adds a zoom or a "
            "step — zooms are optional, never a rule, and a bare jump cut is "
            "the accepted grammar).")
    others = []
    if rep.get("camera_changes"):
        others.append(f"{rep['camera_changes']} camera change(s)")
    if rep.get("inserts"):
        others.append(f"{rep['inserts']} join(s) at an insert")
    if rep.get("contiguous"):
        others.append(f"{rep['contiguous']} source-continuous split(s)")
    if not rows:
        return (head + " No same-shot jump cut in this programme"
                + (f" ({', '.join(others)}, not jumps)" if others else "") + ".")
    n_cov = sum(1 for r in rows if r["covered"])
    flagged = [r for r in rows if r["flags"]]
    lines = [head,
             f"{len(rows)} same-shot jump cut(s) in {rep.get('program_s')}s: "
             f"{n_cov} covered, {len(rows) - n_cov} bare"
             + (f"; {len(flagged)} flagged" if flagged else "")
             + (f" ({', '.join(others)} not listed)" if others else "") + "."]
    for r in rows[:LIST_MAX]:
        e0, s1 = r["src"]
        flags = ""
        if r["flags"]:
            flags = " FLAG: " + ", ".join(
                {"hook": f"inside the hook's first {HOOK_S:g}s",
                 "stutter": f"{r['step'] * 100:.0f}% step < "
                            f"{STUTTER_STEP * 100:.0f}% (a stutter)"}[f]
                for f in r["flags"]) + "."
        lines.append(f"  {r['t']:.2f}s  src {e0:.2f}->{s1:.2f} "
                     f"({r['skip']:.2f}s removed) · {r['visibility']}: "
                     f"{_evidence_text(r)} · {_cover_text(r)}.{flags}")
        if r["options"]:
            lines.append("      options: " + " | ".join(r["options"]))
    if len(rows) > LIST_MAX:
        lines.append(f"  (+{len(rows) - LIST_MAX} more joins)")
    return "\n".join(lines)


def advisory_line(rep):
    """One craft note for taste.critique, or '': the flagged joins (hook,
    stutter steps), bare joins with a graphic change within reach, and bare
    joins whose jump is visible. A bare, steady jump cut goes unmentioned."""
    rows = rep.get("rows") or []
    hook = [r for r in rows if "hook" in r["flags"] and not r["covered"]]
    stut = [r for r in rows if "stutter" in r["flags"]]
    bare = [r for r in rows if not r["covered"]]
    near = [r for r in bare if r["near"]]
    vis = [r for r in bare if r["visibility"] == "visible"]
    parts = []

    def ts(rs, k=taste.JUMP_CUT_LIST):
        s = ", ".join(f"{r['t']:g}s" for r in rs[:k])
        return s + (f" (+{len(rs) - k})" if len(rs) > k else "")
    if hook:
        parts.append(f"a jump cut at {ts(hook)} sits inside the hook's first "
                     f"{HOOK_S:g}s (restore the pause or start on the later take)")
    if stut:
        steps = sorted({round(r["step"] * 100) for r in stut})
        parts.append(f"the framing steps only {'/'.join(f'{s:.0f}' for s in steps)}% "
                     f"at {ts(stut)} — under ~{STUTTER_STEP * 100:.0f}% a step reads "
                     "as a stutter, not a cut: remove it (a bare cut is fine) or "
                     f"raise it to ≥{STEP_OPTION * 100:.0f}% only where a cut "
                     "genuinely pops")
    if vis:
        parts.append(f"{len(vis)} bare jump cut{'s' if len(vis) != 1 else ''} "
                     f"where the speaker visibly jumps: {ts(vis)}")
    for r in near[:2]:
        nb = min(r["near"], key=lambda q: abs(q["delta"]))
        side = "before" if nb["delta"] < 0 else "after"
        parts.append(f"{nb['label']} {nb['how']} {abs(nb['delta']):.2f}s {side} "
                     f"the {r['t']:g}s cut — moving that change onto the cut "
                     "makes one event of the two")
    if not parts:
        return ""
    return ("jump cuts: " + "; ".join(parts)
            + ". Options, never a rule: leave it, move a graphic change onto "
            "the cut, cover it with B-roll, restore or re-cut the join, or "
            "(rare) a ≥12% step on that one cut — "
            "conceal_jump_cuts(mode='report') lists every join with its "
            "evidence and options and writes nothing.")


# ── write time ────────────────────────────────────────────────────────────

def _cut_state(edl):
    fx = (edl or {}).get("effects") or {}
    return json.dumps([(edl or {}).get("keep"), (edl or {}).get("inserts"),
                       fx.get("zooms"),
                       [(c.get("cut_steps"), c.get("start"), c.get("end"))
                        for c in fx.get("picture_cards") or []
                        if isinstance(c, dict)]],
                      sort_keys=True, default=str)


def write_note(prev_edl, new_edl, index):
    """A one-line JUMP-CUT NOTE for an EDL write, or '': hook jump cuts and
    stutter steps this write introduced. Cheap (index evidence only, no
    caption plan) and only when the cuts or the framing steps changed."""
    if not new_edl or not new_edl.get("keep") or \
            _cut_state(prev_edl) == _cut_state(new_edl):
        return ""
    try:
        new = report(new_edl, index, captions=False)["rows"]
        old = report(prev_edl, index, captions=False)["rows"] \
            if prev_edl and prev_edl.get("keep") else []
    except Exception:  # noqa: BLE001 — a note never fails a write
        return ""

    # Keyed by the SOURCE join, not its programme second: a cut or restore
    # earlier in the programme moves every later cut, and an old stutter
    # step that merely moved is not news.
    def keyed(rows, flag):
        return {r["src"] for r in rows if flag in r["flags"]
                and (flag != "hook" or not r["covered"])}
    hook = [r for r in new if r["src"] in
            keyed(new, "hook") - keyed(old, "hook")]
    stut = [r for r in new if r["src"] in
            keyed(new, "stutter") - keyed(old, "stutter")]
    parts = []
    if hook:
        where = ", ".join(f"{r['t']:g}s" for r in hook)
        parts.append(f"a jump cut at {where} now sits inside the hook's "
                     f"first {HOOK_S:g}s")
    if stut:
        parts.append("a framing step under ~10% on the jump cut at "
                     + ", ".join(f"{r['t']:g}s ({r['step'] * 100:.0f}%)" for r in stut[:4])
                     + " reads as a stutter, not a cut")
    if not parts:
        return ""
    return ("JUMP-CUT NOTE (advisory): " + "; ".join(parts)
            + ". Leave it, restore or re-cut the join, or remove/raise the step "
            "— conceal_jump_cuts(mode='report') lists every join with its options.")


# ── the tool ──────────────────────────────────────────────────────────────

def _pop_cache(ctx):
    cache = getattr(ctx, "_jumpcut_pops", None)
    if cache is None:
        cache = {}
        try:
            setattr(ctx, "_jumpcut_pops", cache)
        except Exception:  # noqa: BLE001
            pass
    return cache


def _pop_key(e0, s1):
    return (round(float(e0), 3), round(float(s1), 3))


def pop_measure(ctx, edl):
    """pop(e0, s1, t) -> cut_steps.pop_score across one join, measured
    inside what the frame shows, or None when this context has no main
    video. LAZY like motion_tools.jump_cut_measure: the proxy is resolved
    on the first join actually measured; a context that cannot decode the
    footage answers None per join. Results are kept on the context
    (cached_pop reads them back without decoding)."""
    if not getattr(ctx, "has_main_video", True):
        return None
    state = {}
    cache = _pop_cache(ctx)

    def pop(e0, s1, t):
        key = _pop_key(e0, s1)
        if key in cache:
            return cache[key]
        cache[key] = _pop_now(e0, s1, t)
        return cache[key]

    def _pop_now(e0, s1, t):
        import cut_steps
        if "src" not in state:
            state["src"] = None
            try:
                proxy = ctx.proxy_path()
                video = (getattr(ctx, "index", None) or {}).get("video") or {}
                if proxy and os.path.exists(proxy) and video.get("width") \
                        and video.get("height"):
                    state["src"] = (proxy, float(video["width"]),
                                    float(video["height"]),
                                    float(video.get("fps") or 30.0))
            except Exception:  # noqa: BLE001 — face evidence only
                state["src"] = None
        if not state["src"]:
            return None
        proxy, sw, sh, fps = state["src"]
        index = getattr(ctx, "index", None) or {}
        card, _why = cut_steps._source_card(edl, t - 0.05, t + 0.05)
        rect = cut_steps._visible_rect(edl, index, s1, card)
        return cut_steps.measure_pop(proxy, e0, s1, rect, fps, sw, sh)
    return pop


def cached_pop(ctx):
    """pop(e0, s1, t) reading back what pop_measure measured on this
    context, decoding nothing (None where nothing was measured)."""
    cache = getattr(ctx, "_jumpcut_pops", None) or {}
    return lambda e0, s1, t: cache.get(_pop_key(e0, s1))


def tool_report(ctx):
    """conceal_jump_cuts(mode='report'): the full report on the current EDL,
    with exact-frame face evidence and the picture change across each cut
    where this context can decode the footage. Writes nothing."""
    import motion_tools
    from timeline import Timeline
    if not getattr(ctx, "has_main_video", True):
        return "No jump cuts: this program has no main video. Nothing was written."
    edl = json.loads(json.dumps(ctx.latest_edl()["json"]))
    if not edl.get("keep"):
        return "No jump cuts: this program has no kept footage. Nothing was written."
    index = getattr(ctx, "index", None) or {}
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    try:
        canvas = motion_tools._canvas_size(ctx, edl)
    except Exception:  # noqa: BLE001
        canvas = None
    rep = report(edl, index, tl, measure=motion_tools.jump_cut_measure(ctx),
                 pop=pop_measure(ctx, edl), canvas=canvas)
    return format_report(rep) + "\nNothing was written."
