"""Sidecar children for the anchor kinds (3A): landmarks.face,
landmarks.hand, landmarks.pose, track.points, camera.motion.

Each ``child_*(spec, ch) -> (path, meta)`` measures one chunk [t0, t1) of
SOURCE seconds on the proxy and writes one JSON file, exactly like
analysis_jobs._child_faces: ``meta`` carries fps, frames, width, height,
ms_per_frame and qa (``coverage`` = share of samples with a detection).
All coordinates in the JSON are SOURCE FRAME FRACTIONS (4 decimals).
"""

import json
import os
import time

import numpy as np

CODE_VERSION = 1


def _decode(spec, ch, fps, max_w):
    """(frames generator, w, h, n) for the chunk at ``fps``, the proxy
    scaled to at most ``max_w`` wide."""
    import analysis_jobs
    src = spec["source"]
    sw, sh = int(src["width"]), int(src["height"])
    w = analysis_jobs._even(min(sw, int(max_w)))
    h = analysis_jobs._even(sh * w / float(sw))
    n = max(1, int(round((ch["t1"] - ch["t0"]) * fps)))
    gen = analysis_jobs._decode(spec["src"], ch["t0"], ch["t1"] - ch["t0"],
                                w, h, fps, n)
    return gen, w, h, n


def _fps(spec, default):
    p = spec["params"]
    return max(1.0, min(float(p.get("fps") or default),
                        float(spec["source"]["fps"] or default)))


def _r(v):
    return round(float(v), 4)


