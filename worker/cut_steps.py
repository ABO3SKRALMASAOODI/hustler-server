"""OPTIONAL jump-cut concealment: hard framing steps on same-angle cuts that
visibly pop (conceal_jump_cuts).

WHY (judges, Oct 2026)

Pause removal on a single-camera talk leaves same-angle jump cuts, and on
the Jobs card four of them in the first 4 s made the head and hands jump
inside a constant frame. Editors hide such a cut by changing the framing ON
it — tight from the cut to the next one, wide again at the one after — so the
cut reads as a second camera. The judges asked for that to be automatic; the
owner's rule (Oct 10 2026) is that zooms are optional, never a rule, and that
overused moves make an edit look childish. So this is a TOOL the editor
chooses when cuts visibly pop, never a default: no look, planner, preset or
render path writes it, and a bare jump cut stays the accepted grammar.

WHAT IT WRITES

Cut hygiene, not expressive camera: nothing animates. On each selected cut
the framing STEPS by ``step`` (10-15%, default 12%) and holds to the next
cut, where it steps back — so consecutive popping cuts alternate tight /
wide. Round 4 (Oct 2026): the judged 7.4% steps on the Jobs card read as a
stutter, not a cut — under ~10% a step is worse than a bare cut.

THE REPORT COMES FIRST

``mode='report'`` writes nothing: it lists every same-shot join with how
visible its jump is, what already covers it and the editor's options
(jump_cut_report.py) — leave it, move a graphic change onto it, restore or
re-cut the join, or this step on that one cut.

* full-frame footage: a ``cut_step`` zoom — mode punch, ramp_s 0 (an instant
  step), aimed at the speaker's face, from the cut to the next cut. The
  camera renders it like any zoom; the zoom-rhythm critics leave it out.
* a source-fed picture card: a ``cut_steps`` span on the card — the card's
  SOURCE rect scaled over the footage after the cut (one enlargement from the
  source, never crop-then-zoom). A card already near its upscale cap
  (picture_cards.SOURCE_UPSCALE_CAP; a 480p archival talk) steps WIDE instead
  of tight, so the alternation never softens the picture — and so does a
  card whose tight step would push the speaker's head (hair, ears, chin)
  out of the card (tight_step_crops_head; the cut stays bare when the
  source has no room to step wide). A card composes
  its footage per kept segment, so a card step holds to the end of that
  segment (through a camera change inside it), never to a mid-segment cut.

A re-cut moves cut-step zooms with their footage; one whose cut is gone is
dropped (timeline.remap_program_items) rather than left stepping mid-shot.

WHICH CUTS (measurably pop)

Same-angle joins the picture leaves bare (taste.uncovered_jump_cuts: no
camera change, no insert, no transition, no zoom or crop re-aim already on
it), where either the speaker's head jumps (face evidence, taste.jarring) or
the picture itself jumps: the mean change across the cut, inside what the
frame shows, is POP_RATIO times the motion between frames either side of it
and at least POP_MIN. ``at`` names cuts by program second instead (the editor
saw them pop on the render).
"""

import json
import math
import os

STEP_DEFAULT = 0.12
STEP_MIN, STEP_MAX = 0.10, 0.15
# A step this small still reads as a framing change on a hard cut (taste
# counts a written cut step as cover from here, like an expressive zoom);
# under it the frame stutters (jump_cut_report.STUTTER_STEP).
CUT_STEP_MIN = STEP_MIN
# At most this many cuts are measured on real frames per call.
MEASURE_MAX = 24
# Picture pop: the mean absolute change across the cut (0-1 luma, on a
# blurred POP_GRID-wide copy of what the frame shows) against the motion
# between frames two apart on either side. Calibrated on the showcase
# sources (see tests/test_cut_steps.py for the synthetic contract).
POP_GRID = 48
POP_RATIO = 2.5
POP_MIN = 0.02
NATURAL_FLOOR = 0.004
AT_TOL_S = 0.15
ID_PREFIX = "cs"
PURPOSE = ("cut hygiene: hard framing step on a same-angle jump cut that "
           "visibly pops (conceal_jump_cuts)")


