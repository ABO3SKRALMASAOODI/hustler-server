"""E3: the reader. ffmpeg decodes, on a thread that runs ahead of the render.

Colour is explicit at the way in: the source's matrix and range are named, never guessed by swscale.
Micro-shots from one parent share one decode window, in a bounded frame cache. ``progress`` moves on
every decoded frame, so the watchdog can tell a slow seek in a 4K original from a hang.
"""
import collections
import json
import os
import queue
import subprocess
import threading
import time

from .faults import SceneFault

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
FP = os.environ.get("FFPROBE_BIN", "ffprobe")
MAX_PIC = 2160                               # no picture needs more than this on a 1920 frame
# The scaler's flags, in and out. The accurate ones are the default because they are what the golden
# colour tests pin; they also switch swscale's SIMD off, so a tier that must be fast can name others
# (lane D measured "bicubic" on the box). One variable, so decode and encode always agree.
SWS = os.environ.get("VALMERA_SCENE_SWS") or "lanczos+accurate_rnd+full_chroma_int+full_chroma_inp"
_MATRIX = {"bt709": "bt709", "smpte170m": "smpte170m", "bt470bg": "bt470bg", "fcc": "fcc", "smpte240m": "smpte240m",
           "bt2020nc": "bt2020", "bt2020c": "bt2020"}
# The way in, for a standard-range source: zimg (ffmpeg's zscale) resizes with Lanczos and converts to
# RGB in one vectorised pass. swscale's accurate path for the same job is scalar C: on the box it was
# 33 ms of CPU a 1080p frame against 21 (14 of that is the H.264 decode itself), and the two agree to
# 54 dB. swscale stays for what zimg is not asked to do: a matrix it has no name for, a build without
# zimg, or a scaler named by hand (VALMERA_SCENE_SWS).
_ZMATRIX = {"bt709": "bt709", "smpte170m": "smpte170m", "bt470bg": "bt470bg", "bt2020": "bt2020nc"}
_zimg = None


