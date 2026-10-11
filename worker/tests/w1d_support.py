"""Shared helpers for track 1D's tests: synthetic sources, lossless renders
through the real ``renderer.render_edl``, decoded frames.

Every encode is LOSSLESS (x264 ``-crf 0``) so decoded frames are the
filtergraph's own frames: a lossy encoder smears a 1-frame change across its
GOP and hides exactly what these tests pin (frame-exact accents, bit-exact
pixels outside a region, untouched brand frames).
"""

import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

IDX = {"words": [], "silences": [], "sentences": [],
       "video": {"duration": 12.0, "width": 640, "height": 360,
                 "fps": 25.0}}


def make_source(path, *, w=640, h=360, rate="25", dur=12.0, freq=440,
                kind="testsrc2"):
    """A synthetic source: every frame different, sine audio, H.264."""
    if os.path.exists(path):
        return path
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"{kind}=s={w}x{h}:r={rate}:d={dur}", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:sample_rate=48000:"
                    f"duration={dur}", "-c:v", "libx264", "-preset",
                    "ultrafast", "-crf", "12", "-g", "25", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", path], check=True)
    return path


def make_montage(path, *, w=640, h=360, rate="25", seg=3.0):
    """Four visibly different 3-s shots (four lavfi patterns) in one file,
    so keep spans with source handles cut between real shots."""
    if os.path.exists(path):
        return path
    pats = [f"testsrc2=s={w}x{h}:r={rate}:d={seg}",
            f"mandelbrot=s={w}x{h}:r={rate},trim=duration={seg}",
            f"smptehdbars=s={w}x{h}:r={rate}:d={seg}",
            f"rgbtestsrc=s={w}x{h}:r={rate}:d={seg}"]
    args = []
    for p in pats:
        args += ["-f", "lavfi", "-i", p]
    args += ["-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:"
             f"duration={4 * seg}"]
    fc = "".join(f"[{i}:v]format=yuv420p,setsar=1,"
                 f"fps={rate}[v{i}];" for i in range(4))
    fc += "".join(f"[v{i}]" for i in range(4)) + \
        "concat=n=4:v=1:a=0[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex",
                    fc, "-map", "[v]", "-map", "4:a", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "10", "-g", "25",
                    "-c:a", "aac", "-shortest", path], check=True)
    return path


def lossless(monkeypatch):
    """Make every renderer encode lossless for this test."""
    import renderer
    real = renderer._render_media_run

    def run(cmd, **kw):
        cmd = list(cmd)
        if "-crf" in cmd:
            cmd[cmd.index("-crf") + 1] = "0"
        for flag in ("-maxrate", "-bufsize"):
            while flag in cmd:
                i = cmd.index(flag)
                del cmd[i:i + 2]
        return real(cmd, **kw)
    monkeypatch.setattr(renderer, "_render_media_run", run)


def render(edl, src, out, *, idx=None, preview=True, want_wm=False,
           suppress_outro=True, asset_locals=None, duration=12.0):
    """(seconds, report rows) of a validated render of ``edl``."""
    import fxgraph
    import renderer
    import schemas
    edl = schemas.validate_edl(json.loads(json.dumps(edl)),
                               duration).model_dump()
    rows = []
    tok = fxgraph.collect(rows)
    wd = out + ".wd"
    os.makedirs(wd, exist_ok=True)
    try:
        dur = renderer.render_edl(edl, idx or IDX, src, out, wd, preview,
                                  want_wm=want_wm,
                                  suppress_outro=suppress_outro,
                                  asset_locals=asset_locals or {})
    finally:
        fxgraph.stop_collecting(tok)
    return dur, fxgraph.merge_report(rows)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams",
                          "v:0", "-show_entries",
                          "stream=width,height,pix_fmt,color_range",
                          "-of", "json", path], capture_output=True,
                         text=True, check=True).stdout
    return json.loads(out)["streams"][0]


def frames(path):
    """(Y [n, H, W] uint8, whole yuv420p frames [n, bytes]) of a video."""
    p = probe(path)
    W, H = p["width"], p["height"]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f",
                          "rawvideo", "-pix_fmt", "yuv420p", "-"],
                         capture_output=True, check=True).stdout
    fs = W * H * 3 // 2
    n = len(raw) // fs
    a = np.frombuffer(raw, np.uint8)[:n * fs].reshape(n, fs)
    return a[:, :W * H].reshape(n, H, W), a


def changed(a, b):
    """Frame indexes where two decoded videos differ."""
    n = min(len(a), len(b))
    return [i for i in range(n) if not np.array_equal(a[i], b[i])]


def audio_md5(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map",
                        "0:a:0", "-f", "md5", "-"], capture_output=True,
                       text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def frame_md5s(path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map",
                          "0:v:0", "-f", "framemd5", "-"],
                         capture_output=True, text=True, check=True).stdout
    return [ln.split(",")[-1].strip() for ln in out.splitlines()
            if ln and not ln.startswith("#")]


def accent(fid, start_f, fps, kind="negative", n=1, stage="pre_captions",
           **kw):
    """An accent item on exact program frames [start_f, start_f + n)."""
    f3 = round(float(fps), 3)
    it = {"id": fid, "type": "accent", "stage": stage,
          "start": round(start_f / f3, 2),
          "end": round((start_f + n) / f3, 2), "kind": kind}
    it.update(kw)
    return it
