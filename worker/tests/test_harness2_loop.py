"""Harness v2: routing in run_agent_job (A2), the loop end to end with a
scripted model (E18), resume without a duplicate write, and the lane chain
(E19's widened switch)."""
import copy
import json
import os
import sys
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
if WORKER not in sys.path:
    sys.path.insert(0, WORKER)

import agent_loop  # noqa: E402
import agent_tools  # noqa: E402
import db as dbx  # noqa: E402
import read_tools  # noqa: E402
from harness2 import chain, commit, loop, tools as tools_mod  # noqa: E402
from w0c_support import FakeDb, tool_context  # noqa: E402


class LoopDb(FakeDb):
    def __init__(self, edl, version=7):
        super().__init__(edl, version)
        self.messages, self.checkpoints, self.continuations = [], [], []
        self.activity = []

    def run(self, fn, *a, **k):
        if fn is dbx.add_message:
            row = {"session_id": a[0], "role": a[1], "content": a[2],
                   "meta": a[3] if len(a) > 3 else None}
            (self.activity if a[1] == "activity" else
             self.messages).append(row)
            return len(self.messages)
        if fn is loop.save_checkpoint:
            self.checkpoints.append(copy.deepcopy(a[2]))
            return None
        if fn is dbx.enqueue_agent_continuation:
            self.continuations.append(a[4])
            return 990 + len(self.continuations)
        if fn in (dbx.recent_chat, read_tools.assets_by_kinds,
                  dbx.edl_history):
            return []
        return super().run(fn, *a, **k)


def _resp(content=None, calls=()):
    tcs = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(
        name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(calls)]
    msg = SimpleNamespace(content=content, tool_calls=tcs, refusal=None)
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg,
                                                    finish_reason="stop")],
                           usage=usage)


class Script:
    def __init__(self, replies, base_url="http://fake.local/v1"):
        self.replies = list(replies)
        self.seen = []
        self.base_url = base_url
        self.chat = SimpleNamespace(completions=SimpleNamespace(
            create=self.create))

    def create(self, **body):
        self.seen.append(copy.deepcopy(body.get("messages")))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _lane(client, name="fake", model="gpt-test-editor"):
    return {"name": name, "model": model, "client": client,
            "base_url": "http://fake.local/v1", "api_key": "x"}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    import llm
    monkeypatch.setattr(llm, "fence", lambda model: None)
    monkeypatch.setattr(agent_loop, "_sleep_keeping_lease",
                        lambda *a, **k: None)
    monkeypatch.setattr(loop, "_assets", lambda ctx: [])
    monkeypatch.setattr(loop.render_tool, "start_preview",
                        lambda ctx, quality="draft": {"handle": None})
    chain.reopen()
    commit._RECEIPTS.clear()
    yield
    chain.reopen()
    commit._RECEIPTS.clear()


def _turn(keep=None):
    ctx, _fake = tool_context({"keep": keep or [[0.0, 60.0]]}, duration=60.0)
    db = LoopDb(ctx.db.rows[-1]["json"])
    ctx.db = db
    ctx.cut_sound_source = lambda: None
    job = {"id": 501, "project_id": 7, "user_id": 3, "total_claims": 1,
           "payload": {"message_id": 9, "harness": "v2"}}
    ctx.job = job
    msg = {"id": 9, "content": "cut 10-20 out"}
    return ctx, db, job, msg


# -- E18 the loop ----------------------------------------------------------

def test_a_short_turn_runs_end_to_end():
    ctx, db, job, msg = _turn()
    script = Script([
        _resp(calls=[("edit", {"ops": [{"cut": [[10, 20]]}],
                               "note": "cut"})]),
        _resp(calls=[("timeline", {})]),
        _resp(content="Cut 10-20 s; the film is 50 s now."),
    ])
    ctx.agent_lanes = [_lane(script)]
    result = loop.run(ctx, db, job, 11, msg, None)
    assert result["status"] == "replied" and result["harness"] == "v2"
    assert result["edl_changed"] is True and db.inserts == [8]
    reply = db.messages[-1]
    assert reply["role"] == "assistant"
    assert reply["content"].startswith("Cut 10-20 s")
    assert reply["meta"]["harness"] == "v2"
    assert reply["meta"]["edl_version"] == 8
    assert len(db.checkpoints) == 2           # one per tool step
    assert result["tools"] == {"edit": 1, "timeline": 1}
    sent = script.seen[0]
    assert sent[0]["role"] == "system" and "seven tools" in sent[0]["content"]
    assert "cut 10-20 out" in sent[-1]["content"]
    # the tool result the model read is the envelope
    tool_msgs = [m for m in script.seen[1] if m.get("role") == "tool"]
    assert tool_msgs and "status: done" in tool_msgs[-1]["content"]
    assert len(db.activity) == 2


