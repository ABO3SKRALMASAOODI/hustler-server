"""Synthetic footage for track 1B's CI tests: a figure walking across a
textured room, with its EXACT source-space person matte.

``make_subject_source`` writes the picture (H.264 + a sine track) and the
matte (FFV1 gray, lossless) from the same geometry, so a subject effect can
be checked pixel for pixel against the mask that drove it.
"""

import os
import subprocess

import numpy as np


def _figure(w, h, cx, cy, s):
    """A person-like silhouette (head, neck, torso, arms) at 2x supersample,
    returned as a float alpha (h, w) in 0..1."""
    W2, H2 = w * 2, h * 2
    ys, xs = np.mgrid[0:H2, 0:W2].astype(np.float32) / 2.0
    head = ((xs - cx) / (0.11 * s)) ** 2 + ((ys - (cy - 0.42 * s))
                                           / (0.14 * s)) ** 2 <= 1.0
    neck = (np.abs(xs - cx) <= 0.05 * s) & (ys >= cy - 0.32 * s) & \
        (ys <= cy - 0.2 * s)
    tx = (xs - cx) / (0.30 * s)
    ty = (ys - (cy + 0.25 * s)) / (0.48 * s)
    torso = (tx ** 2 + ty ** 2 <= 1.0) & (ys >= cy - 0.22 * s)
    arm_l = (np.abs(xs - (cx - 0.33 * s)) <= 0.06 * s) & \
        (ys >= cy - 0.12 * s) & (ys <= cy + 0.42 * s)
    arm_r = (np.abs(xs - (cx + 0.33 * s)) <= 0.06 * s) & \
        (ys >= cy - 0.12 * s) & (ys <= cy + 0.42 * s)
    m = (head | neck | torso | arm_l | arm_r).astype(np.float32)
    return m.reshape(h, 2, w, 2).mean(axis=(1, 3))


def background(w, h, k):
    """A textured, coloured room that moves a little each frame."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    r = 90 + 60 * np.sin(xs / 23.0 + k * 0.07)
    g = 70 + 50 * np.cos(ys / 17.0 - k * 0.05)
    b = 110 + 40 * np.sin((xs + ys) / 31.0)
    checker = ((xs // 24 + ys // 24) % 2) * 25
    return np.clip(np.stack([r + checker, g + checker * 0.6,
                             b + checker * 0.3], -1), 0, 255)


def make_subject_source(path, mask_path, *, w=640, h=360, rate="25",
                        dur=6.0, colour=(222, 170, 140), cut_at=None):
    """Write the picture and its matte. The figure walks left to right;
    with ``cut_at`` (seconds) a second "shot" (another background and the
    figure on the other side) starts there."""
    if os.path.exists(path) and os.path.exists(mask_path):
        return path, mask_path
    num, _, den = str(rate).partition("/")
    fps = float(num) / float(den or 1)
    n = int(round(dur * fps))
    vid = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
         "rgb24", "-s", f"{w}x{h}", "-r", rate, "-i", "pipe:0", "-f",
         "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:"
         f"duration={dur}", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "10", "-g", str(int(fps)), "-pix_fmt", "yuv420p", "-c:a",
         "aac", "-shortest", path], stdin=subprocess.PIPE)
    msk = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
         "gray", "-s", f"{w}x{h}", "-r", rate, "-i", "pipe:0", "-c:v",
         "ffv1", "-pix_fmt", "gray", mask_path], stdin=subprocess.PIPE)
    col = np.array(colour, np.float32)
    cut_f = None if cut_at is None else int(round(cut_at * fps))
    for k in range(n):
        second = cut_f is not None and k >= cut_f
        bg = background(w, h, k + (500 if second else 0))
        if second:
            bg = bg[:, ::-1] * 0.7
        t = k / max(1, n - 1)
        cx = w * ((0.65 - 0.2 * t) if second else (0.3 + 0.4 * t))
        a = _figure(w, h, cx, h * 0.55, h * 0.75)[..., None]
        shade = 0.85 + 0.15 * np.linspace(0, 1, w, dtype=np.float32)[None, :,
                                                                         None]
        img = bg * (1 - a) + col * shade * a
        vid.stdin.write(np.clip(img, 0, 255).astype(np.uint8).tobytes())
        msk.stdin.write(np.clip(a[..., 0] * 255 + 0.5, 0, 255)
                        .astype(np.uint8).tobytes())
    for p in (vid, msk):
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError("synthetic source failed")
    return path, mask_path


def make_still(path, w=640, h=360, colour=(30, 120, 220)):
    """A plate image (a gradient card) for background tests."""
    if os.path.exists(path):
        return path
    from PIL import Image
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.stack([np.full((h, w), colour[0], np.float32) + xs / w * 30,
                    np.full((h, w), colour[1], np.float32) + ys / h * 30,
                    np.full((h, w), colour[2], np.float32)], -1)
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(path)
    return path


def make_clip(path, w=640, h=360, rate="25", dur=4.0):
    """A plate video (testsrc2) for background tests."""
    if os.path.exists(path):
        return path
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={w}x{h}:r={rate}:d={dur}", "-c:v",
                    "libx264", "-preset", "ultrafast", "-crf", "12",
                    "-pix_fmt", "yuv420p", path], check=True)
    return path
