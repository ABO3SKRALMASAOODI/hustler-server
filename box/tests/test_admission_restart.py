"""box/admission.py, R1 chaos (30 Oct 2026): an admission restart with
calls in flight.

Live (systemctl restart with 5 calls in flight: 2 approval previews, a
final, a 30-min and a 2-h index): every queue-backed call finished on its
first claim; the dispatcher reconciled them from PostgreSQL. A synchronous
child has no queue row: kept `unknown`, its parent tool waited out the
child's whole lease (frames 4 min, stock_acquire / mcp_media 25 min) and
its quick slot stayed fenced as long. Now a lost child is fenced at once.

Run locally: python -m pytest box/tests
"""

import importlib.util
import json
import os
import time

import pytest

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


@pytest.fixture
def adm(tmp_path, monkeypatch):
    monkeypatch.setenv("BOX_STATE_DB", str(tmp_path / "adm.sqlite"))
    monkeypatch.setenv("BOX_RELEASE", str(tmp_path / "release.json"))
    monkeypatch.setenv("BOX_STEAL_LOG", str(tmp_path / "steal.log"))
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOX_ENV_FILE", str(tmp_path / "executor.env"))
    monkeypatch.setenv("BOX_CONFIG", str(tmp_path / "box.json"))
    spec = importlib.util.spec_from_file_location(
        f"box_r1r_{time.monotonic_ns()}", os.path.join(BOX_DIR,
                                                        "admission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "mem_available_gib", lambda: 20.0)
    monkeypatch.setattr(module, "docker_cpu_shares", lambda _c, _s: True)
    monkeypatch.setattr(module, "docker_state", lambda _c: (True, "t0"))
    monkeypatch.setattr(module, "container_health", lambda port, timeout=3.0:
                        (200, {"status": "ok", "role": "executor",
                               "code_version": "v1"}))
    return module


RAW = {"owner_user_ids": [60], "disk_min_free_gib": 0, "slots": [
    {"name": "i1", "lane": "interactive", "port": 9101, "cpus": 2},
    {"name": "look1", "lane": "interactive", "port": 9107, "cpus": 2,
     "quick": True},
    {"name": "b1", "lane": "batch", "port": 9109, "cpus": 4}]}


def _box(adm):
    return adm.Box(adm.Config(RAW), adm.EnvSecret("/nonexistent"), "tok")


def _running(box, call_id, jtype, slot, child):
    t = time.time()
    box.put({"call_id": call_id, "lane": box.by_name[slot].lane,
             "job_type": jtype, "status": "running", "slot": slot,
             "job": json.dumps({"id": None if child else 9, "type": jtype,
                                "project_id": None if child else 7,
                                "total_claims": None if child else 1}),
             "launch_id": "launch-aaaa", "read_only": 0,
             "priority": "customer", "user_id": 5, "envelope": None,
             "error": None, "created_at": t - 30, "active_until": t + 3000,
             "started_at": t - 20, "container_started": "t0",
             "disconnected_at": None, "terminal_at": None,
             "abandon_reason": None})


def test_a_restart_fences_a_lost_child_and_keeps_queue_calls(adm,
                                                             monkeypatch):
    first = _box(adm)
    _running(first, "bx-frames-child1", "frames", "look1", child=True)
    _running(first, "bx-final-abc", "final", "b1", child=False)
    restarted = _box(adm)        # a new process on the same state
    child = restarted.get("bx-frames-child1")
    queue = restarted.get("bx-final-abc")
    assert child["status"] == "stopping"
    assert "answer was lost" in child["abandon_reason"]
    assert queue["status"] == "unknown"   # PostgreSQL holds its outcome

    fenced = []
    monkeypatch.setattr(adm, "docker_restart",
                        lambda c: fenced.append(c) or True)
    restarted.poll_slots()
    assert fenced == ["vbox-look1"]
    child = restarted.get("bx-frames-child1")
    assert child["status"] == "failed"
    env = json.loads(child["envelope"])
    assert env["retryable"] is True
    assert env["failure"]["kind"] == "transient_infrastructure"
    assert "admission restarted" in env["error"]
    # The final keeps its slot until its executor answers through the DB.
    assert restarted.get("bx-final-abc")["status"] == "unknown"
    assert restarted.active_call("b1") == "bx-final-abc"
    assert restarted.active_call("look1") is None


def test_an_unknown_child_left_by_an_older_restart_is_fenced_too(adm):
    first = _box(adm)
    _running(first, "bx-capture-child2", "capture", "look1", child=True)
    rec = first.get("bx-capture-child2")
    rec["status"] = "unknown"
    first.put(rec)
    assert _box(adm).get("bx-capture-child2")["status"] == "stopping"


def test_queued_children_still_get_their_nothing_ran_refusal(adm):
    first = _box(adm)
    t = time.time()
    first.put({"call_id": "bx-frames-child3", "lane": "interactive",
               "job_type": "frames", "status": "submitted", "slot": None,
               "job": json.dumps({"id": None, "type": "frames",
                                  "project_id": None, "total_claims": None}),
               "launch_id": "launch-bbbb", "read_only": 0,
               "priority": "customer", "user_id": 5, "envelope": None,
               "error": None, "created_at": t, "active_until": t + 300,
               "started_at": None, "container_started": None,
               "disconnected_at": None, "terminal_at": None,
               "abandon_reason": None})
    rec = _box(adm).get("bx-frames-child3")
    assert rec["status"] == "failed"
    env = json.loads(rec["envelope"])
    assert env["safe_to_fallback"] is True and env["box_wait"] is True
