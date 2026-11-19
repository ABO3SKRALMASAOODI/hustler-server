"""What the palette's devices read of a film, by signal, measured once and cached in the work folder: the base shot
on any frame and the cuts, the take's person matte (RVM), faces and eyes (YuNet), round shapes (the alien bank's
Hough), a still's depth when a depth model is installed. No model of meaning: nothing here reads a word.

    s = Sense(l1, work)
    s.shot_at(f), s.cuts(), s.take_shots()
    patch_assets = s.matte()            # {"matte": {...}} when the take's matte could be made (written once)
    s.faces(asset_id, t)                # [[x0, y0, x1, y1, eye_y]] picture fractions
    s.rounds(asset_id, t)               # [(cx, cy, r)] picture fractions
    s.depth(asset_id)                   # HxW float32 0..1 (near = 1) or None
    s.lum                               # frames on which an earlier device changed the whole frame's light
"""
import hashlib
import json
import os
import subprocess

import numpy as np

from .. import reader
from ..plan import Plan

MODELS = os.environ.get("VALMERA_MODELS", "/Users/masaoodi/Documents/Valmera/.valmera/models")
RVM = os.path.join(MODELS, "rvm_mobilenetv3_fp32.onnx")
YUNET = os.path.join(MODELS, "face_detection_yunet_2023mar.onnx")
MATTE_SIDE = 384          # the matte's long side: the runtime samples it through the shot's crop with linear filtering
MATTE_FPS = 15.0          # the matte's own rate: the runtime reads the frame nearest each programme frame (plan.matte_frame)


def ff():
    return os.environ.get("FFMPEG_BIN", reader.FF)


