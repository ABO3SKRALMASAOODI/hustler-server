"""How much a source shot moves by content, on the proxy already downloaded (no model, no API): dense optical flow
(Farneback) at 15 fps on 270x480 (480x270 for a landscape source), the camera's global similarity removed by a RANSAC
fit, the 90th percentile of what is left per frame pair. The same signal as the stories skill's reel.clip_motion and
the rater's residual. Used only when a caller asks for motion first (run.find motion_first)."""
import fcntl
import json
import math
import os
import subprocess

from . import measurements

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
    key = (proxy, measurements.stamp(proxy), ffmpeg or os.environ.get("FOOTAGE_FFMPEG"), a, b)
    if key in _CACHE:
        return _CACHE[key]
    ff = ffmpeg or os.environ.get("FOOTAGE_FFMPEG") or "ffmpeg"
    dur = min(b - a, MAX_SHOT_S)
    w, h = _size(proxy)
    try:
        pr = subprocess.run([ff, "-v", "error", "-ss", f"{a:.3f}", "-i", proxy, "-t", f"{dur:.3f}", "-vf",
                             f"fps={FPS},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            capture_output=True, timeout=120)
        pr.check_returncode()
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
    if cv:
        _CACHE[key] = res
    return res


def _record(proxy):
    if not os.environ.get("FOOTAGE_KEEP_SOURCES"):
        return None
    try:
        import cv2
    except ImportError:
        return None
    return measurements.record(proxy, "motion", ("farneback-residual-v1", FPS, MAX_SHOT_S, cv2.__version__),
                               os.environ.get("FOOTAGE_FFMPEG") or "ffmpeg")


def _read(path):
    try:
        rows = json.loads(path.read_text())
        return rows if isinstance(rows, dict) else {}
    except (OSError, ValueError):
        return {}


def _valid(value, a, b):
    return (isinstance(value, dict) and isinstance(value.get("p90"), (float, int))
            and math.isfinite(value["p90"]) and value["p90"] >= 0
            and isinstance(value.get("peak"), (float, int)) and a <= value["peak"] <= b)


def moving_intervals(proxy, shots):
    if not proxy or not os.path.isfile(proxy):
        return set()
    path = _record(proxy)
    rows = _read(path) if path is not None else {}
    return {(a, b) for a, b in shots
            if _valid(value := rows.get(json.dumps([float(a), float(b)])), a, b)
            and value["p90"] >= FLOOR}


def of_shots(proxy, shots, log=print, min_s=0.3, stats=None):
    """Measure each eligible interval once. A restarted scan can have a different filename."""
    spans = list(dict.fromkeys((a, b) for a, b in shots if b-a >= min_s))
    if not spans or not proxy or not os.path.isfile(proxy):
        return {}
    path = _record(proxy)
    if path is None:
        if stats is not None:
            stats["measured"] += len(spans)
        return {sh: of_shot(proxy, *sh) for sh in spans}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows = _read(path)
            out = {}
            for a, b in spans:
                key = json.dumps([float(a), float(b)])
                value = rows.get(key)
                if not _valid(value, a, b):
                    value = of_shot(proxy, a, b)
                    if _valid(value, a, b):
                        rows[key] = value
                        part = path.with_suffix(".part")
                        part.write_text(json.dumps(rows))
                        part.replace(path)
                    if stats is not None:
                        stats["measured"] += 1
                elif stats is not None:
                    stats["reused"] += 1
                out[a, b] = value
            return out
    except OSError as exc:
        log(f"motion cache unavailable: {exc}")
        return {sh: of_shot(proxy, *sh) for sh in spans}
