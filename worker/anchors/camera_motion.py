"""Camera motion: the frame-to-frame homography of the BACKGROUND (3A).

Features on everything but the people (faces found by YuNet, grown to a
body column — the matte is not readable inside a child, and RANSAC drops
what the column misses) are tracked frame to frame with LK; a RANSAC
homography per pair is chained to the chunk's first frame. ``static`` when
no frame's corners move more than ``static_px`` from where they began:
then every plane on the shot is a single homography (3C's fast path).
Model-free.
"""

import numpy as np


def person_mask(shape, faces, grow_w=1.8):
    """255 where people are NOT (the background), from face boxes px: each
    face grown to a column from just above the head to the frame's foot."""
    h, w = shape[:2]
    m = np.full((h, w), 255, np.uint8)
    for x0, y0, x1, y1 in faces or ():
        fw, fh = x1 - x0, y1 - y0
        cx = (x0 + x1) / 2.0
        a = int(max(0, cx - fw * grow_w))
        b = int(min(w, cx + fw * grow_w))
        top = int(max(0, y0 - 0.5 * fh))
        m[top:h, a:b] = 0
    return m


def pair_h(prev, cur, mask=None, max_pts=400):
    """The homography prev -> cur (3x3) from background features, and the
    inlier share; (identity, 0) when it cannot be measured."""
    import cv2
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=max_pts, qualityLevel=0.01,
                                  minDistance=8, mask=mask, blockSize=7)
    if pts is None or len(pts) < 12:
        return np.eye(3), 0.0
    nxt, st, _e = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None,
                                           winSize=(21, 21), maxLevel=3)
    ok = st.reshape(-1) == 1
    if ok.sum() < 12:
        return np.eye(3), 0.0
    a, b = pts.reshape(-1, 2)[ok], nxt.reshape(-1, 2)[ok]
    H, inl = cv2.findHomography(a, b, cv2.RANSAC, 1.5)
    if H is None:
        return np.eye(3), 0.0
    return H, float(inl.mean()) if inl is not None else 0.0


def chain(frames, masks=None):
    """[H_t] mapping each frame to frame 0 (3x3, pixels), [inlier share]."""
    Hs = [np.eye(3)]
    inl = [1.0]
    acc = np.eye(3)
    for i in range(1, len(frames)):
        H, q = pair_h(frames[i - 1], frames[i],
                      None if masks is None else masks[i - 1])
        # H maps i-1 -> i; we want i -> 0: acc_i = acc_{i-1} @ inv(H)
        try:
            acc = acc @ np.linalg.inv(H)
        except np.linalg.LinAlgError:
            pass
        acc = acc / acc[2, 2]
        Hs.append(acc.copy())
        inl.append(q)
    return Hs, inl


def corner_shift(H, w, h):
    """Max displacement (px) of the frame's corners under H."""
    c = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], np.float64)
    p = c @ H.T
    p = p[:, :2] / p[:, 2:3]
    return float(np.max(np.hypot(*(p - c[:, :2]).T)))


def is_static(Hs, w, h, static_px=None):
    tol = static_px if static_px is not None else 0.004 * max(w, h)
    return all(corner_shift(H, w, h) <= tol for H in Hs)
