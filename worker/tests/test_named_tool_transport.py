"""Exercise named decisions at the HTTP boundary, not a mocked LLM wrapper."""
import copy
import json
from types import SimpleNamespace

import pytest

import llm


@pytest.mark.parametrize("name", [
    "compile_request", "record_request_contract", "plan_continuous_take",
])
@pytest.mark.parametrize("shape", ["chat", "responses"])
def test_named_decision_reaches_responses_and_returns_tool_result(monkeypatch, name, shape):
    choice = ({"type": "function", "function": {"name": name}} if shape == "chat"
              else {"type": "function", "name": name})
    original = copy.deepcopy(choice)
    calls = []

    def post(url, **kwargs):
        body = kwargs["json"]
        assert url.endswith("/responses")
        assert body["tool_choice"] == {"type": "function", "name": name}
        assert body["tools"][0]["name"] == name
        assert body["tools"][0]["strict"] is True
        calls.append(body)
        payload = {"status": "completed", "output": [{
            "type": "function_call", "call_id": "decision-1", "name": name,
            "arguments": '{"ok":true}',
        }], "usage": {"input_tokens": 100, "output_tokens": 20}}
        return SimpleNamespace(status_code=200, headers={}, json=lambda: payload)

    monkeypatch.setattr(llm.requests, "post", post)
    client = SimpleNamespace(base_url="https://api.openai.com/v1", api_key="unit-test")
    result = llm.create_with_dialect(
        client, "gpt-6.1-sol", [{"role": "user", "content": "Record the decision."}],
        tools=[{"type": "function", "function": {
            "name": name, "strict": True, "parameters": {"type": "object", "properties": {
                "ok": {"type": "boolean"}}, "required": ["ok"],
                "additionalProperties": False}}}],
        tool_choice=choice, max_tokens=100)
    assert len(calls) == 1
    assert choice == original
    call = result.choices[0].message.tool_calls[0]
    assert call.function.name == name
    assert json.loads(call.function.arguments) == {"ok": True}


@pytest.mark.parametrize("choice", ["none", "auto", "required", {"type": "web_search_preview"}])
def test_existing_tool_selection_modes_are_preserved(monkeypatch, choice):
    bodies = []
    payload = {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": "OK"}]}], "usage": {}}
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw:
        bodies.append(kw["json"]) or SimpleNamespace(
            status_code=200, headers={}, json=lambda: payload))
    llm.responses_create("https://api.openai.com/v1", "unit-test", "gpt-6.1-sol",
                         [{"role": "user", "content": "Check"}], [], tool_choice=choice)
    assert bodies[0]["tool_choice"] == choice


@pytest.mark.parametrize("choice", [
    {"type": "function"},
    {"type": "function", "function": {"name": ""}},
    {"type": "function", "name": "wrong", "function": {"name": "intended"}},
])
def test_invalid_named_selection_fails_before_network(monkeypatch, choice):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: pytest.fail("Network called"))
    with pytest.raises(ValueError):
        llm.responses_create("https://api.openai.com/v1", "unit-test", "gpt-6.1-sol",
                             [], [], tool_choice=choice)
