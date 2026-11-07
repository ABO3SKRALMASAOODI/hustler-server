"""What the removal of the review left resting on the order things happened in.

The first pass replaced a model's verdict with measured facts. Those facts were
true of a turn that ran start to finish in one process, in the order the replays
happened to take. Real turns restart, and a real model reads the same words
differently one time in twelve, so each of these held on the replays and failed
when it was attacked.
"""
import hashlib
from types import SimpleNamespace as NS

import pytest

import agent_loop
import agent_prompt
import agent_tools
import db as dbx
import followup_recipes
import request_contract
from schemas import edl_signature
from test_review_loop_after_preview import (AUTO_RENDER, ASK, _auto_render_returns,
                                            _delivered, _nothing_changed, _run, _walled)


def _msgs(calls):
    return [str(m.get("content")) for call in calls for m in call["messages"]]


def _scripted(ctx, calls, replies):
    """Each model call takes the next reply; a callable runs first, as a tool
    refused between two model calls would."""
    queue = iter(replies)

    def create(**kwargs):
        calls.append(kwargs)
        step = next(queue, "Nothing more from me.")
        if callable(step):
            step = step()
        return NS(choices=[NS(message=NS(content=step, tool_calls=[]),
                              finish_reason="stop")], usage=None)
    ctx.llm_client = NS(chat=NS(completions=NS(create=create)))


# ---- a restart must not lose the edit that landed before it ------------------

def _restarted(monkeypatch, draft="Everything you asked for is in."):
    """The successor of a worker that died after its write: the timeline moved,
    and this process has no versions_written and no render of its own."""
    ctx, db, posted, checkpoint, calls = _delivered(monkeypatch, draft)
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    ctx.turn_baseline_from_death_resume = True
    ctx.turn_baseline_digest = "the digest of the request's first timeline"
    return ctx, db, posted, checkpoint, calls


def test_a_landed_edit_after_a_restart_is_not_told_nothing_changed(monkeypatch):
    """The brief for a resumed turn says "do NOT redo it". A nudge saying the
    timeline did not change is false and asks for the work twice."""
    ctx, db, _posted, _checkpoint, calls = _restarted(monkeypatch)
    result = _run(ctx, db)
    assert len(calls) == 1
    assert not any("timeline has not changed" in m for m in _msgs(calls))
    assert result["outcome"] == "fulfilled"


def test_a_landed_edit_after_a_restart_is_charged_like_any_delivered_edit(monkeypatch):
    ctx, _db, _posted, _checkpoint, _calls = _restarted(monkeypatch)
    assert agent_loop._deliverable_changed(ctx)
    assert agent_loop._turn_completion(ctx, "replied") == ("fulfilled", True)


def test_a_landed_edit_after_a_restart_gets_its_preview(monkeypatch):
    """Nothing else renders it: the automatic render looked at
    versions_written, and the customer's reply would arrive with no picture."""
    ctx, db, _posted, _checkpoint, _calls = _restarted(monkeypatch)
    rendered = []
    monkeypatch.setattr(agent_tools, "render_preview",
                        lambda _ctx, **_kw: rendered.append(1) or "Preview ready.")
    AUTO_RENDER(ctx, db, 2, {})
    assert rendered == [1]


def test_a_restart_that_landed_nothing_still_changed_nothing(monkeypatch):
    """The durable digest says the timeline is where the request found it."""
    ctx, db, _posted, _checkpoint, calls = _restarted(monkeypatch)
    ctx.turn_baseline_digest = hashlib.sha256(
        edl_signature(ctx.latest_edl()["json"]).encode("utf-8")).hexdigest()
    assert ctx.turn_baseline_from_death_resume
    _run(ctx, db)
    assert any("timeline has not changed" in m for m in _msgs(calls))
    assert agent_loop._turn_completion(ctx, "replied")[1] is False


# ---- the wall opens in any order -----------------------------------------------

