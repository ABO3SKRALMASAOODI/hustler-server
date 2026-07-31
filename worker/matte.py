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
import shutil
import subprocess
import uuid

import numpy as np

import config
import media
import personseg

# Bumped when anything below would produce a DIFFERENT mask from the same
# footage. It rides the mask's cache fingerprint, so a change here re-measures
# instead of serving a mask built by the old arithmetic — the same reason the
# erase path fingerprints its own derivation.
VERSION = 10

# ── v10: temporal coherence comes from the MODEL, not from gates (round 69) ──
# The user filmed v9 failing five ways on project 300 in one afternoon, and
# every one of them is the same defect: per-frame thresholded decisions on a
# per-frame model flicker at the decision boundary. The armchair toggled
# between wholly-masked and wholly-carved (letters "behind the couch normally
# then all of a sudden on the couch"); the sample-lerped masks led the walker
# ("i dim things that i havent reached yet"); the walker himself lost pixels
# to the furniture zone where he lingered ("things ... appear on me"); and the
# 320px boundary wobble flickered letter fragments with nobody near them.
# Measured on that exact window: a 19.4%-of-frame mask change between two
# CONSECUTIVE frames, 8174 pixels toggling 12+ times, the chair flipping
# fully-on/fully-off 20 times.
#
# v10 replaces the whole cascade with RobustVideoMatting (personseg.
# rvm_stream): a video matting network whose four recurrent state tensors
# carry the previous frames' answer INTO each new frame. Run on EVERY mask
# frame (no strided sampling, so nothing is ever lerped between two subject
# positions), its soft alpha goes to the encoder untouched — no threshold, no
# vote, no zones, no morphology, no feather. Same window, measured: max
# frame-to-frame change 1.6%, 377 pixels toggling 12+, the chair never
# claimed at all (it is not a person — furniture separation stops being a
# gate and becomes the model's task definition), and the walker holds his
# full silhouette standing still or crossing furniture. The u2net + v9-gate
# path below survives as the middle rung of the honest-off ladder (RVM file
# absent -> u2net -> photometric); the refusal bounds and their meanings are
# unchanged.
RVM_MIN_FPS = 12.0           # fps halving floor under a strangled budget

# ── v6: the subject is FOUND, not inferred (round 64) ───────────────────────
# Versions 2-5 are four consecutive rounds of cleverer photometrics failing on
# the same real footage: a dark walker in a dark lamp-lit room, whose body
# differs from the plate by less than the noise the thresholds must clear. The
# shipped mask measured the subject at 2.7% of the frame against a visible
# ~15%, and the title printed straight across him — "why are the captions on
# top of the thing they were supposed to be behind". That is physics, not
# tuning: absolute differences cannot see dark-on-dark. So when the person
# model is available (personseg — executor-side in production), the mask comes
# from a per-frame segmentation instead, and the photometric pipeline below
# survives only as the fallback for deployments without the model.
#   * The model is run on a BUDGET of frames (SEG_BUDGET, evenly spaced) and
#     the soft masks between samples are linearly blended — full mask rate on
#     any normal window, graceful degradation on a pathological 15s one.
#   * STATIC things the model likes (an armchair reads as vaguely person-
#     shaped to u2net_human_seg — watched on the round-64 validation frame)
#     are separated from the subject by CONFIDENCE, not motion — see the v9
#     block below for why the motion gates of v6-v8 could not hold.
#   * A moving camera is fine here — each frame is segmented on its own — so
#     the v6 path drops the photometric path's stillness requirement.
SEG_THRESHOLD = 0.5          # calibrated sigmoid output; verified on real
#                              frames (empty room peaks at 0.001)
SEG_BUDGET = 240             # max model passes per window
SEG_MIN_BLOB_PX = 82         # components under this (model res) are speckle
SEG_VOTE = 5                 # majority vote window, in sampled frames
# ── v9: the person is EMPHATIC; furniture is REPEATED-BUT-WEAK (round 67) ───
# v6-v8 were three rounds of blob-level gates (static presence, flicker
# confidence, per-pixel toggle budget) trying to keep u2net's furniture
# detections out of the mask, and the round-67 screenshots show both ways
# that architecture loses on the same armchair:
#   * The gates keyed on PER-FRAME context. The armchair's blob merged with
#     the walker whenever he passed it, inheriting his presence/confidence
#     immunity — so the chair occluded the title exactly while he was near
#     and released it when he left. A static object whose mask state follows
#     the PERSON's position is the strobe the user filmed ("the o and w
#     started appearing on the couch ... just as i go in").
#   * The toggle budget punished the PIXELS that strobe — for the WHOLE
#     window. The person then walked through the punished region and was
#     unmasked there: "the w is even sitting on top of me". A guard that can
#     erase the subject is worse than the flicker it guards against.
# The v9 rule set is per-PIXEL and window-stable, from two measured facts
# (round 64 validation, incl. the real dark-room frame): the model is
# emphatic about people (~1.0 even on a dark walker) and merely tempted by
# furniture (hovers near threshold). So:
#   * A component is the SUBJECT only if it contains an emphatic core
#     (>= SEG_SEED_PX pixels at >= SEG_STRONG). The armchair alone never
#     qualifies; a standing-still person always does — the v6 motion gate
#     and its standing-person exception both retire.
#   * A FURNITURE ZONE is computed once for the window: pixels the model
#     claims repeatedly (presence >= SEG_STATIC_PRESENCE) but never
#     emphatically (mean confidence < SEG_STRONG). Inside a kept component,
#     zone pixels survive only when they are emphatic IN THIS FRAME — so a
#     merged person+armchair blob keeps the person and sheds the chair, in
#     every frame, whether or not they touch. Constant by construction:
#     nothing about the decision reads the person's current position.
SEG_STRONG = 0.75            # emphatic per-pixel confidence (the round-66
#                              measured boundary: furniture flicker sits
#                              under it, people at ~1.0)
SEG_SEED_PX = 12             # emphatic pixels a component needs to be the
#                              subject (model res; a person 3% of frame width
#                              still clears it several times over)
SEG_STATIC_PRESENCE = 0.35   # claimed in this share of samples = repeated
#                              (low on purpose: a chair claimed only while
#                              the person stands beside it still accumulates
#                              this much presence across a window)

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
# The mask is measured at most at this rate. It was capped at 30 for one
# round on the theory that a frame-doubled mask under 60 fps footage lags "at
# most 1/60 s, invisible under the feather" — which is true for a walking
# TORSO and false for a swinging ARM: a hand crosses letters at hundreds of
# px/s, the half-rate mask trails it by a visible sliver, and the letters
# flicker against the limb (watched on the round-63 render). The mask now
# follows the render rate up to 60; the cost returns only on 60 fps sources.
MASK_MAX_FPS = 60.0

