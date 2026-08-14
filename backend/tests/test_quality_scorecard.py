"""Production scorecard keeps quality, cost and churn independently visible."""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from routes.admin_video import _quality_scorecard  # noqa: E402


def test_scorecard_groups_release_fingerprints_and_preserves_dimensions():
    now = datetime.now(timezone.utc)
    rows = [
        {"created_at": now, "exported_after": True,
         "rapid_followup": False, "meta": {
            "outcome": "fulfilled", "quality_status": "pass",
            "feedback": "up",
            "editing_metrics": {
                "code_version": "aaaaaaaaaaaa",
                "editorial_family": "podcast_conversation",
                "editorial_contract_v": 1,
                "tokens_in": 1000,
                "tokens_cached_in": 600,
                "tokens_out": 100, "prompt_cache_ratio": .6,
                "estimated_model_cost_usd": .12,
                "model_calls": 4, "agent_dispatches": 3, "tool_calls": 8,
                "model_calls_by_purpose": {"agent": 3, "story": 1},
                "versions_written": 2, "previews_rendered": 1,
                "recipe_calls": 1, "recipe_commits": 1,
                "recipe_operations_committed": 7,
                "recipe_references_resolved": 3,
                "post_plan_tool_schema_chars": 22000,
                "music_candidates_measured": 3,
                "broll_sequence_casts": 1,
                "broll_moments_cast": 4,
                "broll_moments_abstained": 1,
                "broll_renditions_reviewed": 3,
                "broll_renditions_rejected": 1,
                "broll_renditions_uncertain": 1,
                "sfx_abstentions": 1,
                "music_abstentions": 1,
                "caption_treatment_casts": 1,
                "caption_visual_casts": 1,
                "caption_visual_cast_fallbacks": 0,
                "caption_proof_candidates_rendered": 20,
                "caption_proof_pages": 5,
                "uploaded_media_comparisons": 1,
                "uploaded_media_assets_requested": 6,
                "uploaded_media_assets_compared": 6,
                "uploaded_media_frames_compared": 24,
                "uploaded_media_comparison_pages": 3,
                "editorial_family_explicit": True,
                "format_cast_confidence": 1.0,
                "format_cast_abstained": 0,
                "department_decisions": 6,
                "department_promises": 4,
                "department_promises_fulfilled": 4,
                "department_execution_gaps": 0,
                "motion_contract_active": 1,
                "motion_contract_beats": 4,
                "motion_contract_mapped_beats": 4,
                "motion_contract_fulfilled_beats": 4,
                "motion_contract_gaps": 0,
                "readiness_previews_prevented": 2,
                "treatment_judge_reviews": 1,
                "treatment_judge_accepts": 1,
                "audio_mix_reviews_reused": 2,
                "visual_reviews_reused": 1,
                "visual_passes_reopened": 1,
                "visual_repairs_resolved": 1,
                "complete_previews_routed_to_proof": 4,
                "treatment_profile": {
                    "transition_style": "flash",
                    "zoom_modes": ["ease", "punch"]},
                "editorial_decisions": [
                    {"kind": "broll_cast", "decision": "use",
                     "placement_status": "placed"},
                    {"kind": "music_cast", "decision": "none",
                     "placement_status": "not_applicable"},
                    {"kind": "caption_style", "decision": "use",
                     "preset": "editorial",
                     "placement_strategy": "adaptive",
                     "placement_status": "not_trackable"},
                ],
                "story_reviews": 1,
                "clean_finishing_checkpoints": 1,
                "quality_evidence": {"visual_critic_verdict": "pass",
                    "screening_frames": 32, "screening_pages": 2,
                    "story_review_verdict": "pass",
                    "visual_rubric": {"visual_coherence": {
                        "level": "strong", "evidence": "one language",
                        "confidence": .9}},
                    "visual_finding_categories": []}},
            "tool_outcomes": {"success": 8}}},
        {"created_at": now, "exported_after": False,
         "rapid_followup": True, "meta": {
            "outcome": "partial", "quality_status": "advisory",
            "feedback": "down",
            "editing_metrics": {
                "code_version": "aaaaaaaaaaaa",
                "editorial_family": "podcast_conversation",
                "editorial_contract_v": 1,
                "tokens_in": 3000,
                "tokens_cached_in": 600,
                "tokens_out": 300, "prompt_cache_ratio": .2,
                "estimated_model_cost_usd": .28,
                "model_calls": 8, "agent_dispatches": 6, "tool_calls": 12,
                "model_calls_by_purpose": {"agent": 6, "story": 2},
                "versions_written": 6, "previews_rendered": 2,
                "recipe_calls": 3, "recipe_commits": 1,
                "recipe_aborts": 2,
                "recipe_operations_committed": 4,
                "recipe_references_resolved": 1,
                "post_plan_tool_schema_chars": 30000,
                "duplicate_writes_prevented": 2,
                "post_pass_variations_prevented": 3,
                "department_decisions": 6,
                "department_promises": 4,
                "department_promises_fulfilled": 2,
                "department_execution_gaps": 2,
                "motion_contract_active": 1,
                "motion_contract_beats": 4,
                "motion_contract_mapped_beats": 3,
                "motion_contract_fulfilled_beats": 1,
                "motion_contract_gaps": 2,
                "department_closure_rejections": 1,
                "treatment_judge_reviews": 1,
                "treatment_judge_reviews_reused": 1,
                "treatment_judge_revisions": 1,
                "editorial_decisions": [
                    {"kind": "broll_cast", "decision": "use",
                     "placement_status": "not_placed"},
                ],
                "quality_evidence": {"visual_critic_verdict": "repair",
                    "screening_frames": 16, "screening_pages": 1,
                    "story_review_verdict": "repair",
                    "story_finding_categories": ["missing_context"],
                    "visual_rubric": {"visual_coherence": {
                        "level": "weak", "evidence": "mixed styles",
                        "confidence": .9}},
                    "visual_finding_categories": ["style_coherence"]}},
            "tool_outcomes": {"success": 10, "refused": 2}}},
    ]
    card = _quality_scorecard(rows)[0]

    assert card["turns"] == 2
    assert card["editorial_family"] == "podcast_conversation"
    assert card["outcomes"] == {"fulfilled": 1, "partial": 1}
    assert card["quality"] == {"pass": 1, "advisory": 1}
    assert card["user_feedback"] == {
        "up": 1, "down": 1, "unrated": 0, "up_rate": .5}
    assert card["behavior"] == {
        "exported_before_next_request": 1,
        "rapid_followup": 1, "no_observed_signal": 0}
    assert card["averages"]["tokens_in"] == 2000
    assert card["averages"]["tokens_cached_in"] == 600
    assert card["averages"]["estimated_model_cost_usd"] == .2
    assert card["averages"]["model_calls"] == 6
    assert card["averages"]["agent_dispatches"] == 4.5
    assert card["averages"]["tool_calls"] == 10
    assert card["averages"]["versions_written"] == 4
    assert card["averages"]["recipe_calls"] == 2
    assert card["averages"]["recipe_commits"] == 1
    assert card["averages"]["recipe_aborts"] == 1
    assert card["averages"]["recipe_operations_committed"] == 5.5
    assert card["averages"]["recipe_references_resolved"] == 2
    assert card["averages"]["tool_schema_chars"] == 26000
    assert card["averages"]["screening_frames"] == 24
    assert card["averages"]["screening_pages"] == 1.5
    assert card["contract_versions"] == {"1": 2}
    assert card["story_review"] == {"pass": 1, "repair": 1}
    assert card["averages"]["story_reviews"] == .5
    assert card["averages"]["clean_finishing_checkpoints"] == .5
    assert card["averages"]["post_pass_variations_prevented"] == 1.5
    assert card["averages"]["broll_sequence_casts"] == .5
    assert card["averages"]["broll_moments_cast"] == 2
    assert card["averages"]["broll_moments_abstained"] == .5
    assert card["averages"]["broll_renditions_reviewed"] == 1.5
    assert card["averages"]["broll_renditions_rejected"] == .5
    assert card["averages"]["broll_renditions_uncertain"] == .5
    assert card["averages"]["sfx_abstentions"] == .5
    assert card["averages"]["music_abstentions"] == .5
    assert card["averages"]["caption_treatment_casts"] == .5
    assert card["averages"]["caption_visual_casts"] == .5
    assert card["averages"]["caption_visual_cast_fallbacks"] == 0
    assert card["averages"]["caption_proof_candidates_rendered"] == 10
    assert card["averages"]["caption_proof_pages"] == 2.5
    assert card["averages"]["uploaded_media_comparisons"] == .5
    assert card["averages"]["uploaded_media_assets_requested"] == 3
    assert card["averages"]["uploaded_media_assets_compared"] == 3
    assert card["averages"]["uploaded_media_frames_compared"] == 12
    assert card["averages"]["uploaded_media_comparison_pages"] == 1.5
    assert card["averages"]["editorial_family_explicit"] == .5
    assert card["averages"]["format_cast_confidence"] == .5
    assert card["averages"]["format_cast_abstained"] == 0
    assert card["averages"]["audio_mix_reviews_reused"] == 1
    assert card["averages"]["visual_reviews_reused"] == .5
    assert card["averages"]["visual_passes_reopened"] == .5
    assert card["averages"]["visual_repairs_resolved"] == .5
    assert card["averages"]["complete_previews_routed_to_proof"] == 2
    assert card["averages"]["department_decisions"] == 6
    assert card["averages"]["department_promises"] == 4
    assert card["averages"]["department_promises_fulfilled"] == 3
    assert card["averages"]["department_execution_gaps"] == 1
    assert card["averages"]["motion_contract_active"] == 1
    assert card["averages"]["motion_contract_beats"] == 4
    assert card["averages"]["motion_contract_mapped_beats"] == 3.5
    assert card["averages"]["motion_contract_fulfilled_beats"] == 2.5
    assert card["averages"]["motion_contract_gaps"] == 1
    assert card["averages"]["department_closure_rejections"] == .5
    assert card["averages"]["readiness_previews_prevented"] == 1
    assert card["averages"]["treatment_judge_reviews"] == 1
    assert card["averages"]["treatment_judge_reviews_reused"] == .5
    assert card["averages"]["treatment_judge_accepts"] == .5
    assert card["averages"]["treatment_judge_revisions"] == .5
    assert card["averages"]["treatment_judge_abstentions"] == 0
    assert card["tool_outcomes"] == {"success": 18, "refused": 2}
    assert card["model_call_purposes"] == {"agent": 9, "story": 3}
    assert card["editorial_decisions"] == {
        "broll_cast:use": 2, "music_cast:none": 1,
        "caption_style:use": 1}
    assert card["editorial_choices"] == {
        "caption_style:preset=editorial": 1,
        "caption_style:placement_strategy=adaptive": 1}
    assert card["treatment_choices"] == {
        "transition_style=flash": 1,
        "zoom_modes=ease": 1,
        "zoom_modes=punch": 1}
    assert card["decision_placements"] == {
        "placed": 1, "not_applicable": 1, "not_trackable": 1,
        "not_placed": 1}
    assert card["distributions"]["agent_dispatches"] == {
        "p50": 4.5, "p90": 5.7, "max": 6.0}
    assert card["distributions"]["tokens_in"] == {
        "p50": 2000.0, "p90": 2800.0, "max": 3000.0}
    assert card["efficiency"] == {
        "weighted_prompt_cache_ratio": .3,
        "input_tokens_per_agent_dispatch": 444.4,
        "agent_dispatches_per_written_version": 1.125,
        "recipe_commit_rate": .5,
        "operations_per_recipe_commit": 5.5,
        "department_promise_fulfillment_rate": .75,
        "motion_contract_fulfillment_rate": .714,
    }
    assert card["rubric"]["visual_coherence"]["strong"] == 1
    assert card["rubric"]["visual_coherence"]["weak"] == 1
    assert card["rubric"]["typography"]["missing"] == 2
    assert card["finding_categories"] == {
        "style_coherence": 1, "story/missing_context": 1}
