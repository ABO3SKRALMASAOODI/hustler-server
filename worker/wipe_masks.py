"""Precomputed masks, stills and render context for track 1D's devices.

Wave 1, track 1D (ROADMAP "1D — Punctuation, strobes and junctions v2";
worker/devices/INTERFACES_W1.md §3.4). Everything a 1D compiler draws that
is a SHAPE is computed here, in numpy / PIL, before ffmpeg runs, and enters
the graph as a gray clip: wipes, irises, columns, staircases, box steps,
bands, the picture card's rounded window, seeded invert boxes. ffmpeg then
only composites (alphamerge + overlay, the 0.38 CPU-s ``maskedmerge`` path
of ROADMAP §2) — never a per-pixel expression (``geq`` / ``xfade=custom``
were measured 40x slower and are refused as a wipe engine).

Masks are anti-aliased analytically (coverage of each pixel by the shape,
not a supersampled raster), eased (never linear), and written as PNG
sequences (repeated frames hard-linked) that enter the render as ffmpeg
INPUTS: the same device at the same size writes byte-identical files, so
two renders are frame-identical (determinism).

Render context. A compiler's ``prepare(plan, item)`` sees the FxPlan only.
Card regions need the EDL's picture cards, a strobe's clip frames need the
main video and the project's media. ``render_env(plan)`` returns them:
from ``plan.edl`` / ``plan.media`` when the plan carries them (the seam
1D asked the round lead for), else from the render call it is running in
(``fxgraph.prepare(edl, ...)`` and ``renderer.render_edl(edl_dict, index,
src_path, ..., asset_locals=...)``, read by their pinned parameter names).
Absent context never raises: the device takes its next rung.
"""

import hashlib
import math
import os
import sys

import numpy as np

# Wipe edges: a soft edge this share of the frame's short side (a hard
# 1-px wipe reads as a glitch, ref 22@≈4.9's column edges are soft).
EDGE_SHARE = 0.012
# PNG compression for mask sequences: masks are flat, level 1 is ~5x
# faster than the default and still ~1% of the raw size.
PNG_LEVEL = 1


# ── easing ──────────────────────────────────────────────────────────────

def clamp01(x):
    return np.clip(x, 0.0, 1.0)


def ease_in_out_cubic(p):
    p = float(min(1.0, max(0.0, p)))
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def ease_in_cubic(p):
    p = float(min(1.0, max(0.0, p)))
    return p ** 3


def ease_out_cubic(p):
    p = float(min(1.0, max(0.0, p)))
    return 1 - (1 - p) ** 3


def ease_in_out_sine(p):
    p = float(min(1.0, max(0.0, p)))
    return -(math.cos(math.pi * p) - 1) / 2


def smoothstep(a, b, x):
    if b <= a:
        return 1.0 if x >= b else 0.0
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def progress(j, n):
    """The progress of window frame j of n: the frame's own centre, so a
    window never shows a frame of pure A at its end or pure B at its start
    when the wipe spans the whole window."""
    return (j + 0.5) / float(max(1, n))


# ── analytic shapes (coverage 0..1, float32 H x W) ──────────────────────

def _grid(W, H):
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    return xs + 0.5, ys + 0.5


_GRIDS = {}


def grid(W, H):
    key = (int(W), int(H))
    if key not in _GRIDS:
        if len(_GRIDS) > 4:
            _GRIDS.clear()
        _GRIDS[key] = _grid(int(W), int(H))
    return _GRIDS[key]


def disc(W, H, cx, cy, r, edge=1.0):
    """Coverage of a disc of radius ``r`` px centred at (cx, cy) px."""
    xs, ys = grid(W, H)
    d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    return clamp01((r - d) / max(edge, 1e-3) + 0.5)


def half_plane(coord, edge_at, edge=1.0, below=True):
    """Coverage of the side of a line ``coord <= edge_at`` (below=True)."""
    if below:
        return clamp01((edge_at - coord) / max(edge, 1e-3) + 0.5)
    return clamp01((coord - edge_at) / max(edge, 1e-3) + 0.5)


def box(W, H, x0, y0, x1, y1, edge=1.0):
    """Coverage of an axis-aligned box [x0, x1) x [y0, y1) in px."""
    xs, ys = grid(W, H)
    cx = clamp01(np.minimum((xs - x0), (x1 - xs)) / max(edge, 1e-3) + 0.5)
    cy = clamp01(np.minimum((ys - y0), (y1 - ys)) / max(edge, 1e-3) + 0.5)
    return cx * cy


