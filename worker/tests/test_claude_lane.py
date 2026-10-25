"""The Claude trial lane's adapter (worker/claude_lane.py).

The loop speaks OpenAI chat shapes; this module must translate them to the
Messages API losslessly, cache at the stability boundaries, translate the
answer back, meter its exact cost, and fail CLOSED on any billing or key
problem (never the paid OpenAI wallet)."""

import json
from types import SimpleNamespace

import pytest

import claude_lane
import claude_trial
import config
import llm
import model_prices


TOOLS = [{"type": "function", "function": {
    "name": "keep_segments", "description": "Keep these ranges.",
    "parameters": {"type": "object", "properties": {
        "segments": {"type": "array"}}}}}]


def _png_url():
    return "data:image/jpeg;base64,QUJD"


def test_leading_systems_become_the_cached_system_prompt():
    system, msgs, tools = claude_lane.to_anthropic([
        {"role": "system", "content": "kernel"},
        {"role": "system", "content": "capabilities"},
        {"role": "system", "content": "project"},
        {"role": "user", "content": "cut it"},
    ], TOOLS, "claude-haiku-5-5")
    assert [b["text"] for b in system] == ["kernel", "capabilities", "project"]
    assert msgs == [{"role": "user", "content": [
        {"type": "text", "text": "cut it"}]}]
    assert tools[0]["name"] == "keep_segments"
    assert tools[0]["input_schema"]["type"] == "object"


def test_tool_calls_and_results_pair_up():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "system", "content": "k"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "keep_segments", "arguments": '{"segments": [[0, 4]]}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "OK v2"},
        {"role": "user", "content": [
            {"type": "text", "text": "frame"},
            {"type": "image_url", "image_url": {"url": _png_url()}}]},
    ], TOOLS, "claude-haiku-5-5")
    assert msgs[1]["role"] == "assistant"
    use = msgs[1]["content"][0]
    assert use == {"type": "tool_use", "id": "c1", "name": "keep_segments",
                   "input": {"segments": [[0, 4]]}}
    turn = msgs[2]["content"]
    assert turn[0] == {"type": "tool_result", "tool_use_id": "c1",
                       "content": "OK v2"}
    assert turn[1] == {"type": "text", "text": "frame"}
    assert turn[2]["type"] == "image"
    assert turn[2]["source"] == {"type": "base64", "media_type": "image/jpeg",
                                 "data": "QUJD"}


def test_orphans_are_repaired_never_sent():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "user", "content": "go"},
        # compaction removed the call but left its result
        {"role": "tool", "tool_call_id": "gone", "content": "old result"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "type": "function",
             "function": {"name": "keep_segments", "arguments": "{}"}}]},
        # and this call never got a result
    ], TOOLS, "claude-haiku-5-5")
    flat = json.dumps(msgs)
    assert '"tool_use_id": "gone"' not in flat
    assert "Earlier tool result: old result" in flat
    last = msgs[-1]
    assert last["role"] == "user"
    assert last["content"][0]["tool_use_id"] == "c2"
    assert last["content"][0]["is_error"] is True


def test_mid_conversation_system_notes_are_placed_legally():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "system", "content": "kernel"},
        {"role": "user", "content": "filmstrip"},
        {"role": "system", "content": "state block"},   # followed by a user
        {"role": "user", "content": "request"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "keep_segments", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "OK"},
        {"role": "system", "content": "finishing status"},  # last: legal
    ], TOOLS, "claude-haiku-5-5")
    # the illegal one became a marked text block inside the user turn
    first = msgs[0]["content"]
    assert any(b.get("text", "").endswith("state block") and
               b["text"].startswith(claude_lane._SYSTEM_NOTE) for b in first)
    assert msgs[-1] == {"role": "system", "content": "finishing status"}
    # roles strictly alternate between user and assistant before it
    roles = [m["role"] for m in msgs[:-1]]
    assert all(a != b for a, b in zip(roles, roles[1:]))


def test_models_without_mid_system_support_get_text_notes():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "user", "content": "x"},
        {"role": "system", "content": "note"},
    ], TOOLS, "claude-sonnet-5")
    assert all(m["role"] != "system" for m in msgs)


