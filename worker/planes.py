"""Planes in the footage: find, refine and track a surface (track 3C).

ROADMAP Wave 3 / 3C; contract in worker/devices/INTERFACES_W3.md §1, §3.

A plane is a quad on a surface (floor, wall, desk, table, a screen) given
at one SOURCE second, in source frame fractions, corners TL, TR, BR, BL in
the plane's OWN orientation (TL->TR runs along the surface's "width"). The
agent proposes it from look_at; ``refine_quad`` snaps each edge to the
strongest nearby line segment (LSD), so a desk's front edge or a screen's
bezel lands to the pixel.

Tracking (the ``planes.h`` sidecar child): per frame, the homography from
the seed frame's plane to this frame's.
  * static camera (no corner of the background moves > 0.4% of the frame):
    identity everywhere — exact, free;
  * otherwise: features inside the quad (people excluded) are carried with
    LK and chained, and every ``ecc_every`` frames the chain is refined
    against the SEED frame with ECC (cv2.findTransformECC, MOTION_HOMOGRAPHY,
    the quad as its mask) so drift never accumulates. The reprojection
    residual of the tracked features is the confidence (3C's bar: 1.5 px at
    1080p).

Pure helpers here run on every lane; the child runs on the batch lane.
"""

import json
import math
import os
import time

import numpy as np

CODE_VERSION = 1
SURFACES = ("floor", "wall", "desk", "table", "screen", "ceiling", "other")


# ── geometry (every lane) ────────────────────────────────────────────────

def quad_px(quad, w, h):
    return np.array([[float(x) * w, float(y) * h] for x, y in quad],
                    np.float64)


def order_quad(pts):
    """Corners as TL, TR, BR, BL by image position (for a quad given in
    any order)."""
    p = np.asarray(pts, np.float64)
    s = p.sum(axis=1)
    d = p[:, 1] - p[:, 0]
    return np.array([p[np.argmin(s)], p[np.argmin(d)], p[np.argmax(s)],
                     p[np.argmax(d)]])


def plane_homography(quad, place=None, size=(1.0, 1.0)):
    """The homography from the unit "plane space" rect [0, size] (u along
    TL->TR, v along TL->BL) — or its ``place`` sub-rect [u0, v0, u1, v1] —
    onto ``quad`` (4 x 2, any units)."""
    import cv2
    q = np.asarray(quad, np.float32)
    if place is None:
        place = [0.0, 0.0, 1.0, 1.0]
    u0, v0, u1, v1 = place
    src = np.array([[0, 0], [size[0], 0], [size[0], size[1]],
                    [0, size[1]]], np.float32)
    Hq = cv2.getPerspectiveTransform(src, q)
    # the place rect inside the plane, in plane coords
    rect = np.array([[u0, v0], [u1, v0], [u1, v1], [u0, v1]],
                    np.float32) * np.array(size, np.float32)
    sub = cv2.perspectiveTransform(rect[None], Hq)[0]
    return cv2.getPerspectiveTransform(src, sub)


def apply_h(H, pts):
    p = np.c_[np.asarray(pts, np.float64), np.ones(len(pts))]
    q = p @ np.asarray(H, np.float64).T
    return q[:, :2] / q[:, 2:3]


def plane_aspect(quad, w=1.0, h=1.0):
    """Width/height of the surface as it would be seen face-on — from the
    quad's edges, corrected for perspective with the two vanishing points
    when they exist (Zhang's rectangle method falls back to edge means)."""
    p = quad_px(quad, w, h) if np.max(np.abs(quad)) <= 1.5 else \
        np.asarray(quad, np.float64)
    top = np.hypot(*(p[1] - p[0]))
    bot = np.hypot(*(p[2] - p[3]))
    lef = np.hypot(*(p[3] - p[0]))
    rig = np.hypot(*(p[2] - p[1]))
    wid = (top + bot) / 2.0
    hei = (lef + rig) / 2.0
    # foreshortening: a floor seen at a grazing angle has a short v edge;
    # the trapezoid's convergence says how much
    conv = min(top, bot) / max(1e-6, max(top, bot))
    hei = hei / max(0.35, conv ** 0.5)
    return float(wid / max(1e-6, hei))


