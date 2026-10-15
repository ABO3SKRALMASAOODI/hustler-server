"""EfficientSAM-Ti: one object mask from a box and/or points (3B).

The opencv_zoo export (image_segmentation_efficientsam_ti_2025april.onnx,
Apache-2.0) takes a 1024 x 1024 RGB image in [0, 1], up to 6 prompt points
with labels (1 foreground, 0 background, 2 / 3 a box's top-left /
bottom-right, -1 padding) and returns 3 candidate masks with predicted
IoUs. We run it on a SQUARE CROP round the prompt (the box grown 1.7x, at
least ``min_crop`` px): a bottle that is 5% of the frame gets 1024 px of
the model's attention instead of 50.
"""

import numpy as np

from anchors import models

SIZE = 1024
MAX_POINTS = 6


class Segmenter:
    def __init__(self, path, threads=0):
        self.sess = models.onnx_session(path, threads)

    def _run(self, rgb, pts, labels):
        import cv2
        im = cv2.resize(rgb, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
        blob = (im.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        P = np.zeros((1, 1, MAX_POINTS, 2), np.float32)
        L = np.full((1, 1, MAX_POINTS), -1.0, np.float32)
        h, w = rgb.shape[:2]
        for k, ((x, y), lab) in enumerate(list(zip(pts, labels))[
                :MAX_POINTS]):
            P[0, 0, k] = [x * SIZE / w, y * SIZE / h]
            L[0, 0, k] = lab
        masks, ious = self.sess.run(None, {"batched_images": blob,
                                           "batched_point_coords": P,
                                           "batched_point_labels": L})
        return masks[0, 0], ious[0, 0]

    def segment(self, frame_bgr, box=None, points=None, min_crop=256,
                grow=1.7):
        """(mask uint8 0/255 at frame size, score) for a box [x0, y0, x1,
        y1] px and/or points [[x, y, 1|0], ...] px."""
        import cv2
        h, w = frame_bgr.shape[:2]
        pts_all = [(float(p[0]), float(p[1])) for p in points or []]
        if box is not None:
            bx = [float(v) for v in box]
        elif pts_all:
            xs, ys = [p[0] for p in pts_all], [p[1] for p in pts_all]
            r = max(24.0, 0.08 * max(w, h))
            bx = [min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r]
        else:
            raise ValueError("a segmentation needs a box or points")
        cx, cy = (bx[0] + bx[2]) / 2.0, (bx[1] + bx[3]) / 2.0
        side = max(min_crop, grow * max(bx[2] - bx[0], bx[3] - bx[1]))
        side = min(side, float(max(w, h)))
        x0 = int(round(min(max(cx - side / 2.0, 0.0), max(0.0, w - side))))
        y0 = int(round(min(max(cy - side / 2.0, 0.0), max(0.0, h - side))))
        x1, y1 = min(w, int(round(x0 + side))), min(h, int(round(y0 + side)))
        crop = cv2.cvtColor(frame_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
        pts, labels = [], []
        if box is not None:
            pts += [(bx[0] - x0, bx[1] - y0), (bx[2] - x0, bx[3] - y0)]
            labels += [2, 3]
        for p in points or []:
            pts.append((float(p[0]) - x0, float(p[1]) - y0))
            labels.append(1 if (len(p) < 3 or p[2]) else 0)
        masks, ious = self._run(crop, pts, labels)
        k = int(np.argmax(ious))
        m = (masks[k] > 0).astype(np.uint8) * 255
        ch, cw = y1 - y0, x1 - x0
        m = cv2.resize(m, (cw, ch), interpolation=cv2.INTER_LINEAR)
        full = np.zeros((h, w), np.uint8)
        full[y0:y1, x0:x1] = m
        return full, float(ious[k])


def mask_box(mask, thr=127):
    """[x0, y0, x1, y1] px of a mask's pixels over ``thr``, or None."""
    ys, xs = np.where(mask > thr)
    if xs.size == 0:
        return None
    return [float(xs.min()), float(ys.min()), float(xs.max() + 1),
            float(ys.max() + 1)]


def iou(a, b, thr=127):
    A, B = a > thr, b > thr
    u = np.logical_or(A, B).sum()
    return float(np.logical_and(A, B).sum()) / float(u) if u else 0.0
