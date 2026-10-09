"""Footage-only rounded cards, before captions/graphics and native branding.

One cached antialiased plate per design. Opening/closing uses cheap native
overlay/fade filters, not per-frame image generation or rasterized typography.
The card is a window of the original program, never a prepared child master.

Designed backdrops (PictureCard.background_style / grain / vignette) replace
the flat colour around the card:

* vertical_gradient / radial_gradient are STATIC: the gradient and vignette
  are baked (dithered, so 8-bit steps never band) into the same one-input
  plate the flat card uses. The plate's hole carries the clean fill, which is
  split off as the layer under the opening footage, so a fading card
  materializes out of its own backdrop instead of out of a flat slab.
* blur is DYNAMIC: a blurred, darkened copy of the card's own footage fills
  the canvas. It is computed at 1/8 resolution (blur + grade there cost next
  to nothing) and scaled up. Because the backdrop moves, the rounded window
  that clips the footage cannot be baked into an opaque plate; the backdrop
  is laid back over everything outside the window through a static mask
  instead, and a decor plate (shadow, vignette, hairline) goes on top.

Film grain is temporal luma noise with a fixed seed (deterministic renders)
applied to the backdrop only — the footage itself is never re-noised. A card
with none of these options renders through the historical graph, unchanged.
"""
import hashlib
import json
import math
import os

DESIGNED_STYLES = ("vertical_gradient", "radial_gradient", "blur")
BLUR_DIM_DEFAULT = .45
GRAIN_SEED = 4271


def pixels(W, H, rect):
    def even(value):
        return int(round(value / 2)) * 2
    x, y = even(W * rect[0]), even(H * rect[1])
    right, bottom = min(W, even(W * rect[2])), min(H, even(H * rect[3]))
    return x, y, max(2, right - x), max(2, bottom - y)


def designed(spec):
    """True when the card draws a designed backdrop (not the flat colour)."""
    return bool(spec.get("background_style") in DESIGNED_STYLES
                or spec.get("grain") or spec.get("vignette"))


def plate_path(workdir, W, H, spec, kind="plate"):
    keys = ("box", "radius", "border", "border_color", "background", "shadow")
    if designed(spec):
        keys += ("background_style", "background_color2", "vignette")
    style = {k: spec.get(k) for k in keys}
    digest = hashlib.sha256(json.dumps([W, H, style], sort_keys=True).encode()).hexdigest()[:20]
    suffix = "" if kind == "plate" else f"-{kind}"
    return os.path.join(workdir, f"picture-card-{digest}{suffix}.png")


def _rgb(color):
    import numpy as np
    c = (color or "#101012").lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def _smooth(t):
    return t * t * (3.0 - 2.0 * t)


def _vignette(W, H, strength):
    """Multiplicative darkening, 1.0 in the middle, falling off toward the
    corners in the canvas's own aspect (an ellipse on a portrait frame)."""
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.hypot((xx + .5) / W - .5, (yy + .5) / H - .5) / np.float32(.7071)
    fall = _smooth(np.clip((r - .32) / .68, 0, 1))
    return 1.0 - np.float32(.78 * float(strength)) * fall


def _fill(W, H, spec):
    """H x W x 3 float32 backdrop for the static styles (before shadow)."""
    import numpy as np
    style = spec.get("background_style")
    c1 = _rgb(spec.get("background"))
    c2 = (_rgb(spec["background_color2"]) if spec.get("background_color2")
          else c1 * np.float32(.3))
    if style == "vertical_gradient":
        t = _smooth(np.linspace(0, 1, H, dtype=np.float32))[:, None, None]
        img = np.broadcast_to(c1 * (1 - t) + c2 * t, (H, W, 3)).copy()
    elif style == "radial_gradient":
        x0, y0, x1, y1 = spec["box"]
        cx, cy = (x0 + x1) / 2 * W, (y0 + y1) / 2 * H
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.hypot(xx - cx, yy - cy)
        far = max(np.hypot(px - cx, py - cy) for px in (0, W) for py in (0, H))
        t = _smooth(np.clip(d / np.float32(far * .92), 0, 1))[..., None]
        img = c1 * (1 - t) + c2 * t
    else:
        img = np.broadcast_to(c1, (H, W, 3)).astype(np.float32).copy()
    if spec.get("vignette"):
        img *= _vignette(W, H, spec["vignette"])[..., None]
    return img