def test_conversation_must_start_with_user_and_not_end_on_assistant():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "assistant", "content": "earlier reply"},
    ], TOOLS, "claude-haiku-5-5")
    assert msgs[0]["role"] == "user"
    assert msgs[-1]["role"] == "user"


def test_cache_breakpoints_sit_on_stability_boundaries():
    system, msgs, tools = claude_lane.to_anthropic([
        {"role": "system", "content": "kernel"},
        {"role": "system", "content": "capabilities"},
        {"role": "system", "content": "trial note"},
        {"role": "system", "content": "project"},
        {"role": "user", "content": "go"},
    ], TOOLS * 2, "claude-haiku-5-5")
    claude_lane._mark_cache(system, msgs, tools)
    marks = (sum("cache_control" in t for t in tools)
             + sum("cache_control" in b for b in system)
             + sum("cache_control" in b for m in msgs for b in m["content"]))
    assert marks <= 4
    assert "cache_control" in tools[-1] and "cache_control" not in tools[0]
    assert "cache_control" in system[1] and "cache_control" in system[-1]
    assert "cache_control" in msgs[-1]["content"][-1]


def _message(blocks, stop="tool_use", **usage):
    u = dict(input_tokens=20, output_tokens=50, cache_read_input_tokens=1000,
             cache_creation_input_tokens=100)
    u.update(usage)
    return SimpleNamespace(content=blocks, stop_reason=stop, stop_details=None,
                           usage=SimpleNamespace(**u), _request_id="req_1")


def test_answers_come_back_in_chat_shape_with_exact_cost():
    msg = _message([
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="Cutting."),
        SimpleNamespace(type="tool_use", id="t1", name="keep_segments",
                        input={"segments": [[1, 2]]})])
    out = claude_lane.from_anthropic(msg, "claude-haiku-5-5")
    choice = out.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.content == "Cutting."
    call = choice.message.tool_calls[0]
    assert (call.id, call.function.name) == ("t1", "keep_segments")
    assert json.loads(call.function.arguments) == {"segments": [[1, 2]]}
    u = out.usage
    assert u.prompt_tokens == 1120 and u.completion_tokens == 50
    assert llm.cached_input_tokens(u) == 1000
    expected = (20 * 0.10 + 1000 * 0.01 + 100 * 0.125 + 50 * 0.50) / 1e6
    assert u.provider_cost_usd == pytest.approx(expected)
    assert llm.provider_cost_usd(u, "claude-haiku-5-5") == pytest.approx(expected)


def test_haiku_long_prompt_rate_card_applies_to_the_whole_request():
    short = model_prices.claude_usage_cost("claude-haiku-5-5", 1000, 100,
                                           90000, 0)
    long = model_prices.claude_usage_cost("claude-haiku-5-5", 1000, 100,
                                          100000, 0)
    assert short == pytest.approx((1000 * .1 + 90000 * .01 + 100 * .5) / 1e6)
    assert long == pytest.approx((1000 * .5 + 100000 * .05 + 100 * 2.5) / 1e6)
    # an unknown claude model never prices at zero
    assert model_prices.claude_usage_cost("claude-new-9", 1000, 0) > 0


def test_stop_reasons_and_refusals_map_to_the_loop_vocabulary():
    out = claude_lane.from_anthropic(_message(
        [SimpleNamespace(type="text", text="x")], stop="max_tokens"),
        "claude-haiku-5-5")
    assert out.choices[0].finish_reason == "length"
    refused = _message([], stop="refusal")
    refused.stop_details = SimpleNamespace(explanation="declined")
    out = claude_lane.from_anthropic(refused, "claude-haiku-5-5")
    assert out.choices[0].message.refusal == "declined"
    assert out.choices[0].message.content == "declined"
    assert not llm.empty_agent_response(out.choices[0].message,
                                        out.choices[0].finish_reason)


class _Stream:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.result


class _FakeSdk:
    def __init__(self, results):
        self.results = list(results)
        self.bodies = []
        self.messages = self

    def with_options(self, **kw):
        return self

    def stream(self, **body):
        self.bodies.append(body)
        return _Stream(self.results.pop(0))


class _StatusError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code = status


