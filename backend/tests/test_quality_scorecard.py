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
                "tokens_out": 100, "prompt_cache_ratio": .6,
                "estimated_model_cost_usd": .12,
                "model_calls": 4, "agent_dispatches": 3, "tool_calls": 8,
                "versions_written": 2, "previews_rendered": 1,
                "recipe_calls": 1, "recipe_commits": 1,
                "recipe_operations_committed": 7,
                "recipe_references_resolved": 3,
                "post_plan_tool_schema_chars": 22000,
                "music_candidates_measured": 3,
                "story_reviews": 1,
                "clean_finishing_checkpoints": 1,
                "quality_evidence": {"visual_critic_verdict": "pass",
                    "screening_frames": 32, "screening_pages": 2,
                    "story_review_verdict": "pass",
                    "visual_rubric": {"visual_coherence": {
                        "level": "strong", "evidence": "one language",
                        "confidence": .9}},
                    "visual_finding_categories": []}}}},
        {"created_at": now, "exported_after": False,
         "rapid_followup": True, "meta": {
            "outcome": "partial", "quality_status": "advisory",
            "feedback": "down",
            "editing_metrics": {
                "code_version": "aaaaaaaaaaaa",
                "editorial_family": "podcast_conversation",
                "editorial_contract_v": 1,
                "tokens_in": 3000,
                "tokens_out": 300, "prompt_cache_ratio": .2,
                "estimated_model_cost_usd": .28,
                "model_calls": 8, "agent_dispatches": 6, "tool_calls": 12,
                "versions_written": 6, "previews_rendered": 2,
                "recipe_calls": 3, "recipe_commits": 1,
                "recipe_aborts": 2,
                "recipe_operations_committed": 4,
                "recipe_references_resolved": 1,
                "post_plan_tool_schema_chars": 30000,
                "duplicate_writes_prevented": 2,
                "post_pass_variations_prevented": 3,
                "quality_evidence": {"visual_critic_verdict": "repair",
                    "screening_frames": 16, "screening_pages": 1,
                    "story_review_verdict": "repair",
                    "story_finding_categories": ["missing_context"],
                    "visual_rubric": {"visual_coherence": {
                        "level": "weak", "evidence": "mixed styles",
                        "confidence": .9}},
                    "visual_finding_categories": ["style_coherence"]}}}},
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
    assert card["rubric"]["visual_coherence"]["strong"] == 1
    assert card["rubric"]["visual_coherence"]["weak"] == 1
    assert card["rubric"]["typography"]["missing"] == 2
    assert card["finding_categories"] == {
        "style_coherence": 1, "story/missing_context": 1}
