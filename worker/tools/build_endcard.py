"""Build the Valmera end card PNG that every export ends on.

The mark is the SITE's robot — the full-body white robot in the landing-page
navbar (`frontend-next/public/hustler-robot95.riv`). It is not redrawn here:
`worker/brand/robot.png` is that Rive artboard rendered to a transparent PNG at
2400px, so the end card and the site show the same character, pixel for pixel.
See brand/README.md for the one-line Chrome command that regenerates it.

Only one correction is applied, and it is deliberate: the artwork is drawn for a
LIGHT background, so its black parts (visor, arm and leg detail, the antenna
stalk) are meant to read as ink. On the black end card every one of them still
reads — they sit inside white shapes and become negative space — EXCEPT the
antenna stalk, which has white on neither side and simply vanishes, leaving the
red ball floating unattached. `_fix_antenna` repaints exactly those rows white.

Layout is set in INK boxes, never text boxes: a text box carries the font's full
ascent/descent, so a nominal gap measures nearly double in real whitespace.
"""
import os
from PIL import Image, ImageDraw, ImageFont

WHITE = (255, 255, 255, 255)

# Type colours. The tagline is deliberately NOT mid-grey — a solid #9E9E9E line
# under a pure-white wordmark is the exact "cheap web ad" tell. Muted white sits
# back into the black without reading as a second, greyer brand. 59% is as low
# as it goes and still survives the render chain: the card is scaled to ~0.6x
# frame width and re-encoded at CRF 20+, and anything under ~50% at this size
# came back from ffmpeg as a grey smudge.
TAGLINE = (255, 255, 255, 150)
RULE = (255, 255, 255, 80)

ROBOT_PNG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "brand", "robot.png")


def _ink(img):
    b = img.getbbox()
    return img.crop(b) if b else img


def _fix_antenna(img):
    """Repaint the antenna stalk white so it survives on black.

    The stalk is found structurally, not by hard-coded coordinates: scan down
    from the top and take the rows whose only opaque pixels are near-black AND
    span less than 12% of the width — that is the thin bar between the red ball
    and the head, and nothing else in the artboard is both that narrow and that
    dark. Stop at the first row that fails (the head's crown), so no black
    inside the head or body is ever touched.
    """
    img = img.convert("RGBA")
    px = img.load()
    W, H = img.size
    hit = 0
    for y in range(H):
        xs = [x for x in range(W) if px[x, y][3] > 128]
        if not xs:
            continue
        dark = [x for x in xs if max(px[x, y][:3]) < 60]
        if not dark:
            continue                      # ball rows / clean rows: keep going
        if len(xs) > 0.12 * W:
            break                         # reached the head — stop, keep ink
        for x in dark:
            px[x, y] = WHITE
        hit += len(dark)
    return img, hit


def robot(height_px):
    """The site's robot, RGBA, transparent, ready for a black card."""
    src = Image.open(ROBOT_PNG).convert("RGBA")
    src, n = _fix_antenna(src)
    if not n:
        raise SystemExit("antenna stalk not found — did brand/robot.png "
                         "change? Re-check _fix_antenna before shipping a "
                         "card with a floating antenna ball.")
    w = max(1, round(src.width * height_px / src.height))
    return _ink(src.resize((w, height_px), Image.LANCZOS))


def _text(text, font, fill, tracking):
    """Text as a tight-cropped RGBA layer, letter-spaced by hand (PIL has no
    tracking). Cropping to ink is what makes the vertical rhythm below real."""
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    widths = [probe.textlength(c, font=font) for c in text]
    total = int(sum(widths) + tracking * max(0, len(text) - 1)) + 80
    asc, dsc = font.getmetrics()
    layer = Image.new("RGBA", (total, asc + dsc + 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x = 40.0
    for c, w in zip(text, widths):
        d.text((x, 40), c, font=font, fill=fill)
        x += w + tracking
    return _ink(layer)


def build(out_path, pjs_path):
    """Compose the card.

    Proportions are the point. The old card set a 300px wordmark under a 720px
    robot with a 74px grey line — three competing weights, all near the same
    size, which is what read as an ad. Here one element leads (the mark), the
    wordmark is secondary, and the attribution is a hairline-quiet tracked
    caption. Nothing repeats: the wordmark says the name once, the caption says
    what happened, so "Valmera" is not printed twice on the same card.
    """
    ROBOT_H = 660
    WORD_SIZE, WORD_TRACK = 232, -0.030    # -0.03em — the site's wordmark
    # Caption width is what sizes the whole card: the renderer fits the card
    # inside a box, so the WIDEST element decides how big everything else
    # lands. A long, heavily-tracked caption silently shrank the mark to 13%
    # of frame height. Short line, moderate tracking, and the robot leads.
    CAP_SIZE, CAP_TRACK = 52, 0.30
    GAP_MARK = 104                         # robot -> wordmark (ink gap)
    GAP_RULE = 52                          # wordmark -> hairline
    GAP_CAP = 46                           # hairline -> caption
    RULE_H = 3

    word_f = ImageFont.truetype(pjs_path, WORD_SIZE)
    word_f.set_variation_by_axes([800])
    cap_f = ImageFont.truetype(pjs_path, CAP_SIZE)
    cap_f.set_variation_by_axes([600])

    mark = robot(ROBOT_H)
    word = _text("Valmera", word_f, WHITE, WORD_TRACK * WORD_SIZE)
    cap = _text("EDITED WITH VALMERA", cap_f, TAGLINE, CAP_TRACK * CAP_SIZE)

    rule_w = int(cap.width * 0.30)
    rule = Image.new("RGBA", (rule_w, RULE_H), RULE)

    W = max(mark.width, word.width, cap.width)
    H = (mark.height + GAP_MARK + word.height + GAP_RULE + RULE_H
         + GAP_CAP + cap.height)
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = 0
    for part, gap in ((mark, GAP_MARK), (word, GAP_RULE), (rule, GAP_CAP),
                      (cap, 0)):
        card.paste(part, ((W - part.width) // 2, y), part)
        y += part.height + gap

    card.save(out_path)
    print(f"card {card.size[0]}x{card.size[1]}  aspect "
          f"{card.size[0] / card.size[1]:.3f}  robot {mark.size}  "
          f"word {word.size}  caption {cap.size}  "
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
