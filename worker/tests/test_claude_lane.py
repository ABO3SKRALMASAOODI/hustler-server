"""The free-trial Claude lane (owner decision D2, 25 Oct 2026).

The agent loop speaks OpenAI chat.completions; claude_lane translates to the
Anthropic Messages API and back. These pin the translation rules that would
otherwise 400 every trial turn: tool pairing, the first/last turn, no
sampling parameters, thinking off, mid-conversation system notes.
"""
import json
from types import SimpleNamespace as NS

import claude_lane
import config
import llm


def _history():
    return [
        {"role": "system", "content": "You are the editor."},
        {"role": "user", "content": "Cut the pauses."},
        {"role": "assistant", "content": "On it.", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "cut_silences", "arguments": "{}"}},
            {"id": "c2", "type": "function",
             "function": {"name": "get_edl", "arguments": "{\"x\": 1}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "EDL v2 cut 4 pauses"},
        {"role": "tool", "tool_call_id": "c2", "content": "EDL v2: ..."},
        {"role": "system", "content": "Render before replying."},
        {"role": "user", "content": [
            {"type": "text", "text": "Frames:"},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpg;base64,QUJD"}}]},
    ]


def test_history_becomes_valid_alternating_turns():
    system, messages = claude_lane.convert_messages(_history())
    assert system == "You are the editor."
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assistant = messages[1]["content"]
    assert assistant[0] == {"type": "text", "text": "On it."}
    assert [b["id"] for b in assistant[1:]] == ["c1", "c2"]
    assert assistant[2]["input"] == {"x": 1}
    after = messages[2]["content"]
    # tool results first, then the system note and the user's frames
    assert [b["type"] for b in after[:2]] == ["tool_result", "tool_result"]
    assert after[2]["text"].startswith("[Valmera system note] Render")
    assert after[-1] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}


def test_a_closing_operator_note_stays_a_system_message():
    _system, out = claude_lane.convert_messages(
        _history()[:5] + [{"role": "system", "content": "Reply now."}])
    assert out[-1] == {"role": "system", "content": "Reply now."}
    assert out[-2]["role"] == "user"
    assert all("_system_text" not in b for m in out[:-1] for b in m["content"])


def test_unanswered_and_orphaned_tool_calls_are_repaired():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "look_at", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "zzz", "content": "stale"},
        {"role": "assistant", "content": "done"},
    ]
    _system, out = claude_lane.convert_messages(messages)
    first_reply = out[2]["content"]
    assert first_reply[0] == {"type": "tool_result", "tool_use_id": "a",
                              "content": "(no result was recorded for this call)"}
    assert first_reply[1]["text"] == "[earlier tool result] stale"
    # never ends on an assistant turn (no prefill)
    assert out[-1]["role"] == "user"


def test_request_has_no_sampling_and_no_thinking():
    request = claude_lane.build_request(
        "claude-haiku-5-5", _history(),
        tools=[{"type": "function", "function": {
            "name": "cut_silences", "description": "trim",
            "parameters": {"type": "object", "properties": {}}}}],
        max_tokens=8000, temperature=0.4, reasoning_effort="max",
        service_tier="priority", extra_headers={"x": "y"})
    assert "temperature" not in request and "service_tier" not in request
    assert "extra_headers" not in request
    assert request["thinking"] == {"type": "disabled"}
    assert request["output_config"] == {"effort": "high"}
    assert request["tools"][0]["input_schema"]["type"] == "object"
    assert request["tool_choice"] == {"type": "auto"}
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["max_tokens"] == 8000
    no_tools = claude_lane.build_request("claude-haiku-5-5", _history())
    assert "tools" not in no_tools and "tool_choice" not in no_tools


def _reply(stop="tool_use"):
    return NS(id="msg_1", model="claude-haiku-5-5", stop_reason=stop,
              stop_details=None,
              content=[NS(type="text", text="Cutting now."),
                       NS(type="tool_use", id="t9", name="cut_silences",
                          input={"min_silence_s": 0.5})],
              usage=NS(input_tokens=1000, cache_read_input_tokens=9000,
                       cache_creation_input_tokens=500, output_tokens=200))


def test_response_maps_to_the_chat_shape_and_prices_exactly():
    out = claude_lane.convert_response(_reply())
    choice = out.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.content == "Cutting now."
    call = choice.message.tool_calls[0]
    assert call.id == "t9" and call.function.name == "cut_silences"
    assert json.loads(call.function.arguments) == {"min_silence_s": 0.5}
    assert out.usage.prompt_tokens == 10500
    assert out.usage.prompt_tokens_details["cached_tokens"] == 9000
    cost = claude_lane.usage_cost_usd(out.usage, "claude-haiku-5-5")
    expected = (1000 * 0.10 + 9000 * 0.01 + 500 * 0.125 + 200 * 0.50) / 1e6
    assert abs(cost - expected) < 1e-12
    assert llm.provider_cost_usd(out.usage, "claude-haiku-5-5") == cost
    refused = claude_lane.convert_response(_reply("refusal"))
    assert refused.choices[0].finish_reason == "content_filter"
    assert refused.choices[0].message.refusal


def test_long_prompts_use_the_higher_rate_card():
    usage = NS(input_tokens=120000, cache_read_input_tokens=0,
               cache_creation_input_tokens=0, output_tokens=0)
    assert abs(claude_lane.usage_cost_usd(usage, "claude-haiku-5-5")
               - 120000 * 0.10 * 5 / 1e6) < 1e-12


def test_client_has_the_chat_completions_shape(monkeypatch):
    seen = {}

    class Messages:
        def create(self, **kwargs):
            seen.update(kwargs)
            return _reply("end_turn")

    def factory(max_retries):
        seen["max_retries"] = max_retries
        return NS(messages=Messages())

    client = claude_lane.ClaudeChatClient(sdk_factory=factory)
    quiet = llm.without_sdk_retries(client)
    resp = quiet.chat.completions.create(
        model="claude-haiku-5-5", messages=_history(), tools=[],
        max_tokens=900, temperature=0.2)
    assert seen["max_retries"] == 0
    assert seen["model"] == "claude-haiku-5-5" and seen["max_tokens"] == 900
    assert "temperature" not in seen
    assert resp.choices[0].finish_reason == "stop"


def test_trial_turns_look_through_the_claude_lane(monkeypatch):
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    vclient, vmodel = llm.vision_client_for(config.TRIAL_PLAN)
    assert isinstance(vclient, claude_lane.ClaudeChatClient)
    assert vmodel == config.TRIAL_AGENT_MODEL


def test_lane_is_off_without_a_key(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert claude_lane.available() is False
    assert claude_lane.trial_lane() is None
