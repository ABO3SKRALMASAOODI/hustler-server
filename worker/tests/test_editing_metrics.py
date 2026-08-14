"""Outcome telemetry makes quality/cost cohorts measurable in production."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_loop  # noqa: E402


def test_outcome_metrics_join_cost_evidence_plan_and_edit_shape():
    edl = {
        "keep": [[0, 10], [12, 20]], "inserts": [], "speed": [],
        "captions": {"mode": "from_transcript"},
        "music": [{"id": "mu1"}], "sfx": [{"id": "sx1"}],
        "overlays": [{"id": "ov1", "fit": "cover"}],
        "texts": [{"id": "tx1"}],
        "effects": {"zooms": [{"id": "zm1"}, {"id": "zm2"}]},
    }
    ctx = SimpleNamespace(
        turn_tool_outcomes=[{"kind": "success"}, {"kind": "no_change"}],
        write_attempts=2,
        model_usage={
            "agent": {"in": 1000, "cached": 600, "out": 120},
            "vision": {"in": 500, "cached": 0, "out": 40}},
        editing_metrics={"music_candidates_measured": 4,
                         "duplicate_writes_prevented": 1,
                         "model_calls_by_purpose": {
                             "agent": 3, "vision_look": 1}},
        versions_written=[2, 3], rendered_versions={3}, edit_plan=None,
        last_visual_critic={
            "verdict": "pass", "findings": [],
            "rubric": {"visual_coherence": {
                "level": "strong", "evidence": "one visual language",
                "confidence": .9}}},
        last_taste=[], last_audio_qc_findings=[],
        index={"video": {"duration": 20}, "words": [], "shots": []},
        has_main_video=True,
        latest_edl=lambda: {"json": edl})

    meta = agent_loop._outcome_meta(ctx, "fulfilled")
    metrics = meta["editing_metrics"]

    assert metrics["tokens_in"] == 1500
    assert metrics["tokens_cached_in"] == 600
    assert metrics["prompt_cache_ratio"] == .4
    assert metrics["versions_written"] == 2
    assert metrics["music_candidates_measured"] == 4
    assert metrics["model_calls"] == 4
    assert metrics["agent_dispatches"] == 3
    assert metrics["tool_calls"] == 2
    assert metrics["edit_shape"] == {
        "duration_s": 18.0, "cuts": 1, "captions": True,
        "music_layers": 1, "sfx": 1, "zooms": 2,
        "broll_overlays": 1, "designed_texts": 1,
        "vector_graphics": 0}
    assert metrics["blueprint_state"] == "none"
    assert len(metrics["code_version"]) == 12
    assert metrics["editorial_family"] == "mixed_other"
    assert metrics["editorial_contract_v"] == 1
    assert metrics["quality_evidence"]["screening_frames"] == 0
    assert metrics["quality_evidence"]["screening_pages"] == 0
    assert metrics["quality_evidence"]["visual_critic_verdict"] == "pass"
    assert metrics["quality_evidence"]["visual_rubric"][
        "visual_coherence"]["level"] == "strong"
