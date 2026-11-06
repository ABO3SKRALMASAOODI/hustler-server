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


AUTO_RENDER = agent_loop._auto_render_if_needed
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


def test_a_reply_with_no_write_gets_one_measured_nudge_then_is_delivered(monkeypatch):
    """What the review did catch, measured for nothing: the brief says edit,
    the timeline did not change. A refusal or "this cannot be done" stands."""
    refusal = "I can't do that on this footage, so nothing has changed."
    ctx, db, posted, _checkpoint, calls = _delivered(monkeypatch, refusal)
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    result = _run(ctx, db)
    assert len(calls) == 2
    assert "timeline has not changed" in calls[1]["messages"][-1]["content"]
    assert posted[-1][0] == refusal
    assert result["outcome"] == "blocked" and result["billable"] is False


def test_a_draft_written_before_the_automatic_render_is_rewritten_once(monkeypatch):
    """Replay p5450: the editor skipped render_preview, wrote "I have not
    rendered it yet", the loop rendered, and that sentence reached the customer
    beside the attached preview."""
    ctx, db, posted, _checkpoint, calls = _delivered(monkeypatch)
    drafts = iter(["I have not rendered it yet.", "The new cut is in the player."])

    def create(**kwargs):
        calls.append(kwargs)
        return NS(choices=[NS(message=NS(content=next(drafts), tool_calls=[]),
                              finish_reason="stop")], usage=None)
    ctx.llm_client = NS(chat=NS(completions=NS(create=create)))
    ctx.last_preview = {"edl_version": 72}

    def auto_render(*_args):
        ctx.last_preview = {"edl_version": 73, "duration_s": 20, "asset_id": 5}
        return ctx.latest_edl(), ""
    monkeypatch.setattr(agent_loop, "_auto_render_if_needed", auto_render)
    result = _run(ctx, db)
    assert len(calls) == 2
    assert any("predates it" in str(m.get("content")) for m in calls[1]["messages"])
    assert posted[-1][0] == "The new cut is in the player."
    assert result["status"] == "replied"


def test_a_failed_automatic_render_keeps_the_draft_and_its_note(monkeypatch):
    ctx, db, posted, _checkpoint, calls = _delivered(monkeypatch)
    ctx.last_preview = {"edl_version": 72}

    def auto_render(*_args):
        ctx.last_preview = {"edl_version": 73, "duration_s": 20, "asset_id": 5}
        return ctx.latest_edl(), "\n\n(Heads up: the preview render failed.)"
    monkeypatch.setattr(agent_loop, "_auto_render_if_needed", auto_render)
    _run(ctx, db)
    assert len(calls) == 1
    assert posted[-1][0].startswith(DRAFT) and "Heads up" in posted[-1][0]


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
    """User 2744: 105 minutes of transcript and storyboard re-sent on every
    assessment, 7.24 M tokens in 18 calls. The source no longer enters any call
    after the preview, whatever its size."""
    ctx, db, _posted, _checkpoint, calls = _delivered(monkeypatch)
    ctx.index = dict(ctx.index or {}, sentences=[
        {"id": i, "t0": i * 3.0, "t1": i * 3.0 + 3, "text": "palabra " * 12,
         "speaker": 0} for i in range(2400)])
    _run(ctx, db)
    assert len(calls) == 1
    assert "palabra" not in json.dumps(calls[0]["messages"], default=str)


# ---- adversarial verification: nothing can be lost once the render exists --
#
# Production, 14 days: 15 of 108 finished turns ended on the automatic render,
# so the rewrite below is a mainstream path. It runs after the customer's video
# exists: whatever goes wrong in it must leave the delivery standing.

def _rendered_by_the_loop(monkeypatch, first=DRAFT):
    ctx, db, posted, checkpoint, calls = _delivered(monkeypatch, first)
    ctx.last_preview = {"edl_version": 72}

    def auto_render(*_args):
        ctx.last_preview = {"edl_version": 73, "duration_s": 20, "asset_id": 5}
        return ctx.latest_edl(), ""
    monkeypatch.setattr(agent_loop, "_auto_render_if_needed", auto_render)
    return ctx, db, posted, checkpoint, calls


def _reply(text, finish="stop"):
    return NS(choices=[NS(message=NS(content=text, tool_calls=[]),
                          finish_reason=finish)], usage=None)


