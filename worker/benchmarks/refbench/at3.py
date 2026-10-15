"""Wave 3 acceptance (AT-3A .. AT-3F): measured on the Valmera Footage
Library with sidecars measured locally by the batch-lane children
(benchmarks.refbench.w3; ``VALMERA_SIDECAR_DIR`` a LocalStore).

    cd worker && python -m benchmarks.refbench.at3 AT-3A-2 [AT-3B-1 ...]
    cd worker && python -m benchmarks.refbench.at3 all

Each AT is a function returning {"id", "metrics": {name: {value, bar, pass}},
"detail", "board": [paths], "status"} — ``status`` is ``pass`` | ``fail`` |
``partial`` | ``blocked`` (footage the owner must shoot: HC-x) | ``not run``.
No human labels exist for this footage, so where the roadmap's bar is an
error against ground truth the harness says exactly what stands in for it
(e.g. test-retest on the mirrored frame, IoU against a per-frame
re-segmentation from the same prompt) — never a number it cannot back.

Internal only: references, fixtures, renders and boards never enter git or
product storage. Boards: ~/Documents/Valmera/.valmera/refbench/boards/wave3.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

from . import vfl as vflmod
from . import w3

BOARDS = os.environ.get("VALMERA_W3_BOARDS") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/refbench/boards/wave3")
MODELS = os.environ.get("VALMERA_MODELS_DIR") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/models")


# ── plumbing ───────────────────────────────────────────────────────────

def _m(value, bar, op="<=", unit=""):
    if value is None:
        return {"value": None, "bar": bar, "op": op, "pass": None,
                "unit": unit}
    v = float(value)
    ok = {"<=": v <= bar, ">=": v >= bar, "==": v == bar, "<": v < bar,
          ">": v > bar}[op]
    return {"value": round(v, 4), "bar": bar, "op": op, "pass": bool(ok),
            "unit": unit}


def _status(metrics, extra_fail=False):
    vals = [m["pass"] for m in metrics.values()]
    if extra_fail or any(v is False for v in vals):
        return "fail"
    if any(v is None for v in vals):
        return "partial"
    return "pass"


def board_dir(at):
    d = os.path.join(BOARDS, at)
    os.makedirs(d, exist_ok=True)
    return d


def win(vid, name):
    return vflmod.window(vid, name)


def frames_bgr(path, t, n=1, fps=None, w=None):
    """n frames from file second ``t`` (BGR uint8); at the file's own size
    unless ``w`` (then even height, aspect kept)."""
    info = w3.probe(path)
    W, H = info["width"], info["height"]
    if w:
        H = int(round(H * w / float(W) / 2)) * 2
        W = int(w)
    vf = [f"scale={W}:{H}"]
    if fps:
        vf.insert(0, f"fps={fps}")
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
         "-an", "-vf", ",".join(vf), "-frames:v", str(n), "-f", "rawvideo",
         "-pix_fmt", "bgr24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def decal(path, w=24, h=24, n=25, fps=30.0):
    """An opaque white square clip (qtrle ARGB): its alpha centroid is the
    graphic's centre, exactly."""
    import motion_engine
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=white:s={w}x{h}:r={fps}:d={n / fps}", "-frames:v",
         str(n), "-vf", "format=argb", "-c:v", "qtrle", "-pix_fmt", "argb",
         path], check=True)
    return motion_engine.RenderedClip(path=path, x=0, y=0, w=w, h=h,
                                      frames=n, captured=n, cached=False,
                                      seconds=0.0)


def rgba(path, w, h):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f",
                        "rawvideo", "-pix_fmt", "rgba", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.uint8).reshape(-1, h, w, 4)


def centroid(fr):
    ys, xs = np.where(fr[..., 3] > 127)
    if xs.size == 0:
        return None
    return float(xs.mean()), float(ys.mean())


def world_ctx(w, edl, aspect="9:16", workdir=None):
    """(world_layer.Ctx, validated edl) for a fixture window's EDL (file
    clock), the render's own geometry."""
    import renderer
    import schemas
    import world_layer
    from timeline import Timeline
    src = w3.source_ref(w)
    e = schemas.validate_edl(edl, src.duration_s).model_dump()
    tl = Timeline(e["keep"], e.get("inserts") or [], e.get("speed") or [])
    W, H = renderer.frame_dims(float(w["width"]), float(w["height"]),
                               (e.get("frame") or {}).get("ratio"))
    idx = w3.index_for(w)
    c = world_layer.Ctx(e, int(W), int(H), 30.0, tl=tl, source=src,
                        index=idx, workdir=workdir or tempfile.mkdtemp(),
                        out_duration=tl.out_duration, src_path=w["path"])
    return c, e


