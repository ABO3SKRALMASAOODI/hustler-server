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
    assert body["prompt_cache_key"] == "valmera-edit-77"
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