def test_a_refusal_after_the_nudge_was_spent_still_opens_the_lane(monkeypatch):
    """Prose first spends the nudge, then the write is refused. p5339's wall
    with the two steps the other way round."""
    ctx, db, _posted, _checkpoint, calls = _walled(monkeypatch)
    ctx.semantic_contract.pop("refused_lanes", None)

    def refused():
        request_contract.note_refused(ctx, ["texts"])
        return "The title is locked, so I need you to confirm an override."
    _scripted(ctx, calls, ["Here is my plan for the title.", refused, "Done."])
    _run(ctx, db)
    assert "texts" not in request_contract.protected_lanes(ctx)
    assert len(calls) == 3
    assert "lifted for this request" in _msgs(calls[2:])[-1]


def test_nothing_new_to_open_keeps_the_nudge_to_once(monkeypatch):
    ctx, db, _posted, _checkpoint, calls = _walled(monkeypatch)
    _scripted(ctx, calls, ["It cannot be done."] * 6)
    _run(ctx, db)
    assert len(calls) == 2


def test_a_second_question_after_a_refusal_does_not_reach_the_customer(monkeypatch):
    """The refusal itself says to ask, and the first hold is already spent."""
    ctx, _db, _posted, _checkpoint, _calls = _walled(monkeypatch)
    ctx.semantic_contract.pop("refused_lanes", None)
    ctx.agent_lanes = [{"name": "claude"}]
    assert agent_tools.ask_user(ctx, "Which font?").startswith("CORRECTION_NEEDED")
    request_contract.note_refused(ctx, ["texts"])
    held = agent_tools.ask_user(ctx, "May I override the lock on the titles?")
    assert held.startswith("CORRECTION_NEEDED") and "lifted for this request" in held
    assert "texts" not in request_contract.protected_lanes(ctx)


def test_a_real_second_question_still_reaches_the_customer(monkeypatch):
    ctx, _db, _posted, _checkpoint, _calls = _walled(monkeypatch)
    ctx.semantic_contract.pop("refused_lanes", None)
    ctx.agent_lanes = [{"name": "claude"}]
    agent_tools.ask_user(ctx, "Which font?")
    with pytest.raises(agent_tools.AskUser):
        agent_tools.ask_user(ctx, "Please upload the logo file.")


def test_a_question_after_work_landed_opens_nothing(monkeypatch):
    ctx, _db, _posted, _checkpoint, _calls = _walled(monkeypatch)
    ctx.versions_written = [73]
    ctx.agent_lanes = [{"name": "claude"}]
    with pytest.raises(agent_tools.AskUser):
        agent_tools.ask_user(ctx, "Which of these two endings?")
    assert "texts" in request_contract.protected_lanes(ctx)


# ---- nobody read it, so the doubt is the customer's ----------------------------

def test_an_open_brief_that_changed_nothing_is_not_charged():
    ctx = _nothing_changed(semantic_contract={"mode": "open", "requirements": []})
    outcome, billable = agent_loop._turn_completion(ctx, "replied")
    assert (outcome, billable) == ("fulfilled", False)


def test_an_open_brief_that_delivered_an_edit_is_charged(monkeypatch):
    ctx, _db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    ctx.semantic_contract["mode"] = "open"
    ctx.turn_baseline_digest = None
    ctx.turn_start_edl = {"version": 70, "json": {"keep": []}}
    assert agent_loop._turn_completion(ctx, "replied")[1] is True


def test_an_open_brief_stays_on_the_jobs_record_of_a_fulfilled_turn(monkeypatch):
    """Nothing else files it: the turn succeeded."""
    ctx, db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    ctx.semantic_contract["mode"] = "open"
    ctx.semantic_contract["unavailable"] = "Contract field matches no allowed shape"
    result = _run(ctx, db)
    assert result["outcome"] == "fulfilled"
    assert result["honesty"]["brief_unavailable"].startswith("Contract field")