def test_a_provider_failure_on_the_rewrite_keeps_the_delivered_turn(monkeypatch):
    ctx, db, posted, _checkpoint, calls = _rendered_by_the_loop(monkeypatch)

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            raise RuntimeError("529 overloaded")
        return _reply(DRAFT)
    ctx.llm_client = NS(chat=NS(completions=NS(create=create)))
    result = _run(ctx, db)
    assert result["status"] == "replied"
    assert posted[-1][0] == DRAFT


def test_a_spent_budget_does_not_replace_the_delivery_with_a_stop_message(monkeypatch):
    ctx, db, posted, _checkpoint, calls = _rendered_by_the_loop(monkeypatch)
    ctx.over_budget = lambda: bool(calls)
    result = _run(ctx, db)
    assert len(calls) == 1
    assert result["status"] == "replied"
    assert posted[-1][0] == DRAFT


def test_a_long_render_does_not_start_a_new_slice_for_one_sentence(monkeypatch):
    """Execution slices end at 420 s. A render that crossed that line must not
    turn the rewrite into a whole new planning slice."""
    ctx, db, posted, checkpoint, _calls = _rendered_by_the_loop(monkeypatch)

    def auto_render(*_args):
        monkeypatch.setattr(agent_loop.config, "AGENT_TURN_TIMEOUT_S", -1.0)
        ctx.last_preview = {"edl_version": 73, "duration_s": 20, "asset_id": 5}
        return ctx.latest_edl(), ""
    monkeypatch.setattr(agent_loop, "_auto_render_if_needed", auto_render)
    result = _run(ctx, db)
    assert result["status"] == "replied"
    assert checkpoint == {}
    assert posted


def test_the_rewrite_cannot_start_new_work(monkeypatch):
    ctx, db, _posted, _checkpoint, calls = _rendered_by_the_loop(monkeypatch)
    _run(ctx, db)
    assert len(calls) == 2
    assert calls[1]["tool_choice"] == "none"


def test_a_cut_off_rewrite_keeps_the_complete_draft(monkeypatch):
    ctx, db, posted, _checkpoint, calls = _rendered_by_the_loop(monkeypatch)

    def create(**kwargs):
        calls.append(kwargs)
        return _reply(DRAFT) if len(calls) == 1 else _reply("The new cu", "length")
    ctx.llm_client = NS(chat=NS(completions=NS(create=create)))
    _run(ctx, db)
    assert posted[-1][0] == DRAFT


# ---- adversarial verification: the facts that replaced the verdict ----------

def test_a_failure_the_same_tool_recovered_from_stays_a_delivered_request(monkeypatch):
    """Production 165718: "Add the music remove the sound" was delivered with
    2 failed receipts and reported fulfilled, because the assessor cleared them.
    38 of 40 failed receipts in 14 days recovered through the same tool. With
    the assessor gone each one would file a P0 "ended partial" ticket."""
    ctx, _db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    agent_loop._record_outer_tool_outcome(
        ctx, "erase_region", "TRANSIENT_FAILURE: The repaint failed.")
    assert agent_loop._turn_completion(ctx)[0] == "partial"
    agent_loop._record_outer_tool_outcome(ctx, "erase_region", "EDL v74 saved.")
    assert agent_loop._turn_completion(ctx)[0] == "fulfilled"


def _auto_render_returns(monkeypatch, ctx, db, text):
    monkeypatch.setattr(agent_loop, "_recover_catastrophic_timeline_collapse",
                        lambda *_a: None)
    monkeypatch.setattr(agent_loop, "_single_shot_design", lambda *_a: "")
    monkeypatch.setattr(agent_tools, "render_preview", lambda *_a, **_k: text)
    ctx.rendered_versions = set()
    return AUTO_RENDER(ctx, db, 2, {})


def test_the_automatic_render_corrects_a_failed_render_call(monkeypatch):
    ctx, db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    agent_loop._record_outer_tool_outcome(
        ctx, "render_preview", "TRANSIENT_FAILURE: The render queue timed out.")
    _latest, fail_note = _auto_render_returns(monkeypatch, ctx, db, "Preview ready.")
    ctx.rendered_versions.add(73)
    assert not fail_note
    assert agent_loop._turn_completion(ctx)[0] == "fulfilled"


