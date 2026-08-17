"""Billing follows delivered value, not whether the worker returned done."""

import os
import sys
import hashlib
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop  # noqa: E402
from schemas import edl_signature  # noqa: E402


def _ctx(**overrides):
    base = dict(
        versions_written=[], images_generated=[], videos_generated=[],
        urls_fetched=[], web_recordings=[], audio_extracted=[], stock_added=[],
        audio_fetched=[], rendered_versions=set(),
        last_preview=None, turn_tool_outcomes=[], write_attempts=0,
        edit_plan=None, plan_revised_this_turn=False,
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


def test_empty_canvas_answer_without_created_media_or_timeline_is_free():
    ctx = _ctx(
        has_main_video=False,
        turn_tool_outcomes=[{"tool": "list_assets", "kind": "success"}])
    assert agent_loop._turn_completion(ctx) == ("blocked", False)


def test_cached_preview_is_not_current_turn_value():
    ctx = _ctx(
        last_preview={"url": "https://cached.example/preview.mp4",
                      "cached": True},
        rendered_versions={6}, write_attempts=1)
    assert agent_loop._turn_completion(ctx, "timeout") == \
        ("internal_error", False)


def test_preview_rendered_this_turn_is_delivered_value():
    ctx = _ctx(
        last_preview={"url": "https://new.example/preview.mp4",
                      "cached": False},
        rendered_versions={7}, write_attempts=1)
    assert agent_loop._turn_completion(ctx, "timeout") == ("partial", True)


def test_intermediate_asset_does_not_bill_a_timed_out_edit():
    ctx = _ctx(audio_extracted=["audio/extracted.wav"], write_attempts=1)
    assert agent_loop._turn_completion(ctx, "timeout") == ("partial", False)


def test_awaiting_user_without_a_deliverable_is_free():
    ctx = _ctx(write_attempts=1)
    assert agent_loop._turn_completion(ctx, "awaiting_user") == \
        ("blocked", False)


def test_awaiting_user_does_not_bill_an_intermediate_asset():
    ctx = _ctx(audio_extracted=["audio/extracted.wav"])
    assert agent_loop._turn_completion(ctx, "awaiting_user") == \
        ("partial", False)


def test_prior_durable_plan_does_not_make_a_read_only_answer_an_edit_failure():
    ctx = _ctx(
        edit_plan={"steps": ["make a reel"]},
        turn_tool_outcomes=[{"tool": "get_transcript", "kind": "success"}])
    assert agent_loop._turn_completion(ctx) == ("fulfilled", True)


def test_free_edit_marker_uses_turn_delta_not_absolute_project_version():
    unchanged = _ctx(versions_written=[])
    unchanged.turn_start_edl = {
        "version": 8, "json": {"keep": [[0, 20]], "captions": None}}
    unchanged.latest_edl = lambda: {
        "version": 8, "json": {"keep": [[0, 20]], "captions": None}}
    assert agent_loop._turn_edl_changed(unchanged) is False

    changed = _ctx(versions_written=[9])
    changed.turn_start_edl = {
        "version": 8, "json": {"keep": [[0, 20]], "captions": None}}
    changed.latest_edl = lambda: {
        "version": 9, "json": {"keep": [[0, 12]], "captions": None}}
    assert agent_loop._turn_edl_changed(changed) is True

    restored = _ctx(versions_written=[9, 10])
    restored.turn_start_edl = {
        "version": 8, "json": {"keep": [[0, 20]], "captions": None}}
    restored.latest_edl = lambda: {
        "version": 10, "json": {"keep": [[0, 20]], "captions": None}}
    assert agent_loop._turn_edl_changed(restored) is False


def test_death_resume_keeps_original_turn_delta_without_another_write():
    original = {"keep": [[0, 20]], "captions": None}
    landed = {"keep": [[0, 12]], "captions": None}
    ctx = _ctx(versions_written=[])
    # The successor snapshots the already-edited state locally, but the copied
    # job payload carries the original request's durable identity.
    ctx.turn_start_edl = {"version": 9, "json": landed}
    ctx.turn_baseline_digest = hashlib.sha256(
        edl_signature(original).encode("utf-8")).hexdigest()
    ctx.turn_baseline_from_death_resume = True
    ctx.latest_edl = lambda: {"version": 9, "json": landed}
    assert agent_loop._turn_edl_changed(ctx) is True


def test_manual_concurrent_write_is_not_attributed_to_read_only_agent():
    original = {"keep": [[0, 20]], "captions": None}
    manual = {"keep": [[0, 15]], "captions": None}
    ctx = _ctx(versions_written=[])
    ctx.turn_start_edl = {"version": 8, "json": original}
    ctx.turn_baseline_digest = hashlib.sha256(
        edl_signature(original).encode("utf-8")).hexdigest()
    ctx.turn_baseline_from_death_resume = False
    ctx.latest_edl = lambda: {"version": 9, "json": manual}
    assert agent_loop._turn_edl_changed(ctx) is False


def test_nothing_dumps_the_turn_on_repeated_tool_failure():
    """The stall used to finalize after two matching errors. That is how
    an Openverse 401 and a sequence_map REJECTED each killed a whole
    vlog. Tool errors stay in the result; the agent keeps going."""
    ctx = _ctx()
    agent_loop._record_outer_tool_outcome(
        ctx, "apply_edit_recipe",
        "RECIPE ABORTED at operation 4 (add_zoom): target at 7.91s")
    agent_loop._record_outer_tool_outcome(
        ctx, "apply_edit_recipe",
        "RECIPE ABORTED at operation 4 (add_zoom): target at 7.92s")
    assert agent_loop._repeated_tool_failure(ctx) is False
    ctx = _ctx()
    agent_loop._record_outer_tool_outcome(
        ctx, "search_sfx",
        "Sound search failed (HTTP 401 from api.openverse.org).")
    agent_loop._record_outer_tool_outcome(
        ctx, "search_sfx",
        "Sound search failed (HTTP 401 from api.openverse.org).")
    assert agent_loop._repeated_tool_failure(ctx) is False
    ctx = _ctx()
    agent_loop._record_outer_tool_outcome(
        ctx, "set_edit_plan",
        "REJECTED: sequence_map[4] range 858.0-881.8s falls outside "
        "its cited source evidence (859.680-873.725s).")
    agent_loop._record_outer_tool_outcome(
        ctx, "set_edit_plan",
        "REJECTED: sequence_map[4] range 860.0-882.0s falls outside "
        "its cited source evidence (859.680-873.725s).")
    assert agent_loop._repeated_tool_failure(ctx) is False


def test_unused_fetched_music_is_disclosed():
    ctx = _ctx(audio_fetched=["music/1/track.mp3"])
    ctx.latest_edl = lambda: {"json": {"music": []}}
    note = agent_loop._unused_fetched_audio_note(ctx)
    assert "not placed" in note
    ctx.latest_edl = lambda: {"json": {
        "music": [{"storage_key": "music/1/track.mp3"}]}}
    assert agent_loop._unused_fetched_audio_note(ctx) == ""