def test_a_read_brief_leaves_nothing_on_the_record(monkeypatch):
    ctx, db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    assert "brief_unavailable" not in _run(ctx, db)["honesty"]


def test_the_reason_survives_a_continuation_on_the_brief(monkeypatch):
    ctx = NS(request_contract_required=True, verification_request=ASK, user_message=ASK,
             semantic_contract=None, editing_metrics={})
    request_contract._open_brief(ctx, ASK, "key", "provider down")
    honesty = {}
    agent_loop._note_unread_brief(ctx, honesty)
    assert honesty == {"brief_unavailable": "provider down"}


# ---- told once is not cleared ----------------------------------------------------

def _broken(monkeypatch):
    ctx, db, posted, checkpoint, calls = _delivered(monkeypatch)
    ctx.semantic_contract["requirements"].append({
        "id": "r2", "action": "preserve", "lanes": ["texts"], "quote": ASK,
        "description": "Keep the titles", "inherited": False})
    ctx.turn_start_edl = {"version": 70,
                          "json": {"texts": [{"id": "old", "text": "KEEP"}]}}
    return ctx, db, posted, checkpoint, calls


def test_a_protection_still_broken_at_delivery_is_not_fulfilled(monkeypatch):
    ctx, db, _posted, _checkpoint, calls = _broken(monkeypatch)
    result = _run(ctx, db)
    assert any("Restore the customer's concrete" in m for m in _msgs(calls))
    assert result["outcome"] == "partial"


def test_a_protection_the_editor_restored_is_fulfilled(monkeypatch):
    ctx, db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    ctx.semantic_contract["requirements"].append({
        "id": "r2", "action": "preserve", "lanes": ["texts"], "quote": ASK,
        "description": "Keep the titles", "inherited": False})
    ctx.turn_start_edl = {"version": 70, "json": ctx.latest_edl()["json"]}
    assert _run(ctx, db)["outcome"] == "fulfilled"


def test_a_fault_measuring_protections_cannot_cost_a_delivered_turn(monkeypatch):
    ctx, _db, _posted, _checkpoint, _calls = _broken(monkeypatch)

    def down(*_args, **_kwargs):
        raise RuntimeError("database went away")
    monkeypatch.setattr(request_contract, "_violations", down)
    assert request_contract.broken_protections(ctx) == []


def test_only_an_edit_is_measured_at_delivery(monkeypatch):
    ctx, _db, _posted, _checkpoint, _calls = _broken(monkeypatch)
    ctx.semantic_contract["mode"] = "inspect"
    assert request_contract.broken_protections(ctx) == []


# ---- a change must name what it writes -------------------------------------------

P_TITLE = "make the opening title a little bigger"
OLDER = "make the captions bigger"


def _reads(monkeypatch, *replies):
    """ensure() on what the model answers, in order, with no model. A raised
    exception in the list is what that call does."""
    ctx = NS(request_contract_required=True, verification_request=P_TITLE,
             user_message=P_TITLE, semantic_contract=None, index={},
             request_contract_history=[{"role": "user", "content": OLDER}],
             latest_edl=lambda: {"version": 15, "json": {}}, editing_metrics={},
             operator_instruction=None, request_preservation_baseline=None)
    seen = []
    queue = iter(replies)

    def call(_ctx, _name, _schema, _system, evidence):
        seen.append(evidence)
        step = next(queue)
        if isinstance(step, Exception):
            raise step
        return step
    monkeypatch.setattr(request_contract, "_call", call)
    request_contract.ensure(ctx)
    return ctx, seen


def _reading(changed):
    return {"mode": "edit", "music_requested": False, "timing_policy": "preserve",
            "output_count": 1, "changed_lanes": list(changed),
            "requirements": [
                {"description": "Bigger title", "action": "change", "source_ref": 0},
                {"description": "Keep the rest", "action": "preserve",
                 "source_ref": 1, "lanes": ["texts", "captions"]}]}


