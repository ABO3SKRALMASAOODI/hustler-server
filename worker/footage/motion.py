"""How much a source shot moves by content, on the proxy already downloaded (no model, no API): dense optical flow
(Farneback) at 15 fps on 270x480 (480x270 for a landscape source), the camera's global similarity removed by a RANSAC
fit, the 90th percentile of what is left per frame pair. The same signal as the stories skill's reel.clip_motion and
the rater's residual. Used only when a caller asks for motion first (run.find motion_first)."""
import os
import subprocess

FLOOR = 1.0               # px a frame at 270x480: under it a shot reads as a still
FPS = 15.0
MAX_SHOT_S = 8.0          # a long take is read over its first 8 s
_CACHE = {}


def curve(frames):
    """Per frame pair, the 90th percentile of the flow left after the global similarity."""
    import cv2
    import numpy as np
    if len(frames) < 2:
        return []
    ys, xs = np.mgrid[8:frames.shape[1]:16, 8:frames.shape[2]:16]
    pts = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
    out = []
    for k in range(len(frames) - 1):
        a, b = frames[k], frames[k + 1]
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        d = flow[pts[:, 1].astype(int), pts[:, 0].astype(int)]
        M, _ = cv2.estimateAffinePartial2D(pts, pts + d, method=cv2.RANSAC, ransacReprojThreshold=1.0)
        res = np.linalg.norm(d - (pts @ M[:, :2].T + M[:, 2] - pts), axis=1) if M is not None else np.linalg.norm(d, axis=1)
        out.append(float(np.percentile(res, 90)))
    return out


def _size(proxy):
    """270x480, or 480x270 for a landscape source (the rater's frame for the clip as it plays)."""
    try:
        import cv2
        cap = cv2.VideoCapture(proxy)
        land = cap.get(cv2.CAP_PROP_FRAME_WIDTH) > cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        cap.release()
    except Exception:  # noqa: BLE001
        land = True
    return (480, 270) if land else (270, 480)


def of_shot(proxy, a, b, ffmpeg=None):
    """{"p90": the median of the shot's curve, "peak": the time (s, in the source) of its most moving half second}."""
    import numpy as np
    key = (proxy, round(a, 3), round(b, 3))
    if key in _CACHE:
        return _CACHE[key]
    ff = ffmpeg or os.environ.get("FOOTAGE_FFMPEG") or "ffmpeg"
    dur = min(b - a, MAX_SHOT_S)
    w, h = _size(proxy)
    try:
        pr = subprocess.run([ff, "-v", "error", "-ss", f"{a:.3f}", "-i", proxy, "-t", f"{dur:.3f}", "-vf",
                             f"fps={FPS},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            capture_output=True, timeout=120)
        n = len(pr.stdout) // (w * h)
        fr = np.frombuffer(pr.stdout[:n * w * h], np.uint8).reshape(n, h, w)
        cv = curve(fr)
    except Exception:  # noqa: BLE001 - an unreadable shot counts as still
        cv = []
    if not cv:
        res = dict(p90=0.0, peak=None)
    else:
        k = max(int(0.5 * FPS), 1)
        best = max(range(max(len(cv) - k + 1, 1)), key=lambda i: sum(cv[i:i + k]))
        res = dict(p90=round(float(np.median(cv)), 3), peak=round(a + (best + k / 2) / FPS, 3))
    _CACHE[key] = res
    return res


def of_shots(proxy, shots, log=print, min_s=0.3):
    """{(a, b): of_shot} for every shot of a source of at least min_s seconds (an empty dict without a proxy)."""
    if not proxy or not os.path.exists(str(proxy)):
        return {}
    return {(a, b): of_shot(proxy, a, b) for a, b in shots if b - a >= min_s}
