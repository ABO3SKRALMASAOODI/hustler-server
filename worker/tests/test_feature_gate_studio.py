"""Shorts and Website demos come with Pro and Max, in the Studio (worker).

Owner rule (30 Oct 2026, verbatim): "clicking on shorts or website demo in
trial or plus must show an upgrade card no podcasts or recordings for those
plans excluding the mcp".

Proved here: a Studio agent turn of a gated account (feature_gate.py: a
free-trial account, a new Plus subscriber) never runs make_shorts (on a long
source), record_website, record_website_demo or showcase_demo; the turn ends
on the upgrade card and is not charged. MCP calls, Pro, Max and grandfathered
accounts run them exactly as before. The shorts runner and the trial's URL
First cut answer a gated Studio job with the same card, uncharged.
"""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import agent_loop
import agent_tools
import db as dbx
import feature_gate
import job_completion
from schemas import default_edl

WORKER = Path(__file__).resolve().parents[1]
GATED_CALLS = {
    "make_shorts": {},
    "record_website": {"url": "https://acme.io"},
    "record_website_demo": {"url": "https://acme.io", "steps": []},
    "showcase_demo": {"asset_key": "captures/9/a.mp4"},
}


class Db:
    def __init__(self, why="trial", duration=600.0):
        self.why = why
        self.gate_reads = 0
        self.rows = [{"version": 1, "json": default_edl(duration)}]
        self.posted = []
        self.events = []

    def run(self, fn, *args):
        if fn is feature_gate.safe_reason:
            self.gate_reads += 1
            return self.why
        if fn is dbx.latest_edl:
            return self.rows[-1]
        if fn is dbx.insert_edl:
            self.rows.append({"version": self.rows[-1]["version"] + 1,
                              "json": args[1]})
            return self.rows[-1]["version"]
        if fn is dbx.add_message:
            self.posted.append({"session": args[0], "role": args[1],
                                "content": args[2], "meta": args[3]})
            return 900 + len(self.posted)
        if fn is dbx.record_client_event:
            self.events.append(args)
            return None
        if fn is dbx.recent_llm_tokens:
            return 0
        return None


def _ctx(monkeypatch, why="trial", job_type="agent_turn", duration=600.0,
         parent=None, payload=None):
    db = Db(why, duration)
    ctx = agent_tools.ToolContext(
        db, {"id": 3, "type": job_type, "user_id": 8,
             "payload": dict(payload or {})},
        {"id": 9, "chat_session_id": 2, "parent_project_id": parent},
        {"video": {"duration": duration, "width": 1920, "height": 1080,
                   "fps": 30, "has_audio": True}}, "/tmp")
    ran = []
    for name in GATED_CALLS:
        _fn, desc, props = agent_tools.TOOLS[name]

        def fake(ctx, _name=name, **kwargs):
            ran.append(_name)
            return "NO CHANGE — test double"
        monkeypatch.setitem(agent_tools.TOOLS, name, (fake, desc, props))
    return ctx, db, ran


def test_the_module_is_the_backends_byte_for_byte():
    assert (WORKER / "feature_gate.py").read_bytes() == \
        (WORKER.parent / "backend" / "feature_gate.py").read_bytes()


@pytest.mark.parametrize("why", ["trial", "plus"])
@pytest.mark.parametrize("name", sorted(GATED_CALLS))
def test_a_gated_studio_turn_never_runs_the_tool(monkeypatch, why, name):
    ctx, db, ran = _ctx(monkeypatch, why=why)
    with pytest.raises(agent_tools.FeatureUpgrade) as raised:
        agent_tools.execute(ctx, name, dict(GATED_CALLS[name]))
    assert ran == []
    assert raised.value.feature == feature_gate.TOOL_FEATURES[name]
    assert raised.value.reason == why and raised.value.tool == name


@pytest.mark.parametrize("name", sorted(GATED_CALLS))
def test_mcp_calls_are_never_gated(monkeypatch, name):
    """Owner: "excluding the mcp" — on any plan, trial or Plus included."""
    ctx, db, ran = _ctx(monkeypatch, why="trial", job_type="mcp_tool")
    agent_tools.execute(ctx, name, dict(GATED_CALLS[name]))
    assert ran == [name]
    assert db.gate_reads == 0


