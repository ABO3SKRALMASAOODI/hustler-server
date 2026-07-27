"""Round 51 — the floating rounded window, and the gradient behind it.

"Put it in that rounded window on a gradient" is the single most-asked-for
treatment for a screen recording, and it is three effects that have to agree:
the picture inset, its corners rounded, a soft shadow under it, all sitting on
a colour or gradient backdrop.

It renders as ONE overlay of ONE pre-built RGBA plate:

    backdrop + shadow are baked into the plate's OPAQUE pixels
    the picture area is a rounded TRANSPARENT hole

so the graph is `scale, pad, overlay` — three cheap filters — instead of a geq
pass computing a rounded-rectangle distance field per pixel per frame. On the
1-vCPU box that is the difference between a treatment people can use and one
that doubles every render. The corner antialiasing and the shadow's falloff are
resolution-independent because they are drawn once, by Pillow, at output size.

`gradient_image` is the SAME renderer add_color_screen uses for its cards —
moved here so both call one implementation. agent_tools._gradient_image now
delegates; there is no second gradient.
"""
import os

GRADIENT_DIRECTIONS = ("vertical", "horizontal", "diagonal", "radial")


def hex_rgb(color):
    c = str(color).lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def gradient_image(w, h, rgb1, rgb2, direction):
    """A two-colour linear/radial gradient. numpy-vectorised (already a hard
    dependency of the worker) so a 1080x1920 backdrop builds in a few ms."""
    import numpy as np
    from PIL import Image
    if direction == "radial":
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
        d = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
        t = np.clip(d / np.sqrt(2.0), 0.0, 1.0)
    elif direction == "horizontal":
        t = np.tile(np.linspace(0.0, 1.0, w), (h, 1))
    elif direction == "diagonal":
        gx = np.linspace(0.0, 1.0, w)[None, :]
        gy = np.linspace(0.0, 1.0, h)[:, None]
        t = np.clip((gx + gy) / 2.0, 0.0, 1.0)
    else:                                            # vertical (default)
        t = np.tile(np.linspace(0.0, 1.0, h), (w, 1)).T
    t = t[..., None]
    c1 = np.array(rgb1, dtype=np.float32)
    c2 = np.array(rgb2, dtype=np.float32)
    arr = (c1 * (1.0 - t) + c2 * t).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _even(n):
    return max(2, int(round(float(n) / 2)) * 2)


def picture_box(W, H, inset):
    """(pw, ph, ox, oy) — the inset picture's size and top-left corner.

    The scale is UNIFORM (both axes shrink by `inset`), which is what keeps the
    inset window the same shape as the output. Shrinking only the width would
    stretch the picture, and nothing in the rest of the chain would show it.
    """
    k = 1.0 - float(inset)
    pw, ph = _even(W * k), _even(H * k)
    return pw, ph, _even((W - pw) / 2), _even((H - ph) / 2)


def build_plate(path, W, H, spec):
    """Write the RGBA plate for `spec` (a ScreenFrame dict) and return
    (path, (pw, ph, ox, oy)). Cached by the caller on the parameters."""
    from PIL import Image, ImageDraw, ImageFilter

    inset = float(spec.get("inset", 0.08))
    shadow = float(spec.get("shadow", 0.5))
    pw, ph, ox, oy = picture_box(W, H, inset)
    # radius is a fraction of the PICTURE's short side, so the same value looks
    # the same on a 9:16 phone edit and a 16:9 desktop one.
    radius = int(round(float(spec.get("radius", 0.04)) * min(pw, ph)))
    radius = max(0, min(radius, min(pw, ph) // 2))

    c1 = hex_rgb(spec.get("background") or "#0B0B0B")
    c2 = spec.get("background2")
    direction = spec.get("direction") or "vertical"
    if c2:
        base = gradient_image(W, H, c1, hex_rgb(c2), direction)
    else:
        base = Image.new("RGB", (W, H), c1)

    box = (ox, oy, ox + pw, oy + ph)
    if shadow > 0.001:
        # A drop shadow is only ever visible OUTSIDE the picture — the rest of
        # it lands in the transparent hole and is thrown away. Drawn on its own
        # layer and blurred, then composited as a multiply-ish darkening so it
        # reads on a light backdrop as well as a dark one.
        spread = max(2, int(round(min(W, H) * 0.02 * (0.4 + shadow))))
        drop = max(1, int(round(spread * 0.55)))
        layer = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle(
            (box[0] - spread * 0.15, box[1] - spread * 0.15 + drop,
             box[2] + spread * 0.15, box[3] + spread * 0.15 + drop),
            radius=radius + int(spread * 0.15),
            fill=int(200 * min(1.0, shadow)))
        layer = layer.filter(ImageFilter.GaussianBlur(spread))
        base = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)),
                               base, layer)

    # The hole. Alpha 0 inside the rounded picture rect, 255 everywhere else —
    # so overlaying this plate over the padded picture rounds its corners and
    # paints the backdrop in one composite. Drawn at 4x and downsampled because
    # Pillow's rounded_rectangle is not antialiased and a hard corner staircase
    # is the one artefact that makes this look cheap.
    ss = 4
    mask = Image.new("L", (W * ss, H * ss), 255)
    ImageDraw.Draw(mask).rounded_rectangle(
        (box[0] * ss, box[1] * ss, box[2] * ss - 1, box[3] * ss - 1),
        radius=radius * ss, fill=0)
    mask = mask.resize((W, H), Image.LANCZOS)

    plate = base.convert("RGBA")
    plate.putalpha(mask)
    plate.save(path)
    return path, (pw, ph, ox, oy)


