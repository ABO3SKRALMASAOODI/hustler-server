"""T2: the type engine's raster half. One layout, two rasters, packed in one atlas.

R  exact area coverage at twice the target size (more when the word scales up), minified on the GPU:
   tiny captions stay true where a distance field loses them
G  the shadow copy, B the glow copy: the same coverage, blurred at compile
A  a signed distance to the outline, 0.5 on the edge, for effects: stroke, weight, masks

Sprites sit on an 8-texel grid in the atlas, so a word at rest still lands on exactly one mip.
Everything here is numpy and OpenCV: the compile needs no GL.
"""
import math

import cv2
import numpy as np

from . import type as T

GRID = 8                # atlas placement grid, texels: the largest supersample, so mips stay registered
GUTTER = 16
PAGE = 4096
SPREAD = 4.0            # distance field reach, output pixels each side of the outline


def coverage(lines, w, h):
    """Exact area coverage of closed outlines, as a signed accumulation buffer. lines: Nx4 float,
    pixels, y down. Each edge is split at every row and column it crosses, so every piece lies in
    one cell and adds its true area. Overlapping contours of a variable font stay solid."""
    A_ = np.zeros((h + 2, w + 3), np.float64)
    x0, y0, x1, y1 = lines.T
    keep = y0 != y1
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    sign = np.where(y1 > y0, 1.0, -1.0)
    flip = y1 < y0
    x0, x1 = np.where(flip, x1, x0), np.where(flip, x0, x1)
    y0, y1 = np.where(flip, y1, y0), np.where(flip, y0, y1)
    r0 = np.floor(y0).astype(np.int64)
    n = np.ceil(y1).astype(np.int64) - r0
    idx = np.repeat(np.arange(len(x0)), n)
    row = r0[idx] + (np.arange(len(idx)) - np.repeat(np.cumsum(n) - n, n))
    dxdy = (x1 - x0) / (y1 - y0)
    ya, yb = np.maximum(row, y0[idx]), np.minimum(row + 1, y1[idx])
    xa, xb = x0[idx] + (ya - y0[idx]) * dxdy[idx], x0[idx] + (yb - y0[idx]) * dxdy[idx]
    dy = (yb - ya) * sign[idx]
    xl, xr = np.minimum(xa, xb), np.maximum(xa, xb)
    c0 = np.floor(xl).astype(np.int64)
    m = np.maximum(np.ceil(xr).astype(np.int64) - c0, 1)
    j = np.repeat(np.arange(len(xl)), m)
    col = c0[j] + (np.arange(len(j)) - np.repeat(np.cumsum(m) - m, m))
    left, right = np.maximum(col, xl[j]), np.minimum(col + 1, xr[j])
    span = (xr - xl)[j]
    frac = np.where(span > 1e-12, (right - left) / np.maximum(span, 1e-12), 1.0)
    d = dy[j] * frac
    xm = np.clip(0.5 * (left + right) - col, 0, 1)
    rr = row[j]
    np.add.at(A_, (rr, col), d * (1 - xm))
    np.add.at(A_, (rr, col + 1), d * xm)
    return np.clip(np.abs(np.cumsum(A_, axis=1)), 0, 1)[:h, :w].astype(np.float32)


