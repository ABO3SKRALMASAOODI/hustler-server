"""Agent turn latency: the 13 seconds the user spends per round trip.

Measured across 385 completed turns, the per-LLM-call cost is FLAT — 14.1s at
one call, 11.9 at four, 14.4 at ten, 11.8 at forty-nine. Turn duration is
round-trip count times a constant, and it has nothing to do with video size.

Where the constant goes: an average agent call produces 646 output tokens of
which 402 are reasoning, at roughly 50 tokens/second. Input is ~32k and almost
entirely fixed overhead re-sent every iteration (88 tool schemas ~= 17.4k
tokens, the system prompt ~= 15.1k), but DeepSeek serves 31.7k of that from
cache, so it is OUTPUT generation that the user waits on.

That leaves two levers, and this file pins both:
  1. Fewer round trips — the loop has always executed a whole batch of tool
     calls in one iteration, and the prompt now asks for that.
  2. Less reasoning per trip — AGENT_REASONING_EFFORT, which has to be SAFE to
     switch on against a provider we cannot test from here.

    cd worker && python -m pytest tests/test_agent_latency.py -q
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_prompt                                     # noqa: E402
import agent_loop                                       # noqa: E402
import agent_tools                                      # noqa: E402
import llm                                              # noqa: E402


class _Err(Exception):
    pass


# ── the reasoning knob must be safe to turn on ──────────────────────────────

def test_an_unknown_reasoning_effort_is_recognised_as_a_bad_parameter():
    """The shapes an OpenAI-compatible provider uses to say "I don't know that".

    This matters because the alternative is a 400 on EVERY iteration after the
    first, on every turn, for every user — the setting is an env var somebody
    flips on Render against a provider that cannot be tested from here.
    """
    for body in [
        "Error code: 400 - unrecognized request argument supplied: reasoning_effort",
        "400: Unknown parameter: 'reasoning_effort'.",
        "invalid_request_error: extra inputs are not permitted (reasoning_effort)",
        "reasoning_effort is not supported by this model",
    ]:
        assert llm.looks_like_bad_parameter(_Err(body), "reasoning_effort"), body


def test_a_real_failure_is_never_mistaken_for_an_unsupported_parameter():
    """Latching on any 400 would disable the setting because of an unrelated
    bad request, and reading an outage as "unsupported" would hide the outage."""
    for body in [
        "500 Internal Server Error",
        "429 rate limit exceeded",
        "context_length_exceeded: too many tokens",
        "401 invalid api key",
        # Mentions the field but is not a rejection OF the field.
        "reasoning_effort accepted; upstream timeout",
        # A rejection of a DIFFERENT field must not disable this one.
        "unknown parameter: 'temperature'",
    ]:
        assert not llm.looks_like_bad_parameter(_Err(body), "reasoning_effort"), body


def test_the_rejection_latches_per_model_so_it_costs_one_call_not_one_per_step():
    """A turn runs up to dozens of iterations. Re-learning the same refusal on
    each of them would turn a harmless setting into a per-step tax."""
    llm._no_reasoning_effort.clear()
    try:
        assert llm.reasoning_effort_rejected("model-a") is False
        llm.mark_reasoning_effort_rejected("model-a")
        assert llm.reasoning_effort_rejected("model-a") is True
        # Per MODEL: free and paid tiers can run on different providers, and one
        # provider's refusal says nothing about the other's.
        assert llm.reasoning_effort_rejected("model-b") is False
    finally:
        llm._no_reasoning_effort.clear()


# ── fewer round trips ───────────────────────────────────────────────────────

def test_the_prompt_asks_for_independent_tool_calls_in_one_message():
    """The loop has ALWAYS executed a whole batch of tool calls in a single
    iteration — `for tc in msg.tool_calls` — but nothing ever asked the model
    to send more than one. Four independent writes as four round trips is 52
    seconds of the user watching a spinner instead of 13.
    """
    p = agent_prompt.SYSTEM_PROMPT
    assert "SAME message" in p, "the prompt must ask for batched tool calls"
    # And it must say when NOT to batch, or the model will pass a timestamp it
    # has not been given yet — the one rule this whole prompt is built on.
    assert "find_silences before cut_silences" in p
    assert "13 seconds" in p, "the reason should be the measurement, not an assertion"


def test_the_loop_still_dispatches_every_tool_call_in_a_batch():
    """Guards the half that makes batching worth asking for. If a refactor ever
    executed only the first call of a batch, the prompt above would make the
    agent SLOWER and quietly drop work."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "agent_loop.py")).read()
    assert "for tc in msg.tool_calls:" in src, (
        "the loop must iterate the whole batch; executing msg.tool_calls[0] "
        "would silently drop every call after the first")


def test_initial_filmstrip_pixels_are_not_resent_after_planning():
    filmstrip_image = {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,FILMSTRIP"},
    }
    exact_image = {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,EXACT"},
    }
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "FILMSTRIPS & STILLS — fresh"},
            {"type": "text", "text": "[MAIN VIDEO 0-30s]"},
            filmstrip_image,
        ]},
        {"role": "user", "content": [
            {"type": "text", "text": "Frames for your own eyes"},
            exact_image,
        ]},
    ]
    assert agent_loop._compact_initial_filmstrip(messages) is True
    assert all(p["type"] == "text" for p in messages[0]["content"])
    assert any("look_at" in p["text"] for p in messages[0]["content"])
    # Evidence requested after planning is not the broad filmstrip and must
    # remain in the model's context for the next decision.
    assert messages[1]["content"][1] is exact_image
    assert agent_loop._compact_initial_filmstrip(messages) is False


def test_prompt_prefers_one_atomic_recipe_for_multi_move_edits():
    p = agent_prompt.SYSTEM_PROMPT
    assert "apply_edit_recipe" in p
    assert "aborts the entire batch" in p


def test_post_plan_tool_catalog_keeps_capability_but_drops_repeated_handbook():
    full = agent_tools.openai_tools("any-model")
    compact = agent_tools.openai_tools("any-model", compact=True)
    assert [x["function"]["name"] for x in compact] == [
        x["function"]["name"] for x in full]
    assert [x["function"]["parameters"] for x in compact] == [
        x["function"]["parameters"] for x in full]
    full_bytes = len(json.dumps(full, separators=(",", ":")))
    compact_bytes = len(json.dumps(compact, separators=(",", ":")))
    assert compact_bytes < full_bytes * 0.5
