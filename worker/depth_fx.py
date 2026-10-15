"""Depth and 2.5D devices: the ``depth`` fx compiler (track 4A;
worker/devices/INTERFACES_W4.md §3.2).

Every kind is precomputed in ``prepare`` — pixels and maps in numpy/OpenCV,
written once as an intermediate clip of exactly the window's frames — and
composited at its stage by ffmpeg (``finish.window_composite``): the
program outside the window is bit-exact, a seeded kind is deterministic,
and nothing here runs a model (depth comes from the ``depth.small``
sidecar; the person matte from ``matte.person``).

  parallax      a still (an asset, a main-video frame, or the program's
                frame at ``start``) split into depth layers (feathered at
                depth quantiles), the background behind every nearer layer
                inpainted once, then each layer moved by its own depth along
                ``move``: a 2.5D camera move. Rungs: depth -> two layers from
                the person matte (reduced) -> ken_burns (substitute)
  ken_burns     the classic 2D move over the still (cover-fit, eased)
  dof           depth of field: varblur with a radius map |depth - focus|
  rack_focus    the focus depth travels from ``focus_from`` to ``focus``
                over ``rack_f`` frames that END on ``rack_at`` (a word)
  fog           the far planes dissolve into ``colour`` by depth
  light         a band of light travels through the scene near -> far
  point_cloud   a LiDAR-style cloud of the shot (or of a still): points from
                depth (x the person matte when focus is on the subject),
                a slow orbit, a palette by depth, a scanline sweep, trails;
                on black (default), over the dimmed program, or screened
                over it
  push_through  OPT-IN camera move (card adds_zoom): the picture pushes in
                while ``text`` set in Z rushes past the lens and defocuses

Program-space depth: each program frame's SOURCE second (the timeline),
the depth keyframes interpolated there (depth_track.DepthClip.at), laid
into the program through the picture's own geometry (card_geom.windows:
crop, follow, picture cards) and smoothed in time. ``world`` stage only for
the kinds that read the live shot (depth sits under the picture before the
camera); stills may run at ``screen``.
"""

import hashlib
import math
import os
import subprocess

import numpy as np

import faultinject
import fxprims

KINDS = ("parallax", "ken_burns", "dof", "rack_focus", "fog", "light",
         "point_cloud", "push_through")
LIVE_KINDS = ("dof", "rack_focus", "fog", "light")
FILTERS = ("split", "trim", "setpts", "settb", "format", "scale", "overlay",
           "alphamerge", "lut", "varblur", "maskedmerge", "nullsink", "null",
           "mergeplanes", "blend", "color", "crop", "fps")
KEEPS_FRAME_TAGS = True

# Parallax layering: depth slices, edge feather (share of the frame's short
# side) and the inpaint working size (long side, px).
LAYERS = 5
FEATHER = 0.006
INPAINT_SIDE = 640
# Maximum blur radius at blur=1 (share of the frame height).
MAX_BLUR = 0.018
# Point cloud: points at density 1, internal resolution of the depth grid.
CLOUD_POINTS = 200_000
CLOUD_GRID = 540
# Palettes by depth (near -> far), RGB 0..1.
PALETTES = {
    "red": ((1.0, 0.42, 0.3), (0.95, 0.12, 0.07), (0.38, 0.02, 0.03)),
    "cyan": ((0.85, 1.0, 1.0), (0.1, 0.85, 1.0), (0.02, 0.18, 0.4)),
    "white": ((1.0, 1.0, 1.0), (0.75, 0.78, 0.82), (0.22, 0.24, 0.3)),
    "amber": ((1.0, 0.95, 0.75), (1.0, 0.62, 0.1), (0.4, 0.13, 0.02)),
}
# CPU ms per output frame per megapixel (numpy precompute + composite),
# measured locally on 1080x1920 (report estimate only).
MS_PER_MPX = {"parallax": 9.0, "ken_burns": 4.0, "dof": 14.0,
              "rack_focus": 14.0, "fog": 6.0, "light": 7.0,
              "point_cloud": 22.0, "push_through": 12.0}
_HEX = {"white": (255, 255, 255), "black": (0, 0, 0)}


# ── small helpers ────────────────────────────────────────────────────────

def ease(x, kind="in_out"):
    x = min(1.0, max(0.0, float(x)))
    if kind == "linear":
        return x
    if kind == "out":
        return 1.0 - (1.0 - x) ** 3
    return x * x * (3.0 - 2.0 * x)


def colour_rgb(c, accent=None, default=(255, 255, 255)):
    c = (c or "").strip().lower()
    if c in _HEX:
        return _HEX[c]
    if c == "accent":
        c = (accent or "").lower()
    if len(c) == 7 and c.startswith("#"):
        try:
            return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass
    return default


def _state(plan):
    return plan.__dict__.setdefault("_w4_depth", {})


def _env(plan):
    import subject_fx
    return subject_fx.render_env(plan)


def _edl(plan):
    import subject_fx
    return subject_fx.plan_edl(plan)


def _accent(plan):
    cap = (_edl(plan) or {}).get("captions")
    style = (cap.get("style") or {}) if isinstance(cap, dict) else {}
    return str(style.get("highlight_color") or "") or None


def _key(item, plan, extra=""):
    blob = repr((sorted((k, repr(v)) for k, v in item.items()), plan.W,
                 plan.H, round(plan.fps, 3), plan.preview, extra))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class ClipWriter:
    """Raw frames in, an intermediate clip out (ffv1: lossless, exact;
    gray maps, RGB pictures and BGRA planes)."""

    def __init__(self, path, W, H, fps, pix="gray"):
        self.path, self.W, self.H = path, int(W), int(H)
        self.pix = pix
        self.n = 0
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
             pix, "-s", f"{self.W}x{self.H}", "-r", f"{float(fps):.6f}",
             "-i", "-", "-c:v", "ffv1", "-level", "3", "-threads", "2",
             path], stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, frame):
        a = np.ascontiguousarray(frame)
        self.proc.stdin.write(a.tobytes())
        self.n += 1

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        err = self.proc.stderr.read().decode("utf-8", "replace")
        rc = self.proc.wait()
        if rc != 0:
            raise RuntimeError(f"intermediate clip failed: {err[-200:]}")
        return self.path


# ── stills: where a parallax / ken_burns picture comes from ─────────────

def _decode_frame(path, t, W=None, H=None):
    """One RGB frame of ``path`` at ``t`` seconds (an image: its only
    frame), optionally resized; None on failure."""
    import qa_metrics
    info = qa_metrics.probe(path)
    w, h = int(info.get("width") or 0), int(info.get("height") or 0)
    if not w or not h:
        return None
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if t and float(t) > 0:
        cmd += ["-ss", f"{float(t):.3f}"]
    cmd += ["-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
            "rgb24", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=60).stdout
    except Exception:  # noqa: BLE001
        return None
    if len(raw) < w * h * 3:
        return None
    img = np.frombuffer(raw[:w * h * 3], np.uint8).reshape(h, w, 3)
    if W and H:
        import cv2
        img = cv2.resize(img, (int(W), int(H)), interpolation=cv2.INTER_AREA)
    return img


