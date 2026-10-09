#!/usr/bin/env python3
"""Validate Valmera podcast-shorts contract JSON.

JSON Schema handles shape and local conditionals. This module also checks the
cross-field timing, accounting, and identifier invariants that Draft 2020-12
cannot express without non-standard extensions.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


def _load_jsonschema() -> tuple[Any, Any]:
    """Load jsonschema, re-executing with an installed local Python if needed."""
    try:
        from jsonschema import Draft202012Validator, FormatChecker

        return Draft202012Validator, FormatChecker
    except ModuleNotFoundError as original_error:
        if __name__ != "__main__":
            raise RuntimeError(
                "validate_contract requires jsonschema>=4.18; import it with a Python "
                "environment that provides jsonschema"
            ) from original_error

        candidates: list[Path] = []
        configured = os.environ.get("VALMERA_CONTRACT_PYTHON")
        if configured:
            candidates.append(Path(configured).expanduser())
        pyenv_versions = Path.home() / ".pyenv" / "versions"
        if pyenv_versions.is_dir():
            candidates.extend(
                sorted(pyenv_versions.glob("*/bin/python"), reverse=True)
            )
        current = Path(sys.executable).resolve()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved == current or not resolved.is_file():
                continue
            probe = subprocess.run(
                [
                    str(resolved),
                    "-c",
                    "from jsonschema import Draft202012Validator, FormatChecker",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if probe.returncode == 0:
                os.execv(str(resolved), [str(resolved), *sys.argv])

        print(
            "validate_contract requires jsonschema>=4.18. Run it with an environment "
            "that provides jsonschema, or set VALMERA_CONTRACT_PYTHON to that Python executable.",
            file=sys.stderr,
        )
        raise SystemExit(2)


Draft202012Validator, FormatChecker = _load_jsonschema()


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = SKILL_ROOT / "schemas"
EPSILON = 1e-6

LEGACY_AUDIO_ATTESTATION_KEYS = {
    "audio_on",
    "source_fully_watched_with_audio",
    "full_reference_watched_with_audio",
    "complete_reference_watched_with_audio",
    "full_preview_watched_with_audio",
    "candidate_tracks_listened",
    "first_audible_music_s",
    "perceptible",
    "vibe_fit",
    "dialogue_clear",
    "full_latest_preview_watched",
    "continuous_full_preview_watch",
    "preview_watch_coverage",
    "preselection_watch",
    "full_watch_complete",
    "full_watch_coverage",
    "watch_gaps",
}


def _load_canonical_fingerprint_module() -> Any:
    path = Path(__file__).with_name("canonical_fingerprint.py")
    spec = importlib.util.spec_from_file_location("valmera_canonical_fingerprint", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load canonical fingerprint helper at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CANONICAL_FINGERPRINT = _load_canonical_fingerprint_module()


def _canonical_fingerprint(kind: str, data: dict[str, Any]) -> str:
    """Compute through the sibling helper using its Decimal JSON domain."""
    decimal_payload = json.loads(
        json.dumps(data, ensure_ascii=False, allow_nan=False),
        parse_float=Decimal,
        parse_int=Decimal,
    )
    return CANONICAL_FINGERPRINT.fingerprint(kind, decimal_payload)


def _canonical_object_digest(data: Any) -> str:
    """Digest any JSON evidence object with the same canonical number domain."""
    decimal_payload = json.loads(
        json.dumps(data, ensure_ascii=False, allow_nan=False),
        parse_float=Decimal,
        parse_int=Decimal,
    )
    canonical = CANONICAL_FINGERPRINT.canonical_json(decimal_payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def schema_path(name: str) -> Path:
    """Resolve a schema name without allowing path traversal."""
    normalized = name.removesuffix(".schema.json")
    if not normalized or any(part in normalized for part in ("/", "\\", "..")):
        raise ValueError(f"invalid schema name: {name!r}")
    path = SCHEMA_DIR / f"{normalized}.schema.json"
    if not path.is_file():
        available = ", ".join(p.name.removesuffix(".schema.json") for p in sorted(SCHEMA_DIR.glob("*.schema.json")))
        raise ValueError(f"unknown schema {name!r}; available: {available}")
    return path


def load_json(path_or_dash: str) -> Any:
    if path_or_dash == "-":
        return json.load(sys.stdin)
    with Path(path_or_dash).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _json_path(parts: Iterable[Any]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def _interval_errors(interval: Any, path: str, duration: float | None = None) -> list[str]:
    if not isinstance(interval, list) or len(interval) != 2:
        return []
    start, end = interval
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return []
    errors: list[str] = []
    if end <= start:
        errors.append(f"{path}: interval end must be greater than start")
    if duration is not None and end > duration + EPSILON:
        errors.append(f"{path}: interval ends after duration {duration}")
    return errors


def _coverage_errors(intervals: Any, duration: Any, path: str) -> list[str]:
    if not isinstance(duration, (int, float)) or duration <= 0 or not isinstance(intervals, list):
        return []
    errors: list[str] = []
    if not intervals:
        return [f"{path}: coverage must contain at least one interval"]
    cursor = 0.0
    for index, interval in enumerate(intervals):
        errors.extend(_interval_errors(interval, f"{path}[{index}]", float(duration)))
        if not isinstance(interval, list) or len(interval) != 2:
            continue
        start, end = interval
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if not math.isclose(float(start), cursor, abs_tol=EPSILON):
            relation = "gap" if float(start) > cursor else "overlap or out-of-order interval"
            errors.append(f"{path}[{index}]: {relation}; expected start {cursor}, got {start}")
        cursor = max(cursor, float(end))
    if not math.isclose(cursor, float(duration), abs_tol=EPSILON):
        errors.append(f"{path}: coverage must end at {duration}, got {cursor}")
    return errors


def _partial_coverage_errors(intervals: Any, duration: Any, path: str) -> list[str]:
    """Validate truthful partial watch intervals without requiring gap-free coverage."""
    if not isinstance(duration, (int, float)) or duration <= 0 or not isinstance(intervals, list):
        return []
    errors: list[str] = []
    previous_end = 0.0
    for index, interval in enumerate(intervals):
        errors.extend(_interval_errors(interval, f"{path}[{index}]", float(duration)))
        if not isinstance(interval, list) or len(interval) != 2:
            continue
        start, end = interval
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if index and float(start) < previous_end - EPSILON:
            errors.append(f"{path}[{index}]: overlaps or is out of order")
        previous_end = max(previous_end, float(end))
    return errors


def _coverage_complement(intervals: Any, duration: Any) -> list[list[float]] | None:
    """Return the ordered unwatched complement, or None for malformed intervals."""
    if not isinstance(duration, (int, float)) or duration <= 0 or not isinstance(intervals, list):
        return None
    cursor = 0.0
    gaps: list[list[float]] = []
    for interval in intervals:
        if not isinstance(interval, list) or len(interval) != 2:
            return None
        start, end = interval
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        start_f, end_f = float(start), float(end)
        if start_f < cursor - EPSILON or end_f <= start_f or end_f > float(duration) + EPSILON:
            return None
        if start_f > cursor + EPSILON:
            gaps.append([cursor, start_f])
        cursor = max(cursor, end_f)
    if cursor < float(duration) - EPSILON:
        gaps.append([cursor, float(duration)])
    return gaps


def _interval_lists_match(left: Any, right: Any) -> bool:
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
        return False
    for left_interval, right_interval in zip(left, right):
        if not (
            isinstance(left_interval, list)
            and isinstance(right_interval, list)
            and len(left_interval) == 2
            and len(right_interval) == 2
            and all(isinstance(value, (int, float)) for value in left_interval + right_interval)
            and math.isclose(float(left_interval[0]), float(right_interval[0]), abs_tol=EPSILON)
            and math.isclose(float(left_interval[1]), float(right_interval[1]), abs_tol=EPSILON)
        ):
            return False
    return True


def _merged_intervals(intervals: list[list[float]]) -> list[list[float]]:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + EPSILON:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


def _legacy_attestation_errors(value: Any, path: str = "$") -> list[str]:
    """Reject fields that claim Codex heard or continuously watched media."""
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            lowered = key.lower()
            if (
                key in LEGACY_AUDIO_ATTESTATION_KEYS
                or key.endswith("_watched_with_audio")
                or key.endswith("_listened")
                or "heard" in lowered
            ):
                errors.append(f"{child_path}: legacy auditory/self-attestation field is forbidden in v2")
            errors.extend(_legacy_attestation_errors(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_legacy_attestation_errors(child, f"{path}[{index}]"))
    return errors


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _coverage_and_gap_errors(
    coverage: Any,
    gaps: Any,
    duration: Any,
    path: str,
    *,
    require_complete: bool,
) -> list[str]:
    """Validate inspected/processed batches and their exact unprocessed complement."""
    errors = _partial_coverage_errors(coverage, duration, f"{path}.coverage")
    errors.extend(_partial_coverage_errors(gaps, duration, f"{path}.gaps"))
    expected = _coverage_complement(coverage, duration)
    if expected is not None and not _interval_lists_match(gaps, expected):
        errors.append(f"{path}.gaps: must equal the exact complement of coverage")
    if require_complete:
        errors.extend(_coverage_errors(coverage, duration, f"{path}.coverage"))
        if gaps != []:
            errors.append(f"{path}.gaps: complete evidence requires zero gaps")
    return errors


def _samples_within_coverage(samples: list[float], coverage: Any) -> bool:
    if not isinstance(coverage, list):
        return False
    for sample in samples:
        if not any(
            isinstance(interval, list)
            and len(interval) == 2
            and all(isinstance(value, (int, float)) for value in interval)
            and float(interval[0]) - EPSILON <= sample <= float(interval[1]) + EPSILON
            for interval in coverage
        ):
            return False
    return True


def _visual_inspection_errors(
    evidence: Any,
    path: str,
    expected_duration: Any,
    *,
    require_complete: bool,
    max_allowed_gap_s: float | None,
    coverage_field: str = "coverage",
    gap_field: str = "gaps",
) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    errors: list[str] = []
    duration = evidence.get("duration_s")
    if isinstance(expected_duration, (int, float)) and isinstance(duration, (int, float)):
        if not math.isclose(float(duration), float(expected_duration), abs_tol=EPSILON):
            errors.append(f"{path}.duration_s: must match the media/program duration")
    coverage = evidence.get(coverage_field)
    gaps = evidence.get(gap_field)
    errors.extend(
        _coverage_and_gap_errors(
            coverage,
            gaps,
            duration,
            path,
            require_complete=require_complete,
        )
    )
    raw_samples = evidence.get("sampled_frame_times")
    if not isinstance(raw_samples, list) or not all(isinstance(v, (int, float)) for v in raw_samples):
        return errors
    samples = [float(v) for v in raw_samples]
    if any(right <= left + EPSILON for left, right in zip(samples, samples[1:])):
        errors.append(f"{path}.sampled_frame_times: samples must be strictly increasing and unique")
    if isinstance(duration, (int, float)) and any(
        value < -EPSILON or value > float(duration) + EPSILON for value in samples
    ):
        errors.append(f"{path}.sampled_frame_times: samples must lie within duration")
    if samples and not _samples_within_coverage(samples, coverage):
        errors.append(f"{path}.sampled_frame_times: every sample must lie in an inspected batch")
    if isinstance(duration, (int, float)) and samples:
        boundaries = [0.0, *samples, float(duration)]
        computed = max(right - left for left, right in zip(boundaries, boundaries[1:]))
        reported = evidence.get("max_sample_gap_s")
        if not isinstance(reported, (int, float)) or not math.isclose(
            float(reported), computed, abs_tol=1e-3
        ):
            errors.append(
                f"{path}.max_sample_gap_s: must equal computed maximum sample gap {computed:.6f}"
            )
        if max_allowed_gap_s is not None and computed > max_allowed_gap_s + EPSILON:
            errors.append(
                f"{path}.sampled_frame_times: maximum gap {computed:.3f}s exceeds {max_allowed_gap_s:.3f}s"
            )
    return errors


def _processed_transcript_errors(
    evidence: Any,
    path: str,
    expected_duration: Any,
    *,
    require_complete: bool,
    coverage_field: str,
    gap_field: str,
) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    errors: list[str] = []
    duration = evidence.get("duration_s")
    if isinstance(expected_duration, (int, float)) and isinstance(duration, (int, float)):
        if not math.isclose(float(duration), float(expected_duration), abs_tol=EPSILON):
            errors.append(f"{path}.duration_s: must match the media/program duration")
    errors.extend(
        _coverage_and_gap_errors(
            evidence.get(coverage_field),
            evidence.get(gap_field),
            duration,
            path,
            require_complete=require_complete,
        )
    )
    return errors


def _program_speech_transcript_errors(
    evidence: Any,
    path: str,
    expected_duration: Any,
    *,
    require_complete: bool,
) -> list[str]:
    """Validate factual ASR over the actual stored preview's extracted audio stream."""
    if not isinstance(evidence, dict):
        return []
    errors: list[str] = []
    duration = evidence.get("duration_s")
    if all(isinstance(value, (int, float)) for value in (duration, expected_duration)):
        if not math.isclose(float(duration), float(expected_duration), abs_tol=EPSILON):
            errors.append(f"{path}.duration_s: must match the current preview duration")
    status = evidence.get("status")
    if require_complete and status != "complete":
        errors.append(f"{path}.status: complete preview ASR evidence is required")
    stream = evidence.get("audio_stream")
    if isinstance(stream, dict):
        pcm_duration = stream.get("extracted_pcm_duration_s")
        source_start = stream.get("source_start_s")
        expected_offset = source_start if isinstance(source_start, (int, float)) else 0.0
        if isinstance(evidence.get("media_time_offset_s"), (int, float)):
            if not math.isclose(
                float(evidence["media_time_offset_s"]),
                float(expected_offset),
                abs_tol=EPSILON,
            ):
                errors.append(
                    f"{path}.media_time_offset_s: must match the extracted audio stream start"
                )
        if isinstance(pcm_duration, (int, float)):
            errors.extend(
                _coverage_and_gap_errors(
                    evidence.get("processed_coverage"),
                    evidence.get("processing_gaps"),
                    pcm_duration,
                    f"{path}.asr_processing",
                    require_complete=require_complete or status == "complete",
                )
            )
        if all(isinstance(value, (int, float)) for value in (duration, pcm_duration)):
            media_end = min(float(duration), float(expected_offset) + float(pcm_duration))
            expected_coverage = (
                [[float(expected_offset), media_end]]
                if media_end > float(expected_offset) + EPSILON
                else []
            )
            if not _interval_lists_match(
                stream.get("media_timeline_coverage"), expected_coverage
            ):
                errors.append(
                    f"{path}.audio_stream.media_timeline_coverage: must map extracted PCM onto preview time"
                )
            expected_gaps = _coverage_complement(expected_coverage, duration)
            if expected_gaps is not None and not _interval_lists_match(
                stream.get("media_timeline_gaps"), expected_gaps
            ):
                errors.append(
                    f"{path}.audio_stream.media_timeline_gaps: must equal the exact non-audio preview complement"
                )
        for span_kind in ("words", "sentences"):
            previous_end = 0.0
            for index, span in enumerate(evidence.get(span_kind, [])):
                if not isinstance(span, dict):
                    continue
                errors.extend(
                    _interval_errors(
                        [span.get("start_s"), span.get("end_s")],
                        f"{path}.{span_kind}[{index}]",
                        float(pcm_duration)
                        if isinstance(pcm_duration, (int, float))
                        else None,
                    )
                )
                start, end = span.get("start_s"), span.get("end_s")
                if isinstance(start, (int, float)) and float(start) < previous_end - EPSILON:
                    errors.append(f"{path}.{span_kind}[{index}]: spans must be ordered and nonoverlapping")
                if isinstance(end, (int, float)):
                    previous_end = max(previous_end, float(end))
    elif status in {"complete", "partial"}:
        errors.append(f"{path}.audio_stream: complete/partial ASR requires extracted-stream facts")
    elif status == "unavailable":
        errors.extend(
            _coverage_and_gap_errors(
                evidence.get("processed_coverage"),
                evidence.get("processing_gaps"),
                duration,
                path,
                require_complete=False,
            )
        )
    tool_lineage = evidence.get("tool_lineage")
    if isinstance(tool_lineage, dict):
        decode = tool_lineage.get("decode_config")
        if isinstance(decode, dict):
            if _normalized_sha256(tool_lineage.get("decode_config_sha256")) != _normalized_sha256(
                _canonical_object_digest(decode)
            ):
                errors.append(f"{path}.tool_lineage.decode_config_sha256: must digest decode_config")
            if decode.get("model") != evidence.get("asr_model"):
                errors.append(f"{path}.tool_lineage.decode_config.model: must match ASR model")
    return errors