def plate_key(W, H, spec):
    """Filename-safe identity of a plate, so two renders of the same EDL (and
    two effects in one render) reuse one file instead of redrawing it."""
    return "sf_{}x{}_{}_{}_{}_{}_{}_{}".format(
        W, H,
        f"{float(spec.get('inset', 0.08)):.3f}",
        f"{float(spec.get('radius', 0.04)):.3f}",
        f"{float(spec.get('shadow', 0.5)):.3f}",
        str(spec.get("background") or "#0B0B0B").lstrip("#"),
        str(spec.get("background2") or "none").lstrip("#"),
        spec.get("direction") or "vertical")


def plate_path(workdir, W, H, spec):
    return os.path.join(workdir, plate_key(W, H, spec) + ".png")


# ── Mid-video aspect change ────────────────────────────────────────────────
# A rendered file has one resolution for its whole duration. So "go vertical
# for this bit" is an animated MATTE inside the fixed canvas: the bars close in
# over `duration_s` and stay closed until the next shift. Nothing about the
# timeline, the audio or the caption timing changes — which is precisely why
# this is safe to do mid-video, and why it can be previewed instantly.

# How hard the picture pushes in as the frame narrows, per unit of frame area
# lost — and the ceiling on it. A 16:9 -> 9:16 shift loses 68% of the frame;
# at this gain that is a 34% push, which reads as a deliberate reframe. Higher
# and the subject's head leaves the top of a vertical crop.
SHIFT_ZOOM_GAIN = 0.5
SHIFT_ZOOM_MAX = 0.35


def ratio_window(ratio, W, H):
    """(wf, hf): the fraction of the canvas width/height the target aspect
    occupies, centred. 'source' (or a ratio equal to the canvas) is the whole
    frame, which is how a shift BACK to full frame is expressed."""
    if not ratio or ratio == "source":
        return 1.0, 1.0
    try:
        rw, rh = (float(x) for x in str(ratio).split(":"))
    except (TypeError, ValueError):
        return 1.0, 1.0
    if not (rw > 0 and rh > 0 and W > 0 and H > 0):
        return 1.0, 1.0
    canvas, target = float(W) / float(H), rw / rh
    if target < canvas - 1e-6:               # taller than the canvas: pillars
        return target / canvas, 1.0
    if target > canvas + 1e-6:               # wider: letterbox
        return 1.0, canvas / target
    return 1.0, 1.0


def shift_tracks(shifts, W, H, prog):
    """Keyframe tracks for the matte, as fractions of the PROGRAM duration.

    Returns (w_points, h_points, zoom_points) in the shape travel.path_value_expr
    consumes: [{"f": .., "v": ..}]. Each shift contributes a hold at its start
    value and a move to its target — the hold is what makes the morph begin at
    `at` rather than drifting from the previous shift, and zero-delta segments
    cost nothing because the expression only emits the deltas.
    """
    if prog <= 0:
        return [], [], []
    wf, hf = 1.0, 1.0
    wpts = [{"f": 0.0, "v": 1.0}]
    hpts = [{"f": 0.0, "v": 1.0}]
    zpts = [{"f": 0.0, "v": 0.0}]
    for s in sorted(shifts, key=lambda x: float(x["at"])):
        at = max(0.0, float(s["at"]))
        dur = max(0.01, float(s.get("duration_s") or 0.8))
        nwf, nhf = ratio_window(s.get("ratio"), W, H)
        nz = 0.0
        if s.get("zoom", True):
            nz = min(SHIFT_ZOOM_MAX, (1.0 - nwf * nhf) * SHIFT_ZOOM_GAIN)
        f0 = min(1.0, at / prog)
        f1 = min(1.0, (at + dur) / prog)
        for pts, old, new in ((wpts, wf, nwf), (hpts, hf, nhf),
                              (zpts, zpts[-1]["v"], nz)):
            pts.append({"f": round(f0, 5), "v": round(old, 5)})
            pts.append({"f": round(f1, 5), "v": round(new, 5)})
        wf, hf = nwf, nhf
    for pts in (wpts, hpts, zpts):
        if pts[-1]["f"] < 1.0:
            pts.append({"f": 1.0, "v": pts[-1]["v"]})
    return wpts, hpts, zpts


def track_varies(pts):
    return any(abs(p["v"] - pts[0]["v"]) > 1e-4 for p in pts)
