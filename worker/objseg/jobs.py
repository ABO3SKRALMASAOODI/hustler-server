"""The ``objects.sam2`` sidecar child (3B).

params (keyed: the same prompt and window always hit the same chunks):
  prompt   {src_t, box?: [x0, y0, x1, y1], points?: [[x, y, 1|0], ...]}
           SOURCE fractions at SOURCE second src_t
  window   [a, b] SOURCE seconds where the object matters (<= 6 s by the
           3B bound; the verb clips it to the prompt's shot)
  fps      mask rate (<= the source's), default 30
  max_w    working width, default 960

Writes the chunk [t0, t1) as a gray H.264 mask (``mask.h264.v1``, exactly
the ``matte.person`` layout: frame j at t0 + j / fps), black outside the
window. The keyframe and every re-prompt run EfficientSAM-Ti; the frames
between are carried by optical flow (propagate.py).
"""

import os
import subprocess
import tempfile
import time

import numpy as np

CODE_VERSION = 1
MAX_WINDOW_S = 6.0


def _decode_span(spec, lo, hi, fps, max_w):
    import analysis_jobs
    src = spec["source"]
    sw, sh = int(src["width"]), int(src["height"])
    w = analysis_jobs._even(min(sw, int(max_w)))
    h = analysis_jobs._even(sh * w / float(sw))
    n = max(1, int(round((hi - lo) * fps)))
    frames = list(analysis_jobs._decode(spec["src"], lo, hi - lo, w, h, fps,
                                        n))
    return frames, w, h


def child_objects(spec, ch):
    import analysis_jobs
    from . import propagate, sam
    p = spec["params"]
    eng = spec["engine"]
    prompt = p.get("prompt") or {}
    win = p.get("window") or [ch["t0"], ch["t1"]]
    fps = max(1.0, min(float(p.get("fps") or 30.0),
                       float(spec["source"]["fps"] or 30.0)))
    pt = float(prompt.get("src_t", win[0]))
    a, b = float(win[0]), float(win[1])
    lo = max(a, min(float(ch["t0"]), pt))
    hi = min(b, max(float(ch["t1"]), pt + 1.0 / fps))
    t_start = time.perf_counter()
    frames, w, h = ([], 0, 0)
    if hi > lo:
        frames, w, h = _decode_span(spec, lo, hi, fps,
                                    p.get("max_w") or 960)
    masks = []
    stats = {}
    if frames:
        seg = sam.Segmenter((eng.get("paths") or {})["efficientsam_ti"],
                            eng.get("threads") or 0)
        key = min(len(frames) - 1, max(0, int(round((pt - lo) * fps))))
        box = prompt.get("box")
        pts = prompt.get("points") or []
        key_mask, score = seg.segment(
            frames[key],
            box=[box[0] * w, box[1] * h, box[2] * w, box[3] * h]
            if box else None,
            points=[[q[0] * w, q[1] * h, q[2] if len(q) > 2 else 1]
                    for q in pts] or None)
        masks, stats = propagate.propagate(frames, key, key_mask, seg)
        stats["key_score"] = round(score, 3)
    else:
        # the window misses this chunk: an all-black chunk keeps the grid
        import analysis_jobs as aj
        src = spec["source"]
        w = aj._even(min(int(src["width"]), int(p.get("max_w") or 960)))
        h = aj._even(int(src["height"]) * w / float(src["width"]))
    t_model = time.perf_counter() - t_start
    n_out = max(1, int(round((ch["t1"] - ch["t0"]) * fps)))
    out = os.path.join(spec["outdir"], f"{ch['out_key']}.mp4")
    err = tempfile.TemporaryFile()
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "pipe:0",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", out],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
    black = np.zeros((h, w), np.uint8)
    hit, flick, prev = 0, [], None
    try:
        for k in range(n_out):
            t = ch["t0"] + k / fps
            m = black
            if masks and lo - 0.5 / fps <= t < hi + 0.5 / fps:
                j = min(len(masks) - 1, max(0, int(round((t - lo) * fps))))
                m = propagate.soften(masks[j], 0.8)
            if m is not black and m.max() > 127:
                hit += 1
            if prev is not None and (m.any() or prev.any()):
                d = np.abs(m.astype(np.int16) - prev.astype(np.int16))
                edge = (m > 20) ^ (m > 235)
                if edge.any():
                    flick.append(float(d[edge].mean()) / 255.0)
            prev = m
            enc.stdin.write(m.tobytes())
    finally:
        enc.stdin.close()
        rc = enc.wait()
    if rc != 0:
        err.seek(0)
        raise RuntimeError("object mask encoder failed: "
                           + err.read().decode("utf-8", "replace")[-200:])
    err.close()
    if not frames and hi > lo:
        raise analysis_jobs.DecodeError("no frames decoded in the window")
    return out, {"fps": round(fps, 6), "frames": n_out, "width": w,
                 "height": h, "padded": 0,
                 "ms_per_frame": round(1000.0 * t_model
                                       / max(1, len(frames)), 2),
                 "qa": {"flicker": round(float(np.mean(flick)), 5)
                        if flick else 0.0, "edge_chatter": None,
                        "coverage": round(hit / float(n_out), 4),
                        "stats": stats}}
