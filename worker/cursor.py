"""Round 51 — finding the mouse pointer in a screen recording and redrawing it.

On a screen recording the pointer is the narrator: it is what the viewer's eye
follows and it is the only thing on screen that says "a person is doing this".
It is also, at 1080p scaled into a phone-sized player, about four grey pixels.
Users say this as "the cursor is too small" and "the cursor is too jittery",
and until now the honest answer was that we could not touch it.

Three things happen here, in this order, and each is a separate failure mode:

  1. LOCATE. Normalised-correlation template match against a drawn pointer
     sprite, over a coarse working resolution, with the search restricted to a
     window around the previous hit once the track is established. Subpixel
     refined by fitting a parabola to the correlation peak — without that the
     redrawn pointer quantises to the working grid and visibly stair-steps.

  2. SMOOTH. A one-euro filter over the located track. Not a moving average:
     a moving average that removes hand tremor also smears a fast deliberate
     flick into a lazy drift, which looks worse than the tremor. One-euro
     adapts its cutoff to the speed — it filters hard when the hand is resting
     and barely at all when the pointer is crossing the screen.

  3. REDRAW. The original pointer is repainted out at its DETECTED position
     (it has to be: the smoothed position is somewhere else, so leaving it
     would show two cursors) and a scaled sprite is drawn at the SMOOTHED one,
     with an expanding ring at each caller-supplied click time.

WHAT THIS DOES NOT DO: it does not detect clicks. Clicks are not visible in a
screen recording — nothing in the pixels distinguishes a press from a hover —
so click_times is supplied by the caller (record_website_demo already knows
them exactly; a user's own recording gets them from the person who made it).
Guessing them from cursor dwell time would put ripples on hesitation.

Everything here is SOURCE time. The pass bakes into a derived copy of the
source, so every later cut carries it for free and no timestamp moves.
"""
import math
import os
import subprocess

import numpy as np

import config
import media

# The pointer we draw, and the shape we look for: the macOS arrow silhouette,
# the same outline webrecord.py injects into its demo captures. Points are on a
# 26x26 grid (the SVG viewBox), so one polygon serves every size.
ARROW = np.array([(3.0, 1.6), (3.0, 20.4), (8.1, 15.6), (11.4, 22.9),
                  (14.9, 21.3), (11.6, 14.2), (18.6, 13.9)],
                 dtype=np.float32)
ARROW_GRID = 26.0
# The tip is the anchor. A pointer's position IS its tip — anchoring at the
# sprite's centre would make a 3x cursor point somewhere the 1x one did not.
ARROW_TIP = (3.0, 1.6)

# Search resolution. Big enough that the POINTER survives it, which is the
# binding constraint and not the frame size: measured against synthetic
# screen captures, a 26px pointer is found cleanly and a 13px one (the same
# capture downscaled to 640 wide) is not — the arrow is only ~400 pixels of
# signal to begin with. So a 4K capture is halved and a 1080p one is left
# alone. The cost is bounded anyway: the whole-frame search happens on the
# first frame and after a lost lock; every other frame searches a window
# around where the pointer just was.
WORK_W = 1280
# Once the track is established, only look near where it was. A pointer cannot
# teleport, and the window turns a whole-frame search into a local one.
TRACK_WINDOW_PX = 90
# Below this score the "hit" is background texture that happens to look like
# an arrow. Measured on synthetic captures: a real pointer scores 0.7+ and the
# best false positive on cursor-less UI scores ~0.2, so the gap is wide and
# this sits in the middle of it.
MIN_SCORE = 0.45
# A pointer is drawn at a handful of sizes across the platforms people record
# on (and a Retina capture doubles it). Searching a few scales costs a few
# correlations per frame and is the difference between working on a MacBook
# recording and not.
SEARCH_SCALES = (0.7, 1.0, 1.4, 2.0)

RIPPLE_S = 0.55
RIPPLE_MAX_R = 2.6          # multiples of the drawn cursor's height