def test_a_killed_worker_resumes_without_a_duplicate_write(monkeypatch):
    ctx, db, job, msg = _turn()
    calls = [("edit", {"ops": [{"cut": [[10, 20]]}], "note": "cut"})]

    class Died(Exception):
        pass

    real = loop._checkpoint

    def die(*a, **k):
        raise Died("worker killed after the write, before the checkpoint")
    monkeypatch.setattr(loop, "_checkpoint", die)
    ctx.agent_lanes = [_lane(Script([_resp(calls=calls)]))]
    with pytest.raises(Died):
        loop.run(ctx, db, job, 11, msg, None)
    assert db.inserts == [8]
    monkeypatch.setattr(loop, "_checkpoint", real)
    # The resume (same root, no checkpoint for that step) replays the step.
    ctx2, _f = tool_context({"keep": [[0.0, 60.0]]}, duration=60.0)
    ctx2.db, ctx2.job, ctx2.cut_sound_source = db, job, (lambda: None)
    ctx2.agent_lanes = [_lane(Script([_resp(calls=calls),
                                      _resp(content="Done: 50 s.")]))]
    result = loop.run(ctx2, db, dict(job, payload=dict(job["payload"],
                                                        death_resume=1)),
                      11, msg, None)
    assert db.inserts == [8]                  # no second version
    assert result["edl_version"] == 8


def test_a_slice_over_budget_continues_with_its_checkpoint(monkeypatch):
    ctx, db, job, msg = _turn()
    monkeypatch.setattr(loop, "MAX_STEPS_PER_SLICE", 1)
    ctx.agent_lanes = [_lane(Script([
        _resp(calls=[("timeline", {})])]))]
    result = loop.run(ctx, db, job, 11, msg, None)
    assert result["status"] == "continued"
    nxt = db.continuations[-1]
    assert nxt["harness"] == "v2"                     # the stamp rides on
    assert nxt["continuation_state"]["v2"]["step"] == 1
    # the next slice starts from that checkpoint
    ctx.agent_lanes = [_lane(Script([_resp(content="All good.")]))]
    job2 = dict(job, id=502, payload=nxt)
    out = loop.run(ctx, db, job2, 11, msg, nxt["continuation_state"])
    assert out["status"] == "replied"


# -- E19 the lane chain -----------------------------------------------------

class Err(Exception):
    def __init__(self, text, status=None):
        super().__init__(text)
        self.status_code = status


def test_a_billing_error_on_lane_one_runs_the_turn_on_lane_two():
    ctx, db, job, msg = _turn()
    first = Script([Err("Your credit balance is too low", 400)])
    second = Script([_resp(content="Here it is.")])
    ctx.agent_lanes = [_lane(first, "claude_paid"), _lane(second, "luna")]
    result = loop.run(ctx, db, job, 11, msg, None)
    assert result["status"] == "replied"
    assert db.messages[-1]["content"] == "Here it is."
    assert chain.latched(ctx.agent_lanes[0])


@pytest.mark.parametrize("err,kind", [
    (Err("overloaded_error", 529), "transient"),
    (Err("upstream 502", 502), "transient"),
    (Err("invalid x-api-key", 401), "auth"),
    (Err("insufficient_quota", 429), "billing"),
    (Err("rate limit", 429), "rate_limit"),
    (TimeoutError("Request timed out"), "transient"),
])
def test_errors_are_classified(err, kind):
    assert chain.classify(err) == kind


def test_with_every_lane_down_one_parked_message_then_it_runs(monkeypatch):
    ctx, db, job, msg = _turn()
    monkeypatch.setattr(loop, "PARK_WAIT_S", 0.0)
    ctx.agent_lanes = [_lane(Script([Err("credit balance too low", 402)]))]
    result = loop.run(ctx, db, job, 11, msg, None)
    assert result["status"] == "continued"
    parked = [m for m in db.messages if (m["meta"] or {}).get("parked")]
    assert len(parked) == 1 and parked[0]["meta"]["why"] == "provider"
    nxt = db.continuations[-1]
    assert nxt["continuation_state"]["v2"]["parks"] == 1
    # the probe opens the lane: the parked request runs, no second message
    chain.reopen()
    ctx.agent_lanes = [_lane(Script([_resp(content="Back and done.")]))]
    out = loop.run(ctx, db, dict(job, id=503, payload=nxt), 11, msg,
                   nxt["continuation_state"])
    assert out["status"] == "replied"
    assert len([m for m in db.messages
                if (m["meta"] or {}).get("parked")]) == 1


# -- A2 routing -------------------------------------------------------------