def test_a_change_that_names_no_lane_is_asked_once_and_the_freeze_goes(monkeypatch):
    """1 of 12 real compiles of this request froze the title it was changing."""
    ctx, seen = _reads(monkeypatch, _reading([]), _reading(["texts"]))
    assert len(seen) == 2
    assert "changed_lanes" in seen[1]["validation_error"]
    protected = request_contract.protected_lanes(ctx)
    assert "texts" not in protected and "captions" in protected


def test_a_brief_that_names_its_lanes_costs_one_call(monkeypatch):
    _ctx, seen = _reads(monkeypatch, _reading(["texts"]))
    assert len(seen) == 1


def test_a_second_unnamed_reading_stands(monkeypatch):
    """A change can write something no lane holds, such as a b-roll source."""
    ctx, seen = _reads(monkeypatch, _reading([]), _reading([]))
    assert len(seen) == 2
    assert ctx.semantic_contract["mode"] == "edit"


@pytest.mark.parametrize("failure", [
    ValueError("Contract field matches no allowed shape at /"),
    RuntimeError("provider down")])
def test_a_failed_correction_keeps_the_first_reading(monkeypatch, failure):
    """Asking again must not be able to cost the customer the brief."""
    ctx, _seen = _reads(monkeypatch, _reading([]), failure)
    assert ctx.semantic_contract["mode"] == "edit"
    assert "brief_unavailable" not in ctx.editing_metrics
    assert {r["id"] for r in ctx.semantic_contract["requirements"]} >= {"r1", "r2"}


def test_a_lost_lease_during_the_correction_still_ends_the_execution(monkeypatch):
    with pytest.raises(dbx.JobLeaseLost):
        _reads(monkeypatch, _reading([]), dbx.JobLeaseLost("replaced"))


@pytest.mark.parametrize("mode", ["inspect", "clarify"])
def test_only_an_edit_is_asked_what_it_writes(monkeypatch, mode):
    reading = dict(_reading([]), mode=mode)
    reading["requirements"] = [{"description": "Should this cut go?", "action": "change",
                                "source_ref": 0}]
    _ctx, seen = _reads(monkeypatch, reading)
    assert len(seen) == 1


def test_a_brief_with_no_change_row_is_not_asked_what_it_writes(monkeypatch):
    reading = _reading([])
    reading["requirements"] = [reading["requirements"][1]]
    _ctx, seen = _reads(monkeypatch, reading)
    assert len(seen) == 1


# ---- a read cannot leave a request unfinished -------------------------------------

JUSTIFY_EARLY = ("PREREQUISITE: no complete verification record exists for EDL v11. "
                 "Render and inspect the complete preview first.")


def test_a_bookkeeping_call_made_too_early_leaves_a_delivered_edit_fulfilled(monkeypatch):
    """p5388: justified before the render, the render cleared the finding,
    nothing called it again, and the delivered edit ended partial: a P0."""
    ctx, _db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    agent_loop._record_outer_tool_outcome(
        ctx, "justify_verification_findings", JUSTIFY_EARLY)
    assert agent_loop._turn_completion(ctx, "replied")[0] == "fulfilled"


@pytest.mark.parametrize("tool", sorted(agent_loop._DELIVERS_NOTHING))
def test_every_read_is_counted_as_delivering_nothing(monkeypatch, tool):
    ctx, _db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    agent_loop._record_outer_tool_outcome(ctx, tool, JUSTIFY_EARLY)
    assert agent_loop._turn_completion(ctx, "replied")[0] == "fulfilled"


@pytest.mark.parametrize("tool", ["fetch_url", "edit_shorts", "start_asset_index"])
def test_a_tool_that_brings_work_keeps_its_prerequisite_open(monkeypatch, tool):
    """The customer's clip never arrived: that is not a delivered request."""
    ctx, _db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    agent_loop._record_outer_tool_outcome(
        ctx, tool, "PREREQUISITE: the source is still processing.")
    assert agent_loop._turn_completion(ctx, "replied")[0] == "partial"