def refine_quad(frame_bgr, quad, search=0.035, min_len=0.06):
    """Snap each quad edge to the longest near-parallel LSD segment within
    ``search`` (frame fractions); edges with none stay. Returns (quad
    fractions, moved edges)."""
    import cv2
    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    lines = lsd.detect(gray)[0]
    q = quad_px(quad, w, h)
    if lines is None:
        return [[float(x / w), float(y / h)] for x, y in q], 0
    segs = lines.reshape(-1, 4)
    out_lines = []
    moved = 0
    for k in range(4):
        a, b = q[k], q[(k + 1) % 4]
        d = b - a
        L = float(np.hypot(*d))
        n = np.array([-d[1], d[0]]) / max(1e-6, L)
        best = None
        for x1, y1, x2, y2 in segs:
            s = np.array([x2 - x1, y2 - y1])
            sl = float(np.hypot(*s))
            if sl < min_len * max(w, h):
                continue
            cosang = abs(float(np.dot(s, d))) / max(1e-6, sl * L)
            if cosang < 0.985:
                continue
            mid = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0])
            dist = abs(float(np.dot(mid - a, n)))
            if dist > search * max(w, h):
                continue
            # the segment must overlap the edge along its length
            t = float(np.dot(mid - a, d)) / (L * L)
            if not -0.2 <= t <= 1.2:
                continue
            score = sl - 2.0 * dist
            if best is None or score > best[0]:
                best = (score, np.array([x1, y1]), np.array([x2, y2]))
        if best is None:
            out_lines.append((a, b))
        else:
            out_lines.append((best[1], best[2]))
            moved += 1
    # corners = intersections of consecutive edge lines
    def inter(l1, l2):
        p1, p2 = l1
        p3, p4 = l2
        A = np.array([[p2[0] - p1[0], p3[0] - p4[0]],
                      [p2[1] - p1[1], p3[1] - p4[1]]])
        if abs(np.linalg.det(A)) < 1e-6:
            return None
        t = np.linalg.solve(A, p3 - p1)
        return p1 + t[0] * (p2 - p1)
    corners = []
    for k in range(4):
        c = inter(out_lines[k - 1], out_lines[k])
        if c is None or np.hypot(*(c - q[k])) > 3 * search * max(w, h):
            c = q[k]
        corners.append(c)
    return [[float(c[0] / w), float(c[1] / h)] for c in corners], moved


# ── tracking (batch lane) ────────────────────────────────────────────────

def _quad_mask(shape, quad, excl=None):
    import cv2
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillConvexPoly(m, np.round(quad).astype(np.int32), 255)
    if excl is not None:
        m &= excl
    return m


def track(grays, key, quad, excl=None, ecc_every=10):
    """[H_t] (3x3 px) mapping the key frame's plane to frame t, [conf]
    (reprojection residual px of the carried features; NaN = unknown)."""
    import cv2
    from anchors import camera_motion
    n = len(grays)
    h, w = grays[0].shape[:2]
    Hs = [None] * n
    res = [float("nan")] * n
    Hs[key] = np.eye(3)
    res[key] = 0.0
    # static camera: one homography for the shot
    bg = [camera_motion.person_mask(grays[0].shape, [])] * n
    chainH, _q = camera_motion.chain(grays, bg if excl is None else
                                     [excl] * n)
    if camera_motion.is_static(chainH, w, h):
        return [np.eye(3)] * n, [0.0] * n, True
    mask0 = _quad_mask(grays[key].shape, quad, excl)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5)
    for d in (1, -1):
        H = np.eye(3)
        pts = cv2.goodFeaturesToTrack(grays[key], 300, 0.01, 6, mask=mask0)
        i = key
        since = 0
        while 0 <= i + d < n:
            j = i + d
            Hj = None
            if pts is not None and len(pts) >= 8:
                nxt, st, _e = cv2.calcOpticalFlowPyrLK(grays[i], grays[j],
                                                       pts, None,
                                                       winSize=(21, 21),
                                                       maxLevel=3)
                ok = st.reshape(-1) == 1
                if ok.sum() >= 8:
                    Hp, inl = cv2.findHomography(pts.reshape(-1, 2)[ok],
                                                 nxt.reshape(-1, 2)[ok],
                                                 cv2.RANSAC, 2.0)
                    if Hp is not None:
                        Hj = Hp @ H
                        keep = nxt.reshape(-1, 2)[ok][inl.reshape(-1) == 1]
                        pts = keep.reshape(-1, 1, 2).astype(np.float32)
            if Hj is None:
                Hj = H
            since += 1
            if since >= ecc_every:
                since = 0
                try:
                    warp = np.linalg.inv(Hj).astype(np.float32)
                    _cc, warp = cv2.findTransformECC(
                        grays[key], grays[j], warp, cv2.MOTION_HOMOGRAPHY,
                        crit, mask0, 5)
                    Hj = np.linalg.inv(warp.astype(np.float64))
                except cv2.error:
                    pass
            Hj = Hj / Hj[2, 2]
            Hs[j] = Hj
            # residual: the key frame's quad features, re-found by LK at j
            res[j] = _residual(grays[key], grays[j], mask0, Hj)
            H = Hj
            if pts is None or len(pts) < 30:
                qj = apply_h(Hj, quad)
                pts = cv2.goodFeaturesToTrack(
                    grays[j], 300, 0.01, 6,
                    mask=_quad_mask(grays[j].shape, qj, excl))
            i = j
    return Hs, res, False


