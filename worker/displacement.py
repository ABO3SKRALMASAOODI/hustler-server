"""Displacement materials for region effects (track 1C; ROADMAP §1C, §2).

Ribbed (fluted) glass, refraction and heat haze compile to ffmpeg's
``displace`` with two gray maps (x and y; 128 = no displacement, a level
of v moves the sample by v - 128 pixels). The maps are built ONCE per item
in numpy and written as images — never a per-frame ``geq`` expression
(ROADMAP §2 benchmark: displace with a map built once costs 1.7 CPU-s per
50 frames of 1080p against 4.1 for a per-frame geq map).

  ribbed_glass  vertical cylindrical ribs: inside each rib the picture is
                a magnified, slightly shifted copy (a fluted glass panel),
                with a soft highlight on each rib's left edge and a dark
                line on its right (an RGBA "shade" still laid over the
                displaced picture)
  refraction    a smooth, seeded low-frequency field: the picture bends as
                if seen through thick moulded glass; per frame, the
                region's own edges add a bevel (bend + sheen) derived from
                its mask with gblur + convolution (no per-pixel expression)
  heat          a rising shimmer: a short seamless loop of maps (one second)
                played on repeat

Sizes are pixels of the branch the material runs on (the region's box);
pattern sizes are authored at a 1080-px-high frame and scale with the
frame height ``H`` and the item's ``scale``. ``strength`` (0-1) scales the
displacement itself, so a half-strength material is a gentler glass, not
a half-mixed one.

Pure numpy + Pillow; nothing here runs ffmpeg except to encode the heat
loop (lossless ffv1).
"""

import os
import subprocess

import numpy as np

REF_H = 1080.0
RIB_PX = 40.0             # flute width at a 1080-px short side
RIB_SPREAD = 1.8          # source width a flute shows / its width (> 1:
                          # each flute shows a compressed slice of a wider
                          # strip — fluted glass minifies)
RIB_SHADE = 0.13          # peak darkening at a flute's edge
RIB_SHINE = 0.07          # peak brightening across its lit side
REFRACT_LENS = 0.16       # the lens's magnification pull toward the centre
REFRACT_CELL_PX = 150.0   # refraction ripple feature size
REFRACT_AMP_PX = 10.0     # its peak displacement
# Refraction's bevel (built per frame from the region's own mask, so a
# word's letters each bend at their edges like cut glass; compositor
# ._bevel): the mask is blurred by BEVEL_PX, its gradient bends the picture
# by up to BEVEL_BEND_PX toward the inside of the shape and lights the
# edges facing up-left by up to BEVEL_SHINE levels (the far edges darken by
# BEVEL_SHADOW of that).
BEVEL_PX = 7.0
BEVEL_BEND_PX = 12.0
BEVEL_SHINE = 60.0
BEVEL_SHADOW = 0.35
HEAT_WAVE_PX = 46.0       # heat ripple wavelength
HEAT_AMP_PX = 5.0         # heat ripple amplitude
HEAT_LOOP_S = 1.0         # one seamless loop


def _u8(field):
    return np.clip(np.round(128.0 + field), 0, 255).astype(np.uint8)