def _write(spec, ch, suffix, data):
    out = os.path.join(spec["outdir"], f"{ch['out_key']}.{suffix}")
    with open(out, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    return out


def _meta(fps, frames, w, h, t_model, i, hit, extra_qa=None):
    qa = {"flicker": None, "edge_chatter": None,
          "coverage": round(hit / float(max(1, frames)), 4)}
    qa.update(extra_qa or {})
    return {"fps": round(fps, 6), "frames": frames, "width": w, "height": h,
            "ms_per_frame": round(1000.0 * t_model / max(1, i), 3),
            "qa": qa}


# ── landmarks.face ─────────────────────────────────────────────────────

def child_face(spec, ch):
    import analysis_jobs
    from . import faces
    eng = spec["engine"]
    paths = eng.get("paths") or {}
    fps = _fps(spec, 15.0)
    gen, w, h, n = _decode(spec, ch, fps, spec["params"].get("max_w")
                           or 1280)
    ft = faces.FaceTracker(paths["yunet"],
                           paths["mediapipe_face_landmarker"],
                           det_w=int(spec["params"].get("det_w") or 640),
                           max_faces=int(spec["params"].get("max_faces") or 4),
                           threads=eng.get("threads") or 0)
    samples, t_model, i, hit = [], 0.0, 0, 0
    for frame in gen:
        t = time.perf_counter()
        found = ft.step(frame)
        t_model += time.perf_counter() - t
        rows = []
        for f in found:
            mesh = f["mesh"]
            nm = faces.named_points(mesh)
            pts = []
            for name in faces.NAMED_ORDER:
                x, y = nm[name]
                pts += [_r(x / w), _r(y / h)]
            oval = []
            for k in faces.OVAL:
                oval += [_r(mesh[k][0] / w), _r(mesh[k][1] / h)]
            cx, cy, rx, ry, deg = faces.head_ellipse(mesh)
            b = f["box"]
            rows.append({"s": round(float(f["score"]), 3),
                         "b": [_r(b[0] / w), _r(b[1] / h), _r(b[2] / w),
                               _r(b[3] / h)],
                         "p": pts, "o": oval,
                         "hp": list(faces.head_pose(mesh)),
                         "he": [_r(cx / w), _r(cy / h), _r(rx / w),
                                _r(ry / h), round(deg, 2)]})
        hit += 1 if rows else 0
        samples.append({"t": round(ch["t0"] + i / fps, 3), "faces": rows})
        i += 1
    if not samples:
        raise analysis_jobs.DecodeError("no frames decoded in the chunk")
    path = _write(spec, ch, "json", {
        "version": 1, "kind": "landmarks.face", "fps": fps, "width": w,
        "height": h, "names": list(faces.NAMED_ORDER),
        "oval": len(faces.OVAL), "samples": samples})
    return path, _meta(fps, len(samples), w, h, t_model, i, hit)


# ── landmarks.hand ─────────────────────────────────────────────────────

def child_hand(spec, ch):
    import analysis_jobs
    from . import hands
    eng = spec["engine"]
    paths = eng.get("paths") or {}
    fps = _fps(spec, 15.0)
    gen, w, h, n = _decode(spec, ch, fps, spec["params"].get("max_w")
                           or 1280)
    ht = hands.HandTracker(paths["mediapipe_hand_landmarker"],
                           max_hands=int(spec["params"].get("max_hands")
                                         or 2),
                           threads=eng.get("threads") or 0)
    samples, t_model, i, hit = [], 0.0, 0, 0
    for frame in gen:
        t = time.perf_counter()
        found = ht.step(frame)
        t_model += time.perf_counter() - t
        rows = []
        for hd in found:
            p = np.asarray(hd["pts"])
            flat = []
            for x, y in p:
                flat += [_r(x / w), _r(y / h)]
            rows.append({"s": round(float(hd["score"]), 3),
                         "side": hd["side"], "p": flat})
        hit += 1 if rows else 0
        samples.append({"t": round(ch["t0"] + i / fps, 3), "hands": rows})
        i += 1
    if not samples:
        raise analysis_jobs.DecodeError("no frames decoded in the chunk")
    path = _write(spec, ch, "json", {
        "version": 1, "kind": "landmarks.hand", "fps": fps, "width": w,
        "height": h, "names": list(hands.NAMES), "samples": samples})
    return path, _meta(fps, len(samples), w, h, t_model, i, hit)


# ── landmarks.pose ─────────────────────────────────────────────────────

def child_pose(spec, ch):
    import analysis_jobs
    from . import faces, pose
    eng = spec["engine"]
    paths = eng.get("paths") or {}
    fps = _fps(spec, 15.0)
    gen, w, h, n = _decode(spec, ch, fps, spec["params"].get("max_w")
                           or 1280)
    det = faces.FaceTracker(paths["yunet"],
                            paths["mediapipe_face_landmarker"],
                            threads=eng.get("threads") or 0)
    pt = pose.PoseTracker(paths["mediapipe_pose_landmarker"],
                          threads=eng.get("threads") or 0)
    seed = spec["params"].get("seed_box")
    samples, t_model, i, hit = [], 0.0, 0, 0
    for frame in gen:
        t = time.perf_counter()
        face_box = None
        if pt.roi is None:
            dets = det.detect(frame)
            if dets:
                face_box = dets[0][1]
            elif seed:
                # a person box from the caller (a far figure YuNet misses):
                # its top seventh is the head
                x0, y0, x1, y1 = (seed[0] * w, seed[1] * h, seed[2] * w,
                                  seed[3] * h)
                fh = (y1 - y0) / 7.0
                cx = (x0 + x1) / 2.0
                face_box = [cx - fh * 0.4, y0, cx + fh * 0.4, y0 + fh]
        found = pt.step(frame, face_box)
        t_model += time.perf_counter() - t
        rows = []
        if found is not None:
            flat = []
            for x, y, vis, _pres in found["pts"]:
                flat += [_r(x / w), _r(y / h), round(float(vis), 2)]
            rows.append({"s": round(float(found["score"]), 3), "p": flat})
        hit += 1 if rows else 0
        samples.append({"t": round(ch["t0"] + i / fps, 3), "people": rows})
        i += 1
    if not samples:
        raise analysis_jobs.DecodeError("no frames decoded in the chunk")
    path = _write(spec, ch, "json", {
        "version": 1, "kind": "landmarks.pose", "fps": fps, "width": w,
        "height": h, "names": list(pose.NAMES), "samples": samples})
    return path, _meta(fps, len(samples), w, h, t_model, i, hit)


# ── track.points ───────────────────────────────────────────────────────

def _gray_frames(spec, ch, fps, max_w):
    import cv2
    gen, w, h, n = _decode(spec, ch, fps, max_w)
    frames = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in gen]
    return frames, w, h


