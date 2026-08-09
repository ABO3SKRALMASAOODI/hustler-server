"""A stale hosting override must not put the dispatcher back at two slots."""

import os
import subprocess
import sys


def test_agent_slots_have_a_four_turn_floor():
    worker_dir = os.path.dirname(os.path.dirname(__file__))
    env = dict(os.environ, WORKER_AGENT_SLOTS="2",
               PYTHONPATH=worker_dir)
    value = subprocess.check_output(
        [sys.executable, "-c", "import config; print(config.AGENT_SLOTS)"],
        cwd=worker_dir, env=env, text=True)
    assert value.strip() == "4"
