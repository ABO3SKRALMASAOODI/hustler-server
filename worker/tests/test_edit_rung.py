"""The OpenAI edit rung (worker/edit_rung.py, 14 Nov 2026).

An OpenAI model the Gauntlet scoreboard cleared may decide a customer's edit
on the Claude lane, and Haiku answers the SAME call on any OpenAI failure, for
the rest of that turn; a wallet or key refusal shuts the rung for every turn
for a while. The customer's credits and a trial's allowance are priced at
Haiku's card for the same tokens; the true OpenAI cost stays on the row."""

import json
from types import SimpleNamespace

import pytest

import claude_lane
import claude_plans
import claude_trial
import config
import edit_rung
import llm
import model_prices
from tests.test_claude_lane import _FakeSdk, _message  # noqa: F401

TOOLS = [{"type": "function", "function": {
    "name": "keep_segments", "description": "Keep these ranges.",
    "parameters": {"type": "object", "properties": {
        "segments": {"type": "array"}}}}}]
MSGS = [{"role": "system", "content": "kernel"},
        {"role": "user", "content": [
            {"type": "text", "text": "cut the pauses"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {
                "name": "keep_segments", "arguments": '{"segments": [[0, 4]]}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "EDL v2"}]


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if not isinstance(body, str) else body

    def json(self):
        return self._body


def _ok(text="Done: the pauses are cut.", calls=()):
    out = [{"type": "function_call", "call_id": c, "name": "keep_segments",
            "arguments": '{"segments": [[0, 2]]}'} for c in calls]
    if text:
        out.append({"type": "message", "content": [{"type": "output_text", "text": text}]})
    return _Resp(200, {"id": "resp_1", "status": "completed", "output": out,
                       "usage": {"input_tokens": 20000, "output_tokens": 300,
                                 "input_tokens_details": {"cached_tokens": 15000},
                                 "output_tokens_details": {"reasoning_tokens": 40}}})


@pytest.fixture
def rung(monkeypatch):
    edit_rung.reopen()
    edit_rung.clear_turn()
    claude_lane.reopen()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "EDIT_RUNG_MODELS", "gpt-6.1-sol")
    monkeypatch.setattr(config, "EDIT_RUNG_FALLBACK_MODEL", "claude-haiku-5-5")
    monkeypatch.setattr(claude_lane, "anthropic", object())
    monkeypatch.setattr(claude_lane.time, "sleep", lambda s: None)
    monkeypatch.setattr(edit_rung.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "get_recorder", lambda: None)
    # a plain attribute (another test's monkeypatch may already have left one)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    metered, paid, alerts = [], [], []
    monkeypatch.setattr(claude_trial, "meter",
                        lambda model, usd, kind="llm": metered.append((model, usd, kind)))
    monkeypatch.setattr(claude_trial, "meter_paid",
                        lambda model, usd: paid.append((model, usd)))
    monkeypatch.setattr(claude_trial, "_alert", lambda *a: alerts.append(a))
    import openai_credit
    openai_credit.reopen()
    monkeypatch.setattr(openai_credit, "_alert", getattr(openai_credit, "_real_alert", openai_credit._alert))
    # the credit gate's alert row is confirmed by its own queue (openai_credit._queue_alert)
    monkeypatch.setattr(openai_credit, "_queue_alert",
                        lambda key, subject, html: alerts.append((key, "openai_credit")) or True)
    posts = []
    replies = []

    def post(url, json=None, headers=None, timeout=None):
        posts.append(json)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    yield SimpleNamespace(metered=metered, paid=paid, alerts=alerts, posts=posts,
                          replies=replies,
                          use=lambda sdk: monkeypatch.setattr(claude_lane, "_sdk", lambda: sdk))
    edit_rung.reopen()
    edit_rung.clear_turn()
    claude_lane.reopen()


def test_only_scoreboard_models_ride_the_rung(rung):
    assert edit_rung.is_rung_model("gpt-6.1-sol")
    assert not edit_rung.is_rung_model("gpt-6-luna")
    assert not edit_rung.is_rung_model("claude-haiku-5-5")
    # the switches accept the rung model and nothing else from OpenAI
    assert claude_trial.parse_flag({"model": "gpt-6.1-sol"})["model"] == "gpt-6.1-sol"
    assert claude_trial.parse_flag({"model": "gpt-6-luna"})["model"] == config.CLAUDE_TRIAL_MODEL
    flag = claude_plans.parse_flag({"on": True, "cutoff_at": "2026-10-30T00:00:00Z",
                                    "plan_models": {"plus_2026": "gpt-6.1-sol",
                                                    "pro_2026": "gpt-6-luna"}})
    assert claude_plans.lane_model(flag, "plus_2026") == "gpt-6.1-sol"
    assert claude_plans.lane_model(flag, "pro_2026") == "claude-haiku-5-5"


def test_a_rung_call_answers_on_openai_and_meters_the_trial_at_haikus_card(rung):
    rung.replies.append(_ok(calls=("call_9",)))
    scope = claude_trial.Scope(5, 1.0, 0.0, "gpt-6.1-sol", job_id=77)
    claude_trial.activate(scope)
    try:
        resp = claude_lane.ClaudeLaneClient().chat.completions.create(
            model="gpt-6.1-sol", messages=MSGS, tools=TOOLS, max_tokens=4000,
            reasoning_effort="medium")
    finally:
        claude_trial.deactivate()
    body = rung.posts[0]
    assert body["model"] == "gpt-6.1-sol" and body["store"] is False
    assert body["reasoning"] == {"effort": "medium"}
    assert body["prompt_cache_key"] == "valmera:editor"
    kinds = [i.get("type") or i.get("role") for i in body["input"]]
    assert kinds == ["developer", "user", "function_call", "function_call_output"]
    assert body["input"][1]["content"][1]["type"] == "input_image"
    assert resp.choices[0].message.tool_calls[0].id == "call_9"
    assert resp.usage.answered_model == "gpt-6.1-sol"
    true_cost = model_prices.luna6_usage_cost(20000, 300, 15000, 0, "default", model="gpt-6.1-sol")
    assert true_cost == pytest.approx(model_prices.base_usage_cost("gpt-6.1-sol", 20000, 300,
                                                                   cached=15000))
    assert resp.usage.provider_cost_usd == pytest.approx(true_cost)
    # the trial allowance: the same tokens at Haiku's card, off the Claude line
    [(model, usd, kind)] = rung.metered
    assert kind == "openai" and model == "gpt-6.1-sol"
    assert usd == pytest.approx(llm.credit_parity_cost_usd(resp.usage, "claude-haiku-5-5"))
    assert usd < true_cost / 5
    assert rung.paid == []          # never the paid Claude ledger


def test_any_openai_failure_is_answered_by_haiku_in_the_same_call_and_turn(rung):
    rung.replies.extend([_Resp(500, "upstream"), _Resp(502, "upstream")])
    sdk = _FakeSdk([_message([SimpleNamespace(type="text", text="haiku did it")], stop="end_turn"),
                    _message([SimpleNamespace(type="text", text="haiku again")], stop="end_turn")])
    rung.use(sdk)
    client = claude_lane.ClaudeLaneClient()
    resp = client.chat.completions.create(model="gpt-6.1-sol", messages=MSGS, tools=TOOLS)
    assert resp.choices[0].message.content == "haiku did it"
    assert resp.usage.answered_model == "claude-haiku-5-5"
    assert sdk.bodies[0]["model"] == "claude-haiku-5-5"
    assert len(rung.posts) == 2            # one retry, then the fallback
    # the rest of the turn stays on Haiku: no OpenAI call in front
    resp = client.chat.completions.create(model="gpt-6.1-sol", messages=MSGS, tools=TOOLS)
    assert resp.choices[0].message.content == "haiku again"
    assert len(rung.posts) == 2
    assert edit_rung.available()           # a 5xx does not shut the rung for others
    assert len(rung.paid) == 2             # Haiku's calls are Anthropic money, metered there


def test_a_timeout_or_an_empty_answer_falls_back_too(rung):
    import httpx
    rung.replies.extend([httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")])
    rung.use(_FakeSdk([_message([SimpleNamespace(type="text", text="ok")], stop="end_turn")]))
    resp = claude_lane.create("gpt-6.1-sol", MSGS, tools=TOOLS)
    assert resp.usage.answered_model == "claude-haiku-5-5"
    edit_rung.clear_turn()
    rung.replies.append(_Resp(200, {"status": "completed", "output": [], "usage": {}}))
    rung.use(_FakeSdk([_message([SimpleNamespace(type="text", text="ok")], stop="end_turn")]))
    resp = claude_lane.create("gpt-6.1-sol", MSGS, tools=TOOLS)
    assert resp.usage.answered_model == "claude-haiku-5-5"


def test_the_credit_running_out_shuts_the_rung_for_every_turn_and_alerts(rung):
    rung.replies.append(_Resp(429, {"error": {"code": "insufficient_quota",
                                              "message": "You exceeded your current quota"}}))
    rung.use(_FakeSdk([_message([SimpleNamespace(type="text", text="a")], stop="end_turn"),
                       _message([SimpleNamespace(type="text", text="b")], stop="end_turn")]))
    claude_lane.create("gpt-6.1-sol", MSGS, tools=TOOLS)
    assert len(rung.posts) == 1            # a wallet refusal is not retried
    assert not edit_rung.available() and "insufficient_quota" in edit_rung.closed_reason()
    assert len(rung.alerts) == 1
    edit_rung.clear_turn()                 # a new turn: still straight to Haiku
    resp = claude_lane.create("gpt-6.1-sol", MSGS, tools=TOOLS)
    assert resp.usage.answered_model == "claude-haiku-5-5" and len(rung.posts) == 1


def test_the_row_names_the_model_that_ran_and_prices_credits_at_haikus_card(rung, monkeypatch):
    rows, charged = [], []

    def recorder(purpose, request, response, usage):
        rows.append((request, response))
    monkeypatch.setattr(llm, "get_recorder", lambda: recorder)
    monkeypatch.setattr(llm, "_charge", lambda p, req, resp, u: charged.append(req["model"]))
    rung.replies.append(_ok())
    resp = claude_lane.create("gpt-6.1-sol", MSGS, tools=TOOLS)
    llm.record("agent", {"model": "gpt-6.1-sol"}, {"content": "x"}, resp.usage)
    request, response = rows[-1]
    assert request["model"] == "gpt-6.1-sol" and response["edit_rung"] is True
    assert response["provider_cost_usd"] == pytest.approx(resp.usage.provider_cost_usd)
    assert response["credit_price_model"] == "claude-haiku-5-5"
    assert response["credit_cost_usd"] == pytest.approx(
        llm.credit_parity_cost_usd(resp.usage, "claude-haiku-5-5"))
    assert charged[-1] == "gpt-6.1-sol"
    # a call asked of sol that Haiku answered is a Haiku row
    rung.replies.append(_Resp(500, "x"))
    rung.replies.append(_Resp(500, "x"))
    rung.use(_FakeSdk([_message([SimpleNamespace(type="text", text="h")], stop="end_turn")]))
    resp = claude_lane.create("gpt-6.1-sol", MSGS, tools=TOOLS)
    llm.record("agent", {"model": "gpt-6.1-sol"}, {"content": "h"}, resp.usage)
    request, response = rows[-1]
    assert request["model"] == "claude-haiku-5-5" and request["asked_model"] == "gpt-6.1-sol"
    assert "edit_rung" not in response and charged[-1] == "claude-haiku-5-5"


def test_the_claude_lane_never_takes_the_responses_shortcut(rung):
    assert llm.responses_available("gpt-6.1-sol", claude_lane.BASE_URL) is False
    assert llm.responses_available("gpt-6.1-sol", "https://api.openai.com/v1") is True


def test_an_unreachable_fallback_sends_the_call_back_to_the_rung(rung):
    # EDIT-CORE gate, 17 Nov 2026 (p5873 job 56): a 502 on the rung moved the turn to Haiku, Haiku
    # could not be reached (APIConnectionError) and the turn failed. The rung is the lane that works.
    class APIConnectionError(Exception):
        pass

    class _Down:
        bodies = []
        messages = None

        def __init__(self):
            self.messages = self

        def with_options(self, **kw):
            return self

        def stream(self, **body):
            self.bodies.append(body)
            raise APIConnectionError("Connection error.")
    rung.replies.extend([_Resp(502, "schema"), _Resp(502, "schema"), _ok("rung answered")])
    rung.use(_Down())
    resp = claude_lane.create("gpt-6.1-sol", MSGS, tools=TOOLS)
    assert resp.choices[0].message.content == "rung answered"
    assert resp.usage.answered_model == "gpt-6.1-sol"
    assert len(rung.posts) == 3
    # the turn is not left marked on the lane that cannot be reached
    assert not edit_rung.turn_fell_back()
    rung.replies.append(_ok("next call on the rung"))
    assert claude_lane.create("gpt-6.1-sol", MSGS, tools=TOOLS).choices[0].message.content == \
        "next call on the rung"


def test_a_fallback_refusal_that_is_not_a_lost_connection_stands(rung):
    rung.replies.extend([_Resp(500, "x"), _Resp(500, "x")])

    class _Bad:
        def __init__(self):
            self.messages = self

        def with_options(self, **kw):
            return self

        def stream(self, **body):
            raise ValueError("bad request shape")
    rung.use(_Bad())
    with pytest.raises(ValueError):
        claude_lane.create("gpt-6.1-sol", MSGS, tools=TOOLS)
    assert len(rung.posts) == 2


def test_sol_62_wire_prefix_grows_across_steps_and_new_job_recorders(monkeypatch):
    from copy import deepcopy
    import json
    model = "gpt-6.2-sol"
    core = [{"type": "function", "function": {"name": "look", "parameters": {"type": "object"}}}]
    extra = {"type": "function", "function": {"name": "grade", "parameters": {"type": "object"}}}
    messages = [{"role": "system", "content": "editor kernel"},
                {"role": "system", "content": "capabilities"},
                {"role": "user", "content": "first request"}]
    plan = claude_lane.tool_plan(core, core + [extra], "high")
    plan["initial_extra"] = []
    def wire(job, history, tools, effort):
        monkeypatch.setattr(llm, "get_recorder", lambda: SimpleNamespace(
            job_id=job, editor_session_key="edit:17:42"))
        return edit_rung.request_body(model, history, tools, effort=effort, tool_plan=plan)
    a = wire(1, messages, core, "high")
    history = deepcopy(messages) + [
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "look", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "observed subject"}]
    claude_lane.surface_tools(plan, history, [extra] + core)
    b = wire(1, history, [extra] + core, "low")
    c = wire(2, history + [{"role": "assistant", "content": "Done."},
                            {"role": "user", "content": "Follow-up"}], core + [extra], "low")
    for before, after in ((a, b), (b, c)):
        # Compare transmitted JSON bytes, including the position of the tool and effort updates.
        assert json.dumps(after["input"][:len(before["input"])]).encode() == json.dumps(before["input"]).encode()
        for field in ("tools", "model", "reasoning", "prompt_cache_key", "prompt_cache_options"):
            assert before[field] == after[field]
    assert a["prompt_cache_key"] == "valmera:edit:17:42"
    assert [i["type"] for i in b["input"] if i.get("type")][-2:] == ["additional_tools", "configuration_update"]
    assert a["input"][0]["content"][0]["prompt_cache_breakpoint"]["ttl"] == "12h"


def test_sol_reported_reads_and_both_write_lifetimes_survive_llm_record(monkeypatch):
    rows = []
    monkeypatch.setattr(llm, "get_recorder", lambda: lambda *args: rows.append(args))
    u = edit_rung._RespUsage("gpt-6.2-sol", {"input_tokens": 10000, "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": 8000, "cache_write_tokens": 1500,
                                "cache_write_tokens_details": {"ttl_12h_tokens": 1000}}})
    u.prompt_cache = {"key": "valmera:edit:17:42", "cached_tokens": u.cache_read_input_tokens}
    llm.record("agent", {"model": "gpt-6.2-sol"}, {}, u)
    row = rows[0][2]
    assert [row[k] for k in ("cached_in","cache_write_in","cache_write_12h_in","uncached_in")] == [8000,1500,1000,500]
    assert row["prompt_cache"]["cached_tokens"] == 8000
    assert row["provider_cost_usd"] == pytest.approx((8000*.1 + 500*2 + 500*2.5 + 1000*5 + 100*10)/1e6)


def test_sol_trial_keeps_actual_opus_planner(monkeypatch):
    from agent_loop import _trial_planner_step
    monkeypatch.setattr(config, "EDIT_RUNG_MODELS", "gpt-6.2-sol")
    ctx = SimpleNamespace(claude_trial=SimpleNamespace(
        planner_model="claude-opus-5-5", remaining_usd=5, planner_effort="xhigh", executor_effort="high"))
    assert _trial_planner_step(ctx, "gpt-6.2-sol", 0, "medium")[0] == "claude-opus-5-5"
    assert _trial_planner_step(ctx, "gpt-6.2-sol", 1, "medium")[0] == "gpt-6.2-sol"


def test_sol_new_job_rebuilds_changed_project_state_behind_real_previous_wire(monkeypatch):
    import json
    import edit_session_history as sessions
    model = "gpt-6.2-sol"
    key = "edit:17:99"
    monkeypatch.setattr(llm, "get_recorder", lambda: SimpleNamespace(editor_session_key=key))
    spec = [{"type": "function", "function": {"name": "look", "parameters": {"type": "object"}}}]
    def plan():
        return dict(claude_lane.tool_plan(spec, spec, "high"), initial_extra=[], static_messages=2)
    base = [{"role": "system", "content": "kernel"}, {"role": "system", "content": "capabilities"}]
    first = edit_rung.request_body(model, base + [
        {"role": "system", "content": "version one"},
        {"role": "user", "content": "trim"},
        {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "look", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "trim inspected"}], spec, tool_plan=plan())
    sessions.remember(key, 18, first, 2400)
    next_plan = plan()
    next_plan.update(previous_body=sessions.previous(key, 18, 5000), skip_chat_until=3)
    second = edit_rung.request_body(model, base + [
        {"role": "user", "content": "trim"}, {"role": "assistant", "content": "Trimmed."},
        {"role": "system", "content": "version two"}, {"role": "user", "content": "add captions"}],
        spec, tool_plan=next_plan)
    assert json.dumps(second["input"][:len(first["input"])]).encode() == json.dumps(first["input"]).encode()
    assert [m["content"] for m in second["input"][len(first["input"]):]] == ["Trimmed.", "version two", "add captions"]
    assert sessions.previous("different session", 18, 5000) is None
    assert sessions.previous(key, 17, 5000) is None
    assert sessions.previous(key, 18, 50) is None
