"""Build the compact animated Valmera end card used by every export.

Round 102 — v7. The previous card asked a reel viewer to read five stacked
elements and made the robot a third of the screen. This version is a compact
signature with one reading path:

                         Edited by
                    [robot]  Valmera
                       www.valmera.io

"Edited by" arrives first and holds alone for about half a second. The brand
lockup and URL then resolve in sequence, hold long enough to register, and fade
to black at five seconds. The exported MP4 is the production asset; the PNG is
its fully-revealed poster and the renderer's graceful fallback if the animation
is ever missing from a build.

The robot is the existing white navbar mark. Only its antenna stalk is lifted
to white so the red ball stays visibly attached on black; every other pixel is
preserved. This is an established correction shared with the previous card,
not a generated or replacement logo.
"""
import math
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont


WHITE = (255, 255, 255, 255)
SOFT_WHITE = (255, 255, 255, 215)
BLACK_RGB = (0, 0, 0)

# 1080p is the native social-video delivery size. The renderer scales this
# square-pixel master to fit any output without cropping it.
CARD_W, CARD_H = 1080, 1920
FPS = 30
DURATION_S = 5.0

# The signature occupies about one fifth of a vertical reel instead of most of
# the screen. "Edited by" is intentionally the largest element; the robot and
# product name are supporting attribution, and the URL is the quiet final read.
HEADLINE_SIZE = 138
ROBOT_H = 148
NAME_SIZE = 82
URL_SIZE = 36
GAP_HEADLINE = 36
GAP_LOCKUP = 30
LOCKUP_GAP = 28
AI_GAP = 16

_WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRAND = os.path.join(_WORKER, "brand")
_FONTS = os.path.join(_WORKER, "fonts")
ROBOT_PNG = os.path.join(_BRAND, "robot.png")
INTER_BLACK = os.path.join(_FONTS, "InterDisplay-Black.ttf")
INTER_BOLD = os.path.join(_FONTS, "InterDisplay-Bold.ttf")
JAKARTA_XB = os.path.join(_FONTS, "PlusJakartaSans-ExtraBold.ttf")


def _ink(img):
    """Crop an RGBA layer to its visible ink."""
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def _white_stalk(img):
    """Lift only the dark antenna stalk so the red ball does not float."""
    px = img.load()
    head_top = 0
    for y in range(img.height):
        visible = sum(1 for x in range(img.width) if px[x, y][3] > 8)
        if visible > img.width * 0.20:
            head_top = y
            break
    touched = 0
    for y in range(head_top):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a < 8:
                continue
            if max(r, g, b) < 90 and max(r, g, b) - min(r, g, b) < 28:
                px[x, y] = (255, 255, 255, a)
                touched += 1
    if touched == 0:
        raise RuntimeError(
            "no antenna stalk found above y=%d in %s — the artwork changed"
            % (head_top, ROBOT_PNG))
    return img


def _robot(height_px):
    src = _white_stalk(Image.open(ROBOT_PNG).convert("RGBA"))
    width = max(1, round(src.width * height_px / src.height))
    return _ink(src.resize((width, height_px), Image.Resampling.LANCZOS))