def _smooth_noise(w, h, cell, seed):
    """A smooth field in [-1, 1]: seeded noise on a coarse grid, upsampled
    bicubic (Pillow), so the bend has no visible grid."""
    from PIL import Image
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    gw = max(2, int(np.ceil(w / cell)) + 2)
    gh = max(2, int(np.ceil(h / cell)) + 2)
    coarse = rng.uniform(-1.0, 1.0, (gh, gw)).astype(np.float32)
    im = Image.fromarray(coarse, mode="F").resize(
        (int(w + 2 * cell), int(h + 2 * cell)), Image.BICUBIC)
    a = np.asarray(im, dtype=np.float32)
    ox, oy = int(cell // 2), int(cell // 2)
    a = a[oy:oy + h, ox:ox + w]
    m = float(np.abs(a).max()) or 1.0
    return a / m


def ribbed(w, h, *, H, scale=1.0, strength=1.0):
    """(xmap, ymap, shade RGBA) of a fluted (reeded) glass panel: vertical
    flutes, each a cylindrical lens that shows a compressed slice of a
    wider strip of the picture behind it, lit from the left (a soft sheen
    across the lit side, a soft shadow into the next flute)."""
    rib = max(6.0, RIB_PX * float(H) / REF_H * float(scale))
    x = np.arange(w, dtype=np.float32)
    u = (x % rib) / rib - 0.5                       # -0.5 .. 0.5 in a flute
    # sample x + u * rib * (spread - 1): the flute minifies a wider strip;
    # a smooth (sine-weighted) profile keeps the flute edges soft
    prof = np.sin(np.pi * u) / 2.0                  # -0.5 .. 0.5, eased
    dx = prof * rib * (RIB_SPREAD - 1.0) * float(strength)
    dx = np.clip(dx, -127, 127)
    xmap = np.repeat(_u8(dx)[None, :], h, axis=0)
    ymap = np.full((h, w), 128, np.uint8)
    v = u + 0.5                                     # 0 .. 1 across a flute
    shine = RIB_SHINE * np.exp(-((v - 0.32) / 0.2) ** 2)
    shade = RIB_SHADE * (np.clip((v - 0.62) / 0.38, 0, 1) ** 2)
    out = np.zeros((h, w, 4), np.uint8)
    white = shine >= shade
    level = np.where(white, 255, 0).astype(np.uint8)
    alpha = np.clip(np.maximum(shine, shade) * float(strength) * 255.0,
                    0, 255).astype(np.uint8)
    for c in range(3):
        out[..., c] = level[None, :]
    out[..., 3] = alpha[None, :]
    return xmap, ymap, out


def refraction(w, h, *, H, scale=1.0, strength=1.0, seed=0):
    """(xmap, ymap) of thick moulded glass over the box: a lens that pulls
    the picture toward the box's centre (it shows the plate magnified,
    the way type cut from glass does) with a smooth seeded ripple."""
    k = float(H) / REF_H
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    lens = REFRACT_LENS * float(strength)
    fx = -(xx - cx) * lens
    fy = -(yy - cy) * lens
    cell = max(8.0, REFRACT_CELL_PX * k * float(scale))
    amp = REFRACT_AMP_PX * k * float(strength) * min(2.0, float(scale))
    fx += _smooth_noise(w, h, cell, seed * 2 + 1) * amp
    fy += _smooth_noise(w, h, cell, seed * 2 + 2) * amp * 0.6
    return _u8(np.clip(fx, -127, 127)), _u8(np.clip(fy, -127, 127))


def heat_frames(w, h, *, H, fps, scale=1.0, strength=1.0, seed=0):
    """[(xmap, ymap)] — one seamless second of rising heat shimmer."""
    k = float(H) / REF_H
    lam = max(6.0, HEAT_WAVE_PX * k * float(scale))
    amp = HEAT_AMP_PX * k * float(strength) * min(2.0, float(scale))
    n = max(2, int(round(HEAT_LOOP_S * float(fps))))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    env = 0.6 + 0.4 * _smooth_noise(w, h, lam * 3.0, seed + 7)
    out = []
    for i in range(n):
        ph = 2.0 * np.pi * i / n                    # loops exactly
        dy = amp * env * np.sin(2 * np.pi * yy / lam + ph
                                + 0.7 * np.sin(2 * np.pi * xx / (lam * 2.3)))
        dx = 0.45 * amp * env * np.sin(2 * np.pi * xx / (lam * 1.7)
                                       - ph + 1.3)
        out.append((_u8(dx), _u8(dy)))
    return out


def _encode_gray(frames, w, h, fps, path):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
           "gray", "-s", f"{w}x{h}", "-r", f"{float(fps):.3f}", "-i", "-",
           "-c:v", "ffv1", "-pix_fmt", "gray", path]
    exe = os.environ.get("FFMPEG_BIN")
    if exe:
        cmd[0] = exe
    p = subprocess.run(cmd, input=b"".join(f.tobytes() for f in frames),
                       capture_output=True, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"heat map encode failed: "
                           f"{p.stderr.decode(errors='replace')[-300:]}")
    return path


def write_maps(kind, w, h, workdir, key, *, scale=1.0, strength=1.0,
               seed=0, fps=30.0, H=1080):
    """Build and write one material's maps for a w x h branch. Returns
    {"x": path, "y": path, "shade": path | None, "animated": bool}."""
    from PIL import Image
    w, h = int(w), int(h)
    base = os.path.join(workdir, f"fxmat_{key}_{kind}")
    out = {"shade": None, "animated": False}
    if kind == "ribbed_glass":
        xm, ym, shade = ribbed(w, h, H=H, scale=scale, strength=strength)
        Image.fromarray(shade, "RGBA").save(base + "_shade.png")
        out["shade"] = base + "_shade.png"
    elif kind == "refraction":
        xm, ym = refraction(w, h, H=H, scale=scale, strength=strength,
                            seed=seed)
    elif kind == "heat":
        frames = heat_frames(w, h, H=H, fps=fps, scale=scale,
                             strength=strength, seed=seed)
        out["x"] = _encode_gray([f[0] for f in frames], w, h, fps,
                                base + "_x.mkv")
        out["y"] = _encode_gray([f[1] for f in frames], w, h, fps,
                                base + "_y.mkv")
        out["animated"] = True
        return out
    else:
        raise ValueError(f"unknown material {kind!r}")
    Image.fromarray(xm, "L").save(base + "_x.png")
    Image.fromarray(ym, "L").save(base + "_y.png")
    out["x"], out["y"] = base + "_x.png", base + "_y.png"
    return out
