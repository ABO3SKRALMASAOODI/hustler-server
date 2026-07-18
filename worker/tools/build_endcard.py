"""Build the Valmera end card PNG that every export ends on.

The robot is REDRAWN as primitives, not upscaled: the only art in the repo is
a 180px favicon (public/icon-512.png), and 180 -> 2048 is an 11x blow-up that
looks like a blurry sticker next to crisp type. Every measurement below was
read off that favicon programmatically (row-profile fit, see analyze_logo.py),
so this is the same robot, at vector quality.

Coordinates are in the favicon's 180x180 space and scaled at draw time.
"""
import os
from PIL import Image, ImageDraw, ImageFont

RED    = (235, 50, 35, 255)     # #EB3223  head visor, antenna ball
RED_ST = (249, 53, 37, 255)     # #F93525  antenna stalk (very slightly hotter)
PINK   = (239, 135, 131, 255)   # #EF8783  stalk droplet highlights
WHITE  = (255, 255, 255, 255)
INK    = (0, 0, 0, 255)

SS = 8                          # supersample factor; downsampled with LANCZOS
SRC_W, SRC_H = 180.0, 182.0     # favicon space, extended 2px: the real head
                                # bottom (y=181) is CROPPED by the icon frame.


def _superellipse(cx, cy, a, b, n, steps=720):
    """The head. Fitted n=2.2 — it is measurably neither an ellipse (n=2,
    too fat at the cheeks) nor a rounded rect (too square at the crown)."""
    pts = []
    for i in range(steps):
        t = 2.0 * 3.141592653589793 * i / steps
        import math
        ct, st = math.cos(t), math.sin(t)
        x = cx + a * (abs(ct) ** (2.0 / n)) * (1 if ct >= 0 else -1)
        y = cy + b * (abs(st) ** (2.0 / n)) * (1 if st >= 0 else -1)
        pts.append((x, y))
    return pts


def _arc_band(d, cx, cy, r, stroke, x_from, x_to, colour, s):
    """Stroked circular arc clipped to an x range, drawn as a polygon band so
    the caps stay round at any scale (PIL's arc() width is pixel-quantised)."""
    import math
    a0 = math.acos(max(-1.0, min(1.0, (x_from - cx) / r)))
    a1 = math.acos(max(-1.0, min(1.0, (x_to - cx) / r)))
    lo, hi = min(a0, a1), max(a0, a1)
    outer, inner = [], []
    steps = 240
    for i in range(steps + 1):
        t = lo + (hi - lo) * i / steps
        ct, st = math.cos(t), math.sin(t)
        outer.append(((cx + (r + stroke / 2) * ct) * s,
                      (cy + (r + stroke / 2) * st) * s))
        inner.append(((cx + (r - stroke / 2) * ct) * s,
                      (cy + (r - stroke / 2) * st) * s))
    d.polygon(outer + inner[::-1], fill=colour)
    # round caps
    for t in (lo, hi):
        px, py = cx + r * math.cos(t), cy + r * math.sin(t)
        rr = stroke / 2
        d.ellipse([(px - rr) * s, (py - rr) * s, (px + rr) * s, (py + rr) * s],
                  fill=colour)


def draw_robot(height_px):
    """The Valmera robot, RGBA, transparent background."""
    s = (height_px * SS) / SRC_H
    W, H = int(SRC_W * s), int(SRC_H * s)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def rrect(x0, y0, x1, y1, r, fill):
        d.rounded_rectangle([x0 * s, y0 * s, x1 * s, y1 * s],
                            radius=r * s, fill=fill)

    def circle(cx, cy, r, fill):
        d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s],
                  fill=fill)

    # --- behind the head: ears, then the antenna, so both tuck under it -----
    rrect(1, 110, 10, 148, 4, RED)          # left ear
    rrect(169, 110, 178, 148, 4, RED)       # right ear
    rrect(87, 20, 94, 74, 3.4, RED_ST)      # antenna stalk
    for cy in (32.0, 46.0, 61.0):           # the three droplet highlights
        d.polygon([(90.5 * s, (cy - 5.2) * s), (93.0 * s, cy * s),
                   (90.5 * s, (cy + 5.2) * s), (88.0 * s, cy * s)], fill=PINK)
    circle(90.5, 14.5, 14, RED)             # antenna ball

    # --- head ---------------------------------------------------------------
    d.polygon([(x * s, y * s) for x, y in
               _superellipse(89.5, 125, 79, 56, 2.2)], fill=WHITE)

    # --- face ---------------------------------------------------------------
    _arc_band(d, 90.5, -12.0, 103.0, 3.2, 42, 139, INK, s)   # brow
    rrect(39, 100, 142, 148, 20, RED)                        # visor
    circle(59.5, 123.5, 10.5, INK)                           # left eye
    circle(121.5, 123.5, 10.5, INK)                          # right eye
    _arc_band(d, 90.5, 130.0, 12.0, 3.4, 78.5, 102.5, INK, s)  # smile

    return img.resize((int(SRC_W * height_px / SRC_H), height_px),
                      Image.LANCZOS)