def test_a_failed_automatic_render_stays_on_the_record(monkeypatch):
    ctx, db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    _latest, fail_note = _auto_render_returns(
        monkeypatch, ctx, db, "TRANSIENT_FAILURE: The render queue timed out.")
    assert "preview render failed" in fail_note
    assert ctx.turn_tool_outcomes[-1]["kind"] == "failed"
    assert agent_loop._turn_completion(ctx, fail_note=fail_note)[0] == "partial"


def test_a_clarifying_reply_waits_on_the_customer_and_is_not_charged():
    outcome, billable = agent_loop._turn_completion(
        _nothing_changed(semantic_contract={"mode": "clarify"}))
    assert outcome == "awaiting_user" and billable is False


def test_a_clarifying_turn_ends_waiting_through_the_loop(monkeypatch):
    ctx, db, posted, _checkpoint, calls = _delivered(
        monkeypatch, "Do you want the serif or the script caption?")
    ctx.semantic_contract["mode"] = "clarify"
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    result = _run(ctx, db)
    assert len(calls) == 1
    assert result["status"] == "awaiting_user"
    assert result["billable"] is False
    assert posted[-1][1]["outcome"] == "awaiting_user"


def test_a_music_offer_on_a_turn_that_wrote_nothing_waits_on_the_customer(monkeypatch):
    """Production 166495: the assessor marked this turn waiting. Without it an
    open track choice would end blocked, which files a P0 for a question."""
    ctx, db, _posted, _checkpoint, calls = _delivered(
        monkeypatch, "Nothing has changed yet.")
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    monkeypatch.setattr(music_offer, "turn_end_offer",
                        lambda *_a: "Pick one of these three tracks.")
    result = _run(ctx, db)
    assert result["outcome"] == "awaiting_user"
    assert result["status"] == "awaiting_user"
    assert result["billable"] is False


def test_a_write_that_ended_where_it_began_is_a_request_that_changed_nothing(monkeypatch):
    """The credit rule of 25 Oct: versions that end where they began are not a
    delivery. The assessor was the only thing catching this on a reply that
    claimed success; versions_written alone reads it as fulfilled and charged."""
    ctx, db, _posted, _checkpoint, calls = _delivered(monkeypatch, "All done.")
    monkeypatch.setattr(agent_loop, "_turn_edl_changed", lambda _ctx: False)
    result = _run(ctx, db)
    assert len(calls) == 2
    assert any("timeline has not changed" in str(m.get("content"))
               for m in calls[1]["messages"])
    assert result["outcome"] != "fulfilled"
    assert result["billable"] is False


def test_a_cached_preview_of_the_old_cut_is_not_a_change(monkeypatch):
    """The brief says edit, the editor re-opened the existing render and said
    done. The turn would end blocked, so it must get its nudge first."""
    ctx, db, _posted, _checkpoint, calls = _delivered(monkeypatch, "All done.")
    ctx.versions_written = []
    ctx.last_preview = {"edl_version": 73, "cached": True}
    result = _run(ctx, db)
    assert len(calls) == 2
    assert result["outcome"] == "blocked" and result["billable"] is False


def test_a_render_only_request_is_not_nudged(monkeypatch):
    ctx, db, _posted, _checkpoint, calls = _delivered(monkeypatch)
    ctx.versions_written = []
    result = _run(ctx, db)
    assert len(calls) == 1
    assert result["status"] == "replied"


def test_the_nudge_sends_a_real_question_to_ask_user_and_is_the_only_hold(monkeypatch):
    ctx, db, _posted, _checkpoint, calls = _delivered(monkeypatch, "Which file?")
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    _run(ctx, db)
    assert "ask_user" in calls[1]["messages"][-1]["content"]
    # ask_user's own first hold would make the same point a second time.
    assert ctx._question_held is True


def test_a_spent_budget_skips_the_nudge_and_keeps_the_editors_words(monkeypatch):
    honest = "Nothing has changed: that capability does not exist."
    ctx, db, posted, _checkpoint, calls = _delivered(monkeypatch, honest)
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    ctx.over_budget = lambda: bool(calls)
    _run(ctx, db)
    assert len(calls) == 1
    assert posted[-1][0] == honest


