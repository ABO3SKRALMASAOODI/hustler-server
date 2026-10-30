"""box/boxctl.py, R1 chaos (30 Oct 2026): when admission may restart.

Live: an admission restart with five queue-backed calls in flight lost
nothing (each finished on its first claim). Waiting for a fully idle box
instead held a slot-plan change behind a 2-h index and needed a drain that
made every new call wait 50+ minutes. Now only a synchronous child (its
answer has no queue row) or a call starting on a slot defers it.

Run locally: python -m pytest box/tests
"""

import importlib.util
import json
import os
import sqlite3
import time

import pytest

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("BOX_STATE_DB", str(tmp_path / "admission.sqlite"))
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOX_CONFIG", str(tmp_path / "box.json"))
    spec = importlib.util.spec_from_file_location(
        f"boxctl_r1_{time.monotonic_ns()}", os.path.join(BOX_DIR,
                                                          "boxctl.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "log", lambda msg: None)
    return module


def _calls(path, rows):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE calls (call_id TEXT, status TEXT, job TEXT)")
    for call_id, status, job_id in rows:
        db.execute("INSERT INTO calls VALUES (?, ?, ?)", (
            call_id, status, json.dumps({"id": job_id, "type": "x",
                                         "total_claims":
                                             None if job_id is None else 1})))
    db.commit()
    db.close()


def test_long_queue_calls_never_hold_an_admission_restart(ctl, tmp_path):
    _calls(tmp_path / "admission.sqlite", [
        ("bx-index-a", "running", 125401), ("bx-final-b", "unknown", 125405),
        ("bx-preview-c", "running", 125403), ("bx-old", "done", None),
        ("bx-wait", "submitted", 125406)])
    assert ctl.admission_restart_blocker() is None


@pytest.mark.parametrize("status,job_id,why", (
    ("running", None, "child"), ("unknown", None, "child"),
    ("starting", 125407, "starting")))
def test_a_child_or_a_starting_call_defers_it(ctl, tmp_path, status, job_id,
                                             why):
    _calls(tmp_path / "admission.sqlite", [
        ("bx-index-a", "running", 125401), ("bx-x", status, job_id)])
    assert why in ctl.admission_restart_blocker()


def test_an_unreadable_state_keeps_the_old_idle_rule(ctl):
    assert ctl.admission_restart_blocker(
        {"slots": {"busy": 1}, "queue": {"depth": 0}}) == "calls in flight"
    assert ctl.admission_restart_blocker(
        {"slots": {"busy": 0}, "queue": {"depth": 0}}) is None


def test_the_deferred_restart_runs_as_soon_as_no_child_runs(ctl, tmp_path,
                                                           monkeypatch):
    _calls(tmp_path / "admission.sqlite", [("bx-index-a", "running", 1)])
    flag = tmp_path / "admission-restart-pending"
    flag.write_text("")
    ran = []
    monkeypatch.setattr(ctl, "sh", lambda args, check=True: ran.append(args))
    ctl.pending_admission_restart()
    assert ran == [["systemctl", "restart", "valmera-admission"]]
    assert not flag.exists()
