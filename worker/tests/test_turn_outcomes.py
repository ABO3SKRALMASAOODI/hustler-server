"""Billing follows delivered value, not whether the worker returned done."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop  # noqa: E402


def _ctx(**overrides):
    base = dict(
        versions_written=[], images_generated=[], videos_generated=[],
        urls_fetched=[], web_recordings=[], audio_extracted=[], stock_added=[],
        audio_fetched=[],
        last_preview=None, turn_tool_outcomes=[], write_attempts=0,
        edit_plan=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_rejected_recipe_with_no_output_is_free_and_blocked():
    ctx = _ctx(turn_tool_outcomes=[
        {"tool": "apply_edit_recipe", "kind": "refused"}],
        write_attempts=1)
    assert agent_loop._turn_completion(ctx) == ("blocked", False)


def test_internal_tool_failure_with_no_output_is_free():
    ctx = _ctx(turn_tool_outcomes=[
        {"tool": "fetch_music", "kind": "failed"}])
    assert agent_loop._turn_completion(ctx) == ("internal_error", False)


def test_failure_classifier_catches_mid_sentence_save_failures():
    from agent_tools import tool_result_kind
    assert tool_result_kind(
        "Downloaded the stock clip but could not save it (storage down).") \
        == "failed"
    assert tool_result_kind("Audio analysis unavailable for this video") \
        == "failed"
    assert tool_result_kind("REJECTED: asset could not be recovered") \
        == "refused"


def test_fetched_audio_is_delivered_value():
    ctx = _ctx(audio_fetched=["music/1/track.mp3"],
               turn_tool_outcomes=[{"tool": "fetch_music", "kind": "success"}])
    assert agent_loop._turn_completion(ctx) == ("fulfilled", True)


def test_successful_edit_remains_billable_even_after_a_repairable_refusal():
    ctx = _ctx(versions_written=[2], turn_tool_outcomes=[
        {"tool": "apply_edit_recipe", "kind": "refused"},
        {"tool": "apply_edit_recipe", "kind": "success"}],
        write_attempts=2)
    assert agent_loop._turn_completion(ctx) == ("fulfilled", True)


def test_read_only_analysis_answer_remains_billable():
    ctx = _ctx(turn_tool_outcomes=[
        {"tool": "get_transcript", "kind": "success"}])
    assert agent_loop._turn_completion(ctx) == ("fulfilled", True)


def test_identical_deterministic_failure_stops_before_third_model_call():
    ctx = _ctx()
    agent_loop._record_outer_tool_outcome(
        ctx, "apply_edit_recipe",
        "RECIPE ABORTED at operation 4 (add_zoom): target at 7.91s")
    assert agent_loop._repeated_tool_failure(ctx) is False
    agent_loop._record_outer_tool_outcome(
        ctx, "apply_edit_recipe",
        "RECIPE ABORTED at operation 4 (add_zoom): target at 7.92s")
    assert agent_loop._repeated_tool_failure(ctx) is True
    assert "add_zoom" in ctx.last_tool_result


def test_unused_fetched_music_is_disclosed():
    ctx = _ctx(audio_fetched=["music/1/track.mp3"])
    ctx.latest_edl = lambda: {"json": {"music": []}}
    note = agent_loop._unused_fetched_audio_note(ctx)
    assert "not placed" in note
    ctx.latest_edl = lambda: {"json": {
        "music": [{"storage_key": "music/1/track.mp3"}]}}
    assert agent_loop._unused_fetched_audio_note(ctx) == ""