def to_u8(cov):
    return np.round(clamp01(cov) * 255.0).astype(np.uint8)


# ── junction mask sequences (one uint8 H x W per window frame) ─────────

def edge_px(W, H, share=EDGE_SHARE):
    return max(1.0, share * min(W, H))


def wipe(W, H, n, direction):
    """A straight soft-edged wipe; ``direction`` is where the edge travels
    (``left``: the incoming shot enters from the right)."""
    xs, ys = grid(W, H)
    e = edge_px(W, H)
    out = []
    for j in range(n):
        p = ease_in_out_cubic(progress(j, n))
        if direction == "left":
            cov = half_plane(xs, W + e - p * (W + 2 * e), e, below=False)
        elif direction == "right":
            cov = half_plane(xs, -e + p * (W + 2 * e), e)
        elif direction == "up":
            cov = half_plane(ys, H + e - p * (H + 2 * e), e, below=False)
        else:
            cov = half_plane(ys, -e + p * (H + 2 * e), e)
        out.append(to_u8(cov))
    return out


def iris(W, H, n, centre, halo=0.42):
    """A circle growing from ``centre`` (px) to past the farthest corner,
    accelerating (ref 03@7.93's element iris), with a translucent outer
    ring of the incoming shot riding ahead of the hard edge (``halo``
    opacity) — the iris blooms rather than punches."""
    cx, cy = centre
    far = max(math.hypot(cx - x, cy - y) for x in (0, W) for y in (0, H))
    e = max(1.5, edge_px(W, H, 0.004))
    ring = 0.24
    out = []
    for j in range(n):
        p = progress(j, n)
        # mostly ease-in, landing exactly on the far corner (plus the ring)
        r = (far * (1 + ring) + e) * (0.65 * ease_in_cubic(p)
                                      + 0.35 * ease_in_out_cubic(p))
        core = disc(W, H, cx, cy, r / (1 + ring), e)
        if halo and r > 0:
            xs, ys = grid(W, H)
            d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
            outer = clamp01((r - d) / max(e, r * 0.06))
            core = np.maximum(core, halo * outer)
        out.append(to_u8(core))
    return out


def columns(W, H, n, count, direction):
    """``count`` vertical columns, each filling top to bottom (bottom to
    top for ``up``), staggered across the frame in the travel direction."""
    count = max(2, int(count or 6))
    xs, ys = grid(W, H)
    e = edge_px(W, H)
    col = np.minimum((xs / W * count).astype(np.int32), count - 1)
    order = col if direction in ("right", "down", "up", None) else \
        (count - 1 - col)
    stagger = 0.45
    span = 1.0 - stagger
    out = []
    for j in range(n):
        p = progress(j, n)
        local = np.clip((p - order * (stagger / max(1, count - 1))) / span,
                        0.0, 1.0)
        local = local * local * (3 - 2 * local)          # smoothstep
        if direction == "up":
            edge_at = H + e - local * (H + 2 * e)
            cov = half_plane(ys, edge_at, e, below=False)
        else:
            edge_at = -e + local * (H + 2 * e)
            cov = half_plane(ys, edge_at, e)
        out.append(to_u8(cov))
    return out


def bands(W, H, n, count, direction):
    """``count`` horizontal bands, alternate bands wiping in from opposite
    sides, staggered top to bottom (bottom to top for ``up``)."""
    count = max(2, int(count or 8))
    xs, ys = grid(W, H)
    e = edge_px(W, H)
    band = np.minimum((ys / H * count).astype(np.int32), count - 1)
    order = (count - 1 - band) if direction == "up" else band
    stagger = 0.4
    span = 1.0 - stagger
    out = []
    for j in range(n):
        p = progress(j, n)
        local = np.clip((p - order * (stagger / max(1, count - 1))) / span,
                        0.0, 1.0)
        local = local * local * (3 - 2 * local)
        from_left = (band % 2 == 0) if direction != "left" else \
            (band % 2 == 1)
        a = half_plane(xs, -e + local * (W + 2 * e), e)
        b = half_plane(xs, W + e - local * (W + 2 * e), e, below=False)
        out.append(to_u8(np.where(from_left, a, b)))
    return out