def test_a_render_still_running_stays_open_until_its_job_lands(monkeypatch):
    ctx, _db, _posted, _checkpoint, _calls = _delivered(monkeypatch)
    agent_loop._record_outer_tool_outcome(
        ctx, "render_preview",
        "PREREQUISITE: complete preview for EDL v73 is still running as job 41.")
    assert agent_loop._turn_completion(ctx, "replied")[0] == "partial"
    agent_tools._resolve_job_waits(ctx, 41)
    assert agent_loop._turn_completion(ctx, "replied")[0] == "fulfilled"


# ---- a word list never outranks the model's reading -----------------------------

P5388 = "Turn this into a punchy original 16:9 short with big kinetic captions"


def _followup(text, mode="edit", changed=()):
    ctx = NS(semantic_contract={"mode": mode, "changed_lanes": list(changed)},
             latest_edl=lambda: {"version": 3, "json": {"keep": [[0, 20]]}})
    followup_recipes.start(ctx, text)
    return ctx


def test_a_frame_the_customer_typed_is_not_refused_as_not_asked():
    """p5388, replayed: the frame stayed 9:16 and the reply said it was
    "refused as outside this request"."""
    ctx = _followup(P5388, changed=["frame_ratio", "captions", "timeline_timing"])
    assert followup_recipes.classify(P5388) == ["punchier"]
    assert not followup_recipes.active(ctx)
    assert followup_recipes.refusal(ctx, "set_frame", {"ratio": "16:9"}) is None
    assert followup_recipes.note(ctx, P5388) == ""
    assert followup_recipes.check_write(
        ctx, {"frame": {"ratio": "9:16"}}, {"frame": {"ratio": "16:9"}}) is None


def test_the_frame_alone_is_enough_to_stand_the_recipe_down():
    assert not followup_recipes.active(_followup(P5388, changed=["frame_ratio"]))


def test_an_interjection_is_not_a_request_to_remove_filler():
    text = "Umm you didn't do anything that I asked and made me wait around 2 hours"
    assert not followup_recipes.active(_followup(
        text, changed=["cuts", "timeline_timing", "music", "texts"]))


def test_a_reading_that_agrees_keeps_the_recipe_and_its_guard():
    """The one real firing in 14 days: an unasked zoom on a title request."""
    text = "make the opening title a little bigger"
    ctx = _followup(text, changed=["texts"])
    assert followup_recipes.active(ctx)
    assert "NOT ASKED" in followup_recipes.refusal(ctx, "add_zoom", {})


def test_effects_beside_a_cut_keeps_the_recipe():
    """The recipe's own case: "cut the boring parts" came back longer."""
    ctx = _followup("cut the boring parts",
                    changed=["cuts", "timeline_timing", "effects"])
    assert followup_recipes.active(ctx)["direction"] == "shorter"


@pytest.mark.parametrize("mode", ["open", "inspect", "clarify"])
def test_no_edit_brief_means_no_recipe(mode):
    """An open brief promises nothing frozen, and writes are refused in the
    other two, so a recipe would tell the editor to do what cannot land."""
    assert not followup_recipes.active(_followup("make the captions bigger", mode))


def test_with_no_brief_at_all_the_words_stand():
    ctx = NS(semantic_contract=None,
             latest_edl=lambda: {"version": 3, "json": {"keep": [[0, 20]]}})
    assert followup_recipes.start(ctx, "make the captions bigger")


def test_the_reply_is_written_for_someone_who_has_never_seen_our_internals():
    """Nothing rewrites the reply now, and 7 of 31 replayed replies named a
    version ("passed on the current version (v23)")."""
    text = " ".join(str(v) for v in vars(agent_prompt).values() if isinstance(v, str))
    assert "No version numbers, no tool names and no names of our checks" in text