def _dither(img):
    """Quantize with +-0.5 LSB of fixed-seed noise: smooth gradients never
    band at 8 bits, and the same design always yields the same pixels."""
    import numpy as np
    rng = np.random.default_rng(GRAIN_SEED)
    noise = rng.random(img.shape[:2], dtype=np.float32)[..., None] - .5
    return np.clip(img + noise, 0, 255).astype(np.uint8)


def _geometry_masks(W, H, spec):
    """(hole alpha, border ring, shadow) as PIL L images at W x H, drawn
    exactly as build_plate draws them."""
    from PIL import Image, ImageDraw, ImageFilter
    x, y, w, h = pixels(W, H, spec["box"])
    radius = float(spec.get("radius", .045)) * min(w, h)
    shadow = float(spec.get("shadow", .35))
    shade = Image.new("L", (W, H))
    if shadow:
        blur = max(2, round(min(W, H) * .018))
        ImageDraw.Draw(shade).rounded_rectangle(
            (x, y + blur*.6, x+w-1, y+h-1+blur*.6),
            radius=radius, fill=round(shadow*190))
        shade = shade.filter(ImageFilter.GaussianBlur(blur))
    ss = 3
    border = float(spec.get("border", .001)) * min(W, H)
    alpha = Image.new("L", (W*ss, H*ss), 255)
    ImageDraw.Draw(alpha).rounded_rectangle(
        (x*ss, y*ss, (x+w)*ss-1, (y+h)*ss-1), radius=radius*ss, fill=0)
    ring = Image.new("L", (W, H))
    if border:
        edge = Image.new("L", (W*ss, H*ss))
        d = ImageDraw.Draw(edge)
        d.rounded_rectangle(((x-border)*ss, (y-border)*ss,
                             (x+w+border)*ss-1, (y+h+border)*ss-1),
                            radius=(radius+border)*ss, fill=255)
        d.rounded_rectangle((x*ss, y*ss, (x+w)*ss-1, (y+h)*ss-1),
                            radius=radius*ss, fill=0)
        ring = edge.resize((W, H), Image.Resampling.LANCZOS)
    return alpha.resize((W, H), Image.Resampling.LANCZOS), ring, shade


def build_designed_plate(path, W, H, spec):
    """Static designed backdrop: gradient/solid + vignette + shadow + border,
    opaque outside the card. The hole keeps the CLEAN fill in its RGB (alpha
    0), which the graph splits off as the layer under the opening footage."""
    import numpy as np
    from PIL import Image
    hole, ring, shade = _geometry_masks(W, H, spec)
    fill = _fill(W, H, spec)
    a = np.asarray(hole, np.float32)[..., None] / 255.0
    s = np.asarray(shade, np.float32)[..., None] / 255.0
    b = np.asarray(ring, np.float32)[..., None] / 255.0
    img = fill * (1 - s * a)                 # shadow only where the plate shows
    img = img * (1 - b) + _rgb(spec.get("border_color", "#444444")) * b
    rgba = np.dstack([_dither(img), np.asarray(hole, np.uint8)])
    # Fast deflate: the dither makes this a large PNG, decoded once a render.
    Image.fromarray(rgba, "RGBA").save(path, compress_level=1)
    return path


def build_decor(path, mask_path, W, H, spec):
    """Dynamic (blur) backdrop: a decor plate — shadow, vignette, hairline as
    straight RGBA over a transparent canvas — and the static mask (white
    outside the card) through which the backdrop is laid back over the
    footage's overhang. Both exclude the card's own window."""
    import numpy as np
    from PIL import Image
    hole, ring, shade = _geometry_masks(W, H, spec)
    outside = np.asarray(hole, np.float32) / 255.0
    s = np.asarray(shade, np.float32) / 255.0
    v = (1.0 - _vignette(W, H, spec["vignette"])) if spec.get("vignette") \
        else np.zeros((H, W), np.float32)
    b = np.asarray(ring, np.float32) / 255.0
    dark = 1.0 - (1.0 - s) * (1.0 - v)        # black: shadow over vignette
    alpha = b + dark * (1.0 - b)              # hairline on top
    color = _rgb(spec.get("border_color", "#444444"))[None, None, :] * \
        (b / np.maximum(alpha, 1e-6))[..., None]
    rgba = np.dstack([np.clip(color + .5, 0, 255).astype(np.uint8),
                      np.clip(alpha * outside * 255 + .5, 0, 255).astype(np.uint8)])
    Image.fromarray(rgba, "RGBA").save(path)
    hole.save(mask_path)
    return path, mask_path


