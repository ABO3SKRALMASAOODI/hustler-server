"""A planned deploy stops queue claims before it waits on active work."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as worker_main  # noqa: E402


def test_draining_lane_exits_without_claiming_or_counting_a_worker_death(
        monkeypatch):
    calls = []

    class FakeDb:
        def run(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("a draining lane must not touch the queue")

    monkeypatch.setattr(worker_main.dbx, "Db", FakeDb)
    worker_main.DRAINING.set()
    try:
        worker_main.lane("agent-test", worker_main.AGENT_TYPES, 1, 0.01)
    finally:
        worker_main.DRAINING.clear()
    assert calls == []