def _unplayable_render_rows(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("gate") == "render"
        and item.get("reason_code") in {"corrupt_render", "unplayable_render"}
    ]


def _has_unplayable_render_evidence(items: Any) -> bool:
    return bool(_unplayable_render_rows(items))


def _selection_errors(data: dict[str, Any]) -> list[str]:
    duration = data.get("source_duration_s")
    visual = data.get("source_visual_inspection")
    transcript = data.get("source_speech_transcript")
    errors = _visual_inspection_errors(
        visual,
        "$.source_visual_inspection",
        duration,
        require_complete=True,
        max_allowed_gap_s=None,
    )
    errors.extend(
        _processed_transcript_errors(
            transcript,
            "$.source_speech_transcript",
            duration,
            require_complete=True,
            coverage_field="coverage",
            gap_field="gaps",
        )
    )
    source_sha = _normalized_sha256(data.get("source_sha256"))
    for path, evidence in (
        ("$.source_visual_inspection", visual),
        ("$.source_speech_transcript", transcript),
    ):
        if isinstance(evidence, dict) and _normalized_sha256(evidence.get("media_sha256")) != source_sha:
            errors.append(f"{path}.media_sha256: must match source_sha256")
    if isinstance(visual, dict):
        configured = visual.get("configured_sample_step_s")
        reported = visual.get("max_sample_gap_s")
        if isinstance(configured, (int, float)) and isinstance(reported, (int, float)):
            if float(reported) > float(configured) + EPSILON:
                errors.append(
                    "$.source_visual_inspection.max_sample_gap_s: cannot exceed configured_sample_step_s"
                )
        for field in ("shot_index_exhausted", "page_cursors_exhausted"):
            if visual.get(field) is not True:
                errors.append(f"$.source_visual_inspection.{field}: must be true")
    clips = data.get("clips")
    if not isinstance(clips, list) or not isinstance(duration, (int, float)):
        return errors

    ranks: list[int] = []
    ranges: list[tuple[float, float, int]] = []
    for index, clip in enumerate(clips):
        if not isinstance(clip, dict):
            continue
        rank, start, end = clip.get("rank"), clip.get("start"), clip.get("end")
        if isinstance(rank, int):
            ranks.append(rank)
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        errors.extend(_interval_errors([start, end], f"$.clips[{index}].range", float(duration)))
        clip_duration = float(end) - float(start)
        if clip_duration < 10 - EPSILON or clip_duration > 120 + EPSILON:
            errors.append(f"$.clips[{index}]: duration must be between 10 and 120 seconds")
        ranges.append((float(start), float(end), index))

    if len(ranks) != len(set(ranks)):
        errors.append("$.clips: ranks must be unique")
    if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
        errors.append("$.clips: ranks must be contiguous from 1 through clip count")
    if len({(start, end) for start, end, _ in ranges}) != len(ranges):
        errors.append("$.clips: source ranges must be unique")
    ordered = sorted(ranges)
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1] - EPSILON:
            errors.append(
                f"$.clips[{current[2]}]: overlaps $.clips[{previous[2]}] "
                f"({current[0]} < {previous[1]})"
            )
    try:
        expected_fingerprint = _canonical_fingerprint("selection", data)
    except (TypeError, ValueError) as exc:
        errors.append(f"$.selection_fingerprint: canonicalization failed: {exc}")
    else:
        if data.get("selection_fingerprint") != expected_fingerprint:
            errors.append(
                "$.selection_fingerprint: does not match canonical selection payload "
                f"(expected {expected_fingerprint})"
            )
    return errors


