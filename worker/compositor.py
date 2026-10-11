"""Footage-aware compositor (track 1C; ROADMAP Wave 1 §1C,
worker/devices/INTERFACES_W1.md §1.3-§1.5, §3.3).

Three things live here:

1. **Region effects** (``REGION``, the compiler of the ``region`` fx type):
   one effect chain — negate, solarise (optionally a colour duotone), mono,
   posterize, threshold, blur, pixelate, exposure, tint, or a displacement
   material (ribbed glass, refraction, heat) — applied inside a rect,
   ellipse, polygon (static or keyframed), a glyph of a motion graphic, a
   face or the picture card. The processed copy takes the region mask as its
   alpha and is laid back over the untouched picture: outside the region and
   the window every pixel is the picture's own (``region_locality`` = 0).
   Materials displace through maps built ONCE in numpy (displacement.py);
   nothing here is a per-pixel expression (ROADMAP §2 benchmarks).

2. **Glyph masks and boxes** (``glyph_mask``, ``glyph_box``): the alpha of a
   motion graphic's own rendered clip, placed where the render draws it, in
   program space (mask label ``glyph:<motion id>``). ``prepare_composites``
   (called from ``motion_layer.prepare_inputs``, which renders the clips
   BEFORE fxgraph.prepare) registers every clip that an fx item names, so a
   region effect, an accent region or a letter-window junction (1D) can
   draw inside the letters.

3. **Composites on motion graphics** (``MotionItem.composite``):
   ``prepare_composites`` resolves what each composite needs — a footage
   fill (a project asset, a colour, the program itself a moment away, the
   shot across the item's cut), the clipping region, the ``auto`` blend
   (measured on the plate under the graphic), the ghost pass's stroke-only
   clip (motion/lib/paint.js) — and ``emit`` / ``emit_ghost`` compile it
   where ``motion_layer.overlay_clip`` / ``composite_behind`` would have laid
   the clip. An item with ``composite`` None never reaches this module: its
   filters are exactly today's (tests pin them byte for byte).

Timing. Every composite branch is stamped exactly like the legacy clip
overlay (frame k of the item at ``start + k / fps``, the overlay enabled on
``gte(t,start)*lt(t,end)``) and padded from t = 0 with transparent frames
(``_stamp``), so a branch derived from the picture never makes ``overlay``
buffer the program up to the item (fxprims' re-pad rule), and the composite
lands on exactly the frames the plain graphic would.

Degrade, never fail. Anything a composite cannot build (a fill whose media
is missing, a region the render cannot see, an auto blend with no plate)
drops that ingredient with a logged note and keeps the graphic; a region
effect whose region cannot be built is omitted with its reason.
"""

import dataclasses
import json
import math
import os
import subprocess
import threading

import faultinject
import fxgraph
import fxprims

# ── constants ────────────────────────────────────────────────────────────

# Filters this module's compilers emit (fxgraph checks the lane has them).
FILTERS = ("split", "trim", "setpts", "settb", "tpad", "overlay", "format",
           "crop",
           "alphamerge", "alphaextract", "negate", "hue", "lutrgb", "lutyuv",
           "lut", "colorchannelmixer", "gblur", "scale", "pad", "nullsink",
           "null")
# Only the displacement materials need these: a lane without them draws a
# material as its substitute (the region_soften blur), never omits every
# region effect (fxgraph omits a whole compiler whose FILTERS are missing).
MATERIAL_FILTERS = ("displace", "convolution", "blend")
# Region-effect defaults at a 1080-px-high frame (scaled by H and ``scale``).
BLUR_SIGMA_FRAC = 0.012
PIXEL_BLOCK_FRAC = 0.028
# Default Look accent when the short has none (hook_title's default).
ACCENT_FALLBACK = "#FFD84D"
# ffmpeg blend mode for each composite blend name.
BLEND_FFMPEG = {"screen": "screen", "add": "addition", "multiply": "multiply",
                "overlay": "overlay", "softlight": "softlight",
                "difference": "difference", "exclusion": "exclusion",
                "lighten": "lighten", "darken": "darken"}
# ``auto``: plate statistics under the graphic's box (plate.py grids, 0-255
# mean luma and per-cell standard deviation).
AUTO_BUSY_DETAIL = 22.0     # mean in-cell luma std above which a plate is busy
AUTO_BUSY_SPREAD = 38.0     # or: std of the cell means across the box
AUTO_DARK_LUMA = 0.32       # mean relative luminance below which it is dark
AUTO_MIN_LC = 45.0          # the result must read (APCA |Lc|) ...
AUTO_MIN_SHARE = 0.9        # ... over this share of the box's cells
# How long the program is held for a fill frozen across a cut (next /
# previous on the main footage) or a program-offset fill.
FREEZE_MAX_S = 3.0
# Light templates whose spec carries a composite default (screen / add
# light instead of normal-alpha paint): applied when a write asks for it.
LIGHT_TEMPLATES = ("flash_transition", "light_leak", "film_burn")


def fps3(fps):
    return fxprims.fps3(fps)


def _log(msg):
    print(f"[compositor] {msg}", flush=True)


# ── the legacy clip clock ────────────────────────────────────────────────

def pad_frames(s, fps):
    """Frames of a branch stamped like the legacy clip overlay that come
    before program second ``s``: the frames k/fps with k/fps < s."""
    return max(0, int(math.ceil(float(s) * float(fps) - 1e-9)))


def _stamp(s, fps, alpha=True):
    """Filter tail that pads a window-local branch (frame k = the item's
    k-th frame) from t = 0 and stamps it on the legacy clip clock: pad frame
    N at N/fps, item frame k at s + k/fps — exactly where
    ``setpts=PTS+s/TB`` put the plain clip's frames. The pads exist before
    the picture reaches the item, so a branch derived from the picture never
    holds the program up (fxprims.repad's rule)."""
    P = pad_frames(s, fps)
    f = float(fps)
    color = "black@0" if alpha else "black"
    lead = (f"tpad=start={P}:start_mode=add:color={color}," if P else "")
    # a microsecond time base first: a branch that went through a colour
    # source or an overlay may carry 1/fps, where s + k/fps rounds to the
    # wrong frame (0.3 s at 25 fps is frame 7.5)
    return (f"{lead}settb=AVTB,setpts='if(lt(N,{P}),N/{f:.6f},"
            f"{float(s):.4f}+(N-{P})/{f:.6f})/TB'")


def _fit(w, h, alpha=True):
    """Filter text that makes a clip's frames exactly w x h: the engine's
    recorded box and the captured frames can differ by a pixel at
    non-integer design scales (16:9 at 1920 wide), and every composite
    pairs the clip with crops of exactly its box."""
    c = "black@0" if alpha else "black"
    return (f"crop=min(iw\\,{int(w)}):min(ih\\,{int(h)}):0:0,"
            f"pad={int(w)}:{int(h)}:0:0:color={c}")


def _enable(s, e):
    return f"gte(t,{float(s):.4f})*lt(t,{float(e):.4f})"


def window_frames(s, e, fps):
    """How many frames the legacy overlay shows an item for: frames of the
    program clock with gte(t,s)*lt(t,e)."""
    f = fps3(fps)
    f0 = int(math.ceil(round(float(s), 4) * f - 1e-9))
    f1 = int(math.ceil(round(float(e), 4) * f - 1e-9))
    return max(1, f1 - f0)


# ── per-render registry (prepare_inputs -> fxgraph.prepare -> emit) ─────
# motion_layer.prepare_inputs renders the clips before fxgraph.prepare runs;
# the plan and the motion inputs share the render's workdir, which keys
# what the glyph providers need. Bounded; never part of the EDL.
_REGISTRY = {}
_REG_LOCK = threading.Lock()
_REG_MAX = 32


def _rkey(workdir):
    return os.path.abspath(str(workdir or ""))


def build_scope(ctx):
    """Reset per-build pad bookkeeping when ``ctx`` belongs to a NEW
    filtergraph build. The renderer builds the graph more than once per
    render (the dialogue-level measuring pass, the audio-only rebuilds,
    then the real one) from ONE plan, so "one pad per input" bookkeeping
    kept on the plan must start afresh with each build's ``parts`` list:
    the static-region set (regions.pad), the keyed-region set and the glyph
    inputs handed out. Returns True on a new build."""
    plan = getattr(ctx, "plan", None)
    if plan is None:
        return False
    if plan.__dict__.get("_w1c_build") is ctx.parts:
        return False
    plan.__dict__["_w1c_build"] = ctx.parts
    plan.__dict__["_regions_spent"] = set()
    plan.__dict__[_KEYED_SPENT] = set()
    with _REG_LOCK:
        for g in (_REGISTRY.get(_rkey(getattr(plan, "workdir", None)))
                  or {}).values():
            g["spent"] = 0
    return True


def register_glyphs(workdir, entries):
    """``entries``: {motion id: {"inputs": [input idx, ...], "x", "y",
    "w", "h", "start", "end", "W", "H", "fps"}} for this render."""
    with _REG_LOCK:
        if len(_REGISTRY) >= _REG_MAX:
            _REGISTRY.clear()
        _REGISTRY[_rkey(workdir)] = {k: dict(v, spent=0)
                                     for k, v in (entries or {}).items()}


