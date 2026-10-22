"""The free-tier corner mark (v6, Oct 2026): a sponsor-style signature at
the TOP-RIGHT of every free-tier final.

The owner's reference is a YouTube Short's sponsor mark ("PRESENTED BY"
in small tracked caps over a bold "ramp" wordmark with its logo, crisp
white, top-right). Valmera's version is three lines on ONE right edge:

              EDITED USING          small tracked caps (Inter Display Bold)
      [robot] Valmera AI            the brand wordmark (Plus Jakarta Sans
                                    ExtraBold, the landing page's face)
                valmera.io          the address, small

Every line ends on the same column: the right edge of its INK (not its
advance box), so the three right edges are one clean vertical. The robot
is the site's white robot (its dark antenna stalk lifted to white, as on
the end card, so the red ball never floats on a dark plate), centred on
the wordmark's caps.

It is drawn ONCE per output size as an RGBA image (type set with HarfBuzz
pair kerning on the bundled font files, so it reads exactly like the site's
wordmark) and laid over the programme by one ffmpeg overlay. The mark reads
on any footage through soft shades under the type and the robot (SHADES):
no box, no outline.

This module owns the mark's geometry. renderer.watermark_geometry,
keepout.watermark_zone and render_qc read it from here, so the overlay,
the reserved zone every layout keeps clear and the QC crop can never drift
apart.
"""

import functools
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
ROBOT_PNG = os.path.join(HERE, "brand", "robot.png")
NAME_FONT = os.path.join(FONTS, "PlusJakartaSans-ExtraBold.ttf")
SMALL_FONT = os.path.join(FONTS, "InterDisplay-Bold.ttf")

# Bump with config.WATERMARK_VERSION whenever the drawing changes (it keys
# the per-size image cache and is stamped into the file name).
DESIGN = 6

# ── the design, in px of a 1920-tall frame (scaled by H / 1920) ──────────
REF_H = 1920.0
KICK_SIZE = 24.0          # "EDITED USING"
KICK_TRACK = 0.22         # em, tracked caps
NAME_SIZE = 57.0          # "Valmera AI"
NAME_TRACK = -0.012       # em, the wordmark's tight set
URL_SIZE = 27.0           # "valmera.io"
URL_TRACK = 0.02
ROBOT_K = 2.05            # robot height / the wordmark's cap height
ROBOT_GAP = 0.27          # robot-to-wordmark gap, in wordmark em
KICK_GAP = 0.32           # kicker baseline -> wordmark cap top, x cap
URL_GAP = 0.34            # wordmark baseline -> address ascender, x cap
KICK_ALPHA = 0.94         # the two small lines sit a touch under the name
URL_ALPHA = 0.94

# Legibility on any footage, without a box or an outline (chosen on the
# comparison board, .valmera/engines/brand/board3, over a crisp shadow alone
# and a tight halo): three soft shades under the type and the robot, each
# (dx, dy, blur radius, opacity, spread) in px of a 1920 frame --
#   AMBIENT  a broad, faint dimming that follows the letterforms: invisible
#            on dark footage, it gives the small lines their contrast on a
#            white sky or a white wall (never a shape you can see);
#   WIDE     the soft drop that lifts the mark off busy footage;
#   TIGHT    a contact shade: every glyph keeps a crisp edge on white.
SHADOW_AMBIENT = (0.0, 2.0, 26.0, 0.34, 7.0)
SHADOW_WIDE = (0.0, 3.0, 11.0, 0.42, 0.0)
SHADOW_TIGHT = (0.0, 1.6, 2.2, 0.62, 0.0)
SHADES = (SHADOW_AMBIENT, SHADOW_WIDE, SHADOW_TIGHT)

try:
    import config as _config
    TEXT_KICK = _config.WATERMARK_KICKER_TEXT
    TEXT_NAME = _config.WATERMARK_NAME_TEXT
    TEXT_URL = _config.WATERMARK_URL_TEXT
except Exception:  # noqa: BLE001 — the design's own copy
    TEXT_KICK, TEXT_NAME, TEXT_URL = "EDITED USING", "Valmera AI", "valmera.io"

_LOCK = threading.Lock()


# ── type ────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=8)
def _hb_font(path):
    try:
        import uharfbuzz as hb
    except Exception:  # noqa: BLE001 — the Pillow fallback below
        return None
    face = hb.Face(hb.Blob.from_file_path(path))
    return hb.Font(face), face.upem


def _advances(text, path, size):
    """Per-character pen advances (px) with the font's pair kerning: HarfBuzz
    on the bundled file (ligatures off, so characters and glyphs are 1:1),
    else Pillow's own advances (the 'kern' table only)."""
    hbf = _hb_font(path)
    if hbf is not None:
        import uharfbuzz as hb
        font, upem = hbf
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(font, buf, {"kern": True, "liga": False, "clig": False})
        infos, pos = buf.glyph_infos, buf.glyph_positions
        if len(infos) == len(text) and \
                [i.cluster for i in infos] == list(range(len(text))):
            return [p.x_advance * size / float(upem) for p in pos]
    from PIL import ImageFont
    f = ImageFont.truetype(path, max(1, int(round(size))))
    out = []
    for i, ch in enumerate(text):
        if i + 1 < len(text):
            out.append(f.getlength(ch + text[i + 1]) - f.getlength(text[i + 1]))
        else:
            out.append(f.getlength(ch))
    return out


