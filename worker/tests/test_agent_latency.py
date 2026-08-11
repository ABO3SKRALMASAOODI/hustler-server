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
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_prompt                                     # noqa: E402
import agent_loop                                       # noqa: E402
import agent_tools                                      # noqa: E402
import llm                                              # noqa: E402
from schemas import default_edl                         # noqa: E402


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
    assert "NOT a reconnaissance round" in p
    assert "NEVER needs a prior `get_words`" in p
    assert "include every exact evidence read" in p


def test_broad_speech_polish_is_finished_in_the_first_candidate():
    p = agent_prompt.SYSTEM_PROMPT
    assert '"polished/professional social clip"' in p
    assert "word-safe filler/dead-pause cleanup" in p
    assert "first-pass social mastering" in p


def test_video_info_exposes_exact_fillers_without_an_extra_read_round():
    index = {
        "video": {"duration": 6.52, "width": 960, "height": 540,
                  "fps": 25.0, "has_audio": True},
        "words": [{"w": "um", "t0": 3.2, "t1": 3.68,
                   "filler": True}],
        "shots": [], "sentences": [], "silences": [], "speakers": 1,
    }
    ctx = SimpleNamespace(
        has_main_video=True, index=index, duration=6.52,
        latest_edl=lambda: {"version": 1, "json": default_edl(6.52)},
    )
    info = agent_tools.get_video_info(ctx)
    assert "'um' @3.2-3.68s" in info
    assert "already has these exact indexed spans" in info
    assert "do NOT call get_words first" in info


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


def test_successful_auto_preview_rubric_is_not_reported_as_render_failure(
        monkeypatch):
    class Ctx:
        versions_written = [2]
        rendered_versions = set()
        autorendered = False
        autorendering = False
        job = {"id": 99}

        def latest_edl(self):
            return {"version": 2, "json": {}}

    monkeypatch.setattr(
        agent_loop.agent_tools, "render_preview",
        lambda _ctx: ("Preview v2 rendered: 6.1s. CHECK: deliberate crop — "
                      "FAILED if the subject is clipped."))
    monkeypatch.setattr(agent_loop, "_activity", lambda *_a, **_k: None)
    _latest, fail_note = agent_loop._auto_render_if_needed(
        Ctx(), object(), 7, {})
    assert fail_note is None


def test_failed_edl_preview_gets_one_new_version_repair_pass():
    class Ctx:
        last_preview_failure = {
            "error": "invalid filtergraph", "agent_repairable": True}

        @staticmethod
        def latest_edl():
            return {"version": 7, "json": {}}

    messages = []
    pushed = agent_loop._preview_repair_pushback(
        Ctx(), messages, agent_loop.time.monotonic(), False)
    assert pushed is True
    assert "immutable EDL v7" in messages[-1]["content"]
    assert "new EDL version" in messages[-1]["content"]
    assert agent_loop._preview_repair_pushback(
        Ctx(), messages, agent_loop.time.monotonic(), True) is False


def test_same_failed_preview_version_is_not_enqueued_again():
    class Ctx:
        rendered_versions = set()
        failed_preview_versions = {
            4: {"error": "invalid EDL", "agent_repairable": True}}
        last_preview = None

        @staticmethod
        def latest_edl():
            return {"version": 4, "json": {}}

    result = agent_tools.render_preview(Ctx())
    assert result.startswith("Preview render FAILED:")
    assert "NOT re-enqueued" in result
    assert "NEW EDL version" in result


def test_speculative_preview_remembers_the_exact_adopted_job(monkeypatch):
    """A terminal speculative failure must be read, not inserted again."""
    class FakeDb:
        calls = []

        def run(self, fn, *_args):
            self.calls.append(fn)
            if fn is agent_tools.dbx.pending_preview_job:
                return 77
            raise AssertionError("an existing speculative row was duplicated")

    class Ctx:
        write_calls = []
        rendered_versions = set()
        spec_enqueued = set()
        spec_preview_jobs = {}
        project_id = 12
        db = FakeDb()

        @staticmethod
        def latest_edl():
            return {"version": 4, "json": {}}

    monkeypatch.setattr(agent_tools.config, "SPECULATIVE_PREVIEWS", True)
    monkeypatch.setattr(agent_tools.config, "SPECULATIVE_PREVIEWS_MAX", 2)
    agent_tools.speculative_preview(Ctx())
    assert Ctx.spec_preview_jobs == {4: 77}
    assert Ctx.spec_enqueued == {4}
