"""The agent loop has progress boundaries, not fixed call allowances."""

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_loop                                               # noqa: E402
import config                                                   # noqa: E402


def test_fixed_iteration_and_model_call_limits_are_gone():
    for name in ("AGENT_MAX_ITERATIONS", "AGENT_MAX_MODEL_CALLS",
                 "AGENT_AUTO_CONTINUES"):
        assert not hasattr(config, name)
    assert not hasattr(agent_loop, "_continue_decision")

    source = inspect.getsource(agent_loop._run_loop)
    assert "while True:" in source
    assert "range(config.AGENT_MAX" not in source
    assert "model-call ceiling" not in source
    assert "remaining_calls" not in source
    assert "only a few model calls remain" not in source


def test_real_external_boundaries_create_durable_continuations():
    source = inspect.getsource(agent_loop._run_loop)
    assert "SHUTDOWN.is_set()" in source
    assert "ctx.over_budget()" in source
    assert "AGENT_TURN_TIMEOUT_S" in source
    assert "_durable_continuation" in source
    assert "enqueue_agent_continuation" in source
    assert "execution slice boundary" in source
    assert '"images_generated"' in source
    assert '"videos_generated"' in source
    assert '"root_agent_job_id"' in source
    assert config.AGENT_TURN_TOTAL_TIMEOUT_S - config.AGENT_TURN_TIMEOUT_S \
        >= 120
    assert 'and n_clock < 1' not in source
    assert '"start_version": start_version' in source
    assert 'say \\"continue\\"' not in source.lower()
    assert "tell me to continue" not in source.lower()


def test_continuation_note_preserves_autonomy():
    note = agent_loop._CONTINUATION_NOTE.format(
        done="set_speed x4, render_preview x3", why="progress window",
        plan="")
    assert "NOT seen any reply" in note
    assert "NOT sent anything new" in note
    assert "use, repeat, inspect, write, and preview" in note
    assert "set_speed x4" in note