def cover_crop(img, W, H, centre=(0.5, 0.5), margin=1.0):
    """``img`` cover-fit to W x H (``margin`` > 1 leaves room for a move)."""
    import cv2
    h, w = img.shape[:2]
    k = max(W / float(w), H / float(h)) * float(margin)
    nw, nh = max(W, int(math.ceil(w * k))), max(H, int(math.ceil(h * k)))
    big = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA
                     if k < 1 else cv2.INTER_CUBIC)
    cx = int(round(centre[0] * nw - W / 2.0))
    cy = int(round(centre[1] * nh - H / 2.0))
    cx = min(max(0, cx), nw - W)
    cy = min(max(0, cy), nh - H)
    return big[cy:cy + H, cx:cx + W]


def _main_window(plan, src_t, t):
    """The source rect (fractions) the program shows at program second
    ``t`` (SOURCE second ``src_t``), expanded to the frame's aspect, or
    None."""
    import card_geom
    src = plan.source
    sw = getattr(src, "width", None)
    sh = getattr(src, "height", None)
    if not sw or not sh:
        return None
    wins = card_geom.windows(_edl(plan), src_t, plan.W, plan.H, sw, sh,
                             None, t)
    if not wins:
        return None
    box, rect = max(wins, key=lambda w: (w[0][2] - w[0][0])
                    * (w[0][3] - w[0][1]))
    return [float(v) for v in rect]


def still_for(plan, item):
    """(rgb W x H cover-fit, depth source, info) for a still-based kind:
    ``depth source`` is ("asset", path, sha) or ("main", src_t)."""
    import subject_fx
    W, H = int(plan.W), int(plan.H)
    src = item.get("source") or {}
    kind = src.get("kind") if src else None
    if kind == "asset":
        path = subject_fx.asset_path(plan, src["asset_key"])
        img = _decode_frame(path, float(src.get("offset_s") or 0.0)) \
            if path else None
        if img is None:
            return None, None, "the still could not be read"
        return img, ("asset", path), None
    env = _env(plan)
    path = env.get("src_path")
    if not path:
        return None, None, "this render names no main video"
    if kind == "source":
        src_t = float(src["src_t"])
        t = float(item["start"])
    else:
        t = float(item["start"])
        src_t = plan.tl.out_to_src(t) if plan.tl is not None else None
        if src_t is None:
            return None, None, "the program shows no main footage at start"
    img = _decode_frame(path, src_t)
    if img is None:
        return None, None, "the main video frame could not be read"
    rect = _main_window(plan, src_t, t)
    if rect:
        h, w = img.shape[:2]
        x0, y0 = int(rect[0] * w), int(rect[1] * h)
        x1, y1 = max(x0 + 2, int(rect[2] * w)), max(y0 + 2, int(rect[3] * h))
        img = img[y0:y1, x0:x1]
    return img, ("main", src_t, rect), None


def still_depth(plan, item, img, origin, wait_s):
    """(depth 0..1 at img's size, rung, reason) for a still: the depth
    sidecar of the asset / the main video's keyframe; the person matte as
    two planes when depth is absent (main video only); None otherwise."""
    import cv2
    import depth_track
    h, w = img.shape[:2]
    dest = os.path.join(plan.workdir or "/tmp", "w4depth")
    if origin[0] == "asset":
        clip = _local_still_depth(origin[1])
        if clip is None:
            ref = _asset_ref(origin[1])
            if ref is not None:
                clip, _st = depth_track.fetch(ref, [(0.0, 0.04)], dest,
                                              wait_s=wait_s)
        if clip is not None and len(clip):
            d = cv2.resize(clip.d[0], (w, h), interpolation=cv2.INTER_LINEAR)
            return d, "full", "depth"
        return None, "substitute", "no depth measured for this still yet"
    src_t, rect = origin[1], origin[2]
    clip = None
    if plan.source is not None:
        clip, _st = depth_track.fetch(plan.source, [(max(0.0, src_t - 0.3),
                                                     src_t + 0.3)], dest,
                                      wait_s=wait_s)
    d = clip.at(src_t) if clip is not None else None
    if d is not None:
        d = _crop_map(d, rect, w, h)
        return d, "full", "depth"
    m = _matte_at(plan, src_t, dest)
    if m is not None:
        m = _crop_map(m, rect, w, h)
        return np.clip(cv2.GaussianBlur(m, (0, 0), max(1.0, h * 0.004)),
                       0, 1), "reduced", "two planes from the person matte " \
            "(depth not measured here yet)"
    return None, "substitute", "neither depth nor the person matte is " \
        "measured here yet"


def _crop_map(m, rect, w, h):
    import cv2
    if rect:
        mh, mw = m.shape[:2]
        x0, y0 = int(rect[0] * mw), int(rect[1] * mh)
        x1, y1 = max(x0 + 2, int(rect[2] * mw)), max(y0 + 2,
                                                       int(rect[3] * mh))
        m = m[y0:y1, x0:x1]
    return cv2.resize(m.astype(np.float32), (w, h),
                      interpolation=cv2.INTER_LINEAR)


def _asset_ref(path):
    """A SourceRef for a local still (its own sha), or None."""
    try:
        import media
        import qa_metrics
        import sidecars
        info = qa_metrics.probe(path)
        sha = media.sha256_file(path)
        return sidecars.source_from(sha, path, {
            "duration": 0.04, "fps": 25.0, "width": info.get("width"),
            "height": info.get("height")}, None, [])
    except Exception:  # noqa: BLE001
        return None


def _local_still_depth(path):
    """Dev/refbench lanes with torch: measure the still in-process
    (``VALMERA_DEPTH_LOCAL=1``). Production renders never do this."""
    if os.environ.get("VALMERA_DEPTH_LOCAL", "") not in ("1", "true"):
        return None
    try:
        import depth_track
        return depth_track.local_clip(path, 0.0, 0.04, fps=25.0)
    except Exception as e:  # noqa: BLE001
        print(f"[depth] local still depth failed: {str(e)[:120]}",
              flush=True)
        return None


def _matte_at(plan, src_t, dest):
    """The person matte (0..1, source space at its chunk size) at SOURCE
    second ``src_t``, from ready matte.person chunks; None when absent."""
    try:
        import matte_track
        import qa_metrics
        import sidecars
        if plan.source is None:
            return None
        h = matte_track.read_handle("matte.person", plan.source,
                                    [(max(0.0, src_t - 0.2), src_t + 0.2)])
        if h is None:
            return None
        for f in sidecars.fetch(h, dest):
            if f["t0"] - 1e-3 <= src_t < f["t1"] + 1e-3:
                fps = float((f.get("meta") or {}).get("fps") or 30.0)
                k = int(round((src_t - f["t0"]) * fps))
                idx, frames = qa_metrics.read_frames(f["path"], gray=True,
                                                     frames={k})
                if frames:
                    return np.asarray(frames[0], np.float32) / 255.0
    except Exception as e:  # noqa: BLE001 — absent: the substitute rung
        print(f"[depth] matte read failed: {str(e)[:120]}", flush=True)
    return None


