"""Comparison boards: 2-up stills, side-by-side clips, landing strips.

Internal only (they show reference frames). Everything is drawn with PIL
and composited with ffmpeg's hstack/overlay, so no drawtext/libfreetype
build is needed.
"""

import os
import shutil
import subprocess
import tempfile

import numpy as np

import qa_metrics

BG = (18, 18, 20)
FG = (238, 238, 232)
ACCENT = (255, 201, 64)


def _font(size):
    from PIL import ImageFont
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
    return ImageFont.load_default()


def grab(path, t, height):
    """RGB frame at ``t`` seconds (first frame with PTS >= t), scaled to
    ``height`` (aspect kept, even width)."""
    info = qa_metrics.probe(path)
    w = max(2, int(round(info["width"] * height / info["height"] / 2)) * 2)
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.4f}",
         "-i", path, "-frames:v", "1", "-vf", f"scale={w}:{height}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    if len(out) < w * height * 3:
        return np.zeros((height, w, 3), np.uint8)
    return np.frombuffer(out[:w * height * 3], np.uint8).reshape(
        height, w, 3)


def _caption(img, lines, y, x=16, size=22, color=FG):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    f = _font(size)
    for ln in lines:
        d.text((x, y), ln, fill=color, font=f)
        y += int(size * 1.35)
    return y


def still_2up(left, right, out, *, height=720, title=None, footer=()):
    """left/right: (path, t, label). Writes a PNG: the two frames side by
    side at one height, labels above, ``footer`` lines below."""
    from PIL import Image
    a = grab(left[0], left[1], height)
    b = grab(right[0], right[1], height)
    gap, top = 24, 70
    bottom = 40 + 30 * max(1, len(footer))
    W = a.shape[1] + b.shape[1] + 3 * gap
    H = top + height + bottom
    img = Image.new("RGB", (W, H), BG)
    img.paste(Image.fromarray(a), (gap, top))
    img.paste(Image.fromarray(b), (2 * gap + a.shape[1], top))
    if title:
        _caption(img, [title], 12, x=gap, size=26, color=ACCENT)
    _caption(img, [f"{left[2]}  @{left[1]:.2f}s"], 44, x=gap, size=18)
    _caption(img, [f"{right[2]}  @{right[1]:.2f}s"], 44,
             x=2 * gap + a.shape[1], size=18)
    _caption(img, list(footer), top + height + 14, x=gap, size=20)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img.save(out)
    return out


def strip(path, center_t, out, *, before=3, after=6, height=270, label=""):
    """The landing strip: frames -before..+after around ``center_t``,
    frame numbers burned in (the proof_device format)."""
    from PIL import Image
    info = qa_metrics.probe(path)
    fps = info["fps"] or 30.0
    c = int(round(center_t * fps))
    want = list(range(max(0, c - before), c + after + 1))
    w = max(2, int(round(info["width"] * height / info["height"] / 2)) * 2)
    idx, frames = qa_metrics.read_frames(path, size=(w, height),
                                         frames=set(want))
    img = Image.new("RGB", (len(idx) * (w + 6) + 6, height + 60), BG)
    for k, (f, fr) in enumerate(zip(idx, frames)):
        img.paste(Image.fromarray(fr), (6 + k * (w + 6), 40))
        _caption(img, [f"{f - c:+d}" + (" *" if f == c else "")], 10,
                 x=8 + k * (w + 6), size=18,
                 color=ACCENT if f == c else FG)
    if label:
        _caption(img, [label], height + 42, x=8, size=14)
    img.save(out)
    return out


def _label_png(text, width, path, size=26):
    from PIL import Image
    img = Image.new("RGBA", (width, 44), (0, 0, 0, 160))
    _caption(img, [text], 8, x=12, size=size)
    img.save(path)
    return path


def clip_2up(left, right, out, *, height=720, audio="left", crf=23):
    """left/right: (path, t0, t1, label). A side-by-side MP4 at one height,
    as long as the shorter span, each side labelled. Audio from ``audio``
    ('left', 'right' or None)."""
    la = left[2] - left[1]
    right_len = right[2] - right[1]
    dur = max(0.04, min(la, right_len))
    il = qa_metrics.probe(left[0])
    ir = qa_metrics.probe(right[0])
    wl = max(2, int(round(il["width"] * height / il["height"] / 2)) * 2)
    wr = max(2, int(round(ir["width"] * height / ir["height"] / 2)) * 2)
    tmp = tempfile.mkdtemp(prefix="refbench_board_")
    pl = _label_png(left[3], wl, os.path.join(tmp, "l.png"))
    pr = _label_png(right[3], wr, os.path.join(tmp, "r.png"))
    fps = il["fps"] or 30.0
    graph = (f"[0:v]trim=start={left[1]:.4f}:duration={dur:.4f},"
             f"setpts=PTS-STARTPTS,scale={wl}:{height},fps={fps:.5f},"
             f"setsar=1[a];"
             f"[1:v]trim=start={right[1]:.4f}:duration={dur:.4f},"
             f"setpts=PTS-STARTPTS,scale={wr}:{height},fps={fps:.5f},"
             f"setsar=1[b];"
             f"[a][2:v]overlay=0:0[al];[b][3:v]overlay=0:0[bl];"
             f"[al][bl]hstack=inputs=2,format=yuv420p[v]")
    maps = ["-map", "[v]"]
    src = {"left": (0, left[1]), "right": (1, right[1])}.get(audio)
    if src is not None and (il if src[0] == 0 else ir)["has_audio"]:
        graph += (f";[{src[0]}:a]atrim=start={src[1]:.4f}:"
                  f"duration={dur:.4f},asetpts=PTS-STARTPTS[au]")
        maps += ["-map", "[au]", "-c:a", "aac", "-b:a", "160k"]
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", left[0],
           "-i", right[0], "-i", pl, "-i", pr, "-filter_complex", graph] + \
        maps
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-movflags", "+faststart", out]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    try:
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def contact(path, out, *, n=12, cols=6, height=180, t0=None, t1=None,
            title=""):
    """n evenly spaced frames of a video, timestamps burned in."""
    from PIL import Image
    info = qa_metrics.probe(path)
    a = t0 or 0.0
    b = t1 or info["duration"]
    ts = [a + (b - a) * (i + 0.5) / n for i in range(n)]
    frames = [grab(path, t, height) for t in ts]
    w = frames[0].shape[1]
    rows = (n + cols - 1) // cols
    img = Image.new("RGB", (cols * (w + 6) + 6, rows * (height + 30) + 40),
                    BG)
    _caption(img, [title], 8, x=8, size=20, color=ACCENT)
    for k, (t, fr) in enumerate(zip(ts, frames)):
        x = 6 + (k % cols) * (w + 6)
        y = 40 + (k // cols) * (height + 30)
        img.paste(Image.fromarray(fr), (x, y + 24))
        _caption(img, [f"{t:.2f}s"], y + 2, x=x + 2, size=16)
    img.save(out)
    return out
