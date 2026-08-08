"""Build the Valmera end card PNG that every export ends on.

Round 101 — v6. A 2160x3840 (9:16) RGBA card, the shape every reel is
already in:

    THIS VIDEO WAS EDITED BY
          [the robot]
          Valmera.io
    AI VIDEO EDITING AGENT
       [ TRY IT FREE ]

What changed from v5, and why:

  * ONE PIXEL EDIT, AND ONLY ONE. The antenna STALK is repainted white
    (see _white_stalk) so the red ball reads as attached to the head
    instead of hovering over it. Everything else about the mark is left
    exactly as drawn.

  * NOTHING SITS BEHIND THE ROBOT. v3 recolored the gray-red plan robot to
    survive bare black and it read as the wrong robot; v4 seated it on an
    elevated panel and the panel read as a background; v5 replaced the panel
    with two blurred radial washes and at reel scale those read as a grey
    smudge behind the character — still a background. v6 wears the WHITE
    navbar / free-plan mark, EXACTLY as drawn — no recolour, no lift, no
    glow — and the alpha channel is empty everywhere the artwork is not.

    The cost is known and accepted: the mark's remaining pure-black parts
    (neck, elbow joints, shins) are invisible against a black frame. That
    is a deliberate trade for true colour on true transparency — the only
    two ways out are recolouring the whole mark or putting light behind it,
    and both have shipped and both were rejected.

  * PURE 9:16, and the robot is a MARK, not a poster. It sits at ~34% of
    the frame height with the wordmark as the only large type.

  * READING ORDER. The sentence sets up the robot, the robot is the answer
    to it, and the destination follows — and the whole stack is CENTRED, so
    the setup line sits in the frame rather than pinned to the top edge.

  * ONE SENTENCE. v5 shouted three lines of display type at a viewer one
    thumb-flick from leaving, and none of them said what the product was.

Layout is set in INK boxes, never text boxes: a text box carries the font's
full ascent/descent, so a nominal gap measures nearly double in whitespace.
The finished stack is centred in the canvas.
"""
import os
from PIL import Image, ImageDraw, ImageFont

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
SOFT = (255, 255, 255, 148)      # the sentence under the robot
FAINT = (255, 255, 255, 117)     # the descriptor under the wordmark
# The robot's own red (sampled from the antenna ball). One red, one job: the
# ".io". A second red anywhere tips the card from premium into ad.
RED = (250, 5, 5, 255)

# The canvas IS the delivery shape.
CARD_W, CARD_H = 2160, 3840

# Type and geometry. Changing any of these is a card redesign — bump
# OUTRO_VERSION in worker/config.py AND backend/routes/video.py.
EYEBROW_SIZE = 92
ROBOT_H = 1290
WORD_SIZE = 300
IO_SIZE = 210
SUB_SIZE = 62
CTA_SIZE = 55
GAP_EYEBROW = 110
GAP_ROBOT = 150
GAP_WORD = 48        # the descriptor belongs TO the wordmark, so it sits
GAP_SUB = 150        # close under it and the pill sits well clear

_WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRAND = os.path.join(_WORKER, "brand")
_FONTS = os.path.join(_WORKER, "fonts")
ROBOT_PNG = os.path.join(_BRAND, "robot.png")            # the WHITE mark
INTER_BLACK = os.path.join(_FONTS, "InterDisplay-Black.ttf")
INTER_XB = os.path.join(_FONTS, "InterDisplay-ExtraBold.ttf")


def _ink(img):
    b = img.getbbox()
    return img.crop(b) if b else img


def _white_stalk(img):
    """Repaint the antenna STALK white. The single pixel edit on this card.

    The stalk is the neutral ink ABOVE the head dome, and the dome's top is
    found structurally — the first row where the artwork covers more than a
    fifth of its own width, which the ball (about a tenth) never does. So
    the ball, the visor and every dark part below the head are untouched no
    matter how the artboard is redrawn.

    Raises rather than ships silently if nothing is repainted: a black
    stalk on a black frame is a red ball hovering off a head, which is the
    exact defect this exists to prevent.
    """
    px = img.load()
    head_top = 0
    for y in range(img.height):
        n = sum(1 for x in range(img.width) if px[x, y][3] > 8)
        if n > img.width * 0.20:
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
            "no antenna stalk found above y=%d in %s — the artwork changed "
            "and the red ball would float off the head" % (head_top,
                                                           ROBOT_PNG))
    return img


