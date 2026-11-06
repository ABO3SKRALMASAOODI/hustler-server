"""Drawing the pictures of one upload: the blurred fill and the filmstrip.

Both read rectangles and frames measured by picture_map.py (written into the
items by the tools), so a render never guesses where a photo sits.

THE FILL lives inside the picture's own block (renderer._normalize_video):
everything after it, a dissolve, a zoom, a grade, a caption, reads the filled
picture without knowing it is there. Its blur runs at a sixth of the frame:
4 ms a frame at 1080p where the full-size boxblur of ``pad_blur`` costs 30.

THE FILMSTRIP is a clip precomputed in ``prepare``: sub-pixel scrolling is a
per-frame resample ffmpeg's crop and overlay cannot do (both land on whole
pixels, and a slow scroll judders), and the frames are composited in YUV, so
a photo reaches the program with the source's own values, no colour matrix on
the way. It is laid over the program at stage ``world``; the program's grade
is applied to it because that stage sits after the grade.
"""

import hashlib
import math
import os
import subprocess

import fxprims
import wipe_masks

# The bars' edge is one analysis pixel wide: stay inside it.
RECT_INSET = 1.0 / 640.0
FILL_DIVISOR = 6
STRIP_PARALLAX = 0.35
STRIP_EASE = 0.12               # share of the run spent reaching full speed
STRIP_DIM = 0.45


def _even(v):
    return int(v) // 2 * 2


def rect_pixels(rect, sw, sh):
    """(x, y, w, h) of a fraction rectangle on a sw x sh frame: even, and
    just inside the sides that have a bar."""
    x0, y0, x1, y1 = (rect["x0"], rect["y0"], rect["x1"], rect["y1"]) \
        if isinstance(rect, dict) else rect
    ix = math.ceil(RECT_INSET * sw) if x0 > 0 else 0
    iy = math.ceil(RECT_INSET * sw) if y0 > 0 else 0
    x = min(sw - 2, _even(math.ceil(x0 * sw) + ix + 1))
    y = min(sh - 2, _even(math.ceil(y0 * sh) + iy + 1))
    r = sw if x1 >= 1 else int(x1 * sw) - ix
    b = sh if y1 >= 1 else int(y1 * sh) - iy
    return x, y, max(2, _even(r - x)), max(2, _even(b - y))


def fill_items(edl):
    return [i for i in (edl or {}).get("fx") or []
            if isinstance(i, dict) and i.get("type") == "photo_fill"]


def fill_at(items, src_t):
    """(item, span) of the fill drawing source second ``src_t``, or None."""
    for it in items or []:
        for sp in it.get("spans") or []:
            if float(sp["t0"]) - 1e-6 <= src_t < float(sp["t1"]) - 1e-6:
                return it, sp
    return None


def fill_chain(parts, in_label, out_label, W, H, uid, *, item, span, src_size,
               tail, late="", frames=None, fps=None):
    """Bring in_label (the source's frames) to W x H: the picture fitted,
    over its own blurred, enlarged copy. No bars, and no pixel of the picture
    touched beyond the fit.

    A held photo is composed once and looped: every frame of it is the same,
    so 83 photos cost 83 frames of this chain, not 10,000."""
    sw, sh = src_size
    if span.get("still") and frames and fps and int(frames) > 1:
        pick = min(2, int(frames) - 1)      # clear of the join's own frame
        parts.append(f"[{in_label}]trim=start_frame={pick}:end_frame="
                     f"{pick + 1},setpts=PTS-STARTPTS[flS{uid}]")
        in_label = f"flS{uid}"
        tail = (f"loop=loop={int(frames) - 1}:size=1:start=0,"
                f"setpts=N/{fxprims.fps3(fps):.3f}/TB,{tail}")
    x, y, w, h = rect_pixels(span["rect"], sw, sh)
    bw, bh = max(16, _even(W / FILL_DIVISOR)), max(16, _even(H / FILL_DIVISOR))
    sigma = max(1.0, float(item.get("blur") or 0.05) * bw)
    dim = float(item.get("dim") if item.get("dim") is not None else 0.2)
    keep = 1.0 - dim
    lut = f"lutyuv=y=16+(val-16)*{keep:.3f}," if dim > 1e-3 else ""
    parts.append(f"[{in_label}]crop={w}:{h}:{x}:{y},format=yuv420p,"
                 f"split[flA{uid}][flB{uid}]")
    # threads=1: a scale filter starts a thread per core of its own, and a
    # film has hundreds of these (renderer._filter_thread_args).
    parts.append(f"[flA{uid}]scale={bw}:{bh}:force_original_aspect_ratio="
                 f"increase:flags=fast_bilinear:threads=1,crop={bw}:{bh},"
                 f"gblur=sigma={sigma:.2f},{lut}scale={W}:{H}:flags=bilinear:"
                 f"threads=1[flBG{uid}]")
    parts.append(f"[flB{uid}]scale={W}:{H}:force_original_aspect_ratio="
                 f"decrease:flags=lanczos:threads=1,setsar=1[flFG{uid}]")
    parts.append(f"[flBG{uid}][flFG{uid}]overlay=(W-w)/2:(H-h)/2,{tail}"
                 f"{late}[{out_label}]")


