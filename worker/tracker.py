"""Following a filmed screen through the takeover window, frame by frame.

Round 63. The screen takeover (round 55) pins content to corners measured ONCE
and the pin stays rigid for the whole push — while the footage under it is a
HANDHELD phone shot that wobbles a few pixels every frame. The content visibly
slides against the glass it claims to be glued to, which is the single loudest
"this is an effect" signal the move produces; a user watching the round-62c
motion profile (fade-on, landing punch, frame-identical handoff) still called
the join obvious, and the slide is why. The honest limit written into round 55
("the pin does not track a screen that pans") was never a law of nature — it
was the missing piece of work, and this is that piece.

HOW: Lucas-Kanade optical flow on corner features found INSIDE the screen quad
at the window's first frame, tracked forward through every frame, with a
per-frame homography (RANSAC) mapping the first frame's surviving features to
their current positions. The homography moves the QUAD's corners, not the
features — so losing half the features to a hand crossing the screen still
moves the quad rigidly with the survivors, and RANSAC throws out the hand.

WHERE: the executor, always, when one is configured (round 61b's rule: the
window usually lives in a user's ORIGINAL — a 4K HEVC phone clip — and one
decoded 4K frame is ~240 MB resident; the dispatcher dies, the user reads
"I lost my connection"). The runner downloads the object, decodes the window
ONCE at a working width, and only a few hundred floats come back. A remote
failure NEVER falls back to decoding an original locally — the takeover keeps
its static pin instead, which is exactly what shipped before this existed.

WHAT COMES BACK: sampled quads (window-relative seconds -> 8 corner fractions)
plus a quality report. The caller stores them in the EDL only when the track
is confident AND the screen actually moves — a tripod shot keeps the plain
static pin and the EDL stays byte-identical to round 55's.
"""

import os
import shutil
import uuid

import numpy as np

import config
import media
import storage
from schemas import quad_is_sane

# The window is decoded at this width — flow needs structure, not resolution,
# and a 4K frame at 960 wide is a 16x lighter decode. Corners come back as
# FRACTIONS, so the working width never leaks into the result.
TRACK_WIDTH = 960
# Flow is tracked at most at this rate. Hand wobble lives well under 15 Hz;
# the renderer lerps between samples, and 30 samples/s of 8 floats is already
# more curve than the eye can check.
TRACK_MAX_FPS = 30.0
# At most this many keyframes ride back / into the EDL — the renderer builds
# one piecewise-linear expression per corner coordinate, and the graph grows
# with every knot. 24 knots over a 5 s window is a knot every 0.2 s, enough
# for hand shake (the residual between knots is sub-pixel).
MAX_KEYFRAMES = 24
# The track is unusable when fewer than this fraction of the features that
# started the window are still inlying at its end — the screen was occluded,
# left the frame, or the flow latched onto something else.
MIN_ALIVE_FRAC = 0.4
MIN_FEATURES = 12
# A screen that never moves more than this (fraction of the frame diagonal,
# largest corner excursion over the window) is a tripod shot: return no path
# so the EDL keeps the static pin it always had.
STATIC_EPS_FRAC = 0.004
# RANSAC reprojection tolerance, in working-width pixels.
RANSAC_TOL_PX = 3.0


def _decode_gray(path, start, dur, w, h, fps):
    import subprocess
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}",
           "-t", f"{max(0.05, dur):.3f}", "-i", path,
           "-vf", f"scale={w}:{h},fps={fps:.5f}",
           "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"]
    frame_bytes = w * h
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                            bufsize=min(frame_bytes * 8, 8 << 20))
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w)
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()


