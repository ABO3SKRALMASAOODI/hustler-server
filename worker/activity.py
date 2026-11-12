"""Where something is happening in a long take: a measured activity track.

P0 #60 (9-10 Nov 2026): a Plus customer uploaded a 45-minute volleyball match
(one locked-off camera) and asked five times to "cut the space between
serves". Every request ended in a question with no cut: the agent's only
picture search sampled one frame every ~19 s, nothing was transcribed, and it
asked the customer for timestamps. An editor would scrub the take and see the
rallies; the product has to measure them.

This module measures, it does not interpret: per sample (ACTIVITY_FPS per
second of source) the share of the picture that changed since the previous
sample (motion), and the strength of sharp sound transients over the room's
own floor (a ball hit, a whistle, a shout; audio onsets). The two are scaled
against the take's own distribution and combined into one score; the
segmenter then finds the stretches where the score stays high (active) and
where it stays low (idle), with hysteresis and minimum lengths so a single
twitch neither starts nor breaks a stretch. What the stretches MEAN (a rally,
a set change) is the agent's to judge from the picture; the tool only says
where activity rises and falls, and how strongly.
"""
import math
import os
import subprocess
import threading
import time

import numpy as np

ACTIVITY_VERSION = 1
ACTIVITY_FPS = 4.0          # samples per source second
FRAME_W, FRAME_H = 96, 54   # analysis raster (grey); fine for whole-body motion
PIXEL_DELTA = 10            # a pixel "changed" when it moved this many levels
AUDIO_RATE = 8000
AUDIO_HOP = 80              # 10 ms energy frames
SMOOTH_S = 1.5              # the score is averaged over this window
LOCAL_S = 20.0              # the local midline: the rhythm of plays and pauses
FLOOR_SHARE = 0.6           # below this share of the take's split is idle anyway
SOUND_AGREES = 0.15         # sound joins only when it follows the picture
MEASURE_BUDGET_S = 900.0    # a 45-min proxy decodes in a few minutes


def _ffmpeg():
    return os.environ.get("FFMPEG_BIN") or "ffmpeg"


def _motion(path, fps, budget_s, cancel=None, progress=None):
    """Per-sample share of changed pixels (index i = time i/fps)."""
    cmd = [_ffmpeg(), "-v", "error", "-nostdin", "-threads", "0", "-i", path,
           "-map", "0:v:0", "-an", "-sn", "-dn",
           "-vf", (f"fps={fps:g},scale={FRAME_W}:{FRAME_H}:flags=area,"
                   "format=gray"),
           "-f", "rawvideo", "pipe:1"]
    size = FRAME_W * FRAME_H
    out = []
    prev = None
    started = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=size * 64)
    try:
        while True:
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).astype(np.int16)
            if prev is None:
                out.append(0.0)
            else:
                out.append(float(np.count_nonzero(
                    np.abs(frame - prev) >= PIXEL_DELTA)) / size)
            prev = frame
            if len(out) % 400 == 0:
                if progress is not None:
                    progress(len(out) / fps)
                if cancel is not None and cancel.is_set():
                    raise TimeoutError("cancelled")
                if time.monotonic() - started > budget_s:
                    raise TimeoutError(
                        f"motion pass over {budget_s:.0f}s budget at "
                        f"{len(out) / fps:.0f}s of source")
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        proc.wait()
    if len(out) >= 2:
        out[0] = out[1]
    return np.asarray(out, dtype=np.float32)


