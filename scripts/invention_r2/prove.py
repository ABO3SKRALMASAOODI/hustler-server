"""Render a study from a frame-aligned clean take and local Valmera sound libraries.

PYTHONPATH=worker python scripts/invention_r2/prove.py ID TAKE WORK OUTPUT MODELS
FFMPEG_BIN and FFPROBE_BIN select the local binaries. cuts in cues.json give
source start and duration in seconds, concatenated at 24 fps with 48 kHz sound.
"""

import argparse
import importlib
import json
import os
from pathlib import Path

import cv2
import numpy as np


def score(audio, plan, cue, event, models, work, output):
    sr, fps = audio.SR, plan.fps
    n = round(plan.dur / fps * sr)
    library = models / "music_ref/tracks" / (cue["music"] + ".mp3")
    song = audio.pcm(str(library), cue["song_at"] + n / sr + 3)
    # Find a harmonic attack near the chosen bar, put that attack on the picture.
    mark = round(cue["song_at"] * sr)
    hop = sr // 100
    region = song[mark - 30 * hop : mark + 30 * hop].reshape(60, hop, 2)
    power = np.log(np.mean(region**2, axis=(1, 2)) + 1e-9)
    attack = mark + (int(np.argmax(np.diff(power))) + 1 - 30) * hop
    at = round(event / fps * sr)
    begin = attack - at
    bed = song[begin : begin + n].copy()
    gain = 0.053 / max(0.001, np.sqrt(np.mean(bed**2)))
    time = np.arange(n) / sr
    envelope = np.interp(
        time,
        [0, 0.18, max(0.21, at / sr - 0.42), at / sr, at / sr + 0.22, n / sr - 0.44, n / sr],
        [0.12, 0.76, 0.76, 0.34, 1.28, 1.28, 0],
    )
    bed *= (gain * envelope[:, None]).astype(np.float32)
    raw = audio.pcm(str(models / "sfx_ref" / (cue["fx"] + ".flac")))
    peak = np.argmax(np.abs(raw).max(axis=1))
    start = (at - peak) / sr
    plan.doc["sound"] = [
        {
            "id": "accent",
            "s": cue["fx"],
            "event_f": int(event),
            "start_s": max(0, start),
            "trim_s": max(0, -start),
            "db": float(cue.get("fx_peak", -16) - 20 * np.log10(np.max(np.abs(raw)))),
            "duck_db": 3,
        }
    ]
    voice, fx, placed = audio.split_audio(plan)
    if placed["skipped"] or not np.any(voice) or not np.any(fx):
        raise ValueError(f"Incomplete sound design: {placed}")
    out = str(work / "score.m4a")
    report = audio.master(voice, fx, out, str(work), n / sr, bed=bed)
    audio.mux(str(work / "picture.mp4"), out, str(output))
    return {
        "master": report,
        "song": cue["music"],
        "song_start_s": begin / sr,
        "song_onset_s": attack / sr,
        "accent": plan.doc["sound"][0],
        "placed": placed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device")
    for name in ("take", "work", "output", "models"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    cue = json.loads(Path(__file__).with_name("cues.json").read_text())[args.device]
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["VALMERA_SOUNDS"] = str(args.models.resolve() / "sfx_ref")
    cv2.setNumThreads(1)
    from scene import audio, run
    from scene import compile as compiler
    from scene.alien import merge, sense

    fps = 24
    info = sense.probe(str(args.take))
    n = round(sum(d for _, d in cue["cuts"]) * fps)
    doc = {
        "l1": 1,
        "fps": fps,
        "dur_f": n,
        "frame": [1280, 720],
        "seed": 22,
        "assets": {"main": dict(info, kind="video", path=str(args.take.resolve()))},
        "spine": [{"f": [0, n], "src": [0, n / fps]}],
        "film": {"grain": 0.008},
        "items": [{"k": "shot", "id": "interview", "pic": "main", "f": [0, n], "src": [0, n / fps]}],
    }
    ask = dict(cue, id=args.device, models=str(args.models.resolve()))
    for key in ("beats", "ends"):
        if key in cue:
            ask[key] = [round(t * fps) for t in cue[key]]
    if args.device == "double_signature":
        ask["line"] = "I can make it work."
    device = importlib.import_module("scene.alien.studies." + args.device)
    patch = device.plan(doc, str(work), ask)
    applied = merge(doc, patch)
    plan = compiler.compile(applied, str(work), segment=False, strict=True)
    if plan.warn:
        raise ValueError(plan.warn)
    run.render_plan(
        plan, str(work / "picture.mp4"), tier="final", audio=False, use_cache=False, cache=str(work / "cache")
    )
    event = patch["log"].get("accent_frame", ask["beats"][-1])
    sound = score(audio, plan, cue, event, args.models.resolve(), work, args.output)
    (work / "measure.json").write_text(
        json.dumps({"picture": patch["log"], "audio": sound, "frames": n, "fps": fps}, indent=2)
    )


if __name__ == "__main__":
    main()
