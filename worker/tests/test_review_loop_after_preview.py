"""Reliability class review-loop-after-preview (census, 6 Nov).

Production, 3-5 Nov: `assess_request` ran up to 13 times on one customer chain,
up to 409,450 prompt tokens a call, re-entered the editor up to three times per
evidence state and then replaced the editor's reply with its own prose. It took
the paid lane's p50 from 213 s to 793 s, changed its verdict on 68% of identical
timelines and delivered no repair on any of 13 customer chains.

p5464 is the case these tests replay: "Add really aesthetically pleasing nature
like captions, keep them engaging" had its first complete preview at 124 s and
ended at 835 s with 6 previews, 12 assessments and a reply telling the customer
the request was unmet.
"""
import json
from types import SimpleNamespace as NS

import agent_loop
import agent_tools
import claude_lane
import db as dbx
import music_offer
import preview_budget
import request_contract
from test_quality_gates_advisory import _loop_ctx


ASK = "Add really aesthetically pleasing nature like captions, keep them engaging"
DRAFT = ("I added serif motion captions with gold accent words on the meaning "
         "words, and they run through the whole 75-second edit.")


def _contract(ctx, mode="edit"):
    ctx.request_contract_required = True
    ctx.verification_request = ASK
    ctx.user_message = ASK
    ctx.semantic_contract = {
        "schema_version": 1, "request_digest": request_contract.digest(ctx),
        "mode": mode, "output_count": 1, "timing_policy": "preserve",
        "music_requested": False,
        "requirements": [
            {"id": "r1", "action": "change", "lanes": [], "quote": ASK,
             "description": "Aesthetically pleasing nature-like captions"}]}


def _delivered(monkeypatch, draft=DRAFT):
    """The state p5464 was in at 124 s: one write, its complete preview
    rendered, nothing measured open. Every model call is counted."""
    ctx, db, posted, checkpoint = _loop_ctx(monkeypatch)
    edl = ctx.latest_edl()["json"]
    edl["texts"] = []
    ctx.verification_records[73] = dict(
        status="passed", complete_preview_passed=True, unresolved_findings=[])
    _contract(ctx)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return NS(choices=[NS(message=NS(content=draft, tool_calls=[]),
                              finish_reason="stop")], usage=None)
    ctx.llm_client = NS(chat=NS(completions=NS(create=create)))
    return ctx, db, posted, checkpoint, calls


def _run(ctx, db):
    return agent_loop._run_loop(
        ctx, db, {"id": 3, "project_id": 9, "user_id": 8, "payload": {}},
        2, {"id": 1, "content": ctx.user_message})


def test_the_turn_ends_on_the_first_complete_preview_with_one_model_call(monkeypatch):
    ctx, db, posted, checkpoint, calls = _delivered(monkeypatch)
    result = _run(ctx, db)
    assert result["status"] == "replied"
    assert result["outcome"] == "fulfilled"
    assert checkpoint == {}
    # The reply was drafted. Nothing runs after it: no structured review, no
    # repair re-entry, no second draft.
    assert len(calls) == 1
    assert not any("tool_choice" in call for call in calls)


def test_the_customer_reads_the_editors_own_reply(monkeypatch):
    draft = "Los subtítulos ya están en todo el vídeo, con acentos dorados."
    ctx, db, posted, _checkpoint, _calls = _delivered(monkeypatch, draft)
    _run(ctx, db)
    text, meta = posted[-1]
    assert text == draft
    assert "request_completion" not in meta


def test_no_reviewer_verdict_is_left_in_the_record_of_the_turn(monkeypatch):
    ctx, db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    result = _run(ctx, db)
    assert "unmet_asks" not in result["honesty"]
    assert "request_requirements" not in result["honesty"]


def test_the_assessment_is_gone_not_capped():
    for name in ("assess", "reply", "completion", "awaiting_input",
                 "current_assessment", "ASSESS", "ASSESSMENT_SCHEMA"):
        assert not hasattr(request_contract, name)
    fields = agent_loop._PREVIEW_CHECKPOINT_FIELDS
    assert "request_assessment" not in fields
    assert "request_contract_repairs" not in fields