def _glyph(plan, motion_id):
    with _REG_LOCK:
        return (_REGISTRY.get(_rkey(getattr(plan, "workdir", None))) or {}) \
            .get(str(motion_id))


def glyph_box(plan, motion_id, t=None):
    """[x0, y0, x1, y1] frame fractions of where motion item ``motion_id``
    draws on this render (its clip's box), or None (not drawn here, or
    ``t`` outside its window). INTERFACES_W1.md §1.5."""
    g = _glyph(plan, motion_id)
    if not g or not g.get("W") or not g.get("H"):
        return None
    if t is not None and not (float(g["start"]) - 1e-6 <= float(t)
                              < float(g["end"]) + 1e-6):
        return None
    W, H = float(g["W"]), float(g["H"])
    return [round(g["x"] / W, 5), round(g["y"] / H, 5),
            round((g["x"] + g["w"]) / W, 5), round((g["y"] + g["h"]) / H, 5)]


def glyph_bbox_px(plan, motion_id):
    g = _glyph(plan, motion_id)
    if not g:
        return None
    return fxprims.even_box((g["x"], g["y"], g["w"], g["h"]), g["W"], g["H"])


def glyph_mask(plan, name, space, ctx):
    """Mask provider for ``glyph:<motion id>`` (fxgraph.MASK_PEERS): that
    motion item's clip alpha, placed where the render draws it, as a fresh
    gray PROGRAM mask (W x H, the full program, black outside the item's
    window), or None. Each call consumes one of the clip inputs
    prepare_composites opened for it (one per fx reference). The glyph is
    drawn on the screen (or, behind the subject, before the camera); both
    spaces read the same mask."""
    if ctx is None or ":" not in str(name):
        return None
    mid = str(name).split(":", 1)[1]
    build_scope(ctx)
    with _REG_LOCK:
        reg = _REGISTRY.get(_rkey(getattr(plan, "workdir", None))) or {}
        g = reg.get(mid)
        if not g or g["spent"] >= len(g.get("inputs") or []):
            return None
        idx = g["inputs"][g["spent"]]
        g["spent"] += 1
    W, H = int(g["W"]), int(g["H"])
    fps = float(g.get("fps") or plan.fps)
    s, e = float(g["start"]), float(g["end"])
    u = ctx.uid("gm")
    total = float(getattr(plan, "program_s", 0.0) or e) + 1.0
    # the clip alpha on the legacy clip clock (exactly where the overlay
    # draws it), resampled onto the program grid the way the overlay reads
    # it (the latest clip frame at or before each program frame), black
    # outside the item's window
    ctx.parts.append(
        f"[{idx}:v]setpts=PTS-STARTPTS,format=rgba,"
        f"{_fit(g['w'], g['h'])},alphaextract,"
        f"format=gray,tpad=stop_mode=clone:stop_duration={2.0 / fps:.4f},"
        f"pad={W}:{H}:{int(g['x'])}:{int(g['y'])}:black,"
        f"{_stamp(s, fps, alpha=False)},"
        f"tpad=stop_mode=add:stop_duration={max(0.0, total - s):.4f}"
        f":color=black,fps={fps3(fps):.3f}:round=up,"
        f"trim=end={total:.4f},"
        f"lut=c0=0:enable='not({_enable(s, e)})',format=gray[{u}]")
    return u


# ── keyframed regions (regions.keyed_prepare / keyed_pad) ────────────────

def _ease(name, p):
    p = min(1.0, max(0.0, float(p)))
    if name == "hold":
        return 0.0
    if name == "in":
        return p * p * p
    if name == "out":
        return 1.0 - (1.0 - p) ** 3
    if name == "in_out":
        return 4 * p * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2
    return p


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _keys(region):
    out = []
    for k in _get(region, "keys") or []:
        out.append({"t": float(_get(k, "t")),
                    "points": [[float(a), float(b)] for a, b in
                               _get(k, "points")],
                    "ease": _get(k, "ease") or "linear"})
    return out


def keyed_points(region, t):
    """The region's points at ``t`` seconds into its item: held before the
    first key and after the last, eased between keys (a key's ``ease`` shapes
    the move to the NEXT key; ``hold`` steps). Keys with a different vertex
    count step instead of morphing."""
    keys = _keys(region)
    if not keys:
        return None
    if t <= keys[0]["t"]:
        return keys[0]["points"]
    for a, b in zip(keys, keys[1:]):
        if a["t"] <= t < b["t"]:
            if len(a["points"]) != len(b["points"]):
                return a["points"]
            p = _ease(a["ease"], (t - a["t"]) / max(1e-6, b["t"] - a["t"]))
            return [[pa[0] + (pb[0] - pa[0]) * p, pa[1] + (pb[1] - pa[1]) * p]
                    for pa, pb in zip(a["points"], b["points"])]
    return keys[-1]["points"]


def _draw(shape, pts, W, H, cw=None, ch=None, ox=0, oy=0):
    """PIL L image (cw x ch, default W x H) of one shape at frame-fraction
    points of a W x H frame, the canvas's origin at pixel (ox, oy)."""
    from PIL import Image, ImageDraw
    im = Image.new("L", (int(cw or W), int(ch or H)), 0)
    d = ImageDraw.Draw(im)
    if shape == "polygon":
        if len(pts) >= 3:
            d.polygon([(x * W - ox, y * H - oy) for x, y in pts], fill=255)
        return im
    (x0, y0), (x1, y1) = pts[0], pts[1]
    box = [min(x0, x1) * W - ox, min(y0, y1) * H - oy,
           max(x0, x1) * W - ox - 1, max(y0, y1) * H - oy - 1]
    if box[2] >= box[0] and box[3] >= box[1]:
        (d.ellipse if shape == "ellipse" else d.rectangle)(box, fill=255)
    return im


def keyed_bbox(region, W, H):
    """Even pixel box covering every key of a keyframed rect/ellipse/polygon
    (feather included), or None when it is inverted (it covers the frame)."""
    keys = _keys(region)
    if not keys or _get(region, "invert"):
        return None
    xs = [p[0] for k in keys for p in k["points"]]
    ys = [p[1] for k in keys for p in k["points"]]
    pad = float(_get(region, "feather_px") or 0.0) * 2.0 * H / 1080.0 + 2
    return fxprims.even_box((min(xs) * W - pad, min(ys) * H - pad,
                             (max(xs) - min(xs)) * W + 2 * pad,
                             (max(ys) - min(ys)) * H + 2 * pad), W, H)


