"""Carry one object's mask through a window (3B).

From the keyframe mask (SAM on the prompt) both ways, frame by frame:

  * DIS optical flow (OpenCV core) from the next frame back to the current
    one warps the mask (``remap``); a still object (median flow over it
    under ``still_px``) keeps its mask exactly — no drift, no shimmer;
  * every ``every`` frames of motion (every ``every_still`` frames when
    still) SAM is asked again with the carried box and the carried mask's
    centroid; its answer replaces the carried mask only when they agree
    (IoU >= ``accept_iou``): a re-prompt never jumps to a neighbour;
  * the result keeps a 1-px soft edge.

Bounded by ROADMAP 3B: windows of at most 6 s.
"""

import numpy as np

from .sam import iou, mask_box

WORK_W = 480


def _small(frame):
    import cv2
    h, w = frame.shape[:2]
    sw = min(WORK_W, w)
    sh = max(2, int(round(h * sw / float(w))))
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (sw, sh), interpolation=cv2.INTER_AREA)


def _centroid(mask):
    ys, xs = np.where(mask > 127)
    if xs.size == 0:
        return None
    # the interior point nearest the centroid (a hollow object's centroid
    # can sit outside it)
    cx, cy = xs.mean(), ys.mean()
    k = int(np.argmin((xs - cx) ** 2 + (ys - cy) ** 2))
    return float(xs[k]), float(ys[k])


def propagate(frames, key_index, key_mask, segmenter=None, every=4,
              every_still=30, accept_iou=0.55, still_px=0.15):
    """Masks (uint8 0/255, frame size) for every frame in ``frames`` (BGR),
    from ``key_mask`` on ``frames[key_index]``. Returns (masks, stats)."""
    import cv2
    n = len(frames)
    h, w = frames[0].shape[:2]
    smalls = [_small(f) for f in frames]
    sh, sw = smalls[0].shape[:2]
    kx, ky = w / float(sw), h / float(sh)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    masks = [None] * n
    masks[key_index] = key_mask
    stats = {"reprompts": 0, "accepted": 0, "still_frames": 0}
    for d in (1, -1):
        cur = key_mask
        i = key_index
        since = 0
        while 0 <= i + d < n:
            j = i + d
            flow = dis.calc(smalls[j], smalls[i], None)   # j -> i
            m_s = cv2.resize(cur, (sw, sh),
                             interpolation=cv2.INTER_NEAREST) > 127
            ring = cv2.dilate(m_s.astype(np.uint8), np.ones((9, 9),
                                                            np.uint8)) > 0
            mag = np.hypot(flow[..., 0], flow[..., 1])[ring]
            moving = mag.size > 0 and float(np.median(mag)) >= still_px
            if moving:
                fx = cv2.resize(flow[..., 0], (w, h)) * kx
                fy = cv2.resize(flow[..., 1], (w, h)) * ky
                warped = cv2.remap(cur, gx + fx, gy + fy, cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_CONSTANT,
                                   borderValue=0)
                warped = np.where(warped > 127, 255, 0).astype(np.uint8)
            else:
                warped = cur
                stats["still_frames"] += 1
            since += 1
            due = every if moving else every_still
            if segmenter is not None and since >= due:
                since = 0
                box = mask_box(warped)
                c = _centroid(warped)
                if box is not None and c is not None:
                    pad = 0.05 * max(box[2] - box[0], box[3] - box[1])
                    box = [box[0] - pad, box[1] - pad, box[2] + pad,
                           box[3] + pad]
                    cand, _s = segmenter.segment(frames[j], box=box,
                                                 points=[[c[0], c[1], 1]])
                    stats["reprompts"] += 1
                    if iou(cand, warped) >= accept_iou:
                        warped = cand
                        stats["accepted"] += 1
            masks[j] = warped
            cur = warped
            i = j
    return masks, stats


def soften(mask, px=1.0):
    import cv2
    if px <= 0:
        return mask
    return cv2.GaussianBlur(mask, (0, 0), px)
