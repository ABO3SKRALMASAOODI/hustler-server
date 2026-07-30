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
VERSION = 1

# Sampling for the background plate. 24 samples spread over the window is
# enough for a median to see past a subject that lingers, and few enough to hold
# in memory at proxy resolution (24 x 960x540x3 is ~37 MB).
PLATE_SAMPLES = 24
# A pixel counts as subject when it differs from the plate by more than this,
# in 0-255 luma-ish distance. 18 is above codec noise and camera grain at
# proxy bitrates and below any real change of content.
DIFF_THRESHOLD = 18.0
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
            stack.append(f.astype(np.float32))
            break
    if len(stack) < 6:
        return None
    return np.median(np.stack(stack), axis=0)


def _mask_frames(frames, plate, cv2, feather):
    """Per-frame subject mask, 0-255, from the distance to the plate."""
    for f in frames:
        # Max across channels, not the mean: a red jumper against a grey wall
        # differs hugely in one channel and barely in the others, and averaging
        # that dilutes a real difference into noise.
        d = np.max(np.abs(f.astype(np.float32) - plate), axis=2)
        m = (d > DIFF_THRESHOLD).astype(np.uint8) * 255
        if cv2 is not None:
            # Close first, then open: closing fills the holes a plain-coloured
            # torso leaves where it happens to match the wall behind it (which
            # is where a title lives), and opening then drops the speckle that
            # closing would have grown. The other order eats the person.
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
            k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k2)
            if feather > 0:
                # A hard 1-pixel edge is what makes a composite look pasted.
                m = cv2.GaussianBlur(m, (feather * 2 + 1, feather * 2 + 1), 0)
        yield m


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
    plate = _plate(src, start, dur, w, h, extra_vf)
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
        for m in _mask_frames(frames, plate, cv2, feather):
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
