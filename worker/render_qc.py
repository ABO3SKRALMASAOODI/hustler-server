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
    a full-frame crop (a close-up may lose the top of the hair there). And
    no face lies under a panel's softened corner (CardPanel.conceal, its
    feather included: the judged smear over Rogan's jaw).
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

QC_VERSION = 3              # v3: orphan shots, reframes off a cut, the hook frame
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
ORPHAN_FRAMES = 2           # a shot this short between two cuts is an orphan
REFRAME_REACH = 3           # a crop switch 1-3 frames off a cut is off the cut
HOOK_LOOK_S = 0.6           # the opening the feed shows first
HOOK_REF_S = 1.5            # frames read to learn this face's open eyes
HOOK_W = 720                # width the hook frames are read at
EYE_NEIGHBORS = 12          # strict: a closed lid seldom passes for an eye
EYE_CLOSED_RUN = 4          # frames (~0.13 s): a blink, not a detector miss
EYE_OPEN_SHARE = 0.3        # ...judged only where this share reads 2 eyes
HOOK_FACE_MIN = 0.12        # a face this wide (share of the frame) or more
HOOK_SOUND_MS = (5, 12)     # the first sound read (just after the edge fade:
                            # an opening on a clean onset is quiet here)
HOOK_SOUND_DB = -30.0       # louder than this, and well over the floor,
HOOK_SOUND_OVER = 15.0      # ...is a programme opening mid-sound
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
    trans_windows = []
    if trans:
        try:
            reach = int(math.ceil(float((trans or {}).get("duration_s") or .5)
                                  * fps)) + 1
        except (AttributeError, TypeError, ValueError):
            reach = int(fps // 2)
        trans_windows = [(n - reach, n + reach) for n in cut_frames]
        events += trans_windows
    reframes, true_cuts, has_shots = _reframes(edl, index, tl, fps, src_fps,
                                               origin)
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
    # Softened corners (a burned-in box a still speaker panel could not leave
    # out, CardPanel.conceal) as canvas zones, feather included: no face may
    # sit under one (judges, Oct 2026, round 7: a smear over Rogan's jaw).
    softened = []
    try:
        import picture_cards as _pc
        for c in fx.get("picture_cards") or []:
            a, b = float(c["start"]), float(c["end"])
            for pn in c.get("panels") or []:
                if pn.get("conceal") and not pn.get("follow"):
                    zones = _pc.conceal_canvas(pn["box"], pn["source"],
                                               pn["conceal"], W, H)
                    if zones:
                        softened.append((a, b, zones))
    except Exception:
        softened = []
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
            "layout": layout, "still": still, "bands": bands,
            "softened": softened,
            "trans_windows": trans_windows, "reframes": reframes,
            "true_cuts": true_cuts, "has_shots": has_shots,
            "hook": _hook_plan(edl, index, tl)}


