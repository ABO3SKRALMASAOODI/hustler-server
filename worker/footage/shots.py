"""A source's shots (ffmpeg's scene score on the scanning copy) and its frames at given times."""
import concurrent.futures as cf
import json
import os
import re
import subprocess

from . import fetch

SCENE = 0.30          # the threshold the reference study read the references' cuts with
MIN_GAP_S = 0.08


def probe(path):
    """{dur, w, h, fps} of a media file."""
    r = subprocess.run([fetch.ffprobe(), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate",
                        "-of", "json", path], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout or "{}")
    except ValueError:
        d = {}
    v = next((s for s in d.get("streams") or [] if s.get("codec_type") == "video"), {})
    num, _, den = str(v.get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return dict(dur=float((d.get("format") or {}).get("duration") or 0.0), w=v.get("width"), h=v.get("height"), fps=round(fps, 3),
                audio=any(s.get("codec_type") == "audio" for s in d.get("streams") or []))


def cuts(path, upto):
    """Shots [(t0, t1)] of the first upto seconds."""
    r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-nostats", "-t", f"{upto:.2f}", "-i", path, "-an", "-vf",
                        f"scale=320:-2,select='gt(scene,{SCENE})',showinfo", "-f", "null", "-"], capture_output=True, text=True)
    ts = []
    for t in (float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)):
        if not ts or t - ts[-1] > MIN_GAP_S:
            ts.append(t)
    b = [0.0] + [t for t in ts if 0.05 < t < upto - 0.05] + [upto]
    return [(round(b[i], 3), round(b[i + 1], 3)) for i in range(len(b) - 1) if b[i + 1] - b[i] > 0.04]


def shot_at(shots, t):
    for s in shots:
        if s[0] <= t < s[1]:
            return s
    return shots[-1] if shots else (0.0, t + 1.0)


def frames(path, times, out_dir, w=384, workers=8):
    """{t: jpg path} for each time (fast seek on the scanning copy)."""
    os.makedirs(out_dir, exist_ok=True)

    def one(t):
        p = os.path.join(out_dir, f"f{t:09.3f}.jpg")
        if not os.path.exists(p):
            subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                            "-vf", f"scale={w}:-2", "-q:v", "3", "-y", p], capture_output=True)
        return t, (p if os.path.exists(p) else None)
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        return {t: p for t, p in pool.map(one, times) if p}
