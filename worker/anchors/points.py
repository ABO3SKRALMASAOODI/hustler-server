"""Point tracks: a seeded point followed through a shot (3A ``track.points``).

A lone pixel is a fragile thing to track, so a seed carries a small patch:
good features within ``radius`` of it are tracked with pyramidal
Lucas-Kanade both ways (forward-backward error over ``fb_max`` px drops a
feature), the patch's motion is the RANSAC similarity of the survivors, and
the seed moves with it. Too few survivors re-seed features round the
current position; none at all ends the track (``ok`` false), and a reader
holds the last pose or degrades (query.py). Model-free; OpenCV core only.
"""

import numpy as np

LK = dict(winSize=(21, 21), maxLevel=3,
          criteria=(3, 30, 0.01))   # cv2.TERM_CRITERIA_EPS | COUNT == 3


def _features(gray, x, y, radius, mask=None):
    import cv2
    h, w = gray.shape[:2]
    m = np.zeros((h, w), np.uint8)
    cv2.circle(m, (int(round(x)), int(round(y))), int(radius), 255, -1)
    if mask is not None:
        m &= mask
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=60, qualityLevel=0.01,
                                  minDistance=3, mask=m, blockSize=5)
    if pts is None or len(pts) < 4:
        # a flat patch: track a small grid round the seed instead
        g = np.array([[x + dx, y + dy] for dx in (-radius / 2, 0, radius / 2)
                      for dy in (-radius / 2, 0, radius / 2)], np.float32)
        return g.reshape(-1, 1, 2)
    return pts.astype(np.float32)


def _step(prev, cur, pts, fb_max):
    import cv2
    nxt, st, _e = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, **LK)
    if nxt is None:
        return None, None
    back, st2, _e2 = cv2.calcOpticalFlowPyrLK(cur, prev, nxt, None, **LK)
    fb = np.linalg.norm((back - pts).reshape(-1, 2), axis=1)
    good = (st.reshape(-1) == 1) & (st2.reshape(-1) == 1) & (fb < fb_max)
    return nxt, good


def track(frames, seed_index, x, y, radius=14.0, fb_max=1.2, min_keep=5):
    """Track one point through ``frames`` (gray uint8 list) from
    ``frames[seed_index]`` at (x, y) px, both directions. Returns a list
    (one per frame) of (x, y, ok)."""
    import cv2
    n = len(frames)
    out = [None] * n
    out[seed_index] = (float(x), float(y), True)
    for direction in (1, -1):
        px, py = float(x), float(y)
        pts = _features(frames[seed_index], px, py, radius)
        alive = True
        i = seed_index
        while 0 <= i + direction < n:
            j = i + direction
            if not alive:
                out[j] = (px, py, False)
                i = j
                continue
            nxt, good = _step(frames[i], frames[j], pts, fb_max)
            if nxt is None or int(good.sum()) < 3:
                alive = False
                out[j] = (px, py, False)
                i = j
                continue
            a = pts.reshape(-1, 2)[good]
            b = nxt.reshape(-1, 2)[good]
            M, inl = cv2.estimateAffinePartial2D(
                a, b, method=cv2.RANSAC, ransacReprojThreshold=1.5)
            if M is None:
                d = np.median(b - a, axis=0)
                px, py = px + float(d[0]), py + float(d[1])
            else:
                px, py = (float(M[0, 0] * px + M[0, 1] * py + M[0, 2]),
                          float(M[1, 0] * px + M[1, 1] * py + M[1, 2]))
            keep = b
            if inl is not None:
                keep = b[inl.reshape(-1) == 1]
            if len(keep) < min_keep:
                pts = _features(frames[j], px, py, radius)
            else:
                pts = keep.reshape(-1, 1, 2).astype(np.float32)
            out[j] = (px, py, True)
            i = j
    return out