# ── v4: the camera never actually holds still ───────────────────────────────
# Round 63, from the same project 246 footage one round later: with v3 live,
# the title STILL lost a band of letters wider than the walker. Two mechanisms,
# both measured off the screenshots and the stored mask:
#   * HANDHELD JITTER: a phone drifts a few pixels even "held still". At a
#     hard bright/dark boundary (a TV bezel, a lamp halo's rim) a 2px shift is
#     a 100+ luma difference — far past any threshold — so every high-contrast
#     edge in the frame wore a masked stripe. The noise map lifts thresholds
#     where jitter flickers, but a slow drift reads as signal, not noise.
#     Fix: ALIGN each frame to the plate (phase correlation at half res, a
#     global translation) before differencing, and un-shift the finished mask
#     back into the frame's own geometry. The plate itself is built from
#     samples aligned to the first sample for the same reason.
#   * LIGHT OCCLUSION: a body passing a lamp dims the wall around it (and
#     brightens it again as it leaves). v3's shadow test only forgave darkening
#     down to 0.5x with near-zero channel spread — a lamp shadow is 3-5x dark,
#     and on near-black pixels the ratio's spread is pure noise, so the whole
#     halo band was kept as "subject" and ate the title around the walker.
#     Fix: classify per BLOB, not per pixel. An illumination change is the same
#     surface under different light — channel-uniform scaling, and NO NEW
#     EDGES: the wall's own texture stays where it was. A person REPLACES the
#     surface and always brings a silhouette of edges the plate does not have.
#     Pooled over a blob, those two cues separate a shadow from a torso even in
#     the dark, where any single pixel is uninformative.
ALIGN_DOWNSCALE = 2          # phase correlation at 1/2 proxy resolution
MAX_ALIGN_PX = 14.0          # beyond this the camera is genuinely moving
ALIGN_DEADBAND_PX = 2        # under this per axis = estimator noise, not drift
ALIGN_MIN_RESPONSE = 0.03    # phaseCorrelate confidence floor; below = don't
RATIO_LO, RATIO_HI = 0.28, 2.4   # how far light may scale and still be light
SPREAD_BASE = 0.055          # allowed channel spread of the ratio...
SPREAD_DARK = 4.0            # ...plus this over (plate luma + 10). Small on
#                              purpose: round 63's first draft gave dark
#                              pixels generous slack and a noisy dark SUBJECT
#                              started classifying as illumination — the v2
#                              overprint bug reborn. Where the ratio is too
#                              noisy to decide, the BLOB correlation below
#                              decides instead.
EDGE_NEW = 26.0              # sobel-magnitude increase that counts as an edge
#                              the plate does not have
BLOB_ILLUM_FRAC = 0.75       # blob is illumination when this much of it fits
BLOB_NOVELTY_MAX = 0.06      # ...and it brought this few new-edge pixels
BLOB_CORR_SHADOW = 0.5       # gradient-structure correlation with the plate
#                              above which a blob is the SAME SURFACE re-lit
ILLUM_CARVE = 0.85           # inside a kept blob, carve pixels this deep in a
#                              coherent illumination field (smoothed fraction)


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


