"""Subject sprites: the person cut out of one frame as an RGBA PNG.

Track 1B (ROADMAP Wave 1; INTERFACES_W1.md §6.3: a library function in
Wave 1, no verb). A sprite is the subject as a GRAPHIC — the cut-out a
motion graphic flies in, a 3D choreography (Wave 3) orbits, a poster lays
on a field (refs 01@1.708–3.79, 41@≈9–11.7). It is built from the same
person matte every subject effect uses (``matte.person``, measured in
SOURCE space by track 1A's sidecar) and registered as a project image, so
every later device reads it like any other asset.

  cutout(rgb, alpha)            -> (PIL RGBA image, bbox)  pure numpy/PIL
  decode_pair(video, matte, t)  -> (rgb, alpha) of one SOURCE second
  from_sidecar(source, video, t, dest_dir) -> (image, bbox) | None
  save_asset(ctx, image, name)  -> storage key (a project ``image_ref``)

Edges are DECONTAMINATED: in the soft band the colour comes from the
nearby solid subject (a normalised convolution), never from the plate the
subject stood in front of — a cut-out laid on a new background shows no
fringe of the old one. Browserless and model-free (the matte is a
sidecar); never raises out of ``from_sidecar`` (None: no matte yet).
"""

import os
import subprocess

import numpy as np

# alpha at or above SOLID is subject; below CLEAR it is plate (dropped)
SOLID = 0.92
CLEAR = 0.02
# the decontamination kernel (px at a 1080-line frame): colour is pulled
# from solid subject pixels this close
BLEED_PX = 6.0
PAD_PX = 8


def _gauss(img, sigma):
    import cv2
    k = max(3, int(round(sigma * 3)) * 2 + 1)
    return cv2.GaussianBlur(img, (k, k), sigma)


def cutout(rgb, alpha, *, pad_px=PAD_PX, decontaminate=True):
    """The subject of ``rgb`` (H, W, 3 uint8) under ``alpha`` (H, W, 0..1
    float or 0..255 uint8) as a cropped RGBA PIL image and its (x, y, w, h)
    box in the frame, or (None, None) when the matte holds no subject."""
    from PIL import Image
    a = np.asarray(alpha, np.float32)
    if a.max() > 1.5:
        a = a / 255.0
    a = np.clip(a, 0.0, 1.0)
    a[a < CLEAR] = 0.0
    if not (a > 0).any():
        return None, None
    rgb = np.asarray(rgb, np.float32)
    if decontaminate:
        h = rgb.shape[0]
        sigma = max(1.0, BLEED_PX * h / 1080.0)
        solid = (a >= SOLID).astype(np.float32)
        num = _gauss(rgb * solid[..., None], sigma)
        den = _gauss(solid, sigma)[..., None]
        clean = np.where(den > 1e-3, num / np.maximum(den, 1e-3), rgb)
        # solid pixels keep their own colour; soft ones take the subject's
        w = np.clip((a - 0.5) / (SOLID - 0.5), 0.0, 1.0)[..., None]
        soft = (a > 0) & (a < SOLID)
        rgb = np.where(soft[..., None], w * rgb + (1 - w) * clean, rgb)
    ys, xs = np.nonzero(a > 0)
    H, W = a.shape
    x0 = max(0, int(xs.min()) - pad_px)
    y0 = max(0, int(ys.min()) - pad_px)
    x1 = min(W, int(xs.max()) + 1 + pad_px)
    y1 = min(H, int(ys.max()) + 1 + pad_px)
    out = np.dstack([np.clip(rgb[y0:y1, x0:x1] + 0.5, 0, 255),
                     np.clip(a[y0:y1, x0:x1] * 255.0 + 0.5, 0, 255)])
    return Image.fromarray(out.astype(np.uint8), "RGBA"), \
        (x0, y0, x1 - x0, y1 - y0)


def _frame(path, t, size=None, gray=False):
    vf = [f"scale={size[0]}:{size[1]}:flags=bicubic"] if size else []
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.4f}",
           "-i", path, "-frames:v", "1"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-f", "image2pipe", "-vcodec", "png", "-"]
    from io import BytesIO

    from PIL import Image
    raw = subprocess.run(cmd, capture_output=True, check=True,
                         timeout=60).stdout
    im = Image.open(BytesIO(raw))
    return np.asarray(im.convert("L" if gray else "RGB"))


def decode_pair(video, matte, t, matte_t=None):
    """(rgb, alpha) of ``video`` at second ``t`` and the matte clip at
    ``matte_t`` (default ``t``), the matte scaled to the picture."""
    rgb = _frame(video, t)
    h, w = rgb.shape[:2]
    alpha = _frame(matte, t if matte_t is None else matte_t, size=(w, h),
                   gray=True)
    return rgb, alpha


def from_sidecar(source, video, src_t, dest_dir):
    """The subject at SOURCE second ``src_t`` from a READY ``matte.person``
    sidecar chunk (never launches or waits for analysis). (image, bbox) or
    None when no measured chunk covers that second."""
    try:
        import sidecars
        h = sidecars.ensure("matte.person", source, [(src_t, src_t + 0.05)],
                            priority="backfill")
        for ch in sidecars.fetch(h, dest_dir):
            if ch["t0"] - 1e-6 <= src_t < ch["t1"]:
                rgb, alpha = decode_pair(video, ch["path"], src_t,
                                         matte_t=src_t - ch["t0"])
                img, box = cutout(rgb, alpha)
                return (img, box) if img is not None else None
    except Exception as e:  # noqa: BLE001 — a sprite never fails a turn
        print(f"[sprites] no sprite at {src_t:g}s: {str(e)[:160]}",
              flush=True)
    return None


def save_asset(ctx, image, name="subject sprite"):
    """Upload ``image`` (RGBA) and register it as a project image; returns
    its storage key."""
    import db as dbx
    import storage
    import url_media
    path = os.path.join(ctx.workdir, f"sprite_{os.getpid()}_{id(image)}.png")
    image.save(path)
    key = url_media.storage_key(ctx.project_id, url_media.KIND_IMAGE, path)
    storage.upload_file(path, key, "image/png")
    ctx.db.run(dbx.insert_asset, ctx.project_id, url_media.KIND_IMAGE, key,
               bytes_=os.path.getsize(path), width=image.width,
               height=image.height,
               meta={"filename": f"{name}.png", "sprite": True,
                     "alpha": True})
    return key
