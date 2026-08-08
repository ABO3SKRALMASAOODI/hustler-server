"""Build the Valmera end card PNG that every export ends on.

Round 99b: the card the owner spec'd, top to bottom —

    THIS VIDEO WAS EDITED
        BY AN AI AGENT
         [the robot]
         Valmera.io

The mark is the GRAY-RED robot — the Pro plan's card robot on the billing
page (`frontend-next/public/hustler-robot112.riv`), rendered to
`worker/brand/robot112.png` at 2400px via the headless-Chrome recipe in
brand/README.md, so the card and the site show the same character.

Two corrections, both deliberate:

* BLACK-FLOOR LIFT. The artwork is drawn for a light card, so its pure-black
  parts (antenna stalk, neck, visor plate, chest port, upper legs) are meant
  to read as ink. On the black end card every one of them would dissolve into
  the background — a floating red antenna ball, a head hovering off its body.
  `_lift_blacks` raises just those near-black pixels to a dark charcoal that
  still reads darker than the body grays but no longer vanishes.

* SILHOUETTE GLOW. A dark robot on a black card needs an edge: a soft
  radial wash, barely above black and slightly red, sits behind the mark so
  the silhouette separates without reading as a "spotlight effect".

Layout is set in INK boxes, never text boxes: a text box carries the font's
full ascent/descent, so a nominal gap measures nearly double in whitespace.
"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

WHITE = (255, 255, 255, 255)
# The robot's own red (sampled from the antenna ball) — the ".io" and nothing
# else. One accent, used once, is what keeps the card premium instead of ad.
RED = (250, 5, 5, 255)

ROBOT_PNG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "brand", "robot112.png")

# Near-black pixels lift to this — visibly darker than the body's ~#2E3237
# grays, visibly lighter than the void.
FLOOR = (36, 38, 43)
FLOOR_MAX = 24          # max(r,g,b) at or under this counts as "vanishes"


def _ink(img):
    b = img.getbbox()
    return img.crop(b) if b else img


def _lift_blacks(img):
    """Raise pure-black ink to FLOOR so it survives on a black card.
    Returns (img, n_lifted); a zero count means the asset changed and the
    lift no longer matches it — refuse to ship a dissolving robot."""
    img = img.convert("RGBA")
    px = img.load()
    W, H = img.size
    n = 0
    for y in range(H):
        for x in range(W):
            r, g, b, a = px[x, y]
            if a > 40 and max(r, g, b) <= FLOOR_MAX:
                px[x, y] = (FLOOR[0], FLOOR[1], FLOOR[2], a)
                n += 1
    return img, n


def robot(height_px):
    """The billing page's gray-red robot, lifted for black, glow attached."""
    src = Image.open(ROBOT_PNG).convert("RGBA")
    src, n = _lift_blacks(src)
    if not n:
        raise SystemExit("no near-black ink found — did brand/robot112.png "
                         "change? Re-check _lift_blacks before shipping a "
                         "card whose robot dissolves into the background.")
    # Lift the body grays a touch: at reel scale, ~#2E3237 on black reads
    # as mud. 1.22x keeps the charcoal identity while the silhouette reads.
    from PIL import ImageEnhance
    src = ImageEnhance.Brightness(src).enhance(1.22)
    w = max(1, round(src.width * height_px / src.height))
    mark = _ink(src.resize((w, height_px), Image.LANCZOS))

    # The glow: a blurred ellipse, barely above black with a red lean, on a
    # canvas padded enough that the blur never clips to a visible square.
    pad = int(height_px * 0.22)
    gw, gh = mark.width + 2 * pad, mark.height + 2 * pad
    glow = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.ellipse([pad * 0.4, pad * 0.4, gw - pad * 0.4, gh - pad * 0.4],
              fill=(74, 30, 30, 118))
    glow = glow.filter(ImageFilter.GaussianBlur(pad * 0.62))
    glow.paste(mark, (pad, pad), mark)
    return glow


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


def _wordmark(word_f, io_f):
    """Valmera.io as one ink layer — the site's wordmark plus its domain,
    baseline-aligned, the ".io" in the robot's red."""
    name = _text("Valmera", word_f, WHITE, -0.030 * word_f.size)
    io = _text(".io", io_f, RED, -0.020 * io_f.size)
    gap = int(word_f.size * 0.045)
    W = name.width + gap + io.width
    H = max(name.height, io.height)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # Baseline alignment by bottom edge: both crops end at their baseline-ish
    # ink bottom ("Valmera" has no descenders; neither does ".io" beyond the
    # dot sitting ON the baseline), so bottom-aligning IS baseline-aligning.
    layer.paste(name, (0, H - name.height), name)
    layer.paste(io, (name.width + gap, H - io.height), io)
    return layer


def build(out_path, pjs_path):
    """Compose the card.

    One reading order, three registers: the STATEMENT (two big tracked lines
    — what happened), the CHARACTER (the robot, largest element — who did
    it), the NAME (Valmera.io — where). The robot leads the hierarchy per
    the owner's spec ("the agent is big"); the headline is the widest
    element and therefore sizes the card; the wordmark sits just under the
    robot so character and name read as one unit.
    """
    HEAD_SIZE = 148
    HEAD_TRACK = 0.045                     # airy, deliberate, not shouty
    HEAD_LEAD = 58                         # between the two headline lines
    ROBOT_H = 1620                         # "the agent is big" — it LEADS
    WORD_SIZE = 252
    IO_SIZE = 178
    GAP_HEAD = 40                          # headline -> robot (glow pads add air)
    GAP_WORD = 10                          # robot -> wordmark (ditto)

    head_f = ImageFont.truetype(pjs_path, HEAD_SIZE)
    head_f.set_variation_by_axes([800])
    word_f = ImageFont.truetype(pjs_path, WORD_SIZE)
    word_f.set_variation_by_axes([800])
    io_f = ImageFont.truetype(pjs_path, IO_SIZE)
    io_f.set_variation_by_axes([800])

    line1 = _text("THIS VIDEO WAS EDITED", head_f, WHITE,
                  HEAD_TRACK * HEAD_SIZE)
    line2 = _text("BY AN AI AGENT", head_f, WHITE, HEAD_TRACK * HEAD_SIZE)
    mark = robot(ROBOT_H)
    word = _wordmark(word_f, io_f)

    W = max(line1.width, line2.width, mark.width, word.width)
    H = (line1.height + HEAD_LEAD + line2.height + GAP_HEAD
         + mark.height + GAP_WORD + word.height)
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = 0
    for part, gap in ((line1, HEAD_LEAD), (line2, GAP_HEAD),
                      (mark, GAP_WORD), (word, 0)):
        card.paste(part, ((W - part.width) // 2, y), part)
        y += part.height + gap

    card.save(out_path)
    print(f"card {card.size[0]}x{card.size[1]}  aspect "
          f"{card.size[0] / card.size[1]:.3f}  robot {mark.size}  "
          f"headline {line1.size}/{line2.size}  wordmark {word.size}  "
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