# ── parallax: layered 2.5D ───────────────────────────────────────────────

def build_layers(img, depth, n_layers=LAYERS):
    """[(rgb, alpha, centre_depth)] far -> near. The farthest layer is
    opaque everywhere, with everything nearer inpainted out of it (so a
    move never uncovers a hole); nearer layers carry their own feathered
    slice. Returns (layers, inpaint_mask) — the share of the frame that is
    inpainted content (what a move may reveal)."""
    import cv2
    h, w = depth.shape
    qs = np.quantile(depth, np.linspace(0, 1, n_layers + 1)[1:-1])
    edges = [0.0] + [float(q) for q in qs] + [1.01]
    feather = max(1.0, FEATHER * min(h, w))
    layers = []
    inpaint_mask = np.zeros((h, w), bool)
    k = INPAINT_SIDE / float(max(h, w))
    sw, sh = max(8, int(w * k)), max(8, int(h * k))
    small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
    for i in range(n_layers):
        lo, hi = edges[i], edges[i + 1]
        own = (depth >= lo) & (depth < hi)
        nearer = depth >= hi
        if i == 0:
            hole = nearer
            if hole.any():
                hs = cv2.resize(hole.astype(np.uint8) * 255, (sw, sh),
                                interpolation=cv2.INTER_NEAREST)
                hs = cv2.dilate(hs, np.ones((5, 5), np.uint8))
                filled = cv2.inpaint(small, hs, 7, cv2.INPAINT_TELEA)
                up = cv2.resize(filled, (w, h), interpolation=cv2.INTER_CUBIC)
                soft = cv2.GaussianBlur(hole.astype(np.float32), (0, 0),
                                        feather)[..., None]
                rgb = (img * (1 - soft) + up * soft).astype(np.uint8)
                inpaint_mask = hole
            else:
                rgb = img
            alpha = np.ones((h, w), np.float32)
        else:
            m = own | nearer if i == n_layers - 1 else own
            # a nearer slice keeps a little of itself past its edge, so
            # neighbouring slices overlap instead of tearing
            grow = cv2.dilate(m.astype(np.uint8),
                              np.ones((3, 3), np.uint8)).astype(np.float32)
            alpha = np.clip(cv2.GaussianBlur(grow, (0, 0), feather), 0, 1)
            rgb = img
        centre = float(np.median(depth[own])) if own.any() else (lo + hi) / 2
        layers.append((rgb, alpha, centre))
    return layers, inpaint_mask


def layer_affine(path, amount, d, p, W, H):
    """The 2x3 affine moving a layer at depth ``d`` (0 far .. 1 near) at
    progress ``p`` of ``path``: nearer moves more (parallax)."""
    a = float(amount)
    par = 0.25 + 0.75 * d                  # near layers move the most
    cx, cy = W / 2.0, H / 2.0
    s, tx, ty = 1.0, 0.0, 0.0
    if path in ("push_in", "pull_out"):
        z = a * 0.22 * par
        s = 1.0 + (z * p if path == "push_in" else z * (1 - p))
        s *= 1.0 + 0.04 * a                 # never reveal an edge
    elif path in ("drift_left", "drift_right"):
        sign = -1.0 if path == "drift_left" else 1.0
        tx = sign * (p - 0.5) * a * 0.10 * W * par
        s = 1.0 + 0.06 * a + 0.04 * a * par
    elif path in ("rise", "fall"):
        sign = -1.0 if path == "rise" else 1.0
        ty = sign * (p - 0.5) * a * 0.08 * H * par
        s = 1.0 + 0.06 * a + 0.04 * a * par
    else:                                   # orbit: an arc with depth
        sign = -1.0 if path == "orbit_left" else 1.0
        ang = sign * (p - 0.5) * math.pi * 0.5
        tx = math.sin(ang) * a * 0.09 * W * (par - 0.45)
        s = 1.0 + 0.07 * a + 0.03 * a * par * math.cos(ang)
    return np.float32([[s, 0, (1 - s) * cx + tx],
                       [0, s, (1 - s) * cy + ty]])


def render_parallax(layers, W, H, n, move, writer):
    """Composite the layers for n frames along ``move`` into ``writer``
    (RGB). Returns the share of frames' pixels showing inpainted
    background (measured on frame 0 and the last)."""
    import cv2
    path = (move or {}).get("path") or "push_in"
    amount = float((move or {}).get("amount") or 0.35)
    ez = (move or {}).get("ease") or "in_out"
    for i in range(n):
        p = ease(i / max(1, n - 1), ez)
        acc = None
        for rgb, alpha, d in layers:
            m = layer_affine(path, amount, d, p, W, H)
            lay = cv2.warpAffine(rgb, m, (W, H), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REFLECT)
            if acc is None:
                acc = lay.astype(np.float32)
                continue
            al = cv2.warpAffine(alpha, m, (W, H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT,
                                borderValue=0)[..., None]
            acc = acc * (1 - al) + lay.astype(np.float32) * al
        writer.write(np.clip(acc + 0.5, 0, 255).astype(np.uint8))


def visible_inpaint(layers, inpaint_mask, W, H, move, n):
    """The largest share of the frame (over first / middle / last frames)
    where inpainted background shows through the nearer layers."""
    import cv2
    path = (move or {}).get("path") or "push_in"
    amount = float((move or {}).get("amount") or 0.35)
    ez = (move or {}).get("ease") or "in_out"
    worst = 0.0
    for i in (0, n // 2, n - 1):
        p = ease(i / max(1, n - 1), ez)
        bg = cv2.warpAffine(inpaint_mask.astype(np.float32),
                            layer_affine(path, amount, layers[0][2], p, W, H),
                            (W, H), flags=cv2.INTER_LINEAR)
        cover = np.zeros((H, W), np.float32)
        for rgb, alpha, d in layers[1:]:
            al = cv2.warpAffine(alpha, layer_affine(path, amount, d, p, W, H),
                                (W, H), flags=cv2.INTER_LINEAR)
            cover = cover + (1 - cover) * al
        shown = bg * (1 - cover)
        worst = max(worst, float((shown > 0.5).mean()))
    return worst


def render_ken_burns(img, W, H, n, move, writer):
    import cv2
    path = (move or {}).get("path") or "push_in"
    amount = float((move or {}).get("amount") or 0.35)
    ez = (move or {}).get("ease") or "in_out"
    base = cover_crop(img, W, H, margin=1.0 + 0.12 * amount)
    for i in range(n):
        p = ease(i / max(1, n - 1), ez)
        m = layer_affine(path, amount * 0.8, 0.5, p, W, H)
        writer.write(cv2.warpAffine(base, m, (W, H), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REFLECT))


# ── live depth in program space ──────────────────────────────────────────

class GuideClip:
    """Gray source frames over a SOURCE range (the picture the depth sits
    under), read once: the guided filter snaps depth edges to them."""

    def __init__(self, path, rng, fps, width=640, src_wh=None):
        import qa_metrics
        self.t, self.f = [], []
        if not path or not rng:
            return
        sw, sh = src_wh or (None, None)
        if not sw or not sh:
            info = qa_metrics.probe(path)
            sw, sh = info.get("width"), info.get("height")
        w = int(min(width, sw or width))
        w -= w % 2
        h = int(round((sh or 9) * w / float(sw or 16) / 2)) * 2
        a, b = float(rng[0]), float(rng[1])
        try:
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{a:.3f}",
                 "-t", f"{b - a:.3f}", "-i", path, "-an", "-vf",
                 f"fps={float(fps):.6f},scale={w}:{h}:flags=area,"
                 "format=gray", "-f", "rawvideo", "-"],
                capture_output=True, timeout=120).stdout
        except Exception:  # noqa: BLE001
            raw = b""
        n = len(raw) // (w * h)
        for k in range(n):
            self.t.append(a + k / float(fps))
            self.f.append(np.frombuffer(raw[k * w * h:(k + 1) * w * h],
                                        np.uint8).reshape(h, w))

    def __len__(self):
        return len(self.t)

    def at(self, src_t):
        if not self.t:
            return None
        k = int(np.argmin(np.abs(np.asarray(self.t) - float(src_t))))
        if abs(self.t[k] - float(src_t)) > 0.2:
            return None
        return self.f[k].astype(np.float32) / 255.0