def test_a_continuation_from_the_old_build_finishes_normally(monkeypatch):
    """A request in flight during the deploy carries the removed fields."""
    ctx, db, posted, _checkpoint, calls = _delivered(monkeypatch)
    cont = {"why": "productive work remains", "work_slices": 1, "n": 1,
            "steps": 3, "start_version": 72,
            "request_assessment": {"complete": False, "requirements": [
                {"id": "r1", "status": "unmet"}]},
            "request_contract_repairs": [["digest", 72, "evidence"]],
            "request_preflights": 2}
    result = agent_loop._run_loop(
        ctx, db, {"id": 4, "project_id": 9, "user_id": 8, "payload": {}},
        2, {"id": 1, "content": ctx.user_message}, _cont=cont)
    assert result["status"] == "replied"
    assert posted[-1][0] == DRAFT
    assert len(calls) == 1


def test_the_brief_does_not_promise_a_review_that_no_longer_exists(monkeypatch):
    ctx, db, _posted, _checkpoint, calls = _delivered(monkeypatch)
    _run(ctx, db)
    system = [m["content"] for m in calls[0]["messages"]
              if m["role"] == "system" and "AUTHORITATIVE" in str(m["content"])]
    assert system
    assert "checked against the delivered preview" not in system[0]
    assert "A successful render alone is not completion" not in system[0]
    assert "nothing reviews it after you" in system[0]


# ---- measured facts still decide ------------------------------------------

def test_a_measured_defect_still_gets_its_repair(monkeypatch):
    ctx, db, posted, checkpoint, _calls = _delivered(monkeypatch)
    ctx.verification_records[73] = dict(
        status="repair_required", complete_preview_passed=True,
        unresolved_findings=[dict(code="corrupt_glyph", severity="error",
                                  message="Text contains null glyphs.")])
    result = _run(ctx, db)
    assert result["status"] == "continued"
    assert checkpoint["why"] == "verification repair remains"
    assert posted == []


def test_a_protected_lane_is_measured_before_the_render(monkeypatch):
    ctx, db, _posted, _checkpoint, calls = _delivered(monkeypatch)
    ctx.semantic_contract["requirements"].append(
        {"id": "no_music", "action": "avoid", "lanes": ["music"], "quote": ASK,
         "description": "No music"})
    ctx.latest_edl()["json"]["music"] = [{"asset_key": "added.mp3"}]
    message = request_contract.preflight(ctx)
    assert "no_music" in message
    assert not calls


def _nothing_changed(**overrides):
    values = dict(
        request_contract_required=True, versions_written=[], images_generated=[],
        videos_generated=[], urls_fetched=[], web_recordings=[],
        audio_extracted=[], stock_added=[], audio_fetched=[],
        rendered_versions=set(), last_preview=None, turn_tool_outcomes=[],
        write_attempts=0, edit_plan=None, plan_revised_this_turn=False,
        verification_request=ASK, user_message=ASK,
        semantic_contract={"mode": "edit"})
    values.update(overrides)
    return NS(**values)


def test_an_edit_request_that_changed_nothing_is_not_fulfilled_and_not_charged():
    assert agent_loop._turn_completion(_nothing_changed()) == ("blocked", False)


def test_a_question_to_the_customer_waits_and_is_not_charged():
    outcome, billable = agent_loop._turn_completion(
        _nothing_changed(), "awaiting_user")
    assert outcome == "awaiting_user" and billable is False


def test_an_answer_needs_no_timeline_change():
    outcome, billable = agent_loop._turn_completion(
        _nothing_changed(semantic_contract={"mode": "inspect"}))
    assert outcome == "fulfilled" and billable is True


def test_an_unresolved_tool_failure_stays_partial_on_a_delivered_edit(monkeypatch):
    ctx, db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    ctx.turn_tool_outcomes = [{"tool": "find_footage", "kind": "failed"}]
    assert agent_loop._turn_completion(ctx)[0] == "partial"


def test_a_music_choice_waits_on_the_customer_without_a_reviewer():
    assert music_offer.choice_outcome("fulfilled", "Pick a track.") == "awaiting_user"
    assert music_offer.choice_outcome("partial", "Pick a track.") == "partial"


# ---- a second model's opinion informs, it does not reopen ------------------

