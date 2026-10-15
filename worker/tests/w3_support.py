"""Wave 3 test support: sidecar chunks written straight into a local store
(no model), synthetic decal clips, and a world_layer context — so the world
compositor is measured exactly, on CI, without Chromium or models."""

import json
import os
import subprocess
import time

import numpy as np


def put_chunk(store_root, src, kind, span, data=None, path=None,
              params=None, meta=None):
    """One ready chunk of ``kind`` over ``span`` (SOURCE seconds): JSON
    ``data`` or a file ``path``, with a valid manifest. Returns its key."""
    import sidecars
    sp = sidecars.spec(kind)
    resolved = sidecars.resolve_params(kind, params)
    store = sidecars.LocalStore(store_root)
    os.makedirs(store_root, exist_ok=True)
    key = sidecars.sidecar_key(kind, src.sha256, sp["model_id"],
                               sp["model_version"], resolved, span)
    if data is not None:
        raw = json.dumps(data).encode()
        store.put_bytes(sidecars.data_key(kind, src.sha256, key, sp["ext"]),
                        raw)
        size = len(raw)
    else:
        store.put_file(sidecars.data_key(kind, src.sha256, key, sp["ext"]),
                       path, sp["content_type"])
        size = os.path.getsize(path)
    m = {"manifest_version": sidecars.MANIFEST_VERSION, "key": key,
         "kind": kind, "model_id": sp["model_id"],
         "model_version": sp["model_version"], "params": resolved,
         "src_range": list(span), "fps": 25.0, "frames": 1, "width": 640,
         "height": 360, "format": sp["format"], "ext": sp["ext"],
         "bytes": size, "created_at": time.time(),
         "qa": {"flicker": None, "edge_chatter": None, "coverage": 1.0},
         "engine": kind, "source_sha": src.sha256}
    m.update(meta or {})
    store.put_bytes(sidecars.manifest_key(kind, src.sha256, key),
                    json.dumps(m).encode())
    return key


def source(sha="ab" * 32, w=640, h=360, fps=25.0, dur=6.0):
    import sidecars
    return sidecars.SourceRef(sha256=sha, storage_key="/nonexistent.mp4",
                              duration_s=dur, fps=fps, width=w, height=h)


def index_for(src):
    return {"video": {"width": src.width, "height": src.height,
                      "duration": src.duration_s, "fps": src.fps,
                      "sha256": src.sha256}, "words": []}


def square_clip(path, w=40, h=40, n=50, fps=25.0, colour="white"):
    """A qtrle ARGB clip: an opaque ``colour`` square, n frames."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c={colour}:s={w}x{h}:r={fps}:d={n / fps}",
         "-frames:v", str(n), "-vf", "format=argb", "-c:v", "qtrle",
         "-pix_fmt", "argb", path], check=True)
    import motion_engine
    return motion_engine.RenderedClip(path=path, x=10, y=10, w=w, h=h,
                                      frames=n, captured=n, cached=False,
                                      seconds=0.0)


def rgba_frames(path, w, h):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f",
                        "rawvideo", "-pix_fmt", "rgba", "-"],
                       capture_output=True, check=True)
    a = np.frombuffer(p.stdout, np.uint8)
    return a.reshape(-1, h, w, 4)


def alpha_centroid(frame):
    a = frame[..., 3]
    ys, xs = np.where(a > 127)
    if xs.size == 0:
        return None
    return float(xs.mean()), float(ys.mean())


def ctx(edl, src, W=640, H=360, fps=25.0, workdir=None):
    import schemas
    import world_layer
    from timeline import Timeline
    e = schemas.validate_edl(edl, src.duration_s).model_dump()
    tl = Timeline(e["keep"], e.get("inserts") or [], e.get("speed") or [])
    return world_layer.Ctx(e, W, H, fps, tl=tl, source=src,
                           index=index_for(src), workdir=workdir,
                           out_duration=tl.out_duration), e


def gray_mask_clip(path, w, h, fps, n, rect):
    """A gray H.264 mask clip: ``rect`` (px x0, y0, x1, y1) white."""
    x0, y0, x1, y1 = rect
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=black:s={w}x{h}:r={fps}:d={n / fps + 0.5}",
         "-vf", f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:"
         f"color=white:t=fill,format=gray", "-frames:v", str(n),
         "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv420p", path],
        check=True)
    return path
