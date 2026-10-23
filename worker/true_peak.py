"""The delivered file's true peak holds the ceiling — measured, not assumed.

Studio project 2592 (user 1970, 22 Oct 2026): a mastered 19-minute gaming
edit measured +0.33 dBTP on its draft preview (job 89245) and -0.7 dBTP on
the approval render of the same version (job 89154), although the master
chain (renderer.build_filtergraph) is loudnorm TP=-2 plus a hard limiter at
-2.5 dBFS. The chain holds on the lossless mix; the AAC encoder adds the
overshoot (dense, brick-limited sound and loud onsets reconstruct up to ~3-5
dB over the limited level, at 128k and 192k alike). audio_qc then told the
editor to call set_master_loudness, which was already on, so the finding was
one the turn could never fix.

So the shipped file itself is measured after the encode. When its true peak
is over CEILING_DBTP, its sound is re-encoded through a latency-compensated
limiter set lower by the measured excess (the picture is stream-copied, the
duration and A/V alignment are unchanged) and measured again, a few passes at
most. Only the peaks move: the integrated loudness stays within a few tenths
of a LU. Failure keeps the original file — a render never fails over its own
audio review.
"""

import math
import os
import re
import shutil
import subprocess

# The never-exceed ceiling on a delivered file (EBU R128 / streaming
# practice for lossy delivery: -1 dBTP), and the level a correction aims for
# so a second encode's own overshoot still lands under it.
CEILING_DBTP = -1.0
TARGET_DBTP = -1.5
# The renderer's own limiter (renderer.build_filtergraph alimiter limit=0.75).
RENDER_LIMIT_DBFS = 20.0 * math.log10(0.75)
MAX_PASSES = 3
# A limiter set lower than this is crushing the programme, not catching
# codec overs; the pass gives up and keeps the best file it has.
FLOOR_LIMIT_DBFS = -12.0
MEASURE_TIMEOUT_S = 300

_TP_RE = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[0-9.]+|-inf)\s*dBFS")


def measure(path, timeout=MEASURE_TIMEOUT_S):
    """True peak of the file's first audio stream in dBTP, or None when it
    has no audio or could not be measured. ebur128 peak=true: 4x
    oversampled, one audio-only decode (seconds for a 20-minute mix)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", path,
           "-map", "0:a:0", "-af", "ebur128=peak=true:framelog=quiet",
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:  # noqa: BLE001 — measurement only
        return None
    text = (proc.stderr or b"").decode("utf-8", "replace")
    found = _TP_RE.findall(text)
    if not found:
        return None
    value = found[-1]
    if value == "-inf":
        return -math.inf
    try:
        return float(value)
    except ValueError:
        return None


def limiter_chain(limit_dbfs):
    """The correction's sound chain: the limiter the renderer masters with
    (5 ms lookahead, latency-compensated so the picture stays in sync,
    level=0 so it never makes up gain), run at 192 kHz like the master."""
    limit = max(0.0625, min(1.0, 10.0 ** (float(limit_dbfs) / 20.0)))
    return ("aresample=192000,"
            f"alimiter=limit={limit:.5f}:attack=5:release=50:level=0:"
            "latency=1,"
            "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo")


def next_limit(limit_dbfs, measured_dbtp, target=TARGET_DBTP):
    """The limiter level for the next pass: lower by the measured excess
    over the target (the codec's overshoot rides on the limited level)."""
    return float(limit_dbfs) - max(0.25, float(measured_dbtp) - target)


def _reencode(src, dst, limit_dbfs, bitrate, timeout):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-nostdin",
           "-i", src, "-map", "0:v?", "-map", "0:a:0",
           "-c:v", "copy", "-af", limiter_chain(limit_dbfs),
           "-c:a", "aac", "-b:a", bitrate,
           "-movflags", "+faststart", dst]
    subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)


def _duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, timeout=60)
        return float((out.stdout or b"").decode().strip())
    except Exception:  # noqa: BLE001
        return None


def guard(path, bitrate="192k", workdir=None, ceiling=CEILING_DBTP,
          target=TARGET_DBTP, max_passes=MAX_PASSES, timeout=1800,
          measured=None):
    """Hold ``path``'s true peak at or under ``ceiling`` in place.

    Returns {"tp_in", "tp", "passes", "limit_dbfs", "held"} — ``held`` False
    only when the file is still over the ceiling after every pass (it then
    keeps the best measured file) — or None when the file has no audio or
    could not be measured. ``measured`` skips the first measurement when
    the caller already holds it.
    """
    tp0 = measure(path) if measured is None else measured
    if tp0 is None:
        return None
    report = {"tp_in": _round(tp0), "tp": _round(tp0), "passes": 0,
              "limit_dbfs": None, "held": bool(tp0 <= ceiling)}
    if tp0 <= ceiling:
        return report
    workdir = workdir or os.path.dirname(os.path.abspath(path))
    base_dur = _duration(path)
    best_path, best_tp = None, tp0
    limit = RENDER_LIMIT_DBFS
    tp = tp0
    try:
        for n in range(1, max_passes + 1):
            limit = next_limit(limit, tp, target)
            if limit < FLOOR_LIMIT_DBFS:
                break
            out = os.path.join(workdir, f"tp_guard_{n}.mp4")
            _reencode(path, out, limit, bitrate, timeout)
            dur = _duration(out)
            if base_dur and dur and abs(dur - base_dur) > 0.1:
                # A correction must never change what plays; drop it.
                os.remove(out)
                break
            tp = measure(out)
            if tp is None:
                os.remove(out)
                break
            report["passes"] = n
            if tp < best_tp:
                if best_path:
                    os.remove(best_path)
                best_path, best_tp = out, tp
                report["limit_dbfs"] = round(limit, 2)
            else:
                os.remove(out)
            if best_tp <= ceiling:
                break
    except Exception as exc:  # noqa: BLE001 — never fail the render
        report["error"] = str(exc)[:160]
    if best_path:
        shutil.move(best_path, path)
        report["tp"] = _round(best_tp)
    report["held"] = bool(report["tp"] is not None
                          and report["tp"] <= ceiling)
    return report


def _round(value):
    if value is None:
        return None
    if not math.isfinite(value):
        return -100.0
    return round(float(value), 2)
