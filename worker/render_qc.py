"""Deterministic PICTURE QC over a finished render — audio_qc for the eyes.

WHY (judges, Oct 2026)

Every framing check in the editor reads the EDL and its python mirrors; none
looked at the file that shipped. Three judged defects were only visible
there: a one-frame framing pop on the last frame before a cut (the Elon
short at 21.888 s — the crop switched and the zoom released a frame before
the picture cut), a speaker's nose 3% from the frame edge for two seconds,
and showcase finals that went out without the free-tier watermark. All of
it is measurable in two cheap passes over the rendered file:

  * FRAME DIFFERENCES — every programme frame decoded at 64 px wide (grey,
    streamed: memory stays flat). A cut changes most of the picture at
    once; so does a framing jump. A frame whose change is GLOBAL (most of
    the tiny frame moved, by a lot) and is not on a cut of the edit (keep
    joins, inserts, indexed camera cuts, crop switches — renderer.
    camera_cuts) nor on a deliberate event (a punch zoom's step, a
    full-frame graphic's edge) is reported: a one-frame pop when the next
    or previous frame jumps too, else a jump off a cut. Graphics change a
    region, not the frame, and stay under the bar.
  * FACES — frames sampled at 2 fps (Haar frontal + profile, follow.detect,
    with follow.carry bridging a turned head the detector loses). The
    largest face of a frame (and any face nearly as large) within
    EDGE_CLIP of the frame's edge — or of its picture card's edge, inside a
    card — for two samples running is a clipped face. A face too wide to
    clear both edges is never reported for touching one, a turned face only
    on the side it looks to (the back of a head may meet the edge), and
    nothing covering the watermark's robot is a face.
  * LAYOUT CHANGES (judges, Oct 2026: the Elon stack opened and closed on
    a frame of bare dark canvas — mean luma 66.6 -> 27.4 in one frame) —
    every picture card's entrance and exit is watched on the same pass's
    mean luma: a dissolve must be gradual, so any single-frame drop or
    flash of more than LUMA_JUMP inside it (and well past its even step)
    is a glitch; a card that cuts in or out — on a cut of the edit, another
    card's edge or a focus/follow span edge, where the renderer cuts too —
    may change level on its cut frame, never blink (dark for a frame or
    three and back). Timed flashes, cutaways and full-frame graphics at
    the same moment are deliberate.
  * FACES INSIDE EVERY PANEL (owner rule) — on the face samples: the head
    (the detector box with hair, chin and side margins, as picture_cards.
    panel_keep frames it) of the largest face in a picture card's panel
    must lie inside that panel; the chin and the sides inside the frame of
    a full-frame crop (a close-up may lose the top of the hair there).
  * THE HEADLINE BAND — where a persistent headline holds a band, the band
    is read on frames of its own: no ink there for longer than
    BAND_EMPTY_S (between graphics, before the headline's return) is a
    hole the viewer reads as a dropped layer.
  * BRANDING — the end card (when the variant carries one) is compared
    with the card the renderer composes, and the free-tier robot (when the
    variant should carry the watermark) with the robot image, both by
    normalised correlation at the exact geometry the renderer uses.

Same contract as audio_qc: pure measurement in, plain-language findings out,
never a change to the edit. The agent repairs each finding or keeps it
deliberately and says why (quality_verifier records them as justifiable).
Failure returns None — a render never fails over its own review.
"""
import math
import os
import subprocess
import time

QC_VERSION = 2
DIFF_W = 64                 # width of the frame-difference pass
GLOBAL_SHARE = 0.45         # share of the tiny frame that must change...
GLOBAL_LEVEL = 20.0         # ...with a mean |diff| at least this (0-255)
PIXEL_STEP = 14             # a tiny pixel "changed" past this step
SPIKE_RATIO = 6.0           # and this many times the local median change
FACE_FPS = 2.0
FACE_W = 360
MAX_FACE_FRAMES = 120
EDGE_CLIP = 0.06            # a face this close to a frame/card edge
CLIP_RUN = 2                # ...for this many samples running
MAX_FINDINGS = 6
ENDCARD_MIN_NCC = 0.80
ROBOT_MIN_NCC = 0.60
ROBOT_MIN_PX = 40           # a robot shorter than this is not measured
ROBOT_SAMPLES = 6           # frames the watermark is read on (one seek each)
BUDGET_S = 60.0
MAX_FULL_PASS_S = 180.0     # programmes longer than this skip the frame pass
                            # and sample faces on keyframes
LUMA_JUMP = 0.5             # a frame-to-frame mean-luma change past this share
LUMA_FLOOR = 24.0           # ...between levels, the brighter above this
LAYOUT_REACH = 2            # frames either side of a layout change watched
LUMA_STEP_X = 2.0           # a dissolve's step past this many even steps...
LUMA_STEP_MIN = 8.0         # ...and this many levels is not the dissolve
# The head a panel must hold around a detector box (picture_cards.
# panel_keep's margins) and how far past an edge it may reach (a share of the
# panel) before it is a cut face.
HEAD_HAIR = .22
HEAD_CHIN = .05
HEAD_SIDE = .06
CUT_TOL = .015
BAND_W = 240                # width the headline band is read at
BAND_STEP = 36              # a pixel this far off its row's median is ink
BAND_INK = .003             # ...and this share of the band of it is text
BAND_EMPTY_S = .15          # an empty band longer than this is a hole
BAND_EDGE_S = .65           # ...except at the band's very start or end:
                            # a graphic landing (leaving) this close to the
                            # headline's own edge takes the band from (to)
                            # it (motion_layer.YIELD_EDGE_S, + a frame)


# ═════════════════════════════════════════════════════════════════════════
# The plan: what the edit says the picture should do
# ═════════════════════════════════════════════════════════════════════════

