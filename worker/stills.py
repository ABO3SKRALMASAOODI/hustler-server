"""The still picker: the frame to freeze for a poster (Wave 2, track 2E).

ROADMAP 2E: a freeze poster is a still in the top of its window for
sharpness, whose free region holds the planned lockup box, with no face
under that box. ``pick(path, t0, t1, box)`` decodes the window at
PICK_FPS, scores every frame (Laplacian sharpness, the box's clutter, a
face inside the box) and returns the winner with its percentile.
"""

import subprocess

import numpy as np

PICK_FPS = 10.0
PICK_W = 480


def _frames(path, t0, t1, w=PICK_W, fps=PICK_FPS):
    import library
    cmd = [library._ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t0):.3f}",
           "-i", path, "-t", f"{max(0.1, t1 - t0):.3f}", "-an", "-vf",
           f"fps={fps},scale={w}:-2:flags=area", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    probe = subprocess.run(
        [library._ffmpeg().replace("ffmpeg", "ffprobe"), "-v", "error",
         "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        sw, sh = (int(v) for v in probe.stdout.strip().split(",")[:2])
    except ValueError:
        sw, sh = 16, 9
    h = int(round(w * sh / sw / 2.0)) * 2
    n = len(raw) // (w * h * 3)
    return np.frombuffer(raw[:n * w * h * 3], np.uint8).reshape(n, h, w, 3)


def sharpness(gray):
    g = gray.astype(np.float32)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] +
           g[1:-1, 2:])
    return float(lap.var())


def clutter(gray, box):
    """Edge density inside ``box`` (frame fractions): how busy the region
    the lockup will sit on is."""
    h, w = gray.shape
    x0, y0, x1, y1 = (int(box[0] * w), int(box[1] * h), int(box[2] * w),
                      int(box[3] * h))
    reg = gray[max(0, y0):max(y0 + 2, y1), max(0, x0):max(x0 + 2, x1)]
    gy, gx = np.gradient(reg.astype(np.float32))
    return float((np.hypot(gx, gy) > 18.0).mean())


def _overlap(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def pick(path, t0, t1, box=None, fps=PICK_FPS):
    """{t, sharp, sharp_pct, clutter, face_overlap, faces} of the best
    poster frame in [t0, t1] (source seconds of ``path``)."""
    import library
    frames = _frames(path, t0, t1, fps=fps)
    if not len(frames):
        raise ValueError("no frames in that window")
    rows = []
    for i, rgb in enumerate(frames):
        gray = rgb.mean(axis=2)
        faces = []
        try:
            yn = library.yunet_faces(rgb)
            faces = [d["box"] for d in yn] if yn is not None else []
        except Exception:  # noqa: BLE001
            faces = []
        ov = max((_overlap(f, box) / max(1e-6, (f[2] - f[0]) *
                                         (f[3] - f[1]))
                  for f in faces), default=0.0) if box else 0.0
        rows.append({"t": round(t0 + i / fps, 3), "sharp": sharpness(gray),
                     "clutter": clutter(gray, box) if box else 0.0,
                     "face_overlap": round(ov, 3), "faces": faces})
    sh = np.array([r["sharp"] for r in rows])
    for r in rows:
        r["sharp_pct"] = round(float((sh <= r["sharp"]).mean()), 3)
    ok = [r for r in rows if r["face_overlap"] <= 0.0] or rows

    def score(r):
        return r["sharp_pct"] - 1.5 * r["clutter"]
    best = max(ok, key=score)
    return dict(best, n=len(rows))
