"""What was left of the review layer must not be able to stop a customer.

The assessment is gone. The one model call that reads the request stays, and so
does the exact check of what it protects. Both could still hold a turn: the
call failed the whole request before its first action (#136, #138), and the
check refused every render and every reply until the editor cleared a finding
it might not be able to clear. A check repairs, or the work goes out with an
honest note.
"""
import json
from types import SimpleNamespace as NS

import pytest

import agent_loop
import db as dbx
import request_contract
from test_review_loop_after_preview import ASK, _delivered, _run


def _ctx(request=ASK):
    return NS(request_contract_required=True, verification_request=request,
              user_message=request, semantic_contract=None, index={},
              request_contract_history=[], editing_metrics={},
              latest_edl=lambda: {"version": 3, "json": {}},
              operator_instruction=None, request_preservation_baseline=None)


def test_a_reply_the_brief_cannot_read_does_not_stop_the_request(monkeypatch):
    calls = []

    def unreadable(*_args):
        calls.append(1)
        raise ValueError("Contract field matches no allowed shape at /")
    monkeypatch.setattr(request_contract, "_call", unreadable)
    ctx = _ctx()
    brief = request_contract.ensure(ctx)
    assert brief["mode"] == "open"
    assert brief["request_digest"] == request_contract.digest(ctx)
    assert brief["requirements"][0]["quote"] == ASK
    assert len(calls) == 2
    assert "no allowed shape" in ctx.editing_metrics["brief_unavailable"]


def test_a_provider_fault_on_the_brief_is_not_retried_here(monkeypatch):
    """The editing call meets the same provider next, and the loop owns that
    recovery. A second try here only makes the customer wait."""
    calls = []

    def down(*_args):
        calls.append(1)
        raise RuntimeError("responses HTTP 400: tool_choice.name")
    monkeypatch.setattr(request_contract, "_call", down)
    assert request_contract.ensure(_ctx())["mode"] == "open"
    assert len(calls) == 1


def test_a_lost_lease_still_ends_the_execution(monkeypatch):
    def lost(*_args):
        raise dbx.JobLeaseLost("replaced")
    monkeypatch.setattr(request_contract, "_call", lost)
    with pytest.raises(dbx.JobLeaseLost):
        request_contract.ensure(_ctx())


def test_a_request_with_no_words_does_not_stop_the_turn(monkeypatch):
    monkeypatch.setattr(request_contract, "_call", lambda *_a: pytest.fail("no call"))
    assert request_contract.ensure(_ctx(request=""))["mode"] == "open"


def test_an_open_brief_is_read_once_per_request(monkeypatch):
    calls = []

    def unreadable(*_args):
        calls.append(1)
        raise ValueError("nope")
    monkeypatch.setattr(request_contract, "_call", unreadable)
    ctx = _ctx()
    request_contract.ensure(ctx)
    request_contract.ensure(ctx)
    assert len(calls) == 2


def test_an_open_brief_freezes_nothing(monkeypatch):
    def unreadable(*_args):
        raise ValueError("nope")
    monkeypatch.setattr(request_contract, "_call", unreadable)
    ctx = _ctx()
    ctx.turn_start_edl = {"version": 3, "json": {}}
    request_contract.ensure(ctx)
    assert request_contract.protected_lanes(ctx) == set()
    assert request_contract.forbidden_additions(ctx, {"music": [{}]}) == []
    assert request_contract.preflight(ctx) is None


def test_the_turn_delivers_through_a_model_reply_with_no_brief_in_it(monkeypatch):
    """The real path. The model answers the forced call with prose, as a
    refusal does, twice; the same turn then edits and delivers."""
    ctx, db, posted, _checkpoint, calls = _delivered(monkeypatch)
    ctx.semantic_contract = None
    ctx.request_contract_history = []
    ctx.index = {}
    ctx.agent_model = "gpt-test"
    result = _run(ctx, db)
    assert result["status"] == "replied"
    assert result["outcome"] == "fulfilled"
    forced = [call for call in calls if "tool_choice" in call]
    assert len(forced) == 2
    assert len(calls) == 3
    text, meta = posted[-1]
    assert "request brief" not in text.lower()
    assert meta["editing_metrics"]["brief_unavailable"]


def test_a_question_under_an_open_brief_is_not_pushed_into_an_edit(monkeypatch):
    """The nudge is for a request we know is an edit. Here nobody read it, and
    a question must stay a question."""
    ctx, db, posted, _checkpoint, calls = _delivered(
        monkeypatch, "It runs 75 seconds and has one speaker.")
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    ctx.semantic_contract["mode"] = "open"
    result = _run(ctx, db)
    assert result["outcome"] == "fulfilled"
    assert len(calls) == 1
    assert posted[-1][0].startswith("It runs 75 seconds")