def child_points(spec, ch):
    """Seeds (params ``seeds``: [{id, src_t, x, y}] fractions) tracked over
    the chunk; a seed outside the chunk starts at the chunk edge nearest
    it, flagged ``carried`` (a reader prefers the chunk holding its
    seed)."""
    import analysis_jobs
    from . import points
    fps = _fps(spec, 30.0)
    t_start = time.perf_counter()
    frames, w, h = _gray_frames(spec, ch, fps,
                                spec["params"].get("max_w") or 960)
    if not frames:
        raise analysis_jobs.DecodeError("no frames decoded in the chunk")
    times = [round(ch["t0"] + k / fps, 3) for k in range(len(frames))]
    tracks, hit = [], 0
    for sd in spec["params"].get("seeds") or []:
        st = float(sd.get("src_t", ch["t0"]))
        k = int(round((st - ch["t0"]) * fps))
        carried = not 0 <= k < len(frames)
        k = min(max(k, 0), len(frames) - 1)
        rows = points.track(frames, k, float(sd["x"]) * w,
                            float(sd["y"]) * h,
                            radius=float(sd.get("radius_px") or 14.0))
        tr = [[times[j], _r(x / w), _r(y / h), bool(ok)]
              for j, (x, y, ok) in enumerate(rows)]
        hit += sum(1 for r in tr if r[3])
        tracks.append({"id": sd.get("id"), "seed_t": st,
                       "carried": carried, "rows": tr})
    t_model = time.perf_counter() - t_start
    path = _write(spec, ch, "json", {
        "version": 1, "kind": "track.points", "fps": fps, "width": w,
        "height": h, "tracks": tracks})
    return path, _meta(fps, len(frames), w, h, t_model, len(frames),
                       hit / float(max(1, len(tracks))))


# ── camera.motion ──────────────────────────────────────────────────────

def child_camera(spec, ch):
    import analysis_jobs
    from . import camera_motion, faces
    eng = spec["engine"]
    paths = eng.get("paths") or {}
    fps = _fps(spec, 15.0)
    t_start = time.perf_counter()
    import cv2
    gen, w, h, n = _decode(spec, ch, fps, spec["params"].get("max_w") or 640)
    det = None
    if paths.get("yunet"):
        from . import models
        det = models.yunet(paths["yunet"], (w, h))
    grays, masks = [], []
    for frame in gen:
        boxes = []
        if det is not None:
            _ok, fs = det.detect(frame)
            for f in fs if fs is not None else ():
                boxes.append([float(f[0]), float(f[1]), float(f[0] + f[2]),
                              float(f[1] + f[3])])
        grays.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        masks.append(camera_motion.person_mask(frame.shape, boxes))
    if not grays:
        raise analysis_jobs.DecodeError("no frames decoded in the chunk")
    del faces
    Hs, inl = camera_motion.chain(grays, masks)
    static = camera_motion.is_static(Hs, w, h)
    # in FRACTIONS: H_frac = S^-1 H S with S = diag(w, h, 1)
    S = np.diag([float(w), float(h), 1.0])
    Si = np.diag([1.0 / w, 1.0 / h, 1.0])
    samples = []
    for k, (H, q) in enumerate(zip(Hs, inl)):
        Hf = Si @ H @ S
        Hf = Hf / Hf[2, 2]
        samples.append({"t": round(ch["t0"] + k / fps, 3),
                        "H": [round(float(v), 6) for v in Hf.reshape(-1)],
                        "q": round(q, 3)})
    t_model = time.perf_counter() - t_start
    path = _write(spec, ch, "json", {
        "version": 1, "kind": "camera.motion", "fps": fps, "width": w,
        "height": h, "static": bool(static), "samples": samples})
    return path, _meta(fps, len(samples), w, h, t_model, len(samples),
                       len(samples), {"static": bool(static)})
