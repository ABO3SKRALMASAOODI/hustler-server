"""Cutting the moving subject out of a steady shot, so words can sit BEHIND it.

The move users ask for by name: a big title on the wall/street/floor and the
person walks IN FRONT of the letters. Every piece of it already existed except
the one that matters — something that knows which pixels are the person. The
renderer draws text on top of the picture; there is no "behind" without a matte.

WHAT THIS DOES, AND WHY THIS WAY

  A steady shot is two things added together: a background that does not change
  and a subject that does. So photograph the background from the shot itself —
  the per-pixel MEDIAN over the window, which is what the pixel looked like in
  the majority of frames, i.e. with the subject somewhere else — and every
  frame's distance from that plate is the subject. This is the same idea
  inpaint._build_plate uses to reconstruct what was behind a caption, pointed
  the other way: inpaint wants the plate, this wants the difference.

  The alternative is a segmentation model. It would handle a moving camera,
  which this cannot, and it would cost a model file in the image, a CPU forward
  pass per frame on the box that also runs the agent turn, and a dependency
  whose output nobody can check. The difference matte is arithmetic: it is fast,
  it is deterministic, and — the part that decides it — it MEASURES its own
  confidence. A moving camera makes almost the whole frame differ from the
  plate, so the same number that finds the subject also tells us there is no
  subject to find, and the tool refuses instead of hiding the text behind a
  full-frame smear.

WHAT COMES OUT is a GRAYSCALE video, not the cut-out subject in colour. The
renderer alpha-merges it onto a copy of its own picture, so the subject's pixels
are the render's own — full resolution, already graded. That is what lets this
be measured on the 540p proxy (fast, and the only thing on the small box) and
still composite cleanly into a 4K export: an upscaled MASK softens an edge by a
pixel, where an upscaled subject would be a blurry patch in a sharp frame.

Everything here is bounded by design: one window of a few seconds, decoded once.
"""

import os
import subprocess

import numpy as np

import config
import media

# Bumped when anything below would produce a DIFFERENT mask from the same
# footage. It rides the mask's cache fingerprint, so a change here re-measures
# instead of serving a mask built by the old arithmetic — the same reason the
# erase path fingerprints its own derivation.
VERSION = 2

# Sampling for the background plate. 24 samples spread over the window is
# enough for a median to see past a subject that lingers, and few enough to hold
# in memory at proxy resolution (24 x 960x540x3 is ~37 MB).
PLATE_SAMPLES = 24
# A pixel counts as subject when it differs from the plate by more than this,
# in 0-255 luma-ish distance. 18 is above codec noise and camera grain at
# proxy bitrates and below any real change of content.
#
# ...on footage whose exposure holds still. VERSION 2 exists because a real
# title-behind-a-walk shipped ~30% right on a dark handheld iPhone shot
# (project 246, 2026-07-30): the phone's auto-exposure breathed as the subject
# crossed the lamp, which moved EVERY pixel a couple of dozen luma values and
# lit the mask up in a band across the middle of the frame — words vanished
# behind nobody — while the dark-jacket-on-dark-room subject himself sat UNDER
# the threshold and got overprinted. One fixed global number cannot serve both,
# so v2 measures per frame and per pixel:
#   * a per-frame, per-channel BIAS (the median of frame-minus-plate) is
#     subtracted before thresholding — auto-exposure and white-balance drift
#     move the whole frame together, and the median is immune to the subject
#     (who is bounded by MAX_COVERAGE);
#   * the threshold is lifted per PIXEL by that pixel's own temporal noise,
#     measured from the same samples the plate came from — a flickering TV,
#     shimmering foliage or a noisy shadow buys itself headroom instead of
#     buying a hole in the title;
#   * what survives is cleaned structurally (dropping specks no person could
#     be, filling holes a plain torso leaves) and steadied by a 3-frame
#     majority vote, so one frame's flicker cannot strobe the composite.
DIFF_THRESHOLD = 18.0
# Per-pixel threshold lift: threshold = max(DIFF_THRESHOLD, NOISE_K x that
# pixel's mean absolute deviation across the plate samples), capped at
# THRESHOLD_CEIL so a wildly noisy region can still yield a subject rather
# than going permanently blind.
NOISE_K = 4.0
THRESHOLD_CEIL = 64.0
# A connected blob smaller than this fraction of the frame is speckle, not a
# person (a subject worth hiding words behind is bounded below by
# MIN_COVERAGE, which is 5x this).
MIN_BLOB_FRAC = 0.0008
# Bounds on the answer, and both are refusals rather than degradations.
#   above MAX: the frame is changing everywhere — a moving camera, a whip pan,
#              a cut inside the window, a light being switched on. There is no
#              "subject" in that, and the matte would hide the whole title.
#   below MIN: nothing moves. The text would be "behind" nothing, and the user
#              would see an ordinary title and conclude the feature is broken.
MAX_COVERAGE = 0.55
MIN_COVERAGE = 0.004
# How long a window this is allowed to chew, on the box that is also running the
# agent turn. A title behind someone is a 2-6 second beat; 15 is generous.
MAX_WINDOW_S = 15.0