def _reference_profile_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    duration = data.get("reference_duration_s")
    reference_sha = _normalized_sha256(data.get("reference_sha256"))
    youtube_id = data.get("youtube_video_id")
    asset_id = data.get("parent_reference_asset_id")
    previsual = data.get("preselection_visual_inspection")
    pretranscript = data.get("preselection_speech_transcript")
    signal = data.get("signal_analysis")
    postvisual = data.get("postupload_visual_inspection")
    posttranscript = data.get("postupload_speech_transcript")

    errors.extend(
        _visual_inspection_errors(
            previsual,
            "$.preselection_visual_inspection",
            duration,
            require_complete=True,
            max_allowed_gap_s=1.0,
        )
    )
    errors.extend(
        _visual_inspection_errors(
            postvisual,
            "$.postupload_visual_inspection",
            duration,
            require_complete=True,
            max_allowed_gap_s=1.0,
        )
    )
    if isinstance(previsual, dict) and previsual.get("youtube_video_id") != youtube_id:
        errors.append("$.preselection_visual_inspection.youtube_video_id: must match reference")
    if isinstance(postvisual, dict):
        if postvisual.get("parent_reference_asset_id") != asset_id:
            errors.append("$.postupload_visual_inspection.parent_reference_asset_id: must match profile")
        if _normalized_sha256(postvisual.get("media_sha256")) != reference_sha:
            errors.append("$.postupload_visual_inspection.media_sha256: must match reference SHA-256")
        previous_boundary = -1.0
        for index, check in enumerate(postvisual.get("observed_boundary_checks", [])):
            if not isinstance(check, dict):
                continue
            boundary = check.get("boundary_time_s")
            if isinstance(boundary, (int, float)):
                if boundary < previous_boundary - EPSILON:
                    errors.append("$.postupload_visual_inspection.observed_boundary_checks: rows must be time-ordered")
                previous_boundary = float(boundary)
                if isinstance(duration, (int, float)) and boundary > float(duration) + EPSILON:
                    errors.append(f"$.postupload_visual_inspection.observed_boundary_checks[{index}].boundary_time_s: exceeds duration")
            times = check.get("checked_frame_times", [])
            if isinstance(times, list) and all(isinstance(value, (int, float)) for value in times):
                if any(right <= left + EPSILON for left, right in zip(times, times[1:])):
                    errors.append(f"$.postupload_visual_inspection.observed_boundary_checks[{index}].checked_frame_times: must be strictly increasing")
                if isinstance(duration, (int, float)) and any(value > float(duration) + EPSILON for value in times):
                    errors.append(f"$.postupload_visual_inspection.observed_boundary_checks[{index}].checked_frame_times: exceeds duration")

    if isinstance(pretranscript, dict):
        if _normalized_sha256(pretranscript.get("media_sha256")) != reference_sha:
            errors.append("$.preselection_speech_transcript.media_sha256: must match reference SHA-256")
        transcript_duration = pretranscript.get("duration_s")
        if all(isinstance(value, (int, float)) for value in (duration, transcript_duration)):
            if not math.isclose(float(duration), float(transcript_duration), abs_tol=EPSILON):
                errors.append("$.preselection_speech_transcript.duration_s: must match reference duration")
        stream = pretranscript.get("audio_stream")
        asr = pretranscript.get("asr")
        tool_lineage = pretranscript.get("tool_lineage")
        if isinstance(tool_lineage, dict):
            decode_config = tool_lineage.get("decode_config")
            if isinstance(decode_config, dict):
                if _normalized_sha256(tool_lineage.get("decode_config_sha256")) != _normalized_sha256(_canonical_object_digest(decode_config)):
                    errors.append("$.preselection_speech_transcript.tool_lineage.decode_config_sha256: must digest decode_config")
                if isinstance(asr, dict) and decode_config.get("model") != asr.get("model"):
                    errors.append("$.preselection_speech_transcript.tool_lineage.decode_config.model: must match ASR model")
        if isinstance(stream, dict) and isinstance(asr, dict):
            pcm_duration = stream.get("extracted_pcm_duration_s")
            errors.extend(
                _coverage_errors(
                    asr.get("processed_coverage"),
                    pcm_duration,
                    "$.preselection_speech_transcript.asr.processed_coverage",
                )
            )
            if asr.get("processing_gaps") != []:
                errors.append("$.preselection_speech_transcript.asr.processing_gaps: ASR processing must have zero internal gaps")
            offset = asr.get("media_time_offset_s")
            source_start = stream.get("source_start_s")
            expected_offset = source_start if isinstance(source_start, (int, float)) else 0.0
            if isinstance(offset, (int, float)) and not math.isclose(float(offset), float(expected_offset), abs_tol=EPSILON):
                errors.append("$.preselection_speech_transcript.asr.media_time_offset_s: must match audio stream start")
            if all(isinstance(value, (int, float)) for value in (duration, pcm_duration, expected_offset)):
                media_end = min(float(duration), float(expected_offset) + float(pcm_duration))
                expected_coverage = [[float(expected_offset), media_end]] if media_end > float(expected_offset) else []
                if not _interval_lists_match(stream.get("media_timeline_coverage"), expected_coverage):
                    errors.append("$.preselection_speech_transcript.audio_stream.media_timeline_coverage: must map extracted PCM onto media time")
                expected_gaps = _coverage_complement(expected_coverage, duration)
                if expected_gaps is not None and not _interval_lists_match(stream.get("media_timeline_gaps"), expected_gaps):
                    errors.append("$.preselection_speech_transcript.audio_stream.media_timeline_gaps: must equal the exact non-audio complement")
            for span_kind in ("words", "sentences"):
                previous_end = 0.0
                for index, span in enumerate(asr.get(span_kind, [])):
                    if not isinstance(span, dict):
                        continue
                    errors.extend(_interval_errors([span.get("start_s"), span.get("end_s")], f"$.preselection_speech_transcript.asr.{span_kind}[{index}]", float(pcm_duration) if isinstance(pcm_duration, (int, float)) else None))
                    if isinstance(span.get("start_s"), (int, float)) and float(span["start_s"]) < previous_end - EPSILON:
                        errors.append(f"$.preselection_speech_transcript.asr.{span_kind}[{index}]: spans must be ordered and nonoverlapping")
                    if isinstance(span.get("end_s"), (int, float)):
                        previous_end = max(previous_end, float(span["end_s"]))

    if isinstance(posttranscript, dict):
        if posttranscript.get("parent_reference_asset_id") != asset_id:
            errors.append("$.postupload_speech_transcript.parent_reference_asset_id: must match profile")
        if _normalized_sha256(posttranscript.get("media_sha256")) != reference_sha:
            errors.append("$.postupload_speech_transcript.media_sha256: must match reference SHA-256")
        errors.extend(
            _processed_transcript_errors(
                posttranscript,
                "$.postupload_speech_transcript",
                duration,
                require_complete=True,
                coverage_field="processed_coverage",
                gap_field="processing_gaps",
            )
        )
    if isinstance(signal, dict):
        if _normalized_sha256(signal.get("media_sha256")) != reference_sha:
            errors.append("$.signal_analysis.media_sha256: must match reference SHA-256")
        signal_duration = signal.get("duration_s")
        if all(isinstance(value, (int, float)) for value in (duration, signal_duration)) and not math.isclose(float(duration), float(signal_duration), abs_tol=EPSILON):
            errors.append("$.signal_analysis.duration_s: must match reference duration")
        for index, interval in enumerate(signal.get("silence_intervals", [])):
            errors.extend(_interval_errors(interval, f"$.signal_analysis.silence_intervals[{index}]", float(duration) if isinstance(duration, (int, float)) else None))

    selected_at = _parse_datetime(data.get("selected_at"))
    completion_rows: list[tuple[str, Any]] = []
    if isinstance(previsual, dict):
        completion_rows.append(("preselection_visual_inspection", previsual.get("completed_at")))
    if isinstance(pretranscript, dict) and isinstance(pretranscript.get("asr"), dict):
        completion_rows.append(("preselection_speech_transcript.asr", pretranscript["asr"].get("completed_at")))
    if isinstance(signal, dict):
        completion_rows.append(("signal_analysis", signal.get("completed_at")))
    engagement = data.get("engagement_observations")
    if isinstance(engagement, list):
        evidence_keys = [
            (row.get("metric"), row.get("observed_value"), row.get("evidence_url"))
            for row in engagement
            if isinstance(row, dict)
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            errors.append("$.engagement_observations: duplicate evidence rows are not allowed")
        completion_rows.extend(
            (f"engagement_observations[{index}]", row.get("observed_at"))
            for index, row in enumerate(engagement)
            if isinstance(row, dict)
        )
    if selected_at is not None:
        for path, raw_time in completion_rows:
            completed_at = _parse_datetime(raw_time)
            if completed_at is not None and completed_at > selected_at:
                errors.append(f"$.selected_at: must not precede {path} evidence completion")
        for path, row in (
            ("postupload_visual_inspection", postvisual),
            ("postupload_speech_transcript", posttranscript),
        ):
            if isinstance(row, dict):
                completed_at = _parse_datetime(row.get("completed_at"))
                if completed_at is not None and completed_at < selected_at:
                    errors.append(f"$.{path}.completed_at: postupload verification cannot precede final selection")

    music = data.get("music_identity")
    music_ids = {
        row.get("evidence_id")
        for row in music.get("evidence", [])
        if isinstance(music, dict) and isinstance(row, dict)
    } if isinstance(music, dict) else set()
    if isinstance(music, dict):
        for index, row in enumerate(music.get("evidence", [])):
            if isinstance(row, dict) and selected_at is not None:
                observed_at = _parse_datetime(row.get("observed_at"))
                if observed_at is not None and observed_at > selected_at:
                    errors.append(f"$.music_identity.evidence[{index}].observed_at: selection cannot precede identity metadata")
    predecision_ids: set[Any] = set(music_ids)
    for row in (previsual, signal):
        if isinstance(row, dict):
            predecision_ids.add(row.get("evidence_id"))
    if isinstance(pretranscript, dict):
        predecision_ids.add(pretranscript.get("evidence_id"))
    predecision_ids.update(
        row.get("evidence_id") for row in engagement or [] if isinstance(row, dict)
    )
    predecision_ids.discard(None)
    decision = data.get("selection_decision")
    if isinstance(decision, dict):
        missing = set(decision.get("evidence_ids", [])) - predecision_ids
        if missing:
            errors.append("$.selection_decision.evidence_ids: must cite declared preselection evidence")
        if decision.get("rationale") != data.get("selection_rationale"):
            errors.append("$.selection_decision.rationale: must equal selection_rationale")
        required_decision_ids = {
            previsual.get("evidence_id") if isinstance(previsual, dict) else None,
            pretranscript.get("evidence_id") if isinstance(pretranscript, dict) else None,
            signal.get("evidence_id") if isinstance(signal, dict) else None,
        }
        required_decision_ids.discard(None)
        if not required_decision_ids.issubset(set(decision.get("evidence_ids", []))):
            errors.append(
                "$.selection_decision.evidence_ids: must cite visual, transcript, and signal evidence"
            )
        if isinstance(signal, dict) and signal.get("status") != "complete":
            errors.append("$.signal_analysis.status: final selection requires completed aggregate signal evidence")
        engagement_ids = {
            row.get("evidence_id") for row in engagement or [] if isinstance(row, dict)
        }
        if not (set(decision.get("evidence_ids", [])) & engagement_ids):
            errors.append("$.selection_decision.evidence_ids: viral selection must cite engagement evidence")
        if isinstance(music, dict) and music.get("status") == "identified":
            if not (set(decision.get("evidence_ids", [])) & music_ids):
                errors.append("$.selection_decision.evidence_ids: identified music must cite identity metadata")
        if isinstance(signal, dict) and signal.get("evidence_id") in set(decision.get("evidence_ids", [])) and signal.get("status") != "complete":
            errors.append("$.selection_decision.evidence_ids: unavailable signal analysis cannot support the selection")

    if isinstance(previsual, dict) and isinstance(postvisual, dict):
        if previsual.get("evidence_id") == postvisual.get("evidence_id"):
            errors.append("$: preselection and postupload visual evidence IDs must be distinct")
        if _canonical_object_digest(previsual) == _canonical_object_digest(postvisual):
            errors.append("$: postupload visual verification must be distinct from preselection evidence")
    if isinstance(pretranscript, dict) and isinstance(posttranscript, dict):
        if pretranscript.get("evidence_id") == posttranscript.get("evidence_id"):
            errors.append("$: preselection and postupload transcript evidence IDs must be distinct")

    observations = data.get("observations")
    if not isinstance(observations, list):
        return errors
    ids: list[str] = []
    all_profile_evidence = set(predecision_ids)
    for row in (postvisual, posttranscript):
        if isinstance(row, dict):
            all_profile_evidence.add(row.get("evidence_id"))
    all_profile_evidence.discard(None)
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            continue
        observation_id = observation.get("observation_id")
        if isinstance(observation_id, str):
            ids.append(observation_id)
        if observation.get("start_s") is not None or observation.get("end_s") is not None:
            errors.extend(
                _interval_errors(
                    [observation.get("start_s"), observation.get("end_s")],
                    f"$.observations[{index}]",
                    float(duration) if isinstance(duration, (int, float)) else None,
                )
            )
        evidence_kind = observation.get("evidence_kind")
        cited_ids = set(observation.get("evidence_ids", []))
        if cited_ids - all_profile_evidence:
            errors.append(f"$.observations[{index}].evidence_ids: cites undeclared profile evidence")
        if evidence_kind == "music_metadata_inference":
            if cited_ids - music_ids:
                errors.append(f"$.observations[{index}].evidence_ids: music inference may cite only music identity metadata")
            forbidden_claim_terms = {
                "timing", "starts", "entry", "intensity", "instrument", "instrumentation",
                "loud", "quiet", "drop", "build", "bpm", "tempo",
            }
            statement = str(observation.get("statement", "")).lower()
            if any(term in statement for term in forbidden_claim_terms):
                errors.append(f"$.observations[{index}].statement: metadata alone cannot establish timing, intensity, instrumentation, or signal behavior")
        elif evidence_kind == "visual":
            visual_ids = {
                row.get("evidence_id") for row in (previsual, postvisual) if isinstance(row, dict)
            }
            if cited_ids - visual_ids:
                errors.append(f"$.observations[{index}].evidence_ids: visual observation must cite visual evidence")
        elif evidence_kind == "transcript":
            transcript_ids = {
                row.get("evidence_id") for row in (pretranscript, posttranscript) if isinstance(row, dict)
            }
            if cited_ids - transcript_ids:
                errors.append(f"$.observations[{index}].evidence_ids: transcript observation must cite transcript evidence")
    if len(ids) != len(set(ids)):
        errors.append("$.observations: observation_id values must be unique")
    return errors


def _child_assignment_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    source = data.get("source")
    if isinstance(source, dict):
        source_duration = source.get("source_duration_s")
        bounded_duration = (
            float(source_duration)
            if isinstance(source_duration, (int, float))
            else None
        )
        start, end = source.get("approved_start_s"), source.get("approved_end_s")
        errors.extend(
            _interval_errors(
                [start, end], "$.source.approved_range", bounded_duration
            )
        )
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            clip_duration = float(end) - float(start)
            if clip_duration < 10 - EPSILON or clip_duration > 120 + EPSILON:
                errors.append("$.source: approved range must be between 10 and 120 seconds")
        seeded_start = source.get("seeded_child_start_s")
        seeded_end = source.get("seeded_child_end_s")
        errors.extend(
            _interval_errors(
                [seeded_start, seeded_end],
                "$.source.seeded_child_range",
                bounded_duration,
            )
        )
        if all(
            isinstance(value, (int, float))
            for value in (start, end, seeded_start, seeded_end)
        ):
            ranges_match = (
                float(start) == float(seeded_start)
                and float(end) == float(seeded_end)
            )
            expected_reason = "none" if ranges_match else "word_boundary_snap"
            if source.get("seed_snap_reason") != expected_reason:
                errors.append(
                    "$.source.seed_snap_reason: must be 'none' only for an exact seed "
                    "range match, otherwise 'word_boundary_snap'"
                )

    slate = data.get("candidate_slate")
    candidate_ids: list[str] = []
    if isinstance(slate, list):
        for index, candidate in enumerate(slate):
            if not isinstance(candidate, dict):
                continue
            candidate_id = candidate.get("candidate_id")
            if isinstance(candidate_id, str):
                candidate_ids.append(candidate_id)
            if candidate.get("dominant_mode") == candidate.get("secondary_mode"):
                errors.append(f"$.candidate_slate[{index}]: dominant and secondary modes must differ")
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append("$.candidate_slate: candidate_id values must be unique")
    profile = data.get("story_profile")
    if isinstance(profile, dict) and profile.get("classification_status") == "hybrid":
        if profile.get("dominant_mode") == profile.get("secondary_mode"):
            errors.append("$.story_profile: hybrid dominant and secondary modes must differ")

    treatment = data.get("treatment")
    if isinstance(treatment, dict) and treatment.get("music_policy") != "none":
        assigned_start = treatment.get("assigned_music_start_s")
        assigned_end = treatment.get("assigned_music_end_s")
        errors.extend(
            _interval_errors(
                [assigned_start, assigned_end],
                "$.treatment.assigned_music_range",
            )
        )

    exclusions = data.get("sibling_asset_windows_to_avoid")
    exclusion_keys: list[tuple[Any, Any, Any, Any]] = []
    if isinstance(exclusions, list):
        for index, window in enumerate(exclusions):
            if not isinstance(window, dict):
                continue
            stable_ids = [
                value
                for value in (window.get("canonical_source_id"), window.get("sha256"))
                if value is not None
            ]
            if len(stable_ids) != 1:
                errors.append(
                    f"$.sibling_asset_windows_to_avoid[{index}]: exactly one stable "
                    "identity (canonical_source_id or sha256) is required"
                )
            if window.get("provider") == "youtube" and not (
                isinstance(window.get("canonical_url"), str)
                and (
                    "youtube.com/" in window["canonical_url"]
                    or "youtu.be/" in window["canonical_url"]
                )
            ):
                errors.append(
                    f"$.sibling_asset_windows_to_avoid[{index}].canonical_url: "
                    "YouTube provider requires a YouTube URL"
                )
            stable = stable_ids[0] if len(stable_ids) == 1 else None
            exclusion_keys.append(
                (
                    window.get("provider"),
                    stable,
                    window.get("source_start_s"),
                    window.get("source_duration_s"),
                )
            )
        if len(exclusion_keys) != len(set(exclusion_keys)):
            errors.append("$.sibling_asset_windows_to_avoid: duplicate stable source windows are not allowed")

    reference_transfer = data.get("reference_transfer")
    if isinstance(reference_transfer, dict):
        considered = set(
            reference_transfer.get("reference_observation_ids_considered", [])
        )
        dispositions: dict[Any, str] = {}
        for decision_kind in ("transfer", "adapt", "reject"):
            for row_index, row in enumerate(
                reference_transfer.get(decision_kind, [])
            ):
                if not isinstance(row, dict):
                    continue
                for observation_id in row.get("reference_observation_ids", []):
                    if observation_id not in considered:
                        errors.append(
                            f"$.reference_transfer.{decision_kind}[{row_index}]."
                            "reference_observation_ids: every decision citation must "
                            "appear in reference_observation_ids_considered"
                        )
                    previous = dispositions.get(observation_id)
                    if previous is not None and previous != decision_kind:
                        errors.append(
                            f"$.reference_transfer: observation {observation_id!r} "
                            f"cannot be both {previous} and {decision_kind}"
                        )
                    dispositions[observation_id] = decision_kind

    try:
        expected_fingerprint = _canonical_fingerprint("assignment", data)
    except (TypeError, ValueError) as exc:
        errors.append(f"$.assignment_input_fingerprint: canonicalization failed: {exc}")
    else:
        if data.get("assignment_input_fingerprint") != expected_fingerprint:
            errors.append(
                "$.assignment_input_fingerprint: does not match canonical assignment payload "
                f"(expected {expected_fingerprint})"
            )
    return errors


def _recast_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        expected_fingerprint = _canonical_fingerprint("recast", data)
    except (TypeError, ValueError) as exc:
        errors.append(f"$.recast_input_fingerprint: canonicalization failed: {exc}")
    else:
        if data.get("recast_input_fingerprint") != expected_fingerprint:
            errors.append(
                "$.recast_input_fingerprint: does not match canonical pre-approval "
                f"recast payload (expected {expected_fingerprint})"
            )
    treatment_delta = data.get("approved_treatment_delta")
    if isinstance(treatment_delta, dict) and treatment_delta.get("music_policy") != "none":
        assigned_start = treatment_delta.get("assigned_music_start_s")
        assigned_end = treatment_delta.get("assigned_music_end_s")
        errors.extend(
            _interval_errors(
                [assigned_start, assigned_end],
                "$.approved_treatment_delta.assigned_music_range",
            )
        )
        if (
            treatment_delta.get("music_entry_policy") in {"delayed", "payoff_only"}
            and isinstance(assigned_start, (int, float))
            and assigned_start <= EPSILON
        ):
            errors.append(
                "$.approved_treatment_delta.assigned_music_start_s: "
                "delayed/payoff-only music must start after 0"
            )
    assessments = data.get("candidate_assessments")
    if not isinstance(assessments, list):
        return errors
    inspected_ids = set(data.get("inspected_evidence_ids", []))
    for index, assessment in enumerate(assessments):
        if not isinstance(assessment, dict):
            continue
        supporting = set(assessment.get("supporting_evidence_ids", []))
        contradicting = set(assessment.get("contradicting_evidence_ids", []))
        uninspected = (supporting | contradicting) - inspected_ids
        if uninspected:
            errors.append(
                f"$.candidate_assessments[{index}]: evidence must be included in "
                "inspected_evidence_ids: " + ", ".join(map(str, sorted(uninspected)))
            )
        if supporting & contradicting:
            errors.append(
                f"$.candidate_assessments[{index}]: the same evidence cannot both "
                "support and contradict a candidate"
            )
    ids = [item.get("candidate_id") for item in assessments if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append("$.candidate_assessments: candidate_id values must be unique")
    recommended_id = data.get("recommended_candidate_id")
    recommended_rows = [
        item
        for item in assessments
        if isinstance(item, dict) and item.get("recommendation") == "recommended"
    ]
    if data.get("status") == "awaiting_parent_approval":
        if recommended_id not in ids:
            errors.append("$.recommended_candidate_id: must identify an assessed candidate")
        if len(recommended_rows) != 1:
            errors.append("$.candidate_assessments: exactly one candidate must be recommended")
        elif recommended_rows[0].get("candidate_id") != recommended_id:
            errors.append("$.recommended_candidate_id: must match the recommended assessment")
    if data.get("status") == "approved":
        approved_id = data.get("approved_candidate_id")
        matching = [
            item
            for item in assessments
            if isinstance(item, dict) and item.get("candidate_id") == approved_id
        ]
        if len(matching) != 1:
            errors.append("$.approved_candidate_id: must identify exactly one assessed candidate")
        elif matching[0].get("recommendation") == "rejected" or matching[0].get("evidence_matches") is not True:
            errors.append("$.approved_candidate_id: approved candidate must match evidence and not be rejected")
        approved_cast = data.get("approved_cast")
        if isinstance(approved_cast, dict):
            if approved_cast.get("classification_status") == "hybrid":
                if approved_cast.get("dominant_mode") == approved_cast.get("secondary_mode"):
                    errors.append("$.approved_cast: hybrid dominant and secondary modes must differ")
    return errors


def _visual_provenance_errors(
    container: dict[str, Any],
    duration: float | None,
    path: str,
    *,
    check_provider_counts: bool,
    terminal_pass: bool,
) -> list[str]:
    errors: list[str] = []
    records = container.get("visual_provenance_records")
    if not isinstance(records, list):
        return errors
    youtube_count = 0
    pexels_count = 0
    pexels_keys: set[str] = set()
    provenance_keys: list[tuple[Any, Any, Any]] = []
    restriction_keys = set(container.get("assets_with_known_usage_restrictions", []))
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        provider = record.get("provider")
        youtube_count += provider == "youtube"
        pexels_count += provider == "pexels"
        if provider == "pexels" and isinstance(record.get("asset_key"), str):
            pexels_keys.add(record["asset_key"])
        errors.extend(
            _interval_errors(
                [record.get("output_start_s"), record.get("output_end_s")],
                f"{path}.visual_provenance_records[{index}].output_range",
                duration,
            )
        )
        if provider == "youtube" and not (
            isinstance(record.get("canonical_url"), str)
            and (
                "youtube.com/" in record["canonical_url"]
                or "youtu.be/" in record["canonical_url"]
            )
        ):
            errors.append(
                f"{path}.visual_provenance_records[{index}].canonical_url: "
                "YouTube provider requires a YouTube URL"
            )
        evidence_status = record.get("available_usage_evidence")
        known_restriction = record.get("known_usage_restriction")
        if evidence_status == "known_restriction" and not known_restriction:
            errors.append(
                f"{path}.visual_provenance_records[{index}].known_usage_restriction: "
                "required when evidence status is known_restriction"
            )
        asset_key = record.get("asset_key")
        if known_restriction and asset_key not in restriction_keys:
            errors.append(
                f"{path}.assets_with_known_usage_restrictions: must include {asset_key!r}"
            )
        if terminal_pass and record.get("visually_inspected") is not True:
            errors.append(
                f"{path}.visual_provenance_records[{index}].visually_inspected: "
                "must be true for an accepted result"
            )
        provenance_keys.append(
            (asset_key, record.get("source_start_s"), record.get("source_duration_s"))
        )
    if len(provenance_keys) != len(set(provenance_keys)):
        errors.append(f"{path}.visual_provenance_records: duplicate asset/source windows are not allowed")
    if check_provider_counts:
        if container.get("youtube_asset_uses") != youtube_count:
            errors.append(f"{path}.youtube_asset_uses: must equal YouTube provenance record count")
        if container.get("pexels_asset_uses") != pexels_count:
            errors.append(f"{path}.pexels_asset_uses: must equal Pexels provenance record count")

    exceptions = container.get("topical_pexels_exceptions")
    if isinstance(exceptions, list):
        for index, exception in enumerate(exceptions):
            if not isinstance(exception, dict):
                continue
            errors.extend(
                _interval_errors(
                    [exception.get("output_start_s"), exception.get("output_end_s")],
                    f"{path}.topical_pexels_exceptions[{index}].output_range",
                    duration,
                )
            )
            asset_key = exception.get("pexels_asset_key")
            if asset_key not in pexels_keys:
                errors.append(
                    f"{path}.topical_pexels_exceptions[{index}].pexels_asset_key: "
                    "must identify a Pexels provenance record"
                )
            candidates = exception.get("inspected_youtube_candidates")
            if isinstance(candidates, list):
                if terminal_pass and not candidates:
                    errors.append(
                        f"{path}.topical_pexels_exceptions[{index}]."
                        "inspected_youtube_candidates: accepted Pexels exceptions require evidence"
                    )
                urls = [
                    row.get("canonical_url")
                    for row in candidates
                    if isinstance(row, dict)
                ]
                if len(urls) != len(set(urls)):
                    errors.append(
                        f"{path}.topical_pexels_exceptions[{index}]."
                        "inspected_youtube_candidates: URLs must be unique"
                    )
    return errors


def _music_timing_errors(
    music: dict[str, Any],
    duration: float | None,
    path: str,
    *,
    terminal_pass: bool,
) -> list[str]:
    errors: list[str] = []
    policy = music.get("music_policy")
    identity = music.get("music_identity")
    edl = music.get("edl_facts")
    analysis = music.get("selected_track_analysis")
    inference = music.get("music_fit_inference")
    shortlist = music.get("candidate_metadata_shortlist")
    mix = music.get("mix_measurements")
    if not all(isinstance(row, dict) for row in (identity, edl, analysis, inference, mix)):
        return errors

    identity_ids = [
        row.get("evidence_id")
        for row in identity.get("evidence", [])
        if isinstance(row, dict)
    ]
    if len(identity_ids) != len(set(identity_ids)):
        errors.append(f"{path}.music_identity.evidence: evidence_id values must be unique")
    allowed_evidence_ids = {
        "visual:render-inspection",
        "transcript:program",
        *(value for value in identity_ids if isinstance(value, str)),
    }
    analysis_id = analysis.get("evidence_id")
    if isinstance(analysis_id, str):
        allowed_evidence_ids.add(analysis_id)
    cited = set(inference.get("evidence_ids", []))
    if cited - allowed_evidence_ids:
        errors.append(
            f"{path}.music_fit_inference.evidence_ids: must cite persisted identity/analysis evidence"
        )

    candidate_ids: list[Any] = []
    selected_rows: list[dict[str, Any]] = []
    if isinstance(shortlist, list):
        for index, row in enumerate(shortlist):
            if not isinstance(row, dict):
                continue
            candidate_ids.append(row.get("candidate_id"))
            if row.get("selected") is True:
                selected_rows.append(row)
            missing = set(row.get("identity_evidence_ids", [])) - set(identity_ids)
            if missing:
                errors.append(
                    f"{path}.candidate_metadata_shortlist[{index}].identity_evidence_ids: "
                    "must cite music_identity evidence"
                )
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append(f"{path}.candidate_metadata_shortlist: candidate_id values must be unique")

    if policy == "none":
        if identity.get("status") != "not_applicable":
            errors.append(f"{path}.music_identity.status: no-music policy requires not_applicable")
        if inference.get("result") != "not_applicable":
            errors.append(f"{path}.music_fit_inference.result: no-music policy requires not_applicable")
        if shortlist:
            errors.append(f"{path}.candidate_metadata_shortlist: no-music policy requires an empty list")
        if analysis.get("status") != "not_applicable":
            errors.append(f"{path}.selected_track_analysis.status: no-music policy requires not_applicable")
        return errors

    selection_mode = music.get("selection_mode")
    if selection_mode == "compared_catalog_slate" and len(shortlist or []) < 3:
        errors.append(f"{path}.candidate_metadata_shortlist: catalog comparison requires at least three candidates")
    if terminal_pass and selection_mode == "compared_catalog_slate":
        for index, row in enumerate(shortlist or []):
            provenance = row.get("music_provenance") if isinstance(row, dict) else None
            if not (
                isinstance(provenance, dict)
                and provenance.get("license_verification_status") == "provider_metadata_exposed"
                and provenance.get("commercial_use_allowed") is True
                and provenance.get("derivatives_allowed") is True
                and isinstance(provenance.get("attribution_required"), bool)
            ):
                errors.append(
                    f"{path}.candidate_metadata_shortlist[{index}].music_provenance: accepted catalog slate candidates must all be commercially usable and editable"
                )
            if isinstance(row, dict) and isinstance(provenance, dict):
                if row.get("candidate_id") != provenance.get("provider_candidate_id"):
                    errors.append(
                        f"{path}.candidate_metadata_shortlist[{index}].candidate_id: "
                        "must equal provider_candidate_id"
                    )
    if selection_mode == "user_supplied_exact" and len(shortlist or []) != 1:
        errors.append(f"{path}.candidate_metadata_shortlist: user-supplied exact selection requires one candidate")
    if selection_mode == "user_supplied_exact":
        evidence_by_id = {
            row.get("evidence_id"): row
            for row in identity.get("evidence", [])
            if isinstance(row, dict)
        }
        supplied_ids = set((shortlist or [{}])[0].get("identity_evidence_ids", [])) if shortlist else set()
        if not any(
            isinstance(evidence_by_id.get(evidence_id), dict)
            and evidence_by_id[evidence_id].get("source") == "upload_manifest"
            for evidence_id in supplied_ids
        ):
            errors.append(
                f"{path}.candidate_metadata_shortlist: user-supplied exact selection requires upload-manifest identity evidence"
            )
        if shortlist and not isinstance(shortlist[0].get("user_supplied_license_evidence"), dict):
            errors.append(
                f"{path}.candidate_metadata_shortlist: user-supplied exact selection requires explicit license evidence"
            )

    if len(selected_rows) != 1:
        errors.append(f"{path}.candidate_metadata_shortlist: music policy requires exactly one selected candidate")
    elif isinstance(identity, dict):
        selected = selected_rows[0]
        if selected.get("title") != identity.get("title") or selected.get("artist") != identity.get("artist"):
            errors.append(
                f"{path}.candidate_metadata_shortlist: selected title/artist must match music_identity"
            )
        if selected.get("candidate_id") != edl.get("track_stable_id"):
            errors.append(
                f"{path}.edl_facts.track_stable_id: must equal the selected shortlist candidate_id"
            )
        if selection_mode == "compared_catalog_slate":
            selected_provenance = selected.get("music_provenance")
            provider_candidate_id = (
                selected_provenance.get("provider_candidate_id")
                if isinstance(selected_provenance, dict)
                else None
            )
            if selected.get("candidate_id") != provider_candidate_id:
                errors.append(
                    f"{path}.candidate_metadata_shortlist: selected catalog candidate_id "
                    "must equal provider_candidate_id"
                )
        elif selection_mode == "user_supplied_exact":
            supplied = selected.get("user_supplied_license_evidence")
            media_sha = supplied.get("media_sha256") if isinstance(supplied, dict) else None
            if _normalized_sha256(selected.get("candidate_id")) != _normalized_sha256(media_sha):
                errors.append(
                    f"{path}.candidate_metadata_shortlist: user-supplied candidate_id "
                    "must equal the licensed media content SHA-256"
                )
        for field in ("music_provenance", "user_supplied_license_evidence"):
            if selected.get(field) != edl.get(field):
                errors.append(
                    f"{path}.edl_facts.{field}: must match the selected catalog metadata row"
                )
    assigned_start, assigned_end = music.get("assigned_start_s"), music.get("assigned_end_s")
    actual_start, actual_end = edl.get("output_start_s"), edl.get("output_end_s")
    errors.extend(_interval_errors([assigned_start, assigned_end], f"{path}.assigned_range", duration))
    errors.extend(_interval_errors([actual_start, actual_end], f"{path}.edl_facts.output_range", duration))
    entry_policy = music.get("entry_policy")
    if entry_policy in {"delayed", "payoff_only"} and isinstance(assigned_start, (int, float)):
        if assigned_start <= EPSILON:
            errors.append(f"{path}.assigned_start_s: delayed/payoff-only music must start after 0")
    if entry_policy in {"hook", "whole_program"} and assigned_start != 0:
        errors.append(f"{path}.assigned_start_s: hook/whole-program music must start at 0")
    if terminal_pass and all(
        isinstance(value, (int, float))
        for value in (assigned_start, assigned_end, actual_start, actual_end)
    ):
        if not math.isclose(float(assigned_start), float(actual_start), abs_tol=EPSILON):
            errors.append(f"{path}.edl_facts.output_start_s: must match assigned_start_s")
        if not math.isclose(float(assigned_end), float(actual_end), abs_tol=EPSILON):
            errors.append(f"{path}.edl_facts.output_end_s: must match assigned_end_s")
        if duration is not None and not math.isclose(float(actual_end), duration, abs_tol=EPSILON):
            errors.append(f"{path}.edl_facts.output_end_s: accepted music must reach program end")
        if identity.get("status") != "identified":
            errors.append(f"{path}.music_identity.status: accepted music must be reliably identified")
        provenance = edl.get("music_provenance")
        user_license = edl.get("user_supplied_license_evidence")
        if selection_mode == "compared_catalog_slate":
            rights_ok = (
                isinstance(provenance, dict)
                and provenance.get("license_verification_status") == "provider_metadata_exposed"
                and provenance.get("commercial_use_allowed") is True
                and provenance.get("derivatives_allowed") is True
                and isinstance(provenance.get("attribution_required"), bool)
                and isinstance(provenance.get("provider"), str)
                and isinstance(provenance.get("creator"), str)
                and bool(
                    provenance.get("provider_reported_license_id")
                    or provenance.get("provider_reported_license_label")
                )
                and provenance.get("source_audio_stream_status") == "complete"
                and provenance.get("source_has_audio_stream") is True
                and _normalized_sha256(provenance.get("downloaded_sha256")) is not None
                and user_license is None
            )
        else:
            rights_ok = (
                isinstance(user_license, dict)
                and user_license.get("commercial_use_allowed") is True
                and user_license.get("derivatives_allowed") is True
                and provenance is None
            )
        if not rights_ok:
            errors.append(f"{path}.edl_facts: accepted music requires commercial and derivative-use rights evidence")
        if isinstance(provenance, dict) and edl.get("source_has_audio_stream") != provenance.get("source_has_audio_stream"):
            errors.append(f"{path}.edl_facts.source_has_audio_stream: must match fetched music provenance")
        attribution_required = (
            provenance.get("attribution_required")
            if isinstance(provenance, dict)
            else user_license.get("attribution_required")
            if isinstance(user_license, dict)
            else None
        )
        if attribution_required is True and not (
            isinstance(edl.get("attribution"), str) and edl["attribution"].strip()
        ):
            errors.append(f"{path}.edl_facts.attribution: required attribution must be persisted")
        if analysis.get("status") != "available":
            errors.append(
                f"{path}.selected_track_analysis.status: accepted music requires deterministic selected-track facts"
            )
        elif analysis.get("evidence_id") not in cited:
            errors.append(
                f"{path}.music_fit_inference.evidence_ids: accepted music fit must cite selected-track analysis"
            )
        if inference.get("result") != "fit":
            errors.append(f"{path}.music_fit_inference.result: accepted music requires Codex inference fit")
    if identity.get("status") in {"not_identified", "ambiguous"}:
        confidence = inference.get("confidence")
        if isinstance(confidence, (int, float)) and confidence > 0.5 + EPSILON:
            errors.append(f"{path}.music_fit_inference.confidence: unidentified/ambiguous identity is capped at 0.5")
    offset, source_duration = edl.get("source_offset_s"), edl.get("source_duration_s")
    if isinstance(offset, (int, float)) and isinstance(source_duration, (int, float)):
        if offset >= source_duration:
            errors.append(f"{path}.source_offset_s: must be inside the music asset")
    if edl.get("loop") is False and all(
        isinstance(value, (int, float))
        for value in (actual_start, actual_end, offset, source_duration)
    ):
        if source_duration - offset + EPSILON < actual_end - actual_start:
            errors.append(f"{path}: unlooped source does not cover the actual output interval")
    if isinstance(source_duration, (int, float)):
        for field in (
            "energy_loudest_time_s",
            "energy_quietest_time_s",
            "largest_rise_end_s",
        ):
            value = analysis.get(field)
            if isinstance(value, (int, float)) and value > float(source_duration) + EPSILON:
                errors.append(f"{path}.selected_track_analysis.{field}: exceeds track source duration")
        beat_times = analysis.get("beat_time_sample_s", [])
        if isinstance(beat_times, list) and all(isinstance(value, (int, float)) for value in beat_times):
            if any(right <= left + EPSILON for left, right in zip(beat_times, beat_times[1:])):
                errors.append(f"{path}.selected_track_analysis.beat_time_sample_s: must be strictly increasing")
            if any(float(value) > float(source_duration) + EPSILON for value in beat_times):
                errors.append(f"{path}.selected_track_analysis.beat_time_sample_s: exceeds track source duration")
            beat_count = analysis.get("beat_count")
            if isinstance(beat_count, int) and len(beat_times) != min(8, beat_count):
                errors.append(f"{path}.selected_track_analysis.beat_time_sample_s: must contain the first min(8, beat_count) beats")
    if analysis.get("status") == "available":
        if analysis.get("tempo_status") == "detected" and not all(
            isinstance(analysis.get(field), (int, float))
            for field in ("bpm", "bpm_confidence")
        ):
            errors.append(
                f"{path}.selected_track_analysis: detected tempo requires BPM and confidence"
            )
        if not all(
            isinstance(analysis.get(field), (int, float))
            for field in (
                "energy_loudest_time_s",
                "energy_quietest_time_s",
                "energy_quietest_db_below_peak",
            )
        ):
            errors.append(
                f"{path}.selected_track_analysis: available analysis requires exposed energy extrema facts"
            )
    if isinstance(mix.get("program_duration_s"), (int, float)) and duration is not None:
        if not math.isclose(float(mix["program_duration_s"]), duration, abs_tol=EPSILON):
            errors.append(f"{path}.mix_measurements.program_duration_s: must match program duration")
    if terminal_pass:
        if mix.get("status") != "complete":
            errors.append(f"{path}.mix_measurements.status: accepted result requires complete deterministic QC")
        for field in ("integrated_lufs", "true_peak_dbtp", "loudness_range_lu"):
            if not isinstance(mix.get(field), (int, float)):
                errors.append(f"{path}.mix_measurements.{field}: accepted result requires a measured value")
        if mix.get("findings") or mix.get("warnings"):
            errors.append(f"{path}.mix_measurements: accepted result cannot retain unresolved findings or warnings")
    for index, interval in enumerate(mix.get("silences", [])):
        errors.extend(_interval_errors(interval, f"{path}.mix_measurements.silences[{index}]", duration))
    return errors


def _cta_fact_errors(
    audio: dict[str, Any], cta: Any, path: str, *, terminal_pass: bool
) -> list[str]:
    if not isinstance(cta, dict):
        return []
    errors: list[str] = []
    edl = audio.get("edl_facts", {}) if isinstance(audio, dict) else {}
    policy = audio.get("music_policy") if isinstance(audio, dict) else None
    if policy == "none":
        if cta.get("tail_eligible") is not False or cta.get("tail_configured") is not False:
            errors.append(f"{path}: no-music policy cannot claim a configured music tail")
        for field in (
            "tail_music_item_id",
            "tail_music_asset_id",
            "tail_music_muted",
            "tail_music_gain_db",
            "music_output_start_s",
            "music_output_end_s",
            "music_source_offset_s",
            "music_source_duration_s",
            "music_source_has_audio_stream",
            "music_loop",
            "tail_music_item_fade_out_s",
        ):
            if cta.get(field) is not None:
                errors.append(f"{path}.{field}: no-music policy requires null")
        return errors
    program_duration = cta.get("program_duration_s")
    cta_duration = cta.get("cta_duration_s")
    output_start = cta.get("music_output_start_s")
    output_end = cta.get("music_output_end_s")
    source_offset = cta.get("music_source_offset_s")
    source_duration = cta.get("music_source_duration_s")
    loop = cta.get("music_loop")
    numeric = all(
        isinstance(value, (int, float))
        for value in (
            program_duration,
            cta_duration,
            output_start,
            output_end,
            source_offset,
            source_duration,
        )
    )
    source_viable = False
    overlaps_final_program_frame = False
    if numeric:
        authored_program_span = float(output_end) - float(output_start)
        required_source_consumption = (
            float(program_duration) - float(output_start) + float(cta_duration)
        )
        source_remaining = float(source_duration) - float(source_offset)
        source_viable = (
            cta.get("music_source_has_audio_stream") is True
            and float(source_duration) > EPSILON
            and float(source_offset) + EPSILON < float(source_duration)
            and authored_program_span > EPSILON
            and required_source_consumption > EPSILON
            and (loop is True or source_remaining + EPSILON >= required_source_consumption)
        )
        overlaps_final_program_frame = (
            float(output_start) < float(program_duration) - EPSILON
            and math.isclose(
                float(output_end),
                float(program_duration),
                abs_tol=EPSILON,
            )
        )
    derived = (
        numeric
        and edl.get("music_item_present") is True
        and cta.get("tail_music_item_id") == edl.get("music_item_id")
        and cta.get("tail_music_asset_id") == edl.get("music_asset_id")
        and cta.get("tail_music_muted") is False
        and cta.get("tail_music_gain_db") == edl.get("gain_db")
        and cta.get("music_output_start_s") == edl.get("output_start_s")
        and cta.get("music_output_end_s") == edl.get("output_end_s")
        and cta.get("music_source_offset_s") == edl.get("source_offset_s")
        and cta.get("music_source_duration_s") == edl.get("source_duration_s")
        and cta.get("music_source_has_audio_stream") == edl.get("source_has_audio_stream")
        and cta.get("music_loop") == edl.get("loop")
        and source_viable
        and overlaps_final_program_frame
        and cta.get("tail_music_item_fade_out_s") == 0
        and cta.get("whole_program_fade_present") is False
        and cta.get("tail_overlapping_music_item_count") == 1
        and cta.get("duplicated_in_edl") is False
        and cta.get("preview_omits_cta") is True
        and cta.get("renderer_outro_contract_version") == "valmera-cta-tail-v1"
    )
    if cta.get("tail_eligible") is not derived:
        errors.append(f"{path}.tail_eligible: must be derived from persisted EDL tail facts")
    if terminal_pass and (not derived or cta.get("tail_configured") is not True):
        errors.append(f"{path}: accepted music requires an eligible and configured final-export tail")
    return errors


def _editor_result_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    duration = data.get("editorial_duration_s")
    status = data.get("status")
    visual_evidence = data.get("render_visual_inspection")
    transcript = data.get("program_speech_transcript")
    render_exceptions = _unplayable_render_rows(data.get("issues"))
    ordinary_evidence_gate = status == "ready" or not render_exceptions
    errors.extend(
        _visual_inspection_errors(
            visual_evidence,
            "$.render_visual_inspection",
            duration,
            require_complete=ordinary_evidence_gate,
            max_allowed_gap_s=1.0 if ordinary_evidence_gate else None,
        )
    )
    errors.extend(
        _program_speech_transcript_errors(
            transcript,
            "$.program_speech_transcript",
            duration,
            require_complete=ordinary_evidence_gate,
        )
    )
    if isinstance(transcript, dict):
        if transcript.get("render_edl_version") != data.get("preview_edl_version"):
            errors.append("$.program_speech_transcript.render_edl_version: must bind the preview EDL")
        word_coverage = transcript.get("caption_word_coverage")
        if isinstance(word_coverage, dict):
            kept = word_coverage.get("transcribed_word_count")
            captioned = word_coverage.get("captioned_word_count")
            uncovered = word_coverage.get("uncovered_word_count")
            if all(isinstance(value, int) for value in (kept, captioned, uncovered)):
                if captioned + uncovered != kept:
                    errors.append("$.program_speech_transcript.caption_word_coverage: captioned + uncovered must equal kept words")
    provenance = data.get("preview_render_provenance")
    if isinstance(provenance, dict):
        receipt = provenance.get("preview_receipt", {})
        retrieval = provenance.get("retrieval", {})
        if receipt.get("edl_version") != data.get("preview_edl_version"):
            errors.append("$.preview_render_provenance.edl_version: must bind current preview EDL")
        if all(isinstance(value, (int, float)) for value in (receipt.get("duration_s"), duration)):
            if not math.isclose(float(receipt["duration_s"]), float(duration), abs_tol=EPSILON):
                errors.append("$.preview_render_provenance.duration_s: must match editorial duration")
        if isinstance(transcript, dict):
            retrieved_at = _parse_datetime(retrieval.get("retrieved_at"))
            completed_at = _parse_datetime(transcript.get("completed_at"))
            if retrieved_at is not None and completed_at is not None and completed_at < retrieved_at:
                errors.append("$.program_speech_transcript.completed_at: ASR cannot precede preview retrieval")
    if ordinary_evidence_gate:
        pagination = data.get("evidence_pagination")
        if not isinstance(pagination, dict) or any(value is not True for value in pagination.values()):
            errors.append("$.evidence_pagination: ready/ordinary repair evidence must exhaust every page")
    elif isinstance(visual_evidence, dict):
        gaps = visual_evidence.get("gaps")
        if not gaps:
            errors.append("$.render_visual_inspection.gaps: corrupt/unplayable exception requires truthful gaps")
        exception_ranges = [
            [float(row["start_s"]), float(row["end_s"])]
            for row in render_exceptions
            if isinstance(row.get("start_s"), (int, float))
            and isinstance(row.get("end_s"), (int, float))
            and row["end_s"] > row["start_s"]
        ]
        if not _interval_lists_match(_merged_intervals(exception_ranges), gaps):
            errors.append("$.issues: corrupt/unplayable intervals must equal visual inspection gaps")
    if data.get("status") == "ready":
        if data.get("final_edl_version") != data.get("preview_edl_version"):
            errors.append("$: ready result requires final_edl_version == preview_edl_version")
        if isinstance(data.get("starting_edl_version"), int) and isinstance(data.get("final_edl_version"), int):
            if data["final_edl_version"] < data["starting_edl_version"]:
                errors.append("$.final_edl_version: cannot precede starting_edl_version")

    visuals = data.get("visuals")
    if isinstance(visuals, dict):
        errors.extend(
            _visual_provenance_errors(
                visuals,
                float(duration) if isinstance(duration, (int, float)) else None,
                "$.visuals",
                check_provider_counts=True,
                terminal_pass=data.get("status") == "ready",
            )
        )

    audio = data.get("audio")
    if isinstance(audio, dict):
        errors.extend(
            _music_timing_errors(
                audio,
                float(duration) if isinstance(duration, (int, float)) else None,
                "$.audio",
                terminal_pass=data.get("status") == "ready",
            )
        )
        cta = data.get("cta")
        if isinstance(cta, dict) and all(
            isinstance(value, (int, float))
            for value in (cta.get("program_duration_s"), duration)
        ) and not math.isclose(float(cta["program_duration_s"]), float(duration), abs_tol=EPSILON):
            errors.append("$.cta.program_duration_s: must match editorial duration")
        errors.extend(
            _cta_fact_errors(
                audio,
                data.get("cta"),
                "$.cta",
                terminal_pass=data.get("status") == "ready",
            )
        )
    for index, issue in enumerate(data.get("issues", [])):
        if isinstance(issue, dict):
            errors.extend(
                _interval_errors(
                    [issue.get("start_s"), issue.get("end_s")],
                    f"$.issues[{index}]",
                    float(duration) if isinstance(duration, (int, float)) else None,
                )
            )
    try:
        expected_fingerprint = _canonical_fingerprint("editor-result", data)
    except (TypeError, ValueError) as exc:
        errors.append(f"$.result_fingerprint: canonicalization failed: {exc}")
    else:
        if data.get("result_fingerprint") != expected_fingerprint:
            errors.append(
                "$.result_fingerprint: does not match canonical editor-result payload "
                f"(expected {expected_fingerprint})"
            )
    return errors


def _parent_qc_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    status = data.get("status")
    provenance = data.get("preview_render_provenance")
    receipt = provenance.get("preview_receipt", {}) if isinstance(provenance, dict) else {}
    retrieval = provenance.get("retrieval", {}) if isinstance(provenance, dict) else {}
    duration = receipt.get("duration_s")
    visual = data.get("render_visual_inspection")
    transcript = data.get("program_speech_transcript")
    violations = data.get("violations")
    render_exceptions = _unplayable_render_rows(violations)
    transcript_exception = any(
        isinstance(row, dict) and row.get("reason_code") == "transcript_unavailable"
        for row in violations or []
    )
    ordinary_visual_gate = status == "pass" or not render_exceptions
    ordinary_transcript_gate = status == "pass" or not transcript_exception
    errors.extend(
        _visual_inspection_errors(
            visual,
            "$.render_visual_inspection",
            duration,
            require_complete=ordinary_visual_gate,
            max_allowed_gap_s=1.0 if ordinary_visual_gate else None,
        )
    )
    errors.extend(
        _program_speech_transcript_errors(
            transcript,
            "$.program_speech_transcript",
            duration,
            require_complete=ordinary_transcript_gate,
        )
    )
    if isinstance(provenance, dict):
        if receipt.get("edl_version") != data.get("preview_edl_version"):
            errors.append("$.preview_render_provenance.edl_version: must bind the parent preview EDL")
        if isinstance(transcript, dict):
            retrieved_at = _parse_datetime(retrieval.get("retrieved_at"))
            completed_at = _parse_datetime(transcript.get("completed_at"))
            if retrieved_at is not None and completed_at is not None and completed_at < retrieved_at:
                errors.append("$.program_speech_transcript.completed_at: parent ASR cannot precede preview retrieval")
    if isinstance(transcript, dict):
        if transcript.get("render_edl_version") != data.get("preview_edl_version"):
            errors.append("$.program_speech_transcript.render_edl_version: must bind the parent preview EDL")
        counts = transcript.get("caption_word_coverage")
        if isinstance(counts, dict) and all(
            isinstance(counts.get(field), int)
            for field in ("transcribed_word_count", "captioned_word_count", "uncovered_word_count")
        ):
            if counts["captioned_word_count"] + counts["uncovered_word_count"] != counts["transcribed_word_count"]:
                errors.append("$.program_speech_transcript.caption_word_coverage: captioned + uncovered must equal transcribed words")
    if render_exceptions and isinstance(visual, dict):
        expected_ranges = [
            [float(row["start_s"]), float(row["end_s"])]
            for row in render_exceptions
            if isinstance(row.get("start_s"), (int, float))
            and isinstance(row.get("end_s"), (int, float))
            and row["end_s"] > row["start_s"]
        ]
        if not _interval_lists_match(_merged_intervals(expected_ranges), visual.get("gaps")):
            errors.append("$.violations: corrupt/unplayable ranges must equal visual evidence gaps")
    if not render_exceptions and not transcript_exception:
        pagination = data.get("evidence_pagination")
        qc_evidence = data.get("qc_evidence")
        if not isinstance(pagination, dict) or any(value is not True for value in pagination.values()):
            errors.append("$.evidence_pagination: ordinary parent QC must exhaust every page")
        if not isinstance(qc_evidence, dict) or any(value is not True for value in qc_evidence.values()):
            errors.append("$.qc_evidence: ordinary parent QC must complete every factual check")
    if data.get("status") == "pass" and data.get("live_edl_version") != data.get("preview_edl_version"):
        errors.append("$: pass requires live_edl_version == preview_edl_version")
    broll = data.get("broll")
    if isinstance(broll, dict):
        errors.extend(
            _visual_provenance_errors(
                broll,
                float(duration) if isinstance(duration, (int, float)) else None,
                "$.broll",
                check_provider_counts=False,
                terminal_pass=data.get("status") == "pass",
            )
        )
    music = data.get("music")
    if isinstance(music, dict):
        errors.extend(
            _music_timing_errors(
                music,
                float(duration) if isinstance(duration, (int, float)) else None,
                "$.music",
                terminal_pass=data.get("status") == "pass",
            )
        )
        errors.extend(
            _cta_fact_errors(
                music,
                data.get("cta"),
                "$.cta",
                terminal_pass=data.get("status") == "pass",
            )
        )
        cta = data.get("cta")
        if isinstance(cta, dict) and all(
            isinstance(value, (int, float))
            for value in (cta.get("program_duration_s"), duration)
        ) and not math.isclose(float(cta["program_duration_s"]), float(duration), abs_tol=EPSILON):
            errors.append("$.cta.program_duration_s: must match parent preview duration")
        inference = music.get("music_fit_inference")
        if isinstance(inference, dict):
            digest_payload = {
                "preview_render_provenance": provenance,
                "render_visual_inspection": visual,
                "program_speech_transcript": transcript,
                "music_identity": music.get("music_identity"),
                "candidate_metadata_shortlist": music.get("candidate_metadata_shortlist"),
                "selected_track_analysis": music.get("selected_track_analysis"),
                "edl_facts": music.get("edl_facts"),
                "mix_measurements": music.get("mix_measurements"),
                "inference": {
                    key: value
                    for key, value in inference.items()
                    if key != "independent_evidence_digest"
                },
            }
            expected_digest = _canonical_object_digest(digest_payload)
            if _normalized_sha256(inference.get("independent_evidence_digest")) != _normalized_sha256(expected_digest):
                errors.append("$.music.music_fit_inference.independent_evidence_digest: must digest the parent's exact independent evidence")
    for index, violation in enumerate(data.get("violations", [])):
        if isinstance(violation, dict):
            errors.extend(
                _interval_errors(
                    [violation.get("start_s"), violation.get("end_s")],
                    f"$.violations[{index}]",
                    float(duration) if isinstance(duration, (int, float)) else None,
                )
            )
    try:
        expected_qc_fingerprint = _canonical_fingerprint("parent-qc", data)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"$.qc_fingerprint: canonicalization failed: {exc}")
    else:
        if data.get("qc_fingerprint") != expected_qc_fingerprint:
            errors.append(
                "$.qc_fingerprint: does not match canonical parent-QC payload "
                f"(expected {expected_qc_fingerprint})"
            )
    return errors


def _youtube_url_id(record: dict[str, Any]) -> str | None:
    url = record.get("canonical_url")
    if isinstance(url, str) and "?v=" in url:
        return url.rsplit("?v=", 1)[1]
    return None


def _acquisition_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    source = data.get("source")
    reference = data.get("reference")
    if isinstance(source, dict):
        errors.extend(
            _visual_inspection_errors(
                source.get("source_visual_inspection"),
                "$.source.source_visual_inspection",
                source.get("duration_s"),
                require_complete=True,
                max_allowed_gap_s=None,
            )
        )
        errors.extend(
            _processed_transcript_errors(
                source.get("source_speech_transcript"),
                "$.source.source_speech_transcript",
                source.get("duration_s"),
                require_complete=True,
                coverage_field="coverage",
                gap_field="gaps",
            )
        )
        for key in ("source_visual_inspection", "source_speech_transcript"):
            evidence = source.get(key)
            if isinstance(evidence, dict) and _normalized_sha256(evidence.get("media_sha256")) != _normalized_sha256(source.get("sha256")):
                errors.append(f"$.source.{key}.media_sha256: must match acquired source SHA-256")
        if _youtube_url_id(source) != source.get("youtube_video_id"):
            errors.append("$.source.canonical_url: video ID must match source.youtube_video_id")
    if isinstance(reference, dict):
        if _youtube_url_id(reference) != reference.get("youtube_video_id"):
            errors.append("$.reference.canonical_url: video ID must match reference.youtube_video_id")
        if isinstance(source, dict):
            if source.get("youtube_video_id") == reference.get("youtube_video_id"):
                errors.append("$.reference.youtube_video_id: source and reference must be different videos")
            if source.get("ledger_instance_id") != reference.get("ledger_instance_id"):
                errors.append("$.reference.ledger_instance_id: source and reference must use the same ledger")
            if source.get("asset_id") == reference.get("asset_id"):
                errors.append("$.reference.asset_id: source and reference assets must differ")
        for field in ("preselection_duration_s", "postupload_duration_s"):
            value = reference.get(field)
            if all(isinstance(item, (int, float)) for item in (value, reference.get("duration_s"))):
                if not math.isclose(float(value), float(reference["duration_s"]), abs_tol=EPSILON):
                    errors.append(f"$.reference.{field}: must match acquired reference duration")
        if _normalized_sha256(reference.get("postupload_media_sha256")) != _normalized_sha256(reference.get("sha256")):
            errors.append("$.reference.postupload_media_sha256: must match acquired reference SHA-256")
        posttranscript = reference.get("postupload_speech_transcript")
        if isinstance(posttranscript, dict):
            if posttranscript.get("parent_reference_asset_id") != reference.get("asset_id"):
                errors.append("$.reference.postupload_speech_transcript.parent_reference_asset_id: must match acquired asset")
            if _normalized_sha256(posttranscript.get("media_sha256")) != _normalized_sha256(reference.get("sha256")):
                errors.append("$.reference.postupload_speech_transcript.media_sha256: must match acquired reference SHA-256")
    return errors


def _coordinator_result_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    rows = data.get("arc_accounting")
    if not isinstance(rows, list):
        return errors
    selected = data.get("selected_arc_count")
    accounted = data.get("accounted_arc_count")
    if selected != len(rows):
        errors.append("$.selected_arc_count: must equal arc_accounting length")
    if accounted != len(rows):
        errors.append("$.accounted_arc_count: must equal arc_accounting length")

    arc_ids: list[str] = []
    ranks: list[int] = []
    child_ids: list[int] = []
    ranges: list[tuple[float, float, int]] = []
    status_counts = {"generated": 0, "pending": 0, "failed": 0}
    ready_count = blocked_count = 0
    nonterminal_generated_count = 0
    terminal_status = data.get("status") in {
        "ready_for_studio_export",
        "partial",
        "blocked",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("arc_id"), str):
            arc_ids.append(row["arc_id"])
        if isinstance(row.get("selection_rank"), int):
            ranks.append(row["selection_rank"])
        generation_status = row.get("generation_status")
        if generation_status in status_counts:
            status_counts[generation_status] += 1
        child_id = row.get("child_project_id")
        if isinstance(child_id, int):
            child_ids.append(child_id)
        ready_count += row.get("editor_status") == "ready" and row.get("parent_qc_status") == "pass"
        blocked_count += row.get("editor_status") == "blocked" or row.get("parent_qc_status") == "blocked"
        if generation_status == "generated" and (
            row.get("editor_status"), row.get("parent_qc_status")
        ) not in {("ready", "pass"), ("blocked", "blocked")}:
            nonterminal_generated_count += 1
        if generation_status == "generated" and terminal_status:
            terminal_pair = (row.get("editor_status"), row.get("parent_qc_status"))
            if terminal_pair not in {("ready", "pass"), ("blocked", "blocked")}:
                errors.append(
                    f"$.arc_accounting[{index}]: terminal generated row must be "
                    "editor ready/parent-QC pass or editor blocked/parent-QC blocked"
                )
            if terminal_pair == ("blocked", "blocked"):
                job_id = row.get("generation_job_id")
                if not (
                    (isinstance(job_id, str) and bool(job_id))
                    or (
                        isinstance(job_id, int)
                        and not isinstance(job_id, bool)
                        and job_id >= 1
                    )
                ):
                    errors.append(
                        f"$.arc_accounting[{index}].generation_job_id: "
                        "terminal blocked row requires a stable nonempty job ID"
                    )
                for field in ("treatment_name", "reference_adaptation_summary"):
                    if not isinstance(row.get(field), str) or not row[field]:
                        errors.append(
                            f"$.arc_accounting[{index}].{field}: "
                            "terminal blocked row requires nonempty evidence"
                        )
                if not row.get("failed_gates"):
                    errors.append(
                        f"$.arc_accounting[{index}].failed_gates: "
                        "terminal blocked row requires at least one failed gate"
                    )
        if (
            generation_status == "generated"
            and row.get("editor_status") == "ready"
            and row.get("parent_qc_status") == "pass"
        ):
            job_id = row.get("generation_job_id")
            if not (
                (isinstance(job_id, str) and bool(job_id))
                or (isinstance(job_id, int) and not isinstance(job_id, bool) and job_id >= 1)
            ):
                errors.append(
                    f"$.arc_accounting[{index}].generation_job_id: "
                    "ready/pass row requires a stable nonempty job ID"
                )
            live_version = row.get("live_edl_version")
            preview_version = row.get("preview_edl_version")
            if not isinstance(live_version, int) or not isinstance(preview_version, int):
                errors.append(
                    f"$.arc_accounting[{index}]: ready/pass row requires live and preview EDL versions"
                )
            elif live_version != preview_version:
                errors.append(
                    f"$.arc_accounting[{index}]: ready/pass live and preview EDL versions must match"
                )
            for field in ("treatment_name", "reference_adaptation_summary"):
                if not isinstance(row.get(field), str) or not row[field]:
                    errors.append(
                        f"$.arc_accounting[{index}].{field}: ready/pass row requires nonempty evidence"
                    )
        start, end = row.get("start_s"), row.get("end_s")
        errors.extend(_interval_errors([start, end], f"$.arc_accounting[{index}].range"))
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            arc_duration = float(end) - float(start)
            if arc_duration < 10 - EPSILON or arc_duration > 120 + EPSILON:
                errors.append(f"$.arc_accounting[{index}]: selected arc must be between 10 and 120 seconds")
            ranges.append((float(start), float(end), index))

    if len(arc_ids) != len(set(arc_ids)):
        errors.append("$.arc_accounting: arc_id values must be unique")
    if len(ranks) != len(set(ranks)):
        errors.append("$.arc_accounting: selection_rank values must be unique")
    if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
        errors.append("$.arc_accounting: selection ranks must be contiguous from 1")
    if len(child_ids) != len(set(child_ids)):
        errors.append("$.arc_accounting: generated child_project_id values must be unique")
    for previous, current in zip(sorted(ranges), sorted(ranges)[1:]):
        if current[0] < previous[1] - EPSILON:
            errors.append(f"$.arc_accounting[{current[2]}]: overlaps selected arc at row {previous[2]}")

    expected = {
        "generated_count": status_counts["generated"],
        "pending_count": status_counts["pending"],
        "failed_generation_count": status_counts["failed"],
        "ready_count": int(ready_count),
        "blocked_count": int(blocked_count),
    }
    for field, value in expected.items():
        if data.get(field) != value:
            errors.append(f"$.{field}: expected {value} from arc_accounting")
    if selected != sum(status_counts.values()):
        errors.append("$: every selected arc must be exactly one of generated, pending, or failed")
    if terminal_status and int(ready_count) + int(blocked_count) != status_counts["generated"]:
        errors.append(
            "$: every generated row in a terminal result must be exactly ready/pass or "
            "blocked/blocked"
        )
    status = data.get("status")
    blocked_phase = data.get("blocked_phase")
    if status == "ready_for_studio_export":
        if not (
            isinstance(selected, int)
            and selected > 0
            and status_counts["generated"] == selected
            and int(ready_count) == selected
            and status_counts["pending"] == 0
            and status_counts["failed"] == 0
            and int(blocked_count) == 0
        ):
            errors.append(
                "$: ready_for_studio_export requires every selected arc to be generated, "
                "editor ready, and parent-QC pass"
            )
    if status == "partial":
        if not (
            status_counts["pending"] == 0
            and nonterminal_generated_count == 0
            and int(ready_count) >= 1
            and (status_counts["failed"] >= 1 or int(blocked_count) >= 1)
        ):
            errors.append(
                "$: partial requires at least one ready/pass arc, at least one terminal "
                "failed-or-blocked arc, zero pending arcs, and no active generated child"
            )
    if status == "blocked" and blocked_phase == "reference":
        if not (
            status_counts["pending"] == selected
            and status_counts["generated"] == 0
            and status_counts["failed"] == 0
        ):
            errors.append("$: reference block requires every selected arc to remain pending")
    if status == "blocked" and blocked_phase == "acquisition_record":
        if not (
            status_counts["pending"] == selected
            and status_counts["generated"] == 0
            and status_counts["failed"] == 0
        ):
            errors.append(
                "$: acquisition_record block requires every selected arc to remain pending"
            )
    if status == "blocked" and blocked_phase == "materialization":
        if not (
            status_counts["failed"] == selected
            and status_counts["generated"] == 0
            and status_counts["pending"] == 0
        ):
            errors.append(
                "$: materialization block requires every selected arc to fail generation"
            )
    if status == "blocked" and blocked_phase == "child_qc":
        if not (
            status_counts["generated"] >= 1
            and status_counts["pending"] == 0
            and int(ready_count) == 0
            and int(blocked_count) == status_counts["generated"]
            and status_counts["generated"] + status_counts["failed"] == selected
            and nonterminal_generated_count == 0
        ):
            errors.append(
                "$: child_qc terminal block requires at least one generated-and-blocked arc, "
                "allows only failed-generation siblings, and permits no ready, pending, or active child"
            )
    if status == "blocked" and int(ready_count) > 0:
        errors.append(
            "$: blocked cannot contain ready/pass arcs; mixed ready and failed/blocked "
            "outcomes must use partial"
        )
    if status == "in_progress" and not (
        status_counts["pending"] > 0 or nonterminal_generated_count > 0
    ):
        errors.append(
            "$: in_progress requires a pending generation or a nonterminal generated child"
        )
    if status == "partial" and isinstance(selected, int) and selected > 0:
        if int(ready_count) == selected:
            errors.append(
                "$: partial cannot contain only ready/pass arcs; use ready_for_studio_export"
            )
        if status_counts["generated"] == selected and int(blocked_count) == selected:
            errors.append(
                "$: partial cannot contain only generated-and-blocked arcs; use "
                "blocked with blocked_phase=child_qc"
            )
    return errors


SEMANTIC_VALIDATORS = {
    "selection": _selection_errors,
    "reference-profile": _reference_profile_errors,
    "child-assignment": _child_assignment_errors,
    "pre-mutation-recast": _recast_errors,
    "editor-result": _editor_result_errors,
    "parent-qc": _parent_qc_errors,
    "acquisition-record": _acquisition_errors,
    "coordinator-run-result": _coordinator_result_errors,
}


def artifact_type(data: dict[str, Any]) -> str | None:
    """Identify a contract artifact from its stable discriminator fields."""
    if data.get("schema_version") == "valmera-topic-acquisition-v2":
        return "acquisition-record"
    if data.get("schema_version") == "valmera-pre-mutation-recast-v2":
        return "pre-mutation-recast"
    if data.get("schema_version") == "valmera-coordinator-run-result-v2":
        return "coordinator-run-result"
    if (
        data.get("schema_version") == "2"
        and data.get("selected_by") == "coordinator"
        and "clips" in data
    ):
        return "selection"
    if data.get("reference_profile_version") == "2" and "observations" in data:
        return "reference-profile"
    if data.get("assignment_schema_version") == "valmera-editorial-child-assignment-v2":
        if "assignment_status" in data:
            return "child-assignment"
        if data.get("result_schema_version") == "valmera-child-editor-result-v2":
            return "editor-result"
        if data.get("qc_schema_version") == "valmera-parent-qc-v2":
            return "parent-qc"
    return None


def _normalized_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.removeprefix("sha256:").lower()


def _selection_acquisition_errors(
    selection: dict[str, Any], acquisition: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    source = acquisition.get("source")
    if not isinstance(source, dict):
        return ["$lineage: acquisition source record is missing"]
    comparisons = (
        ("parent project", selection.get("parent_project_id"), acquisition.get("parent_project_id")),
        ("source YouTube video", selection.get("source_youtube_video_id"), source.get("youtube_video_id")),
    )
    for label, selected_value, acquired_value in comparisons:
        if selected_value != acquired_value:
            errors.append(f"$lineage: selection {label} does not match acquisition")
    if _normalized_sha256(selection.get("source_sha256")) != _normalized_sha256(source.get("sha256")):
        errors.append("$lineage: selection source SHA-256 does not match acquisition")
    selected_duration = selection.get("source_duration_s")
    acquired_duration = source.get("duration_s")
    if not (
        isinstance(selected_duration, (int, float))
        and isinstance(acquired_duration, (int, float))
        and math.isclose(float(selected_duration), float(acquired_duration), abs_tol=EPSILON)
    ):
        errors.append("$lineage: selection source duration does not match acquisition")
    clips = selection.get("clips")
    selected_count = len(clips) if isinstance(clips, list) else None
    if selected_count != acquisition.get("selected_clip_count"):
        errors.append("$lineage: acquisition selected_clip_count does not equal selection clips")
    abstained = selection.get("abstained")
    if abstained != acquisition.get("abstained"):
        errors.append("$lineage: selection and acquisition abstention state differs")
    reference = acquisition.get("reference")
    if abstained is True:
        if reference is not None or acquisition.get("status") != "abstained":
            errors.append("$lineage: abstained selection requires acquisition with no reference")
    elif abstained is False:
        if not isinstance(reference, dict) or acquisition.get("status") != "ready_for_materialization":
            errors.append("$lineage: nonempty selection requires a committed acquisition reference")
    for field in ("source_visual_inspection", "source_speech_transcript"):
        if selection.get(field) != source.get(field):
            errors.append(f"$lineage: selection and acquisition {field} evidence differs")
    return errors


def _reference_acquisition_errors(
    profile: dict[str, Any], acquisition: dict[str, Any]
) -> list[str]:
    reference = acquisition.get("reference")
    if not isinstance(reference, dict):
        return ["$lineage: reference profile exists but acquisition reference is null"]
    errors: list[str] = []
    for label, profile_value, acquired_value in (
        ("asset ID", profile.get("parent_reference_asset_id"), reference.get("asset_id")),
        ("YouTube video ID", profile.get("youtube_video_id"), reference.get("youtube_video_id")),
    ):
        if profile_value != acquired_value:
            errors.append(f"$lineage: reference profile {label} does not match acquisition")
    if _normalized_sha256(profile.get("reference_sha256")) != _normalized_sha256(reference.get("sha256")):
        errors.append("$lineage: reference profile SHA-256 does not match acquisition")
    reference_duration = reference.get("duration_s")
    bounded_duration = (
        float(reference_duration)
        if isinstance(reference_duration, (int, float))
        else None
    )
    profile_duration = profile.get("reference_duration_s")
    if not (
        bounded_duration is not None
        and isinstance(profile_duration, (int, float))
        and math.isclose(float(profile_duration), bounded_duration, abs_tol=EPSILON)
    ):
        errors.append("$lineage: reference profile duration does not match acquisition")

    bindings: tuple[tuple[str, Any, Any], ...] = (
        ("reference profile version", profile.get("reference_profile_version"), reference.get("reference_profile_version")),
        ("preselection visual evidence ID", profile.get("preselection_visual_inspection", {}).get("evidence_id"), reference.get("preselection_visual_evidence_id")),
        ("preselection visual digest", _canonical_object_digest(profile.get("preselection_visual_inspection")), reference.get("preselection_visual_inspection_digest")),
        ("preselection visual media SHA", profile.get("preselection_visual_inspection", {}).get("media_sha256"), reference.get("preselection_media_sha256")),
        ("preselection visual duration", profile.get("preselection_visual_inspection", {}).get("duration_s"), reference.get("preselection_duration_s")),
        ("preselection transcript digest", _canonical_object_digest(profile.get("preselection_speech_transcript")), reference.get("preselection_speech_transcript_digest")),
        ("preselection transcript evidence ID", profile.get("preselection_speech_transcript", {}).get("evidence_id"), reference.get("preselection_speech_evidence_id")),
        ("preselection transcript text digest", profile.get("preselection_speech_transcript", {}).get("asr", {}).get("transcript_text_sha256"), reference.get("transcript_text_sha256")),
        ("preselection PCM digest", profile.get("preselection_speech_transcript", {}).get("audio_pcm_sha256"), reference.get("audio_pcm_sha256")),
        ("music identity digest", _canonical_object_digest(profile.get("music_identity")), reference.get("music_identity_digest")),
        ("selection decision digest", _canonical_object_digest(profile.get("selection_decision")), reference.get("selection_decision_digest")),
        ("signal status", profile.get("signal_analysis", {}).get("status"), reference.get("signal_analysis_status")),
        ("signal evidence ID", profile.get("signal_analysis", {}).get("evidence_id"), reference.get("signal_analysis_evidence_id")),
        ("postupload visual evidence ID", profile.get("postupload_visual_inspection", {}).get("evidence_id"), reference.get("postupload_visual_evidence_id")),
        ("postupload visual digest", _canonical_object_digest(profile.get("postupload_visual_inspection")), reference.get("postupload_visual_inspection_digest")),
        ("postupload media SHA", profile.get("postupload_visual_inspection", {}).get("media_sha256"), reference.get("postupload_media_sha256")),
        ("postupload duration", profile.get("postupload_visual_inspection", {}).get("duration_s"), reference.get("postupload_duration_s")),
        ("postupload transcript", profile.get("postupload_speech_transcript"), reference.get("postupload_speech_transcript")),
    )
    for label, profile_value, acquisition_value in bindings:
        if label.endswith("SHA") or "digest" in label.lower():
            matches = _normalized_sha256(profile_value) == _normalized_sha256(acquisition_value)
        elif isinstance(profile_value, (int, float)) and isinstance(acquisition_value, (int, float)):
            matches = math.isclose(float(profile_value), float(acquisition_value), abs_tol=EPSILON)
        else:
            matches = profile_value == acquisition_value
        if not matches:
            errors.append(f"$lineage: {label} does not match acquisition")
    signal = profile.get("signal_analysis")
    expected_signal_digest = (
        _canonical_object_digest(signal)
        if isinstance(signal, dict) and signal.get("status") == "complete"
        else None
    )
    if _normalized_sha256(expected_signal_digest) != _normalized_sha256(reference.get("signal_analysis_digest")):
        errors.append("$lineage: signal analysis digest does not match acquisition")
    for index, observation in enumerate(profile.get("observations", [])):
        if isinstance(observation, dict):
            errors.extend(
                _interval_errors(
                    [observation.get("start_s"), observation.get("end_s")],
                    f"$.observations[{index}]",
                    bounded_duration,
                )
            )
    return errors


def _assignment_acquisition_errors(
    assignment: dict[str, Any], acquisition: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    source = acquisition.get("source")
    if not isinstance(source, dict):
        return ["$lineage: acquisition source record is missing"]
    for label, assigned_value, acquired_value in (
        ("run_id", assignment.get("run_id"), acquisition.get("run_id")),
        ("parent_project_id", assignment.get("parent_project_id"), acquisition.get("parent_project_id")),
        ("source.asset_id", assignment.get("source", {}).get("asset_id"), source.get("asset_id")),
        (
            "source.youtube_video_id",
            assignment.get("source", {}).get("youtube_video_id"),
            source.get("youtube_video_id"),
        ),
    ):
        if assigned_value != acquired_value:
            errors.append(f"$lineage: assignment {label} does not match acquisition")
    assigned_duration = assignment.get("source", {}).get("source_duration_s")
    acquired_duration = source.get("duration_s")
    if not (
        isinstance(assigned_duration, (int, float))
        and isinstance(acquired_duration, (int, float))
        and math.isclose(
            float(assigned_duration), float(acquired_duration), abs_tol=EPSILON
        )
    ):
        errors.append("$lineage: assignment source duration does not match acquisition")
    transfer = assignment.get("reference_transfer")
    reference = acquisition.get("reference")
    if isinstance(transfer, dict) and transfer.get("status") in {"applicable", "partial", "inapplicable"}:
        if not isinstance(reference, dict):
            errors.append("$lineage: applicable assignment reference has no acquisition reference")
        else:
            for label, assigned_value, acquired_value in (
                ("parent asset ID", transfer.get("parent_reference_asset_id"), reference.get("asset_id")),
                ("YouTube video ID", transfer.get("reference_youtube_video_id"), reference.get("youtube_video_id")),
            ):
                if assigned_value != acquired_value:
                    errors.append(f"$lineage: assignment reference {label} does not match acquisition")
            if _normalized_sha256(transfer.get("reference_sha256")) != _normalized_sha256(reference.get("sha256")):
                errors.append("$lineage: assignment reference SHA-256 does not match acquisition")
    elif isinstance(reference, dict) and isinstance(transfer, dict) and transfer.get("status") == "not_supplied":
        errors.append("$lineage: acquisition has a reference but assignment says not_supplied")
    return errors


def _assignment_reference_errors(
    assignment: dict[str, Any], profile: dict[str, Any]
) -> list[str]:
    transfer = assignment.get("reference_transfer")
    if not isinstance(transfer, dict) or transfer.get("status") == "not_supplied":
        return ["$lineage: reference profile supplied for an assignment without an applicable reference"]
    errors: list[str] = []
    if assignment.get("reference_profile_version") != profile.get("reference_profile_version"):
        errors.append("$lineage: assignment reference profile version does not match profile")
    for label, assigned_value, profile_value in (
        ("parent asset ID", transfer.get("parent_reference_asset_id"), profile.get("parent_reference_asset_id")),
        ("YouTube video ID", transfer.get("reference_youtube_video_id"), profile.get("youtube_video_id")),
    ):
        if assigned_value != profile_value:
            errors.append(f"$lineage: assignment reference {label} does not match profile")
    if _normalized_sha256(transfer.get("reference_sha256")) != _normalized_sha256(profile.get("reference_sha256")):
        errors.append("$lineage: assignment reference SHA-256 does not match profile")
    available_ids = {
        row.get("observation_id")
        for row in profile.get("observations", [])
        if isinstance(row, dict)
    }
    cited_ids: set[Any] = set()
    cited_ids.update(transfer.get("reference_observation_ids_considered", []))
    for decision_kind in ("transfer", "adapt", "reject"):
        for row in transfer.get(decision_kind, []):
            if isinstance(row, dict):
                cited_ids.update(row.get("reference_observation_ids", []))
    missing_ids = sorted(value for value in cited_ids if value not in available_ids)
    if missing_ids:
        errors.append(
            "$lineage: assignment cites reference observation IDs absent from profile: "
            + ", ".join(map(str, missing_ids))
        )
    return errors


def _editor_claims_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_schema_version": result.get("result_schema_version"),
        "result_fingerprint": result.get("result_fingerprint"),
        "final_edl_version": result.get("final_edl_version"),
        "preview_edl_version": result.get("preview_edl_version"),
        "preview_render_provenance": result.get("preview_render_provenance"),
        "audio": result.get("audio"),
        "cta": result.get("cta"),
        "render_visual_inspection": result.get("render_visual_inspection"),
        "program_speech_transcript": result.get("program_speech_transcript"),
        "visual_provenance_records": result.get("visuals", {}).get(
            "visual_provenance_records", []
        ),
    }


def _provenance_identity_windows(records: Any) -> set[tuple[Any, Any, Any, Any, Any]]:
    if not isinstance(records, list):
        return set()
    return {
        (
            row.get("provider"),
            row.get("canonical_source_id")
            or _normalized_sha256(row.get("sha256")),
            row.get("source_start_s"),
            row.get("source_duration_s"),
            row.get("asset_key"),
        )
        for row in records
        if isinstance(row, dict)
    }


def cross_contract_errors(schema_name: str, data: dict[str, Any], against: dict[str, Any]) -> list[str]:
    """Check stable identities against one auto-detected upstream artifact."""
    errors: list[str] = []
    normalized = schema_name.removesuffix(".schema.json")
    upstream_type = artifact_type(against)
    if upstream_type is None:
        return ["$against: cannot identify upstream contract artifact type"]

    if normalized == "selection" and upstream_type == "acquisition-record":
        return _selection_acquisition_errors(data, against)
    if normalized == "acquisition-record" and upstream_type == "selection":
        return _selection_acquisition_errors(against, data)
    if normalized == "reference-profile" and upstream_type == "acquisition-record":
        return _reference_acquisition_errors(data, against)
    if normalized == "acquisition-record" and upstream_type == "reference-profile":
        return _reference_acquisition_errors(against, data)
    if normalized == "child-assignment" and upstream_type == "acquisition-record":
        return _assignment_acquisition_errors(data, against)
    if normalized == "child-assignment" and upstream_type == "reference-profile":
        return _assignment_reference_errors(data, against)

    if normalized == "pre-mutation-recast" and upstream_type == "child-assignment":
        if against.get("assignment_status") != "requires_pre_mutation_recast":
            errors.append("$lineage: pre-mutation recast requires an ambiguous assignment")
        for field in (
            "assignment_schema_version",
            "taxonomy_version",
            "prompt_version",
            "treatment_contract_version",
            "reference_profile_version",
            "assignment_id",
            "assignment_input_fingerprint",
            "run_id",
            "parent_project_id",
            "child_project_id",
        ):
            if data.get(field) != against.get(field):
                errors.append(f"$.{field}: must echo the child assignment")
        assigned_candidates = {
            row.get("candidate_id"): row
            for row in against.get("candidate_slate", [])
            if isinstance(row, dict)
        }
        recast_assessments = {
            row.get("candidate_id"): row
            for row in data.get("candidate_assessments", [])
            if isinstance(row, dict)
        }
        assignment_evidence_ids: set[Any] = set()
        for candidate in assigned_candidates.values():
            assignment_evidence_ids.update(candidate.get("evidence_ids", []))
        inspected_ids = set(data.get("inspected_evidence_ids", []))
        fabricated_inspection = inspected_ids - assignment_evidence_ids
        if fabricated_inspection:
            errors.append(
                "$.inspected_evidence_ids: contains IDs absent from frozen assignment "
                + ", ".join(map(str, sorted(fabricated_inspection)))
            )
        if set(assigned_candidates) != set(recast_assessments):
            errors.append("$.candidate_assessments: IDs must exactly match assignment candidate_slate")
        for candidate_id in set(assigned_candidates) & set(recast_assessments):
            candidate = assigned_candidates[candidate_id]
            assessment = recast_assessments[candidate_id]
            for field in ("dominant_mode", "secondary_mode"):
                if assessment.get(field) != candidate.get(field):
                    errors.append(
                        f"$.candidate_assessments[{candidate_id}].{field}: "
                        "must echo assignment candidate cast"
                    )
            candidate_evidence = set(candidate.get("evidence_ids", []))
            supporting = set(assessment.get("supporting_evidence_ids", []))
            contradicting = set(assessment.get("contradicting_evidence_ids", []))
            fabricated_assessment = (supporting | contradicting) - candidate_evidence
            if fabricated_assessment:
                errors.append(
                    f"$.candidate_assessments[{candidate_id}]: evidence IDs absent "
                    "from the frozen candidate evidence: "
                    + ", ".join(map(str, sorted(fabricated_assessment)))
                )
            uninspected = (supporting | contradicting) - inspected_ids
            if uninspected:
                errors.append(
                    f"$.candidate_assessments[{candidate_id}]: assessment evidence "
                    "must be included in inspected_evidence_ids: "
                    + ", ".join(map(str, sorted(uninspected)))
                )
            if supporting & contradicting:
                errors.append(
                    f"$.candidate_assessments[{candidate_id}]: the same evidence "
                    "cannot both support and contradict a candidate"
                )
        if data.get("status") == "approved":
            approved_id = data.get("approved_candidate_id")
            candidate = assigned_candidates.get(approved_id)
            if not isinstance(candidate, dict):
                errors.append("$.approved_candidate_id: must identify an assignment candidate")
            else:
                approved_cast = data.get("approved_cast", {})
                for field in ("dominant_mode", "secondary_mode"):
                    if approved_cast.get(field) != candidate.get(field):
                        errors.append(
                            f"$.approved_cast.{field}: must match approved assignment candidate"
                        )
                expected_classification = (
                    "hybrid" if candidate.get("secondary_mode") is not None else "clear"
                )
                if approved_cast.get("classification_status") != expected_classification:
                    errors.append(
                        "$.approved_cast.classification_status: must match the approved cast"
                    )
                if data.get("approved_treatment_delta", {}).get("name") != candidate.get(
                    "treatment_name"
                ):
                    errors.append(
                        "$.approved_treatment_delta.name: must equal approved candidate treatment_name"
                    )
                approved_assessment = recast_assessments.get(approved_id, {})
                if not approved_assessment.get("supporting_evidence_ids"):
                    errors.append(
                        "$.approved_candidate_id: approved candidate requires frozen supporting evidence"
                    )
            delta = data.get("approved_treatment_delta", {})
            source = against.get("source", {})
            seeded_start = source.get("seeded_child_start_s")
            seeded_end = source.get("seeded_child_end_s")
            assigned_end = delta.get("assigned_music_end_s")
            if all(
                isinstance(value, (int, float))
                for value in (seeded_start, seeded_end, assigned_end)
            ) and float(assigned_end) > float(seeded_end) - float(seeded_start) + EPSILON:
                errors.append(
                    "$.approved_treatment_delta.assigned_music_end_s: "
                    "must not exceed the seeded child duration"
                )
        return errors

    if normalized == "editor-result" and upstream_type == "child-assignment":
        for field in (
            "assignment_schema_version",
            "prompt_version",
            "assignment_id",
            "assignment_input_fingerprint",
            "run_id",
            "parent_project_id",
            "child_project_id",
            "card",
        ):
            if data.get(field) != against.get(field):
                errors.append(f"$.{field}: must echo the child assignment")
        if data.get("source_lineage") != against.get("source"):
            errors.append("$.source_lineage: must echo immutable assignment seed lineage")
        assigned_treatment = against.get("treatment", {})
        result_treatment = data.get("treatment", {})
        for result_field, assigned_value in (
            ("treatment_contract_version", against.get("treatment_contract_version")),
            ("reference_profile_version", against.get("reference_profile_version")),
        ):
            if result_treatment.get(result_field) != assigned_value:
                errors.append(f"$.treatment.{result_field}: must echo immutable assignment treatment")
        reference_transfer = against.get("reference_transfer", {})
        if result_treatment.get("reference_status") != reference_transfer.get("status"):
            errors.append("$.treatment.reference_status: must match assignment reference status")
        reference_applicable = reference_transfer.get("status") in {"applicable", "partial"}
        if result_treatment.get("reference_applicable") is not reference_applicable:
            errors.append("$.treatment.reference_applicable: must match assignment reference status")
        expected_child_reference = (
            reference_transfer.get("child_reference_asset_id") if reference_applicable else None
        )
        if result_treatment.get("child_reference_asset_id") != expected_child_reference:
            errors.append("$.treatment.child_reference_asset_id: must match assignment reference asset")

        assigned_profile = against.get("story_profile", {})
        result_visuals = data.get("visuals", {})
        direct_cast = assigned_profile.get("classification_status") in {"clear", "hybrid"}
        if direct_cast:
            if result_treatment.get("name") != assigned_treatment.get("name"):
                errors.append("$.treatment.name: must echo immutable assignment treatment")
            for result_field, assigned_value in (
                ("classification_status", assigned_profile.get("classification_status")),
                ("dominant_mode", assigned_profile.get("dominant_mode")),
                ("secondary_mode", assigned_profile.get("secondary_mode")),
                ("visual_identity", against.get("visual_identity", {}).get("name")),
                ("motifs", against.get("visual_identity", {}).get("motifs")),
            ):
                if result_visuals.get(result_field) != assigned_value:
                    errors.append(f"$.visuals.{result_field}: must echo assignment visual identity")

        audio = data.get("audio", {})
        if direct_cast:
            for result_field, assignment_field in (
                ("music_policy", "music_policy"),
                ("music_brief", "music_brief"),
                ("entry_policy", "music_entry_policy"),
                ("assigned_start_s", "assigned_music_start_s"),
                ("assigned_end_s", "assigned_music_end_s"),
                ("no_music_justification", "no_music_justification"),
            ):
                if audio.get(result_field) != assigned_treatment.get(assignment_field):
                    errors.append(f"$.audio.{result_field}: must echo treatment.{assignment_field}")
        if against.get("assignment_status") == "blocked_before_mutation":
            errors.append("$lineage: blocked_before_mutation assignment cannot produce editor-result")
        if audio.get("edl_facts", {}).get("track_stable_id") in set(
            against.get("sibling_music_tracks_to_avoid", [])
        ):
            errors.append("$.audio.edl_facts.track_stable_id: reuses a sibling-excluded music identity")

        for record_index, record in enumerate(result_visuals.get("visual_provenance_records", [])):
            if not isinstance(record, dict):
                continue
            record_start = record.get("source_start_s")
            record_duration = record.get("source_duration_s")
            for exclusion in against.get("sibling_asset_windows_to_avoid", []):
                if not isinstance(exclusion, dict):
                    continue
                same_identity = exclusion.get("provider") == record.get("provider")
                if same_identity and exclusion.get("canonical_source_id") is not None:
                    same_identity = exclusion.get("canonical_source_id") == record.get("canonical_source_id")
                elif same_identity and exclusion.get("sha256") is not None:
                    same_identity = _normalized_sha256(exclusion.get("sha256")) == _normalized_sha256(record.get("sha256"))
                if not same_identity or not all(
                    isinstance(value, (int, float))
                    for value in (
                        record_start,
                        record_duration,
                        exclusion.get("source_start_s"),
                        exclusion.get("source_duration_s"),
                    )
                ):
                    continue
                record_end = record_start + record_duration
                exclusion_end = exclusion["source_start_s"] + exclusion["source_duration_s"]
                if record_start < exclusion_end - EPSILON and exclusion["source_start_s"] < record_end - EPSILON:
                    errors.append(
                        f"$.visuals.visual_provenance_records[{record_index}]: "
                        "intersects a sibling-excluded source window"
                    )
        return errors

    if normalized == "editor-result" and upstream_type == "pre-mutation-recast":
        for result_field, recast_field in (
            ("assignment_schema_version", "assignment_schema_version"),
            ("prompt_version", "prompt_version"),
            ("assignment_id", "assignment_id"),
            ("assignment_input_fingerprint", "assignment_input_fingerprint"),
            ("run_id", "run_id"),
            ("parent_project_id", "parent_project_id"),
            ("child_project_id", "child_project_id"),
        ):
            if data.get(result_field) != against.get(recast_field):
                errors.append(f"$.{result_field}: must echo the approved recast identity")
        result_treatment = data.get("treatment", {})
        if result_treatment.get("treatment_contract_version") != against.get(
            "treatment_contract_version"
        ):
            errors.append(
                "$.treatment.treatment_contract_version: must echo approved recast identity"
            )
        if result_treatment.get("reference_profile_version") != against.get(
            "reference_profile_version"
        ):
            errors.append(
                "$.treatment.reference_profile_version: must echo approved recast identity"
            )
        if against.get("status") != "approved":
            if data.get("status") == "ready":
                errors.append("$lineage: ready editor result requires an approved recast")
            return errors

        approved_cast = against.get("approved_cast", {})
        delta = against.get("approved_treatment_delta", {})
        visuals = data.get("visuals", {})
        audio = data.get("audio", {})
        if result_treatment.get("name") != delta.get("name"):
            errors.append("$.treatment.name: must match approved treatment delta")
        for result_field, approved_value in (
            ("classification_status", approved_cast.get("classification_status")),
            ("dominant_mode", approved_cast.get("dominant_mode")),
            ("secondary_mode", approved_cast.get("secondary_mode")),
            ("visual_identity", delta.get("visual_identity")),
            ("motifs", delta.get("visual_motifs")),
        ):
            if visuals.get(result_field) != approved_value:
                errors.append(f"$.visuals.{result_field}: must match approved recast")
        for result_field, delta_field in (
            ("music_policy", "music_policy"),
            ("music_brief", "music_brief"),
            ("entry_policy", "music_entry_policy"),
            ("assigned_start_s", "assigned_music_start_s"),
            ("assigned_end_s", "assigned_music_end_s"),
            ("no_music_justification", "no_music_justification"),
        ):
            if audio.get(result_field) != delta.get(delta_field):
                errors.append(f"$.audio.{result_field}: must match approved treatment delta")
        return errors

    if normalized == "parent-qc" and upstream_type in {"child-assignment", "editor-result"}:
        for field in (
            "assignment_id",
            "assignment_input_fingerprint",
            "run_id",
            "parent_project_id",
            "child_project_id",
            "card",
        ):
            if field not in against and field in {"run_id", "card"}:
                continue
            if data.get(field) != against.get(field):
                errors.append(f"$.{field}: must echo the upstream assignment/result")
        expected_source = (
            against.get("source")
            if upstream_type == "child-assignment"
            else against.get("source_lineage")
        )
        if data.get("source_lineage") != expected_source:
            errors.append("$.source_lineage: must echo immutable child seed lineage")
        music = data.get("music", {})
        if upstream_type == "child-assignment":
            treatment = against.get("treatment", {})
            if against.get("assignment_status") != "requires_pre_mutation_recast":
                for qc_field, assignment_field in (
                    ("music_policy", "music_policy"),
                    ("music_brief", "music_brief"),
                    ("entry_policy", "music_entry_policy"),
                    ("assigned_start_s", "assigned_music_start_s"),
                    ("assigned_end_s", "assigned_music_end_s"),
                    ("no_music_justification", "no_music_justification"),
                ):
                    if music.get(qc_field) != treatment.get(assignment_field):
                        errors.append(f"$.music.{qc_field}: must echo treatment.{assignment_field}")
            return errors

        upstream_music = against.get("audio", {})
        expected_claims = _editor_claims_from_result(against)
        if data.get("editor_claims") != expected_claims:
            errors.append("$.editor_claims: must preserve the editor's reported evidence")
        if data.get("status") == "pass":
            if data.get("live_edl_version") != expected_claims["final_edl_version"]:
                errors.append("$.live_edl_version: pass must equal the editor's final EDL")
            if data.get("preview_edl_version") != expected_claims["preview_edl_version"]:
                errors.append("$.preview_edl_version: pass must equal the editor's preview EDL")
        parent_provenance = data.get("preview_render_provenance", {})
        child_provenance = against.get("preview_render_provenance", {})
        if parent_provenance == child_provenance:
            errors.append("$.preview_render_provenance: parent must persist a distinct current preview receipt")
        if isinstance(parent_provenance, dict) and isinstance(child_provenance, dict):
            parent_retrieved = _parse_datetime(parent_provenance.get("retrieval", {}).get("retrieved_at"))
            child_retrieved = _parse_datetime(child_provenance.get("retrieval", {}).get("retrieved_at"))
            if parent_retrieved is not None and child_retrieved is not None and parent_retrieved < child_retrieved:
                errors.append("$.preview_render_provenance.retrieved_at: parent receipt cannot predate editor receipt")
        for field in (
            "music_policy",
            "entry_policy",
            "assigned_start_s",
            "assigned_end_s",
            "no_music_justification",
        ):
            if music.get(field) != upstream_music.get(field):
                errors.append(f"$.music.{field}: must echo immutable assigned audio policy")
        broll = data.get("broll", {})
        upstream_visuals = against.get("visuals", {})
        claimed_windows = _provenance_identity_windows(
            upstream_visuals.get("visual_provenance_records")
        )
        observed_windows = _provenance_identity_windows(
            broll.get("visual_provenance_records")
        )
        if claimed_windows != observed_windows:
            if data.get("status") == "pass":
                errors.append(
                    "$.broll.visual_provenance_records: pass requires the same stable "
                    "asset identities/windows as the editor claim"
                )
            elif not any(
                isinstance(row, dict) and row.get("gate") in {"broll", "render"}
                for row in data.get("violations", [])
            ):
                errors.append(
                    "$.violations: observed provenance mismatch requires a B-roll/render violation"
                )
        observed_music_differs = any(
            music.get(field) != upstream_music.get(field)
            for field in (
                "music_identity",
                "selection_mode",
                "candidate_metadata_shortlist",
                "selected_track_analysis",
                "edl_facts",
                "mix_measurements",
                "audit",
            )
        )
        observed_cta_differs = data.get("cta") != against.get("cta")
        parent_transcript = data.get("program_speech_transcript", {})
        child_transcript = against.get("program_speech_transcript", {})
        rendered_media_differs = (
            isinstance(parent_transcript, dict)
            and isinstance(child_transcript, dict)
            and _normalized_sha256(parent_transcript.get("preview_media_sha256"))
            != _normalized_sha256(child_transcript.get("preview_media_sha256"))
        )
        evidence_violation = any(
            isinstance(row, dict) and row.get("gate") in {"music", "audio", "render"}
            for row in data.get("violations", [])
        )
        if data.get("status") == "pass" and (
            observed_music_differs or observed_cta_differs or rendered_media_differs
        ):
            errors.append(
                "$: pass requires parent factual music/CTA/render identity to match the editor result"
            )
        if (
            data.get("status") != "pass"
            and (observed_music_differs or observed_cta_differs or rendered_media_differs)
            and not evidence_violation
        ):
            errors.append(
                "$.violations: contrary parent factual evidence requires a music/audio/render violation"
            )
        for parent_key, child_key in (
            ("render_visual_inspection", "render_visual_inspection"),
            ("program_speech_transcript", "program_speech_transcript"),
        ):
            parent_row = data.get(parent_key)
            child_row = against.get(child_key)
            if parent_row == child_row:
                errors.append(f"$.{parent_key}: parent evidence must be independently persisted")
            if isinstance(parent_row, dict) and isinstance(child_row, dict):
                parent_completed = _parse_datetime(parent_row.get("completed_at"))
                child_completed = _parse_datetime(child_row.get("completed_at"))
                if parent_completed is not None and child_completed is not None and parent_completed < child_completed:
                    errors.append(f"$.{parent_key}.completed_at: parent evidence cannot predate editor evidence")
        upstream_treatment = against.get("treatment", {})
        qc_treatment = data.get("treatment", {})
        for field in (
            "treatment_contract_version",
            "reference_profile_version",
            "name",
            "reference_status",
            "reference_applicable",
            "child_reference_asset_id",
        ):
            if qc_treatment.get(field) != upstream_treatment.get(field):
                errors.append(f"$.treatment.{field}: must echo editor treatment/reference identity")
        if data.get("story", {}).get("final_spoken_line") != against.get("story", {}).get("final_spoken_line"):
            errors.append("$.story.final_spoken_line: must echo editor story identity")
        return errors

    if normalized == "parent-qc" and upstream_type == "pre-mutation-recast":
        for result_field, recast_field in (
            ("assignment_schema_version", "assignment_schema_version"),
            ("prompt_version", "prompt_version"),
            ("assignment_id", "assignment_id"),
            ("assignment_input_fingerprint", "assignment_input_fingerprint"),
            ("run_id", "run_id"),
            ("parent_project_id", "parent_project_id"),
            ("child_project_id", "child_project_id"),
        ):
            if data.get(result_field) != against.get(recast_field):
                errors.append(f"$.{result_field}: must echo the approved recast identity")
        if against.get("status") != "approved":
            errors.append("$lineage: parent QC for an ambiguous assignment requires an approved recast")
            return errors
        delta = against.get("approved_treatment_delta", {})
        treatment = data.get("treatment", {})
        if treatment.get("treatment_contract_version") != against.get(
            "treatment_contract_version"
        ):
            errors.append(
                "$.treatment.treatment_contract_version: must echo approved recast identity"
            )
        if treatment.get("reference_profile_version") != against.get(
            "reference_profile_version"
        ):
            errors.append(
                "$.treatment.reference_profile_version: must echo approved recast identity"
            )
        if treatment.get("name") != delta.get("name"):
            errors.append("$.treatment.name: must match approved treatment delta")
        music = data.get("music", {})
        for qc_field, delta_field in (
            ("music_policy", "music_policy"),
            ("music_brief", "music_brief"),
            ("entry_policy", "music_entry_policy"),
            ("assigned_start_s", "assigned_music_start_s"),
            ("assigned_end_s", "assigned_music_end_s"),
            ("no_music_justification", "no_music_justification"),
        ):
            if music.get(qc_field) != delta.get(delta_field):
                errors.append(f"$.music.{qc_field}: must match approved treatment delta")
        return errors

    if normalized == "child-assignment" and upstream_type == "selection":
        card = data.get("card")
        matching = [clip for clip in against.get("clips", []) if isinstance(clip, dict) and clip.get("rank") == card]
        if len(matching) != 1:
            errors.append("$.card: must identify exactly one selected clip")
        else:
            clip = matching[0]
            source = data.get("source", {})
            for assigned_field, selected_field in (("approved_start_s", "start"), ("approved_end_s", "end")):
                if source.get(assigned_field) != clip.get(selected_field):
                    errors.append(f"$.source.{assigned_field}: must equal selected clip {selected_field}")
            for field in ("title", "hook", "story", "opening_line", "closing_line", "selection_reason"):
                if data.get(field) != clip.get(field):
                    errors.append(f"$.{field}: must echo selected clip rank {card}")
        if data.get("parent_project_id") != against.get("parent_project_id"):
            errors.append("$.parent_project_id: must echo selection")
        if data.get("source", {}).get("youtube_video_id") != against.get("source_youtube_video_id"):
            errors.append("$.source.youtube_video_id: must echo selection")
        assigned_duration = data.get("source", {}).get("source_duration_s")
        selected_duration = against.get("source_duration_s")
        if not (
            isinstance(assigned_duration, (int, float))
            and isinstance(selected_duration, (int, float))
            and math.isclose(
                float(assigned_duration), float(selected_duration), abs_tol=EPSILON
            )
        ):
            errors.append("$.source.source_duration_s: must echo selection")
        return errors

    if normalized == "coordinator-run-result" and upstream_type == "selection":
        if data.get("status") == "blocked_before_selection":
            return ["$lineage: blocked_before_selection result must not cite a selection"]
        if data.get("selection_fingerprint") != against.get("selection_fingerprint"):
            errors.append("$.selection_fingerprint: must echo selection")
        for result_field, selected_field in (
            ("parent_project_id", "parent_project_id"),
            ("source_youtube_video_id", "source_youtube_video_id"),
            ("source_sha256", "source_sha256"),
        ):
            if result_field == "source_sha256":
                matches = _normalized_sha256(data.get(result_field)) == _normalized_sha256(
                    against.get(selected_field)
                )
            else:
                matches = data.get(result_field) == against.get(selected_field)
            if not matches:
                errors.append(f"$.{result_field}: must echo selection")
        selected_rows = {
            clip.get("rank"): (clip.get("start"), clip.get("end"), clip.get("title"))
            for clip in against.get("clips", [])
            if isinstance(clip, dict)
        }
        result_rows = {
            row.get("selection_rank"): (row.get("start_s"), row.get("end_s"), row.get("title"))
            for row in data.get("arc_accounting", [])
            if isinstance(row, dict)
        }
        if selected_rows != result_rows:
            errors.append("$.arc_accounting: must account one-to-one for the frozen selected arcs")
        if data.get("selected_arc_count") != len(against.get("clips", [])):
            errors.append("$.selected_arc_count: must equal frozen selection clip count")
        return errors

    if normalized == "coordinator-run-result" and upstream_type == "acquisition-record":
        if data.get("status") == "blocked_before_selection":
            return ["$lineage: blocked_before_selection result has no acquisition record"]
        source = against.get("source", {})
        for result_field, acquired_value in (
            ("run_id", against.get("run_id")),
            ("topic", against.get("topic")),
            ("parent_project_id", against.get("parent_project_id")),
            ("source_youtube_video_id", source.get("youtube_video_id")),
            ("source_asset_id", source.get("asset_id")),
        ):
            if data.get(result_field) != acquired_value:
                errors.append(f"$.{result_field}: must echo acquisition record")
        if _normalized_sha256(data.get("source_sha256")) != _normalized_sha256(source.get("sha256")):
            errors.append("$.source_sha256: must echo acquisition source SHA-256")
        reference = against.get("reference")
        expected_reference = reference if isinstance(reference, dict) else {}
        for result_field, reference_field in (
            ("reference_youtube_video_id", "youtube_video_id"),
            ("reference_asset_id", "asset_id"),
        ):
            if data.get(result_field) != expected_reference.get(reference_field):
                errors.append(f"$.{result_field}: must echo acquisition reference")
        if _normalized_sha256(data.get("reference_sha256")) != _normalized_sha256(
            expected_reference.get("sha256")
        ):
            errors.append("$.reference_sha256: must echo acquisition reference SHA-256")
        if data.get("selected_arc_count") != against.get("selected_clip_count"):
            errors.append("$.selected_arc_count: must echo acquisition selected_clip_count")
        if data.get("abstained") != against.get("abstained"):
            errors.append("$.abstained: must echo acquisition abstention state")
        return errors

    if normalized == "coordinator-run-result" and upstream_type == "reference-profile":
        if data.get("status") in {"blocked_before_selection", "abstained"}:
            return ["$lineage: this coordinator result cannot cite a reference profile"]
        for result_field, profile_field in (
            ("reference_youtube_video_id", "youtube_video_id"),
            ("reference_asset_id", "parent_reference_asset_id"),
        ):
            if data.get(result_field) != against.get(profile_field):
                errors.append(f"$.{result_field}: must echo reference profile")
        if _normalized_sha256(data.get("reference_sha256")) != _normalized_sha256(
            against.get("reference_sha256")
        ):
            errors.append("$.reference_sha256: must echo reference profile")
        return errors

    return [
        f"$against: no lineage rule for {normalized} against {upstream_type}"
    ]


def cross_contract_set_errors(
    schema_name: str,
    data: dict[str, Any],
    upstreams: list[dict[str, Any]],
) -> list[str]:
    """Enforce relationships that require more than one upstream artifact."""
    if schema_name not in {"editor-result", "parent-qc"}:
        return []
    assignments = [row for row in upstreams if artifact_type(row) == "child-assignment"]
    recasts = [row for row in upstreams if artifact_type(row) == "pre-mutation-recast"]
    errors: list[str] = []
    if len(assignments) > 1:
        errors.append(f"$against: {schema_name} accepts exactly one child assignment")
        return errors
    if recasts and not assignments:
        errors.append("$against: approved recast must be supplied with its immutable assignment")
        return errors
    if not assignments:
        return errors
    assignment = assignments[0]
    requires_recast = assignment.get("assignment_status") == "requires_pre_mutation_recast"
    if requires_recast:
        approved = [row for row in recasts if row.get("status") == "approved"]
        if len(approved) != 1:
            artifact_label = "editor result" if schema_name == "editor-result" else "parent QC"
            errors.append(
                f"$against: {artifact_label} for ambiguous assignment requires exactly one approved recast"
            )
        else:
            errors.extend(_recast_errors(approved[0]))
            errors.extend(cross_contract_errors("pre-mutation-recast", approved[0], assignment))
    if not requires_recast and recasts:
        errors.append("$against: concrete assignment must not be overridden by a recast")
    return errors


def validate_instance(schema_name: str, data: Any, against: Any | None = None) -> list[str]:
    path = schema_path(schema_name)
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    schema_errors = sorted(validator.iter_errors(data), key=lambda error: list(error.absolute_path))
    errors = [f"{_json_path(error.absolute_path)}: {error.message}" for error in schema_errors]
    errors.extend(_legacy_attestation_errors(data))
    if errors or not isinstance(data, dict):
        return errors
    normalized = path.name.removesuffix(".schema.json")
    semantic = SEMANTIC_VALIDATORS.get(normalized)
    if semantic is not None:
        errors.extend(semantic(data))
    if against is not None:
        upstreams = against if isinstance(against, list) else [against]
        valid_upstreams: list[dict[str, Any]] = []
        for index, upstream in enumerate(upstreams):
            if not isinstance(upstream, dict):
                errors.append(f"$against[{index}]: upstream artifact must be a JSON object")
                continue
            valid_upstreams.append(upstream)
            errors.extend(cross_contract_errors(normalized, data, upstream))
        errors.extend(cross_contract_set_errors(normalized, data, valid_upstreams))
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True, help="schema name, with or without .schema.json")
    parser.add_argument("--input", default="-", help="JSON file, or - for stdin (default)")
    parser.add_argument(
        "--against",
        action="append",
        help="repeatable upstream JSON artifact for auto-detected lineage checks",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        data = load_json(args.input)
        loaded_upstreams = [load_json(path) for path in args.against] if args.against else []
        against: Any | None
        if not loaded_upstreams:
            against = None
        elif len(loaded_upstreams) == 1:
            against = loaded_upstreams[0]
        else:
            against = loaded_upstreams
        errors = validate_instance(args.schema, data, against)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"validation setup error: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    normalized = schema_path(args.schema).name.removesuffix(".schema.json")
    print(f"VALID {normalized}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
