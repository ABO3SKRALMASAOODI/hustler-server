#!/usr/bin/env python3
"""Executable fixtures and adversarial tests for Valmera's v2 contracts."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "validate_contract", SCRIPT_DIR / "validate_contract.py"
)
assert SPEC and SPEC.loader
validate_contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validate_contract
SPEC.loader.exec_module(validate_contract)

SOURCE_ID = "abcdefghijk"
REFERENCE_ID = "lmnopqrstuv"
RAW_SHA = "b" * 64
SHA = "sha256:" + "a" * 64
PREVIEW_SHA = "sha256:" + "9" * 64
PCM_SHA = "sha256:" + "8" * 64
TRANSCRIPT_SHA = "sha256:" + "7" * 64
EVIDENCE_CLAIMS = [
    "direct_audio_perception",
    "music_vibe",
    "music_quality",
    "subjective_intelligibility",
    "emotional_fit",
    "auditory_review",
]


def _sha(character: str) -> str:
    return "sha256:" + character * 64


def _samples(duration: float, step: float) -> list[float]:
    values: list[float] = []
    cursor = 0.0
    while cursor < duration:
        values.append(round(cursor, 6))
        cursor += step
    if not values or values[-1] != duration:
        values.append(duration)
    return values


def _fingerprint(kind: str, value: dict, field: str) -> dict:
    value[field] = validate_contract._canonical_fingerprint(kind, value)
    return value


def _decode_config() -> dict:
    return {
        "model": "small",
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": 5,
        "temperature_ladder": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": False,
        "vad_filter": True,
        "vad_parameters": {
            "min_silence_duration_ms": 500,
            "speech_pad_ms": 400,
        },
        "initial_prompt_sha256": _sha("1"),
        "hotwords_sha256": _sha("2"),
    }


def _tool_lineage() -> dict:
    decode = _decode_config()
    return {
        "helper_sha256": _sha("3"),
        "worker_transcriber_sha256": _sha("4"),
        "faster_whisper_version": "1.2.3",
        "model_artifact_id": "Systran/faster-whisper-small",
        "model_revision": "model-revision-1",
        "model_revision_status": "resolved_cache_ref",
        "decode_config": decode,
        "decode_config_sha256": validate_contract._canonical_object_digest(decode),
    }


def _source_visual_inspection() -> dict:
    return {
        "method": "indexed_filmstrip_and_shot_pages",
        "surface": "valmera_source_index",
        "media_sha256": RAW_SHA,
        "duration_s": 100.0,
        "coverage": [[0.0, 100.0]],
        "gaps": [],
        "sampled_frame_times": _samples(100.0, 25.0),
        "configured_sample_step_s": 25.0,
        "max_sample_gap_s": 25.0,
        "shot_index_exhausted": True,
        "page_cursors_exhausted": True,
        "completed_at": "2026-08-18T09:40:00Z",
    }


def _source_speech_transcript() -> dict:
    return {
        "source": "valmera_indexed_transcript",
        "media_sha256": RAW_SHA,
        "duration_s": 100.0,
        "coverage": [[0.0, 100.0]],
        "gaps": [],
        "text_sha256": TRANSCRIPT_SHA,
        "completed_at": "2026-08-18T09:41:00Z",
    }


def selection() -> dict:
    value = {
        "schema_version": "2",
        "parent_project_id": 123,
        "selected_by": "coordinator",
        "source_youtube_video_id": SOURCE_ID,
        "source_sha256": RAW_SHA,
        "source_duration_s": 100.0,
        "source_visual_inspection": _source_visual_inspection(),
        "source_speech_transcript": _source_speech_transcript(),
        "clips": [
            {
                "rank": 1,
                "start": 10.0,
                "end": 30.0,
                "title": "A complete idea",
                "hook": "What changed everything?",
                "score": 92,
                "story": {
                    "setup": "The old approach failed.",
                    "development": "A new constraint changed the design.",
                    "payoff": "The result finally worked.",
                },
                "opening_line": "What changed everything?",
                "closing_line": "That is when it worked.",
                "selection_reason": "Complete, non-overlapping micro-story.",
            }
        ],
        "selection_fingerprint": SHA,
        "coordinator_approved": True,
        "abstained": False,
        "abstain_reason": "",
    }
    return _fingerprint("selection", value, "selection_fingerprint")


def _local_reference_transcript() -> dict:
    decode = _decode_config()
    return {
        "schema_version": "valmera-local-transcript-v1",
        "status": "complete",
        "evidence_id": "transcript-evidence:" + "c" * 64,
        "status_semantics": "asr_completed_over_the_extracted_audio_stream_not_transcript_accuracy",
        "evidence_role": "factual_transcription_only",
        "creative_decision_made": False,
        "media_filename": "reference.mp4",
        "media_sha256": "sha256:" + RAW_SHA,
        "audio_pcm_sha256": _sha("d"),
        "duration_s": 40.0,
        "has_audio_stream": True,
        "audio_stream": {
            "stream_index": 0,
            "codec_name": "aac",
            "source_start_s": 0.0,
            "declared_duration_s": 40.0,
            "start_time_fallback": None,
            "extracted_pcm_duration_s": 40.0,
            "media_timeline_coverage": [[0.0, 40.0]],
            "media_timeline_gaps": [],
        },
        "tool_lineage": {
            "helper_sha256": _sha("3"),
            "worker_transcriber_sha256": _sha("4"),
            "faster_whisper_version": "1.2.3",
            "model_artifact_id": "Systran/faster-whisper-small",
            "model_revision": "model-revision-1",
            "model_revision_status": "resolved_cache_ref",
            "decode_config": decode,
            "decode_config_sha256": validate_contract._canonical_object_digest(decode),
        },
        "asr": {
            "engine": "faster-whisper",
            "model": "small",
            "language": "en",
            "timestamp_origin": "extracted_audio_stream_start",
            "media_time_offset_s": 0.0,
            "processed_coverage": [[0.0, 40.0]],
            "processing_gaps": [],
            "words": [{"text": "We changed the design", "start_s": 0.2, "end_s": 1.4}],
            "sentences": [{"text": "We changed the design.", "start_s": 0.2, "end_s": 1.4}],
            "transcript_text_sha256": _sha("e"),
            "warnings": [],
            "completed_at": "2026-08-18T09:56:00Z",
        },
        "claims_not_made": copy.deepcopy(EVIDENCE_CLAIMS),
    }


def _postupload_transcript() -> dict:
    return {
        "source": "valmera_indexed_transcript",
        "status": "complete",
        "evidence_id": "transcript-evidence:" + "f" * 64,
        "status_semantics": "processing_complete_means_declared_coverage_not_accuracy_or_auditory_review",
        "evidence_role": "factual_transcription_and_timing_only",
        "creative_decision_made": False,
        "parent_reference_asset_id": 401,
        "role": "shorts_reference",
        "storage_key": "clips/123/reference.mp4",
        "media_sha256": RAW_SHA,
        "duration_s": 40.0,
        "processed_coverage": [[0.0, 40.0]],
        "processing_gaps": [],
        "transcript_text_sha256": _sha("e"),
        "completed_at": "2026-08-18T10:02:00Z",
        "claims_not_made": copy.deepcopy(EVIDENCE_CLAIMS),
    }


def reference_profile() -> dict:
    rationale = "The visible proof, compact story arc, and public engagement make this transferable."
    return {
        "reference_profile_version": "2",
        "parent_reference_asset_id": 401,
        "youtube_video_id": REFERENCE_ID,
        "reference_sha256": RAW_SHA,
        "reference_duration_s": 40.0,
        "preselection_visual_inspection": {
            "youtube_video_id": REFERENCE_ID,
            "evidence_id": "reference-visual-preselection",
            "method": "frame_sampling",
            "surface": "youtube_ordinary_player",
            "media_sha256": None,
            "duration_s": 40.0,
            "coverage": [[0.0, 40.0]],
            "gaps": [],
            "sampled_frame_times": _samples(40.0, 1.0),
            "configured_sample_step_s": 1.0,
            "max_sample_gap_s": 1.0,
            "sample_schedule_complete": True,
            "observed_boundary_checks_complete": True,
            "completed_at": "2026-08-18T09:55:00Z",
        },
        "preselection_speech_transcript": _local_reference_transcript(),
        "music_identity": {
            "status": "identified",
            "title": "Reference Bed",
            "artist": "Example Artist",
            "evidence": [
                {
                    "evidence_id": "music-evidence-reference-label",
                    "source": "source_page_metadata",
                    "detail": "The ordinary source page visibly names the track and artist.",
                    "source_url": f"https://www.youtube.com/shorts/{REFERENCE_ID}",
                    "observed_at": "2026-08-18T09:54:00Z",
                }
            ],
        },
        "postupload_visual_inspection": {
            "method": "frame_sampling",
            "surface": "valmera_reference_asset",
            "evidence_id": "reference-visual-postupload",
            "parent_reference_asset_id": 401,
            "role": "shorts_reference",
            "storage_key": "clips/123/reference.mp4",
            "media_sha256": RAW_SHA,
            "duration_s": 40.0,
            "coverage": [[0.0, 40.0]],
            "gaps": [],
            "sampled_frame_times": _samples(40.0, 1.0),
            "configured_sample_step_s": 1.0,
            "max_sample_gap_s": 1.0,
            "sample_schedule_complete": True,
            "observed_boundary_checks": [
                {
                    "boundary_time_s": 12.0,
                    "kind": "visible_cut",
                    "checked_frame_times": [11.9, 12.1],
                    "result": "no_visible_leak",
                }
            ],
            "unknown_boundaries_not_proven_absent": True,
            "completed_at": "2026-08-18T10:02:00Z",
        },
        "postupload_speech_transcript": _postupload_transcript(),
        "selected_at": "2026-08-18T09:59:00Z",
        "selection_rationale": rationale,
        "selection_decision": {
            "selected_by": "coordinator_codex",
            "claim_type": "inference",
            "decision_basis": "codex_visual_transcript_metadata_measurement_inference",
            "evidence_ids": [
                "reference-visual-preselection",
                "transcript-evidence:" + "c" * 64,
                "music-evidence-reference-label",
                "reference-signal-analysis",
                "engagement-evidence-views",
            ],
            "rationale": rationale,
            "confidence": 0.86,
            "limitations": ["Metadata and sampled frames do not establish subjective audio experience."],
        },
        "signal_analysis": {
            "status": "complete",
            "evidence_id": "reference-signal-analysis",
            "scope": "full_mixed_soundtrack",
            "method": "ffmpeg_ebur128_and_silencedetect",
            "tool_version": "ffmpeg 7.1",
            "media_sha256": RAW_SHA,
            "duration_s": 40.0,
            "aggregate_signal_present": True,
            "integrated_lufs": -15.8,
            "true_peak_dbtp": -1.2,
            "silence_intervals": [],
            "evidence_digest": _sha("5"),
            "completed_at": "2026-08-18T09:57:00Z",
            "unavailable_reason": None,
        },
        "engagement_observations": [
            {
                "evidence_id": "engagement-evidence-views",
                "metric": "views",
                "observed_value": "1.2M",
                "observed_at": "2026-08-18T09:53:00Z",
                "evidence_url": f"https://www.youtube.com/shorts/{REFERENCE_ID}",
            }
        ],
        "virality_qualification_rationale": "The visible seven-figure view count is a strong engagement signal.",
        "observations": [
            {
                "observation_id": "ref-hook-01",
                "dimension": "hook",
                "start_s": 0.0,
                "end_s": 2.5,
                "statement": "Sampled frames and transcript show the premise before the first cut.",
                "evidence_kind": "visual",
                "evidence_ids": ["reference-visual-preselection"],
                "decision_basis": "sampled_frame_observation",
                "rationale": "The sampled opening frames visibly establish the premise.",
                "confidence": 0.9,
            },
            {
                "observation_id": "ref-music-01",
                "dimension": "music",
                "start_s": None,
                "end_s": None,
                "statement": "The named music identity is a metadata clue for later treatment inference.",
                "evidence_kind": "music_metadata_inference",
                "evidence_ids": ["music-evidence-reference-label"],
                "decision_basis": "codex_visual_transcript_metadata_measurement_inference",
                "rationale": "Only visible identity metadata is asserted; no sonic properties are claimed.",
                "confidence": 0.45,
            },
        ],
    }


def _candidate(candidate_id: str, mode: str) -> dict:
    evidence_ids = ["shot:source-2"] if mode == "conversation" else [
        "transcript:10.0-30.0",
        "shot:source-2",
    ]
    return {
        "candidate_id": candidate_id,
        "dominant_mode": mode,
        "secondary_mode": None,
        "confidence": 0.6,
        "evidence_ids": evidence_ids,
        "treatment_name": f"{mode} treatment",
        "editorial_thesis": "Let the evidence determine visual intensity.",
        "risk_if_wrong": "The edit could fight the speaker.",
    }


def child_assignment() -> dict:
    chosen = selection()["clips"][0]
    value = {
        "assignment_schema_version": "valmera-editorial-child-assignment-v2",
        "taxonomy_version": "valmera-editorial-taxonomy-v1",
        "prompt_version": "valmera-shorts-editor-v6",
        "assignment_id": "topic-run:child-456:assignment-1",
        "assignment_status": "ready_for_editor",
        "pre_mutation_recast_status": "not_required",
        "candidate_slate": [],
        "blocked_reason": None,
        "blocked_evidence_ids": [],
        "run_id": "topic-run",
        "parent_project_id": 123,
        "child_project_id": 456,
        "card": 1,
        "title": chosen["title"],
        "hook": chosen["hook"],
        "story": copy.deepcopy(chosen["story"]),
        "opening_line": chosen["opening_line"],
        "closing_line": chosen["closing_line"],
        "selection_reason": chosen["selection_reason"],
        "audience_or_style_note": "Keep the conversation authentic.",
        "treatment_contract_version": "2",
        "reference_profile_version": None,
        "assignment_input_fingerprint": SHA,
        "source": {
            "youtube_video_id": SOURCE_ID,
            "asset_id": 400,
            "source_duration_s": 100.0,
            "approved_start_s": 10.0,
            "approved_end_s": 30.0,
            "seeded_child_start_s": 10.0,
            "seeded_child_end_s": 30.0,
            "seed_snap_reason": "none",
            "seed_range_verified_by": "authoritative_child_edl",
            "seed_range_evidence_digest": _sha("6"),
        },
        "story_profile": {
            "classification_status": "clear",
            "confidence": 0.9,
            "dominant_mode": "conversation",
            "secondary_mode": None,
            "audience_promise": "Understand why the design changed.",
            "emotional_trajectory": "uncertainty to clarity",
            "evidence_need": ["speaker conviction"],
            "performance_strength": "strong",
            "pace": "natural",
            "source_picture_fitness": "strong",
        },
        "treatment": {
            "name": "speaker-led clarity",
            "decision_basis": [
                {
                    "evidence_ids": ["transcript:10.0-20.0"],
                    "fact": "The transcript and sampled picture support a speaker-led turn.",
                    "editorial_implication": "Use restrained proof shots.",
                }
            ],
            "opening": "Begin on the speaker.",
            "a_roll_b_roll_strategy": "Mostly A-roll with selective proof.",
            "visual_motifs": [],
            "energy_curve": "steady and attentive",
            "caption_rhythm": "one or two words on semantic emphasis",
            "music_policy": "none",
            "music_brief": None,
            "music_entry_policy": "none",
            "assigned_music_start_s": None,
            "assigned_music_end_s": None,
            "no_music_justification": "Silence protects the authentic exchange.",
            "transition_grammar": "clean hard cuts",
            "color_texture": "natural",
            "ending": "Hold on the speaker's final line.",
            "must_avoid": ["spectacle"],
        },
        "reference_transfer": {
            "status": "not_supplied",
            "parent_reference_asset_id": None,
            "child_reference_asset_id": None,
            "reference_storage_key": None,
            "reference_sha256": None,
            "role_verified": False,
            "ready": False,
            "reference_youtube_video_id": None,
            "reference_observation_ids_considered": [],
            "inapplicability_rationale": None,
            "transfer": [],
            "adapt": [],
            "reject": [],
        },
        "visual_identity": {
            "name": "quiet technical clarity",
            "motifs": [],
            "energy_curve": "steady",
            "sibling_motifs_to_avoid": [],
        },
        "sibling_asset_windows_to_avoid": [],
        "sibling_music_tracks_to_avoid": [],
    }
    return _fingerprint("assignment", value, "assignment_input_fingerprint")


def ambiguous_assignment() -> dict:
    value = child_assignment()
    value["assignment_status"] = "requires_pre_mutation_recast"
    value["pre_mutation_recast_status"] = "pending_parent_approval"
    value["candidate_slate"] = [
        _candidate("cast-conversation", "conversation"),
        _candidate("cast-explanation", "explanation"),
    ]
    value["story_profile"].update(
        {
            "classification_status": "ambiguous",
            "confidence": 0.45,
            "dominant_mode": None,
            "secondary_mode": None,
        }
    )
    return _fingerprint("assignment", value, "assignment_input_fingerprint")


def music_assignment(entry_policy: str = "hook", start_s: float = 0.0) -> dict:
    value = child_assignment()
    value["treatment"].update(
        {
            "music_policy": "subtle_bed",
            "music_brief": "Warm restrained instrumental support with no vocals.",
            "music_entry_policy": entry_policy,
            "assigned_music_start_s": start_s,
            "assigned_music_end_s": 20.0,
            "no_music_justification": None,
        }
    )
    return _fingerprint("assignment", value, "assignment_input_fingerprint")


def referenced_assignment() -> dict:
    value = child_assignment()
    value["reference_profile_version"] = "2"
    value["reference_transfer"] = {
        "status": "applicable",
        "parent_reference_asset_id": 401,
        "child_reference_asset_id": 501,
        "reference_storage_key": "clips/123/reference.mp4",
        "reference_sha256": RAW_SHA,
        "role_verified": True,
        "ready": True,
        "reference_youtube_video_id": REFERENCE_ID,
        "reference_observation_ids_considered": ["ref-hook-01"],
        "inapplicability_rationale": None,
        "transfer": [
            {
                "reference_observation_ids": ["ref-hook-01"],
                "story_evidence_ids": ["transcript:10.0-20.0"],
                "relationship": "The premise lands before the first cut.",
                "application": "Keep the opening premise immediate.",
            }
        ],
        "adapt": [],
        "reject": [],
    }
    return _fingerprint("assignment", value, "assignment_input_fingerprint")


def inapplicable_reference_assignment() -> dict:
    value = child_assignment()
    value["reference_profile_version"] = "2"
    value["reference_transfer"] = {
        "status": "inapplicable",
        "parent_reference_asset_id": 401,
        "child_reference_asset_id": None,
        "reference_storage_key": None,
        "reference_sha256": RAW_SHA,
        "role_verified": True,
        "ready": False,
        "reference_youtube_video_id": REFERENCE_ID,
        "reference_observation_ids_considered": ["ref-hook-01"],
        "inapplicability_rationale": "The reference spectacle fights this quiet exchange.",
        "transfer": [],
        "adapt": [],
        "reject": [
            {
                "reference_observation_ids": ["ref-hook-01"],
                "story_evidence_ids": ["transcript:10.0-20.0"],
                "relationship": "The reference begins with a spectacle montage.",
                "application": "Reject that montage because the speaker performance is the hook.",
            }
        ],
    }
    return _fingerprint("assignment", value, "assignment_input_fingerprint")


def recast_result() -> dict:
    assignment = ambiguous_assignment()
    value = {
        "schema_version": "valmera-pre-mutation-recast-v2",
        "assignment_schema_version": "valmera-editorial-child-assignment-v2",
        "taxonomy_version": "valmera-editorial-taxonomy-v1",
        "prompt_version": "valmera-shorts-editor-v6",
        "treatment_contract_version": "2",
        "reference_profile_version": None,
        "assignment_id": assignment["assignment_id"],
        "assignment_input_fingerprint": assignment["assignment_input_fingerprint"],
        "recast_input_fingerprint": SHA,
        "run_id": assignment["run_id"],
        "parent_project_id": assignment["parent_project_id"],
        "child_project_id": assignment["child_project_id"],
        "status": "awaiting_parent_approval",
        "mutation_performed": False,
        "inspected_evidence_ids": ["transcript:10.0-30.0", "shot:source-2"],
        "candidate_assessments": [
            {
                "candidate_id": "cast-conversation",
                "dominant_mode": "conversation",
                "secondary_mode": None,
                "evidence_matches": True,
                "supporting_evidence_ids": ["shot:source-2"],
                "contradicting_evidence_ids": [],
                "recommendation": "recommended",
                "rationale": "The sampled performance carries the explanation.",
            },
            {
                "candidate_id": "cast-explanation",
                "dominant_mode": "explanation",
                "secondary_mode": None,
                "evidence_matches": True,
                "supporting_evidence_ids": ["transcript:10.0-30.0"],
                "contradicting_evidence_ids": ["shot:source-2"],
                "recommendation": "viable",
                "rationale": "Evidence shots help, but should remain selective.",
            },
        ],
        "recommended_candidate_id": "cast-conversation",
        "approved_by": None,
        "approved_candidate_id": None,
        "approved_cast": None,
        "approved_treatment_delta": None,
        "contradiction_summary": None,
        "outstanding_job_ids": [],
    }
    return _fingerprint("recast", value, "recast_input_fingerprint")


def approved_recast() -> dict:
    value = recast_result()
    value.update(
        {
            "status": "approved",
            "approved_by": "coordinator",
            "approved_candidate_id": "cast-conversation",
            "approved_cast": {
                "classification_status": "clear",
                "dominant_mode": "conversation",
                "secondary_mode": None,
            },
            "approved_treatment_delta": {
                "name": "conversation treatment",
                "visual_identity": "evidence-led conversation",
                "visual_motifs": ["speaker conviction"],
                "music_policy": "none",
                "music_brief": None,
                "music_entry_policy": "none",
                "assigned_music_start_s": None,
                "assigned_music_end_s": None,
                "no_music_justification": "The unresolved conversational tension needs silence.",
            },
        }
    )
    return value


def editor_issue(
    gate: str = "render",
    reason_code: str = "other",
    evidence: str = "The rendered evidence failed the gate.",
    start_s: float = 0.0,
    end_s: float = 20.0,
) -> dict:
    return {
        "gate": gate,
        "reason_code": reason_code,
        "start_s": start_s,
        "end_s": end_s,
        "evidence": evidence,
        "required_change": "Repair and render a new current preview.",
    }


def qc_violation(
    gate: str = "render",
    reason_code: str = "other",
    evidence: str = "Independent QC observed a failed gate.",
    start_s: float = 0.0,
    end_s: float = 20.0,
) -> dict:
    return {
        "gate": gate,
        "reason_code": reason_code,
        "start_s": start_s,
        "end_s": end_s,
        "evidence": evidence,
        "required_change": "Repair and return a new current render.",
    }


def visual_provenance(*, inspected: bool | None = True) -> dict:
    return {
        "asset_key": "clips/123/broll.mp4",
        "provider": "youtube",
        "canonical_source_id": "zyxwvutsrqp",
        "sha256": None,
        "canonical_url": "https://www.youtube.com/watch?v=zyxwvutsrqp",
        "title": "Official launch",
        "author_or_uploader": "Official channel",
        "output_start_s": 2.0,
        "output_end_s": 6.0,
        "source_start_s": 8.0,
        "source_duration_s": 4.0,
        "raw_automated_license_status": None,
        "available_usage_evidence": "not_assessed",
        "usage_evidence_detail": None,
        "usage_evidence_source_url": None,
        "observed_at": "2026-08-18T10:00:00Z",
        "required_attribution": None,
        "known_usage_restriction": None,
        "visually_inspected": inspected,
    }


def visual_provenance_record(*, inspected: bool | None = True) -> dict:
    return visual_provenance(inspected=inspected)


def preview_provenance(*, edl_version: int = 3, parent_qc: bool = False) -> dict:
    return {
        "retrieval": {
            "source_tool": "watch_video",
            "render": True,
            "frames": False,
            "delivery": "url",
            "windowed_or_transcoded": False,
            "retrieved_at": (
                "2026-08-18T10:20:00Z" if parent_qc else "2026-08-18T10:10:00Z"
            ),
        },
        "preview_receipt": {
            "asset_id": 701 if parent_qc else 700,
            "render_job_id": 801 if parent_qc else 800,
            "edl_version": edl_version,
            "duration_s": 20.0,
            "audio_model_review": False,
            "listen_keys_count": 0,
            "listen_clips_count": 0,
            "audio_reviewer_findings_count": 0,
            "meta_sha256": "6" * 64 if parent_qc else "5" * 64,
        },
    }


def render_visual_inspection(*, parent_qc: bool = False) -> dict:
    return {
        "status": "complete",
        "evidence_id": "visual:render-inspection",
        "method": "timestamped_frame_sampling_and_frame_level_checks",
        "surface": "downloaded_preview",
        "duration_s": 20.0,
        "coverage": [[0.0, 20.0]],
        "gaps": [],
        "sampled_frame_times": _samples(20.0, 1.0),
        "max_sample_gap_s": 1.0,
        "shot_boundaries_exhausted": True,
        "junction_checks_exhausted": True,
        "replacement_checks_exhausted": True,
        "final_tail_checks_exhausted": True,
        "evidence_digest": _sha("6" if parent_qc else "5"),
        "completed_at": (
            "2026-08-18T10:21:00Z" if parent_qc else "2026-08-18T10:11:00Z"
        ),
    }


def program_speech_transcript(*, edl_version: int = 3, parent_qc: bool = False) -> dict:
    completed_at = "2026-08-18T10:22:00Z" if parent_qc else "2026-08-18T10:12:00Z"
    return {
        "schema_version": "valmera-local-transcript-v1",
        "status": "complete",
        "evidence_id": "transcript-evidence:" + ("6" if parent_qc else "5") * 64,
        "source": "render_asr",
        "status_semantics": "asr_completed_over_the_extracted_audio_stream_not_transcript_accuracy",
        "evidence_role": "factual_transcription_and_caption_timing_only",
        "creative_decision_made": False,
        "render_edl_version": edl_version,
        "preview_media_sha256": _sha("5"),
        "audio_pcm_sha256": _sha("4" if parent_qc else "3"),
        "duration_s": 20.0,
        "has_audio_stream": True,
        "audio_stream": {
            "stream_index": 0,
            "codec_name": "aac",
            "source_start_s": 0.0,
            "declared_duration_s": 20.0,
            "start_time_fallback": None,
            "extracted_pcm_duration_s": 20.0,
            "media_timeline_coverage": [[0.0, 20.0]],
            "media_timeline_gaps": [],
        },
        "tool_lineage": _tool_lineage(),
        "asr_engine": "faster-whisper",
        "asr_model": "small",
        "asr_language": "en",
        "timestamp_origin": "extracted_audio_stream_start",
        "media_time_offset_s": 0.0,
        "processed_coverage": [[0.0, 20.0]],
        "processing_gaps": [],
        "words": [
            {"text": "What", "start_s": 0.1, "end_s": 0.4},
            {"text": "changed", "start_s": 0.4, "end_s": 0.9},
        ],
        "sentences": [
            {"text": "What changed?", "start_s": 0.1, "end_s": 0.9}
        ],
        "transcript_text_sha256": _sha("2"),
        "warnings": [],
        "caption_word_coverage": {
            "transcribed_word_count": 2,
            "captioned_word_count": 2,
            "uncovered_word_count": 0,
        },
        "evidence_digest": _sha("1" if parent_qc else "0"),
        "completed_at": completed_at,
        "limitations": [
            "server_render_sha_not_exposed",
            "ASR text may contain recognition errors and is not subjective listening.",
        ],
        "claims_not_made": copy.deepcopy(EVIDENCE_CLAIMS),
    }


def no_music_audio() -> dict:
    return {
        "music_policy": "none",
        "music_brief": None,
        "entry_policy": "none",
        "assigned_start_s": None,
        "assigned_end_s": None,
        "no_music_justification": "Silence protects the authentic exchange.",
        "music_identity": {
            "status": "not_applicable",
            "title": None,
            "artist": None,
            "evidence": [],
        },
        "selection_mode": "none",
        "candidate_metadata_shortlist": [],
        "selected_track_analysis": {
            "status": "not_applicable",
            "evidence_id": None,
            "analyzed_media_sha256": None,
            "scope": None,
            "tool_name": None,
            "tool_version_status": "not_applicable",
            "tool_version": None,
            "evidence_digest": None,
            "tempo_status": "not_applicable",
            "bpm": None,
            "bpm_confidence": None,
            "beat_count": None,
            "beat_time_sample_s": [],
            "beat_sample_scope": None,
            "energy_loudest_time_s": None,
            "energy_quietest_time_s": None,
            "energy_quietest_db_below_peak": None,
            "largest_rise_db": None,
            "largest_rise_end_s": None,
            "unexposed_facts": [],
            "unavailability_reason": None,
        },
        "edl_facts": {
            "music_item_present": False,
            "music_item_id": None,
            "music_asset_id": None,
            "track_stable_id": None,
            "music_provenance": None,
            "user_supplied_license_evidence": None,
            "attribution": None,
            "output_start_s": None,
            "output_end_s": None,
            "source_offset_s": None,
            "source_duration_s": None,
            "source_has_audio_stream": None,
            "loop": False,
            "gain_db": None,
            "ducking_enabled": False,
            "ducking_amount_db": None,
            "fade_in_s": None,
            "fade_out_s": None,
            "program_overlap_s": 0,
            "speech_overlap_s": 0,
            "source_coverage_valid": None,
            "loop_coverage_valid": None,
            "timing_matches_assignment": None,
        },
        "mix_measurements": {
            "status": "complete",
            "program_duration_s": 20.0,
            "integrated_lufs": -16.0,
            "true_peak_dbtp": -1.2,
            "loudness_range_lu": 5.5,
            "silences": [],
            "findings": [],
            "warnings": [],
            "clipping_sample_count": None,
            "clipping_evidence_source": None,
            "measurement_source": "valmera_audio_qc",
            "evidence_digest": _sha("a"),
            "measured_at": "2026-08-18T10:12:30Z",
        },
        "music_fit_inference": {
            "decided_by": "child_editor_codex",
            "decision_basis": "codex_visual_transcript_metadata_measurement_inference",
            "result": "not_applicable",
            "evidence_ids": [],
            "rationale": "The assigned treatment deliberately uses no music.",
            "confidence": None,
            "limitations": ["No direct audio perception is claimed."],
        },
        "audit": "PASS",
    }


def no_music_cta() -> dict:
    return {
        "duplicated_in_edl": False,
        "tail_eligible": False,
        "tail_configured": False,
        "preview_omits_cta": True,
        "tail_music_item_id": None,
        "tail_music_asset_id": None,
        "tail_music_muted": None,
        "tail_music_gain_db": None,
        "program_duration_s": 20.0,
        "cta_duration_s": 5,
        "music_output_start_s": None,
        "music_output_end_s": None,
        "music_source_offset_s": None,
        "music_source_duration_s": None,
        "music_source_has_audio_stream": None,
        "music_loop": None,
        "renderer_outro_contract_version": "valmera-cta-tail-v1",
        "tail_music_item_fade_out_s": None,
        "whole_program_fade_present": False,
        "tail_overlapping_music_item_count": 0,
    }


def editor_result(*, assignment: dict | None = None) -> dict:
    assignment = copy.deepcopy(assignment or child_assignment())
    source = assignment["source"]
    reference_status = assignment["reference_transfer"]["status"]
    reference_applicable = reference_status in {"applicable", "partial"}
    value = {
        "result_schema_version": "valmera-child-editor-result-v2",
        "result_fingerprint": SHA,
        "assignment_schema_version": assignment["assignment_schema_version"],
        "assignment_id": assignment["assignment_id"],
        "assignment_input_fingerprint": assignment["assignment_input_fingerprint"],
        "prompt_version": "valmera-shorts-editor-v6",
        "run_id": assignment["run_id"],
        "valmera_lease_id": "lease-child-edit-1",
        "valmera_lease_generation": 1,
        "attempt_purpose": "edit",
        "repair_round": 0,
        "parent_project_id": assignment["parent_project_id"],
        "child_project_id": assignment["child_project_id"],
        "card": assignment["card"],
        "source_lineage": copy.deepcopy(source),
        "status": "ready",
        "starting_edl_version": 2,
        "final_edl_version": 3,
        "preview_edl_version": 3,
        "preview_current": True,
        "editorial_duration_s": 20.0,
        "story": {
            "hook": "PASS",
            "context": "PASS",
            "development": "PASS",
            "payoff": "PASS",
            "power_ending": "PASS",
            "final_spoken_line": assignment["closing_line"],
        },
        "treatment": {
            "treatment_contract_version": "2",
            "reference_profile_version": assignment["reference_profile_version"],
            "name": assignment["treatment"]["name"],
            "story_profile_matches_evidence": True,
            "audience_promise_fulfilled": True,
            "emotional_trajectory_fulfilled": True,
            "reference_status": reference_status,
            "reference_applicable": reference_applicable,
            "child_reference_asset_id": assignment["reference_transfer"]["child_reference_asset_id"],
            "reference_evidence_status": (
                "complete_visual_transcript_metadata"
                if reference_status in {"applicable", "partial", "inapplicable"}
                else "not_supplied"
            ),
            "transfer_honored": True,
            "adapt_honored": True,
            "reject_honored": True,
            "generic_preset_used": False,
        },
        "visuals": {
            "classification_status": "clear",
            "dominant_mode": "conversation",
            "secondary_mode": None,
            "visual_identity": assignment["visual_identity"]["name"],
            "motifs": [],
            "broll_coverage_percent": 0.0,
            "longest_effective_broll_hold_s": 0.0,
            "holds_over_5_25_s": [],
            "full_timeline_visual_map_complete": True,
            "meaningless_visual_intervals": [],
            "empty_wall_intervals": [],
            "subject_loss_intervals": [],
            "reframe_drift_intervals": [],
            "mandatory_spans_covered": True,
            "repeated_source_windows": 0,
            "repeated_visual_concepts": 0,
            "sibling_window_reuse": 0,
            "youtube_asset_uses": 0,
            "pexels_asset_uses": 0,
            "topical_pexels_exceptions": [],
            "visual_provenance_records": [],
            "assets_with_known_usage_restrictions": [],
            "all_assets_visually_inspected": True,
            "junction_frame_check": "PASS",
            "retention_pacing_check": "PASS",
        },
        "captions": {
            "max_words_seen": 2,
            "max_lines_seen": 1,
            "density_violations": 0,
            "wrap_violations": 0,
            "uncovered_words": 0,
            "overlaps": 0,
            "warnings": 0,
            "first_caption_delay_s": 0.05,
            "rendered_pixel_check": "PASS",
        },
        "audio": no_music_audio(),
        "cta": no_music_cta(),
        "preview_render_provenance": preview_provenance(),
        "render_visual_inspection": render_visual_inspection(),
        "program_speech_transcript": program_speech_transcript(),
        "evidence_pagination": {
            "project_state_exhausted": True,
            "edl_exhausted": True,
            "kept_transcript_exhausted": True,
            "shots_exhausted": True,
            "assets_exhausted": True,
            "reference_evidence_exhausted": True,
        },
        "outstanding_job_ids": [],
        "edits": ["Preserved the speaker-led story and applied modern single-line captions."],
        "issues": [],
    }
    return _fingerprint("editor-result", value, "result_fingerprint")


def editor_result_with_reference() -> dict:
    return editor_result(assignment=referenced_assignment())


def editor_result_after_approved_recast() -> dict:
    value = editor_result(assignment=ambiguous_assignment())
    approved = approved_recast()
    delta = approved["approved_treatment_delta"]
    value["treatment"]["name"] = delta["name"]
    value["visuals"]["visual_identity"] = delta["visual_identity"]
    value["visuals"]["motifs"] = copy.deepcopy(delta["visual_motifs"])
    value["audio"]["no_music_justification"] = delta["no_music_justification"]
    return _fingerprint("editor-result", value, "result_fingerprint")


def parent_qc(*, editor: dict | None = None) -> dict:
    editor = copy.deepcopy(editor or editor_result())
    music = copy.deepcopy(editor["audio"])
    music["music_fit_inference"] = {
        "decided_by": "parent_coordinator_codex",
        "decision_basis": "codex_visual_transcript_metadata_measurement_inference",
        "result": "not_applicable",
        "evidence_ids": [],
        "rationale": "The frozen treatment intentionally contains no music.",
        "confidence": None,
        "limitations": ["No direct audio perception is claimed."],
        "independent_recomputed": True,
        "independent_evidence_digest": SHA,
    }
    provenance = preview_provenance(parent_qc=True)
    visual = render_visual_inspection(parent_qc=True)
    transcript = program_speech_transcript(parent_qc=True)
    digest_payload = {
        "preview_render_provenance": provenance,
        "render_visual_inspection": visual,
        "program_speech_transcript": transcript,
        "music_identity": music["music_identity"],
        "candidate_metadata_shortlist": music["candidate_metadata_shortlist"],
        "selected_track_analysis": music["selected_track_analysis"],
        "edl_facts": music["edl_facts"],
        "mix_measurements": music["mix_measurements"],
        "inference": {
            key: value
            for key, value in music["music_fit_inference"].items()
            if key != "independent_evidence_digest"
        },
    }
    music["music_fit_inference"]["independent_evidence_digest"] = (
        validate_contract._canonical_object_digest(digest_payload)
    )
    all_true_qc = {
        key: True
        for key in [
            "editor_result_fingerprint_verified",
            "live_edl_read",
            "preview_render_provenance_verified",
            "preview_edl_binding_verified",
            "preview_media_sha_binding_verified",
            "program_speech_transcript_read",
            "caption_audit_run",
            "music_identity_evidence_read",
            "candidate_metadata_shortlist_read",
            "selected_track_analysis_read",
            "edl_music_facts_read",
            "mix_measurements_read",
            "music_fit_independently_recomputed",
            "opening_inspected",
            "all_broll_junctions_inspected",
            "all_replacement_clips_inspected",
            "payoff_inspected",
            "final_five_seconds_inspected",
            "cta_tail_facts_read",
        ]
    }
    value = {
        "qc_schema_version": "valmera-parent-qc-v2",
        "qc_fingerprint": SHA,
        "assignment_schema_version": editor["assignment_schema_version"],
        "assignment_id": editor["assignment_id"],
        "assignment_input_fingerprint": editor["assignment_input_fingerprint"],
        "prompt_version": "valmera-shorts-editor-v6",
        "run_id": editor["run_id"],
        "parent_project_id": editor["parent_project_id"],
        "child_project_id": editor["child_project_id"],
        "card": editor["card"],
        "source_lineage": copy.deepcopy(editor["source_lineage"]),
        "editor_task_id": "editor-task-456",
        "editor_claims": validate_contract._editor_claims_from_result(editor),
        "repair_round": editor["repair_round"],
        "status": "pass",
        "live_edl_version": editor["final_edl_version"],
        "preview_edl_version": editor["final_edl_version"],
        "preview_current": True,
        "preview_render_provenance": provenance,
        "render_visual_inspection": visual,
        "program_speech_transcript": transcript,
        "evidence_pagination": {
            "project_state_exhausted": True,
            "edl_exhausted": True,
            "kept_transcript_exhausted": True,
            "shots_exhausted": True,
            "assets_exhausted": True,
            "reference_evidence_exhausted": True,
        },
        "qc_evidence": all_true_qc,
        "story": {
            "coherent": True,
            "hook_context_development_payoff": True,
            "strongest_ending": True,
            "final_spoken_line": editor["story"]["final_spoken_line"],
        },
        "treatment": {
            "treatment_contract_version": "2",
            "reference_profile_version": editor["treatment"]["reference_profile_version"],
            "name": editor["treatment"]["name"],
            "story_fit": True,
            "audience_promise_fulfilled": True,
            "emotional_trajectory_fulfilled": True,
            "reference_status": editor["treatment"]["reference_status"],
            "reference_applicable": False,
            "child_reference_asset_id": None,
            "reference_evidence_status": "not_supplied",
            "transfer_fit": True,
            "adapt_fit": True,
            "reject_fit": True,
            "generic_preset_detected": False,
        },
        "captions": {
            "single_active_layer": True,
            "single_rendered_row": True,
            "max_words_seen": 2,
            "modern_premium_style": True,
            "mechanical_audit_pass": True,
        },
        "picture": {
            "meaningful_visual_entire_timeline": True,
            "empty_or_unedited_intervals": [],
            "reframe_drift_intervals": [],
        },
        "broll": {
            "quality_matches_program": True,
            "semantically_relevant": True,
            "longest_effective_hold_s": 0.0,
            "unjustified_holds_over_5_25_s": [],
            "repeated_windows": 0,
            "junction_check_pass": True,
            "youtube_before_pexels_verified": True,
            "visual_provenance_records": [],
            "topical_pexels_exceptions": [],
            "assets_with_known_usage_restrictions": [],
            "all_assets_visually_inspected": True,
        },
        "music": music,
        "cta": copy.deepcopy(editor["cta"]),
        "violations": [],
    }
    return _fingerprint("parent-qc", value, "qc_fingerprint")


def acquisition_record() -> dict:
    selected = selection()
    profile = reference_profile()
    previsual = profile["preselection_visual_inspection"]
    pretranscript = profile["preselection_speech_transcript"]
    postvisual = profile["postupload_visual_inspection"]
    signal = profile["signal_analysis"]
    return {
        "schema_version": "valmera-topic-acquisition-v2",
        "run_id": "topic-run",
        "topic": "How constraints improve design",
        "parent_project_id": 123,
        "status": "ready_for_materialization",
        "abstained": False,
        "abstain_reason": None,
        "selected_clip_count": 1,
        "source": {
            "youtube_video_id": SOURCE_ID,
            "canonical_url": f"https://www.youtube.com/watch?v={SOURCE_ID}",
            "title": "Long source conversation",
            "ledger_instance_id": "ledger-instance-1",
            "reservation_id": "source-reservation-1",
            "fence": 1,
            "asset_id": 400,
            "sha256": RAW_SHA,
            "duration_s": 100.0,
            "ledger_status": "committed",
            "source_visual_inspection": copy.deepcopy(selected["source_visual_inspection"]),
            "source_speech_transcript": copy.deepcopy(selected["source_speech_transcript"]),
        },
        "reference": {
            "youtube_video_id": REFERENCE_ID,
            "canonical_url": f"https://www.youtube.com/watch?v={REFERENCE_ID}",
            "title": "Reference short",
            "ledger_instance_id": "ledger-instance-1",
            "reservation_id": "reference-reservation-1",
            "fence": 2,
            "asset_id": 401,
            "sha256": RAW_SHA,
            "duration_s": 40.0,
            "ledger_status": "committed",
            "role_verified": True,
            "reference_profile_version": "2",
            "preselection_visual_evidence_id": previsual["evidence_id"],
            "preselection_visual_inspection_digest": validate_contract._canonical_object_digest(previsual),
            "preselection_media_sha256": None,
            "preselection_duration_s": 40.0,
            "preselection_speech_transcript_digest": validate_contract._canonical_object_digest(pretranscript),
            "preselection_speech_evidence_id": pretranscript["evidence_id"],
            "transcript_text_sha256": pretranscript["asr"]["transcript_text_sha256"],
            "audio_pcm_sha256": pretranscript["audio_pcm_sha256"],
            "music_identity_digest": validate_contract._canonical_object_digest(profile["music_identity"]),
            "selection_decision_digest": validate_contract._canonical_object_digest(profile["selection_decision"]),
            "signal_analysis_status": signal["status"],
            "signal_analysis_evidence_id": signal["evidence_id"],
            "signal_analysis_digest": validate_contract._canonical_object_digest(signal),
            "postupload_visual_evidence_id": postvisual["evidence_id"],
            "postupload_visual_inspection_digest": validate_contract._canonical_object_digest(postvisual),
            "postupload_media_sha256": RAW_SHA,
            "postupload_duration_s": 40.0,
            "postupload_speech_transcript": copy.deepcopy(profile["postupload_speech_transcript"]),
        },
    }


def coordinator_result(*, editor: dict | None = None, qc: dict | None = None) -> dict:
    editor = copy.deepcopy(editor or editor_result())
    qc = copy.deepcopy(qc or parent_qc(editor=editor))
    selected = selection()
    return {
        "schema_version": "valmera-coordinator-run-result-v2",
        "prompt_version": "valmera-shorts-editor-v6",
        "run_id": "topic-run",
        "topic": "How constraints improve design",
        "parent_project_id": 123,
        "source_youtube_video_id": SOURCE_ID,
        "source_asset_id": 400,
        "source_sha256": RAW_SHA,
        "reference_youtube_video_id": REFERENCE_ID,
        "reference_asset_id": 401,
        "reference_sha256": RAW_SHA,
        "selection_fingerprint": selected["selection_fingerprint"],
        "status": "ready_for_studio_export",
        "abstained": False,
        "abstain_reason": None,
        "blocked_phase": None,
        "blocked_reason": None,
        "blocked_evidence": [],
        "selected_arc_count": 1,
        "accounted_arc_count": 1,
        "generated_count": 1,
        "pending_count": 0,
        "failed_generation_count": 0,
        "ready_count": 1,
        "blocked_count": 0,
        "arc_accounting": [
            {
                "arc_id": "arc-1",
                "selection_rank": 1,
                "start_s": 10.0,
                "end_s": 30.0,
                "title": "A complete idea",
                "generation_status": "generated",
                "assignment_input_fingerprint": editor["assignment_input_fingerprint"],
                "editor_result_fingerprint": editor["result_fingerprint"],
                "parent_qc_fingerprint": qc["qc_fingerprint"],
                "child_project_id": 456,
                "generation_job_id": 900,
                "generation_failure": None,
                "editor_status": "ready",
                "parent_qc_status": "pass",
                "live_edl_version": editor["final_edl_version"],
                "preview_edl_version": editor["preview_edl_version"],
                "treatment_name": editor["treatment"]["name"],
                "reference_adaptation_summary": "No reference was applied to this quiet conversational story.",
                "failed_gates": [],
            }
        ],
    }


class ContractSchemaTests(unittest.TestCase):
    def assert_valid(self, kind: str, value: dict) -> None:
        self.assertEqual([], validate_contract.validate_instance(kind, value))

    def assert_invalid(self, kind: str, value: dict) -> None:
        self.assertTrue(validate_contract.validate_instance(kind, value))

    def test_all_schemas_are_draft_2020_12_valid(self) -> None:
        for path in sorted((SCRIPT_DIR.parent / "schemas").glob("*.schema.json")):
            with self.subTest(path=path.name):
                Draft202012Validator.check_schema(json.loads(path.read_text()))

    def test_v2_happy_path_artifacts_validate(self) -> None:
        artifacts = {
            "selection": selection(),
            "reference-profile": reference_profile(),
            "child-assignment": child_assignment(),
            "pre-mutation-recast": recast_result(),
            "editor-result": editor_result(),
            "parent-qc": parent_qc(),
            "acquisition-record": acquisition_record(),
            "coordinator-run-result": coordinator_result(),
        }
        for kind, artifact in artifacts.items():
            with self.subTest(kind=kind):
                self.assert_valid(kind, artifact)

    def test_full_lineage_chain_validates(self) -> None:
        selected = selection()
        profile = reference_profile()
        acquired = acquisition_record()
        editor = editor_result()
        qc = parent_qc(editor=editor)
        final = coordinator_result(editor=editor, qc=qc)
        pairs = [
            ("selection", selected, acquired),
            ("reference-profile", profile, acquired),
            ("acquisition-record", acquired, selected),
            ("acquisition-record", acquired, profile),
            ("editor-result", editor, child_assignment()),
            ("parent-qc", qc, editor),
            ("coordinator-run-result", final, selected),
            ("coordinator-run-result", final, acquired),
            ("coordinator-run-result", final, profile),
        ]
        for kind, artifact, upstream in pairs:
            with self.subTest(kind=kind, upstream=validate_contract.artifact_type(upstream)):
                self.assertEqual([], validate_contract.cross_contract_errors(kind, artifact, upstream))

    def test_legacy_hearing_claims_are_rejected_recursively(self) -> None:
        bad = editor_result()
        bad["audio"]["full_preview_watched_with_audio"] = True
        bad = _fingerprint("editor-result", bad, "result_fingerprint")
        errors = validate_contract.validate_instance("editor-result", bad)
        self.assertTrue(any("legacy auditory/self-attestation field" in row for row in errors))

    def test_preview_receipt_must_disable_audio_model_review(self) -> None:
        bad = editor_result()
        bad["preview_render_provenance"]["preview_receipt"]["audio_model_review"] = True
        bad = _fingerprint("editor-result", bad, "result_fingerprint")
        self.assert_invalid("editor-result", bad)

    def test_preview_asr_must_cover_the_actual_current_render(self) -> None:
        bad = editor_result()
        bad["program_speech_transcript"]["processing_gaps"] = [[4.0, 5.0]]
        bad = _fingerprint("editor-result", bad, "result_fingerprint")
        self.assert_invalid("editor-result", bad)

    def test_reference_selection_requires_visual_asr_signal_and_engagement_evidence(self) -> None:
        bad = reference_profile()
        bad["selection_decision"]["evidence_ids"].remove("reference-signal-analysis")
        self.assert_invalid("reference-profile", bad)

    def test_reference_selection_cannot_precede_transcript_and_signal_evidence(self) -> None:
        bad = reference_profile()
        bad["selected_at"] = "2026-08-18T09:55:30Z"
        self.assert_invalid("reference-profile", bad)

    def test_parent_qc_must_be_independent_and_bind_editor_fingerprint(self) -> None:
        editor = editor_result()
        bad = parent_qc(editor=editor)
        bad["preview_render_provenance"] = copy.deepcopy(editor["preview_render_provenance"])
        bad = _fingerprint("parent-qc", bad, "qc_fingerprint")
        self.assertTrue(validate_contract.cross_contract_errors("parent-qc", bad, editor))

    def test_no_music_ready_result_has_no_fake_cta_tail(self) -> None:
        good = editor_result()
        self.assertFalse(good["cta"]["tail_eligible"])
        self.assertFalse(good["cta"]["tail_configured"])
        self.assert_valid("editor-result", good)

    def test_coordinator_terminal_truth_table_rejects_ready_plus_pending_partial(self) -> None:
        bad = coordinator_result()
        bad["status"] = "partial"
        bad["pending_count"] = 1
        bad["ready_count"] = 0
        row = bad["arc_accounting"][0]
        row.update(
            {
                "generation_status": "pending",
                "assignment_input_fingerprint": None,
                "editor_result_fingerprint": None,
                "parent_qc_fingerprint": None,
                "child_project_id": None,
                "generation_job_id": None,
                "editor_status": "not_started",
                "parent_qc_status": "not_run",
                "live_edl_version": None,
                "preview_edl_version": None,
                "treatment_name": None,
                "reference_adaptation_summary": None,
            }
        )
        self.assert_invalid("coordinator-run-result", bad)

    def test_coordinator_fingerprints_are_required_for_ready_row(self) -> None:
        bad = coordinator_result()
        bad["arc_accounting"][0]["parent_qc_fingerprint"] = None
        self.assert_invalid("coordinator-run-result", bad)

    def test_assignment_fingerprint_tampering_fails(self) -> None:
        bad = child_assignment()
        bad["title"] = "Changed after approval"
        self.assert_invalid("child-assignment", bad)

    def test_reference_acquisition_duration_lineage_fails_closed(self) -> None:
        bad = acquisition_record()
        bad["reference"]["postupload_duration_s"] = 39.0
        self.assert_invalid("acquisition-record", bad)


if __name__ == "__main__":
    unittest.main()