def _text(text, font, fill, tracking=0.0):
    """Render tight-cropped text with explicit letter spacing."""
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    widths = [probe.textlength(char, font=font) for char in text]
    total = int(sum(widths) + tracking * max(0, len(text) - 1)) + 48
    asc, desc = font.getmetrics()
    layer = Image.new("RGBA", (total, asc + desc + 48), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x = 24.0
    for char, width in zip(text, widths):
        draw.text((x, 24), char, font=font, fill=fill)
        x += width + tracking
    return _ink(layer)


def _lockup(robot, name):
    """Build the small horizontal robot + Valmera attribution row."""
    height = max(robot.height, name.height)
    width = robot.width + LOCKUP_GAP + name.width
    row = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    row.alpha_composite(robot, (0, (height - robot.height) // 2))
    row.alpha_composite(name, (robot.width + LOCKUP_GAP,
                               (height - name.height) // 2))
    return row


def _brand_name(name, ai):
    """Set the AI qualifier beside Valmera without making it a second wordmark."""
    height = max(name.height, ai.height)
    label = Image.new("RGBA", (name.width + AI_GAP + ai.width, height),
                      (0, 0, 0, 0))
    label.alpha_composite(name, (0, height - name.height))
    label.alpha_composite(ai, (name.width + AI_GAP, height - ai.height))
    return label


def _elements():
    headline_font = ImageFont.truetype(JAKARTA_XB, HEADLINE_SIZE)
    name_font = ImageFont.truetype(INTER_BLACK, NAME_SIZE)
    url_font = ImageFont.truetype(INTER_BOLD, URL_SIZE)

    headline = _text("Edited by", headline_font, WHITE, -3.0)
    name = _brand_name(_text("Valmera", name_font, WHITE, -2.0),
                       _text("AI", name_font, WHITE, -2.0))
    url_line = _text("www.valmera.io", url_font, SOFT_WHITE, 0.0)
    lockup = _lockup(_robot(ROBOT_H), name)

    block_h = (headline.height + GAP_HEADLINE + lockup.height
               + GAP_LOCKUP + url_line.height)
    top = (CARD_H - block_h) // 2
    return (
        (headline, top),
        (lockup, top + headline.height + GAP_HEADLINE),
        (url_line, top + headline.height + GAP_HEADLINE + lockup.height
         + GAP_LOCKUP),
    )


def _smoothstep(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _progress(t, start, end):
    return _smoothstep((t - start) / max(0.001, end - start))


def _with_opacity(layer, opacity):
    if opacity >= 0.999:
        return layer
    faded = layer.copy()
    alpha = faded.getchannel("A").point(
        lambda value: round(value * max(0.0, min(1.0, opacity))))
    faded.putalpha(alpha)
    return faded


def _place(frame, layer, center_y, progress, travel=24, scale_from=1.0,
           global_opacity=1.0):
    """Ease one element upward and in, retaining a fixed visual centre."""
    scale = scale_from + (1.0 - scale_from) * progress
    if abs(scale - 1.0) > 0.001:
        size = (max(1, round(layer.width * scale)),
                max(1, round(layer.height * scale)))
        layer = layer.resize(size, Image.Resampling.LANCZOS)
    layer = _with_opacity(layer, progress * global_opacity)
    x = (CARD_W - layer.width) // 2
    y = round(center_y - layer.height / 2 + travel * (1.0 - progress))
    frame.alpha_composite(layer, (x, y))


def frame_at(t, elements=None):
    """Return the designed animation frame at time ``t`` seconds."""
    elements = elements or _elements()
    headline, lockup, url_line = elements

    # The final 0.36s resolves to black. This makes the MP4 itself complete;
    # the render pipeline also fades the segment as a codec-safe guard.
    global_opacity = 1.0 - _progress(t, 4.64, 5.0)
    frame = Image.new("RGBA", (CARD_W, CARD_H), (*BLACK_RGB, 255))

    _place(frame, headline[0], headline[1] + headline[0].height / 2,
           _progress(t, 0.05, 0.38), travel=28,
           global_opacity=global_opacity)
    _place(frame, lockup[0], lockup[1] + lockup[0].height / 2,
           _progress(t, 0.90, 1.25), travel=20, scale_from=0.92,
           global_opacity=global_opacity)
    _place(frame, url_line[0], url_line[1] + url_line[0].height / 2,
           _progress(t, 1.05, 1.33), travel=14,
           global_opacity=global_opacity)
    return frame.convert("RGB")


def build(poster_path, video_path=None):
    """Build the fully revealed PNG and, when requested, the animated MP4."""
    elements = _elements()
    poster = frame_at(1.60, elements)
    poster.save(poster_path, optimize=True)

    if video_path:
        command = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{CARD_W}x{CARD_H}", "-r", str(FPS), "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", video_path,
        ]
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE)
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg is required to build endcard.mp4") from exc
        try:
            frame_count = int(math.ceil(DURATION_S * FPS))
            for index in range(frame_count):
                process.stdin.write(frame_at(index / FPS, elements).tobytes())
            process.stdin.close()
            return_code = process.wait()
        except Exception:
            process.kill()
            process.wait()
            raise
        if return_code:
            raise RuntimeError("ffmpeg failed while building endcard.mp4")

    print(
        f"end card {CARD_W}x{CARD_H}; signature "
        f"{elements[0][0].width}x"
        f"{elements[2][1] + elements[2][0].height - elements[0][1]}; "
        f"poster {os.path.getsize(poster_path) / 1024:.0f} KB"
        + (f"; animation {os.path.getsize(video_path) / 1024:.0f} KB"
           if video_path else ""))
    return poster


if __name__ == "__main__":
    os.makedirs(_BRAND, exist_ok=True)
    build(os.path.join(_BRAND, "endcard.png"),
          os.path.join(_BRAND, "endcard.mp4"))
