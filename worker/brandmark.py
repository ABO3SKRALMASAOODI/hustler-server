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
# The dark twin (16 Nov 2026): the same drawing in a near-black ink over light
# shades, for a bright picture under the mark (ink_spans).
DARK_INK = (17, 17, 20)
DARK_SHADES = ((0.0, 2.0, 26.0, 0.30, 7.0), (0.0, 3.0, 11.0, 0.36, 0.0), (0.0, 1.6, 2.2, 0.55, 0.0))

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

def _shaded(type_alpha, robot_layer, size, k, dark=False):
    """The final RGBA: the soft shades of everything (SHADES), then the
    white type and the robot over them. ``dark``: the same drawing in DARK_INK
    over light shades (DARK_SHADES), for a bright picture under the mark."""
    from PIL import Image, ImageChops, ImageFilter
    w, h = size
    ink = ImageChops.lighter(type_alpha, robot_layer.getchannel("A"))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shade_rgb = (255, 255, 255) if dark else (0, 0, 0)
    for dx, dy, blur, alpha, spread in (DARK_SHADES if dark else SHADES):
        m = ink
        if spread > 0:
            m = m.filter(ImageFilter.MaxFilter(2 * max(1, int(round(spread * k))) + 1))
        m = m.filter(ImageFilter.GaussianBlur(max(0.1, blur * k)))
        m = m.point(lambda v, a=alpha: int(round(v * a)))
        layer = Image.new("RGBA", (w, h), shade_rgb + (0,))
        layer.putalpha(ImageChops.offset(m, int(round(dx * k)), int(round(dy * k))))
        out = Image.alpha_composite(out, layer)
    white = Image.new("RGBA", (w, h), (DARK_INK if dark else (255, 255, 255)) + (0,))
    white.putalpha(type_alpha)
    out = Image.alpha_composite(out, white)
    return Image.alpha_composite(out, _inked_robot(robot_layer) if dark else robot_layer)


def _inked_robot(layer):
    """The robot for the dark ink: its white body and stalk in DARK_INK, its
    coloured parts (the red ball) and its own dark details as drawn."""
    import numpy as np
    from PIL import Image
    a = np.asarray(layer).astype(np.float32)
    rgb = a[..., :3]
    lum = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    white = np.clip((lum - 150.0) / 80.0, 0, 1) * np.clip(1.0 - sat / 60.0, 0, 1)
    dark = np.asarray(DARK_INK, np.float32)
    detail = (lum < 110) & (sat < 60)              # the face's dark details turn light so it stays a face
    out = rgb * (1 - white[..., None]) + dark * white[..., None]
    out[detail] = 255.0 - rgb[detail]
    return Image.fromarray(np.dstack([out, a[..., 3]]).clip(0, 255).astype(np.uint8), "RGBA")


def render(W, H, dark=False):
    """The mark as an RGBA image the size of layout()['image'] (shadow pad
    included), for an output of height H; ``dark``: in the dark ink."""
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
    return _shaded(type_alpha, robot_layer, (w, h), p["k"], dark=dark)


def image_path(H, workdir, dark=False):
    """The mark image for output height H, written once into ``workdir``;
    ``dark``: the dark-ink twin (same drawing, same geometry)."""
    path = os.path.join(workdir, f"valmera_mark_v{DESIGN}{'_dark' if dark else ''}_{int(H)}.png")
    with _LOCK:
        if not os.path.exists(path):
            tmp = path + ".tmp.png"
            render(0, H, dark=dark).save(tmp, optimize=False)
            os.replace(tmp, path)
    return path


