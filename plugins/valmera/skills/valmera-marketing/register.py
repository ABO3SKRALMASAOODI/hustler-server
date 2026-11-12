"""Where a real Valmera export shows its source picture, measured from the pixels.

A before/after only reads as one when both sides hold the same picture in the same place. This
finds, for one programme second of a delivered short, the rectangle of the frame that is the
source picture, the crop of the source that fills it, and the source frame that is on screen.
Signal processing only: normalised correlation over scale, then a row-by-row agreement test.
"""
import subprocess

import cv2
import numpy as np


def frame(ff, path, t, w=None, h=None):
    """One frame at second t as gray float32 (and its size)."""
    vf = ["-vf", f"scale={w}:{h}"] if w else []
    pr = subprocess.run([ff.replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                         "stream=width,height", "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    sw, sh = [int(x) for x in pr.stdout.strip().split(",")[:2]]
    W, H = (w, h) if w else (sw, sh)
    r = subprocess.run([ff, "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1", *vf, "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8)[: W * H].reshape(H, W).astype(np.float32) / 255.0


def fit(short, raw, scales=None):
    """(scale, ox, oy, score): short_px = raw_px * scale + (ox, oy). Matched features, then a
    similarity transform by consensus, so type and cards laid over the picture do not move it."""
    sift = cv2.SIFT_create(nfeatures=6000)
    a8, b8 = (raw * 255).astype(np.uint8), (short * 255).astype(np.uint8)
    ka, da = sift.detectAndCompute(a8, None)
    kb, db = sift.detectAndCompute(b8, None)
    if da is None or db is None or len(ka) < 12 or len(kb) < 12:
        return 1.0, 0.0, 0.0, 0.0
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(da, db, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.72 * n.distance]
    if len(good) < 12:
        return 1.0, 0.0, 0.0, 0.0
    src = np.float32([ka[m.queryIdx].pt for m in good])
    dst = np.float32([kb[m.trainIdx].pt for m in good])
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.0, maxIters=5000)
    if M is None:
        return 1.0, 0.0, 0.0, 0.0
    s = float(np.hypot(M[0, 0], M[1, 0]))
    k = inl.ravel().astype(bool)
    # no rotation is expected: refit scale and offset alone on the inliers
    cs, cd = src[k].mean(0), dst[k].mean(0)
    s = float(np.sqrt(((dst[k] - cd) ** 2).sum() / max(((src[k] - cs) ** 2).sum(), 1e-6)))
    ox, oy = cd - cs * s
    return s, float(ox), float(oy), float(k.sum()) / max(len(good), 1)


def warp(raw, s, ox, oy, size):
    M = np.float32([[s, 0, ox], [0, s, oy]])
    return cv2.warpAffine(raw, M, size, flags=cv2.INTER_LINEAR, borderValue=-1.0)


def rows(short, warped, tol=0.10):
    """Top and bottom of the source picture in the export: the warped source's own extent, drawn in
    while the export shows something else there (a page, a band). None when the two do not agree."""
    valid = warped >= 0
    ys = np.where(valid.any(1))[0]
    if not len(ys):
        return None
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    d = np.abs(short - np.maximum(warped, 0))
    d[~valid] = 0
    row = cv2.blur((d.sum(1) / np.maximum(valid.sum(1), 1)).reshape(-1, 1), (1, 7)).ravel()
    while y0 < y1 - 1 and row[y0] > tol:
        y0 += 1
    while y1 - 1 > y0 and row[y1 - 1] > tol:
        y1 -= 1
    a = cv2.Sobel(short, cv2.CV_32F, 1, 0, ksize=3)
    b = cv2.Sobel(np.maximum(warped, 0), cv2.CV_32F, 1, 0, ksize=3)
    c = (a * b).sum(1) / (np.sqrt((a * a).sum(1) * (b * b).sum(1)) + 1e-6)
    if y1 - y0 < 40 or float(np.median(c[y0:y1])) < 0.3:
        return None
    return y0, y1


def register(ff, short_path, raw_path, t_short, t_raw, raw_fps=30.0):
    S = frame(ff, short_path, t_short)
    R = frame(ff, raw_path, t_raw)
    s, ox, oy, score = fit(S, R)
    H, W = S.shape
    Wp = warp(R, s, ox, oy, (W, H))
    run = rows(S, Wp)
    if run is None:
        raise RuntimeError(f"the source picture was not found in the export (score {score:.2f})")
    y0, y1 = run
    xs = np.where(Wp[(y0 + y1) // 2] >= 0)[0]
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    # which source frame is on screen: the neighbour that agrees best inside the picture
    best = (1e9, 0)
    for d in range(-3, 4):
        Rd = R if d == 0 else frame(ff, raw_path, t_raw + d / raw_fps)
        Wd = warp(Rd, s, ox, oy, (W, H))
        e = float(np.abs(cv2.Laplacian(S[y0:y1, x0:x1], cv2.CV_32F) - cv2.Laplacian(Wd[y0:y1, x0:x1], cv2.CV_32F)).mean())
        if e < best[0]:
            best = (e, d)
    Hr, Wr = R.shape
    crop = [(x0 - ox) / s / Wr, (y0 - oy) / s / Hr, (x1 - ox) / s / Wr, (y1 - oy) / s / Hr]
    return dict(rect=[x0 / W, y0 / H, x1 / W, y1 / H], crop=[round(max(0.0, min(1.0, c)), 5) for c in crop],
                scale=round(s, 4), score=round(score, 3), frame_offset=best[1], frame=[W, H], raw=[Wr, Hr])
