"""Chroma key of the main footage: a green or blue screen replaced by a new
background, or the screen's leftover fringe cleaned off footage that was
keyed before.

p6311 (26 Nov 2026): "this video is shot on a green background, remove the
green around Elena, the candles and the objects, clean edges, no green
spill, composite it over a new background (I will say which if needed)".
The upload was a greeting keyed earlier over pictures of the city: a green
line 1-2 px wide runs round the presenter, the flags and the fir sprigs,
and a green disc sits round the candle flame. The product had no keying at
all, and its first version of this tool laid a new background behind the
person cut-out, which drops the candles and props and leaves the green line.

Two operations, each written as ``effects.key`` and rendered on the SOURCE
clock (right after repaint patches, before any trim, framing or grade):

* ``mode="key"``: a screen behind the subject. chromakey alpha on the
  measured backdrop colour, despill on a band of the foreground next to the
  screen (green props and clothes away from it keep their colour), and the
  new background under everything that is not the screen.
* ``mode="fringe"``: footage keyed before. The leftover screen colour is
  measured, every pixel of that colour is filled from the picture around it
  (a normalised blur of the pixels that are not the leftover colour), so the
  line and the disc take the colour of what lies next to them. The
  footage's own backgrounds, the presenter and every prop stay.

Measurement, all signal processing of the footage, none of the ask:

* ``measure_frames`` reads the backdrop: screen (green / blue), its colour,
  how much of the picture it covers, in how many sampled frames, how steady
  its colour is between frames, and chromakey's ``similarity`` that takes
  the measured spread. ``keyable`` says whether a screen stands behind the
  subject; a dim, unevenly lit screen that fills the frame is one, a teal
  wall or a sky in a few frames of a composite is not. When there is no
  screen, the leftover fringe (thin screen-coloured lines) is read instead.
* ``dry_run`` pushes sampled frames through the render's own chain twice,
  over black and over white. Their difference is the key's real
  transparency per pixel, measured on the output, independent of the key's
  own settings: how much of the measured screen still shows, and how much
  of the subject was keyed away.
"""

import os
import subprocess

import numpy as np

# A pixel belongs to a screen when that channel leads both others by this
# factor and is bright enough to be a lit backdrop (not a dark green shirt
# in shadow). Signal measurement of the footage, not a reading of the ask.
SCREEN_LEAD = 1.25
SCREEN_MIN = 60
# The screen REGION a dry run measures against: a looser reading that keeps
# a vignetted, dim corner of the same backdrop (it is still screen to a
# viewer, and a key that leaves it is not done).
REGION_LEAD = 1.15
REGION_MIN = 35
# Below this share of the frame there is no backdrop to key.
BACKDROP_MIN_SHARE = 0.03
# A backdrop is a screen when it stands behind the subject in (nearly) every
# sampled frame with one steady colour, and either covers a real part of
# the picture at any visible tint, or is a strongly coloured smaller patch
# (a tight shot of a lit screen). Measured 26 Nov: synthetic dim and
# vignetted screens (60,140,70)..(100,180,100) read share 0.71, tint
# 0.06-0.11, steadiness 0.001; 14 real-set reference films and the p6311
# composite read share <= 0.09, or a colour that moves between frames.
SCREEN_SHARE = 0.15
SCREEN_MIN_TINT = 0.04
SMALL_SCREEN_TINT = 0.13
SCREEN_PRESENCE = 0.75
SCREEN_STEADY = 0.04
SIMILARITY_MIN, SIMILARITY_MAX = 0.04, 0.40
DEFAULT_BLEND = 0.06
DEFAULT_DESPILL = 0.6
# Leftover fringe: thin screen-coloured lines on footage keyed before.
FRINGE_LEAD = 1.12
FRINGE_MIN = 60
FRINGE_MIN_SHARE = 0.0005
# A leftover is lines: at least this share of the tinted pixels must be
# thin (p6311 green: 0.76; its twilight-city blue: 0.09).
FRINGE_LINES = 0.4
# The fill takes a pixel in full at this share of the thin fringe's median
# channel excess (p6311 measures 23 at 540 px), and starts at half of it.
FRINGE_FULL = 1.0
FRINGE_EXCESS_MIN, FRINGE_EXCESS_MAX = 15.0, 60.0
# A faint screen tint (a 1 px line, a block of green chroma beside an edge)
# is despilled in place over this excess ramp.
FRINGE_TINT_LO, FRINGE_TINT_HI = 3.0, 10.0


