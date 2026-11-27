"""box/boxctl.py: idle slots whose memory crept up are recycled (27 Nov 2026).

Four idle batch slots held 10.9 GiB; MemAvailable 2.5 < reserve 3.5 held four
trial calls for 19 minutes with 30 slots free. ``clean`` now restarts the
fattest idle slots; a busy, retiring or call-holding slot is never touched.

Run locally: python -m pytest box/tests
"""

import importlib.util
import os
import time

import pytest

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("BOX_STATE_DB", str(tmp_path / "admission.sqlite"))
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOX_CONFIG", str(tmp_path / "box.json"))
    spec = importlib.util.spec_from_file_location(
        f"boxctl_recycle_{time.monotonic_ns()}",
        os.path.join(BOX_DIR, "boxctl.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "log", lambda msg: None)
    return module


STATS = """vbox-batch4 3.558GiB / 12GiB
vbox-batch2 3.052GiB / 12GiB
vbox-batch1 2.142GiB / 12GiB
vbox-agent1 954MiB / 2GiB
vbox-mcp1 231.1MiB / 2GiB
postgres 1.2GiB / 4GiB
"""


def _slot(name, **kw):
    d = {"name": name, "ready": True, "busy": False, "retiring": False,
         "call": None, "release": "abc"}
    d.update(kw)
    return d


def test_parse_slot_memory(ctl):
    mem = ctl.parse_slot_memory(STATS)
    assert set(mem) == {"batch4", "batch2", "batch1", "agent1", "mcp1"}
    assert mem["batch4"] == pytest.approx(3.558, abs=0.01)
    assert mem["agent1"] == pytest.approx(954 / 1024, abs=0.01)


def test_fattest_idle_first_and_capped(ctl):
    state = {"memory": {"available_gib": 9.0, "reserve_gib": 3.5},
             "slot_detail": [_slot("batch1"), _slot("batch2"),
                             _slot("batch4"), _slot("agent1"),
                             _slot("mcp1")]}
    mem = ctl.parse_slot_memory(STATS)
    assert ctl.recycle_candidates(state, mem, {}) == \
        ["batch4", "batch2", "batch1"]
    assert ctl.recycle_candidates(
        state, mem, {"idle_recycle_max_per_run": 1}) == ["batch4"]


def test_never_busy_retiring_or_calling(ctl):
    state = {"memory": {"available_gib": 9.0, "reserve_gib": 3.5},
             "slot_detail": [_slot("batch4", busy=True),
                             _slot("batch2", retiring=True),
                             _slot("batch1", call="bx-1"),
                             _slot("agent1", ready=False)]}
    assert ctl.recycle_candidates(
        state, ctl.parse_slot_memory(STATS), {}) == []


def test_tight_memory_lowers_threshold(ctl):
    slots = [_slot("agent1"), _slot("mcp1")]
    mem = ctl.parse_slot_memory(STATS)
    calm = {"memory": {"available_gib": 9.0, "reserve_gib": 3.5},
            "slot_detail": slots}
    tight = {"memory": {"available_gib": 2.5, "reserve_gib": 3.5},
             "slot_detail": slots}
    assert ctl.recycle_candidates(calm, mem, {}) == []
    assert ctl.recycle_candidates(tight, mem, {}) == ["agent1"]


def test_recycle_retires_restarts_and_readmits(ctl, monkeypatch):
    state = {"memory": {"available_gib": 2.5, "reserve_gib": 3.5},
             "slot_detail": [_slot("batch4"), _slot("batch2")]}
    calls = []
    monkeypatch.setattr(ctl, "config", lambda: {
        "idle_recycle_max_per_run": 2,
        "slots": [{"name": "batch4", "lane": "batch", "port": 1},
                  {"name": "batch2", "lane": "batch", "port": 2}]})
    monkeypatch.setattr(ctl, "admission_state", lambda: state)
    monkeypatch.setattr(ctl, "slot_memory_gib",
                        lambda: ctl.parse_slot_memory(STATS))
    monkeypatch.setattr(ctl, "slot_has_live_call",
                        lambda name: name == "batch2")
    monkeypatch.setattr(ctl, "admin",
                        lambda m, p, b=None: calls.append((p, b)) or {})
    monkeypatch.setattr(ctl, "sh",
                        lambda args, **kw: calls.append(tuple(args)))
    monkeypatch.setattr(ctl, "wait_ready", lambda *a, **kw: True)
    assert ctl.recycle_idle_slots() == ["batch4"]
    assert ("docker", "restart", "-t", "20", "vbox-batch4") in calls
    assert not any(c[:2] == ("docker", "restart") and "vbox-batch2" in c
                   for c in calls)
    # Every retired slot is readmitted, the kept one included.
    for n in ("batch4", "batch2"):
        assert ("/admin/slot", {"name": n, "retiring": True}) in calls
        assert ("/admin/slot", {"name": n, "retiring": False}) in calls
