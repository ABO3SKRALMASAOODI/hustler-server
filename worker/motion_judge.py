"""Bounded visual-motion evidence for downloaded video candidates.

Still frames answer *what* a clip depicts, but they cannot answer whether it
is a frozen still, a gentle camera move, chaotic handheld footage, or a rapid
montage.  This module measures that missing temporal evidence from a tiny
grayscale proxy.  It is intentionally descriptive rather than aesthetic:
the director decides whether calm or energetic motion suits the story.

The decoder emits at most ``MAX_SAMPLES`` 160x90 frames and inspects at most
``MAX_WINDOW_S`` seconds. For a long source that budget is distributed across
the whole runtime instead of silently describing only its opening. A 4K
source therefore never becomes a 4K numpy array, and the result is small
enough to persist in asset metadata.
"""

import json
import math
import subprocess

import numpy as np


MOTION_PROFILE_VERSION = 2
FRAME_W = 160
FRAME_H = 90
MAX_SAMPLES = 120
MAX_WINDOW_S = 30.0
MAX_SECTIONS = 6


class MotionProfileError(RuntimeError):
    pass


def _percentile(values, pct):
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float32), pct))


def _intensity(mean_motion, p90_motion, freeze_share):
    if freeze_share >= 0.82 or (mean_motion < 1.15 and p90_motion < 2.0):
        return "frozen_or_nearly_static"
    if mean_motion < 3.2 and p90_motion < 6.0:
        return "gentle"
    if mean_motion < 7.5 and p90_motion < 14.0:
        return "moderate"
    return "high"


def _probe_duration(path):
    try:
        raw = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path], capture_output=True, check=True,
            timeout=20).stdout
        return float((json.loads(raw).get("format") or {}).get("duration") or 0)
    except Exception:
        return 0.0


