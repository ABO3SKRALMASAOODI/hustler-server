"""Video slots: real footage composited into the rects a page declares
(Wave 3, 3E; devices/INTERFACES_W3.md §6).

A layout page (mosaic, scroll wall, stack, device stage, flat-lay, glass
card) calls ``MG.slot(id, x, y, w, h, {r, z, opacity, fit})`` every frame;
the motion engine records the rects (``<clip>.slots.json``). Here, before
the filter graph is built, each slot is filled with its source — a project
image or video, or the main footage from a source second — scaled to
cover (or contain) its rect, rounded by ``r`` and laid UNDER the page's own
pixels (frames, labels and shadows sit on top). One mechanism serves
mosaics, device screens, UI cards and grids (ROADMAP 3E).

Sources come from the item's params: ``tiles`` or ``panels`` (a list),
``screen`` or ``image`` (one), as strings:

    asset:<storage key>[+loop]   a project image or video
    source:<seconds>             the main footage from that source second
    <storage key>                shorthand for asset:<key>

Slot ids ``t0, t1, ...`` take ``tiles[k]`` (or ``panels[k]``); ``screen``
and ``image`` take theirs; ``<id>#<tag>`` draws a crop of source ``<id>``.
A slot whose source cannot be read stays the page's own placeholder.
Pixels are decoded once per source at the slot's largest size; still
images are read once.
"""

import json
import os
import subprocess
import tempfile

import numpy as np

MAX_VIDEO_SOURCES = 64


def wants(item, clip):
    return bool(getattr(clip, "path", None)) and os.path.exists(
        clip.path + ".slots.json")


def _sources(item):
    p = item.get("params") or {}
    out = {}
    for k, v in enumerate(p.get("tiles") or p.get("panels") or []):
        if v:
            out[f"t{k}"] = str(v)
    for key in ("screen", "image"):
        if p.get(key):
            out[key] = str(p[key])
    return out


def _parse(spec):
    s = str(spec).strip()
    loop = s.endswith("+loop")
    if loop:
        s = s[:-5]
    if s.startswith("source:"):
        return {"kind": "source", "t": float(s.split(":", 1)[1])}
    if s.startswith("asset:"):
        s = s.split(":", 1)[1]
    return {"kind": "asset", "key": s, "loop": loop}


def _is_image(path):
    return os.path.splitext(path)[1].lower() in (".png", ".jpg", ".jpeg",
                                                 ".webp", ".bmp")


class _Source:
    """Frames of one source at (w, h), cover-cropped (or contained). A
    still is read once; a video STREAMS (one ffmpeg pipe, read in step with
    the compositor), so 64 video tiles cost 64 small decoders, never their
    frames held in memory."""

    def __init__(self, path, w, h, fps, n, start=0.0, loop=False,
                 fit="cover"):
        self.path, self.w, self.h = path, int(w), int(h)
        self.still = None
        self.proc = None
        self.last = None
        self.pos = -1
        if fit == "cover":
            vf = (f"scale={self.w}:{self.h}:force_original_aspect_ratio="
                  f"increase,crop={self.w}:{self.h}")
        else:
            vf = (f"scale={self.w}:{self.h}:force_original_aspect_ratio="
                  f"decrease,pad={self.w}:{self.h}:(ow-iw)/2:(oh-ih)/2:"
                  "color=black@0")
        if _is_image(path):
            out = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vf",
                 vf + ",format=rgba", "-frames:v", "1", "-f", "rawvideo",
                 "-pix_fmt", "rgba", "pipe:1"],
                capture_output=True, check=False).stdout
            if len(out) >= self.w * self.h * 4:
                self.still = np.frombuffer(out[:self.w * self.h * 4],
                                           np.uint8).reshape(self.h, self.w, 4)
            return
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-threads", "2"]
        if loop:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-ss", f"{max(0.0, start):.3f}", "-i", path, "-an", "-vf",
                f"fps={fps:.6f},{vf}", "-frames:v", str(int(n)), "-f",
                "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)

    @property
    def readable(self):
        return self.still is not None or self.proc is not None

    def at(self, k):
        if self.still is not None:
            return self.still
        size = self.w * self.h * 4
        while self.proc is not None and self.pos < k:
            buf = self.proc.stdout.read(size)
            if len(buf) < size:
                self.close()
                break
            self.last = np.frombuffer(buf, np.uint8).reshape(self.h,
                                                             self.w, 4)
            self.pos += 1
        return self.last

    def close(self):
        if self.proc is not None:
            try:
                self.proc.stdout.close()
                self.proc.kill()
                self.proc.wait()
            except Exception:  # noqa: BLE001
                pass
            self.proc = None


