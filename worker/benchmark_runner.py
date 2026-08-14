"""Prepare and run blinded finished-edit benchmark manifests.

``editorial_benchmark`` deliberately judges evidence instead of decoding
videos itself.  This module is the reproducible bridge from ordinary finished
files to that contract: both sides get the same bounded output-time sampling,
the same number of visual tiles, and an opening/middle/ending audio reel.  It
never uses an EDL to give Valmera extra evidence a human reference would not
have.

Input manifest (paths may be relative to the manifest file)::

    {"cases": [{
      "id": "founder-01", "family": "talking_head_social",
      "brief": "a concise, credible founder reel",
      "source_context_path": "source.txt", "human_winner": "right",
      "left": {"video_path": "candidate.mp4", "story_text_path": "a.txt"},
      "right": {"video_path": "reference.mp4", "story_text_path": "b.txt"}
    }]}

The prepared manifest contains absolute evidence paths and can be evaluated on
any configured worker with ``python worker/benchmark_runner.py evaluate ...``.
Human labels remain beside model evidence; they never alter a model prompt.
"""

import argparse
import json
import os
import re
from pathlib import Path

import editorial_benchmark
import media
import screening
import sheets


EVIDENCE_VERSION = 1


def _slug(value):
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "case")).strip("-")
    return (clean or "case")[:80]


def _resolve(path, base):
    if not path:
        return None
    value = Path(path).expanduser()
    if not value.is_absolute():
        value = Path(base) / value
    return str(value.resolve())


def _read_text(value, path, base):
    if value is not None:
        return str(value)
    resolved = _resolve(path, base)
    if not resolved:
        return None
    return Path(resolved).read_text(encoding="utf-8")


def visual_plan(duration, max_frames=24):
    """Fair output-only sampling with explicit opening/closing coverage."""
    duration = max(0.0, float(duration or 0.0))
    count = max(1, int(max_frames or 1))
    if duration <= 0.02:
        return []
    # An empty EDL prevents candidate internals from influencing the sample;
    # a human render and a Valmera render receive the same clock-only plan.
    return screening.plan({}, duration, max_frames=count, base_frames=count)


def audio_windows(duration, span_s=6.0, count=3):
    """Opening/middle/ending windows, deduplicated for short outputs."""
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0.1:
        return []
    span = min(max(float(span_s or 6.0), 1.0), duration)
    centers = [duration * i / max(1, count - 1)
               for i in range(max(1, int(count or 1)))]
    out = []
    for center in centers:
        start = min(max(0.0, center - span / 2.0), duration - span)
        row = (round(start, 3), round(start + span, 3))
        if not out or abs(row[0] - out[-1][0]) > 0.08:
            out.append(row)
    return out


def _audio_reel(video_path, out_path, windows):
    """Concatenate bounded windows without decoding any picture."""
    chains, pads = [], []
    for i, (start, end) in enumerate(windows):
        chains.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},"
            f"asetpts=PTS-STARTPTS[a{i}]")
        pads.append(f"[a{i}]")
    graph = ";".join(chains + [
        "".join(pads) + f"concat=n={len(windows)}:v=0:a=1[outa]"])
    media.run([
        "ffmpeg", "-y", "-i", video_path, "-filter_complex", graph,
        "-map", "[outa]", "-ac", "1", "-ar", "22050", "-c:a",
        "libmp3lame", "-b:a", "64k", out_path,
    ], timeout=600)
    return out_path


