"""Track 1A test support: synthetic footage whose person matte is known
EXACTLY, its matte.person sidecar chunks written straight into a local
store (no model), and a render hook that shows the program matte.

The synthetic "subject" is a white rectangle moving across a mid-gray
plate (optionally jumping to another place at a camera cut). Its matte is
the same rectangle, white on black, drawn by the same ffmpeg expression at
the sidecar's resolution and rate, frame j at source t0 + j / fps, exactly
as analysis_jobs._child_matte writes a chunk. A render of the program
matte (``show_matte``) must then be the picture's own rectangle: frame for
frame, cut for cut, reframe for reframe.
"""

import json
import os
import subprocess
import time

import numpy as np

GRAY = 96          # plate luma (the rectangle is 255; the matte 255 on 0)


def rect_expr(cut_at=None, x0=40.0, speed=60.0, x_after=380.0, t="t"):
    """ffmpeg x expression (of ``t``, source seconds) of the rectangle."""
    if cut_at is None:
        return f"{x0}+{t}*{speed}"
    return (f"if(lt({t},{cut_at}),{x0}+{t}*{speed},"
            f"{x_after}-({t}-{cut_at})*{speed})")


def rect_at(t, w=640, cut_at=None, x0=40.0, speed=60.0, x_after=380.0,
            scale=1.0):
    x = (x0 + t * speed) if cut_at is None or t < cut_at else \
        (x_after - (t - cut_at) * speed)
    return x * scale


def make_source(path, *, w=640, h=360, fps=25, dur=8.0, rw=80, rh=160,
                y=100, cut_at=None):
    """The synthetic source (near-lossless H.264, keyframe every frame so
    any trim is exact)."""
    x = rect_expr(cut_at)
    graph = (f"color=c=0x{GRAY:02x}{GRAY:02x}{GRAY:02x}:s={w}x{h}:r={fps}:"
             f"d={dur}[bg];color=c=white:s={rw}x{rh}:r={fps}:d={dur}[sq];"
             f"[bg][sq]overlay=x='{x}':y={y}:eof_action=pass,"
             "format=yuv420p[v]")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-filter_complex", graph,
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
         "-map", "[v]", "-map", "0:a", "-t", str(dur),
         "-c:v", "libx264", "-crf", "4", "-g", "1", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", path], check=True)
    return path


def write_chunks(store_root, src, *, rw=80, rh=160, y=100, cut_at=None,
                 params=None, model=True):
    """matte.person chunks for every grid chunk of ``src`` (a
    sidecars.SourceRef), drawn like the source's rectangle, with valid
    manifests. Returns the chunk keys."""
    import sidecars
    sp = sidecars.spec("matte.person")
    resolved = sidecars.resolve_params("matte.person", params)
    store = sidecars.LocalStore(store_root)
    os.makedirs(store_root, exist_ok=True)
    sw, sh = int(src.width), int(src.height)
    mw = min(sw, int(resolved.get("max_w") or 960))
    mw -= mw % 2
    mh = int(round(sh * mw / float(sw)))
    mh -= mh % 2
    k = mw / float(sw)
    fps = min(float(resolved.get("fps_max") or 30.0), float(src.fps))
    keys = []
    for t0, t1 in sidecars.chunk_ranges(src.shots, src.duration_s):
        key = sidecars.sidecar_key("matte.person", src.sha256,
                                   sp["model_id"], sp["model_version"],
                                   resolved, (t0, t1))
        n = max(1, int(round((t1 - t0) * fps)))
        tmp = os.path.join(store_root, f"tmp_{key[:10]}.mp4")
        graph = (f"color=c=black:s={mw}x{mh}:r={fps}:d={n / fps + 1}[bg];"
                 f"color=c=white:s={int(round(rw * k))}x{int(round(rh * k))}"
                 f":r={fps}:d={n / fps + 1}[sq];"
                 f"[bg][sq]overlay=x='({rect_expr(cut_at, t=f'(t+{t0})')})*{k}':"
                 f"y={y * k},format=gray,trim=end_frame={n}[v]")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex",
                        graph, "-map", "[v]", "-c:v", "libx264", "-crf",
                        "20", "-pix_fmt", "yuv420p", "-r", f"{fps:.6f}",
                        tmp], check=True)
        store.put_file(sidecars.data_key("matte.person", src.sha256, key),
                       tmp, "video/mp4")
        manifest = {
            "manifest_version": sidecars.MANIFEST_VERSION, "key": key,
            "kind": "matte.person", "model_id": sp["model_id"],
            "model_version": sp["model_version"], "params": resolved,
            "src_range": [t0, t1], "fps": round(fps, 6), "frames": n,
            "width": mw, "height": mh, "format": sp["format"],
            "ext": sp["ext"], "bytes": os.path.getsize(tmp),
            "created_at": time.time(),
            "qa": {"flicker": 0.01, "edge_chatter": 0.0, "coverage": []},
            "engine": "rvm_mobilenetv3" if model else "u2net",
            "source_sha": src.sha256, "warmup_s": 0.0}
        store.put_bytes(sidecars.manifest_key("matte.person", src.sha256,
                                              key),
                        json.dumps(manifest).encode("utf-8"))
        os.remove(tmp)
        keys.append(key)
    return keys


def show_matte(monkeypatch):
    """Make every behind-subject composite output its MASK as the picture
    (the program matte the composite would use, full frame, gray)."""
    import motion_layer

    def _show(parts, vlabel, mask_label, j, **kw):
        # the mask's pixels laid opaque over the picture: every frame keeps
        # the picture's timestamps and metadata (a source-fed picture card
        # downstream selects its frames by their layout tags)
        parts.append(f"[{mask_label}]format=yuv420p[shm{j}m]")
        parts.append(f"[{vlabel}][shm{j}m]overlay=0:0:eof_action=pass"
                     f"[shm{j}]")
        return f"shm{j}"
    monkeypatch.setattr(motion_layer, "composite_behind", _show)


def frames(path, size=None):
    """Decoded luma planes (uint8 HxW) of every frame."""
    import media
    info = media.probe(path)
    w, h = size or (int(info["width"]), int(info["height"]))
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                        f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt",
                        "gray", "-"], capture_output=True, check=True)
    a = np.frombuffer(p.stdout, np.uint8)
    return a.reshape(-1, h, w)


def iou(a, b):
    u = np.logical_or(a, b).sum()
    return 1.0 if u == 0 else float(np.logical_and(a, b).sum()) / float(u)