def build_plate(path, W, H, spec):
    from PIL import Image, ImageDraw, ImageFilter
    x, y, w, h = pixels(W, H, spec["box"])
    radius = float(spec.get("radius", .045)) * min(w, h)
    bg = spec.get("background", "#101012")
    image = Image.new("RGB", (W, H), bg)
    shadow = float(spec.get("shadow", .35))
    if shadow:
        mask = Image.new("L", (W, H))
        blur = max(2, round(min(W, H) * .018))
        ImageDraw.Draw(mask).rounded_rectangle(
            (x, y + blur*.6, x+w-1, y+h-1+blur*.6),
            radius=radius, fill=round(shadow*190))
        image = Image.composite(Image.new("RGB", (W,H)), image,
                                mask.filter(ImageFilter.GaussianBlur(blur)))
    # Supersample both the border and the transparent hole. Width is relative
    # to the canvas short side, so it survives both proof and HD delivery.
    ss = 3
    border = float(spec.get("border", .001)) * min(W,H)
    alpha = Image.new("L", (W*ss,H*ss), 255)
    ImageDraw.Draw(alpha).rounded_rectangle(
        (x*ss,y*ss,(x+w)*ss-1,(y+h)*ss-1), radius=radius*ss, fill=0)
    if border:
        edge = Image.new("L", (W*ss,H*ss))
        d = ImageDraw.Draw(edge)
        d.rounded_rectangle(((x-border)*ss,(y-border)*ss,
                             (x+w+border)*ss-1,(y+h+border)*ss-1),
                            radius=(radius+border)*ss, fill=255)
        d.rounded_rectangle((x*ss,y*ss,(x+w)*ss-1,(y+h)*ss-1),
                            radius=radius*ss, fill=0)
        image = Image.composite(Image.new("RGB", (W,H), spec.get("border_color", "#444444")),
                                image, edge.resize((W,H), Image.Resampling.LANCZOS))
    image = image.convert("RGBA")
    image.putalpha(alpha.resize((W,H), Image.Resampling.LANCZOS))
    image.save(path)
    return path


def prepare_inputs(edl, workdir, W, H, fps, args, next_idx):
    inputs = []
    for spec in (edl.get("effects") or {}).get("picture_cards") or []:
        if designed(spec):
            # Designed plates are ONE decoded frame each, repeated in the
            # graph (_still): `-loop 1` would re-decode a full-canvas PNG
            # and re-convert it every frame, which measured as most of the
            # designed backdrop's cost.
            if spec.get("background_style") == "blur":
                path = plate_path(workdir, W, H, spec, "decor")
                mask = plate_path(workdir, W, H, spec, "mask")
                if not (os.path.exists(path) and os.path.exists(mask)):
                    build_decor(path, mask, W, H, spec)
                args.extend(["-framerate", str(fps), "-i", path,
                             "-framerate", str(fps), "-i", mask])
                inputs.append((next_idx, dict(spec, _mask_input=next_idx + 1)))
                next_idx += 2
                continue
            path = plate_path(workdir, W, H, spec)
            if not os.path.exists(path):
                build_designed_plate(path, W, H, spec)
            args.extend(["-framerate", str(fps), "-i", path])
            inputs.append((next_idx, spec))
            next_idx += 1
            continue
        path = plate_path(workdir, W, H, spec)
        if not os.path.exists(path):
            build_plate(path, W, H, spec)
        # Bound each branch to its own window: cards do not decode/composite
        # throughout a long program just because the first one starts early.
        args.extend(["-loop", "1", "-t", str(spec["end"]-spec["start"]+.1),
                     "-r", str(fps), "-i", path])
        inputs.append((next_idx, spec))
        next_idx += 1
    return inputs, next_idx


def _still(length, fps):
    """Repeat a single decoded (and already converted) frame for the card's
    window plus a frame of slack, at the program rate."""
    return (f"loop=loop=-1:size=1:start=0,"
            f"trim=end_frame={int(math.ceil((length + .1) * float(fps)))}")