def _round_mask(w, h, r):
    import cv2
    m = np.zeros((h, w), np.uint8)
    r = int(max(0, min(r, min(w, h) / 2)))
    if r <= 0:
        m[:] = 255
        return m
    cv2.rectangle(m, (r, 0), (w - 1 - r, h - 1), 255, -1)
    cv2.rectangle(m, (0, r), (w - 1, h - 1 - r), 255, -1)
    for cx, cy in ((r, r), (w - 1 - r, r), (r, h - 1 - r),
                   (w - 1 - r, h - 1 - r)):
        cv2.circle(m, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
    return m


def fill(item, clip, ctx, warn=None):
    """The clip with its slots filled (a new RenderedClip), or None."""
    import cv2
    import motion_engine
    import world_layer
    with open(clip.path + ".slots.json") as f:
        data = json.load(f)
    rows = data.get("frames") or []
    if not rows or not any(rows):
        return None
    scale = float(data.get("scale") or 1.0)
    fps = float(getattr(ctx, "fps", 30.0) or 30.0)
    n = int(clip.frames)
    specs = _sources(item)
    # each slot's largest size (output px) over the clip
    maxsz, fits = {}, {}
    for fr in rows:
        for row in fr:
            sid, x, y, w, h, r, z, op, fit = row[:9]
            crop = row[9] if len(row) > 9 else None
            base_id = sid.split("#", 1)[0]
            if crop:
                # a crop of a source is decoded whole, at the size that
                # gives the crop its rect's pixels
                kx = max(1e-3, crop[2] - crop[0])
                ky = max(1e-3, crop[3] - crop[1])
                w, h = w / kx, h / ky
            mw, mh = maxsz.get(base_id, (0, 0))
            maxsz[base_id] = (max(mw, w * scale), max(mh, h * scale))
            fits.setdefault(base_id, fit)
    fetch = getattr(ctx, "fetch_asset", None)
    srcs, bad = {}, []
    for sid, (mw, mh) in maxsz.items():
        spec = specs.get(sid)
        if not spec:
            continue
        if len([q for q in srcs.values() if q.still is None]) >= \
                MAX_VIDEO_SOURCES:
            bad.append(sid)
            continue
        try:
            src = _parse(spec)
            w2, h2 = max(2, int(round(mw))), max(2, int(round(mh)))
            if src["kind"] == "source":
                path = getattr(ctx, "src_path", None)
                start = src["t"] - float(getattr(ctx, "clock_offset", 0.0))
                loop = False
            else:
                path = fetch(src["key"]) if fetch else None
                start, loop = 0.0, bool(src.get("loop"))
            if not path or not os.path.exists(path):
                bad.append(sid)
                continue
            srcs[sid] = _Source(path, w2, h2, fps, n, start, loop,
                                fits.get(sid) or "cover")
        except Exception:  # noqa: BLE001 — that slot keeps its placeholder
            bad.append(sid)
    if bad and warn:
        warn(f"motion '{item.get('id')}': slot(s) {', '.join(bad[:6])} "
             "could not be filled (their source is unreadable); the page's "
             "placeholder shows there")
    if not srcs:
        return None
    W, H = int(clip.w), int(clip.h)
    out_dir = os.path.join(getattr(ctx, "workdir", None)
                           or tempfile.gettempdir(), "world")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"slots_{item.get('id')}_"
                       f"{os.path.basename(clip.path)}")
    enc = world_layer._Encoder(out, W, H, fps)
    masks = {}
    bd = (item.get("params") or {}).get("backdrop")
    base = np.zeros((H, W, 4), np.float32)
    bd2 = (item.get("params") or {}).get("backdrop2")

    def _rgb(h):
        v = int(str(h)[1:], 16)
        return np.array([v >> 16 & 255, v >> 8 & 255, v & 255], np.float32)
    if bd and str(bd).startswith("#") and len(str(bd)) == 7:
        top = _rgb(bd)
        bot = _rgb(bd2) if bd2 and str(bd2).startswith("#") and \
            len(str(bd2)) == 7 else top
        ramp = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None, None]
        base[..., :3] = top * (1 - ramp) + bot * ramp
        base[..., 3] = 255.0
    try:
        k = 0
        for page in world_layer._decode_rgba(clip.path, W, H):
            if k >= n:
                break
            canvas = base.copy()
            fr = rows[min(k, len(rows) - 1)] if rows else []
            for row in sorted(fr, key=lambda q: q[6]):
                sid, x, y, w, h, r, z, op, fit = row[:9]
                crop = row[9] if len(row) > 9 else None
                s = srcs.get(sid.split("#", 1)[0])
                img = s.at(k) if s else None
                if img is None:
                    continue
                if crop:
                    ih, iw = img.shape[:2]
                    cx0, cy0 = int(crop[0] * iw), int(crop[1] * ih)
                    cx1, cy1 = max(cx0 + 2, int(crop[2] * iw)), \
                        max(cy0 + 2, int(crop[3] * ih))
                    img = img[cy0:cy1, cx0:cx1]
                X, Y = x * scale - clip.x, y * scale - clip.y
                w2, h2 = int(round(w * scale)), int(round(h * scale))
                if w2 < 2 or h2 < 2:
                    continue
                tile = img if (img.shape[1], img.shape[0]) == (w2, h2) else \
                    cv2.resize(np.ascontiguousarray(img), (w2, h2),
                               interpolation=cv2.INTER_AREA)
                key = (w2, h2, int(round(r * scale)))
                m = masks.get(key)
                if m is None:
                    m = masks[key] = _round_mask(w2, h2, key[2])
                x0, y0 = int(round(X)), int(round(Y))
                xa, ya = max(0, x0), max(0, y0)
                xb, yb = min(W, x0 + w2), min(H, y0 + h2)
                if xb <= xa or yb <= ya:
                    continue
                t = tile[ya - y0:yb - y0, xa - x0:xb - x0].astype(np.float32)
                a = (m[ya - y0:yb - y0, xa - x0:xb - x0].astype(np.float32)
                     / 255.0) * (t[..., 3] / 255.0) * float(op)
                dst = canvas[ya:yb, xa:xb]
                dst[..., :3] = t[..., :3] * a[..., None] + dst[..., :3] * (
                    1 - a[..., None])
                dst[..., 3] = a * 255 + dst[..., 3] * (1 - a)
            pa = page[..., 3:4].astype(np.float32) / 255.0
            rgb = page[..., :3].astype(np.float32) * pa + canvas[..., :3] * (
                1 - pa)
            alpha = page[..., 3].astype(np.float32) + canvas[..., 3] * (
                1 - pa[..., 0])
            frame = np.zeros((H, W, 4), np.uint8)
            frame[..., :3] = np.clip(rgb, 0, 255)
            frame[..., 3] = np.clip(alpha, 0, 255)
            enc.write(frame)
            k += 1
    finally:
        enc.close()
        for q in srcs.values():
            q.close()
    return motion_engine.RenderedClip(path=out, x=clip.x, y=clip.y, w=W,
                                      h=H, frames=n, captured=n,
                                      cached=False, seconds=0.0)


def spill(item, ctx, fps=10.0):
    """The screen source's mean colour per 1/fps over the item (for a
    device stage's light spill): [[r, g, b], ...] 0..255, or None."""
    p = item.get("params") or {}
    spec = p.get("screen")
    if not spec:
        return None
    src = _parse(spec)
    if src["kind"] == "source":
        path = getattr(ctx, "src_path", None)
        start = src["t"] - float(getattr(ctx, "clock_offset", 0.0))
    else:
        fetch = getattr(ctx, "fetch_asset", None)
        path = fetch(src["key"]) if fetch else None
        start = 0.0
    if not path or not os.path.exists(path):
        return None
    dur = float(item["end"]) - float(item["start"])
    n = max(1, int(round(dur * fps)) + 1)
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0, start):.3f}",
         "-i", path, "-an", "-vf", f"fps={fps},scale=8:8:flags=area",
         "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24",
         "pipe:1"], capture_output=True, check=False).stdout
    rows = []
    for i in range(len(out) // 192):
        a = np.frombuffer(out[i * 192:(i + 1) * 192], np.uint8).reshape(64, 3)
        rows.append([int(v) for v in a.mean(axis=0)])
    return rows or None