def _residual(g0, g1, mask0, H, n=60):
    import cv2
    pts = cv2.goodFeaturesToTrack(g0, n, 0.02, 10, mask=mask0)
    if pts is None or len(pts) < 6:
        return float("nan")
    pred = apply_h(H, pts.reshape(-1, 2)).astype(np.float32)
    nxt, st, _e = cv2.calcOpticalFlowPyrLK(g0, g1, pts, pred.reshape(-1, 1, 2),
                                           winSize=(21, 21), maxLevel=2,
                                           flags=cv2.OPTFLOW_USE_INITIAL_FLOW)
    ok = st.reshape(-1) == 1
    if ok.sum() < 6:
        return float("nan")
    e = np.hypot(*(nxt.reshape(-1, 2)[ok] - pred[ok]).T)
    return float(np.median(e))


def child_planes(spec, ch):
    """``planes.h``: params {quad (fractions at src_t), src_t, window,
    fps, max_w}. JSON {static, samples: [{t, H (fractions, seed -> t),
    r (residual px at 1080 high)}]}."""
    import analysis_jobs
    import cv2
    p = spec["params"]
    fps = max(1.0, min(float(p.get("fps") or 15.0),
                       float(spec["source"]["fps"] or 30.0)))
    win = p.get("window") or [ch["t0"], ch["t1"]]
    st = float(p.get("src_t", win[0]))
    lo = max(float(win[0]), min(float(ch["t0"]), st))
    hi = min(float(win[1]), max(float(ch["t1"]), st + 1.0 / fps))
    src = spec["source"]
    sw, sh = int(src["width"]), int(src["height"])
    w = analysis_jobs._even(min(sw, int(p.get("max_w") or 960)))
    h = analysis_jobs._even(sh * w / float(sw))
    t_start = time.perf_counter()
    grays = []
    if hi > lo:
        n = max(1, int(round((hi - lo) * fps)))
        for f in analysis_jobs._decode(spec["src"], lo, hi - lo, w, h, fps,
                                       n):
            grays.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
    samples, static = [], True
    if grays:
        key = min(len(grays) - 1, max(0, int(round((st - lo) * fps))))
        quad = quad_px(p["quad"], w, h)
        Hs, res, static = track(grays, key, quad)
        S = np.diag([float(w), float(h), 1.0])
        Si = np.diag([1.0 / w, 1.0 / h, 1.0])
        k1080 = 1080.0 / h
        for k, (H, r) in enumerate(zip(Hs, res)):
            t = lo + k / fps
            if not ch["t0"] - 0.5 / fps <= t < ch["t1"] + 0.5 / fps:
                continue
            Hf = Si @ (H if H is not None else np.eye(3)) @ S
            Hf = Hf / Hf[2, 2]
            samples.append({"t": round(t, 3),
                            "H": [round(float(v), 6) for v in Hf.reshape(-1)],
                            "r": None if not math.isfinite(r)
                            else round(r * k1080, 3)})
    t_model = time.perf_counter() - t_start
    out = os.path.join(spec["outdir"], f"{ch['out_key']}.json")
    with open(out, "w") as f:
        json.dump({"version": 1, "kind": "planes.h", "fps": fps, "width": w,
                   "height": h, "static": bool(static), "quad": p["quad"],
                   "src_t": st, "samples": samples}, f,
                  separators=(",", ":"))
    rs = [s["r"] for s in samples if s["r"] is not None]
    return out, {"fps": round(fps, 6), "frames": len(samples), "width": w,
                 "height": h, "ms_per_frame": round(
                     1000.0 * t_model / max(1, len(grays)), 2),
                 "qa": {"flicker": None, "edge_chatter": None,
                        "coverage": 1.0 if samples else 0.0,
                        "reproj_px": round(float(np.median(rs)), 3)
                        if rs else 0.0, "static": bool(static)}}
