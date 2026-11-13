"""lamp: a word that is a light, fed by the voice. On its word the display line switches on in the film's
ink colour and the take is relit by it on every frame: the wall takes its colour and falls off with
distance, the side of the speaker that faces it picks up a rim, the speaker throws a shadow on the wall
away from it, and the room's own exposure comes down as a camera would stop down. How bright it burns
on a frame is the level of the voice on that frame, so it swells on a stressed syllable and dies in a
breath. A pasted word looks like an app; a word that lights the set looks shot (ALIEN L1 and L2, one device).

The relit take is baked once (numpy, light solved at a quarter size, applied at full size) into a clip
inside the work folder, and the scene's shot is pointed at it; the word itself stays type, drawn by the
engine over (or, with a matte, behind) the person. The matte is the scene's own ``matte`` asset: this
module runs no model. Without one the wall is lit and nobody casts a shadow.

ask  {"dev": "lamp", "at": [a, z], "t": "LOYAL", "ink": "#FF4A1C", "font": "InterDisplay-Black.ttf",
      "width": 0.62 (of the frame), "seat": [centre x, baseline y] (default: beside the head, found),
      "faces": [[x, y, w, h]] frame fractions, "room": 0.42 (how far the room falls at full voice),
      "behind": true (the word stands behind the person when the scene has a matte), "tail": 10 (frames
      the light takes to die after the word), "audio": programme-time audio file (default: main via the spine)}
"""
import os
import subprocess

import numpy as np

from . import glyphs, sense

K = 0.25                      # light is low frequency: it is solved on a quarter-size grid


def _hex(c):
    c = (c or "#FF4A1C").lstrip("#")
    return np.array([int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], np.float32)


def seat_of(faces, bw, bh):
    """Beside the head, on the side with more room, the baseline near the eyes: a lamp at arm's length."""
    if not faces:
        return 0.5, 0.30
    fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]
    left, right = fx, 1.0 - (fx + fw)
    if max(left, right) >= bw + 0.04:
        cx = (fx - 0.03 - bw / 2) if left > right else (fx + fw + 0.03 + bw / 2)
        return float(min(max(cx, bw / 2 + 0.04), 1 - bw / 2 - 0.04)), float(min(max(fy + fh * 0.62, 0.14 + bh), 0.8))
    return 0.5, float(max(fy - 0.035, 0.12 + bh))          # no room beside him: over his head


def light_field(E):
    """How much light a glowing word throws on every point of its own plane: the word blurred at three reaches
    (a near glow, the spill, the room), so it falls off with distance and never ends on an edge."""
    import cv2
    h = E.shape[0]
    out = np.zeros_like(E)
    for sig, wgt in ((0.012, 0.9), (0.05, 1.0), (0.16, 1.25)):
        b = cv2.GaussianBlur(E, (0, 0), sig * h)
        out += wgt * b / max(float(b.max()), 1e-4)
    return out / 3.15