def _audio(path, fps, budget_s):
    """Per-sample onset strength: how far the loudest 10 ms frame in the
    sample rises over the median energy of the surrounding 2 s, in dB.
    None when the file has no audio stream."""
    cmd = [_ffmpeg(), "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0",
           "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(AUDIO_RATE),
           "-af", "highpass=f=300", "-f", "s16le", "pipe:1"]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=budget_s)
    except subprocess.TimeoutExpired:
        return None
    raw = np.frombuffer(res.stdout, dtype=np.int16)
    if raw.size < AUDIO_RATE:
        return None
    n = raw.size // AUDIO_HOP
    frames = raw[:n * AUDIO_HOP].astype(np.float32).reshape(n, AUDIO_HOP)
    energy = 10.0 * np.log10(np.mean(frames * frames, axis=1) + 1.0)
    if float(np.max(energy) - np.min(energy)) < 3.0:
        return None  # a flat or silent track carries no events
    per_s = AUDIO_RATE / AUDIO_HOP           # 100 energy frames a second
    # the room's floor: a running median over 2 s, from a strided sample
    win = int(per_s * 2)
    stride = 10
    coarse = energy[::stride]
    k = max(1, win // stride)
    pad = np.pad(coarse, (k // 2, k - k // 2 - 1), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(pad, k)
    floor = np.repeat(np.median(windows, axis=1), stride)[:n]
    rise = np.clip(energy - floor, 0.0, None)
    hop = per_s / fps
    m = int(math.floor(n / hop))
    out = np.zeros(m, dtype=np.float32)
    for i in range(m):
        a, b = int(i * hop), int((i + 1) * hop)
        if b > a:
            out[i] = float(np.max(rise[a:b]))
    return out


def measure(path, fps=ACTIVITY_FPS, budget_s=MEASURE_BUDGET_S, cancel=None,
            progress=None):
    """The raw track of one file: {"version", "fps", "motion", "audio",
    "duration", "elapsed_s"}; motion and audio are lists of floats (audio
    None when there is no usable sound)."""
    started = time.monotonic()
    holder = {}

    def _aud():
        try:
            holder["audio"] = _audio(path, fps, budget_s)
        except Exception as exc:  # noqa: BLE001
            holder["audio"] = None
            holder["audio_error"] = f"{type(exc).__name__}: {exc}"[:160]

    th = threading.Thread(target=_aud, daemon=True, name="activity-audio")
    th.start()
    motion = _motion(path, fps, budget_s, cancel=cancel, progress=progress)
    th.join(max(1.0, budget_s - (time.monotonic() - started)))
    audio = holder.get("audio")
    if audio is not None:
        if audio.size < motion.size:
            audio = np.pad(audio, (0, motion.size - audio.size))
        audio = audio[:motion.size]
    return {"version": ACTIVITY_VERSION, "fps": float(fps),
            "motion": [round(float(v), 5) for v in motion],
            "audio": (None if audio is None
                      else [round(float(v), 2) for v in audio]),
            "duration": round(motion.size / float(fps), 2),
            "elapsed_s": round(time.monotonic() - started, 2)}


def _smooth(x, n):
    if n <= 1 or x.size == 0:
        return x
    k = np.ones(n, dtype=np.float32) / n
    pad = np.pad(x, (n // 2, n - n // 2 - 1), mode="edge")
    return np.convolve(pad, k, mode="valid")


def _scaled(x):
    """0 at the take's quiet level (10th percentile), 1 at its busy level
    (90th): the score is relative to this footage, never a fixed number."""
    lo, hi = np.percentile(x, 10), np.percentile(x, 90)
    if hi - lo < 1e-6:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.5)


def _otsu(x, bins=64):
    """The split between the two populations of the score (low/high)."""
    hist, edges = np.histogram(x, bins=bins)
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return float(np.median(x))
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = total - w0
    m0 = np.cumsum(hist * mids)
    mu0 = m0 / np.maximum(w0, 1e-9)
    mu1 = (m0[-1] - m0) / np.maximum(w1, 1e-9)
    between = w0 * w1 * (mu0 - mu1) ** 2
    return float(mids[int(np.argmax(between))])


def score(track):
    """(score array in 0..1.5, fps, components used). Sound joins the
    picture only when the two agree (their correlation over the take is at
    least SOUND_AGREES): in a busy gym the sound is everyone's, not this
    court's, and it would only blur the picture's rhythm (P0 #60: -0.03)."""
    fps = float(track["fps"])
    motion = np.asarray(track.get("motion") or [], dtype=np.float32)
    n = max(1, int(round(SMOOTH_S * fps)))
    pic = _smooth(_scaled(motion), n)
    used = ["motion"]
    audio = track.get("audio")
    if audio and pic.size > 8:
        a = np.asarray(audio, dtype=np.float32)[:motion.size]
        if a.size == motion.size and float(np.percentile(a, 90)) > 3.0:
            snd = _smooth(_scaled(a), n)
            if np.std(snd) > 1e-6 and np.std(pic) > 1e-6 and \
                    float(np.corrcoef(pic, snd)[0, 1]) >= SOUND_AGREES:
                used.append("sound")
                return 0.65 * pic + 0.35 * snd, fps, used
    return pic, fps, used


def segment(track, start=0.0, end=None, min_active_s=2.0, min_idle_s=1.5,
            sensitivity=0.5):
    """Active and idle stretches of a measured track, in SOURCE seconds.

    sensitivity 0..1 moves the split between quiet and busy (0.5 = the
    measured split of this take; higher finds more, quieter activity).
    Returns {"active": [[a, b, peak, mean]], "idle": [[a, b, mean]],
    "threshold", "basis", "fps"}."""
    s, fps, used = score(track)
    if s.size == 0:
        return {"active": [], "idle": [], "threshold": None, "basis": used,
                "fps": fps}
    lo_i = max(0, int(math.floor(float(start or 0.0) * fps)))
    hi_i = s.size if end is None else min(s.size,
                                           int(math.ceil(float(end) * fps)))
    split = _otsu(s[lo_i:hi_i] if hi_i > lo_i else s)
    sens = min(1.0, max(0.0, float(sensitivity)))
    # A play rises over the take's LOCAL midline (the pauses between serves
    # are short dips inside a busy stretch, which one global split merges),
    # and never counts below a floor share of the global split (a long
    # break stays idle however its own noise wobbles).
    local = _smooth(s, max(1, int(round(LOCAL_S * fps))))
    gain = 1.0 + (0.5 - sens) * 0.4     # 0.5 -> the midline itself
    thr = np.maximum(local * gain, split * FLOOR_SHARE * (1.5 - sens))
    hi_t, lo_t = thr * 1.05, thr * 0.95  # hysteresis
    on = np.zeros(s.size, dtype=bool)
    state = False
    for i in range(lo_i, hi_i):
        if state:
            state = s[i] >= lo_t[i]
        else:
            state = s[i] >= hi_t[i]
        on[i] = state
    runs = []
    i = lo_i
    while i < hi_i:
        j = i
        while j < hi_i and on[j] == on[i]:
            j += 1
        runs.append([bool(on[i]), i, j])
        i = j
    # an idle gap shorter than min_idle_s inside activity is part of it
    min_idle, min_act = min_idle_s * fps, min_active_s * fps
    changed = True
    while changed:
        changed = False
        for k in range(1, len(runs) - 1):
            st, a, b = runs[k]
            if not st and (b - a) < min_idle and runs[k - 1][0] \
                    and runs[k + 1][0]:
                runs[k - 1:k + 2] = [[True, runs[k - 1][1], runs[k + 1][2]]]
                changed = True
                break
    runs = [[st and (b - a) >= min_act, a, b] for st, a, b in runs]
    merged = []
    for st, a, b in runs:
        if merged and merged[-1][0] == st:
            merged[-1][2] = b
        else:
            merged.append([st, a, b])
    active, idle = [], []
    for st, a, b in merged:
        seg = s[a:b]
        t0, t1 = round(a / fps, 2), round(b / fps, 2)
        if st:
            active.append([t0, t1, round(float(seg.max()), 2),
                           round(float(seg.mean()), 2)])
        else:
            idle.append([t0, t1, round(float(seg.mean()), 2)])
    return {"active": active, "idle": idle,
            "threshold": round(float(split * FLOOR_SHARE * (1.5 - sens)), 3),
            "basis": used, "fps": fps}


if __name__ == "__main__":  # measure one file: python activity.py <path> <out.json>
    import json
    import sys
    tr = measure(sys.argv[1], progress=lambda t: print(f"{t:.0f}s", flush=True))
    with open(sys.argv[2], "w") as fh:
        json.dump(tr, fh)
    seg = segment(tr)
    print(json.dumps({"elapsed_s": tr["elapsed_s"], "duration": tr["duration"],
                      "active": len(seg["active"]), "idle": len(seg["idle"]),
                      "threshold": seg["threshold"], "basis": seg["basis"]}))
