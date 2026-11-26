"""Chroma key of the main footage: a green or blue screen replaced by a new
background, with the backdrop's colour cast taken off the subject's edges.

p6311 (26 Nov 2026): "this video is shot on a green background, remove the
green around Elena, the candles and the objects, clean edges, no green
spill, composite it over a new background". The product had no keying at
all: set_background swaps what is behind the PERSON matte (candles, flags
and desks go with the backdrop), and the agent answered "no green found".

Three parts, one source of truth for each:

* ``measure_frames`` reads the backdrop off sampled frames: which screen
  (green / blue), its colour, how much of the frame it covers, and the
  chromakey ``similarity`` that takes the whole measured backdrop (the
  spread of its colour distances, in chromakey's own UV metric).
* ``graph_parts`` is the ffmpeg chain the renderer splices on the SOURCE
  clock (right after repaint patches, before any trim, framing or grade):
  chromakey alpha, despill on the foreground, the new background under it.
  Every branch is a split of the same source stream, so every input of
  every merge carries the same timestamps.
* ``dry_run`` pushes sampled frames through that very chain and measures
  the share of backdrop-coloured pixels before and after, so the tool that
  writes the key reports a measured result, never a hope.
"""

import os
import subprocess

import numpy as np

# A pixel belongs to a screen when that channel leads both others by this
# factor and is bright enough to be a lit backdrop (not a dark green shirt
# in shadow). Signal measurement of the footage, not a reading of the ask.
SCREEN_LEAD = 1.25
SCREEN_MIN = 60
# Below this share of the frame there is no backdrop to key: the footage
# was keyed before (only a thin spill line remains) or never had one.
BACKDROP_MIN_SHARE = 0.03
# A keyable screen is a strongly coloured backdrop: its colour sits at least
# this far from neutral grey in chromakey's own UV metric (a lit green
# screen measures about 0.18-0.21, a blue one about 0.20). A night sky,
# foliage or a teal wall in an already-composited picture measures 0.08-0.11
# and is never taken for a screen.
SCREEN_MIN_CHROMA = 0.13
SIMILARITY_MIN, SIMILARITY_MAX = 0.06, 0.40
DEFAULT_BLEND = 0.06


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