def _rgb_to_uv(rgb):
    """BT.601 limited-range chroma, as ffmpeg's chromakey converts its key
    colour (RGB_TO_U_CCIR / RGB_TO_V_CCIR)."""
    r, g, b = (rgb[..., 0].astype(np.float64), rgb[..., 1].astype(np.float64),
               rgb[..., 2].astype(np.float64))
    u = (-0.148 * r - 0.291 * g + 0.439 * b) + 128.0
    v = (0.439 * r - 0.368 * g - 0.071 * b) + 128.0
    return u, v


def uv_distance(rgb, key_rgb):
    """chromakey's per-pixel difference: sqrt(du^2+dv^2) / (255*sqrt(2))."""
    u, v = _rgb_to_uv(rgb)
    ku, kv = _rgb_to_uv(np.asarray(key_rgb, dtype=np.float64)[None, :])
    return np.sqrt((u - ku[0]) ** 2 + (v - kv[0]) ** 2) / (255.0 * 2 ** 0.5)


def _lead_mask(rgb, screen, lead, floor):
    rgb = rgb.astype(np.int32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    if screen == "blue":
        return (b > floor) & (b > lead * r) & (b > lead * g)
    return (g > floor) & (g > lead * r) & (g > lead * b)


def screen_mask(rgb, screen):
    return _lead_mask(rgb, screen, SCREEN_LEAD, SCREEN_MIN)


def region_mask(rgb, screen):
    return _lead_mask(rgb, screen, REGION_LEAD, REGION_MIN)


def _hexrgb(colour):
    c = str(colour or "").lstrip("#")
    return [int(c[i:i + 2], 16) for i in (0, 2, 4)]


def _shift_reduce(m, k, fn):
    out = m.copy()
    for _ in range(int(k)):
        p = np.pad(out, 1, mode="edge")
        acc = p[1:-1, 1:-1].copy()
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                acc = fn(acc, p[dy:dy + out.shape[0], dx:dx + out.shape[1]])
        out = acc
    return out


def erode(m, k=1):
    return _shift_reduce(m, k, np.logical_and)


def dilate(m, k=1):
    return _shift_reduce(m, k, np.logical_or)


def _tint(colour):
    return float(uv_distance(np.array([[128.0, 128.0, 128.0]]),
                             _hexrgb(colour))[0])


def _reading(px, share):
    key = np.median(px, axis=0)
    dist = uv_distance(px, key)
    sim = float(np.clip(np.percentile(dist, 95) + 0.02,
                        SIMILARITY_MIN, SIMILARITY_MAX))
    colour = "#%02x%02x%02x" % tuple(int(round(c)) for c in key)
    return colour, round(sim, 3), round(_tint(colour), 3)


def excess(rgb, screen):
    """How far the screen's channel stands above the picture's own colour:
    min(C - mean(others), 2(C - other1), 2(C - other2)), 0 when it does not
    lead. Exactly what the fringe render computes (colorchannelmixer, the
    darker of the three planes), so the measurement and the render agree."""
    rgb = rgb.astype(np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    c, o1, o2 = (b, r, g) if screen == "blue" else (g, r, b)
    e = np.minimum(c - 0.5 * (o1 + o2), np.minimum(2 * (c - o1), 2 * (c - o2)))
    return np.clip(e, 0.0, 255.0)


def measure_fringe(frames, screens=("green", "blue")):
    """The leftover of a screen on footage keyed before: lines of the
    screen's colour (what a 2 px opening removes). A leftover is mostly
    lines; a colour that belongs to the picture (a twilight sky, a teal
    wall, foliage) is mostly areas. {"screen", "colour", "excess" (the
    channel excess the fill takes in full), "share", "lines" (share of the
    tinted pixels that are lines)} or None when there is no such line."""
    best = None
    for screen in screens:
        px, n_thin, n_all, total = [], 0, 0, 0
        for f in frames:
            m = _lead_mask(f, screen, FRINGE_LEAD, FRINGE_MIN)
            thin = m & ~dilate(erode(m, 2), 2)
            total += m.size
            n_all += int(m.sum())
            n_thin += int(thin.sum())
            if thin.any():
                px.append(f[thin])
        share = n_thin / float(total or 1)
        lines = n_thin / float(n_all or 1)
        if share < FRINGE_MIN_SHARE or lines < FRINGE_LINES or not px:
            continue
        px = np.concatenate(px)
        raw = float(np.median(excess(px, screen)))
        if raw < FRINGE_EXCESS_MIN:      # dim tinted noise, not a leftover
            continue
        if best is None or lines > best[2]:
            best = (screen, share, lines, px, raw)
    if best is None:
        return None
    screen, share, lines, px, raw = best
    key = np.median(px, axis=0)
    full = float(np.clip(raw * FRINGE_FULL, FRINGE_EXCESS_MIN,
                         FRINGE_EXCESS_MAX))
    return {"screen": screen,
            "colour": "#%02x%02x%02x" % tuple(int(round(c)) for c in key),
            "excess": round(full, 1), "share": round(share, 4),
            "lines": round(lines, 3)}


def measure_frames(frames, screens=("green", "blue")):
    """frames: list of HxWx3 uint8 RGB arrays. The backdrop reading:
    {"screen", "colour" ('#rrggbb'), "share" (0-1 of all sampled pixels),
    "similarity", "chroma" (tint), "presence" (share of frames showing it),
    "steady" (median UV distance of each frame's colour from the key),
    "keyable", "fringe" (measure_fringe when not keyable)}."""
    frames = [np.asarray(f) for f in frames if f is not None]
    empty = {"screen": "green", "colour": None, "share": 0.0,
             "similarity": None, "chroma": 0.0, "presence": 0.0,
             "steady": None, "keyable": False, "fringe": None}
    if not frames:
        return empty
    best = None
    for screen in screens:
        masks = [screen_mask(f, screen) for f in frames]
        n = sum(int(m.sum()) for m in masks)
        if best is None or n > best[1]:
            best = (screen, n, masks)
    screen, n, masks = best
    total = sum(m.size for m in masks)
    share = n / float(total or 1)
    if not n:
        return dict(empty, screen=screen, fringe=measure_fringe(frames, screens))
    px = np.concatenate([f[m] for f, m in zip(frames, masks) if m.any()])
    colour, sim, tint = _reading(px, share)
    key = _hexrgb(colour)
    present, drift = 0, []
    for f, m in zip(frames, masks):
        if m.mean() >= BACKDROP_MIN_SHARE / 3:
            present += 1
            fk = np.median(f[m], axis=0)
            drift.append(float(uv_distance(fk[None, :], key)[0]))
    presence = present / float(len(frames))
    steady = float(np.median(drift)) if drift else None
    keyable = bool(
        presence >= SCREEN_PRESENCE and steady is not None
        and steady <= SCREEN_STEADY and tint >= SCREEN_MIN_TINT
        and (share >= SCREEN_SHARE
             or (share >= BACKDROP_MIN_SHARE and tint >= SMALL_SCREEN_TINT)))
    return {"screen": screen, "colour": colour, "share": round(share, 4),
            "similarity": sim, "chroma": tint,
            "presence": round(presence, 3),
            "steady": None if steady is None else round(steady, 3),
            "keyable": keyable,
            "fringe": None if keyable else measure_fringe(frames, screens)}


def screen_share(frames, screen, colour, similarity):
    """Share of pixels that are screen-coloured AND within ``similarity`` of
    the backdrop colour (a reading of SOURCE frames; a dry run measures the
    key itself with ``dry_run``)."""
    key = _hexrgb(colour)
    hit, total = 0, 0
    for f in frames:
        f = np.asarray(f)
        m = screen_mask(f, screen) & (uv_distance(f, key) <= similarity)
        hit += int(m.sum())
        total += m.size
    return hit / float(total or 1)


# ── the render chain ────────────────────────────────────────────────────────

def _hex(c, default="000000"):
    c = str(c or "").lstrip("#")
    return c.lower() if len(c) == 6 else default


def band_px(src_h):
    """Width of the despill band next to the screen, and of the fringe
    growth, in source pixels: 2 px at 540, 4 at 1080, 8 at 2160."""
    return max(2, min(8, int(round(float(src_h or 1080) / 270.0))))


_EXCESS_MIX = {
    # colorchannelmixer rows (R', G', B') giving C - mean(others),
    # 2(C - other1), 2(C - other2) for the screen's channel C.
    "green": "rr=-0.5:rg=1:rb=-0.5:gr=-2:gg=2:gb=0:br=0:bg=2:bb=-2",
    "blue": "rr=-0.5:rg=-0.5:rb=1:gr=-2:gg=0:gb=2:br=0:bg=-2:bb=2",
}


def _fringe_parts(vsrc, item, src_h, t):
    """Take the leftover screen colour off footage keyed before, in two
    measured strengths of the screen channel's excess (see ``excess``):

    * a faint tint (excess from FRINGE_TINT_LO to FRINGE_TINT_HI, grown by
      the band) is despilled in place: the line keeps its light and texture
      and loses the green (a 1 px outline, a compression block of green
      chroma next to an edge);
    * a strong leftover (excess from half of ``excess`` to ``excess``, grown
      by the band: the disc round a flame, a thick line) is filled from the
      picture around it, blur(P*W) / blur(W) with W = 1 - M, merged by M.

    Grey, white, skin and the footage's own backgrounds have no excess and
    are untouched."""
    screen = "blue" if item.get("spill") == "blue" else "green"
    hi = float(item.get("excess") or 30.0)
    lo = hi / 2.0
    mix = float(item.get("despill") if item.get("despill") is not None
                else DEFAULT_DESPILL)
    grow = ",".join(["dilation"] * band_px(src_h))
    r = max(4, int(round(float(src_h or 1080) * 0.03)))
    blur = f"boxblur={r}:2:{r}:2"
    tlo, thi = FRINGE_TINT_LO, FRINGE_TINT_HI

    def ramp(a, b):
        # grown by the band, then feathered by it (a hard-edged mask
        # draws the 8 px compression blocks of the leftover as squares)
        k = band_px(src_h)
        return (f"lut=y='clip((val-{a:.2f})*255/{b - a:.2f},0,255)',"
                f"format=gray,{grow},format=gray,boxblur={k}:1,format=gray")
    return [
        f"[{vsrc}]split=2[{t}a][{t}b]",
        # Every grey stream is pinned to gray: left free, format
        # negotiation carries gbrp back through these filters and the lut
        # then ramps one plane only.
        f"[{t}a]format=gbrp,colorchannelmixer={_EXCESS_MIX[screen]},"
        f"extractplanes=r+g+b[{t}e1][{t}e2][{t}e3]",
        f"[{t}e1]format=gray[{t}g1]",
        f"[{t}e2]format=gray[{t}g2]",
        f"[{t}e3]format=gray[{t}g3]",
        f"[{t}g1][{t}g2]blend=all_mode=darken,format=gray[{t}e12]",
        f"[{t}e12][{t}g3]blend=all_mode=darken,format=gray,split=2"
        f"[{t}ex1][{t}ex2]",
        f"[{t}ex1]{ramp(tlo, thi)},format=gbrp[{t}mt]",
        f"[{t}ex2]{ramp(lo, hi)},split=3[{t}m1][{t}m2][{t}m3]",
        # faint tint: despilled in place
        f"[{t}b]format=gbrp,split=2[{t}b1][{t}b2]",
        f"[{t}b2]format=rgba,despill=type={screen}:mix={mix:.3f}:expand=0,"
        f"format=gbrp[{t}bd]",
        f"[{t}b1][{t}bd][{t}mt]maskedmerge,split=2[{t}p1][{t}p2]",
        # strong leftover: filled from the picture around it
        f"[{t}m1]negate,format=gbrp[{t}wg]",
        f"[{t}p1][{t}wg]blend=all_mode=multiply,{blur}[{t}pw]",
        f"[{t}m2]negate,format=gbrp,{blur}[{t}wb]",
        f"[{t}pw][{t}wb]blend=all_mode=divide[{t}fill]",
        f"[{t}m3]format=gbrp[{t}m]",
        f"[{t}p2][{t}fill][{t}m]maskedmerge,format=yuv420p[{t}out]",
    ], f"{t}out"


def graph_parts(vsrc, item, bg_idx=None, src_w=None, src_h=None, tag="ck"):
    """(parts, out_label): the keyed composite of [vsrc] (or, for
    ``mode="fringe"``, [vsrc] with its leftover fringe filled). ``bg_idx``
    is the ffmpeg input index of the background picture (image, video or
    the gradient plate) — None for a solid colour, drawn on the source
    itself."""
    t = tag
    if item.get("mode") == "fringe":
        return _fringe_parts(vsrc, item, src_h, t)
    sim = float(item.get("similarity") or 0.12)
    blend = float(item.get("blend") if item.get("blend") is not None
                  else DEFAULT_BLEND)
    despill = float(item.get("despill") if item.get("despill") is not None
                    else DEFAULT_DESPILL)
    spill = "blue" if item.get("spill") == "blue" else "green"
    bg = item.get("background") or {"kind": "solid", "colour": "#000000"}
    key = (f"[{t}a]format=yuva420p,chromakey=0x{_hex(item.get('colour'))}"
           f":{sim:.4f}:{blend:.4f},alphaextract")
    parts = [f"[{vsrc}]split=3[{t}f][{t}a][{t}b]"]
    if despill > 0:
        # Despill only on a band of the foreground next to the screen (the
        # subject's edges, hair, the rim of a prop): a green prop or a teal
        # shirt away from the screen keeps its colour.
        band = ",".join(["erosion"] * band_px(src_h))
        parts += [
            key + f",split=2[{t}al][{t}am]",
            f"[{t}am]format=gray,{band},negate,format=gray,format=gbrp"
            f"[{t}bm]",
            f"[{t}f]split=2[{t}f1][{t}f2]",
            f"[{t}f1]format=gbrp[{t}fo]",
            f"[{t}f2]format=rgba,despill=type={spill}:mix={despill:.3f}:"
            f"expand=0,format=gbrp[{t}fd]",
            f"[{t}fo][{t}fd][{t}bm]maskedmerge,format=rgba[{t}d]",
        ]
    else:
        parts += [key + f"[{t}al]", f"[{t}f]format=rgba[{t}d]"]
    parts.append(f"[{t}d][{t}al]alphamerge[{t}fg]")
    if bg_idx is None:
        parts.append(f"[{t}b]drawbox=x=0:y=0:w=iw:h=ih:"
                     f"color=0x{_hex(bg.get('colour'))}@1:t=fill[{t}bg]")
    else:
        w, h = int(src_w or 1920), int(src_h or 1080)
        parts.append(f"[{bg_idx}:v]scale={w}:{h}:force_original_aspect_ratio="
                     f"increase,crop={w}:{h},setsar=1,format=yuv420p[{t}bi]")
        # The source branch is the MAIN input: the composite keeps the
        # source's clock; a still holds its one frame (eof_action=repeat).
        parts.append(f"[{t}b][{t}bi]overlay=eof_action=repeat:shortest=0"
                     f"[{t}bg]")
    parts.append(f"[{t}bg][{t}fg]overlay=shortest=0:format=auto,"
                 f"format=yuv420p[{t}out]")
    return parts, f"{t}out"


def gradient_plate(path, w, h, colour, colour2):
    """A vertical two-colour gradient PNG at the source's size."""
    from PIL import Image
    a = np.array([int(_hex(colour)[i:i + 2], 16) for i in (0, 2, 4)], float)
    b = np.array([int(_hex(colour2 or colour)[i:i + 2], 16) for i in (0, 2, 4)],
                 float)
    k = np.linspace(0.0, 1.0, int(h))[:, None, None]
    img = (a[None, None, :] * (1 - k) + b[None, None, :] * k)
    img = np.repeat(img, int(w), axis=1).astype(np.uint8)
    Image.fromarray(img, "RGB").save(path)
    return path


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
FALLBACK_BG = {"kind": "gradient", "colour": "#2b3140", "colour2": "#0d0f14"}


def input_args(edl, workdir, src_w, src_h, fetch, attempts=2):
    """(args, item): ffmpeg input args for the key's background picture
    ([] for a solid colour, a fringe clean or no key) and the item the
    render keys with. ``fetch(key)`` returns a local path or URL; a fetch
    that fails is tried again, and a picture that still cannot be read
    keys onto the film's neutral gradient, with ``item["fallback"]``
    saying why, so the render never fails and never goes black."""
    item = ((edl or {}).get("effects") or {}).get("key")
    if not item:
        return [], None
    if item.get("mode") == "fringe":
        return [], item
    bg = item.get("background") or {}
    kind = bg.get("kind")
    if kind == "asset" and bg.get("asset_key"):
        err = None
        for _ in range(max(1, int(attempts))):
            try:
                local = fetch(bg["asset_key"])
                if local:
                    ext = os.path.splitext(str(bg["asset_key"]))[1].lower()
                    if ext in IMAGE_EXTS:
                        return ["-i", local], item
                    return ["-stream_loop", "-1", "-i", local], item
            except Exception as e:  # noqa: BLE001 - tried again, then repaired
                err = e
        item = dict(item, background=dict(FALLBACK_BG),
                    fallback=(f"background {bg['asset_key']} could not be "
                              f"read ({str(err)[:120] if err else 'empty'})"))
        bg = item["background"]
        kind = "gradient"
    if kind == "gradient":
        path = os.path.join(workdir, f"key_gradient_{item.get('id', 'k')}.png")
        gradient_plate(path, src_w or 1920, src_h or 1080, bg.get("colour"),
                       bg.get("colour2"))
        return ["-i", path], item
    return [], item


# ── measured dry run ────────────────────────────────────────────────────────

def sample_pairs(path, times, size=270):
    """[(t, frame)]: the RGB frame of ``path`` at each time it could be read,
    scaled to size x size, with the time it was read at (a time that cannot
    be read, past the end for instance, is left out with its frame)."""
    out = []
    for t in times:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
             "-frames:v", "1", "-vf", f"scale={size}:{size},format=rgb24",
             "-f", "rawvideo", "-"], capture_output=True, timeout=60)
        if r.returncode == 0 and len(r.stdout) == size * size * 3:
            out.append((t, np.frombuffer(r.stdout, np.uint8).reshape(
                size, size, 3)))
    return out