# ── the ink follows the picture (owner, 16 Nov 2026) ─────────────────────
# "When the video is white it appears in black." The mark keeps its place, its
# drawing and its end card; only its ink changes: white over most footage, the
# dark twin while the picture under the mark is bright. Never a flicker: the
# brightness under the ink is smoothed over INK_SMOOTH_S, the ink turns dark at
# INK_DARK_AT and back only under INK_LIGHT_AT (hysteresis), each ink holds at
# least INK_HOLD_S (a shorter bright or dark stretch keeps the ink it has), and a
# change is a INK_FADE_S cross-fade.
INK_DARK_AT = 0.70        # mean luma (0-1) under the ink that turns it dark
INK_LIGHT_AT = 0.56       # and that turns it back
INK_HOLD_S = 1.5
INK_SMOOTH_S = 0.4
INK_FADE_S = 0.25


def ink_spans(levels, fps):
    """[(t0, t1)]: the programme seconds the mark is in its dark ink, from the
    picture's brightness under the ink, one value (0-1) a frame."""
    import numpy as np
    x = np.asarray(levels, np.float64)
    if not len(x):
        return []
    n = max(1, int(round(INK_SMOOTH_S * fps)))
    if n > 1 and len(x) > n:
        pad = np.concatenate([np.full(n // 2, x[0]), x, np.full(n - 1 - n // 2, x[-1])])
        x = np.convolve(pad, np.ones(n) / n, mode="valid")
    hold = max(1, int(round(INK_HOLD_S * fps)))
    state = np.zeros(len(x), bool)
    dark = bool(x[0] >= INK_DARK_AT)
    for i, v in enumerate(x):
        if not dark and v >= INK_DARK_AT:
            dark = True
        elif dark and v <= INK_LIGHT_AT:
            dark = False
        state[i] = dark
    # a stretch shorter than the hold keeps the ink around it (the opening and the last stretch included)
    changed = True
    while changed:
        changed = False
        runs, a = [], 0
        for i in range(1, len(state) + 1):
            if i == len(state) or state[i] != state[a]:
                runs.append((a, i))
                a = i
        if len(runs) < 2:
            break
        k = min(range(len(runs)), key=lambda j: runs[j][1] - runs[j][0])
        a, b = runs[k]
        if b - a < hold:
            state[a:b] = not state[a]
            changed = True
    out, a = [], None
    for i, d in enumerate(list(state) + [False]):
        if d and a is None:
            a = i
        elif not d and a is not None:
            out.append((round(a / fps, 3), round(i / fps, 3)))
            a = None
    return out


def ink_levels(video, W, H, fps, ff="ffmpeg", anchor_y=None, until_s=None, geometry=None, under=None):
    """The picture's mean brightness (0-1, Rec.709 luma) under the mark's ink,
    one value a frame of ``video`` (W x H). ``geometry``: watermark_geometry's
    dict when the caller has it. ``under``: the RGBA mark (render()) the video
    already carries at geometry['image']: it is taken out of each frame
    (picture = (frame - a*mark) / (1 - a), where the mark leaves a fifth or more
    of the picture showing), so a marked export reads the picture under it."""
    if under is not None:
        return _levels_under(video, W, H, ff, until_s, geometry, under)
    import subprocess
    import numpy as np
    g = geometry
    if g is None:
        import renderer
        g = renderer.watermark_geometry(W, H, anchor_y)
    x0, y0, x1, y1 = g["ink"]
    w, h = max(2, x1 - x0) // 2 * 2, max(2, y1 - y0) // 2 * 2
    cmd = [ff, "-v", "error"] + (["-t", f"{float(until_s):.3f}"] if until_s else []) + [
        "-i", video, "-vf", f"crop={w}:{h}:{x0}:{y0},scale=32:12:flags=area,format=gray",
        "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True, timeout=900).stdout
    a = np.frombuffer(raw, np.uint8)
    n = len(a) // (32 * 12)
    return (a[:n * 32 * 12].reshape(n, -1).astype(np.float32).mean(axis=1) / 255.0).tolist()


def _levels_under(video, W, H, ff, until_s, g, under):
    import subprocess
    import numpy as np
    x0, y0, x1, y1 = g["ink"]
    ix, iy = g["image"][0], g["image"][1]
    w, h = max(2, x1 - x0) // 2 * 2, max(2, y1 - y0) // 2 * 2
    m = np.asarray(under, np.float32)[y0 - iy:y0 - iy + h, x0 - ix:x0 - ix + w]
    a = m[..., 3] / 255.0
    yc = 0.2126 * m[..., 0] + 0.7152 * m[..., 1] + 0.0722 * m[..., 2]
    keep = a < 0.8
    if keep.sum() < 50:
        keep = a < 0.95
    cmd = [ff, "-v", "error"] + (["-t", f"{float(until_s):.3f}"] if until_s else []) + [
        "-i", video, "-vf", f"crop={w}:{h}:{x0}:{y0},format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True, timeout=900).stdout
    fr = np.frombuffer(raw, np.uint8)
    n = len(fr) // (w * h)
    fr = fr[:n * w * h].reshape(n, h, w).astype(np.float32)
    bg = (fr - (a * yc)[None]) / np.maximum(1.0 - a, 0.05)[None]
    return (np.clip(bg[:, keep], 0, 255).mean(axis=1) / 255.0).tolist()


def ink_parts(base, out, light_idx, dark_idx, x, y, spans, end_s=None):
    """The filtergraph that lays the mark at (x, y) on stream ``base``: the
    white mark from input ``light_idx``, cross-faded into the dark twin from
    input ``dark_idx`` over ``spans`` (ink_spans). Without spans it is the
    one overlay the mark always was. ``light_idx`` None: the white mark is
    already in the picture (a finished export) and only the dark twin is laid,
    over it, on the spans."""
    if light_idx is None and not spans:
        return [f"[{base}]null[{out}]"]
    if not spans or dark_idx is None:
        return [f"[{light_idx}:v]format=rgba[wmbot]",
                f"[{base}][wmbot]overlay={x}:{y}:format=auto:shortest=1[{out}]"]
    f = INK_FADE_S
    light, a = [], 0.0
    for t0, t1 in spans:
        if t0 > a + 1e-6 or a == 0.0:
            light.append((a, t0))
        a = t1
    light.append((a, None))
    light = [(p, q) for p, q in light if q is None or q > p + 1e-6] if light_idx is not None else []
    parts = ([f"[{light_idx}:v]format=rgba,split={len(light)}" + "".join(f"[wl{i}]" for i in range(len(light)))] if light else []) + \
        [f"[{dark_idx}:v]format=rgba,split={len(spans)}" + "".join(f"[wd{i}]" for i in range(len(spans)))]
    layers = []
    for i, (p, q) in enumerate(light):
        fx = []
        if p > 0:
            fx.append(f"fade=t=in:st={p:.3f}:d={f:.3f}:alpha=1")
        if q is not None:
            fx.append(f"fade=t=out:st={q:.3f}:d={f:.3f}:alpha=1")
        en = f"between(t,{p:.3f},{q + f:.3f})" if q is not None else f"gte(t,{p:.3f})"
        layers.append((f"wl{i}", fx, en))
    for i, (p, q) in enumerate(spans):
        fx = [f"fade=t=in:st={p:.3f}:d={f:.3f}:alpha=1", f"fade=t=out:st={q:.3f}:d={f:.3f}:alpha=1"]
        layers.append((f"wd{i}", fx, f"between(t,{p:.3f},{q + f:.3f})"))
    cur = base
    for j, (lab, fx, en) in enumerate(layers):
        src = lab
        if fx:
            parts.append(f"[{lab}]" + ",".join(fx) + f"[{lab}f]")
            src = lab + "f"
        nxt = out if j == len(layers) - 1 else f"wm{j}"
        parts.append(f"[{cur}][{src}]overlay={x}:{y}:format=auto:shortest=1:enable='{en}'[{nxt}]")
        cur = nxt
    return parts