def track_quad(local_path, start, dur, quad_frac, work_w=TRACK_WIDTH):
    """Track `quad_frac` (8 corner fractions, perspective order) from `start`
    for `dur` seconds. Returns (quads, quality):

      quads   — [[t_rel, x0..y3], ...] window-relative, first entry t=0 with
                the INPUT quad verbatim (the measured corners stay authoritative
                for the frame they were measured on), or None when the track is
                not usable / the screen is static.
      quality — dict with alive fraction, samples, max excursion, and `why`
                when quads is None.
    """
    import cv2
    info = media.probe(local_path)
    sw, sh = int(info["width"]), int(info["height"])
    if sw <= 0 or sh <= 0:
        return None, {"why": "the clip reports no dimensions"}
    src_fps = max(1.0, min(float(info["fps"]) or 30.0, 120.0))
    fps = min(src_fps, TRACK_MAX_FPS)
    w = min(int(work_w), sw)
    w -= w % 2
    h = max(2, int(round(sh * w / float(sw) / 2)) * 2)

    q0 = np.array(quad_frac, np.float32).reshape(4, 2) * [w, h]

    frames = _decode_gray(local_path, start, dur, w, h, fps)
    first = next(frames, None)
    if first is None:
        return None, {"why": "no frames decoded in the window"}

    # Features INSIDE the quad, inset 6% so the bezel's own edge — which
    # belongs to the DEVICE, not the screen content, but moves identically —
    # still qualifies while the outside world does not.
    ctr = q0.mean(axis=0)
    inset = ctr + (q0 - ctr) * 0.94
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, np.array(
        [inset[0], inset[1], inset[3], inset[2]], np.int32), 255)
    pts0 = cv2.goodFeaturesToTrack(first, maxCorners=160, qualityLevel=0.01,
                                   minDistance=7, mask=mask, blockSize=7)
    if pts0 is None or len(pts0) < MIN_FEATURES:
        return None, {"why": (f"only {0 if pts0 is None else len(pts0)} "
                              "trackable features on the screen — too dark "
                              "or too flat to follow")}
    pts0 = pts0.reshape(-1, 2)
    alive = np.ones(len(pts0), bool)
    prev = first
    prev_pts = pts0.copy()
    quads = [(0.0, q0.copy())]
    t_step = 1.0 / fps
    t = 0.0
    lk = dict(winSize=(21, 21), maxLevel=3,
              criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                        30, 0.01))
    for f in frames:
        t += t_step
        cur, st, _ = cv2.calcOpticalFlowPyrLK(
            prev, f, prev_pts.reshape(-1, 1, 2), None, **lk)
        cur = cur.reshape(-1, 2)
        st = st.reshape(-1).astype(bool)
        alive &= st
        if alive.sum() < max(MIN_FEATURES, int(len(pts0) * MIN_ALIVE_FRAC)):
            return None, {"why": (f"lost the screen {t:.2f}s into the window "
                                  f"({int(alive.sum())} of {len(pts0)} "
                                  "features left) — something crossed it or "
                                  "it left the frame"),
                          "alive": round(alive.sum() / len(pts0), 2)}
        H, inl = cv2.findHomography(pts0[alive], cur[alive], cv2.RANSAC,
                                    RANSAC_TOL_PX)
        if H is None:
            return None, {"why": f"the flow degenerated {t:.2f}s in"}
        q = cv2.perspectiveTransform(q0.reshape(-1, 1, 2), H).reshape(4, 2)
        flat = [float(v) for v in (q / [w, h]).reshape(-1)]
        ok, _why = quad_is_sane([round(v, 5) for v in flat])
        if not ok or not all(-0.5 <= v <= 1.5 for v in flat):
            return None, {"why": (f"the tracked quad folded {t:.2f}s in — "
                                  "the motion is not a rigid screen")}
        quads.append((t, q.copy()))
        prev = f
        prev_pts = cur
    if len(quads) < 3:
        return None, {"why": "the window is too short to track"}

    diag = float(np.hypot(w, h))
    base = quads[0][1]
    exc = max(float(np.abs(q - base).max()) for _, q in quads) / diag
    quality = {"alive": round(float(alive.sum()) / len(pts0), 2),
               "samples": len(quads),
               "max_excursion_frac": round(exc, 5)}
    if exc < STATIC_EPS_FRAC:
        quality["why"] = "the screen holds still — a static pin is exact"
        return None, quality

    # Light smoothing (3-tap) so one frame's RANSAC wobble does not jiggle the
    # pin, then decimate to keyframes the renderer can afford. First and last
    # stay verbatim: t=0 is the measured quad, t=end is the arrival geometry.
    sm = [quads[0]]
    for i in range(1, len(quads) - 1):
        sm.append((quads[i][0],
                   (quads[i - 1][1] + quads[i][1] + quads[i + 1][1]) / 3.0))
    sm.append(quads[-1])
    if len(sm) > MAX_KEYFRAMES:
        idx = np.linspace(0, len(sm) - 1, MAX_KEYFRAMES).round().astype(int)
        sm = [sm[i] for i in sorted(set(idx.tolist()))]
    out = [[round(float(tt), 3)] + [round(float(v), 5)
                                    for v in (q / [w, h]).reshape(-1)]
           for tt, q in sm]
    return out, quality


def run_track_job(worker_db, job):
    """Executor-side runner (capture/frames-shaped: synchronous, no row).

    payload: {storage_key, start, dur, corners} -> {"quads", "quality"}.
    `worker_db` is unused — this writes no rows; only floats come back.
    """
    payload = job.get("payload") or {}
    key = payload.get("storage_key")
    start = float(payload.get("start") or 0.0)
    dur = float(payload.get("dur") or 0.0)
    corners = payload.get("corners")
    if not key or dur <= 0.05 or not corners or len(corners) != 8:
        raise ValueError("track job needs storage_key, start, dur, corners[8]")
    workdir = os.path.join(config.TMP_DIR, f"trk_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        local = os.path.join(workdir, "src" + os.path.splitext(key)[1])
        storage.download_to(key, local)
        quads, quality = track_quad(local, start, dur,
                                    [float(v) for v in corners])
        return {"quads": quads, "quality": quality}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
