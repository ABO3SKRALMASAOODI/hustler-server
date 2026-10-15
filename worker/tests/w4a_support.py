"""Track 4A test support: synthetic footage whose DEPTH is known exactly,
its ``depth.small`` sidecar chunks written straight into a local store (no
model, no torch: CI-safe), and frame readers.

The synthetic source is split down the middle: the left half is the NEAR
plane (depth 1), the right half the FAR plane (depth 0), both textured
(testsrc2) so blur is measurable. A still (an asset image) gets the same
split.
"""

import json
import os
import subprocess
import time

import numpy as np


def make_split_source(path, *, w=640, h=360, rate="25", dur=8.0):
    """Two textured halves (testsrc2 left, a mirrored testsrc2 right) with
    sine audio; lossless-ish H.264, a keyframe every frame."""
    if os.path.exists(path):
        return path
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=s={w}x{h}:r={rate}:d={dur}", "-f", "lavfi", "-i",
         f"sine=frequency=330:sample_rate=48000:duration={dur}",
         "-filter_complex", "[0:v]split[a][b];[b]hflip,negate[bn];"
         f"[a][bn]blend=all_expr='if(lt(X,W/2),A,B)',format=yuv420p[v]",
         "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "6",
         "-g", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         path], check=True)
    return path


def make_index_source(path, *, w=64, h=64, rate="25", dur=6.0):
    """A source whose frame k has luma 4 * (k mod 50) + 20: the picture
    names its own frame index (time devices are checked frame by frame)."""
    if os.path.exists(path):
        return path
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=black:s={w}x{h}:r={rate}:d={dur}", "-f", "lavfi", "-i",
         f"sine=frequency=330:sample_rate=48000:duration={dur}",
         "-vf", "geq=lum='4*mod(N\\,50)+20':cb=128:cr=128,format=yuv420p",
         "-c:v", "libx264", "-crf", "0", "-g", "1", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", path], check=True)
    return path


def make_split_still(path, w=960, h=540):
    from PIL import Image
    rng = np.random.default_rng(7)
    a = (rng.random((h, w, 3)) * 255).astype(np.uint8)
    # coarse texture so blur and parallax read
    a = np.kron(a[::8, ::8], np.ones((8, 8, 1), np.uint8))[:h, :w]
    a[:, : w // 2, 0] = np.maximum(a[:, : w // 2, 0], 150)
    Image.fromarray(a).save(path)
    return path


def split_depth(h, w, near_left=True):
    d = np.zeros((h, w), np.float32)
    if near_left:
        d[:, : w // 2] = 1.0
    else:
        d[:, w // 2:] = 1.0
    return d


def write_depth_chunks(store_root, src, depth_fn=None, *, fps=8.0,
                       max_side=518, params=None):
    """depth.small chunks over every grid chunk of ``src`` (a SourceRef),
    each keyframe the map ``depth_fn(t, h, w)`` (default: the split), with
    valid manifests. Returns the chunk keys."""
    import sidecars
    sp = sidecars.spec("depth.small")
    resolved = sidecars.resolve_params("depth.small", params)
    store = sidecars.LocalStore(store_root)
    os.makedirs(store_root, exist_ok=True)
    sw, sh = int(src.width), int(src.height)
    k = int(resolved.get("max_side") or max_side) / float(max(sw, sh))
    w = max(2, int(round(sw * k)) // 2 * 2)
    h = max(2, int(round(sh * k)) // 2 * 2)
    fps = float(resolved.get("fps") or fps)
    keys = []
    spans = sidecars.chunk_ranges(src.shots, src.duration_s) \
        if src.duration_s > 0.05 else [(0.0, 0.04)]
    for t0, t1 in spans:
        key = sidecars.sidecar_key("depth.small", src.sha256,
                                   sp["model_id"], sp["model_version"],
                                   resolved, (t0, t1))
        n = max(1, int(round((t1 - t0) * fps)))
        ts = [t0 + i / fps for i in range(n)]
        maps = [(depth_fn or (lambda t, hh, ww: split_depth(hh, ww)))(
            t, h, w) for t in ts]
        q = (np.clip(np.stack(maps), 0, 1) * 65535 + 0.5).astype(np.uint16)
        tmp = os.path.join(store_root, f"tmp_{key[:10]}.npz")
        np.savez_compressed(tmp, depth=q, t=np.asarray(ts, np.float32),
                            near_is_high=np.asarray(1, np.uint8))
        store.put_file(sidecars.data_key("depth.small", src.sha256, key,
                                         "npz"), tmp,
                       "application/octet-stream")
        manifest = {
            "manifest_version": sidecars.MANIFEST_VERSION, "key": key,
            "kind": "depth.small", "model_id": sp["model_id"],
            "model_version": sp["model_version"], "params": resolved,
            "src_range": [t0, t1], "fps": fps, "frames": n, "width": w,
            "height": h, "format": sp["format"], "ext": sp["ext"],
            "bytes": os.path.getsize(tmp), "created_at": time.time(),
            "qa": {"flicker": 0.0, "edge_chatter": None, "coverage": None},
            "engine": "depth_anything_v2_small", "source_sha": src.sha256}
        store.put_bytes(sidecars.manifest_key("depth.small", src.sha256,
                                              key),
                        json.dumps(manifest).encode("utf-8"))
        os.remove(tmp)
        keys.append(key)
    return keys


def index_for(src, info, words=()):
    """An index naming its source (the sha in a thumb key, as indexed
    projects do) so a render reads its sidecars."""
    return {"words": list(words), "silences": [], "sentences": [],
            "shots": [{"start": 0.0,
                       "thumb_key": f"tiles/7/{src.sha256}/s0.jpg"}],
            "video": {"width": info["width"], "height": info["height"],
                      "fps": info["fps"], "duration": info["duration"],
                      "sha256": src.sha256}}


def frames_rgb(path, size=None):
    import qa_metrics
    _idx, fr = qa_metrics.read_frames(path, size=size)
    return [np.asarray(f) for f in fr]


def lap_var(gray):
    """Laplacian variance (sharpness) of a gray image."""
    import cv2
    return float(cv2.Laplacian(np.asarray(gray, np.float32),
                               cv2.CV_32F).var())


def gray(rgb):
    a = np.asarray(rgb, np.float32)
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