def _at():
    import agent_tools
    return agent_tools


# ── the EDL side ─────────────────────────────────────────────────────────

def is_step_zoom(z):
    return isinstance(z, dict) and bool(z.get("cut_step"))


def strip(edl):
    """``edl`` (a copy) without any cut step: (edl, zooms removed, card
    spans removed)."""
    edl = json.loads(json.dumps(edl))
    fx = dict(edl.get("effects") or {})
    zooms = fx.get("zooms") or []
    kept = [z for z in zooms if not is_step_zoom(z)]
    nz = len(zooms) - len(kept)
    fx["zooms"] = kept
    nc = 0
    cards = []
    for cd in fx.get("picture_cards") or []:
        cd = dict(cd)
        if cd.get("cut_steps"):
            nc += len(cd["cut_steps"])
            cd["cut_steps"] = None
        cards.append(cd)
    if fx.get("picture_cards") is not None:
        fx["picture_cards"] = cards
    edl["effects"] = fx
    return edl, nz, nc


def expressive_zooms(zooms):
    """The zooms an editor chose as camera moves (cut steps left out)."""
    return [z for z in zooms or [] if not is_step_zoom(z)]


def steps_on(edl):
    """(zoom steps, card steps) currently in the EDL."""
    fx = edl.get("effects") or {}
    zs = [z for z in fx.get("zooms") or [] if is_step_zoom(z)]
    cs = [st for cd in fx.get("picture_cards") or [] if isinstance(cd, dict)
          for st in cd.get("cut_steps") or []]
    return zs, cs


# ── measuring the pop ────────────────────────────────────────────────────

def _blur_grid(gray, rect, grid=POP_GRID):
    """A blurred ``grid``-wide copy of ``rect`` (source fractions) of a gray
    frame, as float 0-1."""
    import numpy as np
    h, w = gray.shape
    x0 = int(max(0, min(w - 2, math.floor(rect[0] * w))))
    x1 = int(max(x0 + 2, min(w, math.ceil(rect[2] * w))))
    y0 = int(max(0, min(h - 2, math.floor(rect[1] * h))))
    y1 = int(max(y0 + 2, min(h, math.ceil(rect[3] * h))))
    a = gray[y0:y1, x0:x1].astype(np.float32) / 255.0
    gw = max(4, int(grid))
    gh = max(4, int(round(gw * a.shape[0] / max(1, a.shape[1]))))
    # area-average onto the grid (a cheap blur that also forgives sub-pixel
    # jitter and compression noise)
    ys = np.linspace(0, a.shape[0], gh + 1).astype(int)
    xs = np.linspace(0, a.shape[1], gw + 1).astype(int)
    out = np.zeros((gh, gw), np.float32)
    for j in range(gh):
        row = a[ys[j]:max(ys[j] + 1, ys[j + 1])]
        for i in range(gw):
            out[j, i] = row[:, xs[i]:max(xs[i] + 1, xs[i + 1])].mean()
    return out


def _diff(a, b):
    import numpy as np
    return float(np.mean(np.abs(a - b)))


def pop_score(before, after, rect):
    """{cut, natural, ratio} for gray frames ``before`` (the last frames
    before a cut, oldest first) and ``after`` (the first frames after it),
    measured inside ``rect`` (source fractions), or None without enough
    frames."""
    if len(before) < 2 or len(after) < 2:
        return None
    b = [_blur_grid(g, rect) for g in before[-3:]]
    a = [_blur_grid(g, rect) for g in after[:3]]
    cut = _diff(b[-1], a[0])
    natural = max(_diff(b[-1], b[0]), _diff(a[0], a[-1]))
    return {"cut": round(cut, 4), "natural": round(natural, 4),
            "ratio": round(cut / max(natural, NATURAL_FLOOR), 2)}


def pops(score):
    return bool(score) and score["cut"] >= POP_MIN - 1e-9 \
        and score["ratio"] >= POP_RATIO - 1e-9


def _frames(path, a, b, fps, width, height):
    import follow
    try:
        return follow._decode_gray(path, a, b, fps, width, height, 30)
    except Exception:  # noqa: BLE001 — that cut goes unmeasured
        return []


