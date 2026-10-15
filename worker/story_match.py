"""Story finders over the footage library (Wave 2, track 2E).

Pure functions over library entries ``{key, t0, t1, mid, desc}`` (the
``embed.classic`` descriptors of library.py; the main video is key
``"main"``). Each finder answers an editor's recall question:

  same_framing   the same framing on another day / in another take
  day_night      the same place by day and by night
  shape_chain    circle-centred shots whose circles line up across cuts
  face_aligned   faces whose eyes sit on one line (the accelerating face
                 montage), with the crop that conforms each
  match_action   a movement that continues across a cut (flow direction)

``conform_crop`` turns an alignment into an insert ``crop`` window of the
canvas aspect (InsertItem.crop: one region of the clip as the scene), so a
finder's result is ONE apply_edit_batch of inserts, nothing new to render.
"""

import math

import numpy as np

EYE_LINE_Y = 0.42          # where an aligned eye line sits (frame height)
EYE_DIST = 0.16            # inter-eye distance on the canvas (frame width)
CIRCLE_AT = (0.5, 0.5)
CIRCLE_R = 0.22            # circle radius on the canvas (frame height)


def _cos(a, b):
    a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
    if a.shape != b.shape or not a.size:
        return -1.0
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


# slices of the classic vector (library.describe): luma 8x8, Lab 4x4 x3,
# edge orientations 2x2 x8
LUMA, LAB, EDGES = slice(0, 64), slice(64, 112), slice(112, 144)


def _part(d, sl):
    v = d.get("vec") or []
    return v[sl] if len(v) >= sl.stop else v


def framing_score(a, b):
    """How alike two framings are: the colour layout (where the set's
    palette sits) plus the edge structure (its lines). Measured on the
    40-clip pool (AT 2E-1) it beats luma layout alone, which moves with
    every pose."""
    return _cos(_part(a, LAB), _part(b, LAB)) + \
        _cos(_part(a, EDGES), _part(b, EDGES))


def same_framing(ref, entries, k=5, exclude_key=None, min_gap_s=20.0):
    """Top-k entries framed like ``ref`` (a descriptor). The ref's own clip
    is excluded within ``min_gap_s`` of the query time (``exclude_key`` =
    (key, t))."""
    out = []
    for e in entries:
        d = e.get("desc") or {}
        if exclude_key and e.get("key") == exclude_key[0] and \
                abs(float(e.get("mid", 0)) - float(exclude_key[1])) < \
                min_gap_s:
            continue
        s = framing_score(ref, d)
        # faces in the same places count (the guest's chair is the set)
        fa, fb = ref.get("faces") or [], d.get("faces") or []
        if fa and fb:
            ca = np.mean([[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]
                          for b in fa], axis=0)
            cb = np.mean([[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]
                          for b in fb], axis=0)
            s += 0.15 * max(0.0, 1.0 - 4.0 * float(np.hypot(*(ca - cb))))
        out.append((round(s, 4), e))
    out.sort(key=lambda x: -x[0])
    return out[:k]


def day_night(entries, min_sim=0.8):
    """[(day entry, night entry, similarity)] pairs: one framing, two
    lights."""
    days = [e for e in entries if not (e.get("desc") or {}).get("night")]
    nights = [e for e in entries if (e.get("desc") or {}).get("night")]
    pairs = []
    for n in nights:
        best = None
        for d in days:
            s = framing_score(n["desc"] or {}, d["desc"] or {}) / 2.0
            if s >= min_sim and (best is None or s > best[2]):
                best = (d, n, round(s, 4))
        if best:
            pairs.append(best)
    return sorted(pairs, key=lambda p: -p[2])