def sample_frames(path, times, size=270):
    """RGB frames of ``path`` at ``times`` (seconds), scaled to size x size."""
    return [f for _t, f in sample_pairs(path, times, size)]


def _render_probe(path, t, item, size):
    """The source frame at ``t`` through the render's own chain: for a key
    twice (over black, over white), for a fringe clean once."""
    if item.get("mode") == "fringe":
        parts, out = graph_parts("ksrc", item, None, size, size)
        fc = ";".join([f"[0:v]scale={size}:{size},setsar=1[ksrc]"] + parts)
        width = size
    else:
        pa, oa = graph_parts("ks1", dict(item, background={
            "kind": "solid", "colour": "#000000"}), None, size, size, tag="ka")
        pb, ob = graph_parts("ks2", dict(item, background={
            "kind": "solid", "colour": "#ffffff"}), None, size, size, tag="kb")
        fc = ";".join([f"[0:v]scale={size}:{size},setsar=1,split=2[ks1][ks2]"]
                      + pa + pb + [f"[{oa}][{ob}]hstack=2[kout]"])
        out, width = "kout", size * 2
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
         "-frames:v", "1", "-filter_complex", fc, "-map", f"[{out}]",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode(errors="replace")[-300:])
    if len(r.stdout) != size * width * 3:
        return None
    return np.frombuffer(r.stdout, np.uint8).reshape(size, width, 3)


