"""Contract reasoning, provider empties and malformed tool calls stay recoverable."""
from types import SimpleNamespace as NS
import pytest
import config
import llm
import agent_tools
import model_prices


def test_advanced_high_on_every_step_legacy_settings_unchanged(monkeypatch):
    monkeypatch.setattr(config, "AGENT_REASONING_EFFORT", "max")
    monkeypatch.setattr(config, "AGENT_REASONING_EFFORT_DISPATCH", "medium")
    for step in (0, 1, 20):
        assert llm.editor_reasoning_effort("advanced", step) == "high"
    for plan in ("ai", "ai_pro", "ai_max", "mcp", "free"):
        assert llm.editor_reasoning_effort(plan, 0) == "max"
        assert llm.editor_reasoning_effort(plan, 1) == "medium"


def test_explicit_reasoning_opens_responses_even_with_empty_legacy_effort(monkeypatch):
    monkeypatch.setattr(config, "AGENT_REASONING_EFFORT", "")
    monkeypatch.setattr(config, "AGENT_RESPONSES_LANE", True)
    monkeypatch.setattr(llm, "_responses_dead", set())
    assert llm.responses_available("gpt-6.1-sol", "https://api.openai.com/v1", effort="high")
    assert not llm.responses_available("legacy", "https://api.openai.com/v1")


@pytest.mark.parametrize("response", [NS(), NS(choices=[]), NS(choices=[NS(message=None)])])
def test_empty_envelope_is_recoverable(response):
    choice = llm.agent_response_choice(response)
    assert llm.empty_agent_response(choice.message, choice.finish_reason)


@pytest.mark.parametrize("finish", [None, "stop", "length"])
def test_empty_reply_is_recoverable_for_every_finish(finish):
    assert llm.empty_agent_response(NS(content=" ", tool_calls=[]), finish)


def test_explicit_refusal_filter_text_and_tool_calls_are_not_empty():
    for message, finish in [(NS(content="", refusal="refused"), "stop"),
                            (NS(content=""), "content_filter"),
                            (NS(content="Done"), "stop"),
                            (NS(content="", tool_calls=[object()]), "tool_calls")]:
        assert not llm.empty_agent_response(message, finish)


@pytest.mark.parametrize("raw", ['[]', 'null', '1', '"text"', '{bad', ''])
def test_invalid_arguments_never_turn_into_default_operation(raw):
    with pytest.raises((ValueError, TypeError)):
        agent_tools.parse_tool_arguments(raw)


def test_valid_tool_arguments_keep_values():
    assert agent_tools.parse_tool_arguments('{"enabled":false}') == {"enabled": False}
    assert agent_tools.parse_tool_arguments('{}') == {}


@pytest.mark.parametrize("args", [[], None, "bad", {"preset": "stacked", "style": 5}])
def test_bad_arguments_return_correction_without_running_tool(monkeypatch, args):
    calls = []
    monkeypatch.setitem(agent_tools.TOOLS, "set_caption_style", (lambda *a, **k: calls.append(k), "", {}))
    ctx = NS()
    result = agent_tools.execute(ctx, "set_caption_style", args)
    assert not calls
    assert ctx.last_structured_tool_outcome["status"] == "correction_needed"
    assert "No operation was run" in result


def test_advanced_allowance_covers_forty_dollars_of_provider_cost():
    assert 8000 * model_prices.USD_PER_CREDIT == 40


def test_completed_empty_responses_stays_on_reasoning_lane():
    response = llm._from_responses({"status": "completed", "output": [], "usage": {"output_tokens": 40}})
    choice = response.choices[0]
    assert llm.empty_agent_response(choice.message, choice.finish_reason)
    assert response.usage.completion_tokens == 40


def test_responses_refusal_is_preserved_without_fallback_or_retry():
    response = llm._from_responses({"status": "completed", "output": [
        {"type": "message", "content": [{"type": "refusal", "refusal": "Cannot assist with that request."}]}]})
    choice = response.choices[0]
    assert choice.message.refusal == "Cannot assist with that request."
    assert choice.message.content == choice.message.refusal
    assert not llm.empty_agent_response(choice.message, choice.finish_reason)