def test_run_agent_job_routes_by_the_stamp(monkeypatch):
    import claude_trial
    import llm
    src = open(os.path.join(WORKER, "agent_loop.py")).read()
    branch = src.index('if payload.get("harness") == "v2":')
    assert branch < src.index("attachment_note = _attachment_context(")
    assert src.index("if (job.get(\"payload\") or {}).get(\"first_cut\"):") \
        < branch
    seen = []
    monkeypatch.setattr(loop, "run", lambda *a, **k: seen.append("v2") or {
        "status": "replied", "harness": "v2"})
    monkeypatch.setattr(agent_loop, "_run_loop", lambda *a, **k: seen.append(
        "v1") or {"status": "replied"})
    ctx, _fake = tool_context({"keep": [[0.0, 60.0]]}, duration=60.0)
    monkeypatch.setattr(agent_loop, "_attachment_context",
                        lambda *a, **k: "")
    for name in ("_before_first_cut_note", "_followup_start",
                 "_typed_asks_start"):
        monkeypatch.setattr(agent_loop, name, lambda *a, **k: "")
    import request_contract
    monkeypatch.setattr(request_contract, "ensure", lambda ctx: None)
    monkeypatch.setattr(request_contract, "history", lambda *a, **k: [])
    monkeypatch.setattr(request_contract, "edit_origin", lambda *a, **k: None)
    monkeypatch.setattr(agent_tools, "queue_sharper_preview",
                        lambda ctx: None)
    monkeypatch.setattr(claude_trial, "resolve", lambda *a, **k:
                        SimpleNamespace(kind="none", scope=None))
    monkeypatch.setattr(llm, "agent_lanes_for", lambda *a, **k: [
        _lane(Script([]))])
    monkeypatch.setattr(agent_loop, "_claude_paid_lane", lambda *a, **k: None)
    monkeypatch.setattr(agent_loop, "_customer_verification_request",
                        lambda *a, **k: None)
    monkeypatch.setattr(agent_loop, "_request_media_keys", lambda *a, **k: [])

    class Db(LoopDb):
        def run(self, fn, *a, **k):
            name = getattr(fn, "__name__", "")
            if fn is dbx.get_project:
                return {"id": 7, "chat_session_id": 11}
            if name == "_get_msg":
                return {"id": 9, "content": "hello", "role": "user"}
            if fn is dbx.latest_asset:
                return None
            if fn is dbx.user_billing:
                return (True, "pro_2026", False)
            if fn is dbx.user_credits_balance:
                return 100
            return super().run(fn, *a, **k)
    import website_engine
    monkeypatch.setattr(website_engine, "route_url", lambda *a, **k: None)
    db = Db(ctx.db.rows[-1]["json"])
    for payload in ({"message_id": 9, "harness": "v2"}, {"message_id": 9}):
        agent_loop.run_agent_job(db, {"id": 600, "project_id": 7,
                                      "user_id": 3, "payload": payload})
    assert seen == ["v2", "v1"]


# -- F21 a long analysis inside a turn is progress, never a stall ------------

def test_a_long_tool_call_continues_the_turn_instead_of_stalling(
        monkeypatch):
    ctx, db, job, msg = _turn()
    clock = {"t": 1000.0}
    monkeypatch.setattr(loop.time, "monotonic", lambda: clock["t"])
    real = tools_mod.dispatch

    def slow(c, name, args, wait_s=None):
        clock["t"] += 360.0                       # a 6-minute measurement
        return real(c, name, args, wait_s=wait_s)
    monkeypatch.setattr(tools_mod, "dispatch", slow)
    ctx.agent_lanes = [_lane(Script([
        _resp(calls=[("timeline", {})]),
        _resp(calls=[("timeline", {})])]))]
    result = loop.run(ctx, db, job, 11, msg, None)
    assert result["status"] == "continued"         # not failed, not stalled
    assert db.continuations[-1]["continuation_state"]["v2"]["step"] == 2
    assert not [m for m in db.messages if m["role"] == "assistant"]


def test_a_turn_stops_on_what_the_account_can_pay(monkeypatch):
    ctx, db, job, msg = _turn()
    script = Script([_resp(calls=[("edit", {"ops": [{"cut": [[10, 20]]}]})]),
                     _resp(content="never reached")])
    ctx.agent_lanes = [_lane(script)]
    ctx.subscribed, ctx.trialing, ctx.claude_trial = True, False, None
    spent = {"n": 0}

    def over():
        spent["n"] += 1
        return spent["n"] > 1                      # after the first step
    ctx.over_budget = over
    result = loop.run(ctx, db, job, 11, msg, None)
    assert result["outcome"] == "budget" and db.inserts == [8]
    reply = db.messages[-1]
    assert reply["meta"]["credits_exhausted"] is True
    assert "saved" in reply["content"] and len(script.replies) == 1