def staircase(W, H, n, direction):
    """A diagonal wipe in square steps: a grid of cells revealed corner to
    corner, each cell easing in (a stepped staircase edge)."""
    cols = 8
    rows = max(2, int(round(cols * H / float(W))))
    xs, ys = grid(W, H)
    ci = np.minimum((xs / W * cols).astype(np.int32), cols - 1)
    ri = np.minimum((ys / H * rows).astype(np.int32), rows - 1)
    if direction == "left":
        ci = cols - 1 - ci
    elif direction == "up":
        ri = rows - 1 - ri
    rank = (ci + ri).astype(np.float32) / float(cols + rows - 2)
    spread = 0.62
    out = []
    for j in range(n):
        p = progress(j, n)
        local = np.clip((p - rank * spread) / (1.0 - spread), 0.0, 1.0)
        out.append(to_u8(local * local * (3 - 2 * local)))
    return out


def box_steps(W, H, n, count, centre):
    """(reveal masks, flash masks): the incoming shot opens inside a box
    centred on ``centre`` that grows in ``count`` steps to the full frame
    (ref 14@15.30–16.07); each step lands with its new box filled white for
    a frame, decaying over two (the flash masks)."""
    from fx_schemas.accents import box_step_frames, box_step_shares
    shares = box_step_shares(count)
    lands = box_step_frames(len(shares), n)
    cx, cy = centre
    boxes = []
    for s in shares:
        bw, bh = math.sqrt(s) * W, math.sqrt(s) * H
        x0 = min(max(0.0, cx - bw / 2), W - bw)
        y0 = min(max(0.0, cy - bh / 2), H - bh)
        boxes.append((x0, y0, x0 + bw, y0 + bh))
    reveal, flash = [], []
    decay = (1.0, 0.55, 0.22)
    for j in range(n):
        k = max([i for i, f in enumerate(lands) if f <= j], default=-1)
        if k < 0:
            reveal.append(np.zeros((H, W), np.uint8))
        else:
            reveal.append(to_u8(box(W, H, *boxes[k], edge=1.0)))
        fl = np.zeros((H, W), np.float32)
        if k >= 0 and j - lands[k] < len(decay):
            fl = box(W, H, *boxes[k], edge=1.0) * decay[j - lands[k]]
        flash.append(to_u8(fl))
    return reveal, flash, lands


def stretch_band(W, H, n, h, line, vertical=True):
    """Pixel stretch: the share of the frame covered by the streak of one
    line (row ``line`` px for vertical streaks), growing from the line to
    the whole frame on the cut (frames < h), receding after it."""
    xs, ys = grid(W, H)
    coord = ys if vertical else xs
    size = H if vertical else W
    e = edge_px(W, H, 0.006)
    out = []
    for j in range(n):
        if j < h:
            s = ease_in_cubic((j + 1) / float(h))
        else:
            s = 1.0 - ease_out_cubic((j - h + 1) / float(max(1, n - h)))
        lo = line - s * (line + e)
        hi = line + s * (size - line + e)
        cov = np.minimum(half_plane(coord, lo, e, below=False),
                         half_plane(coord, hi, e))
        out.append(to_u8(cov))
    return out


# ── faults ──────────────────────────────────────────────────────────────

def precompute_guard():
    """The ``oom`` fault takes the path of a precompute the kernel killed
    (INTERFACES.md §6.2): a MemoryError fxgraph.prepare turns into the
    item's ``omit`` row (a device degrades, never fails a render)."""
    import faultinject
    if faultinject.fault("oom:fx_precompute"):
        raise MemoryError("fx precompute was OOM-killed")


# ── frame clocks ────────────────────────────────────────────────────────

def pts(fps3):
    """``setpts`` that stamps frame N on the program grid. ROUNDED: setpts
    truncates its result, and N / fps / TB in floating point lands a hair
    under N when the stream's time base is exactly 1/fps (an image input
    read with -framerate): measured, frames 57-59 at 25 fps took the pts of
    their predecessors and an overlay showed every later frame one early."""
    return f"setpts=round(N/{float(fps3):.3f}/TB)"


def repad(f0, fps, alpha=False):
    """fxprims.repad with the rounded stamp (see ``pts``): a window branch
    re-padded to the program clock from frame ``f0``."""
    import fxprims
    color = "black@0" if alpha else "black"
    lead = f"start={int(f0)}:" if f0 > 0 else ""
    return (f",tpad={lead}stop=1:start_mode=add:stop_mode=add:color={color},"
            f"{pts(fxprims.fps3(fps))}")


# ── files ───────────────────────────────────────────────────────────────

