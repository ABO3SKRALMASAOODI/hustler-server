#!/usr/bin/env python3
"""Measure a REFERENCE EDIT's structure — the deterministic half of grammar
extraction (round 82d).

The taste plan ("just edit it" -> genre grammar -> build -> score) starts
from exemplar edits people already consider perfect. This tool turns one
exemplar into NUMBERS: shot rhythm, cut-to-beat lock, motion, grade
fingerprint, energy arc. The perceptual half (what a human/vision model
reads off the frames: subject rotation, caption style, effect vocabulary)
is layered on top of these numbers, never instead of them — a model's "the
cuts feel on-beat" is replaced by a measured 78% within 100ms.

Reuses the worker's own runtime machinery (scenes.detect_shots,
perception.analyze_audio), so an exemplar is measured with exactly the same
instruments the agent will use on the user's footage at build time.

Usage:
  python3 tools/measure_reference.py VIDEO [--out DIR]

Writes <stem>.measure.json, <stem>.sheet.jpg (1 fps tiles, row-major: tile
k = second k) and <stem>.hook.jpg (4 fps tiles of the first 6 seconds) to
--out (default: alongside the video).
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import perception                                              # noqa: E402
import scenes                                                  # noqa: E402


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,r_frame_rate",
         "-of", "json", path], capture_output=True, check=True).stdout
    j = json.loads(out)
    dur = float(j["format"]["duration"])
    w = h = fps = None
    for s in j.get("streams", []):
        if s.get("codec_type") == "video":
            w, h = s.get("width"), s.get("height")
            try:
                num, den = s.get("r_frame_rate", "0/1").split("/")
                fps = float(num) / float(den)
            except (ValueError, ZeroDivisionError):
                fps = None
    return dur, w, h, fps


def signalstats(path):
    """One decode pass -> per-frame {t, yavg, ylow, yhigh, satavg, ydif}."""
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-vf", "signalstats,metadata=print:file=-",
           "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout.decode(
        "utf-8", "replace")
    frames, cur = [], None
    for line in out.splitlines():
        m = re.match(r"frame:\d+\s+pts:\S+\s+pts_time:([0-9.]+)", line)
        if m:
            cur = {"t": float(m.group(1))}
            frames.append(cur)
            continue
        m = re.match(r"lavfi\.signalstats\.(\w+)=([-0-9.]+)", line)
        if m and cur is not None:
            cur[m.group(1).lower()] = float(m.group(2))
    return frames


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return s[i]


def beat_lock(cut_times, beats):
    """How locked the cuts are to the musical grid — full beats and
    half-beats separately (cutting the offbeat is a style, not a miss), with
    the random-chance baseline so the number is honest."""
    if not beats or not cut_times:
        return None
    halves = sorted(beats + [(a + b) / 2.0
                             for a, b in zip(beats, beats[1:])])

    def stats(grid):
        offs = [min(abs(c - g) for g in grid) * 1000.0 for c in cut_times]
        return {
            "median_offset_ms": round(_pct(offs, 50), 1),
            "pct_within_50ms": round(
                100.0 * sum(1 for o in offs if o <= 50) / len(offs)),
            "pct_within_100ms": round(
                100.0 * sum(1 for o in offs if o <= 100) / len(offs)),
        }

    interval = (beats[-1] - beats[0]) / max(1, len(beats) - 1)
    return {
        "n_cuts": len(cut_times),
        "beat": stats(beats),
        "half_beat": stats(halves),
        # a random cut lands within ±100ms of a full beat this often — the
        # measured pct only means "synced" when it clears this comfortably
        "random_baseline_pct_100ms": round(
            min(1.0, 0.2 / interval) * 100) if interval else None,
    }


def measure(path, out_dir):
    dur, w, h, fps = probe(path)
    shots = scenes.detect_shots(path, dur)
    try:
        audio = perception.analyze_audio(path)
    except Exception as e:
        print(f"[measure] audio analysis failed: {e}", file=sys.stderr)
        audio = {}
    frames = signalstats(path)

    cut_times = [s.start for s in shots[1:]]
    lengths = [round(s.end - s.start, 3) for s in shots]

    # pacing curve: median shot length per quarter of the runtime
    quarters = [[] for _ in range(4)]
    for s in shots:
        q = min(3, int((s.start / dur) * 4))
        quarters[q].append(s.end - s.start)
    pacing = [round(_pct(q, 50), 2) if q else None for q in quarters]

    # per-shot mean motion (frame-to-frame luma difference)
    def shot_frames(s):
        return [f for f in frames if s.start <= f["t"] < s.end]

    motion_by_shot = []
    for s in shots:
        sf = [f.get("ydif", 0.0) for f in shot_frames(s)]
        motion_by_shot.append(round(sum(sf) / len(sf), 2) if sf else 0.0)

    # flash frames: extreme single-frame luma jumps AWAY from cut points —
    # the strobe/flash vocabulary of fan edits
    cutset = set()
    for c in cut_times:
        for f in frames:
            if abs(f["t"] - c) <= 1.5 / (fps or 24.0):
                cutset.add(f["t"])
    flashes = [round(f["t"], 2) for f in frames
               if f.get("ydif", 0) > 40 and f["t"] not in cutset]

    ydifs = [f.get("ydif", 0.0) for f in frames]
    yavgs = [f.get("yavg", 0.0) for f in frames]
    sats = [f.get("satavg", 0.0) for f in frames]
    contr = [f.get("yhigh", 0.0) - f.get("ylow", 0.0) for f in frames]

    # energy arc: biggest 0.5s-bin jump = the drop
    energy = audio.get("energy") or []
    drop_t = drop_db = None
    for i in range(1, len(energy)):
        d = energy[i] - energy[i - 1]
        if drop_db is None or d > drop_db:
            drop_db, drop_t = d, i * (audio.get("energy_bin_s") or 0.5)

    result = {
        "file": os.path.basename(path),
        "video": {"duration": round(dur, 2), "w": w, "h": h,
                  "fps": round(fps, 2) if fps else None,
                  "aspect": round(w / h, 3) if w and h else None},
        "shots": {
            "count": len(shots),
            "cuts_per_10s": round(len(cut_times) / dur * 10, 1),
            "lengths": lengths,
            "median_s": round(_pct(lengths, 50), 2),
            "p10_s": round(_pct(lengths, 10), 2),
            "p90_s": round(_pct(lengths, 90), 2),
            "pacing_by_quarter_s": pacing,
            "boundaries": [round(c, 2) for c in cut_times],
        },
        "music": {
            "bpm": audio.get("bpm"),
            "bpm_conf": audio.get("bpm_conf"),
            "n_beats": len(audio.get("beats") or []),
            "energy_bin_s": audio.get("energy_bin_s"),
            "energy_db": energy,
            "drop": {"t": round(drop_t, 1), "jump_db": round(drop_db, 1)}
            if drop_t is not None else None,
        },
        "beat_lock": beat_lock(cut_times, audio.get("beats") or []),
        "motion": {
            "mean_ydif": round(sum(ydifs) / len(ydifs), 2) if ydifs else None,
            "by_shot": motion_by_shot,
            "flash_frames": flashes[:40],
            "n_flash_frames": len(flashes),
        },
        "grade": {
            "brightness": round(sum(yavgs) / len(yavgs) / 255.0, 3)
            if yavgs else None,
            "contrast_spread": round(sum(contr) / len(contr) / 255.0, 3)
            if contr else None,
            "saturation": round(sum(sats) / len(sats) / 181.0, 3)
            if sats else None,
        },
    }

    stem = os.path.splitext(os.path.basename(path))[0]
    jpath = os.path.join(out_dir, f"{stem}.measure.json")
    with open(jpath, "w") as f:
        json.dump(result, f, indent=1)

    # contact sheets for HUMAN/vision-model eyes: tile k (row-major) = k sec
    sheet = os.path.join(out_dir, f"{stem}.sheet.jpg")
    rows = int(math.ceil(min(dur, 96) / 6))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path,
                    "-vf", f"fps=1,scale=240:-2,tile=6x{rows}",
                    "-frames:v", "1", sheet], check=True)
    hook = os.path.join(out_dir, f"{stem}.hook.jpg")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", "6", "-i", path,
                    "-vf", "fps=4,scale=240:-2,tile=6x4",
                    "-frames:v", "1", hook], check=True)
    return result, jpath, sheet, hook


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out_dir = args.out or os.path.dirname(os.path.abspath(args.video))
    os.makedirs(out_dir, exist_ok=True)
    result, jpath, sheet, hook = measure(args.video, out_dir)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("motion",)}, indent=1))
    print(f"\nwrote {jpath}\n      {sheet}\n      {hook}")


if __name__ == "__main__":
    main()
