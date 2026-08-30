"""A logical agent turn cannot continue productively forever."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_loop  # noqa: E402
import config  # noqa: E402


def test_productive_continuations_increment_the_durable_slice_count():
    assert agent_loop._continuation_work_slices(
        {}, "productive work remains") == 1
    assert agent_loop._continuation_work_slices(
        {"work_slices": 7}, "execution slice boundary") == 8


def test_operational_handoffs_do_not_consume_productive_slices():
    state = {"work_slices": 4}
    assert agent_loop._continuation_work_slices(state, "platform drain") == 4
    assert agent_loop._continuation_work_slices(
        state, "awaiting complete preview") == 4


def test_productive_slice_limit_is_bounded_but_allows_large_edits():
    assert 2 <= config.AGENT_MAX_PRODUCTIVE_SLICES <= 24