def _decode(path, start, dur, w, h, extra_vf=None, fps=None):
    """Raw BGR frames of ONE window, at w x h. `-ss` before `-i` so the decoder
    seeks instead of reading and throwing away everything before the window."""
    vf = []
    if extra_vf:
        vf.append(extra_vf)
    vf.append(f"scale={w}:{h}")
    if fps:
        vf.append(f"fps={fps:.5f}")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}",
           "-t", f"{max(0.05, dur):.3f}", "-i", path,
           "-vf", ",".join(vf), "-pix_fmt", "bgr24", "-f", "rawvideo",
           "pipe:1"]
    frame_bytes = w * h * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            bufsize=min(frame_bytes * 4, 8 << 20))
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()


def _plate(path, start, dur, w, h, extra_vf=None):
    """The background, photographed from the window itself.

    The MEDIAN and not the mean: a mean is dragged toward whatever the subject
    was wearing everywhere it went, which puts a ghost of the person into the
    background and takes a bite out of their own matte. A median of 24 samples
    is the pixel's majority value, which is the background unless the subject
    covered that pixel in most of the window — and where it did, that pixel is
    lost to the matte, honestly and locally, rather than smearing.
    """
    step = dur / float(PLATE_SAMPLES)
    stack = []
    for i in range(PLATE_SAMPLES):
        t = start + step * (i + 0.5)
        for f in _decode(path, t, 0.06, w, h, extra_vf):
            # uint8, NOT float32 — see below. np.median partitions in the
            # input dtype and returns float64 for the (h, w, 3) result, which
            # is one frame's worth, not the stack's.
            stack.append(f)
            break
    if len(stack) < 6:
        return None, None
    # THE COMMENT ON PLATE_SAMPLES WAS RIGHT; THE CODE WAS NOT (round 61).
    #
    # "24 x 960x540x3 is ~37 MB" is the uint8 figure. Every sample was being
    # widened to float32 on the way in — 4x — and then np.stack COPIED the lot
    # and np.median partitioned another copy, so a 6-second title cost roughly
    # 450 MB of peak RSS. This runs inside the agent turn, which runs on the
    # dispatcher, and on 2026-07-30 it killed that process one second after
    # add_text_behind was called: the user asked for a title behind themselves
    # walking and got "I lost my connection while working on that request".
    #
    # Kept in uint8 the whole way, the same three allocations come to ~110 MB
    # and the arithmetic is identical — median is order statistics, so the
    # dtype it partitions in cannot change which value wins.
    med = np.median(np.stack(stack), axis=0)
    # The same samples the plate came from also say how much each pixel moves
    # WITHOUT a subject in front of it — codec noise, a flickering screen,
    # leaves in a window. Mean absolute deviation from the median, max across
    # channels, accumulated one float32 frame at a time (~6 MB per step, never
    # the stack widened at once).
    #
    # Each sample's GLOBAL bias is removed first, exactly as the mask pass
    # removes it per frame. Without this, auto-exposure breathing counts as
    # per-pixel noise, the threshold map rides to its ceiling everywhere, and
    # a dark subject in a dark room drops below it — measured on a synthetic
    # reproduction: coverage fell to 12% of the subject's true footprint.
    # Drift is the BIAS'S job; this map is for what flickers locally.
    acc = np.zeros((h, w, 3), np.float32)
    for f in stack:
        d = f.astype(np.float32) - med
        bias = np.median(d[::4, ::4, :].reshape(-1, 3), axis=0)
        acc += np.abs(d - bias.astype(np.float32))
    noise = (acc / float(len(stack))).max(axis=2)
    return med, noise