def _small_gray(f, cv2):
    """Half-res float32 gray for phase correlation. Small because the estimate
    is a GLOBAL translation — sub-pixel accuracy at half res is a pixel at
    full, which is inside the feather."""
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    if ALIGN_DOWNSCALE > 1:
        g = cv2.resize(g, (g.shape[1] // ALIGN_DOWNSCALE,
                           g.shape[0] // ALIGN_DOWNSCALE),
                       interpolation=cv2.INTER_AREA)
    return g.astype(np.float32)


def _align_shift(ref_sg, f, cv2, win=None):
    """WHOLE-PIXEL (dx, dy) that maps frame -> reference space, or (0, 0) when
    the correlation is not confident, the move is too big to be hand shake, or
    the move is under a pixel.

    Integer pixels on purpose: a sub-pixel warp interpolates, and bilinear
    interpolation LOW-PASSES the frame while the plate stays sharp — the
    asymmetric smoothing pulled a noisy subject's difference under its own
    threshold (measured on the breathing test: the walker vanished from the
    mask for the last second of the window). np.roll-exact translation keeps
    every pixel value; the sub-pixel residual is what v3 always lived with
    and the noise map already prices in.

    cv2.phaseCorrelate(src1, src2) returns the displacement OF src2 RELATIVE
    TO src1 — the CORRECTIVE shift is its negation (pinned by
    test_alignment_sign_convention_via_np_roll, because the docs are ambiguous
    and the wrong sign makes alignment double the error it exists to remove,
    which is precisely what the first draft shipped)."""
    sg = _small_gray(f, cv2)
    (dx, dy), resp = cv2.phaseCorrelate(ref_sg, sg, win)
    dx = round(-dx * ALIGN_DOWNSCALE)
    dy = round(-dy * ALIGN_DOWNSCALE)
    if resp < ALIGN_MIN_RESPONSE or abs(dx) > MAX_ALIGN_PX \
            or abs(dy) > MAX_ALIGN_PX:
        return 0, 0
    # Per-axis deadband: a 1px "shift" is inside the estimator's own noise on
    # a static scene (measured: a truly still synthetic reported (0, 1) from
    # the moving subject perturbing the peak), and a 1px residual is exactly
    # what the noise map has always priced in. Correct only real drift.
    if abs(dx) < ALIGN_DEADBAND_PX:
        dx = 0
    if abs(dy) < ALIGN_DEADBAND_PX:
        dy = 0
    return int(dx), int(dy)


def _proven_shift(f, target, dx, dy, cv2):
    """The candidate shift, or (0, 0) if it does not clearly IMPROVE the match.

    The estimator's sub-pixel noise doubles through ALIGN_DOWNSCALE and can
    round to a 'shift' on a scene that never moved — and one wrong 2px roll on
    a static shot decorrelates every textured pixel from the plate (measured:
    a single spurious shift took one frame's mask from 4% to 100% coverage).
    So alignment has to earn its keep per frame: apply the shift only when the
    subsampled residual against the target drops by a clear margin. Median,
    not mean — the subject is IN the frame and must not vote."""
    if not dx and not dy:
        return 0, 0
    grid = (slice(None, None, 4), slice(None, None, 4))
    a = f[grid].astype(np.float32)
    t = target[grid].astype(np.float32)
    r0 = float(np.median(np.abs(a - t)))
    fs = _shift(f, dx, dy, cv2)
    r1 = float(np.median(np.abs(fs[grid].astype(np.float32) - t)))
    if r1 < r0 * 0.85:
        return dx, dy
    return 0, 0


def _shift(img, dx, dy, cv2):
    """Translate by INTEGER (dx, dy) with edge replication (a border that goes
    black would difference against the plate as a fake subject). Pure memory
    move — no interpolation, no value changes."""
    dx, dy = int(dx), int(dy)
    if dx == 0 and dy == 0:
        return img
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]),
                          flags=cv2.INTER_NEAREST,
                          borderMode=cv2.BORDER_REPLICATE)


def _sobel_mag(gray_u8, cv2):
    g = cv2.GaussianBlur(gray_u8, (0, 0), 1.2)
    sx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    sy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(sx, sy)


def _plate(path, start, dur, w, h, extra_vf=None, cv2=None):
    """The background, photographed from the window itself.

    The MEDIAN and not the mean: a mean is dragged toward whatever the subject
    was wearing everywhere it went, which puts a ghost of the person into the
    background and takes a bite out of their own matte. A median of 24 samples
    is the pixel's majority value, which is the background unless the subject
    covered that pixel in most of the window — and where it did, that pixel is
    lost to the matte, honestly and locally, rather than smearing.

    Returns (plate, noise, ref_sg): ref_sg is the half-res gray of the FIRST
    sample — the space every sample was aligned into, and the space every
    output frame is aligned into later, so plate and frame always meet in the
    same geometry.
    """
    step = dur / float(PLATE_SAMPLES)
    stack = []
    ref_sg = None
    ref_frame = None
    for i in range(PLATE_SAMPLES):
        t = start + step * (i + 0.5)
        for f in _decode(path, t, 0.06, w, h, extra_vf):
            # uint8, NOT float32 — see below. np.median partitions in the
            # input dtype and returns float64 for the (h, w, 3) result, which
            # is one frame's worth, not the stack's.
            if cv2 is not None:
                if ref_sg is None:
                    ref_sg = _small_gray(f, cv2)
                    ref_frame = f
                else:
                    dx, dy = _align_shift(ref_sg, f, cv2)
                    dx, dy = _proven_shift(f, ref_frame, dx, dy, cv2)
                    f = _shift(f, dx, dy, cv2)
            stack.append(f)
            break
    if len(stack) < 6:
        return None, None, None
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
    return med, noise, ref_sg


