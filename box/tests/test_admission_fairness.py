"""box/admission.py, R1 chaos (30 Oct 2026): a claim that re-asks after
"no room within 150 s" keeps its place in the queue.

Before: every re-ask re-entered at the back of its class, behind every call
that arrived while it waited (under sustained load a call could lose round
after round), and /health's oldest_wait_s reset every 150 s, so the box
under-reported its own waits. The call id names one claim, so its first
arrival is the job's real place.

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
        f"box_r1f_{time.monotonic_ns()}", os.path.join(BOX_DIR,
                                                        "admission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "mem_available_gib", lambda: 20.0)
    monkeypatch.setattr(module, "docker_state", lambda _c: (True, "t0"))
    monkeypatch.setattr(module, "docker_cpu_shares", lambda _c, _s: True)
    monkeypatch.setattr(module, "disk_report",
                        lambda path=None: {"free_gb": 80, "total_gb": 144})
    return module


def _box(adm):
    raw = {"owner_user_ids": [60], "disk_min_free_gib": 0, "slots": [
        {"name": "i1", "lane": "interactive", "port": 9101, "cpus": 2}]}
    box = adm.Box(adm.Config(raw), adm.EnvSecret("/nonexistent"), "tok")
    for slot in box.slots:
        slot.ready, slot.release = False, "v1"      # the lane is full
    return box


def _launch(box, call_id, launch_id, wait=0):
    return box.execute("interactive", call_id, {
        "job": {"id": 5, "type": "preview", "project_id": 7,
                "total_claims": 1, "user_id": 9, "dispatcher_source": "v1"},
        "launch_id": launch_id, "max_wait_s": wait})


def test_a_reasking_claim_keeps_its_first_arrival(adm, monkeypatch):
    box = _box(adm)
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(adm, "now", lambda: clock["t"])
    code, body = _launch(box, "bx-preview-p7-aaaa", "launch-0001")
    assert code == 429 and body["box_wait"] is True
    first = box.get("bx-preview-p7-aaaa")
    assert first["status"] == "refused"
    clock["t"] += 150.5
    seen = {}
    real_assign = box._assign

    def spy():
        for w in box.waiting.values():
            seen[w["call_id"]] = w["enqueued"]
        return real_assign()
    monkeypatch.setattr(box, "_assign", spy)
    code, _ = _launch(box, "bx-preview-p7-aaaa", "launch-0002")
    assert code == 429
    assert seen["bx-preview-p7-aaaa"] == 1_000_000.0
    assert box.get("bx-preview-p7-aaaa")["created_at"] == 1_000_000.0


def test_a_new_claim_or_an_old_refusal_starts_a_new_place(adm, monkeypatch):
    box = _box(adm)
    clock = {"t": 2_000_000.0}
    monkeypatch.setattr(adm, "now", lambda: clock["t"])
    _launch(box, "bx-preview-p7-bbbb", "launch-0001")
    clock["t"] += adm.KEEP_PLACE_S + 1
    _launch(box, "bx-preview-p7-bbbb", "launch-0002")
    assert box.get("bx-preview-p7-bbbb")["created_at"] == clock["t"]
    _launch(box, "bx-preview-p7-cccc", "launch-0003")
    assert box.get("bx-preview-p7-cccc")["created_at"] == clock["t"]


def test_the_queue_orders_a_reasking_claim_ahead_of_later_arrivals(adm):
    box = _box(adm)
    box.slots[0].ready = True
    early = {"call_id": "a", "lane": "interactive", "priority": "customer",
             "enqueued": 10.0, "slot": None, "job_type": "preview",
             "child": False, "source": "v1", "affinity": None}
    late = dict(early, call_id="b", enqueued=20.0)
    for w in (late, early):
        box.waiting[w["call_id"]] = w
        box.put({"call_id": w["call_id"], "lane": "interactive",
                 "job_type": "preview", "status": "submitted", "slot": None,
                 "job": json.dumps({"id": 1, "type": "preview",
                                    "project_id": 7, "total_claims": 1}),
                 "launch_id": "launch-x", "read_only": 0,
                 "priority": "customer", "user_id": 9, "envelope": None,
                 "error": None, "created_at": w["enqueued"],
                 "active_until": 1e12, "started_at": None,
                 "container_started": None, "disconnected_at": None,
                 "terminal_at": None, "abandon_reason": None})
    with box.lock:
        box._assign()
    assert early["slot"] == "i1" and late["slot"] is None