def _mask_frames(frames, plate, noise, cv2):
    """Per-frame subject mask, 0-255 BINARY, from the distance to the plate.

    Feathering happens after the temporal vote in the caller — blurring here
    would turn the vote's crisp majority into mush.
    """
    thr = None
    if noise is not None:
        thr = np.clip(np.maximum(DIFF_THRESHOLD, NOISE_K * noise),
                      DIFF_THRESHOLD, THRESHOLD_CEIL)
        if cv2 is not None:
            # Smoothed so the threshold map has no 1-pixel cliffs of its own.
            thr = cv2.GaussianBlur(thr, (0, 0), 3)
    for f in frames:
        diff = f.astype(np.float32) - plate
        # Auto-exposure/white-balance drift moves the WHOLE frame together;
        # the per-channel median of the difference is that drift (the subject
        # cannot dominate a median — it is bounded by MAX_COVERAGE), and
        # subtracting it is what lets a phone breathe its exposure without the
        # mask claiming the whole room moved.
        bias = np.median(diff[::4, ::4, :].reshape(-1, 3), axis=0)
        # Max across channels, not the mean: a red jumper against a grey wall
        # differs hugely in one channel and barely in the others, and averaging
        # that dilutes a real difference into noise.
        d = np.max(np.abs(diff - bias.astype(np.float32)), axis=2)
        m = ((d > thr) if thr is not None
             else (d > DIFF_THRESHOLD)).astype(np.uint8) * 255
        if cv2 is not None:
            # Close first, then open: closing fills the holes a plain-coloured
            # torso leaves where it happens to match the wall behind it (which
            # is where a title lives), and opening then drops the speckle that
            # closing would have grown. The other order eats the person.
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
            k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k2)
            # Structure, not just texture: drop blobs no person could be, then
            # fill the holes a person always has (a matte with a window through
            # the torso prints the title THROUGH the subject, which is the
            # single most visible way this effect fails).
            nlab, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
            if nlab > 1:
                h_, w_ = m.shape
                min_px = max(64, int(MIN_BLOB_FRAC * w_ * h_))
                keep = np.zeros(nlab, bool)
                keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_px
                m = np.where(keep[lab], 255, 0).astype(np.uint8)
            m = _fill_holes(m, cv2)
        yield m


