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


def rotation_of(stream):
    """Degrees the display matrix rotates this stream by (0/±90/180).

    A phone writes the sensor's frame and a matrix saying how to turn it, so
    the coded size is NOT what anyone sees: a clip recorded holding the phone
    one way is stored the other way round plus a 90° matrix.
    """
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"])))
            except (TypeError, ValueError):
                pass
    try:                                    # older ffprobe: a rotate tag
        return int(round(float((stream.get("tags") or {}).get("rotate"))))
    except (TypeError, ValueError):
        return 0


def probe(path):
    """What a PLAYER shows for this file — not what the container claims.

    Two of these fields used to be read straight off the container and were
    wrong for ordinary phone footage:

    * width/height are the DISPLAY size, i.e. the display matrix applied. The
      coded size alone said "1284x2778 portrait" for a clip that is landscape
      on every player and came out of our own proxy encoder at 1558x720 —
      ffmpeg auto-rotates before -vf, so the index disagreed with the video it
      described and the agent reasoned about the wrong orientation.
    * video_duration is the PICTURE track's own length, which can be far
      shorter than `duration` (the container's). An iOS screen recording stops
      emitting frames while the screen is static, so a 16.65s clip can hold
      2.37s of video against a full-length audio track. `duration` stays the
      container's, because that IS what a player shows — it holds the last
      frame for the rest — but callers that touch frames need to know the
      picture runs out early rather than seeking into nothing.
    """
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
    try:
        video_duration = round(float(v.get("duration")), 3)
    except (TypeError, ValueError):
        video_duration = None               # container carries no per-stream length

    def _rate(key):
        val = v.get(key) or "0/1"
        num, den = (val.split("/") + ["1"])[:2]
        return float(num) / float(den) if float(den or 0) else 0.0

    r, avg = _rate("r_frame_rate"), _rate("avg_frame_rate")
    fps = avg or r or 30.0
    vfr = bool(r and avg and abs(r - avg) > 0.5)
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if abs(rotation_of(v)) % 180 == 90:
        w, h = h, w
    # Pixel aspect ratio. Almost always 1, but anamorphic material (old DV,
    # some broadcast sources) stores a 16:9 picture in 4:3 pixels, and any
    # filter chain that forces setsar=1 without widening the frame squashes
    # it. 1.0 on anything unparseable — a wrong non-1 value distorts every
    # frame, where a wrong 1.0 only reverts to today's behaviour.
    sar = 1.0
    try:
        sn, sd = (str(v.get("sample_aspect_ratio") or "1:1").split(":") + ["1"])[:2]
        sar = float(sn) / float(sd) if float(sd or 0) else 1.0
    except (TypeError, ValueError):
        sar = 1.0
    if not (0.1 <= sar <= 10.0):
        sar = 1.0
    return {
        "sar": round(sar, 6),
        "duration": round(duration, 3),
        "video_duration": video_duration,
        "fps": round(fps, 3),
        "width": w,
        "height": h,
        "has_audio": a is not None,
        "vfr": vfr,
    }


def _encode_proxy(src, dst, fps, vfr, has_audio, pad_s=0.0, progress_cb=None,
                  expected_out_s=None):
    h = config.PROXY_HEIGHT
    vf = rf"scale=-2:min({h}\,floor(ih/2)*2)"
    if pad_s > 0:
        # Clone the last frame forward. After scale, so it clones a proxy frame.
        vf += f",tpad=stop_mode=clone:stop_duration={pad_s:.3f}"
    cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf,
           "-c:v", "libx264", "-preset", config.PROXY_PRESET,
           "-crf", str(config.PROXY_CRF), "-pix_fmt", "yuv420p"]
    if vfr:
        cmd += ["-fps_mode", "cfr", "-r", f"{max(1.0, min(fps, 60.0)):.3f}"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]
    # -progress on stdout: without it this encode reports NOTHING for its whole
    # run — 15 min on a 19-min source — so the job sits at one percentage and
    # the user watches a frozen bar. It also buys the stall watchdog: the plain
    # branch of run() has none, so a wedged proxy encode holds the only index
    # slot for the full 90-min ffmpeg wall clock.
    if progress_cb and expected_out_s:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += ["-movflags", "+faststart", dst]
    run(cmd, progress_cb=progress_cb, expected_out_s=expected_out_s)


# A proxy shorter than this fraction/margin of the recording isn't a proxy of
# the recording. Loose enough that a normal encode's last-frame rounding never
# trips it.
PROXY_SHORT_FRAC = 0.02
PROXY_SHORT_MIN_S = 0.4


