"""A found still as a moving clip (lane K round six, 14 Nov 2026).

Today's renderer draws a cutaway still as one fixed crop: an overlay has no camera, and a move the compiler wrote on
a photograph was held (`moves_held_on_pictures`). The judge moves the camera on most pictures, fast in and easing
out, and opens 28% of its shots on a soft frame (judge/metromedia/TEARDOWN.md rows 5 and 11); the rater read our
found pictures as "windowed stills on a dark page" (RATINGS/2026-11-13-K5.json).

So a still shot whose window moves, or that opens soft, is baked here into a short clip of exactly its frames: the
still seen through each frame's window, the first frames blurred when the shot opens soft, encoded once. The lowering
then places the clip as an ordinary full-frame cutaway, so the film's look reaches it as it reaches any clip
(renderer.cutaway_look_chain) and nothing in the renderer changes.

One ffmpeg process a still (`_bake_ffmpeg`): the part of the still the move uses, twice the output's size, then
zoompan through the windows (the path as straight segments between the frames where it bends). Measured on the box
image at 2 CPUs, 22 frames at 720 x 1280: 0.33 s, against 0.85 s resampling each frame in Pillow, and a film holds
about twenty of them inside its deadline. Pillow (`_bake_pillow`) is the second rung when ffmpeg refuses. No model, no
network. A still that cannot be baked is drawn as before.
"""
import hashlib
import json
import math
import os
import subprocess

# Finishes that change with the story's section (judge/metromedia/TEARDOWN.md row 10: B&W on 12% of the judge's
# shots, tape and projector on 11 to 13% in two reels, never film-wide). A found still the Score marks is baked with
# its finish, so the finish is on that picture only and the take beside it stays as shot. Each is one ffmpeg chain
# after the camera: no file, no model.
#   archive: the past. Black and white, a little more contrast, moving grain, the corners a stop down.
#   tape:    a broadcast, a screen, a recording. Scan lines, the red and blue a pixel apart, moving noise, colour up.
FINISHES = {
    "archive": "hue=s=0,eq=contrast=1.1:brightness=-0.015,noise=alls=13:allf=t,vignette=angle=PI/5",
    "tape": "rgbashift=rh=-2:bh=2,eq=saturation=1.18:contrast=1.05,noise=alls=9:allf=t,drawgrid=w=iw:h=3:t=1:color=black@0.22",
}
TAIL_F = 4                 # frames of the last window after the shot's end: a cutaway never runs out a frame early
SOFT_PX = 15.0             # blur radius of the first frame of a soft start, at a frame 1080 wide
CRF = 20


def ffmpeg():
    try:
        from . import boards
        return boards.FFMPEG
    except Exception:  # noqa: BLE001
        return os.environ.get("FFMPEG") or "ffmpeg"


def size_for(W, H):
    """The size a still is baked at: the film's frame, never over 720 on its short side (VALMERA_REEL_MAX sets it).
    Measured on the box image at 2 CPUs: 21 stills of one film took 25 s at 1080 and the film has a deadline; at 720 a
    half-second cutaway that is moving reads the same, and the previews are 720 wide."""
    cap = int(os.environ.get("VALMERA_REEL_MAX", "720"))
    k = min(1.0, cap / float(min(W, H)))
    return int(round(W * k / 2)) * 2, int(round(H * k / 2)) * 2


def key_of(still, rects, W, H, fps, soft, finish=None):
    st = os.stat(still)
    raw = json.dumps([os.path.basename(still), st.st_size, [[round(float(v), 5) for v in r] for r in rects], W, H, fps, soft] + ([finish] if finish else []), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:14]


def _keys(rows, most=10, tol=0.0008):
    """Frames where a path bends: straight lines between them stay within `tol` of it."""
    n = len(rows)
    if n <= 2:
        return list(range(n))
    keep = [0, n - 1]
    while len(keep) < most:
        worst, at = tol, None
        for p, q in zip(keep, keep[1:]):
            for i in range(p + 1, q):
                u = (i - p) / (q - p)
                d = max(abs(rows[i][k] - (rows[p][k] + (rows[q][k] - rows[p][k]) * u)) for k in range(len(rows[i])))
                if d > worst:
                    worst, at = d, i
        if at is None:
            break
        keep = sorted(keep + [at])
    return keep


def _expr(vals, keys):
    """A piecewise-linear function of zoompan's output frame number `on` through vals at keys, as one flat sum."""
    out = f"{vals[keys[0]]:.6f}"
    for p, q in zip(keys, keys[1:]):
        slope = (vals[q] - vals[p]) / (q - p)
        if abs(slope) > 1e-9:
            out += f"+({slope:.8f})*min(max(on-{p}\\,0)\\,{q - p})"
    return out