def raster_keyed(region, W, H, fps, start, n, path):
    """Encode the keyframed region as a gray clip of ``n`` frames at
    ``fps`` (frame k at ``start + k / fps`` seconds; keys are relative to
    ``start``), W x H, feather and invert baked in. Lossless (ffv1).
    Frames that repeat are drawn once."""
    from PIL import Image, ImageFilter, ImageOps
    shape = _get(region, "shape")
    W, H = int(W), int(H)
    feather = float(_get(region, "feather_px") or 0.0) * H / 1080.0
    inv = bool(_get(region, "invert"))
    box = keyed_bbox(dict(region, invert=False) if isinstance(region, dict)
                     else {"keys": _get(region, "keys"),
                           "feather_px": _get(region, "feather_px")}, W, H)
    ox, oy, bw, bh = box if box else (0, 0, W, H)
    cmd = [_ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
           "gray", "-s", f"{W}x{H}", "-r", f"{fps3(fps):.3f}", "-i", "-",
           "-c:v", "ffv1", "-pix_fmt", "gray", path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    last_pts, last_bytes = None, None
    try:
        for k in range(int(n)):
            pts = keyed_points(region, k / float(fps))
            key = json.dumps([[round(a, 5), round(b, 5)] for a, b in pts])
            if key != last_pts:
                crop = _draw(shape, pts, W, H, bw, bh, ox, oy)
                if feather > 0:
                    crop = crop.filter(ImageFilter.GaussianBlur(feather))
                full = Image.new("L", (W, H), 0)
                full.paste(crop, (ox, oy))
                if inv:
                    full = ImageOps.invert(full)
                last_pts, last_bytes = key, full.tobytes()
            proc.stdin.write(last_bytes)
        proc.stdin.close()
        err = proc.stderr.read()
        if proc.wait() != 0:
            raise RuntimeError(f"keyed region encode failed: "
                               f"{err.decode(errors='replace')[-300:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    return path


_KEYED_SPENT = "_keyed_spent"


def keyed_prepare(plan, item, region, key):
    """regions.keyed_prepare: the keyframed region as a gray clip over the
    item's window, registered as an input. None, or (rung, reason)."""
    if not plan.workdir or not plan.W or not plan.H:
        return ("omit", "no workdir/geometry for the region mask")
    if faultinject.fault("oom:fx_precompute"):
        raise MemoryError("fx precompute was OOM-killed")
    if (item or {}).get("start") is None or (item or {}).get("end") is None:
        return ("omit", "a keyframed region needs its item's window")
    f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
    path = os.path.join(plan.workdir,
                        f"fxkeyed_{item.get('id')}_{key}.mkv")
    raster_keyed(region, plan.W, plan.H, plan.fps, float(item["start"]),
                 f1 - f0, path)
    plan.add_input(f"keyed:{item.get('id')}:{key}", ["-i", path])
    return None


def keyed_pad(ctx, item, region, key):
    """regions.keyed_pad: a fresh gray PROGRAM mask of the keyframed region
    (black outside the item's window), or None."""
    k = f"keyed:{item.get('id')}:{key}"
    build_scope(ctx)
    spent = ctx.plan.__dict__.setdefault(_KEYED_SPENT, set())
    label = ctx.input_label(k)
    if label is None or k in spent:
        return None
    spent.add(k)
    f0, f1 = fxprims.frame_span(item["start"], item["end"], ctx.fps)
    after = max(1, int(ctx.n_frames) - f1 + 1)
    out = ctx.uid("kr")
    lead = f"start={f0}:" if f0 > 0 else ""
    ctx.parts.append(
        f"[{label}]format=gray,trim=end_frame={f1 - f0},setpts=PTS-STARTPTS,"
        f"tpad={lead}stop={after}:start_mode=add:stop_mode=add:color=black,"
        f"setpts=N/{ctx.fps3:.3f}/TB[{out}]")
    return out


# ── region effects (the ``region`` fx type) ─────────────────────────────

def _hex_rgb(hexv):
    h = str(hexv).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


_LUMA = ("colorchannelmixer=rr=.2126:rg=.7152:rb=.0722:gr=.2126:gg=.7152"
         ":gb=.0722:br=.2126:bg=.7152:bb=.0722")


def _duotone(hexv):
    """lutrgb mapping a gray picture to black -> colour -> white."""
    terms = []
    for ch, c in zip("rgb", _hex_rgb(hexv)):
        terms.append(f"{ch}='if(lt(val,128),val*{c}/128,"
                     f"{c}+(val-128)*{255 - c}/127)'")
    return "lutrgb=" + ":".join(terms)


def resolve_colour(colour, edl=None):
    """'white' | 'black' | 'accent' | '#rrggbb' -> '#rrggbb'."""
    c = str(colour or "").strip().lower()
    if c == "white":
        return "#ffffff"
    if c == "black":
        return "#000000"
    if c == "accent":
        try:
            import motion_look
            acc = motion_look.short_look(edl or {}).get("accent")
            if acc:
                return str(acc).lower()
        except Exception:  # noqa: BLE001 — the Look never fails a render
            pass
        return ACCENT_FALLBACK.lower()
    return c if c.startswith("#") and len(c) == 7 else "#ffffff"


def effect_chain(item, w, h, H, edl=None):
    """The ffmpeg chain of a non-material region effect on a w x h branch
    (H: the frame height the defaults scale with)."""
    eff = item["effect"]
    scale = float(item.get("scale") or 1.0)
    if eff == "negate":
        return "negate"
    if eff == "mono":
        return "hue=s=0"
    if eff == "threshold":
        return "lutyuv=y='if(gt(val,125),235,16)':u=128:v=128"
    if eff == "posterize":
        q = "floor(val/64)*85"
        return f"format=gbrp,lutrgb=r='{q}':g='{q}':b='{q}'"
    if eff == "exposure":
        g = 2.0 ** (float(item.get("ev") or 0.0) / 2.2)
        q = f"clip(val*{g:.5f},0,255)"
        return f"format=gbrp,lutrgb=r='{q}':g='{q}':b='{q}'"
    if eff == "solarise":
        tri = "if(lt(val,128),2*val,2*(255-val))"
        if item.get("colour"):
            hexv = resolve_colour(item["colour"], edl)
            return (f"format=gbrp,{_LUMA},lutrgb=r='{tri}':g='{tri}':"
                    f"b='{tri}',{_duotone(hexv)}")
        return f"format=gbrp,lutrgb=r='{tri}':g='{tri}':b='{tri}'"
    if eff == "tint":
        hexv = resolve_colour(item["colour"], edl)
        return f"format=gbrp,{_LUMA},{_duotone(hexv)}"
    if eff == "blur":
        sigma = max(1.0, BLUR_SIGMA_FRAC * H * scale)
        if item.get("_reduced") and w >= 8 and h >= 8:
            # the reduced rung: the same blur computed at half resolution
            return (f"scale={max(2, int(w) // 2)}:{max(2, int(h) // 2)}:"
                    f"flags=area,gblur=sigma={sigma / 2:.2f},"
                    f"scale={int(w)}:{int(h)}:flags=bilinear")
        return f"gblur=sigma={sigma:.2f}"
    if eff == "pixelate":
        b = max(2, int(round(PIXEL_BLOCK_FRAC * H * scale)))
        sw, sh = max(1, int(round(w / b))), max(1, int(round(h / b)))
        return (f"scale={sw}:{sh}:flags=area,"
                f"scale={int(w)}:{int(h)}:flags=neighbor")
    raise ValueError(f"no chain for effect {eff!r}")


def _drawn_effect(detail, item):
    """The effect a region item actually draws on this render: its own, or
    the substitute rung's (a material on a lane without ``displace`` or over
    the render budget becomes the region_soften card's blur)."""
    if (detail or {}).get("rung") == "substitute" and \
            (detail or {}).get("substitute") == "region_soften":
        return "blur"
    if item.get("effect") in ("ribbed_glass", "refraction", "heat") and \
            fxgraph.missing_filters(MATERIAL_FILTERS):
        return "blur"
    return item.get("effect")


def _bevel_sigma(item, W, H):
    """Refraction's bevel blur (px of this frame): BEVEL_PX at a 1080-px
    short side, scaled with the frame and the item's ``scale``."""
    import displacement as dm
    return max(1.0, dm.BEVEL_PX * min(int(W), int(H)) / dm.REF_H
               * float(item.get("scale") or 1.0))


def _maps_key(item):
    return f"maps:{item.get('id')}"


class _Region:
    """Compiler for EDL.fx ``region`` items (fxgraph protocol)."""

    FILTERS = FILTERS
    # composites through region_fx / the same re-pad overlay: every
    # entering frame (and its layout tags) is kept
    KEEPS_FRAME_TAGS = True

    def _bbox(self, plan, item, W, H):
        box = self._shape_bbox(plan, item, W, H)
        if box and item.get("effect") == "refraction":
            # room for the bevel's blur, so a letter on the box's edge
            # still bends and catches light on that side
            p = int(math.ceil(3.0 * _bevel_sigma(item, W, H))) + 2
            box = fxprims.even_box((box[0] - p, box[1] - p, box[2] + 2 * p,
                                    box[3] + 2 * p), W, H)
        return box

    def _shape_bbox(self, plan, item, W, H):
        import regions
        region = item.get("region") or {}
        shape = _get(region, "shape")
        if regions.is_static(region):
            return regions.bbox(region, W, H)
        if shape in ("rect", "ellipse", "polygon"):
            return keyed_bbox(region, W, H)
        if shape == "glyph" and not _get(region, "invert"):
            return glyph_bbox_px(plan, _get(region, "ref"))
        return None

    def prepare(self, plan, item):
        import regions
        region = item.get("region") or {}
        if _get(region, "shape") == "glyph" and \
                _glyph(plan, _get(region, "ref")) is None:
            return ("omit", f"motion item {_get(region, 'ref')!r} draws "
                            "nothing on this render (its glyphs are absent)")
        got = regions.prepare(plan, item, region)
        if got:
            return got
        detail = plan.devices.get(item.get("id"))
        drawn = _drawn_effect(detail, item)
        if drawn in ("ribbed_glass", "refraction", "heat"):
            return self._prepare_maps(plan, item)
        if drawn != item.get("effect") and \
                (detail or {}).get("rung") != "substitute":
            return ("substitute", "this ffmpeg lacks displace -> a blur "
                    "(region_soften)")
        return None

    def _prepare_maps(self, plan, item):
        import displacement
        if faultinject.fault("oom:fx_precompute"):
            raise MemoryError("fx precompute was OOM-killed")
        W, H = int(plan.W or 0), int(plan.H or 0)
        if not plan.workdir or not W or not H:
            return ("omit", "no workdir/geometry for the material maps")
        box = self._bbox(plan, item, W, H) or (0, 0, W, H)
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        got = displacement.write_maps(
            item["effect"], box[2], box[3], plan.workdir,
            f"{item.get('id')}", scale=float(item.get("scale") or 1.0),
            strength=float(item.get("strength", 1.0)),
            seed=int(item.get("seed") or 0), fps=plan.fps, H=min(W, H))
        n = f1 - f0
        dur = (n + 2) / fps3(plan.fps)
        for part in ("x", "y", "shade"):
            path = got.get(part)
            if not path:
                continue
            if got.get("animated") and part != "shade":
                args = ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", path]
            else:
                args = ["-loop", "1", "-framerate", f"{fps3(plan.fps):.3f}",
                        "-t", f"{dur:.3f}", "-i", path]
            plan.add_input(f"{_maps_key(item)}:{part}", args)
        return None

    def cost_ms(self, plan, item):
        W, H = float(plan.W or 1080), float(plan.H or 1920)
        try:
            box = self._bbox(plan, item, int(W), int(H))
        except Exception:  # noqa: BLE001
            box = None
        mpx = (box[2] * box[3] if box else W * H) / 1e6
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        per = {"ribbed_glass": 9.0, "refraction": 9.0, "heat": 10.0,
               "blur": 6.0, "pixelate": 3.0}.get(item.get("effect"), 2.5)
        return (f1 - f0) * mpx * per

    def landing(self, plan, item):
        return [fxprims.frame_at(float(item["start"]), plan.fps)]

    def stage(self, ctx, items):
        import regions
        build_scope(ctx)
        label = ctx.label
        W, H = int(ctx.W), int(ctx.H)
        edl = getattr(ctx.plan, "edl", None)
        for it in items:
            region = it.get("region") or {}
            mask = regions.pad(ctx, it, region)
            if mask is None:
                ctx.note(it, "omit", f"its {_get(region, 'shape')} region is "
                         "not available on this render")
                continue
            strength = float(it.get("strength", 1.0))
            dev = ctx.device(it)
            eff = _drawn_effect(dev, it)
            if eff != it.get("effect"):
                it = dict(it, effect=eff, scale=None)
            if (dev or {}).get("rung") == "reduced":
                it = dict(it, _reduced=True)
            mat = eff in ("ribbed_glass", "refraction", "heat")
            if strength < 0.999 and not mat:
                m2 = ctx.uid("rs")
                ctx.parts.append(f"[{mask}]format=gray,"
                                 f"lut=c0='val*{strength:.4f}'[{m2}]")
                mask = m2
            box = self._bbox(ctx.plan, it, W, H)
            spans = [ctx.frames(it)]
            u = ctx.uid("rg")
            if mat:
                label = self._material(ctx, label, f"{u}o", mask, it, spans,
                                       box, u)
                continue
            w, h = (box[2], box[3]) if box else (W, H)
            op = effect_chain(it, w, h, H, edl)
            label = fxprims.region_fx(ctx.parts, label, f"{u}o", mask, op,
                                      spans, uid=u, fps=ctx.fps, bbox=box)
        return label

    def _bevel(self, parts, src, xlab, ylab, item, W, H, uid):
        """Refraction's bevel from the region's own (cropped) mask: blurred
        by BEVEL_PX, its central-difference gradient (``convolution``,
        bias 128) is added to the material's maps (``grainmerge``: map +
        bend - 128), so the picture bends inward at every edge of the
        shape — each letter of a word is its own piece of glass — and a
        diagonal gradient lights the edges facing up-left and darkens the
        far ones (laid on the displaced picture, again by grainmerge).
        Inside the shape away from its edges both are exactly neutral.
        Returns the new (xmap, ymap, sheen) labels."""
        import displacement as dm
        st = max(0.0, min(1.0, float(item.get("strength", 1.0))))
        sigma = _bevel_sigma(item, W, H)
        k = sigma / dm.BEVEL_PX
        # a gaussian edge's central difference peaks at ~203/sigma levels
        bend = dm.BEVEL_BEND_PX * k * st * sigma / 203.0
        shine = dm.BEVEL_SHINE * st * sigma / 203.0
        sh = dm.BEVEL_SHADOW
        parts.append(f"[{src}]gblur=sigma={sigma:.3f},"
                     f"split=3[{uid}b0][{uid}b1][{uid}b2]")
        parts.append(f"[{uid}b0]convolution=0m='0 0 0 -1 0 1 0 0 0':"
                     f"0rdiv={bend:.6f}:0bias=128,format=gbrp[{uid}bx]")
        parts.append(f"[{uid}b1]convolution=0m='0 -1 0 0 0 0 0 1 0':"
                     f"0rdiv={bend:.6f}:0bias=128,format=gbrp[{uid}by]")
        parts.append(f"[{uid}b2]convolution=0m='0 -1 0 -1 0 1 0 1 0':"
                     f"0rdiv={shine:.6f}:0bias=128,"
                     f"lut=c0='if(gte(val,128),val,128-(128-val)*{sh})',"
                     f"format=gbrp[{uid}bs]")
        parts.append(f"[{xlab}][{uid}bx]blend=all_mode=grainmerge[{uid}mx]")
        parts.append(f"[{ylab}][{uid}by]blend=all_mode=grainmerge[{uid}my]")
        return f"{uid}mx", f"{uid}my", f"{uid}bs"

    def _material(self, ctx, in_label, out_label, mask, item, spans, bbox,
                  uid):
        """fxprims.region_fx with the displacement material as its op
        (three inputs, so it cannot ride region_fx's single chain): the
        picture and the mask trimmed to the window hull and cropped to the
        box, the maps trimmed alike, ``displace`` (and the rib shading),
        alpha from the mask, re-padded and laid back over the untouched
        picture. Outside the mask and the window: the picture, bit for
        bit."""
        parts = ctx.parts
        spans = fxprims.merge_frame_spans(spans)
        if not spans:
            parts.append(f"[{mask}]nullsink")
            return in_label
        f0, f1 = spans[0][0], spans[-1][1]
        n = f1 - f0
        expr = fxprims.enable_expr(spans)
        trim = f"trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS"
        crop = (f",crop={bbox[2]}:{bbox[3]}:{bbox[0]}:{bbox[1]}"
                if bbox else "")
        ov = fxprims.composite_overlay(parts, in_label)
        alpha_fmt = ",format=yuva420p" if ov == fxprims.OVERLAY else ""
        maps = {}
        for part in ("x", "y", "shade"):
            lab = ctx.input_label(f"{_maps_key(item)}:{part}")
            if lab is None:
                continue
            maps[part] = f"{uid}{part}m"
            fmt = "rgba" if part == "shade" else "gbrp"
            parts.append(f"[{lab}]trim=end_frame={n},setpts=PTS-STARTPTS,"
                         f"format={fmt}[{maps[part]}]")
        if "x" not in maps or "y" not in maps:
            parts.append(f"[{mask}]nullsink")
            for lab in maps.values():
                parts.append(f"[{lab}]nullsink")
            ctx.note(item, "omit", "its displacement maps were not built")
            return in_label
        xlab, ylab, sheen = maps["x"], maps["y"], None
        if item.get("effect") == "refraction" and \
                float(item.get("strength", 1.0)) > 0.01:
            parts.append(f"[{mask}]{trim}{crop},format=gray,"
                         f"split[{uid}m][{uid}mb]")
            xlab, ylab, sheen = self._bevel(parts, f"{uid}mb", xlab, ylab,
                                            item, int(ctx.W), int(ctx.H),
                                            uid)
        else:
            parts.append(f"[{mask}]{trim}{crop},format=gray[{uid}m]")
        parts.append(f"[{in_label}]split[{uid}a][{uid}b]")
        parts.append(f"[{uid}b]{trim}{crop},format=gbrp[{uid}s]")
        parts.append(f"[{uid}s][{xlab}][{ylab}]displace="
                     f"edge=smear[{uid}d]")
        shaded = f"{uid}d"
        if "shade" in maps:
            parts.append(f"[{uid}d][{maps['shade']}]overlay=0:0:"
                         f"format=gbrp[{uid}sh]")
            shaded = f"{uid}sh"
        if sheen:
            parts.append(f"[{shaded}][{sheen}]blend=all_mode=grainmerge"
                         f"[{uid}bv]")
            shaded = f"{uid}bv"
        parts.append(f"[{shaded}]null{alpha_fmt}[{uid}p]")
        parts.append(f"[{uid}p][{uid}m]alphamerge"
                     f"{fxprims.repad(f0, ctx.fps, alpha=True)}[{uid}w]")
        xy = f"x={bbox[0]}:y={bbox[1]}:" if bbox else ""
        parts.append(f"[{uid}a][{uid}w]"
                     f"{ov.replace('overlay=', 'overlay=' + xy)}:"
                     f"enable='{expr}'[{out_label}]")
        return out_label


REGION = _Region()


# ── composites on motion graphics: prepare (motion_layer.prepare_inputs) ─

@dataclasses.dataclass
class CompositeSide:
    """What prepare_composites resolved for one composite item; carried on
    its RenderedClip (``clip.composite``) to emit()."""
    W: int
    H: int
    fps: float
    blend: str = "normal"
    fill: dict = None            # resolved fill (see _resolve_fill)
    field: str = None            # '#rrggbb'
    region: dict = None          # {"idx", "still"} | {"mask": label}
    stroke: dict = None          # {"idx", "x", "y", "w", "h"}
    ghost: dict = None           # {"opacity", "width_px"}
    notes: list = dataclasses.field(default_factory=list)


def has_composite(item):
    return isinstance(item, dict) and isinstance(item.get("composite"), dict)


def glyph_refs(edl):
    """{motion id: n} — how many fx references name each motion item's
    glyphs (a ``glyph`` region anywhere, or a ``glyph`` origin)."""
    out = {}

    def walk(v):
        if isinstance(v, dict):
            if v.get("shape") == "glyph" and v.get("ref"):
                out[str(v["ref"])] = out.get(str(v["ref"]), 0) + 1
            elif v.get("kind") == "glyph" and v.get("ref"):
                out[str(v["ref"])] = out.get(str(v["ref"]), 0) + 1
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk([i for i in (edl or {}).get("fx") or [] if isinstance(i, dict)])
    return out


def needed(edl, items):
    """Does this render need prepare_composites at all? (No: the motion
    layer's inputs are exactly today's.)"""
    return bool(glyph_refs(edl)) or any(has_composite(i) for i in items or [])


def _ffmpeg():
    return os.environ.get("FFMPEG_BIN") or "ffmpeg"


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif",
              ".tiff")


def _is_image(path):
    return os.path.splitext(str(path or ""))[1].lower() in IMAGE_EXTS


def _timeline(edl):
    import timeline
    keep = [(float(k[0]), float(k[1])) for k in (edl or {}).get("keep") or []
            if k is not None and len(k) >= 2]
    return timeline.Timeline(keep, (edl or {}).get("inserts") or [],
                             (edl or {}).get("speed"))


def _blocks(edl):
    """[(out_start, out_end, block)] in program order (timeline.
    program_blocks without its 2-decimal rounding)."""
    import timeline
    try:
        blocks = timeline.program_blocks(edl)
    except Exception:  # noqa: BLE001
        return []
    tl = _timeline(edl)
    # exact starts: cumulative lengths in the same block order
    out, acc = [], 0.0
    ins = sorted((edl or {}).get("inserts") or [],
                 key=timeline._ins_sort_key)
    for b in blocks:
        if b["kind"] == "footage":
            # find this segment's exact length
            L = None
            for i, (s_, e_) in enumerate(tl.segs):
                if abs(round(s_, 2) - b["src_start"]) < 0.006 and \
                        abs(round(e_, 2) - b["src_end"]) < 0.006:
                    L = tl.seg_out_len[i]
                    break
            if L is None:
                L = b["out_end"] - b["out_start"]
        else:
            it = next((x for x in ins if x.get("id") == b.get("id")), None)
            L = float((it or {}).get("duration_s")
                      or (b["out_end"] - b["out_start"]))
            b = dict(b, item=it)
        out.append((acc, acc + L, b))
        acc += L
    return out


def _cut_for(item, blocks, kind):
    """(cut second, block) the ``next`` / ``previous`` fill of ``item``
    names: next = the first cut after the item starts (up to a second past
    its end) and the block after it; previous = the last cut before the
    item ends (from a second before its start) and the block before it."""
    s, e = float(item["start"]), float(item["end"])
    if kind == "next":
        for a, _b, blk in blocks[1:]:
            if s + 1e-3 < a <= e + 1.0:
                return a, blk
        return None, None
    prev = None
    for k, (a, _b, blk) in enumerate(blocks[1:], start=1):
        if s - 1.0 <= a < e - 1e-3:
            prev = (a, blocks[k - 1][2])
    return prev if prev else (None, None)


def _cover_chain(W, H, crop=None, fit=None):
    """Scale a fill source to the frame: cover (centre crop) by default; an
    insert's own crop rect first; 'pad' / 'pad_blur' fits contain on black.
    The cover crops the source to the frame's aspect BEFORE scaling, so a
    16:9 or 4K source is scaled once, to W x H, never past it."""
    chain = []
    if crop and len(crop) == 4:
        x0, y0, x1, y1 = (float(v) for v in crop)
        chain.append(f"crop=iw*{x1 - x0:.5f}:ih*{y1 - y0:.5f}:"
                     f"iw*{x0:.5f}:ih*{y0:.5f}")
    if fit in ("pad", "pad_blur"):
        chain.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease")
        chain.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black")
    else:
        ar = float(W) / float(H)
        chain.append(f"crop='min(iw,ih*{ar:.6f})':'min(ih,iw/{ar:.6f})'")
        chain.append(f"scale={W}:{H}")
    chain.append("setsar=1")
    return ",".join(chain)


def _media_input(args, next_idx, path, *, start, dur, fps, loop=False):
    """Append one fill input (an image looped as frames, or a video from
    ``start`` seconds); returns its index and the next free one."""
    if _is_image(path):
        args += ["-loop", "1", "-framerate", f"{fps3(fps):.3f}",
                 "-t", f"{dur + 0.5:.3f}", "-i", path]
    else:
        pre = ["-stream_loop", "-1"] if loop else []
        args += pre + ["-ss", f"{max(0.0, start):.3f}",
                       "-t", f"{dur + 0.5:.3f}", "-i", path]
    return next_idx, next_idx + 1


def _resolve_fill(fill, item, edl, W, H, fps, args, next_idx, fetch_asset,
                  source_path, notes):
    """The fill as emit() draws it, opening any input it needs:

      {"kind": "media", "idx", "lead": s, "rate", "chain", "freeze_after"}
          frames of a file (an asset, an insert across the cut, the main
          video), window-local, ``lead`` seconds of its first frame frozen
          first (a cut with no handle)
      {"kind": "colour", "hex"}
      {"kind": "program", "offset"}       the picture, ``offset`` s away
      {"kind": "freeze_next", "cut"} / {"kind": "freeze_prev", "cut"}
          the program across a cut of the main footage, its edge frame held
    or None (dropped, with a note)."""
    kind = fill.get("kind")
    s, e = float(item["start"]), float(item["end"])
    span = e - s
    phase = float(item.get("phase_s") or 0.0)
    if kind == "colour":
        return {"kind": "colour",
                "hex": resolve_colour(fill.get("colour"), edl)}
    if kind == "program":
        return {"kind": "program", "offset": float(fill.get("offset_s") or 0)}
    if kind == "asset":
        key = fill.get("asset_key")
        try:
            local = fetch_asset(key) if fetch_asset else None
        except Exception as e_:  # noqa: BLE001
            local = None
            notes.append(f"fill {key} unavailable ({str(e_)[:120]})")
        if not local:
            notes.append(f"fill {key} could not be fetched")
            return None
        start = float(fill.get("offset_s") or 0.0) + phase
        idx, next_idx = _media_input(args, next_idx, local, start=start,
                                     dur=span, fps=fps,
                                     loop=bool(fill.get("loop")))
        return {"kind": "media", "idx": idx, "lead": 0.0, "rate": 1.0,
                "chain": _cover_chain(W, H), "_next": next_idx}
    if kind == "source":
        src_t = float(fill.get("src_t") or 0.0) + phase
        if source_path:
            idx, next_idx = _media_input(args, next_idx, source_path,
                                         start=src_t, dur=span, fps=fps)
            return {"kind": "media", "idx": idx, "lead": 0.0, "rate": 1.0,
                    "chain": _cover_chain(W, H), "_next": next_idx}
        try:
            p = _timeline(edl).src_to_out(src_t)
        except Exception:  # noqa: BLE001
            p = None
        if p is not None and abs(p - s) <= FREEZE_MAX_S:
            notes.append("source fill read from the program (the render "
                         "passed no source path)")
            return {"kind": "program", "offset": round(p - s, 4)}
        notes.append("a source fill needs the main video here (the render "
                     "passed no source path); drawn in its own colour")
        return None
    if kind in ("next", "previous"):
        blocks = _blocks(edl)
        cut, blk = _cut_for(item, blocks, kind)
        if cut is None:
            notes.append(f"no cut for its {kind} fill; drawn in its own "
                         "colour")
            return None
        if blk.get("kind") == "insert" and blk.get("asset_key") and \
                blk.get("media") != "image":
            key = blk["asset_key"]
            try:
                local = fetch_asset(key) if fetch_asset else None
            except Exception:  # noqa: BLE001
                local = None
            if local:
                rate = float(blk.get("rate") or 1.0)
                clip0 = float(blk.get("clip_start_s") or 0.0)
                if kind == "next":
                    # the insert's own clock continues seamlessly at the cut
                    m0 = clip0 + (s - cut) * rate
                else:
                    a_prev = next((a for a, _b, b in blocks if b is blk), cut)
                    m0 = clip0 + (s - a_prev) * rate
                lead = max(0.0, -m0) / rate
                idx, next_idx = _media_input(
                    args, next_idx, local, start=max(0.0, m0),
                    dur=span * rate, fps=fps)
                if lead > 1e-3:
                    notes.append(f"{kind} fill: {lead:.2f}s before the clip "
                                 "starts is its first frame held")
                item_ins = blk.get("item") or {}
                return {"kind": "media", "idx": idx, "lead": lead,
                        "rate": rate,
                        "chain": _cover_chain(W, H, item_ins.get("crop"),
                                              item_ins.get("fit")),
                        "_next": next_idx}
        if blk.get("kind") == "insert" and blk.get("media") == "image":
            key = blk["asset_key"]
            try:
                local = fetch_asset(key) if fetch_asset else None
            except Exception:  # noqa: BLE001
                local = None
            if local:
                idx, next_idx = _media_input(args, next_idx, local, start=0,
                                             dur=span, fps=fps)
                item_ins = blk.get("item") or {}
                return {"kind": "media", "idx": idx, "lead": 0.0,
                        "rate": 1.0,
                        "chain": _cover_chain(W, H, item_ins.get("crop"),
                                              item_ins.get("fit")),
                        "_next": next_idx}
        # main footage (or an insert we cannot open): the program itself
        # across the cut, its edge frame held on the far side
        hold = (cut - s) if kind == "next" else (e - cut)
        if hold > FREEZE_MAX_S:
            notes.append(f"{kind} fill would hold the program {hold:.1f}s; "
                         "drawn in its own colour")
            return None
        notes.append(f"{kind} fill: the shot's edge frame is held "
                     f"{max(0.0, hold):.2f}s (no handle on this lane)")
        return {"kind": "freeze_next" if kind == "next" else "freeze_prev",
                "cut": round(cut, 4)}
    notes.append(f"unknown fill kind {kind!r}")
    return None


def _decode_rgba(path, t, w, h):
    """One RGBA frame of a clip at ``t`` seconds as a numpy array."""
    import numpy as np
    out = subprocess.run(
        [_ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
         "-frames:v", "1", "-vf", f"format=rgba,{_fit(w, h)}",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, timeout=60, check=False).stdout
    if len(out) < w * h * 4:
        return None
    return np.frombuffer(out[:w * h * 4], np.uint8).reshape(h, w, 4)


def _plate_cells(plate, box, W, H):
    """(means, details) 0-255 of the plate grid cells under ``box`` (pixel
    x, y, w, h), over every measured moment, or (None, None)."""
    import base64
    if not plate or not plate.get("s"):
        return None, None
    cols, rows = int(plate.get("c") or 0), int(plate.get("r") or 0)
    if not cols or not rows:
        return None, None
    x, y, w, h = box
    c0, c1 = int(x / W * cols), int(math.ceil((x + w) / W * cols))
    r0, r1 = int(y / H * rows), int(math.ceil((y + h) / H * rows))
    dq = float(plate.get("dq") or 4)
    means, dets = [], []
    for s in plate["s"]:
        try:
            g = list(base64.b64decode(s["g"]))
        except Exception:  # noqa: BLE001
            continue
        d = None
        if s.get("d"):
            try:
                raw = base64.b64decode(s["d"])
                d = []
                for b in raw:
                    d += [(b >> 4) * dq, (b & 15) * dq]
            except Exception:  # noqa: BLE001
                d = None
        for r in range(max(0, r0), min(rows, r1)):
            for c in range(max(0, c0), min(cols, c1)):
                k = r * cols + c
                if k < len(g):
                    means.append(g[k])
                    if d and k < len(d):
                        dets.append(d[k])
    return (means or None), (dets or None)


def _rel_lum(v):
    """Relative luminance (0..1) of an sRGB 0-255 gray level."""
    c = float(v) / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _apca(text_y, bg_y):
    try:
        import qa_metrics
        return abs(float(qa_metrics.apca(text_y, bg_y)))
    except Exception:  # noqa: BLE001 — a coarse fallback
        return abs(text_y - bg_y) * 100.0


def _blend_value(mode, b, l):
    """One channel (0-255) of an ffmpeg blend mode, base b, layer l."""
    B, L = b / 255.0, l / 255.0
    if mode == "difference":
        r = abs(B - L)
    elif mode == "screen":
        r = 1 - (1 - B) * (1 - L)
    elif mode == "multiply":
        r = B * L
    elif mode in ("add", "addition"):
        r = min(1.0, B + L)
    elif mode == "exclusion":
        r = B + L - 2 * B * L
    elif mode == "lighten":
        r = max(B, L)
    elif mode == "darken":
        r = min(B, L)
    else:
        r = L
    return r * 255.0


def auto_blend(plate, box, W, H, ink_luma):
    """The ``auto`` blend for a graphic whose ink has luma ``ink_luma``
    (0-255) over the plate cells under ``box``: difference over a busy
    plate, screen over a dark one, else normal — then legibility is
    MEASURED on the result (APCA Lc of the blended ink against each cell);
    a candidate that does not read over AUTO_MIN_SHARE of the cells falls
    back to the next (difference -> screen -> normal). Returns (mode,
    reason)."""
    means, dets = _plate_cells(plate, box, W, H)
    if not means:
        return "normal", "no plate measured under it"
    import statistics
    mean = statistics.fmean(means)
    spread = statistics.pstdev(means) if len(means) > 1 else 0.0
    detail = statistics.fmean(dets) if dets else 0.0
    busy = detail >= AUTO_BUSY_DETAIL or spread >= AUTO_BUSY_SPREAD
    dark = _rel_lum(mean) <= AUTO_DARK_LUMA
    order = (["difference", "screen", "normal"] if busy else
             ["screen", "difference", "normal"] if dark else
             ["normal"])
    why = ("busy plate" if busy else "dark plate" if dark else
           "calm, lit plate")
    for mode in order:
        if mode == "normal":
            return "normal", why
        reads = 0
        for m in means:
            res = _blend_value(mode, m, ink_luma)
            if _apca(_rel_lum(res), _rel_lum(m)) >= AUTO_MIN_LC:
                reads += 1
        share = reads / float(len(means))
        if share >= AUTO_MIN_SHARE:
            return mode, f"{why}; Lc>={AUTO_MIN_LC:g} on {share:.0%}"
    return "normal", why


def _ink_luma(clip, fps):
    """Mean luma (0-255) of the clip's opaque ink at its middle frame."""
    try:
        import numpy as np
        a = _decode_rgba(clip.path, 0.5 * clip.frames / max(1.0, float(fps)),
                         clip.w, clip.h)
        if a is None:
            return None
        ink = a[..., 3] >= 200
        if not ink.any():
            ink = a[..., 3] >= 64
        if not ink.any():
            return None
        rgb = a[..., :3][ink].astype(float)
        return float((rgb @ np.array([0.2126, 0.7152, 0.0722])).mean())
    except Exception:  # noqa: BLE001
        return None


def _region_side(region, item, W, H, fps, workdir, args, next_idx, notes):
    """The composite's clipping region as an input: a static still or a
    keyframed clip (window-local); anchored shapes (face, card) read the
    render's masks at emit when a plan is passed. Returns (side, next)."""
    import regions
    shape = _get(region, "shape")
    s, e = float(item["start"]), float(item["end"])
    if regions.is_static(region):
        im = regions.still(region, W, H)
        if im is None:
            notes.append("its clip region is empty at this size")
            return None, next_idx
        import hashlib
        tag = hashlib.sha1(json.dumps(region, sort_keys=True, default=str)
                           .encode()).hexdigest()[:10]
        path = os.path.join(workdir, f"mgregion_{item.get('id')}_{tag}.png")
        im.save(path)
        args += ["-loop", "1", "-framerate", f"{fps3(fps):.3f}",
                 "-t", f"{e - s + 0.5:.3f}", "-i", path]
        return {"idx": next_idx, "still": True}, next_idx + 1
    if shape in ("rect", "ellipse", "polygon"):
        n = int(math.ceil((e - s) * float(fps))) + 2
        path = os.path.join(workdir, f"mgregion_{item.get('id')}.mkv")
        raster_keyed(region, W, H, fps, s, n, path)
        args += ["-i", path]
        return {"idx": next_idx, "still": False}, next_idx + 1
    if shape in ("face", "card"):
        ref = _get(region, "ref")
        label = shape if shape == "card" or ref is None else f"face:{ref}"
        return {"mask": label}, next_idx
    notes.append(f"a {shape} clip region is not supported")
    return None, next_idx


def ghost_of(comp, item):
    """The ghost pass a composite asks for, or None: only behind_subject
    graphics have hidden letters; 'off' and None draw none."""
    g = (comp or {}).get("ghost")
    if g is None or g == "off" or (item or {}).get("layer") != \
            "behind_subject":
        return None
    if g == "auto":
        return {"opacity": 0.45, "width_px": 2.5, "threshold": 0.25}
    if isinstance(g, dict):
        return {"opacity": float(g.get("opacity", 0.45)),
                "width_px": float(g.get("width_px", 2.5)),
                "threshold": float(g.get("threshold", 0.25))}
    return None


def hidden_share(clip, mask_path, item, W, H, fps, samples=6):
    """The largest hidden share of any glyph of a behind-subject clip over
    ``samples`` moments: glyphs are the connected components of the clip's
    ink (alpha >= 128), hidden = covered by the subject mask (>= 50%), the
    mask brought to the frame the way the renderer brings it (W x H). None
    when it cannot be measured."""
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    s, e = float(item["start"]), float(item["end"])
    span = e - s
    W, H = int(W), int(H)
    worst, seen = 0.0, 0
    for k in range(samples):
        t = span * (k + 0.5) / samples
        a = _decode_rgba(clip.path, t, clip.w, clip.h)
        if a is None:
            continue
        out = subprocess.run(
            [_ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i",
             mask_path, "-frames:v", "1", "-vf", f"scale={W}:{H}",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=60, check=False).stdout
        if len(out) < W * H:
            continue
        m = np.frombuffer(out[:W * H], np.uint8).reshape(H, W)
        sub = m[clip.y:clip.y + clip.h, clip.x:clip.x + clip.w] >= 128
        if sub.shape != a.shape[:2]:
            continue
        ink = (a[..., 3] >= 128).astype(np.uint8)
        n, lab = cv2.connectedComponents(ink, connectivity=8)
        seen += 1
        for c in range(1, n):
            g = lab == c
            area = int(g.sum())
            if area < 40:
                continue
            worst = max(worst, float((g & sub).sum()) / area)
    return worst if seen else None


def prepare_composites(edl, inputs, *, workdir, W, H, fps, args, next_idx,
                       fetch_asset=None, plates=None, source_path=None):
    """Resolve every composite among the rendered motion ``inputs``
    ([(idx, item, clip)]) and register the glyph clips fx items name.

    ``plates``: {id(item piece): MG.plate dict} (the auto blend's evidence).
    Opens the inputs a composite needs (fills, region masks, the ghost's
    stroke clips) on ``args``; returns (inputs, next_idx) with each
    composite's clip a copy carrying ``composite`` (CompositeSide). Never
    raises: an ingredient that cannot be built is dropped with a note."""
    import motion_engine
    import motion_templates
    out = []
    W, H = int(W), int(H)
    strokes = []
    for idx, item, clip in inputs:
        if not has_composite(item):
            out.append((idx, item, clip))
            continue
        comp = item["composite"]
        side = CompositeSide(W=W, H=H, fps=float(fps))
        try:
            if comp.get("fill"):
                f = _resolve_fill(comp["fill"], item, edl, W, H, fps, args,
                                  next_idx, fetch_asset, source_path,
                                  side.notes)
                if f is not None:
                    next_idx = f.pop("_next", next_idx)
                side.fill = f
            if comp.get("field") and comp.get("fill_mode", "inside") == \
                    "inside":
                side.field = resolve_colour(comp["field"], edl)
            if comp.get("region"):
                side.region, next_idx = _region_side(
                    comp["region"], item, W, H, fps, workdir, args,
                    next_idx, side.notes)
            blend = comp.get("blend") or "normal"
            if blend == "auto":
                ink = _ink_luma(clip, fps)
                if ink is None:
                    blend, why = "normal", "its ink could not be read"
                else:
                    blend, why = auto_blend(
                        (plates or {}).get(id(item)),
                        (clip.x, clip.y, clip.w, clip.h), W, H, ink)
                side.notes.append(f"blend auto -> {blend} ({why})")
            side.blend = blend
            g = ghost_of(comp, item)
            if g is not None and g["threshold"] > 0 and fetch_asset and \
                    (item.get("behind") or {}).get("asset_key"):
                # switch on only when a glyph is hidden past the threshold
                # (measured on the stored subject mask; unmeasurable: on)
                try:
                    hs = hidden_share(clip, fetch_asset(
                        item["behind"]["asset_key"]), item, W, H, fps)
                except Exception:  # noqa: BLE001
                    hs = None
                if hs is not None and hs < g["threshold"]:
                    side.notes.append(f"ghost off: no glyph is more than "
                                      f"{hs:.0%} hidden")
                    g = None
                elif hs is not None:
                    side.notes.append(f"ghost on: a glyph is {hs:.0%} hidden")
            if g is not None:
                strokes.append((item, side, g))
        except Exception as e:  # noqa: BLE001 — degrade the composite
            side.notes.append(f"composite prepare failed "
                              f"({type(e).__name__}: {str(e)[:120]})")
        clip = dataclasses.replace(clip)
        clip.composite = side
        out.append((idx, item, clip))
    # the ghost pass: one stroke-only render of each behind graphic
    if strokes:
        jobs, metas = [], []
        for item, side, g in strokes:
            try:
                # width_px is output pixels at a 1080-px short side; the
                # page draws in a 1080-wide design space
                css = float(g["width_px"]) * min(W, H) / float(W)
                piece = dict(item, params=dict(
                    item.get("params") or {},
                    _paint={"mode": "stroke", "width": round(css, 3)}))
                piece.pop("composite", None)
                jobs.append(motion_templates.build_job(piece, W, H, fps))
                metas.append((item, side, g))
            except Exception as e:  # noqa: BLE001
                side.notes.append(f"ghost pass skipped ({str(e)[:120]})")
        if jobs:
            try:
                clips = motion_engine.render_jobs(
                    jobs, os.path.join(workdir, "motion"))
            except Exception as e:  # noqa: BLE001
                clips = [None] * len(jobs)
                _log(f"ghost pass not rendered: {str(e)[:160]}")
            for (item, side, g), sc in zip(metas, clips):
                if sc is None or sc.empty:
                    side.notes.append("ghost pass drew nothing")
                    continue
                args += ["-i", sc.path]
                side.stroke = {"idx": next_idx, "x": sc.x, "y": sc.y,
                               "w": sc.w, "h": sc.h}
                side.ghost = g
                next_idx += 1
    # glyph inputs for fx references (region effects, accents, junctions)
    refs = glyph_refs(edl)
    entries = {}
    for idx, item, clip in out:
        mid = str(item.get("id"))
        n = refs.get(mid, 0)
        if not n or mid in entries or clip is None or clip.empty:
            continue
        ins = []
        for _k in range(n):
            args += ["-i", clip.path]
            ins.append(next_idx)
            next_idx += 1
        entries[mid] = {"inputs": ins, "x": clip.x, "y": clip.y, "w": clip.w,
                        "h": clip.h, "start": float(item["start"]),
                        "end": float(item["end"]), "W": W, "H": H,
                        "fps": float(fps)}
    register_glyphs(workdir, entries)
    for _i, item, clip in out:
        side = getattr(clip, "composite", None)
        if side and side.notes:
            _log(f"motion '{item.get('id')}': " + "; ".join(side.notes))
    return out, next_idx


# ── composites on motion graphics: emit (motion_layer.overlay_clip) ──────

def _plan_mask(plan, label, space="screen"):
    try:
        return fxgraph.mask(plan, label, space)
    except Exception:  # noqa: BLE001
        return None


def emit(parts, vlabel, idx, item, clip, fps, tag, plan=None):
    """Lay one composite graphic over [vlabel]; returns the new label.

    In order, window-local (frame k = the item's k-th frame):
      1. the glyph layer: the clip's own pixels (rgba, its box);
      2. the fill: inside = the fill's pixels in the glyphs' alpha (the box),
         with the ``field`` colour around them (the frame); outside = the
         fill over the frame with the glyphs cut out as windows;
      3. the clipping region multiplies the layer's alpha;
      4. the opacity scales it;
      5. a blend other than normal mixes the layer with the picture under
         its box (out = base + (MODE(base, layer) - base) * alpha);
      6. stamped on the legacy clip clock and overlaid on the item's frames
         (``gte(t,start)*lt(t,end)``), padded from t = 0."""
    comp = item.get("composite") or {}
    side = getattr(clip, "composite", None) or CompositeSide(
        W=0, H=0, fps=float(fps))
    s, e = float(item["start"]), float(item["end"])
    W, H = int(side.W), int(side.H)
    u = tag
    n = window_frames(s, e, fps) + 2
    fr = float(fps)
    x, y, w, h = int(clip.x), int(clip.y), int(clip.w), int(clip.h)
    # 1. the glyph layer
    parts.append(f"[{idx}:v]setpts=PTS-STARTPTS,format=rgba,{_fit(w, h)},"
                 f"tpad=stop_mode=clone:stop={n},trim=end_frame={n},"
                 f"setpts=N/{fr:.6f}/TB[{u}g]")
    layer, box = f"{u}g", (x, y, w, h)
    main = vlabel
    # 2. the fill (and the field)
    fill = side.fill
    fmode = comp.get("fill_mode") or "inside"
    if fill is not None and W and H:
        fl, main = _fill_stream(parts, main, fill, item, fps, W, H, n, u)
        if fmode == "outside":
            parts.append(f"[{layer}]alphaextract,pad={W}:{H}:{x}:{y}:black,"
                         f"negate[{u}inv]")
            parts.append(f"[{fl}][{u}inv]alphamerge[{u}lf]")
            layer, box = f"{u}lf", (0, 0, W, H)
        else:
            parts.append(f"[{fl}]crop={w}:{h}:{x}:{y}[{u}fc]")
            parts.append(f"[{layer}]alphaextract[{u}fa]")
            parts.append(f"[{u}fc][{u}fa]alphamerge[{u}lf]")
            layer = f"{u}lf"
    if side.field and W and H and fmode == "inside":
        parts.append(f"color=c=0x{side.field.lstrip('#')}:s={W}x{H}:"
                     f"r={fr:.6f},format=rgba,trim=end_frame={n},"
                     f"setpts=N/{fr:.6f}/TB[{u}fd]")
        parts.append(f"[{u}fd][{layer}]overlay={x}:{y}:format=auto,"
                     f"format=rgba[{u}fdl]")
        layer, box = f"{u}fdl", (0, 0, W, H)
    bx, by, bw, bh = box
    # 3. the clipping region
    if side.region:
        rm = None
        if "idx" in side.region:
            parts.append(f"[{side.region['idx']}:v]format=gray,"
                         f"trim=end_frame={n},setpts=N/{fr:.6f}/TB,"
                         f"crop={bw}:{bh}:{bx}:{by}[{u}rm]")
            rm = f"{u}rm"
        elif plan is not None and W and H:
            pm = _plan_mask(plan, side.region["mask"])
            if pm is not None:
                parts.append(f"[{pm}]format=gray,trim=start={s:.4f},"
                             f"setpts=PTS-STARTPTS,crop={bw}:{bh}:{bx}:{by},"
                             f"tpad=stop_mode=clone:stop={n},"
                             f"trim=end_frame={n},setpts=N/{fr:.6f}/TB"
                             f"[{u}rm]")
                rm = f"{u}rm"
        if rm is not None:
            parts.append(f"[{layer}]split[{u}r1][{u}r2]")
            parts.append(f"[{u}r2]alphaextract[{u}ra]")
            parts.append(f"[{u}ra][{rm}]blend=all_mode=multiply[{u}rb]")
            parts.append(f"[{u}r1][{u}rb]alphamerge[{u}gr]")
            layer = f"{u}gr"
        else:
            _log(f"motion '{item.get('id')}': its clip region is not "
                 "available on this render; drawn unclipped")
    # 4. the opacity
    op = comp.get("opacity")
    if op is not None and float(op) < 0.999:
        parts.append(f"[{layer}]format=rgba,colorchannelmixer="
                     f"aa={float(op):.4f}[{u}go]")
        layer = f"{u}go"
    # 5. the blend with the picture under the box
    mode = side.blend if side.blend in BLEND_FFMPEG else "normal"
    if mode != "normal":
        parts.append(f"[{main}]split[{u}m0][{u}m1]")
        main = f"{u}m0"
        parts.append(f"[{u}m1]trim=start={s:.4f}:end={e + 1.0 / fr:.4f},"
                     f"setpts=PTS-STARTPTS,crop={bw}:{bh}:{bx}:{by},"
                     f"format=gbrp,tpad=stop_mode=clone:stop={n},"
                     f"trim=end_frame={n},setpts=N/{fr:.6f}/TB[{u}b]")
        parts.append(f"[{layer}]split[{u}l1][{u}l2]")
        parts.append(f"[{u}l1]format=gbrp[{u}lr]")
        parts.append(f"[{u}l2]alphaextract[{u}la]")
        parts.append(f"[{u}b][{u}lr]blend=all_mode={BLEND_FFMPEG[mode]}"
                     f"[{u}bl]")
        parts.append(f"[{u}bl][{u}la]alphamerge[{u}bla]")
        layer = f"{u}bla"
    # 6. on the legacy clip clock, over the item's frames
    parts.append(f"[{layer}]trim=end_frame={n},format=rgba,"
                 f"{_stamp(s, fps)}[{u}c]")
    parts.append(f"[{main}][{u}c]overlay=x={bx}:y={by}:eof_action=pass"
                 f":format=auto:enable='{_enable(s, e)}'[{u}o]")
    return f"{u}o"


def _fill_stream(parts, main, fill, item, fps, W, H, n, u):
    """(label of a window-local W x H fill of n frames, main label)."""
    s, e = float(item["start"]), float(item["end"])
    fr = float(fps)
    kind = fill["kind"]
    tail = (f"tpad=stop_mode=clone:stop={n},trim=end_frame={n},"
            f"setpts=N/{fr:.6f}/TB")
    if kind == "colour":
        parts.append(f"color=c=0x{fill['hex'].lstrip('#')}:s={W}x{H}:"
                     f"r={fr:.6f},trim=end_frame={n},"
                     f"setpts=N/{fr:.6f}/TB[{u}f]")
        return f"{u}f", main
    if kind == "media":
        rate = float(fill.get("rate") or 1.0)
        lead = float(fill.get("lead") or 0.0)
        pre = (f"tpad=start_duration={lead:.4f}:start_mode=clone,"
               if lead > 1e-3 else "")
        speed = f"setpts=PTS/{rate:.5f}," if abs(rate - 1.0) > 1e-4 else ""
        parts.append(f"[{fill['idx']}:v]setpts=PTS-STARTPTS,{speed}"
                     f"fps={fps3(fps):.3f},{fill['chain']},format=rgba,"
                     f"{pre}{tail}[{u}f]")
        return f"{u}f", main
    parts.append(f"[{main}]split[{u}pm][{u}pf]")
    main = f"{u}pm"
    one = 1.0 / fr
    if kind == "program":
        off = float(fill.get("offset") or 0.0)
        a = max(0.0, s + off)
        lead = max(0.0, -(s + off))
        pre = (f"tpad=start_duration={lead:.4f}:start_mode=clone,"
               if lead > 1e-3 else "")
        parts.append(f"[{u}pf]trim=start={a:.4f}:end={e + off + one:.4f},"
                     f"setpts=PTS-STARTPTS,format=rgba,{pre}{tail}[{u}f]")
    elif kind == "freeze_next":
        cut = float(fill["cut"])
        # the second frame of the shot after the cut (robust to a cut a
        # frame either side of the Timeline's), held back to the start
        a = cut + 1.5 * one
        hold = max(0.0, a - s - 0.5 * one)
        parts.append(f"[{u}pf]trim=start={a:.4f}:end={max(e, a) + one:.4f},"
                     f"setpts=PTS-STARTPTS,format=rgba,"
                     f"tpad=start_duration={hold:.4f}:start_mode=clone,"
                     f"{tail}[{u}f]")
    elif kind == "freeze_prev":
        cut = float(fill["cut"])
        # the shot before the cut plays to its second-to-last frame (robust
        # to a cut a frame either side), which is then held: from the
        # item's start when that is before the cut, else that frame alone
        b = cut - 1.5 * one
        a = s if s < b - one else b - one
        parts.append(f"[{u}pf]trim=start={max(0.0, a):.4f}:end={b:.4f},"
                     f"setpts=PTS-STARTPTS,format=rgba,{tail}[{u}f]")
    else:
        parts.append(f"[{u}pf]nullsink")
        parts.append(f"color=c=black@0:s={W}x{H}:r={fr:.6f},format=rgba,"
                     f"trim=end_frame={n}[{u}f]")
    return f"{u}f", main


def emit_ghost(parts, vlabel, mask_label, item, clip, fps, j):
    """The ghost pass over a behind-subject composite (INTERFACES_W1.md
    §3.3): the stroke-only render of the graphic, kept only where the
    subject (``mask_label``, the program-clock gray mask the subject was
    lifted with) covers it, at the ghost's opacity — so the letters the
    head hides still read as outlines and nothing changes where the glyphs
    are visible. Consumes ``mask_label``; returns the new label."""
    side = getattr(clip, "composite", None)
    st = side.stroke if side else None
    g = side.ghost if side else None
    if not st or not g:
        parts.append(f"[{mask_label}]nullsink")
        return vlabel
    s, e = float(item["start"]), float(item["end"])
    fr = float(fps)
    u = f"gh{j}"
    sx, sy, sw, sh = (int(st["x"]), int(st["y"]), int(st["w"]),
                      int(st["h"]))
    parts.append(f"[{st['idx']}:v]setpts=PTS-STARTPTS,format=rgba,"
                 f"{_fit(sw, sh)},"
                 f"tpad=stop_mode=clone:stop_duration={2.0 / max(fr, 1):.4f},"
                 f"setpts=PTS+{s:.4f}/TB[{u}s]")
    parts.append(f"[{mask_label}]format=gray,crop={sw}:{sh}:{sx}:{sy}"
                 f"[{u}m]")
    parts.append(f"[{u}s]split[{u}s1][{u}s2]")
    parts.append(f"[{u}s2]alphaextract[{u}a]")
    parts.append(f"[{u}a][{u}m]blend=all_mode=multiply:shortest=0"
                 f"[{u}am]")
    parts.append(f"[{u}s1][{u}am]alphamerge,"
                 f"colorchannelmixer=aa={float(g['opacity']):.4f}[{u}l]")
    parts.append(f"[{vlabel}][{u}l]overlay=x={sx}:y={sy}:eof_action=pass"
                 f":format=auto:enable='{_enable(s, e)}'[{u}o]")
    return f"{u}o"


# ── device-specific metrics (qa_metrics.register; ROADMAP §4.5) ──────────

def _register_metrics():
    try:
        import numpy as np
        import qa_metrics
    except Exception:  # noqa: BLE001 — metrics are a test/refbench aid
        return

    def reg(name, bar):
        if name in qa_metrics.METRICS:
            return lambda fn: fn
        return qa_metrics.register(name, bar)

    @reg("fill_psnr", (">=", 40.0))
    def fill_psnr(*, render, reference, mask, bar=None):
        """PSNR (dB) of the rendered pixels inside ``mask`` (glyph interior,
        eroded so antialiased edges are not judged) against a direct crop of
        the fill (1C-1)."""
        m = np.asarray(mask, bool)
        a = np.asarray(render, float)[m]
        b = np.asarray(reference, float)[m]
        mse = float(((a - b) ** 2).mean()) if a.size else 0.0
        v = 99.0 if mse <= 1e-9 else 10 * math.log10(255.0 ** 2 / mse)
        return qa_metrics._result("fill_psnr", round(v, 2), bar,
                                  {"pixels": int(m.sum())})

    @reg("no_darker", ("<=", 1.0))
    def no_darker(*, with_fx, without_fx, bar=None):
        """The most any pixel's luma got DARKER (0-255) — additive light
        (screen / add) never darkens (1C-7)."""
        a = np.asarray(with_fx, float)
        b = np.asarray(without_fx, float)
        v = float(max(0.0, (b - a).max())) if a.size else 0.0
        return qa_metrics._result("no_darker", round(v, 2), bar,
                                  {"pixels_darker_2":
                                   int(((b - a) > 2).sum())})


_register_metrics()
