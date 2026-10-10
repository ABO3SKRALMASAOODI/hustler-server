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

QC_VERSION = 1
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
BUDGET_S = 60.0
MAX_FULL_PASS_S = 180.0     # programmes longer than this skip the frame pass
                            # and sample faces on keyframes


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
    layers = list(edl.get("typography_scenes") or []) + \
        list(fx.get("picture_cards") or []) + list(edl.get("overlays") or []) + \
        list(fx.get("frame_shifts") or [])
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
    for item in layers:
        if not isinstance(item, dict):
            continue
        for key, lo, hi in (("start", 0, 3), ("end", -3, 0)):
            try:
                n = renderer.first_frame_at(float(item[key]), fps)
            except (KeyError, TypeError, ValueError):
                continue
            events.append((n + lo, n + hi))
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
            cards.append((float(c["start"]), float(c["end"]),
                          [float(v) for v in c["box"]]))
        except (KeyError, TypeError, ValueError):
            continue
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
            "end_frame": int(round(float(program_s) * fps)),
            "cut_frames": cut_frames, "events": events, "cards": cards,
            "watermark": wm, "endcard": endcard}


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


def frame_changes(path, program_s, W, H, deadline):
    """[(level, share)] per programme frame k >= 1: the mean |change| from
    frame k-1 (0-255) and the share of the tiny frame that changed."""
    import numpy as np
    w, h = DIFF_W, _tiny_h(W, H, DIFF_W)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-threads", "4",
           "-skip_loop_filter", "all", "-i", path,
           "-t", f"{program_s:.3f}", "-an", "-sn", "-dn", "-map", "0:v:0",
           "-vf", f"scale={w}:{h}:flags=area,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    out, prev = [], None
    for f in _stream(cmd, w, h, deadline):
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
    rows = []
    for t, dets in samples:
        if mark:
            dets = [d for d in dets or []
                    if not (d[0][0] <= mark[0] <= d[0][2]
                            and d[0][1] <= mark[1] <= d[0][3])]
        if not dets:
            rows.append((t, None))
            continue
        card = next((c for c in cards if c[0] <= t < c[1]), None)
        area = card[2] if card else [0.0, 0.0, 1.0, 1.0]
        inside = [d for d in dets
                  if area[0] - .02 <= (d[0][0] + d[0][2]) / 2 <= area[2] + .02
                  and area[1] - .02 <= (d[0][1] + d[0][3]) / 2 <= area[3] + .02]
        if not inside:
            rows.append((t, None))
            continue
        top = max(d[0][3] - d[0][1] for d in inside)
        worst = None
        for box, look in inside:
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
    span = max(.5, plan_["program_s"] - 1.0)
    fps = min(2.0, 6.0 / span)
    got = _gray_frames(path, w, h, fps, ss=.5, t=span,
                       extra_vf=f"crop={w}:{h}:{int(wm['x'])}:{int(wm['y'])},",
                       deadline=deadline)
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
    except Exception as exc:
        res["skipped"].append(f"faces: {str(exc)[:80]}")
    if prog <= MAX_FULL_PASS_S:
        try:
            changes = frame_changes(path, prog, W, H, deadline)
            res["jumps"] = [list(j) for j in jumps(changes, plan_)]
        except Exception as exc:
            res["skipped"].append(f"jumps: {str(exc)[:80]}")
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
    for t0, t1, side, gap in res.get("clipped") or []:
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
                "; no clipped face, no single-frame jump off a cut).") if bits else ""
    return ""
