import threading

import main as worker_main


class _NoopDb:
    def run(self, *_args, **_kwargs):
        return None


def _fresh_drain(monkeypatch):
    drain = threading.Event()
    monkeypatch.setattr(worker_main, "DRAINING", drain)
    monkeypatch.setattr(worker_main.dbx, "Db", lambda: _NoopDb())
    return drain


def test_supervisor_restarts_a_loop_that_returns(monkeypatch):
    drain = _fresh_drain(monkeypatch)
    calls = []

    def loop():
        calls.append("called")
        if len(calls) == 2:
            drain.set()

    worker_main._supervise("test-lane", loop, restart_delay_s=0)

    assert calls == ["called", "called"]


def test_supervisor_restarts_after_a_base_exception(monkeypatch):
    drain = _fresh_drain(monkeypatch)
    calls = []

    def loop():
        calls.append("called")
        if len(calls) == 1:
            raise KeyboardInterrupt("library escaped the lane")
        drain.set()

    worker_main._supervise("test-lane", loop, restart_delay_s=0)

    assert calls == ["called", "called"]


def test_supervisor_does_not_restart_during_planned_drain(monkeypatch):
    drain = _fresh_drain(monkeypatch)
    calls = []

    def loop():
        calls.append("called")
        drain.set()

    worker_main._supervise("test-lane", loop, restart_delay_s=0)

    assert calls == ["called"]