def sprite(height, rgba=True):
    """The pointer, drawn `height` pixels tall, as BGRA uint8.

    Supersampled 4x and downsampled so the outline is antialiased — a hard
    polygon edge is what makes a redrawn cursor look pasted on.
    """
    import cv2
    ss = 4
    h = max(8, int(round(height)))
    scale = (h * ss) / ARROW_GRID
    pts = np.round(ARROW * scale).astype(np.int32)
    w = int(math.ceil(ARROW_GRID * scale))
    big = np.zeros((h * ss + 2, w + 2, 4), np.uint8)
    # Dark outline first, white fill inside it: a single flat colour vanishes
    # against half the screens people record.
    cv2.polylines(big, [pts], True, (17, 17, 17, 255),
                  max(2, int(round(1.4 * scale))), cv2.LINE_AA)
    cv2.fillPoly(big, [pts], (255, 255, 255, 255), cv2.LINE_AA)
    cv2.polylines(big, [pts], True, (17, 17, 17, 255),
                  max(2, int(round(1.4 * scale))), cv2.LINE_AA)
    small = cv2.resize(big, (max(4, w // ss + 1), max(6, h + 1)),
                       interpolation=cv2.INTER_AREA)
    if not rgba:
        return small[:, :, :3]
    return small


def _template(height):
    """(gray_template, mask) for matchTemplate at a given pointer height.

    The mask is the sprite's own alpha, and it is what makes this work at all:
    the comparison then only looks at the arrow's pixels, so a white pointer on
    a white page and the same pointer on a dark one score identically. Without
    it, normalised CORRELATION against a mostly-white template scores ~0.7
    everywhere on a light UI — a detector that finds a cursor in every frame of
    footage that has none.
    """
    import cv2
    sp = sprite(height)
    gray = cv2.cvtColor(sp[:, :, :3], cv2.COLOR_BGR2GRAY)
    mask = sp[:, :, 3]
    return gray, mask


def _subpixel(surface, x, y):
    """Parabolic refinement of a correlation peak, in surface coordinates."""
    h, w = surface.shape
    dx = dy = 0.0
    if 0 < x < w - 1:
        a, b, c = float(surface[y, x - 1]), float(surface[y, x]), \
            float(surface[y, x + 1])
        d = a - 2 * b + c
        if abs(d) > 1e-9:
            dx = max(-0.5, min(0.5, 0.5 * (a - c) / d))
    if 0 < y < h - 1:
        a, b, c = float(surface[y - 1, x]), float(surface[y, x]), \
            float(surface[y + 1, x])
        d = a - 2 * b + c
        if abs(d) > 1e-9:
            dy = max(-0.5, min(0.5, 0.5 * (a - c) / d))
    return x + dx, y + dy


class _OneEuro:
    """One-euro filter (Casiez et al.). min_cutoff sets how hard a RESTING
    pointer is filtered; beta how quickly the filter gets out of the way when
    the pointer moves fast. Both derived from one 0-1 `smoothing` knob so the
    tool has one number to expose."""

    def __init__(self, freq, min_cutoff, beta, d_cutoff=1.0):
        self.freq = max(1.0, float(freq))
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x = None
        self._dx = 0.0

    @staticmethod
    def _alpha(cutoff, freq):
        tau = 1.0 / (2 * math.pi * max(1e-6, cutoff))
        te = 1.0 / freq
        return 1.0 / (1.0 + tau / te)

    def __call__(self, x):
        if self._x is None:
            self._x = float(x)
            return self._x
        dx = (float(x) - self._x) * self.freq
        a_d = self._alpha(self.d_cutoff, self.freq)
        self._dx = a_d * dx + (1 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff, self.freq)
        self._x = a * float(x) + (1 - a) * self._x
        return self._x


def _decode(src, w, h, fps, pix="bgr24"):
    return subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", src,
         "-vf", f"scale={w}:{h},fps={fps:.5f}",
         "-pix_fmt", pix, "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        bufsize=min(w * h * 3 * 4, 8 << 20))


def track(src, *, progress_cb=None):
    """Pass 1: locate the pointer in every frame.

    Returns {"points": [(x, y, score) or None per frame], "fps", "width",
    "height", "scales": the winning sprite height in FULL-RES pixels}.
    Runs at WORK_W and reports full-resolution coordinates.
    """
    import cv2
    info = media.probe(src)
    W, H = int(info["width"]), int(info["height"])
    fps = max(1.0, min(float(info["fps"]) or 30.0, 120.0))
    dur = float(info["duration"])
    sar = float(info.get("sar") or 1.0)
    if abs(sar - 1.0) > 0.001:
        W = max(2, int(round(W * sar / 2)) * 2)

    ww = min(WORK_W, W)
    k = ww / float(W)
    wh = max(2, int(round(H * k / 2)) * 2)
    ww = max(2, int(round(ww / 2)) * 2)
    expected = max(1, int(round(dur * fps)))

    # A pointer is ~26 CSS px tall; at typical capture scales that is 18-52
    # full-res pixels. Templates are built at the WORKING scale.
    tmpls = []
    for s in SEARCH_SCALES:
        th = 26.0 * s * k
        if th < 7 or th > wh / 3:
            continue
        g, m = _template(th)
        tmpls.append((s, g, m))
    if not tmpls:
        g, m = _template(max(8, 26.0 * k))
        tmpls = [(1.0, g, m)]

    dec = _decode(src, ww, wh, fps)
    fb = ww * wh * 3
    points, sizes, last = [], [], None
    try:
        i = 0
        while True:
            buf = dec.stdout.read(fb)
            if not buf or len(buf) < fb:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(wh, ww, 3)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Local search once we have a lock; whole frame otherwise.
            if last is not None:
                lx, ly = last
                win = int(TRACK_WINDOW_PX * k) + 20
                x0 = max(0, int(lx * k) - win)
                y0 = max(0, int(ly * k) - win)
                x1 = min(ww, int(lx * k) + win)
                y1 = min(wh, int(ly * k) + win)
            else:
                x0, y0, x1, y1 = 0, 0, ww, wh
            roi = gray[y0:y1, x0:x1]
            best = None
            for s, tg, tm in tmpls:
                th, tw = tg.shape
                if roi.shape[0] <= th or roi.shape[1] <= tw:
                    continue
                # MASKED SQUARED DIFFERENCE, not correlation. See _template:
                # correlation against a bright template saturates on a bright
                # UI. Inverted so higher is better, like every other score
                # here. NaN (a degenerate window) is the worst possible match,
                # never the best.
                surf = cv2.matchTemplate(roi, tg, cv2.TM_SQDIFF_NORMED,
                                         mask=tm)
                surf = 1.0 - np.nan_to_num(surf, nan=1.0, posinf=1.0,
                                           neginf=1.0)
                _mn, mx, _ml, ml = cv2.minMaxLoc(surf)
                if best is None or mx > best[0]:
                    best = (mx, ml, surf, s, th)
            if best and best[0] >= MIN_SCORE:
                score, (mx_, my_), surf, s, th = best
                fx, fy = _subpixel(surf, mx_, my_)
                # matchTemplate reports the template's TOP-LEFT; the pointer's
                # position is its tip.
                tip_x = fx + ARROW_TIP[0] * (th / ARROW_GRID)
                tip_y = fy + ARROW_TIP[1] * (th / ARROW_GRID)
                px = (x0 + tip_x) / k
                py = (y0 + tip_y) / k
                points.append((px, py, float(score)))
                sizes.append(th / k)          # the winning template, full-res
                last = (px, py)
            else:
                points.append(None)
                # One miss is a pointer crossing a busy region; a run of them
                # means it left (or was never there). Drop the lock so the next
                # frame searches the whole picture again.
                if len(points) >= 3 and all(p is None for p in points[-3:]):
                    last = None
            i += 1
            if progress_cb and i % 60 == 0:
                progress_cb(min(0.45, 0.45 * i / float(expected)))
    finally:
        dec.stdout.close()
        dec.wait(timeout=30)
    # The MEASURED pointer height, in full-resolution pixels: the median of
    # the template scale that actually won. This is what makes `scale` mean
    # "N times the size it really is" on a 720p capture and a 4K one alike —
    # deriving it from the frame height instead would be a guess, and a wrong
    # guess draws a 3x cursor that does not cover the 1x one it replaces.
    return {"points": points, "fps": fps, "width": W, "height": H,
            "duration_s": dur, "frames": len(points),
            "cursor_h": float(np.median(sizes)) if sizes else None}


def smooth_track(points, fps, smoothing):
    """Fill the gaps, then one-euro filter. Returns [(x, y) or None].

    Gaps are filled by INTERPOLATION between the surrounding hits, not by
    holding the last one: a pointer that was missed for four frames while it
    crossed a white panel did keep moving, and holding makes it stutter
    exactly where the detector was weakest.
    """
    n = len(points)
    if not n:
        return []
    idx = [i for i, p in enumerate(points) if p is not None]
    if not idx:
        return [None] * n
    filled = [None] * n
    for i in idx:
        filled[i] = (points[i][0], points[i][1])
    # interpolate interior gaps; clamp the ends to the first/last real hit
    for a, b in zip(idx, idx[1:]):
        if b - a <= 1:
            continue
        (xa, ya), (xb, yb) = filled[a], filled[b]
        for j in range(a + 1, b):
            f = (j - a) / float(b - a)
            filled[j] = (xa + (xb - xa) * f, ya + (yb - ya) * f)
    for j in range(0, idx[0]):
        filled[j] = filled[idx[0]]
    for j in range(idx[-1] + 1, n):
        filled[j] = filled[idx[-1]]

    s = max(0.0, min(1.0, float(smoothing)))
    if s <= 0.001:
        return filled
    # smoothing 0 -> no filtering; 1 -> a resting pointer is nailed down.
    # beta stays high enough that a deliberate flick is never smeared.
    min_cutoff = 4.0 * (1.0 - s) + 0.35 * s
    # beta falls off QUADRATICALLY, and that is the whole tuning. At beta 0.09
    # a pointer crossing the screen at 120 px/s raises its own cutoff to ~12Hz
    # and the filter stops doing anything at exactly the speeds a hand shakes
    # at; the square makes high smoothing mean high smoothing while a genuine
    # flick still outruns the filter.
    beta = 0.004 + 0.25 * (1.0 - s) ** 2
    fx = _OneEuro(fps, min_cutoff, beta)
    fy = _OneEuro(fps, min_cutoff, beta)
    return [(fx(p[0]), fy(p[1])) for p in filled]


def _blend(dst, sprite_bgra, x, y):
    """Alpha-composite `sprite_bgra` with its TIP at (x, y). Clipped to the
    frame, so a pointer at the edge draws the part that is on screen."""
    sh, sw = sprite_bgra.shape[:2]
    tipx = int(round(ARROW_TIP[0] * sh / ARROW_GRID))
    tipy = int(round(ARROW_TIP[1] * sh / ARROW_GRID))
    x0, y0 = int(round(x)) - tipx, int(round(y)) - tipy
    H, W = dst.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    w = min(sw - sx0, W - dx0)
    h = min(sh - sy0, H - dy0)
    if w <= 0 or h <= 0:
        return
    src = sprite_bgra[sy0:sy0 + h, sx0:sx0 + w]
    a = (src[:, :, 3:4].astype(np.float32) / 255.0)
    roi = dst[dy0:dy0 + h, dx0:dx0 + w]
    dst[dy0:dy0 + h, dx0:dx0 + w] = (
        roi.astype(np.float32) * (1 - a) + src[:, :, :3].astype(np.float32) * a
    ).astype(np.uint8)


def _erase(frame, x, y, size, inpaint_mask):
    """Repaint the ORIGINAL pointer out around (x, y).

    Small and local: cv2.inpaint over a mask the size of the pointer's own
    bounding box. It has to happen — the redrawn cursor sits at the SMOOTHED
    position, so without this a jittery source shows the real pointer shaking
    beside the smooth one.
    """
    import cv2
    H, W = frame.shape[:2]
    r = int(round(size))
    x0, y0 = max(0, int(x) - 4), max(0, int(y) - 4)
    x1, y1 = min(W, int(x) + r), min(H, int(y) + r)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return frame
    inpaint_mask[:] = 0
    inpaint_mask[y0:y1, x0:x1] = 255
    return cv2.inpaint(frame, inpaint_mask, 3, cv2.INPAINT_TELEA)


def _ripple(frame, x, y, phase, radius_px):
    """One expanding ring at (x, y). phase 0..1."""
    import cv2
    if phase < 0 or phase > 1:
        return
    ease = 1.0 - (1.0 - phase) ** 3          # fast out, settles
    r = int(round(radius_px * (0.25 + ease * RIPPLE_MAX_R * 0.4)))
    if r < 2:
        return
    alpha = 1.0 if phase < 0.45 else max(0.0, 1.0 - (phase - 0.45) / 0.55)
    if alpha <= 0.01:
        return
    layer = frame.copy()
    th = max(2, int(round(radius_px * 0.10)))
    cv2.circle(layer, (int(round(x)), int(round(y))), r, (255, 255, 255),
               th, cv2.LINE_AA)
    cv2.circle(layer, (int(round(x)), int(round(y))), r + th, (20, 20, 20),
               max(1, th // 2), cv2.LINE_AA)
    cv2.addWeighted(layer, alpha, frame, 1 - alpha, 0, frame)


def enhance(src, out_full, out_proxy=None, *, scale=2.0, smoothing=0.5,
            click_highlight=True, click_times=(), progress_cb=None,
            crf=18, preset="veryfast"):
    """Locate, smooth and redraw the pointer into a drop-in replacement source.

    Duration, frame rate and audio are preserved for the same reason
    inpaint.clean_video preserves them: the project's index, transcript and
    every EDL timestamp were measured against the original and must keep
    pointing at the same moment.

    Returns stats including `found_frac`, which the caller REPORTS rather than
    smooths over — a recording with no visible pointer has to say so.
    """
    import cv2
    tr = track(src, progress_cb=progress_cb)
    pts = tr["points"]
    found = sum(1 for p in pts if p is not None)
    found_frac = (found / float(len(pts))) if pts else 0.0
    W, H, fps = tr["width"], tr["height"], tr["fps"]
    smoothed = smooth_track(pts, fps, smoothing)

    # The drawn pointer's height, from what the detector MEASURED (falling
    # back to the platform-standard 26 CSS px at this resolution only when
    # nothing was found — in which case nothing is drawn anyway).
    base_h = tr.get("cursor_h") or max(14.0, 26.0 * (H / 900.0))
    draw_h = base_h * float(scale)
    sp = sprite(draw_h)
    erase_size = base_h * 1.35
    clicks = sorted(float(t) for t in (click_times or []))

    frame_bytes = W * H * 3
    expected = max(1, int(round(tr["duration_s"] * fps)))
    dec = _decode(src, W, H, fps)
    x264p = f"rc-lookahead={config.CLEAN_X264_LOOKAHEAD}:sync-lookahead=0"
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
         "-r", f"{fps:.5f}", "-i", "pipe:0", "-i", src,
         "-map", "0:v:0", "-map", "1:a?",
         "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
         "-threads", str(config.CLEAN_X264_THREADS),
         "-x264-params", x264p,
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out_full],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)

    mask = np.zeros((H, W), np.uint8)
    i, last_frame, drawn = 0, None, 0
    try:
        while True:
            buf = dec.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
            t = i / fps
            raw = pts[i] if i < len(pts) else None
            sm = smoothed[i] if i < len(smoothed) else None
            if sm is not None:
                if raw is not None and float(scale) > 1.001:
                    frame = _erase(frame, raw[0], raw[1], erase_size, mask)
                if click_highlight and clicks:
                    for ct in clicks:
                        ph = (t - ct) / RIPPLE_S
                        if 0.0 <= ph <= 1.0:
                            _ripple(frame, sm[0], sm[1], ph, draw_h)
                _blend(frame, sp, sm[0], sm[1])
                drawn += 1
            last_frame = frame
            enc.stdin.write(frame.tobytes() if frame.flags["C_CONTIGUOUS"]
                            else np.ascontiguousarray(frame).tobytes())
            i += 1
            if progress_cb and i % 30 == 0:
                progress_cb(min(0.99, 0.45 + 0.54 * i / float(expected)))
        # Same tail-pad as clean_video: a picture track that ends before the
        # container does must not shorten the replacement source, or every EDL
        # timestamp past that point falls off the end.
        while last_frame is not None and i < expected:
            enc.stdin.write(np.ascontiguousarray(last_frame).tobytes())
            i += 1
    finally:
        try:
            enc.stdin.close()
        except Exception:
            pass
        dec.stdout.close()
        dec.wait(timeout=30)
        err = enc.stderr.read().decode("utf-8", "replace")[-800:]
        rc = enc.wait(timeout=config.FFMPEG_TIMEOUT_S)
    if rc != 0 or not os.path.exists(out_full):
        raise media.MediaError(f"cursor encode failed: {err or 'no output'}")

    if out_proxy:
        h = config.PROXY_HEIGHT
        media.run(["ffmpeg", "-y", "-v", "error", "-i", out_full,
                   "-vf", rf"scale=-2:min({h}\,floor(ih/2)*2)",
                   "-c:v", "libx264", "-preset", config.PROXY_PRESET,
                   "-crf", str(config.PROXY_CRF),
                   "-threads", str(config.CLEAN_X264_THREADS),
                   "-x264-params", x264p,
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                   "-movflags", "+faststart", out_proxy])
    if progress_cb:
        progress_cb(1.0)
    return {"frames": i, "frames_drawn": drawn,
            "found_frac": round(found_frac, 3),
            "width": W, "height": H, "fps": round(fps, 3),
            "duration_s": round(tr["duration_s"], 3),
            "cursor_px": round(draw_h, 1),
            "clicks": len(clicks)}
