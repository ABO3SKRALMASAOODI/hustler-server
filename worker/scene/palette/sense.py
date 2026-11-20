"""What the palette's devices read of a film, by signal, measured once and cached in the work folder: the base shot
on any frame and the cuts, the take's person matte (RVM), faces and eyes (YuNet), round shapes (the alien bank's
Hough), a still's depth (Depth Anything V2 where torch is installed, else a measured stand-in), text lines, the
strongest hue, where the light is, the voice's level. No model of meaning: nothing here reads a word.

    s = Sense(l1, work)
    s.shot_at(f), s.cuts(), s.take_shots()
    patch_assets = s.matte()            # {"matte": {...}} when the take's matte could be made (written once)
    s.faces(asset_id, t)                # [[x0, y0, x1, y1, eye_y]] picture fractions
    s.rounds(asset_id, t)               # [(cx, cy, r)] picture fractions
    s.depth(asset_id)                   # HxW float32 0..1 (near = 1); None only when the picture cannot be read
    s.text_boxes(asset_id)              # [(x0, y0, x1, y1)] lines of text, the most prominent first ([] when none)
    s.hue(asset_id), s.bright(asset_id), s.luma(asset_id), s.level()
    s.lum                               # frames on which an earlier device changed the whole frame's light
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

from .. import reader
from ..plan import Plan, fingerprint

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(os.path.dirname(HERE))


def model_dirs():
    """Where model files are looked for: VALMERA_MODELS (the workflow shell sets it on the Mac), VALMERA_MODELS_DIR
    (the worker's own convention, sidecar_kinds.models_dir) and the worker's models folder (the image bakes them
    there). No machine's path is written here."""
    return [d for d in (os.environ.get("VALMERA_MODELS", "").strip(), os.environ.get("VALMERA_MODELS_DIR", "").strip(),
                        os.path.join(WORKER, "models")) if d]


def model(name):
    """The first copy of a model file in model_dirs() (or its ``depth`` folder), else None."""
    for sub in ("", "depth"):
        for d in model_dirs():
            p = os.path.join(d, sub, name)
            if os.path.exists(p):
                return p
    return None


def torch_pythons():
    """Pythons that may hold torch for the depth child: VALMERA_TORCH_PY (or the older VALMERA_DEPTH_PY), then this one."""
    return [p for p in (os.environ.get("VALMERA_TORCH_PY", "").strip(), os.environ.get("VALMERA_DEPTH_PY", "").strip(),
                        sys.executable) if p]


def rvm_path():
    return model("rvm_mobilenetv3_fp32.onnx")


def yunet_path():
    return model("face_detection_yunet_2023mar.onnx")
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
        from ._util import is_take, take_id
        tid = take_id(self.l1)
        take = self.path_of(tid) if tid else None
        out = {}
        if take and os.path.exists(take) and rvm_path():
            try:
                # only the stretch of the take this film shows is matted; the frames before it are black
                srcs = [s["src"] for s in self.take_shots() if s.get("src") and is_take(self.l1, s)]
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
        if im is not None and yunet_path():
            try:
                import cv2
                h, w = im.shape[:2]
                det = cv2.FaceDetectorYN.create(yunet_path(), "", (w, h), 0.7, 0.3, 50)
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
        """Relative depth of a picture, float32 at the picture's own size (long side at most 1024): 1 is near.
        Depth Anything V2 Small in a child process where torch is installed; else a measured stand-in (the
        subject's region by GrabCut from the centre, over a floor that comes nearer towards the bottom)."""
        p = self.path(aid)
        if not p or not os.path.exists(p):
            return None
        key = hashlib.sha1(f"{fingerprint(p)}:{t:.3f}".encode()).hexdigest()[:16]
        out = os.path.join(self._dir(), "depth", f"{key}.npy")
        if os.path.exists(out):
            return np.load(out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        img = self.picture(aid, t, long_side=1024)
        if img is None:
            return None
        d = self._depth_model(img)
        how = "model"
        if d is None:
            d, how = self._depth_signal(img), "signal"
        d = d.astype(np.float32)
        lo, hi = np.percentile(d, 1), np.percentile(d, 99)
        d = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
        np.save(out, d)
        self.notes.append(f"depth {aid}: {how}")
        return d

    def _depth_model(self, img):
        w = model("depth_anything_v2_vits.pth")
        if not w:
            return None
        with tempfile.TemporaryDirectory() as td:
            ip, op = os.path.join(td, "in.npy"), os.path.join(td, "out.npy")
            np.save(ip, img)
            for py in torch_pythons():
                if not py or not os.path.exists(py):
                    continue
                r = subprocess.run([py, os.path.join(HERE, "_depth_child.py"), WORKER, w, ip, op], capture_output=True, text=True, timeout=600)
                if r.returncode == 0 and os.path.exists(op):
                    return np.load(op)
        return None

    @staticmethod
    def _depth_signal(img):
        import cv2
        h, w = img.shape[:2]
        k = 512 / max(h, w)
        sm = cv2.resize(img, (max(int(w * k), 8), max(int(h * k), 8)), interpolation=cv2.INTER_AREA)
        sh, sw = sm.shape[:2]
        mask = np.zeros((sh, sw), np.uint8)
        rect = (int(sw * 0.14), int(sh * 0.08), int(sw * 0.72), int(sh * 0.9))
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(cv2.cvtColor(sm, cv2.COLOR_RGB2BGR), mask, rect, bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
            fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1.0, 0.0).astype(np.float32)
        except cv2.error:
            fg = np.zeros((sh, sw), np.float32)
        floor = np.linspace(0.0, 0.45, sh, dtype=np.float32)[:, None].repeat(sw, 1)
        d = np.maximum(floor, cv2.GaussianBlur(fg, (0, 0), 3) * 0.95)
        return cv2.resize(d, (w, h), interpolation=cv2.INTER_LINEAR)

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

    # ------------------------------------------------------------- pictures and the voice

    @property
    def notes(self):
        return self._cache.setdefault("_notes", [])

    def path(self, aid):
        """The file of an asset (relative paths against the work)."""
        return self.path_of(aid)

    def picture(self, aid, t=0.0, long_side=None):
        """The picture of an asset as RGB uint8 (a still, or a video's frame at ``t`` seconds)."""
        from ..alien import sense as AS
        p = self.path(aid)
        if not p or not os.path.exists(p):
            return None
        a = AS.still(p, t)
        if a is not None and long_side and max(a.shape[:2]) > long_side:
            import cv2
            k = long_side / max(a.shape[:2])
            a = cv2.resize(a, (int(a.shape[1] * k), int(a.shape[0] * k)), interpolation=cv2.INTER_AREA)
        return a

    def level(self):
        """The voice's level on every programme frame (scene.alien.sense.level); a film it cannot read is quiet."""
        if "_level" not in self._cache:
            from ..alien import sense as AS
            try:
                self._cache["_level"] = AS.level(self.l1, self.work)
            except Exception:      # noqa: BLE001 - a level we cannot read is a quiet film, never a stop
                self._cache["_level"] = np.zeros(int(self.l1.get("dur_f") or 1), np.float32)
        return self._cache["_level"]

    # ------------------------------------------------------------- text, colour and light

    def text_boxes(self, aid, t=0.0):
        """Lines of text on a picture (a document, a page, a screen), by their strokes: a gradient closed along
        the line, kept where it is long, thin and dense. Fractions of the picture; the most prominent first."""
        import cv2
        p = self.path(aid)
        key = os.path.join(self._dir(), f"text-{fingerprint(p)}-{t:.2f}.json") if p and os.path.exists(p) else None
        if key and os.path.exists(key):
            return [tuple(b) for b in json.load(open(key))]
        img = self.picture(aid, t, long_side=1400)
        if img is None:
            return []
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        h, w = g.shape
        grad = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        k = max(int(w / 70), 5)
        line = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, max(k // 6, 1))))
        n, lab, st, _ = cv2.connectedComponentsWithStats(line, 8)
        boxes = []
        for i in range(1, n):
            x, y, bw_, bh, area = st[i]
            if bw_ < w * 0.08 or bh < 6 or bh > h * 0.12 or bw_ / max(bh, 1) < 2.5:
                continue
            dens = float((bw[y:y + bh, x:x + bw_] > 0).mean())
            if dens < 0.12 or dens > 0.8:
                continue
            score = bw_ * bh * (0.5 + dens) * (1.0 + bh / (h * 0.03))
            boxes.append((score, (x / w, y / h, (x + bw_) / w, (y + bh) / h)))
        boxes.sort(key=lambda b: -b[0])
        out = [b for _, b in boxes[:12]]
        if key:
            json.dump(out, open(key, "w"))
        return out

    def hue(self, aid, t=0.0):
        """The strongest colour a picture is made of: the peak of the hue histogram over its saturated pixels."""
        import cv2
        img = self.picture(aid, t, long_side=320)
        if img is None:
            return "#D2302A"
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        m = (hsv[..., 1] > 90) & (hsv[..., 2] > 50)
        if m.sum() < 50:
            return "#D2302A"
        hist = np.bincount((hsv[..., 0][m].astype(int) // 5) % 36, minlength=36).astype(np.float32)
        hist = np.convolve(np.concatenate([hist[-1:], hist, hist[:1]]), [0.25, 0.5, 0.25], "valid")
        hb = int(np.argmax(hist)) * 5 + 2
        rgb = cv2.cvtColor(np.uint8([[[hb, 230, 220]]]), cv2.COLOR_HSV2RGB)[0, 0]
        return "#%02X%02X%02X" % tuple(int(v) for v in rgb)

    def luma(self, aid, t=0.0, crop=None, q=50):
        """A percentile of a picture's light (0..1), over the part the frame shows."""
        img = self.picture(aid, t, long_side=320)
        if img is None:
            return 0.45
        if crop:
            h, w = img.shape[:2]
            img = img[int(crop[1] * h):max(int(crop[3] * h), int(crop[1] * h) + 2), int(crop[0] * w):max(int(crop[2] * w), int(crop[0] * w) + 2)]
        y = (img.astype(np.float32) / 255.0) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        return float(np.percentile(y, q))

    def bright(self, aid, t=0.0, crop=None):
        """Where a picture's light is: the peak of its blurred luminance, as fractions of ``crop`` (the part of the
        picture the frame shows; the whole picture when None)."""
        import cv2
        img = self.picture(aid, t, long_side=640)
        if img is None:
            return (0.72, 0.3)
        if crop:
            h, w = img.shape[:2]
            x0, y0, x1, y1 = int(crop[0] * w), int(crop[1] * h), max(int(crop[2] * w), int(crop[0] * w) + 2), max(int(crop[3] * h), int(crop[1] * h) + 2)
            img = img[y0:y1, x0:x1]
        y = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32), (0, 0), 6)
        h, w = y.shape
        y *= np.linspace(1.0, 0.55, h, dtype=np.float32)[:, None]     # lights hang high; the caption seat is low
        e = max(int(0.06 * min(h, w)), 1)
        y[:e], y[-e:], y[:, :e], y[:, -e:] = 0, 0, 0, 0
        _, _, _, (x, yy) = cv2.minMaxLoc(y)
        return (x / img.shape[1], yy / img.shape[0])


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
    sess = ort.InferenceSession(rvm_path(), so, providers=["CPUExecutionProvider"])
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
