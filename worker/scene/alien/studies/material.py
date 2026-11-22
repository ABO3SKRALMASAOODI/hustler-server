"""Make source-derived plates for the scene compositor, in programme coordinates."""

import os
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from .. import sense


def ease(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def mix(a, b, t):
    return cv2.addWeighted(a, 1.0 - float(t), b, float(t), 0.0)


def moments(l1, work, frames):
    return [sense.shot_frames(l1, work, f, f + 1)[f] for f in frames]


def face(rgb, model):
    """Eye midpoint, eye separation and face bounds in this picture."""
    small = cv2.resize(rgb, (640, round(640 * rgb.shape[0] / rgb.shape[1])))
    det = cv2.FaceDetectorYN_create(str(model), "", (640, small.shape[0]), 0.60)
    _, rows = det.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    if rows is None:
        raise ValueError("No visible face in the selected exposure")
    r = max(rows, key=lambda row: row[2] * row[3]) * (rgb.shape[1] / 640)
    eyes = r[4:8].reshape(2, 2)
    return eyes.mean(0), np.linalg.norm(eyes[1] - eyes[0]), r[:4]


def paper(shape, colour, seed=41):
    h, w = shape[:2]
    rng = np.random.default_rng(seed)
    fibre = rng.normal(0, 1.5, (h, w, 1))
    return np.uint8(np.clip(np.asarray(colour) + fibre, 0, 255))


def word(ident, value, voice, start, end, x, y, ink):
    return {
        "k": "word",
        "id": ident,
        "t": value,
        "v": voice,
        "f": [start, end],
        "on": start,
        "role": "display",
        "x": x,
        "y": y,
        "ink": ink,
        "z": 8,
        "s": 1.0,
    }


def plate(l1, work, ask, a, z, pictures, log, items=(), voices=None):
    """Write only the altered span. Unaffected shots, speech and captions stay live."""
    name = ask["id"]
    root = Path(work) / "alien"
    root.mkdir(parents=True, exist_ok=True)
    fps = float(l1["fps"])
    w, h = l1["frame"]
    fd, temp = tempfile.mkstemp(prefix="study-", suffix=".mp4", dir=root)
    os.close(fd)
    cmd = [
        sense.ff(),
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{w}x{h}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-threads",
        "2",
        "-c:v",
        "libx264",
        "-crf",
        "13",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        temp,
    ]
    try:
        with subprocess.Popen(cmd, stdin=subprocess.PIPE) as enc:
            count = 0
            for rgb in pictures:
                if rgb.shape != (h, w, 3) or rgb.dtype != np.uint8:
                    raise ValueError("Picture does not match the scene canvas")
                enc.stdin.write(rgb.tobytes())
                count += 1
            enc.stdin.close()
            if enc.wait() or count != z - a:
                raise ValueError(f"Incomplete plate at {count} of {z - a} frames")
    except BaseException:
        os.unlink(temp)
        raise
    img = f"{name}.exposure"
    duration = (z - a) / fps
    kept, gone, _ = sense.carve(l1, a, z)
    return {
        "items": kept + [{"k": "shot", "id": img, "pic": img, "f": [a, z], "src": [0.0, duration]}, *items],
        "drop": gone,
        "voices": voices or {},
        "log": log,
        "assets": {
            img: {
                "kind": "video",
                "path": os.path.relpath(temp, work),
                "w": w,
                "h": h,
                "fps": fps,
                "dur": duration,
            }
        },
    }


def take_frames(l1, work, a, z):
    """Read contiguous moving shots in one decode, even when a gesture lasts seconds."""
    fps = l1["fps"]
    width, height = l1["frame"]
    shots = sorted((it for it in l1["items"] if it["k"] == "shot"), key=lambda it: it["f"][0])
    for shot in shots:
        start, end = max(a, shot["f"][0]), min(z, shot["f"][1])
        if start >= end:
            continue
        path = sense.path_of(l1, work, shot["pic"])
        info = sense.probe(path)
        first = sense.src_time(shot, start, fps)
        step = (sense.src_time(shot, end, fps) - first) / (end - start)
        if info["fps"] == 0 or step <= 0:
            still = sense.still(path, first)
            stream = iter([still] * (end - start))
        else:
            stream = sense.frames(path, first, end - start, 1 / step)
        last = None
        try:
            for f in range(start, end):
                last = next(stream, last)
                if last is None:
                    raise ValueError("Empty source span")
                crop = sense.crop_at(shot, f, info["w"], info["h"], width, height)
                if list(crop) == [0.0, 0.0, 1.0, 1.0] and last.shape[:2] == (height, width):
                    yield last
                else:
                    yield sense.view(last, crop, width, height)
        finally:
            if hasattr(stream, "close"):
                stream.close()


def serif(h, fraction=0.1):
    return {"font": "InstrumentSerif-Regular.ttf", "px": round(h * fraction), "shaped": True}


def sans(h, fraction=0.042):
    return {"font": "InterDisplay-Bold.ttf", "px": round(h * fraction), "shaped": True}
