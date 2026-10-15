"""Clean plates: the shot without its people, for shadow transfer (3C).

The ``plate.clean`` sidecar child: ``samples`` frames spread over the chunk,
their per-pixel temporal MEDIAN (a person who moves leaves the plate; one
who never moves stays in it — which is harmless: shadow transfer reads the
plate only OUTSIDE the subject). One PNG per chunk, source resolution
capped at ``max_w``.

Shadow transfer (world_layer.py) multiplies a plane decal by
``clip(blur(L_frame) / blur(L_plate), lo, hi)``: where the creator's real
shadow falls, the surface is darker than its clean self, and so is the type
painted on it. LaMa (ROADMAP §7 optional) is not used: the median is exact
wherever the plate was ever visible, and the ratio needs nothing more.
"""

import os
import time

import numpy as np

CODE_VERSION = 1


def child_clean(spec, ch):
    import cv2
    import analysis_jobs
    p = spec["params"]
    src = spec["source"]
    sw, sh = int(src["width"]), int(src["height"])
    w = analysis_jobs._even(min(sw, int(p.get("max_w") or 960)))
    h = analysis_jobs._even(sh * w / float(sw))
    k = max(5, int(p.get("samples") or 25))
    dur = float(ch["t1"]) - float(ch["t0"])
    fps = max(0.2, min(float(src["fps"] or 25.0), k / max(0.04, dur)))
    t_start = time.perf_counter()
    frames = list(analysis_jobs._decode(spec["src"], ch["t0"], dur, w, h,
                                        fps, k))
    if not frames:
        raise analysis_jobs.DecodeError("no frames decoded in the chunk")
    stack = np.stack(frames, axis=0)
    plate = np.median(stack, axis=0).astype(np.uint8)
    # how much of the plate actually changed over the chunk (a static
    # subject never leaves): reported, never used to refuse
    spread = float(np.mean(np.std(stack.astype(np.float32), axis=0)))
    out = os.path.join(spec["outdir"], f"{ch['out_key']}.png")
    cv2.imwrite(out, plate)
    t_model = time.perf_counter() - t_start
    return out, {"fps": round(fps, 6), "frames": 1, "width": w, "height": h,
                 "ms_per_frame": round(1000.0 * t_model / len(frames), 2),
                 "qa": {"flicker": None, "edge_chatter": None,
                        "coverage": 1.0, "spread": round(spread, 3),
                        "samples": len(frames)}}


def shadow_ratio(frame_l, plate_l, sigma, lo=0.25, hi=1.08):
    """The shadow-transfer multiplier from two luma images (float 0..1, same
    size): blur(frame) / blur(plate), clipped."""
    import cv2
    f = cv2.GaussianBlur(frame_l.astype(np.float32), (0, 0), sigma)
    q = cv2.GaussianBlur(plate_l.astype(np.float32), (0, 0), sigma)
    return np.clip(f / np.maximum(q, 0.02), lo, hi)