def _mask_frames(frames, plate, noise, cv2, ref_sg=None):
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
    plate_f = plate.astype(np.float32)
    plate_gray_sobel = None
    plate_luma = None
    hann = None
    if cv2 is not None:
        pg = np.clip(plate, 0, 255).astype(np.uint8)
        plate_gray_sobel = _sobel_mag(cv2.cvtColor(pg, cv2.COLOR_BGR2GRAY),
                                      cv2)
        plate_luma = plate_f.mean(axis=2)
        if ref_sg is not None:
            hann = cv2.createHanningWindow(
                (ref_sg.shape[1], ref_sg.shape[0]), cv2.CV_32F)
    for f in frames:
        dx = dy = 0
        if cv2 is not None and ref_sg is not None:
            # ONE global translation per frame. This is hand shake, not a
            # stabiliser: a real pan blows past MAX_ALIGN_PX, alignment turns
            # itself off, the whole frame differs from the plate and the
            # coverage refusal below tells the user the camera moved.
            dx, dy = _align_shift(ref_sg, f, cv2, hann)
            dx, dy = _proven_shift(f, plate_f, dx, dy, cv2)
            f = _shift(f, dx, dy, cv2)
        diff = f.astype(np.float32) - plate_f
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
        on = (d > thr) if thr is not None else (d > DIFF_THRESHOLD)
        m = on.astype(np.uint8) * 255
        if cv2 is not None:
            # ── what changed vs what is merely LIT differently ────────────
            # Per-pixel evidence, pooled per blob below. ratio = how each
            # channel scaled; an illumination change scales them together
            # (hue preserved), a body replaces the surface. Dark pixels get
            # spread slack — their ratio is noise — which is exactly why the
            # per-pixel answer alone cannot be trusted and the blob pools it.
            # The GLOBAL bias comes off first, same as the diff above: auto-
            # exposure breathing is additive in our model, and an offset
            # smeared into a ratio reads as channel spread — at the breathing
            # peaks a clean 0.65x wall shadow failed the uniformity test and
            # was kept as subject.
            ratio = (f.astype(np.float32) - bias.astype(np.float32) + 4.0) \
                / (plate_f + 4.0)
            rmean = ratio.mean(axis=2)
            rspread = ratio.max(axis=2) - ratio.min(axis=2)
            spread_allow = SPREAD_BASE + SPREAD_DARK / (plate_luma + 10.0)
            # NEW EDGES: texture the plate does not have. A shadow falls ON
            # the wall's texture (its own edges stay put; only the penumbra
            # rim is new); a person brings a silhouette and folds. This is
            # the cue that still works where the ratio is noise.
            fg = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            frame_sobel = _sobel_mag(fg, cv2)
            novel = (frame_sobel - 1.15 * plate_gray_sobel) > EDGE_NEW
            illum = ((rmean > RATIO_LO) & (rmean < RATIO_HI)
                     & (rspread < spread_allow) & ~novel)
            # Confident illumination comes off BEFORE morphology, exactly
            # where v3 subtracted its shadow test: leaving it in lets the
            # close bridge a shadow band, the subject and stray speckle into
            # one mega-blob no later stage can classify (measured: mean
            # coverage rode to 10x the subject's footprint).
            m = (on & ~illum).astype(np.uint8) * 255
            # Close first, then open: closing fills the holes a plain-coloured
            # torso leaves where it happens to match the wall behind it (which
            # is where a title lives), and opening then drops the speckle that
            # closing would have grown. The other order eats the person.
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
            k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k2)
            # ── per-BLOB: same surface re-lit, or a new thing in the shot? ──
            # (round 63). The per-pixel test above is deliberately timid — at
            # dark pixels a ratio says nothing (v3 kept a 3-5x lamp shadow as
            # "subject" for exactly that reason, and a first draft that gave
            # dark pixels slack started eating dark SUBJECTS instead). What a
            # single pixel cannot answer, a blob of thousands can, and the
            # question with actual discriminating power in the dark is about
            # STRUCTURE, not colour: a shadow falls ON the wall — the wall's
            # own gradient field survives, merely dimmed, so the blob's sobel
            # correlates with the plate's — where a person REPLACES the wall
            # and their texture has no relationship to what was behind them.
            h_, w_ = m.shape
            min_px = max(64, int(MIN_BLOB_FRAC * w_ * h_))
            nlab, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
            if nlab > 1:
                illum_f = illum.astype(np.float32)
                novel_f = novel.astype(np.float32)
                keep = np.zeros(nlab, bool)
                carve_blobs = []
                for b in range(1, nlab):
                    area = stats[b, cv2.CC_STAT_AREA]
                    if area < min_px:
                        continue
                    x0 = stats[b, cv2.CC_STAT_LEFT]
                    y0 = stats[b, cv2.CC_STAT_TOP]
                    bw = stats[b, cv2.CC_STAT_WIDTH]
                    bh = stats[b, cv2.CC_STAT_HEIGHT]
                    sel = lab[y0:y0 + bh, x0:x0 + bw] == b
                    fi = float(illum_f[y0:y0 + bh, x0:x0 + bw][sel].mean())
                    fn = float(novel_f[y0:y0 + bh, x0:x0 + bw][sel].mean())
                    sf = frame_sobel[y0:y0 + bh, x0:x0 + bw][sel]
                    sp = plate_gray_sobel[y0:y0 + bh, x0:x0 + bw][sel]
                    sf = sf - sf.mean()
                    sp = sp - sp.mean()
                    denom = float(np.sqrt((sf * sf).sum() * (sp * sp).sum()))
                    corr = float((sf * sp).sum()) / denom if denom > 1e-6 \
                        else 1.0
                    # denom ~ 0 is a structureless void (a black region with
                    # no gradients anywhere) — nothing to hide words behind;
                    # treated as re-lit surface, not subject.
                    if corr > BLOB_CORR_SHADOW or (fi > BLOB_ILLUM_FRAC
                                                   and fn < BLOB_NOVELTY_MAX):
                        continue        # a shadow / a moving pool of light
                    keep[b] = True
                    if fi > 0.25:
                        # Person + their attached shadow can arrive as ONE
                        # blob (morphology bridges them at the feet). Carve
                        # only where the illumination field is COHERENT —
                        # deep inside a smoothed run of illumination pixels —
                        # never on the person's own noisy interior.
                        carve_blobs.append(b)
                m = np.where(keep[lab], 255, 0).astype(np.uint8)
                if carve_blobs:
                    coh = cv2.blur(illum_f, (15, 15))
                    carve = (coh > ILLUM_CARVE) & ~novel & \
                        np.isin(lab, carve_blobs)
                    m[carve] = 0
                    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k2)
                    # re-drop anything the carve shaved below personhood
                    nl2, lb2, st2, _ = cv2.connectedComponentsWithStats(m, 8)
                    if nl2 > 1:
                        keep2 = np.zeros(nl2, bool)
                        keep2[1:] = st2[1:, cv2.CC_STAT_AREA] >= min_px
                        m = np.where(keep2[lb2], 255, 0).astype(np.uint8)
            # Structure, not just texture: fill the holes a person always has
            # (a matte with a window through the torso prints the title
            # THROUGH the subject, the single most visible way this fails).
            m = _fill_holes(m, cv2)
            if dx or dy:
                # The mask was measured in PLATE space; the composite needs it
                # in the frame's own geometry, or the cut-out subject sits a
                # jitter off the real one.
                m = _shift(m, -dx, -dy, cv2)
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


