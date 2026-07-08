"""ffmpeg / ffprobe primitives. The agent never touches pixels — everything
pixel-shaped funnels through here."""

import hashlib
import json
import os
import re
import subprocess

import config


class MediaError(RuntimeError):
    pass


def run(cmd, timeout=None, progress_cb=None, expected_out_s=None):
    """Run ffmpeg/ffprobe. With progress_cb, parses -progress pipe:1 output
    and reports percent of expected_out_s."""
    timeout = timeout or config.FFMPEG_TIMEOUT_S
    if progress_cb and expected_out_s:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        tail = []
        try:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        secs = int(line.split("=", 1)[1]) / 1_000_000.0
                        progress_cb(min(0.999, secs / max(0.01, expected_out_s)))
                    except ValueError:
                        pass
            _, err = proc.communicate(timeout=timeout)
            tail = (err or "").splitlines()[-12:]
        except subprocess.TimeoutExpired:
            proc.kill()
            raise MediaError(f"ffmpeg timed out after {timeout}s")
        if proc.returncode != 0:
            raise MediaError("ffmpeg failed: " + " | ".join(tail))
        return ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MediaError(f"{os.path.basename(cmd[0])} timed out after {timeout}s")
    if p.returncode != 0:
        tail = (p.stderr or "").splitlines()[-12:]
        raise MediaError(f"{os.path.basename(cmd[0])} failed: " + " | ".join(tail))
    return p.stdout


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fps_of(stream):
    for key in ("avg_frame_rate", "r_frame_rate"):
        v = stream.get(key) or ""
        if "/" in v:
            num, den = v.split("/")
            if float(den or 0) > 0 and float(num) > 0:
                return float(num) / float(den)
    return 30.0


def probe(path):
    """duration, fps, resolution, has_audio, vfr flag."""
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", path], timeout=120)
    data = json.loads(out)
    v = next((s for s in data.get("streams", [])
              if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", [])
              if s.get("codec_type") == "audio"), None)
    if v is None:
        raise MediaError("No video stream found in file")
    duration = float(data.get("format", {}).get("duration")
                     or v.get("duration") or 0)
    if duration <= 0:
        raise MediaError("Could not determine video duration")

    def _rate(key):
        val = v.get(key) or "0/1"
        num, den = (val.split("/") + ["1"])[:2]
        return float(num) / float(den) if float(den or 0) else 0.0

    r, avg = _rate("r_frame_rate"), _rate("avg_frame_rate")
    fps = avg or r or 30.0
    vfr = bool(r and avg and abs(r - avg) > 0.5)
    return {
        "duration": round(duration, 3),
        "fps": round(fps, 3),
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "has_audio": a is not None,
        "vfr": vfr,
    }


def make_proxy(src, dst, fps, vfr, has_audio):
    """720p H.264 proxy, +faststart. VFR sources are normalized to CFR here so
    every downstream timestamp is stable."""
    vf = r"scale=-2:min(720\,floor(ih/2)*2)"
    cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-pix_fmt", "yuv420p"]
    if vfr:
        cmd += ["-fps_mode", "cfr", "-r", f"{max(1.0, min(fps, 60.0)):.3f}"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", dst]
    run(cmd)


def extract_wav(src, dst):
    run(["ffmpeg", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", dst])


_SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")


def detect_silences(wav_path, duration):
    """ffmpeg silencedetect -> [[t0, t1], ...] (seconds)."""
    cmd = ["ffmpeg", "-i", wav_path, "-af",
           f"silencedetect=noise={config.SILENCE_NOISE_DB}:d={config.SILENCE_MIN_S}",
           "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=config.FFMPEG_TIMEOUT_S)
    silences, start = [], None
    for line in (p.stderr or "").splitlines():
        ms = _SIL_START.search(line)
        if ms:
            start = float(ms.group(1))
            continue
        me = _SIL_END.search(line)
        if me and start is not None:
            silences.append([round(start, 2), round(float(me.group(1)), 2)])
            start = None
    if start is not None:                       # silence runs to the end
        silences.append([round(start, 2), round(duration, 2)])
    return silences


def frame_at(src, t, dst, width=None, quality=4):
    vf = ["-vf", rf"scale={width}:-2"] if width else []
    run(["ffmpeg", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", src,
         "-frames:v", "1", *vf, "-q:v", str(quality), dst], timeout=120)


def duration_of(path):
    return probe(path)["duration"]


def probe_audio_duration(path):
    """Duration of an audio-only file (probe() requires a video stream)."""
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", path], timeout=120)
    try:
        return round(float(out.strip()), 3)
    except (TypeError, ValueError):
        raise MediaError(f"Could not determine audio duration of {path}")