@pytest.mark.parametrize("name", sorted(GATED_CALLS))
def test_pro_max_and_grandfathered_studio_turns_run_as_before(monkeypatch,
                                                              name):
    ctx, db, ran = _ctx(monkeypatch, why=None)
    agent_tools.execute(ctx, name, dict(GATED_CALLS[name]))
    assert ran == [name]


def test_make_shorts_on_own_short_footage_is_an_ordinary_edit(monkeypatch):
    """A sub-minute source or a generated short: make_shorts only routes the
    turn into a normal edit of the user's own footage, which stays theirs."""
    ctx, _db, ran = _ctx(monkeypatch, why="trial", duration=30.0)
    agent_tools.execute(ctx, "make_shorts", {})
    ctx, _db, ran2 = _ctx(monkeypatch, why="plus", parent=4)
    agent_tools.execute(ctx, "make_shorts", {})
    assert ran == ["make_shorts"] and ran2 == ["make_shorts"]


def test_ordinary_tools_and_operator_repairs_are_untouched(monkeypatch):
    ctx, db, _ran = _ctx(monkeypatch, why="trial")
    out = agent_tools.execute(ctx, "set_color_grade", {"preset": "warm"})
    assert out.startswith("EDL v1 -> v2") and db.gate_reads == 0
    ctx, db, ran = _ctx(monkeypatch, why="trial",
                        payload={"operator_repair": True})
    agent_tools.execute(ctx, "record_website", dict(GATED_CALLS[
        "record_website"]))
    assert ran == ["record_website"]


def test_the_gate_is_read_once_per_turn(monkeypatch):
    ctx, db, _ran = _ctx(monkeypatch, why="plus")
    for name in ("record_website", "showcase_demo"):
        with pytest.raises(agent_tools.FeatureUpgrade):
            agent_tools.execute(ctx, name, dict(GATED_CALLS[name]))
    assert db.gate_reads == 1


def test_a_lookup_failure_reads_as_not_gated(monkeypatch):
    ctx, db, ran = _ctx(monkeypatch, why=None)

    def broken(fn, *args):
        if fn is feature_gate.safe_reason:
            raise RuntimeError("db down")
        return Db.run(db, fn, *args)
    db.run = broken
    agent_tools.execute(ctx, "record_website",
                        dict(GATED_CALLS["record_website"]))
    assert ran == ["record_website"]


# ------------------------------------------------------- the agent loop

def _loop(monkeypatch, why="trial"):
    ctx, db, ran = _ctx(monkeypatch, why=why)
    ctx.user_message = "Record my website https://acme.io as a demo"
    ctx.verification_request = ctx.user_message
    ctx.turn_start_edl = db.rows[-1]
    ctx.turn_baseline_digest = "before"
    ctx.over_budget = lambda: False
    ctx.adopted_steer_job_ids = set()
    ctx._proof_ranges_by_version = {}
    monkeypatch.setattr(agent_loop, "_build_messages", lambda *a, **k: [
        {"role": "user", "content": ctx.user_message}])
    monkeypatch.setattr(agent_loop, "_adopt_steering_messages", lambda *a: 1)
    monkeypatch.setattr(agent_loop, "_activity", lambda *a, **k: None)
    monkeypatch.setattr(agent_loop.llm, "responses_available",
                        lambda *a, **k: False)
    monkeypatch.setattr(agent_loop.llm, "record", lambda *a: None)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        call = NS(id="t1", type="function", function=NS(
            name="record_website_demo",
            arguments=json.dumps(GATED_CALLS["record_website_demo"])))
        return NS(choices=[NS(message=NS(content="", tool_calls=[call]),
                              finish_reason="tool_calls")], usage=None)
    ctx.llm_client = NS(chat=NS(completions=NS(create=create)))
    job = {"id": 3, "type": "agent_turn", "project_id": 9, "user_id": 8,
           "payload": {"message_id": 1}}
    result = agent_loop._run_loop(ctx, db, job, 2,
                                  {"id": 1, "content": ctx.user_message})
    return result, db, ran, calls, job