OPINION = {"verdict": "repair", "findings": [{
    "severity": "major", "category": "style_coherence", "time_s": 11.0,
    "target_id": None, "motion_motif": None, "confidence": .95,
    "evidence": "caption family changes mid-edit",
    "repair": "use one caption family"}]}


def _rendered(monkeypatch, critic=OPINION):
    """A real render_preview return path with the render job already done."""
    from test_director_blueprint import _tool_ctx

    ctx, fake = _tool_ctx()
    ctx.job = {"id": 3, "user_id": 8, "payload": {}}
    ctx.project_id = 9
    ctx.verification_request = ASK
    ctx.user_message = ASK
    ctx.turn_start_edl = fake.rows[0]
    version = fake.rows[-1]["version"]
    done = {"state": "done", "result": {
        "duration_s": 20.0, "asset_id": 5, "render_asset_id": 5,
        "storage_key": "renders/5.mp4", "edl_version": version}}
    original = fake.run

    def run(fn, *args):
        if fn is dbx.get_or_enqueue_preview_job:
            return 77, True
        if fn is dbx.get_job:
            return done
        if fn is dbx.upsert_verification_record:
            return True
        return original(fn, *args)
    fake.run = run
    monkeypatch.setattr(agent_tools, "_queue_check_frames", lambda *a: False)
    monkeypatch.setattr(agent_tools, "_preview_fallback_check", lambda *a: None)
    monkeypatch.setattr(agent_tools, "_preview_critic_report", lambda *a: critic)
    monkeypatch.setattr(agent_tools, "_review_program_story", lambda *a: None)
    monkeypatch.setattr(agent_tools, "_prefetch_story_review", lambda *a: None)
    monkeypatch.setattr(agent_tools, "_drain_story_prefetch", lambda *a: None)
    return ctx, agent_tools.render_preview(ctx, complete=True)


def test_a_critic_opinion_is_recorded_and_the_version_still_passes(monkeypatch):
    ctx, note = _rendered(monkeypatch)
    record = ctx.verification_records[1]
    assert record["status"] == "passed"
    assert record["unresolved_findings"] == []
    assert any("caption family changes" in row["message"]
               for row in record["advisories"])
    assert ctx.last_taste == []
    # The editor still reads it, as an opinion.
    assert "caption family changes" in note
    assert "second opinion" in note
    assert "VERSION VERIFICATION RECORD: repair required" not in note


def test_no_opinion_note_when_the_critic_found_nothing(monkeypatch):
    ctx, note = _rendered(monkeypatch, {"verdict": "pass", "findings": []})
    assert ctx.verification_records[1]["status"] == "passed"
    assert "second opinion" not in note


def test_a_critic_opinion_alone_keeps_the_export_open_with_no_note(monkeypatch):
    ctx, _note = _rendered(monkeypatch)
    quality = agent_loop._quality_handoff(ctx)
    assert quality["export_ready"] is True
    assert quality["quality_status"] == "passed"
    assert quality["quality_findings"] == []


def test_the_trial_repair_allowance_counts_measured_defects_only(monkeypatch):
    ctx, _note = _rendered(monkeypatch)
    ctx.request_contract_required = True
    assert preview_budget.repair_allowed(ctx) is False


# ---- the model-call shape left behind --------------------------------------

def test_the_brief_is_the_one_structured_call_and_stays_one_shot():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "request"}]}]
    tools = [{"name": "record_request_contract", "input_schema": {"type": "object"}}]
    assert claude_lane._one_shot(msgs, tools)
    other = [{"name": "assess_request", "input_schema": {"type": "object"}}]
    assert not claude_lane._one_shot(msgs, other)


def test_a_two_hour_source_costs_the_turn_no_extra_tokens(monkeypatch):
    """User 2744: 90 minutes of transcript and storyboard re-sent on every
    assessment, 7.24 M tokens in 18 calls. The source no longer enters any call
    after the preview, whatever its size."""
    ctx, db, _posted, _checkpoint, calls = _delivered(monkeypatch)
    ctx.index = dict(ctx.index or {}, sentences=[
        {"id": i, "t0": i * 3.0, "t1": i * 3.0 + 3, "text": "palabra " * 12,
         "speaker": 0} for i in range(2400)])
    _run(ctx, db)
    assert len(calls) == 1
    assert "palabra" not in json.dumps(calls[0]["messages"], default=str)
