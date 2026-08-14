"""Outcome telemetry makes quality/cost cohorts measurable in production."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_loop  # noqa: E402
import agent_tools  # noqa: E402


def test_decision_trace_is_compact_allowlisted_and_bounded():
    ctx = SimpleNamespace(editing_metrics={})

    for i in range(34):
        agent_tools._decision_trace(
            ctx, " broll   cast ", moment_id=f"moment-{i}",
            candidate_ids=["a", 2, {"raw": "payload"}, float("nan")]
                          + list(range(20)),
            decision="use", confidence=float("inf"),
            evidence="  actual   frame  ",
            purpose="x" * 500, raw_url="https://private.example/file",
            arbitrary_payload={"must": "not persist"})

    rows = ctx.editing_metrics["editorial_decisions"]
    assert len(rows) == 32
    assert ctx.editing_metrics["editorial_decisions_recorded"] == 32
    assert ctx.editing_metrics["editorial_decisions_dropped"] == 2
    assert rows[0]["kind"] == "broll cast"
    assert rows[0]["sequence"] == 1
    assert rows[-1]["sequence"] == 32
    assert rows[0]["candidate_ids"] == ["a", 2.0] + [float(i) for i in range(8)]
    assert "confidence" not in rows[0]
    assert rows[0]["evidence"] == "actual frame"
    assert len(rows[0]["purpose"]) == 420
    assert "raw_url" not in rows[0]
    assert "arbitrary_payload" not in rows[0]


def test_outcome_metrics_join_cost_evidence_plan_and_edit_shape():
    edl = {
        "keep": [[0, 10], [12, 20]], "inserts": [], "speed": [],
        "captions": {"mode": "from_transcript",
                     "style": {"preset": "editorial", "layout": "stack",
                               "animation": "rise"},
                     "placement_track": [{"t0": 0, "t1": 18,
                                           "position": "bottom"}]},
        "music": [{"id": "mu1", "storage_key": "music/one",
                   "duck_mode": "smooth"}],
        "sfx": [{"id": "sx1", "storage_key": "sfx/one"}],
        "overlays": [{"id": "ov1", "fit": "cover",
                      "asset_key": "broll/one"}],
        "texts": [{"id": "tx1", "template": "lower_third",
                   "entrance": "slide_up", "motion": {"opacity": []}}],
        "frame": {"ratio": "9:16", "mode": "crop"},
        "effects": {"grade": "cinematic",
                    "transition": {"style": "flash", "scope": "scene"},
                    "stylize": [{"kind": "grain"}],
                    "zooms": [{"id": "zm1", "mode": "ease"},
                              {"id": "zm2"}]},
    }
    ctx = SimpleNamespace(
        turn_tool_outcomes=[{"kind": "success"}, {"kind": "no_change"}],
        write_attempts=2,
        model_usage={
            "agent": {"in": 1000, "cached": 600, "out": 120},
            "vision": {"in": 500, "cached": 0, "out": 40}},
        editing_metrics={"music_candidates_measured": 4,
                         "duplicate_writes_prevented": 1,
                         "editorial_decisions": [
                             {"kind": "broll_download", "decision": "use",
                              "asset_key": "broll/one"},
                             {"kind": "music_fetch", "decision": "use",
                              "asset_key": "music/one"},
                             {"kind": "sfx_fetch", "decision": "use",
                              "asset_key": "sfx/not-used"},
                             {"kind": "music_cast", "decision": "none"},
                             {"kind": "broll_cast", "decision": "use"},
                         ],
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
    assert metrics["treatment_profile"] == {
        "frame_ratio": "9:16", "frame_mode": "crop",
        "caption_placement": "adaptive",
        "caption_preset": ["editorial"],
        "caption_animation": ["rise"], "caption_layout": ["stack"],
        "grade": "cinematic", "transition_style": "flash",
        "transition_scope": "scene", "stylize_kinds": ["grain"],
        "zoom_modes": ["ease", "punch"],
        "text_templates": ["lower_third"],
        "text_entrances": ["slide_up"], "text_exits": ["none"],
        "text_motion": ["keyframed"], "overlay_modes": ["cover"],
        "overlay_entrances": ["none"], "overlay_exits": ["none"],
        "music_ducking": ["smooth"], "music_looping": False}
    assert metrics["blueprint_state"] == "none"
    assert len(metrics["code_version"]) == 12
    assert metrics["editorial_family"] == "mixed_other"
    assert metrics["editorial_contract_v"] == 2
    assert metrics["quality_evidence"]["screening_frames"] == 0
    assert metrics["quality_evidence"]["screening_pages"] == 0
    assert metrics["quality_evidence"]["visual_critic_verdict"] == "pass"
    assert metrics["quality_evidence"]["visual_rubric"][
        "visual_coherence"]["level"] == "strong"
    decisions = metrics["editorial_decisions"]
    assert [row["placement_status"] for row in decisions] == [
        "placed", "placed", "not_placed", "not_applicable", "not_trackable"]
    assert decisions[0]["placements"] == [
        {"role": "overlay", "element_id": "ov1"}]
    assert decisions[1]["placements"] == [
        {"role": "music", "element_id": "mu1"}]
    assert decisions[2]["placements"] == []


def test_outcome_does_not_call_asset_unused_when_latest_edl_is_unavailable():
    ctx = SimpleNamespace(
        turn_tool_outcomes=[], write_attempts=0, model_usage={},
        editing_metrics={"editorial_decisions": [
            {"kind": "music_fetch", "decision": "use",
             "asset_key": "music/candidate"}]},
        versions_written=[], rendered_versions=set(), edit_plan=None,
        index={}, has_main_video=False,
        latest_edl=lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    metrics = agent_loop._outcome_meta(ctx, "partial")["editing_metrics"]
    trace = metrics["editorial_decisions"][0]
    assert trace["placement_status"] == "edl_unavailable"
    assert trace["placements"] == []