def _safe_id(value):
    """A file-name-safe digest of an fx id (ids are free text)."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _link(src, dst):
    try:
        if os.path.exists(dst):
            os.remove(dst)
        os.link(src, dst)
    except OSError:
        import shutil
        shutil.copyfile(src, dst)


def write_sequence_at(dirpath, frames):
    """Write uint8 gray frames as ``dirpath``/f_%05d.png (removing any
    later frames a previous write left), returning the image2 pattern.
    For inputs registered in ``prepare`` and finalized at emit (a junction
    learns its exact window at emit): ffmpeg opens the files only when the
    graph runs, after every compiler has emitted."""
    from PIL import Image
    os.makedirs(dirpath, exist_ok=True)
    seen = {}
    for i, f in enumerate(frames):
        path = os.path.join(dirpath, f"f_{i:05d}.png")
        fk = hashlib.sha256(f.tobytes()).hexdigest()
        if fk in seen:
            _link(seen[fk], path)
            continue
        Image.fromarray(f, "L").save(path, compress_level=PNG_LEVEL)
        seen[fk] = path
    i = len(frames)
    while os.path.exists(os.path.join(dirpath, f"f_{i:05d}.png")):
        os.remove(os.path.join(dirpath, f"f_{i:05d}.png"))
        i += 1
    return os.path.join(dirpath, "f_%05d.png")


def write_still(path, arr):
    """One uint8 gray (or uint16 for remap maps) still at ``path``."""
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if arr.dtype == np.uint16:
        Image.fromarray(arr.astype("<u2")).save(path)
    else:
        Image.fromarray(arr, "L").save(path, compress_level=PNG_LEVEL)
    return path


def sequence_input(pattern, fps3):
    """ffmpeg input args for a mask sequence. Masks enter as INPUTS, never
    through the ``movie`` source: a movie-read PNG declares full range and
    the colour-range negotiation then pulled the whole program to pc
    (yuvj420p; every frame changed — measured)."""
    return ["-framerate", f"{float(fps3):.3f}", "-i", str(pattern)]


def still_input(path):
    return ["-i", str(path)]


def seq_chain(label, fps3, n, fmt="gray"):
    """In-graph tail of a sequence input: exactly ``n`` frames on the
    window clock."""
    return (f"[{label}]format={fmt},{pts(fps3)},trim=end_frame={int(n)}")


def still_chain(label, fps3, n, fmt="gray"):
    """One still input held ``n`` frames (decoded once; ``loop`` repeats
    the frame)."""
    return (f"[{label}]format={fmt},loop=loop=-1:size=1,{pts(fps3)},"
            f"trim=end_frame={int(n)}")


# ── per-frame strength (no file: an expression on a 2x2 frame) ──────────

def piecewise_expr(values, var="N", scale=255.0, clamp=True):
    """A compact expression over frame index ``var``: ``values`` maps a
    frame to a value (0..1 strengths scaled by ``scale``, or with
    ``clamp=False`` any non-negative number; missing frames are 0). Runs of
    equal value collapse to one ``between`` term."""
    items = sorted((int(k), float(v)) for k, v in values.items() if v > 0)
    terms, run = [], None
    for k, v in items:
        q = int(round((min(1.0, v) if clamp else v) * scale))
        if q <= 0:
            continue
        if run and run[1] == k - 1 and run[2] == q:
            run[1] = k
        else:
            if run:
                terms.append(run)
            run = [k, k, q]
    if run:
        terms.append(run)
    if not terms:
        return "0"
    out = []
    for a, b, q in terms:
        out.append(f"eq({var},{a})*{q}" if a == b else
                   f"between({var},{a},{b})*{q}")
    return "+".join(out)


def strength_chain(values, W, H, fps3, n):
    """A W x H gray stream of ``n`` frames whose every pixel is the
    frame's strength (0..255): a 2 x 2 frame evaluated per frame (four
    pixels: free) and scaled up."""
    expr = piecewise_expr(values)
    return (f"color=c=black:s=2x2:r={fps3:.3f},trim=end_frame={int(n)},"
            f"format=gray,geq=lum='{expr}',"
            f"scale={int(W)}:{int(H)}:flags=neighbor,{pts(fps3)}")


# ── render context ──────────────────────────────────────────────────────

_ENV = "_w1d_env"


def render_env(plan):
    """{"edl", "src_path", "asset_locals", "index"} for this render, each
    possibly None. From the plan when it carries them (``plan.edl``,
    ``plan.media`` = {src_path, asset_locals, index, fetch_asset}), else
    from the calling render (see the module doc). Cached per plan."""
    if plan is None:
        return {"edl": None, "src_path": None, "asset_locals": None,
                "index": None, "fetch_asset": None}
    hit = plan.__dict__.get(_ENV)
    if hit is not None:
        return hit
    env = {"edl": getattr(plan, "edl", None), "src_path": None,
           "asset_locals": None, "index": None, "fetch_asset": None}
    media = getattr(plan, "media", None)
    if isinstance(media, dict):
        for k in ("src_path", "asset_locals", "index", "fetch_asset"):
            env[k] = media.get(k)
    if env["edl"] is None or env["src_path"] is None:
        try:
            _from_stack(env)
        except Exception:  # noqa: BLE001 — context is best effort
            pass
    plan.__dict__[_ENV] = env
    return env


def _from_stack(env):
    """Read the pinned parameters of the render call this prepare runs
    under: ``fxgraph.prepare(edl, ...)`` (INTERFACES.md §4.4) and
    ``renderer.render_edl(edl_dict, index, src_path, ...,
    asset_locals=...)``."""
    f = sys._getframe(1)
    prep = getattr(sys.modules.get("fxgraph"), "prepare", None)
    rend = getattr(sys.modules.get("renderer"), "render_edl", None)
    prep_code = getattr(prep, "__code__", None)
    rend_code = getattr(rend, "__code__", None)
    depth = 0
    while f is not None and depth < 80:
        code = f.f_code
        if env["edl"] is None and code is prep_code:
            edl = f.f_locals.get("edl")
            if isinstance(edl, dict) and edl:
                env["edl"] = edl
        if code is rend_code:
            loc = f.f_locals
            if env["edl"] is None and isinstance(loc.get("edl_dict"), dict):
                env["edl"] = loc.get("edl_dict")
            if env["src_path"] is None and loc.get("src_path") \
                    and os.path.exists(str(loc.get("src_path"))):
                env["src_path"] = str(loc.get("src_path"))
            if env["asset_locals"] is None:
                env["asset_locals"] = loc.get("asset_locals") or {}
            if env["index"] is None:
                env["index"] = loc.get("index")
            break
        f = f.f_back
        depth += 1


def asset_local(plan, key):
    """A local path for project media ``key``, or None (never raises):
    the plan's own fetcher, the render's asset_locals, then the renderer's
    asset cache / storage read (``renderer._render_asset_source``)."""
    env = render_env(plan)
    try:
        fetch = env.get("fetch_asset")
        if callable(fetch):
            got = fetch(key)
            if got and os.path.exists(str(got)):
                return str(got)
        known = (env.get("asset_locals") or {}).get(key)
        if known and os.path.exists(str(known)):
            return str(known)
        import renderer
        got = renderer._render_asset_source(key, "fx", 0, plan.workdir,
                                            env.get("asset_locals"))
        if got and os.path.exists(str(got)):
            return str(got)
    except Exception:  # noqa: BLE001
        return None
    return None


# ── stills ──────────────────────────────────────────────────────────────

def ffmpeg_bin():
    return os.environ.get("FFMPEG_BIN") or "ffmpeg"


def grab_frame(path, t, max_side=None):
    """One RGB frame (PIL) of a video or image at second ``t``, or None."""
    import subprocess
    from PIL import Image
    try:
        ext = os.path.splitext(str(path))[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
                   ".tif", ".tiff"):
            im = Image.open(path)
            im.load()
            return im.convert("RGB")
        vf = "format=rgb24"
        if max_side:
            vf = (f"scale='if(gt(iw,ih),min({int(max_side)},iw),-2)':"
                  f"'if(gt(iw,ih),-2,min({int(max_side)},ih))',{vf}")
        r = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss",
                            f"{max(0.0, float(t)):.3f}", "-i", str(path),
                            "-frames:v", "1", "-vf", vf, "-f", "image2pipe",
                            "-c:v", "png", "-"], capture_output=True,
                           timeout=60, check=False)
        if r.returncode != 0 or not r.stdout:
            return None
        import io
        im = Image.open(io.BytesIO(r.stdout))
        im.load()
        return im.convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def fit_image(im, W, H, fit="cover", ref_size=None):
    """``im`` brought to exactly W x H. cover: scaled to fill, centre
    crop; contain: scaled to fit on black; register: scaled exactly as an
    image of ``ref_size`` would be for cover (every image of a variant set
    lands on the same pixels)."""
    from PIL import Image
    W, H = int(W), int(H)
    im = im.convert("RGB")
    if fit == "register" and ref_size and tuple(im.size) != tuple(ref_size):
        im = im.resize((int(ref_size[0]), int(ref_size[1])),
                       Image.Resampling.LANCZOS)
    iw, ih = im.size
    if fit == "contain":
        k = min(W / iw, H / ih)
        nw, nh = max(1, int(round(iw * k))), max(1, int(round(ih * k)))
        canvas = Image.new("RGB", (W, H), (0, 0, 0))
        canvas.paste(im.resize((nw, nh), Image.Resampling.LANCZOS),
                     ((W - nw) // 2, (H - nh) // 2))
        return canvas
    k = max(W / iw, H / ih)
    nw, nh = max(W, int(math.ceil(iw * k))), max(H, int(math.ceil(ih * k)))
    big = im.resize((nw, nh), Image.Resampling.LANCZOS)
    x0, y0 = (nw - W) // 2, (nh - H) // 2
    return big.crop((x0, y0, x0 + W, y0 + H))


def dominant_hue(im, sat_min=0.25, val_min=0.18):
    """The dominant saturated hue of an RGB image as a vivid '#rrggbb'
    (ref 01@3.833: the outgoing shot's red floods the incoming one), or
    None when the picture is achromatic."""
    import colorsys
    a = np.asarray(im.convert("RGB").resize((96, 96)), np.float32) / 255.0
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    keep = (sat >= sat_min) & (mx >= val_min)
    if keep.sum() < 20:
        return None
    r, g, b = a[..., 0][keep], a[..., 1][keep], a[..., 2][keep]
    hues = np.array([colorsys.rgb_to_hsv(x, y, z)[0]
                     for x, y, z in zip(r, g, b)])
    w = sat[keep] * mx[keep]
    hist, edges = np.histogram(hues, bins=36, range=(0, 1), weights=w)
    k = int(np.argmax(hist))
    h = (edges[k] + edges[k + 1]) / 2.0
    rr, gg, bb = colorsys.hsv_to_rgb(h, 0.9, 1.0)
    return "#{:02x}{:02x}{:02x}".format(int(rr * 255), int(gg * 255),
                                        int(bb * 255))


def hex_rgb(colour, accent=None, default="#ffffff"):
    """(r, g, b) 0..255 for an FxColour (``accent`` resolves to the Look's
    accent when known)."""
    c = str(colour or default).lower()
    if c == "white":
        c = "#ffffff"
    elif c == "black":
        c = "#000000"
    elif c == "accent":
        c = str(accent or default).lower()
    if not (c.startswith("#") and len(c) == 7):
        c = default
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def look_accent(edl):
    try:
        import motion_look
        return motion_look.short_look(edl or {}).get("accent")
    except Exception:  # noqa: BLE001
        return None


# ── picture cards (regions.card_prepare / card_pad) ─────────────────────

def card_spec_at(edl, t0, t1=None):
    """The picture card on screen over program [t0, t1) — the one on
    screen at the window's middle — or None."""
    try:
        import card_geom
    except Exception:  # noqa: BLE001
        return None
    mid = float(t0) if t1 is None else (float(t0) + float(t1)) / 2.0
    return card_geom.card_at(edl or {}, mid)


def card_mask(W, H, spec):
    """255 inside the card's rounded window(s), drawn exactly as the card's
    own plate draws its hole (picture_cards._geometry_masks: 3x
    supersampled rounded rectangles, LANCZOS down), so a card-clipped hit
    fills the footage window and not one pixel of the backdrop."""
    import picture_cards
    from PIL import ImageOps
    hole = picture_cards._geometry_masks(int(W), int(H), spec)[0]
    return ImageOps.invert(hole)


def card_digest(W, H, spec):
    import json
    keys = ("box", "radius", "panels")
    d = {k: spec.get(k) for k in keys}
    return hashlib.sha256(json.dumps([int(W), int(H), d], sort_keys=True,
                                     default=str).encode()).hexdigest()[:16]


def card_mask_path(workdir, W, H, spec):
    """The card mask PNG in ``workdir`` (written once per geometry)."""
    path = os.path.join(workdir, f"w1d_card_{card_digest(W, H, spec)}.png")
    if not os.path.exists(path):
        card_mask(W, H, spec).save(path, compress_level=PNG_LEVEL)
    return path