class ProgramDepth:
    """Depth (and the person matte, for focus on the subject) per program
    frame of a window, in program space, edge-snapped to the picture
    (guided filter) and smoothed in time."""

    def __init__(self, plan, item, wait_s):
        import depth_track
        self.plan, self.item = plan, item
        self.W, self.H, self.fps = int(plan.W), int(plan.H), plan.fps
        self.f0, self.f1 = fxprims.frame_span(item["start"], item["end"],
                                              plan.fps)
        self.clip, self.status = None, "absent"
        self.matte_only = False
        dest = os.path.join(plan.workdir or "/tmp", "w4depth")
        src = plan.source
        self.sw = getattr(src, "width", None)
        self.sh = getattr(src, "height", None)
        rng = self._src_range()
        if src is not None and rng:
            self.clip, self.status = depth_track.fetch(src, [rng], dest,
                                                       wait_s=wait_s)
        if self.clip is None and src is not None and rng:
            self.mattes = self._matte_clip(rng, dest)
            self.matte_only = self.mattes is not None
        else:
            self.mattes = None
        self._prev = None
        self.guide = None
        if self.ok and rng:
            self.guide = GuideClip(_env(plan).get("src_path"), rng,
                                   getattr(src, "fps", None) or plan.fps,
                                   src_wh=(self.sw, self.sh))

    def _src_range(self):
        tl = self.plan.tl
        if tl is None:
            return None
        a = tl.out_to_src(float(self.item["start"]))
        b = tl.out_to_src(max(float(self.item["start"]),
                              float(self.item["end"]) - 0.01))
        if a is None or b is None:
            return None
        return (max(0.0, min(a, b) - 0.2), max(a, b) + 0.2)

    def _matte_clip(self, rng, dest):
        """A DepthClip-shaped clip of the person matte (1 = subject)."""
        try:
            import depth_track
            import matte_track
            import qa_metrics
            import sidecars
            h = matte_track.read_handle("matte.person", self.plan.source,
                                        [rng])
            ts, ds = [], []
            for f in sidecars.fetch(h, dest) if h is not None else []:
                fps = float((f.get("meta") or {}).get("fps") or 30.0)
                ks = range(max(0, int((rng[0] - f["t0"]) * fps)),
                           int((rng[1] - f["t0"]) * fps) + 1)
                idx, frames = qa_metrics.read_frames(
                    f["path"], gray=True, frames=set(ks), size=(480, 270)
                    if (self.sw or 16) > (self.sh or 9) else (270, 480))
                for k, fr in zip(idx, frames):
                    ts.append(f["t0"] + k / fps)
                    ds.append(np.asarray(fr, np.float32) / 255.0)
            if not ts:
                return None
            return depth_track.DepthClip(np.asarray(ts), np.stack(ds),
                                         self.plan.source.shots)
        except Exception as e:  # noqa: BLE001
            print(f"[depth] matte clip failed: {str(e)[:120]}", flush=True)
            return None

    @property
    def ok(self):
        return self.clip is not None or self.mattes is not None

    def frame(self, i):
        """Program-space depth of window frame i (0..1, 1 near) or None."""
        import depth_track
        t = (self.f0 + i) / self.plan.fps3
        clip = self.clip if self.clip is not None else self.mattes
        m = depth_track.to_program(clip, _edl(self.plan), self.plan.tl, t,
                                   self.W, self.H, self.sw, self.sh)
        if m is None:
            self._prev = None
            return None
        if self.guide is not None and len(self.guide):
            import depth_track
            g = depth_track.to_program(self.guide, _edl(self.plan),
                                       self.plan.tl, t, self.W, self.H,
                                       self.sw, self.sh)
            if g is not None:
                m = np.clip(guided(g, m, max(2, int(self.H * 0.008)),
                                   2e-3), 0.0, 1.0)
        elif self.matte_only:
            import cv2
            m = cv2.GaussianBlur(m, (0, 0), max(1.0, self.H * 0.004))
        self._prev = m if self._prev is None else \
            0.55 * self._prev + 0.45 * m
        return self._prev

    def guide_frame(self, i):
        """The picture (gray 0..1) under window frame i in program space,
        or None."""
        if self.guide is None or not len(self.guide):
            return None
        import depth_track
        t = (self.f0 + i) / self.plan.fps3
        return depth_track.to_program(self.guide, _edl(self.plan),
                                      self.plan.tl, t, self.W, self.H,
                                      self.sw, self.sh)

    def person(self, i):
        """Program-space person matte at window frame i (None without)."""
        import depth_track
        if self.mattes is None and not getattr(self, "_tried_matte", False):
            self._tried_matte = True
            rng = self._src_range()
            if rng and self.plan.source is not None:
                self.mattes = self._matte_clip(rng, os.path.join(
                    self.plan.workdir or "/tmp", "w4depth"))
        if self.mattes is None:
            return None
        t = (self.f0 + i) / self.plan.fps3
        return depth_track.to_program(self.mattes, _edl(self.plan),
                                      self.plan.tl, t, self.W, self.H,
                                      self.sw, self.sh)