def plan(edl, index, *, W, H, fps, outro_s=0.0, want_wm=False,
         wm_anchor_y=None, src_fps=None, origin=None):
    """Everything the check needs from the edit, in programme seconds:
    the cut frames, the event frames (deliberate global changes), the
    picture-card windows, the end card and the watermark geometry."""
    import renderer
    from timeline import Timeline
    edl = edl or {}
    fps = float(fps or 30.0)
    tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                  edl.get("speed"))
    program_s = renderer.program_render_s(tl, fps)
    cuts = set()
    for o in {origin, None, 0.0}:
        try:
            cuts.update(renderer.camera_cuts(edl, index, tl, fps, src_fps, o))
        except Exception:
            continue
    cut_frames = sorted({renderer.first_frame_at(c, fps) for c in cuts})
    events = []                   # (frame lo, frame hi) deliberate changes
    deliberate = []               # ...of them, those not the cards' own
    try:
        zooms = renderer.camera_zooms(edl, index, tl, fps, src_fps, origin)
    except Exception:
        zooms = ((edl.get("effects") or {}).get("zooms") or [])
    for z in zooms or []:
        for key in ("start", "end"):
            try:
                n = renderer.first_frame_at(float(z[key]), fps)
            except (KeyError, TypeError, ValueError):
                continue
            events.append((n, n))
    fx = edl.get("effects") or {}
    # Layers that can change the WHOLE picture where they start and end
    # (their entrances animate over a few frames). A motion graphic counts
    # only when its measured footprint covers much of the frame: a caption
    # or a lower third is a region, and exempting it would hide a pop on
    # its edge.
    layers = list(fx.get("picture_cards") or [])
    # Overlays (a B-roll cutaway is a cover overlay) and aspect shifts are
    # timed by a start and a duration: both edges are deliberate, and an
    # aspect shift's whole morph is.
    for item in edl.get("overlays") or []:
        try:
            a = float(item["start"])
            layers.append({"start": a, "end": a + float(item["duration_s"])})
        except (KeyError, TypeError, ValueError):
            continue
    for item in fx.get("frame_shifts") or []:
        try:
            a = float(item["at"])
            b = a + float(item.get("duration_s") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        events.append((renderer.first_frame_at(a, fps) - 1,
                       renderer.first_frame_at(b, fps) + 3))
        deliberate.append(events[-1])
    for item in edl.get("vectors") or []:
        try:
            if float(item.get("width") or 0) * float(item.get("height") or 0) >= .4:
                layers.append(item)
        except (AttributeError, TypeError, ValueError):
            continue
    # A finishing effect (a flash, a shake, an agent's own filter chain) may
    # change the whole picture anywhere inside its window: all of it is
    # deliberate.
    for item in list(fx.get("stylize") or []) + list(fx.get("custom") or []):
        if not isinstance(item, dict):
            continue
        a = item.get("start")
        b = item.get("end")
        timed = a is not None or b is not None
        a = 0.0 if a is None else float(a)
        b = float(program_s) if b is None else float(b)
        events.append((renderer.first_frame_at(a, fps) - 1,
                       renderer.first_frame_at(b, fps) + 3))
        if timed:
            # a timed flash or shake (a whole-programme look never dips)
            deliberate.append(events[-1])
    for item in edl.get("motion") or []:
        if not isinstance(item, dict):
            continue
        box = ((item.get("footprint") or {}).get("box")) or item.get("box")
        try:
            area = (float(box[2]) - float(box[0])) * (float(box[3]) - float(box[1]))
        except (TypeError, ValueError, IndexError):
            area = 1.0
        if area >= .4:
            layers.append(item)
    card_items = {id(c) for c in fx.get("picture_cards") or []}
    for item in layers:
        if not isinstance(item, dict):
            continue
        for key, lo, hi in (("start", 0, 3), ("end", -3, 0)):
            try:
                n = renderer.first_frame_at(float(item[key]), fps)
            except (KeyError, TypeError, ValueError):
                continue
            events.append((n + lo, n + hi))
            if id(item) not in card_items:
                # a cutaway, a full-frame graphic: its own edge may change
                # the whole picture's level (the layout watch leaves it be)
                deliberate.append((n + lo - 1, n + hi + 1))
    trans = fx.get("transition")
    if trans:
        try:
            reach = int(math.ceil(float((trans or {}).get("duration_s") or .5)
                                  * fps)) + 1
        except (AttributeError, TypeError, ValueError):
            reach = int(fps // 2)
        events += [(n - reach, n + reach) for n in cut_frames]
    cards = []
    for c in fx.get("picture_cards") or []:
        try:
            # a stacked card's footage sits in its panels' boxes
            areas = [[float(v) for v in p["box"]] for p in c.get("panels") or []] \
                or [[float(v) for v in c["box"]]]
            cards.append((float(c["start"]), float(c["end"]), areas))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    # Programme windows whose picture is not the speaker's framing: spliced
    # inserts (B-roll, title and colour cards — a canvas programme is all
    # inserts) and full-frame overlay cutaways. A face in a stock clip is
    # nobody the editor framed.
    others = [(float(ws), float(ws) + float(wd))
              for ws, wd in tl.insert_positions()]
    for item in edl.get("overlays") or []:
        if not isinstance(item, dict) or item.get("screen") or \
                item.get("fit") not in ("cover", "picture"):
            continue
        try:
            a = float(item["start"])
            others.append((a, a + float(item["duration_s"])))
        except (KeyError, TypeError, ValueError):
            continue
    end_frame = int(round(float(program_s) * fps))
    # Layout changes: each picture card's start and end, with the frames its
    # dissolve spans (an animated end the renderer cuts — on a cut of the
    # edit — is a cut here too).
    layout = []
    import picture_cards
    # Where the renderer cuts an animated end too (build_filtergraph): on a
    # cut of the edit, on another card's edge (its layout is the
    # neighbour) and on a focus-track or follow-span edge (the footage's
    # own shot change). Judged as a cut there, never as a dissolve.
    cut_like = set(cut_frames)
    edge_t = []
    for c in fx.get("picture_cards") or []:
        try:
            edge_t.append((id(c), float(c["start"])))
            edge_t.append((id(c), float(c["end"])))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    src_edges = []
    try:
        import follow
        src_edges += [float(x) for x in follow.edges(edl)]
    except Exception:
        pass
    for span in ((edl.get("frame") or {}).get("focus_track") or []):
        for key in ("t0", "t1"):
            try:
                src_edges.append(float(span[key]))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
    for x in src_edges:
        try:
            p = tl.src_to_out(x)
        except Exception:
            p = None
        if p is not None:
            cut_like.add(renderer.first_frame_at(p, fps))
    for c in fx.get("picture_cards") or []:
        try:
            ent, ext = picture_cards.animation_windows(c)
            c0, c1 = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        others_at = [renderer.first_frame_at(t, fps)
                     for cid, t in edge_t if cid != id(c)]
        for t, w, opening in ((c0, ent, True), (c1, ext, False)):
            n = renderer.first_frame_at(t, fps)
            if n <= 1 or n >= end_frame - 1:
                continue
            on_cut = any(abs(n - cf) <= 1 for cf in cut_like) or \
                any(abs(n - o) <= 1 for o in others_at)
            d = 0 if (w is None or on_cut) else \
                max(1, int(round((w[1] - w[0]) * fps)))
            lo, hi = (n, n + d) if opening else (n - d, n)
            layout.append({"t": round(t, 3), "lo": lo, "hi": hi,
                           "kind": "dissolve" if d else "cut",
                           "id": c.get("id"), "opening": opening})
    # Deliberate whole-picture fades the layout watch leaves alone: the
    # programme's fade in/out, and junction transitions that dip or flash.
    still = []
    try:
        fi = float(fx.get("fade_in_s") or 0.0)
        fo = float(fx.get("fade_out_s") or 0.0)
    except (TypeError, ValueError):
        fi = fo = 0.0
    if fi > 0:
        still.append((0, int(math.ceil(fi * fps)) + 1))
    if fo > 0:
        still.append((end_frame - int(math.ceil(fo * fps)) - 1, end_frame))
    still += deliberate
    if trans and str((trans or {}).get("style") or "").startswith(("dip", "flash")):
        reach = int(math.ceil(float((trans or {}).get("duration_s") or .5)
                              * fps)) + 1
        still += [(n - reach, n + reach) for n in cut_frames]
    # The persistent headline's band: where it draws (its footprint) over
    # its window — a hole there is read on the render (band_gaps).
    bands = []
    try:
        import caption_carry
        import motion_templates
        ar = caption_carry.frame_ar(int(W), int(H))
        for m in edl.get("motion") or []:
            if not isinstance(m, dict) or not motion_templates.persistent(m):
                continue
            # the whole band it holds (its y and height), full width, below
            # the free-tier mark's zone (the mark itself is not the band's
            # ink); else where it draws
            prm = m.get("params") or {}
            box = None
            try:
                y, h = float(prm["y"]), float(prm["height"])
                top = y - h / 2.0
                try:
                    import keepout
                    top = max(top, float(keepout.watermark_zone(int(W), int(H))[3]))
                except Exception:
                    pass
                if y + h / 2.0 - top > .02:
                    box = [.06, top, .94, y + h / 2.0]
            except (KeyError, TypeError, ValueError):
                box = None
            box = box or caption_carry.footprint_box(m, ar)
            if not box:
                import motion_layer
                box = motion_layer._band_box(m, int(W), int(H))
            if box:
                bands.append({"t0": float(m["start"]), "t1": float(m["end"]),
                              "box": [float(v) for v in box], "id": m.get("id")})
    except Exception:
        bands = []
    wm = None
    if want_wm:
        g = renderer.watermark_geometry(int(W), int(H), wm_anchor_y)
        wm = {"x": g["margin_x"], "y": g["margin_y"], "w": g["rw"], "h": g["rh"],
              "robot": renderer.robot_path()}
    endcard = None
    if outro_s and outro_s > 0:
        endcard = {"path": renderer.endcard_path(), "s": float(outro_s)}
    return {"program_s": float(program_s), "fps": fps, "W": int(W), "H": int(H),
            # the programme's last frame cuts to the end card (or the loop)
            "end_frame": end_frame,
            "cut_frames": cut_frames, "events": events, "cards": cards,
            "others": others, "watermark": wm, "endcard": endcard,
            "layout": layout, "still": still, "bands": bands}


# ═════════════════════════════════════════════════════════════════════════
# Passes
# ═════════════════════════════════════════════════════════════════════════

def _stream(cmd, w, h, deadline):
    """Yield grey frames (h x w uint8) from an ffmpeg rawvideo pipe."""
    import numpy as np
    size = w * h
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("picture QC budget")
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w)
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        proc.wait()


def _tiny_h(W, H, w):
    return max(2, int(round(w * float(H) / float(W) / 2.0)) * 2)


def frame_changes(path, program_s, W, H, deadline, lumas=None):
    """[(level, share)] per programme frame k >= 1: the mean |change| from
    frame k-1 (0-255) and the share of the tiny frame that changed.
    ``lumas`` (a list) receives every frame's mean luma (frame 0 on)."""
    import numpy as np
    w, h = DIFF_W, _tiny_h(W, H, DIFF_W)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-threads", "4",
           "-skip_loop_filter", "all", "-i", path,
           "-t", f"{program_s:.3f}", "-an", "-sn", "-dn", "-map", "0:v:0",
           "-vf", f"scale={w}:{h}:flags=area,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    out, prev = [], None
    for f in _stream(cmd, w, h, deadline):
        if lumas is not None:
            lumas.append(float(f.mean()))
        f = f.astype(np.int16)
        if prev is not None:
            d = np.abs(f - prev)
            out.append((float(d.mean()), float((d > PIXEL_STEP).mean())))
        prev = f
    return out


def jumps(changes, plan_):
    """Global jumps off the edit's cuts: [(kind, frame, near_cut_frame)]
    with kind 'pop' (a frame that lasted one frame between two jumps) or
    'jump' (one jump not on a cut or a deliberate event)."""
    import numpy as np
    n = len(changes)
    if not n:
        return []
    lv = np.array([c[0] for c in changes])
    cuts = set(plan_.get("cut_frames") or [])
    last = plan_.get("end_frame")
    if last is not None:
        cuts.add(int(last))
    events = plan_.get("events") or []

    def frame(k):                      # changes[k] is the jump INTO frame k+1
        return k + 1

    def is_global(k):
        if not 0 <= k < n:
            return False
        level, share = changes[k]
        if level < GLOBAL_LEVEL or share < GLOBAL_SHARE:
            return False
        lo, hi = max(0, k - 15), min(n, k + 16)
        near = np.concatenate([lv[lo:max(lo, k - 1)], lv[min(hi, k + 2):hi]])
        base = float(np.median(near)) if len(near) else 0.0
        return level >= SPIKE_RATIO * max(base, 1.0)

    def deliberate(fr):
        return fr in cuts or any(a <= fr <= b for a, b in events)
    out = []
    for k in range(n):
        if not is_global(k):
            continue
        fr = frame(k)
        if deliberate(fr) or (last is not None and fr >= last):
            continue
        if is_global(k + 1):
            out.append(("pop", fr, frame(k + 1) if frame(k + 1) in cuts else None))
        elif is_global(k - 1):
            out.append(("pop", fr - 1, frame(k - 1) if frame(k - 1) in cuts else None))
        else:
            near = min(cuts, key=lambda c: abs(c - fr)) if cuts else None
            out.append(("jump", fr, near if near is not None and abs(near - fr) <= 3
                        else None))
    seen, uniq = set(), []
    for row in out:
        if row[1] not in seen:
            seen.add(row[1])
            uniq.append(row)
    return uniq


def layout_glitches(lumas, plan_):
    """[(kind, frame, luma before, luma after, card id)] — at a picture
    card's layout change, a single-frame mean-luma drop ('dip') or rise
    ('flash') of more than LUMA_JUMP inside a dissolve (which must be
    gradual), or a blink at a cut (dark for a frame or three, then back).
    A cut's own change of level on its frame is the cut. Programme fades
    and dip/flash junction transitions are deliberate. A dissolve's step is
    a glitch only past LUMA_STEP_X times the even step of its whole change
    too: a short dissolve from a bright shot to a dark card halves the
    level on its last frame by design."""
    out, seen = [], set()
    n = len(lumas or [])
    still = plan_.get("still") or []
    for edge in plan_.get("layout") or []:
        lo = max(1, int(edge["lo"]) - LAYOUT_REACH)
        hi = min(n - 1, int(edge["hi"]) + LAYOUT_REACH)
        if hi < lo:
            continue
        # the even per-frame step of the dissolve's whole change
        span = max(1, int(edge["hi"]) - int(edge["lo"]) + 1)
        even = abs(lumas[min(n - 1, int(edge["hi"]))]
                   - lumas[max(0, int(edge["lo"]) - 1)]) / span
        for k in range(lo, hi + 1):
            if any(a <= k <= b for a, b in still):
                continue
            a, b = lumas[k - 1], lumas[k]
            if max(a, b) < LUMA_FLOOR:
                continue
            drop = b < (1.0 - LUMA_JUMP) * a
            flash = a < (1.0 - LUMA_JUMP) * b
            if not (drop or flash):
                continue
            if edge["kind"] != "cut" and \
                    abs(b - a) <= max(LUMA_STEP_X * even, LUMA_STEP_MIN):
                continue                      # the dissolve's own even step
            if edge["kind"] == "cut":
                # the cut's own step is the cut; a blink comes back
                back = lumas[k + 1:k + 4]
                if not (drop and back and max(back) >= (1.0 - LUMA_JUMP / 2) * a):
                    continue
            key = (edge.get("id"), edge["lo"])
            if key in seen:
                continue
            seen.add(key)
            out.append(("dip" if drop else "flash", k, round(a, 1), round(b, 1),
                        edge.get("id")))
    return out


def _head(box):
    """The head a panel must hold around a detector box (fractions)."""
    w, h = box[2] - box[0], box[3] - box[1]
    return [box[0] - HEAD_SIDE * w, box[1] - HEAD_HAIR * h,
            box[2] + HEAD_SIDE * w, box[3] + HEAD_CHIN * h]


def cut_faces(samples, plan_):
    """[(t0, t1, side, over, where)] runs of samples whose main face's head
    (_head: hair, chin, sides) reaches past the edge of the picture-card
    panel (or card) it sits in by more than CUT_TOL of that panel — or, on
    a full-frame crop, past the frame's bottom or sides: a cut face (the
    owner's rule). A turned face is judged on its leading side; ``over``
    the largest overreach seen (a share of the area)."""
    cards = plan_.get("cards") or []
    wm = plan_.get("watermark") or {}
    mark = None
    if wm.get("w") and plan_.get("W") and plan_.get("H"):
        mark = ((wm["x"] + wm["w"] / 2.0) / plan_["W"],
                (wm["y"] + wm["h"] / 2.0) / plan_["H"])
    others = plan_.get("others") or []
    rows = []
    for t, dets in samples:
        dets = [d for d in dets or [] if not (mark and d[0][0] <= mark[0] <= d[0][2]
                                              and d[0][1] <= mark[1] <= d[0][3])]
        if not dets or any(a <= t < b for a, b in others):
            rows.append((t, None))
            continue
        card = next((c for c in cards if c[0] <= t < c[1]), None)
        areas = card[2] if card else [[0.0, 0.0, 1.0, 1.0]]
        if areas and not isinstance(areas[0], (list, tuple)):
            areas = [areas]
        top = max(d[0][3] - d[0][1] for d in dets)
        worst = None
        for box, look in dets:
            if box[3] - box[1] < .6 * top:
                continue
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            area = next((ar for ar in areas if ar[0] - .02 <= cx <= ar[2] + .02
                         and ar[1] - .02 <= cy <= ar[3] + .02), None)
            if area is None:
                continue
            head = _head(box)
            aw, ah = area[2] - area[0], area[3] - area[1]
            sides = []
            if look <= 0:
                sides.append(("left", (area[0] - head[0]) / aw))
            if look >= 0:
                sides.append(("right", (head[2] - area[2]) / aw))
            sides.append(("bottom", (head[3] - area[3]) / ah))
            if card:
                sides.append(("top", (area[1] - head[1]) / ah))
            for side, over in sides:
                if over > CUT_TOL and (worst is None or over > worst[1]):
                    worst = (side, over)
        rows.append((t, worst))
    runs, cur = [], None
    for t, hit in rows:
        if hit and cur and cur["side"] == hit[0]:
            cur["t1"], cur["n"] = t, cur["n"] + 1
            cur["over"] = max(cur["over"], hit[1])
            continue
        if cur and cur["n"] >= CLIP_RUN:
            runs.append(cur)
        cur = ({"t0": t, "t1": t, "side": hit[0], "over": hit[1], "n": 1}
               if hit else None)
    if cur and cur["n"] >= CLIP_RUN:
        runs.append(cur)
    out = []
    for r in runs:
        where = "frame"
        if any(c[0] <= r["t0"] < c[1] for c in cards):
            card = next(c for c in cards if c[0] <= r["t0"] < c[1])
            where = "panel" if len(card[2]) > 1 else "card"
        out.append((r["t0"], r["t1"], r["side"], round(r["over"], 3), where))
    return out


def band_ink(path, band, W, H, deadline, fps=None):
    """[(t, ink share)] per programme frame inside ``band``'s window: the
    share of the band's pixels that stand off their row's median by
    BAND_STEP or more (type against a smooth backdrop)."""
    import numpy as np
    x0, y0, x1, y1 = band["box"]
    bx, by = int(max(0.0, x0) * W) // 2 * 2, int(max(0.0, y0) * H) // 2 * 2
    bw = max(2, int((min(1.0, x1) - max(0.0, x0)) * W) // 2 * 2)
    bh = max(2, int((min(1.0, y1) - max(0.0, y0)) * H) // 2 * 2)
    w = BAND_W
    h = max(2, int(round(w * bh / float(bw) / 2.0)) * 2)
    t0, t1 = float(band["t0"]), float(band["t1"])
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-threads", "4",
           "-ss", f"{t0:.3f}", "-i", path, "-t", f"{max(.05, t1 - t0):.3f}",
           "-an", "-sn", "-dn", "-map", "0:v:0",
           "-vf", f"crop={bw}:{bh}:{bx}:{by},scale={w}:{h}:flags=area,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    out = []
    step = 1.0 / float(fps or 30.0)
    for i, f in enumerate(_stream(cmd, w, h, deadline)):
        g = f.astype(np.int16)
        med = np.median(g, axis=1, keepdims=True)
        out.append((round(t0 + i * step, 3),
                    float((np.abs(g - med) >= BAND_STEP).mean())))
    return out


def band_gaps(ink, fps=30.0):
    """[(t0, t1)] runs of frames with no ink (under BAND_INK) longer than
    BAND_EMPTY_S — the headline band standing empty. A run at the band's
    very start or end under BAND_EDGE_S is the headline's own yield to a
    graphic landing (leaving) at its edge (motion_layer.yield_windows: a
    headline shown for a moment there is a flash), not a dropped layer."""
    out, run = [], None
    step = 1.0 / float(fps or 30.0)
    first = ink[0][0] if ink else 0.0
    last = ink[-1][0] if ink else 0.0

    def close(r):
        if not r:
            return
        length = r[1] + step - r[0]
        if length <= BAND_EMPTY_S + 1e-6:
            return
        if length < BAND_EDGE_S and (r[0] <= first + 1e-6
                                     or r[1] >= last - 1e-6):
            return
        out.append((round(r[0], 2), round(r[1] + step, 2)))
    for t, share in ink or []:
        if share < BAND_INK:
            run = [run[0], t] if run else [t, t]
            continue
        close(run)
        run = None
    close(run)
    return out


def face_samples(path, program_s, W, H, deadline, keyframes=False):
    """[(t, [(box, look)])] faces in frames sampled at FACE_FPS over the
    programme (boxes in fractions of the output frame); ``keyframes`` reads
    only the encoder's keyframes (a long programme: seconds, not minutes)."""
    import follow
    import subject
    cv2 = subject._cv2()
    if cv2 is None:
        return []
    cascades = subject._cascades(cv2)
    if not cascades:
        return []
    fps = min(FACE_FPS, MAX_FACE_FRAMES / max(program_s, 1e-6))
    w = FACE_W
    h = _tiny_h(W, H, w)
    off = .5 / fps
    if keyframes:
        out = []
        for t in _keyframe_times(path, program_s, MAX_FACE_FRAMES, deadline):
            got = _gray_frames(path, w, h, None, ss=t, t=None,
                               extra_vf=f"select='eq(n,0)',scale={w}:{h},",
                               deadline=deadline)
            if got:
                out.append((round(t, 3), follow.detect(got[0], cv2, cascades)))
        return out
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-threads", "4",
           "-ss", f"{off:.3f}", "-i", path, "-t", f"{max(.05, program_s - off):.3f}",
           "-an", "-sn", "-dn", "-map", "0:v:0",
           "-vf", f"fps={fps:.4f},scale={w}:{h},format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    times, grays, found = [], [], []
    for i, g in enumerate(_stream(cmd, w, h, deadline)):
        t = off + i / fps
        if t >= program_s:
            break
        times.append(round(t, 3))
        grays.append(g.copy())
        found.append(follow.detect(g, cv2, cascades))
    found = follow.carry(times, grays, found, cv2)
    return list(zip(times, found))


def _keyframe_times(path, program_s, most, deadline):
    """Up to ``most`` keyframe times of the programme, evenly picked (a
    seek to a keyframe decodes one frame)."""
    left = max(1.0, deadline - time.monotonic())
    r = subprocess.run(["ffprobe", "-v", "error", "-skip_frame", "nokey",
                        "-select_streams", "v:0", "-show_entries",
                        "frame=pts_time", "-of", "csv=p=0", path],
                       capture_output=True, text=True, timeout=left)
    ts = []
    for line in r.stdout.split():
        try:
            t = float(line.strip().strip(","))
        except ValueError:
            continue
        if 0.0 <= t < program_s:
            ts.append(t)
    if len(ts) > most:
        step = len(ts) / float(most)
        ts = [ts[int(i * step)] for i in range(most)]
    return ts


def clipped_faces(samples, plan_):
    """[(t0, t1, side, gap)] runs of samples whose main face sits within
    EDGE_CLIP of the frame's (or its card's) edge: side 'left' / 'right' /
    'top' / 'bottom', gap the smallest share seen."""
    cards = plan_.get("cards") or []
    wm = plan_.get("watermark") or {}
    mark = None
    if wm.get("w") and plan_.get("W") and plan_.get("H"):
        # the robot reads as a face to the detector: nothing that covers
        # the mark is the speaker
        mark = ((wm["x"] + wm["w"] / 2.0) / plan_["W"],
                (wm["y"] + wm["h"] / 2.0) / plan_["H"])
    others = plan_.get("others") or []
    rows = []
    for t, dets in samples:
        if mark:
            dets = [d for d in dets or []
                    if not (d[0][0] <= mark[0] <= d[0][2]
                            and d[0][1] <= mark[1] <= d[0][3])]
        if not dets or any(a <= t < b for a, b in others):
            rows.append((t, None))
            continue
        card = next((c for c in cards if c[0] <= t < c[1]), None)
        areas = card[2] if card else [[0.0, 0.0, 1.0, 1.0]]
        if areas and not isinstance(areas[0], (list, tuple)):
            areas = [areas]                    # a plan written before panels

        def area_of(box):
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            return next((ar for ar in areas
                         if ar[0] - .02 <= cx <= ar[2] + .02
                         and ar[1] - .02 <= cy <= ar[3] + .02), None)
        inside = [(d, area_of(d[0])) for d in dets]
        inside = [(d, ar) for d, ar in inside if ar is not None]
        if not inside:
            rows.append((t, None))
            continue
        top = max(d[0][3] - d[0][1] for d, _ar in inside)
        worst = None
        for (box, look), area in inside:
            if box[3] - box[1] < .6 * top:
                continue
            aw, ah = area[2] - area[0], area[3] - area[1]
            fw, fh = (box[2] - box[0]) / aw, (box[3] - box[1]) / ah
            gaps = []
            if fw <= 1.0 - 2.0 * EDGE_CLIP:
                # a turned face is judged on its leading side (the nose):
                # the back of its head may meet the edge by design, and the
                # profile detector's box runs well past it
                if look <= 0:
                    gaps.append(("left", (box[0] - area[0]) / aw))
                if look >= 0:
                    gaps.append(("right", (area[2] - box[2]) / aw))
            if fh <= 1.0 - 2.0 * EDGE_CLIP:
                gaps += [("top", (box[1] - area[1]) / ah),
                         ("bottom", (area[3] - box[3]) / ah)]
            for side, gap in gaps:
                if gap < EDGE_CLIP and (worst is None or gap < worst[1]):
                    worst = (side, gap)
        rows.append((t, worst))
    runs, cur = [], None
    for t, hit in rows:
        if hit and cur and cur["side"] == hit[0]:
            cur["t1"], cur["n"] = t, cur["n"] + 1
            cur["gap"] = min(cur["gap"], hit[1])
            continue
        if cur and cur["n"] >= CLIP_RUN:
            runs.append(cur)
        cur = ({"t0": t, "t1": t, "side": hit[0], "gap": hit[1], "n": 1}
               if hit else None)
    if cur and cur["n"] >= CLIP_RUN:
        runs.append(cur)
    return [(r["t0"], r["t1"], r["side"], max(0.0, r["gap"])) for r in runs]


def _ncc(a, b, mask=None):
    import numpy as np
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    if mask is not None:
        a, b = a[mask], b[mask]
    a = a - a.mean()
    b = b - b.mean()
    den = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum()) / den if den > 1e-6 else 0.0


def _gray_frames(src, w, h, fps, ss=None, t=None, extra_vf="", deadline=None):
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += ["-i", src]
    if t is not None:
        cmd += ["-t", f"{t:.3f}"]
    if "eq(n,0)" in extra_vf:
        cmd += ["-frames:v", "1"]
    vf = f"fps={fps:.3f},{extra_vf}format=gray" if fps else f"{extra_vf}format=gray"
    cmd += ["-an", "-map", "0:v:0", "-vf", vf, "-f", "rawvideo",
            "-pix_fmt", "gray", "-"]
    return [f.copy() for f in _stream(cmd, w, h, deadline or time.monotonic() + 30)]


def endcard_match(path, plan_, deadline):
    """The best correlation of the rendered outro with the card the
    renderer composes (the card fitted inside 98% of the frame on black),
    or None when it cannot be measured."""
    import numpy as np
    card = plan_.get("endcard") or {}
    src = card.get("path")
    if not src or not os.path.exists(src):
        return None
    W, H = plan_["W"], plan_["H"]
    w = DIFF_W
    h = _tiny_h(W, H, w)
    got = _gray_frames(path, w, h, 4.0, ss=plan_["program_s"] + .5,
                       t=max(.5, card["s"] - 1.0),
                       extra_vf=f"scale={w}:{h}:flags=area,", deadline=deadline)
    if not got:
        return 0.0
    cw, ch = max(2, int(w * .98)), max(2, int(h * .98))
    img = src.lower().endswith((".png", ".jpg", ".jpeg"))
    ref = _gray_frames(src, w, h, None if img else 4.0, t=None if img else 8.0,
                       extra_vf=(f"scale={cw}:{ch}:force_original_aspect_ratio="
                                 f"decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"),
                       deadline=deadline)
    if img:
        ref = ref[:1]
    if not ref:
        return None
    # the card fades in and out: judge the frames that carry it
    lit = [g for g in got if float(g.mean()) > 2.0] or got
    return max(_ncc(g, r) for g in lit for r in ref)


def robot_match(path, plan_, deadline):
    """The median correlation of the watermark corner with the robot over
    a few programme frames, or None when it cannot be measured."""
    import numpy as np
    wm = plan_.get("watermark") or {}
    robot = wm.get("robot")
    if not robot or not os.path.exists(robot):
        return None
    try:
        import cv2
    except Exception:
        return None
    rgba = cv2.imread(robot, cv2.IMREAD_UNCHANGED)
    if rgba is None or rgba.ndim != 3 or rgba.shape[2] != 4:
        return None
    w, h = int(wm["w"]), int(wm["h"])
    if h < ROBOT_MIN_PX:
        return None                # too few pixels to tell after encoding
    rgba = cv2.resize(rgba, (w, h), interpolation=cv2.INTER_AREA)
    alpha = rgba[:, :, 3] > 230
    if alpha.sum() < 30:
        return None
    luma = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_BGR2GRAY)
    # ROBOT_SAMPLES frames spread over the programme, one seek each: a
    # decode of the whole programme for six frames was minutes on a long
    # final, and a pipe that yields a frame per minute of video cannot be
    # stopped by the budget between frames.
    span = max(.5, plan_["program_s"] - 1.0)
    got = []
    for k in range(ROBOT_SAMPLES):
        if time.monotonic() > deadline:
            break
        t = .5 + span * (k + .5) / ROBOT_SAMPLES
        got += _gray_frames(
            path, w, h, None, ss=t,
            extra_vf=(f"crop={w}:{h}:{int(wm['x'])}:{int(wm['y'])},"
                      "select='eq(n,0)',"), deadline=deadline)[:1]
    if not got:
        return 0.0
    scores = sorted(_ncc(g, luma, alpha) for g in got)
    return scores[len(scores) // 2]


# ═════════════════════════════════════════════════════════════════════════
# The check
# ═════════════════════════════════════════════════════════════════════════

def _t(frame, fps):
    return frame / float(fps)


def check(path, plan_, budget_s=BUDGET_S):
    """Measure the rendered file at ``path`` against ``plan_`` (plan()).
    {"version", "findings": [str], "jumps", "clipped", "endcard",
    "watermark", "faces"} — or None when nothing could be measured."""
    if not path or not os.path.exists(path) or not plan_:
        return None
    deadline = time.monotonic() + float(budget_s)
    fps, W, H = plan_["fps"], plan_["W"], plan_["H"]
    prog = plan_["program_s"]
    res = {"version": QC_VERSION, "findings": [], "jumps": [], "clipped": [],
           "cut": [], "layout": [], "band": [],
           "endcard": None, "watermark": None, "faces": 0, "skipped": []}
    # Cheapest and most important first: the branding a final must carry,
    # then the faces, then the full-rate frame pass (shorts only — a long
    # programme's every frame is minutes of decode).
    if plan_.get("endcard"):
        try:
            res["endcard"] = endcard_match(path, plan_, deadline)
        except Exception as exc:
            res["skipped"].append(f"endcard: {str(exc)[:80]}")
    if plan_.get("watermark"):
        try:
            res["watermark"] = robot_match(path, plan_, deadline)
        except Exception as exc:
            res["skipped"].append(f"watermark: {str(exc)[:80]}")
    try:
        samples = face_samples(path, prog, W, H, deadline,
                               keyframes=prog > MAX_FULL_PASS_S)
        res["faces"] = sum(1 for _t_, d in samples if d)
        res["clipped"] = [list(c) for c in clipped_faces(samples, plan_)]
        res["cut"] = [list(c) for c in cut_faces(samples, plan_)]
    except Exception as exc:
        res["skipped"].append(f"faces: {str(exc)[:80]}")
    if prog <= MAX_FULL_PASS_S:
        try:
            lumas = []
            changes = frame_changes(path, prog, W, H, deadline, lumas=lumas)
            res["jumps"] = [list(j) for j in jumps(changes, plan_)]
            res["layout"] = [list(g) for g in layout_glitches(lumas, plan_)]
        except Exception as exc:
            res["skipped"].append(f"jumps: {str(exc)[:80]}")
        for band in plan_.get("bands") or []:
            try:
                res["band"] += [list(g) + [band.get("id")] for g in band_gaps(
                    band_ink(path, band, W, H, deadline, fps), fps)]
            except Exception as exc:
                res["skipped"].append(f"band: {str(exc)[:80]}")
    else:
        res["skipped"].append(f"jumps: a {prog:.0f}s programme is past the "
                              f"{MAX_FULL_PASS_S:.0f}s frame pass")
    res["findings"] = findings(res, plan_)
    return res


def findings(res, plan_):
    """Plain-language findings from a check result, worst first."""
    fps = plan_.get("fps") or 30.0
    out = []
    if plan_.get("endcard") and res.get("endcard") is not None and \
            res["endcard"] < ENDCARD_MIN_NCC:
        out.append(
            f"END CARD MISSING: the last {plan_['endcard']['s']:g}s do not show "
            "the Valmera end card (match "
            f"{res['endcard']:.2f}) — the card is automatic; re-render the "
            "export and report it if it persists")
    if plan_.get("watermark") and res.get("watermark") is not None and \
            res["watermark"] < ROBOT_MIN_NCC:
        out.append(
            "WATERMARK MISSING: this free-tier export should carry the "
            "Valmera watermark top-left and the frames do not show it (match "
            f"{res['watermark']:.2f}) — the mark is automatic; re-render the "
            "export and report it if it persists")
    for kind, fr, cut in res.get("jumps") or []:
        t = _t(fr, fps)
        if kind == "pop":
            where = (f", the frame next to the cut at {_t(cut, fps):.2f}s"
                     if cut is not None else "")
            out.append(
                f"ONE-FRAME POP at {t:.2f}s (frame {fr}{where}): the framing "
                "jumps for a single frame — a zoom or crop switch a frame off "
                "the cut. look_at the frames either side; move the zoom/crop "
                "edge onto the cut")
        else:
            near = (f" ({abs(fr - cut)} frame{'s' if abs(fr - cut) != 1 else ''} "
                    f"off the cut at {_t(cut, fps):.2f}s)" if cut is not None else "")
            out.append(
                f"JUMP OFF A CUT at {t:.2f}s (frame {fr}){near}: the whole "
                "picture changes on a frame that is not a cut of the edit — a "
                "framing pop, or a camera cut inside the source the edit does "
                "not know; look_at it")
    for kind, fr, a, b, cid in res.get("layout") or []:
        out.append(
            f"LAYOUT {'DIP' if kind == 'dip' else 'FLASH'} at {_t(fr, fps):.2f}s "
            f"(frame {fr}): the picture's mean luma "
            f"{'drops' if kind == 'dip' else 'jumps'} {a:.0f} -> {b:.0f} in one "
            f"frame where picture card {cid} opens or closes — a layout change "
            "must dissolve from/to the full-frame shot or cut to populated "
            "panels, never show the bare canvas; look_at it")
    for t0, t1, side, over, where in res.get("cut") or []:
        out.append(
            f"FACE CUT BY THE {where.upper()} EDGE {t0:.1f}-{t1:.1f}s: the "
            f"speaker's {'chin' if side == 'bottom' else 'hair' if side == 'top' else side + ' side'} "
            f"runs {100 * over:.0f}% past the {where}'s {side} edge — re-solve "
            "the card's source rect from the face (set_picture_card source="
            "'auto'), re-aim the crop, or cut away")
    for t0, t1, bid in res.get("band") or []:
        out.append(
            f"EMPTY HEADLINE BAND {t0:.2f}-{t1:.2f}s: the band the persistent "
            f"headline '{bid}' holds is blank for {t1 - t0:.2f}s — a dropped "
            "layer to the viewer; "
            "the headline holds whenever no graphic occupies the band (start "
            "each graphic on its first visible word)")
    cut_runs = res.get("cut") or []
    for t0, t1, side, gap in res.get("clipped") or []:
        if any(c[2] == side and c[0] <= t1 and t0 <= c[1] for c in cut_runs):
            continue                          # reported as a cut face
        out.append(
            f"FACE AT THE {side.upper()} EDGE {t0:.1f}-{t1:.1f}s: the speaker's "
            f"face comes within {100 * gap:.0f}% of the "
            f"{'card' if any(c[0] <= t0 < c[1] for c in plan_.get('cards') or []) else 'frame'}"
            f"'s {side} edge — re-aim (set_frame focus/follow, the card's "
            "source rect) or cut away")
    return out[:MAX_FINDINGS]


def summary_line(res):
    """One line for the editor's render result ('' when clean/absent)."""
    if not res:
        return ""
    bits = []
    if res.get("faces") is not None:
        bits.append(f"{res['faces']} face samples")
    if res.get("endcard") is not None:
        bits.append(f"end card {'present' if res['endcard'] >= ENDCARD_MIN_NCC else 'MISSING'}")
    if res.get("watermark") is not None:
        bits.append(f"watermark {'present' if res['watermark'] >= ROBOT_MIN_NCC else 'MISSING'}")
    if not res.get("findings"):
        return (" PICTURE CHECK: clean (" + ", ".join(bits) +
                "; no clipped or cut face, no single-frame jump off a cut, "
                "no dip at a layout change).") if bits else ""
    return ""
