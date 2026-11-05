"""A pause already reviewed must not reopen after unrelated visual/SFX edits."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import agent_tools
import audio_qc
import quality_verifier as qv
import request_contract as rc
from schemas import default_edl
from test_request_contract import context


PAUSE = (4.0, 6.0)
INDEX = {"video": {"duration": 20, "width": 1920, "height": 1080},
         "tile_keys": ["original-source-a/frame.jpg"]}


def pause_message():
    # Existing display shape, used on both sides of the regression replay.
    s, e = PAUSE
    return (f"dead air: {s:.1f}-{e:.1f}s is silent for {e - s:.1f}s — "
            "tighten the cut, or let music/room tone cover it if the pause is deliberate")


def record(version, edl, prior=None, index=None, visual=None, project=7):
    return qv.build_verification_record(
        project, version, {}, edl, INDEX if index is None else index,
        preview={"edl_version": version, "duration_s": 20,
                 "audio_qc": {"silences": [list(PAUSE)]}},
        audio_findings=[pause_message()], visual_findings=visual,
        prior_records=prior)


def accepted_pause(edl):
    result = record(1, edl)
    return qv.justify_findings(
        result, [result["unresolved_findings"][0]["finding_id"]],
        "Original rhetorical pause, preserved at the customer's explicit request.")


def test_a_long_question_keeps_its_actual_decision_and_all_options(monkeypatch):
    ctx = SimpleNamespace()
    question = "Source and current timeline detail. " * 24 + "Keep the ending or replace it with source credits?"
    offers = []
    monkeypatch.setattr(agent_tools.music_gate, "repeat_offer_refusal", lambda *a: None)
    monkeypatch.setattr(agent_tools.music_gate, "open_offer", lambda c, q: offers.append(q))
    with pytest.raises(agent_tools.AskUser) as exc:
        agent_tools.ask_user(ctx, question)
    assert exc.value.question == question
    assert offers == [question]


def test_accepted_pause_survives_visual_edits_and_known_sound_outside_window():
    edl = default_edl(20)
    prior = accepted_pause(edl)
    changed = deepcopy(edl)
    changed["texts"] = [{"id": "t1", "text": "Source credit", "start": 12, "end": 16}]
    changed["sfx"] = [{"id": "sx1", "at": 1.0, "dur_s": None,
                       "storage_key": "sfx/7/lib-whoosh_soft_1-8303a0e4ab.flac", "gain_db": -16}]
    changed["keep"] = [[0, 10], [10, 20]]  # Unchanged clock, different segmentation.
    after = record(2, changed, {1: prior})
    assert not after["unresolved_findings"]
    assert after["status"] == "justified"
    assert after["justifications"][0]["inherited_from_version"] == 1


@pytest.mark.parametrize("change", ["within", "unknown_extent", "clock", "mix", "source"])
def test_accepted_pause_does_not_override_changed_or_unknown_audio(change):
    edl = default_edl(20)
    prior = accepted_pause(edl)
    changed, index = deepcopy(edl), deepcopy(INDEX)
    if change == "within":
        changed["sfx"] = [{"id": "sx1", "at": 5, "dur_s": 1, "storage_key": "new.wav"}]
    elif change == "unknown_extent":
        changed["sfx"] = [{"id": "sx1", "at": 0, "storage_key": "unmeasured.wav"}]
    elif change == "clock":
        changed["keep"] = [[0, 9], [11, 22]]
    elif change == "mix":
        changed["volume"] = [{"start": 0, "end": 20, "gain_db": -10}]
    else:
        index["tile_keys"] = ["different-source/frame.jpg"]
    after = record(2, changed, {1: prior}, index=index)
    assert any(r["department"] == "audio_review" for r in after["unresolved_findings"])


def test_audio_justification_cannot_clear_a_new_visual_defect_or_other_project():
    edl = default_edl(20)
    prior = accepted_pause(edl)
    after = record(2, edl, {1: prior}, visual=["The new graphic obscures the face."])
    assert [r["department"] for r in after["unresolved_findings"]] == ["visual_review"]
    other = record(2, edl, {1: prior}, project=8)
    assert any(r["department"] == "audio_review" for r in other["unresolved_findings"])


def test_preserved_clock_is_not_unmet_because_source_contains_a_pause(monkeypatch):
    ctx = context()
    ctx.semantic_contract["requirements"] = [{"id": "system:timing", "action": "preserve",
        "lanes": ["timeline_timing"], "description": "Preserve picture timing and duration"}]
    monkeypatch.setattr(rc, "_call", lambda *a, **k: {
        "completed": "The full duration is retained.",
        "remaining": "Cutting a source pause would change the protected duration.",
        "requirements": [{"id": "system:timing", "status": "unmet", "blocker_kind": "customer_input",
            "reason": "Cutting a source pause would change the protected duration.", "evidence": []}]})
    result = rc.assess(ctx)
    assert result["complete"]
    assert result["remaining"] == ""
    # Actual timing damage is still an error, regardless of model confidence.
    ctx.latest_edl()["json"]["keep"] = [[0, 12]]
    result = rc.assess(ctx, "Changed timeline")
    assert not result["complete"]


def test_semantic_contract_owns_transition_intent_not_a_sound_keyword():
    edl = default_edl(20)
    request = "Add subtle transition sound effects and gentle zooms where appropriate."
    contract = {"mode": "edit", "requirements": [{"id": "r1", "action": "change",
        "description": "Subtle sounds synchronized with B-roll transitions", "lanes": []}]}
    result = qv.build_verification_record(7, 1, {}, edl, INDEX,
        preview={"edl_version": 1, "duration_s": 20}, request_text=request,
        semantic_contract=contract)
    assert not any(r["code"] == "requested_transitions_missing" for r in result["findings"])
