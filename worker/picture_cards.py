"""Footage-only rounded cards, before captions/graphics and native branding.

One cached antialiased plate per design. Opening/closing uses cheap native
overlay/fade filters, not per-frame image generation or rasterized typography.
The card is a window of the original program, never a prepared child master.
"""
import hashlib
import json
import os


def pixels(W, H, rect):
    def even(value):
        return int(round(value / 2)) * 2
    x, y = even(W * rect[0]), even(H * rect[1])
    right, bottom = min(W, even(W * rect[2])), min(H, even(H * rect[3]))
    return x, y, max(2, right - x), max(2, bottom - y)


def plate_path(workdir, W, H, spec):
    style = {k: spec.get(k) for k in
             ("box", "radius", "border", "border_color", "background", "shadow")}
    digest = hashlib.sha256(json.dumps([W, H, style], sort_keys=True).encode()).hexdigest()[:20]
    return os.path.join(workdir, f"picture-card-{digest}.png")


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
        path = plate_path(workdir, W, H, spec)
        if not os.path.exists(path):
            build_plate(path, W, H, spec)
        # The plate is ONE decoded still that overlay holds for the whole
        # card (append_graph). It used to be a `-loop 1 -t window -r fps`
        # input that re-decoded the 1080x1920 RGBA PNG every frame; ffmpeg 8
        # decodes such inputs ahead of the graph and parks the 8 MB frames
        # in RAM — 12 cards held ~3.3 GB of identical plates on a 40 s final.
        args.extend(["-i", path])
        inputs.append((next_idx, spec))
        next_idx += 1
    return inputs, next_idx


def append_graph(parts, vlabel, inputs, W, H, fps, source_rect=None):
    if not inputs:
        return vlabel
    sx, sy, sw, sh = pixels(W,H,source_rect or [0,0,1,1])
    for j, (idx, spec) in enumerate(inputs or []):
        p = f"pc{j}"
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
        # MEMORY: the card is a window of the program itself, so its first
        # frame cannot exist until the program reaches `start`, and overlay's
        # framesync releases no main frame until it has seen the secondary's
        # first timestamp. Every full-resolution program frame before `start`
        # therefore queued in RAM: 6 cards on a 40 s 1080x1920 program peaked
        # at 6.4 GB, and finals were OOM-killed. Two lead frames that tpad
        # emits on demand before any input arrives tell framesync "nothing
        # until start-2/fps", so the main stream flows and at most a frame or
        # two waits. They sit just before `start`, where the enable window
        # keeps the overlay off, so they are never drawn. tpad keeps the
        # card's own 1/fps timebase and shifts it by exactly those two ticks,
        # which `PTS-2` removes: every card frame keeps the timestamp the
        # direct `PTS+start/TB` gave it, so the composite is bit-identical.
        # shortest=0 + eof_action=repeat: the single plate frame is held over
        # every card frame, and [placed] alone decides the card's length —
        # exactly what the window-length looped plate (always the longer
        # input under shortest=1) produced before.
        parts.append(f"[{p}placed][{p}plate]overlay=0:0:shortest=0:eof_action=repeat:format=auto,"
                     f"tpad=start=2,setpts=PTS-2+{start:.6f}/TB[{p}card]")
        # trim/setpts and framesync quantize fractional cut clocks differently.
        # The card branch can reach EOF one or two frames before the program
        # clock reaches end. Hold its last composed frame until that exact
        # boundary; passing through at EOF briefly exposes square footage.
        # The explicit enable interval still releases it at the authored end.
        parts.append(f"[{p}pass][{p}card]overlay=0:0:eof_action=repeat:repeatlast=1:enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[{p}out]")
        vlabel = f"{p}out"
    return vlabel