def compose_decal(c, e, item_idx=0, size=24):
    """Run the world compositor on a square decal for motion item
    ``item_idx``: [(program t, centre px | None)] per frame, plus notes."""
    import world_layer
    item = e["motion"][item_idx]
    n = max(1, int(round((float(item["end"]) - float(item["start"]))
                         * c.fps)))
    clip = decal(os.path.join(c.workdir, f"decal_{item['id']}.mov"),
                 size, size, n, c.fps)
    new, notes = world_layer.compose(item, clip, c)
    if new is None:
        return [], notes
    fr = rgba(new.path, new.w, new.h)
    out = []
    for k in range(min(n, len(fr))):
        t = float(item["start"]) + (k + 0.5) / c.fps
        cc = centroid(fr[k])
        out.append((t, None if cc is None else (cc[0] + new.x,
                                                 cc[1] + new.y)))
    return out, notes


def to_program_px(c, t, xy):
    """SOURCE fractions -> program pixels at program second ``t``."""
    import world_layer
    tr = c.tl.out_to_src(t)
    if tr is None:
        return None
    A = world_layer.program_affine(c.geometry("world"), t, tr)
    if A is None:
        return None
    x = A[0, 0] * xy[0] + A[0, 1] * xy[1] + A[0, 2]
    y = A[1, 0] * xy[0] + A[1, 1] * xy[1] + A[1, 2]
    return x * c.W, y * c.H


def _nearest(samples, t, tol=0.04):
    best = None
    for s in samples:
        d = abs(float(s["t"]) - t)
        if d <= tol and (best is None or d < best[0]):
            best = (d, s)
    return None if best is None else best[1]


def _pct(values, q):
    v = [x for x in values if x is not None and np.isfinite(x)]
    return float(np.percentile(v, q)) if v else None