@pytest.fixture
def lane(monkeypatch):
    claude_lane.reopen()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(claude_lane, "anthropic", object())
    metered, refused = [], []
    monkeypatch.setattr(claude_trial, "meter",
                        lambda model, usd, kind="llm": metered.append(usd))
    monkeypatch.setattr(claude_trial, "provider_refused",
                        lambda kind, exc: refused.append(kind))
    monkeypatch.setattr(claude_lane.time, "sleep", lambda s: None)
    yield SimpleNamespace(metered=metered, refused=refused,
                          use=lambda sdk: monkeypatch.setattr(
                              claude_lane, "_sdk", lambda: sdk))
    claude_lane.reopen()


def test_the_shim_is_a_drop_in_client_and_meters_each_call(lane):
    sdk = _FakeSdk([_message([SimpleNamespace(type="text", text="done")],
                             stop="end_turn")])
    lane.use(sdk)
    client = llm.without_sdk_retries(claude_lane.ClaudeLaneClient())
    resp = client.chat.completions.create(
        model="claude-haiku-5-5",
        messages=[{"role": "system", "content": "k"},
                  {"role": "user", "content": "hi"}],
        tools=TOOLS, max_completion_tokens=4000, temperature=0.2,
        reasoning_effort="low", service_tier="priority")
    assert resp.choices[0].message.content == "done"
    body = sdk.bodies[0]
    assert body["max_tokens"] == 4000
    assert body["output_config"] == {"effort": "low"}
    assert "temperature" not in body and "service_tier" not in body
    assert len(lane.metered) == 1 and lane.metered[0] > 0


def test_billing_refusal_closes_the_lane_and_alerts(lane):
    sdk = _FakeSdk([_StatusError(400, "Your credit balance is too low to "
                                      "access the Anthropic API.")])
    lane.use(sdk)
    with pytest.raises(claude_lane.ClaudeLaneUnavailable) as err:
        claude_lane.create("claude-haiku-5-5",
                           [{"role": "user", "content": "x"}])
    assert lane.refused == ["billing"]
    assert not claude_lane.available()
    assert llm.is_quota_error(err.value)        # the loop treats it as a wallet
    # closed: the next call does not even reach Anthropic
    with pytest.raises(claude_lane.ClaudeLaneUnavailable):
        claude_lane.create("claude-haiku-5-5",
                           [{"role": "user", "content": "x"}])
    assert len(sdk.bodies) == 1


def test_auth_refusal_also_fails_closed(lane):
    lane.use(_FakeSdk([_StatusError(401, "invalid x-api-key")]))
    with pytest.raises(claude_lane.ClaudeLaneUnavailable):
        claude_lane.create("claude-haiku-5-5",
                           [{"role": "user", "content": "x"}])
    assert lane.refused == ["auth"]


def test_overloaded_is_retried_but_rate_limits_reach_the_loop(lane):
    ok = _message([SimpleNamespace(type="text", text="ok")], stop="end_turn")
    sdk = _FakeSdk([_StatusError(529, "overloaded"), ok])
    lane.use(sdk)
    assert claude_lane.create("claude-haiku-5-5", [
        {"role": "user", "content": "x"}]).choices[0].message.content == "ok"
    limited = _StatusError(429, "rate_limit_error")
    lane.use(_FakeSdk([limited]))
    with pytest.raises(_StatusError) as err:
        claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}])
    assert llm.is_rate_limit_error(err.value)


def test_no_key_means_closed_never_another_wallet(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert not claude_lane.configured()
    with pytest.raises(claude_lane.ClaudeLaneUnavailable):
        claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}])


def test_forced_tool_choice_degrades_on_models_that_reject_it():
    assert claude_lane._tool_choice("required", "claude-sonnet-5-5") is None
    assert claude_lane._tool_choice("required", "claude-haiku-5-5") == {"type": "any"}
    assert claude_lane._tool_choice("none", "claude-sonnet-5-5") == {"type": "none"}


def test_lane_dict_carries_no_key():
    lane = claude_lane.lane("claude-haiku-5-5")
    assert lane["provider"] == "anthropic"
    assert lane["api_key"] == "anthropic"
    assert not llm.responses_available(lane["model"], lane["base_url"],
                                       effort="high")