def _bake_ffmpeg(still, rects, w, h, fps, out_path, soft, sw, sh, finish=None):
    n = len(rects)
    # the part of the still the move uses, grown to the frame's shape (padded where the still ends: never shown)
    x0, y0 = min(r[0] for r in rects) * sw, min(r[1] for r in rects) * sh
    x1, y1 = max(r[2] for r in rects) * sw, max(r[3] for r in rects) * sh
    ew, eh = x1 - x0, y1 - y0
    if ew / eh < w / h:
        ew = eh * w / h
    else:
        eh = ew * h / w
    cw, ch = int(min(sw - int(x0), math.ceil(ew))), int(min(sh - int(y0), math.ceil(eh)))
    ex0, ey0 = int(x0), int(y0)
    big = 2
    zs = [ew / max((r[2] - r[0]) * sw, 1.0) for r in rects]
    xs = [(r[0] * sw - ex0) / ew * (w * big) for r in rects]
    ys = [(r[1] * sh - ey0) / eh * (h * big) for r in rects]
    keys = _keys([[z, x / (w * big), y / (h * big)] for z, x, y in zip(zs, xs, ys)])
    vf = [f"crop={cw}:{ch}:{ex0}:{ey0}", f"pad={int(math.ceil(ew))}:{int(math.ceil(eh))}:0:0:black",
          f"scale={w * big}:{h * big}:flags=bicubic",
          f"zoompan=z={_expr(zs, keys)}:x={_expr(xs, keys)}:y={_expr(ys, keys)}:d={n}:s={w}x{h}:fps={fps:g}"]
    for i in range(int(soft or 0)):
        sig = (SOFT_PX * (w / 1080.0) * (1.0 - i / float(soft)) ** 1.3 + 0.6) / 2.0
        vf.append(f"gblur=sigma={sig:.2f}:enable=eq(n\\,{i})")
    if finish in FINISHES:
        vf.append(FINISHES[finish])
    tmp = out_path + ".part.mp4"
    cmd = [ffmpeg(), "-v", "error", "-y", "-i", still, "-vf", ",".join(vf), "-frames:v", str(n), "-an", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", str(CRF), "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 800:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError("ffmpeg: " + (r.stderr or "")[-200:])
    os.replace(tmp, out_path)


def _bake_pillow(im, rects, w, h, fps, out_path, soft, finish=None):
    from PIL import Image, ImageFilter
    sw, sh = im.size
    tmp = out_path + ".part.mp4"
    cmd = [ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-"] \
        + (["-vf", FINISHES[finish]] if finish in FINISHES else []) + [
           "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CRF), "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        last_box, last_img = None, None
        for i, r in enumerate(rects):
            box = (max(0.0, r[0] * sw), max(0.0, r[1] * sh), min(float(sw), r[2] * sw), min(float(sh), r[3] * sh))
            soft_now = soft and i < soft
            if box == last_box and not soft_now and last_img is not None:
                fr = last_img
            else:
                fr = im.resize((w, h), Image.BICUBIC if box[2] - box[0] < w else Image.BILINEAR, box=box, reducing_gap=2.0)
                if soft_now:
                    fr = fr.filter(ImageFilter.GaussianBlur(SOFT_PX * (w / 1080.0) * (1.0 - i / float(soft)) ** 1.3 + 0.6))
                else:
                    last_box, last_img = box, fr
            proc.stdin.write(fr.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait(timeout=60) != 0:
            raise RuntimeError(f"ffmpeg: {err[-200:]}")
    except Exception:
        proc.kill()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    os.replace(tmp, out_path)


def bake(still, rects, W, H, fps, out_path, soft=0, finish=None):
    """Write the clip. rects: one window per frame, source fractions [x0, y0, x1, y1]. Raises on any fault."""
    from PIL import Image
    w, h = size_for(W, H)
    with Image.open(still) as im:
        sw, sh = im.size
        frames = [[min(max(float(v), 0.0), 1.0) for v in r] for r in rects]
        frames = frames + [frames[-1]] * TAIL_F
        if any((r[2] - r[0]) * sw < 2 or (r[3] - r[1]) * sh < 2 for r in frames):
            raise ValueError("a window holds no picture")
        how = "ffmpeg"
        try:
            if os.environ.get("VALMERA_REEL_PILLOW") == "1":
                raise RuntimeError("pillow asked for")
            if any(abs(((r[2] - r[0]) * sw) / max((r[3] - r[1]) * sh, 1e-6) / (w / float(h)) - 1.0) > 0.02 for r in frames):
                raise RuntimeError("a window of another shape than the frame: resampled frame by frame")
            _bake_ffmpeg(still, frames, w, h, fps, out_path, soft, sw, sh, finish)
        except Exception:  # noqa: BLE001 - the second rung: each frame resampled here
            how = "pillow"
            rgb = im.convert("RGB")
            rgb.load()
            _bake_pillow(rgb, frames, w, h, fps, out_path, soft, finish)
    return {"w": w, "h": h, "dur": round(len(frames) / float(fps), 3), "fps": float(fps), "how": how, "finish": finish if finish in FINISHES else None}
