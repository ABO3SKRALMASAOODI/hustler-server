"""Footage-library helpers for track 1B's local-only acceptance tests.

``rvm_matte`` measures a SOURCE-space person matte for a trimmed VFL window
the way track 1A's ``matte.person`` sidecar does (raw RVM alpha, at most
960 px wide, H.264 CRF 20 gray, the recurrent state reset at every cut),
and caches it next to the fixtures (internal derived data, never
committed). The 1B stand-in for 1A (``w1b_stub1a.install_matte``) maps it
through the program geometry.

Internal only: fixture footage and references never leave this machine.
"""

import hashlib
import json
import os
import subprocess

import numpy as np

FIXTURES = os.environ.get("VALMERA_FIXTURES_DIR",
                          os.path.expanduser(
                              "~/Documents/Valmera/.valmera/fixtures"))
MODELS = os.path.expanduser("~/Documents/Valmera/.valmera/models")
CACHE = os.path.join(FIXTURES, "derived", "w1b_mattes")
RVM_MODEL = os.environ.get("MATTE_RVM_MODEL") or os.path.join(
    MODELS, "rvm_mobilenetv3_fp32.onnx")
CUT_DIFF = 28.0          # mean |delta| (0..255) of a 64x36 gray: a cut


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams",
                          "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate,nb_frames",
                          "-show_entries", "format=duration", "-of", "json",
                          path], capture_output=True, text=True,
                         check=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    return {"width": int(s["width"]), "height": int(s["height"]),
            "fps": num / den, "rate": s["r_frame_rate"],
            "duration": float(d["format"]["duration"])}


def _frames(path, w, h, start=None, dur=None):
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-an", "-vf", f"scale={w}:{h}:flags=area",
            "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = w * h * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def rvm_matte(src, *, max_w=960, threads=0, start=None, dur=None):
    """(mask_path, info) for ``src`` (cached by content)."""
    import cv2
    import personseg
    info = probe(src)
    w = min(max_w, info["width"])
    w -= w % 2
    h = int(round(info["height"] * w / info["width"]))
    h -= h % 2
    st = os.stat(src)
    key = hashlib.sha256(json.dumps([os.path.abspath(src), st.st_size,
                                     int(st.st_mtime), w, h, start, dur,
                                     "v1"]).encode()).hexdigest()[:16]
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f"{os.path.basename(src)}.{key}.mp4")
    meta = out + ".json"
    if os.path.exists(out) and os.path.exists(meta):
        return out, json.load(open(meta))
    sess = personseg.rvm_session_at(RVM_MODEL, threads)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt",
         "gray", "-s", f"{w}x{h}", "-r", info["rate"], "-i", "pipe:0",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-an", out + ".tmp.mp4"],
        stdin=subprocess.PIPE)
    prev, stream, cuts, n = None, None, [], 0
    for f in _frames(src, w, h, start, dur):
        small = cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (64, 36),
                           interpolation=cv2.INTER_AREA).astype(np.float32)
        if stream is None or (prev is not None and
                              float(np.abs(small - prev).mean()) > CUT_DIFF):
            if stream is not None:
                cuts.append(n)
            stream = personseg.rvm_stream_on(sess, w, h)
        prev = small
        a = stream.step(f, cv2)
        enc.stdin.write((a * 255.0 + 0.5).astype(np.uint8).tobytes())
        n += 1
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("mask encode failed")
    os.replace(out + ".tmp.mp4", out)
    data = {"src": src, "width": w, "height": h, "fps": info["fps"],
            "frames": n, "cuts": cuts}
    with open(meta, "w") as fh:
        json.dump(data, fh)
    return out, data


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(p, rvm_matte(p))
