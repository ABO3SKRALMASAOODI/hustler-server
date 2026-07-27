"""Where the subject is, measured from the pixels — no vision model.

Round 52. auto_reframe's whole promise is "it measures where the subject
actually sits and centers the crop there", and on 2026-07-27 it could not keep
it for a single user: the vision provider was unconfigured, so every 9:16
reframe fell through to a DEAD-CENTRE crop and the agent had to append the same
apology five times — "sin modelo de visión no pude detectar al sujeto
automáticamente; si el orador no está centrado, avísame". A vertical crop of a
landscape frame throws away 44% of the width, so "centred" is not a mild
degradation; it is the difference between a speaker in frame and a speaker's
shoulder in frame.

A face is not a thing that needs a language model to find. OpenCV ships Haar
cascades in the wheel this image already installs (inpaint.py and cursor.py
both use cv2), they run in milliseconds on a downscaled frame, and on the
footage people actually reframe — a person talking — they are MORE reliable
than asking a multimodal model to estimate a coordinate. So faces are measured
first, always, whether or not a vision provider exists; vision becomes the
fallback for footage with no face in it, and a gradient-energy centroid is the
last resort, which still beats the middle of the frame.

Pure measurement: returns points, never writes an EDL.
"""

import os

# Fractions of the frame. A detection this far out is almost always a false
# positive (a face-shaped patch of background at the very edge).
_EDGE_GUARD = 0.02
# A face's centre is its geometric middle; a head sits slightly above it and
# the eyes higher still. Cropping on the eyeline is what portrait framing
# means, so the point is nudged UP by this fraction of the face box's height.
_EYELINE_LIFT = 0.12


def _cv2():
    try:
        import cv2
        return cv2
    except Exception:
        return None


def _cascades(cv2):
    """Frontal + profile detectors, or [] when the data files are missing."""
    out = []
    try:
        base = cv2.data.haarcascades
    except Exception:
        return out
    for name in ("haarcascade_frontalface_default.xml",
                 "haarcascade_profileface.xml"):
        path = os.path.join(base, name)
        if not os.path.exists(path):
            continue
        try:
            c = cv2.CascadeClassifier(path)
            if not c.empty():
                out.append(c)
        except Exception:
            continue
    return out


def _faces_in(cv2, cascades, gray):
    """[(x, y, w, h)] in `gray` pixel coordinates, biggest first."""
    hits = []
    for c in cascades:
        try:
            found = c.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=6,
                                       minSize=(max(24, gray.shape[1] // 20),
                                                max(24, gray.shape[0] // 20)))
        except Exception:
            continue
        for (x, y, w, h) in found:
            hits.append((int(x), int(y), int(w), int(h)))
    hits.sort(key=lambda b: -(b[2] * b[3]))
    return hits


def _energy_point(np, gray):
    """Centroid of gradient energy: where the DETAIL is.

    Backgrounds are smooth (a wall, sky, bokeh, a blurred room) and subjects
    are not, so the detail centroid lands on the subject far more often than
    the frame centre does. Squaring the magnitude sharpens that preference;
    the centre prior keeps a busy edge from dragging the crop off a subject
    that really is centred.
    """
    g = gray.astype("float32")
    gx = abs(g[:, 2:] - g[:, :-2])
    gy = abs(g[2:, :] - g[:-2, :])
    e = gx[1:-1, :] ** 2 + gy[:, 1:-1] ** 2
    if e.sum() <= 0:
        return 0.5, 0.5
    h, w = e.shape
    ys, xs = np.arange(h) + 1.0, np.arange(w) + 1.0
    cx = float((e.sum(axis=0) * xs).sum() / e.sum()) / (w + 2)
    cy = float((e.sum(axis=1) * ys).sum() / e.sum()) / (h + 2)
    # 65% measurement, 35% centre — enough to move the crop meaningfully,
    # not enough to slam it into a corner on a noisy frame.
    return 0.5 + 0.65 * (cx - 0.5), 0.5 + 0.65 * (cy - 0.5)


def points_from_frames(paths, max_width=640):
    """Measure the subject in each frame.

    Returns (points, method) where points is [(x, y)] in frame fractions and
    method is 'faces' | 'energy' | None. Frames that cannot be read are simply
    skipped — a partial measurement is still a measurement.
    """
    cv2 = _cv2()
    if cv2 is None or not paths:
        return [], None
    try:
        import numpy as np
    except Exception:
        return [], None
    cascades = _cascades(cv2)
    face_pts, energy_pts = [], []
    for p in paths:
        img = None
        try:
            img = cv2.imread(p)
        except Exception:
            img = None
        if img is None:
            continue
        h, w = img.shape[:2]
        if w > max_width:                       # detection is scale-invariant
            scale = max_width / float(w)
            try:
                img = cv2.resize(img, (max_width, max(1, int(h * scale))))
            except Exception:
                pass
            h, w = img.shape[:2]
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)
        except Exception:
            continue
        faces = _faces_in(cv2, cascades, gray) if cascades else []
        if faces:
            # The biggest face is the subject. Several faces in one frame is a
            # group shot, and framing on the largest is what a human does.
            x, y, fw, fh = faces[0]
            px = (x + fw / 2.0) / w
            py = (y + fh / 2.0 - fh * _EYELINE_LIFT) / h
            if _EDGE_GUARD <= px <= 1 - _EDGE_GUARD and \
                    _EDGE_GUARD <= py <= 1 - _EDGE_GUARD:
                face_pts.append((round(px, 4), round(py, 4)))
                continue
        ex, ey = _energy_point(np, gray)
        energy_pts.append((round(ex, 4), round(ey, 4)))

    # Faces win whenever they were found in a meaningful share of the samples:
    # one lucky detection across ten frames is noise, but half of them is a
    # person who is in this video.
    if face_pts and len(face_pts) >= max(1, len(paths) // 3):
        return face_pts, "faces"
    if energy_pts:
        return energy_pts, "energy"
    return (face_pts, "faces") if face_pts else ([], None)


def median_point(points):
    """The median x and median y of measured points.

    Median, not mean: one wide establishing shot must not drag the crop off
    every close-up. (The same reason the vision path medians its answers.)
    """
    if not points:
        return None
    xs = sorted(p[0] for p in points)
    ys = sorted(p[1] for p in points)
    return (round(xs[len(xs) // 2], 3), round(ys[len(ys) // 2], 3))


def spread(points):
    """How far the subject travels across the samples, as a fraction of the
    frame — max minus min on the wider-moving axis. A big spread means one
    fixed focus point cannot hold the subject, which is a thing to SAY rather
    than hide."""
    if len(points) < 2:
        return 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return round(max(max(xs) - min(xs), max(ys) - min(ys)), 3)
