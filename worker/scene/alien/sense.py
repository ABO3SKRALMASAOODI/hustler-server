"""What a machine can know of a whole film by signal alone: any frame of any source, the picture a viewer
sees through a shot's crop, the level of the voice on every frame. ffmpeg, numpy and OpenCV. No model,
no meaning: who is in a picture, which word matters and which picture answers it are handed in.
"""
import os
import subprocess

import numpy as np

from .. import reader
from ..plan import AR

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def ff():
    return os.environ.get("FFMPEG_BIN", reader.FF)


def path_of(l1, work, aid):
    a = (l1.get("assets") or {}).get(aid)
    if not a or not a.get("path"):
        return None
    p = a["path"]
    return p if os.path.isabs(p) else os.path.join(work, p)


_PROBE = {}


def probe(path):
    """{"w", "h", "fps", "dur"} of a video, or of a still (dur 0). Cached: a film asks the same file often."""
    if path not in _PROBE:
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            from PIL import Image, ImageOps
            with Image.open(path) as im:
                w, h = ImageOps.exif_transpose(im).size
            _PROBE[path] = {"w": w, "h": h, "fps": 0.0, "dur": 0.0}
        else:
            _PROBE[path] = reader.probe(path)
    return _PROBE[path]


def frames(path, t0=0.0, n=None, fps=30.0, size=None, gray=False):
    """Frames of a video from ``t0`` at ``fps``, as arrays (RGB, or one plane when ``gray``), at ``size`` (w, h)
    or the source's own. One decode for the run; the caller stops reading when it has enough."""
    info = probe(path)
    w, h = size or (info["w"], info["h"])
    w, h = int(w) // 2 * 2, int(h) // 2 * 2
    pix, ch = ("gray", 1) if gray else ("rgb24", 3)
    cmd = [ff(), "-nostdin", "-v", "error", "-ss", f"{max(t0, 0.0):.4f}", "-i", path, "-an", "-sn",
           "-vf", f"fps={fps}:round=near,scale={w}:{h}:flags=bicubic", "-pix_fmt", pix, "-f", "rawvideo"]
    if n:
        cmd += ["-frames:v", str(int(n))]
    p = subprocess.Popen(cmd + ["-"], stdout=subprocess.PIPE)
    size_b = w * h * ch
    try:
        while True:
            b = p.stdout.read(size_b)
            if len(b) < size_b:
                break
            a = np.frombuffer(b, np.uint8)
            yield a.reshape(h, w) if gray else a.reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def still(path, t=0.0, size=None, gray=False):
    """One picture: a still file itself, or the frame of a video at ``t`` seconds."""
    if probe(path)["fps"] == 0.0:
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(path)).convert("L" if gray else "RGB")
        if size:
            im = im.resize((int(size[0]), int(size[1])), Image.BICUBIC)
        return np.asarray(im, np.uint8)
    for fr in frames(path, t, 1, 30.0, size, gray):
        return fr.copy()
    return None


def cover(tw, th, w, h):
    """The engine's default crop: centred cover (runtime.cover)."""
    ta, ra = tw / th, w / h
    if ta > ra:
        e = (1 - ra / ta) / 2
        return [e, 0.0, 1 - e, 1.0]
    e = (1 - ta / ra) / 2
    return [0.0, e, 1.0, 1 - e]


def crop_at(shot, f, pw, ph, W, H):
    """The crop a shot draws on programme frame f, as [x0, y0, x1, y1] of its source."""
    c = AR(shot.get("crop"), f, shot["f"][0])
    return [float(v) for v in c] if c is not None else cover(pw, ph, W, H)


def src_time(shot, f, fps):
    """Seconds into its source that a shot shows on programme frame f (the plan's clock, without ``step``)."""
    lo, hi = shot["f"]
    src = shot.get("src") or [0.0, 0.0]
    return float(src[0]) + (float(src[1]) - float(src[0])) * (f - lo) / max(hi - lo, 1)