def _fill_holes(m, cv2):
    """Fill regions of background fully enclosed by subject. Flood-fills the
    background from a border pixel that IS background; a mask touching every
    border pixel has no reachable outside and is returned unchanged."""
    h, w = m.shape
    border = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    border += [(x, 0) for x in range(0, w, max(1, w // 16))]
    border += [(x, h - 1) for x in range(0, w, max(1, w // 16))]
    border += [(0, y) for y in range(0, h, max(1, h // 16))]
    border += [(w - 1, y) for y in range(0, h, max(1, h // 16))]
    seed = next(((x, y) for x, y in border if m[y, x] == 0), None)
    if seed is None:
        return m
    ff = m.copy()
    scratch = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(ff, scratch, seed, 255)
    # ff is now white everywhere EXCEPT enclosed background — the holes.
    return cv2.bitwise_or(m, cv2.bitwise_not(ff))


def _vote3(masks):
    """3-frame majority vote, frame-aligned: output i is the majority of
    frames i-1, i, i+1 (edges vote with themselves doubled). One frame's
    flicker — a threshold grazed by noise for a single frame — cannot strobe
    the composite, while anything real survives two of three frames."""
    def vote(a, b, c):
        s = (a > 127).astype(np.uint8) + (b > 127) + (c > 127)
        return np.where(s >= 2, 255, 0).astype(np.uint8)

    p2 = p1 = None
    for m in masks:
        if p1 is None:
            p1 = m
            continue
        yield vote(p1 if p2 is None else p2, p1, m)
        p2, p1 = p1, m
    if p1 is not None:
        yield p1 if p2 is None else vote(p2, p1, p1)


def measure_and_build(src, out_path, start, dur, *, box=None, fps=None,
                      extra_vf=None, width=None, height=None, feather=3):
    """Build the subject mask for [start, start+dur] of `src`.

    src should be the PROXY — see the module docstring: the mask is resolution
    independent, and this runs on the box that is also running the agent turn.

    box: (x, y, w, h) fractions of the frame that the text will occupy, used
    only to report how much of the words the subject actually crosses. A title
    the subject never walks in front of renders as an ordinary title, and the
    user would be told "it's behind you" about something they cannot see.

    Returns a dict of measurements. `ok` False means REFUSE and `why` says what
    to tell the user; nothing is written in that case.
    """
    try:
        import cv2
    except Exception:
        cv2 = None
    info = media.probe(src)
    sw, sh = int(info["width"]), int(info["height"])
    src_fps = max(1.0, min(float(info["fps"]) or 30.0, 120.0))
    out_fps = float(fps or src_fps)
    w = int(width or sw)
    h = int(height or sh)
    w, h = max(2, w - w % 2), max(2, h - h % 2)
    if dur > MAX_WINDOW_S:
        return {"ok": False,
                "why": (f"the window is {dur:.1f}s and I measure the subject "
                        f"frame by frame — over {MAX_WINDOW_S:.0f}s that does "
                        "not finish inside one edit turn. Use a shorter "
                        "window for the words behind you")}
    plate, noise = _plate(src, start, dur, w, h, extra_vf)
    if plate is None:
        return {"ok": False,
                "why": ("I could not read enough frames from that moment to "
                        "photograph the background behind you")}

    frames = _decode(src, start, dur, w, h, extra_vf, fps=out_fps)
    total = w * h
    n = 0
    cov_sum = 0.0
    cov_max = 0.0
    hits = 0
    box_px = None
    if box:
        bx = int(max(0.0, min(1.0, box[0])) * w)
        by = int(max(0.0, min(1.0, box[1])) * h)
        bw = max(1, int(max(0.01, min(1.0, box[2])) * w))
        bh = max(1, int(max(0.01, min(1.0, box[3])) * h))
        box_px = (bx, by, min(w, bx + bw), min(h, by + bh))
    box_cov = 0.0

    frame_bytes = w * h
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
         "-r", f"{out_fps:.5f}", "-i", "pipe:0",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-threads", str(config.CLEAN_X264_THREADS),
         "-pix_fmt", "yuv420p", "-an",
         "-movflags", "+faststart", out_path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)
    try:
        for m in _vote3(_mask_frames(frames, plate, noise, cv2)):
            if cv2 is not None and feather > 0:
                # A hard 1-pixel edge is what makes a composite look pasted.
                # After the vote, so the majority is taken on crisp binaries.
                m = cv2.GaussianBlur(m, (feather * 2 + 1, feather * 2 + 1), 0)
            on = m > 127
            c = float(on.sum()) / float(total)
            cov_sum += c
            cov_max = max(cov_max, c)
            if c >= MIN_COVERAGE:
                hits += 1
            if box_px:
                x0, y0, x1, y1 = box_px
                sub = on[y0:y1, x0:x1]
                if sub.size:
                    box_cov = max(box_cov, float(sub.sum()) / float(sub.size))
            enc.stdin.write(m.tobytes() if m.flags["C_CONTIGUOUS"]
                            else np.ascontiguousarray(m).tobytes())
            n += 1
    finally:
        try:
            enc.stdin.close()
        except Exception:
            pass
        err = enc.stderr.read().decode("utf-8", "replace")[-500:]
        rc = enc.wait(timeout=config.FFMPEG_TIMEOUT_S)

    if n == 0 or rc != 0 or not os.path.exists(out_path):
        return {"ok": False,
                "why": (f"the frames at that moment could not be processed "
                        f"({err or 'no output'})")}
    mean_cov = cov_sum / float(n)
    res = {"ok": True, "frames": n, "width": w, "height": h,
           "fps": round(out_fps, 3), "coverage": round(mean_cov, 4),
           "coverage_max": round(cov_max, 4),
           "moving_frames": hits,
           "text_covered": round(box_cov, 3) if box_px else None,
           "no_cv2": cv2 is None}
    # THE MEASUREMENT IS THE FEATURE. Both of these are real footage, not edge
    # cases: people hand-hold their cameras, and people ask for this on a static
    # tripod shot of an empty room.
    if mean_cov > MAX_COVERAGE:
        res.update(ok=False, why=(
            f"{mean_cov * 100:.0f}% of the frame changes through that window, "
            "so there is no still background to put words on — the camera is "
            "moving (or the shot cuts inside the window). This effect needs a "
            "shot where the camera holds still and the subject moves. Offer a "
            "normal title instead, and say why"))
    elif mean_cov < MIN_COVERAGE:
        res.update(ok=False, why=(
            "nothing moves in front of the camera through that window "
            f"({mean_cov * 100:.2f}% of the frame changes), so there is "
            "nothing for the words to go behind. Point it at a moment where "
            "the subject is actually moving across the frame"))
    if not res["ok"]:
        try:
            os.remove(out_path)
        except OSError:
            pass
    return res