def _box_px(box, w, h):
    if not box:
        return None
    bx = int(max(0.0, min(1.0, box[0])) * w)
    by = int(max(0.0, min(1.0, box[1])) * h)
    bw = max(1, int(max(0.01, min(1.0, box[2])) * w))
    bh = max(1, int(max(0.01, min(1.0, box[3])) * h))
    return (bx, by, min(w, bx + bw), min(h, by + bh))


def _box_frame_stats(on, box_px):
    """(area_frac, width_frac) of the text box the mask claims this frame.

    width_frac is the LEGIBILITY number (round 63): the fraction of the line's
    WIDTH where a column of the box is at least a quarter covered. 11% of the
    box's AREA sounds like nothing and can still be two whole letters gone —
    a word with two letters missing is a broken word, and the agent told a
    user "only 11%" about a title that read as gibberish. Width interrupted
    maps to letters interrupted, which is what eyes actually measure.
    """
    x0, y0, x1, y1 = box_px
    sub = on[y0:y1, x0:x1]
    if not sub.size:
        return 0.0, 0.0
    area = float(sub.sum()) / float(sub.size)
    colcov = sub.mean(axis=0)
    width = float((colcov >= 0.25).mean())
    return area, width


def _seg_sample_masks(frames, est_frames, cv2):
    """(sample_indices, sample_masks_320, n_frames): the model run on a
    budgeted, evenly-strided subset of the window's frames. Masks are kept at
    model resolution as uint8 — a whole window of them is a few MB."""
    stride = max(1, int(np.ceil(max(1.0, est_frames) / SEG_BUDGET)))
    idxs, masks = [], []
    n = 0
    for f in frames:
        if n % stride == 0:
            soft = personseg.segment(f, cv2)
            idxs.append(n)
            masks.append((soft * 255.0).astype(np.uint8))
        n += 1
    return idxs, masks, n