# ---- the wall on p5339: a brief that froze what it was asked to change ------
#
# "i need the text to slide in way fast and change the font, make them bolder"
# compiled, on 10 of 12 real compiles, to change requirements beside
# preserve=[texts] carried over from the request before. Every title write was
# refused and the customer was asked to confirm an override of what they had
# just typed. Removing the assessment exposed this; it did not cause it.

P5339 = "i need the text to slide in way fast and change the font, make them bolder"
EARLIER = "Make the title pass behind my body with tracked masking"


def _compiled(monkeypatch, requirements, request=P5339, changed=()):
    """The real ensure() on what the model returned, with no model."""
    ctx = NS(request_contract_required=True, verification_request=request,
             user_message=request, semantic_contract=None, index={},
             request_contract_history=[{"role": "user", "content": EARLIER}],
             latest_edl=lambda: {"version": 15, "json": {}},
             operator_instruction=None, request_preservation_baseline=None)
    monkeypatch.setattr(request_contract, "_call", lambda *_a: {
        "mode": "edit", "music_requested": False, "timing_policy": "edit",
        "output_count": 1, "changed_lanes": list(changed),
        "requirements": requirements})
    request_contract.ensure(ctx)
    return ctx


def _title_change():
    return {"description": "Change the title font", "action": "change",
            "source_ref": 0}


def _inherited_freeze():
    return {"description": "Keep the kinetic title", "action": "preserve",
            "source_ref": 1, "lanes": ["texts", "cuts"]}


def test_a_lane_a_change_writes_cannot_be_frozen_in_the_same_brief(monkeypatch):
    ctx = _compiled(monkeypatch, [_title_change(), _inherited_freeze()],
                    changed=["texts"])
    protected = request_contract.protected_lanes(ctx)
    assert "texts" not in protected
    assert "cuts" in protected


def test_the_render_preflight_agrees_with_the_write_guard(monkeypatch):
    """preflight reads the rows. Lifting the freeze in protected_lanes alone
    let a write through that the render then called a violation."""
    ctx = _compiled(monkeypatch, [_title_change(), _inherited_freeze()],
                    changed=["texts"])
    ctx.turn_start_edl = {"json": {"texts": [{"id": "t", "text": "old"}]}}
    ctx.latest_edl = lambda: {"version": 16, "json": {
        "texts": [{"id": "t", "text": "new"}]}}
    assert request_contract.preflight(ctx) is None


def test_a_channel_a_change_writes_is_not_forbidden_by_an_old_avoid(monkeypatch):
    ctx = _compiled(monkeypatch, [
        {"description": "Add a new track", "action": "change", "source_ref": 0},
        {"description": "No music", "action": "avoid", "source_ref": 1,
         "lanes": ["music"]}], changed=["music"])
    assert request_contract.forbidden_additions(ctx, {"music": [{}]}) == []


def test_the_brief_records_which_message_asked(monkeypatch):
    ctx = _compiled(monkeypatch, [_title_change(), _inherited_freeze()],
                    changed=["texts"])
    rows = ctx.semantic_contract["requirements"]
    assert [row["inherited"] for row in rows] == [False, True]


def test_every_line_of_a_multi_line_request_is_the_latest_request(monkeypatch):
    ctx = _compiled(monkeypatch, [
        {"description": "Keep the cut", "action": "preserve", "source_ref": 1,
         "lanes": ["cuts"]}], request="Change the font.\nKeep the cut as it is.")
    assert ctx.semantic_contract["requirements"][0]["inherited"] is False


def test_the_compile_schema_makes_the_brief_name_what_it_writes():
    schema = request_contract._compile_schema({0: "request"})
    assert "changed_lanes" in schema["required"]
    assert set(schema["properties"]["changed_lanes"]["items"]["enum"]) \
        == set(request_contract.LANES)


def test_a_brief_that_omits_what_it_writes_keeps_its_requirements():
    """Strict tools enforce the field. An omission must only lose the
    protection against a self-contradiction, not the whole read."""
    call = NS(function=NS(name="record_request_contract", arguments=json.dumps({
        "mode": "edit", "music_requested": False, "timing_policy": "edit",
        "output_count": 1, "requirements": [
            {"description": "Bolder", "action": "change", "source_ref": 0}]})))
    response = NS(choices=[NS(message=NS(tool_calls=[call]), finish_reason="stop")],
                  usage=None)
    ctx = NS(llm_client=NS(chat=NS(completions=NS(create=lambda **_k: response))),
             agent_model="gpt-test")
    schema = request_contract._compile_schema({0: "request"})
    value = request_contract._call(ctx, "record_request_contract", schema, "s", {})
    assert value["changed_lanes"] == []
    assert value["requirements"][0]["description"] == "Bolder"


