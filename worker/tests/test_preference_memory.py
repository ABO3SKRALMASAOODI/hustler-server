"""Outcome memory learns stable relationships without copying content."""

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_loop  # noqa: E402
import db  # noqa: E402
import preference_memory  # noqa: E402


def _row(feedback=None, exported=False, decisions=None, profile=None):
    return {"feedback": feedback, "exported_after": exported,
            "decisions": decisions or [], "profile": profile or {}}


def test_repeated_same_family_outcomes_create_bounded_stable_prior():
    decisions = [
        {"kind": "caption_style", "decision": "use",
         "preset": "editorial", "placement_strategy": "adaptive",
         "candidate_id": "private-id", "reason": "raw customer words"},
        {"kind": "music_cast", "decision": "none",
         "reason": "private confession detail"},
    ]
    block = preference_memory.prompt_block([
        _row("up", decisions=decisions),
        _row(exported=True, decisions=decisions),
    ], "talking_head_social")

    assert "caption preset=editorial" in block
    assert "caption placement_strategy=adaptive" in block
    assert "music_cast decision=none" in block
    assert "weak prior evidence" in block
    assert "latest user brief" in block
    assert "private-id" not in block
    assert "raw customer words" not in block
    assert "private confession detail" not in block


def test_repeated_final_treatment_profile_teaches_style_not_content():
    profile = {"transition_style": "flash", "zoom_modes": ["ease"],
               "text_fonts": ["Anton"], "unknown_private": "raw words"}
    block = preference_memory.prompt_block([
        _row("up", profile=profile),
        _row(exported=True, profile=profile),
    ], "talking_head_social")

    assert "treatment transition_style=flash" in block
    assert "treatment zoom_modes=ease" in block
    assert "treatment text_fonts=Anton" in block
    assert "raw words" not in block


def test_one_off_or_contradictory_signal_does_not_become_a_preference():
    choice = [{"kind": "caption_style", "preset": "impact"}]
    assert preference_memory.summarize([_row("up", decisions=choice)]) == []
    assert preference_memory.summarize([
        _row("up", decisions=choice),
        _row("down", decisions=choice),
    ]) == []


def test_latest_style_in_one_message_wins_over_intermediate_variation():
    rows = [_row("up", decisions=[
                {"kind": "caption_style", "preset": "impact"},
                {"kind": "caption_style", "preset": "clean"}]),
            _row("up", decisions=[
                {"kind": "caption_style", "preset": "clean"}])]
    evidence = preference_memory.summarize(rows)
    assert any(row["value"] == "clean" for row in evidence)
    assert not any(row["value"] == "impact" for row in evidence)


def test_db_query_and_state_integration_are_account_and_family_scoped():
    source = inspect.getsource(db.editorial_preference_rows)
    assert "p.user_id = %s" in source
    assert "editorial_family" in source
    assert "download_triggered" in source
    assert "treatment_profile" in source
    state_source = inspect.getsource(agent_loop.state_block)
    assert "dbx.editorial_preference_rows" in state_source
    assert "preference_memory.prompt_block" in state_source