def _grain(spec):
    """Temporal luma grain for a backdrop branch ('' when off). Strength 20
    is Gaussian sigma ~11.5/255; .25 lands on the references' sigma ~3."""
    g = float(spec.get("grain") or 0)
    if g <= 0:
        return ""
    return f"noise=c0s={max(1, round(20 * g))}:c0f=t:c0_seed={GRAIN_SEED},"


def _blur_chain(W, H, spec):
    """Cover-scale the footage to 1/8 of the canvas, blur and darken there,
    and scale back up: a full-canvas backdrop for the cost of a thumbnail."""
    bw, bh = max(16, int(round(W / 16)) * 2), max(16, int(round(H / 16)) * 2)
    k = 1.0 - float(spec.get("background_dim") if spec.get("background_dim")
                    is not None else BLUR_DIM_DEFAULT)
    # Proportional to the canvas, so a 360p preview and a 1080p final show
    # the same softness.
    sigma = max(1.0, min(bw, bh) / 32.0)
    return (f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"setsar=1,format=yuv420p,gblur=sigma={sigma:.2f}:steps=2,"
            f"lutyuv=y='16+(val-16)*{k:.3f}':u='128+(val-128)*.85':v='128+(val-128)*.85',"
            f"scale={W}:{H}:flags=bicubic,setsar=1")


def _append_designed(parts, vlabel, p, idx, spec, W, H, fps, source_rect):
    """The designed-backdrop variant of append_graph's per-card branch."""
    sx, sy, sw, sh = pixels(W, H, source_rect or [0, 0, 1, 1])
    start, end = float(spec["start"]), float(spec["end"])
    length = end-start
    phase = float(spec.get("phase_s") or 0)
    full = float(spec.get("full_duration_s") or length)
    edge = min(float(spec.get("duration_s", .45)), full*.3)
    x, y, w, h = pixels(W, H, spec["box"])
    blur = spec.get("background_style") == "blur"
    parts.append(f"[{vlabel}]split[{p}pass][{p}src]")
    # Transparent padding: a fit=pad card shows its backdrop around the
    # footage rather than a flat bar.
    fit = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=rgba"
           if spec.get("fit", "crop") == "crop" else
           f"scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1,format=rgba,"
           f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0")
    head = f"trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS+{phase:.6f}/TB,crop={sw}:{sh}:{sx}:{sy}"
    tile = fit
    ent, ext = spec.get("entrance", "lift"), spec.get("exit", "fade")
    if ent in ("fade", "lift"):
        tile += f",fade=t=in:st=0:d={edge:.6f}:alpha=1"
    if ext in ("fade", "lift"):
        tile += f",fade=t=out:st={full-edge:.6f}:d={edge:.6f}:alpha=1"
    tile += f",setpts=PTS-{phase:.6f}/TB"
    if blur:
        parts.append(f"[{p}src]{head},split[{p}tsrc][{p}bsrc]")
        parts.append(f"[{p}tsrc]{tile}[{p}tile]")
        parts.append(f"[{p}bsrc]{_blur_chain(W, H, spec)},{_grain(spec)}"
                     f"setpts=PTS-{phase:.6f}/TB,split[{p}bg][{p}bgtop]")
    else:
        parts.append(f"[{p}src]{head},{tile}[{p}tile]")
        parts.append(f"[{idx}:v]format=rgba,split[{p}plate0][{p}under0]")
        # The plate's hole carries the clean fill; dropping alpha makes it
        # the layer the opening footage fades in over.
        parts.append(f"[{p}under0]format=yuv420p,{_still(length, fps)}[{p}bg]")
        grain = _grain(spec)
        parts.append(f"[{p}plate0]" + (f"format=yuva444p,{_still(length, fps)},{grain.rstrip(',')}"
                                       if grain else _still(length, fps)) + f"[{p}plate]")
    ye = str(y)
    if ent in ("lift", "reveal"):
        distance = h if ent == "reveal" else H*.022
        ye += f"+{distance:.3f}*pow(max(0,1-(t+{phase:.6f})/{edge:.6f}),3)"
    if ext in ("lift", "reveal"):
        distance = h if ext == "reveal" else H*.022
        ye += f"+{distance:.3f}*pow(max(0,(t+{phase:.6f}-{full-edge:.6f})/{edge:.6f}),3)"
    parts.append(f"[{p}bg][{p}tile]overlay=x={x}:y='{ye}':shortest=1:format=auto[{p}placed]")
    if blur:
        # The moving backdrop goes back over the footage's overhang outside
        # the rounded window (the lift/reveal slide), then the decor plate.
        parts.append(f"[{spec['_mask_input']}:v]format=gray,{_still(length, fps)}[{p}mask]")
        parts.append(f"[{p}bgtop][{p}mask]alphamerge[{p}clip]")
        parts.append(f"[{p}placed][{p}clip]overlay=0:0:shortest=1:format=auto[{p}framed]")
        parts.append(f"[{idx}:v]format=rgba,{_still(length, fps)}[{p}plate]")
        parts.append(f"[{p}framed][{p}plate]overlay=0:0:shortest=1:format=auto,setpts=PTS+{start:.6f}/TB[{p}card]")
    else:
        parts.append(f"[{p}placed][{p}plate]overlay=0:0:shortest=1:format=auto,setpts=PTS+{start:.6f}/TB[{p}card]")
    parts.append(f"[{p}pass][{p}card]overlay=0:0:eof_action=repeat:repeatlast=1:enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[{p}out]")
    return f"{p}out"


