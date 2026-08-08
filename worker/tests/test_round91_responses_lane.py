"""Round 91: the agent gets its thinking back on the same model.

gpt-5.6-luna refuses function tools and reasoning_effort together on
/v1/chat/completions and names the endpoint that allows both. The loop had
correctly chosen tools, which meant 875 consecutive agent calls with ZERO
reasoning tokens from Jul 31 2026 — the model it replaced was spending 335-621
per call. This pins the translation both ways and, most importantly, that the
lane can only ever ADD thinking: every failure in it falls back to the
chat/completions call that runs today.

Pure logic — no network. Run from worker/:
    python tests/test_round91_responses_lane.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                # noqa: E402
import llm                                                   # noqa: E402

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


print("== the lane opens WITHOUT waiting for the model to fail first ==")

MODEL = "gpt-5.6-luna"
llm._responses_dead.discard(MODEL)

# THE BUG THIS PINS. The gate first required tools_need_effort_none(model) —
# i.e. the model must ALREADY have been refused on chat/completions in THIS
# process. That latch starts empty on every boot, so a fresh worker's first
# agent call found it unset, took the chat path, ate the 400, latched, retried
# with effort='none' and answered without thinking. Job 2602 (Aug 5 2026,
# 20:55, eleven minutes after the deploy) was a ONE-CALL turn, so that was the
# entire turn: 0 reasoning tokens, and the fix that had just shipped never ran.
llm._tools_effort_none.discard(MODEL)
check("OPEN on a fresh process, before any failure has been seen",
      llm.responses_available(MODEL, "https://api.openai.com/v1") is True)
llm.mark_tools_need_effort_none(MODEL)
check("...and still open once the model has refused",
      llm.responses_available(MODEL, "https://api.openai.com/v1") is True)

check("closed against a provider that does not serve /v1/responses (xAI)",
      llm.responses_available(MODEL, "https://api.x.ai/v1") is False)
_lane = config.AGENT_RESPONSES_LANE
config.AGENT_RESPONSES_LANE = False
check("closed when switched off from the environment",
      llm.responses_available(MODEL, "https://api.openai.com/v1") is False)
config.AGENT_RESPONSES_LANE = _lane
_eff = config.AGENT_REASONING_EFFORT
config.AGENT_REASONING_EFFORT = ""
check("closed when there is no reasoning to ask for anyway",
      llm.responses_available(MODEL, "https://api.openai.com/v1") is False)
config.AGENT_REASONING_EFFORT = _eff

print("== a doomed lane is paid once per process, a blip is not ==")

llm.mark_responses_dead(MODEL)
check("a model latched dead stops being tried",
      llm.responses_available(MODEL, "https://api.openai.com/v1") is False)
llm._responses_dead.discard(MODEL)
check("a 404 counts as 'not here' and latches",
      llm.looks_like_responses_unsupported(
          RuntimeError("responses HTTP 404: no such endpoint")) is True)
check("so does the model refusing the request shape",
      llm.looks_like_responses_unsupported(
          RuntimeError("responses HTTP 400: invalid_request_error")) is True)
check("a TIMEOUT does not — that must not cost thinking for the whole process",
      llm.looks_like_responses_unsupported(
          RuntimeError("Read timed out after 120s")) is False)
check("nor does a 500",
      llm.looks_like_responses_unsupported(
          RuntimeError("responses HTTP 503: upstream unavailable")) is False)

print("== chat messages translate into Responses input ==")

msgs = [
    {"role": "system", "content": "You are an editor."},
    {"role": "user", "content": "cut the dead air"},
    {"role": "assistant", "content": "on it",
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "cut_silences",
                                  "arguments": '{"pad":0.1}'}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "EDL v1 -> v2"},
    {"role": "user", "content": [
        {"type": "text", "text": "[frame @2.0s]"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA"}},
    ]},
]
inp = llm._to_responses_input(msgs)
kinds = [i.get("type") or i.get("role") for i in inp]
check("every message maps to something",
      kinds == ["system", "user", "assistant", "function_call",
                "function_call_output", "user"])
check("a tool RESULT carries the call_id the model can match",
      inp[4]["call_id"] == "call_1" and inp[4]["output"] == "EDL v1 -> v2")
check("a tool CALL keeps its name and raw arguments",
      inp[3]["name"] == "cut_silences"
      and json.loads(inp[3]["arguments"])["pad"] == 0.1)
check("assistant text becomes output_text, user text input_text",
      inp[2]["content"][0]["type"] == "output_text"
      and inp[1]["content"][0]["type"] == "input_text")
check("a look's frame becomes input_image (the agent keeps its eyes)",
      inp[5]["content"][1]["type"] == "input_image"
      and inp[5]["content"][1]["image_url"].startswith("data:image/jpeg"))

tools = [{"type": "function",
          "function": {"name": "add_text", "description": "Burn text.",
                       "parameters": {"type": "object", "properties": {}}}}]
rt = llm._to_responses_tools(tools)
check("tool schemas flatten one level", rt[0]["name"] == "add_text"
      and rt[0]["type"] == "function" and "function" not in rt[0])

print("== Responses output reads back as a chat completion ==")

payload = {
    "status": "completed",
    "output": [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "call_id": "call_9", "name": "add_zoom",
         "arguments": '{"start":3.0}'},
    ],
    "usage": {"input_tokens": 4000, "output_tokens": 812,
              "output_tokens_details": {"reasoning_tokens": 700},
              "input_tokens_details": {"cached_tokens": 3584}},
}
r = llm._from_responses(payload)
msg = r.choices[0].message
check("a tool call survives the round trip",
      msg.tool_calls[0].function.name == "add_zoom"
      and msg.tool_calls[0].id == "call_9")
check("...with its arguments intact",
      json.loads(msg.tool_calls[0].function.arguments)["start"] == 3.0)
check("finish_reason is tool_calls", r.choices[0].finish_reason == "tool_calls")
check("REASONING TOKENS ARE REPORTED — the whole point",
      llm.reasoning_tokens(r.usage) == 700)
check("...and the cache discount still applies",
      llm.cached_input_tokens(r.usage) == 3584)
check("prompt/completion counts map to the billing names",
      (r.usage.prompt_tokens, r.usage.completion_tokens) == (4000, 812))

text = llm._from_responses({
    "status": "completed",
    "output": [{"type": "message",
                "content": [{"type": "output_text", "text": "It's 32s now."}]}],
    "usage": {}})
check("a plain text reply comes back as content",
      text.choices[0].message.content == "It's 32s now."
      and text.choices[0].message.tool_calls is None)

check("a truncated response reports finish_reason='length' (the loop retries)",
      llm._from_responses({
          "status": "incomplete",
          "output": [{"type": "message",
                      "content": [{"type": "output_text", "text": "x"}]}],
          "usage": {}}).choices[0].finish_reason == "length")

print("== anything unrecognised RAISES, so the caller falls back ==")


def raises(fn, *a):
    try:
        fn(*a)
    except Exception:
        return True
    return False


check("a body with no output raises", raises(llm._from_responses, {"x": 1}))
check("an empty output raises (never a silent no-op step)",
      raises(llm._from_responses, {"output": [], "usage": {}}))
check("reasoning ALONE with no message or call raises",
      raises(llm._from_responses,
             {"output": [{"type": "reasoning", "id": "r"}], "usage": {}}))

# ROUND 97: unless the payload says WHY it is empty — status 'incomplete'
# means reasoning ate the whole max_output_tokens budget. That must come back
# as a 'length' completion (the loop's doubling retry handles it in-lane),
# never as a raise, because the raise reruns the step on chat/completions at
# effort='none' — the lane's whole point, lost on the steps thinking hardest.
_inc = llm._from_responses(
    {"output": [{"type": "reasoning", "id": "r"}], "status": "incomplete",
     "usage": {"output_tokens": 8000,
               "output_tokens_details": {"reasoning_tokens": 8000}}})
check("an INCOMPLETE empty payload is a 'length' completion, not a raise",
      _inc.choices[0].finish_reason == "length"
      and _inc.choices[0].message.content is None
      and _inc.choices[0].message.tool_calls is None)
check("...whose usage still carries the reasoning spend",
      _inc.usage.reasoning_tokens == 8000)
check("a 400 about the effort VALUE does not latch the lane dead",
      llm.looks_like_responses_unsupported(
          RuntimeError("responses HTTP 400: Invalid value for "
                       "'reasoning.effort': 'xmax'")) is False)
check("an unmapped content part raises rather than being dropped",
      raises(llm._to_responses_input,
             [{"role": "user", "content": [{"type": "video_url"}]}]))
check("an image part with no url raises",
      raises(llm._to_responses_input,
             [{"role": "user",
               "content": [{"type": "image_url", "image_url": {}}]}]))
check("a tool with no name raises", raises(llm._to_responses_tools,
                                           [{"type": "function",
                                             "function": {}}]))

print("== the loop cannot lose a turn to this lane ==")

import inspect                                               # noqa: E402
import agent_loop                                            # noqa: E402
src = inspect.getsource(agent_loop._run_loop)
check("the responses call is wrapped in try/except",
      "llm.responses_create(" in src and "resp = None" in src)
check("...and the chat/completions call still runs when resp is None",
      "while resp is None:" in src)
check("the fallback is announced once per turn, not per step",
      "_responses_warned" in src)



print("== the effort, and telling the truth about it ==")

_src = inspect.getsource(agent_loop._run_loop)

check("reasoning effort is MAX (gpt-5.6: none|low|medium|high|xhigh|max)",
      config.AGENT_REASONING_EFFORT == "max")
# Round 100: the lane sends TIERED effort — the configured value on the
# planning step (iteration 0), the dispatch value after. A dispatch step at
# 'max' thinks for minutes about "call the next tool" (job 3211: 49k
# reasoning tokens, 14 minutes, another user starved behind it).
check("...and the tiered step effort is what the lane sends",
      "effort=step_effort" in _src)
check("...planning keeps the configured effort",
      "config.AGENT_REASONING_EFFORT if iteration == 0" in _src)
check("...dispatch runs at the dispatch effort, which is set and lighter",
      config.AGENT_REASONING_EFFORT_DISPATCH in ("low", "medium"))
check("the lane call gets the thinking-sized timeout, not the 90s dispatch "
      "one", "timeout=config.AGENT_LANE_TIMEOUT_S" in _src)
check("...which exists, sits above LLM_TIMEOUT_S and under the turn ceiling",
      config.LLM_TIMEOUT_S < config.AGENT_LANE_TIMEOUT_S
      < config.AGENT_TURN_TIMEOUT_S)
check("the recorded row says WHICH api answered",
      '"api": "responses"' in _src and '"api": "chat.completions"' in _src)
check("...and records the effort the lane actually sent, not the chat value",
      "used_lane" in _src and '"reasoning_effort": used_lane' in _src)

print(f"\n{PASS} checks passed")