@pytest.mark.parametrize("why", ["trial", "plus"])
def test_the_turn_ends_on_the_upgrade_card_and_is_not_charged(monkeypatch,
                                                              why):
    result, db, ran, calls, job = _loop(monkeypatch, why)
    assert ran == [] and len(calls) == 1          # no second model call
    assert result["billable"] is False and result["credits_charged"] == 0.0
    assert result["outcome"] == "upgrade_required"
    assert result["feature_upgrade"] == "website_demo"
    assert job_completion._result_is_billable(job, result) is False
    assert job_completion._qualifies_subscribe_gate(job, result) is False
    (card,) = db.posted
    assert card["role"] == "assistant"
    meta = card["meta"]
    assert meta["kind"] == "feature_upgrade" and meta["reason"] == why
    assert meta["feature"] == "website_demo"
    assert meta["request_message_id"] == 1 and meta["edl_version"] == 1
    assert meta["outcome"] == "upgrade_required"
    assert "subscription_offer" not in meta      # no price copied here
    assert card["content"].startswith("Shorts and Website demos come with")
    assert db.events and db.events[-1][2] == "feature_upgrade_shown"


# ------------------------------------------- the shorts runner, First cut

class Stop(Exception):
    pass


class RunnerDb(Db):
    def run(self, fn, *args):
        if fn is dbx.get_project:
            return {"id": 9, "chat_session_id": 2, "meta": {}, "user_id": 8}
        if fn is dbx.latest_asset:
            return {"id": 41, "sha256": "s"}
        if fn is dbx.get_index_by_sha:
            return {"json": {"video": {"duration": 3600.0}}}
        if fn is dbx.user_credits_balance:
            raise Stop()
        return super().run(fn, *args)


@pytest.mark.parametrize("source", ["user", "agent", "agent_direct"])
def test_a_studio_shorts_run_answers_with_the_card(source):
    import shorts
    db = RunnerDb("trial")
    job = {"id": 5, "type": "shorts_plan", "project_id": 9, "user_id": 8,
           "payload": {"source": source}}
    result = shorts.run_shorts_plan(db, job)
    assert result["billable"] is False and result["clips"] == 0
    assert result["feature_upgrade"] == "shorts"
    assert job_completion._result_is_billable(job, result) is False
    assert job_completion._qualifies_subscribe_gate(job, result) is False
    assert db.posted[-1]["meta"]["kind"] == "feature_upgrade"
    assert db.posted[-1]["meta"]["feature"] == "shorts"


def test_an_mcp_shorts_run_is_never_gated():
    import shorts
    db = RunnerDb("trial")
    job = {"id": 5, "type": "shorts_plan", "project_id": 9, "user_id": 8,
           "payload": {"source": "mcp_direct", "clips": []}}
    with pytest.raises(Stop):                     # it went on to billing
        shorts.run_shorts_plan(db, job)
    assert db.gate_reads == 0 and db.posted == []


def test_an_ungated_studio_shorts_run_goes_on_as_before():
    import shorts
    db = RunnerDb(None)
    with pytest.raises(Stop):
        shorts.run_shorts_plan(db, {"id": 5, "type": "shorts_plan",
                                    "project_id": 9, "user_id": 8,
                                    "payload": {"source": "user"}})
    assert db.posted == []


def test_the_trials_url_first_cut_answers_with_the_card(monkeypatch):
    import first_cut
    db = Db("trial", duration=0.0)
    monkeypatch.setattr(agent_tools, "execute", lambda *a, **k: (_ for _ in ()
                        ).throw(AssertionError("no tool may run")))
    job = {"id": 6, "type": "agent_turn", "project_id": 9, "user_id": 8,
           "payload": {"first_cut": True, "trial_lane": True,
                       "url": "https://acme.io", "message_id": 12}}
    result = first_cut.run(db, job, {"id": 9, "chat_session_id": 2}, None)
    assert result["billable"] is False and result["credits_charged"] == 0.0
    assert result["outcome"] == "upgrade_required"
    assert job_completion._result_is_billable(job, result) is False
    (card,) = db.posted
    assert card["meta"]["kind"] == "feature_upgrade"
    assert card["meta"]["feature"] == "website_demo"
    assert card["meta"]["request_message_id"] == 12


def test_a_first_cut_of_uploaded_footage_never_reads_the_gate():
    """These accounts keep the normal First cut of their own footage."""
    src = (WORKER / "first_cut.py").read_text()
    run = src[src.index("def run(worker_db, job, project, index_row):"):]
    assert ("    if url:\n        # Website demos come with Pro and Max"
            in run)
    assert run.count("stop = _feature_gate_stop(") == 1
