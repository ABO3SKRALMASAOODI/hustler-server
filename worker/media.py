"""ffmpeg / ffprobe primitives. The agent never touches pixels — everything
pixel-shaped funnels through here."""

import collections
import hashlib
import json
import os
import re
import subprocess
import threading
import time

import config


class MediaError(RuntimeError):
    pass


def run(cmd, timeout=None, progress_cb=None, expected_out_s=None):
    """Run ffmpeg/ffprobe. With progress_cb, parses -progress pipe:1 output
    and reports percent of expected_out_s."""
    timeout = timeout or config.FFMPEG_TIMEOUT_S
    if progress_cb and expected_out_s:
        # ffmpeg logs to stderr for the whole encode. Left as its own
        # un-drained PIPE it fills the OS buffer, ffmpeg blocks on write,
        # stops emitting progress, and the reader deadlocks — a font-less
        # Devanagari caption run spamming "glyph not found" per frame did
        # exactly this in prod (progress froze ~14% in, slot wedged for
        # hours). Merge stderr INTO stdout so one continuously-drained
        # stream carries both; the buffer can never fill.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        # A genuine hang emits nothing on either stream, so a watchdog still
        # enforces a hard wall-clock cap and a shorter no-progress stall cap;
        # killing the process closes the pipe and unblocks the read loop.
        stall_s = config.FFMPEG_STALL_TIMEOUT_S
        last = [time.monotonic()]
        kill_reason = []

        def _watchdog():
            start = time.monotonic()
            while proc.poll() is None:
                now = time.monotonic()
                if now - start > timeout:
                    kill_reason.append(f"wall-clock {timeout}s exceeded")
                    proc.kill()
                    return
                if now - last[0] > stall_s:
                    kill_reason.append(f"no progress for {stall_s}s")
                    proc.kill()
                    return
                time.sleep(2)

        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()
        # Keep only real log lines for error reporting — the merged stream is
        # dominated by -progress key=value pairs and ffmpeg's status ticker.
        tail = collections.deque(maxlen=40)
        _noise = ("out_time", "frame=", "fps=", "bitrate=", "total_size=",
                  "speed=", "progress=", "dup_frames=", "drop_frames=",
                  "stream_", "size=")
        try:
            for line in proc.stdout:
                last[0] = time.monotonic()
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        secs = int(line.split("=", 1)[1]) / 1_000_000.0
                        progress_cb(min(0.999, secs / max(0.01, expected_out_s)))
                    except ValueError:
                        pass
                elif line and not line.startswith(_noise):
                    tail.append(line)
            proc.wait()
        finally:
            wd.join(timeout=3)
        if kill_reason:
            raise MediaError(f"ffmpeg killed: {kill_reason[0]}")
        if proc.returncode != 0:
            raise MediaError("ffmpeg failed: " + " | ".join(list(tail)[-12:]))
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


def black_seconds(path, duration=None):
    """Total seconds of (near-)black video via ffmpeg blackdetect on a cheap
    downscaled/low-fps pass (fast even on a full-res final). Best-effort: any
    failure returns 0.0 so render verification can never itself fail a good
    render — a broken render is caught by the duration check regardless."""
    cmd = ["ffmpeg", "-i", path, "-vf",
           "fps=4,scale=64:-2,blackdetect=d=0.1:pix_th=0.10",
           "-an", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=config.FFMPEG_TIMEOUT_S)
    except Exception:
        return 0.0
    total = 0.0
    for m in re.finditer(r"black_duration:(\d+(?:\.\d+)?)", p.stderr or ""):
        total += float(m.group(1))
    return round(total, 2)


def detect_silences(wav_path, duration):
    """ffmpeg silencedetect -> [[t0, t1], ...] (seconds). Raises MediaError on
    a nonzero ffmpeg exit so a failed detection is distinguishable from a
    genuinely silence-free clip (the indexer records it as a warning instead
    of silently degrading to 'no silences')."""
    cmd = ["ffmpeg", "-i", wav_path, "-af",
           f"silencedetect=noise={config.SILENCE_NOISE_DB}:d={config.SILENCE_MIN_S}",
           "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=config.FFMPEG_TIMEOUT_S)
    if p.returncode != 0:
        tail = "\n".join((p.stderr or "").strip().splitlines()[-6:])
        raise MediaError(f"silencedetect failed (rc={p.returncode}): {tail}")
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
    """Write ONE frame from `src` at ~t seconds to `dst`.

    ffmpeg's exit code is NOT a trustworthy success signal here. Seeking at or
    past the last frame encodes nothing, and through ffmpeg 6 that prints
    "Output file is empty, nothing was encoded ... this may be an error" and
    exits ZERO without creating dst (ffmpeg 7+ turned it into a hard error).
    The worker image is Debian's ffmpeg 5.x — the exit-0 flavour. A caller that
    trusts rc=0 is then holding a path to a file that does not exist, which is
    exactly how one missing 320px thumbnail took down an entire index (the
    upload died on FileNotFoundError and the user was told "I couldn't analyze
    that video"). So the POSTCONDITION is verified here rather than assumed: a
    readable frame is on disk or this raises MediaError — the failure every
    caller already handles.

    Two seek modes are tried before giving up. Input seek (-ss before -i) is
    the fast path. Output seek (-ss after -i) decodes from the start: slower,
    but it lands frames that input seek misses on files with sparse keyframes
    or edit lists (phone screen recordings are full of both).
    """
    vf = ["-vf", rf"scale={width}:-2"] if width else []
    ts = f"{max(0.0, t):.3f}"
    attempts = (
        ["ffmpeg", "-y", "-ss", ts, "-i", src,
         "-frames:v", "1", *vf, "-q:v", str(quality), dst],
        ["ffmpeg", "-y", "-i", src, "-ss", ts,
         "-frames:v", "1", *vf, "-q:v", str(quality), dst],
    )
    last_err = None
    for cmd in attempts:
        try:
            run(cmd, timeout=120)
        except MediaError as e:
            last_err = str(e)
            continue
        if os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return dst
        last_err = "ffmpeg reported success but wrote no frame"
        # A partial/zero-byte file would otherwise look like a real frame to
        # the next existence check.
        try:
            os.unlink(dst)
        except OSError:
            pass
    raise MediaError(f"no frame at {ts}s of {os.path.basename(src)}: "
                     f"{last_err}")


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