def screen_mask(rgb, screen):
    rgb = rgb.astype(np.int32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    if screen == "blue":
        return (b > SCREEN_MIN) & (b > SCREEN_LEAD * r) & (b > SCREEN_LEAD * g)
    return (g > SCREEN_MIN) & (g > SCREEN_LEAD * r) & (g > SCREEN_LEAD * b)


def measure_frames(frames):
    """frames: list of HxWx3 uint8 RGB arrays. Returns the backdrop reading:
    {"screen", "colour" ('#rrggbb'), "share" (0-1 of all sampled pixels),
    "similarity", "keyable" (share >= BACKDROP_MIN_SHARE)}."""
    frames = [np.asarray(f) for f in frames if f is not None]
    if not frames:
        return {"screen": "green", "colour": None, "share": 0.0,
                "similarity": None, "chroma": 0.0, "keyable": False}
    best = None
    for screen in ("green", "blue"):
        px, total = [], 0
        for f in frames:
            m = screen_mask(f, screen)
            total += m.size
            if m.any():
                px.append(f[m])
        n = sum(len(p) for p in px)
        share = n / float(total or 1)
        if best is None or share > best[1]:
            best = (screen, share, np.concatenate(px) if px else None)
    screen, share, px = best
    if px is None or not len(px):
        return {"screen": screen, "colour": None, "share": 0.0,
                "similarity": None, "chroma": 0.0, "keyable": False}
    key = np.median(px, axis=0)
    dist = uv_distance(px, key)
    sim = float(np.clip(np.percentile(dist, 95) + 0.02,
                        SIMILARITY_MIN, SIMILARITY_MAX))
    colour = "#%02x%02x%02x" % tuple(int(round(c)) for c in key)
    chroma = float(uv_distance(np.array([[128.0, 128.0, 128.0]]), key)[0])
    return {"screen": screen, "colour": colour, "share": round(share, 4),
            "similarity": round(sim, 3), "chroma": round(chroma, 3),
            "keyable": (share >= BACKDROP_MIN_SHARE
                        and chroma >= SCREEN_MIN_CHROMA)}


def screen_share(frames, screen, colour, similarity):
    """Share of pixels a viewer reads as backdrop: screen-coloured AND within
    the key's distance of the backdrop colour."""
    key = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
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


def graph_parts(vsrc, item, bg_idx=None, src_w=None, src_h=None, tag="ck"):
    """(parts, out_label): the keyed composite of [vsrc]. ``bg_idx`` is the
    ffmpeg input index of the background picture (image, video or the
    gradient plate) — None for a solid colour, drawn on the source itself."""
    t = tag
    sim = float(item.get("similarity") or 0.12)
    blend = float(item.get("blend") if item.get("blend") is not None
                  else DEFAULT_BLEND)
    despill = float(item.get("despill") if item.get("despill") is not None
                    else 0.6)
    spill = "blue" if item.get("spill") == "blue" else "green"
    bg = item.get("background") or {"kind": "solid", "colour": "#000000"}
    parts = [f"[{vsrc}]split=3[{t}f][{t}a][{t}b]",
             f"[{t}a]format=yuva420p,chromakey=0x{_hex(item.get('colour'))}"
             f":{sim:.4f}:{blend:.4f},alphaextract[{t}al]"]
    fg = f"{t}f"
    if despill > 0:
        parts.append(f"[{t}f]format=rgba,despill=type={spill}:"
                     f"mix={despill:.3f}:expand=0[{t}d]")
        fg = f"{t}d"
    else:
        parts.append(f"[{t}f]format=rgba[{t}d]")
        fg = f"{t}d"
    parts.append(f"[{fg}][{t}al]alphamerge[{t}fg]")
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


def input_args(edl, workdir, src_w, src_h, fetch):
    """ffmpeg input args for the key's background picture, or [] for a solid
    colour (or no key). ``fetch(key)`` returns a local path or URL."""
    item = ((edl or {}).get("effects") or {}).get("key")
    if not item:
        return []
    bg = item.get("background") or {}
    kind = bg.get("kind")
    if kind == "gradient":
        path = os.path.join(workdir, f"key_gradient_{item.get('id', 'k')}.png")
        gradient_plate(path, src_w or 1920, src_h or 1080, bg.get("colour"),
                       bg.get("colour2"))
        return ["-i", path]
    if kind == "asset" and bg.get("asset_key"):
        local = fetch(bg["asset_key"])
        if os.path.splitext(str(bg["asset_key"]))[1].lower() in IMAGE_EXTS:
            return ["-i", local]
        return ["-stream_loop", "-1", "-i", local]
    return []


# ── measured dry run ────────────────────────────────────────────────────────

def sample_frames(path, times, size=270):
    """RGB frames of ``path`` at ``times`` (seconds), scaled to size x size."""
    out = []
    for t in times:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
             "-frames:v", "1", "-vf", f"scale={size}:{size},format=rgb24",
             "-f", "rawvideo", "-"], capture_output=True, timeout=60)
        if r.returncode == 0 and len(r.stdout) == size * size * 3:
            out.append(np.frombuffer(r.stdout, np.uint8).reshape(size, size, 3))
    return out


def dry_run(path, item, times, size=270):
    """Key the sampled frames with the render's own chain over a solid
    background and measure the backdrop share before and after."""
    before = sample_frames(path, times, size)
    item = dict(item)
    probe = dict(item, background={"kind": "solid", "colour": "#808080"})
    parts, out = graph_parts("ksrc", probe, None, size, size)
    after = []
    for t in times:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
             "-frames:v", "1", "-filter_complex",
             ";".join([f"[0:v]scale={size}:{size},setsar=1[ksrc]"] + parts),
             "-map", f"[{out}]", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            capture_output=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or b"").decode(errors="replace")[-300:])
        if len(r.stdout) == size * size * 3:
            after.append(np.frombuffer(r.stdout, np.uint8).reshape(size, size, 3))
    screen = item.get("spill") or "green"
    sim = float(item.get("similarity") or 0.12)
    return {"before": round(screen_share(before, screen, item["colour"], sim), 4),
            "after": round(screen_share(after, screen, item["colour"], sim), 4),
            "frames": len(after)}
