"""Agent slots: the env is a real knob again (round 101).

Round 100 put a max(4, env) floor here so a stale hosting override could not
quietly undo the capacity fix. Then 19 agent turns died as "Worker died and
retries are exhausted" in three days — the box OOMs under concurrent turns —
and the floor was the one thing forbidding the operator from trading latency
for survival. The contract now: default 4, env wins in BOTH directions,
absolute floor of 1 so a typo can't park the lane at zero.
"""

import os
import subprocess
import sys


def _slots(env_value):
    worker_dir = os.path.dirname(os.path.dirname(__file__))
    env = dict(os.environ, PYTHONPATH=worker_dir)
    env.pop("WORKER_AGENT_SLOTS", None)
    if env_value is not None:
        env["WORKER_AGENT_SLOTS"] = env_value
    return subprocess.check_output(
        [sys.executable, "-c", "import config; print(config.AGENT_SLOTS)"],
        cwd=worker_dir, env=env, text=True).strip()


def test_agent_slots_default_four():
    assert _slots(None) == "4"


def test_agent_slots_env_may_lower():
    assert _slots("2") == "2"


def test_agent_slots_env_may_raise():
    assert _slots("6") == "6"


def test_agent_slots_never_zero():
    assert _slots("0") == "1"