def _sheet(images, out, cols=4, label_h=26, labels=None):
    import cv2
    if not images:
        return None
    h = max(i.shape[0] for i in images)
    w = max(i.shape[1] for i in images)
    rows = (len(images) + cols - 1) // cols
    canvas = np.full((rows * (h + label_h), min(cols, len(images)) * w, 3),
                     24, np.uint8)
    for k, im in enumerate(images):
        r, cidx = divmod(k, cols)
        y0, x0 = r * (h + label_h), cidx * w
        canvas[y0 + label_h:y0 + label_h + im.shape[0],
               x0:x0 + im.shape[1]] = im
        if labels and k < len(labels):
            cv2.putText(canvas, str(labels[k])[:60], (x0 + 6, y0 + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1,
                        cv2.LINE_AA)
    cv2.imwrite(out, canvas)
    return out


def _program_frame(w, edl, t, W, H):
    """The source frame under program second ``t`` cropped to the program
    (for board overlays): via the render geometry's affine."""
    import cv2
    c, _e = world_ctx(w, edl)
    tr = c.tl.out_to_src(t)
    fr = frames_bgr(w["path"], tr, 1)[0]
    import world_layer
    A = world_layer.program_affine(c.geometry("world"), t, tr)
    sh, sw = fr.shape[:2]
    M = np.array([[A[0, 0] * c.W / sw, A[0, 1] * c.W / sh, A[0, 2] * c.W],
                  [A[1, 0] * c.H / sw, A[1, 1] * c.H / sh, A[1, 2] * c.H]])
    out = cv2.warpAffine(fr, M, (c.W, c.H), flags=cv2.INTER_AREA)
    if (W, H) != (c.W, c.H):
        out = cv2.resize(out, (W, H), interpolation=cv2.INTER_AREA)
    return out, c


# ── 3A anchors ─────────────────────────────────────────────────────────

def _hand_tracker():
    import sidecar_kinds
    from anchors import hands
    path, why = sidecar_kinds.model_file("mediapipe_hand_landmarker")
    if not path:
        raise RuntimeError(why)
    return hands.HandTracker(path)


def _mirror_error_hands(path, t0, t1, fps=10.0, w=1280):
    """Test-retest landmark precision (no human labels): each frame and its
    mirror image through the same tracker; the mirrored landmarks mapped
    back. Returns per-frame max error over the four hand-frame vertices
    (both thumb and index tips) as a share of the width, and per-frame
    index-tip error."""
    ht_a, ht_b = _hand_tracker(), _hand_tracker()
    n = int(round((t1 - t0) * fps))
    quad_err, tip_err = [], []
    for fr in frames_bgr(path, t0, n, fps, w):
        a = ht_a.step(fr)
        b = ht_b.step(np.ascontiguousarray(fr[:, ::-1]))
        if not a or not b:
            continue
        W = fr.shape[1]
        A = [np.asarray(h["pts"], float) for h in a]
        B = []
        for h in b:
            p = np.asarray(h["pts"], float).copy()
            p[:, 0] = (W - 1) - p[:, 0]
            B.append(p)
        errs_tip, verts = [], []
        for pa in A:
            pb = min(B, key=lambda q: np.linalg.norm(q[0] - pa[0]))
            errs_tip.append(np.linalg.norm(pa[8] - pb[8]) / W)
            verts += [np.linalg.norm(pa[k] - pb[k]) / W for k in (4, 8)]
        tip_err.append(min(errs_tip))
        if len(A) >= 2 and len(B) >= 2:
            quad_err.append(max(verts))
    return quad_err, tip_err


def _hand_frame_quads(path, t0, t1, fps=15.0, w=1280):
    """(t, quads, quads_mirror): the hand-frame quad (both thumb and index
    tips, ordered convex) per frame from the frame and from its mirror
    image (mapped back), SOURCE fractions; NaN where two hands are not
    seen."""
    from anchors import query
    ht_a, ht_b = _hand_tracker(), _hand_tracker()
    n = int(round((t1 - t0) * fps))
    T, QA, QB = [], [], []
    for i, fr in enumerate(frames_bgr(path, t0, n, fps, w)):
        Wd, Hd = fr.shape[1], fr.shape[0]
        a = ht_a.step(fr)
        b = ht_b.step(np.ascontiguousarray(fr[:, ::-1]))
        T.append(t0 + i / fps)

        def quad(hands, mirror):
            if len(hands) < 2:
                return np.full((4, 2), np.nan)
            P = []
            for h in hands:
                p = np.asarray(h["pts"], float).copy()
                if mirror:
                    p[:, 0] = (Wd - 1) - p[:, 0]
                P.append(p / [Wd, Hd])
            P.sort(key=lambda p: p[:, 0].mean())
            if query.hands_overlap(P[0], P[-1]):
                return np.full((4, 2), np.nan)   # the series' own rules
            q = query.frame_quad(P[0], P[-1])
            return np.full((4, 2), np.nan) if q is None else q
        QA.append(quad(a or [], False))
        QB.append(quad(b or [], True))
    return np.array(T), np.array(QA), np.array(QB)


def at_3a_1():
    """Effect window bounded by the fingers (hand.frame quad) on V12: the
    delivered (zero-phase smoothed) quad, test-retest on the mirror."""
    from anchors import query
    w = win("V12", "gestures")
    T, QA, QB = _hand_frame_quads(w["path"], 7.0, 24.5)
    asp = w["width"] / float(w["height"])
    out = []
    for Q in (QA, QB):
        with np.errstate(all="ignore"):
            xy = np.nanmean(Q, axis=1)
        ser = query.Series(T, xy, quad=Q, kind="hand").smoothed(0.5, asp)
        out.append(ser)
    both = np.isfinite(QA).all(axis=(1, 2)) & np.isfinite(QB).all(
        axis=(1, 2))
    raw_err, sm_err = [], []
    for k in np.where(both)[0]:
        raw_err.append(float(np.max(np.hypot(
            (QA[k, :, 0] - QB[k, :, 0]),
            (QA[k, :, 1] - QB[k, :, 1]) / asp))))
        qa, qb = out[0].quad[k], out[1].quad[k]
        if np.all(np.isfinite(qa)) and np.all(np.isfinite(qb)):
            sm_err.append(float(np.max(np.hypot(
                qa[:, 0] - qb[:, 0], (qa[:, 1] - qb[:, 1]) / asp))))
    # the error of ONE pass is about the pair difference / sqrt(2)
    one = [e / math.sqrt(2.0) for e in sm_err]
    share = float(np.mean(np.array(one) <= 0.02)) if one else None
    metrics = {
        "quad_vertex_err_le_2pct_share": _m(share, 0.95, ">="),
        "quad_vertex_err_p95_pct": _m(None if not one else
                                      100 * _pct(one, 95), 2.0, "<=",
                                      "% width"),
    }
    return {"metrics": metrics, "status": _status(metrics),
            "detail": {"frames_two_hands": int(both.sum()),
                       "raw_pair_err_p95_pct":
                           None if not raw_err else
                           round(100 * _pct(raw_err, 95), 3),
                       "method": "test-retest without labels: the frame "
                                 "and its mirror image through the same "
                                 "landmarker, both quads smoothed as the "
                                 "compositor smooths them (zero-phase "
                                 "One-Euro); one pass's error = the pair's "
                                 "worst-vertex difference / sqrt 2",
                       "solarise_inside": "not run (a region-clipped "
                                          "finish on hand.frame joins 1E's "
                                          "region masks: integrator item)"}}


def at_3a_2():
    """A card riding a fingertip (V12 gestures): placement vs the measured
    fingertip, and jitter while the hand is still."""
    import cv2
    w = win("V12", "gestures")
    keep = [[7.87, 23.87]]
    edl = {"keep": keep, "frame": {"ratio": "9:16", "mode": "crop"},
           "motion": [{"id": "gc", "template": "html", "html": "x",
                       "start": 1.2, "end": 4.6,
                       "anchor": {"to": "hand.index_tip", "pin": [0.5, 0.5],
                                  "lost": "hold"}}]}
    c, e = world_ctx(w, edl)
    got, notes = compose_decal(c, e)
    from anchors import query
    lo, hi = c.tl.out_to_src(0.5), c.tl.out_to_src(8.0)
    samples, _sh, _m2 = query.load("landmarks.hand", c.source, [(lo, hi)])
    # the reference: the RAW landmark (unsmoothed), linearly interpolated
    # between the 15 fps samples, the same hand the anchor follows
    raw = query.hand_series(samples, "index_tip", "any")
    errs, raw_xy, cen = [], [], []
    for t, cc in got:
        xy, _s, _a, _q, ok = raw.at(np.array([c.tl.out_to_src(t)]))
        if cc is None or not ok[0]:
            continue
        tip = to_program_px(c, t, xy[0])
        errs.append(math.hypot(tip[0] - cc[0], tip[1] - cc[1]) / c.W)
        raw_xy.append(tip)
        cen.append(cc)
    share = float(np.mean(np.array(errs) <= 0.015)) if errs else None
    # jitter while still: the card centre's frame-to-frame acceleration
    # (second difference, px) where the raw tip moves < 0.3 % of the width
    # per frame — over the whole held stretch of the shot
    hold = dict(edl["motion"][0], start=0.5, end=8.0)
    c2, e2 = world_ctx(w, dict(edl, motion=[hold]))
    got2, _n2 = compose_decal(c2, e2)
    C, R = [], []
    for t, cc in got2:
        xy, _s, _a, _q, ok = raw.at(np.array([c2.tl.out_to_src(t)]))
        if cc is None or not ok[0]:
            C.append(None)
            R.append(None)
            continue
        C.append(cc)
        R.append(to_program_px(c2, t, xy[0]))
    jit = []
    for k in range(1, len(C) - 1):
        if None in (C[k - 1], C[k], C[k + 1], R[k - 1], R[k + 1]):
            continue
        if math.hypot(R[k + 1][0] - R[k - 1][0], R[k + 1][1]
                      - R[k - 1][1]) / 2.0 / c2.W >= 0.003:
            continue
        ax = C[k + 1][0] - 2 * C[k][0] + C[k - 1][0]
        ay = C[k + 1][1] - 2 * C[k][1] + C[k - 1][1]
        jit.append(math.hypot(ax, ay) / c2.W)
    metrics = {
        "centre_err_le_1_5pct_share": _m(share, 0.90, ">="),
        "centre_err_p90_pct": _m(None if not errs else 100 * _pct(errs, 90),
                                 1.5, "<=", "% width"),
        "still_jitter_rms_pct": _m(None if len(jit) < 10 else
                                   100 * float(np.sqrt(np.mean(
                                       np.square(jit)))), 0.3, "<=",
                                   "% width"),
    }
    _, tip_err = _mirror_error_hands(w["path"], 9.1, 12.4)
    # board: three moments with the measured tip (green) and card (red)
    bd = board_dir("AT-3A-2")
    ims, labels = [], []
    for k in (5, len(got) // 2, len(got) - 6):
        if not (0 <= k < len(got)) or got[k][1] is None:
            continue
        t, cc = got[k]
        im, _c = _program_frame(w, edl, t, c.W, c.H)
        xy, _s, _a, _q, ok = raw.at(np.array([c.tl.out_to_src(t)]))
        if ok[0]:
            q = to_program_px(c, t, xy[0])
            cv2.circle(im, (int(q[0]), int(q[1])), 6, (0, 255, 0), 2)
        cv2.rectangle(im, (int(cc[0] - 12), int(cc[1] - 12)),
                      (int(cc[0] + 12), int(cc[1] + 12)), (0, 0, 255), 2)
        ims.append(im)
        labels.append(f"t={t:.2f}")
    board = _sheet(ims, os.path.join(bd, "fingertip_card.png"), cols=3,
                   labels=labels)
    return {"metrics": metrics, "status": _status(metrics),
            "board": [board],
            "detail": {"frames": len(errs), "still_frames": len(jit),
                       "notes": notes,
                       "landmark_retest_p90_pct":
                           None if not tip_err else
                           round(100 * _pct(tip_err, 90), 3),
                       "method": "the world compositor on a square decal "
                                 "pinned at its centre to hand.index_tip "
                                 "(default smoothing, zero-phase); error = "
                                 "decal centre vs the RAW measured "
                                 "fingertip interpolated between samples, "
                                 "program px / width; jitter = RMS second "
                                 "difference of the decal centre where the "
                                 "raw tip moves < 0.3 % width per frame"}}


def _face_tracker():
    import sidecar_kinds
    from anchors import faces
    yunet = os.path.join(MODELS, "face_detection_yunet_2023mar.onnx")
    path, why = sidecar_kinds.model_file("mediapipe_face_landmarker")
    if not path:
        raise RuntimeError(why)
    return faces.FaceTracker(yunet, path)


def _eye_error(c, e, follow, eyes, t0):
    """Per-frame eye errors (frame heights) of a plate registered by the
    head anchor with ``follow``: frame-``t0`` eye positions carried by the
    compositor's own per-frame transforms vs the measured eyes."""
    item = dict(e["motion"][0], anchor=dict(e["motion"][0]["anchor"],
                                            follow=follow))
    n = int(round((float(item["end"]) - float(item["start"])) * c.fps))
    clip = decal(os.path.join(c.workdir, "plate.mov"), 64, 64, n, c.fps)
    import world_layer
    plan, notes = world_layer.compose(item, clip, c, plan_only=True)
    if plan is None:
        return [], notes
    ks = [k for k in range(n) if plan.M[k] is not None]
    if not ks:
        return [], notes
    k0 = ks[0]
    ts = [float(item["start"]) + (k + 0.5) / c.fps for k in range(n)]
    M0inv = np.linalg.inv(plan.M[k0])

    def measured(k):
        out = []
        for ser in eyes:
            xy, _s, _a, _q, ok = ser.at(np.array([c.tl.out_to_src(ts[k])]))
            if not ok[0]:
                return None
            out.append(to_program_px(c, ts[k], xy[0]))
        return out
    m0 = measured(k0)
    if m0 is None:
        return [], notes
    local = [M0inv @ np.array([p[0], p[1], 1.0]) for p in m0]
    errs = []
    for k in ks:
        mk = measured(k)
        if mk is None:
            continue
        for lp, mp in zip(local, mk):
            q = plan.M[k] @ lp
            errs.append(math.hypot(q[0] / q[2] - mp[0], q[1] / q[2] - mp[1])
                        / c.H)
    return errs, notes


def at_3a_3():
    """Registration to the head through a turn (V01, Thiel turning from
    frontal to three-quarter): a plate registered by the head anchor keeps
    its eye sockets on the measured eyes."""
    from anchors import query
    w = win("V01", "thiel_ai_bigger")
    src = w3.source_ref(w)
    samples, _sh, meta = query.load("landmarks.face", src, [(0.0, 30.0)])
    if not samples:
        return {"status": "not run", "metrics": {},
                "detail": {"why": "V01 landmarks.face not measured"}}
    edl = {"keep": [[0.0, 30.0]],
           "motion": [{"id": "xr", "template": "html", "html": "x",
                       "start": 1.0, "end": 26.0,
                       "anchor": {"to": "head", "pin": [0.5, 0.5]}}]}
    c, e = world_ctx(w, edl, aspect="source")
    eyes = [query.face_series(samples, "eye_l", "any", meta),
            query.face_series(samples, "eye_r", "any", meta)]
    sim, _n1 = _eye_error(c, e, ["position", "scale", "rotation"], eyes,
                          1.0)
    shp, notes = _eye_error(c, e, ["position", "scale", "rotation",
                                   "shape"], eyes, 1.0)
    yaw = []
    t_s, _P = query.face_points(samples)
    for s_ in samples:
        fs = s_.get("faces") or []
        if fs and 1.0 <= float(s_["t"]) <= 26.0:
            f = max(fs, key=lambda f: (f["b"][2] - f["b"][0]))
            yaw.append(float(f["hp"][0]))
    yaw_range = (float(np.percentile(yaw, 97.5) - np.percentile(yaw, 2.5))
                 if yaw else None)
    metrics = {
        "eye_socket_err_p95_pct_h": _m(None if not shp else
                                       100 * _pct(shp, 95), 2.0, "<=",
                                       "% height"),
        "yaw_range_deg": _m(yaw_range, 30.0, ">=", "deg"),
    }
    return {"metrics": metrics, "status": _status(metrics),
            "detail": {"frames": len(shp) // 2, "notes": notes,
                       "similarity_only_p95_pct_h":
                           None if not sim else round(100 * _pct(sim, 95),
                                                      3),
                       "method": "anchor head + follow shape (an affine "
                                 "fitted to the face mesh each frame, "
                                 "zero-phase smoothed); frame-0 eye "
                                 "positions carried by the compositor's own "
                                 "per-frame transforms vs the measured "
                                 "eyes; yaw range = 2.5-97.5 percentile of "
                                 "the mesh's yaw estimate",
                       "anatomy_plates": "not rendered (public-domain "
                                         "plates not in the library)"}}


def at_3a_4():
    """Iris opening from the pupil (V08 close-up): a decal anchored to the
    iris lands on the iris landmark; the landmark's own test-retest."""
    w = win("V08", "garry_closeup")
    edl = {"keep": [[0.0, 12.0]], "frame": {"ratio": "9:16",
                                            "mode": "crop"},
           "motion": [{"id": "ir", "template": "html", "html": "x",
                       "start": 1.0, "end": 5.0,
                       "anchor": {"to": "face.iris_l", "pin": [0.5, 0.5],
                                  "smooth": 0.0}}]}
    c, e = world_ctx(w, edl)
    got, notes = compose_decal(c, e, size=10)
    from anchors import query
    samples, _sh, meta = query.load("landmarks.face", c.source,
                                    [(0.5, 5.5)])
    series = query.face_series(samples, "iris_l", "any", meta)
    errs = []
    for t, cc in got:
        if cc is None:
            continue
        tr = c.tl.out_to_src(t)
        xy, _s, _a, _q, ok = series.at(np.array([tr]))
        if not ok[0]:
            continue
        q = to_program_px(c, t, xy[0])
        errs.append(math.hypot(q[0] - cc[0], q[1] - cc[1]) / c.W)
    # landmark test-retest: the mirrored frame (the left iris becomes the
    # right one in the mirror)
    ft_a, ft_b = _face_tracker(), _face_tracker()
    from anchors import faces
    rt = []
    for fr in frames_bgr(w["path"], 1.0, 20, 5.0, 1280):
        a = ft_a.step(fr)
        b = ft_b.step(np.ascontiguousarray(fr[:, ::-1]))
        if not a or not b:
            continue
        Wd = fr.shape[1]
        na = faces.named_points(a[0]["mesh"])
        nb = faces.named_points(b[0]["mesh"])
        xb, yb = nb["iris_r"]
        rt.append(math.hypot(na["iris_l"][0] - ((Wd - 1) - xb),
                             na["iris_l"][1] - yb) / Wd)
    metrics = {
        "origin_err_p95_pct": _m(None if not errs else
                                 100 * _pct(errs, 95), 1.0, "<=",
                                 "% width"),
        "iris_retest_p95_pct": _m(None if not rt else 100 * _pct(rt, 95),
                                  1.0, "<=", "% width"),
    }
    return {"metrics": metrics, "status": _status(metrics),
            "detail": {"frames": len(errs), "retest_frames": len(rt),
                       "notes": notes}}


def at_3a_5():
    """A doodle tip on a tracked point for 3 s (V12 walk-on, the camera
    following): the decal on the track vs the track, and the track's own
    forward-backward error."""
    import cv2
    w = win("V12", "walkon")
    seed = {"src_t": 7.0, "x": 0.17, "y": 0.58}
    d = {"id": "leaf", "type": "track", "stage": "world", "start": 0.6,
         "end": 3.8, "seed": seed}
    edl = {"keep": [[6.0, 10.0]], "fx": [d],
           "motion": [{"id": "ar", "template": "html", "html": "x",
                       "start": 0.7, "end": 3.7,
                       "anchor": {"to": "track:leaf", "pin": [0.5, 0.5],
                                  "smooth": 0.0}}]}
    c, e = world_ctx(w, edl, aspect="source")
    got, notes = compose_decal(c, e, size=12)
    import sidecars
    import world_defs
    from anchors import query
    kind, ranges, params = world_defs.request(e["fx"][0], c.tl)
    rows = [sidecars.read_json_chunk(kind, c.source.sha256, m)
            for _c, m in sidecars.ready_chunks(kind, c.source.sha256,
                                               c.source.shots,
                                               c.source.duration_s, ranges,
                                               params)]
    series = query.track_series([r for r in rows if r], "leaf")
    errs = []
    for t, cc in got:
        if cc is None:
            continue
        tr = c.tl.out_to_src(t)
        xy, _s, _a, _q, ok = series.at(np.array([tr]))
        if not ok[0]:
            continue
        q = to_program_px(c, t, xy[0])
        errs.append(math.hypot(q[0] - cc[0], q[1] - cc[1]) / c.W)
    # forward-backward: LK from the last tracked frame back to the seed
    fb = None
    try:
        ok_idx = np.where(series.ok)[0]
        t_end = float(series.t[ok_idx[-1]])
        n = int(round((t_end - seed["src_t"]) * 30))
        frs = frames_bgr(w["path"], seed["src_t"], n + 1, 30.0, 960)
        g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frs]
        Wd, Hd = g[0].shape[1], g[0].shape[0]
        p = np.array([[series.xy[ok_idx[-1]][0] * Wd,
                       series.xy[ok_idx[-1]][1] * Hd]], np.float32)
        for k in range(len(g) - 1, 0, -1):
            p, st, _err = cv2.calcOpticalFlowPyrLK(
                g[k], g[k - 1], p.reshape(-1, 1, 2), None, winSize=(21, 21),
                maxLevel=3)
            if not st[0][0]:
                p = None
                break
            p = p.reshape(-1, 2)
        if p is not None:
            fb = math.hypot(p[0][0] / Wd - seed["x"],
                            (p[0][1] / Hd - seed["y"]) * Hd / Wd)
    except Exception:  # noqa: BLE001
        fb = None
    okk = np.where(series.ok)[0]
    span = float(series.t[okk[-1]] - series.t[okk[0]]) if okk.size else 0.0
    metrics = {
        "tip_err_max_pct": _m(None if not errs else 100 * max(errs), 1.5,
                              "<=", "% width"),
        "track_fb_err_pct": _m(None if fb is None else 100 * fb, 1.5, "<=",
                               "% width"),
        "tracked_span_s": _m(span, 3.0, ">=", "s"),
    }
    return {"metrics": metrics, "status": _status(metrics),
            "detail": {"frames": len(errs), "notes": notes,
                       "method": "decal on track:leaf vs the track; the "
                                 "track itself checked by LK from its last "
                                 "frame back to the seed (no labels)"}}


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) \
        - ix * iy
    return ix * iy / u if u > 0 else 0.0