def test_every_refusal_label_maps_back_to_its_lane():
    import scope_guard
    assert scope_guard.lanes_of(["designed text overlays"]) == {"texts"}
    assert scope_guard.lanes_of(["the picture timing and duration", "picture cuts"]) \
        == {"timeline_timing", "cuts"}
    assert scope_guard.lanes_of(["picture timing and duration during an "
                                 "audio-only revision"]) == set()


def _walled(monkeypatch, draft="I need you to confirm an override."):
    """The state p5339 reached: the model froze texts, an old message is the
    only protection, and the guard has refused the title writes."""
    ctx, db, posted, checkpoint, calls = _delivered(monkeypatch, draft)
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    ctx.semantic_contract["requirements"].append({
        "id": "r2", "action": "preserve", "lanes": ["texts", "cuts"],
        "quote": EARLIER, "description": "Keep the title", "inherited": True})
    request_contract.note_refused(ctx, ["texts"])
    return ctx, db, posted, checkpoint, calls


def test_a_refused_lane_only_an_earlier_message_protects_opens_at_the_nudge(monkeypatch):
    ctx, db, _posted, _checkpoint, calls = _walled(monkeypatch)
    _run(ctx, db)
    assert "texts" not in request_contract.protected_lanes(ctx)
    nudge = next(m["content"] for m in calls[1]["messages"]
                 if "timeline has not changed" in str(m.get("content")))
    assert "lifted for this request" in nudge and "texts" in nudge


def test_a_lane_nobody_tried_to_write_stays_protected(monkeypatch):
    ctx, db, _posted, _checkpoint, _calls = _walled(monkeypatch)
    _run(ctx, db)
    assert "cuts" in request_contract.protected_lanes(ctx)


def test_what_the_latest_request_protects_stays_protected(monkeypatch):
    ctx, db, _posted, _checkpoint, calls = _walled(monkeypatch)
    ctx.semantic_contract["requirements"].append({
        "id": "r3", "action": "preserve", "lanes": ["texts"], "quote": ASK,
        "description": "Leave the titles alone", "inherited": False})
    _run(ctx, db)
    assert "texts" in request_contract.protected_lanes(ctx)
    assert not any("lifted for this request" in str(m.get("content"))
                   for m in calls[1]["messages"])


def test_a_brief_from_the_old_build_has_no_inherited_field_and_keeps_its_freeze(monkeypatch):
    ctx, db, _posted, _checkpoint, _calls = _walled(monkeypatch)
    del ctx.semantic_contract["requirements"][-1]["inherited"]
    _run(ctx, db)
    assert "texts" in request_contract.protected_lanes(ctx)


def test_the_ask_user_hold_opens_the_lane_too(monkeypatch):
    """p5339 went through this door first: refused, refused, ask_user."""
    ctx, _db, _posted, _checkpoint, _calls = _walled(monkeypatch)
    ctx.agent_lanes = [{"name": "claude"}]
    held = agent_tools.ask_user(ctx, "May I override the lock on the titles?")
    assert held.startswith("CORRECTION_NEEDED")
    assert "lifted for this request" in held
    assert "texts" not in request_contract.protected_lanes(ctx)


def test_the_write_guard_records_the_lane_it_refused(monkeypatch):
    from test_director_blueprint import _tool_ctx

    ctx, _fake = _tool_ctx()
    ctx.request_contract_required = True
    ctx.verification_request = ctx.user_message = ASK
    ctx.turn_start_edl = ctx.latest_edl()
    _contract(ctx)
    ctx.semantic_contract["requirements"].append({
        "id": "r2", "action": "preserve", "lanes": ["texts"], "quote": EARLIER,
        "description": "Keep the title", "inherited": True})
    edl = json.loads(json.dumps(ctx.latest_edl()["json"]))
    edl["texts"] = (edl.get("texts") or []) + [dict(
        id="tx9", text="New", start=0.0, end=1.0)]
    result = ctx.write_edl(edl, "add a title")
    assert result.startswith("REJECTED")
    assert ctx.semantic_contract["refused_lanes"] == ["texts"]
