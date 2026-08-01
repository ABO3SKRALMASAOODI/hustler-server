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


def run(cmd, timeout=None, progress_cb=None, expected_out_s=None,
        cancelled_cb=None):
    """Run ffmpeg/ffprobe. With progress_cb, parses -progress pipe:1 output
    and reports percent of expected_out_s.

    `cancelled_cb` is the FOURTH watchdog reason (round 73): a callable that
    returns True once the job this encode belongs to is no longer ours. It
    hangs off the watchdog rather than off progress_cb because the watchdog
    thread is the only thing here that can actually stop ffmpeg — raising out
    of progress_cb unwinds the read loop and leaves the subprocess running,
    which is the failure it exists to prevent, not a way to fix it. The callback
    must not block: renderer feeds it a flag that the progress write already
    set, so it never touches the database.

    Both branches decode with errors="replace". ffmpeg's log is NOT UTF-8: it
    echoes container metadata verbatim (a Shift-JIS title, a CP-1251 artist)
    and, on damaged input, prints raw bytes inside its decode warnings. With
    strict decoding — the default for text=True — that raised UnicodeDecodeError
    *out of subprocess.run itself*, which is not a MediaError, so every caller's
    `except MediaError` missed it and the whole agent turn died. It cost a real
    user their edit on 2026-07-25 ("'utf-8' codec can't decode byte 0xf9").
    A log line is diagnostics; it must never be able to fail a job.
    """
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
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace")
        # A genuine hang emits nothing on either stream, so a watchdog still
        # enforces a hard wall-clock cap and a shorter no-progress stall cap;
        # killing the process closes the pipe and unblocks the read loop.
        stall_s = config.FFMPEG_STALL_TIMEOUT_S
        last = [time.monotonic()]
        kill_reason = []
        # How much output time is still explainable. Past this the graph is
        # producing video that the timeline says does not exist, which only
        # happens when something in it cannot end (a -loop input with no -t,
        # an overlay with shortest=0). That is a certainty, not a guess, so it
        # is safe to kill on — and it is the ONLY one of the three watchdogs
        # that could have caught the watermark runaway, because a runaway
        # keeps emitting progress (never stalls) for far longer than anyone
        # will wait (the wall-clock cap is an hour out).
        overrun_at = (expected_out_s * config.FFMPEG_OVERRUN_FACTOR
                      + config.FFMPEG_OVERRUN_FLOOR_S)

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
                # Abandoned: the dispatcher gave up on this job and requeued
                # it, so every frame from here is being computed for nobody —
                # next to the retry that replaced us, on a second 8-vCPU
                # instance. Never let a diagnostic callback fail an encode.
                try:
                    if cancelled_cb and cancelled_cb():
                        kill_reason.append(
                            "job was cancelled or handed to another worker")
                        proc.kill()
                        return
                except Exception:
                    pass
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
                        # Check BEFORE clamping. The min(0.999, ...) below is
                        # what hid the watermark runaway for a full hour: it
                        # turned "this encode is 40x past the end of the video"
                        # into a serene 99.9% and threw the evidence away.
                        if secs > overrun_at:
                            kill_reason.append(
                                f"runaway encode: produced {secs:.0f}s of "
                                f"output for a {expected_out_s:.0f}s timeline "
                                f"— the filtergraph cannot terminate")
                            proc.kill()
                            break
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
        p = subprocess.run(cmd, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
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


def _proxy_vf(h, pad_s=0.0):
    vf = rf"scale=-2:min({h}\,floor(ih/2)*2)"
    if pad_s > 0:
        # Clone the last frame forward. After scale, so it clones a proxy frame.
        vf += f",tpad=stop_mode=clone:stop_duration={pad_s:.3f}"
    return vf


def _encode_proxy(src, dst, fps, vfr, has_audio, pad_s=0.0, progress_cb=None,
                  expected_out_s=None):
    # DO NOT SLICE THIS ENCODE ACROSS CORES. It was tried and MEASURED, on a
    # 90s 3840x2160 95Mbps file (the profile of the customer upload that spent
    # 386.8s of a 493s index right here):
    #
    #   serial                     37.3s
    #   8 slices, 1 thread each    53.0s
    #   8 slices, auto threads     53.1s
    #   4 slices, 2 threads each   50.9s
    #   2 slices, auto threads     53.5s
    #
    # Every slicing arrangement was SLOWER. The reason is the shape of the
    # cost, which the next two numbers pin exactly: decoding alone, with no
    # scale and no encode, is 34.9s of that 37.3s — 94% — and decoding with
    # `-threads 1` is 125.4s against 34.9s on auto. So the expensive stage is
    # the 4K decode, it already frame-threads across every core, and running N
    # ffmpegs simply makes them contend for cores one of them was already
    # using. Scaler choice is noise for the same reason (bicubic 37.1s,
    # fast_bilinear 43.9s, bilinear 48.7s, area 44.2s — the default is also
    # the fastest).
    #
    # The honest conclusion: there is no CPU-side headroom here. Making 4K
    # indexing dramatically faster means not decoding 4K on a CPU at all —
    # hardware decode, or a proxy produced by the client's own GPU before
    # upload — not a cleverer arrangement of this loop.
    cmd = ["ffmpeg", "-y", "-i", src, "-vf",
           _proxy_vf(config.PROXY_HEIGHT, pad_s),
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

# How far a BROWSER-BUILT proxy may sit from the duration the browser reported
# for the original before we refuse to index it.
#
# Deliberately not PROXY_SHORT_FRAC. That fraction is a warning threshold on an
# encode we performed ourselves and can vouch for; this is a refusal gate on a
# file produced by someone else's machine, and 2% of the 3-hour maximum is 3.6
# MINUTES of footage that would silently not be in the edit. The floor stays
# generous because container duration legitimately disagrees with frame
# duration by a frame or two of audio tail; the ceiling is what makes the gate
# mean the same thing for a 30-second clip and a 3-hour one.
CLIENT_PROXY_GAP_FRAC = 0.02
CLIENT_PROXY_GAP_MIN_S = 1.0
CLIENT_PROXY_GAP_MAX_S = 5.0


def client_proxy_gap_tolerance(declared_duration):
    """Seconds of disagreement allowed between a browser proxy and its source."""
    return max(CLIENT_PROXY_GAP_MIN_S,
               min(CLIENT_PROXY_GAP_FRAC * float(declared_duration or 0),
                   CLIENT_PROXY_GAP_MAX_S))


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


def adopt_client_proxy(src, dst, warnings=None):
    """Turn a browser-built proxy into one of OURS, as cheaply as it allows.

    The browser produced this with WebCodecs so the customer did not have to
    upload 4 GiB before editing, but it is not automatically the same artifact
    `make_proxy` produces, and two things downstream care:

      * MOOV POSITION. The browser writes with fastStart 'off' — every write is
        an append, so the sink never has to hold a whole hour-long file in
        order to patch a header. Ours is +faststart because the studio streams
        and seeks it constantly. A remux fixes that as a STREAM COPY: seconds
        even for an hour, no pixels touched.
      * CFR. Every downstream timestamp — shot boundaries, thumbnails, the
        preview render's own concat — assumes a constant rate, which is why
        make_proxy normalizes VFR sources. A VFR proxy is the one case worth
        paying for a re-encode, and it is cheap here precisely because the
        input is already 540p: the expensive half of a proxy encode is
        decoding the 4K source, and that has already happened on the client.

    Returns the probe of the adopted file.
    """
    info = probe(src)
    if not info["vfr"]:
        try:
            run(["ffmpeg", "-y", "-i", src, "-c", "copy",
                 "-movflags", "+faststart", dst])
            return probe(dst)
        except MediaError as e:
            # A remux should not fail on a file we can probe, but if it does,
            # re-encoding is always available and is still far cheaper than
            # having demanded the original.
            print(f"[media] client proxy remux failed ({e}) — re-encoding",
                  flush=True)
            if warnings is not None:
                warnings.append("the prepared video had to be re-encoded on "
                                "the server (its container could not be "
                                "remuxed)")
    else:
        print("[media] client proxy is variable-rate — normalizing to CFR",
              flush=True)
    _encode_proxy(src, dst, info["fps"], True, info["has_audio"],
                  expected_out_s=info["duration"])
    return probe(dst)


def extract_wav(src, dst):
    run(["ffmpeg", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", dst])


# Codecs an .m4a container carries unchanged, so the extraction is a remux
# (a second or two on a long clip) instead of a full decode+encode.
AUDIO_COPY_CODECS = ("aac", "alac")


def audio_stream_of(path):
    """The first audio stream's codec and channel count, or None when the file
    carries no sound at all.

    probe() only answers has_audio, and the copy-vs-re-encode decision needs
    the codec name. Separate ffprobe rather than another probe() field because
    this runs on files probe() would reject outright — an audio-only upload
    has no video stream."""
    out = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
               "-show_entries", "stream=codec_name,channels",
               "-print_format", "json", path], timeout=120)
    try:
        streams = json.loads(out).get("streams") or []
    except (TypeError, ValueError):
        return None
    if not streams:
        return None
    s = streams[0]
    try:
        ch = int(s.get("channels") or 0)
    except (TypeError, ValueError):
        ch = 0
    return {"codec": (s.get("codec_name") or "").lower(), "channels": ch}


def extract_audio_track(src, dst):
    """Write a video's audio to a standalone .m4a and return its duration.

    Raises MediaError("no audio stream") when the source is silent — the
    caller must say so rather than hand the user a file of silence.

    The picture is never decoded (-vn + -map 0:a:0), which is the whole point:
    this is how "use the song from this video, not its scene" is served. AAC
    (what phones and TikTok downloads carry) is stream-COPIED; anything else
    is encoded to AAC once, with the copy attempt retried as an encode because
    a copy can still fail on an exotic container even when the codec matches.
    """
    info = audio_stream_of(src)
    if not info:
        raise MediaError("no audio stream")
    head = ["ffmpeg", "-y", "-i", src, "-vn", "-sn", "-dn", "-map", "0:a:0"]
    tail = ["-movflags", "+faststart", dst]
    attempts = []
    if info["codec"] in AUDIO_COPY_CODECS:
        attempts.append(head + ["-c:a", "copy"] + tail)
    attempts.append(head + ["-c:a", "aac", "-b:a", "192k"] + tail)
    last_err = None
    for cmd in attempts:
        try:
            run(cmd, timeout=1800)
        except MediaError as e:
            last_err = str(e)
            continue
        if os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return probe_audio_duration(dst)
        last_err = "ffmpeg reported success but wrote no audio"
        try:
            os.unlink(dst)          # a 0-byte file would look like a real one
        except OSError:
            pass
    raise MediaError(f"could not extract audio from "
                     f"{os.path.basename(src)}: {last_err}")


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
                           errors="replace",
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
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
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


def frame_at(src, t, dst, width=None, quality=4, timeout=120):
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

    Three seek modes are tried before giving up. Input seek (-ss before -i) is
    the fast path. Output seek (-ss after -i) decodes from the start: slower,
    but it lands frames that input seek misses on files with sparse keyframes
    or edit lists (phone screen recordings are full of both). The last is the
    fully explicit form — pin the video stream, drop every other one, and name
    the muxer with -update 1. Without that the image2 muxer has to guess that a
    filename with no %d in it is a single still, and it logs that guess at
    ERROR level; builds that turn it into a real error write nothing at all.
    """
    vf = ["-vf", rf"scale={width}:-2"] if width else []
    ts = f"{max(0.0, t):.3f}"
    # -threads 1 for ONE frame. Frame threading allocates a full decode picture
    # buffer per thread, so a 4K source costs 618 MB resident to hand back a
    # single still — measured, and it is what OOM-killed the dispatcher when the
    # timeline started sampling users' own uploads (worker/filmstrip.py). One
    # frame has nothing to parallelise: single-threaded is 240 MB and, on the
    # input-seek path, actually FASTER (0.51s of CPU against 1.12s).
    th = ["-threads", "1"]
    attempts = (
        ["ffmpeg", "-y", *th, "-ss", ts, "-i", src,
         "-frames:v", "1", *vf, "-q:v", str(quality), dst],
        ["ffmpeg", "-y", *th, "-i", src, "-ss", ts,
         "-frames:v", "1", *vf, "-q:v", str(quality), dst],
        ["ffmpeg", "-y", *th, "-i", src, "-ss", ts, "-map", "0:v:0",
         "-an", "-sn", "-dn", "-frames:v", "1", *vf, "-q:v", str(quality),
         "-f", "image2", "-update", "1", dst],
    )
    last_err = None
    for cmd in attempts:
        try:
            run(cmd, timeout=timeout)
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