def dry_run(path, item, times, size=270):
    """Measure the key the render would draw, on sampled frames.

    Key: the share of the screen REGION (region_mask on the source, a
    looser reading than the key's own colour distance, eroded 2 px off the
    subject's edge) that still shows (transparency < 50%), and the mean
    transparency of the subject (everything 3 px or more away from that
    region). Fringe: the share of the picture reading as the leftover's
    channel-led colour before and after, and the share the clean changed."""
    # each source frame stays with the time it was read at: the render
    # probe of that same time is compared with it
    before = sample_pairs(path, times, size)
    screen = item.get("spill") or "green"
    if item.get("mode") == "fringe":
        tinted_b = tinted_a = changed = total = 0
        n = 0
        for t, f in before:
            out = _render_probe(path, t, item, size)
            if out is None:
                continue
            n += 1
            tinted_b += int(_lead_mask(f, screen, FRINGE_LEAD, FRINGE_MIN).sum())
            tinted_a += int(_lead_mask(out, screen, FRINGE_LEAD,
                                       FRINGE_MIN).sum())
            changed += int((np.abs(out.astype(np.int16) - f.astype(np.int16))
                            .max(axis=-1) > 12).sum())
            total += f.shape[0] * f.shape[1]
        total = float(total or 1)
        return {"mode": "fringe", "before": round(tinted_b / total, 4),
                "after": round(tinted_a / total, 4),
                "changed": round(changed / total, 4), "frames": n}
    left = region = lost = subject = 0.0
    n = 0
    for t, f in before:
        out = _render_probe(path, t, item, size)
        if out is None:
            continue
        n += 1
        a = out[:, :size].astype(np.float32)
        b = out[:, size:].astype(np.float32)
        clear = np.clip((b - a).mean(axis=-1) / 255.0, 0.0, 1.0)
        reg = region_mask(f, screen)
        inner = erode(reg, 2)
        subj = ~dilate(reg, 3)
        left += float((clear[inner] < 0.5).sum())
        region += float(inner.sum())
        lost += float(clear[subj].sum())
        subject += float(subj.sum())
    return {"mode": "key",
            "screen": round(region / float(n * size * size or 1), 4),
            "left": round(left / (region or 1.0), 4),
            "subject_lost": round(lost / (subject or 1.0), 4),
            "frames": n}
