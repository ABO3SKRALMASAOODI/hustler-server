"""Carry one object's mask through a window (3B).

From the keyframe mask (SAM on the prompt) both ways, frame by frame:

  * DIS optical flow (OpenCV core) from the next frame back to the current
    one warps the mask (``remap``); a still object (median flow INSIDE it
    under ``still_px``) keeps its mask exactly — no drift, no shimmer, even
    when the person behind it moves;
  * every ``every`` frames of motion (every ``every_still`` frames when
    still) SAM is asked again with the carried box and the carried mask's
    centroid; its answer replaces the carried mask only when they agree
    (IoU >= ``accept_iou`` and an area within AREA_RANGE): a re-prompt
    never jumps to, or leaks onto, a neighbour;
  * the result keeps a 1-px soft edge.

Bounded by ROADMAP 3B: windows of at most 6 s.
"""

import math

import numpy as np

from .sam import iou, mask_box

WORK_W = 480
# a re-prompt's answer may differ in area from the carried mask by at most
# this much (a leak onto a same-coloured neighbour grows the area)
AREA_RANGE = (0.75, 1.33)
STILL_AREA_RANGE = (0.87, 1.15)      # vs the keyframe's own mask
STILL_ACCEPT_IOU = 0.85
KEY_AREA_RANGE = (0.6, 1.6)          # a moving object vs its keyframe
RIGID_SPREAD_PX = 0.6                # flow spread (work px) of one piece


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
    key_area = max(1.0, float((key_mask > 127).sum()))
    stats = {"reprompts": 0, "accepted": 0, "still_frames": 0}
    key_box = mask_box(key_mask)
    key_pt = _centroid(key_mask)
    for d in (1, -1):
        cur = key_mask
        i = key_index
        since = 0
        # the object's rigid track: the key box and an interior point moved
        # by the median flow inside the mask, re-anchored (shift and scale)
        # on every accepted re-prompt (the re-prompt's prompt — a box read
        # off a carried mask would grow with any smear)
        T_acc = np.eye(3)
        while 0 <= i + d < n:
            j = i + d
            flow = dis.calc(smalls[j], smalls[i], None)   # j -> i
            m_s = cv2.resize(cur, (sw, sh),
                             interpolation=cv2.INTER_NEAREST) > 127
            # the object's OWN motion: flow inside its (eroded) mask, not a
            # ring round it — a still mic in front of a moving speaker must
            # not slide with the speaker
            inner = cv2.erode(m_s.astype(np.uint8), np.ones((3, 3),
                                                            np.uint8)) > 0
            if inner.sum() < 12:
                inner = m_s
            mag = np.hypot(flow[..., 0], flow[..., 1])[inner]
            moving = mag.size > 0 and float(np.median(mag)) >= still_px
            if moving:
                fxi, fyi = flow[..., 0][inner], flow[..., 1][inner]
                mdx, mdy = float(np.median(fxi)), float(np.median(fyi))
                # translation only: a scale fitted to the flow of a small,
                # low-texture object drifts (measured on V03's mic); zoom is
                # re-learned from the re-prompts instead
                T = np.array([[1.0, 0, -mdx * kx], [0, 1.0, -mdy * ky],
                              [0, 0, 1.0]])
                T_acc = T @ T_acc
                pred = (np.c_[np.argwhere(inner)[:, ::-1] * [kx, ky],
                              np.ones(int(inner.sum()))] @ T.T)[:, :2]
                obs = np.argwhere(inner)[:, ::-1] * [kx, ky] - np.c_[
                    fxi * kx, fyi * ky]
                spread = float(np.median(np.hypot(*(pred - obs).T))) / kx
                rigid = spread < RIGID_SPREAD_PX
                if rigid:
                    # moving as one piece: carry the mask whole by the fitted
                    # similarity (a per-pixel warp smears a small object's
                    # edge into its neighbours)
                    warped = cv2.warpAffine(cur, T[:2].astype(np.float32),
                                            (w, h), flags=cv2.INTER_LINEAR,
                                            borderMode=cv2.BORDER_CONSTANT,
                                            borderValue=0)
                else:
                    fx = cv2.resize(flow[..., 0], (w, h)) * kx
                    fy = cv2.resize(flow[..., 1], (w, h)) * ky
                    warped = cv2.remap(cur, gx + fx, gy + fy,
                                       cv2.INTER_LINEAR,
                                       borderMode=cv2.BORDER_CONSTANT,
                                       borderValue=0)
                warped = np.where(warped > 127, 255, 0).astype(np.uint8)
            else:
                warped = cur
                rigid = True
                stats["still_frames"] += 1
            since += 1
            due = every if moving else every_still
            if segmenter is not None and since >= due:
                since = 0
                box = c = None
                if key_box is not None and key_pt is not None:
                    q = np.array([[key_box[0], key_box[1], 1],
                                  [key_box[2], key_box[1], 1],
                                  [key_box[2], key_box[3], 1],
                                  [key_box[0], key_box[3], 1]]) @ T_acc.T
                    box = [q[:, 0].min(), q[:, 1].min(), q[:, 0].max(),
                           q[:, 1].max()]
                    cp = T_acc @ np.array([key_pt[0], key_pt[1], 1.0])
                    c = (cp[0], cp[1])
                if box is not None and c is not None:
                    pad = 0.05 * max(box[2] - box[0], box[3] - box[1])
                    box = [box[0] - pad, box[1] - pad, box[2] + pad,
                           box[3] + pad]
                    cand, _s = segmenter.segment(frames[j], box=box,
                                                 points=[[c[0], c[1], 1]])
                    stats["reprompts"] += 1
                    a_c = float((cand > 127).sum())
                    if moving:
                        ref_a = max(1.0, float((warped > 127).sum()))
                        # one piece moving: its shape is known (the shifted
                        # keyframe answer); a re-prompt must nearly agree
                        lo_a, hi_a, need = AREA_RANGE + (accept_iou,)
                        if not (KEY_AREA_RANGE[0] <= a_c / key_area
                                <= KEY_AREA_RANGE[1]):
                            lo_a = hi_a = -1.0          # refuse
                    else:
                        # a still object: the keyframe's answer stands; a
                        # re-prompt only refines it (never compounds a
                        # leak, one accepted step after another)
                        ref_a = key_area
                        lo_a, hi_a, need = STILL_AREA_RANGE + (
                            max(accept_iou, STILL_ACCEPT_IOU),)
                    if iou(cand, warped) >= need and \
                            lo_a <= a_c / ref_a <= hi_a:
                        warped = cand
                        stats["accepted"] += 1
                        nb = mask_box(cand)
                        if nb is not None and key_box is not None:
                            # re-anchor the rigid track on the answer
                            sc = math.sqrt(max(1e-6, (nb[2] - nb[0])
                                               * (nb[3] - nb[1]))
                                           / max(1e-6, (key_box[2]
                                                        - key_box[0])
                                                 * (key_box[3]
                                                    - key_box[1])))
                            kc = ((key_box[0] + key_box[2]) / 2.0,
                                  (key_box[1] + key_box[3]) / 2.0)
                            nc = ((nb[0] + nb[2]) / 2.0,
                                  (nb[1] + nb[3]) / 2.0)
                            T_acc = np.array([[sc, 0, nc[0] - sc * kc[0]],
                                              [0, sc, nc[1] - sc * kc[1]],
                                              [0, 0, 1.0]])
            masks[j] = warped
            cur = warped
            i = j
    return masks, stats


def soften(mask, px=1.0):
    import cv2
    if px <= 0:
        return mask
    return cv2.GaussianBlur(mask, (0, 0), px)