def _ink(img):
    b = img.getbbox()
    return img.crop(b) if b else img


def _render_text(text, font, fill, tracking):
    """Text as a tight-cropped RGBA layer. Composing from INK boxes (not PIL
    text boxes) is what makes the vertical rhythm real: a text box carries the
    font's full ascent/descent, so a nominal 86px gap under the robot measured
    174px of actual whitespace."""
    probe = Image.new("RGBA", (10, 10))
    pd = ImageDraw.Draw(probe)
    widths = [pd.textlength(c, font=font) for c in text]
    total = int(sum(widths) + tracking * (len(text) - 1)) + 40
    a, dsc = font.getmetrics()
    layer = Image.new("RGBA", (total, a + dsc + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x = 20.0
    for c, w in zip(text, widths):
        d.text((x, 20), c, font=font, fill=fill)
        x += w + tracking
    return _ink(layer)


def build(out_path, pjs_path):
    ROBOT_H = 720
    WORD_SIZE, WORD_TRACK = 300, -0.030     # -0.03em, as the site sets it
    LINE_SIZE, LINE_TRACK = 74, 0.055       # opened up: small grey text on
                                            # black needs air to stay legible
    GAP_ROBOT, GAP_WORD = 84, 78            # INK gaps, not text-box gaps

    word_f = ImageFont.truetype(pjs_path, WORD_SIZE)
    word_f.set_variation_by_axes([800])      # the site's wordmark weight
    line_f = ImageFont.truetype(pjs_path, LINE_SIZE)
    line_f.set_variation_by_axes([500])

    robot = draw_robot(ROBOT_H)
    word = _render_text("Valmera", word_f, WHITE, WORD_TRACK * WORD_SIZE)
    line = _render_text("Edited by Valmera agent", line_f,
                        (158, 158, 158, 255), LINE_TRACK * LINE_SIZE)

    W = max(robot.width, word.width, line.width)
    H = robot.height + GAP_ROBOT + word.height + GAP_WORD + line.height
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = 0
    for part in (robot, word, line):
        card.paste(part, ((W - part.width) // 2, y), part)
        y += part.height + (GAP_ROBOT if part is robot else GAP_WORD)

    card.save(out_path)
    print(f"card {card.size[0]}x{card.size[1]}  aspect "
          f"{card.size[0] / card.size[1]:.3f}  robot {robot.size}  "
          f"word {word.size}  line {line.size}  "
          f"({os.path.getsize(out_path) / 1024:.0f} KB)")
    return card
PJS_URL = ("https://raw.githubusercontent.com/google/fonts/main/ofl/"
           "plusjakartasans/PlusJakartaSans%5Bwght%5D.ttf")


def _font(cache):
    """Plus Jakarta Sans (SIL OFL 1.1), the site's wordmark face.

    Fetched rather than vendored: only the RENDERED PIXELS ship in the image,
    and the OFL restricts distributing font software, not images made with it.
    """
    if not os.path.exists(cache):
        import urllib.request
        print(f"fetching Plus Jakarta Sans -> {cache}")
        urllib.request.urlretrieve(PJS_URL, cache)
    return cache


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    brand = os.path.join(os.path.dirname(here), "brand")
    os.makedirs(brand, exist_ok=True)
    build(os.path.join(brand, "endcard.png"),
          _font(os.path.join(here, "PJS.ttf")))