def _reframes(edl, index, tl, fps, src_fps, origin):
    """(reframes, true cut frames, has_shots). reframes: [(frame, source
    second, kind)] — where the crop re-aims (an internal focus_track edge
    that changes the aim, a frame-follow span edge) on the frame the
    renderer switches it (composition_handoff / focus_handoff), leaving out
    those it lands on a keep join. True cuts: where the PICTURE cuts — keep
    joins that skip source time, insert edges, indexed camera cuts — never
    a crop switch itself."""
    import renderer
    import captions as caplib
    sfps = float(src_fps or ((index or {}).get("video") or {}).get("fps")
                 or fps or 30.0)
    o = 0.0 if origin is None else origin
    segs = getattr(tl, "segs", None) or []

    def frame_of_source(h):
        for s, e in segs:
            if s + 1e-3 < h < e - 1e-3:
                p = tl.src_to_out(h)
                return None if p is None else renderer.first_frame_at(p, fps)
        return None
    true = set()
    try:
        true.update(renderer.first_frame_at(c, fps)
                    for c in caplib.program_cuts(tl))
    except Exception:
        pass
    shots = []
    for shot in (index or {}).get("shots") or []:
        try:
            c = float(shot["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if c > 0.0:
            shots.append(c)
            f = frame_of_source(renderer.focus_handoff(c, sfps, o, fps))
            if f is not None:
                true.add(f)
    out = []
    frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
    track = [sp for sp in (frame or {}).get("focus_track") or []
             if isinstance(sp, dict)]
    if track:
        base = (frame.get("focus_x"), frame.get("focus_y"),
                frame.get("mode") or "crop")
        edges = set()
        for sp in track:
            for key in ("t0", "t1"):
                try:
                    edges.add(float(sp[key]))
                except (KeyError, TypeError, ValueError):
                    continue
        for edge in sorted(edges)[1:-1]:
            if renderer._aim_at(track, edge - 1e-3, base) == \
                    renderer._aim_at(track, edge + 1e-3, base):
                continue
            try:
                h = renderer.composition_handoff(edge, segs, index, sfps, o,
                                                 fps)
            except Exception:
                h = None
            f = frame_of_source(h) if h is not None else None
            if f is not None:
                out.append((f, round(edge, 3), "focus"))
    try:
        import follow
        spans = [sp for sp in follow.frame_spans(edl) if isinstance(sp, dict)]
    except Exception:
        spans = []
    edges = set()
    for sp in spans:
        for key in ("t0", "t1"):
            try:
                edges.add(round(float(sp[key]), 3))
            except (KeyError, TypeError, ValueError):
                continue
    for edge in sorted(edges):
        if any(abs(edge - x) <= 1e-3 for _f, x, _k in out):
            continue
        if not any(s + 2.0 / sfps < edge < e - 2.0 / sfps for s, e in segs):
            continue
        f = frame_of_source(renderer.focus_handoff(edge, sfps, o, fps))
        if f is not None:
            out.append((f, edge, "follow"))
    return sorted(out), sorted(true), bool(shots)


def _hook_plan(edl, index, tl):
    """What the hook check needs: the first kept SOURCE second and the words
    around it, when the programme opens on the main footage; else None."""
    keep = edl.get("keep") or []
    try:
        if not keep or any(float(a) <= 1e-3 for a, _d in tl.insert_positions()):
            return None
        src0 = float(keep[0][0])
    except Exception:
        return None
    words = []
    for w in (index or {}).get("words") or []:
        try:
            t0, t1 = float(w["t0"]), float(w["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        if src0 - 1.0 <= t0 <= src0 + 3.0:
            words.append({"w": str(w.get("w") or ""), "t0": t0, "t1": t1})
    music = bool(edl.get("music") or [
        s for s in edl.get("sfx") or []
        if isinstance(s, dict) and s.get("at") is not None
        and float(s["at"]) < 0.5])
    return {"src0": src0, "words": words, "music": music}


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


def _global_test(changes):
    """is_global(k): is changes[k] (the change INTO frame k+1) a global
    jump — most of the tiny frame moved, by a lot, far past the local
    median change?"""
    import numpy as np
    n = len(changes)
    lv = np.array([c[0] for c in changes]) if n else np.zeros(0)

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
    return is_global


def orphans(changes, plan_):
    """Shots that last 1-ORPHAN_FRAMES frames between two whole-picture
    changes — a flash of another framing or shot, whatever the plan calls
    them (judges, round 7: the crop switched one frame before the source's
    camera cut, and both changes sat on 'cuts' of the edit, so the pop
    check passed it). [(first frame, frames, reframe source second or
    None)]. The programme's first frame and the end card's cut count as
    changes; deliberate whole-picture moments (fades, flashes, shakes,
    junction transitions, full-frame layers' edges) are left alone."""
    n = len(changes)
    if not n:
        return []
    is_global = _global_test(changes)
    marks = [0] + [k + 1 for k in range(n) if is_global(k)]
    last = plan_.get("end_frame")
    if last is not None and int(last) > marks[-1]:
        marks.append(int(last))
    quiet = list(plan_.get("still") or []) + \
        list(plan_.get("trans_windows") or [])
    reframes = plan_.get("reframes") or []
    out = []
    for a, b in zip(marks, marks[1:]):
        if not 1 <= b - a <= ORPHAN_FRAMES:
            continue
        if any(lo <= b + 1 and a - 1 <= hi for lo, hi in quiet):
            continue
        why = next((src for fr, src, _k in reframes if a - 1 <= fr <= b + 1),
                   None)
        out.append((a, b - a, why))
    return out


def reframes_off_cut(plan_):
    """Crop switches the edit places off the picture's cuts (plan only):
    [(kind, frame, cut frame or None, source second, what)] — 'off' when a
    switch lands 1-REFRAME_REACH frames from a cut (a sliver of one
    framing on the other shot), 'mid' when a focus re-aim sits inside a
    shot of an index that lists its shots (a visible jump with no cut)."""
    cuts = plan_.get("true_cuts") or []
    out = []
    for fr, src, kind in plan_.get("reframes") or []:
        near = min(cuts, key=lambda c: abs(c - fr)) if cuts else None
        d = abs(near - fr) if near is not None else None
        if d is not None and 1 <= d <= REFRAME_REACH:
            out.append(("off", fr, near, src, kind))
        elif kind == "focus" and plan_.get("has_shots") and \
                (d is None or d > REFRAME_REACH):
            out.append(("mid", fr, None, src, kind))
    return out


def jumps(changes, plan_):
    """Global jumps off the edit's cuts: [(kind, frame, near_cut_frame)]
    with kind 'pop' (a frame that lasted one frame between two jumps) or
    'jump' (one jump not on a cut or a deliberate event)."""
    n = len(changes)
    if not n:
        return []
    cuts = set(plan_.get("cut_frames") or [])
    last = plan_.get("end_frame")
    if last is not None:
        cuts.add(int(last))
    events = plan_.get("events") or []

    def frame(k):                      # changes[k] is the jump INTO frame k+1
        return k + 1

    is_global = _global_test(changes)

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


def eye_counts(path, plan_, deadline, cv2=None):
    """[(eyes, face)] for the programme's first HOOK_REF_S: per frame, the
    eyes a strict Haar eye cascade finds inside the largest face
    (None when there is none, or it is too small to judge). A closed lid
    seldom passes the strict cascade; an open frontal eye nearly always
    does — so a run of frames without two eyes, on a face whose other
    frames show two, is closed eyes (a blink), not a detector miss."""
    import follow
    import subject
    cv2 = cv2 or subject._cv2()
    if cv2 is None:
        return []
    eye = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
    if eye.empty():
        return []
    W, H, fps = plan_["W"], plan_["H"], float(plan_.get("fps") or 30.0)
    w = HOOK_W
    h = _tiny_h(W, H, w)
    n = int(round(HOOK_REF_S * fps))
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", path,
           "-frames:v", str(n), "-an", "-sn", "-dn", "-map", "0:v:0",
           "-vf", f"scale={w}:{h},format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    out = []
    for g in _stream(cmd, w, h, deadline):
        faces = follow.detect(g, cv2=cv2)
        if not faces:
            out.append((None, None))
            continue
        box = max(faces, key=lambda f: f[0][2] - f[0][0])[0]
        fw = box[2] - box[0]
        if fw < HOOK_FACE_MIN:
            out.append((None, box))
            continue
        x0, y0 = int(box[0] * w), int(box[1] * h)
        x1, y1 = int(box[2] * w), int(box[3] * h)
        roi = cv2.equalizeHist(
            g[y0 + int(.15 * (y1 - y0)):y0 + int(.55 * (y1 - y0)), x0:x1])
        px = max(1, x1 - x0)
        found = eye.detectMultiScale(roi, 1.05, EYE_NEIGHBORS,
                                     minSize=(max(10, px // 9),) * 2,
                                     maxSize=(max(11, px // 3),) * 2)
        out.append((len(found), box))
    return out


def closed_eyes(counts, fps):
    """(first closed frame, frames closed, first open frame or None) when
    the speaker's eyes are closed in the opening HOOK_LOOK_S — frame 0
    itself, or a blink of EYE_CLOSED_RUN+ frames starting in it — on a face
    whose frames in HOOK_REF_S show two eyes at least EYE_OPEN_SHARE of the
    time; else None (open, or not measurable)."""
    judged = [(k, e) for k, (e, _b) in enumerate(counts) if e is not None]
    if len(judged) < max(6, len(counts) // 3):
        return None
    if sum(1 for _k, e in judged if e >= 2) < EYE_OPEN_SHARE * len(judged):
        return None
    look = int(round(HOOK_LOOK_S * fps))
    closed = [k for k, e in judged if e <= 1]
    runs, start, prev = [], None, None
    for k in closed:
        if start is not None and k == prev + 1:
            prev = k
            continue
        if start is not None:
            runs.append((start, prev + 1))
        start = prev = k
    if start is not None:
        runs.append((start, prev + 1))
    for a, b in runs:
        if a >= look:
            break
        if (a == 0 and b - a >= 2) or b - a >= EYE_CLOSED_RUN:
            opened = None
            for k in range(b, len(counts) - 2):
                if all(counts[j][0] is not None and counts[j][0] >= 2
                       for j in (k, k + 1, k + 2)):
                    opened = k
                    break
            return (a, b - a, opened)
    return None


def opening_sound(path, deadline):
    """(dB of the programme's first sound, the floor of its first 0.5 s),
    read on HOOK_SOUND_MS after the de-click edge fade; None unread."""
    import numpy as np
    if time.monotonic() > deadline:
        return None
    sr = 16000
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-t", "0.5",
             "-map", "0:a:0", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
            capture_output=True, timeout=30)
    except Exception:
        return None
    x = np.frombuffer(proc.stdout or b"", np.float32)
    a, b = (int(sr * ms / 1000) for ms in HOOK_SOUND_MS)
    if len(x) < sr // 4:
        return None

    def db(seg):
        r = float(np.sqrt(np.mean(np.square(seg, dtype=np.float64))))
        return 20.0 * math.log10(r + 1e-9)
    first = db(x[a:b])
    hop = sr // 50
    floor = min(db(x[i:i + hop]) for i in range(0, len(x) - hop + 1, hop))
    return round(first, 1), round(floor, 1)


def hook_advice(plan_, closed, fps):
    """The clean start an eyes-closed opening is offered (never applied):
    the first open-eye frame as a source second, and the first word onset
    from there."""
    hook = plan_.get("hook") or {}
    if not hook or closed is None or closed[2] is None:
        return None
    src0 = float(hook["src0"])
    t_open = src0 + closed[2] / float(fps)
    words = hook.get("words") or []
    nxt = next((w for w in sorted(words, key=lambda w: w["t0"])
                if w["t0"] >= t_open - 0.02), None)
    dropped = [w["w"] for w in words if src0 - 0.02 <= w["t0"] < t_open - 0.02]
    start = t_open if nxt is None else max(t_open, nxt["t0"] - 0.03)
    return {"start": round(start, 2), "open": round(t_open, 2),
            "word": nxt["w"] if nxt else None,
            "word_t0": round(nxt["t0"], 2) if nxt else None,
            "drops": dropped}


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


# A face whose box lies this much (a share of its area) under a panel's
# softened corner, for CLIP_RUN samples running, is a face under a smear.
SOFT_HIT = .10


def softened_faces(samples, plan_):
    """[(t0, t1, share)] runs of samples whose main face lies under a stack
    panel's softened corner (plan 'softened': canvas zones, feather in) by
    more than SOFT_HIT of the face's area — the softening of a burned-in box
    smeared over the speaker. ``share`` the most seen."""
    zones = plan_.get("softened") or []
    if not zones:
        return []
    others = plan_.get("others") or []         # inserts play full-frame
    rows = []
    for t, dets in samples:
        live = [z for a, b, zs in zones if a <= t < b for z in zs] \
            if not any(a <= t < b for a, b in others) else []
        hit = None
        if live and dets:
            top = max(d[0][3] - d[0][1] for d in dets)
            for box, _look in dets:
                if box[3] - box[1] < .6 * top:
                    continue
                area = max(1e-9, (box[2] - box[0]) * (box[3] - box[1]))
                share = sum(max(0.0, min(box[2], z[2]) - max(box[0], z[0]))
                            * max(0.0, min(box[3], z[3]) - max(box[1], z[1]))
                            for z in live) / area
                if share > SOFT_HIT and (hit is None or share > hit):
                    hit = share
        rows.append((t, hit))
    runs, cur = [], None
    for t, hit in rows:
        if hit is not None:
            cur = [cur[0], t, cur[2] + 1, max(cur[3], hit)] if cur else [t, t, 1, hit]
            continue
        if cur and cur[2] >= CLIP_RUN:
            runs.append((cur[0], cur[1], round(cur[3], 3)))
        cur = None
    if cur and cur[2] >= CLIP_RUN:
        runs.append((cur[0], cur[1], round(cur[3], 3)))
    return runs


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
           "orphans": [],
           "cut": [], "softened": [], "layout": [], "band": [],
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
        res["softened"] = [list(c) for c in softened_faces(samples, plan_)]
    except Exception as exc:
        res["skipped"].append(f"faces: {str(exc)[:80]}")
    res["reframes"] = []
    try:
        res["reframes"] = [list(r) for r in reframes_off_cut(plan_)]
    except Exception as exc:
        res["skipped"].append(f"reframes: {str(exc)[:80]}")
    if plan_.get("hook"):
        try:
            closed = closed_eyes(eye_counts(path, plan_, deadline), fps)
            if closed:
                res["hook_eyes"] = list(closed)
                res["hook_advice"] = hook_advice(plan_, closed, fps)
        except Exception as exc:
            res["skipped"].append(f"hook eyes: {str(exc)[:80]}")
        if not plan_["hook"].get("music"):
            try:
                snd = opening_sound(path, deadline)
                if snd and snd[0] > HOOK_SOUND_DB and \
                        snd[0] > snd[1] + HOOK_SOUND_OVER:
                    res["hook_sound"] = list(snd)
            except Exception as exc:
                res["skipped"].append(f"hook sound: {str(exc)[:80]}")
    if prog <= MAX_FULL_PASS_S:
        try:
            lumas = []
            changes = frame_changes(path, prog, W, H, deadline, lumas=lumas)
            res["jumps"] = [list(j) for j in jumps(changes, plan_)]
            res["orphans"] = [list(o) for o in orphans(changes, plan_)]
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
    for fr, frames, src in res.get("orphans") or []:
        if any(j[1] in range(fr - 1, fr + frames + 1)
               for j in res.get("jumps") or []):
            continue                            # reported as a pop/jump
        why = (f" — the crop switch at source {src:g}s is not on the "
               "source's camera cut" if src is not None else "")
        out.append(
            f"ORPHAN FRAME{'S' if frames > 1 else ''} at {_t(fr, fps):.2f}s "
            f"(frame {fr}{f'-{fr + frames - 1}' if frames > 1 else ''}): a "
            f"shot that lasts {frames} frame{'s' if frames > 1 else ''} "
            f"between two cuts{why}; look_at the frames either side and put "
            "the switch (focus_track/follow edge, card window, zoom, keep "
            "edge) on the cut")
    for kind, fr, cut, src, what in res.get("reframes") or []:
        if kind == "off":
            out.append(
                f"REFRAME OFF THE CUT at {_t(fr, fps):.2f}s: the {what} "
                f"switch at source {src:g}s lands {abs(fr - cut)} frame"
                f"{'s' if abs(fr - cut) != 1 else ''} from the picture's cut "
                f"at {_t(cut, fps):.2f}s — an orphan framing; move the edge "
                "onto the cut")
        else:
            out.append(
                f"REFRAME MID-SHOT at {_t(fr, fps):.2f}s: the crop re-aims "
                f"(focus_track edge at source {src:g}s) with no cut there — "
                "a visible jump; put the edge on a camera cut or a keep "
                "join, or remove it")
    if res.get("hook_eyes"):
        a, n, opened = res["hook_eyes"]
        adv = res.get("hook_advice") or {}
        offer = ""
        if adv:
            offer = (f" The nearest clean start is source {adv['start']:.2f}s "
                     f"(eyes open from {adv['open']:.2f}s"
                     + (f", on the onset of '{adv['word']}'" if adv.get("word")
                        else "")
                     + (f"; drops '{' '.join(adv['drops'])}'" if adv.get("drops")
                        else "")
                     + ").")
        if adv.get("drops"):
            # a later start that loses the hook's first words is a worse
            # hook than a blink: say what it costs
            offer += (" That start cuts the hook's opening words — keep the "
                      "current start unless the line still reads without "
                      "them" + (" (frame 0 is open: a blink after it reads "
                                "natural)." if a > 0 else "."))
        out.append(
            f"HOOK OPENS ON CLOSED EYES {_t(a, fps):.2f}-{_t(a + n, fps):.2f}s: "
            f"the speaker's eyes are closed for {n} frames of the opening the "
            "feed shows first." + offer + " The first cut is the editor's: "
            "nothing was moved — keep it if the moment reads natural")
    if res.get("hook_sound"):
        first, floor = res["hook_sound"]
        out.append(
            f"HOOK OPENS MID-SOUND: the programme's first sound plays at "
            f"{first:.0f} dB ({first - floor:.0f} dB over its quiet) — the "
            "first keep starts inside a word or a breath. Start it in the "
            "pause before the first word (keep tools place a new start "
            "there; get_words for the onset)")
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
    for t0, t1, share in res.get("softened") or []:
        out.append(
            f"FACE UNDER A SOFTENED CORNER {t0:.1f}-{t1:.1f}s: {100 * share:.0f}% "
            "of the speaker's face lies under the panel's softened corner (the "
            "burned-in box its framing could not leave out) — a smear on the "
            "face; set_picture_card again (an 'auto' speaker panel trims the "
            "softening off the head) or show the speaker another way")
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
                "no orphan frame, no dip at a layout change).") if bits else ""
    return ""