def _seg_maps(masks):
    """(presence, conf) per pixel across the window's samples: the share of
    samples where the model claims the pixel at all, and the mean confidence
    where claimed. These two numbers are what separate a person from a chair:
    a chair is claimed often and never emphatically; a person is emphatic
    wherever they are, and wherever they linger their presence rides high WITH
    that confidence."""
    stack = np.stack([m.astype(np.float32) / 255.0 for m in masks])
    binm = stack >= SEG_THRESHOLD
    presence = binm.mean(axis=0).astype(np.float32)
    on_count = np.maximum(binm.sum(axis=0), 1)
    conf = ((stack * binm).sum(axis=0) / on_count).astype(np.float32)
    return presence, conf


def _seg_furniture_zone(presence, conf):
    """Pixels the model claims repeatedly but never emphatically, decided ONCE
    for the whole window. Boolean (320, 320) or None when nothing qualifies.
    This is a region, not a verdict: inside it, per-frame emphatic evidence
    still wins (the person standing in front of the armchair is masked; the
    armchair alone never is)."""
    zone = (presence >= SEG_STATIC_PRESENCE) & (conf < SEG_STRONG)
    return zone if bool(zone.any()) else None


def _seg_person_frames(masks, zone, cv2):
    """Per-sample BINARY person mask (uint8 0/255, model res).

    Two per-pixel rules, no frame-level context — which is what makes the
    result temporally stable by construction:
      * a connected component is kept only when it carries an emphatic core
        (>= SEG_SEED_PX pixels at >= SEG_STRONG) and is at least
        SEG_MIN_BLOB_PX — the armchair-alone and codec speckle both die here;
      * within a kept component, furniture-zone pixels survive only where
        THIS frame is emphatic — a merged person+chair blob sheds the chair
        without the person losing so much as his silhouette.
    """
    out = []
    bin_thr = int(SEG_THRESHOLD * 255)
    strong_thr = int(SEG_STRONG * 255)
    for m in masks:
        binm = (m > bin_thr).astype(np.uint8)
        strong = m > strong_thr
        nlab, lab, stats, _ = cv2.connectedComponentsWithStats(binm * 255, 8)
        keep = np.zeros(nlab, bool)
        for b in range(1, nlab):
            if stats[b, cv2.CC_STAT_AREA] < SEG_MIN_BLOB_PX:
                continue
            x0 = stats[b, cv2.CC_STAT_LEFT]
            y0 = stats[b, cv2.CC_STAT_TOP]
            bw = stats[b, cv2.CC_STAT_WIDTH]
            bh = stats[b, cv2.CC_STAT_HEIGHT]
            sel = lab[y0:y0 + bh, x0:x0 + bw] == b
            if int(strong[y0:y0 + bh, x0:x0 + bw][sel].sum()) >= SEG_SEED_PX:
                keep[b] = True
        pm = keep[lab]
        if zone is not None:
            pm = pm & (~zone | strong)
        out.append(pm.astype(np.uint8) * 255)
    return out