# ---- the exact check: says it once ------------------------------------------


def _uncleared(monkeypatch):
    """The brief forbids a channel the timeline already has, and the editor
    has no way to clear it. Each model call is counted; the old code asked
    forever, so the fake gives up rather than hang the suite."""
    ctx, db, posted, checkpoint, calls = _delivered(monkeypatch)
    ctx.semantic_contract["requirements"].append({
        "id": "r2", "action": "avoid", "lanes": ["captions"], "quote": ASK,
        "description": "No captions", "inherited": False})
    ctx.latest_edl()["json"]["captions"] = {"enabled": False}
    ctx.turn_start_edl = {"version": 1, "json": {}}
    create = ctx.llm_client.chat.completions.create

    def bounded(**kwargs):
        assert len(calls) < 6, "the check never stopped asking"
        return create(**kwargs)
    ctx.llm_client = NS(chat=NS(completions=NS(create=bounded)))
    return ctx, db, posted, checkpoint, calls


def test_a_finding_the_editor_cannot_clear_does_not_hold_the_reply(monkeypatch):
    ctx, db, posted, _checkpoint, calls = _uncleared(monkeypatch)
    result = _run(ctx, db)
    assert result["status"] == "replied"
    assert len(calls) == 2
    assert posted[-1][0]


def test_the_render_gate_says_it_once_then_the_render_runs(monkeypatch):
    ctx, _db, _posted, _checkpoint, _calls = _uncleared(monkeypatch)
    first = agent_loop._unmet_ask_hold(ctx, "render_preview", {}, ASK)
    assert first.startswith("Restore the customer's")
    assert agent_loop._unmet_ask_hold(ctx, "render_preview", {}, ASK) is None


def test_a_new_finding_is_still_said(monkeypatch):
    ctx, _db, _posted, _checkpoint, _calls = _uncleared(monkeypatch)
    assert request_contract.preflight(ctx)
    assert request_contract.preflight(ctx) is None
    ctx.semantic_contract["requirements"].append({
        "id": "r3", "action": "avoid", "lanes": ["texts"], "quote": ASK,
        "description": "No titles", "inherited": False})
    ctx.latest_edl()["json"]["texts"] = [{"id": "tx1"}]
    finding = request_contract.preflight(ctx)
    assert finding and "r3" in finding


def test_what_was_said_survives_a_continuation():
    assert "semantic_contract" in agent_loop._PREVIEW_CHECKPOINT_FIELDS


# ---- a timing freeze is a freeze -----------------------------------------------


def _compiled(monkeypatch, requirements, timing_policy):
    ctx = _ctx()
    monkeypatch.setattr(request_contract, "_call", lambda *_a: {
        "mode": "edit", "music_requested": False, "timing_policy": timing_policy,
        "output_count": 1, "requirements": requirements})
    request_contract.ensure(ctx)
    return ctx


def _change(*lanes):
    return {"description": "Trim the intro", "action": "change", "source_ref": 0,
            "lanes": list(lanes)}


def test_a_change_that_writes_the_cuts_ends_the_timing_policy(monkeypatch):
    ctx = _compiled(monkeypatch, [_change("cuts")], "preserve")
    assert "timeline_timing" not in request_contract.protected_lanes(ctx)
    assert not any(row["id"] == "system:timing"
                   for row in ctx.semantic_contract["requirements"])


def test_a_change_that_writes_the_cuts_ends_a_timing_row_too(monkeypatch):
    ctx = _compiled(monkeypatch, [_change("cuts"), {
        "description": "Keep the timing", "action": "preserve", "source_ref": 0,
        "lanes": ["timeline_timing", "grade"]}], "edit")
    assert request_contract.protected_lanes(ctx) == {"grade"}


def test_keeping_the_cuts_while_the_speed_changes_is_not_a_contradiction(monkeypatch):
    ctx = _compiled(monkeypatch, [_change("timeline_timing"), {
        "description": "Keep the cuts", "action": "preserve", "source_ref": 0,
        "lanes": ["cuts"]}], "preserve")
    assert request_contract.protected_lanes(ctx) == {"cuts"}


def test_a_styling_change_keeps_its_timing_freeze(monkeypatch):
    ctx = _compiled(monkeypatch, [_change("captions")], "preserve")
    assert "timeline_timing" in request_contract.protected_lanes(ctx)


def test_the_open_brief_is_plain_json():
    ctx = _ctx()
    brief = request_contract._open_brief(ctx, ASK, "k", "why")
    assert json.loads(json.dumps(brief)) == brief
