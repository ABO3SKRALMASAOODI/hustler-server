"""box/boxctl.py, R1 chaos (30 Oct 2026): one update run never waits hours
for a busy slot.

Live: a slot-plan roll waited on a retiring slot running a 2-h index for
the whole drain_timeout_s (7200 s): the update service was busy all that
time, so nothing else converged and a release pushed meanwhile would not
have been built (every new box job waits under skew "hold"). Now each run
waits at most roll_wait_per_run_s (300 s), leaves the busy slot on its
release, and the next run (2 min later) picks it up once it is idle. A call
is still never killed.

Run locally: python -m pytest box/tests
"""

import importlib.util
import os
import time

import pytest

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOX_CONFIG", str(tmp_path / "box.json"))
    spec = importlib.util.spec_from_file_location(
        f"boxctl_r1roll_{time.monotonic_ns()}", os.path.join(BOX_DIR,
                                                              "boxctl.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "log", lambda msg: None)
    return module


def test_one_run_waits_minutes_not_the_whole_drain(ctl):
    assert ctl.roll_wait_s({"drain_timeout_s": 7200}) == 300.0
    assert ctl.roll_wait_s({"drain_timeout_s": 120}) == 120.0
    assert ctl.roll_wait_s({"roll_wait_per_run_s": 900,
                            "drain_timeout_s": 7200}) == 900.0
    assert ctl.roll_wait_s({"roll_wait_per_run_s": "x"}) == 300.0
    assert ctl.roll_wait_s({"roll_wait_per_run_s": 1}) == 30.0


def test_a_busy_slot_is_left_on_its_release_and_never_killed(ctl,
                                                             monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(ctl.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(ctl.time, "sleep",
                        lambda s: clock.__setitem__("t", clock["t"] + s))
    state = {"slot_detail": [{"name": "batch1", "busy": True},
                             {"name": "batch2", "busy": False}]}
    monkeypatch.setattr(ctl, "admission_state", lambda: state)
    admin_calls, replaced = [], []
    monkeypatch.setattr(ctl, "admin",
                        lambda m, p, b=None: admin_calls.append((p, b)))
    monkeypatch.setattr(ctl, "run_slot",
                        lambda slot, source: replaced.append(slot["name"]))
    monkeypatch.setattr(ctl, "wait_ready", lambda slot, source: True)
    slots = [{"name": "batch1", "lane": "batch", "port": 9109},
             {"name": "batch2", "lane": "batch", "port": 9110}]
    ctl.roll_slots(slots, "v2", ctl.roll_wait_s({"drain_timeout_s": 7200}))
    assert replaced == ["batch2"]                 # the busy one untouched
    assert clock["t"] <= 310                      # returned within the run
    assert ("/admin/slot", {"name": "batch1", "retiring": False}) in \
        admin_calls                               # serving again, old code


def test_update_and_apply_use_the_per_run_bound(ctl, monkeypatch):
    seen = []
    monkeypatch.setattr(ctl, "converge",
                        lambda source, commit, wait: seen.append(wait))
    monkeypatch.setattr(ctl, "config", lambda: {"drain_timeout_s": 7200})
    monkeypatch.setattr(ctl, "release",
                        lambda: {"source": "v2", "commit": "c"})
    monkeypatch.setattr(ctl, "sync_limits", lambda: None)
    ctl.cmd_apply()
    assert seen == [300.0]