def view(arr, crop, W, H):
    """A source picture through a crop, at the frame's size: what the viewer sees of it."""
    import cv2
    ph, pw = arr.shape[:2]
    x0, y0, x1, y1 = crop[0] * pw, crop[1] * ph, crop[2] * pw, crop[3] * ph
    sx, sy = W / max(x1 - x0, 1e-6), H / max(y1 - y0, 1e-6)
    M = np.array([[sx, 0.0, -x0 * sx], [0.0, sy, -y0 * sy]], np.float64)
    return cv2.warpAffine(arr, M, (int(W), int(H)), flags=cv2.INTER_AREA if sx < 0.8 else cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def shot_at(l1, f):
    for it in l1.get("items") or []:
        if it.get("k") == "shot" and it["f"][0] <= f < it["f"][1]:
            return it
    return None


def shot_frames(l1, work, a, z, size=None, gray=False, crop=True):
    """What the base picture shows on programme frames [a, z): {frame: array at ``size`` (default the film's)}.
    Plates and missing pictures give nothing. The film's look is not applied: this is the picture, not its grade."""
    fps = float(l1.get("fps") or 30)
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    ow, oh = size or (W, H)
    out = {}
    for sh in sorted((i for i in l1.get("items") or [] if i.get("k") == "shot"), key=lambda i: i["f"][0]):
        lo, hi = max(sh["f"][0], a), min(sh["f"][1], z)
        p = path_of(l1, work, sh.get("pic"))
        if hi <= lo or not p or not os.path.exists(p):
            continue
        info = probe(p)
        if info["fps"] == 0.0:
            src = still(p, gray=gray)
            for f in range(lo, hi):
                out[f] = view(src, crop_at(sh, f, info["w"], info["h"], W, H), ow, oh) if crop else src
            continue
        t0 = src_time(sh, lo, fps)
        speed = (src_time(sh, hi, fps) - t0) / max(hi - lo, 1) * fps      # source seconds a programme second: 1 is real time
        if speed <= 1e-6:                                                 # a held frame: one picture under every frame of the shot
            src = still(p, t0, gray=gray)
            for f in range(lo, hi):
                out[f] = view(src, crop_at(sh, f, info["w"], info["h"], W, H), ow, oh) if crop else src
            continue
        for i, fr in enumerate(frames(p, t0, hi - lo, fps / speed, None, gray)):
            f = lo + i
            if f >= hi:
                break
            out[f] = view(fr, crop_at(sh, f, info["w"], info["h"], W, H), ow, oh) if crop else fr.copy()
        last = max((f for f in out if lo <= f < hi), default=None)
        for f in range(lo, hi):                                           # a clip that ended a frame early holds its last picture
            if f not in out and last is not None:
                out[f] = out[last]
    return out


# ----------------------------------------------------------------- the voice, by signal

def programme_audio(l1, work, ask=None):
    """The file that carries the film's voice in programme time: the ask's own ``audio``, else nothing (the
    spine cuts ``main``, and ``level`` reads it span by span)."""
    p = (ask or {}).get("audio")
    if p:
        return p if os.path.isabs(p) else os.path.join(work, p)
    return None


def _band(path, t0, dur, sr=16000):
    cmd = [ff(), "-nostdin", "-v", "error", "-ss", f"{max(t0, 0.0):.4f}", "-i", path, "-t", f"{dur:.4f}", "-vn", "-ac", "1", "-ar", str(sr),
           "-af", "highpass=f=200,lowpass=f=3800", "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.float32)


def level(l1, work, ask=None, sr=16000):
    """The voice's level on every programme frame, 0..1.5, where 1 is the p95 of the film's own speech: RMS
    of the speech band over a frame and a half. Read from the ask's audio, else from ``main`` through the spine."""
    fps = float(l1.get("fps") or 30)
    n = int(l1["dur_f"])
    hop = sr / fps
    x = np.zeros(int(n * hop) + sr, np.float32)
    p = programme_audio(l1, work, ask)
    if p and os.path.exists(p):
        y = _band(p, 0.0, n / fps + 0.5, sr)
        x[:min(len(y), len(x))] = y[:len(x)]
    else:
        main = path_of(l1, work, "main")
        spine = l1.get("spine") or []
        if not (main and spine and os.path.exists(main)):
            return np.zeros(n, np.float32)
        for e in spine:
            a, b = e["f"]
            y = _band(main, float(e["src"][0]), (b - a) / fps, sr)
            at = int(a * hop)
            x[at:at + len(y)] = y[:max(len(x) - at, 0)]
    win = int(hop * 1.5)
    lev = np.zeros(n, np.float32)
    for i in range(n):
        c = int(i * hop + hop / 2)
        seg = x[max(0, c - win // 2): c + win // 2]
        if len(seg):
            lev[i] = float(np.sqrt(np.mean(seg * seg)))
    loud = lev[lev > lev.max() * 0.05] if lev.max() > 0 else lev[:0]
    ref = float(np.percentile(loud, 95)) if len(loud) else 1.0
    return np.clip(lev / max(ref, 1e-6), 0, 1.5)


def envelope(lev, attack_f=1.2, release_f=6.0, gamma=0.7, floor=0.06):
    """A light that follows a voice: up inside two frames, down over a fifth of a second, out in a real pause,
    and a frame ahead of the sound, as a lip is."""
    e = np.clip((np.asarray(lev, np.float32) - floor) / (1.0 - floor), 0, 1.25) ** gamma
    out = np.zeros_like(e)
    v = 0.0
    ka, kr = 1.0 - np.exp(-1.0 / attack_f), 1.0 - np.exp(-1.0 / release_f)
    for i, t in enumerate(e):
        v += (t - v) * (ka if t > v else kr)
        out[i] = v
    lead = np.concatenate([out[1:], out[-1:]])
    return np.maximum(out, 0.6 * lead)


def peak_near(lev, f, back=2, ahead=5):
    """The frame of the voice's peak nearest a word's onset: where a thing lands so it is felt on the word."""
    a, b = max(int(f) - back, 0), min(int(f) + ahead + 1, len(lev))
    if b <= a or float(np.max(lev[a:b])) <= 0:
        return int(f)
    d = np.diff(np.concatenate([[lev[max(a - 1, 0)]], lev[a:b]]))       # the attack: the frame the level rises most
    return a + int(np.argmax(d))


# ----------------------------------------------------------------- a base shot, cut open

def slice_shot(sh, p0, p1, new_id=None):
    """The part of a base shot on programme frames [p0, p1): its clock, its crop and every per-frame list kept."""
    lo, hi = sh["f"]
    p0, p1 = max(int(p0), lo), min(int(p1), hi)
    it = dict(sh, f=[p0, p1], id=new_id or sh["id"])
    if sh.get("src"):
        s0, s1 = float(sh["src"][0]), float(sh["src"][1])
        r = (s1 - s0) / max(hi - lo, 1)
        it["src"] = [round(s0 + r * (p0 - lo), 5), round(s0 + r * (p1 - lo), 5)]
    for k, v in sh.items():
        if isinstance(v, list) and len(v) == hi - lo and k not in ("f", "src"):
            it[k] = v[p0 - lo:p1 - lo]
    return it


def cut_shot(sh, a, z, fps=30.0):
    """The pieces of a base shot left outside [a, z)."""
    lo, hi = sh["f"]
    out = []
    if a > lo:
        out.append(slice_shot(sh, lo, min(a, hi), sh["id"] if z >= hi else f"{sh['id']}~a"))
    if z < hi:
        out.append(slice_shot(sh, max(z, lo), hi, sh["id"] if a <= lo else f"{sh['id']}~z"))
    return [p for p in out if p["f"][1] > p["f"][0]]


def carve(l1, a, z):
    """(kept pieces, removed ids, the shots that covered the span) for replacing the base picture on [a, z)."""
    keep, gone, was = [], [], []
    for sh in (i for i in l1.get("items") or [] if i.get("k") == "shot"):
        if sh["f"][1] <= a or sh["f"][0] >= z:
            continue
        was.append(sh)
        gone.append(sh["id"])
        keep += cut_shot(sh, a, z)
    return keep, gone, was