def otsu_split(d):
    """(threshold, near median, far median) of a depth map: the subject of
    a talking head is the near mode."""
    v = np.clip(d.ravel(), 0, 1)
    if v.size > 200_000:
        v = v[:: v.size // 200_000 + 1]
    hist, edges = np.histogram(v, bins=64, range=(0, 1))
    w = hist.astype(np.float64)
    c = 0.5 * (edges[1:] + edges[:-1])
    best, thr = -1.0, 0.5
    for k in range(1, 63):
        w0, w1 = w[:k].sum(), w[k:].sum()
        if w0 < 1 or w1 < 1:
            continue
        m0 = (w[:k] * c[:k]).sum() / w0
        m1 = (w[k:] * c[k:]).sum() / w1
        between = w0 * w1 * (m0 - m1) ** 2
        if between > best:
            best, thr = between, float(edges[k])
    near = v[v >= thr]
    far = v[v < thr]
    return (thr, float(np.median(near)) if near.size else 0.9,
            float(np.median(far)) if far.size else 0.2)


def focus_range(d, target, person=None):
    """The in-focus depth interval (lo, hi) ``target`` ({on, depth}) names
    on map ``d`` (1 = near). The subject is the near mode (Otsu split), or
    what the person matte covers when one is given."""
    on = (target or {}).get("on") or "subject"
    if on == "depth":
        v = float(target.get("depth") or 0.5)
        return (v - 0.08, v + 0.08)
    if on == "near":
        return (float(np.percentile(d, 90)), 1.0)
    if on == "far":
        return (0.0, float(np.percentile(d, 10)))
    if person is not None and (person > 0.5).mean() > 0.01:
        inside = d[person > 0.5]
        lo = float(np.percentile(inside, 4))
        return (lo, 1.0) if on == "subject" else (0.0, lo - 0.02)
    thr, _near, _far = otsu_split(d)
    return (thr - 0.02, 1.0) if on == "subject" else (0.0, thr + 0.02)


def focus_depth(d, target, person=None):
    """A single depth value for ``target`` (the centre of its range)."""
    lo, hi = focus_range(d, target, person)
    return (lo + hi) / 2.0


def guided(guide, src, r, eps=1e-3):
    """He et al.'s guided filter (box filters): ``src`` (a depth or weight
    map) re-shaped to follow the edges of ``guide`` (gray 0..1)."""
    import cv2
    I = guide.astype(np.float32)
    p = src.astype(np.float32)
    k = (2 * int(r) + 1, 2 * int(r) + 1)
    mI = cv2.blur(I, k)
    mp = cv2.blur(p, k)
    cIp = cv2.blur(I * p, k) - mI * mp
    vI = cv2.blur(I * I, k) - mI * mI
    a = cIp / (vI + eps)
    b = mp - a * mI
    return cv2.blur(a, k) * I + cv2.blur(b, k)


def radius_map(d, rng, R, ramp=0.28):
    """uint8 radius map IN PIXELS (varblur reads the value as the radius,
    clamped to max_r): 0 inside the in-focus interval ``rng`` (lo, hi),
    rising to ``R`` px over the next ``ramp`` of depth."""
    lo, hi = rng
    dist = np.maximum(lo - d, 0.0) + np.maximum(d - hi, 0.0)
    r = np.clip(dist / ramp, 0.0, 1.0)
    return (r * float(R) + 0.5).astype(np.uint8)


def rack_value(i, n, fps, start, rack_at, rack_f, a, b):
    """The in-focus interval at window frame i: ``a`` until the pull
    starts, then an eased travel to ``b`` that completes ON ``rack_at``
    (each of a, b a (lo, hi) pair)."""
    t = start + i / fps
    # no rack_at: the pull opens the window and lands rack_f frames in (a
    # planner places the window rack_f frames before the word: it lands ON
    # the word)
    end = rack_at if rack_at is not None else start + rack_f / fps
    begin = end - (rack_f - 1) / fps
    if t <= begin:
        return a
    if t >= end - 1e-9:
        return b
    x = ease((t - begin) / max(1e-6, end - begin), "in_out")
    return (a[0] + (b[0] - a[0]) * x, a[1] + (b[1] - a[1]) * x)


# ── point cloud ──────────────────────────────────────────────────────────

class Cloud:
    """A seeded point set over a depth grid; ``render`` splats one frame."""

    def __init__(self, W, H, density=1.0, palette="red", seed=0, trails=2,
                 scan=True):
        self.W, self.H = int(W), int(H)
        # never more points than ~12 % of the output pixels: a cloud reads
        # as points, not a lit silhouette (CLOUD_POINTS at 1080x1920)
        self.n = int(min(CLOUD_POINTS, 0.12 * self.W * self.H)
                     * float(density or 1.0))
        self.palette = PALETTES.get(palette, PALETTES["red"])
        self.rng = np.random.default_rng(int(seed or 0))
        self.trails = int(trails or 0)
        self.scan = bool(True if scan is None else scan)
        self.acc = None
        k = CLOUD_GRID / float(max(W, H))
        self.gw, self.gh = max(8, int(W * k)), max(8, int(H * k))
        self.sel = None

    def _colour(self, d):
        near, mid, far = (np.asarray(c, np.float32) for c in self.palette)
        d = d[:, None]
        lo = far + (mid - far) * np.clip(d * 2.0, 0, 1)
        return np.where(d > 0.5, mid + (near - mid) * np.clip(d * 2 - 1, 0,
                                                              1), lo)

    def render(self, depth, valid, p, i, n, orbit=0.35, shade=None):
        import cv2
        gw, gh = self.gw, self.gh
        d = cv2.resize(depth, (gw, gh), interpolation=cv2.INTER_AREA)
        sh = cv2.resize(shade, (gw, gh), interpolation=cv2.INTER_AREA) \
            if shade is not None else None
        v = cv2.resize(valid.astype(np.float32), (gw, gh),
                       interpolation=cv2.INTER_AREA) > 0.5
        ys, xs = np.nonzero(v)
        if self.sel is None or len(self.sel) != min(self.n, max(1, len(xs))):
            # one seeded subset of grid cells (re-drawn only if the valid
            # area changes size class): points do not crawl frame to frame
            self.order = self.rng.permutation(gw * gh)
        cells = ys * gw + xs
        rank = np.empty(gw * gh, np.int64)
        rank[self.order] = np.arange(gw * gh)
        keep = np.argsort(rank[cells])[:self.n]
        xs, ys = xs[keep], ys[keep]
        dz = d[ys, xs]
        lum = sh[ys, xs] if sh is not None else None
        self.sel = keep
        # camera: points live at Z = 1 (near) .. 3 (far); a slow orbit
        f = 1.15 * max(gw, gh)
        X = (xs - gw / 2.0) / f * 2.0
        Y = (ys - gh / 2.0) / f * 2.0
        Z = 1.0 + (1.0 - dz) * 2.0
        X, Y = X * Z, Y * Z
        ang = (p - 0.5) * float(orbit) * 0.9
        ca, sa = math.cos(ang), math.sin(ang)
        Zc = 2.0
        Xr = ca * X + sa * (Z - Zc)
        Zr = -sa * X + ca * (Z - Zc) + Zc
        px = (Xr / np.maximum(Zr, 0.2) * f / 2.0 + gw / 2.0) * (self.W / gw)
        py = (Y / np.maximum(Zr, 0.2) * f / 2.0 + gh / 2.0) * (self.H / gh)
        ok = (px >= 0) & (px < self.W - 1) & (py >= 0) & (py < self.H - 1)
        px, py, dz = px[ok].astype(np.int32), py[ok].astype(np.int32), dz[ok]
        col = self._colour(dz)
        bright = 0.35 + 0.4 * dz
        if lum is not None:
            # the picture's own light shades the points: a face reads
            bright = bright * (0.35 + 1.3 * lum[ok])
        if self.scan:
            band = (i / max(1, n - 1) * 1.35 - 0.15) * self.H
            bright = bright + 1.6 * np.exp(-((py - band) / (0.02 * self.H))
                                           ** 2)
        img = np.zeros((self.H, self.W, 3), np.float32)
        np.add.at(img, (py, px), col * bright[:, None])
        glow = cv2.GaussianBlur(img, (0, 0), max(1.0, self.H * 0.003))
        img = img + 1.5 * glow
        if self.trails:
            decay = 0.5 + 0.05 * self.trails
            self.acc = img if self.acc is None else np.maximum(
                img, self.acc * decay)
            img = self.acc
        # filmic shoulder: dense areas glow, never clip to a flat blob
        img = 1.0 - np.exp(-1.6 * img)
        return np.clip(img * 255.0, 0, 255).astype(np.uint8)


# ── push through type in Z ───────────────────────────────────────────────

def text_plane(text, W, H, scale, blur, colour=(255, 255, 255),
               alpha=1.0, font=None):
    """An RGBA plane of ``text`` centred, at ``scale`` x its base size
    (base: 38 % of the frame's short side wide), gaussian-defocused by
    ``blur`` px."""
    import cv2
    from PIL import Image, ImageDraw, ImageFont
    base = int(min(W, H) * 0.16)
    fsize = max(8, int(base * scale))
    try:
        fnt = ImageFont.truetype(font or _font_path(), fsize)
    except Exception:  # noqa: BLE001
        fnt = ImageFont.load_default()
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    bb = dr.textbbox((0, 0), text, font=fnt)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    dr.text(((W - tw) / 2 - bb[0], (H - th) / 2 - bb[1]), text,
            font=fnt, fill=tuple(colour) + (int(255 * alpha),))
    a = np.asarray(im)
    if blur > 0.5:
        a = cv2.GaussianBlur(a, (0, 0), float(blur))
    return a


def _font_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("Anton-Regular.ttf", "BebasNeue-Regular.ttf",
                 "Inter-Black.ttf"):
        for d in (os.path.join(here, "fonts"),
                  os.path.join(here, "motion", "fonts")):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    for root in (os.path.join(here, "fonts"),
                 os.path.join(here, "motion", "fonts")):
        for dp, _dn, files in os.walk(root):
            for f in sorted(files):
                if f.lower().endswith((".ttf", ".otf")):
                    return os.path.join(dp, f)
    return None


# ── the compiler ─────────────────────────────────────────────────────────

class _Depth:
    FILTERS = FILTERS
    KEEPS_FRAME_TAGS = KEEPS_FRAME_TAGS

    def cost_ms(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        return 300.0 + (f1 - f0) * mpx * MS_PER_MPX.get(item.get("kind"),
                                                         10.0)

    def landing(self, plan, item):
        if item.get("kind") == "rack_focus":
            at = item.get("rack_at")
            if at is None:
                at = float(item["start"]) + int(item.get("rack_f") or 12) \
                    / plan.fps3
            return [fxprims.frame_at(float(at), plan.fps)]
        return [fxprims.frame_at(float(item["start"]), plan.fps)]

    def prepare(self, plan, item):
        kind = item.get("kind")
        if kind not in KINDS:
            return ("omit", f"unknown depth kind {kind!r}")
        if not plan.W or not plan.H or not plan.workdir:
            return ("omit", "no geometry or workdir for the depth effect")
        if faultinject.fault("oom:fx_precompute"):
            raise MemoryError("the depth precompute was OOM-killed")
        if kind in LIVE_KINDS and item.get("stage") == "screen":
            return ("omit", f"{kind} reads the shot's depth: world stage")
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        n = f1 - f0
        if n < 1:
            return ("omit", "an empty window")
        wait_s = 0.0 if plan.preview else 90.0
        st = {"n": n, "rung": "full", "reason": "ok"}
        os.makedirs(plan.workdir, exist_ok=True)
        try:
            if kind in ("parallax", "ken_burns"):
                st.update(self._prep_still(plan, item, n, wait_s))
            elif kind in ("dof", "rack_focus", "fog", "light"):
                st.update(self._prep_live(plan, item, n, wait_s))
            elif kind == "point_cloud":
                st.update(self._prep_cloud(plan, item, n, wait_s))
            elif kind == "push_through":
                st.update(self._prep_push(plan, item, n))
        except MemoryError:
            raise
        except Exception as e:  # noqa: BLE001 — degrade, never fail
            return ("omit", f"depth precompute failed ({type(e).__name__}: "
                            f"{str(e)[:120]})")
        if st.get("rung") == "omit":
            return ("omit", st.get("reason") or "omitted")
        _state(plan)[item.get("id")] = st
        if st.get("clip"):
            plan.add_input(f"w4d:{item['id']}", ["-i", st["clip"]])
        if st.get("map"):
            plan.add_input(f"w4m:{item['id']}", ["-i", st["map"]])
        if st["rung"] != "full":
            return (st["rung"], st["reason"])
        return None

    # -- stills ---------------------------------------------------------
    def _prep_still(self, plan, item, n, wait_s):
        W, H = int(plan.W), int(plan.H)
        img, origin, why = still_for(plan, item)
        if img is None:
            return {"rung": "omit", "reason": why}
        out = os.path.join(plan.workdir, f"w4d_{_key(item, plan)}.mkv")
        move = item.get("move") or {"path": "push_in", "amount": 0.35,
                                    "ease": "in_out"}
        if item["kind"] == "ken_burns":
            w = ClipWriter(out, W, H, plan.fps3, "rgb24")
            render_ken_burns(img, W, H, n, move, w)
            w.close()
            return {"clip": out, "replace": True}
        depth, rung, reason = still_depth(plan, item, img, origin, wait_s)
        if depth is None:
            w = ClipWriter(out, W, H, plan.fps3, "rgb24")
            render_ken_burns(img, W, H, n, move, w)
            w.close()
            return {"clip": out, "replace": True, "rung": "substitute",
                    "reason": f"{reason} -> ken_burns"}
        margin = 1.0 + 0.10 * float(move.get("amount") or 0.35)
        pic = cover_crop(img, W, H, margin=1.0)
        dep = cover_crop(np.dstack([depth] * 3).astype(np.float32), W, H,
                         margin=1.0)[..., 0]
        layers, hole = build_layers(pic, np.clip(dep, 0, 1))
        w = ClipWriter(out, W, H, plan.fps3, "rgb24")
        render_parallax(layers, W, H, n, move, w)
        w.close()
        vis = visible_inpaint(layers, hole, W, H, move, n)
        del margin
        return {"clip": out, "replace": True, "rung": rung,
                "reason": reason if rung != "full" else "ok",
                "visible_inpaint": round(vis, 4)}

    # -- live depth -----------------------------------------------------
    def _prep_live(self, plan, item, n, wait_s):
        import cv2
        W, H = int(plan.W), int(plan.H)
        pdepth = ProgramDepth(plan, item, wait_s)
        if not pdepth.ok:
            return {"rung": "omit", "reason": "neither depth nor the person "
                    "matte is measured under this window yet (measuring)"}
        rung = "reduced" if pdepth.matte_only else "full"
        reason = ("two planes from the person matte (depth not measured "
                  "here yet)" if pdepth.matte_only else "ok")
        kind = item["kind"]
        out = os.path.join(plan.workdir, f"w4m_{_key(item, plan)}.mkv")
        w = ClipWriter(out, W, H, plan.fps3, "gray")
        blur = float(item.get("blur") if item.get("blur") is not None
                     else 0.6)
        R = max(1, min(255, int(round(MAX_BLUR * H * blur))))
        strength = float(item.get("strength") if item.get("strength")
                         is not None else 0.7)
        first = None
        fa = fb = None
        for i in range(n):
            d = pdepth.frame(i)
            if d is None:
                w.write(np.zeros((H, W), np.uint8))
                continue
            if first is None:
                first = d
                per = None if pdepth.matte_only else pdepth.person(i)
                if kind == "rack_focus":
                    fa = focus_range(d, item.get("focus_from"), per)
                    fb = focus_range(d, item.get("focus"), per)
                elif kind == "dof":
                    fa = fb = focus_range(d, item.get("focus"), per)
            if kind == "dof":
                w.write(radius_map(d, fa, R))
            elif kind == "rack_focus":
                f = rack_value(i, n, plan.fps3, float(item["start"]),
                               item.get("rack_at"), int(item.get("rack_f")
                                                        or 12), fa, fb)
                w.write(radius_map(d, f, R))
            elif kind == "fog":
                far = np.clip((1.0 - d - 0.15) / 0.85, 0, 1) ** 1.4
                w.write((far * strength * 255.0 + 0.5).astype(np.uint8))
            elif kind == "light":
                s = 1.05 - 1.2 * ease(i / max(1, n - 1), "in_out")
                band = np.exp(-((d - s) / 0.09) ** 2)
                band = cv2.GaussianBlur(band.astype(np.float32), (0, 0),
                                        max(1.0, H * 0.006))
                w.write((np.clip(band, 0, 1) * strength * 255.0 + 0.5)
                        .astype(np.uint8))
        w.close()
        return {"map": out, "rung": rung, "reason": reason, "blur_px": R}

    # -- point cloud ----------------------------------------------------
    def _prep_cloud(self, plan, item, n, wait_s):
        W, H = int(plan.W), int(plan.H)
        backdrop = item.get("backdrop") or "black"
        palette = "red"
        c = (item.get("colour") or "").lower()
        if c in ("white",):
            palette = "white"
        elif c and c not in ("accent", "black"):
            rgb = colour_rgb(c)
            palette = "cyan" if rgb[2] > rgb[0] else "amber" \
                if rgb[1] > 0.5 * rgb[0] else "red"
        cloud = Cloud(W, H, item.get("density") or 1.0, palette,
                      seed=item.get("seed") or 0, trails=item.get("trails")
                      if item.get("trails") is not None else 2,
                      scan=item.get("scan"))
        out = os.path.join(plan.workdir, f"w4d_{_key(item, plan)}.mkv")
        move = item.get("move") or {"path": "orbit_left", "amount": 0.35}
        orbit = float(move.get("amount") or 0.35) * (
            -1 if move.get("path") == "orbit_right" else 1)
        if item.get("source"):
            img, origin, why = still_for(plan, item)
            if img is None:
                return {"rung": "omit", "reason": why}
            depth, rung, reason = still_depth(plan, item, img, origin,
                                              wait_s)
            if depth is None:
                return {"rung": "omit", "reason": reason}
            dep = cover_crop(np.dstack([depth] * 3).astype(np.float32), W, H
                             )[..., 0]
            w = ClipWriter(out, W, H, plan.fps3, "rgb24")
            for i in range(n):
                p = ease(i / max(1, n - 1), "in_out")
                w.write(cloud.render(dep, np.ones_like(dep, bool), p, i, n,
                                     orbit))
            w.close()
            return {"clip": out, "replace": backdrop == "black",
                    "backdrop": backdrop, "rung": rung, "reason": reason}
        pdepth = ProgramDepth(plan, item, wait_s)
        if not pdepth.ok:
            return {"rung": "omit", "reason": "neither depth nor the person "
                    "matte is measured under this window yet (measuring)"}
        subject = ((item.get("focus") or {}).get("on") == "subject")
        mattes = pdepth._matte_clip(pdepth._src_range(), os.path.join(
            plan.workdir, "w4depth")) if subject and pdepth.clip is not None \
            else None
        w = ClipWriter(out, W, H, plan.fps3, "rgb24")
        for i in range(n):
            d = pdepth.frame(i)
            if d is None:
                w.write(np.zeros((H, W, 3), np.uint8))
                continue
            valid = np.ones_like(d, bool)
            if subject:
                # without a matte the subject is the near mode of the depth
                valid = d >= otsu_split(d)[0]
            if pdepth.matte_only:
                valid = d > 0.5
            elif mattes is not None:
                import depth_track
                t = (pdepth.f0 + i) / plan.fps3
                m = depth_track.to_program(mattes, _edl(plan), plan.tl, t,
                                           W, H, pdepth.sw, pdepth.sh)
                if m is not None:
                    valid = m > 0.5
            p = ease(i / max(1, n - 1), "in_out")
            w.write(cloud.render(d, valid, p, i, n, orbit,
                                 shade=pdepth.guide_frame(i)))
        w.close()
        rung = "reduced" if pdepth.matte_only else "full"
        return {"clip": out, "replace": backdrop == "black",
                "backdrop": backdrop, "rung": rung,
                "reason": ("silhouette points from the person matte (depth "
                           "not measured here yet)" if pdepth.matte_only
                           else "ok")}

    # -- push through ---------------------------------------------------
    def _prep_push(self, plan, item, n):
        W, H = int(plan.W), int(plan.H)
        out = os.path.join(plan.workdir, f"w4d_{_key(item, plan)}.mkv")
        col = colour_rgb(item.get("colour") or "white", _accent(plan))
        blur = float(item.get("blur") if item.get("blur") is not None
                     else 0.7)
        w = ClipWriter(out, W, H, plan.fps3, "rgba")
        for i in range(n):
            p = ease(i / max(1, n - 1), "in_out")
            # the word starts far (small, sharp) and rushes past the lens:
            # scale grows ~ 1/(distance); defocus grows as it nears
            dist = max(0.06, 1.0 - 0.94 * p)
            scale = 0.9 / dist
            bl = blur * H * 0.03 * max(0.0, (1.0 / dist - 2.0)) / 14.0
            alpha = 1.0 if dist > 0.22 else max(0.0, (dist - 0.06) / 0.16)
            w.write(text_plane(item["text"], W, H, scale, bl, col, alpha))
        w.close()
        amount = float((item.get("move") or {}).get("amount") or 0.35)
        return {"clip": out, "replace": False, "overlay_rgba": True,
                "push": amount}

    # -- emission -------------------------------------------------------
    def stage(self, ctx, items):
        import finish
        return finish.emit_each(ctx, items, self._emit)

    def _emit(self, ctx, label, it):
        import finish
        st = _state(ctx.plan).get(it.get("id")) or {}
        f0, f1 = ctx.frames(it)
        n = f1 - f0
        u = ctx.uid("dp")
        kind = it["kind"]
        clip = ctx.input_label(f"w4d:{it['id']}") if st.get("clip") else None
        mp = ctx.input_label(f"w4m:{it['id']}") if st.get("map") else None
        clock = finish.window_clock(ctx.fps)
        W, H = int(ctx.W), int(ctx.H)
        if st.get("clip") and clip is None or st.get("map") and mp is None:
            raise ValueError("the depth intermediate was never bound")

        def take(lab, fmt):
            out = ctx.uid("dpi")
            ctx.parts.append(f"[{lab}]trim=end_frame={n},{clock},"
                             f"format={fmt}[{out}]")
            return out

        if kind in ("parallax", "ken_burns") or (
                kind == "point_cloud" and st.get("replace")):
            def build(parts, src, uid):
                parts.append(f"[{src}]nullsink")
                return take(clip, "yuv420p")
            return finish.window_composite(ctx, label, [(f0, f1)], build,
                                           uid=u)
        if kind == "point_cloud":
            dim = st.get("backdrop") == "dim"

            def build(parts, src, uid):
                pts = take(clip, "gbrp")
                base = ctx.uid("dpb")
                parts.append(f"[{src}]format=gbrp"
                             + (",lut=c0='val*0.32':c1='val*0.32':"
                                "c2='val*0.32'" if dim else "")
                             + f"[{base}]")
                out = ctx.uid("dpo")
                parts.append(f"[{base}][{pts}]blend=all_mode=screen"
                             f",format=yuv420p[{out}]")
                return out
            return finish.window_composite(ctx, label, [(f0, f1)], build,
                                           uid=u)
        if kind in ("dof", "rack_focus"):
            R = int(st.get("blur_px") or 12)

            def build(parts, src, uid):
                rad = take(mp, "gray")
                p1, p2 = ctx.uid("dpp"), ctx.uid("dpq")
                parts.append(f"[{src}]format=yuv444p,split[{p1}][{p2}]")
                r1, r2 = ctx.uid("dpr"), ctx.uid("dps")
                parts.append(f"[{rad}]split[{r1}][{r2}]")
                bl = ctx.uid("dpl")
                parts.append(f"[{p1}][{r1}]varblur=min_r=0:max_r={R}"
                             f":planes=7[{bl}]")
                # varblur softens even a 0 radius: in-focus pixels come
                # from the untouched picture (the radius map, ramped over
                # its first 2 px, is the merge mask)
                m = ctx.uid("dpm")
                parts.append(f"[{r2}]lut=c0='min(255,val*128)',split=3"
                             f"[{m}x][{m}y][{m}z]")
                parts.append(f"[{m}x][{m}y][{m}z]mergeplanes=0x001020:"
                             f"yuv444p[{m}]")
                out = ctx.uid("dpo")
                parts.append(f"[{p2}][{bl}][{m}]maskedmerge,"
                             f"format=yuv420p[{out}]")
                return out
            return finish.window_composite(ctx, label, [(f0, f1)], build,
                                           uid=u)
        if kind in ("fog", "light"):
            rgb = colour_rgb(it.get("colour") or ("#c9cdd6" if kind == "fog"
                                                  else "#fff1d6"),
                             _accent(ctx.plan))
            hexc = "0x%02x%02x%02x" % rgb

            def build(parts, src, uid):
                wgt = take(mp, "gray")
                a, b = ctx.uid("dpa"), ctx.uid("dpb")
                parts.append(f"[{src}]format=gbrp,split[{a}][{b}]")
                col = ctx.uid("dpc")
                parts.append(f"color=c={hexc}:s={W}x{H}:r={ctx.fps3:.3f}:"
                             f"d={(n + 2) / ctx.fps3:.6f},trim=end_frame={n},"
                             f"{clock},format=gbrp[{col}]")
                tinted = ctx.uid("dpt")
                if kind == "fog":
                    parts.append(f"[{col}]null[{tinted}]")
                    parts.append(f"[{b}]nullsink")
                else:
                    parts.append(f"[{b}][{col}]blend=all_mode=screen"
                                 f"[{tinted}]")
                m3 = ctx.uid("dpm")
                parts.append(f"[{wgt}]format=gray,split=3[{m3}x][{m3}y]"
                             f"[{m3}z]")
                parts.append(f"[{m3}x][{m3}y][{m3}z]mergeplanes=0x001020:"
                             f"gbrp[{m3}]")
                out = ctx.uid("dpo")
                parts.append(f"[{a}][{tinted}][{m3}]maskedmerge,"
                             f"format=yuv420p[{out}]")
                return out
            return finish.window_composite(ctx, label, [(f0, f1)], build,
                                           uid=u)
        if kind == "push_through":
            amount = float(st.get("push") or 0.35)

            def build(parts, src, uid):
                txt = take(clip, "yuva420p")
                z = f"(1+{0.22 * amount:.4f}*(n/{max(1, n - 1)}))"
                pushed = ctx.uid("dpz")
                parts.append(f"[{src}]scale=w='trunc(iw*{z}/2)*2':"
                             f"h='trunc(ih*{z}/2)*2':eval=frame,"
                             f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2,"
                             f"format=yuv420p[{pushed}]")
                out = ctx.uid("dpo")
                parts.append(f"[{pushed}][{txt}]overlay=0:0:format=auto,"
                             f"format=yuv420p[{out}]")
                return out
            return finish.window_composite(ctx, label, [(f0, f1)], build,
                                           uid=u)
        raise ValueError(f"no emitter for depth kind {kind}")


DEPTH = _Depth()
