"""Frames on boards: one ffmpeg seek per frame, so this works on a path or a presigned URL."""
import io
import os
import subprocess

from PIL import Image, ImageFont

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts", "InterDisplay-Bold.ttf")
BG, FG, DIM = (14, 14, 18), (240, 240, 244), (160, 160, 170)
_fonts = {}


def font(size):
    if size not in _fonts:
        try:
            _fonts[size] = ImageFont.truetype(FONT, size)
        except OSError:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def grab(path, t, width=None, height=None):
    """One frame as a PIL image, or None."""
    scale = f"scale={width}:-2" if width else f"scale=-2:{height}"
    cmd = [FFMPEG, "-nostdin", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1",
           "-vf", scale, "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0 or not r.stdout:
        return None
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def fit(im, box_w, box_h):
    """Centred on a dark cell, keeping the whole frame."""
    out = Image.new("RGB", (box_w, box_h), BG)
    w, h = im.size
    s = min(box_w / w, box_h / h)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    out.paste(im.resize((nw, nh), Image.LANCZOS), ((box_w - nw) // 2, (box_h - nh) // 2))
    return out


def save(img, path, quality=80):
    img.convert("RGB").save(path, "JPEG", quality=quality, optimize=True)
