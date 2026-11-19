"""What the palette's devices need to know of a film, measured once and cached in the work folder (``palette/``).
Signal and signal models only: RVM for the person's matte, Depth Anything V2 for a still's depth, OpenCV for text
boxes, hue and light. Nothing here reads a meaning; which moment and which picture come from the ask.

    s = Sense(l1, work)
    s.matte()            -> the asset id "matte" laid on the take (made now with RVM when the film has none)
    s.depth(pic)         -> float32 h x w, 0 far .. 1 near (the model when it is here, else a measured stand-in)
    s.text_boxes(pic)    -> [(x0, y0, x1, y1), ...] fractions of the picture, the most prominent line first
    s.hue(pic)           -> "#RRGGBB": the picture's most present strong colour
    s.bright(pic)        -> (x, y): where its brightest light sits, as fractions
    s.level()            -> the voice's level on every programme frame (scene.alien.sense.level)
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

from ..alien import sense as AS
from ..plan import fingerprint

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(os.path.dirname(HERE))
MODEL_DIRS = [os.environ.get("VALMERA_MODELS", ""), os.path.join(WORKER, "models"),
              "/Users/masaoodi/Documents/Valmera/.valmera/models", "/opt/valmera/models"]
TORCH_PY = [os.environ.get("VALMERA_TORCH_PY", ""), sys.executable,
            "/Users/masaoodi/Documents/Valmera/.valmera/venv-narrate/bin/python"]


def model(name):
    for d in MODEL_DIRS:
        p = os.path.join(d, name) if d else ""
        if p and os.path.exists(p):
            return p
    for d in MODEL_DIRS:            # the depth weights sit in a folder of their own on the Mac
        p = os.path.join(d, "depth", name) if d else ""
        if p and os.path.exists(p):
            return p
    return None


class Sense:
    def __init__(self, l1, work):
        self.l1, self.work = l1, work
        self.dir = os.path.join(work, "palette")
        self._level = None
        self.notes = []

    def _cache(self, *parts):
        os.makedirs(self.dir, exist_ok=True)
        return os.path.join(self.dir, *parts)

    def path(self, aid):
        return AS.path_of(self.l1, self.work, aid)

    def picture(self, aid, t=0.0, long_side=None):
        """The picture of an asset as RGB uint8 (a still, or a video's frame at ``t`` seconds)."""
        p = self.path(aid)
        if not p or not os.path.exists(p):
            return None
        a = AS.still(p, t)
        if a is not None and long_side and max(a.shape[:2]) > long_side:
            import cv2
            k = long_side / max(a.shape[:2])
            a = cv2.resize(a, (int(a.shape[1] * k), int(a.shape[0] * k)), interpolation=cv2.INTER_AREA)
        return a

    # ------------------------------------------------------------- the voice

    def level(self):
        if self._level is None:
            try:
                self._level = AS.level(self.l1, self.work)
            except Exception:      # noqa: BLE001 - a level we cannot read is a quiet film, never a stop
                self._level = np.zeros(int(self.l1["dur_f"]), np.float32)
        return self._level

    # ------------------------------------------------------------- the person's matte (RVM)

    def matte(self):
        """The id of the take's matte asset ("matte"), made now when the film carries none: RVM over every frame
        of the take a shot shows (with a few frames ahead of each window to settle its memory), black elsewhere,
        at the take's own clock, so plan.matte_frame registers it to every take shot. None when there is no take."""
        from ._util import is_take, shots, take_id
        assets = self.l1.setdefault("assets", {})
        if "matte" in assets:
            return "matte"
        t = take_id(self.l1)
        src = self.path(t) if t else None
        if not src or not os.path.exists(src):
            return None
        info = AS.probe(src)
        fps = float(info.get("fps") or 30.0)
        wins = [(float(s["src"][0]), float(s["src"][1])) for s in shots(self.l1) if is_take(self.l1, s)]
        if not wins:
            return None
        key = hashlib.sha1(json.dumps(sorted((round(a, 3), round(b, 3)) for a, b in wins)).encode()).hexdigest()[:8]
        out = self._cache(f"matte-{fingerprint(src)}-{key}.mp4")      # the frames it holds are the frames these shots show
        if not os.path.exists(out):
            self._make_matte(src, info, fps, wins, out)
        assets["matte"] = {"kind": "video", "path": out, "w": int(info["w"]), "h": int(info["h"]), "fps": fps, "dur": info.get("dur")}
        return "matte"

    def _make_matte(self, src, info, fps, wins, out):
        import cv2
        import onnxruntime as ort
        onnx = model("rvm_mobilenetv3_fp32.onnx")
        if not onnx:
            raise RuntimeError("no RVM weights here")
        n = int(round(float(info.get("dur") or 0) * fps)) + 2
        need = np.zeros(n + 1, bool)
        starts = set()
        for a, b in wins:
            i0, i1 = max(int(a * fps) - 8, 0), min(int(np.ceil(b * fps)) + 2, n)
            need[i0:i1] = True
            starts.add(i0)
        w, h = int(info["w"]), int(info["h"])
        k = 640 / max(w, h)
        mw, mh = max(int(w * k) // 2 * 2, 2), max(int(h * k) // 2 * 2, 2)
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        so.inter_op_num_threads = 1
        sess = ort.InferenceSession(onnx, so, providers=["CPUExecutionProvider"])
        ds = np.array([0.5], np.float32)
        enc = subprocess.Popen([AS.ff(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{mw}x{mh}", "-r", f"{fps}",
                                "-i", "-", "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "8", "-g", "30",
                                out + ".part.mp4"], stdin=subprocess.PIPE)
        rec = None
        blank = np.zeros((mh, mw), np.uint8)
        i = -1
        for i, fr in enumerate(AS.frames(src, 0.0, None, fps, (mw, mh))):
            if i >= n:
                break
            if not need[i]:
                enc.stdin.write(blank.tobytes())
                rec = None
                continue
            if rec is None or i in starts:
                rec = [np.zeros((1, 1, 1, 1), np.float32)] * 4
            x = (fr.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
            _, pha, *rec = sess.run(None, {"src": x, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2], "r4i": rec[3], "downsample_ratio": ds})
            m = np.clip(pha[0, 0] * 255.0 + 0.5, 0, 255).astype(np.uint8)
            if m.shape != (mh, mw):
                m = cv2.resize(m, (mw, mh), interpolation=cv2.INTER_LINEAR)
            enc.stdin.write(m.tobytes())
        enc.stdin.close()
        if enc.wait() != 0 or i < 0:
            raise RuntimeError("the matte did not encode")
        os.replace(out + ".part.mp4", out)

    # ------------------------------------------------------------- depth of a still

    def depth(self, aid, t=0.0):
        """Relative depth of a picture, float32 at the picture's own size (long side at most 1024): 1 is near.
        Depth Anything V2 Small in a child process where torch is installed; else a measured stand-in (the
        subject's region by GrabCut from the centre, over a floor that comes nearer towards the bottom)."""
        p = self.path(aid)
        if not p:
            return None
        key = hashlib.sha1(f"{fingerprint(p)}:{t:.3f}".encode()).hexdigest()[:16]
        out = self._cache("depth", f"{key}.npy")
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
            for py in TORCH_PY:
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

    # ------------------------------------------------------------- text, colour and light

    def text_boxes(self, aid, t=0.0):
        """Lines of text on a picture (a document, a page, a screen), by their strokes: a gradient closed along
        the line, kept where it is long, thin and dense. Fractions of the picture; the most prominent first."""
        import cv2
        p = self.path(aid)
        key = self._cache(f"text-{fingerprint(p)}-{t:.2f}.json") if p else None
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