def zimg():
    """Does this ffmpeg carry zscale? Asked once."""
    global _zimg
    if _zimg is None:
        if os.environ.get("VALMERA_SCENE_SWS") or os.environ.get("VALMERA_SCENE_ZIMG") == "0":
            _zimg = False
        else:
            try:
                r = subprocess.run([FF, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
                _zimg = " zscale " in r.stdout
            except (OSError, subprocess.TimeoutExpired):
                _zimg = False
    return _zimg


def way_in():
    """Names the scaler in a plan, so a segment drawn through one is never taken for the other's."""
    return "zimg-lanczos" if zimg() else SWS


def probe(path):
    """Picture size as shown (rotation and non-square pixels applied), rate, length and colour tags."""
    try:
        p = subprocess.run([FP, "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format",
                            "-of", "json", path], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SceneFault("reader", f"ffprobe failed on {os.path.basename(path)}: {e}")
    d = json.loads(p.stdout or "{}")
    s = (d.get("streams") or [{}])[0]
    rot = 0
    for sd in s.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(sd["rotation"]) % 360
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    sar = (s.get("sample_aspect_ratio") or "1:1").split(":")
    if len(sar) == 2 and sar[0] != sar[1] and int(sar[0] or 0) and int(sar[1] or 0):
        w = int(round(w * int(sar[0]) / int(sar[1])))     # non-square pixels: stored size is not picture size
    if rot in (90, 270):
        w, h = h, w
    fr = s.get("avg_frame_rate") or "30/1"
    num, den = (fr.split("/") + ["1"])[:2]
    try:
        fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    except ValueError:
        fps = 30.0
    return {"w": w, "h": h, "fps": fps or 30.0, "dur": float(d.get("format", {}).get("duration") or s.get("duration") or 0),
            "matrix": s.get("color_space"), "range": s.get("color_range"), "trc": s.get("color_transfer")}


def frame_bytes(asset):
    """Bytes in one decoded frame of this asset: RGBA, or one byte a pixel for a matte read as grey."""
    gray = bool(asset.get("gray")) and asset.get("trc") not in ("arib-std-b67", "smpte2084")
    return asset["w"] * asset["h"] * (1 if gray else 4)


def fit(w, h):
    k = min(1.0, MAX_PIC / max(w, h, 1))
    return (max(int(w * k) // 2 * 2, 2), max(int(h * k) // 2 * 2, 2))


def read_filter(info, rate, w, h, gray=False):
    """Decode to full-range RGBA with the source's matrix named, never guessed by swscale. ``gray``:
    one byte a pixel, the luma alone at full range (a matte), which no chroma has to be scaled for."""
    m = _MATRIX.get(info.get("matrix"), "bt709" if info["h"] >= 720 or info["w"] >= 1280 else "smpte170m")
    r = "pc" if info.get("range") == "pc" else "tv"
    sws = SWS
    if info.get("trc") in ("arib-std-b67", "smpte2084"):
        tin = info["trc"]
        return (f"fps={rate}:round=near,zscale=tin={tin}:min=bt2020nc:pin=bt2020:rin=limited:t=linear:npl=203,"
                f"format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=pc,"
                f"scale={w}:{h}:flags={sws},format=rgba")
    if gray:            # luma only: no chroma to scale and no matrix, so the plain planar scaler, which is vectorised
        return f"fps={rate}:round=near,scale={w}:{h}:flags=lanczos+accurate_rnd:in_range={r}:out_range=pc,format=gray"
    if zimg() and m in _ZMATRIX:
        return (f"fps={rate}:round=near,zscale=w={w}:h={h}:filter=lanczos:min={_ZMATRIX[m]}:rin={'full' if r == 'pc' else 'limited'},"
                f"format=gbrp,format=rgba")
    return f"fps={rate}:round=near,scale={w}:{h}:flags={sws}:in_color_matrix={m}:in_range={r}:out_range=pc,format=rgba"


def wide_pipe(f):
    """A megabyte of pipe where the OS gives one (Linux): a frame crosses in four writes, not sixty,
    so neither side is woken sixty times a frame."""
    try:
        import fcntl
        fcntl.fcntl(f.fileno(), getattr(fcntl, "F_SETPIPE_SZ", 1031), 1 << 20)
    except (ImportError, OSError, ValueError, AttributeError):
        pass


class Decoder:
    """One ffmpeg rawvideo pipe, reading forward from frame n0 of an asset at its read rate.

    ``ahead``: frames a thread of its own keeps read. A frame is far larger than a pipe, so without
    it ffmpeg stops after every frame until the reader comes back for it, and two sources on one
    frame decode one after the other; with it they decode side by side."""

    def __init__(self, asset, n0, threads=2, ahead=0):
        gray = bool(asset.get("gray")) and asset.get("trc") not in ("arib-std-b67", "smpte2084")
        self.a, self.pos, self.size = asset, n0, frame_bytes(asset)
        cmd = [FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", str(threads),
               "-ss", f"{n0 / asset['rate']:.6f}", "-i", asset["abs"],
               "-vf", read_filter(asset, asset["rate"], asset["w"], asset["h"], gray),
               "-f", "rawvideo", "-pix_fmt", "gray" if gray else "rgba", "-an", "pipe:1"]
        try:
            self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=self.size)
        except OSError as e:
            raise SceneFault("reader", f"cannot start ffmpeg: {e}")
        wide_pipe(self.p.stdout)
        self.q = self.th = None
        self.closed = self.ended = False
        if ahead > 0:
            self.q = queue.Queue(maxsize=ahead)
            self.th = threading.Thread(target=self._pump, daemon=True)
            self.th.start()

    def _read(self):
        try:
            b = self.p.stdout.read(self.size)
        except (OSError, ValueError):
            return None
        return b if len(b) == self.size else None

    def _pump(self):
        while not self.closed:
            b = self._read()
            while not self.closed:
                try:
                    self.q.put(b, timeout=0.2)
                    break
                except queue.Full:
                    continue
            if b is None:
                return

    def next(self):
        if self.ended:              # the source gave its last frame: asked again, it says so at once
            return None
        if self.q is None:
            b = self._read()
        else:
            b = None
            while not self.closed:
                try:
                    b = self.q.get(timeout=0.5)
                    break
                except queue.Empty:
                    if not self.th.is_alive() and self.q.empty():
                        break
        if b is None:
            self.ended = True
            return None
        self.pos += 1
        return b

    def close(self):
        self.closed = True
        try:
            self.p.kill()
            self.p.wait(timeout=5)
        except Exception:      # noqa: BLE001
            pass
        if self.th is not None:
            self.th.join(timeout=5)
        try:
            self.p.stdout.close()
        except Exception:      # noqa: BLE001
            pass


class Reader(threading.Thread):
    """Reads ahead of the render. demand: {output frame: [(asset id, source frame), ...]}, and
    ``order``: the output frames in the order they will be drawn (rising when not given).
    A frame is kept only while a use is near."""
    HORIZON = 120

    def __init__(self, assets, demand, order=None, ahead=36):
        super().__init__(daemon=True)
        self.assets, self.demand, self.ahead = assets, demand, ahead
        self.order = [f for f in (order if order is not None else sorted(demand)) if f in demand]
        self.uses = collections.defaultdict(collections.deque)
        for i, f in enumerate(self.order):
            for k in demand[f]:
                self.uses[k].append(i)
        self.index = {f: i for i, f in enumerate(self.order)}
        self.frames, self.at, self.done, self.err = {}, -1, False, None
        self.cv = threading.Condition()
        self.decoders = {}
        self.decoded, self.seeks, self.seek_s, self.clock = 0, 0, 0.0, 0
        self.progress = time.monotonic()

    def run(self):
        try:
            for i, f in enumerate(self.order):
                with self.cv:                # the window counts demanded frames, so sparse requests never stall
                    while i > self.at + self.ahead and not self.done:
                        self.cv.wait(0.2)
                    if self.done:
                        return
                for k in self.demand[f]:
                    self.fetch(k)
        except Exception as e:      # noqa: BLE001
            self.err = e
            with self.cv:
                self.cv.notify_all()
        finally:
            for ds in self.decoders.values():
                for d in ds:
                    d.close()

    PER_ASSET, OPEN = 3, 12

    def decoder_for(self, aid, n):
        """A decoder of this asset standing at frame n or up to 90 frames before it; else a new one.

        One source can be on the frame twice, at two moments (the take, and a card of an earlier
        moment of it). With one decoder a source those two reads took turns to reopen it, twice a
        frame, each time through a new ffmpeg and the frames back to the last key frame (on the box:
        80 reopenings in a 31 s film, 0.2 s each). Each moment keeps its own decoder now; the one
        used longest ago goes when an asset has three or the film has twelve."""
        pool = self.decoders.setdefault(aid, [])
        best = None
        for d in pool:
            if d.pos <= n and n - d.pos <= 90 and (best is None or d.pos > best.pos):
                best = d
        if best is None:
            everyone = [d for ds in self.decoders.values() for d in ds]
            if len(pool) >= self.PER_ASSET or len(everyone) >= self.OPEN:
                old = min(pool if len(pool) >= self.PER_ASSET else everyone, key=lambda d: d.used)
                for ds in self.decoders.values():
                    if old in ds:
                        ds.remove(old)
                old.close()
            best = Decoder(self.assets[aid], n, ahead=2)
            best.t0 = time.monotonic()
            pool.append(best)
            self.seeks += 1
        self.clock += 1
        best.used = self.clock
        return best

    def wanted(self, k):
        q = self.uses.get(k)
        return bool(q) and q[0] <= self.at + self.HORIZON + self.ahead

    def store(self, k, b):
        with self.cv:
            self.frames[k] = b
            self.cv.notify_all()

    def fetch(self, k):
        with self.cv:
            if k in self.frames:
                return
        aid, n = k
        d = self.decoder_for(aid, n)
        last = None
        while d.pos <= n:
            at = d.pos
            b = d.next()
            if getattr(d, "t0", None) is not None:      # how long this seek took to give its first frame
                self.seek_s += time.monotonic() - d.t0
                d.t0 = None
            if b is None:           # past the end of the source: hold the last picture
                b = last or self.tail(aid, n)
                self.store(k, b)
                return
            self.decoded += 1
            self.progress = time.monotonic()
            last = b
            if at == n or self.wanted((aid, at)):
                self.store((aid, at), b)

    def tail(self, aid, n):
        a = self.assets[aid]
        for back in (2, 8, 30, 120):
            d = Decoder(a, max(0, n - back))
            got = None
            while True:
                b = d.next()
                if b is None:
                    break
                got = b
            d.close()
            if got:
                return got
        return bytes(frame_bytes(a))

    def get(self, k):
        with self.cv:
            while k not in self.frames:
                if self.err:
                    raise self.err if isinstance(self.err, SceneFault) else SceneFault("reader", repr(self.err))
                if self.done:
                    raise SceneFault("reader", "reader stopped")
                self.cv.wait(0.5)
            return self.frames[k]

    def release(self, f):
        with self.cv:
            i = self.index.get(f)
            if i is not None:
                self.at = max(self.at, i)
            for k in self.demand.get(f, ()):
                q = self.uses.get(k)
                while q and q[0] <= (i if i is not None else -1):
                    q.popleft()
                # Keep a frame the reader may already have passed. Dropping it here would leave a
                # later use waiting on a fetch that already happened.
                if not q or q[0] - self.at > self.HORIZON + self.ahead:
                    self.frames.pop(k, None)
            self.cv.notify_all()

    def stop(self):
        with self.cv:
            self.done = True
            self.cv.notify_all()