def measure_pop(path, e0, s1, rect, fps, src_w, src_h):
    """pop_score across the join (e0 -> s1, SOURCE seconds) of the proxy at
    ``path``, inside ``rect``; None when the frames cannot be decoded."""
    if not path or not os.path.exists(path):
        return None
    w = 320
    h = max(2, int(round(w * float(src_h) / max(float(src_w), 1.0) / 2.0)) * 2)
    fd = 1.0 / max(fps, 1.0)
    before = _frames(path, max(0.0, e0 - 3.2 * fd), e0 - 0.2 * fd, fps, w, h)
    after = _frames(path, s1 + 0.2 * fd, s1 + 3.2 * fd, fps, w, h)
    return pop_score(before, after, rect)


# ── what the frame shows ─────────────────────────────────────────────────

def _source_card(edl, a, b):
    """(card, blocked) for the program span [a, b]: the single-rect source
    card live over all of it, or None; ``blocked`` names why no step can play
    there (a stacked card, or a card that starts or ends inside the span)."""
    import picture_cards
    for cd in ((edl.get("effects") or {}).get("picture_cards") or []):
        if not isinstance(cd, dict):
            continue
        try:
            s, e = float(cd["start"]), float(cd["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if min(e, b) - max(s, a) <= 1e-3:
            continue
        if cd.get("panels"):
            return None, "a stacked card shows two regions (no step plays inside it)"
        if not (s <= a + 1e-3 and e >= b - 1e-3):
            return None, f"picture card '{cd.get('id')}' starts or ends inside that shot"
        if picture_cards.source_fed(cd) and cd.get("source"):
            return cd, None
        return None, (f"picture card '{cd.get('id')}' shows the program picture, "
                      "not a source crop it can step")
    return None, None


def _visible_rect(edl, index, src_t, card):
    """The source rect the frame shows at SOURCE second src_t."""
    import picture_cards
    import renderer
    if card is not None:
        r = picture_cards.source_at(card, src_t)
        if r:
            return r
    video = (index or {}).get("video") or {}
    try:
        sw, sh = float(video["width"]), float(video["height"])
    except (KeyError, TypeError, ValueError):
        return [0.0, 0.0, 1.0, 1.0]
    at = _at()
    frame = edl.get("frame") or {}
    W, H = renderer.frame_dims(sw, sh, frame.get("ratio") or "source")
    src, _dest = renderer.picture_mapping(
        sw, sh, W, H, at._frame_mode_at_source(edl, src_t),
        at._frame_focus_at_source(edl, src_t), frame.get("picture"))
    return src


def card_step_scale(card, src_t, step, src_w, src_h, W, H, tight_ok=True):
    """(scale, why) of a card step at SOURCE second src_t: tighter by
    ``step`` while the enlargement stays under the source upscale cap, else
    wider by it while the source has room, else the larger of what fits
    (None when that is under STEP_MIN). ``tight_ok`` False offers only the
    wide side (a tighter card would crop the speaker's head)."""
    import picture_cards
    rect = picture_cards.source_at(card, src_t)
    box = card.get("box")
    if not rect or not box:
        return None, "the card has no source rect"
    rect = picture_cards.match_rect(rect, box, src_w, src_h, W, H)
    bw = (box[2] - box[0]) * W
    rw = max(1e-6, (rect[2] - rect[0]) * src_w)
    k = bw / rw
    cap = picture_cards.SOURCE_UPSCALE_CAP
    tight = cap / k if tight_ok else 0.0
    wide = min(1.0 / max(rect[2] - rect[0], 1e-6), 1.0 / max(rect[3] - rect[1], 1e-6))
    want = 1.0 + step
    if tight >= want - 1e-6:
        return round(want, 4), f"tight {step * 100:.0f}%"
    if wide >= want - 1e-6:
        if not tight_ok:
            return round(1.0 / want, 4), f"wide {step * 100:.0f}%"
        return round(1.0 / want, 4), (f"wide {step * 100:.0f}% (the {int(src_w)}x"
                                      f"{int(src_h)} source is already {k:.2f}x — "
                                      "tighter would soften it)")
    best = max(tight, wide)
    if best - 1.0 < STEP_MIN - 1e-6:
        if not tight_ok:
            return None, ("a tighter card would crop the speaker's head and the "
                          "source has no room to step wide")
        return None, (f"the card shows {k:.2f}x of a source with no room left "
                      "either way")
    if tight >= wide:
        return round(best, 4), f"tight {(best - 1) * 100:.0f}% (the most the cap allows)"
    return round(1.0 / best, 4), f"wide {(best - 1) * 100:.0f}% (the most the source allows)"


# A tight card step trims every side of what the card shows around its
# centre (picture_cards.step_rect). The owner's first complaint is a card or
# crop that lets part of the speaker's face leave the frame, so a tight step
# is refused wherever it would push the speaker's head (hair, ears, chin:
# picture_cards.head_box) further out of the card than the card's own
# framing already does, by more than HEAD_TOL of the card's size; the step
# then goes wide (more of the source, nothing cropped) or the cut stays bare.
HEAD_TOL = 0.005


def _head_overflow(rect, head):
    """How far ``head`` runs past ``rect`` on its four sides, as shares of
    the rect's width / height (0 when it is inside)."""
    w = max(1e-6, rect[2] - rect[0])
    h = max(1e-6, rect[3] - rect[1])
    return (max(0.0, rect[0] - head[0]) / w + max(0.0, head[2] - rect[2]) / w
            + max(0.0, rect[1] - head[1]) / h + max(0.0, head[3] - rect[3]) / h)


def tight_step_crops_head(card, k, moments, faces_at, src_w, src_h, W, H):
    """The first SOURCE second of ``moments`` where a tight step ``k`` (> 1)
    of ``card`` would push the speaker's head (the largest face there)
    further out of the card than its unstepped framing does, or None. A
    moment with no face measured is no evidence either way."""
    import picture_cards
    box = card.get("box")
    if not box or k <= 1.0:
        return None
    plain = dict(card, cut_steps=None)
    for t in moments:
        try:
            faces = faces_at(t)
        except Exception:  # noqa: BLE001 — a face we cannot read is no evidence
            faces = None
        faces = [f for f in faces or [] if f and len(f) == 4
                 and f[2] > f[0] and f[3] > f[1]]
        rect = picture_cards.source_at(plain, t)
        if not faces or not rect:
            continue
        face = max(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))
        head = picture_cards.head_box(face)
        before = picture_cards.match_rect(rect, box, src_w, src_h, W, H)
        after = picture_cards.match_rect(picture_cards.step_rect(rect, k), box,
                                         src_w, src_h, W, H)
        if _head_overflow(after, head) > _head_overflow(before, head) + HEAD_TOL:
            return t
    return None


def _step(zooms, t, prog, fps):
    """The framing scale change across program second t (0.05 = 5%)."""
    import renderer
    dt = 0.5 / fps
    zb = renderer.zoom_state_at(zooms, t - dt, prog)
    za = renderer.zoom_state_at(zooms, t + dt, prog)
    return max(za[0], zb[0]) / max(1e-6, min(za[0], zb[0])) - 1.0


def _flattens(base, fx, zooms, item, index, tl, fps, nxt):
    """Why a tight step released at ``nxt`` would cancel the framing change
    an editor's own zoom already makes there (a punch starting on that cut:
    1.08 -> 1.12 reads as the same frame), or ''."""
    import renderer
    prog = float(tl.out_duration)
    if nxt >= prog - 1e-3:
        return ""
    try:
        before = renderer.camera_zooms(dict(base, effects=dict(fx, zooms=zooms)),
                                       index, tl, fps)
        after = renderer.camera_zooms(dict(base, effects=dict(fx, zooms=zooms + [item])),
                                      index, tl, fps)
    except Exception:  # noqa: BLE001 — no geometry, no claim
        return ""
    was, now = _step(before, nxt, prog, fps), _step(after, nxt, prog, fps)
    if was >= CUT_STEP_MIN - 1e-6 and now < CUT_STEP_MIN - 1e-6:
        mine = [z.get("id") for z in zooms if not is_step_zoom(z)
                and abs(float(z.get("start", -1)) - nxt) <= 0.1]
        who = f"zoom {', '.join(mine)}" if mine else "the zoom there"
        return (f"releasing at {nxt:.2f}s into {who} would shrink that cut's framing "
                f"change to {now * 100:.0f}% — leave this cut bare, or end that zoom "
                "elsewhere first")
    return ""


def _source_span(tl, a_src, b_src):
    """(t0, t1) SOURCE seconds of a card step covering [a_src, b_src]: from
    the start of the kept segment holding a_src (the cut's first frame) and,
    when b_src ends that segment (the next cut is a keep join), to its end —
    so every python mirror asking about the cut's own frames finds it."""
    for s, e in getattr(tl, "segs", None) or []:
        s, e = float(s), float(e)
        if s - 1e-6 <= a_src <= e + 1e-6:
            t0 = s if a_src - s < 0.02 else a_src
            t1 = e if e - b_src < 0.02 else b_src
            return round(t0, 4), round(max(t1, t0 + 0.01), 4)
    return round(a_src, 4), round(b_src, 4)


def _block_end(tl, c, card):
    """Program second where the render block holding program second ``c``
    ends: the end of its kept segment, or the card's own end inside it."""
    end = float(tl.out_duration)
    for off, L in zip(tl.offsets, tl.seg_out_len):
        if off - 1e-6 <= c < off + L - 1e-6:
            end = off + L
            break
    try:
        ce = float(card["end"])
    except (KeyError, TypeError, ValueError):
        return end
    return min(end, ce) if ce > c + 1e-3 else end


# ── the tool ─────────────────────────────────────────────────────────────

def _fmt_ev(row):
    bits = []
    j = row.get("jump")
    if j:
        bits.append(f"head moves {j['shift']:.2f} face-widths")
    p = row.get("pop")
    if p:
        bits.append(f"picture change {p['ratio']:.1f}x its motion")
    return ", ".join(bits) or "no measurement"


def conceal_jump_cuts(ctx, mode="scale_step", step=None, at=None):
    """Report (mode='report'), write or remove (mode='off') the optional
    cut steps; see the module docstring."""
    import renderer
    import taste
    from timeline import Timeline
    atools = _at()
    mode = str(mode or "scale_step").strip().lower()
    if mode not in ("scale_step", "off", "report"):
        return ("REJECTED: mode is 'report' (list every same-shot jump cut with "
                "its evidence and options; writes nothing), 'scale_step' (write "
                "hard alternating framing steps on the same-angle cuts that "
                "visibly pop) or 'off' (remove them all).")
    if mode == "report":
        import jump_cut_report
        return jump_cut_report.tool_report(ctx)
    cur = json.loads(json.dumps(ctx.latest_edl()["json"]))
    base, nz, nc = strip(cur)
    if mode == "off":
        if not (nz or nc):
            return ("Nothing to remove: this edit has no cut steps (jump cuts are "
                    "bare, which is the default). Nothing was written.")
        return ctx.write_edl(base, f"removed {nz + nc} jump-cut step(s) "
                                   "(conceal_jump_cuts off)")
    if not getattr(ctx, "has_main_video", True) or not base.get("keep"):
        return "REJECTED: jump cuts belong to the main video; this program has none."
    try:
        st = STEP_DEFAULT if step is None else float(step)
    except (TypeError, ValueError):
        return f"REJECTED: step is a number {STEP_MIN:g}-{STEP_MAX:g} (default {STEP_DEFAULT:g})."
    if not STEP_MIN - 1e-9 <= st <= STEP_MAX + 1e-9:
        return (f"REJECTED: step {st:g} is outside {STEP_MIN:g}-{STEP_MAX:g}: below "
                f"{STEP_MIN:g} the frame stutters (the same frame popping, not a "
                f"cut), above {STEP_MAX:g} it reads as a zoom.")
    targets = None
    if at not in (None, "", []):
        try:
            targets = sorted(float(v) for v in (at if isinstance(at, (list, tuple)) else [at]))
        except (TypeError, ValueError):
            return "REJECTED: at is a list of program seconds (the cuts to conceal)."
    index = getattr(ctx, "index", None) or {}
    video = index.get("video") or {}
    try:
        src_w, src_h = float(video["width"]), float(video["height"])
        fps = float(video.get("fps") or 30.0)
    except (KeyError, TypeError, ValueError):
        return "REJECTED: the source's size is unknown (index the video first)."
    tl = Timeline(base["keep"], base.get("inserts") or [], base.get("speed") or [])
    import motion_tools
    faces = motion_tools.jump_cut_measure(ctx)
    rows = taste.uncovered_jump_cuts(base, index, tl, measure=faces)
    if not rows:
        return ("No bare same-angle jump cut in this edit (camera changes, inserts, "
                "transitions and framing changes already sit on every join). "
                "Nothing was written.")
    try:
        proxy = ctx.proxy_path()
    except Exception:  # noqa: BLE001 — face evidence only
        proxy = None
    seg_src = {}
    for i in range(len(tl.segs) - 1):
        seg_src[round(tl.offsets[i + 1], 2)] = (float(tl.segs[i][1]),
                                                float(tl.segs[i + 1][0]))
    measured = sorted(rows, key=lambda r: -float(r.get("skip") or 0))[:MEASURE_MAX]
    seen_picture = False
    for r in measured:
        join = seg_src.get(round(float(r["t"]), 2))
        if not join or not proxy:
            continue
        card, _blocked = _source_card(base, r["t"] - 0.05, r["t"] + 0.05)
        rect = _visible_rect(base, index, join[1], card)
        r["pop"] = measure_pop(proxy, join[0], join[1], rect, fps, src_w, src_h)
        seen_picture = seen_picture or r["pop"] is not None
    if targets is not None:
        chosen = [r for r in rows if any(abs(float(r["t"]) - x) <= AT_TOL_S for x in targets)]
        unmatched = [x for x in targets
                     if not any(abs(float(r["t"]) - x) <= AT_TOL_S for r in rows)]
    else:
        chosen = [r for r in rows if r.get("jarring") or pops(r.get("pop"))]
        unmatched = []
    if not chosen:
        if targets is not None:
            return (f"None of {', '.join(f'{x:g}' for x in targets)}s is a bare "
                    "same-angle jump cut (bare cuts: "
                    + ", ".join(f"{float(r['t']):g}" for r in rows[:12])
                    + "). Nothing was written.")
        quiet = ", ".join(f"{float(r['t']):g}s ({_fmt_ev(r)})" for r in rows[:8])
        if not seen_picture:
            return ("The picture could not be measured here (no decodable footage), "
                    "and no head jump was found on the bare jump cuts: "
                    + ", ".join(f"{float(r['t']):g}s" for r in rows[:12])
                    + ". Nothing was written. Pass at=[...] with the cuts you saw pop "
                    "on the render.")
        return ("No bare jump cut measurably pops (head jump under "
                f"{taste.JUMP_CUT_JAR_SHIFT:g} face-widths and picture change under "
                f"{POP_RATIO:g}x its motion): {quiet}. They read as the speaker's "
                "own motion — leave them bare. Nothing was written. Pass at=[...] "
                "to conceal a cut you saw pop on the render.")
    try:
        cuts = renderer.camera_cuts(base, index, tl, fps)
    except Exception:  # noqa: BLE001
        cuts = []
    prog = float(tl.out_duration)
    W, H = motion_tools._canvas_size(ctx, base)
    fx = dict(base.get("effects") or {})
    zooms = [dict(z) for z in fx.get("zooms") or []]
    cards = [dict(cd) for cd in fx.get("picture_cards") or []]
    by_id = {cd.get("id"): cd for cd in cards}
    written, skipped, released_at = [], [], None
    for r in sorted(chosen, key=lambda q: float(q["t"])):
        c = float(r["t"])
        if released_at is not None and abs(released_at - c) < 1e-3:
            written.append((c, "released", "steps back to the base framing here", r))
            released_at = None
            continue
        released_at = None
        nxt = next((x for x in cuts if x > c + 0.2), None)
        nxt = min(prog, nxt if nxt is not None else prog)
        if nxt - c < 0.2:
            skipped.append((c, "the next cut is under 0.2 s away"))
            continue
        card, blocked = _source_card(base, c, nxt)
        if card is not None:
            # A card composes the main footage per render BLOCK (a kept
            # segment, split only at card edges and follow/focus edges) and
            # takes a step for the whole block (by its midpoint): a card step
            # holds to the end of the block, through a camera change inside
            # it, so what renders is what is written and reported.
            blk = _block_end(tl, c, card)
            if blk > nxt + 1e-3:
                nxt = blk
                card, blocked = _source_card(base, c, nxt)
        if blocked:
            skipped.append((c, blocked))
            continue
        a_src = tl.out_to_src(c + 1e-3)
        b_src = tl.out_to_src(nxt - 1e-3)
        if a_src is None or b_src is None:
            skipped.append((c, "spliced media sits there"))
            continue
        if card is not None:
            mid = (a_src + b_src) / 2.0
            k, why = card_step_scale(card, mid, st, src_w, src_h, W, H)
            if k is not None and k > 1.0:
                # never a tighter card that crops the speaker's head
                def faces_at(t, lo=a_src, hi=b_src):
                    got = faces(t) if faces is not None else None
                    return got or taste._sample_faces(
                        index, max(lo, t - taste.JUMP_CUT_FACE_NEAR_S),
                        min(hi, t + taste.JUMP_CUT_FACE_NEAR_S), t)
                edge = 2.0 / fps
                crops = tight_step_crops_head(
                    card, k, [min(a_src + edge, mid), mid, max(b_src - edge, mid)],
                    faces_at, src_w, src_h, W, H)
                if crops is not None:
                    k, why = card_step_scale(card, mid, st, src_w, src_h, W, H,
                                             tight_ok=False)
                    if k is not None:
                        why += (f" — tighter would crop the speaker's head "
                                f"(source {crops:.2f}s)")
            if k is None:
                skipped.append((c, why))
                continue
            # never a step that opens a sliver of a pillar or frame edge
            # along the card's edge (picture_cards.sliver_shift)
            band = atools._step_sliver(ctx, card, k, a_src, b_src, src_w, src_h, W, H)
            if band:
                skipped.append((c, band))
                continue
            t0, t1 = _source_span(tl, a_src, b_src)
            tgt = by_id[card["id"]]
            spans = list(tgt.get("cut_steps") or [])
            spans.append({"t0": t0, "t1": t1, "scale": k})
            tgt["cut_steps"] = spans
            written.append((c, "card", f"card '{card['id']}' {why} until {nxt:.2f}s", r))
        else:
            room, zbase, stacked_z = atools._zoom_room(ctx, dict(base, effects=dict(fx, zooms=zooms)), c, nxt)
            strength = st if room is None else min(st, math.floor(max(room, 0.0) * 100) / 100)
            if strength < STEP_MIN - 1e-9:
                skipped.append((c, f"the source is already enlarged {zbase or 0:.1f}x — "
                                   "no room for a step"))
                continue
            targets_ = atools._face_at_source_moments(ctx, base, [a_src + 2.0 / fps])
            aim = targets_.get(a_src + 2.0 / fps)
            item = {"id": atools._next_item_id(zooms, ID_PREFIX), "start": round(c, 3),
                    "end": round(nxt, 3), "strength": round(strength, 3), "ramp_s": 0.0,
                    "cx": aim[0] if aim else 0.5, "cy": aim[1] if aim else 0.5,
                    "target_measured": bool(aim), "purpose": PURPOSE, "cut_step": True}
            clash = _flattens(base, fx, zooms, item, index, tl, fps, nxt)
            if clash:
                skipped.append((c, clash))
                continue
            zooms.append(item)
            written.append((c, "zoom", f"tight {strength * 100:.0f}% until {nxt:.2f}s, aimed "
                                       f"{'at the face' if aim else 'at the centre (no face found)'} "
                                       f"[{item['id']}]", r))
        released_at = nxt
    steps = [w for w in written if w[1] != "released"]
    if not steps:
        return ("No cut could take a step: "
                + "; ".join(f"{c:g}s — {why}" for c, why in skipped)
                + ". Nothing was written.")
    fx["zooms"] = zooms
    if cards:
        fx["picture_cards"] = cards
    base["effects"] = fx
    res = ctx.write_edl(base, f"concealed {len(steps)} popping jump cut(s) with hard "
                              f"{st * 100:.0f}% framing steps (optional cut hygiene)")
    if not str(res).startswith("EDL v"):
        return res
    lines = [f"  {c:.2f}s: {what} — {_fmt_ev(r)}" for c, _kind, what, r in written]
    out = res + "\nCut steps (hard, no motion; each holds to the next cut):\n" + "\n".join(lines)
    ends = {round(float(w[3]["t"]), 2) for w in written if w[1] == "released"}
    ends |= {round(float(z["end"]), 2) for z in zooms if is_step_zoom(z)}
    for cd in cards:
        for sp in cd.get("cut_steps") or []:
            p = tl.src_to_out(float(sp["t1"]) - 1e-3)
            if p is not None:
                ends.add(round(p + 1e-3, 2))
    stepped_back = [r for r in rows if r not in chosen
                    and any(abs(float(r["t"]) - x) <= 0.02 for x in ends)]
    if stepped_back:
        out += ("\nAlso changes framing on (a step ending there): "
                + ", ".join(f"{float(r['t']):g}s" for r in stepped_back) + ".")
    left = [r for r in rows if r not in chosen and r not in stepped_back]
    if left and targets is None:
        out += ("\nLeft bare (no measurable pop): "
                + ", ".join(f"{float(r['t']):g}s" for r in left[:10])
                + (f" (+{len(left) - 10} more)" if len(left) > 10 else "") + ".")
    if skipped:
        out += "\nNot stepped: " + "; ".join(f"{c:g}s — {why}" for c, why in skipped) + "."
    if unmatched:
        out += ("\nNot a bare same-angle jump cut: "
                + ", ".join(f"{x:g}s" for x in unmatched) + ".")
    if nz or nc:
        out += f"\nReplaced the {nz + nc} earlier cut step(s)."
    out += ("\nThis is optional cut hygiene, not a camera move: check the cuts on "
            "the render, and conceal_jump_cuts(mode='off') removes every step. Re-run "
            "it after re-cutting (steps are placed on the current cuts).")
    return out


TOOL_SPECS = {
    "conceal_jump_cuts": (
        conceal_jump_cuts,
        "OPTIONAL cut hygiene — never a default. mode='report' FIRST: it lists "
        "every same-shot jump cut (how visible the jump is, what already covers "
        "it — a graphic in/out, a caption change, a step — the hook and stutter "
        "flags, and the options: leave it, move a graphic change onto it, "
        "restore or re-cut the join, or a step) and writes nothing. "
        "mode='scale_step' hides same-angle jump cuts that "
        "VISIBLY POP (pause removal on one camera: the head or hands jump inside a "
        "constant frame) with HARD, non-animated framing steps. Each selected cut "
        "steps the framing by `step` (0.10-0.15, default 0.12; under ~10% a step "
        "stutters) and holds it to the "
        "next cut, where it steps back, so consecutive cuts alternate tight/wide "
        "like a second camera. Full-frame footage gets a cut-step zoom (ramp 0, "
        "aimed at the face); a source picture card alternates its SOURCE crop "
        "(stepping wide instead of tight when the source is near its upscale cap, "
        "e.g. 480p archival, or when tighter would crop the speaker's head). Only bare same-angle cuts that measurably pop are "
        "touched (a head jump, or a picture change well above the speaker's own "
        "motion); `at` = [program seconds] names cuts you saw pop instead. This is "
        "not an expressive zoom and not a rule: a bare jump cut is fine, so use it "
        "only when the render shows cuts popping (typically a static archival or "
        "single-camera talk tightened by pause removal), after the cut is final. "
        "Re-running replaces earlier steps; mode='off' removes them all.",
        {"mode": {"type": "string", "enum": ["report", "scale_step", "off"]},
         "step": {"type": "number"},
         "at": {"type": "array", "items": {"type": "number"}}}),
}
