"""Rotated square regions of interest (the MediaPipe landmark convention).

A ROI is ``(cx, cy, size, rot)`` in image pixels: a square of side ``size``
centred on (cx, cy) whose "up" is tilted ``rot`` radians CLOCKWISE as
displayed (MediaPipe's NormalizedRect.rotation sign). The landmark models
see the crop upright; ``to_image`` maps their points back.
"""

import math


def rotation(p0, p1, target_deg):
    """MediaPipe's rotation for a ROI whose vector p0 -> p1 should point at
    ``target_deg`` (90 = up for hands and bodies, 0 = right for a face's
    eye line), wrapped to [-pi, pi)."""
    ang = math.radians(target_deg) - math.atan2(-(p1[1] - p0[1]),
                                                  p1[0] - p0[0])
    return ang - 2.0 * math.pi * math.floor((ang + math.pi) / (2 * math.pi))


def matrix(roi, out):
    """The 2x3 affine image -> crop (``out`` x ``out``) for ``roi``."""
    import cv2
    cx, cy, size, rot = roi
    M = cv2.getRotationMatrix2D((float(cx), float(cy)), math.degrees(rot),
                                float(out) / max(1e-6, float(size)))
    M[0, 2] += out / 2.0 - cx
    M[1, 2] += out / 2.0 - cy
    return M


def crop(img, roi, out, border=0):
    """The upright ``out`` x ``out`` crop of ``roi`` (black outside)."""
    import cv2
    M = matrix(roi, out)
    return cv2.warpAffine(img, M, (int(out), int(out)),
                          flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT,
                          borderValue=(border, border, border)), M


def to_image(points, M):
    """Crop points (N x >=2, pixels) back to image pixels through the
    inverse of ``M``."""
    import cv2
    import numpy as np
    inv = cv2.invertAffineTransform(M)
    p = np.asarray(points, np.float64)[:, :2]
    return p @ inv[:, :2].T + inv[:, 2]


def from_points(points, rot, scale=1.5, shift=(0.0, 0.0), square=True):
    """A ROI around ``points`` (N x 2 pixels) in the frame rotated by
    ``rot``: their bounding box in that frame, shifted by ``shift``
    (fractions of the box, along the rotated axes), scaled by ``scale``,
    squared on the long side (MediaPipe's RectTransformation)."""
    import numpy as np
    p = np.asarray(points, np.float64)[:, :2]
    c, s = math.cos(rot), math.sin(rot)
    # into the ROI's own axes (undo the clockwise tilt: the same rotation
    # cv2.getRotationMatrix2D(+rot) applies to the crop)
    R = np.array([[c, s], [-s, c]])
    q = p @ R.T
    lo, hi = q.min(axis=0), q.max(axis=0)
    w, h = hi[0] - lo[0], hi[1] - lo[1]
    centre = (lo + hi) / 2.0 + np.array([shift[0] * w, shift[1] * h])
    size = max(w, h) if square else (w + h) / 2.0
    cx, cy = centre @ R          # back to image axes (R is orthonormal)
    return (float(cx), float(cy), float(size * scale), float(rot))


def from_box(box, rot, scale=1.5, shift=(0.0, 0.0)):
    """A ROI from an axis-aligned box [x0, y0, x1, y1] (pixels), rotated."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    c, s = math.cos(rot), math.sin(rot)
    # the shift runs along the ROI's own (rotated) axes, as MediaPipe's
    # RectTransformation does
    dx, dy = shift[0] * w, shift[1] * h
    cx = (x0 + x1) / 2.0 + dx * c - dy * s
    cy = (y0 + y1) / 2.0 + dx * s + dy * c
    return (cx, cy, max(w, h) * scale, rot)
