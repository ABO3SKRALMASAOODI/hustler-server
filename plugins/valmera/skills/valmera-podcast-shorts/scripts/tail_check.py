#!/usr/bin/env python3
"""Flag finals whose programme holds a still picture right before the end card.

The owner (22 Oct 2026): a frozen or slowed last moment before the end card
reads as the video stalling. The payoff holds 0.6-1.2 s on LIVE footage (a
reaction, a nod, a breath), or the short ends on the last word.

Valmera's end card is the last 5.0 s of a final; the join dissolves into it
over the 0.3 s before that. This reads the 2.5 s of programme before the card
and reports the longest run of frames whose mean luma change from the
previous frame is under STILL_YDIF (a held frame re-encoded measures about
0.0; a talking head that sits still is 0.1-0.4). A run of FLAG_S or more is
HELD: that final stalls and must not be delivered (run.py deliver refuses it).

ffmpeg/ffprobe: PATH first, then $VALMERA_FFMPEG_BIN (the ffmpeg binary or
its folder), then the owner's static arm64 build.

usage: tail_check.py [--json] FILE_OR_DIR ...
exit 0 when every file is ok, 1 when any is HELD, 2 when one cannot be read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

BIN_ARM = "/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm"
CARD_S = 5.0
LOOK_S = 2.5
STILL_YDIF = 0.05
FLAG_S = 0.4


class TailCheckError(RuntimeError):
    """The file could not be measured (no ffmpeg, or not a readable video)."""


def _beside(path: str, name: str) -> str | None:
    other = Path(path).with_name(name)
    return str(other) if other.is_file() and os.access(other, os.X_OK) else None


def find_tools() -> tuple[str, str]:
    """(ffmpeg, ffprobe): PATH, then $VALMERA_FFMPEG_BIN, then BIN_ARM."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        ffprobe = shutil.which("ffprobe") or _beside(ffmpeg, "ffprobe")
        if ffprobe:
            return ffmpeg, ffprobe
    env = os.environ.get("VALMERA_FFMPEG_BIN", "").strip()
    candidates = []
    if env:
        p = Path(env).expanduser()
        candidates.append(p / "ffmpeg" if p.is_dir() else p)
    candidates.append(Path(BIN_ARM) / "ffmpeg")
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            ffprobe = _beside(str(c), "ffprobe") or shutil.which("ffprobe")
            if ffprobe:
                return str(c), ffprobe
    raise TailCheckError("ffmpeg and ffprobe not found: put them on PATH, set "
                         "VALMERA_FFMPEG_BIN to the ffmpeg binary or its folder, "
                         f"or install the static build at {BIN_ARM}")


def probe(path: str, ffprobe: str) -> tuple[float, float]:
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=avg_frame_rate:format=duration",
                          "-of", "json", path], capture_output=True, text=True)
    try:
        d = json.loads(out.stdout)
        num, den = d["streams"][0]["avg_frame_rate"].split("/")
        fps = float(num) / float(den or 1)
        dur = float(d["format"]["duration"])
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        raise TailCheckError(f"{os.path.basename(path)} is not a readable video "
                             f"({(out.stderr or '').strip()[:200] or 'no video stream'})")
    if fps <= 0 or dur <= 0:
        raise TailCheckError(f"{os.path.basename(path)} has no measurable video")
    return dur, fps


def check(path: str, card_s: float = CARD_S) -> dict:
    """{"file", "duration", "held_s", "held_ends_before_card_s", "flag"}.
    Raises TailCheckError when the file cannot be measured."""
    ffmpeg, ffprobe = find_tools()
    dur, fps = probe(path, ffprobe)
    start = max(0.0, dur - card_s - LOOK_S)
    span = dur - card_s - start
    if span <= 0:
        raise TailCheckError(f"{os.path.basename(path)} is shorter than its end card")
    err = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-ss", f"{start:.3f}",
                          "-t", f"{span:.3f}", "-i", path, "-vf",
                          "scale=270:480,signalstats,metadata=mode=print:key=lavfi.signalstats.YDIF",
                          "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    ydif = [float(v) for v in re.findall(r"YDIF=([0-9.]+)", err)][1:]  # first frame has no previous
    if not ydif:
        raise TailCheckError(f"{os.path.basename(path)}: no frames decoded before the card")
    best = run = 0
    best_end = None
    for i, v in enumerate(ydif):
        run = run + 1 if v < STILL_YDIF else 0
        if run > best:
            best, best_end = run, i
    held = best / fps
    end_at = start + (best_end + 2) / fps if best_end is not None else None
    return {"file": os.path.basename(path), "duration": round(dur, 2),
            "held_s": round(held, 2),
            "held_ends_before_card_s": (round(dur - card_s - end_at, 2)
                                        if end_at is not None else None),
            "flag": held >= FLAG_S}


def line(r: dict) -> str:
    if r.get("error"):
        return f"ERR  {r['file']}: {r['error']}"
    mark = "HELD" if r["flag"] else "ok  "
    where = (f", ends {r['held_ends_before_card_s']}s before card"
             if r["held_ends_before_card_s"] is not None else "")
    return f"{mark} {r['held_s']:4.2f}s held{where}  {r['file']}"


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    paths: list[str] = []
    for a in (x for x in argv if x != "--json"):
        if os.path.isdir(a):
            paths += sorted(os.path.join(a, f) for f in os.listdir(a) if f.endswith(".mp4"))
        else:
            paths.append(a)
    if not paths:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 2
    rows = []
    for p in paths:
        try:
            rows.append(check(p))
        except TailCheckError as exc:
            rows.append({"file": os.path.basename(p), "error": str(exc), "flag": None})
    if as_json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(line(r))
    if any(r.get("error") for r in rows):
        return 2
    return 1 if any(r["flag"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