def robot(height_px):
    """The white navbar robot at the card's size, stalk repainted white and
    nothing else done to it. No panel, no glow, no other recolour."""
    src = _white_stalk(Image.open(ROBOT_PNG).convert("RGBA"))
    w = max(1, round(src.width * height_px / src.height))
    return _ink(src.resize((w, height_px), Image.LANCZOS))


def _text(text, font, fill, tracking):
    """Text as a tight-cropped RGBA layer, letter-spaced by hand (PIL has no
    tracking). Cropping to ink is what makes the vertical rhythm real."""
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
    name = _text("Valmera", word_f, WHITE, -0.032 * word_f.size)
    io = _text(".io", io_f, RED, -0.020 * io_f.size)
    gap = int(word_f.size * 0.045)
    W = name.width + gap + io.width
    H = max(name.height, io.height)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # Bottom-aligning IS baseline-aligning here: "Valmera" has no descenders
    # and ".io" ends on the baseline too.
    layer.paste(name, (0, H - name.height), name)
    layer.paste(io, (name.width + gap, H - io.height), io)
    return layer


def _cta(label_f, text="TRY IT FREE"):
    """The white pill. On a black frame white is the brightest thing there
    is, which is why the red stays on the ".io" and never competes here.
    Matches the product's own CTA (PlanCTACard, /subscribe). Small on
    purpose: it is the last thing read, not the first thing seen."""
    label = _text(text, label_f, BLACK, 0.13 * label_f.size)
    pad_x = int(label_f.size * 1.20)
    pad_y = int(label_f.size * 0.64)
    W, H = label.width + 2 * pad_x, label.height + 2 * pad_y
    pill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(pill)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2, fill=WHITE)
    pill.paste(label, (pad_x, pad_y), label)
    return pill


def build(out_path):
    """Compose the page and centre it in the 9:16 canvas."""
    eyebrow_f = ImageFont.truetype(INTER_XB, EYEBROW_SIZE)
    word_f = ImageFont.truetype(INTER_BLACK, WORD_SIZE)
    io_f = ImageFont.truetype(INTER_BLACK, IO_SIZE)
    sub_f = ImageFont.truetype(INTER_XB, SUB_SIZE)
    cta_f = ImageFont.truetype(INTER_XB, CTA_SIZE)

    mark = robot(ROBOT_H)
    eyebrow = _text("THIS VIDEO WAS EDITED BY", eyebrow_f, SOFT,
                    0.26 * EYEBROW_SIZE)
    word = _wordmark(word_f, io_f)
    sub = _text("AI VIDEO EDITING AGENT", sub_f, FAINT, 0.30 * SUB_SIZE)
    cta = _cta(cta_f)

    parts = ((eyebrow, GAP_EYEBROW), (mark, GAP_ROBOT), (word, GAP_WORD),
             (sub, GAP_SUB), (cta, 0))
    block = (sum(p.height for p, _ in parts)
             + sum(g for _, g in parts[:-1]))
    if block > CARD_H:
        raise RuntimeError("card content (%dpx) taller than the %dpx canvas"
                           % (block, CARD_H))

    card = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    y = (CARD_H - block) // 2
    for part, gap in parts:
        card.paste(part, ((CARD_W - part.width) // 2, y), part)
        y += part.height + gap

    card.save(out_path)
    print(f"card {card.size[0]}x{card.size[1]}  aspect "
          f"{card.size[0] / card.size[1]:.3f}  block {block}px "
          f"({100.0 * block / CARD_H:.0f}%)  robot {mark.size}  "
          f"wordmark {word.size}  cta {cta.size}  "
          f"({os.path.getsize(out_path) / 1024:.0f} KB)")
    return card


if __name__ == "__main__":
    os.makedirs(_BRAND, exist_ok=True)
    build(os.path.join(_BRAND, "endcard.png"))
