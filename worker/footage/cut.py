"""Cutting a clip: 1-3 s inside one shot, from the source's best stream, reframed to the film, graded to
one look across the film by measured signal (ffmpeg signalstats), never by a guess."""
import os
import re
import statistics
import subprocess

from . import fetch

MIN_S, MAX_S = 1.0, 3.0
PRE_S = 2.0                     # fast seek lands this far before the cut, then an exact seek


def window(shot, t, want):
    """(t0, t1): want seconds (MIN_S..MAX_S) inside the shot, around t, a few frames clear of its cuts."""
    a, b = shot[0] + 0.12, shot[1] - 0.12
    want = min(max(want, MIN_S), MAX_S, max(b - a, 0.2))
    t0 = min(max(t - want / 2, a), max(a, b - want))
    return round(t0, 3), round(t0 + want, 3)


def grab(stream, url, t0, t1, out_path):
    """The section, near-lossless, no sound: ffmpeg seeks the resolved stream; yt-dlp's own section
    download when the stream will not seek or has expired."""
    dur = t1 - t0
    if stream and stream.get("url"):
        hdr = "".join(f"{k}: {v}\r\n" for k, v in (stream.get("headers") or {}).items())
        cmd = [fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(t0 - PRE_S, 0):.3f}"]
        if hdr:
            cmd += ["-headers", hdr]
        cmd += ["-i", stream["url"], "-ss", f"{min(PRE_S, t0):.3f}", "-t", f"{dur:.3f}", "-an", "-c:v", "libx264", "-crf", "12",
                "-preset", "veryfast", "-pix_fmt", "yuv420p", "-y", out_path]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 2000:
                return out_path, "stream"
        except subprocess.TimeoutExpired:
            pass
    raw = out_path + ".src.mp4"
    fetch.section(url, max(t0 - 0.5, 0), t1 + 0.5, raw)
    try:
        r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{min(0.5, t0):.3f}", "-i", raw, "-t", f"{dur:.3f}",
                            "-an", "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-y", out_path],
                           capture_output=True, text=True, timeout=120)
    finally:
        if os.path.exists(raw):
            os.remove(raw)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise fetch.FetchError("the section could not be cut: " + (r.stderr or "")[-200:])
    return out_path, "section"


def local(src, t0, t1, out_path):
    """The section cut from a copy of the source already on disk (fetch.whole), near-lossless, no sound."""
    r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(t0, 0):.3f}", "-i", src, "-t", f"{t1 - t0:.3f}",
                        "-an", "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-y", out_path],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 2000:
        raise fetch.FetchError("the section could not be cut from the whole source: " + (r.stderr or "")[-200:])
    return out_path, "whole"


def measure(path):
    """{yavg, ylow, yhigh, sat, w, h}: the clip's light and colour, over its frames."""
    r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-nostats", "-i", path, "-vf", "scale=320:-2,signalstats,metadata=print",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    def series(k):
        return [float(x) for x in re.findall(rf"lavfi\.signalstats\.{k}=([0-9.]+)", r.stderr)]
    out = {}
    for k, name in (("YAVG", "yavg"), ("YLOW", "ylow"), ("YHIGH", "yhigh"), ("SATAVG", "sat")):
        v = series(k)
        out[name] = round(statistics.median(v), 2) if v else None
    return out


def target(stats):
    """The film's look: the median light, spread and colour of its clips. A film that is mostly black
    and white is graded black and white."""
    ok = [s for s in stats if s.get("yavg") is not None]
    if not ok:
        return dict(yavg=110.0, spread=150.0, sat=40.0, mono=False)
    sats = [s["sat"] for s in ok]
    mono = sum(1 for v in sats if v < 6.0) > len(ok) / 2
    colour = [v for v in sats if v >= 6.0]
    return dict(yavg=statistics.median(s["yavg"] for s in ok), spread=statistics.median(max(s["yhigh"] - s["ylow"], 20.0) for s in ok),
                sat=statistics.median(colour) if colour else 0.0, mono=mono)


def eq_for(s, tgt):
    """ffmpeg eq values that move one clip toward the film's look, bounded so no clip is wrecked."""
    if s.get("yavg") is None:
        return dict(contrast=1.0, brightness=0.0, saturation=1.0)
    spread = max(s["yhigh"] - s["ylow"], 20.0)
    c = min(max(tgt["spread"] / spread, 0.85), 1.25)
    after = (s["yavg"] - 128.0) * c + 128.0
    b = min(max((tgt["yavg"] - after) / 255.0, -0.10), 0.10)
    if tgt["mono"]:
        sat = 0.0
    elif s["sat"] < 6.0 or tgt["sat"] <= 0:
        sat = 1.0
    else:
        sat = min(max(tgt["sat"] / s["sat"], 0.75), 1.3)
    return dict(contrast=round(c, 3), brightness=round(b, 3), saturation=round(sat, 3))


def borders(path):
    """The picture inside a source's own black bars (a 4:3 film pillarboxed in 16:9, a letterboxed
    scan), by ffmpeg cropdetect: "w:h:x:y", or None when the bars are under 8% of the frame."""
    r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-nostats", "-i", path, "-vf", "cropdetect=limit=24:round=2:reset=0",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    from . import shots
    info = shots.probe(path)
    if not found or not info.get("w") or not info.get("h"):
        return None
    w, h, x, y = (int(v) for v in found[-1])
    W0, H0 = int(info["w"]), int(info["h"])
    if w <= 0 or h <= 0 or w * h > 0.92 * W0 * H0 or w * h < 0.2 * W0 * H0:
        return None
    return f"{w}:{h}:{x}:{y}"


def finish(src, out_path, W, H, x=0.5, eq=None, fps=30, trim=(0.0, 0.0)):
    """Reframe to W x H (cover, centred across on x), grade, encode for the film. trim: the fractions of
    height cut from the top and bottom first (a library's timecode band), so the cover crop zooms past it."""
    eq = eq or {}
    top, bottom = (min(max(float(t or 0.0), 0.0), 0.3) for t in (list(trim or (0.0, 0.0)) + [0.0, 0.0])[:2])
    band = f"crop=iw:ih*{1 - top - bottom:.4f}:0:ih*{top:.4f}," if top or bottom else ""
    bars = borders(src)
    vf = ((f"crop={bars}," if bars else "") + band + f"scale=w='if(gt(a,{W}/{H}),-2,{W})':h='if(gt(a,{W}/{H}),{H},-2)':flags=lanczos,"
          f"crop={W}:{H}:x='min(max(iw*{x:.3f}-{W}/2,0),iw-{W})':y='(ih-{H})/2',"
          f"eq=contrast={eq.get('contrast', 1.0)}:brightness={eq.get('brightness', 0.0)}:saturation={eq.get('saturation', 1.0)},"
          f"fps={fps},format=yuv420p")
    r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", src, "-vf", vf, "-an", "-c:v", "libx264", "-crf", "18",
                        "-preset", "medium", "-movflags", "+faststart", "-y", out_path], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("finish failed: " + (r.stderr or "")[-300:])
    return out_path