def _seg_stabilize(masks, cv2, sample_dt):
    """Temporal majority vote over the per-sample person masks, then hole
    filling. This is _vote3's job done for the model path: a detection that
    lives for one or two samples is flicker and dies here; the subject
    survives every vote.

    The window is bounded in REAL TIME, not samples: a moving subject only
    overlaps themselves across ~100ms, so voting across the WIDE gaps of a
    budget-strided window would erase the walker outright (caught by the
    budget test — 6 samples over 3s put him at 6 disjoint positions and the
    majority killed every one). Under coarse sampling the vote narrows to
    nothing and the masks pass through un-voted, which is the honest trade:
    long windows get less flicker protection, never a missing subject.
    Returns uint8 0/255 masks, same length as the input."""
    width = SEG_VOTE
    if sample_dt > 1e-9:
        width = min(SEG_VOTE, 1 + 2 * int(0.075 / sample_dt))
    binm = [(m > int(SEG_THRESHOLD * 255)).astype(np.uint8) for m in masks]
    n = len(binm)
    half = max(0, width // 2)
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        window = binm[lo:hi]
        s = np.sum(window, axis=0)
        m = (s * 2 > len(window)).astype(np.uint8) * 255
        if cv2 is not None:
            m = _fill_holes(m, cv2)
        out.append(m)
    return out


def _seg_mask_frames(idxs, masks, n_frames, w, h, cv2):
    """One soft mask per OUTPUT frame at (w, h): linear blend between the
    bracketing sampled masks (identity when every frame was sampled),
    upscaled last. Yields uint8 (h, w)."""
    j = 0
    for i in range(n_frames):
        while j + 1 < len(idxs) and idxs[j + 1] <= i:
            j += 1
        if j + 1 < len(idxs) and idxs[j] < i:
            t = (i - idxs[j]) / float(idxs[j + 1] - idxs[j])
            soft = ((1.0 - t) * masks[j].astype(np.float32)
                    + t * masks[j + 1].astype(np.float32))
        else:
            soft = masks[j].astype(np.float32)
        m = cv2.resize(soft.astype(np.uint8), (w, h),
                       interpolation=cv2.INTER_LINEAR)
        yield m


def measure_and_build(src, out_path, start, dur, *, box=None, fps=None,
                      extra_vf=None, width=None, height=None, feather=3,
                      allow_model=True):
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
    out_fps = min(float(fps or src_fps), MASK_MAX_FPS)
    w = int(width or sw)
    h = int(height or sh)
    w, h = max(2, w - w % 2), max(2, h - h % 2)
    if dur > MAX_WINDOW_S:
        return {"ok": False,
                "why": (f"the window is {dur:.1f}s and I measure the subject "
                        f"frame by frame — over {MAX_WINDOW_S:.0f}s that does "
                        "not finish inside one edit turn. Use a shorter "
                        "window for the words behind you")}
    use_rvm = (bool(allow_model) and cv2 is not None
               and personseg.rvm_available())
    use_model = (not use_rvm and bool(allow_model) and cv2 is not None
                 and personseg.available())
    if use_rvm:
        # The recurrent model runs on EVERY encoded mask frame — a strided
        # run lerped between two subject positions is the round-68 ghost
        # that dimmed letters the walker had not reached. The budget bounds
        # wall time by halving the mask RATE instead; the renderer's fps
        # conform duplicates frames up to the render rate, so a reduced-rate
        # mask trails a fast limb by at most one mask frame, never by a
        # blended double-exposure.
        while (dur * out_fps > config.MATTE_RVM_BUDGET
               and out_fps / 2.0 >= RVM_MIN_FPS):
            out_fps = out_fps / 2.0
        stream = personseg.rvm_stream(w, h)

        def _rvm_masks():
            for f in _decode(src, start, dur, w, h, extra_vf, fps=out_fps):
                yield (stream.step(f, cv2) * 255.0).astype(np.uint8)
        mask_iter = _rvm_masks()
    elif use_model:
        idxs, s_masks, n_frames = _seg_sample_masks(
            _decode(src, start, dur, w, h, extra_vf, fps=out_fps),
            dur * out_fps, cv2)
        if n_frames == 0 or not s_masks:
            return {"ok": False,
                    "why": ("I could not read enough frames from that "
                            "moment to find the subject")}
        # v9: the furniture zone is measured once from the whole window's
        # soft masks; each sample then reduces to its emphatic-cored person
        # components; the vote smooths what remains. Nothing per-frame reads
        # the person's position, so nothing can toggle with it.
        presence, conf = _seg_maps(s_masks)
        zone = _seg_furniture_zone(presence, conf)
        person = _seg_person_frames(s_masks, zone, cv2)
        sample_dt = ((idxs[1] - idxs[0]) / out_fps if len(idxs) > 1
                     else 1.0 / out_fps)
        proc = _seg_stabilize(person, cv2, sample_dt)
        mask_iter = _seg_mask_frames(idxs, proc, n_frames, w, h, cv2)
    else:
        plate, noise, ref_sg = _plate(src, start, dur, w, h, extra_vf, cv2)
        if plate is None:
            return {"ok": False,
                    "why": ("I could not read enough frames from that moment "
                            "to photograph the background behind you")}
        frames = _decode(src, start, dur, w, h, extra_vf, fps=out_fps)
        mask_iter = _vote3(_mask_frames(frames, plate, noise, cv2, ref_sg))
    total = w * h
    n = 0
    cov_sum = 0.0
    cov_max = 0.0
    hits = 0
    box_px = _box_px(box, w, h)
    box_cov = 0.0
    box_width_cov = 0.0

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
        for m in mask_iter:
            if cv2 is not None and feather > 0 and not use_rvm:
                # A hard 1-pixel edge is what makes a composite look pasted.
                # After the vote, so the majority is taken on crisp binaries.
                # The RVM alpha is exempt: it IS a soft matte already, and
                # blurring it would fatten the subject by the feather width.
                m = cv2.GaussianBlur(m, (feather * 2 + 1, feather * 2 + 1), 0)
            on = m > 127
            c = float(on.sum()) / float(total)
            cov_sum += c
            cov_max = max(cov_max, c)
            if c >= MIN_COVERAGE:
                hits += 1
            if box_px:
                a, wd = _box_frame_stats(on, box_px)
                box_cov = max(box_cov, a)
                box_width_cov = max(box_width_cov, wd)
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
    person_led = use_rvm or use_model
    res = {"ok": True, "frames": n, "width": w, "height": h,
           "fps": round(out_fps, 3), "coverage": round(mean_cov, 4),
           "coverage_max": round(cov_max, 4),
           "moving_frames": hits,
           "text_covered": round(box_cov, 3) if box_px else None,
           "text_width_covered": round(box_width_cov, 3) if box_px else None,
           "method": "person" if person_led else "plate",
           "engine": ("rvm" if use_rvm
                      else "u2net" if use_model else "plate"),
           "matte_version": VERSION,
           "no_cv2": cv2 is None}
    # THE MEASUREMENT IS THE FEATURE. Both refusal bounds are real footage,
    # not edge cases — but what they MEAN depends on how the mask was made.
    if mean_cov > MAX_COVERAGE:
        res.update(ok=False, why=(
            f"the subject fills {mean_cov * 100:.0f}% of the frame through "
            "that window, so words behind them would barely ever be visible. "
            "Offer a normal title instead, and say why")
            if person_led else (
            f"{mean_cov * 100:.0f}% of the frame changes through that window, "
            "so there is no still background to put words on — the camera is "
            "moving (or the shot cuts inside the window). This effect needs a "
            "shot where the camera holds still and the subject moves. Offer a "
            "normal title instead, and say why"))
    elif mean_cov < MIN_COVERAGE:
        res.update(ok=False, why=(
            "I cannot find a person in that window "
            f"({mean_cov * 100:.2f}% of the frame reads as subject), so "
            "there is nothing for the words to go behind. Point it at a "
            "moment where someone is actually in front of the camera")
            if person_led else (
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


def box_stats(mask_path, box):
    """Re-measure an EXISTING mask against a NEW text box.

    Round 63: a cache hit used to parrot the numbers measured for whichever
    text FIRST built the mask — a user shrank their title, the tool quoted
    "11% of the text" from the old, larger box, and the smaller title on
    screen was gibberish. The mask never changes with the box; the numbers
    must. Decoding a 540p gray mp4 is proxy-class work, fine on the small box.

    Returns {"coverage", "coverage_max", "text_covered", "text_width_covered"}
    or None when the mask cannot be read (caller falls back to honesty about
    not knowing, never to the wrong number).
    """
    try:
        info = media.probe(mask_path)
        w, h = int(info["width"]), int(info["height"])
    except Exception:
        return None
    box_px = _box_px(box, w, h)
    cov_sum = cov_max = box_cov = box_width = 0.0
    n = 0
    total = float(w * h)
    for m in _decode(mask_path, 0.0, float(info.get("duration") or
                                           MAX_WINDOW_S) + 1.0, w, h):
        on = m[:, :, 0] > 127
        c = float(on.sum()) / total
        cov_sum += c
        cov_max = max(cov_max, c)
        if box_px:
            a, wd = _box_frame_stats(on, box_px)
            box_cov = max(box_cov, a)
            box_width = max(box_width, wd)
        n += 1
    if n == 0:
        return None
    return {"coverage": round(cov_sum / n, 4),
            "coverage_max": round(cov_max, 4),
            "text_covered": round(box_cov, 3) if box_px else None,
            "text_width_covered": round(box_width, 3) if box_px else None}


def run_matte_job(worker_db, job):
    """Executor-side runner (capture/frames/track-shaped: synchronous, no
    row). The model's forward passes are why this is here — the round-60
    objection to running them on the box that also runs agent turns is still
    valid, so in production the dispatcher never touches personseg.

    payload: {storage_key, start, dur, box, extra_vf, width, height, out_key}
    -> measure_and_build's stats dict. On ok=True the finished mask has
    already been uploaded to out_key; nothing heavy rides the response.
    `worker_db` is unused — this writes no rows.
    """
    import storage
    payload = job.get("payload") or {}
    key = payload.get("storage_key")
    out_key = payload.get("out_key")
    start = float(payload.get("start") or 0.0)
    dur = float(payload.get("dur") or 0.0)
    if not key or not out_key or dur <= 0.05:
        raise ValueError("matte job needs storage_key, out_key, start, dur")
    # The dispatcher fingerprints its cache key with ITS matte.VERSION. An
    # executor running a different version would upload a differently-built
    # mask under that key and poison the cache for every future user of the
    # window — the round-60 false-claim class, silent and permanent. Refuse
    # loudly instead; the dispatcher falls back honestly and says so. A
    # payload WITHOUT the field is an older dispatcher mid-deploy: accept,
    # its fingerprint matches what this code used to build.
    want = payload.get("matte_version")
    if want is not None and int(want) != VERSION:
        raise ValueError(
            f"executor builds matte v{VERSION} but the dispatcher expects "
            f"v{int(want)} — redeploy the executor (gcloud run deploy "
            "valmera-executor --source worker/ --region us-central1)")
    box = payload.get("box")
    workdir = os.path.join(config.TMP_DIR, f"mat_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        local = os.path.join(workdir, "src" + os.path.splitext(key)[1])
        storage.download_to(key, local)
        out = os.path.join(workdir, "mask.mp4")
        stats = measure_and_build(
            local, out, start, dur,
            box=tuple(box) if box else None,
            extra_vf=payload.get("extra_vf"),
            width=payload.get("width"), height=payload.get("height"))
        if stats.get("ok"):
            storage.upload_file(out, out_key, "video/mp4")
        return stats
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
