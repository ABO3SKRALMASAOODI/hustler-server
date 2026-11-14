"""Temporal object support for compositing mattes, without hardening soft edges.

RVM's recurrence supplies alpha and motion-blur detail. EfficientSAM excludes
background objects RVM can attach to a person. Optical flow carries that support
between fresh segmentations; no screen-space average trails a moving limb.
"""
import numpy as np

VERSION = 'sam-support-v1'


def support(mask, cv2):
    """Fill small enclosed segmentation holes; retain genuine large openings.

The support is deliberately wider than the silhouette. It is a background
rejection signal, never the final alpha of a face, hair or a blurred hand.
"""
    m = (mask > 127).astype(np.uint8)
    h, w = m.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(1 - m, 8)
    limit = max(16, int(h * w * .004))
    for k in range(1, n):
        x, y, bw, bh, area = stats[k]
        if x > 0 and y > 0 and x + bw < w and y + bh < h and area <= limit:
            m[labels == k] = 1
    radius = max(2, int(round(max(h, w) * .009)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    return cv2.GaussianBlur(cv2.dilate(m.astype(np.float32), kernel), (5, 5), 0)


class Refiner:
    def __init__(self, path, threads=4):
        from objseg.sam import Segmenter
        import cv2
        self.seg = Segmenter(path, threads=threads)
        self.flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        self.previous = self.mask = None
        self.n = 0
        self.accepted = self.rejected = 0

    def step(self, frame, alpha, cv2):
        from objseg.sam import mask_box
        h, w = alpha.shape
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sw = min(480, w)
        small = cv2.resize(gray, (sw, max(2, round(h * sw / w))))
        carried = None
        if self.previous is not None and self.mask is not None:
            flow = self.flow.calc(small, self.previous, None)
            flow = cv2.resize(flow, (w, h))
            flow[..., 0] *= w / small.shape[1]
            flow[..., 1] *= h / small.shape[0]
            x, y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
            carried = cv2.remap(self.mask, x + flow[..., 0], y + flow[..., 1], cv2.INTER_LINEAR)
        current = carried
        if self.n % 4 == 0 or current is None:
            box = mask_box((alpha * 255).astype(np.uint8), thr=64)
            if box:
                mask, score = self.seg.segment(frame, box=box)
                candidate = support(mask, cv2)
                # An uncertain answer or one losing most people cannot erase them.
                retained = float((alpha * candidate).sum()) / max(1., float(alpha.sum()))
                if score >= .65 and retained >= .65:
                    current = candidate if carried is None else .8 * candidate + .2 * carried
                    self.accepted += 1
                else:
                    self.rejected += 1
                    current = None  # no stale cutout over a newly entering person
        self.previous, self.mask = small, current
        self.n += 1
        return alpha if current is None else np.clip(alpha * current, 0, 1)
