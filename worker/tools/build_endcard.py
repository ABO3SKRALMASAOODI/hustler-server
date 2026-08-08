"""Build the Valmera end card PNG that every export ends on.

Round 100b — v5, the CONVERSION page. Three versions taught three lessons:
v3 recolored the robot to survive bare black and the owner read it as the
wrong robot; v4 kept the true colors by seating it on an elevated panel and
the owner read the panel as "a background behind it"; and Plus Jakarta's
tracked caps read as UI chrome, not a hook. So v5, top to bottom:

    THIS VIDEO WAS            (quiet tracked eyebrow)
    EDITED BY AN              (Anton — the shorts-native display face)
    AI AGENT                  (Anton, red — the line that stops the scroll)
        [the robot]           (TRUE plan-card colors, floating on an
                               ORGANIC red glow — light, not furniture:
                               no rectangle, no edge, nothing that reads
                               as a plate)
    Valmera.io                (the destination)
    [ TRY IT FREE ]           (a filled red pill — the card is a PAGE with
                               a call to action, not a watermark)

The glow is what lets the robot's pure-black ink (antenna stalk, visor,
joints) read against a black frame without repainting a single pixel: two
stacked radial washes, the inner one warm and bright enough to silhouette
the head and torso, both Gaussian-blurred until no boundary survives.

Layout is set in INK boxes, never text boxes: a text box carries the font's
full ascent/descent, so a nominal gap measures nearly double in whitespace.
"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

WHITE = (255, 255, 255, 255)
SOFT = (233, 234, 238, 255)      # the eyebrow — white would fight the hero
# The robot's own red (sampled from the antenna ball): the hero line, the
# ".io" and the CTA fill. One red, three jobs, zero other colors.
RED = (250, 5, 5, 255)

_BRAND = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "brand")
_FONTS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fonts")
ROBOT_PNG = os.path.join(_BRAND, "robot112.png")
ANTON = os.path.join(_FONTS, "Anton-Regular.ttf")
INTER_XB = os.path.join(_FONTS, "InterDisplay-ExtraBold.ttf")
INTER_BLACK = os.path.join(_FONTS, "InterDisplay-Black.ttf")


def _ink(img):
    b = img.getbbox()
    return img.crop(b) if b else img


def robot(height_px):
    """The billing page's gray-red robot — TRUE colors, floating on light.

    No panel, no plate, no recolor. The two radial washes behind it are the
    whole trick: the inner one is bright enough that the pure-black ink
    (antenna, visor, joints) reads as silhouette, and both are blurred past
    the point where any edge could read as a shape.
    """
    src = Image.open(ROBOT_PNG).convert("RGBA")
    w = max(1, round(src.width * height_px / src.height))
    mark = _ink(src.resize((w, height_px), Image.LANCZOS))

    pad = int(height_px * 0.30)
    gw, gh = mark.width + 2 * pad, mark.height + 2 * pad
    out = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))

    glow = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    # Outer wash: deep red, wide, barely-there at the rim.
    d.ellipse([pad * 0.15, pad * 0.15, gw - pad * 0.15, gh - pad * 0.15],
              fill=(122, 20, 20, 120))
    glow = glow.filter(ImageFilter.GaussianBlur(pad * 0.60))
    out.alpha_composite(glow)
    # Inner wash: warmer and brighter, sitting behind head + torso, so the
    # black ink there has something to be dark AGAINST.
    core = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
    d = ImageDraw.Draw(core)
    d.ellipse([gw * 0.17, gh * 0.12, gw * 0.83, gh * 0.74],
              fill=(208, 52, 44, 158))
    core = core.filter(ImageFilter.GaussianBlur(pad * 0.55))
    out.alpha_composite(core)

    out.paste(mark, (pad, pad), mark)
    return out


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


def _cta(label_f, text="TRY IT FREE"):
    """The filled red pill. A page that wants the viewer to GO somewhere
    carries a button, and a button is a shape — the one deliberate shape on
    the card, which is exactly why the glow behind the robot must not be one."""
    label = _text(text, label_f, WHITE, 0.14 * label_f.size)
    pad_x = int(label_f.size * 1.05)
    pad_y = int(label_f.size * 0.58)
    W, H = label.width + 2 * pad_x, label.height + 2 * pad_y
    pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(pill)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2, fill=RED)
    pill.paste(label, (pad_x, pad_y), label)
    # A soft red bloom under the pill so it sits IN the scene's light
    # rather than stickered onto it.
    bloomp = int(H * 0.55)
    out = Image.new("RGBA", (W + 2 * bloomp, H + 2 * bloomp), (0, 0, 0, 0))
    bloom = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(bloom)
    d.rounded_rectangle([bloomp * 0.6, bloomp * 0.6, out.width - bloomp * 0.6,
                         out.height - bloomp * 0.6],
                        radius=H, fill=(210, 30, 30, 110))
    bloom = bloom.filter(ImageFilter.GaussianBlur(bloomp * 0.55))
    out.alpha_composite(bloom)
    out.paste(pill, (bloomp, bloomp), pill)
    return out


def build(out_path):
    """Compose the page.

    One reading order, four registers: the QUIET SETUP ("this video was"),
    the HOOK (Anton, with AI AGENT as the red payoff line), the CHARACTER
    (the robot, largest element), and the DESTINATION (wordmark + CTA pill).
    Anton for the hook because this card plays at the end of vertical
    SHORTS — it is the native display voice of that format; Inter Display
    carries the small lines so the hook stays the only shout.
    """
    EYEBROW_SIZE = 92
    HERO_SIZE = 252
    HERO2_SIZE = 322
    ROBOT_H = 2050
    WORD_SIZE = 290
    IO_SIZE = 205
    CTA_SIZE = 96

    eyebrow_f = ImageFont.truetype(INTER_XB, EYEBROW_SIZE)
    hero_f = ImageFont.truetype(ANTON, HERO_SIZE)
    hero2_f = ImageFont.truetype(ANTON, HERO2_SIZE)
    word_f = ImageFont.truetype(INTER_BLACK, WORD_SIZE)
    io_f = ImageFont.truetype(INTER_BLACK, IO_SIZE)
    cta_f = ImageFont.truetype(INTER_XB, CTA_SIZE)

    eyebrow = _text("THIS VIDEO WAS", eyebrow_f, SOFT,
                    0.34 * EYEBROW_SIZE)
    hero1 = _text("EDITED BY AN", hero_f, WHITE, 0.012 * HERO_SIZE)
    hero2 = _text("AI AGENT", hero2_f, RED, 0.012 * HERO2_SIZE)
    mark = robot(ROBOT_H)
    word = _wordmark(word_f, io_f)
    cta = _cta(cta_f)

    GAP_EYEBROW = 74
    GAP_HERO = 40
    GAP_HERO2 = -110       # the glow's padding supplies the real air
    GAP_ROBOT = -150
    GAP_WORD = 46

    W = max(eyebrow.width, hero1.width, hero2.width, mark.width,
            word.width, cta.width)
    parts = ((eyebrow, GAP_EYEBROW), (hero1, GAP_HERO), (hero2, GAP_HERO2),
             (mark, GAP_ROBOT), (word, GAP_WORD), (cta, 0))
    H = sum(p.height for p, _ in parts) + sum(g for _, g in parts[:-1])
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = 0
    for part, gap in parts:
        card.paste(part, ((W - part.width) // 2, y), part)
        y += part.height + gap

    card.save(out_path)
    print(f"card {card.size[0]}x{card.size[1]}  aspect "
          f"{card.size[0] / card.size[1]:.3f}  robot {mark.size}  "
          f"hero {hero1.size}/{hero2.size}  wordmark {word.size}  "
          f"cta {cta.size}  ({os.path.getsize(out_path) / 1024:.0f} KB)")
    return card


if __name__ == "__main__":
    os.makedirs(_BRAND, exist_ok=True)
    build(os.path.join(_BRAND, "endcard.png"))