def conform_crop(src_w, src_h, canvas_ar, point, size_px, target_xy,
                 target_size, size_axis="w"):
    """An insert crop [x0, y0, x1, y1] (source fractions) of aspect
    ``canvas_ar`` (W/H) that maps the source point ``point`` (px) to
    ``target_xy`` (canvas fractions) and a source length ``size_px`` to
    ``target_size`` (of the canvas width, or height with size_axis 'h').
    None when the window would leave the frame (that shot cannot align)."""
    if size_px <= 0:
        return None
    if size_axis == "w":
        cw_px = size_px / float(target_size)
        ch_px = cw_px / canvas_ar
    else:
        ch_px = size_px / float(target_size)
        cw_px = ch_px * canvas_ar
    x0 = point[0] - target_xy[0] * cw_px
    y0 = point[1] - target_xy[1] * ch_px
    if cw_px > src_w + 0.5 or ch_px > src_h + 0.5 or x0 < -0.5 or \
            y0 < -0.5 or x0 + cw_px > src_w + 0.5 or \
            y0 + ch_px > src_h + 0.5:
        return None
    x0, y0 = max(0.0, x0), max(0.0, y0)
    return [round(x0 / src_w, 4), round(y0 / src_h, 4),
            round(min(1.0, (x0 + cw_px) / src_w), 4),
            round(min(1.0, (y0 + ch_px) / src_h), 4)]


def _max_crop_w(w, h, ar, px, py, ty):
    """The widest crop of aspect ``ar`` that puts source point (px, py) at
    canvas (0.5, ty) and stays inside the frame."""
    return min(w, h * ar, 2 * min(px, w - px), ar * py / max(ty, 1e-6),
               ar * (h - py) / max(1.0 - ty, 1e-6))


MIN_CROP_PX = 300           # a face crop narrower than this is too soft
MAX_EYE_DIST = 0.4


def face_aligned(entries, sizes, canvas_ar, n=10, eye_y=EYE_LINE_Y,
                 eye_dist=None, one_per_key=False):
    """[(entry, crop)] up to ``n`` faces conformed onto ONE eye line: the
    eye midpoint at (0.5, eye_y) and one inter-eye distance (``eye_dist``
    of the canvas width; None = the smallest that every chosen face can
    reach inside its own frame). Level faces only (a tilted head cannot sit
    on a line); faces whose crop would be softer than MIN_CROP_PX are left
    out."""
    cands = []
    for e in entries:
        d = e.get("desc") or {}
        w, h = sizes.get(e["key"], (0, 0))
        if not w or not h:
            continue
        for eyes in (d.get("eyes") or [])[:1]:
            (lx, ly), (rx, ry) = eyes
            dist = math.hypot((rx - lx) * w, (ry - ly) * h)
            tilt = abs(math.degrees(math.atan2((ry - ly) * h,
                                               (rx - lx) * w)))
            if tilt > 8 or dist < 12:
                continue
            mid = (((lx + rx) / 2) * w, ((ly + ry) / 2) * h)
            cw = _max_crop_w(w, h, canvas_ar, mid[0], mid[1], eye_y)
            if cw < MIN_CROP_PX:
                continue
            # the eye distances (canvas-width fractions) this face can take:
            # at least what fits its frame, at most what stays sharp
            lo, hi = dist / cw, min(MAX_EYE_DIST, dist / MIN_CROP_PX)
            if lo <= hi:
                cands.append({"e": e, "dist": dist, "mid": mid, "w": w,
                              "h": h, "lo": lo, "hi": hi})
    if not cands:
        return []
    if eye_dist:
        target = float(eye_dist)
    else:
        # the one eye distance most faces can share (the smallest such:
        # the least zoom)
        best = None
        for t in sorted({c["lo"] for c in cands} | {c["hi"] for c in
                                                    cands}):
            k = sum(1 for c in cands if c["lo"] - 1e-9 <= t <= c["hi"]
                    + 1e-9)
            if best is None or k > best[0]:
                best = (k, t)
        target = best[1]
    fit = [c for c in cands if c["lo"] - 1e-9 <= target <= c["hi"] + 1e-9]
    # sharpest crops first (the most source pixels per canvas pixel), one
    # per clip window
    fit.sort(key=lambda c: -c["dist"])
    chosen, seen = [], set()
    for c in fit:
        k = c["e"]["key"] if one_per_key else (c["e"]["key"],
                                               round(c["e"]["mid"]))
        if k in seen:
            continue
        seen.add(k)
        chosen.append(c)
        if len(chosen) >= n:
            break
    out = []
    for c in chosen:
        crop = conform_crop(c["w"], c["h"], canvas_ar, c["mid"], c["dist"],
                            (0.5, eye_y), target)
        if crop is not None:
            out.append((c["e"], crop))
    return out