class _Fill:
    """The fill is drawn in the blocks; the stage only keeps the item in the
    plan (its presence forces the normalized block clock)."""
    FILTERS = ("crop", "split", "scale", "gblur", "lutyuv", "overlay", "loop")
    KEEPS_FRAME_TAGS = True

    def stage(self, ctx, items):
        return ctx.label


FILL = _Fill()


# ── the strip ────────────────────────────────────────────────────────────

def _ffmpeg_raw(args, data=None, timeout=120):
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *args],
                       input=data, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or b"").decode("utf-8", "replace")[-200:])
    return p.stdout


def _planes(raw, w, h):
    """Y, U, V arrays of one yuv420p frame."""
    import numpy as np
    a = np.frombuffer(raw, np.uint8)
    cw, ch = w // 2, h // 2
    if a.size < w * h + 2 * cw * ch:
        raise RuntimeError("a frame came back short")
    return (a[:w * h].reshape(h, w),
            a[w * h:w * h + cw * ch].reshape(ch, cw),
            a[w * h + cw * ch:w * h + 2 * cw * ch].reshape(ch, cw))


def grab_photo(src, t, rect, sw, sh, sar, height):
    """One picture of the strip at ``height``: (Y, U, V, width)."""
    x, y, w, h = rect_pixels(rect, sw, sh) if rect else (0, 0, sw, sh)
    aspect = (w * float(sar or 1.0)) / float(h)
    out_w = max(2, _even(round(height * aspect)))
    raw = _ffmpeg_raw(["-ss", f"{max(0.0, float(t)):.3f}", "-i", src,
                       "-map", "0:v:0", "-frames:v", "1", "-vf",
                       f"crop={w}:{h}:{x}:{y},scale={out_w}:{height}:"
                       "flags=lanczos,format=yuv420p",
                       "-f", "rawvideo", "-"])
    return (*_planes(raw, out_w, height), out_w)


def _resize_nearest(plane, w, h):
    import numpy as np
    ys = (np.arange(h) * plane.shape[0] / h).astype(np.int32)
    xs = (np.arange(w) * plane.shape[1] / w).astype(np.int32)
    return plane[ys][:, xs]