def writer(path, w, h, fps):
    cmd = [sense.ff(), "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
           "-an", "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv444p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "11", "-g", "15",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", path]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def relight(frame, matte, Lf, colour, e, room, centre):
    """One frame relit. frame: HxWx3 uint8. matte: quarter-size float 0..1 or None. Lf: quarter-size light field.
    colour: the lamp's. e: the lamp's level on this frame. centre: the word's middle on the quarter grid (x, y)."""
    import cv2
    H, W = frame.shape[:2]
    h, w = Lf.shape
    lin = (frame.astype(np.float32) / 255.0) ** 2.2
    small = cv2.resize(lin, (w, h), interpolation=cv2.INTER_AREA)
    lum = small @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    lx, ly = centre[0] - xx, centre[1] - yy
    dist = np.sqrt(lx * lx + ly * ly) + 1e-3
    lx, ly = lx / dist, ly / dist
    wall = Lf.copy()
    rim = np.zeros_like(Lf)
    if matte is not None:
        m = cv2.GaussianBlur(matte, (0, 0), 1.2)
        # the person as a rounded body: a height from the blurred matte, its slope the surface's lean
        body = cv2.GaussianBlur(matte, (0, 0), 0.035 * h)
        nx, ny = -cv2.Sobel(body, cv2.CV_32F, 1, 0, ksize=5), -cv2.Sobel(body, cv2.CV_32F, 0, 1, ksize=5)
        nz = 0.55 * max(float(np.hypot(nx, ny).max()), 1e-4)
        nn = np.sqrt(nx * nx + ny * ny + nz * nz)
        facing = np.clip((nx * lx + ny * ly) / nn + 0.3 * nz / nn, 0, 1)         # a lamp a little in front of his plane
        # the face's own relief, from its shading, read wide so no line is drawn on him: a brow and a cheek lean
        # toward the lamp, the far side of a nose leans away
        g = cv2.GaussianBlur(lum, (0, 0), 0.012 * h)
        bx, by = -cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=5), -cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=5)
        relief = cv2.GaussianBlur((bx * lx + by * ly) * 0.045, (0, 0), 0.006 * h)
        facing = np.clip(facing * (1.0 + np.clip(relief, -0.45, 0.6)), 0, 1.3)
        reach = np.clip(Lf * 2.4, 0, 1) ** 0.9
        rim = cv2.GaussianBlur(m * facing * reach, (0, 0), 0.004 * h)
        # his shadow on the wall: the matte thrown away from the lamp, longer and softer the farther it falls
        sh = np.zeros_like(m)
        for step, soft in ((0.03, 0.012), (0.06, 0.022), (0.10, 0.036)):
            far = cv2.remap(matte, xx + lx * step * h, yy + ly * step * h, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            sh = np.maximum(sh, cv2.GaussianBlur(far, (0, 0), soft * h))
        wall = Lf * (1.0 - 0.85 * np.clip(sh, 0, 1)) * (1.0 - m)
    up = lambda a: cv2.resize(a, (W, H), interpolation=cv2.INTER_CUBIC)          # noqa: E731
    wall_f, rim_f = np.clip(up(wall), 0, None)[..., None], np.clip(up(rim), 0, None)[..., None]
    alb = lin / np.maximum((lin @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None], 0.02) * 0.5 + 0.5      # a lit surface keeps its hue
    base = lin * (1.0 - room * e)
    light = colour[None, None] * e * (1.9 * wall_f * (0.25 + 1.5 * lin) * alb + 2.6 * rim_f * (0.12 + lin) * alb)
    out = base + light
    out = out / (1.0 + 0.35 * np.clip(out - 0.8, 0, None))                       # a shoulder: a lamp blooms, it does not clip
    return (np.clip(out, 0, 1) ** (1 / 2.2) * 255.0 + 0.5).astype(np.uint8)


def plan(l1, work, ask):
    fps = float(l1.get("fps") or 30)
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    a, z = [int(v) for v in ask["at"]]
    text = str(ask.get("t") or "").strip()
    if not text or z - a < 6:
        return {"skip": "lamp needs a word and 6 frames"}
    sh0 = sense.shot_at(l1, a)
    if sh0 is None or not sense.path_of(l1, work, sh0.get("pic")):
        return {"skip": "lamp needs a picture under its word"}
    tail = int(ask.get("tail") if ask.get("tail") is not None else 10)
    z2 = min(z + tail, sh0["f"][1], int(l1["dur_f"]))                 # the light dies inside the shot it was lit in
    a2 = max(a - 1, sh0["f"][0])
    voice = {"font": ask.get("font") or "InterDisplay-Black.ttf", "track": 0, "glow": {"a": 0.55, "blur": 0.03}}
    voice["px"] = round(glyphs.fit_px(voice, text, float(ask.get("width") or 0.62) * W, cap_px=0.16 * H), 2)
    _, b = glyphs.box(voice, text, voice["px"])
    bw, bh = (b[2] - b[0]) / W, (b[3] - b[1]) / H
    sx, sy = [float(v) for v in ask["seat"]] if ask.get("seat") else seat_of(ask.get("faces"), bw, bh)
    pen = (sx * W - (b[0] + b[2]) / 2, sy * H - b[3])
    gw, gh = int(W * K) // 2 * 2, int(H * K) // 2 * 2
    E = glyphs.raster(voice, text, voice["px"], pen, gw, gh, K)
    Lf = light_field(E)
    centre = ((pen[0] + (b[0] + b[2]) / 2) * K, (pen[1] + (b[1] + b[3]) / 2) * K)
    colour = _hex(ask.get("ink")) ** 2.2
    lev = sense.envelope(sense.level(l1, work, ask))
    on = np.zeros(int(l1["dur_f"]) + 1, np.float32)
    on[a:z] = 1.0
    for i in range(z, z2):                                            # after the word the lamp dies on its own curve
        on[i] = np.exp(-(i - z + 1) / max(tail / 3.0, 1.0))
    strike = np.clip((np.arange(len(on)) - a + 1) / 2.0, 0, 1)        # it strikes in two frames
    env = np.zeros(len(on), np.float32)
    env[:len(lev)] = lev
    e = on * strike * np.clip(0.42 + 0.78 * env, 0, 1.25)             # never out while the word stands, brightest on the stress
    room = float(ask.get("room") if ask.get("room") is not None else 0.42)
    frames = sense.shot_frames(l1, work, a2, z2)
    if len(frames) < z2 - a2:
        return {"skip": "the take could not be read under the lamp"}
    matte, mp = {}, sense.path_of(l1, work, "matte")
    if mp and os.path.exists(mp) and sh0.get("src"):
        on_matte = dict(l1, items=[dict(sh0, pic="matte")])          # the matte lies on the take: same clock, same crop
        matte = {f: m.astype(np.float32) / 255.0 for f, m in sense.shot_frames(on_matte, work, a2, z2, size=(gw, gh), gray=True).items()}
    rel = os.path.join("alien", f"{ask['id']}.mp4")
    os.makedirs(os.path.join(work, "alien"), exist_ok=True)
    out = os.path.join(work, rel)
    wr = writer(out, W, H, fps)
    try:
        for f in range(a2, z2):
            wr.stdin.write(relight(frames[f], matte.get(f), Lf, colour, float(e[f]), room, centre).tobytes())
    finally:
        wr.stdin.close()
        wr.wait()
    if wr.returncode:
        return {"skip": "the relit take could not be written"}
    keep, gone, was = sense.carve(l1, a2, z2)
    inherit = {k: v for k, v in sh0.items() if k in ("look", "grade", "shutter", "window")}
    aid = f"alien:{ask['id']}"
    shot = dict(inherit, id=f"{ask['id']}.lit", k="shot", f=[a2, z2], pic=aid, src=[0.0, round((z2 - a2) / fps, 5)])
    vname = f"alien.{ask['id']}"
    op = [round(float(min(max(0.5 + 0.5 * e[f] / 0.8, 0.0), 1.0)) if f < z else float(on[f]), 4) for f in range(a, z2)]
    word = {"id": f"{ask['id']}.word", "k": "word", "f": [a, z2], "t": text, "v": vname, "x": round(pen[0] / W, 6), "y": round(pen[1] / H, 6),
            "ink": ask.get("ink") or "#FF4A1C", "op": op, "role": "big", "on": a, "z": 8}
    if matte and ask.get("behind", True):
        word["behind"] = True
    return {"items": keep + [shot, word], "drop": gone, "voices": {vname: voice},
            "assets": {aid: {"kind": "video", "path": rel}},
            "marks": [{"f": a, "what": "lamp strike"}],
            "seat": {"x": sx, "y": sy, "w": bw, "h": bh, "f": [a, z2]},
            "log": {"px": voice["px"], "seat": [round(sx, 4), round(sy, 4)], "frames": z2 - a2, "matte": bool(matte),
                    "level": [round(float(v), 3) for v in e[a:z2]], "clip": rel}}