def append_graph(parts, vlabel, inputs, W, H, fps, source_rect=None):
    if not inputs:
        return vlabel
    sx, sy, sw, sh = pixels(W,H,source_rect or [0,0,1,1])
    for j, (idx, spec) in enumerate(inputs or []):
        p = f"pc{j}"
        if designed(spec):
            vlabel = _append_designed(parts, vlabel, p, idx, spec, W, H, fps,
                                      source_rect)
            continue
        start, end = float(spec["start"]), float(spec["end"])
        length = end-start
        phase = float(spec.get("phase_s") or 0)
        full = float(spec.get("full_duration_s") or length)
        edge = min(float(spec.get("duration_s",.45)), full*.3)
        x,y,w,h = pixels(W,H,spec["box"])
        color = spec.get("background", "#101012").replace("#", "0x")
        parts.append(f"[{vlabel}]split[{p}pass][{p}src]")
        fit = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
               if spec.get("fit", "crop") == "crop" else
               f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={color}")
        chain = f"trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS+{phase:.6f}/TB,crop={sw}:{sh}:{sx}:{sy},{fit},setsar=1,format=rgba"
        ent, ext = spec.get("entrance","lift"), spec.get("exit","fade")
        if ent in ("fade","lift"):
            chain += f",fade=t=in:st=0:d={edge:.6f}:alpha=1"
        if ext in ("fade","lift"):
            chain += f",fade=t=out:st={full-edge:.6f}:d={edge:.6f}:alpha=1"
        chain += f",setpts=PTS-{phase:.6f}/TB"
        parts.append(f"[{p}src]{chain}[{p}tile]")
        parts.append(f"color=c={color}:s={W}x{H}:r={fps}:d={length:.6f}[{p}bg]")
        ye = str(y)
        if ent in ("lift","reveal"):
            distance = h if ent=="reveal" else H*.022
            ye += f"+{distance:.3f}*pow(max(0,1-(t+{phase:.6f})/{edge:.6f}),3)"
        if ext in ("lift","reveal"):
            distance = h if ext=="reveal" else H*.022
            ye += f"+{distance:.3f}*pow(max(0,(t+{phase:.6f}-{full-edge:.6f})/{edge:.6f}),3)"
        parts.append(f"[{p}bg][{p}tile]overlay=x={x}:y='{ye}':shortest=1:format=auto[{p}placed]")
        parts.append(f"[{idx}:v]setpts=PTS-STARTPTS,format=rgba[{p}plate]")
        parts.append(f"[{p}placed][{p}plate]overlay=0:0:shortest=1:format=auto,setpts=PTS+{start:.6f}/TB[{p}card]")
        # trim/setpts and framesync quantize fractional cut clocks differently.
        # The card branch can reach EOF one or two frames before the program
        # clock reaches end. Hold its last composed frame until that exact
        # boundary; passing through at EOF briefly exposes square footage.
        # The explicit enable interval still releases it at the authored end.
        parts.append(f"[{p}pass][{p}card]overlay=0:0:eof_action=repeat:repeatlast=1:enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[{p}out]")
        vlabel = f"{p}out"
    return vlabel
