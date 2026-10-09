#!/usr/bin/env python3
"""Create factual ASR evidence for a locally downloaded reference candidate.

This helper extracts and transcribes speech.  It deliberately makes no claims
about music quality, mood, intelligibility-by-ear, or creative fit.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave
from datetime import datetime, timezone


SCHEMA_VERSION = "valmera-local-transcript-v1"
DEFAULT_REPO = Path("/Users/muslimshmary/Documents/hustler-server")


class CandidateTranscriptError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise CandidateTranscriptError(f"required executable not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()[-1200:]
        raise CandidateTranscriptError(f"{command[0]} failed: {detail}") from exc


def probe_media(path: Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "format=duration:stream=index,codec_type,codec_name,start_time,duration",
        "-of", "json", str(path),
    ])
    try:
        payload = json.loads(result.stdout)
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CandidateTranscriptError("ffprobe returned invalid duration metadata") from exc
    audio_streams = [
        stream for stream in payload.get("streams") or []
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]
    audio = audio_streams[0] if audio_streams else None
    if duration <= 0:
        raise CandidateTranscriptError("candidate duration is unavailable or zero")
    if audio is None:
        return {"duration_s": round(duration, 3), "has_audio": False, "audio_stream": None}

    def optional_float(value: object) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return round(parsed, 3)

    start = optional_float(audio.get("start_time"))
    return {
        "duration_s": round(duration, 3),
        "has_audio": True,
        "audio_stream": {
            "stream_index": int(audio.get("index", 0)),
            "codec_name": str(audio.get("codec_name") or "unknown"),
            "source_start_s": max(0.0, start) if start is not None else None,
            "declared_duration_s": optional_float(audio.get("duration")),
        },
    }


def extract_wav(media_path: Path, wav_path: Path) -> None:
    _run([
        "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(media_path),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(wav_path),
    ])
    if not wav_path.exists() or wav_path.stat().st_size == 0:
        raise CandidateTranscriptError("audio extraction produced no bytes")


def pcm_wav_duration_s(wav_path: Path) -> float:
    try:
        with wave.open(str(wav_path), "rb") as source:
            rate = source.getframerate()
            frames = source.getnframes()
    except (OSError, wave.Error) as exc:
        raise CandidateTranscriptError("extracted PCM duration is unreadable") from exc
    if rate <= 0 or frames <= 0:
        raise CandidateTranscriptError("extracted PCM duration is zero")
    return round(frames / float(rate), 3)


def _media_timeline_coverage(media_duration_s: float, audio_start_s: float, audio_duration_s: float) -> tuple[list, list]:
    start = max(0.0, min(media_duration_s, audio_start_s))
    end = max(start, min(media_duration_s, start + audio_duration_s))
    coverage = [[round(start, 3), round(end, 3)]] if end > start else []
    gaps = []
    if start > 0:
        gaps.append([0.0, round(start, 3)])
    if end < media_duration_s:
        gaps.append([round(end, 3), round(media_duration_s, 3)])
    return coverage, gaps


def faster_whisper_version() -> str:
    try:
        return importlib.metadata.version("faster-whisper")
    except importlib.metadata.PackageNotFoundError as exc:
        raise CandidateTranscriptError("faster-whisper package version is unavailable") from exc


def model_artifact_lineage(model: str) -> dict:
    model_path = Path(model).expanduser()
    if model_path.exists():
        return {
            "model_artifact_id": str(model_path.resolve()),
            "model_revision": None,
            "model_revision_status": "local_path_unhashed",
        }
    repo_id = f"Systran/faster-whisper-{model}"
    ref = Path.home() / ".cache" / "huggingface" / "hub" / f"models--Systran--faster-whisper-{model}" / "refs" / "main"
    revision = None
    if ref.is_file():
        candidate = ref.read_text(encoding="utf-8").strip()
        if candidate and all(char in "0123456789abcdefABCDEF" for char in candidate):
            revision = candidate.lower()
    return {
        "model_artifact_id": repo_id,
        "model_revision": revision,
        "model_revision_status": "resolved_cache_ref" if revision else "alias_only_unpinned",
    }


def decode_config_from_worker(worker_transcribe: object, model: str) -> dict:
    config = worker_transcribe.config
    initial_prompt = str(config.WHISPER_INITIAL_PROMPT or "").strip()
    hotwords = str(config.WHISPER_HOTWORDS or "").strip()
    return {
        "model": model,
        "device": str(config.WHISPER_DEVICE),
        "compute_type": str(config.WHISPER_COMPUTE),
        "beam_size": int(config.WHISPER_BEAM_SIZE),
        "temperature_ladder": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "compression_ratio_threshold": config.WHISPER_COMPRESSION_RATIO_THRESHOLD,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": False,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 500, "speech_pad_ms": 400},
        "initial_prompt_sha256": sha256_text(initial_prompt),
        "hotwords_sha256": sha256_text(hotwords),
    }


def resolve_repo(explicit: str | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.getenv("VALMERA_REPO"):
        candidates.append(Path(os.environ["VALMERA_REPO"]).expanduser())
    candidates.append(DEFAULT_REPO)
    for candidate in candidates:
        if (candidate / "worker" / "transcribe.py").is_file():
            return candidate.resolve()
    raise CandidateTranscriptError(
        "Valmera repo not found; pass --repo or set VALMERA_REPO"
    )


def ensure_transcription_runtime(repo: Path, original_argv: list[str]) -> None:
    try:
        import faster_whisper  # noqa: F401
        return
    except ImportError:
        pass
    runtime = repo / "venv" / "bin" / "python"
    if not runtime.is_file():
        raise CandidateTranscriptError(
            "faster-whisper is unavailable and <repo>/venv/bin/python was not found"
        )
    if os.getenv("VALMERA_TRANSCRIBE_REEXEC") == "1":
        raise CandidateTranscriptError("Valmera venv does not provide faster-whisper")
    env = dict(os.environ)
    env["VALMERA_TRANSCRIBE_REEXEC"] = "1"
    os.execve(str(runtime), [str(runtime), str(Path(__file__).resolve()), *original_argv], env)


def transcribe_with_worker(wav_path: Path, repo: Path, model: str) -> tuple[list, list, str, list[str], dict]:
    worker_path = str(repo / "worker")
    if worker_path not in sys.path:
        sys.path.insert(0, worker_path)
    os.environ["TRANSCRIBER"] = "whisper"
    os.environ["WHISPER_MODEL"] = model
    try:
        import transcribe as worker_transcribe
    except Exception as exc:
        raise CandidateTranscriptError(f"could not load Valmera transcription code: {exc}") from exc
    warnings: list[str] = []
    try:
        words, language = worker_transcribe.transcribe(str(wav_path), warnings=warnings)
        sentences = worker_transcribe.group_sentences(words)
    except Exception as exc:
        raise CandidateTranscriptError(f"ASR failed: {exc}") from exc
    word_rows = [
        {
            "text": str(word.w),
            "start_s": round(float(word.t0), 3),
            "end_s": round(float(word.t1), 3),
        }
        for word in words
    ]
    sentence_rows = [
        {
            "text": str(sentence.text),
            "start_s": round(float(sentence.t0), 3),
            "end_s": round(float(sentence.t1), 3),
        }
        for sentence in sentences
    ]
    return (
        word_rows,
        sentence_rows,
        str(language or "unknown"),
        warnings,
        decode_config_from_worker(worker_transcribe, model),
    )


def build_evidence(media_path: Path, repo: Path, model: str) -> dict:
    if not media_path.is_file():
        raise CandidateTranscriptError(f"candidate file does not exist: {media_path}")
    probe = probe_media(media_path)
    if not probe["has_audio"]:
        raise CandidateTranscriptError("candidate has no audio stream to transcribe")
    with tempfile.TemporaryDirectory(prefix="valmera-reference-asr-") as temp_dir:
        wav_path = Path(temp_dir) / "candidate.wav"
        extract_wav(media_path, wav_path)
        extracted_duration = pcm_wav_duration_s(wav_path)
        audio_sha = sha256_file(wav_path)
        words, sentences, language, warnings, decode_config = transcribe_with_worker(wav_path, repo, model)
    transcript_text = " ".join(row["text"] for row in words).strip()
    if not words:
        warnings = [*warnings, "ASR processed the complete audio stream but detected no speech or lyrics"]
    duration = probe["duration_s"]
    audio_stream = dict(probe["audio_stream"])
    audio_start = audio_stream.get("source_start_s")
    if audio_start is None:
        warnings = [*warnings, "audio stream start time was unavailable; media-timeline coverage uses 0.0 as an explicit fallback"]
        audio_start = 0.0
        audio_stream["start_time_fallback"] = "zero_when_ffprobe_unavailable"
    else:
        audio_stream["start_time_fallback"] = None
    timeline_coverage, timeline_gaps = _media_timeline_coverage(duration, audio_start, extracted_duration)
    audio_stream.update({
        "extracted_pcm_duration_s": extracted_duration,
        "media_timeline_coverage": timeline_coverage,
        "media_timeline_gaps": timeline_gaps,
    })
    media_sha = sha256_file(media_path)
    transcript_sha = sha256_text(transcript_text)
    helper_sha = sha256_file(Path(__file__).resolve())
    transcriber_sha = sha256_file(repo / "worker" / "transcribe.py")
    model_lineage = model_artifact_lineage(model)
    decode_config_sha = sha256_text(json.dumps(decode_config, sort_keys=True, separators=(",", ":")))
    evidence_fingerprint = sha256_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "media_sha256": media_sha,
        "audio_pcm_sha256": audio_sha,
        "transcript_text_sha256": transcript_sha,
        "helper_sha256": helper_sha,
        "worker_transcriber_sha256": transcriber_sha,
        "model_artifact_id": model_lineage["model_artifact_id"],
        "model_revision": model_lineage["model_revision"],
        "decode_config_sha256": decode_config_sha,
    }, sort_keys=True, separators=(",", ":")))
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": "transcript-evidence:" + evidence_fingerprint.removeprefix("sha256:"),
        "status": "complete",
        "status_semantics": "asr_completed_over_the_extracted_audio_stream_not_transcript_accuracy",
        "evidence_role": "factual_transcription_only",
        "creative_decision_made": False,
        "media_filename": media_path.name,
        "media_sha256": media_sha,
        "audio_pcm_sha256": audio_sha,
        "duration_s": duration,
        "has_audio_stream": True,
        "audio_stream": audio_stream,
        "tool_lineage": {
            "helper_sha256": helper_sha,
            "worker_transcriber_sha256": transcriber_sha,
            "faster_whisper_version": faster_whisper_version(),
            **model_lineage,
            "decode_config": decode_config,
            "decode_config_sha256": decode_config_sha,
        },
        "asr": {
            "engine": "faster-whisper",
            "model": model,
            "language": language,
            "timestamp_origin": "extracted_audio_stream_start",
            "media_time_offset_s": audio_start,
            "processed_coverage": [[0.0, extracted_duration]],
            "processing_gaps": [],
            "words": words,
            "sentences": sentences,
            "transcript_text_sha256": transcript_sha,
            "warnings": warnings,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        },
        "claims_not_made": [
            "direct_audio_perception",
            "music_vibe",
            "music_quality",
            "subjective_intelligibility",
            "emotional_fit",
            "auditory_review",
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a local reference candidate without making creative audio claims."
    )
    parser.add_argument("media", help="Local downloaded video/audio path")
    parser.add_argument("--output", help="Write JSON to this path instead of stdout")
    parser.add_argument("--repo", help="Valmera repository root")
    parser.add_argument("--model", default=os.getenv("VALMERA_REFERENCE_WHISPER_MODEL", "small"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(raw_argv)
    try:
        repo = resolve_repo(args.repo)
        ensure_transcription_runtime(repo, raw_argv)
        evidence = build_evidence(Path(args.media).expanduser().resolve(), repo, args.model)
    except CandidateTranscriptError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 2
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