def at_3a_6(n_windows=20, fps=5.0):
    """Follow regression: face-loss events of the FOLLOWED face, YuNet vs
    Haar, on 20 VFL windows. The followed face is the largest; a loss = it
    was seen in one sample and no detection overlaps it (IoU >= 0.3) in the
    next, inside a shot (cuts excluded)."""
    import cv2
    data = vflmod.load()
    wins = []
    for vid, s in sorted(data.items()):
        if vid.startswith("_"):
            continue
        ws = s.get("windows") or {}
        it = ws.items() if isinstance(ws, dict) else \
            [(x.get("name"), x) for x in ws]
        for name, _x in it:
            try:
                wins.append(vflmod.window(vid, name))
            except Exception:  # noqa: BLE001
                continue
    wins = [x for x in wins if os.path.exists(x["path"])][:n_windows]
    yunet = cv2.FaceDetectorYN.create(
        os.path.join(MODELS, "face_detection_yunet_2023mar.onnx"), "",
        (320, 180), 0.6, 0.3, 50)
    haar = cv2.CascadeClassifier(os.path.join(
        cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    losses = {"yunet": 0, "haar": 0}
    followed = {"yunet": 0, "haar": 0}
    per = []
    for x in wins:
        dur = min(30.0, float(x["file_span"][1] - x["file_span"][0]))
        frs = frames_bgr(x["path"], 0.0, int(dur * fps), fps, 640)
        prev = {"yunet": None, "haar": None}
        last = None
        wl = {"yunet": 0, "haar": 0}
        for f in frs:
            Hh, Ww = f.shape[:2]
            small = cv2.resize(f, (320, int(round(320 * Hh / Ww))))
            yunet.setInputSize((small.shape[1], small.shape[0]))
            _r, fy = yunet.detect(small)
            sc = Ww / 320.0
            boxes = {"yunet": [] if fy is None else
                     [(b[0] * sc, b[1] * sc, (b[0] + b[2]) * sc,
                       (b[1] + b[3]) * sc) for b in fy],
                     "haar": [(bx, by, bx + bw, by + bh) for bx, by, bw, bh
                              in haar.detectMultiScale(
                                  cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), 1.1,
                                  5, minSize=(40, 40))]}
            g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hist = cv2.calcHist([g], [0], None, [32], [0, 256])
            cv2.normalize(hist, hist)
            cut = last is not None and cv2.compareHist(
                last, hist, cv2.HISTCMP_BHATTACHARYYA) > 0.35
            last = hist
            for k in ("yunet", "haar"):
                bs = boxes[k]
                if prev[k] is not None and not cut:
                    match = [b for b in bs if _iou(b, prev[k]) >= 0.3]
                    if not match:
                        losses[k] += 1
                        wl[k] += 1
                        prev[k] = None
                        continue
                    prev[k] = max(match, key=lambda b: _iou(b, prev[k]))
                    followed[k] += 1
                    continue
                prev[k] = max(bs, key=lambda b: (b[2] - b[0])
                              * (b[3] - b[1])) if bs else None
        per.append({"window": f"{x.get('vfl')}/{x.get('name')}",
                    "losses": wl})
    red = (1 - losses["yunet"] / float(losses["haar"])) \
        if losses["haar"] else None
    metrics = {"loss_reduction_vs_haar": _m(red, 0.30, ">=")}
    return {"metrics": metrics, "status": _status(metrics),
            "detail": {"windows": len(wins), "losses": losses,
                       "followed_samples": followed, "fps": fps,
                       "per_window": per,
                       "test_follow": "run tests/test_follow*.py "
                                      "(reported in WAVE3_RELEASE.md)"}}


# ── registry ──────────────────────────────────────────────────────────

RUNS = {"AT-3A-1": at_3a_1, "AT-3A-2": at_3a_2, "AT-3A-3": at_3a_3,
        "AT-3A-4": at_3a_4, "AT-3A-5": at_3a_5, "AT-3A-6": at_3a_6}
BLOCKED = {}


def run(ids):
    path = os.path.join(BOARDS, "results.json")
    os.makedirs(BOARDS, exist_ok=True)
    try:
        with open(path) as fh:
            res = json.load(fh)
    except (OSError, ValueError):
        res = {}
    for i in ids:
        t0 = time.time()
        if i in BLOCKED:
            r = {"status": "blocked", "metrics": {}, "detail":
                 {"why": BLOCKED[i]}}
        else:
            try:
                r = RUNS[i]()
            except Exception as e:  # noqa: BLE001 — report, keep going
                import traceback
                r = {"status": "error", "metrics": {},
                     "detail": {"error": f"{type(e).__name__}: {e}",
                                "trace": traceback.format_exc()[-1500:]}}
        r["id"] = i
        r["seconds"] = round(time.time() - t0, 1)
        res[i] = r
        print(json.dumps({i: {"status": r["status"], "metrics": {
            k: v.get("value") for k, v in (r.get("metrics") or {}).items()}}
            }), flush=True)
        with open(path, "w") as fh:
            json.dump(res, fh, indent=1, default=str)
    return res


def main(argv):
    ids = argv or ["all"]
    if ids == ["all"]:
        ids = sorted(set(RUNS) | set(BLOCKED))
    run(ids)


if __name__ == "__main__":
    main(sys.argv[1:])