def make_proxy(src, dst, fps, vfr, has_audio, duration=None, progress_cb=None):
    """Downscaled H.264 proxy, +faststart. VFR sources are normalized to CFR
    here so every downstream timestamp is stable.

    The proxy must be a faithful rendition of what a player shows for `src`,
    because every timestamp the agent reasons about is checked against it. A
    picture track can end long before the recording does — an iOS screen
    recording stops writing frames while the screen is static, so a 16.65s clip
    carried 2.37s of video against a full-length audio track — and CFR
    normalization cannot invent frames past the last one it was given. That
    produced a 2s proxy of a 16s recording: the player showed 0:02 while the
    agent was told 0.3 min, and every shot/cut pointed at footage the proxy
    didn't have.

    So the result is MEASURED rather than assumed, and a short picture track is
    filled by holding the last frame — exactly what a player does with the same
    file. Measuring (not trusting the container's per-stream metadata) means
    this covers a genuinely short track and a truncated encode identically,
    without having to tell them apart.
    """
    _encode_proxy(src, dst, fps, vfr, has_audio, progress_cb=progress_cb,
                  expected_out_s=duration)
    if not duration or duration <= 0:
        return
    got = probe(dst)
    have = got["video_duration"] or got["duration"]
    gap = duration - have
    if gap <= max(PROXY_SHORT_MIN_S, PROXY_SHORT_FRAC * duration):
        return
    print(f"[media] proxy picture track ran {gap:.2f}s short of the "
          f"{duration:.2f}s recording ({os.path.basename(src)}) — holding the "
          f"last frame to fill it", flush=True)
    _encode_proxy(src, dst, fps, vfr, has_audio, pad_s=gap,
                  progress_cb=progress_cb, expected_out_s=duration)


def extract_wav(src, dst):
    run(["ffmpeg", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", dst])


_SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")


def black_seconds(path, duration=None):
    """Total seconds of (near-)black video via ffmpeg blackdetect on a cheap
    downscaled/low-fps pass (fast even on a full-res final). Best-effort: any
    failure returns 0.0 so render verification can never itself fail a good
    render — a broken render is caught by the duration check regardless.

    `duration` limits the scan to the first N seconds. It exists so the render
    check can measure the PROGRAMME only: every export now ends on a black
    branded card, and counting those seconds would inflate the black ratio of
    every short video against a source that has no card. (This parameter was
    accepted and silently ignored for a long time — passing it used to be a
    no-op, so a caller that "fixed" the ratio by passing it changed nothing.)
    """
    cmd = ["ffmpeg", "-i", path]
    if duration and duration > 0:
        cmd += ["-t", f"{float(duration):.3f}"]
    cmd += ["-vf", "fps=4,scale=64:-2,blackdetect=d=0.1:pix_th=0.10",
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


def normalize_audio(src, dst, lufs=-16.0, timeout=None):
    """Re-encode audio to MP3 at a consistent perceived loudness.

    Fetched material arrives at wildly different levels: a Great 78 Project
    transfer measured -31.8 dBFS against the bundled library's -16.9, a ~15 dB
    gap. Since every music item then gets the SAME default -18 dB gain (and
    -12 dB more while ducking under speech), an un-normalized archival track
    lands far below where the user expects and reads as "I can't hear the
    music" — a failure the prompt already has a whole bullet about. The
    bundled library was normalized to -16 LUFS on ingest for exactly this
    reason; anything fetched has to meet the same bar or the gain defaults
    mean different things for different sources.

    Re-encoding also drops two nuisances for free: the embedded mjpeg
    cover-art stream IA MP3s carry, and whatever container the source used
    (ogg/flac/wav) — the output is always a plain MP3.

    Single-pass loudnorm. The two-pass form is more accurate but doubles the
    decode of a file we are normalizing for background use, and the error a
    second pass removes is a fraction of a dB.
    """
    run(["ffmpeg", "-v", "error", "-y", "-i", src,
         "-map", "0:a:0", "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11",
         "-c:a", "libmp3lame", "-q:a", "4", "-ar", "48000", dst],
        timeout=timeout)
    return dst


def probe_audio_duration(path):
    """Duration of an audio-only file (probe() requires a video stream)."""
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", path], timeout=120)
    try:
        return round(float(out.strip()), 3)
    except (TypeError, ValueError):
        raise MediaError(f"Could not determine audio duration of {path}")