class Sense:
    def __init__(self, l1, work, pool=None):
        self.l1, self.work = l1, work or "."
        self.pool = pool
        self.lum = []                 # frames of full-frame light changes laid so far (white, black, negative)
        self._cache = {}

    # the scene ---------------------------------------------------

    @property
    def fps(self):
        return float(self.l1.get("fps") or 30)

    @property
    def size(self):
        return [int(v) for v in (self.l1.get("frame") or [1080, 1920])]

    def shots(self):
        return sorted((i for i in self.l1.get("items") or [] if i.get("k") == "shot"), key=lambda i: i["f"][0])

    def shot_at(self, f):
        for it in self.shots():
            if it["f"][0] <= f < it["f"][1]:
                return it
        return None

    def cuts(self):
        """Programme frames where the base picture changes (a new shot starts), the first frame excluded."""
        return sorted({int(it["f"][0]) for it in self.shots() if int(it["f"][0]) > 0})

    def on_cut(self, f, reach=4):
        """The cut nearest f within ``reach`` frames, else f itself: a punctuation lands on the cut when one is near."""
        near = [c for c in self.cuts() if abs(c - f) <= reach]
        return min(near, key=lambda c: (abs(c - f), c)) if near else int(f)

    def spaced(self, f, gap=10, reach=8):
        """The frame nearest f, within ``reach``, at least ``gap`` frames from every earlier full-frame light change
        (the strobes stay under three a second by construction). f itself when nothing is near."""
        if all(abs(f - g) >= gap for g in self.lum):
            return int(f)
        for d in range(1, reach + 1):
            for c in (f + d, f - d):
                if 0 < c < int(self.l1.get("dur_f") or 1) - 1 and all(abs(c - g) >= gap for g in self.lum):
                    return int(c)
        return None

    def take_shots(self):
        return [s for s in self.shots() if Plan.take_shot_of(self.l1.get("assets") or {}, dict(s, src=s.get("src") or [0, 0]))
                or str(s.get("pic")) == "main" or str(s.get("pic")).startswith("own:")]

    def is_take(self, shot):
        p = str((shot or {}).get("pic"))
        return p == "main" or p.startswith("own:")

    def cards_at(self, f):
        return [i for i in self.l1.get("items") or [] if i.get("k") == "card" and i["f"][0] <= f < i["f"][1]]

    def words_in(self, a, b, role=None):
        return [i for i in self.l1.get("items") or [] if i.get("k") == "word" and i["f"][0] < b and i["f"][1] > a
                and (role is None or i.get("role") == role)]

    def path_of(self, aid):
        a = (self.l1.get("assets") or {}).get(aid)
        if not a or not a.get("path"):
            return None
        p = a["path"]
        return p if os.path.isabs(p) else os.path.join(self.work, p)

    def pictures(self):
        """Picture assets a device may draw from (the pool, else every image and video but the matte)."""
        assets = self.l1.get("assets") or {}
        ids = self.pool if self.pool is not None else sorted(assets)
        return [a for a in ids if a in assets and a != "matte" and assets[a].get("kind") in ("image", "video")]

    # cache -------------------------------------------------------

    def _dir(self):
        d = os.path.join(self.work, "palette")
        os.makedirs(d, exist_ok=True)
        return d

    def _key(self, *parts):
        return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]

    # the take's matte -------------------------------------------

    def matte(self):
        """{"matte": asset} once the take's person matte exists (made here with RVM when the scene has none), else {}.
        Written next to the work as a grey video on the take's own clock and shape: the runtime draws the depth
        sandwich through it (a card or word with ``behind``)."""
        assets = self.l1.get("assets") or {}
        if "matte" in assets:
            return {"matte": assets["matte"]}
        if "matte" in self._cache:
            return self._cache["matte"]
        take = self.path_of("main")
        out = {}
        if take and os.path.exists(take) and os.path.exists(RVM):
            try:
                # only the stretch of the take this film shows is matted; the frames before it are black
                srcs = [s["src"] for s in self.take_shots() if s.get("src") and self.path_of(s["pic"]) == take]
                t0 = max(min((float(x[0]) for x in srcs), default=0.0) - 0.5, 0.0)
                t1 = max((float(x[1]) for x in srcs), default=0.0) + 0.5
                p = make_matte(take, os.path.join(self._dir(), f"matte_{self._key(take, os.path.getsize(take), round(t0, 2), round(t1, 2))}.mp4"),
                               t0, t1 if srcs else None)
                out = {"matte": {"kind": "video", "path": p}}
            except Exception as e:      # noqa: BLE001 - a film without a matte keeps its devices that need none
                out = {}
                self._cache["matte_fault"] = repr(e)[:200]
        self._cache["matte"] = out
        return out

    # faces, rounds, depth ---------------------------------------

    def still(self, aid, t=0.0, side=480):
        p = self.path_of(aid)
        if not p or not os.path.exists(p):
            return None
        k = ("still", aid, round(float(t), 2), side)
        if k not in self._cache:
            from ..alien import sense as AS
            info = AS.probe(p)
            w, h = info["w"], info["h"]
            s = side / max(w, h, 1)
            self._cache[k] = AS.still(p, t, (max(int(w * s) // 2 * 2, 2), max(int(h * s) // 2 * 2, 2)))
        return self._cache[k]

    def faces(self, aid, t=0.0):
        """[[x0, y0, x1, y1, eye_y]] in picture fractions (YuNet), [] when none or no detector."""
        k = ("faces", aid, round(float(t), 2))
        if k in self._cache:
            return self._cache[k]
        out = []
        im = self.still(aid, t)
        if im is not None and os.path.exists(YUNET):
            try:
                import cv2
                h, w = im.shape[:2]
                det = cv2.FaceDetectorYN.create(YUNET, "", (w, h), 0.7, 0.3, 50)
                _, rows = det.detect(cv2.cvtColor(im, cv2.COLOR_RGB2BGR))
                for r in rows if rows is not None else []:
                    x, y, bw, bh = [float(v) for v in r[:4]]
                    eye_y = (float(r[5]) + float(r[7])) / 2
                    out.append([x / w, y / h, (x + bw) / w, (y + bh) / h, eye_y / h])
            except Exception:      # noqa: BLE001 - no faces read: the device frames on the picture's centre
                out = []
        self._cache[k] = out
        return out

    def rounds(self, aid, t=0.0, n=4):
        """Round shapes in a picture: [(cx, cy, r)] picture fractions, strongest first (Hough on the gradient)."""
        k = ("rounds", aid, round(float(t), 2))
        if k in self._cache:
            return self._cache[k]
        out = []
        im = self.still(aid, t)
        if im is not None:
            try:
                import cv2
                g = cv2.GaussianBlur(cv2.cvtColor(im, cv2.COLOR_RGB2GRAY), (5, 5), 1.5)
                h, w = g.shape
                c = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1.2, minDist=min(w, h) * 0.25, param1=120, param2=40,
                                     minRadius=int(min(w, h) * 0.06), maxRadius=int(min(w, h) * 0.45))
                for x, y, r in (c[0][:n] if c is not None else []):
                    out.append((float(x) / w, float(y) / h, float(r) / w))
            except Exception:      # noqa: BLE001
                out = []
        self._cache[k] = out
        return out

    def depth(self, aid, t=0.0):
        """A picture's relative depth (near = 1), HxW float32, from Depth Anything V2 Small (worker/depth_track.py) run by
        the Python named in VALMERA_DEPTH_PY (one with torch and the weights: the batch lane's); cached in the work. None
        where no such Python is set up: a device that wants depth runs its fallback."""
        k = ("depth", aid, round(float(t), 2))
        if k in self._cache:
            return self._cache[k]
        self._cache[k] = None
        py, p = os.environ.get("VALMERA_DEPTH_PY"), self.path_of(aid)
        if not py or not p or not os.path.exists(p):
            return None
        out = os.path.join(self._dir(), f"depth_{self._key(p, round(float(t), 2))}.npy")
        if not os.path.exists(out):
            wk = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            code = ("import sys, numpy as np, depth_track as D; c = D.local_clip(sys.argv[1], float(sys.argv[2]), float(sys.argv[2]) + 0.04); "
                    "np.save(sys.argv[3], c.depth[0] if c is not None else np.zeros((1, 1), np.float32))")
            r = subprocess.run([py, "-c", code, p, str(float(t)), out], cwd=wk, capture_output=True, timeout=600)
            if r.returncode != 0 or not os.path.exists(out):
                return None
        self._cache[k] = np.load(out)
        return self._cache[k]

    def seat_luma(self, f, y0=0.55, y1=0.70):
        """The base picture's light (0..1) under a band of the frame on frame f, read from its source."""
        sh = self.shot_at(f)
        if not sh:
            return 0.0
        a = (self.l1.get("assets") or {}).get(sh.get("pic")) or {}
        t = 0.0
        if a.get("kind") == "video" and sh.get("src"):
            lo, hi = sh["f"]
            t = float(sh["src"][0]) + (float(sh["src"][1]) - float(sh["src"][0])) * (f - lo) / max(hi - lo, 1)
        im = self.still(sh["pic"], t, side=160)
        if im is None:
            return 0.0
        h = im.shape[0]
        band = im[int(y0 * h):max(int(y1 * h), int(y0 * h) + 1)]
        return float(band.mean()) / 255.0


def make_matte(src, out, t0=0.0, t1=None, side=MATTE_SIDE, fps=MATTE_FPS):
    """The person's matte of a video with Robust Video Matting (onnx, recurrent), at ``side`` on its long side and
    ``fps``, as a grey H.264 video on the source's own clock from its first frame: the stretch [t0, t1] is matted,
    the frames before t0 are black, and it ends at t1. The runtime reads one byte a pixel."""
    if os.path.exists(out):
        return out
    import onnxruntime as ort
    info = reader.probe(src)
    w, h = int(info["w"]), int(info["h"])
    k = side / max(w, h)
    mw, mh = max(int(w * k) // 2 * 2, 2), max(int(h * k) // 2 * 2, 2)
    so = ort.SessionOptions()
    so.intra_op_num_threads = max(2, min(6, (os.cpu_count() or 4) // 2))
    sess = ort.InferenceSession(RVM, so, providers=["CPUExecutionProvider"])
    rec = [np.zeros((1, 1, 1, 1), np.float32)] * 4
    ratio = np.array([0.6], np.float32)
    lead = int(round(max(float(t0), 0.0) * fps))
    cmd = [ff(), "-nostdin", "-v", "error", "-ss", f"{lead / fps:.4f}", "-i", src, "-an", "-sn"]
    if t1 is not None:
        cmd += ["-t", f"{max(float(t1) - lead / fps, 0.1):.4f}"]
    dec = subprocess.Popen(cmd + ["-vf", f"fps={fps}:round=near,scale={mw}:{mh}:flags=area", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                           stdout=subprocess.PIPE)
    tmp = out + ".part.mp4"
    enc = subprocess.Popen([ff(), "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{mw}x{mh}",
                            "-r", f"{fps:.6f}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                            "-pix_fmt", "yuv420p", tmp], stdin=subprocess.PIPE)
    n = mw * mh * 3
    try:
        blank = np.zeros((mh, mw), np.uint8).tobytes()
        for _ in range(lead):
            enc.stdin.write(blank)
        first = True
        while True:
            b = dec.stdout.read(n)
            if len(b) < n:
                break
            x = np.frombuffer(b, np.uint8).reshape(mh, mw, 3).astype(np.float32).transpose(2, 0, 1)[None] / 255.0
            for _ in range(8 if first else 1):       # the recurrent state settles on the first frame before it is written
                fgr, pha, *rec = sess.run(None, {"src": x, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2], "r4i": rec[3],
                                                 "downsample_ratio": ratio})
            first = False
            enc.stdin.write((np.clip(pha[0, 0], 0, 1) * 255).astype(np.uint8).tobytes())
    finally:
        dec.stdout.close()
        dec.kill()
        dec.wait()
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError("the matte could not be encoded")
    os.replace(tmp, out)
    return out