def profile_frames(frames, sample_fps, analyzed_window_s=None):
    """Turn a sequence of equally spaced grayscale frames into evidence.

    Kept separate from ffmpeg so threshold behavior can be verified with
    synthetic frames.  ``frames`` may be a numpy array or a list of images.
    """
    arr = np.asarray(frames, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[0] < 2:
        raise MotionProfileError("fewer than two decodable video samples")
    fps = max(0.01, float(sample_fps))
    f32 = arr.astype(np.float32)
    brightness = f32.mean(axis=(1, 2))
    contrast = f32.std(axis=(1, 2))
    diffs = np.abs(np.diff(f32, axis=0)).mean(axis=(1, 2))

    # Abrupt changes are large local discontinuities.  The robust floor stops
    # sensor noise or a slow pan from being mislabeled as an edit; spacing
    # collapses a flash/fade spanning neighboring sparse samples to one event.
    median = float(np.median(diffs))
    mad = float(np.median(np.abs(diffs - median)))
    cut_floor = max(16.0, median + 7.0 * max(mad, 0.35))
    candidates = []
    for i, value in enumerate(diffs):
        left = float(diffs[i - 1]) if i else -1.0
        right = float(diffs[i + 1]) if i + 1 < len(diffs) else -1.0
        if float(value) >= cut_floor and value >= left and value >= right:
            candidates.append((i + 1) / fps)
    changes = []
    for at in candidates:
        if not changes or at - changes[-1] >= max(0.45, 1.5 / fps):
            changes.append(at)

    # Within-shot motion excludes likely edits: a cut is not camera/object
    # movement and should not make a calm montage look like shaky footage.
    within = [float(v) for v in diffs if float(v) < cut_floor]
    if not within:
        within = [float(v) for v in diffs]
    mean_motion = float(np.mean(within)) if within else 0.0
    p90_motion = _percentile(within, 90)
    freeze_share = float(np.mean(diffs < 0.8))
    static_share = float(np.mean(diffs < 2.0))
    blank_share = float(np.mean((brightness < 12.0) & (contrast < 7.0)))

    intensity = _intensity(mean_motion, p90_motion, freeze_share)

    window = (float(analyzed_window_s) if analyzed_window_s is not None
              else (arr.shape[0] - 1) / fps)
    return {
        "version": MOTION_PROFILE_VERSION,
        "analyzed_window_s": round(max(0.0, window), 2),
        "sample_fps": round(fps, 3),
        "samples": int(arr.shape[0]),
        "intensity": intensity,
        "mean_frame_change": round(mean_motion, 2),
        "p90_frame_change": round(p90_motion, 2),
        "freeze_share": round(freeze_share, 3),
        "static_share": round(static_share, 3),
        "blank_share": round(blank_share, 3),
        "abrupt_changes": len(changes),
        "abrupt_changes_per_10s": round(
            len(changes) / max(window, 0.01) * 10.0, 2),
        "change_times_s": [round(x, 2) for x in changes[:24]],
    }


def plan_windows(duration_s, budget_s=MAX_WINDOW_S,
                 max_sections=MAX_SECTIONS):
    """Return bounded, evenly distributed ``(start, duration)`` windows.

    Short media keeps the original one-pass behavior. Long media spends the
    exact same decode-time budget in independent windows spanning opening to
    ending, so a late demo/scene/montage cannot be represented by minute one.
    """
    duration = max(0.0, float(duration_s or 0.0))
    budget = max(0.5, float(budget_s or MAX_WINDOW_S))
    if duration <= budget:
        return [(0.0, max(0.5, duration))]
    count = min(MAX_SECTIONS, max(2, int(max_sections or MAX_SECTIONS)))
    window = min(duration, budget) / count
    travel = max(0.0, duration - window)
    return [(round(i * travel / (count - 1), 3), round(window, 3))
            for i in range(count)]


def combine_profiles(sections, source_duration_s):
    """Aggregate independently profiled windows without cross-window cuts."""
    rows = [dict(row) for row in sections or [] if row]
    if not rows:
        raise MotionProfileError("no motion windows were decoded")
    weights = [max(1, int(row.get("samples") or 1) - 1) for row in rows]
    total_weight = float(sum(weights))

    def weighted(key):
        return sum(float(row.get(key) or 0.0) * weight
                   for row, weight in zip(rows, weights)) / total_weight

    mean_motion = weighted("mean_frame_change")
    p90_motion = weighted("p90_frame_change")
    freeze_share = weighted("freeze_share")
    static_share = weighted("static_share")
    blank_share = weighted("blank_share")
    sampled_s = sum(float(row.get("analyzed_window_s") or 0.0)
                    for row in rows)
    changes = sorted(float(value)
                     for row in rows
                     for value in row.get("change_times_s") or [])
    abrupt = sum(int(row.get("abrupt_changes") or 0) for row in rows)
    compact_sections = []
    for row in rows:
        compact_sections.append({key: row.get(key) for key in (
            "start_s", "end_s", "analyzed_window_s", "sample_fps",
            "samples", "intensity", "mean_frame_change",
            "p90_frame_change", "freeze_share", "static_share",
            "blank_share", "abrupt_changes", "change_times_s")})
    return {
        "version": MOTION_PROFILE_VERSION,
        "sampling_mode": ("continuous" if len(rows) == 1 else
                          "distributed_windows"),
        "source_coverage_s": round(max(0.0, float(source_duration_s or 0)), 2),
        "analyzed_window_s": round(sampled_s, 2),
        "sample_fps": round(sum(float(row.get("sample_fps") or 0.0)
                                for row in rows) / len(rows), 3),
        "samples": sum(int(row.get("samples") or 0) for row in rows),
        "intensity": _intensity(mean_motion, p90_motion, freeze_share),
        "mean_frame_change": round(mean_motion, 2),
        "p90_frame_change": round(p90_motion, 2),
        "freeze_share": round(freeze_share, 3),
        "static_share": round(static_share, 3),
        "blank_share": round(blank_share, 3),
        "abrupt_changes": abrupt,
        "abrupt_changes_per_10s": round(
            abrupt / max(sampled_s, 0.01) * 10.0, 2),
        "change_times_s": [round(value, 2) for value in changes[:48]],
        "sections": compact_sections,
    }


def _decode_window(path, start_s, window_s, sample_fps, max_samples):
    expected_max = int(math.ceil(window_s * sample_fps)) + 2
    cmd = ["ffmpeg", "-v", "error"]
    if start_s > 0:
        # Input seek prevents ffmpeg decoding every frame before a late
        # window. Exact frame accuracy is irrelevant at this evidence scale.
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += [
        "-t", f"{window_s:.3f}", "-i", path, "-an", "-vf",
        (f"fps={sample_fps:.6f},"
         f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease,"
         f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2,format=gray"),
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True,
                              timeout=max(30.0, window_s * 2.0 + 15.0))
    except (subprocess.SubprocessError, OSError) as exc:
        raise MotionProfileError(f"motion decode failed ({str(exc)[:160]})")
    frame_bytes = FRAME_W * FRAME_H
    n = min(len(proc.stdout) // frame_bytes, expected_max, int(max_samples))
    if n < 2:
        raise MotionProfileError("fewer than two decodable video samples")
    arr = np.frombuffer(proc.stdout[:n * frame_bytes], dtype=np.uint8)
    return arr.reshape(n, FRAME_H, FRAME_W)


def analyze_video(path, duration_s=None, max_window_s=MAX_WINDOW_S,
                  max_samples=MAX_SAMPLES):
    """Measure temporal behavior with a bounded whole-program sparse decode."""
    try:
        duration = float(duration_s or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        duration = _probe_duration(path)
    if duration <= 0:
        duration = float(max_window_s)
    sample_budget = max(2, int(max_samples))
    if duration > float(max_window_s) and sample_budget < 4:
        raise MotionProfileError(
            "a long source needs at least four samples across two windows")
    section_cap = min(MAX_SECTIONS, max(2, sample_budget // 2))
    windows = plan_windows(duration, max_window_s, section_cap)
    per_window = max(2, sample_budget // len(windows))
    sections = []
    for start, window in windows:
        sample_fps = min(4.0, max(1.0, per_window / max(window, 0.01)))
        arr = _decode_window(path, start, window, sample_fps, per_window)
        actual_window = min(window, (arr.shape[0] - 1) / sample_fps)
        row = profile_frames(arr, sample_fps, actual_window)
        row["start_s"] = round(start, 2)
        row["end_s"] = round(min(duration, start + actual_window), 2)
        row["change_times_s"] = [round(start + value, 2)
                                 for value in row.get("change_times_s") or []]
        sections.append(row)
    return combine_profiles(sections, duration)


def get_or_compute_for_index(worker_db, dbx, index_row, media_path):
    """Read current whole-program motion evidence or upgrade it lazily.

    Version 1 measured only the opening 30 seconds. A targeted JSON merge
    upgrades existing projects without a fleet-wide transcript/filmstrip
    re-index and without risking a concurrent index overwrite.
    """
    idx = index_row.get("json") or {}
    current = idx.get("motion") or {}
    if current.get("version") == MOTION_PROFILE_VERSION:
        return current
    duration = (idx.get("video") or {}).get("duration")
    result = analyze_video(media_path, duration_s=duration)
    try:
        worker_db.run(dbx.set_index_motion, index_row["video_sha256"], result,
                      index_row.get("pipeline_version"))
    except Exception as exc:
        print(f"[motion] sidecar persist failed (non-fatal): {exc}",
              flush=True)
    return result


def describe(profile, source_duration_s=None):
    """Compact, explicit evidence for an editor/LLM; never claims playback."""
    if not profile:
        return ""
    try:
        intensity = str(profile.get("intensity") or "unknown").replace("_", " ")
        freeze = 100.0 * float(profile.get("freeze_share") or 0.0)
        blank = 100.0 * float(profile.get("blank_share") or 0.0)
        changes = int(profile.get("abrupt_changes") or 0)
        window = float(profile.get("analyzed_window_s") or 0.0)
    except (TypeError, ValueError):
        return ""
    extra = f", {blank:.0f}% blank/black" if blank >= 1.0 else ""
    mode = profile.get("sampling_mode")
    coverage = float(profile.get("source_coverage_s") or window)
    scope = (f"{window:g}s across {len(profile.get('sections') or [])} "
             f"distributed windows over {coverage:g}s"
             if mode == "distributed_windows" else f"{window:g}s")
    if not mode and source_duration_s and float(source_duration_s) > window:
        scope += (f" from the opening only of {float(source_duration_s):g}s; "
                  "later motion is not measured in this legacy index")
    return (f"MEASURED MOTION ({scope} sparse proxy; not continuous "
            f"playback): {intensity}, {freeze:.0f}% near-duplicate sample "
            f"intervals, {changes} abrupt visual change(s){extra}.")