def _line(text, path, size, track_em, ss=4):
    """One line of white type as an 8-bit alpha mask cropped to its ink, and
    its baseline / cap metrics: (mask, baseline_y_in_mask, cap_px).

    Drawn at ``ss`` x and reduced with a box filter: Pillow's own glyph
    rasteriser hints small sizes onto the pixel grid, and per-character
    placement at fractional pen positions needs sub-pixel accuracy."""
    from PIL import Image, ImageDraw, ImageFont
    big = size * ss
    font = ImageFont.truetype(path, max(1, int(round(big))))
    adv = _advances(text, path, big)
    track = track_em * big
    asc, desc = font.getmetrics()
    pad = int(big * 0.5)
    width = int(sum(adv) + track * max(0, len(text) - 1)) + 2 * pad
    height = asc + desc + 2 * pad
    canvas = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    x = float(pad)
    base = pad + asc
    for ch, a in zip(text, adv):
        draw.text((x, base), ch, font=font, fill=255, anchor="ls")
        x += a + track
    bbox = canvas.getbbox()
    if not bbox:
        return Image.new("L", (1, 1), 0), 0.0, 0.0
    # crop to the ink on a grid of ``ss`` so the reduction keeps the
    # sub-pixel phase of the pen positions
    x0 = (bbox[0] // ss) * ss
    y0 = (bbox[1] // ss) * ss
    x1 = -(-bbox[2] // ss) * ss
    y1 = -(-bbox[3] // ss) * ss
    canvas = canvas.crop((x0, y0, x1, y1))
    small = canvas.resize((canvas.width // ss, canvas.height // ss),
                          Image.Resampling.BOX)
    cap_box = font.getbbox("H", anchor="ls")
    cap = -cap_box[1] / float(ss) if cap_box else size * 0.72
    return small, (base - y0) / float(ss), cap


def _ink_x1(mask):
    """The last column holding real ink (alpha >= 96): the edge the eye
    reads as where a line ends (anti-aliased fringe is not the edge)."""
    import numpy as np
    a = np.asarray(mask)
    cols = np.where((a >= 96).any(axis=0))[0]
    return int(cols[-1]) + 1 if len(cols) else mask.width


@functools.lru_cache(maxsize=1)
def _robot_master():
    """The site's white robot with its dark antenna stalk lifted to white
    (the end card's correction, tools/build_endcard._white_stalk): every
    other pixel is the brand asset."""
    import numpy as np
    from PIL import Image
    a = np.array(Image.open(ROBOT_PNG).convert("RGBA"))
    seen = (a[..., 3] > 8).sum(axis=1)
    rows = np.where(seen > a.shape[1] * 0.20)[0]
    head_top = int(rows[0]) if len(rows) else 0
    top = a[:head_top].astype(int)
    hi, lo = top[..., :3].max(axis=2), top[..., :3].min(axis=2)
    stalk = (top[..., 3] >= 8) & (hi < 90) & (hi - lo < 28)
    a[:head_top][stalk, :3] = 255
    return Image.fromarray(a, "RGBA")


def robot_rgba(w, h):
    """The robot exactly as the mark draws it, at w x h (RGBA)."""
    from PIL import Image
    return _robot_master().resize((int(w), int(h)), Image.Resampling.LANCZOS)


# ── layout ──────────────────────────────────────────────────────────────

def _even(v):
    return max(2, int(round(v / 2.0)) * 2)


@functools.lru_cache(maxsize=32)
def _parts(H):
    """The three lines and the robot at an output height: masks, metrics and
    their positions inside the mark's own ink box (x from its LEFT edge,
    y from its TOP), plus the box size. Cached per height."""
    k = float(H) / REF_H
    kick, kick_base, kick_cap = _line(TEXT_KICK, SMALL_FONT, KICK_SIZE * k, KICK_TRACK)
    name, name_base, name_cap = _line(TEXT_NAME, NAME_FONT, NAME_SIZE * k, NAME_TRACK)
    url, url_base, url_cap = _line(TEXT_URL, NAME_FONT, URL_SIZE * k, URL_TRACK)
    rh = _even(ROBOT_K * name_cap)
    rw = _even(rh * _robot_master().width / float(_robot_master().height))
    gap = int(round(ROBOT_GAP * NAME_SIZE * k))
    # widths measured to the visible ink edge, so the right edges align
    kick_w, name_w, url_w = _ink_x1(kick), _ink_x1(name), _ink_x1(url)
    row_w = rw + gap + name_w
    width = max(kick_w, row_w, url_w)
    # vertical rhythm, top down, in the wordmark's cap height
    kick_top = 0.0                                   # kicker ink top
    kick_baseline = kick_top + kick_base
    name_cap_top = kick_baseline + KICK_GAP * name_cap
    name_top = name_cap_top - (name_base - name_cap)  # the mask's own top
    name_baseline = name_cap_top + name_cap
    url_top = name_baseline + URL_GAP * name_cap
    robot_top = name_cap_top + name_cap / 2.0 - rh / 2.0
    top = min(kick_top, robot_top, name_top)
    off = -top
    rows = {
        "kick": (kick, width - kick_w, kick_top + off),
        "name": (name, width - name_w, name_top + off),
        "url": (url, width - url_w, url_top + off),
    }
    robot = (width - row_w, robot_top + off, rw, rh)
    height = max(url_top + off + url.height, robot_top + off + rh,
                 name_top + off + name.height)
    return {"rows": rows, "robot": robot, "w": width, "h": height,
            "k": k, "name_cap": name_cap}


def shadow_pad(H):
    """How far the soft shades reach past the ink (px), on every side."""
    k = float(H) / REF_H
    return int(round(max(blur * 2.2 + spread + max(abs(dx), abs(dy))
                         for dx, dy, blur, _a, spread in SHADES) * k)) + 2


def layout(W, H, right=None, top=None):
    """The mark's geometry on a W x H output with its INK's right edge at
    ``right`` and its ink top at ``top`` (px): {
      "ink": [x0, y0, x1, y1]   the type and robot (no shadow),
      "image": [x, y, w, h]     where the overlay image goes (shadow incl.),
      "robot": [x, y, w, h]     the robot as drawn,
      "rows": {name: [x0, y0, x1, y1]} each line's ink}."""
    import math
    p = _parts(int(H))
    x0 = int(round(right - p["w"]))
    y0 = int(round(top))
    pad = shadow_pad(H)
    rx, ry, rw, rh = p["robot"]
    rows = {}
    for key, (mask, ox, oy) in p["rows"].items():
        rows[key] = [x0 + int(round(ox)), y0 + int(round(oy)),
                     x0 + int(round(ox)) + mask.width,
                     y0 + int(round(oy)) + mask.height]
    return {"ink": [x0, y0, x0 + int(math.ceil(p["w"])), y0 + int(math.ceil(p["h"]))],
            "image": [x0 - pad, y0 - pad, int(math.ceil(p["w"])) + 2 * pad,
                      int(math.ceil(p["h"])) + 2 * pad],
            "robot": [x0 + int(round(rx)), y0 + int(round(ry)), int(rw), int(rh)],
            "rows": rows}


# ── drawing ─────────────────────────────────────────────────────────────

def _shaded(type_alpha, robot_layer, size, k):
    """The final RGBA: the soft shades of everything (SHADES), then the
    white type and the robot over them."""
    from PIL import Image, ImageChops, ImageFilter
    w, h = size
    ink = ImageChops.lighter(type_alpha, robot_layer.getchannel("A"))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for dx, dy, blur, alpha, spread in SHADES:
        m = ink
        if spread > 0:
            m = m.filter(ImageFilter.MaxFilter(2 * max(1, int(round(spread * k))) + 1))
        m = m.filter(ImageFilter.GaussianBlur(max(0.1, blur * k)))
        m = m.point(lambda v, a=alpha: int(round(v * a)))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.putalpha(ImageChops.offset(m, int(round(dx * k)), int(round(dy * k))))
        out = Image.alpha_composite(out, layer)
    white = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    white.putalpha(type_alpha)
    out = Image.alpha_composite(out, white)
    return Image.alpha_composite(out, robot_layer)


def render(W, H):
    """The mark as an RGBA image the size of layout()['image'] (shadow pad
    included), for an output of height H."""
    from PIL import Image
    p = _parts(int(H))
    pad = shadow_pad(H)
    import math
    w = int(math.ceil(p["w"])) + 2 * pad
    h = int(math.ceil(p["h"])) + 2 * pad
    from PIL import ImageChops
    type_alpha = Image.new("L", (w, h), 0)
    for key, (mask, ox, oy) in p["rows"].items():
        a = KICK_ALPHA if key == "kick" else URL_ALPHA if key == "url" else 1.0
        m = mask if a >= 0.999 else mask.point(lambda v, a=a: int(round(v * a)))
        layer = Image.new("L", (w, h), 0)
        layer.paste(m, (pad + int(round(ox)), pad + int(round(oy))))
        type_alpha = ImageChops.lighter(type_alpha, layer)
    rx, ry, rw, rh = p["robot"]
    robot_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    robot_layer.alpha_composite(robot_rgba(rw, rh),
                                (pad + int(round(rx)), pad + int(round(ry))))
    return _shaded(type_alpha, robot_layer, (w, h), p["k"])


def image_path(H, workdir):
    """The mark image for output height H, written once into ``workdir``."""
    path = os.path.join(workdir, f"valmera_mark_v{DESIGN}_{int(H)}.png")
    with _LOCK:
        if not os.path.exists(path):
            tmp = path + ".tmp.png"
            render(0, H).save(tmp, optimize=False)
            os.replace(tmp, path)
    return path