def _backdrop(photos, S, H, blur_w):
    """The strip's backdrop: every picture, covering the frame's height,
    run together into one blurred, dimmed band S wide."""
    import numpy as np
    bw, bh = max(32, _even(S / 8)), max(16, _even(H / 8))
    tile = max(2, bw // len(photos) // 2 * 2)
    bw = tile * len(photos)
    planes = [np.concatenate([_resize_nearest(p[k], tile // (2 if k else 1),
                                              bh // (2 if k else 1))
                              for p in photos], axis=1) for k in range(3)]
    raw = b"".join(p.tobytes() for p in planes)
    sigma = max(4.0, 0.05 * blur_w / 8.0)
    out = _ffmpeg_raw(["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                       f"{bw}x{bh}", "-i", "-", "-vf",
                       f"gblur=sigma={sigma:.2f},scale={S}:{H}:flags=bilinear,"
                       f"lutyuv=y=16+(val-16)*{1.0 - STRIP_DIM:.3f}",
                       "-frames:v", "1", "-f", "rawvideo", "-"], raw)
    return _planes(out, S, H)


def _run_position(u, k=STRIP_EASE):
    """0..1 along the run: constant speed between short eased ends."""
    u = min(max(float(u), 0.0), 1.0)
    if k <= 0:
        return u
    v = 1.0 / (1.0 - k)
    if u < k:
        return v * u * u / (2.0 * k)
    if u > 1.0 - k:
        w = 1.0 - u
        return 1.0 - v * w * w / (2.0 * k)
    return v * (u - k / 2.0)


class StripLayout:
    """Where everything sits along the strip, in Y pixels."""

    def __init__(self, widths, W, H, height_share, gap_share, reverse=False):
        self.W, self.H = int(W), int(H)
        self.h = max(4, _even(H * float(height_share)))
        self.gap = _even(W * float(gap_share))
        self.y0 = _even((self.H - self.h) / 2)
        self.pad = _even(W)
        order = list(range(len(widths)))
        if reverse:
            order.reverse()
        self.order = order
        self.x = {}
        cur = self.pad
        for k in order:
            self.x[k] = cur
            cur += int(widths[k]) + self.gap
        self.S = _even(cur - self.gap + self.pad) + 2
        self.widths = widths

    def centre(self, k):
        return self.x[k] + self.widths[k] / 2.0

    def left_edge(self, u, first, last):
        a, b = self.centre(first), self.centre(last)
        return a + (b - a) * _run_position(u) - self.W / 2.0


def compose_strip(path, photos, layout, n, *, first_frame, run_frames, fps,
                  backdrop, final):
    """Write ``n`` frames of the run to ``path``. ``photos``: (Y, U, V, w) in
    the customer's order, ``first_frame``: where this clip starts in the run
    of ``run_frames`` frames."""
    import numpy as np
    W, H, S, band = layout.W, layout.H, layout.S, layout.h
    CW, CH = W // 2, H // 2
    sy = np.full((band, S), 16, np.uint8)
    su = np.full((band // 2, S // 2), 128, np.uint8)
    sv = su.copy()
    cov = np.zeros(S, np.uint8)
    for k, (y, u, v, w) in enumerate(photos):
        x = layout.x[k]
        sy[:, x:x + w] = y
        su[:, x // 2:x // 2 + w // 2] = u
        sv[:, x // 2:x // 2 + w // 2] = v
        cov[x:x + w] = 255
    last = len(photos) - 1
    first_k, last_k = 0, last
    travel = abs(layout.centre(last_k) - layout.centre(first_k))
    bg = None
    if backdrop == "blur":
        bgS = _even(W + travel * STRIP_PARALLAX) + 2
        bg = _backdrop(photos, bgS, H, W)
    enc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo",
         "-pix_fmt", "yuv420p", "-s", f"{W}x{H}", "-r", f"{float(fps):.3f}",
         "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "12" if final else "17", "-pix_fmt", "yuv420p", path],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def shifted(arr, x, width):
        ix = int(math.floor(x))
        f = int((x - ix) * 256)
        a = arr[..., ix:ix + width].astype(np.uint16)
        if f == 0:
            return a
        b = arr[..., ix + 1:ix + 1 + width].astype(np.uint16)
        return (a * (256 - f) + b * f) >> 8

    try:
        div = max(1, int(run_frames) - 1)
        for j in range(int(n)):
            u = (first_frame + j) / div
            x = layout.left_edge(u, first_k, last_k)
            if bg is None:
                oy = np.full((H, W), 16, np.uint8)
                ou = np.full((CH, CW), 128, np.uint8)
                ov = ou.copy()
            else:
                bx = _run_position(u) * travel * STRIP_PARALLAX
                if layout.order[0] != 0:
                    bx = travel * STRIP_PARALLAX - bx
                oy = shifted(bg[0], bx, W).astype(np.uint8)
                ou = shifted(bg[1], bx / 2.0, CW).astype(np.uint8)
                ov = shifted(bg[2], bx / 2.0, CW).astype(np.uint8)
            c = shifted(cov, x, W)
            y0, cy0 = layout.y0, layout.y0 // 2
            py = shifted(sy, x, W)
            oy[y0:y0 + band] = (py * c + oy[y0:y0 + band].astype(np.uint16)
                                * (255 - c)) // 255
            cc = shifted(cov[::2], x / 2.0, CW)
            for pl, s_ in ((ou, su), (ov, sv)):
                px = shifted(s_, x / 2.0, CW)
                rows = pl[cy0:cy0 + band // 2]
                pl[cy0:cy0 + band // 2] = (px * cc + rows.astype(np.uint16)
                                           * (255 - cc)) // 255
            enc.stdin.write(oy.tobytes())
            enc.stdin.write(ou.tobytes())
            enc.stdin.write(ov.tobytes())
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0 or not os.path.exists(path):
        raise RuntimeError("the filmstrip clip could not be encoded")


def _key(plan, item, f0, n):
    blob = repr([item.get("photos"), item.get("direction"), item.get("height"),
                 item.get("gap"), item.get("backdrop"), item.get("run"),
                 item.get("start"), item.get("end"), plan.W, plan.H,
                 plan.fps3, plan.preview, f0, n])
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


class _Filmstrip:
    FILTERS = ("trim", "settb", "setpts", "format", "fade", "tpad", "overlay")
    KEEPS_FRAME_TAGS = True

    def _span(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        f1 = min(f1, int(plan.n_frames))
        # A piece's run may have begun before the piece: r0 can be negative.
        run = item.get("run") or [item["start"], item["end"]]
        r0 = int(round(float(run[0]) * plan.fps3))
        r1 = int(round(float(run[1]) * plan.fps3))
        return f0, f1, r0, r1

    def prepare(self, plan, item):
        wipe_masks.precompute_guard()
        env = wipe_masks.render_env(plan)
        if not plan.workdir or not plan.W or not plan.H:
            return ("omit", "no workdir/geometry for the filmstrip")
        f0, f1, r0, r1 = self._span(plan, item)
        n = f1 - f0
        if n <= 0:
            return ("omit", "its window is outside this program")
        key = _key(plan, item, f0, n)
        path = os.path.join(plan.workdir, f"filmstrip_{key}.mp4")
        ikey = f"filmstrip:{item.get('id')}"
        if env.get("audio_only"):
            plan.add_input(ikey, ["-f", "lavfi", "-i",
                                  f"color=black:s={plan.W}x{plan.H}:"
                                  f"r={plan.fps3:.3f}:d=1"])
            return None
        if not os.path.exists(path):
            src = env.get("src_path")
            if not src:
                return ("omit", "no source frames on this render")
            self._build(plan, item, src, path, n, f0 - r0, r1 - r0)
        plan.add_input(ikey, ["-i", path])
        return None

    def _build(self, plan, item, src, path, n, first_frame, run_frames):
        import media
        info = media.probe(src)
        sw, sh = int(info["width"]), int(info["height"])
        sar = float(info.get("sar") or 1.0)
        W, H = int(plan.W), int(plan.H)
        height = max(4, _even(H * float(item.get("height") or 0.78)))
        photos = [grab_photo(src, p["t"], p.get("rect"), sw, sh, sar, height)
                  for p in item["photos"]]
        layout = StripLayout([p[3] for p in photos], W, H,
                             item.get("height") or 0.78,
                             item.get("gap") if item.get("gap") is not None
                             else 0.02,
                             reverse=item.get("direction") == "right")
        tmp = path + ".part.mp4"
        compose_strip(tmp, photos, layout, n, first_frame=first_frame,
                      run_frames=run_frames, fps=plan.fps3,
                      backdrop=item.get("backdrop") or "blur",
                      final=not plan.preview)
        os.replace(tmp, path)

    def stage(self, ctx, items):
        label = ctx.label
        for it in items:
            mark = len(ctx.parts)
            try:
                label = self._one(ctx, label, it)
            except Exception as e:  # noqa: BLE001 — degrade this strip
                del ctx.parts[mark:]
                ctx.note(it, "omit", f"compile failed ({type(e).__name__}: "
                         f"{str(e)[:120]})")
        return label

    def _one(self, ctx, label, item):
        f0, f1, r0, r1 = self._span(ctx.plan, item)
        n = f1 - f0
        lab = ctx.input_label(f"filmstrip:{item.get('id')}")
        if lab is None or n <= 0:
            raise RuntimeError("the strip's clip was never bound")
        fps3 = ctx.fps3
        fade = float(item.get("fade_s") if item.get("fade_s") is not None
                     else 0.5)
        fades = ""
        if fade > 1e-3:
            into = (f0 - r0) / fps3
            if into < fade:
                d = fade - into
                fades += f",fade=t=in:st=0:d={d:.3f}:alpha=1"
            left = (r1 - f1) / fps3
            if left < fade:
                d = fade - left
                fades += (f",fade=t=out:st={max(0.0, n / fps3 - d):.3f}:"
                          f"d={d:.3f}:alpha=1")
        grade = getattr(ctx.plan, "grade_chain", None)
        graded = f",format=yuv420p,{grade}" if grade else ""
        u = ctx.uid("fs")
        ctx.parts.append(
            f"[{lab}]trim=end_frame={n}{graded},{wipe_masks.pts(fps3)},"
            f"format=yuva420p{fades}{fxprims.repad(f0, ctx.fps, alpha=True)}"
            f"[{u}]")
        ctx.parts.append(f"[{label}][{u}]"
                         f"{fxprims.composite_overlay(ctx.parts, label)}:"
                         f"enable='{fxprims.enable_expr([(f0, f1)])}'"
                         f"[{u}o]")
        return f"{u}o"


FILMSTRIP = _Filmstrip()
