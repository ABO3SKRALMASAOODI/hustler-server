"""The Claude lanes' effort knobs (TRIAL_ECONOMICS, 31 Oct 2026).

The real-session replay picked planning at xhigh and dispatch at medium for
Haiku 5.5 (better score, more asks met, cheaper, faster than high/medium).
Both stay environment knobs (CLAUDE_TRIAL_PLAN_EFFORT / CLAUDE_TRIAL_EFFORT)
and both values must reach the Messages API as output_config.effort.
"""
import os
import re
from pathlib import Path

import claude_lane
import config

SRC = (Path(__file__).resolve().parents[1] / "config.py").read_text()


def test_defaults_are_the_measured_winner():
    assert re.search(r'CLAUDE_TRIAL_PLAN_EFFORT = os\.getenv\("CLAUDE_TRIAL_PLAN_EFFORT", "xhigh"\)', SRC)
    assert re.search(r'CLAUDE_TRIAL_EFFORT = os\.getenv\("CLAUDE_TRIAL_EFFORT", "medium"\)', SRC)
    if "CLAUDE_TRIAL_PLAN_EFFORT" not in os.environ:
        assert config.CLAUDE_TRIAL_PLAN_EFFORT == "xhigh"
    if "CLAUDE_TRIAL_EFFORT" not in os.environ:
        assert config.CLAUDE_TRIAL_EFFORT == "medium"


def test_both_efforts_reach_the_messages_api_unchanged():
    assert claude_lane._effort({"reasoning_effort": "xhigh"}) == "xhigh"
    assert claude_lane._effort({"reasoning_effort": "medium"}) == "medium"
