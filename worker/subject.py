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

import math
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


def _distinct(hits):
    """``hits`` (biggest first) without boxes that mostly overlap a bigger
    one: the frontal and profile cascades both firing on one face are one
    face, not a group shot."""
    out = []
    for x, y, w, h in hits:
        for X, Y, W, H in out:
            ix = max(0, min(x + w, X + W) - max(x, X))
            iy = max(0, min(y + h, Y + H) - max(y, Y))
            if ix * iy > .3 * min(w * h, W * H):
                break
        else:
            out.append((x, y, w, h))
    return out


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
        faces = _distinct(_faces_in(cv2, cascades, gray)) if cascades else []
        if not faces and len(cascades) > 1:
            # The profile cascade finds faces turned screen-LEFT; a speaker
            # turned the other way (toward a slide, an interviewer) is found
            # on the mirrored frame — Oct 2026, a Thiel lecture fell back to
            # pad_blur because no frame of him turned right was "a face".
            flip = cv2.flip(gray, 1)
            faces = _distinct([(w - x - fw, y, fw, fh) for x, y, fw, fh
                               in _faces_in(cv2, cascades[1:], flip)])
        if len(faces) == 1:
            # Face size alone cannot identify the speaker in a group shot.
            x, y, fw, fh = faces[0]
            px = (x + fw / 2.0) / w
            py = (y + fh / 2.0 - fh * _EYELINE_LIFT) / h
            if _EDGE_GUARD <= px <= 1 - _EDGE_GUARD and \
                    _EDGE_GUARD <= py <= 1 - _EDGE_GUARD:
                face_pts.append((round(px, 4), round(py, 4)))
                continue
        ex, ey = _energy_point(np, gray)
        energy_pts.append((round(ex, 4), round(ey, 4)))

    # Faces win only when they were found in a meaningful share of the
    # samples. This threshold used to be `max(1, len(paths) // 3)`, which for
    # the 5 frames auto_reframe samples evaluates to ONE — so a single Haar
    # false positive was enough to declare "a person is in this video". That
    # is not hypothetical: a Mobile Legends gameplay recording, which contains
    # no faces at all, matched in 1 of 5 frames and aimed a 9:16 crop at
    # (0.39, 0.20) — the top-left corner of a HUD. A cascade's false-positive
    # rate is per-frame and independent; a real face in a video someone wants
    # reframed is in most of the frames, so requiring two agreeing detections
    # costs a genuine subject nothing and costs a phantom everything.
    quorum = 1 if len(paths) < 3 else max(2, len(paths) // 3)
    if face_pts and len(face_pts) >= quorum and spread(face_pts) <= 0.5:
        return face_pts, "faces"
    if energy_pts:
        return energy_pts, "energy"
    return (face_pts, "faces") if face_pts else ([], None)


def crop_detail_kept(paths, out_w, out_h, focus=None, max_width=640):
    """How much of the picture's DETAIL survives a crop to out_w:out_h.

    Round 55. A crop is the right way to change aspect only when the frame has
    a subject to follow. When it does not, "reframing" 16:9 to 9:16 is just
    truncation: the crop window is 31.6% of the source width, and the other
    68% — on a game or a screen recording, that is the HUD, the minimap, the
    score, the entire UI — is simply gone. A real user delivered a wide Mobile
    Legends recording, got back a cropped 9:16, and described it exactly: "it
    is not adjusting the video to the dimensions, it is just truncating it."

    So measure it rather than assume. Gradient energy is already this module's
    proxy for where the content is (see _energy_point); integrating it over
    the crop window answers the only question that matters — would a crop cut
    off things the viewer needs? A centre-weighted shot (a person talking)
    keeps most of its detail through a vertical crop. A shot whose detail runs
    edge to edge does not, and that is the footage that must be FITTED into
    the new frame instead of cut down to it.

    Returns a fraction 0-1 (1.0 = the crop discards nothing, which is also
    what a widening or unchanged aspect returns), or None when nothing could
    be measured. `focus` is the (x, y) the crop would be aimed at; None means
    centre.
    """
    cv2 = _cv2()
    if cv2 is None or not paths or not out_w or not out_h:
        return None
    try:
        import numpy as np
    except Exception:
        return None
    fx, fy = (focus or (0.5, 0.5))
    kept = []
    for p in paths:
        try:
            img = cv2.imread(p)
        except Exception:
            img = None
        if img is None:
            continue
        h, w = img.shape[:2]
        if w > max_width:
            try:
                img = cv2.resize(img, (max_width,
                                       max(1, int(h * max_width / float(w)))))
            except Exception:
                pass
            h, w = img.shape[:2]
        try:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        except Exception:
            continue
        g = gray.astype("float32")
        gx = abs(g[:, 2:] - g[:, :-2])
        gy = abs(g[2:, :] - g[:-2, :])
        e = gx[1:-1, :] ** 2 + gy[:, 1:-1] ** 2
        total = float(e.sum())
        if total <= 0:
            continue
        eh, ew = e.shape
        # The crop window the renderer would take: the largest out_w:out_h
        # rectangle that fits, positioned on the focus point and clamped
        # fully inside the frame. Mirrors renderer.frame_dims' geometry.
        want = float(out_w) / float(out_h)
        have = ew / float(eh)
        if want < have:                      # narrower output: cut the sides
            cw, ch = eh * want, float(eh)
        else:                                # wider output: cut top/bottom
            cw, ch = float(ew), ew / want
        x0 = min(max(fx * ew - cw / 2.0, 0.0), max(0.0, ew - cw))
        y0 = min(max(fy * eh - ch / 2.0, 0.0), max(0.0, eh - ch))
        win = e[int(y0):int(y0 + ch), int(x0):int(x0 + cw)]
        kept.append(min(1.0, float(win.sum()) / total))
    if not kept:
        return None
    return round(sorted(kept)[len(kept) // 2], 3)


# Hard-edged bands (judges, Oct 2026). A burned-in browser inset left an
# 8%-wide sliver down a 9:16 crop's edge for 9 s, and an archival door frame
# a grey strip down another: both read as a render glitch. What insets,
# screens, window/door frames and letterbox borders share — and faces,
# bodies and moving content do not — is a long, perfectly straight edge
# that sits in the SAME column across the shot. Measured on the jpegs the
# reframe already pulls; no detector model, no extra decode.
_LINE_WORK_W = 360        # detection width (positions are fractions)
_LINE_STEP = 22           # luma step across the edge (0-255, 2 px apart)
_LINE_MIN_RUN = 0.33      # straight run as a share of the frame's height;
                          # natural background edges measured 0.22-0.31,
                          # the inset 0.39, the door frame 0.53-0.95
_LINE_TOL = 0.012         # same column across frames (fraction of width)


def _longest_runs(mask):
    """Per column: the longest unbroken vertical run of True in `mask`
    (rows x cols), as a row count."""
    import numpy as np
    best = np.zeros(mask.shape[1], np.int32)
    cur = np.zeros(mask.shape[1], np.int32)
    for row in mask:
        cur = np.where(row, cur + 1, 0)
        np.maximum(best, cur, out=best)
    return best


def _frame_lines(cv2, np, gray):
    """[(position, run)] of straight vertical edges in one grey frame."""
    h, w = gray.shape[:2]
    g = gray.astype(np.float32)
    d = np.zeros_like(g)
    d[:, 1:-1] = g[:, 2:] - g[:, :-2]
    found = []
    for sign in (1.0, -1.0):
        raw = (d * sign) > _LINE_STEP
        # One pixel of column jitter (antialiasing, chroma bleed) is still
        # a straight edge; one row of gap is still the same edge.
        edge = raw.copy()
        edge[:, 1:] |= raw[:, :-1]
        edge[:, :-1] |= raw[:, 1:]
        edge[1:-1, :] |= edge[:-2, :] & edge[2:, :]
        runs = _longest_runs(edge)
        for c in range(2, w - 2):
            if runs[c] >= _LINE_MIN_RUN * h and \
                    runs[c] == runs[c - 2:c + 3].max():
                found.append((c / float(w), runs[c] / float(h)))
    found.sort(key=lambda f: -f[1])
    kept = []
    for pos, run in found:
        if all(abs(pos - k[0]) > _LINE_TOL for k in kept):
            kept.append((pos, run))
    return kept


def hard_edge_lines(paths, axis="x"):
    """Persistent straight edges across `paths`: sorted [(position, run)].

    axis 'x' finds vertical lines (position = fraction of the width, the
    edges a side crop can leave a sliver of); 'y' finds horizontal ones
    (position = fraction of the height). A line counts only when it sits in
    the same place in most readable frames — a static inset or frame edge,
    not a moving arm. Unreadable frames are skipped; nothing readable
    returns []."""
    cv2 = _cv2()
    if cv2 is None or not paths:
        return []
    try:
        import numpy as np
    except Exception:
        return []
    per_frame = []
    for p in paths:
        try:
            img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        except Exception:
            img = None
        if img is None:
            continue
        if axis == "y":
            img = img.T
        h, w = img.shape[:2]
        if w > _LINE_WORK_W:
            try:
                img = cv2.resize(img, (_LINE_WORK_W,
                                       max(1, int(h * _LINE_WORK_W / w))),
                                 interpolation=cv2.INTER_AREA)
            except Exception:
                continue
        per_frame.append(_frame_lines(cv2, np, img))
    n = len(per_frame)
    if not n:
        return []
    need = n if n <= 2 else max(2, int(math.ceil(0.6 * n)))
    out = []
    for lines in per_frame:
        for pos, _run in lines:
            if any(abs(pos - o[0]) <= _LINE_TOL for o in out):
                continue
            hits = [next(r for q, r in other if abs(q - pos) <= _LINE_TOL)
                    for other in per_frame
                    if any(abs(q - pos) <= _LINE_TOL for q, _r in other)]
            if len(hits) >= need:
                out.append((round(pos, 4), round(sorted(hits)[len(hits) // 2],
                                                 3)))
    return sorted(out)


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