def prepare_side(video_path, out_dir, side_name, *, story_text=None,
                 max_frames=24, page_tiles=12, audio_span_s=6.0):
    """Build equal visual/audio evidence for one finished video."""
    video_path = str(Path(video_path).expanduser().resolve())
    if not os.path.isfile(video_path):
        raise FileNotFoundError(video_path)
    out_dir = str(Path(out_dir).resolve())
    os.makedirs(out_dir, exist_ok=True)
    info = media.probe(video_path)
    duration = float(info["duration"])
    planned = visual_plan(duration, max_frames=max_frames)
    visual_paths, visual_labels = [], []
    for page_no, page in enumerate(screening.pages(planned, page_tiles), 1):
        path = os.path.join(out_dir,
                            f"{_slug(side_name)}-visual-{page_no}.jpg")
        sheets.build_frames_sheet(
            video_path, path, [row["time_s"] for row in page], cols=4,
            max_tiles=len(page), parallelism=4)
        visual_paths.append(path)
        visual_labels.append(screening.describe_page(page, page_no))

    windows = audio_windows(duration, span_s=audio_span_s)
    audio_path = None
    if info.get("has_audio") and windows:
        audio_path = os.path.join(out_dir, f"{_slug(side_name)}-audio.mp3")
        _audio_reel(video_path, audio_path, windows)
    audio_label = ("; ".join(f"{a:.2f}-{b:.2f}s" for a, b in windows)
                   if audio_path else None)
    return {
        "video_path": video_path,
        "duration_s": round(duration, 3),
        "visual_paths": visual_paths,
        "visual_labels": visual_labels,
        "audio_path": audio_path,
        "audio_label": audio_label,
        "story_text": story_text,
        "evidence_frame_count": len(planned),
    }


def prepare_manifest(manifest, manifest_dir, output_dir):
    """Resolve a source manifest and render its evidence pack."""
    cases = manifest.get("cases") if isinstance(manifest, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest must contain a non-empty cases array")
    output_dir = str(Path(output_dir).resolve())
    prepared = {"evidence_version": EVIDENCE_VERSION, "cases": []}
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise ValueError(f"case {index} must be an object")
        case_id = str(case.get("id") or f"case-{index}")
        case_dir = os.path.join(output_dir, _slug(case_id))
        source_context = _read_text(
            case.get("source_context"), case.get("source_context_path"),
            manifest_dir)
        row = {key: case.get(key) for key in (
            "id", "family", "brief", "human_winner")}
        row["id"] = case_id
        row["source_context"] = source_context
        for side in ("left", "right"):
            spec = case.get(side) or {}
            video_path = _resolve(spec.get("video_path"), manifest_dir)
            if not video_path:
                raise ValueError(f"case {case_id} {side} needs video_path")
            story = _read_text(spec.get("story_text"),
                               spec.get("story_text_path"), manifest_dir)
            row[side] = prepare_side(
                video_path, case_dir, side, story_text=story,
                max_frames=case.get("max_frames", 24),
                page_tiles=case.get("page_tiles", 12),
                audio_span_s=case.get("audio_span_s", 6.0))
        prepared["cases"].append(row)
    return prepared


def evaluate_manifest(prepared):
    results = [editorial_benchmark.evaluate_pair(case)
               for case in prepared.get("cases") or []]
    return {"benchmark_version": editorial_benchmark.BENCHMARK_VERSION,
            "results": results,
            "summary": editorial_benchmark.summarize(results)}


def _load(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def _write(path, value):
    path = str(Path(path).expanduser().resolve())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as target:
        json.dump(value, target, indent=2, ensure_ascii=False)
        target.write("\n")
    os.replace(tmp, path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare or evaluate blinded finished-edit benchmarks")
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("manifest")
    prep.add_argument("output_dir")
    prep.add_argument("--prepared", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("prepared")
    evaluate.add_argument("--results", required=True)
    run = commands.add_parser("run")
    run.add_argument("manifest")
    run.add_argument("output_dir")
    run.add_argument("--prepared", required=True)
    run.add_argument("--results", required=True)
    args = parser.parse_args(argv)

    if args.command in {"prepare", "run"}:
        manifest_path = str(Path(args.manifest).expanduser().resolve())
        prepared = prepare_manifest(
            _load(manifest_path), os.path.dirname(manifest_path),
            args.output_dir)
        _write(args.prepared, prepared)
    else:
        prepared = _load(args.prepared)
    if args.command in {"evaluate", "run"}:
        _write(args.results, evaluate_manifest(prepared))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