def shape_chain(entries, sizes, canvas_ar, min_len=3, n=6,
                at=CIRCLE_AT, radius=None):
    """[(entry, crop)] circle-centred shots (verified Hough circles)
    conformed so every circle sits at ``at`` with ONE radius (``radius``
    of the canvas height; None = the one most shots can share without a
    soft crop): the wheel -> planet -> hatch chain, one shot per look
    (cluster). Fewer than ``min_len``: []."""
    cands = []
    for e in entries:
        d = e.get("desc") or {}
        w, h = sizes.get(e["key"], (0, 0))
        if not w or not h:
            continue
        for cx, cy, r in (d.get("circles") or [])[:2]:
            px, py, rp = cx * w, cy * h, r * h
            cw = _max_crop_w(w, h, canvas_ar, px, py, at[1])
            cw = min(cw, 2 * min(px, w - px) * 1.0)
            ch = cw / canvas_ar
            if cw < MIN_CROP_PX:
                continue
            lo = rp / ch
            hi = min(0.45, rp / (MIN_CROP_PX / canvas_ar))
            if lo <= hi:
                cands.append({"e": e, "p": (px, py), "rp": rp, "w": w,
                              "h": h, "lo": lo, "hi": hi})
            break
    if not cands:
        return []
    if radius:
        target = float(radius)
    else:
        best = None
        for t in sorted({c["lo"] for c in cands} | {c["hi"] for c in
                                                    cands}):
            k = len({(c["e"].get("cluster") or c["e"]["key"]) for c in cands
                     if c["lo"] - 1e-9 <= t <= c["hi"] + 1e-9})
            if best is None or k > best[0]:
                best = (k, t)
        target = best[1]
    fit = [c for c in cands if c["lo"] - 1e-9 <= target <= c["hi"] + 1e-9]
    fit.sort(key=lambda c: -c["rp"])
    out, clusters = [], set()
    for c in fit:
        cl = c["e"].get("cluster") or c["e"]["key"]
        if cl in clusters:
            continue
        crop = conform_crop(c["w"], c["h"], canvas_ar, c["p"], c["rp"], at,
                            target, size_axis="h")
        if crop is None:
            continue
        clusters.add(cl)
        out.append((c["e"], crop))
        if len(out) >= n:
            break
    return out if len(out) >= min_len else []


def flow_direction(frames):
    """Mean optical-flow vector (dx, dy) in frame fractions per frame
    between consecutive gray frames (Farneback at low res)."""
    import cv2
    vs = []
    for a, b in zip(frames, frames[1:]):
        fl = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2,
                                          0)
        vs.append((float(fl[..., 0].mean()) / a.shape[1],
                   float(fl[..., 1].mean()) / a.shape[0]))
    if not vs:
        return (0.0, 0.0)
    return tuple(float(v) for v in np.mean(vs, axis=0))


def match_action(outgoing, candidates, min_speed=0.002):
    """Candidates ranked by how well their opening movement continues the
    outgoing shot's ending movement: [(score, entry)] (cosine of the flow
    vectors, both moving)."""
    ov = outgoing.get("flow_out") or (0.0, 0.0)
    sp = math.hypot(*ov)
    out = []
    for c in candidates:
        cv = c.get("flow_in") or (0.0, 0.0)
        cs = math.hypot(*cv)
        if sp < min_speed or cs < min_speed:
            continue
        cos = (ov[0] * cv[0] + ov[1] * cv[1]) / (sp * cs)
        out.append((round(cos * min(sp, cs) / max(sp, cs), 4), c))
    return sorted(out, key=lambda x: -x[0])


def eye_line_error(eyes_list):
    """Max deviation of the eye midpoints' height from their median
    (fractions of the frame height)."""
    ys = [(l[1] + r[1]) / 2.0 for l, r in eyes_list]
    if not ys:
        return None
    m = float(np.median(ys))
    return max(abs(y - m) for y in ys)