def sdf(cov, spread):
    """Signed distance to the outline in texels, from coverage: positive inside. Sub-texel near the
    edge, where coverage itself is the distance; the transform carries it outwards. Returned as
    0..1 with 0.5 on the edge and ``spread`` texels to either end."""
    inside = cov >= 0.5
    if not inside.any():
        return np.zeros_like(cov)
    d_out = cv2.distanceTransform((~inside).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    d_in = cv2.distanceTransform(inside.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    d = np.where(inside, d_in - 0.5, 0.5 - d_out)
    edge = (d_in <= 1.0) & (d_out <= 1.0)
    d = np.where(edge, cov - 0.5, d)
    return np.clip(0.5 + d / (2.0 * spread), 0.0, 1.0).astype(np.float32)


def soft(cov, sigma):
    """A gaussian of any radius at a steady cost: a wide one is taken on a smaller copy, where it is
    still many texels wide, and eased back up. The result is smooth, so nothing is lost."""
    if sigma <= 0:
        return np.zeros_like(cov)
    if sigma <= 12.0:
        return cv2.GaussianBlur(cov, (0, 0), sigma)
    d = 2 ** int(math.floor(math.log2(sigma / 6.0)))
    h, w = cov.shape
    small = cv2.resize(cov, (max(w // d, 1), max(h // d, 1)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / d)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def supersample(s_max):
    """A power of two, so a word at rest sits on one mip."""
    return 2 if s_max <= 1.0 else 4 if s_max <= 2.0 else 8


def sprite_key(text, vkey, ss, blur_px):
    return f"{vkey}|{ss}|{int(round(blur_px))}|{text}"


def outline_sprite(text, voice, W, ss, blur_px, note=None, extra_pad=0):
    """Layout: shape one word and collect its outline. Cheap, and the only half that touches
    HarfBuzz, so it runs on one thread. ``note`` receives what was replaced: a missing font or
    glyph never stops a film and never draws a box."""
    note = note or (lambda m: None)
    v = dict(voice or {"font": T.FALLBACK[0], "px": 58})
    try:
        face = T.Face.of(v)
    except FileNotFoundError:
        fb = _first_fallback()
        note(f"font {v.get('font') or v.get('family')} is not installed: drew it in {fb}")
        v = {**v, "font": fb, "family": None, "var": None}
        face = T.Face.get(fb)
    px = float(v.get("px", 58))
    if face.missing(text):
        for name in T.FALLBACK:
            try:
                alt = T.Face.get(name)
            except FileNotFoundError:
                continue
            if not alt.missing(text):
                note(f"{v.get('font') or v.get('family')} lacks a glyph in {text!r}: drew it in {name}")
                face = alt
                break
        else:
            note(f"no font here covers {text!r}")
    lines, adv = face.outline(text, px * ss, float(v.get("track", 0)))
    sh, gl = v.get("shadow") or {}, v.get("glow") or {}
    return dict(lines=lines, adv=adv, px=px, ss=ss, sig_s=float(sh.get("blur", 0)) * W, sig_g=float(gl.get("blur", 0)) * W,
                blur_px=blur_px, extra_pad=int(extra_pad), dx_slot=T.compiler_advance(text, v))


def raster_sprite(o):
    """Raster: an outline to the RGBA sprite and its metrics, in output pixels unless said. numpy
    and OpenCV only, so sprites are drawn side by side on threads."""
    lines, adv, px, ss = o["lines"], o["adv"], o["px"], o["ss"]
    pad = int(math.ceil(max(o["sig_s"], o["sig_g"], o["blur_px"]) * 3.2)) + 3 + o["extra_pad"]
    if len(lines):
        bx0, by0 = lines[:, [0, 2]].min() / ss, lines[:, [1, 3]].min() / ss
        bx1, by1 = lines[:, [0, 2]].max() / ss, lines[:, [1, 3]].max() / ss
    else:
        bx0, by0, bx1, by1 = 0.0, -px, adv / ss, 0.0
    x0, y0 = int(math.floor(bx0)) - pad, int(math.floor(by0)) - pad
    x1, y1 = int(math.ceil(bx1)) + pad, int(math.ceil(by1)) + pad
    w, h = (x1 - x0) * ss, (y1 - y0) * ss
    if len(lines):
        ln = lines.copy()
        ln[:, [0, 2]] -= x0 * ss
        ln[:, [1, 3]] -= y0 * ss
        cov = coverage(ln, w, h)
    else:
        cov = np.zeros((h, w), np.float32)
    arr = np.empty((h, w, 4), np.uint8)
    for i, ch in enumerate((cov, soft(cov, o["sig_s"] * ss), soft(cov, o["sig_g"] * ss), sdf(cov, SPREAD * ss))):
        arr[..., i] = (ch * 255 + 0.5).astype(np.uint8)
    meta = {"w": x1 - x0, "h": y1 - y0, "x0": x0, "y0": y0, "ss": ss,
            "cx": (bx0 + bx1) / 2, "cy": (by0 + by1) / 2, "adv": adv / ss,
            "dx": (o["dx_slot"] - adv / ss) / 2, "px": px}
    return arr, meta


def build_sprite(text, voice, W, ss, blur_px, note=None, extra_pad=0):
    """One word as an RGBA array plus its metrics."""
    return raster_sprite(outline_sprite(text, voice, W, ss, blur_px, note, extra_pad))


def _first_fallback():
    for name in T.FALLBACK:
        try:
            T.find_font(name)
            return name
        except FileNotFoundError:
            continue
    raise FileNotFoundError("no fallback font on this machine")


def build_mask(text, voice, note=None, px=384.0, spread=48.0):
    """A glyph as a distance field alone, large, 16 bits: the mask a junction grows from (a period
    that swallows the frame). Returns (uint16 array, meta in mask texels)."""
    v = dict(voice or {"font": T.FALLBACK[0]})
    try:
        face = T.Face.of(v)
    except FileNotFoundError:
        fb = _first_fallback()
        (note or (lambda m: None))(f"font {v.get('font') or v.get('family')} is not installed: mask drawn in {fb}")
        face = T.Face.get(fb)
    lines, adv = face.outline(text, px, 0.0)
    pad = int(math.ceil(spread)) + 2
    if len(lines):
        bx0, by0, bx1, by1 = lines[:, [0, 2]].min(), lines[:, [1, 3]].min(), lines[:, [0, 2]].max(), lines[:, [1, 3]].max()
    else:
        bx0, by0, bx1, by1 = 0.0, -px, adv, 0.0
    x0, y0 = int(math.floor(bx0)) - pad, int(math.floor(by0)) - pad
    x1, y1 = int(math.ceil(bx1)) + pad, int(math.ceil(by1)) + pad
    w, h = x1 - x0, y1 - y0
    ln = lines.copy()
    if len(ln):
        ln[:, [0, 2]] -= x0
        ln[:, [1, 3]] -= y0
        cov = coverage(ln, w, h)
    else:
        cov = np.zeros((h, w), np.float32)
    d = sdf(cov, spread)
    meta = {"w": w, "h": h, "x0": x0, "y0": y0, "cx": (bx0 + bx1) / 2, "cy": (by0 + by1) / 2, "px": px, "spread": spread, "adv": adv}
    return (d * 65535 + 0.5).astype(np.uint16), meta


def pack(sprites):
    """Shelf-pack {key: (array, meta)} into pages. Tall first, on the 8-texel grid, with a gutter.
    Returns (pages: [HxWx4 uint8], {key: meta with page, ax, ay, aw, ah in texels})."""
    order = sorted(sprites, key=lambda k: (-sprites[k][0].shape[0], -sprites[k][0].shape[1], k))
    pages, out = [], {}
    cur = None

    def new_page():
        return {"shelves": [], "y": 0, "items": []}

    def up(v):
        return (v + GRID - 1) // GRID * GRID

    for k in order:
        arr, meta = sprites[k]
        h, w = arr.shape[:2]
        if w > PAGE or h > PAGE:
            raise ValueError(f"sprite {k!r} is {w}x{h}: larger than an atlas page")
        placed = False
        while not placed:
            if cur is None:
                cur = new_page()
                pages.append(cur)
            for sh in cur["shelves"]:
                if sh["h"] >= h and sh["x"] + w <= PAGE:
                    x, y = sh["x"], sh["y"]
                    sh["x"] = up(x + w + GUTTER)
                    placed = True
                    break
            if not placed:
                y = cur["y"]
                if y + h <= PAGE:
                    cur["shelves"].append({"y": y, "h": h, "x": up(w + GUTTER)})
                    cur["y"] = up(y + h + GUTTER)
                    x = 0
                    placed = True
                else:
                    cur = None
        cur["items"].append((k, x, y))
        out[k] = dict(meta, page=len(pages) - 1, ax=x, ay=y, aw=w, ah=h)
    arrays = []
    for pg in pages:
        wmax = max((x + sprites[k][0].shape[1] for k, x, y in pg["items"]), default=GRID)
        hmax = max((y + sprites[k][0].shape[0] for k, x, y in pg["items"]), default=GRID)
        a = np.zeros((up(hmax), up(wmax), 4), np.uint8)
        for k, x, y in pg["items"]:
            s = sprites[k][0]
            a[y:y + s.shape[0], x:x + s.shape[1]] = s
        arrays.append(a)
    return arrays, out
